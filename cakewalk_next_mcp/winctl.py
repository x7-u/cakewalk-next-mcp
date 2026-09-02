"""Locate the Cakewalk Next window and drive it with synthetic keystrokes.

Next exposes no scripting API, no OSC and no Mackie/MCU control surface, so its
documented keyboard shortcuts are the only remote-control surface available.
This module talks to user32 through ctypes so the server has no dependencies
beyond the MCP SDK.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

PROCESS_NAME = "next.exe"
WINDOW_TITLE_SUFFIX = "Cakewalk Next"

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Declare this process DPI-aware so GetWindowRect and SetCursorPos report
    # real pixels. Without it Windows virtualises coordinates for us (reporting
    # e.g. 1536x864 on a 1920x1080 screen at 125%) while screen capture and the
    # actual cursor use physical pixels, so every computed click lands short.
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:  # pragma: no cover - pre-8.1, or already set
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
else:  # pragma: no cover - the tools refuse to run before reaching this
    user32 = kernel32 = None

INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SW_MINIMIZE = 6
SW_SHOW = 5
SW_RESTORE = 9
ASFW_ANY = -1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Pauses after taking focus, before the first keystroke is delivered.
SETTLE_AFTER_FOCUS = 0.25
SETTLE_AFTER_RESTORE = 0.9
MODIFIER_SETTLE = 0.03

MAPVK_VK_TO_VSC = 0

# Left-hand modifier VKs, not the generic VK_CONTROL/VK_SHIFT/VK_MENU. Plain
# Win32 apps accept either, but Cakewalk Next resolves shortcuts from scan
# codes and silently drops chords whose modifier carries none - it then acts on
# the bare key instead, so CTRL+S would fire Split rather than Save.
VK = {
    "CTRL": 0xA2,
    "SHIFT": 0xA0,
    "ALT": 0xA4,
    "SPACE": 0x20,
    "DELETE": 0x2E,
    "ENTER": 0x0D,
    "ESC": 0x1B,
    "TAB": 0x09,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "COMMA": 0xBC,
    "SLASH": 0xBF,
    "BACKSLASH": 0xDC,
    "BACKSPACE": 0x08,
}
VK.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update({"F%d" % n: 0x6F + n for n in range(1, 13)})

EXTENDED_KEYS = {"LEFT", "UP", "RIGHT", "DOWN", "DELETE"}
MODIFIERS = ("CTRL", "SHIFT", "ALT")


class WindowError(RuntimeError):
    """Raised when Cakewalk Next cannot be found or focused."""


if IS_WINDOWS:

    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    # ctypes defaults every return value to C int. On 64-bit that silently
    # truncates HANDLE/HGLOBAL/LRESULT values, and dereferencing the result
    # crashes the process, so the pointer-returning calls are declared here.
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_void_p
    ]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]


def _require_windows():
    if not IS_WINDOWS:
        raise WindowError(
            "Controlling Cakewalk Next requires Windows; this server is running "
            "on %s. Project inspection and MIDI tools still work." % sys.platform
        )


def _process_name(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value.rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(handle)
    return None


def _window_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def find_windows():
    """Return every visible top-level window owned by Next.exe."""
    _require_windows()
    results = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _process_name(pid.value)
        if name == PROCESS_NAME or title.endswith(WINDOW_TITLE_SUFFIX):
            results.append({
                "hwnd": int(hwnd),
                "title": title,
                "pid": pid.value,
                "process": name,
                "minimized": bool(user32.IsIconic(hwnd)),
            })
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    # The main window carries the project name and the longest title.
    results.sort(key=lambda w: len(w["title"]), reverse=True)
    return results


def main_window():
    """Return the primary Cakewalk Next window, or raise if it is not running."""
    windows = find_windows()
    if not windows:
        raise WindowError(
            "Cakewalk Next is not running (no visible Next.exe window found). "
            "Start it, open a project, then retry."
        )
    return windows[0]


def _thread_of(hwnd):
    return user32.GetWindowThreadProcessId(hwnd, None) if hwnd else 0


def focus(hwnd, timeout=2.0):
    """Bring a window to the foreground, working around Windows focus-stealing rules.

    A process that does not already own the foreground is normally refused by
    SetForegroundWindow. The reliable workaround is to attach our input queue to
    the thread that *currently* owns the foreground - attaching only to the
    target thread is not enough - so that Windows treats the call as coming from
    the active app.
    """
    _require_windows()
    if user32.GetForegroundWindow() == hwnd:
        # Still settle. The window may have become foreground a moment ago
        # because the *user* just clicked it, and an input sent on top of that
        # click gets swallowed or merged into a double-click.
        _settle(False)
        return True

    # A window restored from the taskbar is not ready for input the moment it
    # reports itself foreground: it still has to lay out and hand focus to a
    # child control. Keys sent inside that gap are silently dropped, so the
    # settle wait below is longer when we had to un-minimise.
    was_minimized = bool(user32.IsIconic(hwnd))
    if was_minimized:
        user32.ShowWindow(hwnd, SW_RESTORE)

    current = kernel32.GetCurrentThreadId()
    threads = {_thread_of(user32.GetForegroundWindow()), _thread_of(hwnd)}
    threads.discard(0)
    threads.discard(current)

    attached = []
    for thread in threads:
        if user32.AttachThreadInput(current, thread, True):
            attached.append(thread)
    try:
        user32.AllowSetForegroundWindow(ASFW_ANY)
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        for thread in attached:
            user32.AttachThreadInput(current, thread, False)

    if _await_foreground(hwnd, timeout):
        _settle(was_minimized)
        return True

    # A minimise/restore cycle sometimes makes the shell hand focus over.
    user32.ShowWindow(hwnd, SW_MINIMIZE)
    user32.ShowWindow(hwnd, SW_RESTORE)
    if _await_foreground(hwnd, timeout):
        _settle(True)
        return True

    # Last resort, and the one that actually beats a hostile
    # ForegroundLockTimeout: click the window. SetForegroundWindow is refused
    # to background processes, but a synthetic click is ordinary input, and
    # Windows activates whatever gets clicked. The target is the drag strip
    # above the menu bar, which has no controls in it.
    if _click_to_activate(hwnd) and _await_foreground(hwnd, timeout):
        _settle(True)
        return True
    return False


TITLE_STRIP_Y = 7  # logical pixels below the window top: above the menu bar


def _click_to_activate(hwnd):
    """Click the window's title strip purely to give it the foreground."""
    try:
        left, top, width, height = window_rect(hwnd)
        scale = dpi_scale(hwnd)
    except WindowError:
        return False
    if width < 40 or height < 40:
        return False
    # Horizontally centred, so it cannot land on the window's own buttons.
    x = left + width // 2
    y = top + max(2, int(TITLE_STRIP_Y * scale))

    # If something else covers that point, clicking would activate *it* and
    # deliver a stray click into an unrelated application. Refuse instead.
    covering = window_at(x, y)
    if covering is None or owner_pid(covering) != owner_pid(hwnd):
        return False
    try:
        click(x, y)
    except WindowError:
        return False
    return True


