"""Playlist writers: M3U and JSPF."""

import json
from dataclasses import dataclass


@dataclass
class PlaylistEntry:
    artist: str
    title: str
    mbid: str | None = None
    duration: int | None = None


def write_m3u(entries: list[PlaylistEntry], path: str):
    """Write an EXTM3U with one #EXTINF per entry.

    The body line uses a ``# search:Artist - Title`` comment instead of a
    real file path because these are recommendations, not files on disk.
    M3U readers display the EXTINF metadata regardless; our own seed-file
    parser ignores comment lines.
    """
    with open(path, "w") as f:
        f.write("#EXTM3U\n")
        for e in entries:
            dur = e.duration if e.duration else -1
            f.write(f"#EXTINF:{dur},{e.artist} - {e.title}\n")
            f.write(f"# search:{e.artist} - {e.title}\n")


def write_jspf(entries: list[PlaylistEntry], path: str, title: str = "hifi recommendations"):
    """Write a JSPF (JSON Shareable Playlist Format) file."""
    tracks = []
    for e in entries:
        track: dict = {"creator": e.artist, "title": e.title}
        if e.mbid:
            track["identifier"] = [f"https://musicbrainz.org/recording/{e.mbid}"]
        if e.duration:
            track["duration"] = e.duration * 1000  # JSPF wants ms
        tracks.append(track)
    doc = {"playlist": {"title": title, "track": tracks}}
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)


def write(entries: list[PlaylistEntry], path: str):
    """Auto-dispatch by extension."""
    low = path.lower()
    if low.endswith(".jspf") or low.endswith(".json"):
        write_jspf(entries, path)
    else:
        write_m3u(entries, path)
