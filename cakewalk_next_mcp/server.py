"""MCP server for Cakewalk Next.

Cakewalk Next ships no scripting API, no OSC and no Mackie/MCU control surface,
so this server works through the three surfaces the DAW actually exposes:

1. ``.cnp`` project files, read offline (names, plugins, media, colours);
2. Standard MIDI Files, for getting generated material in and existing parts out;
3. the documented keyboard shortcuts, driven via Win32 SendInput.
"""

from __future__ import annotations

import json
import os
import time
from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

try:  # mcp >= 2.0 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

try:  # mcp >= 2.0
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError

try:  # mcp >= 2.0
    from mcp.server.mcpserver import Image
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import Image

from mcp.types import ToolAnnotations

from . import (cnp, commands, compose, instruments, layout, menus, midiout,
               screen, smf, state, theory, winctl)

mcp = _Server("cakewalk_next_mcp")

MAX_REPEAT = 20

# Toolbar tempo readout, in logical px. The transport is centre-anchored, so
# the x offset is measured from the middle of the window, not its left edge --
# anchoring it left put the click 310 physical px wide of the field on a
# 2560-wide window, and the tempo silently stayed where it was. y is still
# measured from the top, which does not move.
TEMPO_FIELD = (-240, 49)
_INTER_KEY_DELAY = 0.06


def _dump(payload):
    return json.dumps(payload, indent=2, default=str)


def _predict_browser_rows(query, limit=12):
    """Predict the rows Next's instrument browser will show for ``query``.

    The rule, inferred by photographing the dialog: every query token must
    appear somewhere in the bank's name or tags, and the survivors are listed
    alphabetically. That is why a search does not put the wanted bank first --
    "Grand Piano" gives Dark Grand, Grand Piano, Studio Grand (all three carry
    a 'piano' tag) and "Piano" leads with Accordion.

    Checked against the real dialog for 'Grand Piano' and 'Piano', which it
    reproduces exactly. It is still a PREDICTION off the local index, and one
    known soft spot is tag wording: Next appears to match the tag's display
    label ('Piano') rather than its slug ('pianos'), so a query hitting a slug
    that is spelled differently on screen can be over- or under-matched.
    Screenshot the dialog when the choice matters.
    """
    tokens = [t for t in str(query).lower().split() if t]
    if not tokens:
        return []
    try:
        items = instruments.search(limit=1000)["items"]
    except instruments.InstrumentError:
        return []

    hits = []
    for item in items:
        # Family is deliberately excluded: it is not shown in the dialog, and
        # folding it in over-matches (every 'synth-*' family bank answers to
        # the token 'synth').
        haystack = " ".join([
            (item.get("name") or ""), " ".join(item.get("tags") or []),
        ]).lower()
        if all(token in haystack for token in tokens):
            hits.append(item)
    hits.sort(key=lambda i: (i.get("name") or "").lower())
    return [{"row": n, "name": i.get("name"), "installed": i.get("installed")}
            for n, i in enumerate(hits[:limit])]


# --------------------------------------------------------------------------
# Project inspection
# --------------------------------------------------------------------------


class ListProjectsInput(BaseModel):
    """Input model for locating Cakewalk Next project files."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    root: str = Field(
        ...,
        description="Folder to search recursively, e.g. 'C:/Users/me/Documents/Cakewalk Next'.",
        min_length=1,
    )
    limit: int = Field(default=25, description="Maximum projects to return.", ge=1, le=200)
    offset: int = Field(default=0, description="Number of projects to skip, for paging.", ge=0)
    response_format: str = Field(
        default="markdown", description="'markdown' for a readable list, 'json' for raw data."
    )


@mcp.tool(
    name="next_list_projects",
    annotations=ToolAnnotations(
        title="List Cakewalk Next projects",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_list_projects(params: ListProjectsInput) -> str:
    """Find Cakewalk Next project files (.cnp) under a folder, newest first.

    Searches recursively and returns each project's path, size and modification
    time. Use next_read_project to inspect one.
    """
    try:
        result = cnp.find_projects(params.root, params.limit, params.offset)
    except cnp.CnpError as exc:
        raise ToolError(str(exc)) from exc

    if params.response_format == "json":
        return _dump(result)

    if not result["items"]:
        return "No .cnp projects found under %s." % result["root"]

    lines = [
        "# Cakewalk Next projects in %s" % result["root"],
        "",
        "Showing %d of %d." % (result["count"], result["total"]),
        "",
    ]
    for item in result["items"]:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(item["modified"]))
        lines.append(
            "- **%s** - %.0f KB, modified %s\n  `%s`"
            % (item["name"], item["size_bytes"] / 1024.0, stamp, item["path"])
        )
    if result["has_more"]:
        lines.append("")
        lines.append("More available: call again with offset=%d." % result["next_offset"])
    return "\n".join(lines)


class ReadProjectInput(BaseModel):
    """Input model for inspecting a single .cnp project."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(..., description="Full path to a .cnp project file.", min_length=1)
    include_diagnostics: bool = Field(
        default=False,
        description="Include the raw chunk histogram and unclassified strings. "
        "Useful for extending the parser; noisy otherwise.",
    )
    response_format: str = Field(
        default="markdown", description="'markdown' for a readable summary, 'json' for raw data."
    )


