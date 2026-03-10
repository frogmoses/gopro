#!/usr/bin/env python3
"""GoPro Hero 13 roast sentinel — vision monitoring for coffee roasting.

Captures images via GoPro over USB, analyzes with Claude Vision API.
Replaces r1-eye's ADB-based capture with Open GoPro SDK.

Commands:
    run_gopro gopro.py ask "What's on my desk?"        # Capture + AI analysis
    run_gopro gopro.py look                             # Capture only (test camera)
    run_gopro gopro.py status                           # Check camera status

    run_gopro gopro.py sentinel start [--bean NAME]     # Start roast sentinel
    run_gopro gopro.py sentinel status                  # Current observations
    run_gopro gopro.py sentinel log                     # Full session log
"""

import argparse
import asyncio
import sys
from pathlib import Path

import gopro_bridge
import vision_client
import sentinel


# Box-drawing characters for status display
H_LINE = "\u2500"
V_LINE = "\u2502"
TL_CORNER = "\u250c"
TR_CORNER = "\u2510"
BL_CORNER = "\u2514"
BR_CORNER = "\u2518"
T_RIGHT = "\u251c"
T_LEFT = "\u2524"


def _box_header(title, width=50):
    """Create a boxed header line."""
    padding = width - len(title) - 4
    return f"{TL_CORNER}{H_LINE} {title} {H_LINE * padding}{TR_CORNER}"


def _box_row(label, value, width=50):
    """Create a box row with label and value."""
    content = f" {label}: {value}"
    padding = width - len(content) - 2
    return f"{V_LINE}{content}{' ' * max(padding, 0)} {V_LINE}"


def _box_footer(width=50):
    """Create a box footer line."""
    return f"{BL_CORNER}{H_LINE * (width - 2)}{BR_CORNER}"


def _box_separator(width=50):
    """Create an inner separator line."""
    return f"{T_RIGHT}{H_LINE * (width - 2)}{T_LEFT}"


# ── Command handlers ──────────────────────────────────────────


async def cmd_ask(args):
    """Capture an image and ask Claude about it."""
    question = args.question

    # Check device first
    if not await gopro_bridge.is_connected():
        print("Error: No GoPro connected via USB")
        sys.exit(1)

    # Capture image
    print("Capturing image...")
    image_path = await gopro_bridge.capture_image()
    if not image_path:
        print("Error: Failed to capture image")
        sys.exit(1)

    print(f"Captured: {image_path}")

    # Send to Claude Vision
    print("Asking Claude...")
    response = vision_client.ask_about_image(image_path, question)
    if not response:
        print("Error: No response from Claude Vision API")
        sys.exit(1)

    # Display the answer
    print()
    print(response)


async def cmd_look(args):
    """Capture an image without AI analysis (camera test)."""
    if not await gopro_bridge.is_connected():
        print("Error: No GoPro connected via USB")
        sys.exit(1)

    print("Capturing image...")
    image_path = await gopro_bridge.capture_image()
    if not image_path:
        print("Error: Failed to capture image")
        sys.exit(1)

    # Report file info
    size_kb = image_path.stat().st_size / 1024
    print(f"Saved: {image_path} ({size_kb:.1f} KB)")


async def cmd_status(args):
    """Show camera connectivity and status."""
    status = await gopro_bridge.device_status()

    if status is None:
        print("No GoPro connected via USB")
        print()
        print("Troubleshooting:")
        print("  1. Check USB-C cable connection")
        print("  2. Ensure camera is powered on")
        print("  3. Verify USB control mode is enabled on camera")
        sys.exit(1)

    # Display status in a box
    w = 50
    lines = [
        _box_header("GoPro Status", w),
        _box_row("Serial", status["serial"] or "unknown", w),
        _box_row("Model", status["model"], w),
        _box_row("Battery", f'{status["battery_percent"]}%', w),
        _box_separator(w),
        _box_row("Connection", status["connection"], w),
        _box_footer(w),
    ]
    print("\n".join(lines))


def cmd_sentinel(args):
    """Roast sentinel — vision monitoring synced to Artisan."""
    if not hasattr(args, "sentinel_command") or not args.sentinel_command:
        print("Sentinel subcommands: start, status, log")
        print()
        print("  start [--bean NAME] [--port PORT]  Start sentinel")
        print("  status                              Last session status")
        print("  log                                 Last session log")
        sys.exit(1)

    if args.sentinel_command == "start":
        sentinel.start_sentinel(
            bean_name=args.bean,
            enable_crack=args.crack,
            ws_port=args.port,
            debug=getattr(args, "debug", False),
        )
    elif args.sentinel_command == "status":
        sentinel.show_sentinel_status()
    elif args.sentinel_command == "log":
        sentinel.show_latest_log()


# ── Main ──────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="GoPro Hero 13 roast sentinel — vision monitoring for coffee roasting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  run_gopro gopro.py ask "What do you see?"                    # AI vision query\n'
            "  run_gopro gopro.py look                                       # Test capture\n"
            "  run_gopro gopro.py status                                     # Camera status\n"
            '  run_gopro gopro.py sentinel start --bean "Ethiopia"           # Start sentinel\n'
            "  run_gopro gopro.py sentinel log                               # View session log\n"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ask — capture + AI query
    p_ask = subparsers.add_parser("ask", help="Ask about what the camera sees")
    p_ask.add_argument("question", help="Question to ask about the image")

    # look — capture only
    subparsers.add_parser("look", help="Capture image only (camera test)")

    # status — camera connectivity
    subparsers.add_parser("status", help="Check GoPro camera status")

    # Sentinel subcommands
    p_sentinel = subparsers.add_parser("sentinel", help="Roast sentinel with Artisan sync")
    sentinel_sub = p_sentinel.add_subparsers(dest="sentinel_command")
    p_sentinel_start = sentinel_sub.add_parser("start", help="Start roast sentinel")
    p_sentinel_start.add_argument("--bean", help="Bean name for the roast")
    p_sentinel_start.add_argument("--port", type=int, default=8765, help="WebSocket port (default: 8765)")
    p_sentinel_start.add_argument("--crack", action="store_true", help="Enable crack detection (Phase 3)")
    p_sentinel_start.add_argument("--debug", action="store_true", help="Log raw WebSocket messages")
    sentinel_sub.add_parser("status", help="Last sentinel session status")
    sentinel_sub.add_parser("log", help="Last sentinel session log")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Async commands dispatched through asyncio.run()
    async_commands = {
        "ask": cmd_ask,
        "look": cmd_look,
        "status": cmd_status,
    }

    # Sync commands
    sync_commands = {
        "sentinel": cmd_sentinel,
    }

    if args.command in async_commands:
        handler = async_commands[args.command]
        try:
            asyncio.run(handler(args))
        except KeyboardInterrupt:
            print("\nInterrupted.")
            sys.exit(130)
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
    elif args.command in sync_commands:
        handler = sync_commands[args.command]
        try:
            handler(args)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            sys.exit(130)
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
