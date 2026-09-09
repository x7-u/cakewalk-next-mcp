"""Writing whole arrangements: form, groove, and the parts themselves.

The rule this module is built around is that a part has to be *about* something.
The first melody written for this server picked, bar by bar, an index into
whatever chord was underneath. Every note was consonant and the result was
still unusable: because the chord tones change each bar, the same "shape" drew
a different contour every time, so the rhythm repeated but the tune never did.
It read as arpeggio noodling.

So melodies here are built from a motif -- a fixed rhythmic cell and a fixed
melodic contour -- which is then stated, sequenced, varied and resolved. The
contour is mapped onto each chord's consonant tones, but the *shape* survives
the chord change, which is what makes a tune recognisable.

Timing conventions, all in beats:
  * swing pushes offbeats late
  * lay describes a part playing deliberately behind the beat
  * lead pulls a part early -- subs need it, because a low fundamental takes
    several cycles to speak and reads late against a sharp kick transient
"""

from __future__ import annotations

import random

from . import theory

BEATS_PER_BAR = 4


# --------------------------------------------------------------------------
# Styles. Each is a complete set of defaults for a genre.
# --------------------------------------------------------------------------
STYLES = {
    "lofi": dict(
        tempo=84, mode="major", progression="lofi_wistful", lift="lofi_lift",
        swing=0.055, backbeat=(1.0, 3.0), lay=0.035, feel="half-time boom bap",
        kick=[[0.0, 0.75, 2.5], [0.0, 2.5, 3.25]],
        keys_register=(52, 72), bass_octave=2, melody_register=(69, 84),
        description="dusty, behind-the-beat, 7ths and 9ths, sub kept simple",
    ),
    "neo_soul": dict(
        tempo=92, mode="major", progression="neo_soul_loop", lift="jazz_251",
        swing=0.045, backbeat=(1.0, 3.0), lay=0.030, feel="laid-back 4/4",
        kick=[[0.0, 1.75, 2.5], [0.0, 2.5, 3.5]],
        keys_register=(52, 74), bass_octave=2, melody_register=(69, 86),
        description="rich extensions, syncopated bass, ghost notes",
    ),
    "trap": dict(
        tempo=140, mode="minor", progression="dark_driving", lift="sad_descent",
        swing=0.0, backbeat=(2.0,), lay=0.0, feel="half-time, 808 led",
        kick=[[0.0, 0.75, 2.5], [0.0, 1.5, 2.5, 3.75]],
        keys_register=(55, 79), bass_octave=1, melody_register=(72, 88),
        description="sparse, dark, rolling hats, long 808 glides",
    ),
    "house": dict(
        tempo=124, mode="minor", progression="modal_dorian", lift="pop_axis",
        swing=0.02, backbeat=(1.0, 3.0), lay=0.0, feel="four to the floor",
        kick=[[0.0, 1.0, 2.0, 3.0], [0.0, 1.0, 2.0, 3.0]],
        keys_register=(55, 76), bass_octave=2, melody_register=(72, 88),
        description="driving, offbeat bass, open hats on the and",
    ),
    "ambient": dict(
        tempo=70, mode="lydian", progression="neo_soul_loop", lift="pop_axis",
        swing=0.0, backbeat=(), lay=0.06, feel="pulseless",
        kick=[[0.0], [0.0]],
        keys_register=(52, 76), bass_octave=2, melody_register=(72, 88),
        description="no backbeat, long sustains, everything blurred",
    ),
}

# --------------------------------------------------------------------------
# Form. Sections in order, each with a length and a density.
#
# `density` scales how much every part plays, 0 silent to 1 full. `lift` marks
# a section that uses the secondary progression and carries the melody.
# --------------------------------------------------------------------------
FORMS = {
    "loop": [
        ("intro", 4, 0.35, False), ("main", 16, 1.0, False), ("outro", 2, 0.3, False),
    ],
    "verse_chorus": [
        ("intro", 4, 0.35, False), ("verse", 8, 0.75, False), ("chorus", 8, 1.0, True),
        ("break", 4, 0.4, False), ("verse", 8, 0.8, False), ("chorus", 8, 1.0, True),
        ("outro", 2, 0.3, False),
    ],
    "short": [
        ("intro", 2, 0.35, False), ("verse", 8, 0.8, False),
        ("chorus", 8, 1.0, True), ("outro", 2, 0.3, False),
    ],
    "long": [
        ("intro", 8, 0.3, False), ("verse", 8, 0.7, False), ("chorus", 8, 1.0, True),
        ("break", 4, 0.4, False), ("verse", 8, 0.8, False), ("chorus", 8, 1.0, True),
        ("bridge", 8, 0.6, False), ("chorus", 8, 1.0, True), ("outro", 4, 0.3, False),
    ],
}


