"""Read-only inspection of Cakewalk Next ``.cnp`` project files.

The format is undocumented. It is a tree of little-endian chunks whose 4CC tags
are stored byte-reversed on disk ('jorp' -> 'proj', 'kcrt' -> 'trck'). Container
chunks carry ``tag(4) + u16 version + u64 payload_size``, but leaf chunks omit
the size field and are delimited by their parent, so a generic walker cannot
recurse safely without the schema.

Rather than guess at offsets, this module reads the two things that *are*
unambiguous, and says so:

* the chunk-tag histogram, which counts structural elements; and
* the length-prefixed string table (``u32 length-including-NUL + bytes``), which
  holds names, paths, colours, plugin slugs and GUIDs.

Everything reported here is derived from those two, so track names and media
references are reliable while note-level and timeline data is simply absent.
Do not treat a missing field as evidence the project lacks that feature.
"""

from __future__ import annotations

import os
import re
import struct

# Chunk tags seen on disk, reversed to their logical names.
KNOWN_TAGS = {
    "docu": "document",
    "docc": "document content",
    "blme": "application metadata",
    "proj": "project",
    "prat": "project attributes",
    "mixs": "mixer",
    "proc": "processor (plugin/instrument slot)",
    "trck": "track",
    "clip": "clip",
    "mclp": "MIDI clip",
    "mdcl": "MIDI clip data",
    "time": "time/tempo element",
    "auto": "automation",
    "regn": "region",
    "rout": "routing",
    "snap": "snapshot",
    "lanc": "lane",
    "opin": "input",
    "ppin": "pin",
}

_GUID32 = re.compile(r"^[0-9a-f]{32}$")
_GUID_DASHED = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ARGB = re.compile(r"^[0-9a-f]{8}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
_AUDIO_EXT = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".oga", ".w64")

MAX_FILE_BYTES = 256 * 1024 * 1024


class CnpError(ValueError):
    """Raised when a file is not a readable Cakewalk Next project."""


def _is_tag(data, offset):
    return offset + 4 <= len(data) and all(97 <= b <= 122 for b in data[offset:offset + 4])


def extract_strings(data):
    """Return ``(offset, text)`` for every length-prefixed ASCII string.

    Strings are stored as a u32 byte count that includes the trailing NUL. The
    scan advances past each accepted string so that text containing digits
    cannot be re-read as a spurious length prefix.
    """
    found = []
    offset = 0
    limit = len(data) - 4
    while offset < limit:
        size = struct.unpack_from("<I", data, offset)[0]
        end = offset + 4 + size
        if 2 <= size <= 512 and end <= len(data) and data[end - 1] == 0:
            raw = data[offset + 4:end - 1]
            if raw and all(32 <= b < 127 for b in raw):
                found.append((offset, raw.decode("ascii")))
                offset = end
                continue
        offset += 1
    return found


def tag_histogram(data):
    """Count chunk tags that sit in a plausible header position.

    A tag only counts when the u64 immediately after its version field is a
    credible payload size. That filter removes the many false hits produced by
    lowercase runs inside ordinary text such as 'processor'.
    """
    counts = {}
    for offset in range(len(data) - 14):
        if not _is_tag(data, offset):
            continue
        tag = data[offset:offset + 4][::-1].decode("ascii")
        if tag not in KNOWN_TAGS:
            continue
        size = struct.unpack_from("<Q", data, offset + 6)[0]
        if size <= len(data):
            counts[tag] = counts.get(tag, 0) + 1
    return counts


def _classify(strings):
    """Split the raw string table into the categories we can identify."""
    out = {
        "tracks": [],
        "buses": [],
        "recorder_sources": [],
        "media": [],
        "paths": [],
        "colors": [],
        "slugs": [],
        "plain": [],
    }
    for offset, text in strings:
        if _GUID32.match(text) or _GUID_DASHED.match(text):
            continue
        if text.startswith("Track: "):
            out["tracks"].append((offset, text[7:]))
        elif text.startswith("Bus: "):
            out["buses"].append((offset, text[5:]))
        elif text.startswith("RecorderSource: "):
            out["recorder_sources"].append((offset, text[16:]))
        elif text.lower().endswith(_AUDIO_EXT):
            out["media"].append((offset, text))
        elif "\\" in text or "/" in text:
            out["paths"].append((offset, text))
        elif _ARGB.match(text):
            out["colors"].append((offset, text))
        elif _SLUG.match(text):
            out["slugs"].append((offset, text))
        else:
            out["plain"].append((offset, text))
    return out


