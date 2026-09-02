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

from . import cnp, commands, instruments, menus, midiout, screen, smf, state, winctl

mcp = _Server("cakewalk_next_mcp")

MAX_REPEAT = 20

# Toolbar tempo readout, in logical px from the window's top-left.
TEMPO_FIELD = (536, 49)
_INTER_KEY_DELAY = 0.06


def _dump(payload):
    return json.dumps(payload, indent=2, default=str)


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
    search box so the wanted instrument is the top result, and clicks Add.

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
    if found and not found.get("installed"):
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
        clicks = menus.choose_instrument(dialog["hwnd"], params.instrument, window["pid"])
    except winctl.WindowError as exc:
        menus.dismiss()
        raise ToolError(str(exc)) from exc

    deadline = time.time() + 15.0
    while time.time() < deadline:
        if menus.instrument_browser(window["pid"], timeout=0.3) is None:
            return _dump({
                "created": params.instrument,
                "resolved": found["name"] if found else None,
                "clicks": {k: list(v) for k, v in clicks.items()},
                "next_step": "Select this track and call next_import_file to put a "
                             "part on it; the import keeps this instrument.",
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
        description="Track number as shown in Next, 1-based. The Master bus is 0.",
        ge=0, le=64,
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
    """Select a track by its number, finding the strip by looking at the screen.

    Strip height is not fixed - showing automation lanes roughly doubles it - so
    this measures the strips from a screen capture rather than assuming a
    constant. Assuming one silently selects the wrong track and drops imported
    parts onto the wrong instrument.

    Prefer create-then-import where you can: a new track is already selected and
    needs no clicking at all. Verify with next_screenshot when it matters.
    """
    try:
        window = winctl.main_window()
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc
    if not winctl.focus(window["hwnd"]):
        raise ToolError(
            "Could not focus Cakewalk Next.%s" % winctl.focus_failure_reason())

    try:
        rows = screen.detect_track_rows(window["hwnd"])
    except screen.CaptureError as exc:
        raise ToolError(str(exc)) from exc

    if params.index >= len(rows):
        raise ToolError(
            "Track %d was not found: only %d strips are visible (0 is Master, so "
            "the highest track is %d). Scroll it into view, or check "
            "next_screenshot." % (params.index, len(rows), max(0, len(rows) - 1))
        )

    y = rows[params.index]
    try:
        winctl.click_in(window["pid"], menus.TRACK_NAME_X, y,
                        "track %d's strip" % params.index)
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc
    time.sleep(0.4)
    return _dump({
        "selected_index": params.index,
        "clicked_at": [menus.TRACK_NAME_X, y],
        "strips_detected": len(rows),
        "note": "Measured from the screen, so automation lanes do not break it. "
                "Confirm with next_screenshot if the next step is destructive.",
    })


class DeleteTrackInput(BaseModel):
    """Input model for deleting a track."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(
        ..., description="Track number to delete, 1-based.", ge=1, le=64)
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

    Returns a picture of the toolbar so the new value can be read back - there
    is no way to query it, so seeing it is the only confirmation available.
    """
    try:
        window = winctl.main_window()
    except winctl.WindowError as exc:
        raise ToolError(str(exc)) from exc
    if not winctl.focus(window["hwnd"]):
        raise ToolError(
            "Could not focus Cakewalk Next.%s" % winctl.focus_failure_reason())

    try:
        left, top, _w, _h = screen.visible_rect(window["hwnd"])
    except screen.CaptureError as exc:
        raise ToolError(str(exc)) from exc
    scale = winctl.dpi_scale(window["hwnd"])
    x, y = left + int(TEMPO_FIELD[0] * scale), top + int(TEMPO_FIELD[1] * scale)

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
        png, _size = screen.capture_region(
            left + int(400 * scale), top + int(30 * scale),
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
