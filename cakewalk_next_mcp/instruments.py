"""The instrument library Cakewalk Next installs locally.

Next stores every BandLab soundbank under
``C:\\ProgramData\\Cakewalk\\Next\\Soundbanks\\<slug>\\<slug>.json``. Each file
describes one instrument, and crucially drum kits list every pad with the MIDI
note that triggers it, so a generated part can hit the right drum by name rather
than by guessing at General MIDI numbers (these kits are not GM-mapped).

The same ``slug`` appears in ``.cnp`` project files, which is what lets
``cnp.parse_project`` report a bare "808-kit" and this module turn it into
"808", a kit whose pad 36 is a kick.
"""

from __future__ import annotations

import json
import os
import re

DEFAULT_ROOT = r"C:\ProgramData\Cakewalk\Next\Soundbanks"

# "036-Kick_808" -> midi 36, "Kick 808"
_SAMPLE_NAME = re.compile(r"^(\d{1,3})[-_](.+)$")

_index_cache = {}


class InstrumentError(ValueError):
    """Raised when the soundbank library cannot be read."""


def library_root(root=None):
    """Return the soundbank directory, or raise if it is not present."""
    path = root or os.environ.get("CAKEWALK_NEXT_SOUNDBANKS") or DEFAULT_ROOT
    if not os.path.isdir(path):
        raise InstrumentError(
            "Soundbank library not found at %s. It ships with Cakewalk Next; set "
            "CAKEWALK_NEXT_SOUNDBANKS if yours lives elsewhere." % path
        )
    return path


