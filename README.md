# GoPro Hero 13 Roast Sentinel

Vision monitoring for coffee roasting using a GoPro Hero 13 over USB. Captures images during a roast, analyzes bean color with Claude Vision API, and logs timestamped observations synced to [Artisan](https://artisan-scope.org/) roast events.

Standalone alternative to [r1-eye](https://github.com/frogmoses/r1-eye) (jailbroken Rabbit R1 over WiFi ADB). Session log format is identical, so [coffee-roasting](https://github.com/frogmoses/coffee-roasting) analysis pipeline consumes logs from either.

## Roast Sentinel

Start the sentinel, then press ON in Artisan to connect:

```bash
run_gopro gopro.py sentinel start --bean "Ethiopia Yirgacheffe"
```

The sentinel captures images throughout the roast, adapting frequency by phase (30s drying, 20s maillard, 10s development). Each image is analyzed by Claude Vision for bean color assessment. A live terminal UI shows development score and color in real time.

Session log saved to `captures/sentinel_YYYY-MM-DD_HHMM.json` on DROP or Ctrl+C.

### Sentinel Options

| Flag | Description |
|------|-------------|
| `--bean NAME` | Bean name for the session log |
| `--crack` | Enable crack detection (Phase 3, experimental) |
| `--port PORT` | WebSocket port (default: 8765) |
| `--debug` | Log raw WebSocket messages |

### Review Past Sessions

```bash
run_gopro gopro.py sentinel status    # Last session summary
run_gopro gopro.py sentinel log       # Full observation log
```

## Quick Commands

```bash
run_gopro gopro.py look               # Capture photo (camera test)
run_gopro gopro.py ask "What do you see?"  # Capture + Claude Vision
run_gopro gopro.py status             # Camera status + battery
```

## Testing Without Hardware

Run the simulation with reference bean images and real Claude Vision API (mocks the GoPro):

```bash
# Terminal 1
run_gopro sim_sentinel.py

# Terminal 2
.venv/bin/python fake_artisan.py --fast    # 30-second compressed roast
```

`fake_artisan.py` also accepts `--native` (Artisan native event tags) and `--port PORT`.

## Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Security bootstrap completed (`run_gopro` wrapper exists)
- `ANTHROPIC_API_KEY` in `~/.config/code-projects/gopro/.env`

### Install

```bash
cd ~/CodeProjects/gopro
uv venv .venv --seed
uv add open-gopro anthropic numpy pillow websockets
```

### Verify Connection

Plug in the GoPro via USB-C, power it on, then:

```bash
run_gopro gopro.py status
```

### Hardware

- **GoPro Hero 13 Black** — stays powered via USB, no battery concerns
- **USB-C cable** to roaster machine
- **Hottop KN-8828B-2K+** with Artisan 4.0

### Artisan Configuration

In Artisan: **Config > Ports > WebSocket tab**

| Field | Value |
|-------|-------|
| Host | `127.0.0.1` |
| Port | `8765` |
| Path | `WebSocket` |

Configure button WebSocket Command actions for ON, START, CHARGE, DRY, FCs, FCe, and COOL END (sends DROP). See [CLAUDE.md](CLAUDE.md) for the full button mapping.

## Deployment

Deploy to the roaster machine via rsync:

```bash
./deploy.sh          # Quick: .py files only (~1s)
./deploy.sh --full   # Full: all files + reinstall deps
```

On the roaster, run from the project directory with the full wrapper path:

```bash
cd ~/CodeProjects/gopro
~/.local/bin/run_gopro gopro.py sentinel start --bean "Ethiopia Yirgacheffe"
```

## Session Log Format

```json
{
  "session_id": "2026-02-28_1518",
  "bean_name": "Ethiopia Yirgacheffe",
  "artisan_events": {"charge": 0.0, "dry": 270.5, "fcs": 450.2, "drop": 570.8},
  "observations": [
    {
      "elapsed_seconds": 1.5,
      "phase": "drying",
      "type": "vision",
      "image_file": "captures/sentinel_20260228_151800.jpg",
      "color_assessment": "Pale green, raw unroasted beans",
      "development_score": 1,
      "uniformity": "Consistent color across all visible beans"
    }
  ]
}
```

For code-level details, SDK API notes, and tuning parameters, see [CLAUDE.md](CLAUDE.md).
