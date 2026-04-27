"""Local music library scanner + seed-file parser."""

import logging
import os
import random
import re
from dataclasses import dataclass

import mutagen

from hifi.config import AUDIO_EXTENSIONS

log = logging.getLogger(__name__)

_EXTINF_RE = re.compile(r"^#EXTINF:[^,]*,(.+)$")
_MBID_KEYS = ("musicbrainz_recordingid", "musicbrainz_trackid")


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
            # ignore other M3U lines (file paths, #EXTM3U, etc.)
            continue
        if line.startswith("#"):
            continue
        s = _parse_artist_title(line)
        if s:
            seeds.append(s)
    return seeds
