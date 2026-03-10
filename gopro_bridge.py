"""GoPro bridge — all camera interactions go through here.

Handles USB connection via Open GoPro SDK, photo capture, file download,
and post-processing. Replaces adb_bridge.py from r1-eye with an async
interface suited to the GoPro Hero 13's HTTP-over-USB API.

Connection: USB-C wired (WiredGoPro). Camera appears as USB NCM ethernet
adapter; SDK communicates via HTTP to 172.2X.1YZ.51:8080.

Hero 13 mDNS bug: Camera doesn't advertise _gopro-web service, so the
serial number (last 3 digits) must be passed manually to WiredGoPro().
"""

import os
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from open_gopro import WiredGoPro
from open_gopro.models import constants, proto
from open_gopro.models.constants import StatusId

# Last 3 digits of GoPro serial (printed on camera body).
# Required because Hero 13 firmware doesn't advertise mDNS.
SERIAL = os.environ.get("GOPRO_SERIAL")

# Local captures directory
CAPTURES_DIR = Path(__file__).parent / "captures"

# Post-processing crop region (fraction of image dimensions).
# Conservative 5% all sides — GoPro has no device housing in frame.
# LINEAR lens mode reduces fisheye distortion.
CROP_TOP = 0.05
CROP_BOTTOM = 0.95
CROP_LEFT = 0.05
CROP_RIGHT = 0.95

# Module-level GoPro instance (set during camera session)
_gopro = None
_camera_session_active = False


# ── Post-processing ────────────────────────────────────────


def _auto_white_balance(img):
    """Apply gray world white balance normalization.

    Adjusts each RGB channel so their means are equal, removing
    color casts from mixed lighting. This keeps bean color readings
    consistent across captures regardless of ambient light shifts.
    """
    arr = np.array(img, dtype=np.float32)
    # Mean of each channel
    avg_r = arr[:, :, 0].mean()
    avg_g = arr[:, :, 1].mean()
    avg_b = arr[:, :, 2].mean()
    # Target: overall mean across all channels
    avg_all = (avg_r + avg_g + avg_b) / 3.0
    # Scale each channel to match the overall mean
    if avg_r > 0:
        arr[:, :, 0] *= avg_all / avg_r
    if avg_g > 0:
        arr[:, :, 1] *= avg_all / avg_g
    if avg_b > 0:
        arr[:, :, 2] *= avg_all / avg_b
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def post_process(image_path):
    """Crop, resize, enhance contrast/sharpness for color analysis.

    Steps:
      1. Crop to bean bed area (removes edges)
      2. Resize to fit Vision API limit (max 2048px long edge)
      3. Gray world white balance (neutralizes color cast)
      4. Contrast enhancement (improves bean-to-background separation)
      5. Unsharp mask sharpening (counteracts motion blur from tumbling)

    Overwrites the image at image_path with the processed version.
    Returns the path unchanged for chaining.
    """
    img = Image.open(image_path)
    w, h = img.size

    # Crop to viewport area
    crop_box = (
        int(w * CROP_LEFT),
        int(h * CROP_TOP),
        int(w * CROP_RIGHT),
        int(h * CROP_BOTTOM),
    )
    img = img.crop(crop_box)

    # Resize if needed — 27MP GoPro photos exceed Claude Vision's 5MB limit.
    # Cap long edge at 2048px (plenty of detail for bean color analysis,
    # keeps JPEG well under 5MB).
    MAX_EDGE = 2048
    w, h = img.size
    if max(w, h) > MAX_EDGE:
        scale = MAX_EDGE / max(w, h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    # White balance normalization
    img = _auto_white_balance(img)

    # Boost contrast — 1.0 is original, 1.3 adds 30% more pop
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.3)

    # Sharpen to counteract motion blur from bean tumbling
    # radius=2 for moderate kernel, percent=150 for noticeable effect,
    # threshold=3 to avoid amplifying noise in smooth areas
    img = img.filter(ImageFilter.UnsharpMask(
        radius=2, percent=150, threshold=3
    ))

    # Save processed image (JPEG for photos, PNG for screencap fallback)
    suffix = Path(image_path).suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        img.save(image_path, quality=95)
    else:
        img.save(image_path)

    return image_path


