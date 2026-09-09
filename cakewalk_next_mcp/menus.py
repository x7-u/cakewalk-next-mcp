"""Click-driven access to Cakewalk Next menu commands that have no shortcut.

Next draws its own menu bar: pressing ALT does nothing and there are no
accelerators, so the only way to reach a command like *Insert > Insert
Audio/MIDI File...* is to click it.

The geometry below was measured off the running app (1.1.0.150) and is stored
at 96 DPI, then scaled by the window's actual DPI at click time. Positions are
relative to the window rect, so moving or resizing the window is fine.

This is inherently more brittle than a keyboard shortcut. Every caller must
confirm the expected dialog actually appeared before typing anything into it -
see ``winctl.find_dialog``.
"""

from __future__ import annotations

import time

from . import layout, winctl

# All values in logical (96 DPI) pixels, relative to the window's top-left.
MENU_BAR_Y = 22
MENU_BAR_X = {
    "file": 59,
    "edit": 108,
    "insert": 164,
    "view": 222,
    "transport": 290,
    "help": 360,
}

MENU_OPEN_ATTEMPTS = 4

FIRST_ITEM_Y = 51
ITEM_SPACING = 28
ITEM_X = 240

# Item order within each menu, as the shipping build lists them.
MENU_ITEMS = {
    "insert": [
        "create_audio_track",
        "create_instrument_track",
        "create_sampler_track",
        "create_pad_controller_track",
        "create_instrument_rack_track",
        "create_track_folder",
        "create_bus",
        "insert_track_template",
        "insert_audio_midi_file",
        "insert_empty_midi_clip",
    ],
    "file": [
        "new_project",
        "open_project",
        "open_recent_project",
        "save_project",
        "save_project_as",
        "save_as_project_template",
        "export_audio",
        "publish_to_bandlab",
        "export_to_cxf",
        "exit",
    ],
}

# Menu commands an agent must never be able to click, for the same reasons the
# equivalent shortcuts are withheld from the command table.
FORBIDDEN_ITEMS = {"publish_to_bandlab", "exit"}


class MenuError(RuntimeError):
    """Raised when a menu path cannot be resolved or clicked."""


def item_position(hwnd, menu, item):
    """Return the physical (x, y) to click for ``menu``/``item``."""
    menu = menu.lower()
    if menu not in MENU_BAR_X:
        raise MenuError(
            "Unknown menu %r. Known menus: %s." % (menu, ", ".join(sorted(MENU_BAR_X)))
        )
    items = MENU_ITEMS.get(menu)
    if items is None:
        raise MenuError("Menu %r has no mapped items." % menu)
    if item not in items:
        raise MenuError(
            "Unknown item %r in the %s menu. Known items: %s."
            % (item, menu, ", ".join(items))
        )

    left, top, _w, _h = winctl.window_rect(hwnd)
    scale = winctl.dpi_scale(hwnd)
    index = items.index(item)
    menu_x = left + MENU_BAR_X[menu] * scale
    menu_y = top + MENU_BAR_Y * scale
    item_x = left + ITEM_X * scale
    item_y = top + (FIRST_ITEM_Y + ITEM_SPACING * index) * scale
    return (menu_x, menu_y), (item_x, item_y)