class ComposeError(ValueError):
    """Raised when a request cannot be turned into an arrangement."""


def _bar(n):
    return (n - 1) * BEATS_PER_BAR


def note(pitch, start, duration, velocity):
    return {"pitch": int(pitch), "start": round(max(0.0, start), 4),
            "duration": round(max(0.01, duration), 4),
            "velocity": max(1, min(127, int(velocity)))}


class Section:
    __slots__ = ("name", "start_bar", "bars", "density", "is_lift", "index")

    def __init__(self, name, start_bar, bars, density, is_lift, index):
        self.name, self.start_bar, self.bars = name, start_bar, bars
        self.density, self.is_lift, self.index = density, is_lift, index

    @property
    def end_bar(self):
        return self.start_bar + self.bars - 1

    def contains(self, bar_no):
        return self.start_bar <= bar_no <= self.end_bar


def build_form(form="verse_chorus", bars=None):
    """Lay sections out on the timeline, optionally stretched to `bars`."""
    if form not in FORMS:
        raise ComposeError("Unknown form %r. Known: %s"
                           % (form, ", ".join(sorted(FORMS))))
    spec = list(FORMS[form])
    if bars:
        total = sum(s[1] for s in spec)
        if bars < len(spec):
            raise ComposeError("%d bars cannot hold %d sections" % (bars, len(spec)))
        scale = float(bars) / total
        scaled, running = [], 0
        for i, (name, length, density, lift) in enumerate(spec):
            new = max(1, int(round(length * scale)))
            if i == len(spec) - 1:
                new = max(1, bars - running)
            scaled.append((name, new, density, lift))
            running += new
        spec = scaled

    sections, bar_no = [], 1
    for i, (name, length, density, lift) in enumerate(spec):
        sections.append(Section(name, bar_no, length, density, lift, i))
        bar_no += length
    return sections


def chord_plan(sections, key, mode, progression, lift_progression):
    """One chord per bar for the whole song."""
    main = theory.progression_chords(
        theory.PROGRESSIONS[progression]["numerals"], key, mode)
    lift = theory.progression_chords(
        theory.PROGRESSIONS[lift_progression]["numerals"], key, mode)

    plan = []
    for section in sections:
        loop = lift if section.is_lift else main
        for i in range(section.bars):
            plan.append(dict(loop[i % len(loop)], bar=section.start_bar + i,
                             section=section.name))
    return plan


def voice_plan(plan, register, rootless=True, bass_octave=2):
    """Voice every bar, carrying the previous voicing so the hand barely moves.

    The bass pitch goes in too. Without it the voicer is blind to the one
    interval most likely to sour a correct chord: a 7th or a 3rd sitting close
    above the root down in the mud.
    """
    previous, out = None, []
    for entry in plan:
        bass = 12 * (bass_octave + 1) + entry["root"]
        voicing = theory.voice_chord(entry["root"], entry["quality"],
                                     register=register, previous=previous,
                                     rootless=rootless, bass=bass)
        previous = voicing
        out.append(voicing)
    return out


