"""Where things actually are on screen, measured rather than assumed.

Every coordinate bug this server has had came from the same mistake: a position
recorded once, on one window size, and then trusted forever. Four of them shipped:

* the toolbar tempo field, offset from the window's left edge when the transport
  is centre-anchored -- on a 2560-wide window the click missed by 310px, and a
  missed click is silent, so the tempo simply stayed where it was;
* the instrument browser's result row, assumed to be the first match;
* TRACK_NAME_X, which lands on the volume fader rather than the track name, so
  selection quietly did nothing and the deletes that followed were no-ops;
* the track strip row, off by one strip, which selects the wrong track -- and a
  wrong selection followed by a delete destroys the wrong part.

So this module looks. It finds the divider between the track header column and
the timeline, then derives every control from that edge, and finds the strips
by scanning for the rules between them. Anything it cannot measure it reports
as unknown instead of guessing.

All returned coordinates are physical screen pixels, ready to click.
"""

from __future__ import annotations

from . import screen, winctl

# Offsets back from the right-hand edge of the track header column. The buttons
# are right-aligned in the strip, so measuring from that edge survives a window
# resize where measuring from the left does not.
CONTROL_OFFSETS = {
    "mute":   -125,
    "solo":    -92,
    "arm":     -61,
    "phones":  -30,
    "name":   -212,      # over the track name text, clear of the fader
    "fader":  -175,
}

NAME_ROW_OFFSET = 34     # from the top of a strip down to its name row
MIN_STRIP = 40           # physical px; anything shorter is not a strip
SEPARATOR_DROP = 10      # luminance fall that marks a rule between strips


class LayoutError(RuntimeError):
    """Raised when the window cannot be measured."""


def _luma(pixels, stride, x, y):
    i = y * stride + x * 4
    return (pixels[i] + pixels[i + 1] + pixels[i + 2]) // 3


