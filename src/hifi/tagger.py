import base64
import time
import urllib.request
import urllib.error
from typing import Any

import musicbrainzngs as mb
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover, AtomDataType
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC

from hifi.config import MUSICBRAINZ_CONFIDENCE_THRESHOLD, COVER_ART_SIZE

mb.set_useragent("hifi", "0.1.0", "https://github.com/Bioanalytica/hifi")

_last_mb_call = 0.0


def _rate_limit():
    global _last_mb_call
    elapsed = time.time() - _last_mb_call
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_mb_call = time.time()


def search_musicbrainz(artist: str, title: str) -> dict[str, str] | None:
    _rate_limit()
    try:
        results = mb.search_recordings(artist=artist, recording=title, limit=5)
    except Exception:
        return None

    recordings = results.get("recording-list", [])
    if not recordings:
        return None

    best = recordings[0]
    score = int(best.get("ext:score", 0))
    if score < MUSICBRAINZ_CONFIDENCE_THRESHOLD:
        return None

    artist_credit = best.get("artist-credit", [])
    mb_artist = artist_credit[0]["artist"]["name"] if artist_credit else artist

    releases = best.get("release-list", [])
    album = releases[0]["title"] if releases else None
    year = releases[0].get("date", "")[:4] if releases else None
    release_id = releases[0]["id"] if releases else None

    return {
        "recording_id": best["id"],
        "artist": mb_artist,
        "title": best["title"],
        "album": album,
        "year": year,
        "release_id": release_id,
    }


def fetch_cover_art(release_id: str) -> bytes | None:
    url = f"https://coverartarchive.org/release/{release_id}/front-{COVER_ART_SIZE}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return None


def embed_tags(file_path: str, title: str, artist: str,
               album: str | None = None, year: str | None = None,
               cover_data: bytes | None = None):
    ext = file_path.rsplit(".", 1)[-1].lower()

    if ext == "opus":
        _tag_opus(file_path, title, artist, album, year, cover_data)
    elif ext == "flac":
        _tag_flac(file_path, title, artist, album, year, cover_data)
    elif ext in ("m4a", "mp4"):
        _tag_m4a(file_path, title, artist, album, year, cover_data)
    elif ext == "mp3":
        _tag_mp3(file_path, title, artist, album, year, cover_data)
    elif ext == "ogg":
        _tag_vorbis(file_path, title, artist, album, year, cover_data)


def _tag_opus(path: str, title: str, artist: str,
              album: str | None, year: str | None, cover: bytes | None):
    audio = OggOpus(path)
    audio["title"] = title
    audio["artist"] = artist
    if album:
        audio["album"] = album
    if year:
        audio["date"] = year
    if cover:
        pic = Picture()
        pic.data = cover
        pic.type = 3
        pic.mime = "image/jpeg"
        audio["metadata_block_picture"] = [
            base64.b64encode(pic.write()).decode("ascii")
        ]
    audio.save()


def _tag_flac(path: str, title: str, artist: str,
              album: str | None, year: str | None, cover: bytes | None):
    audio = FLAC(path)
    audio["title"] = title
    audio["artist"] = artist
    if album:
        audio["album"] = album
    if year:
        audio["date"] = year
    if cover:
        pic = Picture()
        pic.data = cover
        pic.type = 3
        pic.mime = "image/jpeg"
        audio.add_picture(pic)
    audio.save()


def _tag_m4a(path: str, title: str, artist: str,
             album: str | None, year: str | None, cover: bytes | None):
    audio = MP4(path)
    audio["\xa9nam"] = [title]
    audio["\xa9ART"] = [artist]
    if album:
        audio["\xa9alb"] = [album]
    if year:
        audio["\xa9day"] = [year]
    if cover:
        audio["covr"] = [MP4Cover(cover, imageformat=AtomDataType.JPEG)]
    audio.save()


def _tag_mp3(path: str, title: str, artist: str,
             album: str | None, year: str | None, cover: bytes | None):
    try:
        audio = ID3(path)
    except Exception:
        audio = ID3()
    audio.add(TIT2(encoding=3, text=[title]))
    audio.add(TPE1(encoding=3, text=[artist]))
    if album:
        audio.add(TALB(encoding=3, text=[album]))
    if year:
        audio.add(TDRC(encoding=3, text=[year]))
    if cover:
        audio.add(APIC(encoding=3, mime="image/jpeg", type=3, data=cover))
    audio.save(path)


def _tag_vorbis(path: str, title: str, artist: str,
                album: str | None, year: str | None, cover: bytes | None):
    audio = OggVorbis(path)
    audio["title"] = title
    audio["artist"] = artist
    if album:
        audio["album"] = album
    if year:
        audio["date"] = year
    if cover:
        pic = Picture()
        pic.data = cover
        pic.type = 3
        pic.mime = "image/jpeg"
        audio["metadata_block_picture"] = [
            base64.b64encode(pic.write()).decode("ascii")
        ]
    audio.save()


def tag_file(file_path: str, artist: str, title: str,
             skip_musicbrainz: bool = False) -> dict[str, str | None]:
    mb_data = None
    cover_data = None
    final_artist = artist
    final_title = title
    final_album = None
    final_year = None
    mb_id = None

    if not skip_musicbrainz and artist and title:
        mb_data = search_musicbrainz(artist, title)

    if mb_data:
        final_artist = mb_data["artist"]
        final_title = mb_data["title"]
        final_album = mb_data.get("album")
        final_year = mb_data.get("year")
        mb_id = mb_data["recording_id"]

        if mb_data.get("release_id"):
            cover_data = fetch_cover_art(mb_data["release_id"])

    embed_tags(file_path, title=final_title, artist=final_artist,
               album=final_album, year=final_year, cover_data=cover_data)

    return {
        "artist": final_artist,
        "title": final_title,
        "album": final_album,
        "year": final_year,
        "musicbrainz_id": mb_id,
    }