def _pretty(name):
    """Turn a sample file name like 'Kick_808' or '606KitV3-Kick' into words."""
    text = re.sub(r"[_\-]+", " ", name)
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _load(path):
    """Read one soundbank document.

    Nearly every file BandLab ships ends with a stray NUL byte after the closing
    brace, which makes a plain json.load fail with "Extra data" - so the bytes
    are trimmed first, and anything still trailing is ignored via raw_decode.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None

    try:
        text = raw.rstrip(b"\x00 \t\r\n").decode("utf-8-sig")
    except UnicodeDecodeError:
        return None

    try:
        return json.loads(text)
    except ValueError:
        try:
            return json.JSONDecoder().raw_decode(text.lstrip())[0]
        except ValueError:
            return None


def _summarise(data, slug):
    """Reduce one soundbank document to the fields worth reporting."""
    samples = data.get("samples") or []
    entry = {
        "slug": data.get("slug") or slug,
        "name": data.get("name") or slug,
        "category": data.get("category") or ("kit" if "kit" in slug else "instrument"),
        "family": data.get("instrumentSlug"),
        "tags": [t.replace("instrument-", "") for t in (data.get("filters") or [])],
        "synth": data.get("synth"),
        "default_octave": data.get("defaultOctave"),
        "color": ("#" + data["color"]) if data.get("color") else None,
        "sample_count": len(samples),
    }
    if data.get("subTitle"):
        entry["subtitle"] = data["subTitle"]

    notes = [s.get("midiNumber") for s in samples if isinstance(s.get("midiNumber"), int)]
    if notes:
        entry["note_range"] = {"low": min(notes), "high": max(notes)}
    return entry


def build_index(root=None, refresh=False):
    """Parse every soundbank JSON once and cache the result."""
    base = library_root(root)
    if not refresh and base in _index_cache:
        return _index_cache[base]

    index = {}
    for slug in sorted(os.listdir(base)):
        document = os.path.join(base, slug, slug + ".json")
        if not os.path.isfile(document):
            continue
        data = _load(document)
        if not data or data.get("isDeprecated"):
            continue
        entry = _summarise(data, slug)
        # Every bank ships a .json plus a preview .m4a. Anything with more files
        # than that has its actual samples on disk; the rest are the entries the
        # Instrument Browser marks with a download arrow.
        try:
            entry["installed"] = len(os.listdir(os.path.join(base, slug))) > 2
        except OSError:
            entry["installed"] = False
        index[entry["slug"]] = entry
    if not index:
        raise InstrumentError("No readable soundbanks found in %s." % base)
    _index_cache[base] = index
    return index


def search(query=None, category=None, family=None, limit=40, offset=0, root=None,
           installed_only=False):
    """Find instruments by free text, category ('kit'/'instrument') or family.

    ``installed_only`` keeps just the banks whose samples are already on disk.
    The rest show a download arrow in Next's browser and will not sound until
    they are fetched, so an agent picking an instrument should prefer these.
    """
    index = build_index(root)
    items = list(index.values())
    if installed_only:
        items = [i for i in items if i.get("installed")]

    # Reject unknown filters rather than returning an empty list, which would
    # read as "you own no drum kits" instead of "that is not a category".
    if category:
        wanted = category.strip().lower()
        known = sorted({(i["category"] or "").lower() for i in items})
        if wanted not in known:
            raise InstrumentError(
                "Unknown category %r. Valid categories: %s." % (category, ", ".join(known))
            )
        items = [i for i in items if (i["category"] or "").lower() == wanted]
    if family:
        wanted = family.strip().lower()
        known = sorted({(i.get("family") or "").lower() for i in items if i.get("family")})
        if wanted not in known:
            raise InstrumentError(
                "Unknown family %r. Valid families: %s." % (family, ", ".join(known))
            )
        items = [i for i in items if (i.get("family") or "").lower() == wanted]
    if query:
        needle = query.strip().lower()
        scored = []
        for item in items:
            haystack = " ".join(filter(None, [
                item["name"], item["slug"], item.get("subtitle"),
                item.get("family"), " ".join(item["tags"]),
            ])).lower()
            if needle in haystack:
                # Exact and prefix matches on the display name rank first.
                name = item["name"].lower()
                rank = 0 if name == needle else 1 if name.startswith(needle) else 2
                scored.append((rank, item["name"].lower(), item))
        scored.sort(key=lambda s: (s[0], s[1]))
        items = [i for _r, _n, i in scored]
    else:
        items.sort(key=lambda i: i["name"].lower())

    page = items[offset:offset + limit]
    return {
        "total": len(items),
        "count": len(page),
        "offset": offset,
        "items": page,
        "has_more": offset + len(page) < len(items),
        "next_offset": offset + len(page) if offset + len(page) < len(items) else None,
    }


def resolve(name_or_slug, root=None):
    """Return one instrument by exact slug, else by case-insensitive name."""
    index = build_index(root)
    if name_or_slug in index:
        return index[name_or_slug]
    needle = name_or_slug.strip().lower()
    for item in index.values():
        if item["name"].lower() == needle or item["slug"].lower() == needle:
            return item
    return None


def details(name_or_slug, root=None):
    """Return full detail for one instrument, including a kit's pad map."""
    summary = resolve(name_or_slug, root)
    if not summary:
        hits = search(name_or_slug, limit=6, root=root)["items"]
        raise InstrumentError(
            "No instrument called %r.%s"
            % (name_or_slug,
               (" Close matches: %s." % ", ".join(
                   "%s (%s)" % (h["name"], h["slug"]) for h in hits)) if hits else
               " Use next_list_instruments to browse.")
        )

    base = library_root(root)
    data = _load(os.path.join(base, summary["slug"], summary["slug"] + ".json")) or {}
    result = dict(summary)

    pads = []
    for sample in data.get("samples") or []:
        midi = sample.get("midiNumber")
        if not isinstance(midi, int):
            continue
        label = sample.get("fileName") or ""
        match = _SAMPLE_NAME.match(label)
        pads.append({
            "midi": midi,
            "sound": _pretty(match.group(2)) if match else _pretty(label),
            "low": sample.get("minRange"),
            "high": sample.get("maxRange"),
        })
    pads.sort(key=lambda p: p["midi"])

    if result["category"] == "kit":
        # For a kit each sample is a separate drum, so the map is the point.
        result["pads"] = pads
        result["usage"] = (
            "Write drums with next_write_midi using these MIDI numbers as pitch, "
            "on any channel - Next routes the track to this kit, so channel 9 is "
            "not required. These kits are not General MIDI mapped."
        )
    else:
        result["playable_range"] = result.pop("note_range", None)
        result["sampled_notes"] = [p["midi"] for p in pads]
        result["usage"] = (
            "A melodic instrument. Stay within playable_range when writing parts; "
            "notes outside it may not sound."
        )
    return result


# --------------------------------------------------------------------------
# Whether a bank has anything to download.
#
# Only MIDISampleSynth banks are made of samples. VASynth and FMSynth banks
# generate their sound, carry no samples, and so report installed=False for
# ever -- the flag counts sample files on disk and there are none. Treating
# that as "not downloaded, would be silent" wrongly locks out 152 of the 444
# banks, including every 808 synth bass and most of the pads.
# --------------------------------------------------------------------------
SAMPLE_BASED_SYNTH = "MIDISampleSynth"


def is_sample_based(entry):
    """True when this bank's sound comes from samples that must be fetched."""
    return bool(entry) and entry.get("synth") == SAMPLE_BASED_SYNTH


def needs_download(entry):
    """True only when a bank is sample-based AND its samples are missing."""
    return is_sample_based(entry) and not entry.get("installed")
