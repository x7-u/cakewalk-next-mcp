# Development log

How this server was built, in order, including the things that did not work.

Everything here was discovered by trying something against the real application
and watching what happened. Cakewalk Next has no scripting API, so there was no
documentation to follow — only behaviour to observe. The failures are written
down because each one produced a rule, and a reader who only saw the finished
code would have no way to know why the code is shaped the way it is.

---

## 0. The false start

The brief began with a URL: `https://mcp.getcakewalk.io/mcp`. It was registered
as an MCP server and it responded — with `401` and an OAuth challenge.

It was also the wrong thing entirely. "Cakewalk" there is an unrelated company;
the actual subject was **Cakewalk Next**, BandLab's DAW. The registration was
removed and the real work started from nothing.

**Rule:** confirm what a name refers to before building against it.

---

## 1. Research: what does Next actually expose?

The 284-page *Cakewalk Next User Manual* was downloaded and searched for every
plausible automation surface:

| Looked for | Found |
|---|---|
| Scripting API | none |
| OSC | none |
| Mackie Control / MCU / HUI | none |
| Command line | none |
| Keyboard shortcuts | **yes** — a full chapter |
| MIDI import/export | **yes** |
| Project format | `.cnp`, undocumented |

That result determined the whole architecture. With no API, no network control
surface and no scripting, only three routes exist:

1. **`.cnp` project files**, read offline.
2. **Standard MIDI Files**, for getting musical material in and out.
3. **The documented keyboard shortcuts**, driven with Win32 `SendInput`.

Everything the server does still sits on those three, plus screen capture added
much later.

---

## 2. Reverse-engineering the `.cnp` format

A real project file was available, so it was taken apart byte by byte.

The format is a tree of little-endian chunks whose 4CC tags are stored
**byte-reversed**: `jorp` → `proj`, `kcrt` → `trck`, `emit` → `time`. Container
chunks carry `tag(4) + u16 version + u64 payload_size`. That much walks cleanly
from the top of the file.

It then stops walking. Leaf chunks **omit the size field** and are delimited by
their parent, so a generic reader cannot skip a chunk it does not understand —
you need the schema, and there isn't one.

Rather than ship a half-decoded parser that would silently misreport a user's
project, the parser reads only the two layers that *are* unambiguous:

- the **chunk-tag histogram**, which counts structural elements; and
- the **length-prefixed string table** (`u32 length-including-NUL + bytes`),
  which holds names, paths, colours, plugin slugs and GUIDs.

That recovers track names, buses, instrument presets, track colours, referenced
audio and the version of Next that saved the file. It does **not** recover
tempo, time signature, clip positions or notes, and the tool says so rather than
returning nothing and letting the caller assume the project is empty.

**Validated against ground truth:** the parser reported tracks `808`,
`Dark Grand`, `dave`, `606` — and when the project was later opened in Next,
those were exactly the four tracks, in that order, with `dave` showing "File
missing" because its audio lived on an unmounted drive.

---

## 3. First build

Five modules, no dependencies beyond the MCP SDK:

- **`smf.py`** — a Standard MIDI File reader/writer written from scratch.
  Format 1, variable-length quantities, tempo and time-signature meta events.
  Note-offs sort before note-ons at equal ticks, otherwise a re-struck pitch
  silences itself.
- **`winctl.py`** — window discovery, focus and `SendInput`, all through
  `ctypes`, so no `pywin32`.
- **`commands.py`** — the keyboard command vocabulary.
- **`cnp.py`** — the project reader above.
- **`server.py`** — the MCP tools.

---

## 4. MCP SDK 2.x, and a bug that would have been invisible

The installed SDK was **mcp 2.x**, where `FastMCP` is renamed `MCPServer`. A
compatibility shim handles both.

More importantly, the first live test raised a plain `ValueError` and the client
received:

```
Error executing tool next_run_command
```

The carefully written, actionable message was **gone**. Reading the SDK source
showed why: `ToolError` is forwarded to the model verbatim; every other
exception is treated as a crash and its text is deliberately kept server-side.

