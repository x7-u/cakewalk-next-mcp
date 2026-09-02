"""A "Bandit" type beat - melodic trap, in the Juice WRLD / Nick Mira mould.

Genre conventions, from standard popular-music practice:

* **Theory** - Trap sits at 130-170 BPM; C# minor is the dark,
  intense end of the minor keys. i-VI-III-VII is the emo-rap staple.
* **Genre** - "trap, 808, hi-hats, dark, heavy bass", key Am/Cm.
* **Structure** - at 150 BPM, 18 bars is 28.8 seconds.

Progression: **C#m - A - E - B** (i - VI - III - VII), one chord per bar.

Kept deliberately uncluttered: four parts, never more than three sounding at
once, and the arpeggio carries the identity rather than a wall of layers.

    bars  1-2   intro - arpeggio alone, hats join in bar 2
    bars  3-10  hook - full beat
    bars 11-18  verse - drums and bass hold, arpeggio thins to leave vocal room

Bandit's signature low end is a tuned 808 slide. Only four soundbanks are
installed here and none is a tuned 808, so the piano's bottom octave carries
the bass instead - short, hard notes rather than sustained ones.
"""

import asyncio
import json
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJ = os.path.dirname(os.path.abspath(__file__))
BEATS_PER_BAR = 4
BARS = 18
TEMPO = 150
VELOCITY_SCALE = 0.72

INTRO = range(1, 3)          # bars 1-2
HOOK = range(3, 11)          # bars 3-10
VERSE = range(11, 19)        # bars 11-18

# i - VI - III - VII in C# minor. "arp" are the pitches the plucked figure
# uses over that bar, so it can never fight the chord underneath.
PROGRESSION = [
    {"name": "C#m", "bass": 37, "triad": [61, 64, 68], "arp": [73, 76, 80]},
    {"name": "A",   "bass": 33, "triad": [57, 61, 64], "arp": [69, 73, 76]},
    {"name": "E",   "bass": 40, "triad": [64, 68, 71], "arp": [76, 80, 83]},
    {"name": "B",   "bass": 35, "triad": [59, 63, 66], "arp": [71, 75, 78]},
]

# Two-bar kick figure, repeated verbatim - the groove is the repetition.
KICKS = [[0.0, 0.75, 2.5], [0.0, 1.5, 2.5, 3.25]]


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


def drums(pads):
    """Half-time trap kit: backbeat on 3, straight 8th hats, rolls at phrase ends."""
    kick, snare = pad(pads, "kick"), pad(pads, "snare")
    hat, open_hat, crash = pad(pads, "closed"), pad(pads, "open"), pad(pads, "crash")
    notes = []

    for n in range(1, BARS + 1):
        start = bar(n)
        full = n in HOOK or n in VERSE

        if n >= 2:
            for i in range(8):
                notes.append({"pitch": hat, "start": start + i * 0.5, "duration": 0.16,
                              "velocity": 92 if i % 2 == 0 else 64})
            # One 16th roll at the end of each four-bar phrase, nowhere else:
            # sparse fills are what keeps it clean.
            if n % 4 == 2 and full:
                for j in range(4):
                    notes.append({"pitch": hat, "start": start + 3.5 + j * 0.125,
                                  "duration": 0.08, "velocity": 60 + j * 16})
            if n % 2 == 0:
                notes.append({"pitch": open_hat, "start": start + 3.5,
                              "duration": 0.3, "velocity": 80})

        if full:
            for at in KICKS[(n - 1) % 2]:
                notes.append({"pitch": kick, "start": start + at,
                              "duration": 0.22, "velocity": 120})
            notes.append({"pitch": snare, "start": start + 2,
                          "duration": 0.28, "velocity": 110})

        if n == 3:
            notes.append({"pitch": crash, "start": start, "duration": 2, "velocity": 104})
    return notes


def perc(pads):
    """A single clap on the backbeat through the hook. Nothing else."""
    clap = pad(pads, "clap")
    return [{"pitch": clap, "start": bar(n) + 2, "duration": 0.2, "velocity": 96}
            for n in HOOK]


def bass():
    """Short, hard low notes standing in for a tuned 808."""
    notes = []
    for n in range(1, BARS + 1):
        if n in INTRO:
            continue
        start, chord = bar(n), chord_for(n)
        for at, length, vel in ((0.0, 1.2, 112), (2.5, 0.5, 92), (3.25, 0.5, 88)):
            notes.append({"pitch": chord["bass"], "start": start + at,
                          "duration": length, "velocity": vel})
    return notes


def keys():
    """Quiet sustained triad under the hook, for body without clutter."""
    notes = []
    for n in HOOK:
        start, chord = bar(n), chord_for(n)
        for pitch in chord["triad"]:
            notes.append({"pitch": pitch, "start": start,
                          "duration": 3.6, "velocity": 46})
    return notes


def arpeggio():
    """The hook: a plucked up-down figure, the part people remember."""
    shape = [0, 1, 2, 1, 0, 1, 2, 1]          # up and back, eighth notes
    notes = []
    for n in range(1, BARS + 1):
        start, chord = bar(n), chord_for(n)
        if n in VERSE:
            # Thinned to every other note so a vocal has room.
            steps = [(i * 0.5, shape[i]) for i in range(0, 8, 2)]
            level = 66
        else:
            steps = [(i * 0.5, shape[i]) for i in range(8)]
            level = 82 if n in HOOK else 74
        for offset, tone in steps:
            notes.append({"pitch": chord["arp"][tone], "start": start + offset,
                          "duration": 0.38, "velocity": level})
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

            # Bass and pad share the Dark Grand part; the arpeggio is its own track.
            parts = [
                ("Bandit Drums", trim(drums(p808)), 0, "bandit_drums.mid"),
                ("Bandit Clap", trim(perc(p606)), 1, "bandit_clap.mid"),
                ("Bandit Bass", trim(bass() + keys()), 2, "bandit_keys.mid"),
                ("Bandit Arp", trim(arpeggio()), 3, "bandit_arp.mid"),
            ]

            for name, notes, channel, fname in parts:
                path = os.path.join(PROJ, fname)
                if os.path.exists(path):
                    os.remove(path)
                res = await s.call_tool("next_write_midi", {"params": {
                    "path": path, "tempo_bpm": TEMPO,
                    "tracks": [{"name": name, "channel": channel, "notes": notes}],
                }})
                d = json.loads(res.content[0].text)
                print("%-14s %3d notes  %.1f bars" % (name, d["total_notes"], d["length_bars"]))

            seconds = BARS * BEATS_PER_BAR * 60.0 / TEMPO
            print("\n%s  |  %d bars at %d BPM = %.1f seconds"
                  % (" ".join(c["name"] for c in PROGRESSION), BARS, TEMPO, seconds))


asyncio.run(main())