# --------------------------------------------------------------------------
# Parts
# --------------------------------------------------------------------------
def write_drums(sections, pads, style, rng):
    """Kit part. Hats hold time even where the kick and snare drop out."""
    kick = pads.get("kick")
    snare = pads.get("snare", pads.get("clap"))
    hat = pads.get("closed", pads.get("hat"))
    ohat, shaker, clap = pads.get("open"), pads.get("shaker"), pads.get("clap")
    swing, lay = style["swing"], style["lay"]
    out = []

    for section in sections:
        if section.density < 0.3:
            continue
        for i in range(section.bars):
            n = section.start_bar + i
            b = _bar(n)
            full = section.density >= 0.7
            loud = section.is_lift

            if hat:
                flutter = (i + 1) % 8 == 0 and full
                for step in range(8):
                    if flutter and step == 7:
                        continue
                    t = b + step * 0.5 + (swing if step % 2 else 0.0)
                    base = 78 if step % 2 == 0 else 55
                    out.append(note(hat, t, 0.16, _jit(rng, base + (6 if loud else 0), 7)))
                if flutter:
                    for j in range(4):
                        out.append(note(hat, b + 3.5 + j * 0.125, 0.1,
                                        _jit(rng, 52 + j * 9, 4)))
            if ohat and (i + 1) % 4 == 2:
                out.append(note(ohat, b + 3.5 + swing, 0.34, _jit(rng, 74)))

            if not full:
                continue

            for at in style["kick"][i % len(style["kick"])]:
                out.append(note(kick, b + at, 0.3, _jit(rng, 112 if loud else 104)))
            for at in style["backbeat"]:
                out.append(note(snare, b + at + lay, 0.3, _jit(rng, 104 if loud else 96)))
                if loud and clap and clap != snare:
                    out.append(note(clap, b + at + lay + 0.01, 0.2, _jit(rng, 78)))
            if shaker:
                for j in range(4):
                    out.append(note(shaker, b + 0.5 + j + swing, 0.14,
                                    _jit(rng, 60 if loud else 50, 6)))
    return out


BASS_LEAD = 0.03        # ~21 ms at 84 BPM


def write_bass(plan, sections, style, rng):
    """Roots, on the grid and pulled slightly ahead of it.

    A sub needs several cycles before the ear hears a pitch, so it reads late
    against a kick even when the MIDI is exact. It also does not swing: the
    bass is what everything else is judged against.
    """
    octave = style["bass_octave"]
    # How far ahead of the grid the bass sits. A sub needs it: the fundamental
    # takes several cycles to speak, so an exactly-placed note reads late
    # against a kick. An upright or a picked bass speaks immediately and wants
    # none -- give it the lead and it just sounds early.
    lead = style.get("bass_lead", BASS_LEAD)
    out = []
    for entry in plan:
        section = _section_of(sections, entry["bar"])
        if section.density < 0.35:
            continue
        b = _bar(entry["bar"])
        root = 12 * (octave + 1) + entry["root"]
        loud = section.is_lift
        out.append(note(root, b - lead, 1.6, _jit(rng, 84 if loud else 78, 4)))
        if section.density >= 0.6:
            out.append(note(root, b + 2.5 - lead, 0.9, _jit(rng, 72, 4)))
        if section.density >= 0.75 and entry["bar"] % 2 == 1:
            out.append(note(root, b + 1.75 - lead, 0.4, _jit(rng, 62, 4)))
    return out


def write_keys(plan, voicings, sections, style, rng):
    """The chord bed: a hit on 1, a short one on the and-of-2, one on the and-of-3."""
    swing, lay = style["swing"], style["lay"]
    out = []
    for entry, voicing in zip(plan, voicings):
        section = _section_of(sections, entry["bar"])
        if section.density < 0.25:
            continue
        b = _bar(entry["bar"])
        loud = 66 if section.is_lift else (52 if section.density < 0.5 else 58)
        if section.density < 0.5:
            hits = [(0.0, 3.4, loud - 6)]
        else:
            hits = [(0.0, 1.5, loud), (1.5 + swing, 0.7, loud - 16),
                    (2.5 + swing, 1.2, loud - 8)]
        for at, dur, level in hits:
            # Roll the voicing a few ms so no chord lands as a block, but let
            # every voice release together on the bar line.
            for i, pitch in enumerate(voicing):
                out.append(note(pitch, b + at + lay + i * 0.028,
                                dur - i * 0.028, _jit(rng, level, 7)))
    return out


