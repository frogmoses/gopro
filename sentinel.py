"""Roast sentinel — orchestrates vision monitoring during a roast.

Connects to Artisan via WebSocket, captures images periodically,
sends them to Claude Vision for color assessment, and logs
timestamped observations synced to the roast timeline.

Uses GoPro Hero 13 over USB for image capture (replaces r1-eye's
ADB-based capture). All bridge calls are async.

Capture frequency adapts to roast phase:
  - Drying:      every 30 seconds
  - Maillard:    every 20 seconds
  - Development: every 10 seconds (most critical phase)
"""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import gopro_bridge
import artisan_sync
import vision_client
import sentinel_display

# Capture intervals per phase (seconds)
CAPTURE_INTERVALS = {
    "drying": 30,
    "maillard": 20,
    "development": 10,
    "cooling": 0,  # no captures after DROP
}

# Default interval before CHARGE
DEFAULT_INTERVAL = 30

# Log directory
CAPTURES_DIR = Path(__file__).parent / "captures"


class SentinelSession:
    """Manages a single roast monitoring session."""

    def __init__(self, bean_name=None, enable_crack=False, ws_port=8765, debug=False):
        self.bean_name = bean_name or "Unknown"
        self.enable_crack = enable_crack
        self.ws_port = ws_port
        self.debug = debug

        # Session state
        self.session_id = time.strftime("%Y-%m-%d_%H%M")
        self.observations = []
        self.capture_count = 0
        self.latest_observation = None
        self.crack_status = None
        self.running = False

        # Event-triggered capture flag — set by callback, consumed by async loop
        self._event_capture_pending = False

        # Artisan sync server
        self.artisan = artisan_sync.ArtisanServer(port=ws_port, debug=debug)
        self.artisan.on_event(self._on_artisan_event)
        self.artisan.on_connect(self._on_artisan_connect)
        self.artisan.on_disconnect(self._on_artisan_disconnect)

    # Events that should trigger an immediate capture
    CAPTURE_EVENTS = {"CHARGE", "DRY", "FCs", "FCe", "SCs", "SCe", "DROP"}

    def _on_artisan_event(self, event_name, elapsed):
        """Handle a roast event from Artisan."""
        time_str = sentinel_display.fmt_time(elapsed)
        print(f"  Artisan: {event_name} at T+{time_str}")

        # Flag an immediate capture for key roast events
        if event_name in self.CAPTURE_EVENTS:
            self._event_capture_pending = True

        # DROP ends the session
        if event_name == "DROP":
            self.running = False

    def _on_artisan_connect(self):
        """Handle Artisan WebSocket connection."""
        print("  Artisan connected")

    def _on_artisan_disconnect(self):
        """Handle Artisan WebSocket disconnection."""
        print("  Artisan disconnected")

    def _get_capture_interval(self):
        """Get capture interval based on current roast phase."""
        phase = self.artisan.current_phase
        if phase is None:
            return DEFAULT_INTERVAL
        return CAPTURE_INTERVALS.get(phase, DEFAULT_INTERVAL)

    def _build_session_state(self):
        """Build state dict for display rendering."""
        return {
            "bean_name": self.bean_name,
            "elapsed": self.artisan.elapsed(),
            "phase": self.artisan.current_phase,
            "connected": self.artisan.connected,
            "events": self.artisan.events,
            "latest_observation": self.latest_observation,
            "crack_status": self.crack_status,
            "capture_count": self.capture_count,
        }

    async def _capture_and_analyze(self):
        """Capture an image and send to Claude Vision for analysis."""
        # Generate filename tied to session and timestamp
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"sentinel_{timestamp}.jpg"

        # Use quick_capture if camera session is active, else full capture
        if gopro_bridge.is_camera_session_active():
            image_path = await gopro_bridge.quick_capture(filename)
        else:
            image_path = await gopro_bridge.capture_image(filename)
        if not image_path:
            return None

        self.capture_count += 1

        # Get roast context for the vision query
        elapsed = self.artisan.elapsed()
        phase = self.artisan.current_phase

        # Ask Claude about the bean color
        result = vision_client.assess_roast_color(
            image_path,
            elapsed_seconds=elapsed,
            phase=phase,
        )

        if result:
            # Build observation record
            observation = {
                "elapsed_seconds": round(elapsed, 1) if elapsed else 0,
                "phase": phase or "pre-charge",
                "type": "vision",
                "image_file": str(image_path),
                "color_assessment": result.get("color_assessment", ""),
                "development_score": result.get("development_score", 0),
                "uniformity": result.get("uniformity", ""),
            }
            self.observations.append(observation)
            self.latest_observation = observation

        return result

    def _push_log(self, log_file):
        """Push session log to dev machine via rsync (best-effort).

        Uses the same SSH target as the artisan-sync pipeline on the roaster.
        Prints a status line but never raises — a failed push is non-fatal.
        """
        dest = os.environ.get("SENTINEL_RSYNC_DEST")
        if not dest:
            print("Warning: SENTINEL_RSYNC_DEST not set, skipping log push")
            return
        result = subprocess.run(
            ["rsync", "-az", str(log_file), dest],
            capture_output=True,
        )
        if result.returncode == 0:
            print(f"Pushed log to dev machine")
        else:
            print(f"Warning: could not push log to dev machine (rsync exit {result.returncode})")

    def _save_log(self):
        """Save session log to JSON file."""
        CAPTURES_DIR.mkdir(exist_ok=True)
        log_file = CAPTURES_DIR / f"sentinel_{self.session_id}.json"

        log_data = {
            "session_id": self.session_id,
            "bean_name": self.bean_name,
            "artisan_events": {
                k.lower(): v for k, v in self.artisan.events.items()
            },
            "observations": self.observations,
        }

        log_file.write_text(json.dumps(log_data, indent=2, default=str))
        return log_file

    async def run(self):
        """Main sentinel loop — start server, wait for CHARGE, capture periodically."""
        self.running = True

        # Verify GoPro is connected
        if not await gopro_bridge.is_connected():
            print("Error: No GoPro connected")
            return

        # Start Artisan WebSocket server
        print(f"Starting sentinel for: {self.bean_name}")
        print(f"WebSocket server on ws://0.0.0.0:{self.ws_port}/")
        print()
        print("Artisan config (Config → Ports → WebSocket tab):")
        print(f"  Host: 127.0.0.1   Port: {self.ws_port}   Path: WebSocket")
        print()
        print("Waiting for Artisan to connect (press ON in Artisan)...")

        await self.artisan.start()

        # Wait for Artisan connection
        while not self.artisan.connected and self.running:
            sentinel_display.clear_and_render(self._build_session_state())
            await asyncio.sleep(1)

        if not self.running:
            self.artisan.stop()
            return

        # Open camera session as soon as Artisan connects (ON/START).
        # This gets the camera ready before CHARGE.
        print("Artisan connected — opening camera session...")
        await gopro_bridge.start_camera_session()

        # Wait for CHARGE event to start capture loop
        print("Camera ready — waiting for CHARGE...")
        while "CHARGE" not in self.artisan.events and self.running:
            sentinel_display.clear_and_render(self._build_session_state())
            await asyncio.sleep(1)

        if not self.running:
            await gopro_bridge.end_camera_session()
            self.artisan.stop()
            self._save_log()
            return

        # Main capture loop — runs until DROP or Ctrl+C
        print("CHARGE detected — sentinel active")
        last_capture_time = 0

        while self.running:
            capture_needed = False

            # Event-triggered capture (CHARGE, DRY, FCs, etc.)
            if self._event_capture_pending:
                self._event_capture_pending = False
                capture_needed = True

            # Time-based capture (phase intervals)
            now = time.time()
            interval = self._get_capture_interval()
            if interval > 0 and (now - last_capture_time) >= interval:
                capture_needed = True

            if capture_needed:
                await self._capture_and_analyze()
                last_capture_time = time.time()

            # Update display
            sentinel_display.clear_and_render(self._build_session_state())
            await asyncio.sleep(1)

        # Final capture at DROP (flag was set by _on_artisan_event)
        if self._event_capture_pending:
            self._event_capture_pending = False
            await self._capture_and_analyze()

        # Close camera session
        await gopro_bridge.end_camera_session()

        # Save log and push to dev machine
        log_file = self._save_log()
        print(f"\nSession log saved: {log_file}")
        self._push_log(log_file)

        # Show final display
        sentinel_display.clear_and_render(self._build_session_state())

        # Stop WebSocket server
        self.artisan.stop()
        await self.artisan.wait_until_stopped()


