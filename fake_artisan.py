#!/usr/bin/env python3
"""Simulate Artisan WebSocket client for testing the sentinel.

Behaves like real Artisan 4.0:
  1. Connects to ws://localhost:8765/WebSocket (matches Artisan's path)
  2. Sends getData polling every 2 seconds (keeps connection alive)
  3. Sends roast events on a timeline (--fast for ~30s, default ~10min)

The polling loop runs concurrently with the event timeline, just like
real Artisan which polls for temperature data while also sending events.

Usage:
    .venv/bin/python fake_artisan.py           # realistic 10-min timeline
    .venv/bin/python fake_artisan.py --fast     # compressed ~30s timeline
    .venv/bin/python fake_artisan.py --native   # use Artisan native event names
"""

import argparse
import asyncio
import json
import websockets

# Realistic 10-minute roast timeline (seconds from ON)
# Typical Hottop medium roast profile
# DROP is sent via COOL END button (Hottop occupies DROP's action slot)
EVENTS_REAL = [
    (3,    "START"),
    (8,    "CHARGE"),     # T+0:00 — beans in
    (278,  "DRY"),        # T+4:30 — drying ends, maillard begins
    (458,  "FCs"),        # T+7:30 — first crack starts
    (518,  "FCe"),        # T+8:30 — first crack ends
    (578,  "DROP"),       # T+9:30 — sent by COOL END button
]

# Compressed ~30s timeline for quick testing
EVENTS_FAST = [
    (1,  "START"),
    (2,  "CHARGE"),
    (8,  "DRY"),
    (15, "FCs"),
    (20, "FCe"),
    (25, "DROP"),         # sent by COOL END button
]

# Native Artisan event names (sent when event tags are configured)
NATIVE_EVENTS = [
    (3,  "START"),
    (8,  "chargeEvent"),
    (278, "colorChangeEvent"),
    (458, "FirstCrackBeginningEvent"),
    (518, "FirstCrackEndEvent"),
    (578, "coolEvent"),   # COOL END native tag → maps to DROP
]

# Polling interval (Artisan default is ~2s)
POLL_INTERVAL = 2.0


async def poll_data(ws, stop_event):
    """Send getData requests like real Artisan does on every sampling interval."""
    msg_id = 1
    while not stop_event.is_set():
        request = {"command": "getData", "id": msg_id, "machine": 0}
        try:
            await ws.send(json.dumps(request))
            # Read the response
            resp = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(resp)
            # Only print first and every 10th poll to avoid spam
            if msg_id == 1 or msg_id % 10 == 0:
                print(f"  Poll #{msg_id}: BT={data.get('data', {}).get('BT', '?')}")
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            break
        msg_id += 1
        await asyncio.sleep(POLL_INTERVAL)


async def send_events(ws, events, stop_event):
    """Send roast events on the compressed timeline."""
    start = asyncio.get_event_loop().time()

    for delay, event in events:
        # Wait until the right time
        elapsed = asyncio.get_event_loop().time() - start
        wait = delay - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

        # Send the event (button send() format or native message format)
        if "Event" in event:
            # Native Artisan format
            msg = json.dumps({"message": event})
        else:
            # Standard send() format from button actions
            msg = json.dumps({"event": event})
        await ws.send(msg)
        print(f"  >>> {event}")

    # Let polling continue a bit after DROP
    print()
    print("  All events sent. Polling for 3 more seconds...")
    await asyncio.sleep(3)
    stop_event.set()


async def main():
    parser = argparse.ArgumentParser(description="Fake Artisan WebSocket client")
    parser.add_argument("--fast", action="store_true",
                        help="Use compressed ~30s timeline instead of realistic 10min")
    parser.add_argument("--native", action="store_true",
                        help="Use Artisan native event names (chargeEvent, etc.)")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket port")
    args = parser.parse_args()

    # Select event timeline
    if args.native:
        events = NATIVE_EVENTS
        mode = "native event names (~10min)"
    elif args.fast:
        events = EVENTS_FAST
        mode = "fast (~30s)"
    else:
        events = EVENTS_REAL
        mode = "realistic (~10min)"

    url = f"ws://localhost:{args.port}/WebSocket"

    # Show timeline preview
    print(f"Fake Artisan — connecting to {url}")
    print(f"  Mode: {mode}")
    print(f"  Timeline:")
    for t, name in events:
        m, s = divmod(t, 60)
        print(f"    {m}:{s:02d}  {name}")
    print()

    async with websockets.connect(url) as ws:
        print("Connected!")
        print()

        stop_event = asyncio.Event()

        # Run polling and events concurrently
        await asyncio.gather(
            poll_data(ws, stop_event),
            send_events(ws, events, stop_event),
        )

    print("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
