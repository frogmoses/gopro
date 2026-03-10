"""Artisan WebSocket server — receives roast events from Artisan.

Artisan connects to this server as a WebSocket client (Config → Ports →
WebSocket tab). The connection is established by the first button press
that has a WebSocket Command action (typically ON).

Artisan WebSocket protocol:
  - Button event:  {"event": "CHARGE", "id": N, "roasterID": 0}
  - Native events: {"message": "chargeEvent"} (from event tag config)
  - Data request:  {"command": "getData", "id": N, "machine": 0}
    (only if Extra Device configured — not required)

Artisan config (Config → Ports → WebSocket tab):
  Host: 127.0.0.1   Port: 8765   Path: WebSocket

Configure Artisan buttons with WebSocket Command actions:
  ON:     send({"event": "ON"})       ← establishes connection
  START:  send({"event": "START"})
  CHARGE: send({"event": "CHARGE"})
  DRY:    send({"event": "DRY"})
  FCs:    send({"event": "FCs"})
  FCe:    send({"event": "FCe"})
  DROP:   (Hottop Command — not available for WebSocket)
  COOL:   send({"event": "DROP"})  ← COOL END button sends DROP

Note: DROP's action slot is used by Hottop safety commands.
COOL END is configured to send DROP instead, ending the session.
"""

import asyncio
import json
import time

import websockets

# Default server settings
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765

# Roast events we track, in expected order
# START is included because Artisan sends it — we log it but it doesn't
# change the roast phase (phase tracking begins at CHARGE)
ROAST_EVENTS = ["START", "CHARGE", "DRY", "FCs", "FCe", "SCs", "SCe", "DROP"]

# Artisan native event names → our standardized names.
# Artisan sends these when WebSocket event tags are configured in the
# Ports dialog (e.g. DRY = colorChangeEvent, FCs = FirstCrackBeginningEvent).
ARTISAN_EVENT_MAP = {
    # Native Artisan event tag names
    "chargeEvent": "CHARGE",
    "colorChangeEvent": "DRY",          # DRY END = "color change"
    "FirstCrackBeginningEvent": "FCs",
    "FirstCrackEndEvent": "FCe",
    "SecondCrackBeginningEvent": "SCs",
    "SecondCrackEndEvent": "SCe",
    "dropEvent": "DROP",
    "coolEvent": "DROP",              # COOL END → treated as DROP
    # Also accept our standard short names as-is
    "START": "START",
    "CHARGE": "CHARGE",
    "DRY": "DRY",
    "FCs": "FCs",
    "FCe": "FCe",
    "SCs": "SCs",
    "SCe": "SCe",
    "DROP": "DROP",
}

# Map events to roast phases
EVENT_TO_PHASE = {
    "CHARGE": "drying",
    "DRY": "maillard",
    "FCs": "development",
    "FCe": "development",
    "SCs": "development",
    "SCe": "development",
    "DROP": "cooling",
}


