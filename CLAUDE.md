# GoPro Roast Sentinel — AI Agent Reference

## CRITICAL: Security Protocol for Development

**BEFORE writing any code that requires credentials, API keys, or environment variables:**

1. 🔴 **MANDATORY**: Read `~/ClaudeWorkspace/.claude/docs/code-secure.md` completely
2. 🔴 **MANDATORY**: Follow the code-secure.md checklist exactly
3. 🔴 **MANDATORY**: Create `.env.example` file (never actual `.env`)
4. 🔴 **MANDATORY**: Create wrapper script first
5. 🔴 **MANDATORY**: Python code NEVER loads .env files (only `os.environ.get()`)
6. 🔴 **MANDATORY**: You must never access .env files in any way.

**Failure to follow this protocol is a security violation.**

See `~/ClaudeWorkspace/.claude/docs/code-secure.md` for complete implementation details.

See [README.md](README.md) for overview, setup, and usage.

## Running

Always use the wrapper script (injects ANTHROPIC_API_KEY):
```bash
run_gopro gopro.py <command>
```

On the roaster machine, the wrapper isn't on PATH — use full path and run from the project directory:
```bash
cd ~/CodeProjects/gopro
~/.local/bin/run_gopro gopro.py <command>
```

## Repository Structure

```
gopro.py              CLI entry point (argparse)
gopro_bridge.py       GoPro device layer (async, Open GoPro SDK)
sentinel.py           Roast monitoring orchestrator
sentinel_display.py   Live terminal UI (box-drawing)
artisan_sync.py       Artisan WebSocket server
vision_client.py      Claude Vision API client
fake_artisan.py       Test: simulated Artisan client
sim_sentinel.py       Test: full pipeline with reference images
deploy.sh             Rsync deploy to roaster machine
.env.example          Environment variable template
pyproject.toml        uv project config + dependencies
reference/            Reference bean images for simulation (7 images, green → full city)
captures/             Local image + log storage (gitignored)
.github/workflows/    Gitleaks secret scanning
```

## CLI → Module → Function Mapping

### `gopro.py` entry point

| Command | Handler | Calls |
|---------|---------|-------|
| `ask "question"` | `cmd_ask()` (async) | `gopro_bridge.capture_image()` → `vision_client.ask_about_image()` |
| `look` | `cmd_look()` (async) | `gopro_bridge.capture_image()` |
| `status` | `cmd_status()` (async) | `gopro_bridge.device_status()` |
| `sentinel start` | `cmd_sentinel()` (sync) | `sentinel.start_sentinel()` → `SentinelSession.run()` |
| `sentinel status` | `cmd_sentinel()` | `sentinel.show_sentinel_status()` |
| `sentinel log` | `cmd_sentinel()` | `sentinel.show_latest_log()` |

### Sentinel start flags

| Flag | Default | Effect |
|------|---------|--------|
| `--bean NAME` | "Unknown" | Bean name in session log |
| `--crack` | off | Enable crack detection (Phase 3, experimental) |
| `--port PORT` | 8765 | WebSocket server port |
| `--debug` | off | Log raw WebSocket messages to stdout |

## Data Flow

```
Artisan → artisan_sync.py (WebSocket events) → sentinel.py (orchestrator)
                                                     ↓
GoPro ← gopro_bridge.py (USB capture) ← sentinel._capture_and_analyze()
                                                     ↓
                                          vision_client.py (Claude API)
                                                     ↓
                                          sentinel_display.py (terminal UI)
                                                     ↓
                                          captures/sentinel_*.json (log)
```

### Sentinel session lifecycle

1. `start_sentinel()` creates `SentinelSession`, calls `asyncio.run(session.run())`
2. `run()` verifies GoPro connected, starts WebSocket server
3. Waits for Artisan connect (ON button), then calls `gopro_bridge.start_camera_session()`
4. Waits for CHARGE event to start capture loop
5. Main loop: event-triggered + timed captures → `_capture_and_analyze()`
6. Each capture: `gopro_bridge.quick_capture()` → `vision_client.assess_roast_color()`
7. DROP or Ctrl+C: `end_camera_session()`, save log, rsync to dev machine