def write_pad(plan, sections, style, rng, register=(64, 84)):
    """Long tones over the bed. Lifts only, so its entry means something."""
    out = []
    for entry in plan:
        section = _section_of(sections, entry["bar"])
        if not section.is_lift and section.density > 0.4:
            continue
        if section.density < 0.25:
            continue
        voicing = theory.voice_chord(entry["root"], entry["quality"],
                                     register=register, rootless=True, voices=2)
        level = 48 if section.is_lift else 34
        for pitch in voicing[:2]:
            out.append(note(pitch, _bar(entry["bar"]), 3.9, _jit(rng, level, 5)))
    return out


# Rhythmic cells, in beats within one bar: (offset, duration).
MOTIF_CELLS = [
    [(1.5, 0.5), (2.0, 0.5), (2.5, 1.5)],              # three steps off the & of 2
    [(0.5, 0.5), (1.0, 1.25), (2.5, 1.0)],             # opened out
    [(0.0, 0.75), (1.0, 0.5), (1.5, 2.0)],             # front-loaded, long tail
    [(0.5, 0.75), (1.5, 0.5), (2.0, 0.5), (2.5, 1.25)],  # driven, four notes
]
# Melodic contours as scale-step offsets from an anchor.
CONTOURS = [
    (2, 1, 0),        # step down, the most singable shape there is
    (0, 2, 1),        # up then settle
    (3, 1, 0),        # leap in, fall away
    (0, 1, 2, 3),     # rising
]


def write_melody(plan, sections, style, key, mode, rng, register=None):
    """A tune: state the motif, sequence it, vary it, resolve it.

    The contour is fixed and only its anchor moves, so the shape survives each
    chord change. That is the difference between a melody and an arpeggio.
    """
    register = register or style["melody_register"]
    lo, hi = register
    scale, root_pc, mode = theory.scale_pitch_classes(key, mode)
    scale_pitches = sorted(p for p in range(lo, hi + 1) if p % 12 in scale)
    if not scale_pitches:
        raise ComposeError("No scale tones between %d and %d" % (lo, hi))

    lift_sections = [s for s in sections if s.is_lift]
    if not lift_sections:
        lift_sections = [s for s in sections if s.density >= 0.7]

    out, previous = [], None
    for phrase_no, section in enumerate(lift_sections):
        cell_base = 0 if phrase_no % 2 == 0 else 1
        for i in range(section.bars):
            bar_no = section.start_bar + i
            entry = _plan_for(plan, bar_no)
            if entry is None:
                continue
            position = i % 4
            # bars 1-2 state and sequence the motif; bar 3 varies; bar 4 resolves
            cell = MOTIF_CELLS[(cell_base + (1 if position == 3 else 0)) % len(MOTIF_CELLS)]
            contour = CONTOURS[0] if position < 2 else CONTOURS[(phrase_no + 1) % len(CONTOURS)]
            resolving = position == 3

            ok = theory.consonant_pitch_classes(entry["root"], entry["quality"])
            usable = [p for p in scale_pitches if p % 12 in ok]
            if not usable:
                usable = scale_pitches

            anchor = _pick_anchor(usable, previous, resolving, entry, rng, phrase_no)
            level = 82 if section.index >= len(sections) // 2 else 74

            for k, (at, dur) in enumerate(cell):
                step = contour[k % len(contour)]
                pitch = _step_from(usable, anchor, -step if not resolving else -step)
                previous = pitch
                out.append(note(pitch, _bar(bar_no) + at + 0.03, dur,
                                _jit(rng, level, 7)))
    return out


def _pick_anchor(usable, previous, resolving, entry, rng, phrase_no):
    """Start each bar near where the last one ended; land home when resolving."""
    if resolving:
        targets = [p for p in usable if p % 12 == entry["root"] % 12]
        if targets:
            return min(targets, key=lambda p: abs(p - (previous or p)))
    if previous is None:
        return usable[min(len(usable) - 1, int(len(usable) * 0.65))]
    near = sorted(usable, key=lambda p: abs(p - previous))
    return near[min(len(near) - 1, rng.randint(0, 2))]