def _await_foreground(hwnd, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return False


def _settle(was_minimized):
    """Give the newly focused window time to accept keyboard input."""
    time.sleep(SETTLE_AFTER_RESTORE if was_minimized else SETTLE_AFTER_FOCUS)


SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000


def foreground_lock_ms():
    """Return the system's foreground lock timeout in milliseconds.

    Windows refuses SetForegroundWindow from a process that does not already
    own the foreground until this timeout since the last user input elapses.
    A large value (some tuning utilities set 0x7FFFFFFF) means focus can only
    change when the user themselves clicks another window.
    """
    _require_windows()
    value = wintypes.DWORD()
    user32.SystemParametersInfoW(
        SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(value), 0
    )
    return value.value


def focus_failure_reason():
    """Explain, in one actionable sentence, why focus was refused."""
    try:
        lock = foreground_lock_ms()
        active = _window_title(user32.GetForegroundWindow())
    except Exception:  # pragma: no cover - diagnostics must never mask the error
        return ""
    if lock > 60000:
        return (
            " This machine's foreground lock timeout is %d ms, so Windows will "
            "not let a background process take focus at all while another app "
            "(currently %r) is active. Click the Cakewalk Next window once, then "
            "retry." % (lock, active)
        )
    return " %r currently holds the foreground; click Cakewalk Next once, then retry." % active


def parse_chord(chord):
    """Turn 'CTRL+SHIFT+T' into (['CTRL','SHIFT'], 'T')."""
    parts = [p.strip().upper() for p in chord.split("+") if p.strip()]
    if not parts:
        raise WindowError("Empty key chord.")
    modifiers = [p for p in parts if p in MODIFIERS]
    keys = [p for p in parts if p not in MODIFIERS]
    if len(keys) != 1:
        raise WindowError(
            "Key chord %r must name exactly one non-modifier key, got %r."
            % (chord, keys)
        )
    key = keys[0]
    if key not in VK:
        raise WindowError("Unknown key %r in chord %r." % (key, chord))
    return modifiers, key


def _key_event(name, keyup):
    """Build one INPUT record, carrying the hardware scan code alongside the VK.

    Sending wScan=0 is enough for apps that read WM_KEYDOWN's virtual key, but
    anything resolving shortcuts from scan codes ignores such events.
    """
    flags = KEYEVENTF_KEYUP if keyup else 0
    if name in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    vk = VK[name]
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return event


def foreground_is(hwnd):
    """True when ``hwnd`` still owns the foreground."""
    _require_windows()
    return user32.GetForegroundWindow() == hwnd


def require_foreground(hwnd, what="the keystroke"):
    """Abort unless ``hwnd`` is still foreground.

    SendInput goes to whatever holds the foreground *now*, not to whatever we
    focused earlier. Focus can move between one keystroke and the next - closing
    a dialog can hand it back to the previously active application - and without
    this check the rest of a sequence would be typed into that other app.
    """
    if not foreground_is(hwnd):
        actual = _window_title(user32.GetForegroundWindow())
        raise WindowError(
            "Cakewalk Next lost the foreground to %r before %s was sent; "
            "nothing further was typed.%s"
            % (actual or "another window", what, focus_failure_reason())
        )
    return True


def send_chord(chord, expect_hwnd=None):
    """Press a key chord in the foreground window.

    Pass ``expect_hwnd`` to refuse to send unless that window is still focused.
    """
    _require_windows()
    if expect_hwnd is not None:
        require_foreground(expect_hwnd, "%r" % chord)
    modifiers, key = parse_chord(chord)
    sequence = [_key_event(m, False) for m in modifiers]
    sequence.append(_key_event(key, False))
    sequence.append(_key_event(key, True))
    sequence.extend(_key_event(m, True) for m in reversed(modifiers))

    # Modifiers need a beat to register before the base key arrives; a single
    # batched SendInput can be processed faster than the target app samples
    # keyboard state.
    if modifiers:
        down = (INPUT * len(modifiers))(*sequence[: len(modifiers)])
        user32.SendInput(len(modifiers), down, ctypes.sizeof(INPUT))
        time.sleep(MODIFIER_SETTLE)
        sequence = sequence[len(modifiers):]

    array = (INPUT * len(sequence))(*sequence)
    sent = user32.SendInput(len(sequence), array, ctypes.sizeof(INPUT))
    if sent != len(sequence):
        raise WindowError(
            "SendInput delivered %d of %d key events (Windows error %d). Another "
            "app may be blocking input, or Next may be running elevated while "
            "this server is not." % (sent, len(sequence), ctypes.get_last_error())
        )
    return True


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def window_rect(hwnd):
    """Return (left, top, width, height) in physical pixels."""
    _require_windows()
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise WindowError("GetWindowRect failed for window %s." % hwnd)
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def dpi_scale(hwnd):
    """Return the window's DPI scale factor (1.0 at 96 DPI, 1.25 at 125%)."""
    _require_windows()
    try:
        return user32.GetDpiForWindow(hwnd) / 96.0
    except Exception:  # pragma: no cover - pre-Windows 10 1607
        return 1.0


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


GA_ROOT = 2


def window_at(x, y):
    """Return the top-level window actually occupying a screen point."""
    _require_windows()
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    return int(root)


def owner_pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def click_in(pid, x, y, what="a control"):
    """Click only if the point really belongs to process ``pid``.

    A window's rect says where it *would* be, not whether it is on top. Clicking
    a computed position while another application covers it sends the click to
    that application instead - which once put a click into an unrelated browser
    window. Checking what is actually under the point first makes that
    impossible rather than unlikely.
    """
    _require_windows()
    target = window_at(x, y)
    if target is None:
        raise WindowError("Nothing is at (%d, %d); refusing to click." % (x, y))
    actual = owner_pid(target)
    if actual != pid:
        raise WindowError(
            "Refusing to click %s at (%d, %d): that point belongs to %r "
            "(pid %d), not Cakewalk Next (pid %d). Bring Next to the front and "
            "retry." % (what, x, y, _window_title(target) or "another window",
                        actual, pid)
        )
    return click(x, y)


def click(x, y):
    """Left-click at a physical screen coordinate.

    Prefer ``click_in`` wherever the intended target is known: this function
    clicks whatever happens to be on top at that point.
    """
    _require_windows()
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    events = []
    for flag in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        event = INPUT()
        event.type = INPUT_MOUSE
        event.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0)
        events.append(event)
    array = (INPUT * 2)(*events)
    if user32.SendInput(2, array, ctypes.sizeof(INPUT)) != 2:
        raise WindowError(
            "SendInput could not deliver the mouse click (Windows error %d)."
            % ctypes.get_last_error()
        )
    return True