def _nearest_before(candidates, offset):
    """Return the text of the last candidate positioned before ``offset``."""
    best = None
    for pos, text in candidates:
        if pos < offset and (best is None or pos > best[0]):
            best = (pos, text)
    return best[1] if best else None


def parse_project(path, include_diagnostics=False):
    """Inspect a ``.cnp`` file and return everything recoverable from it."""
    if not os.path.isfile(path):
        raise CnpError("No such file: %s" % path)
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        raise CnpError(
            "File is %.1f MB, above the %d MB inspection limit."
            % (size / 1048576.0, MAX_FILE_BYTES // 1048576)
        )
    with open(path, "rb") as handle:
        data = handle.read()

    if len(data) < 32 or data[8:12] != b"ucod":
        raise CnpError(
            "%s does not look like a Cakewalk Next project: expected the 'docu' "
            "chunk tag at byte 8. Cakewalk *Sonar* projects (.cwp) use a "
            "different format and are not supported." % os.path.basename(path)
        )

    strings = extract_strings(data)
    groups = _classify(strings)
    lookup = {text: offset for offset, text in strings}

    app_name = None
    app_version = None
    marker = data.find(b"emlb")
    if marker != -1:
        header = [text for offset, text in strings if marker < offset < marker + 200]
        if header:
            app_name = header[0]
        if len(header) > 1:
            app_version = header[1]

    project_path = None
    project_name = None
    for offset, text in groups["paths"]:
        if text.lower().endswith(".cnp"):
            project_path = text
            project_name = _nearest_before(groups["plain"], offset)
            break

    # An instrument preset slug is preceded by the instrument's display name.
    instruments = []
    for offset, slug in groups["slugs"]:
        display = _nearest_before(groups["plain"], offset)
        if display:
            instruments.append({"name": display, "preset": slug})

    track_names = [text for _offset, text in groups["tracks"]]
    tracks = []
    for name in track_names:
        entry = {"name": name}
        instrument = next((i["name"] for i in instruments if i["name"] == name), None)
        if instrument:
            entry["instrument_preset"] = next(
                i["preset"] for i in instruments if i["name"] == name
            )
        colour_offset = lookup.get("Track: " + name)
        if colour_offset is not None:
            colour = _nearest_before(groups["colors"], colour_offset)
            if colour:
                entry["color_argb"] = "#" + colour
        tracks.append(entry)

    media = []
    seen = set()
    for _offset, text in groups["media"]:
        if text not in seen:
            seen.add(text)
            media.append(text)

    result = {
        "file": os.path.abspath(path),
        "file_size_bytes": size,
        "project_name": project_name,
        "original_path": project_path,
        "created_with": {"app": app_name, "version": app_version},
        "tracks": tracks,
        "track_count": len(tracks),
        "buses": [text for _offset, text in groups["buses"]],
        "instruments": instruments,
        "media_files": media,
        "recorded_sources": sorted({t for _o, t in groups["recorder_sources"]}),
        "referenced_paths": sorted({t for _o, t in groups["paths"]}),
        "coverage_note": (
            "Names, colours, plugin presets and media references are read "
            "directly from the project's string table. Tempo, time signature, "
            "clip positions and note data are NOT recoverable from .cnp with "
            "the current parser - read those from an exported .mid instead."
        ),
    }

    if include_diagnostics:
        counts = tag_histogram(data)
        result["diagnostics"] = {
            "chunk_counts": {
                tag: {"count": n, "meaning": KNOWN_TAGS[tag]}
                for tag, n in sorted(counts.items(), key=lambda kv: -kv[1])
            },
            "string_count": len(strings),
            "unclassified_strings": sorted({t for _o, t in groups["plain"]}),
        }
    return result


def find_projects(root, limit=50, offset=0):
    """Walk ``root`` for .cnp files, newest first, with pagination metadata."""
    if not os.path.isdir(root):
        raise CnpError("Not a directory: %s" % root)

    entries = []
    for folder, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for filename in filenames:
            if filename.lower().endswith(".cnp"):
                full = os.path.join(folder, filename)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                entries.append({
                    "path": full,
                    "name": os.path.splitext(filename)[0],
                    "size_bytes": stat.st_size,
                    "modified": stat.st_mtime,
                })

    entries.sort(key=lambda e: e["modified"], reverse=True)
    page = entries[offset:offset + limit]
    return {
        "root": os.path.abspath(root),
        "total": len(entries),
        "count": len(page),
        "offset": offset,
        "items": page,
        "has_more": offset + len(page) < len(entries),
        "next_offset": offset + len(page) if offset + len(page) < len(entries) else None,
    }