Every error path in the server was switched to `ToolError`. Without that, sixteen
useful diagnostics would have been silently replaced by a generic string.

**Rule:** verify that your error messages actually arrive.

---

## 5. Live testing, round one: the modifier bug

With Next running, commands were sent for real. `F3` worked. `SPACE` worked.
`CTRL+M`, `CTRL+T`, `ALT+H`, `SHIFT+F` did **nothing**.

A clipboard test isolated it: `CTRL+A`/`CTRL+C` in Notepad worked perfectly, so
`SendInput` was fine. The problem was specific to Next.

**Cause:** Cakewalk Next resolves shortcuts from **hardware scan codes**.
`SendInput` records built with `wScan=0` are enough for ordinary Win32 apps, but
Next drops the modifier and acts on the **bare key**.

That is worse than nothing happening:

| Intended | What Next actually did |
|---|---|
| `CTRL+S` (Save) | `S` = **Split** |
| `CTRL+T` (New audio track) | `T` = Show/Hide Tempo Track |
| `CTRL+SHIFT+Z` (Redo) | `Z` = nothing |

The fix sends `MapVirtualKeyW`-derived scan codes with **left-hand** modifier
VKs (`VK_LCONTROL`/`VK_LSHIFT`/`VK_LMENU`) and presses the modifier slightly
ahead of the base key.

Two timing bugs surfaced alongside it: a window restored from the taskbar is not
ready for input the instant it reports itself foreground, and neither is a window
the *user* just clicked. Both needed a settle delay.

---

## 6. `CTRL+S` was never broken

Once modifiers worked, saving still did nothing. Next answered with its own
notification:

> **Save Project Unavailable** — The 'Save Project' operation requires
> activation. Sign in to activate Cakewalk Next.

Not a bug in the server. The tool reports success because the keystroke *was*
delivered; the refusal happens inside Next. `next_app_status` exposes
`has_unsaved_changes` so a caller can check whether a save actually took.

---

## 7. The documentation is wrong; the app is the source of truth

The Help Centre's shortcut article is newer than the PDF and agreed with it. Both
disagree with the shipping build. Reading the app's own menus off-screen found:

| Command | Docs say | Build 1.1.0.150 |
|---|---|---|
| Show Tempo Track | `T` | **`ALT+T`** |
| Show Arranger Track | `A` | **`ALT+A`** |
| `CTRL+G` / `CTRL+U` | Move to / Remove from Track Folder | **Group / Ungroup** |
| `F3` / `F4` | Track / Clip Inspector | **Show Track Plugins / Show Piano Roll** |
| Bounce Selected Clips | `CTRL+B` | **does not exist** |
| Dock/Float Loop Browser | `CTRL+ALT+B` | **does not exist** |

Two chords had been excluded as "ambiguous" on the strength of the docs. Both
were **documentation errors**: `CTRL+B` is only Publish to BandLab, and
`CTRL+ALT+B` is only Bounce Tracks in Place. The latter was restored.

