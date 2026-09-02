# cakewalk-next-mcp

An MCP server for **Cakewalk Next** (BandLab's DAW), so an agent can inspect your
projects, generate MIDI parts for them, and drive the running app.

## Why it works the way it does

Cakewalk Next has no scripting API, no OSC, and no Mackie/MCU control-surface
support — the User Manual documents none of them. That rules out the usual DAW
integration routes, so this server uses the three surfaces Next actually exposes:

| Surface | Used for | Requires Next running? |
|---|---|---|
| `.cnp` project files | reading track/plugin/media structure | no |
| Installed soundbank JSON | what instruments exist, and each kit's pad map | no |
| Standard MIDI Files | getting parts in and out | no |
| Documented keyboard shortcuts (Win32 `SendInput`) | transport, saving, track creation, export | yes |

## Requirements

| Requirement | Why | Needed for |
|---|---|---|
| **Windows** | Everything UI-related is Win32 (`SendInput`, `WindowFromPoint`, GDI capture) | all live control |
| **Python 3.10+** | `match`-free but uses modern typing | everything |
| **`mcp>=1.2`** | the only Python dependency; works on 1.x and 2.x | everything |
| **Cakewalk Next** | the application being driven | all live control |
| **loopMIDI** *(optional)* | Windows has no virtual MIDI cable | `next_play_notes` only |

There are **no other Python dependencies**. MIDI reading/writing, PNG encoding,
screen capture, window control and the `.cnp` parser are all written against the
standard library and `ctypes`, deliberately — a DAW automation tool that drags in
an imaging stack and a MIDI library is harder to trust and harder to install.

The offline tools (project reading, the instrument library, MIDI file read/write)
work on any OS with Python; only the tools that drive the running application
require Windows, and they say so rather than failing obscurely.

## Install

```bash
git clone https://github.com/<you>/cakewalk-next-mcp
cd cakewalk-next-mcp
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
```

Register it with your MCP client. Most clients read a JSON config of this
shape — point `command` at the virtual environment's interpreter:

```json
{
  "mcpServers": {
    "cakewalk-next": {
      "command": "C:/path/to/cakewalk-next-mcp/.venv/Scripts/python.exe",
      "args": ["-m", "cakewalk_next_mcp"]
    }
  }
}
```

The server speaks stdio, so any MCP-compatible client can drive it. Installing
the package (`pip install -e .`) matters: it puts `cakewalk_next_mcp` on the
path so `-m` works regardless of the working directory the client launches it
from.

### You need a virtual MIDI device for live play

`next_play_notes` streams notes into Next in real time. That needs a virtual MIDI
cable: one port this server writes to and Next listens on. **Windows does not
ship one**, and a stock machine reports zero MIDI inputs — the only output,
Microsoft GS Wavetable Synth, plays to the speakers and cannot be heard by
another application.

Install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
(freeware) or:

```bash
winget install TobiasErichsen.loopMIDI
```

It installs a kernel driver, so it **must run elevated**. A silent or
non-interactive install fails with
`Bundle condition evaluated to false: Privileged <> 0`.

Then:

1. Open loopMIDI and click **+** to add a port. **Leave it running** — the port
   exists only while the app does. It registers itself to start with Windows.
2. In Next: **Edit → Preferences → MIDI**, set *Default MIDI Input for new
   tracks* to that port. Note the wording — it applies to **new** tracks, so
   create your instrument track after setting it.
3. Arm an instrument track.

`next_list_midi_ports` reports whether this is set up and says what to fix if
not. Everything except `next_play_notes` works without it.

## Tools

**Project inspection** (offline, read-only)
- `next_list_projects` — find `.cnp` files under a folder, newest first, paginated.
- `next_read_project` — track names, colours, instrument presets, buses, referenced audio, and the Next version that saved it.

**Live MIDI** (needs a virtual MIDI cable — see below)
- `next_list_midi_ports` — what MIDI ports exist and whether live play is possible.
- `next_play_notes` — stream notes in real time so Next sounds them on an armed track.

**Instruments** (offline, read-only)
- `next_list_instruments` — search the soundbanks Next knows about (444 on the machine this was built against: 359 melodic, 85 drum kits) by name, category or family. Pass `installed_only` for the ones actually downloaded — **often only a handful are**, the rest show a download arrow in Next and will not sound until fetched.
- `next_instrument_info` — one instrument in detail. For a drum kit this is the **pad map**; for a melodic bank, the sampled playable range.

**Project settings**
- `next_set_project_tempo` — tell the server the open project's tempo; the MIDI tools default to it.

**MIDI**
- `next_write_midi` — generate a `.mid` from notes in beats. Pitches accept numbers (`60`), names (`C4`, `F#3`, `Bb5`), or a list for chords. Channel 9 is GM drums.
- `next_read_midi` — parse a `.mid` back into tempo, time signature and per-track notes.

**Live control** (Windows only, Next must be running)
- `next_app_status` — is Next running, which project is open, are there unsaved changes.
- `next_list_commands` — the 70 available commands and their shortcuts.
- `next_run_command` — focus the Next window and send one vetted command.
- `next_screenshot` — **look at the window**. Returns a PNG, so the agent can see state it cannot query.
- `next_create_track` — add a track of any type: audio, instrument, sampler, pad controller, instrument rack, folder, bus.
- `next_create_instrument_track` — add a track with a named instrument loaded.
- `next_select_track` — select track *n*, locating the strip by measuring the screen.
- `next_delete_track` — delete a track, its clips and its instrument.
- `next_import_file` — insert an audio/MIDI file via *Insert > Insert Audio/MIDI File...*, which has no shortcut. Lands on the **selected** track and keeps that track's instrument.

## What it cannot do

The `.cnp` format is undocumented and only partly decoded. Container chunks carry
`tag + version + size`, but leaf chunks omit the size field and are delimited by
their parent, so a generic walker cannot recurse without the schema. This server
therefore reads the two unambiguous layers — the chunk histogram and the
length-prefixed string table.

**That means `next_read_project` gives you names, plugins, colours and media, but
not tempo, time signature, clip positions or notes.** To read musical content,
export a MIDI clip from Next (right-click the clip header → *Export to File*) and
pass it to `next_read_midi`. A missing field is a parser limit, not evidence the
project lacks that feature.

Getting parts *in* is covered: `next_import_file` drives *Insert > Insert
Audio/MIDI File...*, so `next_write_midi` output reaches a project without you
dragging anything.

## Three things that will bite you

**1. Saving requires an activated Next.** If Cakewalk Next is not signed in to a
BandLab account, `CTRL+S` is accepted but refused by the app, which shows a
*"Save Project Unavailable — The 'Save Project' operation requires activation"*
notification. `next_run_command` will report success, because the keystroke was
delivered; the refusal happens inside Next. Check `has_unsaved_changes` from
`next_app_status` afterwards to confirm a save actually took.

**2. Modifier chords need real scan codes.** Cakewalk Next resolves shortcuts
from hardware scan codes. `SendInput` records built with `wScan=0` (which is
enough for ordinary Win32 apps, and works fine in Notepad) get their modifier
silently dropped — and Next then acts on the **bare key**. Before this was
fixed, `CTRL+S` fired `S` = *Split*, and `CTRL+T` fired `T` = *Show/Hide Tempo
Track*. `winctl.py` therefore sends `MapVirtualKeyW`-derived scan codes with
left-hand modifier VKs (`VK_LCONTROL`/`VK_LSHIFT`/`VK_LMENU`), and presses the
modifier slightly ahead of the base key. Do not "simplify" that back.

A window restored from the taskbar also needs a settle delay before it accepts
input, or the first keystroke is dropped. So does a window the *user* just
clicked, which is why `focus()` settles even when the window is already
foreground.

**3. Focus is taken by clicking, not by asking.** Windows refuses
`SetForegroundWindow` from a background process until `ForegroundLockTimeout`
elapses since the last user input, and some tuning utilities set that to
`0x7FFFFFFF` — effectively forever. Attach-thread-input,
`SwitchToThisWindow` and minimise/restore all fail against that.

What works is a **synthetic mouse click**: it is ordinary input, so Windows
activates whatever it lands on. `focus()` falls back to clicking the window's
title strip — horizontally centred, above the menu bar, where there are no
controls — and reclaims focus in about 3 seconds even against an infinite lock.
Verified by letting Notepad steal the foreground and taking it back unaided.

Because focus can still move *between* keystrokes, `next_run_command`
re-verifies before every single press and aborts rather than typing the rest of
a sequence into whatever app took over.

## Working with the instruments

Next installs every BandLab soundbank under
`C:\ProgramData\Cakewalk\Next\Soundbanks\<slug>\<slug>.json`, and the `slug` in
those files is the same one `.cnp` stores — which is how `next_read_project` can
turn a bare `808-kit` into "808, a drum kit".

**Drum kits are not General MIDI mapped.** Each kit ships its own pad list, so
ask before writing:

```
next_instrument_info("808-kit")
  -> 36 Kick · 37 Cross Stick · 38 Snare · 39 Clap · 40 Clave
     41 Low Tom · 42 HH Closed · 45 Mid Tom · 46 HH Open
     48 High Tom · 49 Crash · 56 Cowbell
```

Then write those numbers as pitches with `next_write_midi`. The track's own
instrument decides the sound, so **channel 9 is not required** — that convention
is for GM synths. Melodic banks report a `playable_range` (Dark Grand is 22–108);
notes outside it may not sound.

Each of these `.json` files ends with a stray `\x00` after the closing brace, so
a plain `json.load` fails on 440 of 444 — `instruments.py` trims it.

**Playing them live** needs a virtual MIDI cable, because Windows ships none and
a stock machine has no MIDI inputs at all. Install
[loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) (freeware) or:

```bash
winget install TobiasErichsen.loopMIDI
```

It installs a kernel driver, so it **must** run elevated — a silent/
non-interactive install fails with
`Bundle condition evaluated to false: Privileged <> 0`.

Then open loopMIDI and click **+** to add a port (it must keep running — the
port exists only while it does), and in Next set *Edit > Preferences > MIDI >
Default MIDI Input for new tracks* to that port. Note the wording: it applies to
**new** tracks, so create the instrument track after setting it.

`next_list_midi_ports` reports whether this is set up; `next_play_notes` streams
to it. Verified end to end: notes generated here reach an 808 track in Next and
record as a MIDI clip with the correct pads.

`next_play_notes` refuses to fall back to the Microsoft GS Wavetable Synth: that
plays through the speakers and is not a route into another application, so
silently using it would look like success while Next heard nothing.

Live play is for **auditioning and recording**. Notes are only captured if Next
is actually recording; to place a part on the timeline without recording, use
`next_write_midi` plus `next_import_file`.

> Do not test whether Next is listening by trying to open the MIDI input
> exclusively. Next uses WinRT MIDI, not winmm, so the winmm handle stays free
> even while Next is receiving — the check reports "nothing is listening" when
> everything is fine. Only a functional test is meaningful.

**Still not possible:** editing an instrument's own parameters. Those are
custom-drawn plugin panels with no automatable surface.

## Tempo

**Set `next_set_project_tempo` before generating anything.** Nothing can read
the tempo back out of Next: the `.cnp` parser cannot reach it, and the toolbar
is a JUCE-drawn control with no queryable text. So the server stores it, and
`next_write_midi` and `next_play_notes` default to it rather than to an assumed
120 BPM.

Get this wrong and the material is silently misaligned — a part generated at
96 BPM against a 120 BPM project drifts further out of time with every bar,
while still looking correct. When an explicit `tempo_bpm` disagrees with the
stored project tempo, the tools return a warning saying so rather than quietly
obeying.

Every response reports `tempo_source`: `project` (the stored tempo),
`explicit` (you passed one), or `fallback` (nothing known, 120 assumed).

## Building a multi-track arrangement

`next_import_file` drops the file on **whichever track is selected**, and that
track keeps its own instrument. That single behaviour is what makes real
arrangements possible:

```
for each part:
    next_create_instrument_track(instrument)   # e.g. "808", "Dark Grand"
    select that track                          # click its strip
    next_import_file(part.mid)                 # keeps the instrument,
                                               # clip takes the MIDI track name
```

Import with nothing suitable selected and Next creates its own tracks instead,
all using a default instrument.

**Two things that will silently put parts on the wrong track:**

1. **Automation lanes change the strip height.** A track strip is 80px tall
   with lanes hidden and ~160px with them shown, so a hard-coded y selects a
   different track than you meant — the piano part lands on the drum track and
   nothing errors. `menus.track_strip_point()` documents the geometry; hide the
   lanes first (the toggle above the track list) or verify what got selected.
2. **The playhead decides where the clip starts.** Send `go_to_start` before
   importing unless you want the part somewhere else.

Only 4 of the 444 instruments are downloaded on a fresh install, so
`next_create_instrument_track` refuses the rest rather than creating a silent
track.

`undo` does **not** remove a track created this way — select its strip and use
`delete` with `confirm=true`.

## Writing music that actually sounds like music

`next_write_midi` will faithfully render whatever you give it, and correct
quantisation is not the same thing as a good arrangement. A first attempt here
was perfectly on the grid — hats on exact 8ths, chords on downbeats, every clip
starting at bar 1 — and still sounded, in the listener's words, "as though each
instrument is working alone" with "notes completely randomly placed".

Three faults, all compositional rather than technical:

* **The chords changed every bar.** At 120 BPM that is a new chord every two
  seconds. Loop-based genres want a chord to sit for two or four bars.
* **The melody ignored the harmony.** One fixed figure repeated over four
  different chords, so it collided with three of them. Build melodies from the
  chord tones of the bar they sit on.
* **The drum pattern varied every bar.** Nothing repeated long enough to become
  a groove. Repeat a one- or two-bar pattern verbatim and save variation for
  phrase ends.

`make_beat.py` in this repo is the corrected version and shows the shape: a
four-bar harmonic loop, a two-bar drum figure repeated verbatim, a hook built
from chord tones, and sections that add and remove layers rather than rewriting
them.

Also watch levels: four layers at full velocity clipped the master. Scaling
every velocity by a constant keeps the balance between parts while buying back
headroom — fix it in the source material, not on a fader whose value the agent
cannot read.

## How the server navigates Next, and why

Next is a JUCE application: it exposes no scripting API, no OSC and no control
surface, and it draws its entire UI itself, so almost nothing is reachable the
standard Win32 way — `ALT` does not open the menu bar, there are no
accelerators, and no control can be queried for its text or state. Everything below was arrived at by trying the obvious thing, watching
it fail, and finding what the failure implied. The failures are recorded because
each one is a rule someone would otherwise rediscover.

### Seeing beats guessing

**Every navigation bug in this project came from acting on assumed state.** Parts
landed on the wrong instruments twice, keystrokes went to the wrong application
once, and a click landed in an unrelated browser window once. In each case the
code believed something about the screen that was not true.

There is no live project file to read — `ProjectsCache` holds only a thumbnail —
so state cannot be queried. `next_screenshot` closes the gap by returning a PNG
the agent can look at. Capture is GDI via ctypes and PNG encoding is `zlib` from
the standard library, so it adds no dependency. Use it after anything whose
effect cannot otherwise be observed.

### Track strips are measured, not assumed

A strip is 80 logical px tall with automation lanes hidden and roughly 160 with
them shown. Hard-coding 80 silently selects the wrong track — it put the piano
part on the drum track, twice, with no error. `screen.detect_track_rows()` scans
a column down the track list for the dark rules between strips and returns each
strip's y position, so selection follows whatever the UI is actually doing.
Index 0 is the Master bus; index *n* is track *n*.

Better still, **avoid selecting at all**: a newly created track is already the
selected one, so `create_track` → `import_file` builds an arrangement without a
single strip click. That is the recommended pattern.

### Clicks verify their target first

A window's rect says where it *would* be, not whether it is on top. Computing a
position from the rect and clicking while another window covers it sends the
click to that window — which is how a click reached an unrelated browser.
`winctl.click_in(pid, x, y)` calls `WindowFromPoint` and refuses if the point
does not belong to Next. `focus()` will not click-to-activate a covered window
for the same reason. Never call bare `winctl.click()` on a computed target.

### Menus are clicked, confirmed, and retried

`ALT` does nothing in a JUCE app and there are no accelerators, so menu commands
are clicked. An open menu is a separate top-level window titled `menu`, which
gives a reliable "did it open?" check — the item is only clicked once that is
confirmed, because a missed menu-bar click would send the item click into the
arrangement and could drag a clip. The first click after the window takes focus
is sometimes swallowed, so opening retries up to four times.

### Dialogs are driven by message, not by typing

`WM_SETTEXT` to the filename field (control `1148`) and `BM_CLICK` on the Open
button need no focus and cannot race. Typing failed two different ways first:
keystrokes went to whichever control had focus (once the file list, where
`CTRL+A` selected every file), and per-character typing raced the dialog's
autocomplete, which turned a path into `iC:\...\riff.md`. The field is read back
before Open is clicked.

### Focus is taken by clicking

`SetForegroundWindow` is refused to a background process while
`ForegroundLockTimeout` has not elapsed, and that value can be effectively
infinite. Attach-thread-input, `SwitchToThisWindow` and minimise/restore all
fail against it. A synthetic click is ordinary input, so Windows activates
whatever it lands on — `focus()` clicks the window's title strip and reclaims
focus in about three seconds. Because focus can still move *between* keystrokes,
`next_run_command` re-verifies before every press.

### Failures are loud

A minimised window parks at (-32000, -32000); capturing there returns
framebuffer noise that looks like a real image and silently poisons anything
measured from it, so capture refuses. An instrument whose samples are not
downloaded would make a silent track, so `create_instrument_track` refuses.
Deleting a track cannot be undone, so it needs `confirm`. The pattern throughout
is that an operation which cannot be verified should fail rather than report a
success it has not checked.

### Removing an instrument

There is no mapped control for taking an instrument off a track while keeping
the track, so `next_delete_track` removes the track with it; recreate it with
`next_create_track`. Mapping the track inspector's plugin slot would allow an
in-place swap and is the obvious next addition.

## Safety

- `next_run_command` sends **only** the vetted commands in `commands.py`; arbitrary keystrokes are rejected.
- Ten commands (`delete`, `cut`, `new_project`, `open_project`, the bounce/normalize operations, and the Piano Roll note split/merge) require `confirm=true`.
- **Publish to BandLab (`CTRL+B`) is not exposed** — it uploads your project to the internet, which no agent should trigger from one tool call. `menus.py` blocks it by name too, so it cannot be reached via the menus either.
- **Exit (`CTRL+Q`) is not exposed** — quitting mid-session risks losing unsaved work with no way for the agent to answer the save prompt.
- **Arm (`ALT+A`) is not exposed** — the docs give Arm that chord, but the View menu binds `ALT+A` to *Show Arranger Track*, and the app wins. Arm from the UI.
- Running a command steals focus from whatever you are doing.
- Commands act on the current selection and playhead, which the server cannot see. Single-letter commands (`play_pause`, `record`, `split`) are swallowed if a text field inside Next has focus.

## Layout

| File | Contents |
|---|---|
| `cakewalk_next_mcp/server.py` | Tool definitions and input validation |
| `cakewalk_next_mcp/cnp.py` | `.cnp` project reader |
| `cakewalk_next_mcp/smf.py` | Standard MIDI File reader/writer (no dependencies) |
| `cakewalk_next_mcp/winctl.py` | Window discovery, focus, and `SendInput` via ctypes |
| `cakewalk_next_mcp/commands.py` | Command vocabulary and shortcut table |
| `cakewalk_next_mcp/instruments.py` | Installed soundbank index and kit pad maps |
| `cakewalk_next_mcp/midiout.py` | Live MIDI output via winmm |
| `cakewalk_next_mcp/state.py` | Persisted project tempo |
| `cakewalk_next_mcp/screen.py` | GDI screen capture, PNG encoding, track-row detection |
| `make_beat.py` | Example: a 16-bar boom-bap beat, built through the server |
| `make_bandit.py` | Example: a 28.8s melodic-trap beat at 150 BPM |
| `docs/DEVELOPMENT-LOG.md` | How this was built, including what failed |
| `cakewalk_next_mcp/menus.py` | Menu geometry for commands that have no shortcut |

## Where the shortcuts came from

**The app's own menus, read off screen** — not the documentation. Both the v1.1
User Manual (pp. 265–269) and the Help Center article
[Cakewalk Next: Keyboard Shortcuts](https://help.cakewalk.com/hc/en-us/articles/46039690837657-Cakewalk-Next-Keyboard-Shortcuts)
(updated 2026-05-22) disagree with the shipping build. Verified differences:

| Command | Docs say | Build 1.1.0.150 uses |
|---|---|---|
| Show Tempo Track | `T` | `ALT+T` |
| Show Arranger Track | `A` | `ALT+A` |
| `CTRL+G` / `CTRL+U` | Move to / Remove from Track Folder | Group / Ungroup |
| `F3` / `F4` | Track / Clip Inspector | Show Track Plugins / Show Piano Roll |
| Bounce Selected Clips | `CTRL+B` | does not exist (`CTRL+B` is Publish only) |
| Dock/Float Loop Browser | `CTRL+ALT+B` | does not exist (that is Bounce Tracks in Place) |

Undocumented commands that do exist: `CTRL+SPACE`, `CTRL+SHIFT+A`,
`CTRL+SHIFT+R`, `CTRL+ALT+S`, `CTRL+/`, `\`.

Shortcuts are remappable via *Help → Keyboard Shortcuts*; if you have remapped
any, update `commands.py` to match.