class ArtisanServer:
    """WebSocket server that receives and tracks Artisan roast events."""

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, debug=False):
        self.host = host
        self.port = port
        self.debug = debug         # log raw messages for debugging

        # Event tracking
        self.events = {}           # event_name → elapsed_seconds
        self.charge_time = None    # absolute time of CHARGE (T=0 reference)
        self.current_phase = None  # current roast phase string
        self.connected = False     # whether Artisan is connected
        self.data_request_count = 0  # number of getData polls received

        # Callbacks — sentinel hooks into these
        self._on_event = None      # called with (event_name, elapsed_seconds)
        self._on_connect = None    # called when Artisan connects
        self._on_disconnect = None # called when Artisan disconnects

        # Server control
        self._server = None
        self._stop_event = asyncio.Event()

    def on_event(self, callback):
        """Register callback for roast events: callback(event_name, elapsed_seconds)."""
        self._on_event = callback

    def on_connect(self, callback):
        """Register callback for Artisan connection."""
        self._on_connect = callback

    def on_disconnect(self, callback):
        """Register callback for Artisan disconnection."""
        self._on_disconnect = callback

    def elapsed(self):
        """Seconds since CHARGE, or None if CHARGE hasn't happened."""
        if self.charge_time is None:
            return None
        return time.time() - self.charge_time

    def _handle_event(self, event_name):
        """Process a roast event from Artisan."""
        # Record CHARGE as T=0 reference
        if event_name == "CHARGE":
            self.charge_time = time.time()

        # Calculate elapsed time relative to CHARGE
        if self.charge_time is not None:
            elapsed = time.time() - self.charge_time
        else:
            elapsed = 0.0

        # Store event
        self.events[event_name] = round(elapsed, 1)

        # Update current phase
        if event_name in EVENT_TO_PHASE:
            self.current_phase = EVENT_TO_PHASE[event_name]

        # Fire callback
        if self._on_event:
            self._on_event(event_name, elapsed)

    def _parse_message(self, raw):
        """Parse an incoming WebSocket message from Artisan.

        Handles three message formats:
          1. Button send() actions: {"event": "CHARGE"}
          2. Push/native messages:  {"message": "chargeEvent"} or {"message": "CHARGE"}
          3. Data polling:          {"command": "getData", "id": N, "machine": 0}

        Native Artisan event names (e.g. "colorChangeEvent") are mapped
        to our standard names (e.g. "DRY") via ARTISAN_EVENT_MAP.

        Returns:
            Tuple of (msg_type, data) where msg_type is 'event',
            'data_request', or 'unknown'.
        """
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            if self.debug:
                print(f"  [WS] non-JSON: {raw!r}")
            return "unknown", {}

        if self.debug:
            print(f"  [WS] recv: {msg}")

        # Check for button event: {"event": "CHARGE"}
        if "event" in msg:
            # Normalize via event map (passes through unknown names)
            event_name = ARTISAN_EVENT_MAP.get(msg["event"], msg["event"])
            return "event", {"event": event_name}

        # Check for push/native message format: {"message": "colorChangeEvent"}
        if "message" in msg:
            # Normalize native event names to our standard names
            event_name = ARTISAN_EVENT_MAP.get(msg["message"], msg["message"])
            return "event", {"event": event_name}

        # Check for data request: {"command": "getData", "id": 1234, ...}
        if "command" in msg and "id" in msg:
            return "data_request", msg

        if self.debug:
            print(f"  [WS] unknown message format: {msg}")
        return "unknown", msg

    async def _handle_connection(self, websocket):
        """Handle a single Artisan WebSocket connection."""
        # Log connection path for debugging (Artisan uses /WebSocket)
        path = getattr(websocket, "path", None) or getattr(
            getattr(websocket, "request", None), "path", "/"
        )
        if self.debug:
            print(f"  [WS] client connected on path: {path}")

        self.connected = True
        self.data_request_count = 0
        if self._on_connect:
            self._on_connect()

        try:
            async for raw_message in websocket:
                msg_type, data = self._parse_message(raw_message)

                if msg_type == "event":
                    event_name = data.get("event", "")
                    if event_name in ROAST_EVENTS:
                        self._handle_event(event_name)
                    elif self.debug:
                        print(f"  [WS] unrecognized event: {event_name!r}")

                elif msg_type == "data_request":
                    # Respond with dummy data to keep connection alive.
                    # Artisan expects matching message ID.
                    self.data_request_count += 1
                    msg_id = data.get("id", 0)
                    response = {
                        "id": msg_id,
                        "data": {"BT": 0, "ET": 0},
                    }
                    await websocket.send(json.dumps(response))

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.connected = False
            if self._on_disconnect:
                self._on_disconnect()

    async def start(self):
        """Start the WebSocket server (async)."""
        self._stop_event.clear()
        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
        )

    async def wait_until_stopped(self):
        """Block until stop() is called."""
        await self._stop_event.wait()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def stop(self):
        """Signal the server to stop."""
        self._stop_event.set()

    def reset(self):
        """Reset event tracking for a new roast session."""
        self.events = {}
        self.charge_time = None
        self.current_phase = None