The menus also revealed undocumented commands: `CTRL+SPACE`, `CTRL+SHIFT+A`,
`CTRL+SHIFT+R`, `CTRL+ALT+S`, `CTRL+/` and `\`.

The table was rebuilt from the menus. **70 commands, no duplicate chords.**

---

## 8. Driving menus and dialogs

Next is a JUCE application. `ALT` does not open the menu bar and there are no
accelerators, so menu commands must be clicked.

**Menus:** an open menu is a separate top-level window titled `menu`. That gives
a reliable "did it open?" check, and the item is only clicked once the popup is
confirmed — otherwise a missed menu-bar click sends the item click into the
arrangement, where it could drag a clip. The first click after focus is
sometimes swallowed, so opening retries up to four times.

**File dialogs failed twice before working:**

1. Typing went to whichever control had focus. Once that was the file *list*,
   where `CTRL+A` selected every file in the folder.
2. Per-character typing raced the dialog's autocomplete, which rewrote the field
   mid-entry and turned a path into `iC:\...\riff.md` — the `i` from `.mid`
   teleported to the front.

The fix abandons typing: `WM_SETTEXT` to the filename field (control `1148`) and
`BM_CLICK` on the Open button. No focus needed, nothing to race, and the field is
**read back and compared** before Open is clicked.

A ctypes crash was found here too — `GetClipboardData` defaults to a 32-bit
return, truncating the 64-bit handle, so `GlobalLock` dereferenced garbage and
killed the server process. All pointer-returning Win32 calls now have explicit
prototypes.

---

## 9. The instrument library

Next installs every BandLab soundbank locally as JSON. The `slug` in those files
is the **same slug `.cnp` stores**, which is what lets a bare `808-kit` become
"808, a drum kit".

The first index came back with **4 entries out of 444**. Nearly every file ships
with a stray `\x00` after the closing brace, so `json.load` fails with
"Extra data". Trimming the bytes fixed it.

The payoff is the **pad maps**. These kits are *not* General MIDI, so guessing
note numbers gets the wrong drum:

```
808-kit → 36 Kick · 37 Cross Stick · 38 Snare · 39 Clap · 40 Clave
          41 Low Tom · 42 HH Closed · 45 Mid Tom · 46 HH Open
          48 High Tom · 49 Crash · 56 Cowbell
