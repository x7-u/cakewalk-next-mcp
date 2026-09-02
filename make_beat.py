"""A 16-bar rap beat for the soundbanks installed in Cakewalk Next.

Written against standard popular-music conventions:

* **Structure** - Short Form (30-60s): **Hook / Verse / Hook**. A
  32-second piece must open on the hook; the earlier version used a full song's
  Intro-Build-Breakdown-Outro shape and spent its first eight bars arriving.
* **Theory** - C minor is "Dark, intense", and i-VII-VI-VII is the
  "Dark, driving" progression. In C minor: **Cm - Bb - Ab - Bb**.
* **Genre** - Trap sits in Am/Cm with "808, hi-tats, dark, heavy
  bass"; the hip-hop recipe is "clean 808 bass, crisp hi-hats, trap drums".

Structure, 16 bars of 4/4 at 120 BPM = 32 seconds:

    bars  1-4   HOOK    full arrangement, melody carries it
    bars  5-12  VERSE   drums and bass hold, melody drops out to leave
                        space for a vocal - the point of a rap verse
    bars 13-16  HOOK    full arrangement returns, ends on the downbeat

Tempo note: the hip-hop sweet spot is nearer 95 BPM, but this is written to a
120 BPM project. At 120 the half-time backbeat on beat 3 gives a trap feel
rather than a boom-bap one. Use next_set_daw_tempo to change the project.
"""

import asyncio
import json
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJ = os.path.dirname(os.path.abspath(__file__))
BEATS_PER_BAR = 4
BARS = 16
VELOCITY_SCALE = 0.72          # four layers at full tilt clipped the master

HOOK_1 = range(1, 5)           # bars 1-4
VERSE = range(5, 13)           # bars 5-12
HOOK_2 = range(13, 17)         # bars 13-16


def is_hook(n):
    return n in HOOK_1 or n in HOOK_2


# i - VII - VI - VII in C minor: the "dark, driving" progression.
# "tones" are the pitches the hook may use over that bar, so the melody is
# always consonant with the chord underneath it.
PROGRESSION = [
    {"name": "Cm", "bass": 36, "triad": [60, 63, 67], "tones": [72, 75, 79]},
    {"name": "Bb", "bass": 34, "triad": [58, 62, 65], "tones": [74, 77, 82]},
    {"name": "Ab", "bass": 32, "triad": [56, 60, 63], "tones": [72, 75, 80]},
    {"name": "Bb", "bass": 34, "triad": [58, 62, 65], "tones": [74, 77, 82]},
]

# One two-bar kick figure, repeated verbatim: the groove is the repetition.
KICKS = [[0.0, 0.75, 2.5], [0.0, 1.5, 2.5, 3.75]]


def bar(n):
    return (n - 1) * BEATS_PER_BAR


def chord_for(n):
    return PROGRESSION[(n - 1) % len(PROGRESSION)]


def pad(pads, *keywords):
    for name, midi in pads.items():
        if all(k in name for k in keywords):
            return midi
    raise KeyError("no pad matching %s in %s" % (keywords, sorted(pads)))


def trim(notes):
    for n in notes:
        n["velocity"] = max(1, min(127, int(round(n["velocity"] * VELOCITY_SCALE))))
    return notes


def drums_808(pads):
    """Crisp hi-hats over a repeating kick figure, backbeat on 3."""
    kick, snare = pad(pads, "kick"), pad(pads, "snare")
    hat, open_hat, crash = pad(pads, "closed"), pad(pads, "open"), pad(pads, "crash")
    notes = []

    for n in range(1, BARS + 1):
        start = bar(n)
        hook = is_hook(n)

        for i in range(8):
            # Hats ride through everything; a touch louder in the hook.
            notes.append({"pitch": hat, "start": start + i * 0.5, "duration": 0.18,
                          "velocity": (94 if hook else 84) if i % 2 == 0 else 62})
        if n % 2 == 0:
            notes.append({"pitch": open_hat, "start": start + 3.5,
                          "duration": 0.35, "velocity": 84})
        # 16th fill into each new section.
        if n % 4 == 0:
            for j in range(4):
                notes.append({"pitch": hat, "start": start + 3 + j * 0.125,
                              "duration": 0.1, "velocity": 62 + j * 14})

        for at in KICKS[(n - 1) % 2]:
            notes.append({"pitch": kick, "start": start + at,
                          "duration": 0.25, "velocity": 120 if hook else 112})
        notes.append({"pitch": snare, "start": start + 2, "duration": 0.3,
                      "velocity": 112 if hook else 104})

        # Crash marks each hook entry.
        if n in (1, 13):
            notes.append({"pitch": crash, "start": start, "duration": 2, "velocity": 108})
    return notes