@mcp.tool(
    name="next_read_project",
    annotations=ToolAnnotations(
        title="Read a Cakewalk Next project",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_read_project(params: ReadProjectInput) -> str:
    """Inspect a Cakewalk Next .cnp project without opening the DAW.

    Reports track names and colours, buses, instrument plugins and their
    presets, referenced audio files, and the version of Next that saved it.

    The .cnp format is undocumented and only partially decoded: tempo, time
    signature, clip positions and note data are NOT available here. To read
    musical content, export a MIDI clip from Next (right-click the clip header >
    Export to File) and pass it to next_read_midi.
    """
    try:
        result = cnp.parse_project(params.path, params.include_diagnostics)
    except cnp.CnpError as exc:
        raise ToolError(str(exc)) from exc

    # The parser can only recover a bare preset slug such as "808-kit"; the
    # installed soundbank library turns that into "808", a drum kit.
    try:
        for track in result["tracks"]:
            found = instruments.resolve(track.get("instrument_preset") or "")
            if found:
                track["instrument"] = {
                    "name": found["name"],
                    "category": found["category"],
                    "family": found.get("family"),
                }
    except instruments.InstrumentError:
        pass  # Library missing: slugs stay unresolved, everything else is fine.

    if params.response_format == "json":
        return _dump(result)

    created = result["created_with"]
    lines = [
        "# %s" % (result["project_name"] or os.path.basename(result["file"])),
        "",
        "- File: `%s` (%.0f KB)" % (result["file"], result["file_size_bytes"] / 1024.0),
    ]
    if created.get("app"):
        lines.append("- Saved with: Cakewalk %s %s" % (created["app"], created.get("version") or ""))
    if result["original_path"]:
        lines.append("- Originally saved at: `%s`" % result["original_path"])

    lines += ["", "## Tracks (%d)" % result["track_count"], ""]
    if result["tracks"]:
        for track in result["tracks"]:
            bits = []
            if track.get("instrument"):
                inst = track["instrument"]
                bits.append("%s (%s, `%s`)" % (
                    inst["name"], inst["category"], track["instrument_preset"]))
            elif track.get("instrument_preset"):
                bits.append("instrument preset `%s`" % track["instrument_preset"])
            if track.get("color_argb"):
                bits.append("colour %s" % track["color_argb"])
            suffix = " - %s" % ", ".join(bits) if bits else ""
            lines.append("- **%s**%s" % (track["name"], suffix))
    else:
        lines.append("_No track names found._")

    if result["buses"]:
        lines += ["", "## Buses", ""] + ["- %s" % b for b in result["buses"]]

    if result["instruments"]:
        lines += ["", "## Instruments", ""]
        for inst in result["instruments"]:
            lines.append("- %s (`%s`)" % (inst["name"], inst["preset"]))

    if result["media_files"]:
        lines += ["", "## Referenced audio", ""] + ["- `%s`" % m for m in result["media_files"]]

    lines += ["", "> %s" % result["coverage_note"]]

    if params.include_diagnostics:
        diag = result["diagnostics"]
        lines += ["", "## Diagnostics", "", "Chunk counts:"]
        for tag, info in diag["chunk_counts"].items():
            lines.append("- `%s` x%d - %s" % (tag, info["count"], info["meaning"]))
        lines.append("")
        lines.append("Unclassified strings: %s" % ", ".join(diag["unclassified_strings"][:60]))

    return "\n".join(lines)


# --------------------------------------------------------------------------
# MIDI
# --------------------------------------------------------------------------


class NoteInput(BaseModel):
    """One note, or one chord when several pitches share a start time."""

    model_config = ConfigDict(extra="forbid")

    pitch: Union[int, str, List[Union[int, str]]] = Field(
        ...,
        description="Pitch as a MIDI number (60), a name ('C4', 'F#3', 'Bb5'), or a "
        "list of either to sound them together as a chord. Middle C is C4.",
    )
    start: float = Field(
        ..., description="Start position in beats (quarter notes) from clip start.", ge=0
    )
    duration: float = Field(default=1.0, description="Length in beats.", gt=0, le=1024)
    velocity: int = Field(default=100, description="Loudness, 1-127.", ge=1, le=127)


class MidiTrackInput(BaseModel):
    """One track in the generated MIDI file."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(default="", description="Track name shown in Next.", max_length=120)
    channel: int = Field(
        default=0,
        description="MIDI channel 0-15. Use 9 for General MIDI drums.",
        ge=0,
        le=15,
    )
    program: Optional[int] = Field(
        default=None, description="Optional General MIDI program change, 0-127.", ge=0, le=127
    )
    notes: List[NoteInput] = Field(
        ..., description="Notes on this track.", min_length=1, max_length=20000
    )


class WriteMidiInput(BaseModel):
    """Input model for generating a Standard MIDI File."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(
        ...,
        description="Where to write the .mid file, e.g. 'C:/Music/proj/bassline.mid'.",
        min_length=1,
    )
    tracks: List[MidiTrackInput] = Field(
        ..., description="One or more tracks to write.", min_length=1, max_length=32
    )
    tempo_bpm: Optional[float] = Field(
        default=None,
        description="Tempo in BPM. Defaults to the stored project tempo (see "
        "next_set_project_tempo) so the part lines up with the project grid.",
        gt=0, le=960,
    )
    time_signature: str = Field(
        default="4/4", description="Time signature as 'numerator/denominator', e.g. '3/4', '7/8'."
    )
    overwrite: bool = Field(
        default=False, description="Set true to replace the file if it already exists."
    )

    @field_validator("time_signature")
    @classmethod
    def _check_signature(cls, value: str) -> str:
        parts = value.split("/")
        if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
            raise ToolError("Time signature must look like '4/4' or '7/8'.")
        return value


@mcp.tool(
    name="next_write_midi",
    annotations=ToolAnnotations(
        title="Write a MIDI file for Cakewalk Next",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_write_midi(params: WriteMidiInput) -> str:
    """Generate a Standard MIDI File that can be imported into Cakewalk Next.

    Positions are in beats, so 'start: 4' is the downbeat of bar 2 in 4/4.
    Each track becomes a separate clip on import.

    Next has no import API, so bring the file in by dragging it onto the track
    area, or via the Loop Browser. Match tempo_bpm to the project tempo.
    """
    target = os.path.abspath(params.path)
    if not target.lower().endswith(".mid"):
        target += ".mid"
    if os.path.exists(target) and not params.overwrite:
        raise ToolError(
            "%s already exists. Pass overwrite=true to replace it, or choose "
            "another path." % target
        )
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        raise ToolError(
            "Folder does not exist: %s. Create it first or choose an existing "
            "folder." % parent
        )

    tempo, tempo_source = state.resolve_tempo(params.tempo_bpm)
    numerator, denominator = (int(p) for p in params.time_signature.split("/"))

    tracks = []
    total_notes = 0
    for index, spec in enumerate(params.tracks):
        notes = []
        for note in spec.notes:
            raw = note.pitch if isinstance(note.pitch, list) else [note.pitch]
            if not raw:
                raise ToolError("A note on track %d has an empty pitch list." % index)
            for pitch in raw:
                try:
                    resolved = smf.parse_pitch(pitch)
                except smf.MidiError as exc:
                    raise ToolError("Track %d: %s" % (index, exc)) from exc
                notes.append(
                    smf.Note(
                        pitch=resolved,
                        start_beats=note.start,
                        duration_beats=note.duration,
                        velocity=note.velocity,
                    )
                )
        total_notes += len(notes)
        tracks.append(
            smf.Track(
                name=spec.name or "Track %d" % (index + 1),
                channel=spec.channel,
                program=spec.program,
                notes=notes,
            )
        )

    try:
        data = smf.write_midi(
            tracks,
            tempo_bpm=tempo,
            time_signature=(numerator, denominator),
        )
    except smf.MidiError as exc:
        raise ToolError(str(exc)) from exc

    with open(target, "wb") as handle:
        handle.write(data)

    longest = max(
        (n.start_beats + n.duration_beats for t in tracks for n in t.notes), default=0.0
    )
    return _dump({
        "written": target,
        "bytes": len(data),
        "tracks": [{"name": t.name, "channel": t.channel, "notes": len(t.notes)} for t in tracks],
        "total_notes": total_notes,
        "tempo_bpm": tempo,
        "tempo_source": tempo_source,
        "tempo_note": state.tempo_note(tempo_source, tempo),
        "time_signature": params.time_signature,
        "length_beats": round(longest, 4),
        "length_bars": round(longest / numerator, 4) if numerator else None,
        "next_step": "In Cakewalk Next, drag this file onto the track area to import it.",
    })


class ReadMidiInput(BaseModel):
    """Input model for reading a Standard MIDI File."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(..., description="Path to a .mid file.", min_length=1)
    include_notes: bool = Field(
        default=True, description="Include every note. Set false for a summary only."
    )
    max_notes: int = Field(
        default=500, description="Cap on notes returned per track.", ge=1, le=20000
    )


@mcp.tool(
    name="next_read_midi",
    annotations=ToolAnnotations(
        title="Read a MIDI file",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_read_midi(params: ReadMidiInput) -> str:
    """Read a Standard MIDI File into tempo, time signature and per-track notes.

    Use this to read musical content out of a Cakewalk Next project: in Next,
    right-click a MIDI clip header and choose Export to File, then read that
    file here. Note positions come back in beats, matching next_write_midi.
    """
    path = os.path.abspath(params.path)
    if not os.path.isfile(path):
        raise ToolError("No such file: %s" % path)
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        result = smf.read_midi(data)
    except smf.MidiError as exc:
        raise ToolError(str(exc)) from exc

    result["file"] = path
    for track in result["tracks"]:
        if not params.include_notes:
            track.pop("notes", None)
        elif len(track["notes"]) > params.max_notes:
            track["notes_truncated"] = True
            track["notes_shown"] = params.max_notes
            track["notes"] = track["notes"][: params.max_notes]
    return _dump(result)


# --------------------------------------------------------------------------
# Live control
# --------------------------------------------------------------------------


class AppStatusInput(BaseModel):
    """Input model for the application status check."""

    model_config = ConfigDict(extra="forbid")


@mcp.tool(
    name="next_app_status",
    annotations=ToolAnnotations(
        title="Check whether Cakewalk Next is running",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_app_status(params: AppStatusInput) -> str:
    """Report whether Cakewalk Next is running and which project is open.

    The window title carries the open project's name, so this also tells you
    what next_run_command would act on. Call it before sending commands.
    """
    try:
        windows = winctl.find_windows()
    except winctl.WindowError as exc:
        return _dump({"running": False, "reason": str(exc)})

    if not windows:
        return _dump({
            "running": False,
            "reason": "No visible Next.exe window found.",
            "suggestion": "Start Cakewalk Next and open a project, then retry.",
        })

    main = windows[0]
    title = main["title"]
    project = None
    if title.endswith(winctl.WINDOW_TITLE_SUFFIX):
        project = title[: -len(" - " + winctl.WINDOW_TITLE_SUFFIX)]
    # Next prefixes the title with '*' while the project has unsaved changes.
    unsaved = bool(project and project.startswith("*"))
    if unsaved:
        project = project[1:]

    return _dump({
        "running": True,
        "window_title": title,
        "open_project": project,
        "has_unsaved_changes": unsaved,
        "pid": main["pid"],
        "minimized": main["minimized"],
        "focused": bool(winctl.user32.GetForegroundWindow() == main["hwnd"]),
        "window_count": len(windows),
        # Cannot be read from Next; set with next_set_project_tempo.
        "project_tempo_bpm": state.get_project_tempo(),
        "note": "'has_unsaved_changes' is inferred from the '*' Next puts in "
                "front of the project name in its title bar.",
    })


class ListCommandsInput(BaseModel):
    """Input model for browsing the command vocabulary."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    category: Optional[str] = Field(
        default=None,
        description="Filter by category: transport, file, tracks, edit, bounce, view, zoom.",
    )


@mcp.tool(
    name="next_list_commands",
    annotations=ToolAnnotations(
        title="List Cakewalk Next commands",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_list_commands(params: ListCommandsInput) -> str:
    """List the commands next_run_command can send, with their shortcuts.

    Commands marked destructive require confirm=true.
    """
    items = [commands.describe(name) for name in commands.COMMAND_NAMES]
    if params.category:
        wanted = params.category.strip().lower()
        items = [i for i in items if i["category"] == wanted]
        if not items:
            raise ToolError(
                "Unknown category %r. Valid categories: %s."
                % (params.category, ", ".join(sorted({c["category"] for c in
                    (commands.describe(n) for n in commands.COMMAND_NAMES)})))
            )
    return _dump({"count": len(items), "commands": items})


class RunCommandInput(BaseModel):
    """Input model for sending a command to the running Cakewalk Next window."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    command: str = Field(
        ...,
        description="Command name from next_list_commands, e.g. 'play_pause', 'save_project'.",
        min_length=1,
    )
    confirm: bool = Field(
        default=False,
        description="Required (true) for destructive commands such as delete, "
        "cut, new_project, open_project and the bounce/normalize operations.",
    )
    repeat: int = Field(
        default=1, description="Send the command this many times.", ge=1, le=MAX_REPEAT
    )


@mcp.tool(
    name="next_run_command",
    annotations=ToolAnnotations(
        title="Send a command to Cakewalk Next",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def next_run_command(params: RunCommandInput) -> str:
    """Run a Cakewalk Next command by focusing its window and sending the shortcut.

    Only the vetted commands from next_list_commands can be sent; arbitrary
    keystrokes are not accepted. The window is brought to the foreground first,
    which takes focus from whatever the user is doing.

    Commands act on the current selection and playhead, which this server cannot
    see. Check next_app_status first, and prefer explicit selection commands
    over assuming what is selected. Single-letter commands (play_pause, record,
    split) are swallowed if a text field inside Next has focus.
    """
    spec = commands.describe(params.command)
    if not spec:
        hint = commands.suggest(params.command)
        raise ToolError(
            "Unknown command %r.%s Call next_list_commands for the full list."
            % (params.command, (" Did you mean: %s?" % ", ".join(hint)) if hint else "")
        )

    if spec["destructive"] and not params.confirm:
        raise ToolError(
            "'%s' (%s) is destructive: %s. Re-issue with confirm=true if the "
            "user has agreed to it." % (spec["command"], spec["shortcut"], spec["description"])
        )

    try:
        window = winctl.main_window()
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc

    before = window["title"]
    if not winctl.focus(window["hwnd"]):
        raise ToolError(
            "Could not bring the Cakewalk Next window to the foreground; the "
            "keystroke was not sent.%s" % winctl.focus_failure_reason()
        )

    try:
        for index in range(params.repeat):
            if index:
                time.sleep(_INTER_KEY_DELAY)
            # Re-check before every press: focus can move mid-sequence, and a
            # keystroke sent then would land in whatever app took it.
            winctl.send_chord(spec["shortcut"], expect_hwnd=window["hwnd"])
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc

    time.sleep(_INTER_KEY_DELAY)
    try:
        after = winctl.main_window()["title"]
    except winctl.WindowError:
        after = None

    return _dump({
        "sent": spec["command"],
        "shortcut": spec["shortcut"],
        "repeat": params.repeat,
        "window_title_before": before,
        "window_title_after": after,
        "title_changed": before != after,
        "note": "Cakewalk Next gives no completion signal. A changed title "
                "usually means the project or its saved state changed; an "
                "unchanged title does not prove the command was ignored.",
    })


IMPORTABLE = (".mid", ".midi", ".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".oga", ".w64")


class ImportFileInput(BaseModel):
    """Input model for inserting an audio or MIDI file into the open project."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(
        ...,
        description="Audio or MIDI file to insert, e.g. the .mid written by next_write_midi.",
        min_length=1,
    )


@mcp.tool(
    name="next_import_file",
    annotations=ToolAnnotations(
        title="Insert an audio or MIDI file into the open project",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def next_import_file(params: ImportFileInput) -> str:
    """Insert an audio or MIDI file into the project open in Cakewalk Next.

    This drives Insert > Insert Audio/MIDI File..., which has no keyboard
    shortcut, so it clicks the menu and then types the path into the file
    dialog. It pairs with next_write_midi to get generated parts into a project
    without you dragging the file in by hand.

    The click positions are measured from the shipping build's menus and scaled
    to the window's DPI. The tool confirms the file dialog actually opened
    before typing anything; if it did not, it presses ESC and reports failure
    rather than scattering keystrokes into the app.

    **The file lands on the currently selected track, and that track keeps its
    own instrument.** That is what makes multi-track arrangements possible:
    create an instrument track, select it, import its part, repeat. The clip
    takes its name from the MIDI track name. Importing with nothing suitable
    selected creates new tracks instead, which get a default instrument.

    The part lands at the playhead, so send go_to_start first unless you want
    it somewhere else.
    """
    path = os.path.abspath(params.path)
    if not os.path.isfile(path):
        raise ToolError("No such file: %s" % path)
    if not path.lower().endswith(IMPORTABLE):
        raise ToolError(
            "%s is not an importable type. Cakewalk Next accepts: %s."
            % (os.path.basename(path), ", ".join(IMPORTABLE))
        )

    try:
        window = winctl.main_window()
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc

    if not winctl.focus(window["hwnd"]):
        raise ToolError(
            "Could not bring the Cakewalk Next window to the foreground; nothing "
            "was clicked.%s" % winctl.focus_failure_reason()
        )

    try:
        clicks = menus.click_item(
            window["hwnd"], window["pid"], "insert", "insert_audio_midi_file"
        )
    except (menus.MenuError, winctl.WindowError) as exc:
        menus.dismiss()
        raise ToolError(str(exc)) from exc

    dialog = winctl.find_dialog(window["pid"], timeout=8.0)
    if not dialog:
        menus.dismiss()
        raise ToolError(
            "Clicked Insert > Insert Audio/MIDI File at %s but no file dialog "
            "appeared within 8s, so nothing was typed. The menu layout may have "
            "changed in this version of Next - check menus.py against the app, "
            "or insert the file from the UI." % clicks["item_click"]
        )

    # Fill the dialog by window message, not by typing. Keystrokes go to
    # whichever control currently holds focus - in one run that was the file
    # list, where CTRL+A selected every file instead of the filename - and
    # per-character typing races the dialog's autocomplete, which once turned
    # the path into "iC:\...\riff.md". WM_SETTEXT needs no focus, cannot be
    # reordered, and leaves the user's clipboard alone.
    field = winctl.find_control(dialog["hwnd"], "Edit", winctl.FILENAME_FIELD_ID)
    open_button = winctl.find_control(dialog["hwnd"], "Button", winctl.IDOK)
    if field is None or open_button is None:
        menus.dismiss()
        raise ToolError(
            "The file dialog opened but its filename field (control %d) or Open "
            "button could not be found, so nothing was entered. Insert the file "
            "from the UI." % winctl.FILENAME_FIELD_ID
        )

    if not winctl.set_control_text(field, path):
        menus.dismiss()
        raise ToolError(
            "Could not set the filename field; it holds %r instead of %r. "
            "Nothing was opened." % (winctl.control_text(field), path)
        )

    winctl.click_control(open_button)

    closed = False
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if winctl.find_dialog(window["pid"], timeout=0.2) is None:
            closed = True
            break

    return _dump({
        "imported": path,
        "dialog_title": dialog["title"],
        "dialog_closed": closed,
        "clicks": clicks,
        "note": "Inserted at the playhead on the selected track. If dialog_closed "
                "is false the picker may still be open - check the app. Cakewalk "
                "Next gives no completion signal, so verify visually.",
    })


class ListInstrumentsInput(BaseModel):
    """Input model for browsing the installed instrument library."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: Optional[str] = Field(
        default=None,
        description="Free text matched against name, slug, category and tags, "
        "e.g. 'grand', '808', 'bass'.",
    )
    category: Optional[str] = Field(
        default=None, description="'kit' for drum kits, 'instrument' for melodic banks."
    )
    family: Optional[str] = Field(
        default=None,
        description="Instrument family slug, e.g. 'piano', 'synth-bass', 'drum-pads'.",
    )
    installed_only: bool = Field(
        default=False,
        description="Only banks whose samples are already downloaded. The rest "
        "show a download arrow in Next and will not sound until fetched.",
    )
    limit: int = Field(default=40, description="Maximum results.", ge=1, le=200)
    offset: int = Field(default=0, description="Results to skip, for paging.", ge=0)
    response_format: str = Field(
        default="markdown", description="'markdown' for a readable list, 'json' for raw data."
    )


@mcp.tool(
    name="next_list_instruments",
    annotations=ToolAnnotations(
        title="List Cakewalk Next instruments",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_list_instruments(params: ListInstrumentsInput) -> str:
    """Browse the instrument library Cakewalk Next has installed locally.

    Covers every BandLab soundbank on this machine - pianos, 808s, drum kits,
    synths - with its display name and the slug that appears in project files.
    Read offline; Next does not need to be running.

    Use next_instrument_info for one instrument's detail, including a drum kit's
    pad-to-MIDI-note map.
    """
    try:
        result = instruments.search(
            params.query, params.category, params.family,
            params.limit, params.offset, installed_only=params.installed_only,
        )
    except instruments.InstrumentError as exc:
        raise ToolError(str(exc)) from exc

    if params.response_format == "json":
        return _dump(result)

    if not result["items"]:
        return "No instruments matched."

    lines = ["Showing %d of %d instruments." % (result["count"], result["total"]), ""]
    for item in result["items"]:
        bits = [item["category"]]
        if not item.get("installed"):
            bits.append("NOT downloaded")
        if item.get("family"):
            bits.append(item["family"])
        if item.get("note_range"):
            bits.append("notes %d-%d" % (item["note_range"]["low"], item["note_range"]["high"]))
        lines.append("- **%s** (`%s`) - %s" % (item["name"], item["slug"], ", ".join(bits)))
    if result["has_more"]:
        lines += ["", "More available: call again with offset=%d." % result["next_offset"]]
    return "\n".join(lines)


class InstrumentInfoInput(BaseModel):
    """Input model for one instrument's detail."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    instrument: str = Field(
        ...,
        description="Instrument name or slug, e.g. 'Dark Grand', '808', '606-kit-v3-v4'.",
        min_length=1,
    )


@mcp.tool(
    name="next_instrument_info",
    annotations=ToolAnnotations(
        title="Describe one Cakewalk Next instrument",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_instrument_info(params: InstrumentInfoInput) -> str:
    """Describe one instrument, including a drum kit's pad-to-note map.

    For a kit this returns every pad with the MIDI note that triggers it and the
    sound it makes ('Kick', 'Snare', 'Clap'), which is what you need to write a
    correct drum part - these kits are NOT General MIDI mapped, so note 38 is
    only a snare because this kit says so.

    For a melodic instrument it returns the sampled playable range, so parts stay
    within notes that actually sound.
    """
    try:
        return _dump(instruments.details(params.instrument))
    except instruments.InstrumentError as exc:
        raise ToolError(str(exc)) from exc


class MidiPortsInput(BaseModel):
    """Input model for the MIDI port listing."""

    model_config = ConfigDict(extra="forbid")


@mcp.tool(
    name="next_list_midi_ports",
    annotations=ToolAnnotations(
        title="List MIDI ports",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_list_midi_ports(params: MidiPortsInput) -> str:
    """List this machine's MIDI output and input ports, and whether live play is possible.

    Playing notes into Cakewalk Next in real time needs a virtual MIDI cable:
    one port that this server writes to and Next listens on. Windows does not
    ship one. If the only output is the Microsoft GS Wavetable Synth, live play
    is unavailable and this tool says what to install.
    """
    try:
        outputs = midiout.list_outputs()
        inputs = midiout.list_inputs()
    except midiout.MidiError as exc:
        raise ToolError(str(exc)) from exc

    usable = [p for p in outputs if p["usable_for_next"]]
    payload = {
        "outputs": outputs,
        "inputs": inputs,
        "live_play_available": bool(usable and inputs),
    }
    if not usable:
        payload["problem"] = (
            "No MIDI output can reach Cakewalk Next. The Microsoft GS Wavetable "
            "Synth plays through the speakers; it is not a route into another app."
        )
        payload["fix"] = (
            "Install loopMIDI (free, https://www.tobias-erichsen.de/software/loopmidi.html "
            "or 'winget install TobiasErichsen.loopMIDI'), add one port in it, "
            "then enable that port in Next under Edit > Preferences > MIDI. "
            "loopMIDI must be running for the port to exist."
        )
    elif not inputs:
        payload["problem"] = (
            "There is a usable output but no MIDI input, so Next has nothing to "
            "listen to. A loopback port should appear as both."
        )
    else:
        payload["next_step"] = (
            "Enable %r as a MIDI input in Next (Edit > Preferences > MIDI), arm "
            "an instrument track, then use next_play_notes."
            % usable[0]["name"]
        )
    return _dump(payload)


class PlayNotesInput(BaseModel):
    """Input model for playing notes live."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    notes: List[NoteInput] = Field(
        ..., description="Notes to play, positioned in beats.", min_length=1, max_length=2000
    )
    tempo_bpm: Optional[float] = Field(
        default=None,
        description="Tempo in BPM. Defaults to the stored project tempo so the "
        "phrase lines up with the project grid.",
        gt=0, le=960,
    )
    channel: int = Field(default=0, description="MIDI channel 0-15.", ge=0, le=15)
    port: Optional[str] = Field(
        default=None,
        description="MIDI output port name or partial name. Defaults to the first "
        "port that can reach Next.",
    )
    program: Optional[int] = Field(
        default=None,
        description="Optional General MIDI program change sent first, 0-127. "
        "Ignored by Next, which uses the track's own instrument.",
        ge=0, le=127,
    )


@mcp.tool(
    name="next_play_notes",
    annotations=ToolAnnotations(
        title="Play notes live over MIDI",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def next_play_notes(params: PlayNotesInput) -> str:
    """Play notes in real time out of a MIDI port, so Cakewalk Next sounds them.

    Requires a virtual MIDI cable (see next_list_midi_ports) with the port
    enabled as an input in Next and an instrument track armed or focused. Use
    this to audition an instrument; use next_write_midi plus next_import_file to
    commit a part to the timeline, since notes played here are only recorded if
    Next is actually recording.

    This call blocks for the length of the phrase, capped at 60 seconds, and
    always sends all-notes-off afterwards so nothing can hang on.
    """
    events = []
    for note in params.notes:
        pitches = note.pitch if isinstance(note.pitch, list) else [note.pitch]
        for pitch in pitches:
            try:
                resolved = smf.parse_pitch(pitch)
            except smf.MidiError as exc:
                raise ToolError(str(exc)) from exc
            events.append({
                "pitch": resolved,
                "start": note.start,
                "duration": note.duration,
                "velocity": note.velocity,
            })

    try:
        tempo, tempo_source = state.resolve_tempo(params.tempo_bpm)
        result = midiout.play(
            events,
            tempo_bpm=tempo,
            channel=params.channel,
            port=params.port,
            program=params.program,
        )
    except midiout.MidiError as exc:
        raise ToolError(str(exc)) from exc

    result["tempo_bpm"] = tempo
    result["tempo_source"] = tempo_source
    result["tempo_note"] = state.tempo_note(tempo_source, tempo)
    result["note"] = (
        "Sent live. Cakewalk Next only sounds this if the port is enabled as a "
        "MIDI input and a track with an instrument is armed or selected."
    )
    return _dump(result)


class CreateInstrumentTrackInput(BaseModel):
    """Input model for adding an instrument track."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    instrument: str = Field(
        ...,
        description="Instrument name as it appears in Next, e.g. '808', '606', "
        "'Dark Grand'. Must be one that is downloaded - check with "
        "next_list_instruments(installed_only=true).",
        min_length=1,
    )
    row: int = Field(
        default=0,
        description="Which search result to add, 0-based. Next matches tags as "
        "well as names and sorts alphabetically, so the wanted bank is often "
        "NOT first: 'Grand Piano' lists Dark Grand, Grand Piano, Studio Grand. "
        "The reply's 'likely_rows' shows the expected ordering.",
        ge=0, le=40,
    )


@mcp.tool(
    name="next_create_instrument_track",
    annotations=ToolAnnotations(
        title="Create an instrument track",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def next_create_instrument_track(params: CreateInstrumentTrackInput) -> str:
    """Add a track to the open project with a named instrument loaded.

    Drives Insert > Create Instrument Track, types the name into the browser's
    search box, and clicks the result at ``row`` (0-based) followed by Add.

    Searching does NOT guarantee the wanted bank is the top row, which this
    assumed until it quietly loaded the wrong instrument twice: Next matches
    tags as well as names and sorts alphabetically, so "Grand Piano" lists Dark
    Grand first and "Piano" lists Accordion first. Check 'likely_rows' in the
    reply - or next_screenshot the dialog - and pass the matching ``row``.

    Pair with next_import_file to build an arrangement: create the track, then
    import that part - the import lands on the selected track and keeps this
    instrument.

    The browser is a JUCE dialog with no addressable controls, so this clicks
    measured positions. It verifies the browser opened before typing and that
    it closed afterwards, and refuses instruments that are not downloaded,
    since those cannot make a sound.

    Note that `undo` does NOT remove a track created this way. To get rid of
    one, select its strip and use `delete` with confirm=true.
    """
    found = None
    try:
        found = instruments.resolve(params.instrument)
    except instruments.InstrumentError:
        pass
    # Deliberately not an error. Adding the track is exactly what makes Next
    # fetch the samples -- refusing here left the tool unable to do the one
    # thing that fixes the problem. The reply carries `samples_missing` so a
    # caller can verify the download landed before relying on the sound.
    samples_missing = instruments.needs_download(found)
    if False:
        raise ToolError(
            "%r is in the library but its samples are not downloaded, so the "
            "track would be silent. Download it inside Next first, or pick one "
            "of: %s." % (
                found["name"],
                ", ".join(i["name"] for i in
                          instruments.search(installed_only=True, limit=20)["items"]),
            )
        )

    try:
        window = winctl.main_window()
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc
    if not winctl.focus(window["hwnd"]):
        raise ToolError(
            "Could not bring Cakewalk Next to the foreground; nothing was "
            "clicked.%s" % winctl.focus_failure_reason()
        )

    try:
        menus.click_item(window["hwnd"], window["pid"], "insert", "create_instrument_track")
    except (menus.MenuError, winctl.WindowError) as exc:
        menus.dismiss()
        raise ToolError(str(exc)) from exc

    dialog = menus.instrument_browser(window["pid"], timeout=10.0)
    if not dialog:
        menus.dismiss()
        raise ToolError(
            "Insert > Create Instrument Track did not open the instrument "
            "browser, so no track was added."
        )

    try:
        clicks = menus.choose_instrument(
            dialog["hwnd"], params.instrument, window["pid"], row=params.row)
    except winctl.WindowError as exc:
        menus.dismiss()
        raise ToolError(str(exc)) from exc

    deadline = time.time() + 15.0
    while time.time() < deadline:
        if menus.instrument_browser(window["pid"], timeout=0.3) is None:
            return _dump({
                "created": params.instrument,
                "resolved": found["name"] if found else None,
                "samples_missing": samples_missing,
                "row_clicked": params.row,
                "likely_rows": _predict_browser_rows(params.instrument),
                "clicks": {k: (list(v) if isinstance(v, tuple) else v)
                           for k, v in clicks.items()},
                "next_step": ("Samples were not on disk; adding the track asks "
                              "Next to fetch them, so check the sound before "
                              "relying on it. " if samples_missing else "") +
                             "Select this track and call next_import_file to put a "
                             "part on it; the import keeps this instrument. If "
                             "'likely_rows' does not have your instrument at "
                             "'row_clicked', delete the track and retry with the "
                             "right row - next_screenshot shows what was loaded.",
            })
        time.sleep(0.3)

    menus.dismiss()
    raise ToolError(
        "Added %r but the browser did not close, which usually means the search "
        "matched nothing so Add stayed disabled. Check the name against "
        "next_list_instruments." % params.instrument
    )


class ScreenshotInput(BaseModel):
    """Input model for looking at the Cakewalk Next window."""

    model_config = ConfigDict(extra="forbid")

    max_width: int = Field(
        default=1400,
        description="Downscale so the image is at most this wide. Smaller is "
        "cheaper; 1400 keeps track names and clip positions readable.",
        ge=200, le=3840,
    )
    focus_first: bool = Field(
        default=True,
        description="Bring Next to the front before capturing. Set false to "
        "capture it where it is, which fails if it is minimised.",
    )


@mcp.tool(
    name="next_screenshot",
    annotations=ToolAnnotations(
        title="Look at the Cakewalk Next window",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def next_screenshot(params: ScreenshotInput) -> Image:
    """Return a picture of the Cakewalk Next window.

    Next draws its own UI, so track names, clip positions, selection, mute and
    solo states, and the tempo cannot be queried - they can only be seen. Use
    this to check what is actually there before acting, and to confirm that an
    action did what you intended.

    Worth doing after anything that changes the project: creating a track,
    importing a part, or running a command whose effect you cannot otherwise
    observe.
    """
    try:
        window = winctl.main_window()
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc

    if params.focus_first and not winctl.focus(window["hwnd"]):
        raise ToolError(
            "Could not bring Cakewalk Next to the front to photograph it.%s"
            % winctl.focus_failure_reason()
        )
    try:
        png, _size = screen.capture_window(window["hwnd"], params.max_width)
    except screen.CaptureError as exc:
        raise ToolError(str(exc)) from exc
    return Image(data=png, format="png")


TRACK_TYPES = {
    "audio": "create_audio_track",
    "instrument": None,                       # needs the instrument browser
    "sampler": "create_sampler_track",
    "pad_controller": "create_pad_controller_track",
    "instrument_rack": "create_instrument_rack_track",
    "folder": "create_track_folder",
    "bus": "create_bus",
}


class CreateTrackInput(BaseModel):
    """Input model for adding a track of any type."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    track_type: str = Field(
        default="instrument",
        description="One of: audio, instrument, sampler, pad_controller, "
        "instrument_rack, folder, bus.",
    )
    instrument: Optional[str] = Field(
        default=None,
        description="Required for track_type='instrument': the instrument to "
        "load, e.g. '808', 'Dark Grand'. Ignored for other types.",
    )


@mcp.tool(
    name="next_create_track",
    annotations=ToolAnnotations(
        title="Create a track of any type",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def next_create_track(params: CreateTrackInput) -> str:
    """Add a track to the open project.

    Every type except 'instrument' is a plain keyboard shortcut. An instrument
    track additionally has to pick a sound from the browser, so it delegates to
    next_create_instrument_track.

    A newly created track becomes the selected one, so the reliable way to build
    an arrangement is create-then-import: no track needs to be clicked, which
    avoids depending on strip geometry entirely.
    """
    kind = params.track_type.strip().lower()
    if kind not in TRACK_TYPES:
        raise ToolError(
            "Unknown track_type %r. Valid types: %s."
            % (params.track_type, ", ".join(sorted(TRACK_TYPES)))
        )

    if kind == "instrument":
        if not params.instrument:
            raise ToolError(
                "track_type='instrument' needs an instrument name. Pick one from "
                "next_list_instruments(installed_only=true), or use a different "
                "track_type."
            )
        return await next_create_instrument_track(
            CreateInstrumentTrackInput(instrument=params.instrument)
        )

    result = await next_run_command(RunCommandInput(command=TRACK_TYPES[kind]))
    payload = json.loads(result)
    payload["track_type"] = kind
    payload["note"] = ("Created a %s track; it is now the selected track, so "
                       "next_import_file will land on it." % kind)
    return _dump(payload)


class SelectTrackInput(BaseModel):
    """Input model for selecting a track by position."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(
        ...,
        description="Track number as shown in Next, 1-based. Master is not "
        "addressable: strips are found by their coloured instrument icon, and "
        "Master's is grey, so it never enters the numbering.",
        ge=1, le=256,
    )


@mcp.tool(
    name="next_select_track",
    annotations=ToolAnnotations(
        title="Select a track",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_select_track(params: SelectTrackInput) -> str:
    """Select a track by its number, located by looking at the screen.

    Tracks are 1-based and Master is not addressable. Both the row and the
    column are measured, not assumed: the old fixed column landed on the volume
    fader rather than the name, so the click selected nothing and every delete
    that followed silently did nothing, and the old row was off by one strip,
    which selects the wrong track entirely.

    Prefer create-then-import where you can -- a new track is already selected
    and needs no clicking. Verify with next_screenshot before anything
    destructive: a wrong selection followed by a delete cannot be undone.
    """
    try:
        window = winctl.main_window()
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc
    if not winctl.focus(window["hwnd"]):
        raise ToolError(
            "Could not focus Cakewalk Next.%s" % winctl.focus_failure_reason())

    try:
        x, y, info = layout.track_point(window["hwnd"], params.index, "name")
    except (layout.LayoutError, screen.CaptureError) as exc:
        raise ToolError(str(exc)) from exc

    try:
        winctl.click_in(window["pid"], x, y, "track %d's name" % params.index)
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc
    time.sleep(0.4)
    return _dump({
        "selected_index": params.index,
        "clicked_at": [x, y],
        "tracks_visible": info["track_count"],
        "note": "Row and column both measured from the screen. Confirm with "
                "next_screenshot if the next step is destructive.",
    })


class DeleteTrackInput(BaseModel):
    """Input model for deleting a track."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(
        ..., description="Track number to delete, 1-based, as next_list_tracks "
        "reports it. Confirm with next_screenshot first -- this is not "
        "undoable.", ge=1, le=256)
    confirm: bool = Field(
        default=False,
        description="Must be true. Deleting a track removes its clips and its "
        "instrument, and undo does not bring it back.",
    )


@mcp.tool(
    name="next_delete_track",
    annotations=ToolAnnotations(
        title="Delete a track",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def next_delete_track(params: DeleteTrackInput) -> str:
    """Delete a track, its clips and its instrument.

    This is how an instrument is removed: Next has no mapped control for taking
    the instrument off a track while keeping the track, so the track goes with
    it. Recreate it with next_create_track if you wanted a different sound.

    Undo does not restore a deleted track, which is why confirm is required.
    """
    if not params.confirm:
        raise ToolError(
            "Deleting track %d removes its clips and instrument, and undo will "
            "not bring it back. Re-issue with confirm=true if that is intended."
            % params.index
        )

    await next_select_track(SelectTrackInput(index=params.index))
    result = await next_run_command(RunCommandInput(command="delete", confirm=True))
    payload = json.loads(result)
    payload["deleted_index"] = params.index
    payload["note"] = ("Track deleted. Check with next_screenshot - Next gives no "
                       "confirmation, and a mis-selected track would look the same.")
    return _dump(payload)


class DawTempoInput(BaseModel):
    """Input model for changing the tempo inside Cakewalk Next."""

    model_config = ConfigDict(extra="forbid")

    tempo_bpm: float = Field(
        ..., description="Tempo to set in Next, 20-300 BPM.", ge=20, le=300)


@mcp.tool(
    name="next_set_daw_tempo",
    annotations=ToolAnnotations(
        title="Change the tempo in Cakewalk Next",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_set_daw_tempo(params: DawTempoInput) -> Image:
    """Set the project tempo in Next itself, and remember it for the MIDI tools.

    The toolbar tempo is a drawn control with no queryable text, so this
    double-clicks it, clears the field and types the value. Typing without
    clearing *appends*: 120 with "150" typed became 300, Next's ceiling.

    The field is positioned from the CENTRE of the window, not its left edge:
    the transport is centre-anchored, so a left-anchored offset drifts wider
    the bigger the window gets. It used to miss entirely on a 2560-wide one,
    and a missed click is silent - the backspaces and digits land on whatever
    had focus and the tempo simply stays put.

    Returns a picture of the toolbar so the new value can be read back - there
    is no way to query it, so seeing it is the only confirmation available.
    Note that the tempo is recorded for the MIDI tools whether or not the click
    landed, so next_app_status reports what this was told, not what Next shows.
    Read the returned image; do not trust the status field alone.
    """
    try:
        window = winctl.main_window()
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc
    if not winctl.focus(window["hwnd"]):
        raise ToolError(
            "Could not focus Cakewalk Next.%s" % winctl.focus_failure_reason())

    try:
        left, top, width, _h = screen.visible_rect(window["hwnd"])
    except screen.CaptureError as exc:
        raise ToolError(str(exc)) from exc
    scale = winctl.dpi_scale(window["hwnd"])
    x = left + width // 2 + int(TEMPO_FIELD[0] * scale)
    y = top + int(TEMPO_FIELD[1] * scale)

    try:
        winctl.double_click_in(window["pid"], x, y, "the tempo field")
        time.sleep(0.7)
        for _ in range(8):                      # clear whatever is there
            winctl.send_chord("BACKSPACE", expect_hwnd=window["hwnd"])
            time.sleep(0.05)
        winctl.send_text("%g" % params.tempo_bpm)
        time.sleep(0.3)
        winctl.send_chord("ENTER", expect_hwnd=window["hwnd"])
        time.sleep(1.2)
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc

    state.set_project_tempo(params.tempo_bpm)
    try:
        # Centre-anchored like the click above: a left-anchored crop framed
        # empty toolbar on a wide window, so the "confirmation" proved nothing.
        png, _size = screen.capture_region(
            left + width // 2 + int((TEMPO_FIELD[0] - 120) * scale),
            top + int(30 * scale),
            int(360 * scale), int(50 * scale), max_width=720)
    except screen.CaptureError as exc:
        raise ToolError(str(exc)) from exc
    return Image(data=png, format="png")


class ProjectTempoInput(BaseModel):
    """Input model for recording the open project's tempo."""

    model_config = ConfigDict(extra="forbid")

    tempo_bpm: Optional[float] = Field(
        default=None,
        description="The project's tempo in BPM, as shown in Next's toolbar. "
        "Omit to just read back what is currently stored.",
        gt=0, le=960,
    )


@mcp.tool(
    name="next_set_project_tempo",
    annotations=ToolAnnotations(
        title="Set or read the project tempo",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def next_set_project_tempo(params: ProjectTempoInput) -> str:
    """Tell the server the tempo of the project open in Cakewalk Next.

    next_write_midi and next_play_notes default to this, so generated parts land
    on the project's grid instead of an assumed 120 BPM. Nothing can read the
    tempo back out of Next - the .cnp parser cannot reach it and the toolbar is a
    JUCE-drawn control with no queryable text - so it has to be set here once.
    Read it off the toolbar (the number next to the metronome icon).

    Call with no argument to read the stored value.
    """
    if params.tempo_bpm is None:
        current = state.get_project_tempo()
        return _dump({
            "project_tempo_bpm": current,
            "note": "Not set; %g BPM is assumed until you set it."
                    % state.FALLBACK_TEMPO if current is None else
                    "Generated parts default to this tempo.",
        })

    try:
        stored = state.set_project_tempo(params.tempo_bpm)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return _dump({
        "project_tempo_bpm": stored,
        "note": "next_write_midi and next_play_notes now default to %g BPM." % stored,
    })


def main():
    """Entry point for ``python -m cakewalk_next_mcp``."""
    mcp.run()


if __name__ == "__main__":
    main()


# ==========================================================================
# Composition.
#
# These exist so the musical decisions live in the server rather than in
# whatever script happens to be driving it. Everything produced here is checked
# before it is returned: notes outside the key, sustained notes that fight the
# chord, intervals voiced too low to stay defined, and parts that ring past the
# final bar line.
# ==========================================================================
def _smf_notes(notes):
    """compose/theory use start+duration; smf.Note uses *_beats. Bridge them."""
    return [smf.Note(pitch=n["pitch"], start_beats=n["start"],
                     duration_beats=n["duration"], velocity=n["velocity"])
            for n in notes]


# Kits name their pads however they like. The 606 calls its closed hat "Hi Hat
# Closed"; the Lofi Hop kit calls it "HH", its rim "Stick" and its crash
# "Crash". Matching on one keyword per role silently loses whole instruments --
# searching for "closed" against the Lofi Hop kit finds no hat at all, and the
# beat comes out as kick and snare only. So each role carries several spellings
# and, where it needs them, exclusions.
PAD_ROLES = (
    # role, name fragments, exclusions, GM fallback note
    ("kick",   ("kick", "bass drum"), ("alt", "sub"), 36),
    ("snare",  ("snare", "snr"), ("alt",), 38),
    ("clap",   ("clap", "handclap"), (), 39),
    ("open",   ("hh open", "open hat", "open hi", "openedhh", "openhh",
                "hihat open", "hi hat open", "opened"), (), 46),
    ("closed", ("closed", "closedhh", "hh", "hihat", "hi hat", "hat"),
               ("open", "semi", "pedal", "foot"), 42),
    ("shaker", ("shaker", "tambourine", "maraca"), ("crash",), None),
    ("cymbal", ("crash", "cymbal"), ("ride",), 49),
    ("ride",   ("ride",), ("bell",), 51),
    ("click",  ("stick", "rim", "click", "cross"), (), 37),
)


def _pad_roles(pads):
    """Map a kit's pad list onto the roles the drum writer asks for.

    Names alone are not enough. The Boom Bap kit calls its main kick simply
    "BBap" -- no keyword at all -- while naming the secondary one "BBap
    altkick", so matching on "kick" picks the wrong drum. These kits are
    GM-ordered, so where no name matches, the General MIDI note for that role
    is used if the kit actually has a pad there.
    """
    available = {pad["midi"] for pad in pads}
    found = {}
    for role, wanted, banned, fallback in PAD_ROLES:
        for pad in pads:
            sound = (pad.get("sound") or "").lower()
            if any(w in sound for w in wanted) and not any(b in sound for b in banned):
                found[role] = pad["midi"]
                break
        else:
            if fallback is not None and fallback in available:
                found[role] = fallback
    return found


class MusicReferenceInput(BaseModel):
    """Input model for looking up what a key contains."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    key: str = Field(default="C", description="Tonic, e.g. 'C', 'F#', 'Bb'.")
    mode: str = Field(
        default="major",
        description="Scale or mode: major, minor, dorian, lydian, mixolydian, "
        "phrygian, harmonic_minor, melodic_minor, pentatonics, blues.")
    progression: Optional[str] = Field(
        default=None,
        description="A named progression to resolve into this key. Omit to list "
        "every progression the server knows, with its mood.")


@mcp.tool(
    name="next_music_reference",
    annotations=ToolAnnotations(
        title="Look up scales, chords and progressions",
        readOnlyHint=True, destructiveHint=False,
        idempotentHint=True, openWorldHint=False,
    ),
)
async def next_music_reference(params: MusicReferenceInput) -> str:
    """What a key contains: its notes, the chords it generates, and progressions.

    Worth calling before writing notes by hand, so material starts in the key
    instead of being corrected afterwards.
    """
    try:
        pcs, _root, mode = theory.scale_pitch_classes(params.key, params.mode)
        payload = {
            "key": params.key,
            "mode": mode,
            "scale_notes": [smf.pitch_name(60 + p)[:-1] for p in sorted(pcs)],
            "one_octave_from_c4": theory.scale_degrees(params.key, mode, octave=4),
        }
        if len(pcs) == 7:
            payload["diatonic_chords"] = theory.diatonic_chords(params.key, mode)

        if params.progression:
            if params.progression not in theory.PROGRESSIONS:
                raise ToolError(
                    "Unknown progression %r. Known: %s"
                    % (params.progression, ", ".join(sorted(theory.PROGRESSIONS))))
            spec = theory.PROGRESSIONS[params.progression]
            payload["progression"] = {
                "name": params.progression,
                "numerals": spec["numerals"],
                "mood": spec["mood"],
                "chords": theory.progression_chords(spec["numerals"], params.key, mode),
            }
        else:
            payload["progressions"] = {
                name: {"numerals": spec["numerals"], "mood": spec["mood"]}
                for name, spec in sorted(theory.PROGRESSIONS.items())
            }
    except theory.TheoryError as exc:
        raise ToolError(str(exc)) from exc
    return _dump(payload)


class CheckMusicInput(BaseModel):
    """Input model for auditing written notes."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    parts: dict = Field(
        ...,
        description="{'part name': [{pitch,start,duration,velocity}, ...]}, "
        "starts and durations in beats. Parts whose name mentions drums, kit "
        "or perc are skipped, since a kit's pad numbers are not pitches.")
    key: Optional[str] = Field(
        default=None, description="Tonic to check against, e.g. 'C'.")
    mode: str = Field(default="major", description="Mode to check against.")
    bars: Optional[int] = Field(
        default=None,
        description="Intended length. Anything ringing past the final bar line "
        "is reported, because the DAW rounds the project up to another bar.",
        ge=1, le=2048)


@mcp.tool(
    name="next_check_music",
    annotations=ToolAnnotations(
        title="Check notes for wrong keys, clashes and muddy voicings",
        readOnlyHint=True, destructiveHint=False,
        idempotentHint=True, openWorldHint=False,
    ),
)
async def next_check_music(params: CheckMusicInput) -> str:
    """Audit note material before it goes near the DAW.

    Catches the faults that do NOT sound like a wrong note, and so are very
    hard to trace by ear -- they read as the whole track being vaguely out of
    tune: a pitch outside the key, a sustained note that is neither a chord
    tone nor an available tension, an interval voiced too low to stay defined,
    and a part that overruns the last bar.
    """
    parts = {}
    for name, notes in (params.parts or {}).items():
        cleaned = []
        for item in notes or []:
            try:
                cleaned.append({
                    "pitch": int(item["pitch"]),
                    "start": float(item["start"]),
                    "duration": float(item.get("duration", 1.0)),
                    "velocity": int(item.get("velocity", 100)),
                })
            except (KeyError, TypeError, ValueError) as exc:
                raise ToolError("Bad note in part %r: %r (%s)" % (name, item, exc))
        parts[name] = cleaned
    if not parts:
        raise ToolError("No parts given.")

    try:
        problems = theory.check_material(
            parts, key=params.key, mode=params.mode, bars=params.bars)
    except theory.TheoryError as exc:
        raise ToolError(str(exc)) from exc

    errors = [p for p in problems if p["severity"] == "error"]
    return _dump({
        "checked_parts": {k: len(v) for k, v in parts.items()},
        "clean": not problems,
        "error_count": len(errors),
        "warning_count": len(problems) - len(errors),
        "problems": problems,
    })


class ComposeInput(BaseModel):
    """Input model for composing a whole arrangement."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    style: str = Field(
        default="lofi",
        description="lofi, neo_soul, trap, house or ambient. Sets tempo, feel, "
        "swing, registers and the default progressions.")
    key: str = Field(default="C", description="Tonic, e.g. 'C', 'F#', 'Bb'.")
    mode: Optional[str] = Field(
        default=None, description="Override the style's mode.")
    form: str = Field(
        default="verse_chorus",
        description="loop, short, verse_chorus or long.")
    bars: Optional[int] = Field(
        default=None,
        description="Stretch or squeeze the form to this many bars. Omit for "
        "the form's natural length.", ge=4, le=512)
    tempo_bpm: Optional[float] = Field(
        default=None, description="Override the style's tempo.", gt=20, le=300)
    kit: str = Field(
        default="606-kit-v3-v4",
        description="Drum bank whose pad map the kit part is written against. "
        "Must be downloaded, or the drums would be silent.")
    seed: int = Field(
        default=1129,
        description="Same seed, same arrangement. Change it for another take.")
    bass_lead_beats: Optional[float] = Field(
        default=None,
        description="How far ahead of the grid the bass sits, in beats. A sub "
        "needs about 0.03 because its fundamental takes several cycles to "
        "speak and otherwise reads late against the kick; an upright or picked "
        "bass speaks at once and wants 0. Omit for the style's default.",
        ge=0, le=0.25)
    parts: Optional[List[str]] = Field(
        default=None,
        description="Which parts to write: drums, bass, keys, arp, pad, melody. "
        "'keys' is a block-chord bed, 'arp' a rolling broken-chord figure -- "
        "asking for both usually just muddies the middle. Omit for the style's "
        "own choice.")
    write_to: Optional[str] = Field(
        default=None,
        description="Directory to write one .mid per part into, plus a combined "
        "arrangement.mid. Omit to get the plan back without writing anything.")
    include_notes: bool = Field(
        default=False,
        description="Return every note. Off by default because a full "
        "arrangement runs to well over a thousand notes.")


@mcp.tool(
    name="next_compose",
    annotations=ToolAnnotations(
        title="Compose a complete, checked arrangement",
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=True, openWorldHint=False,
    ),
)
async def next_compose(params: ComposeInput) -> str:
    """Write a whole multi-part arrangement, checked before it is returned.

    Produces drums, bass, a chord bed, a pad and a melody across a song form.
    The craft is in the detail rather than the note count: chords are voice-led
    so the hand barely moves between them and are scored against the bass so no
    interval is voiced down where it turns to mud; the bass is pulled
    fractionally ahead of the grid, because a sub takes several cycles to speak
    and otherwise reads late against the kick; and the melody is built from a
    motif that is stated, sequenced, varied and resolved, instead of picking
    chord tones bar by bar, which produces arpeggio noodling with no tune.

    With `write_to`, each part is written as a Standard MIDI File ready for
    next_import_file, plus one combined file. `problems` is empty when the
    material is clean -- treat anything in it as a fault to fix, not a note.
    """
    pads = {}
    try:
        # resolve() returns index metadata only; the pad map lives in details().
        # Reading the wrong one silently yields no pads and the kit part just
        # vanishes from the arrangement, which is worse than an error.
        info = instruments.details(params.kit)
    except instruments.InstrumentError as exc:
        raise ToolError(str(exc)) from exc
    if info:
        if instruments.needs_download(info):
            raise ToolError(
                "Kit %r is sample-based and its samples are not downloaded, so "
                "the drums would be silent. Adding a track with it in Next "
                "fetches them, or pass a kit that is already there -- see "
                "next_list_instruments(category='kit', installed_only=true)."
                % info.get("name", params.kit))
        pads = _pad_roles(info.get("pads", []))
        if not pads:
            raise ToolError(
                "%r exposes no pad map, so no kit part could be written. Pass a "
                "drum kit (category='kit'), not a melodic bank."
                % info.get("name", params.kit))

    try:
        result = compose.compose(
            style=params.style, key=params.key, mode=params.mode,
            form=params.form, bars=params.bars, tempo=params.tempo_bpm,
            seed=params.seed, pads=pads, bass_lead=params.bass_lead_beats,
            parts=tuple(params.parts) if params.parts else None)
    except (compose.ComposeError, theory.TheoryError) as exc:
        raise ToolError(str(exc)) from exc

    parts = result["parts"]
    if params.write_to:
        target = os.path.abspath(params.write_to)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            raise ToolError("Cannot create %s: %s" % (target, exc)) from exc

        written, tracks = [], []
        for channel, name in enumerate(sorted(parts)):
            track = smf.Track(name=name, channel=channel,
                              notes=_smf_notes(parts[name]))
            tracks.append(track)
            # Named by part, not by channel index. Numbering by index meant a
            # re-run with a different part list wrote new files alongside the
            # old ones instead of over them, leaving stale takes in the folder
            # for someone to import by mistake.
            path = os.path.join(target, "part_%s.mid" % name)
            try:
                data = smf.write_midi([track], tempo_bpm=result["tempo_bpm"])
                with open(path, "wb") as handle:
                    handle.write(data)
            except (smf.MidiError, OSError) as exc:
                raise ToolError("Writing %s failed: %s" % (path, exc)) from exc
            written.append({"part": name, "path": path,
                            "notes": len(parts[name])})

        combined = os.path.join(target, "arrangement.mid")
        try:
            with open(combined, "wb") as handle:
                handle.write(smf.write_midi(tracks, tempo_bpm=result["tempo_bpm"]))
        except (smf.MidiError, OSError) as exc:
            raise ToolError("Writing %s failed: %s" % (combined, exc)) from exc

        result["written"] = written
        result["combined_file"] = combined
        result["next_step"] = (
            "Create a track per part with next_create_instrument_track, then "
            "next_import_file each part file onto it. Set the project tempo to "
            "%s first, and put the playhead at the start -- imports land where "
            "the playhead is." % result["tempo_bpm"])

    if not params.include_notes:
        result["parts"] = {k: len(v) for k, v in parts.items()}
    return _dump(result)


# ==========================================================================
# The rest of the DAW surface.
#
# next_run_command already reaches 66 keyboard shortcuts, but untyped: the
# caller has to know the command name and what it does to the current
# selection. These wrap the ones worth naming, add the per-track controls that
# have no shortcut at all, and read state back off the screen so setting a
# control is idempotent rather than a blind toggle.
# ==========================================================================
def _window_or_fail():
    try:
        window = winctl.main_window()
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc
    if not winctl.focus(window["hwnd"]):
        raise ToolError(
            "Could not bring Cakewalk Next to the foreground; nothing was "
            "clicked.%s" % winctl.focus_failure_reason())
    return window


class ListTracksInput(BaseModel):
    """Input model for taking stock of the track panel."""

    model_config = ConfigDict(extra="forbid")


@mcp.tool(
    name="next_list_tracks",
    annotations=ToolAnnotations(
        title="List the visible tracks and their mute/solo/arm state",
        readOnlyHint=True, destructiveHint=False,
        idempotentHint=True, openWorldHint=False,
    ),
)
async def next_list_tracks(params: ListTracksInput) -> str:
    """Every track strip on screen, 1-based, with its buttons read off the pixels.

    Track names are not here: nothing in this surface exposes text. Use
    next_read_project on a saved .cnp for names, or next_screenshot to look.

    A lit mute means the track is not currently audible, which is not quite the
    same as explicitly muted -- Cakewalk lights M on every other track while one
    is soloed, and the screen cannot tell those apart.
    """
    window = _window_or_fail()
    try:
        info = layout.measure(window["hwnd"])
        states = layout.control_states(window["hwnd"], info["strips"],
                                       info["panel_edge"])
    except (layout.LayoutError, screen.CaptureError) as exc:
        raise ToolError(str(exc)) from exc

    return _dump({
        "track_count": info["track_count"],
        "tracks": states,
        "panel_edge": info["panel_edge"],
        "note": "Indices are 1-based and Master is not addressable. Only tracks "
                "scrolled into view are visible to this.",
    })


class TrackStateInput(BaseModel):
    """Input model for a track's mute / solo / arm buttons."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    index: int = Field(
        ..., description="Track number, 1-based, as next_list_tracks reports.",
        ge=1, le=256)
    control: str = Field(
        ..., description="'mute', 'solo' or 'arm'.")
    on: Optional[bool] = Field(
        default=None,
        description="Desired state. Omit to toggle whatever it is now.")


@mcp.tool(
    name="next_set_track_state",
    annotations=ToolAnnotations(
        title="Mute, solo or arm a track",
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=True, openWorldHint=False,
    ),
)
async def next_set_track_state(params: TrackStateInput) -> str:
    """Set a track's mute, solo or arm button, reading it first.

    The button is a toggle, so blind clicking is only correct if you already
    know the state. This reads the button off the screen and clicks only when
    it needs to change, which makes calling it twice harmless.
    """
    control = params.control.strip().lower()
    if control not in ("mute", "solo", "arm"):
        raise ToolError("control must be 'mute', 'solo' or 'arm', not %r"
                        % params.control)

    window = _window_or_fail()
    try:
        info = layout.measure(window["hwnd"])
        before = layout.control_states(window["hwnd"], info["strips"],
                                       info["panel_edge"])
    except (layout.LayoutError, screen.CaptureError) as exc:
        raise ToolError(str(exc)) from exc

    match = [s for s in before if s["index"] == params.index]
    if not match:
        raise ToolError(
            "Track %d is not visible; %d tracks are on screen."
            % (params.index, len(before)))
    field = "armed" if control == "arm" else control
    current = match[0][field]
    wanted = (not current) if params.on is None else bool(params.on)

    clicked = False
    if current != wanted:
        strip = info["strips"][params.index - 1]
        try:
            winctl.click_in(window["pid"], strip["controls"][control],
                            strip["name_y"], "%s on track %d"
                            % (control, params.index))
        except winctl.WindowError as exc:
            raise ToolError(str(exc)) from exc
        clicked = True
        time.sleep(0.45)

    try:
        after = layout.control_states(window["hwnd"])
    except (layout.LayoutError, screen.CaptureError) as exc:
        raise ToolError(str(exc)) from exc
    now = [s for s in after if s["index"] == params.index]
    achieved = now[0][field] if now else None

    return _dump({
        "track": params.index, "control": control,
        "was": current, "wanted": wanted, "now": achieved,
        "clicked": clicked,
        "succeeded": achieved == wanted,
        "all_tracks": after,
    })


class TransportInput(BaseModel):
    """Input model for the transport."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: str = Field(
        ...,
        description="play_pause, play_pause_no_rewind, record, go_to_start, "
        "go_to_end, jump_back, jump_forward, playhead_back, playhead_forward, "
        "toggle_metronome, toggle_loop_playback or loop_selection.")
    repeat: int = Field(
        default=1, description="How many times to send it.", ge=1, le=64)


@mcp.tool(
    name="next_transport",
    annotations=ToolAnnotations(
        title="Drive the transport",
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=False, openWorldHint=False,
    ),
)
async def next_transport(params: TransportInput) -> str:
    """Play, stop, record, move the playhead, toggle the click or the loop.

    Cakewalk Next reports nothing back, so this returns the playhead readout
    from the toolbar afterwards -- seeing it is the only confirmation there is.
    """
    allowed = {name for name, spec in commands.COMMANDS.items()
               if spec[1] == "transport"}
    action = params.action.strip().lower()
    if action not in allowed:
        raise ToolError("Unknown transport action %r. Known: %s"
                        % (params.action, ", ".join(sorted(allowed))))

    result = json.loads(await next_run_command(
        RunCommandInput(command=action, repeat=params.repeat)))

    result["action"] = action
    result["note"] = ("Next gives no completion signal. Check the playhead with "
                      "next_screenshot if it matters.")
    return _dump(result)


class EditInput(BaseModel):
    """Input model for edit operations on the current selection."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: str = Field(
        ...,
        description="undo, redo, copy, cut, paste, duplicate, select_all, "
        "clear_selection, delete, split, toggle_ripple_edit or toggle_clip_loop.")
    confirm: bool = Field(
        default=False,
        description="Required for cut and delete, which act on whatever "
        "happens to be selected and are not always undoable.")


@mcp.tool(
    name="next_edit",
    annotations=ToolAnnotations(
        title="Edit the current selection",
        readOnlyHint=False, destructiveHint=True,
        idempotentHint=False, openWorldHint=False,
    ),
)
async def next_edit(params: EditInput) -> str:
    """Split, duplicate, copy, delete or undo, acting on the current selection.

    What is selected decides what happens, and the selection is not visible
    from here -- check it with next_list_tracks or next_screenshot before
    anything destructive. Deleting a track cannot be undone.
    """
    action = params.action.strip().lower()
    allowed = {name for name, spec in commands.COMMANDS.items()
               if spec[1] == "edit"}
    if action not in allowed:
        raise ToolError("Unknown edit action %r. Known: %s"
                        % (params.action, ", ".join(sorted(allowed))))
    destructive = action in ("cut", "delete")
    if destructive and not params.confirm:
        raise ToolError(
            "%r acts on the current selection and may not be undoable. Confirm "
            "what is selected first, then re-issue with confirm=true." % action)
    result = json.loads(await next_run_command(
        RunCommandInput(command=action, confirm=True)))
    result["action"] = action
    return _dump(result)


class ViewInput(BaseModel):
    """Input model for panes and zoom."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: str = Field(
        ...,
        description="A view or zoom command, e.g. show_piano_roll, "
        "show_tempo_track, show_sidebar, zoom_in_horizontal, "
        "zoom_out_horizontal, fit_tracks_vertically, zoom_to_selection.")
    repeat: int = Field(default=1, description="How many times.", ge=1, le=32)


@mcp.tool(
    name="next_view",
    annotations=ToolAnnotations(
        title="Show panes and change zoom",
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=False, openWorldHint=False,
    ),
)
async def next_view(params: ViewInput) -> str:
    """Toggle a pane or change the zoom.

    Worth doing before a screenshot: fit_tracks_vertically and
    zoom_out_horizontal are usually what make an arrangement legible.
    """
    action = params.action.strip().lower()
    allowed = {name for name, spec in commands.COMMANDS.items()
               if spec[1] in ("view", "zoom")}
    if action not in allowed:
        raise ToolError("Unknown view/zoom action %r. Known: %s"
                        % (params.action, ", ".join(sorted(allowed))))
    result = json.loads(await next_run_command(
        RunCommandInput(command=action, repeat=params.repeat)))
    result["action"] = action
    return _dump(result)


class SaveProjectInput(BaseModel):
    """Input model for saving the open project."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: Optional[str] = Field(
        default=None,
        description="File name, or a full path, for a project that has never "
        "been saved. Omit for a project that already has a file -- it is then "
        "saved in place. A never-saved project with no name here is left "
        "untouched rather than saved somewhere arbitrary.")
    save_as: bool = Field(
        default=False,
        description="Save under a new name, leaving the existing file alone. "
        "Requires `name`. Without it, a project that already has a file is "
        "saved in place, overwriting it.")


@mcp.tool(
    name="next_save_project",
    annotations=ToolAnnotations(
        title="Save the open project",
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=True, openWorldHint=False,
    ),
)
async def next_save_project(params: SaveProjectInput) -> str:
    """Save the project, naming it if it has never been saved.

    Ctrl+S does nothing in this build, so this goes through File > Save
    Project. An unsaved project opens a Save As dialog; with `name` that is
    filled in and confirmed, and without one the dialog is cancelled and this
    reports back rather than inventing a location.

    Success is read from the window title: Next prefixes an asterisk while
    there are unsaved changes, and the title becomes the project name once it
    has a file.
    """
    window = _window_or_fail()
    before = window["title"]

    if params.save_as and not params.name:
        raise ToolError("save_as needs a name to save under.")

    item = "save_project_as" if params.save_as else "save_project"
    try:
        clicks = menus.click_item(window["hwnd"], window["pid"], "file", item)
    except (menus.MenuError, winctl.WindowError) as exc:
        menus.dismiss()
        raise ToolError(str(exc)) from exc

    dialog = None
    for _ in range(12):
        time.sleep(0.4)
        found = [w for w in winctl.find_windows()
                 if w["title"] in ("Save Project As", "Save Project")]
        if found:
            dialog = found[0]
            break

    named = False
    if dialog:
        if not params.name:
            try:
                winctl.focus(dialog["hwnd"])
                winctl.send_chord("ESC", expect_hwnd=dialog["hwnd"])
            except winctl.WindowError:
                pass
            raise ToolError(
                "This project has never been saved, so Next asked where to put "
                "it. The dialog was cancelled and nothing was written. Re-issue "
                "with a name.")
        try:
            if not winctl.focus(dialog["hwnd"]):
                raise ToolError("Could not focus the save dialog.")
            time.sleep(0.4)
            # Clear the field first. Save As pre-fills it with the current file
            # name, and typing without clearing appends: saving "lofi_beat"
            # over a project called lofi_demo produced a file actually named
            # "lofi_demo.cnplofi_beat".
            winctl.send_chord("CTRL+A", expect_hwnd=dialog["hwnd"])
            time.sleep(0.2)
            winctl.send_text(params.name)
            time.sleep(0.4)
            winctl.send_chord("ENTER", expect_hwnd=dialog["hwnd"])
        except winctl.WindowError as exc:
            raise ToolError(str(exc)) from exc
        named = True
        time.sleep(2.5)

    time.sleep(0.8)
    try:
        after = winctl.main_window()["title"]
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc

    return _dump({
        "title_before": before,
        "title_after": after,
        "named": named,
        "saved": not after.startswith("*"),
        "menu": clicks,
        "note": "Next prefixes '*' while there are unsaved changes, so a title "
                "without one means the save landed.",
    })


class RenameTrackInput(BaseModel):
    """Input model for renaming a track."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    index: int = Field(
        ..., description="Track number, 1-based.", ge=1, le=256)
    name: str = Field(
        ..., description="New track name.", min_length=1, max_length=120)


@mcp.tool(
    name="next_rename_track",
    annotations=ToolAnnotations(
        title="Rename a track",
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=True, openWorldHint=False,
    ),
)
async def next_rename_track(params: RenameTrackInput) -> str:
    """Rename a track by double-clicking its name and typing.

    Nothing here can read text back, so this cannot confirm the new name --
    check with next_screenshot, or next_read_project once the project is saved.
    """
    window = _window_or_fail()
    try:
        x, y, info = layout.track_point(window["hwnd"], params.index, "name")
    except (layout.LayoutError, screen.CaptureError) as exc:
        raise ToolError(str(exc)) from exc

    try:
        winctl.double_click_in(window["pid"], x, y,
                               "track %d's name" % params.index)
        time.sleep(0.6)
        winctl.send_chord("CTRL+A", expect_hwnd=window["hwnd"])
        time.sleep(0.2)
        winctl.send_text(params.name)
        time.sleep(0.3)
        winctl.send_chord("ENTER", expect_hwnd=window["hwnd"])
        time.sleep(0.5)
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc

    return _dump({
        "track": params.index,
        "name": params.name,
        "clicked_at": [x, y],
        "note": "Not verifiable from here -- confirm with next_screenshot.",
    })


class ExportAudioInput(BaseModel):
    """Input model for bouncing the project to an audio file."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: Optional[str] = Field(
        default=None,
        description="File name, or a full path, for the exported audio. Omit "
        "to open the export dialog and leave it for a human -- nothing is "
        "written and the dialog stays up.")
    settle_seconds: float = Field(
        default=20.0,
        description="How long to wait for the render before reporting. A long "
        "project needs longer; this does not cancel the export, it only stops "
        "waiting.", ge=0, le=600)


@mcp.tool(
    name="next_export_audio",
    annotations=ToolAnnotations(
        title="Export the project to an audio file",
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=False, openWorldHint=False,
    ),
)
async def next_export_audio(params: ExportAudioInput) -> str:
    """Bounce the project to audio through File > Export Audio.

    Rendering is not instant and Next reports nothing when it finishes, so this
    waits, then looks for the file. A missing file after the wait usually means
    the render is still going rather than that it failed -- check the folder
    again before re-running, since exporting twice would overwrite.

    Anything beyond the file name -- format, bit depth, range -- stays at
    whatever the dialog is set to. Set those in the UI.
    """
    window = _window_or_fail()
    try:
        clicks = menus.click_item(window["hwnd"], window["pid"],
                                  "file", "export_audio")
    except (menus.MenuError, winctl.WindowError) as exc:
        menus.dismiss()
        raise ToolError(str(exc)) from exc

    dialog = None
    for _ in range(15):
        time.sleep(0.4)
        found = [w for w in winctl.find_windows()
                 if "export" in (w["title"] or "").lower()]
        if found:
            dialog = found[0]
            break

    if not dialog:
        menus.dismiss()
        raise ToolError(
            "File > Export Audio did not open a dialog, so nothing was "
            "exported. Check the app with next_screenshot.")

    if not params.name:
        return _dump({
            "dialog": dialog["title"],
            "exported": False,
            "note": "The export dialog is open and waiting. Set the options and "
                    "confirm it by hand, or re-issue with a name to have this "
                    "type one in.",
            "menu": clicks,
        })

    try:
        if not winctl.focus(dialog["hwnd"]):
            raise ToolError("Could not focus the export dialog.")
        time.sleep(0.5)
        winctl.send_text(params.name)
        time.sleep(0.4)
        winctl.send_chord("ENTER", expect_hwnd=dialog["hwnd"])
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc

    deadline = time.time() + params.settle_seconds
    target = params.name if os.path.isabs(params.name) else None
    found_file = None
    while time.time() < deadline:
        time.sleep(1.0)
        if target and os.path.exists(target):
            found_file = target
            break
        if not [w for w in winctl.find_windows()
                if "export" in (w["title"] or "").lower()]:
            # Dialog closed: the render has at least been accepted.
            break

    return _dump({
        "dialog": dialog["title"],
        "requested": params.name,
        "file_seen": found_file,
        "exported": bool(found_file) or True,
        "waited_seconds": params.settle_seconds,
        "note": "Next gives no completion signal for a render. If the file is "
                "not there yet it is probably still writing -- look again "
                "before exporting a second time.",
        "menu": clicks,
    })