def double_click_in(pid, x, y, what="a control"):
    """Double-click, with the same ownership check as click_in."""
    _require_windows()
    click_in(pid, x, y, what)
    time.sleep(0.06)
    return click(x, y)


def send_text(text):
    """Type a string as Unicode key events, independent of keyboard layout.

    Used for file paths, which contain characters (':', '\\\\') whose position
    varies between layouts; KEYEVENTF_UNICODE sidesteps that entirely.
    """
    _require_windows()
    events = []
    for char in text:
        for keyup in (False, True):
            flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
            event = INPUT()
            event.type = INPUT_KEYBOARD
            event.ki = KEYBDINPUT(wVk=0, wScan=ord(char), dwFlags=flags, time=0, dwExtraInfo=0)
            events.append(event)
    if not events:
        return True
    array = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise WindowError(
            "SendInput delivered %d of %d characters." % (sent // 2, len(text))
        )
    return True


def _class_name(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E


def get_clipboard_text():
    """Return the clipboard's text, or None if it holds none."""
    _require_windows()
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.c_wchar_p(pointer).value
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text):
    """Replace the clipboard's contents with ``text``."""
    _require_windows()
    if not user32.OpenClipboard(None):
        raise WindowError("Could not open the clipboard; another app is holding it.")
    try:
        user32.EmptyClipboard()
        buffer = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buffer)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            raise WindowError("GlobalAlloc failed while setting the clipboard.")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise WindowError("GlobalLock failed while setting the clipboard.")
        ctypes.memmove(pointer, buffer, size)
        kernel32.GlobalUnlock(handle)
        # Ownership of the block transfers to the clipboard on success.
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise WindowError("SetClipboardData failed.")
    finally:
        user32.CloseClipboard()
    return True