def click_item(hwnd, pid, menu, item, settle=0.9):
    """Open ``menu``, confirm it is really open, then click ``item``.

    The confirmation matters: if the menu-bar click misses, the item click would
    otherwise land in the arrangement view and could move a clip.
    """
    if item in FORBIDDEN_ITEMS:
        raise MenuError(
            "%r is not available through this server: it publishes the project "
            "or quits the app. Do it from the UI." % item
        )
    (menu_x, menu_y), (item_x, item_y) = item_position(hwnd, menu, item)

    # The first click after the window takes focus is sometimes swallowed, so
    # this retries rather than failing. The item is only clicked once the popup
    # is confirmed open - otherwise the item click would land in the
    # arrangement view and could drag a clip.
    opened = None
    for attempt in range(MENU_OPEN_ATTEMPTS):
        winctl.click_in(pid, menu_x, menu_y, "the %s menu" % menu)
        opened = winctl.find_popup_menu(pid, timeout=settle + 1.0)
        if opened:
            break
        time.sleep(0.6)
    if not opened:
        dismiss()
        raise MenuError(
            "Clicked the %s menu at (%d, %d) %d times but no menu opened, so the "
            "item was not clicked. Check the coordinates in menus.py against the "
            "app." % (menu, int(menu_x), int(menu_y), MENU_OPEN_ATTEMPTS)
        )
    time.sleep(settle)

    # Measure the open menu rather than stepping a fixed distance per item.
    # Menus contain separators, so a stepped offset drifts: in the File menu it
    # lands on "Save As Project Template" when asked for "Save Project".
    items = MENU_ITEMS[menu]
    index = items.index(item)
    try:
        # find_popup_menu returns a bare hwnd, not a window dict.
        rows, rect = layout.popup_item_rows(opened)
    except Exception as exc:      # noqa: BLE001 - measurement can fail many ways
        rows, rect = [], None
        measure_error = exc
    else:
        measure_error = None

    if len(rows) == len(items):
        item_y = rows[index]
        item_x = rect[0] + min(120, rect[2] // 3)
        how = "measured"
    else:
        # Refuse to guess. Clicking the wrong menu item is silent and can be
        # destructive -- New Project discards unsaved work without asking.
        dismiss()
        raise MenuError(
            "The open %s menu shows %d items but %d are mapped%s, so the row "
            "for %r cannot be identified. Re-map MENU_ITEMS[%r] against the "
            "app, or drive this from the UI."
            % (menu, len(rows), len(items),
               "" if measure_error is None else " (%s)" % measure_error,
               item, menu))

    winctl.click_in(pid, item_x, item_y, "menu item %r" % item)
    return {"menu_click": [int(menu_x), int(menu_y)],
            "item_click": [int(item_x), int(item_y)],
            "item_index": index, "rows_seen": len(rows), "targeting": how}


# --------------------------------------------------------------------------
# The "Add Instrument to Track" browser, reached from Insert > Create
# Instrument Track. Offsets are relative to the dialog's own rect at 96 DPI.
# --------------------------------------------------------------------------

BROWSER_TITLE = "Add Instrument to Track"
BROWSER_SEARCH = (674, 48)
BROWSER_FIRST_ROW = (467, 149)
BROWSER_ROW_STEP = 24          # logical px between result rows
BROWSER_ADD = (782, 511)


def instrument_browser(pid, timeout=10.0):
    """Wait for the instrument browser window to appear."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for window in winctl.find_windows():
            if window["title"] == BROWSER_TITLE:
                return window
        time.sleep(0.3)
    return None


def choose_instrument(dialog_hwnd, name, pid=None, row=0):
    """Search the browser for ``name`` and add the match at ``row``.

    Searching narrows the list, but it does NOT make the wanted instrument the
    top row, which this used to assume. Next matches tags as well as names and
    then sorts alphabetically, so "Grand Piano" lists Dark Grand, Grand Piano,
    Studio Grand -- row 0 is the wrong one -- and "Piano" leads with Accordion.

    ``row`` is a 0-based index into the visible results. Callers that cannot be
    sure of the ordering should photograph the dialog before committing; see
    next_create_instrument_track, which returns the row it clicked.
    """
    left, top, _w, _h = winctl.window_rect(dialog_hwnd)
    scale = winctl.dpi_scale(dialog_hwnd)
    if pid is None:
        pid = winctl.owner_pid(dialog_hwnd)

    def at(point):
        return left + int(point[0] * scale), top + int(point[1] * scale)

    row_point = (BROWSER_FIRST_ROW[0], BROWSER_FIRST_ROW[1] + BROWSER_ROW_STEP * row)

    winctl.click_in(pid, *at(BROWSER_SEARCH), what="the browser search box")
    time.sleep(0.5)
    winctl.send_text(name)
    time.sleep(1.8)                      # let the list filter
    winctl.click_in(pid, *at(row_point), what="search result row %d" % row)
    time.sleep(0.8)
    winctl.click_in(pid, *at(BROWSER_ADD), what="the Add button")
    return {"search": at(BROWSER_SEARCH), "row": at(row_point), "add": at(BROWSER_ADD),
            "row_index": row}


# --------------------------------------------------------------------------
# Track strips in the arrangement.
#
# A strip is TRACK_STRIP_HEIGHT tall *only while automation lanes are hidden*.
# Showing them (the toggle above the track list) doubles it, and clicking a
# fixed y then selects the wrong track - which silently drops imported parts
# onto the wrong instruments. Anything selecting tracks by coordinate must
# either hide the lanes first or verify what it selected.
# --------------------------------------------------------------------------

TRACK_STRIP_TOP = 246          # centre of the first track's name row
TRACK_STRIP_HEIGHT = 80        # with automation lanes hidden
TRACK_NAME_X = 240             # right of the name text, left of the M/S buttons


def track_strip_point(index):
    """Physical (x, y) to click to select track ``index`` (1-based)."""
    return TRACK_NAME_X, TRACK_STRIP_TOP + TRACK_STRIP_HEIGHT * (index - 1)


def dismiss(times=2):
    """Press ESC a few times to close any menu or popup left open."""
    for _ in range(times):
        try:
            winctl.send_chord("ESC")
        except winctl.WindowError:
            break
        time.sleep(0.2)
