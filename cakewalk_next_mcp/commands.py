"""The Cakewalk Next command vocabulary, keyed to its shortcuts.

Source of truth: the menus of the running application (Cakewalk Next 1.1.0.150),
read directly off screen. Both the v1.1 User Manual (pp. 265-269) and the Help
Center article "Cakewalk Next: Keyboard Shortcuts" (updated 2026-05-22) disagree
with the shipping build in several places, so the docs are NOT authoritative:

* Show Tempo Track is ALT+T, not T.
* Show Arranger Track is ALT+A, not A.
* CTRL+G is "Group" and CTRL+U is "Ungroup"; the docs call these
  "Move to Track Folder" and "Remove From Folder".
* F3 is "Show Track Plugins" and F4 is "Show Piano Roll"; the docs call these
  the track and clip inspectors.
* The docs list "Bounce Selected Clip(s)" on CTRL+B and "Dock/Float Loop
  Browser" on CTRL+ALT+B. Neither exists in the build: CTRL+B is only
  "Publish to BandLab" and CTRL+ALT+B is only "Bounce Tracks in Place".
* Undocumented commands that do exist: CTRL+SPACE, CTRL+SHIFT+A, CTRL+SHIFT+R,
  CTRL+ALT+S, CTRL+/ and \\.

Deliberate omissions:

* Publish to BandLab (CTRL+B) uploads the project to the internet. An agent
  should not be able to fire that from a single tool call.
* Exit (CTRL+Q) risks losing unsaved work with no way to answer the save prompt.
* Arm (ALT+A) per the docs collides with Show Arranger Track (ALT+A), which is
  what the View menu actually binds. Arm a track from the UI instead.

Shortcuts are remappable via Help > Keyboard Shortcuts. If you have remapped
any, update this table.

``destructive`` marks commands that discard work, overwrite data or start a long
render. Those require an explicit confirmation from the caller.
"""