## Key Parameters and Locations

### Capture intervals — `sentinel.py:29-34`
```python
CAPTURE_INTERVALS = {"drying": 30, "maillard": 20, "development": 10, "cooling": 0}
```

### Post-processing — `gopro_bridge.py:35-38, 73-130`
```python
CROP_TOP = 0.05, CROP_BOTTOM = 0.95, CROP_LEFT = 0.05, CROP_RIGHT = 0.95
MAX_EDGE = 2048  # line 101
# Contrast: enhance(1.3) — line 114
# Unsharp mask: radius=2, percent=150, threshold=3 — line 119
```

### Vision model — `vision_client.py:15`
```python
VISION_MODEL = "claude-sonnet-4-5-20250929"
```

### GoPro serial — `gopro_bridge.py:27`
```python
SERIAL = os.environ.get("GOPRO_SERIAL")  # last 3 digits, from .env
```

### WebSocket server — `artisan_sync.py:37-38`
```python
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
```

### Log push destination — `sentinel.py`
```python
dest = os.environ.get("SENTINEL_RSYNC_DEST")  # e.g. user@host:~/path/captures/
```

## GoPro Bridge Functions

### One-shot (for `ask`, `look` commands)

| Function | Description |
|----------|-------------|
| `capture_image(filename)` | Open → configure → capture → download → close → post-process |
| `device_status()` | Open temp connection, read battery/model |
| `is_connected()` | Open temp connection, verify reachable |

### Session-based (for sentinel mode)

| Function | Description |
|----------|-------------|
| `start_camera_session()` | Open connection, load photo preset, set LINEAR_27_MP lens, disable auto power down |
| `quick_capture(filename)` | Shutter → get media → download → delete from SD → post-process |
| `end_camera_session()` | Close connection |
| `is_camera_session_active()` | Check if session open (sync, no await) |

### Post-processing pipeline (`post_process()`)

1. **Crop** — 5% all sides (removes edges, no device housing in frame)
2. **Resize** — max 2048px long edge (LANCZOS downscale, keeps ~1MB JPEG)
3. **Gray world white balance** — `_auto_white_balance()`, normalizes RGB channel means
4. **Contrast** — 30% boost via `ImageEnhance.Contrast(1.3)`
5. **Unsharp mask** — `radius=2, percent=150, threshold=3`

Crop constants will need tuning once mounted over the roaster.

## Device Details (GoPro Hero 13)

- **Hero 13 mDNS bug:** Camera doesn't advertise `_gopro-web` service, so serial must be passed manually to `WiredGoPro(serial)`
- **SDK transport:** HTTP over USB NCM ethernet adapter (172.2X.1YZ.51:8080)
- **403 on open:** SDK logs a non-fatal 403 during connection — this is normal

### Capture flow (SDK API)

```python
await gopro.http_command.set_shutter(shutter=Toggle.ENABLE)      # take photo
media = (await gopro.http_command.get_last_captured_media()).data  # MediaPath
await gopro.http_command.download_file(camera_file=media.as_path) # download
await gopro.http_command.delete_file(path=media.as_path)          # free SD
```

SDK details:
- `get_last_captured_media()` returns `MediaPath` with `.folder`, `.file`, `.as_path` (NOT `.filename`)
- `download_file()` takes `camera_file=` parameter
- `delete_file()` takes `path=` parameter (different from download)

### Camera settings (`start_camera_session()`)

- Photo preset group: `EnumPresetGroup.PRESET_GROUP_ID_PHOTO`
- Photo lens: `PhotoLens.LINEAR_27_MP` (Hero 13 only accepts `LINEAR_27_MP` and `WIDE_27_MP` — generic `LINEAR` returns failure)
- Auto power down: `AutoPowerDown.NEVER`

