"""Music theory for writing parts that hold together.

This exists because the composition knowledge used to live outside the server,
in whatever throwaway script happened to be generating a track. Two faults got
shipped that way, and neither sounded like "a wrong note" -- they sounded like
the whole track was vaguely out of tune, which is far harder to trace:

* a Bb in the pad of a G7sus, in a piece that is otherwise entirely in C major;
* a major 7th sitting eleven semitones over the bass on Fmaj9 and Cmaj9, low
  enough that it beat against the root instead of colouring it.

Both are mechanical to catch, so they are checked here rather than left to
whoever is writing the notes. `check_material` is the entry point for that.

Pitches are MIDI integers throughout, middle C = 60 = C4.
"""

from __future__ import annotations

from .smf import parse_pitch, pitch_name           # noqa: F401  (re-exported)

# --------------------------------------------------------------------------
# Scales. Semitone offsets from the tonic.
# --------------------------------------------------------------------------
SCALES = {
    "major":            (0, 2, 4, 5, 7, 9, 11),
    "ionian":           (0, 2, 4, 5, 7, 9, 11),
    "dorian":           (0, 2, 3, 5, 7, 9, 10),
    "phrygian":         (0, 1, 3, 5, 7, 8, 10),
    "lydian":           (0, 2, 4, 6, 7, 9, 11),
    "mixolydian":       (0, 2, 4, 5, 7, 9, 10),
    "minor":            (0, 2, 3, 5, 7, 8, 10),
    "aeolian":          (0, 2, 3, 5, 7, 8, 10),
    "locrian":          (0, 1, 3, 5, 6, 8, 10),
    "harmonic_minor":   (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor":    (0, 2, 3, 5, 7, 9, 11),
    "major_pentatonic": (0, 2, 4, 7, 9),
    "minor_pentatonic": (0, 3, 5, 7, 10),
    "blues":            (0, 3, 5, 6, 7, 10),
}

MODE_DEGREE_NAMES = ("I", "II", "III", "IV", "V", "VI", "VII")

# --------------------------------------------------------------------------
# Chords. Semitone offsets from the chord root.
#
# `tensions` are the extensions that may be added or used melodically without
# fighting the chord. `avoid` are scale tones that clash with a chord tone a
# semitone below them -- the classic case is the natural 11th over a major
# triad, which sits a semitone above the 3rd and sours it.
# --------------------------------------------------------------------------
CHORDS = {
    "maj":    dict(intervals=(0, 4, 7),           tensions=(14, 21),     avoid=(17,)),
    "min":    dict(intervals=(0, 3, 7),           tensions=(14, 17, 21), avoid=()),
    "dim":    dict(intervals=(0, 3, 6),           tensions=(14,),        avoid=()),
    "aug":    dict(intervals=(0, 4, 8),           tensions=(14,),        avoid=(17,)),
    "sus2":   dict(intervals=(0, 2, 7),           tensions=(21,),        avoid=()),
    "sus4":   dict(intervals=(0, 5, 7),           tensions=(14, 21),     avoid=()),
    "6":      dict(intervals=(0, 4, 7, 9),        tensions=(14,),        avoid=(17,)),
    "m6":     dict(intervals=(0, 3, 7, 9),        tensions=(14,),        avoid=()),
    "maj7":   dict(intervals=(0, 4, 7, 11),       tensions=(14, 18, 21), avoid=(17,)),
    "min7":   dict(intervals=(0, 3, 7, 10),       tensions=(14, 17, 21), avoid=()),
    "7":      dict(intervals=(0, 4, 7, 10),       tensions=(14, 21),     avoid=(17,)),
    "m7b5":   dict(intervals=(0, 3, 6, 10),       tensions=(14, 17),     avoid=()),
    "dim7":   dict(intervals=(0, 3, 6, 9),        tensions=(14,),        avoid=()),
    "7sus4":  dict(intervals=(0, 5, 7, 10),       tensions=(14, 21),     avoid=()),
    "maj9":   dict(intervals=(0, 4, 7, 11, 14),   tensions=(18, 21),     avoid=(17,)),
    "min9":   dict(intervals=(0, 3, 7, 10, 14),   tensions=(17, 21),     avoid=()),
    "9":      dict(intervals=(0, 4, 7, 10, 14),   tensions=(21,),        avoid=(17,)),
    "min11":  dict(intervals=(0, 3, 7, 10, 14, 17), tensions=(21,),      avoid=()),
    "13":     dict(intervals=(0, 4, 7, 10, 14, 21), tensions=(),         avoid=(17,)),
    "maj13":  dict(intervals=(0, 4, 7, 11, 14, 21), tensions=(),         avoid=(17,)),
    "7b9":    dict(intervals=(0, 4, 7, 10, 13),   tensions=(),           avoid=(17,)),
    "7sharp9": dict(intervals=(0, 4, 7, 10, 15),  tensions=(),           avoid=(17,)),
}

CHORD_ALIASES = {
    "": "maj", "M": "maj", "major": "maj",
    "m": "min", "-": "min", "minor": "min",
    "dom7": "7", "dominant7": "7",
    "m7": "min7", "-7": "min7",
    "M7": "maj7", "ma7": "maj7",
    "m9": "min9", "sus": "sus4", "7sus": "7sus4",
    "hdim7": "m7b5", "o7": "dim7",
}

# --------------------------------------------------------------------------
# Register. The single most common way a correct chord still sounds wrong.
#
# Close intervals lose definition as they descend -- a major 3rd that is warm
# at C4 is mud at C2. These are the lowest pitch at which each interval still
# reads clearly, following the usual arranger's low-interval-limit chart. They
# are guidance, not physics, and are deliberately a little conservative.
# --------------------------------------------------------------------------
LOW_INTERVAL_LIMITS = {
    1: 64,    # minor 2nd    -- E4
    2: 62,    # major 2nd    -- D4
    3: 52,    # minor 3rd    -- E3
    4: 48,    # major 3rd    -- C3
    5: 41,    # perfect 4th  -- F2
    6: 48,    # tritone      -- C3
    7: 34,    # perfect 5th  -- Bb1
    8: 44,    # minor 6th    -- G#2
    9: 41,    # major 6th    -- F2
    10: 45,   # minor 7th    -- A2
    11: 52,   # major 7th    -- E3, the interval that soured Fmaj9 and Cmaj9
}

MUD_LINE = 52          # E3. Below here, stick to 4ths, 5ths and octaves.
SAFE_LOW_INTERVALS = {0, 5, 7, 12, 17, 19, 24}


class TheoryError(ValueError):
    """Raised for an unusable key, chord or progression."""


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------
def normalise_quality(quality):
    q = (quality or "").strip()
    q = CHORD_ALIASES.get(q, q)
    if q not in CHORDS:
        raise TheoryError(
            "Unknown chord quality %r. Known: %s"
            % (quality, ", ".join(sorted(CHORDS))))
    return q


def scale_pitch_classes(tonic, mode="major"):
    """The pitch classes of a key, as a set."""
    mode = (mode or "major").strip().lower().replace(" ", "_").replace("-", "_")
    if mode not in SCALES:
        raise TheoryError(
            "Unknown mode %r. Known: %s" % (mode, ", ".join(sorted(SCALES))))
    root = tonic if isinstance(tonic, int) else parse_pitch(str(tonic) + "4")
    return {(root + step) % 12 for step in SCALES[mode]}, root % 12, mode


def scale_degrees(tonic, mode="major", octave=4):
    """The scale as MIDI pitches across one octave from `octave`."""
    _pcs, root_pc, mode = scale_pitch_classes(tonic, mode)
    base = 12 * (octave + 1) + root_pc
    return [base + step for step in SCALES[mode]]


def chord_pitches(root, quality="maj", octave=4, inversion=0):
    """Absolute MIDI pitches for one chord, root position by default."""
    quality = normalise_quality(quality)
    root_pc = root % 12 if isinstance(root, int) else parse_pitch(str(root) + "4") % 12
    base = 12 * (octave + 1) + root_pc
    notes = [base + i for i in CHORDS[quality]["intervals"]]
    for _ in range(inversion % max(1, len(notes))):
        notes = notes[1:] + [notes[0] + 12]
    return notes


def diatonic_chords(tonic, mode="major", sevenths=True):
    """Every chord the key generates, with roman numeral and function."""
    _pcs, root_pc, mode = scale_pitch_classes(tonic, mode)
    steps = SCALES[mode]
    if len(steps) != 7:
        raise TheoryError("Diatonic chords need a seven-note mode, not %r" % mode)

    out = []
    for degree in range(7):
        stacked = [steps[(degree + k) % 7] + (12 if (degree + k) >= 7 else 0)
                   for k in (0, 2, 4, 6)]
        stacked = []
        for k in (0, 2, 4, 6):
            idx = degree + k
            stacked.append(steps[idx % 7] + 12 * (idx // 7))
        rel = [s - stacked[0] for s in stacked]
        if not sevenths:
            rel = rel[:3]
        quality = _quality_from_intervals(rel)
        numeral = _numeral(degree, quality)
        out.append({
            "degree": degree + 1,
            "numeral": numeral,
            "root": (root_pc + steps[degree]) % 12,
            "root_name": pitch_name(60 + (root_pc + steps[degree]) % 12)[:-1],
            "quality": quality,
            "function": _function(degree, mode),
        })
    return out


def _quality_from_intervals(rel):
    table = {q: list(v["intervals"]) for q, v in CHORDS.items()}
    rel = list(rel)
    for name in ("maj7", "min7", "7", "m7b5", "dim7", "maj", "min", "dim", "aug"):
        if table[name] == rel:
            return name
    return "maj" if 4 in rel else "min"


def _numeral(degree, quality):
    base = MODE_DEGREE_NAMES[degree]
    minorish = quality.startswith("min") or quality.startswith("m7") or quality == "dim"
    numeral = base.lower() if minorish else base
    # ASCII only: this text gets printed to whatever codepage the console has,
    # and the usual degree/slashed-o symbols die on a non-UTF-8 terminal.
    if quality in ("dim", "dim7"):
        numeral += "dim"
    elif quality == "m7b5":
        numeral += "m7b5"
    elif quality in ("maj7",):
        numeral += "maj7"
    elif quality in ("min7",):
        numeral += "7"
    elif quality == "7":
        numeral += "7"
    return numeral


def _function(degree, mode):
    minorish = mode in ("minor", "aeolian", "dorian", "phrygian",
                        "harmonic_minor", "melodic_minor", "locrian")
    if degree in (0, 5):
        return "tonic"
    if degree in (1, 3):
        return "subdominant"
    if degree in (4, 6):
        return "dominant"
    return "mediant" if not minorish else "subdominant"


# --------------------------------------------------------------------------
# Progressions. Roman numerals in, chords out.
# --------------------------------------------------------------------------
PROGRESSIONS = {
    "lofi_wistful":   dict(numerals=["IV", "iii", "vi", "ii"], mode="major",
                           mood="bittersweet, will not settle major or minor"),
    "lofi_lift":      dict(numerals=["ii", "V", "I", "vi"], mode="major",
                           mood="resolves brighter; good for a chorus"),
    "neo_soul_loop":  dict(numerals=["I", "vi", "ii", "V"], mode="major",
                           mood="warm, circular, endlessly loopable"),
    "sad_descent":    dict(numerals=["i", "VII", "VI", "V"], mode="minor",
                           mood="dark, falling, cinematic"),
    "dark_driving":   dict(numerals=["i", "VII", "VI", "VII"], mode="minor",
                           mood="trap and drill sit here"),
    "doo_wop":        dict(numerals=["I", "vi", "IV", "V"], mode="major",
                           mood="classic, nostalgic"),
    "pop_axis":       dict(numerals=["I", "V", "vi", "IV"], mode="major",
                           mood="the most common progression in pop"),
    "jazz_251":       dict(numerals=["ii", "V", "I", "I"], mode="major",
                           mood="the jazz cadence"),
    "modal_dorian":   dict(numerals=["i", "IV", "i", "IV"], mode="dorian",
                           mood="static, hypnotic, groove-first"),
    "andalusian":     dict(numerals=["i", "VII", "VI", "V"], mode="phrygian",
                           mood="spanish, tense descent"),
}

_ROMAN = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}


def parse_numeral(numeral):
    """'bVII', 'ii', 'V7' -> (degree index, flat?, explicit quality or None)."""
    text = numeral.strip()
    flat = text.startswith("b")
    if flat:
        text = text[1:]
    core = ""
    while text and text[0].isalpha() and text[0].lower() in "iv":
        core += text[0]
        text = text[1:]
    if not core:
        raise TheoryError("Cannot read roman numeral %r" % numeral)
    degree = _ROMAN.get(core.lower())
    if degree is None:
        raise TheoryError("Cannot read roman numeral %r" % numeral)
    suffix = text.strip()
    quality = None
    if suffix:
        quality = {"7": "7", "maj7": "maj7", "m7": "min7", "9": "9",
                   "sus4": "sus4", "sus": "sus4", "7sus4": "7sus4",
                   "dim": "dim", "m7b5": "m7b5"}.get(suffix, suffix)
    is_minor_numeral = core.islower()
    return degree, flat, quality, is_minor_numeral


def progression_chords(numerals, tonic, mode="major", sevenths=True):
    """Turn roman numerals into concrete chords in a key."""
    table = diatonic_chords(tonic, mode, sevenths=sevenths)
    _pcs, root_pc, mode = scale_pitch_classes(tonic, mode)
    steps = SCALES[mode]

    out = []
    for numeral in numerals:
        degree, flat, quality, minor_numeral = parse_numeral(numeral)
        entry = table[degree]
        root = entry["root"]
        if flat:
            root = (root - 1) % 12
        if quality is None:
            quality = entry["quality"]
            # Case that disagrees with the key is a request for a borrowed
            # chord, not a mistake. Which borrowed chord matters, though:
            # an upper-case V in a minor key is the harmonic-minor dominant, so
            # it wants a dominant 7th. Making it a maj7 instead invents a major
            # 7th degree that belongs to no minor scale (Gmaj7 in C minor puts
            # an F# in the chord, which is simply wrong).
            minor_key = quality.startswith("min") or quality in ("dim", "dim7", "m7b5")
            if minor_numeral and not minor_key:
                quality = "min7" if sevenths else "min"
            elif not minor_numeral and minor_key:
                if degree == 4:                       # V -- the dominant
                    quality = "7" if sevenths else "maj"
                else:
                    quality = "maj7" if sevenths else "maj"
        out.append({
            "numeral": numeral,
            "root": root,
            "root_name": pitch_name(60 + root)[:-1],
            "quality": normalise_quality(quality),
            "function": entry["function"],
        })
    return out


# --------------------------------------------------------------------------
# Voicing. Register-aware, voice-led.
# --------------------------------------------------------------------------
INTERVAL_NAMES = {
    1: "minor 2nd", 2: "major 2nd", 3: "minor 3rd", 4: "major 3rd",
    5: "perfect 4th", 6: "tritone", 7: "perfect 5th", 8: "minor 6th",
    9: "major 6th", 10: "minor 7th", 11: "major 7th",
}


def voice_chord(root, quality, register=(52, 72), previous=None,
                rootless=False, voices=4, bass=None):
    """Choose the actual notes to play for one chord.

    Keeps the voicing inside `register`, prefers the arrangement closest to
    `previous` so the hand barely moves between chords, and drops the root when
    `rootless` since a bass part is usually covering it.
    """
    quality = normalise_quality(quality)
    lo, hi = register
    intervals = list(CHORDS[quality]["intervals"])
    if rootless and len(intervals) > 3:
        intervals = intervals[1:]

    pcs = sorted({(root + i) % 12 for i in intervals})
    candidates = []
    for pc in pcs:
        octaves = [p for p in range(lo - 12, hi + 13) if p % 12 == pc and lo <= p <= hi]
        candidates.append(octaves or [lo + ((pc - lo) % 12)])

    best, best_cost = None, None
    for combo in _combinations(candidates):
        notes = sorted(combo)
        if len(notes) > voices:
            notes = notes[:voices]
        if notes[-1] - notes[0] > 24:            # keep it a playable hand span
            continue
        cost = _voicing_cost(notes, previous, bass)
        if best_cost is None or cost < best_cost:
            best, best_cost = notes, cost
    return best or sorted(lo + ((pc - lo) % 12) for pc in pcs)


def _combinations(lists):
    out = [[]]
    for options in lists:
        out = [prefix + [o] for prefix in out for o in options]
        if len(out) > 4000:                       # keep the search bounded
            out = out[:4000]
    return out


def _voicing_cost(notes, previous, bass=None):
    cost = 0.0
    spread = notes[-1] - notes[0]
    cost += abs(spread - 12) * 0.4                # favour a compact voicing
    # Scored against the actual bass note where one is known. Without it the
    # voicer cannot see the interval that matters most -- a 7th sitting close
    # over the root is what made the first arrangement sound sour.
    for warn in voicing_warnings(bass, notes):
        cost += 40 * warn["weight"]
    if previous:
        for n in notes:
            cost += min(abs(n - p) for p in previous) * 1.0
    return cost


def voicing_warnings(bass, notes):
    """Register problems in a chord: muddy low intervals, and clashes."""
    problems = []
    pitches = sorted(n for n in notes if n is not None)
    if bass is not None:
        for n in pitches:
            gap = n - bass
            if 0 < gap < 24:
                limit = LOW_INTERVAL_LIMITS.get(gap % 12 if gap < 12 else gap - 12)
                if gap < 12 and limit is not None and bass < limit:
                    problems.append({
                        "kind": "low_interval",
                        "weight": 1.0,
                        "detail": "%s is a %s above bass %s; that interval needs "
                                  "the bass at %s or higher to stay defined"
                                  % (pitch_name(n),
                                     INTERVAL_NAMES.get(gap, "%d semitones" % gap),
                                     pitch_name(bass), pitch_name(limit)),
                    })
    for i in range(len(pitches) - 1):
        gap = pitches[i + 1] - pitches[i]
        if pitches[i] < MUD_LINE and gap not in SAFE_LOW_INTERVALS and gap < 12:
            problems.append({
                "kind": "mud",
                "weight": 0.6,
                "detail": "%s and %s are %d semitones apart below %s, where only "
                          "4ths, 5ths and octaves stay defined"
                          % (pitch_name(pitches[i]), pitch_name(pitches[i + 1]),
                             gap, pitch_name(MUD_LINE)),
            })
    return problems


def consonant_pitch_classes(root, quality):
    """Pitch classes a melody may rest on over this chord."""
    quality = normalise_quality(quality)
    spec = CHORDS[quality]
    good = {(root + i) % 12 for i in spec["intervals"]}
    good |= {(root + t) % 12 for t in spec["tensions"]}
    good -= {(root + a) % 12 for a in spec["avoid"]}
    return good


# --------------------------------------------------------------------------
# Validation -- the part that would have caught both shipped bugs.
# --------------------------------------------------------------------------
def check_material(parts, key=None, mode="major", bars=None,
                   beats_per_bar=4, chords=None, passing_beats=0.5,
                   lead_tolerance=0.125):
    """Check written notes for the faults that read as 'out of tune'.

    `parts` is {name: [note dicts with pitch/start/duration]}. `chords` is an
    optional list of one chord per bar, as returned by `progression_chords`,
    which enables the per-chord consonance check.
    """
    problems = []
    in_key = None
    if key is not None:
        in_key, _root, mode = scale_pitch_classes(key, mode)

    for name, notes in sorted(parts.items()):
        if not notes:
            continue
        if _looks_like_drums(name):
            continue
        for n in notes:
            pitch, start, dur = n["pitch"], float(n["start"]), float(n["duration"])

            # A note placed just before a bar line belongs to the chord it is
            # leading into, not the one it is leaving. Parts that pull ahead of
            # the beat -- a sub, an anticipation -- otherwise get judged against
            # the previous bar and read as false clashes.
            chord = None
            idx = int((start + lead_tolerance) // beats_per_bar)
            if chords and 0 <= idx < len(chords):
                chord = chords[idx]

            chord_pcs = set()
            if chord:
                chord_pcs = {(chord["root"] + i) % 12
                             for i in CHORDS[normalise_quality(chord["quality"])]["intervals"]}

            if (in_key is not None and pitch % 12 not in in_key
                    and pitch % 12 not in chord_pcs and dur >= passing_beats):
                # Outside the key AND not part of the chord being played. A
                # borrowed chord legitimately steps outside the parent scale --
                # a major IV in a minor key, a raised leading tone in a V -- so
                # its own chord tones are not errors, only stray notes are.
                problems.append({
                    "part": name, "severity": "error", "kind": "out_of_key",
                    "bar": idx + 1,
                    "detail": "%s is not in %s %s, and is not a chord tone of "
                              "what is playing; held %.2f beats"
                              % (pitch_name(pitch), key, mode, dur),
                })

            if chord and dur >= passing_beats:
                ok = consonant_pitch_classes(chord["root"], chord["quality"])
                if pitch % 12 not in ok:
                    problems.append({
                        "part": name, "severity": "warning", "kind": "clash",
                        "bar": idx + 1,
                        "detail": "%s over %s%s is neither a chord tone nor an "
                                  "available tension, held %.2f beats"
                                  % (pitch_name(pitch), chord["root_name"],
                                     chord["quality"], dur),
                    })
        if bars is not None:
            end = max(n["start"] + n["duration"] for n in notes)
            limit = bars * beats_per_bar
            if end > limit + 1e-6:
                problems.append({
                    "part": name, "severity": "error", "kind": "overhang",
                    "bar": bars + 1,
                    "detail": "ends at %.4f beats, %.4f past the final bar line; "
                              "the DAW will round the project up a bar"
                              % (end, end - limit),
                })

    problems.extend(_vertical_check(parts, beats_per_bar))
    return problems


def _looks_like_drums(name):
    lowered = name.lower()
    return any(word in lowered for word in ("drum", "kit", "perc", "606", "808 kit"))


def _vertical_check(parts, beats_per_bar):
    """Sound every simultaneous pair against the lowest part."""
    bass_name = None
    lowest = None
    for name, notes in parts.items():
        if not notes or _looks_like_drums(name):
            continue
        avg = sum(n["pitch"] for n in notes) / float(len(notes))
        if lowest is None or avg < lowest:
            lowest, bass_name = avg, name
    if bass_name is None:
        return []

    bass = sorted(parts[bass_name], key=lambda n: n["start"])
    problems, seen = [], set()
    for name, notes in sorted(parts.items()):
        if name == bass_name or _looks_like_drums(name):
            continue
        for n in notes:
            for b in bass:
                if b["start"] <= n["start"] < b["start"] + b["duration"]:
                    for warn in voicing_warnings(b["pitch"], [n["pitch"]]):
                        key = (name, n["pitch"], b["pitch"], warn["kind"])
                        if key in seen:
                            break
                        seen.add(key)
                        problems.append({
                            "part": name, "severity": "warning",
                            "kind": warn["kind"],
                            "bar": int(n["start"] // beats_per_bar) + 1,
                            "detail": warn["detail"],
                        })
                    break
    return problems
