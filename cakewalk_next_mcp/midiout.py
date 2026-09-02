"""Send live MIDI to a Windows MIDI output port, via winmm through ctypes.

Cakewalk Next accepts real-time MIDI on any input device enabled in
*Preferences > MIDI*. Windows ships no virtual loopback, so on a stock machine
the only output is the Microsoft GS Wavetable Synth, which Next cannot listen
to. Installing a virtual MIDI cable (loopMIDI) creates a port that appears as
both an output and an input: this module writes to the output, Next hears the
input, and notes arrive with real timing instead of via a file.

Everything here is ctypes, so the server still has no dependency beyond the
MCP SDK.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"
winmm = ctypes.WinDLL("winmm") if IS_WINDOWS else None

MMSYSERR_NOERROR = 0
CALLBACK_NULL = 0

NOTE_OFF = 0x80
NOTE_ON = 0x90
CONTROL_CHANGE = 0xB0
PROGRAM_CHANGE = 0xC0
ALL_NOTES_OFF = 123
ALL_SOUND_OFF = 120

# A single tool call must not tie up the server indefinitely.
MAX_PHRASE_SECONDS = 60.0

# Ports that exist on every Windows box but cannot reach a DAW.
_BUILTIN_SYNTH = "microsoft gs wavetable synth"


class MidiError(RuntimeError):
    """Raised when a MIDI port cannot be opened or written."""


if IS_WINDOWS:

    class MIDIOUTCAPS(ctypes.Structure):
        _fields_ = [
            ("wMid", wintypes.WORD),
            ("wPid", wintypes.WORD),
            ("vDriverVersion", wintypes.UINT),
            ("szPname", ctypes.c_wchar * 32),
            ("wTechnology", wintypes.WORD),
            ("wVoices", wintypes.WORD),
            ("wNotes", wintypes.WORD),
            ("wChannelMask", wintypes.WORD),
            ("dwSupport", wintypes.DWORD),
        ]

    class MIDIINCAPS(ctypes.Structure):
        _fields_ = [
            ("wMid", wintypes.WORD),
            ("wPid", wintypes.WORD),
            ("vDriverVersion", wintypes.UINT),
            ("szPname", ctypes.c_wchar * 32),
            ("dwSupport", wintypes.DWORD),
        ]


def _require_windows():
    if not IS_WINDOWS:
        raise MidiError("Live MIDI output requires Windows; this is %s." % sys.platform)


def _error_text(code):
    buffer = ctypes.create_unicode_buffer(256)
    winmm.midiOutGetErrorTextW(code, buffer, 256)
    return buffer.value or ("error %d" % code)


def list_outputs():
    """Return every MIDI output port, flagging which can reach a DAW."""
    _require_windows()
    ports = []
    for index in range(winmm.midiOutGetNumDevs()):
        caps = MIDIOUTCAPS()
        if winmm.midiOutGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)):
            continue
        name = caps.szPname
        ports.append({
            "index": index,
            "name": name,
            "voices": caps.wVoices,
            # The built-in synth plays through the speakers; it is not a route
            # into another application.
            "usable_for_next": name.strip().lower() != _BUILTIN_SYNTH,
        })
    return ports


def list_inputs():
    """Return every MIDI input port. Next can only listen to one of these."""
    _require_windows()
    ports = []
    for index in range(winmm.midiInGetNumDevs()):
        caps = MIDIINCAPS()
        if winmm.midiInGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)):
            continue
        ports.append({"index": index, "name": caps.szPname})
    return ports


def resolve_port(port=None):
    """Pick an output port by index, by (partial) name, or automatically."""
    ports = list_outputs()
    if not ports:
        raise MidiError(
            "No MIDI output ports exist on this machine. Install a virtual MIDI "
            "cable such as loopMIDI to create one."
        )

    if port is None:
        usable = [p for p in ports if p["usable_for_next"]]
        if not usable:
            raise MidiError(
                "The only MIDI output is %r, which plays through the Windows "
                "synth and cannot be heard by Cakewalk Next. Install loopMIDI to "
                "create a virtual port, then enable it in Next under "
                "Preferences > MIDI." % ports[0]["name"]
            )
        return usable[0]

    if isinstance(port, int):
        for candidate in ports:
            if candidate["index"] == port:
                return candidate
        raise MidiError(
            "No MIDI output with index %d. Available: %s."
            % (port, ", ".join("%d=%s" % (p["index"], p["name"]) for p in ports))
        )

    needle = str(port).strip().lower()
    matches = [p for p in ports if needle in p["name"].lower()]
    if not matches:
        raise MidiError(
            "No MIDI output matching %r. Available: %s."
            % (port, ", ".join(p["name"] for p in ports))
        )
    return matches[0]


class Output:
    """An open MIDI output port. Always silences its channels on close."""

    def __init__(self, port=None):
        _require_windows()
        self.port = resolve_port(port)
        self.handle = wintypes.HANDLE()
        code = winmm.midiOutOpen(
            ctypes.byref(self.handle), self.port["index"], 0, 0, CALLBACK_NULL
        )
        if code != MMSYSERR_NOERROR:
            raise MidiError(
                "Could not open MIDI output %r: %s. Another application may hold "
                "it open exclusively." % (self.port["name"], _error_text(code))
            )

    def send(self, status, data1, data2=0):
        message = (status & 0xFF) | ((data1 & 0x7F) << 8) | ((data2 & 0x7F) << 16)
        code = winmm.midiOutShortMsg(self.handle, message)
        if code != MMSYSERR_NOERROR:
            raise MidiError("midiOutShortMsg failed: %s" % _error_text(code))

    def note_on(self, note, velocity, channel=0):
        self.send(NOTE_ON | (channel & 0x0F), note, velocity)

    def note_off(self, note, channel=0):
        self.send(NOTE_OFF | (channel & 0x0F), note, 0)

    def program_change(self, program, channel=0):
        self.send(PROGRAM_CHANGE | (channel & 0x0F), program)

    def panic(self):
        """Silence every channel. Cheap insurance against a stuck note."""
        for channel in range(16):
            try:
                self.send(CONTROL_CHANGE | channel, ALL_NOTES_OFF, 0)
                self.send(CONTROL_CHANGE | channel, ALL_SOUND_OFF, 0)
            except MidiError:
                pass

    def close(self):
        if self.handle:
            self.panic()
            winmm.midiOutReset(self.handle)
            winmm.midiOutClose(self.handle)
            self.handle = wintypes.HANDLE()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def play(events, tempo_bpm=120.0, channel=0, port=None, program=None):
    """Play timed note events, blocking until the phrase ends.

    ``events`` are dicts with ``pitch`` (0-127), ``start`` and ``duration`` in
    beats, and optional ``velocity``. Notes are flattened into an ordered
    note-on/note-off timeline and emitted against a monotonic clock, so timing
    does not drift across a long phrase.
    """
    if not events:
        raise MidiError("No notes to play.")
    if not 1.0 <= tempo_bpm <= 960.0:
        raise MidiError("Tempo %s out of range 1-960 BPM." % tempo_bpm)

    seconds_per_beat = 60.0 / tempo_bpm
    timeline = []
    for event in events:
        start = float(event["start"]) * seconds_per_beat
        end = start + float(event["duration"]) * seconds_per_beat
        velocity = int(event.get("velocity", 100))
        timeline.append((start, 1, int(event["pitch"]), velocity))
        timeline.append((end, 0, int(event["pitch"]), 0))

    span = max(t[0] for t in timeline)
    if span > MAX_PHRASE_SECONDS:
        raise MidiError(
            "Phrase is %.1fs long; the limit is %.0fs so a tool call cannot hang. "
            "Split it, or raise the tempo." % (span, MAX_PHRASE_SECONDS)
        )

    # Note-offs sort before note-ons at equal times so a repeated pitch retriggers.
    timeline.sort(key=lambda item: (item[0], item[1]))

    played = 0
    with Output(port) as out:
        if program is not None:
            out.program_change(program, channel)
        origin = time.perf_counter()
        for when, kind, pitch, velocity in timeline:
            delay = when - (time.perf_counter() - origin)
            if delay > 0:
                time.sleep(delay)
            if kind:
                out.note_on(pitch, velocity, channel)
                played += 1
            else:
                out.note_off(pitch, channel)
        # Let the last note ring its full length before the port is reset.
        tail = span - (time.perf_counter() - origin)
        if tail > 0:
            time.sleep(min(tail, 2.0))
        return {"port": out.port["name"], "notes_played": played, "seconds": round(span, 3)}
