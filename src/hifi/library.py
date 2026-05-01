"""Local music library scanner + seed-file parser."""

import json
import logging
import os
import random
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import mutagen

from hifi.config import AUDIO_EXTENSIONS

_SCAN_WORKERS = 16
_OWNED_CACHE_PATH = os.path.expanduser("~/.cache/hifi/owned.json")

log = logging.getLogger(__name__)

_EXTINF_RE = re.compile(r"^#EXTINF:[^,]*,(.+)$")
_MBID_KEYS = ("musicbrainz_recordingid", "musicbrainz_trackid")
_AUDIO_EXT_RE = re.compile(r"\.(opus|flac|m4a|mp3|ogg|wav|aiff?)$", re.IGNORECASE)


@dataclass
class Seed:
    artist: str
    title: str
    mbid: str | None = None
    source_path: str | None = None


def _read_tags(path: str) -> Seed | None:
    try:
        f = mutagen.File(path, easy=True)
    except Exception as e:
        log.debug("mutagen failed on %s: %s", path, e)
        return None
    if not f or not f.tags:
        return None
    artist = (f.tags.get("artist") or [""])[0]
    title = (f.tags.get("title") or [""])[0]
    if not artist or not title:
        return None
    mbid: str | None = None
    for key in _MBID_KEYS:
        v = f.tags.get(key)
        if v:
            mbid = v[0] if isinstance(v, list) else str(v)
            break
    return Seed(artist=artist, title=title, mbid=mbid, source_path=path)


def scan(path: str, sample: int | None = None) -> list[Seed]:
    """Walk ``path`` and return seeds from files with readable tags."""
    files: list[str] = []
    for root, _, names in os.walk(path):
        for n in names:
            if n.lower().endswith(AUDIO_EXTENSIONS):
                files.append(os.path.join(root, n))

    if sample is not None and sample < len(files):
        files = random.sample(files, sample)

    seeds: list[Seed] = []
    for p in files:
        s = _read_tags(p)
        if s:
            seeds.append(s)
        else:
            log.debug("skipping (no tags): %s", p)
    return seeds


def _parse_artist_title(line: str) -> Seed | None:
    line = line.strip()
    if not line or " - " not in line:
        return None
    artist, title = line.split(" - ", 1)
    artist = artist.strip()
    title = title.strip()
    if not artist or not title:
        return None
    return Seed(artist=artist, title=title)


def _parse_path_basename(line: str) -> Seed | None:
    """Parse 'Artist - Title - Album.flac' (or similar) into a Seed.

    Trailing 3rd+ chunks are treated as album metadata and dropped. Used for
    Poweramp-style M3U exports that list bare file paths instead of #EXTINF.
    """
    base = os.path.basename(line.strip())
    base = _AUDIO_EXT_RE.sub("", base)
    if " - " not in base:
        return None
    parts = [p.strip() for p in base.split(" - ")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return Seed(artist=parts[0], title=parts[1])


def _normalize_artist_title(artist: str, title: str) -> str:
    """Lowercased ``artist|title`` key for fuzzy 'do I have this' lookups."""
    return f"{(artist or '').strip().lower()}|{(title or '').strip().lower()}"


def _load_owned_cache() -> dict[str, dict]:
    try:
        with open(_OWNED_CACHE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_owned_cache(cache: dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(_OWNED_CACHE_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="owned-", suffix=".json",
                               dir=os.path.dirname(_OWNED_CACHE_PATH))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cache, f)
        os.replace(tmp, _OWNED_CACHE_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _scan_one(path_mtime: tuple[str, float]) -> tuple[str, float, dict | None]:
    """Read tags for one path; returns (path, mtime, entry-or-None)."""
    path, mtime = path_mtime
    s = _read_tags(path)
    if not s:
        return path, mtime, None
    return path, mtime, {
        "mtime": mtime,
        "mbid": s.mbid,
        "key": _normalize_artist_title(s.artist, s.title),
    }


def collect_owned(paths: list[str]) -> tuple[set[str], set[str]]:
    """Walk owned music directories, return (mbids, normalized titles).

    Used to skip recommendations for tracks the user already has on disk.
    MBID match is the strong signal (when files were tagged via Picard /
    hifi); the normalized ``artist|title`` set covers files without MBIDs.

    Cached at ``~/.cache/hifi/owned.json`` keyed by (path, mtime). First
    run reads every file's tags; subsequent runs only re-read files whose
    mtime changed. Stale entries (paths no longer present) are pruned.
    Tag reads are fanned out across a thread pool because mutagen is
    I/O-bound on network mounts.
    """
    files: list[str] = []
    for p in paths:
        if not os.path.isdir(p):
            log.warning("owned-dir does not exist: %s", p)
            continue
        for root, _, names in os.walk(p):
            for n in names:
                if n.lower().endswith(AUDIO_EXTENSIONS):
                    files.append(os.path.join(root, n))

    mbids: set[str] = set()
    titles: set[str] = set()
    if not files:
        return mbids, titles

    cache = _load_owned_cache()
    new_cache: dict[str, dict] = {}
    to_read: list[tuple[str, float]] = []

    for full in files:
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        cached = cache.get(full)
        if cached and cached.get("mtime") == mtime:
            new_cache[full] = cached
            if cached.get("mbid"):
                mbids.add(cached["mbid"])
            if cached.get("key"):
                titles.add(cached["key"])
        else:
            to_read.append((full, mtime))

    if to_read:
        log.info("owned scan: reading tags for %d new/changed files "
                 "(%d cached)", len(to_read), len(new_cache))
        with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as ex:
            for path, mtime, entry in ex.map(_scan_one, to_read):
                if entry is None:
                    continue
                new_cache[path] = entry
                if entry.get("mbid"):
                    mbids.add(entry["mbid"])
                titles.add(entry["key"])

    if new_cache != cache:
        _save_owned_cache(new_cache)
    return mbids, titles


def read_seed_file(path: str) -> list[Seed]:
    """Parse a seed list from M3U (#EXTINF lines) or plain text.

    Plain text format mirrors ``read_url_file`` in cli.py: one
    ``Artist - Title`` per line, blank lines and ``#`` comments ignored.
    """
    with open(path) as f:
        lines = f.readlines()

    seeds: list[Seed] = []
    is_m3u = path.lower().endswith((".m3u", ".m3u8"))

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if is_m3u:
            m = _EXTINF_RE.match(line)
            if m:
                s = _parse_artist_title(m.group(1))
                if s:
                    seeds.append(s)
                continue
            if line.startswith("#"):
                continue
            s = _parse_path_basename(line)
            if s:
                seeds.append(s)
            continue
        if line.startswith("#"):
            continue
        s = _parse_artist_title(line)
        if s:
            seeds.append(s)
    return seeds
