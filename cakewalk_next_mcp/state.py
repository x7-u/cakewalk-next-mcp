"""Small persisted state for things the server cannot read back from Next.

The project tempo is the important one. Cakewalk Next shows it in the toolbar,
but there is no way to query it: the ``.cnp`` parser cannot reach tempo, and the
toolbar is a JUCE-drawn control with no queryable text. Anything generated or
played at the wrong tempo lands off the project's grid, so the tempo is stored
here once and used as the default everywhere.
"""

from __future__ import annotations

import json
import os

APP_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "cakewalk-next-mcp"
)
STATE_FILE = os.path.join(APP_DIR, "state.json")

FALLBACK_TEMPO = 120.0


def _read():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        return True
    except OSError:
        return False


def get_project_tempo():
    """Return the stored project tempo, or None if it was never set."""
    value = _read().get("project_tempo_bpm")
    return float(value) if isinstance(value, (int, float)) else None


def set_project_tempo(bpm):
    """Remember the project tempo. Returns the value stored."""
    bpm = float(bpm)
    if not 1.0 <= bpm <= 960.0:
        raise ValueError("Tempo %s out of the supported range 1-960 BPM." % bpm)
    data = _read()
    data["project_tempo_bpm"] = bpm
    _write(data)
    return bpm


def resolve_tempo(explicit=None):
    """Pick the tempo to use, and say where it came from.

    Explicit beats stored, stored beats the 120 BPM fallback. The source is
    reported so a caller can see when an assumed tempo was used.
    """
    if explicit is not None:
        return float(explicit), "explicit"
    stored = get_project_tempo()
    if stored is not None:
        return stored, "project"
    return FALLBACK_TEMPO, "fallback"


def tempo_note(source, tempo):
    """One line explaining the tempo choice, for inclusion in tool output."""
    if source == "project":
        return "Used the stored project tempo of %g BPM." % tempo
    if source == "explicit":
        stored = get_project_tempo()
        if stored is not None and abs(stored - tempo) > 0.01:
            return (
                "WARNING: played at %g BPM but the project is %g BPM, so this "
                "will not line up with the project grid. Pass tempo_bpm=%g, or "
                "update the stored tempo with next_set_project_tempo."
                % (tempo, stored, stored)
            )
        return "Used the tempo you specified (%g BPM)." % tempo
    return (
        "No project tempo is known, so %g BPM was assumed. Set the real one with "
        "next_set_project_tempo so generated parts line up with the project grid."
        % tempo
    )