def control_text(hwnd):
    """Read a control's text with WM_GETTEXT (works across process boundaries)."""
    length = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, None)
    if not length:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, ctypes.byref(buffer))
    return buffer.value


WM_SETTEXT = 0x000C
BM_CLICK = 0x00F5
IDOK = 1
FILENAME_FIELD_ID = 1148  # cmb13, the file dialog's filename combo


def descendants(root):
    """Return every descendant window of ``root``.

    EnumChildWindows already walks the entire tree, so this must not recurse -
    doing so returns each control once per ancestor level.
    """
    _require_windows()
    found = []

    def callback(hwnd, _lparam):
        found.append(int(hwnd))
        return True

    user32.EnumChildWindows(root, WNDENUMPROC(callback), 0)
    return found


def find_control(root, classname=None, ctrl_id=None):
    """Return the first descendant matching a window class and/or control id."""
    for hwnd in descendants(root):
        if classname and _class_name(hwnd) != classname:
            continue
        if ctrl_id is not None and user32.GetDlgCtrlID(hwnd) != ctrl_id:
            continue
        return hwnd
    return None


def set_control_text(hwnd, text):
    """Set a control's text with WM_SETTEXT, which needs no keyboard focus.

    Typing into the file dialog races its autocomplete and depends on which
    control happens to hold focus; posting the text is deterministic.
    """
    _require_windows()
    buffer = ctypes.create_unicode_buffer(text)
    user32.SendMessageW(hwnd, WM_SETTEXT, 0, ctypes.byref(buffer))
    return control_text(hwnd) == text


def click_control(hwnd):
    """Press a button by message rather than by moving the mouse."""
    _require_windows()
    user32.SendMessageW(hwnd, BM_CLICK, 0, None)
    return True


MENU_WINDOW_TITLE = "menu"


def find_popup_menu(pid, timeout=2.0):
    """Wait for Cakewalk Next's popup menu window to appear.

    Next is a JUCE application; an open menu is a separate top-level window of
    the same JUCE class whose title is literally "menu". Waiting for it proves
    the menu-bar click landed before any item is clicked blind.
    """
    _require_windows()
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = []

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and _window_title(hwnd) == MENU_WINDOW_TITLE:
                found.append(int(hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if found:
            return found[0]
        time.sleep(0.1)
    return None


def find_dialog(pid, timeout=6.0):
    """Wait for a standard dialog (class #32770) owned by ``pid``.

    Used to confirm that a menu click really opened the file picker before any
    text is typed, so a mis-aimed click cannot scatter keystrokes into the app.
    """
    _require_windows()
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = []

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and _class_name(hwnd) == "#32770":
                found.append({"hwnd": int(hwnd), "title": _window_title(hwnd)})
            return True

        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if found:
            return found[0]
        time.sleep(0.15)
    return None