```

A bank folder containing more than its `.json` plus a preview has its samples on
disk. That distinguishes installed banks from the ones the browser marks with a
download arrow — often only a handful of the 444 — so a track is never created
with an instrument that cannot make a sound.

---

## 10. Live MIDI

Playing an instrument in real time needs a virtual MIDI cable. Windows ships
none, and a stock machine reports **zero MIDI inputs**.

**loopMIDI** provides one. Installing it non-interactively failed:

```
This software can only be installed with administrative rights!
Error 0x81f40001: Bundle condition evaluated to false: Privileged <> 0
```

It installs a kernel driver, so the `--disable-interactivity` flag had
suppressed the elevation prompt it needs. Relaunching the already-hash-verified
installer with `-Verb RunAs` succeeded.

loopMIDI then needs a port added, normally by clicking **+** in its window. It is
a Delphi app with real Win32 controls, so the button was found by class and text
and clicked with `BM_CLICK` — no focus stealing.

**A false negative worth recording:** whether Next is listening was tested by
trying to open the MIDI input exclusively. It always reported "free". Next uses
**WinRT MIDI**, not `winmm`, so the `winmm` handle stays available even while
Next is receiving. The check said "nothing is listening" while everything was
fine. Only a functional test is meaningful — and the functional test passed:
notes generated here reached an 808 track and recorded as a MIDI clip with the
correct pads.

`next_play_notes` refuses to fall back to the Microsoft GS Wavetable Synth. That
plays through the speakers and is not a route into another application, so using
it would look like success while Next heard nothing.

---

## 11. The music was wrong, and it was not a timing bug

The first full beat was reported as sounding "as though each instrument is
working alone", with "notes completely randomly placed".

The MIDI was checked. It was **perfectly quantised** — hi-hats on exact eighths,
chords on downbeats, every clip starting at bar 1. Nothing was random.

The faults were compositional:

1. **Chords changed every bar.** At 120 BPM that is a new chord every two
   seconds. Loop-based genres want a chord to sit for two or four bars.
2. **The melody ignored the harmony.** One fixed figure ran over four different
   chords, colliding with three of them.
3. **The drum pattern varied every bar.** Nothing repeated long enough to become
   a groove.

The rewrite used a four-bar harmonic loop, a two-bar drum figure repeated
verbatim, and a hook built from the **chord tones of the bar it sits on**.

A second pass applied standard songwriting conventions properly:

- **Structure** — the short-form (30-60s) shape is **Hook / Verse / Hook**. The
  earlier arrangement used a three-minute song's Intro-Build-Breakdown shape
  crammed into 32 seconds, spending its first eight bars arriving.
- **Theory** — i-VII-VI-VII is the "dark, driving" progression.
- **Genre** — trap sits in Am/Cm; the verse should thin out to leave
  room for a vocal.

**Levels matter too.** Four layers at full velocity clipped the master. Scaling
every velocity by a constant keeps the balance between parts while buying back
headroom — the right place to fix that is the source material, not a fader whose
value the agent cannot read.

---

## 12. Autonomy

Three problems stood between the server and running unattended.

### Focus was refused

Windows will not let a background process call `SetForegroundWindow` until
`ForegroundLockTimeout` has elapsed since the last user input, and that value can
be set to `0x7FFFFFFF` — effectively forever. Attach-thread-input,
`SwitchToThisWindow` and minimise/restore all failed against it.

What works is a **synthetic mouse click**: it is ordinary input, so Windows
activates whatever it lands on. `focus()` clicks the window's title strip and
reclaims focus in about three seconds. Verified by letting Notepad steal the
foreground and taking it back unaided.

Because focus can move *between* keystrokes, `next_run_command` re-verifies
before every press rather than trusting the initial focus.

### A click went into an unrelated application

While clicking an entry in Next's Quick Start window, the position was computed
from that window's rect — but the window was **behind a browser**. The click went
to the browser.

A window's rect says where it *would* be, not whether it is on top.
`winctl.click_in(pid, x, y)` now calls `WindowFromPoint` and refuses if the point
does not belong to Next:

```
Refusing to click at (805, 387): that point belongs to
'... - Google Chrome' (pid 21312), not Cakewalk Next
```

`focus()` will not click-to-activate a covered window for the same reason. The
bare `click()` is documented as unsafe for computed targets.

### The server was blind

Every navigation failure had the same root cause: **acting on assumed state**.
Parts landed on the wrong instruments twice because a track strip is 80 logical
pixels tall with automation lanes hidden and roughly 160 with them shown — a
hard-coded offset silently selects a different track, and nothing errors.

There is no live project file to read; `ProjectsCache` holds only a thumbnail.
So the agent was given eyes:

- **`next_screenshot`** returns a PNG. Capture is GDI through `ctypes`; PNG
  encoding is `zlib` from the standard library, so no imaging dependency.
- **`screen.detect_track_rows()`** finds each strip by scanning a column for the
  rules between them, so selection follows whatever the UI is actually doing.

Better still, selection is usually avoidable: a newly created track is already
selected, so **create-then-import** builds an arrangement without a single strip
click. That is the recommended pattern.

A guard came out of this too. A minimised window parks at `(-32000, -32000)`, and
capturing there returns framebuffer noise that *looks* like a real screenshot.
It caused a row detector to "find" nothing while it profiled garbage. Capture now
refuses outright.

### The tempo, which could not be set at all

The toolbar tempo is a drawn control with no queryable text. It turned out to be
editable by double-clicking, clearing and typing — but typing without clearing
**appends**: 120 with `150` typed became **300**, Next's ceiling.
`next_set_daw_tempo` does the full sequence and returns a photo of the toolbar,
because seeing the value is the only confirmation available.

---

## 13. Where it ended

**19 tools.** Project reading, an instrument library with pad maps, MIDI in and
out, live MIDI over a virtual cable, track creation and deletion, menu-driven
import, transport and 70 vetted commands, tempo control, and screen capture.

Two complete beats were written, built and played end to end: a 32-second
boom-bap piece and a 28.8-second "Bandit"-type melodic trap beat at 150 BPM in
C# minor, each with four instrument tracks assembled by the server.

### What is still not possible

- **Editing an instrument's parameters.** Plugin panels are custom-drawn with no
  addressable controls. Blind-clicking a synth panel is not a feature.
- **Removing an instrument while keeping its track.** No mapped control exists;
  the track goes with it.
- **Saving unattended.** The *Save As* dialog does not use control `1148` like
  the *Open* dialog, so the filename field was not found. It failed safely
  rather than typing into the wrong control, and remains unmapped.
- **Reading tempo, selection or clip positions programmatically.** They can only
  be seen.