def perc_606(pads):
    """Offbeat shaker interlocks with the hats; clap thickens the hook backbeat."""
    shaker, clap = pad(pads, "shaker"), pad(pads, "clap")
    notes = []
    for n in range(1, BARS + 1):
        start = bar(n)
        for i in range(4):
            notes.append({"pitch": shaker, "start": start + 0.5 + i,
                          "duration": 0.15, "velocity": 70 if is_hook(n) else 58})
        if is_hook(n):
            notes.append({"pitch": clap, "start": start + 2,
                          "duration": 0.2, "velocity": 94})
    return notes


def piano_dark():
    """Held root for weight, chord stabs on 1 and the and-of-3."""
    notes = []
    for n in range(1, BARS + 1):
        start, chord = bar(n), chord_for(n)
        notes.append({"pitch": chord["bass"], "start": start,
                      "duration": 3.8, "velocity": 98 if is_hook(n) else 88})
        level = 64 if is_hook(n) else 52
        for pitch in chord["triad"]:
            notes.append({"pitch": pitch, "start": start,
                          "duration": 1.4, "velocity": level})
            notes.append({"pitch": pitch, "start": start + 2.5,
                          "duration": 1.0, "velocity": level - 14})
    return notes


def melody_studio():
    """The hook line. Absent through the verse so a vocal has room."""
    shapes = [
        [(0.0, 2, 1.5), (1.5, 1, 0.5), (2.0, 0, 1.0), (3.0, 1, 1.0)],
        [(0.0, 1, 1.0), (1.0, 2, 1.0), (2.5, 0, 1.5)],
        [(0.0, 0, 1.5), (1.5, 1, 0.5), (2.0, 2, 2.0)],
        [(0.0, 2, 1.0), (1.0, 1, 1.0), (2.0, 0, 2.0)],
    ]
    notes = []
    for n in list(HOOK_1) + list(HOOK_2):
        chord = chord_for(n)
        for offset, tone, length in shapes[(n - 1) % 4]:
            notes.append({"pitch": chord["tones"][tone],
                          "start": bar(n) + offset, "duration": length,
                          "velocity": 78})
    return notes


async def main():
    params = StdioServerParameters(
        command=os.path.join(PROJ, ".venv", "Scripts", "python.exe"),
        args=["-m", "cakewalk_next_mcp"], cwd=PROJ)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def pads_of(slug):
                res = await s.call_tool("next_instrument_info", {"params": {"instrument": slug}})
                return {p["sound"].lower(): p["midi"]
                        for p in json.loads(res.content[0].text)["pads"]}

            p808 = await pads_of("808-kit")
            p606 = await pads_of("606-kit-v3-v4")

            parts = [
                ("808 Drums", trim(drums_808(p808)), 0, "beat_808d.mid"),
                ("606 Perc", trim(perc_606(p606)), 1, "beat_606p.mid"),
                ("Dark Grand Keys", trim(piano_dark()), 2, "beat_darkg.mid"),
                ("Studio Grand Hook", trim(melody_studio()), 3, "beat_studiog.mid"),
            ]

            for name, notes, channel, fname in parts:
                path = os.path.join(PROJ, fname)
                if os.path.exists(path):
                    os.remove(path)
                res = await s.call_tool("next_write_midi", {"params": {
                    "path": path,
                    "tracks": [{"name": name, "channel": channel, "notes": notes}],
                }})
                d = json.loads(res.content[0].text)
                print("%-20s %3d notes  %.1f bars" % (name, d["total_notes"], d["length_bars"]))

            print("\nstructure : HOOK 1-4 | VERSE 5-12 (melody out) | HOOK 13-16")
            print("progression: %s  (i-VII-VI-VII, 'dark, driving')"
                  % " ".join(c["name"] for c in PROGRESSION))


asyncio.run(main())
