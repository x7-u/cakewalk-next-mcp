"""Screen capture for Cakewalk Next, with no dependencies.

The server is otherwise blind: Next is a JUCE application whose track names,
clip positions and button states are drawn, not exposed as queryable controls,
and there is no live project file on disk to read (``ProjectsCache`` holds only
a thumbnail). Every navigation bug in this project came from acting on guessed
state instead of observed state.

So the agent gets to look. Pixels come from GDI via ctypes and are encoded as
PNG with ``zlib`` from the standard library, which keeps the server free of
imaging dependencies.
"""

from __future__ import annotations

import ctypes
import struct
import zlib
from ctypes import wintypes

from . import winctl

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0

# A full 1938x1038 window is ~2 MB of PNG; downscaling keeps tool results sane
# while staying legible enough to read track names and clip positions.
DEFAULT_MAX_WIDTH = 1400


class CaptureError(RuntimeError):
    """Raised when the screen cannot be read."""


if winctl.IS_WINDOWS:
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def grab(left, top, width, height):
    """Return (width, height, rgb_bytes) for a screen rectangle."""
    if not winctl.IS_WINDOWS:
        raise CaptureError("Screen capture requires Windows.")
    if width <= 0 or height <= 0:
        raise CaptureError("Capture size must be positive, got %dx%d." % (width, height))

    screen_dc = winctl.user32.GetDC(None)
    if not screen_dc:
        raise CaptureError("Could not get the screen device context.")
    memory_dc = bitmap = None
    try:
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not memory_dc or not bitmap:
            raise CaptureError("Could not allocate a capture bitmap.")
        gdi32.SelectObject(memory_dc, bitmap)
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height,
                            screen_dc, int(left), int(top), SRCCOPY):
            raise CaptureError("BitBlt failed (error %d)." % ctypes.get_last_error())

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        # Negative height gives a top-down image, so rows arrive in PNG order.
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        buffer = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer,
                               ctypes.byref(info), DIB_RGB_COLORS):
            raise CaptureError("GetDIBits failed.")
        return width, height, buffer.raw
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        winctl.user32.ReleaseDC(None, screen_dc)


def _downscale(width, height, bgra, max_width):
    """Nearest-neighbour subsample. Crude, but adequate for reading a UI."""
    if max_width <= 0 or width <= max_width:
        return width, height, bgra
    step = (width + max_width - 1) // max_width
    new_w, new_h = (width + step - 1) // step, (height + step - 1) // step
    out = bytearray(new_w * new_h * 4)
    for y in range(new_h):
        src = (y * step) * width * 4
        dst = y * new_w * 4
        for x in range(new_w):
            s = src + (x * step) * 4
            out[dst:dst + 4] = bgra[s:s + 4]
            dst += 4
    return new_w, new_h, bytes(out)


def to_png(width, height, bgra):
    """Encode BGRA pixels as a PNG using only the standard library."""
    stride = width * 4
    rows = bytearray()
    for y in range(height):
        rows.append(0)                       # filter type 0 (None)
        row = bgra[y * stride:(y + 1) * stride]
        # BGRA -> RGB. Slicing each channel and zipping beats a per-pixel loop
        # by enough to matter on a 2-megapixel window.
        rows.extend(b"".join(map(bytes, zip(row[2::4], row[1::4], row[0::4]))))

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))


def visible_rect(hwnd):
    """Return a window's rect, refusing if it is minimised.

    Windows parks a minimised window at (-32000, -32000). Capturing there
    returns whatever noise happens to be in the framebuffer, which looks like
    a real image and silently poisons anything measured from it.
    """
    if winctl.user32.IsIconic(hwnd):
        raise CaptureError(
            "Cakewalk Next is minimised; there is nothing on screen to capture. "
            "Restore it first (any tool that takes focus will)."
        )
    left, top, width, height = winctl.window_rect(hwnd)
    if left <= -30000 or top <= -30000 or width < 50 or height < 50:
        raise CaptureError(
            "Cakewalk Next's window is off-screen or collapsed (%d,%d %dx%d); "
            "nothing to capture." % (left, top, width, height)
        )
    return left, top, width, height


def capture_window(hwnd, max_width=DEFAULT_MAX_WIDTH):
    """Capture a window's screen rectangle as PNG bytes."""
    left, top, width, height = visible_rect(hwnd)
    # Windows reports a few pixels of invisible border; trimming avoids a
    # black frame around the image.
    left, top = max(left, 0), max(top, 0)
    w, h, pixels = grab(left, top, width, height)
    w, h, pixels = _downscale(w, h, pixels, max_width)
    return to_png(w, h, pixels), (w, h)


# Geometry of the track list, in logical (96 DPI) pixels from the window's
# top-left. The panel starts below the toolbar; each track strip is separated
# from the next by a dark rule.
PANEL_LEFT = 4
PANEL_TOP = 118
PANEL_WIDTH = 320
SEPARATOR_DROP = 26        # how much darker a rule is than the strip above it
MIN_STRIP_HEIGHT = 30      # logical px; anything smaller is noise


def detect_track_rows(hwnd):
    """Find each track strip by looking, rather than assuming its height.

    Strip height is not a constant: showing automation lanes roughly doubles
    it, and a fixed offset then selects the wrong track and silently drops
    imported parts onto the wrong instrument. This scans a column down the
    track list for the dark rules between strips and returns the y centre of
    each one, so selection follows whatever the UI is actually doing.

    Returns a list of physical y coordinates, one per strip (Master first).
    """
    left, top, width, height = visible_rect(hwnd)
    scale = winctl.dpi_scale(hwnd)
    x = int(left + PANEL_LEFT * scale)
    y0 = int(top + PANEL_TOP * scale)
    h = max(1, height - int(PANEL_TOP * scale))
    w = int(PANEL_WIDTH * scale)

    _w, rows, pixels = grab(max(x, 0), max(y0, 0), w, h)
    stride = _w * 4
    # Brightness of a column well inside the strip, away from the coloured edge.
    probe = int(min(_w - 1, 200 * scale))
    lum = []
    for y in range(rows):
        i = y * stride + probe * 4
        lum.append((pixels[i] + pixels[i + 1] + pixels[i + 2]) // 3)

    boundaries = []
    for y in range(1, len(lum)):
        if lum[y - 1] - lum[y] >= SEPARATOR_DROP:
            if not boundaries or y - boundaries[-1] >= MIN_STRIP_HEIGHT * scale:
                boundaries.append(y)

    centres = []
    for index, start in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(lum)
        if end - start >= MIN_STRIP_HEIGHT * scale:
            # The name row sits near the top of a strip, not its middle, so a
            # tall strip with automation lanes still resolves to the name row.
            centres.append(max(y0, 0) + start + int(22 * scale))
    return centres


def capture_region(left, top, width, height, max_width=DEFAULT_MAX_WIDTH):
    """Capture an arbitrary screen rectangle as PNG bytes."""
    w, h, pixels = grab(left, top, width, height)
    w, h, pixels = _downscale(w, h, pixels, max_width)
    return to_png(w, h, pixels), (w, h)
