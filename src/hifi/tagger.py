import base64
import re
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

from rapidfuzz import fuzz

from hifi.config import (
    COVER_ART_SIZE,
    MUSICBRAINZ_CONFIDENCE_THRESHOLD,
    MUSICBRAINZ_QUERY_SIMILARITY,
)

mb.set_useragent("hifi", "0.1.0", "https://github.com/Bioanalytica/hifi")

_last_mb_call = 0.0
_FEAT_RE = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)


def _primary_artist(s: str) -> str:
    """Strip ``feat. X`` / ``ft. X`` / ``featuring X`` clauses.

    MB often credits collab tracks to just the primary artist while the
    user's query carries the full feature credit, and vice versa. We
    compare without the feat clause to avoid false-rejecting legit hits.
    """
    return _FEAT_RE.sub("", s).strip()


def _rate_limit():
    global _last_mb_call
    elapsed = time.time() - _last_mb_call
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_mb_call = time.time()


# Release-group secondary types that disqualify a release from the
# "canonical studio album" tier. Compilations / live recordings / demos
# / soundtracks are the usual culprits behind bad album tags ("Greatest
# Hits 2017", "Live at Wembley", "Bandit Rock Most Wanted 2013").
# Mixtape/Street covers user-uploaded mixtapes that frequently surface
# as releases[0].
_EXCLUDED_SECONDARY_TYPES = frozenset({
    "Compilation", "Live", "Demo", "Soundtrack",
    "Mixtape/Street", "Interview", "Audiobook",
    "Remix", "DJ-mix",
})

# Tier-2 fallback only excludes the truly off-album types — used when no
# Tier-1 release exists (e.g., when a song was only ever released on a
# soundtrack or compilation).
_TIER2_EXCLUDED_SECONDARY = frozenset({"Live", "Demo"})


def _release_date_sort_key(release: dict) -> tuple[int, int, int]:
    """Sort key: earliest YYYY-MM-DD first, undated last."""
    date = (release.get("date") or "").strip()
    if not date:
        return (9999, 99, 99)
    parts = date.split("-")
    try:
        y = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 and parts[1] else 1
        d = int(parts[2]) if len(parts) > 2 and parts[2] else 1
        return (y, m, d)
    except (ValueError, IndexError):
        return (9999, 99, 99)


def _is_official_album(release: dict) -> bool:
    if release.get("status") != "Official":
        return False
    rg = release.get("release-group") or {}
    return (rg.get("primary-type") or rg.get("type")) == "Album"


def pick_canonical_release_with_tier(
    recording: dict,
) -> tuple[dict | None, int]:
    """Pick the canonical release for a single recording and report its tier.

    Tier 1: Official + primary-type=Album + no excluded secondary type.
    Tier 2: Official + primary-type=Album, allowing soundtracks /
            compilations but still rejecting Live/Demo.
    Tier 3: any Official release.
    Tier 4: anything in the list.
    Within each tier, earliest release date wins. Returns
    ``(None, 99)`` when the recording has no releases at all.
    """
    releases = recording.get("release-list") or []
    if not releases:
        return None, 99

    def secondaries(rel: dict) -> set[str]:
        rg = rel.get("release-group") or {}
        return set(rg.get("secondary-type-list") or [])

    tier1 = [r for r in releases
             if _is_official_album(r)
             and not (secondaries(r) & _EXCLUDED_SECONDARY_TYPES)]
    if tier1:
        return min(tier1, key=_release_date_sort_key), 1

    tier2 = [r for r in releases
             if _is_official_album(r)
             and not (secondaries(r) & _TIER2_EXCLUDED_SECONDARY)]
    if tier2:
        return min(tier2, key=_release_date_sort_key), 2

    tier3 = [r for r in releases if r.get("status") == "Official"]
    if tier3:
        return min(tier3, key=_release_date_sort_key), 3

    return min(releases, key=_release_date_sort_key), 4


def pick_canonical_release(recording: dict) -> dict | None:
    """Convenience wrapper that drops the tier and just returns the release."""
    release, _ = pick_canonical_release_with_tier(recording)
    return release