# ── Connection ────────────────────────────────────────────


async def is_connected():
    """Check if a GoPro is reachable over USB.

    Opens a temporary connection to verify the camera responds.
    Returns True if the camera is reachable.
    """
    try:
        async with WiredGoPro(SERIAL) as gopro:
            return gopro.is_open
    except Exception:
        return False


async def device_status():
    """Get a summary of device state.

    Opens a temporary connection to read battery and model info.
    Returns dict with status info, or None if not connected.
    """
    try:
        async with WiredGoPro(SERIAL) as gopro:
            # Read camera state — keys are StatusId enum objects
            state = (await gopro.http_command.get_camera_state()).data
            battery_pct = state.get(StatusId.INTERNAL_BATTERY_PERCENTAGE, "?")

            return {
                "serial": SERIAL,
                "model": "Hero 13",
                "battery_percent": battery_pct,
                "connection": "USB",
            }
    except Exception:
        return None


# ── Camera session (for sentinel mode) ─────────────────────


async def start_camera_session():
    """Open GoPro connection and configure for photo capture.

    Sets up the camera for repeated captures: loads photo preset,
    sets LINEAR lens mode, and disables auto power down.
    Call before a series of captures.
    """
    global _gopro, _camera_session_active

    _gopro = WiredGoPro(SERIAL)
    await _gopro.open()

    # Load photo preset group
    await _gopro.http_command.load_preset_group(
        group=proto.EnumPresetGroup.PRESET_GROUP_ID_PHOTO
    )

    # Set photo lens to LINEAR 27MP (Hero 13's linear mode)
    await _gopro.http_setting.photo_lens.set(
        constants.settings.PhotoLens.LINEAR_27_MP
    )

    # Disable auto power down (camera stays on during roast)
    await _gopro.http_setting.auto_power_down.set(
        constants.settings.AutoPowerDown.NEVER
    )

    _camera_session_active = True


async def quick_capture(filename=None):
    """Take a photo, download it, delete from SD, post-process.

    Must call start_camera_session() first. The GoPro capture flow:
      1. Trigger shutter via HTTP
      2. Get the last captured media file info
      3. Download the file to local captures/ dir
      4. Delete from SD card to free space
      5. Post-process the local file

    Args:
        filename: Optional local filename. Auto-generated if None.

    Returns:
        Path to the local processed image file, or None on failure.
    """
    global _gopro

    if _gopro is None:
        print("Error: No camera session active")
        return None

    if filename is None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"capture_{timestamp}.jpg"

    CAPTURES_DIR.mkdir(exist_ok=True)
    local_path = CAPTURES_DIR / filename

    try:
        # Trigger shutter (take the photo)
        await _gopro.http_command.set_shutter(
            shutter=constants.Toggle.ENABLE
        )

        # Get the last captured media file (MediaPath has folder + file)
        media = (await _gopro.http_command.get_last_captured_media()).data
        camera_file = media.as_path  # e.g. "100GOPRO/GP010175.JPG"

        # Download to local path
        await _gopro.http_command.download_file(
            camera_file=camera_file,
            local_file=local_path,
        )

        # Delete from SD card to free space
        await _gopro.http_command.delete_file(path=camera_file)

        # Post-process for color analysis
        post_process(local_path)

        return local_path

    except Exception as e:
        print(f"Capture error: {e}")
        return None


async def end_camera_session():
    """Close the GoPro connection.

    Call when done capturing (after DROP or Ctrl+C).
    """
    global _gopro, _camera_session_active

    if _gopro is not None:
        try:
            await _gopro.close()
        except Exception:
            pass
        _gopro = None

    _camera_session_active = False


def is_camera_session_active():
    """Check if a camera session is currently open (sync)."""
    return _camera_session_active


# ── One-shot capture ───────────────────────────────────────


async def capture_image(filename=None):
    """One-shot capture: open connection, take photo, close, process.

    Used by 'ask' and 'look' commands. For sentinel mode, use the
    session-based functions instead (start/quick/end_camera_session).

    Args:
        filename: Optional local filename. Auto-generated if None.

    Returns:
        Path to the local processed image file, or None on failure.
    """
    try:
        await start_camera_session()
        result = await quick_capture(filename)
        return result
    finally:
        await end_camera_session()