def panel_edge(hwnd, probe_height=900):
    """x of the boundary between the track header column and the timeline.

    Found by walking right along several rows until the flat dark grey of the
    header gives way to something else. Several rows, because a single row can
    land in a gap between clips and never change.
    """
    left, top, width, height = screen.visible_rect(hwnd)
    span = min(700, width)
    gw, gh, px = screen.grab(left, top, span, min(probe_height, height))
    stride = gw * 4

    candidates = []
    for y in range(120, gh, 17):
        base = _luma(px, stride, 20, y)
        for x in range(200, gw - 1):
            if abs(_luma(px, stride, x, y) - base) > 26:
                candidates.append(x)
                break
    if not candidates:
        raise LayoutError(
            "Could not find the edge of the track header column. The window may "
            "be obscured; bring Cakewalk Next to the front and retry.")
    candidates.sort()
    return left + candidates[len(candidates) // 2]


ICON_X = (40, 110)       # the instrument icon column, physical px from the left
ICON_TO_STRIP_TOP = 41   # from the top of the icon back up to the strip's top
MIN_CHROMA = 40          # how colourful a pixel must be to count as an icon
MIN_ICON_WIDTH = 40      # coloured columns an instrument icon spans
# An open automation lane puts a small coloured toggle in the same column, so
# colour alone finds lanes as well as tracks and doubles the count. The
# instrument icon is much wider -- 57 columns against the toggle's 30 -- so
# width is what separates them.


def strip_rows(hwnd, edge=None):
    """The top y of every TRACK strip, in order. Master is not included.

    Found by the coloured instrument icon each track carries. Luminance alone
    is not enough: the volume fader is a wide horizontal control whose top and
    bottom edges register across the whole strip exactly like a separator, so a
    brightness scan reports phantom strips in the middle of real ones. The icon
    is unambiguous -- the panel around it is flat grey, and Master's speaker
    icon is grey too, so tracks fall out of the scan on their own.

    Returning tracks only also removes the off-by-one that made `index=2`
    select track 3 -- there is no longer a Master entry to be off by.
    """
    left, top, width, height = screen.visible_rect(hwnd)
    gw, gh, px = screen.grab(left, top, min(width, ICON_X[1] + 40), height)
    stride = gw * 4

    def icon_width(y):
        """How many columns of this row are coloured."""
        count = 0
        for x in range(ICON_X[0], min(ICON_X[1], gw)):
            i = y * stride + x * 4
            b, g, r = px[i], px[i + 1], px[i + 2]
            if max(b, g, r) - min(b, g, r) >= MIN_CHROMA:
                count += 1
        return count

    runs, current = [], None
    for y in range(gh):
        if icon_width(y) >= MIN_ICON_WIDTH:
            current = [y, y] if current is None else [current[0], y]
        else:
            if current and current[1] - current[0] >= 10:
                runs.append(current)
            current = None
    if current and current[1] - current[0] >= 10:
        runs.append(current)
    if not runs:
        return []

    # The application logo sits in the same column, above the track list, and
    # is just as colourful. Drop anything above the first horizontal rule of
    # the panel itself.
    panel_top = _panel_top(px, stride, gw, gh)
    tops = [r[0] for r in runs if r[0] > panel_top]

    # Stop only at a jump far larger than the usual spacing, which means the
    # track list has ended. Requiring a *uniform* pitch was wrong: opening an
    # automation lane makes one strip taller than its neighbours, and a
    # uniformity test then discards every track below it.
    if len(tops) > 2:
        gaps = sorted(tops[i + 1] - tops[i] for i in range(len(tops) - 1))
        median = gaps[len(gaps) // 2] or 1
        kept = [tops[0]]
        for i in range(len(tops) - 1):
            if tops[i + 1] - tops[i] > median * 3:
                break
            kept.append(tops[i + 1])
        tops = kept
    return [top + y - ICON_TO_STRIP_TOP for y in tops]


def _panel_top(px, stride, gw, gh):
    """y of the rule under the toolbar, above which nothing is a track."""
    x = min(gw - 1, 10)
    previous = _luma(px, stride, x, 0)
    for y in range(1, gh):
        current = _luma(px, stride, x, y)
        if previous - current >= SEPARATOR_DROP and y > 100:
            return y
        previous = current
    return 100


def measure(hwnd):
    """Everything clickable in the track panel, and the transport."""
    left, top, width, height = screen.visible_rect(hwnd)
    scale = winctl.dpi_scale(hwnd)
    edge = panel_edge(hwnd)
    rows = strip_rows(hwnd, edge)

    strips = []
    for index, row_top in enumerate(rows, start=1):   # 1-based; Master excluded
        controls = {name: edge + offset
                    for name, offset in CONTROL_OFFSETS.items()}
        strips.append({
            "index": index,
            "top": row_top,
            "name_y": row_top + NAME_ROW_OFFSET,
            "controls": controls,
        })

    centre = left + width // 2
    return {
        "window": {"left": left, "top": top, "width": width, "height": height},
        "dpi_scale": scale,
        "panel_edge": edge,
        "transport": {
            # Centre-anchored: the transport stays in the middle of the window
            # however wide it gets.
            "tempo": (centre + int(-240 * scale), top + int(49 * scale)),
            "position": (centre + int(300 * scale), top + int(49 * scale)),
        },
        "strips": strips,
        "track_count": len(strips),
    }


def track_point(hwnd, index, control="name"):
    """Where to click for track `index`, 1-based. Master is not addressable.

    Raises rather than returning a plausible-looking wrong coordinate: a wrong
    track click followed by a delete removes the wrong part, and there is no
    undo for a deleted track.
    """
    if control not in CONTROL_OFFSETS:
        raise LayoutError(
            "Unknown control %r. Known: %s"
            % (control, ", ".join(sorted(CONTROL_OFFSETS))))
    info = measure(hwnd)
    strips = info["strips"]
    if not strips:
        raise LayoutError(
            "No track strips were found on screen. The project may have no "
            "tracks, or the window may be obscured.")
    if index < 1 or index > len(strips):
        raise LayoutError(
            "Track %d is not on screen: %d track strips are visible, so 1-%d. "
            "Scroll the track list or check next_screenshot."
            % (index, len(strips), len(strips)))
    strip = strips[index - 1]
    return strip["controls"][control], strip["name_y"], info


# --------------------------------------------------------------------------
# Reading the strip buttons.
#
# Inactive buttons are drawn in flat grey; active ones are coloured, and each
# has its own hue: mute orange, solo green, arm red. Chroma alone separates
# on from off, which is far more robust than matching an exact shade.
# --------------------------------------------------------------------------
CONTROL_ON_CHROMA = 40
CONTROL_PROBE = 7          # half-size of the square sampled around a button


def control_states(hwnd, strips=None, edge=None):
    """Read mute / solo / arm for every visible track.

    Caveat worth passing on: Cakewalk lights the M button of every other track
    while one track is soloed, so a lit M means "not currently audible", which
    is not the same as "explicitly muted". This reports what is on screen and
    does not pretend to tell the two apart.
    """
    info = measure(hwnd) if strips is None else {"strips": strips,
                                                 "panel_edge": edge}
    strips = info["strips"]
    if not strips:
        return []
    left, top, _w, _h = screen.visible_rect(hwnd)
    bottom = max(s["name_y"] for s in strips) + 40
    width = (info.get("panel_edge") or panel_edge(hwnd)) + 10 - left
    gw, gh, px = screen.grab(left, top, max(1, width), max(1, bottom - top))
    stride = gw * 4

    def lit(x, y):
        best = 0
        for dy in range(-CONTROL_PROBE, CONTROL_PROBE + 1):
            for dx in range(-CONTROL_PROBE, CONTROL_PROBE + 1):
                px_x, px_y = x - left + dx, y - top + dy
                if not (0 <= px_x < gw and 0 <= px_y < gh):
                    continue
                i = px_y * stride + px_x * 4
                b, g, r = px[i], px[i + 1], px[i + 2]
                best = max(best, max(b, g, r) - min(b, g, r))
        return best >= CONTROL_ON_CHROMA

    out = []
    for strip in strips:
        out.append({
            "index": strip["index"],
            "mute": lit(strip["controls"]["mute"], strip["name_y"]),
            "solo": lit(strip["controls"]["solo"], strip["name_y"]),
            "armed": lit(strip["controls"]["arm"], strip["name_y"]),
        })
    return out


# --------------------------------------------------------------------------
# Open pop-up menus.
#
# An open menu is its own window, so it can be measured directly. It has to be:
# menus contain separators, so stepping a fixed distance per item drifts. In
# Next's File menu the separators push "Save Project" down by two rows, and a
# stepped click lands on "Save As Project Template" instead.
# --------------------------------------------------------------------------
MENU_TEXT_LUMA = 120     # menu labels are light on a dark popup
MENU_MIN_ROW = 3         # a text band this tall or more is a real item


def popup_item_rows(menu_hwnd):
    """Physical y centres of the items in an open pop-up menu, top to bottom.

    Rows are found from the label text itself, so separators -- which carry no
    text -- are skipped rather than counted as items.
    """
    left, top, width, height = winctl.window_rect(menu_hwnd)
    if width <= 0 or height <= 0:
        raise LayoutError("The open menu has no measurable size.")
    gw, gh, px = screen.grab(left, top, width, height)
    stride = gw * 4
    span = min(gw, 240)

    bands, current = [], None
    for y in range(gh):
        bright = 0
        for x in range(10, span):
            i = y * stride + x * 4
            if (px[i] + px[i + 1] + px[i + 2]) // 3 > MENU_TEXT_LUMA:
                bright += 1
                if bright >= 3:
                    break
        if bright >= 3:
            current = [y, y] if current is None else [current[0], y]
        else:
            if current and current[1] - current[0] >= MENU_MIN_ROW:
                bands.append(current)
            current = None
    if current and current[1] - current[0] >= MENU_MIN_ROW:
        bands.append(current)

    return [top + (a + b) // 2 for a, b in bands], (left, top, width, height)