def pick_best_recording_and_release(
    recordings: list[dict],
    artist: str,
    title: str,
    score_floor: int,
) -> tuple[dict, dict] | None:
    """Walk top-scoring recordings, pick the one whose canonical release
    is at the best tier.

    MB's ``search_recordings`` often returns a *live performance* recording
    as the top hit alongside the studio recording (both ``ext:score=100``,
    different MBIDs). Walking only ``recordings[0]`` locks onto whichever
    came back first; if it happens to be the live version, every release
    in its list is a bootleg. By walking the top ~15 candidates and
    keeping the one with the lowest release tier, we surface the studio
    recording even when MB ranks the live version first.

    Within tier, the recording with the higher ``ext:score`` wins. Returns
    ``(recording, release)`` or ``None`` when nothing passes the
    similarity guards.
    """
    best: tuple[dict, dict] | None = None
    best_key: tuple | None = None
    for rec in recordings[:30]:
        try:
            score = int(rec.get("ext:score", 0))
        except (TypeError, ValueError):
            score = 0
        if score < score_floor:
            break  # Sorted desc; rest score lower too.
        if not _passes_similarity_guards(rec, artist, title):
            continue
        release, tier = pick_canonical_release_with_tier(rec)
        if release is None:
            continue
        # Ordering key: tier ascending (1 best), then release date
        # ascending (earlier = more canonical — beats a 2010 deluxe
        # edition with a 2003 original), then MB score descending as
        # a final tiebreak.
        key = (tier, _release_date_sort_key(release), -score)
        if best_key is None or key < best_key:
            best = (rec, release)
            best_key = key
    return best


def _passes_similarity_guards(
    recording: dict, artist: str, title: str,
) -> bool:
    """Check the MB hit against the user's query string.

    Same two checks as ``search_musicbrainz`` used to do inline:
    token-set ratio on combined "Artist Title" >= MUSICBRAINZ_QUERY_SIMILARITY,
    and a stricter primary-artist ratio >= 90 after ``feat.`` clauses
    are stripped. Reject hits that fail either.
    """
    artist_credit = recording.get("artist-credit") or []
    mb_artist = (artist_credit[0]["artist"]["name"]
                 if artist_credit else artist)
    sim = fuzz.token_set_ratio(
        f"{mb_artist} {recording.get('title', '')}".lower(),
        f"{artist} {title}".lower(),
    )
    if sim < MUSICBRAINZ_QUERY_SIMILARITY:
        return False
    artist_sim = fuzz.ratio(
        _primary_artist(mb_artist).lower(),
        _primary_artist(artist).lower(),
    )
    return artist_sim >= 90


def search_musicbrainz(artist: str, title: str) -> dict[str, Any] | None:
    _rate_limit()
    try:
        # Fetch a generous slice so the walker has room — for famous
        # tracks (Enter Sandman, Killing in the Name) MB carries
        # hundreds of recording rows for live performances, and the
        # studio recording can be well past index 10.
        results = mb.search_recordings(artist=artist, recording=title, limit=30)
    except Exception:
        return None

    recordings = results.get("recording-list", [])
    if not recordings:
        return None

    best_pair = pick_best_recording_and_release(
        recordings, artist, title, MUSICBRAINZ_CONFIDENCE_THRESHOLD,
    )
    if best_pair is None:
        return None
    best, release = best_pair

    artist_credit = best.get("artist-credit", [])
    mb_artist = artist_credit[0]["artist"]["name"] if artist_credit else artist

    # Similarity guards (token-set on full string + strict primary-artist
    # ratio) live inside pick_best_recording_and_release now, so by the
    # time we get here ``best`` is already known to match the query.

    album = release.get("title") if release else None
    year = (release.get("date") or "")[:4] if release else None
    release_id = release.get("id") if release else None
    release_group_id = ((release or {}).get("release-group") or {}).get("id")

    length_ms = best.get("length")
    duration_sec = int(length_ms) // 1000 if length_ms else None

    return {
        "recording_id": best["id"],
        "artist": mb_artist,
        "title": best["title"],
        "album": album,
        "year": year,
        "release_id": release_id,
        "release_group_id": release_group_id,
        "duration": duration_sec,
    }


def fetch_cover_art(release_id: str | None,
                    release_group_id: str | None = None) -> bytes | None:
    """Fetch front cover art bytes from Cover Art Archive.

    Prefers the release-group endpoint (more stable across regional
    editions and remasters), falls back to the specific release. Returns
    ``None`` when neither has art available.
    """
    urls: list[str] = []
    if release_group_id:
        urls.append(
            f"https://coverartarchive.org/release-group/{release_group_id}"
            f"/front-{COVER_ART_SIZE}")
    if release_id:
        urls.append(
            f"https://coverartarchive.org/release/{release_id}"
            f"/front-{COVER_ART_SIZE}")
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            continue
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
        # FLAC's add_picture appends; for clean overwrites (retag) we
        # need to clear existing pictures first so we don't end up with
        # two front covers embedded.
        audio.clear_pictures()
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
        # ID3 allows multiple APIC frames; on retag we replace any
        # existing cover art so we don't end up with a second image.
        audio.delall("APIC")
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

        if mb_data.get("release_id") or mb_data.get("release_group_id"):
            cover_data = fetch_cover_art(
                mb_data.get("release_id"),
                mb_data.get("release_group_id"),
            )

    embed_tags(file_path, title=final_title, artist=final_artist,
               album=final_album, year=final_year, cover_data=cover_data)

    return {
        "artist": final_artist,
        "title": final_title,
        "album": final_album,
        "year": final_year,
        "musicbrainz_id": mb_id,
    }