## Artisan WebSocket Protocol

Sentinel runs a WebSocket **server** on port 8765. Artisan connects as a client.

### Artisan config: Config > Ports > WebSocket tab

| Field | Value |
|-------|-------|
| Host | `127.0.0.1` |
| Port | `8765` |
| Path | `WebSocket` |

### Button WebSocket Command actions

| Button | Action | Notes |
|--------|--------|-------|
| ON | `send({"event": "ON"})` | Establishes WebSocket connection |
| START | `send({"event": "START"})` | Logged, doesn't change phase |
| CHARGE | `send({"event": "CHARGE"})` | T=0 reference, starts capture loop |
| DRY | `send({"event": "DRY"})` | Phase → maillard |
| FCs | `send({"event": "FCs"})` | Phase → development |
| FCe | `send({"event": "FCe"})` | |
| DROP | N/A | Hottop Command uses this slot |
| COOL END | `send({"event": "DROP"})` | Sends DROP, ends session |

DROP's action slot is used by Hottop safety commands. COOL END is configured to send DROP instead.

### Message formats (`artisan_sync.py`)

- Button events: `{"event": "CHARGE"}`
- Native events: `{"message": "chargeEvent"}` (mapped via `ARTISAN_EVENT_MAP`)
- Data polling: `{"command": "getData", "id": N}` (responded with dummy BT/ET data)

### Phase mapping (`artisan_sync.py:70-78`)

| Event | Phase |
|-------|-------|
| CHARGE | drying |
| DRY | maillard |
| FCs/FCe/SCs/SCe | development |
| DROP | cooling |

## Sentinel Capture Logic

Two triggers run simultaneously:

1. **Event-triggered** — immediate capture on CHARGE, DRY, FCs, FCe, SCs, SCe, DROP
2. **Timed interval** — phase-adaptive (drying: 30s, maillard: 20s, development: 10s, cooling: none)

Camera opens on Artisan connect (ON/START), before CHARGE. Capture loop starts at CHARGE. Session ends on DROP or Ctrl+C. Partial logs are saved on interrupt.

## Session Log Schema

Saved to `captures/sentinel_YYYY-MM-DD_HHMM.json`:

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

Development score scale (1-10): green → pale yellow → tan → cinnamon → city → full city → dark → Vienna → French → Italian. Defined in `vision_client.py:149-160`.

## Coding Conventions

- No Python typing (per workspace CLAUDE.md)
- Always provide comments
- Use `uv` for package management (`uv add`, not pip)
- Secrets via `run_gopro` wrapper, never in code — Python reads only `os.environ.get()`
- All bridge calls are `async` (GoPro SDK is asyncio-based)

## Roaster Deployment

- Roaster machine: configure SSH alias in `~/.ssh/config`, set `DEPLOY_SSH_HOST` in `.env`
- Deploy: `./deploy.sh` (quick) or `./deploy.sh --full` (with deps)
- Secrets at `~/.config/code-projects/gopro/.env` on roaster
- Wrapper at `~/.local/bin/run_gopro` (not on PATH — use full path)
- Logs pushed back to dev machine via rsync after DROP (set `SENTINEL_RSYNC_DEST`)

## Differences from r1-eye

| Aspect | r1-eye | gopro |
|--------|--------|-------|
| Device | Rabbit R1 (jailbroken) | GoPro Hero 13 |
| Connection | WiFi ADB | USB-C wired (HTTP over NCM) |
| Capture | Screen tap + shutter (fragile) | SDK HTTP command (reliable) |
| Resolution | 8MP (3264x2448) | 27MP, resized to 2048px for API |
| Bridge | Sync (`adb_bridge.py`) | Async (`gopro_bridge.py`) |
| Live stream | scrcpy (optional) | Not implemented (use GoPro LCD) |
| Motor control | sysfs step motor | N/A |
