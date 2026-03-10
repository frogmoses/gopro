#!/usr/bin/env python3
"""Simulate a sentinel session using reference images + real Claude Vision.

Mocks GoPro captures to return reference bean images based on roast
progress. Uses the REAL vision_client (Claude API) so you see actual
AI color assessments against known reference photos.

Reference images are mapped to roast elapsed time:
    0:00 - 1:00   green (75°F)
    1:00 - 4:00   drying (330°F)
    4:00 - 6:00   cinnamon (385°F)
    6:00 - 7:30   new england (400°F)
    7:30 - 8:30   american (410°F)
    8:30 - 9:30   city (425°F)
    9:30+         full city (440°F)

Usage:
    # Terminal 1: start simulated sentinel
    run_gopro sim_sentinel.py

    # Terminal 2: run fake Artisan
    .venv/bin/python fake_artisan.py          # 10-min realistic
    .venv/bin/python fake_artisan.py --fast    # 30s compressed
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Reference image directory (symlink to r1-eye/reference/)
REF_DIR = Path(__file__).parent / "reference"

# All reference images (for validation)
REF_IMAGES = [
    "75_degrees_green_coffee.png",
    "330_degrees_drying_coffee.png",
    "385_degrees_cinnamon_roast_coffee.png",
    "400_degrees_new_england_roast_coffee.png",
    "410_degrees_american_roast_coffee.png",
    "425_degrees_city_roast_coffee.png",
    "440_degrees_full_city_roast_coffee.png",
]

# Reference images selected by phase + progress within that phase.
# Each phase has a list of (fraction_threshold, image) pairs.
# Fraction = how far through the phase (0.0 = just started, 1.0 = about to end).
# This works regardless of timeline speed.
PHASE_IMAGES = {
    None: [
        (1.0, "75_degrees_green_coffee.png"),
    ],
    "drying": [
        (0.3, "75_degrees_green_coffee.png"),           # early drying: still green
        (0.7, "330_degrees_drying_coffee.png"),          # mid drying: yellowing
        (1.0, "385_degrees_cinnamon_roast_coffee.png"),  # late drying: cinnamon
    ],
    "maillard": [
        (0.5, "400_degrees_new_england_roast_coffee.png"),  # early maillard
        (1.0, "410_degrees_american_roast_coffee.png"),      # late maillard
    ],
    "development": [
        (0.5, "425_degrees_city_roast_coffee.png"),          # early development
        (1.0, "440_degrees_full_city_roast_coffee.png"),     # late development
    ],
    "cooling": [
        (1.0, "440_degrees_full_city_roast_coffee.png"),
    ],
}

# Expected phase durations in a typical roast (seconds).
# Used to calculate progress fraction within each phase.
PHASE_DURATIONS = {
    "drying": 270,      # CHARGE → DRY (~4:30)
    "maillard": 180,    # DRY → FCs (~3:00)
    "development": 120, # FCs → DROP (~2:00)
    "cooling": 60,
}


def _select_reference_image(phase, elapsed):
    """Pick the reference image matching roast phase and progress.

    Uses elapsed time since CHARGE to estimate how far through the
    current phase we are, then selects the appropriate image.
    """
    images = PHASE_IMAGES.get(phase, PHASE_IMAGES[None])

    # For phases with multiple images, calculate progress fraction
    if phase in PHASE_DURATIONS and elapsed is not None:
        # Estimate phase start time from expected durations
        phase_order = [None, "drying", "maillard", "development", "cooling"]
        phase_start = 0
        for p in phase_order[1:]:
            if p == phase:
                break
            phase_start += PHASE_DURATIONS.get(p, 0)

        time_in_phase = max(0, elapsed - phase_start)
        duration = PHASE_DURATIONS[phase]
        fraction = min(time_in_phase / duration, 1.0)

        for threshold, filename in images:
            if fraction <= threshold:
                return REF_DIR / filename

    # Default to first image for this phase
    return REF_DIR / images[0][1]


# Track what images we served
capture_log = []

# Mock gopro_bridge — returns reference images instead of real captures.
# GoPro bridge methods are async, so we use AsyncMock for them.
mock_gopro = MagicMock()
mock_gopro.is_connected = AsyncMock(return_value=True)
mock_gopro.is_camera_session_active.return_value = True
mock_gopro.start_camera_session = AsyncMock(return_value=None)
mock_gopro.end_camera_session = AsyncMock(return_value=None)

# Current roast state — updated by patched _capture_and_analyze
_current_phase = [None]
_current_elapsed = [0.0]

async def fake_quick_capture(filename):
    """Return the reference image matching current roast phase and progress."""
    ref_path = _select_reference_image(_current_phase[0], _current_elapsed[0])
    capture_log.append({
        "elapsed": _current_elapsed[0],
        "phase": _current_phase[0] or "pre-charge",
        "ref_image": ref_path.name,
    })
    return ref_path

mock_gopro.quick_capture = AsyncMock(side_effect=fake_quick_capture)
mock_gopro.capture_image = AsyncMock(side_effect=fake_quick_capture)

# Patch gopro_bridge but keep real vision_client
sys.modules["gopro_bridge"] = mock_gopro

import sentinel as sentinel_mod


# Monkey-patch _capture_and_analyze to update elapsed before mock runs
_original_capture = sentinel_mod.SentinelSession._capture_and_analyze

async def _patched_capture(self):
    """Update roast state before capture so mock picks right image."""
    _current_elapsed[0] = self.artisan.elapsed() or 0.0
    _current_phase[0] = self.artisan.current_phase
    return await _original_capture(self)

sentinel_mod.SentinelSession._capture_and_analyze = _patched_capture


def main():
    # Verify reference images exist
    missing = [f for f in REF_IMAGES if not (REF_DIR / f).exists()]
    if missing:
        print(f"Error: Missing reference images in {REF_DIR}:")
        for f in missing:
            print(f"  {f}")
        sys.exit(1)

    print("=" * 55)
    print("  Sentinel Simulation (reference images + real AI)")
    print("=" * 55)
    print()
    print("  GoPro mocked → reference images based on roast time")
    print("  Vision API → REAL Claude calls (uses API credits)")
    print()
    print("  Phase progression:")
    for phase, images in PHASE_IMAGES.items():
        phase_str = phase or "pre-charge"
        for frac, filename in images:
            label = filename.replace("_", " ").replace(".png", "")
            pct = int(frac * 100)
            print(f"    {phase_str:12s} ≤{pct:3d}%  → {label}")
    print()
    print("  Run fake_artisan.py in another terminal.")
    print()

    sentinel_mod.start_sentinel(
        bean_name="Reference Image Test",
        ws_port=8765,
        debug=False,
    )

    # Print capture summary
    print()
    print(f"  Total captures: {len(capture_log)}")
    print()
    for i, c in enumerate(capture_log, 1):
        elapsed = c["elapsed"]
        m, s = divmod(int(elapsed), 60)
        print(f"    #{i:2d}  T+{m}:{s:02d}  [{c['phase']:12s}]  → {c['ref_image']}")


if __name__ == "__main__":
    main()
