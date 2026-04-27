"""Seed-to-playlist orchestrator built on ListenBrainz Labs.

Given a list of seeds (Artist - Title and optionally MBID), resolve to
canonical recording MBIDs, fetch similar recordings from the LB Labs
``similar-recordings`` endpoint, aggregate consensus across seeds, drop
duplicates and tracks already in the local hifi library, and return a
ranked list of picks.
"""

import logging
from dataclasses import dataclass, field

from hifi.db import Database
from hifi.library import Seed
from hifi.listenbrainz import (
    SimilarRec,
    canonicalize_mbids,
    similar_recordings,
)
from hifi.tagger import search_musicbrainz

log = logging.getLogger(__name__)


@dataclass
class Pick:
    artist: str
    title: str
    mbid: str
    score: float
    seed_count: int = 1
    seeds: list[str] = field(default_factory=list)


def resolve_seed_mbids(seeds: list[Seed], db: Database) -> list[Seed]:
    """Fill in missing MBIDs via DB cache, then MusicBrainz."""
    out: list[Seed] = []
    for s in seeds:
        if s.mbid:
            out.append(s)
            continue

        cached = db.get_by_artist_title(s.artist, s.title)
        if cached and cached["musicbrainz_id"]:
            out.append(Seed(
                artist=s.artist, title=s.title,
                mbid=cached["musicbrainz_id"],
                source_path=s.source_path,
            ))
            continue

        mb = search_musicbrainz(s.artist, s.title)
        if mb and mb.get("recording_id"):
            out.append(Seed(
                artist=mb.get("artist") or s.artist,
                title=mb.get("title") or s.title,
                mbid=mb["recording_id"],
                source_path=s.source_path,
            ))
        else:
            log.info("could not resolve MBID for: %s - %s", s.artist, s.title)
    return out


def aggregate_picks(similars: list[SimilarRec],
                    drop_mbids: set[str]) -> dict[str, Pick]:
    """Collapse similar-recording hits by MBID, summing scores across seeds."""
    by_mbid: dict[str, Pick] = {}
    for s in similars:
        if s.recording_mbid in drop_mbids:
            continue
        existing = by_mbid.get(s.recording_mbid)
        if existing:
            existing.score += s.score
            existing.seed_count += 1
            if s.seed_mbid and s.seed_mbid not in existing.seeds:
                existing.seeds.append(s.seed_mbid)
        else:
            by_mbid[s.recording_mbid] = Pick(
                artist=s.artist_credit_name or "",
                title=s.recording_name or "",
                mbid=s.recording_mbid,
                score=s.score,
                seed_count=1,
                seeds=[s.seed_mbid] if s.seed_mbid else [],
            )
    return by_mbid


def recommend(seeds: list[Seed], db: Database,
              limit: int = 30) -> list[Pick]:
    resolved = resolve_seed_mbids(seeds, db)
    if not resolved:
        log.warning("no seeds resolved to MBIDs; nothing to recommend")
        return []

    raw_mbids = [s.mbid for s in resolved if s.mbid]
    canon_map = canonicalize_mbids(raw_mbids)
    seed_mbids = list({canon_map[m] for m in raw_mbids if m in canon_map})
    if not seed_mbids:
        log.warning("could not canonicalize any seed MBIDs")
        return []

    similars = similar_recordings(seed_mbids)
    if not similars:
        log.warning("LB Labs returned no similar recordings")
        return []

    drop = set(raw_mbids) | set(seed_mbids) | db.get_all_mbids()
    by_mbid = aggregate_picks(similars, drop)

    picks = [p for p in by_mbid.values() if p.artist and p.title]
    picks.sort(key=lambda p: (-p.score, p.artist.lower(), p.title.lower()))
    return picks[:limit]


def troi_lb_radio(prompt: str, mode: str = "medium",
                  limit: int = 30) -> list[Pick]:
    """Run Troi's LB-Radio patch. Requires the optional `troi` extra."""
    try:
        from troi.patches.lb_radio import LBRadioPatch
    except ImportError:
        print("  troi is not installed. Install with: pip install 'hifi[troi]'")
        return []

    try:
        patch = LBRadioPatch({"prompt": prompt, "mode": mode})
        result = patch.generate()
    except Exception as e:
        log.warning("troi LB-Radio failed: %s", e)
        return []

    # Troi returns a Playlist object; pull out recordings.
    recordings = []
    playlists = getattr(result, "playlists", None) or []
    for pl in playlists:
        recordings.extend(getattr(pl, "recordings", []) or [])

    picks: list[Pick] = []
    for r in recordings[:limit]:
        mbid = getattr(r, "mbid", None) or ""
        artist_obj = getattr(r, "artist", None)
        artist = (
            getattr(artist_obj, "name", None)
            or getattr(r, "artist_credit_name", None)
            or ""
        )
        title = getattr(r, "name", None) or getattr(r, "title", None) or ""
        if not (artist and title):
            continue
        picks.append(Pick(artist=artist, title=title, mbid=mbid, score=0.0))
    return picks