COMMANDS = {
    # --- Transport ---
    "play_pause": ("SPACE", "transport", False, "Start or pause playback"),
    "play_pause_no_rewind": ("CTRL+SPACE", "transport", False, "Play/pause without returning to the start position"),
    "record": ("R", "transport", False, "Toggle recording on armed tracks"),
    "go_to_start": ("SHIFT+W", "transport", False, "Move the playhead to the project start"),
    "go_to_end": ("SHIFT+E", "transport", False, "Move the playhead to the project end"),
    "jump_back": ("W", "transport", False, "Jump the playhead backward"),
    "jump_forward": ("E", "transport", False, "Jump the playhead forward"),
    "playhead_back": ("LEFT", "transport", False, "Nudge the playhead back one grid step"),
    "playhead_forward": ("RIGHT", "transport", False, "Nudge the playhead forward one grid step"),
    "toggle_metronome": ("K", "transport", False, "Toggle the metronome"),
    "toggle_loop_playback": ("L", "transport", False, "Toggle loop playback"),
    "loop_selection": ("SHIFT+L", "transport", False, "Set the loop range to the selection"),

    # --- File / project ---
    "save_project": ("CTRL+S", "file", False, "Save the current project"),
    "save_project_as": ("CTRL+SHIFT+S", "file", False, "Open the Save As dialog"),
    "save_as_template": ("CTRL+ALT+S", "file", False, "Save the project as a template"),
    "new_project": ("CTRL+N", "file", True, "Create a new project (discards unsaved changes)"),
    "open_project": ("CTRL+O", "file", True, "Open the project browser (discards unsaved changes)"),
    "export_audio": ("CTRL+E", "file", False, "Open the Export Audio dialog"),
    "preferences": ("CTRL+COMMA", "file", False, "Open Preferences"),

    # --- Track creation ---
    "create_audio_track": ("CTRL+T", "tracks", False, "Create a Mic/Line audio track"),
    "create_instrument_track": ("CTRL+SHIFT+T", "tracks", False, "Create an instrument track"),
    "create_sampler_track": ("CTRL+SHIFT+X", "tracks", False, "Create a sampler track"),
    "create_pad_controller_track": ("CTRL+SHIFT+P", "tracks", False, "Create a pad controller track"),
    "create_instrument_rack_track": ("CTRL+SHIFT+R", "tracks", False, "Create an instrument rack track"),
    "create_track_folder": ("CTRL+SHIFT+G", "tracks", False, "Create a track folder"),
    "create_bus": ("CTRL+ALT+T", "tracks", False, "Create a bus"),
    "group": ("CTRL+G", "tracks", False, "Group the selected tracks/buses"),
    "ungroup": ("CTRL+U", "tracks", False, "Ungroup the selection"),

    # --- Track properties (act on the selected track) ---
    "toggle_solo": ("ALT+S", "tracks", False, "Solo or unsolo the selected track"),
    "toggle_mute": ("ALT+M", "tracks", False, "Mute or unmute the selected track"),

    # --- Editing ---
    "undo": ("CTRL+Z", "edit", False, "Undo the last action"),
    "redo": ("CTRL+SHIFT+Z", "edit", False, "Redo the last undone action"),
    "copy": ("CTRL+C", "edit", False, "Copy the selection"),
    "cut": ("CTRL+X", "edit", True, "Cut the selection"),
    "paste": ("CTRL+V", "edit", False, "Paste at the playhead"),
    "duplicate": ("CTRL+D", "edit", False, "Duplicate the selection"),
    "select_all": ("CTRL+A", "edit", False, "Select all"),
    "clear_selection": ("CTRL+SHIFT+A", "edit", False, "Clear the selection"),
    "delete": ("DELETE", "edit", True, "Delete the selection"),
    "split": ("S", "edit", False, "Split all clips at the playhead"),
    "toggle_ripple_edit": ("CTRL+R", "edit", False, "Enable or disable ripple editing"),
    "toggle_clip_loop": ("CTRL+L", "edit", False, "Toggle looping on the selected clip"),

    # --- Bounce / render ---
    "bounce_to_new_tracks": ("CTRL+SHIFT+B", "bounce", True, "Bounce the selection to new tracks"),
    "bounce_tracks_in_place": ("CTRL+ALT+B", "bounce", True, "Bounce the selected tracks in place (replaces their content)"),

    # --- Piano Roll (only meaningful while the Piano Roll has focus) ---
    "draw_tool": ("D", "piano_roll", False, "Select the Draw tool"),
    "toggle_midi_velocity_display": ("ALT+V", "piano_roll", False, "Show or hide MIDI velocities"),
    "split_selected_notes": ("SHIFT+S", "piano_roll", True, "Split the selected notes"),
    "merge_selected_notes": ("SHIFT+ALT+M", "piano_roll", True, "Merge the selected notes"),

    # --- View ---
    "cakewalk_assist": ("CTRL+SLASH", "view", False, "Open Cakewalk Assist"),
    "show_track_plugins": ("F3", "view", False, "Show or hide the track plugins pane"),
    "show_piano_roll": ("F4", "view", False, "Show or hide the Piano Roll"),
    "show_lyrics_prompter": ("ALT+F7", "view", False, "Toggle the Lyrics Prompter"),
    "show_sidebar": ("BACKSLASH", "view", False, "Show or hide the sidebar"),
    "show_bandlab_sounds": ("F5", "view", False, "Show or hide the BandLab Sounds browser"),
    "show_arranger_inspector": ("F6", "view", False, "Show or hide the arranger inspector"),
    "show_lyrics_inspector": ("F7", "view", False, "Show or hide the lyrics inspector"),
    "show_arranger_track": ("ALT+A", "view", False, "Show or hide the arranger track"),
    "show_lyrics_track": ("ALT+L", "view", False, "Show or hide the lyrics track"),
    "show_tempo_track": ("ALT+T", "view", False, "Show or hide the tempo track"),
    "show_project_info": ("CTRL+SHIFT+I", "view", False, "Show the Project Information editor"),
    "show_virtual_midi_controller": ("CTRL+M", "view", False, "Show or hide the virtual MIDI controller"),
    "toggle_qwerty_midi_input": ("CTRL+ALT+M", "view", False, "Toggle QWERTY-to-MIDI input"),
    "toggle_aim_assist": ("X", "view", False, "Show or hide the aim assist line"),

    # --- Zoom ---
    "zoom_in_horizontal": ("CTRL+RIGHT", "zoom", False, "Zoom in horizontally"),
    "zoom_out_horizontal": ("CTRL+LEFT", "zoom", False, "Zoom out horizontally"),
    "zoom_in_vertical": ("CTRL+UP", "zoom", False, "Zoom in vertically"),
    "zoom_out_vertical": ("CTRL+DOWN", "zoom", False, "Zoom out vertically"),
    "zoom_to_selection": ("F", "zoom", False, "Zoom to the selection"),
    "fit_tracks_vertically": ("SHIFT+F", "zoom", False, "Fit all tracks vertically"),
    "expand_collapse_tracks": ("ALT+H", "zoom", False, "Expand or collapse track heights"),
}

COMMAND_NAMES = sorted(COMMANDS)
DESTRUCTIVE = {name for name, spec in COMMANDS.items() if spec[2]}
CATEGORIES = sorted({spec[1] for spec in COMMANDS.values()})


def describe(name):
    """Return a dict describing one command, or None if the name is unknown."""
    spec = COMMANDS.get(name)
    if not spec:
        return None
    chord, category, destructive, summary = spec
    return {
        "command": name,
        "shortcut": chord,
        "category": category,
        "destructive": destructive,
        "description": summary,
    }


def suggest(name, limit=6):
    """Return command names sharing a word or prefix with an unknown name."""
    needle = name.lower().replace("-", "_")
    scored = []
    for candidate in COMMAND_NAMES:
        if needle in candidate or candidate in needle:
            scored.append((0, candidate))
            continue
        if set(needle.split("_")) & set(candidate.split("_")):
            scored.append((1, candidate))
    scored.sort()
    return [c for _rank, c in scored[:limit]]