def _step_from(usable, anchor, steps):
    if anchor not in usable:
        anchor = min(usable, key=lambda p: abs(p - anchor))
    idx = usable.index(anchor) + steps
    idx = max(0, min(len(usable) - 1, idx))
    return usable[idx]


def _section_of(sections, bar_no):
    for s in sections:
        if s.contains(bar_no):
            return s
    return sections[-1]


def _plan_for(plan, bar_no):
    for entry in plan:
        if entry["bar"] == bar_no:
            return entry
    return None


def _jit(rng, base, spread=6):
    return max(1, min(127, base + rng.randint(-spread, spread)))


# --------------------------------------------------------------------------
# The whole song
# --------------------------------------------------------------------------
def compose(style="lofi", key="C", mode=None, form="verse_chorus", bars=None,
            tempo=None, progression=None, lift_progression=None, seed=1129,
            pads=None, parts=("drums", "bass", "keys", "pad", "melody"),
            bass_lead=None):
    """Compose a complete arrangement and check it before handing it back."""
    if style not in STYLES:
        raise ComposeError("Unknown style %r. Known: %s"
                           % (style, ", ".join(sorted(STYLES))))
    spec = dict(STYLES[style])
    mode = mode or spec["mode"]
    tempo = tempo or spec["tempo"]
    progression = progression or spec["progression"]
    lift_progression = lift_progression or spec["lift"]
    for name in (progression, lift_progression):
        if name not in theory.PROGRESSIONS:
            raise ComposeError("Unknown progression %r. Known: %s"
                               % (name, ", ".join(sorted(theory.PROGRESSIONS))))

    if bass_lead is not None:
        spec["bass_lead"] = float(bass_lead)

    rng = random.Random(seed)
    sections = build_form(form, bars)
    total_bars = sections[-1].end_bar
    plan = chord_plan(sections, key, mode, progression, lift_progression)
    voicings = voice_plan(plan, spec["keys_register"], bass_octave=spec["bass_octave"])

    pads = pads or {}
    written = {}
    if "drums" in parts and pads:
        written["drums"] = write_drums(sections, pads, spec, rng)
    if "bass" in parts:
        written["bass"] = write_bass(plan, sections, spec, rng)
    if "keys" in parts:
        written["keys"] = write_keys(plan, voicings, sections, spec, rng)
    if "pad" in parts:
        written["pad"] = write_pad(plan, sections, spec, rng)
    if "melody" in parts:
        written["melody"] = write_melody(plan, sections, spec, key, mode, rng)

    written = {k: v for k, v in written.items() if v}
    _trim_to_bar_line(written, total_bars)

    problems = theory.check_material(
        written, key=key, mode=mode, bars=total_bars,
        beats_per_bar=BEATS_PER_BAR,
        chords=[_plan_for(plan, b) for b in range(1, total_bars + 1)])

    return {
        "style": style, "key": key, "mode": mode, "tempo_bpm": tempo,
        "bass_lead": spec.get("bass_lead", BASS_LEAD),
        "form": form, "bars": total_bars,
        "seconds": round(total_bars * BEATS_PER_BAR * 60.0 / tempo, 2),
        "sections": [dict(name=s.name, start_bar=s.start_bar, bars=s.bars,
                          density=s.density, lift=s.is_lift) for s in sections],
        "progression": {
            "main": theory.PROGRESSIONS[progression]["numerals"],
            "lift": theory.PROGRESSIONS[lift_progression]["numerals"],
            "chords_per_bar": [
                {"bar": e["bar"], "chord": e["root_name"] + e["quality"],
                 "numeral": e["numeral"], "section": e["section"]} for e in plan],
        },
        "parts": written,
        "note_counts": {k: len(v) for k, v in written.items()},
        "problems": problems,
    }


def _trim_to_bar_line(parts, bars):
    """Nothing may ring past the final bar line.

    An overhang of even a fraction of a beat makes the DAW round the project up
    to another bar, so the arrangement reads as longer than it was written.
    """
    limit = bars * BEATS_PER_BAR
    for notes in parts.values():
        for n in notes:
            if n["start"] + n["duration"] > limit:
                n["duration"] = round(max(0.05, limit - n["start"]), 4)
