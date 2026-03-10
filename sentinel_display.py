"""Terminal display for roast sentinel sessions.

Live-updating box-drawing display showing roast phase, elapsed time,
latest color assessment, crack status, and Artisan event log.
Matches the style of coffee-roasting/roast_display.py.
"""

import os
import sys

# Box-drawing characters
H_LINE = "\u2500"     # ─
V_LINE = "\u2502"     # │
TL_CORNER = "\u250c"  # ┌
TR_CORNER = "\u2510"  # ┐
BL_CORNER = "\u2514"  # └
BR_CORNER = "\u2518"  # ┘
T_RIGHT = "\u251c"    # ├
T_LEFT = "\u2524"     # ┤

# Progress bar characters
BLOCK_FULL = "\u2588"   # █
BLOCK_LIGHT = "\u2591"  # ░

# Display width
WIDTH = 62


def fmt_time(seconds):
    """Format seconds as M:SS."""
    if seconds is None:
        return "--:--"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"


def _header(title):
    """Create a boxed header line."""
    padding = WIDTH - len(title) - 4
    return f"{TL_CORNER}{H_LINE} {title} {H_LINE * padding}{TR_CORNER}"


def _footer():
    """Create a box footer line."""
    return f"{BL_CORNER}{H_LINE * (WIDTH - 2)}{BR_CORNER}"


def _row(left, right=""):
    """Create a box row with left and optional right content."""
    content = f" {left}"
    if right:
        pad = WIDTH - len(content) - len(str(right)) - 3
        content = f" {left}{' ' * max(pad, 1)}{right}"
    padding = WIDTH - len(content) - 2
    return f"{V_LINE}{content}{' ' * max(padding, 0)} {V_LINE}"


def _separator():
    """Create an inner separator line."""
    return f"{T_RIGHT}{H_LINE * (WIDTH - 2)}{T_LEFT}"


def _empty_row():
    """Create an empty content row."""
    return f"{V_LINE}{' ' * (WIDTH - 2)}{V_LINE}"


def _phase_bar(phase):
    """Create a visual phase indicator bar.

    Args:
        phase: Current phase string (drying/maillard/development/cooling).

    Returns:
        Formatted phase bar string.
    """
    phases = ["drying", "maillard", "development", "cooling"]
    labels = ["DRY", "MAI", "DEV", "COOL"]

    parts = []
    for i, (p, label) in enumerate(zip(phases, labels)):
        if p == phase:
            parts.append(f"[{label}]")
        else:
            parts.append(f" {label} ")

    return " → ".join(parts)


def render_status(session):
    """Render the live sentinel status display.

    Args:
        session: Dict with current session state:
            - bean_name: str
            - elapsed: float or None (seconds since CHARGE)
            - phase: str or None
            - connected: bool (Artisan WebSocket connected)
            - events: dict of event_name → elapsed_seconds
            - latest_observation: dict or None (most recent vision result)
            - crack_status: dict or None (crack detection info)
            - capture_count: int

    Returns:
        Multi-line string for terminal display.
    """
    lines = []

    # Header
    bean = session.get("bean_name", "Unknown")
    lines.append(_header(f"GoPro Sentinel: {bean}"))

    # Connection + timing row
    connected = session.get("connected", False)
    conn_str = "CONNECTED" if connected else "WAITING..."
    elapsed = session.get("elapsed")
    time_str = fmt_time(elapsed)
    lines.append(_row(f"Artisan: {conn_str}", f"T+{time_str}"))

    # Phase bar
    phase = session.get("phase")
    if phase:
        lines.append(_row(_phase_bar(phase)))
    else:
        lines.append(_row("Phase: waiting for CHARGE"))

    lines.append(_separator())

    # Artisan events
    events = session.get("events", {})
    if events:
        event_parts = []
        for name in ["CHARGE", "DRY", "FCs", "FCe", "SCs", "SCe", "DROP"]:
            if name in events:
                event_parts.append(f"{name} {fmt_time(events[name])}")
        lines.append(_row("Events: " + " | ".join(event_parts)))
    else:
        lines.append(_row("Events: none"))

    lines.append(_separator())

    # Latest vision observation
    obs = session.get("latest_observation")
    if obs:
        color = obs.get("color_assessment", "")
        score = obs.get("development_score", 0)
        # Truncate long color descriptions
        if len(color) > 45:
            color = color[:42] + "..."
        lines.append(_row(f"Color: {color}"))

        # Development score bar
        filled = min(score, 10)
        bar = BLOCK_FULL * filled + BLOCK_LIGHT * (10 - filled)
        lines.append(_row(f"Development: [{bar}] {score}/10"))

        uniformity = obs.get("uniformity", "")
        if uniformity:
            if len(uniformity) > 45:
                uniformity = uniformity[:42] + "..."
            lines.append(_row(f"Uniformity: {uniformity}"))
    else:
        lines.append(_row("Vision: waiting for first capture..."))

    # Crack detection status (Phase 3)
    crack = session.get("crack_status")
    if crack:
        lines.append(_separator())
        crack_type = crack.get("crack_type", "")
        cpm = crack.get("cracks_per_minute", 0)
        crack_time = crack.get("elapsed_seconds")
        lines.append(_row(
            f"Crack: {crack_type.upper()} detected at T+{fmt_time(crack_time)}",
            f"{cpm} cracks/min",
        ))

    lines.append(_separator())

    # Capture count
    count = session.get("capture_count", 0)
    lines.append(_row(f"Captures: {count}"))

    lines.append(_footer())

    return "\n".join(lines)


def render_log(observations):
    """Render the full session observation log.

    Args:
        observations: List of observation dicts from the sentinel log.

    Returns:
        Formatted string showing timestamped observations.
    """
    lines = []
    lines.append(_header("Sentinel Log"))

    if not observations:
        lines.append(_row("No observations recorded"))
        lines.append(_footer())
        return "\n".join(lines)

    for obs in observations:
        elapsed = obs.get("elapsed_seconds", 0)
        obs_type = obs.get("type", "unknown")
        phase = obs.get("phase", "")
        time_str = fmt_time(elapsed)

        if obs_type == "vision":
            color = obs.get("color_assessment", "")
            score = obs.get("development_score", 0)
            if len(color) > 40:
                color = color[:37] + "..."
            lines.append(_row(f"T+{time_str} [{phase[:3].upper()}]", f"{color} ({score}/10)"))

        elif obs_type == "crack":
            crack_type = obs.get("crack_type", "")
            cpm = obs.get("cracks_per_minute", 0)
            lines.append(_row(f"T+{time_str} [{phase[:3].upper()}]", f"{crack_type.upper()} {cpm}cpm"))

    lines.append(_separator())
    lines.append(_row(f"Total observations: {len(observations)}"))
    lines.append(_footer())

    return "\n".join(lines)


def clear_and_render(session):
    """Clear terminal and render status (for live updates).

    Args:
        session: Session state dict (see render_status).
    """
    # ANSI escape: move cursor to top-left and clear screen
    sys.stdout.write("\033[H\033[J")
    sys.stdout.write(render_status(session))
    sys.stdout.write("\n")
    sys.stdout.flush()
