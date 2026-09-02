"""Minimal Standard MIDI File reader/writer (format 0/1). No dependencies.

Cakewalk Next has no scripting API, so MIDI files are the supported route for
getting generated musical material into a project (drag the .mid onto the
track area, or use File > Import).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

TICKS_PER_BEAT = 480

_NOTE_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_NAME_RE = re.compile(r"^([A-Ga-g])([#b]{0,2})(-?\d{1,2})$")
_SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


class MidiError(ValueError):
    """Raised for malformed MIDI input or unrepresentable musical data."""


def parse_pitch(value):
    """Convert a MIDI note number or a note name such as 'C4'/'F#3'/'Bb5' to 0-127.

    Middle C (MIDI 60) is C4, matching Cakewalk Next's default display.
    """
    if isinstance(value, bool):
        raise MidiError("Invalid pitch %r: expected a note number or name." % (value,))
    if isinstance(value, int):
        if not 0 <= value <= 127:
            raise MidiError("Pitch %d out of range; MIDI notes are 0-127." % value)
        return value

    text = str(value).strip()
    match = _NOTE_NAME_RE.match(text)
    if not match:
        raise MidiError(
            "Could not parse pitch %r. Use a MIDI number (0-127) or a name like "
            "'C4', 'F#3', 'Bb5' (middle C = C4 = 60)." % (value,)
        )
    letter, accidental, octave = match.groups()
    semitone = _NOTE_BASE[letter.upper()]
    semitone += accidental.count("#") - accidental.count("b")
    midi = semitone + (int(octave) + 1) * 12
    if not 0 <= midi <= 127:
        raise MidiError(
            "Pitch %r resolves to MIDI %d, outside the valid range 0-127."
            % (value, midi)
        )
    return midi


def pitch_name(midi):
    """Render a MIDI note number as a sharp-spelled note name (60 -> 'C4')."""
    return "%s%d" % (_SHARP_NAMES[midi % 12], midi // 12 - 1)


@dataclass
class Note:
    """A single note event positioned in beats (quarter notes) from clip start."""

    pitch: int
    start_beats: float
    duration_beats: float
    velocity: int = 100

    def validate(self):
        if not 0 <= self.pitch <= 127:
            raise MidiError("Pitch %d out of range 0-127." % self.pitch)
        if not 1 <= self.velocity <= 127:
            raise MidiError(
                "Velocity %d out of range; use 1-127 (0 is silent, being "
                "equivalent to note-off)." % self.velocity
            )
        if self.start_beats < 0:
            raise MidiError("Note start %s is negative." % self.start_beats)
        if self.duration_beats <= 0:
            raise MidiError(
                "Note duration %s must be greater than 0 beats." % self.duration_beats
            )


@dataclass
class Track:
    """One MIDI track: a named, channel-assigned collection of notes."""

    name: str = ""
    channel: int = 0
    program: int = None
    notes: list = field(default_factory=list)

    def validate(self):
        if not 0 <= self.channel <= 15:
            raise MidiError("Channel %d out of range 0-15." % self.channel)
        if self.program is not None and not 0 <= self.program <= 127:
            raise MidiError("Program %d out of range 0-127." % self.program)
        for note in self.notes:
            note.validate()


def _vlq(value):
    """Encode an integer as a MIDI variable-length quantity."""
    if value < 0:
        raise MidiError("Cannot encode negative delta time %d." % value)
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def _meta(kind, payload):
    return b"\xff" + bytes([kind]) + _vlq(len(payload)) + payload


def _chunk(tag, payload):
    return tag + struct.pack(">I", len(payload)) + payload


def _build_track(events):
    """Serialise (tick, order, message) events into an MTrk chunk payload.

    ``order`` breaks ties so note-offs are emitted before note-ons at the same
    tick; without it a re-struck pitch would be silenced by its own predecessor.
    """
    events.sort(key=lambda item: (item[0], item[1]))
    out = bytearray()
    previous = 0
    for tick, _order, message in events:
        out += _vlq(tick - previous)
        out += message
        previous = tick
    out += _vlq(0) + _meta(0x2F, b"")
    return bytes(out)


def write_midi(tracks, tempo_bpm=120.0, time_signature=(4, 4), ticks_per_beat=TICKS_PER_BEAT):
    """Serialise tracks to a format-1 Standard MIDI File.

    Track 0 is a conductor track carrying tempo and time signature; each
    supplied track becomes its own MTrk so Next imports them as separate clips.
    """
    if not tracks:
        raise MidiError("At least one track is required.")
    if not 1.0 <= tempo_bpm <= 960.0:
        raise MidiError("Tempo %s out of the supported range 1-960 BPM." % tempo_bpm)
    numerator, denominator = time_signature
    if numerator < 1 or denominator < 1 or denominator & (denominator - 1):
        raise MidiError(
            "Invalid time signature %s/%s; the denominator must be a power of "
            "two (2, 4, 8, 16...)." % (numerator, denominator)
        )

    header = struct.pack(">HHH", 1, len(tracks) + 1, ticks_per_beat)
    micros_per_beat = int(round(60000000.0 / tempo_bpm))
    conductor = [
        (0, 0, _meta(0x51, struct.pack(">I", micros_per_beat)[1:])),
        (0, 1, _meta(0x58, bytes([numerator, denominator.bit_length() - 1, 24, 8]))),
    ]

    chunks = [_chunk(b"MThd", header), _chunk(b"MTrk", _build_track(conductor))]

    for track in tracks:
        track.validate()
        events = []
        if track.name:
            events.append((0, 0, _meta(0x03, track.name.encode("utf-8", "replace"))))
        if track.program is not None:
            events.append((0, 1, bytes([0xC0 | track.channel, track.program])))
        for note in track.notes:
            on = int(round(note.start_beats * ticks_per_beat))
            off = on + max(1, int(round(note.duration_beats * ticks_per_beat)))
            events.append((on, 3, bytes([0x90 | track.channel, note.pitch, note.velocity])))
            events.append((off, 2, bytes([0x80 | track.channel, note.pitch, 0])))
        chunks.append(_chunk(b"MTrk", _build_track(events)))

    return b"".join(chunks)


def _read_vlq(data, pos):
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError("Truncated variable-length quantity.")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise MidiError("Variable-length quantity longer than 4 bytes.")


def read_midi(data):
    """Parse a Standard MIDI File into tempo, time signature and per-track notes."""
    if len(data) < 14 or data[:4] != b"MThd":
        raise MidiError(
            "Not a Standard MIDI File: missing the 'MThd' header. Cakewalk Next "
            "exports MIDI by right-clicking a MIDI clip header > Export to File."
        )
    header_len = struct.unpack_from(">I", data, 4)[0]
    fmt, _ntracks, division = struct.unpack_from(">HHH", data, 8)
    if division & 0x8000:
        raise MidiError("SMPTE time division is not supported; expected ticks per beat.")
    ticks_per_beat = division or TICKS_PER_BEAT

    pos = 8 + header_len
    tempo_bpm = 120.0
    time_signature = [4, 4]
    tracks = []

    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        length = struct.unpack_from(">I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + length]
        pos += 8 + length
        if tag != b"MTrk":
            continue

        name = ""
        open_notes = {}
        notes = []
        channels = set()
        tick = 0
        cursor = 0
        status = 0

        while cursor < len(body):
            delta, cursor = _read_vlq(body, cursor)
            tick += delta
            if cursor >= len(body):
                break
            byte = body[cursor]
            if byte & 0x80:
                status = byte
                cursor += 1
            elif not status:
                raise MidiError("Running status used before any status byte.")

            if status == 0xFF:
                kind = body[cursor]
                cursor += 1
                mlen, cursor = _read_vlq(body, cursor)
                payload = body[cursor:cursor + mlen]
                cursor += mlen
                if kind == 0x03 and not name:
                    name = payload.decode("utf-8", "replace")
                elif kind == 0x51 and len(payload) == 3:
                    micros = int.from_bytes(payload, "big")
                    if micros:
                        # SMF stores tempo as integer microseconds per beat, so
                        # the BPM never round-trips exactly; 2dp hides the noise
                        # without flattening genuinely fractional tempos.
                        tempo_bpm = round(60000000.0 / micros, 2)
                elif kind == 0x58 and len(payload) >= 2:
                    time_signature = [payload[0], 1 << payload[1]]
            elif status in (0xF0, 0xF7):
                mlen, cursor = _read_vlq(body, cursor)
                cursor += mlen
            else:
                high = status & 0xF0
                channel = status & 0x0F
                size = 1 if high in (0xC0, 0xD0) else 2
                params = body[cursor:cursor + size]
                cursor += size
                if high == 0x90 and len(params) == 2 and params[1] > 0:
                    channels.add(channel)
                    open_notes[(channel, params[0])] = (tick, params[1])
                elif high == 0x80 or (high == 0x90 and len(params) == 2):
                    started = open_notes.pop((channel, params[0]), None)
                    if started is not None:
                        start_tick, velocity = started
                        notes.append({
                            "pitch": params[0],
                            "note": pitch_name(params[0]),
                            "start_beats": round(start_tick / ticks_per_beat, 6),
                            "duration_beats": round((tick - start_tick) / ticks_per_beat, 6),
                            "velocity": velocity,
                        })

        if notes or name:
            notes.sort(key=lambda n: (n["start_beats"], n["pitch"]))
            longest = max((n["start_beats"] + n["duration_beats"] for n in notes), default=0.0)
            tracks.append({
                "name": name,
                "channels": sorted(channels),
                "note_count": len(notes),
                "length_beats": round(longest, 6),
                "notes": notes,
            })

    return {
        "format": fmt,
        "ticks_per_beat": ticks_per_beat,
        "tempo_bpm": tempo_bpm,
        "time_signature": "%d/%d" % (time_signature[0], time_signature[1]),
        "track_count": len(tracks),
        "tracks": tracks,
    }