def start_sentinel(bean_name=None, enable_crack=False, ws_port=8765, debug=False):
    """Entry point to start a sentinel session (blocking).

    Args:
        bean_name: Name of the bean being roasted.
        enable_crack: Enable audio crack detection (Phase 3).
        ws_port: WebSocket server port for Artisan.
        debug: Log raw WebSocket messages for debugging.
    """
    session = SentinelSession(
        bean_name=bean_name,
        enable_crack=enable_crack,
        ws_port=ws_port,
        debug=debug,
    )

    try:
        asyncio.run(session.run())
    except KeyboardInterrupt:
        print("\nSentinel interrupted")
        session.running = False
        # Clean up camera if it was open
        if gopro_bridge.is_camera_session_active():
            asyncio.run(gopro_bridge.end_camera_session())
        # Save whatever we have and push to dev machine
        log_file = session._save_log()
        print(f"Partial log saved: {log_file}")
        session._push_log(log_file)


def show_latest_log():
    """Display the most recent sentinel session log."""
    log_files = sorted(CAPTURES_DIR.glob("sentinel_*.json"))
    if not log_files:
        print("No sentinel logs found")
        return

    latest = log_files[-1]
    data = json.loads(latest.read_text())

    print(f"Session: {data.get('session_id', 'unknown')}")
    print(f"Bean: {data.get('bean_name', 'unknown')}")
    print()

    observations = data.get("observations", [])
    print(sentinel_display.render_log(observations))


def show_sentinel_status():
    """Show status of any running sentinel (placeholder for future use)."""
    # Check for recent log files
    log_files = sorted(CAPTURES_DIR.glob("sentinel_*.json"))
    if not log_files:
        print("No sentinel sessions found")
        return

    latest = log_files[-1]
    data = json.loads(latest.read_text())
    print(f"Last session: {data.get('session_id', 'unknown')}")
    print(f"Bean: {data.get('bean_name', 'unknown')}")
    print(f"Observations: {len(data.get('observations', []))}")

    events = data.get("artisan_events", {})
    if events:
        print(f"Events: {', '.join(f'{k}={sentinel_display.fmt_time(v)}' for k, v in events.items())}")
