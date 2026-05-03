"""Seed-to-playlist orchestrator built on ListenBrainz Labs.

Given a list of seeds (Artist - Title and optionally MBID), resolve to
canonical recording MBIDs, fetch similar recordings from the LB Labs
``similar-recordings`` endpoint, aggregate consensus across seeds, drop
duplicates and tracks already in the local hifi library, and return a
ranked list of picks. Optionally post-filters by genre tags derived
from the seeds' artists, so picks stay in the same musical neighborhood.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field

import musicbrainzngs as mb

from hifi.db import Database
from hifi.library import Seed, _normalize_artist_title
from hifi.listenbrainz import (
    SimilarRec,
    canonicalize_mbids,
    lookup_with_retry,
    metadata_recording,
    similar_recordings,
)
from hifi.tagger import search_musicbrainz

log = logging.getLogger(__name__)

_ARTIST_TAG_CACHE: dict[str, set[str]] = {}

# Tags that hard-disqualify a pick even when its artist also carries an
# allowlist match. Lots of EDM-crossover artists (Major Lazer, Calvin
# Harris, Mike Posner) are tagged both "edm" *and* "pop" in MusicBrainz,
# so an allowlist that includes "edm" would let pop crossovers through.
# This denylist runs before the allowlist check so any one of these wins.
_DEFAULT_EXCLUDE_GENRES = frozenset({
    # Pop family
    "pop", "dance-pop", "dance pop", "electropop", "electro-pop",
    "synthpop", "synth-pop", "synth pop",
    "alt-pop", "alt pop", "alternative pop", "indie pop",
    "art pop", "pop rock", "pop-rock",
    "k-pop", "j-pop", "kpop", "jpop", "k pop", "j pop",
    # Rap / hip-hop family (still lets "trap edm" / "future bass" through
    # because those specific tags don't appear here, "trap" alone is
    # ambiguous so we deliberately leave it off)
    "hip hop", "hip-hop", "hiphop", "rap", "pop rap", "boom bap",
    "conscious hip hop", "trap rap", "trap metal", "horrorcore",
    "alternative hip hop", "alternative-hip-hop",
    "contemporary rap", "contemporary-rap",
    "underground hip hop", "underground-hip-hop",
    "southern hip hop", "southern-hip-hop",
    # Rock / metal / punk (rare overlap with EDM seeds)
    "rock", "punk", "punk rock", "metal", "alternative rock",
    "alternative", "indie rock", "indie",
    # Off-genre umbrellas
    "country", "folk", "classical", "blues", "jazz",
    "soul", "r&b", "rnb", "contemporary r&b", "alternative r&b",
    "reggae", "ska", "latin",
    # Holiday / religious
    "christmas", "holiday", "gospel", "christian", "worship",
})

# Tags too broad / off-genre to feed into a Troi LB-Radio prompt. Each
# `tag:` clause is OR'd against the catalog, so a single bad tag like
# "pop" or even "electronic" pulls in Madonna-era dance-pop alongside
# the dubstep we actually wanted. Specific subgenres only.
_LB_RADIO_TAG_DENYLIST = frozenset({
    # Off-genre umbrellas
    "pop", "rock", "country", "folk", "jazz", "classical", "blues",
    "soul", "r&b", "rnb", "rap", "hip hop", "hip-hop", "hiphop",
    "reggae", "latin", "world", "indie", "alternative", "metal",
    "punk", "ska", "funk", "disco", "christmas", "holiday",
    # Production / format tags, not genres
    "vocal", "instrumental", "acoustic", "live", "remix", "single",
    "album", "music", "song", "songs", "studio",
    # Language tags
    "english", "english language", "spanish", "french", "german",
    "japanese", "italian", "portuguese", "korean",
    # Electronic umbrellas — too broad to use as a Troi tag clause.
    # These will still drive option 1's post-filter (where we want
    # broad-tag matching) but get stripped before the Troi prompt.
    "electronic", "electronica", "edm", "dance", "electro",
    "electropop", "electro-pop", "electro pop", "dance-pop",
    "synth-pop", "synthpop", "synth pop",
    # Region / language / decade descriptors, not genres
    "european", "american", "british", "uk", "us", "canadian",
    "regional", "international", "western", "eastern",
    "1980s", "1990s", "2000s", "2010s", "2020s",
    "80s", "90s", "00s", "10s", "20s",
})


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


def _artist_tags(artist_mbid: str) -> set[str]:
    """Fetch lowercased tag names for one MB artist, cached per process."""
    cached = _ARTIST_TAG_CACHE.get(artist_mbid)
    if cached is not None:
        return cached
    tags: set[str] = set()
    try:
        r = mb.get_artist_by_id(artist_mbid, includes=["tags"])
        for t in r.get("artist", {}).get("tag-list", []) or []:
            name = (t.get("name") or "").strip().lower()
            if name:
                tags.add(name)
    except Exception as e:
        log.debug("MB tag lookup failed for %s: %s", artist_mbid, e)
    _ARTIST_TAG_CACHE[artist_mbid] = tags
    return tags


def _tags_for_recording(recording_mbid: str,
                        meta: dict[str, dict],
                        mb_fallback: bool = False) -> set[str]:
    """Union of artist tags credited on a recording.

    Fast path: when the meta dict carries ``inline_tags`` (the LB Core
    API ``/1/metadata/recording`` endpoint returns these directly), we
    use them and skip the per-artist MB lookup entirely. Saves ~1s per
    pick versus the old slow path (musicbrainzngs is rate-limited at
    1 req/sec).

    Slow path: per-artist tag lookup via musicbrainzngs, optionally
    seeded by an MB recording lookup when LB gave us no artist info.
    Only reached when the caller passes legacy meta from the Labs
    ``recording-mbid-lookup`` endpoint.
    """
    info = meta.get(recording_mbid) or {}

    inline = info.get("inline_tags")
    if inline is not None:
        return set(inline)

    artist_mbids = list(info.get("artist_mbids") or [])
    if not artist_mbids and mb_fallback:
        try:
            r = mb.get_recording_by_id(recording_mbid,
                                       includes=["artist-credits"])
            for ac in r.get("recording", {}).get("artist-credit", []):
                if isinstance(ac, dict):
                    artist = ac.get("artist") or {}
                    aid = artist.get("id")
                    if aid:
                        artist_mbids.append(aid)
        except Exception as e:
            log.debug("MB recording fallback failed for %s: %s",
                      recording_mbid, e)
    out: set[str] = set()
    for amb in artist_mbids:
        out |= _artist_tags(amb)
    return out


def derive_genre_allowlist(seed_mbids: list[str],
                           meta: dict[str, dict]) -> set[str]:
    """Build a genre allowlist from seed-artist tag frequencies.

    Strategy: collect tag occurrences across every seed's artists, then
    keep tags that show up >= 2 times OR are in the top 10 by frequency.
    Returns the lowercased tag set the post-filter will match against.
    """
    counter: Counter[str] = Counter()
    for m in seed_mbids:
        counter.update(_tags_for_recording(m, meta))
    if not counter:
        return set()
    repeat = {tag for tag, n in counter.items() if n >= 2}
    top = {tag for tag, _ in counter.most_common(10)}
    return repeat | top


def filter_picks_by_owned(picks: list[Pick],
                          owned_mbids: set[str],
                          owned_titles: set[str]) -> list[Pick]:
    """Drop picks whose MBID OR normalized artist|title is already owned."""
    if not owned_mbids and not owned_titles:
        return picks
    out: list[Pick] = []
    for p in picks:
        if p.mbid and p.mbid in owned_mbids:
            continue
        if _normalize_artist_title(p.artist, p.title) in owned_titles:
            continue
        out.append(p)
    return out


def filter_picks_by_genre(picks: list[Pick], allowlist: set[str],
                          pick_meta: dict[str, dict],
                          strict: bool = False,
                          exclude: set[str] | None = None,
                          limit: int | None = None) -> list[Pick]:
    """Drop picks whose artist tags don't intersect ``allowlist``.

    Hard-rejects any pick whose tags intersect ``exclude`` (defaults to
    ``_DEFAULT_EXCLUDE_GENRES``) so EDM-crossover artists tagged both
    "edm" and "pop" don't slip through on the "edm" match.

    When LB Labs gave us no metadata for a pick we'd otherwise consider,
    we fall back to a direct MusicBrainz recording lookup so we don't
    drop legitimate picks just because LB had a transient outage. The
    fallback is rate-limited (1/sec via musicbrainzngs), so we walk
    picks in score order and stop once ``limit`` good picks land if
    given — keeps the slow path bounded to what we actually need.

    Lenient default: if a pick has no known tags after the fallback,
    keep it. Pass ``strict=True`` to drop those instead.
    """
    deny = _DEFAULT_EXCLUDE_GENRES if exclude is None else exclude
    if not allowlist and not deny:
        return picks

    sorted_picks = sorted(picks,
                          key=lambda p: (-p.score, p.artist.lower(),
                                         p.title.lower()))
    kept: list[Pick] = []
    for p in sorted_picks:
        if limit is not None and len(kept) >= limit:
            break
        tags = _tags_for_recording(p.mbid, pick_meta)
        if not tags:
            # LB had no artist info; ask MB directly. Slow but correct.
            tags = _tags_for_recording(p.mbid, pick_meta, mb_fallback=True)
        if tags and (tags & deny):
            continue
        if not tags:
            # No tag info at all — both LB Labs and MB came up empty.
            # When the caller has a denylist active they're explicit
            # about exclusions, so don't trust unknowns. Pure-allowlist
            # callers stay lenient.
            if strict or deny:
                continue
            kept.append(p)
            continue
        if not allowlist or (tags & allowlist):
            kept.append(p)
    return kept


def recommend(seeds: list[Seed], db: Database,
              limit: int = 30,
              genres: set[str] | None = None,
              filter_genre: bool = True,
              strict_genre: bool = False,
              exclude_genres: set[str] | None = None,
              owned_mbids: set[str] | None = None,
              owned_titles: set[str] | None = None) -> list[Pick]:
    resolved = resolve_seed_mbids(seeds, db)
    if not resolved:
        log.warning("no seeds resolved to MBIDs; nothing to recommend")
        return []

    raw_mbids = [s.mbid for s in resolved if s.mbid]
    # Two endpoints serve two needs:
    #  - Labs `recording-mbid-lookup` is the only place that returns
    #    canonical_recording_mbid (used to dedupe Album-Edit vs Original-
    #    Mix MBIDs before similar-recordings). It's research API and
    #    intermittently 500s; we tolerate that by falling back to the
    #    raw input MBIDs when canonicalization isn't available.
    #  - Core `metadata/recording` is the production endpoint that
    #    returns artist mbids + inline tags directly. It's stable and
    #    drives the genre allowlist derivation, so we want it even when
    #    Labs is down.
    seed_meta_labs = lookup_with_retry(raw_mbids)
    seed_meta_core = metadata_recording(raw_mbids)
    seed_meta: dict[str, dict] = {}
    for m in raw_mbids:
        info = dict(seed_meta_core.get(m, {}))
        labs = seed_meta_labs.get(m) or {}
        if labs.get("canonical_recording_mbid"):
            info["canonical_recording_mbid"] = labs["canonical_recording_mbid"]
        # Backfill artist_mbids from Labs when Core has none (rare).
        if not info.get("artist_mbids") and labs.get("artist_mbids"):
            info["artist_mbids"] = labs["artist_mbids"]
        seed_meta[m] = info
    canon_map = {
        m: info["canonical_recording_mbid"]
        for m, info in seed_meta.items()
        if info.get("canonical_recording_mbid")
    }
    seed_mbids = list({canon_map.get(m, m) for m in raw_mbids})
    if not seed_mbids:
        log.warning("no seed MBIDs to query")
        return []

    allowlist: set[str] = set()
    if filter_genre:
        if genres:
            allowlist = {g.strip().lower() for g in genres if g.strip()}
        else:
            allowlist = derive_genre_allowlist(raw_mbids, seed_meta)
        if allowlist:
            log.info("genre allowlist (%d): %s",
                     len(allowlist), sorted(allowlist))
        else:
            log.info("no genre allowlist derived; skipping filter")

    similars = similar_recordings(seed_mbids)
    if not similars:
        log.warning("LB Labs returned no similar recordings")
        return []

    drop = set(raw_mbids) | set(seed_mbids) | db.get_all_mbids()
    if owned_mbids:
        drop |= owned_mbids
    by_mbid = aggregate_picks(similars, drop)
    picks = [p for p in by_mbid.values() if p.artist and p.title]

    if owned_titles:
        before = len(picks)
        picks = filter_picks_by_owned(picks, set(), owned_titles)
        log.info("owned-dir title filter: kept %d of %d picks",
                 len(picks), before)

    if (allowlist or exclude_genres is not None) and picks:
        # Use the LB Core API /1/metadata/recording endpoint here — it
        # returns inline tags directly, so the genre filter doesn't have
        # to fan out to musicbrainzngs (1/sec rate-limit) per pick. Falls
        # back gracefully to MB direct lookup when Core returns nothing.
        pick_meta = metadata_recording([p.mbid for p in picks])
        before = len(picks)
        picks = filter_picks_by_genre(picks, allowlist, pick_meta,
                                       strict=strict_genre,
                                       exclude=exclude_genres,
                                       limit=limit)
        log.info("genre filter: kept %d of %d picks", len(picks), before)
        if not picks:
            log.warning("genre filter dropped every pick; "
                        "re-run with --no-genre-filter to bypass")

    picks.sort(key=lambda p: (-p.score, p.artist.lower(), p.title.lower()))
    return picks[:limit]


def _tag_to_clause(tag: str) -> str:
    """Format a single MB tag as a Troi LB-Radio ``tag:X`` clause.

    Troi's prompt grammar splits on whitespace, so multi-word tags like
    ``"future bass"`` are hyphenated to ``tag:future-bass``.
    """
    return "tag:" + tag.strip().lower().replace(" ", "-")


def _is_lb_radio_denied(tag: str) -> bool:
    """Check ``tag`` against the LB-Radio prompt denylist.

    The denylist mixes hyphen and space spellings; we accept either by
    swapping separators before lookup so a single denylist entry covers
    both forms.
    """
    norm = tag.strip().lower()
    if norm in _LB_RADIO_TAG_DENYLIST:
        return True
    if " " in norm and norm.replace(" ", "-") in _LB_RADIO_TAG_DENYLIST:
        return True
    if "-" in norm and norm.replace("-", " ") in _LB_RADIO_TAG_DENYLIST:
        return True
    return False


def _format_lb_radio_prompt(raw_mbids: list[str], meta: dict[str, dict],
                            max_tags: int = 4) -> str:
    """Format a Troi LB-Radio prompt from the seeds' specific genre tags.

    LB-Radio treats each ``tag:X`` clause as an OR filter, so umbrella
    tags like "electronic" or "pop" pull in everything tagged that way.
    We emit only the most-frequent *specific* subgenres from the seeds,
    dropping anything in the broad / off-genre / regional denylist.
    Multi-word tags are kept and hyphenated.
    """
    counter: Counter[str] = Counter()
    for m in raw_mbids:
        counter.update(_tags_for_recording(m, meta))
    parts: list[str] = []
    for tag, _ in counter.most_common():
        if _is_lb_radio_denied(tag):
            continue
        parts.append(_tag_to_clause(tag))
        if len(parts) >= max_tags:
            break
    return " ".join(parts)


def _format_lb_radio_prompt_from_tags(tags: list[str],
                                      max_tags: int = 8) -> str:
    """Format a Troi LB-Radio prompt from an ordered tag list.

    Used by genre-only mode (``--seed-genre``). The input is expected
    to be ordered by relevance (e.g. LB co-occurrence count, leading
    with the user's seed genre). We dedupe hyphen/space variants, drop
    umbrella / regional / decade / artist-name tags via the existing
    LB-Radio denylist, and emit the rest as ``tag:X`` clauses with
    multi-word tags hyphenated for Troi's whitespace-split grammar.
    """
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        spaced = tag.strip().lower().replace("-", " ")
        if not spaced or spaced in seen:
            continue
        seen.add(spaced)
        if _is_lb_radio_denied(spaced):
            continue
        out.append(spaced)
        if len(out) >= max_tags:
            break
    return " ".join(_tag_to_clause(c) for c in out)


def lb_radio_from_seeds(seeds: list[Seed], db: Database,
                        mode: str = "medium", limit: int = 30,
                        filter_genre: bool = True,
                        strict_genre: bool = False,
                        exclude_genres: set[str] | None = None,
                        owned_mbids: set[str] | None = None,
                        owned_titles: set[str] | None = None,
                        ) -> tuple[str, list[Pick]]:
    """Run Troi LB-Radio with a seed-derived prompt + genre post-filter.

    Returns ``(prompt, picks)`` so the caller can show the user which
    prompt was generated alongside the resulting tracks.
    """
    resolved = resolve_seed_mbids(seeds, db)
    raw_mbids = [s.mbid for s in resolved if s.mbid]
    if not raw_mbids:
        return "", []
    # Core API gives us inline tags directly; we don't need Labs
    # canonicalization for the LB-Radio-from-seeds path because it
    # builds a tag prompt rather than running similar-recordings.
    seed_meta = metadata_recording(raw_mbids)

    prompt = _format_lb_radio_prompt(raw_mbids, seed_meta)
    if not prompt:
        return "", []

    # Over-fetch so the genre + owned filters have room to drop without
    # leaving us under --limit.
    over_fetch = max(limit * 3, 50) if (filter_genre or owned_mbids
                                        or owned_titles) else limit
    picks = troi_lb_radio(prompt, mode, limit=over_fetch)
    if not picks:
        return prompt, []

    db_mbids = db.get_all_mbids()
    combined_owned = (owned_mbids or set()) | db_mbids
    if combined_owned or owned_titles:
        before = len(picks)
        picks = filter_picks_by_owned(picks, combined_owned,
                                      owned_titles or set())
        log.info("LB-Radio owned filter: kept %d of %d picks",
                 len(picks), before)

    if (filter_genre or exclude_genres is not None) and picks:
        allowlist = derive_genre_allowlist(raw_mbids, seed_meta) if filter_genre else set()
        pick_meta = metadata_recording([p.mbid for p in picks])
        before = len(picks)
        picks = filter_picks_by_genre(picks, allowlist, pick_meta,
                                      strict=strict_genre,
                                      exclude=exclude_genres,
                                      limit=limit)
        log.info("LB-Radio genre filter: kept %d of %d picks",
                 len(picks), before)

    return prompt, picks[:limit]


def lb_radio_from_genres(canonical_tags: list[str],
                         allowlist_variants: set[str],
                         db: Database,
                         mode: str = "medium", limit: int = 30,
                         max_prompt_tags: int = 8,
                         strict_genre: bool = False,
                         exclude_genres: set[str] | None = None,
                         owned_mbids: set[str] | None = None,
                         owned_titles: set[str] | None = None,
                         ) -> tuple[str, list[Pick]]:
    """Run Troi LB-Radio with a prompt built from a precomputed tag list.

    Used by ``--seed-genre`` (genre-only mode):

    - ``canonical_tags`` is the relevance-ordered list (most-relevant
      first) used to form the Troi prompt — the user's seed genre at
      index 0, then LB co-occurrence neighbors. Order matters here so
      the prompt's leading clauses are the most-relevant tags.
    - ``allowlist_variants`` is the variant-inclusive set used to
      post-filter Troi's output, catching picks tagged either with
      spaces or hyphens.

    Returns ``(prompt, picks)``.
    """
    prompt = _format_lb_radio_prompt_from_tags(
        canonical_tags, max_tags=max_prompt_tags)
    if not prompt:
        return "", []

    # Genre-only mode is the most filter-heavy path: large libraries
    # eat 80%+ of Troi's output via owned-dir dedup, and the genre
    # filter then trims more on top. Over-fetch aggressively so the
    # final picks list isn't a single track.
    over_fetch = max(limit * 10, 200)
    picks = troi_lb_radio(prompt, mode, limit=over_fetch)
    if not picks:
        return prompt, []

    db_mbids = db.get_all_mbids()
    combined_owned = (owned_mbids or set()) | db_mbids
    if combined_owned or owned_titles:
        before = len(picks)
        picks = filter_picks_by_owned(picks, combined_owned,
                                      owned_titles or set())
        log.info("LB-Radio owned filter: kept %d of %d picks",
                 len(picks), before)

    if (allowlist_variants or exclude_genres is not None) and picks:
        pick_meta = metadata_recording([p.mbid for p in picks])
        before = len(picks)
        picks = filter_picks_by_genre(picks, allowlist_variants, pick_meta,
                                      strict=strict_genre,
                                      exclude=exclude_genres,
                                      limit=limit)
        log.info("LB-Radio genre filter: kept %d of %d picks",
                 len(picks), before)

    return prompt, picks[:limit]


def troi_lb_radio(prompt: str, mode: str = "medium",
                  limit: int = 30) -> list[Pick]:
    """Run Troi's LB-Radio patch. Requires the optional `troi` extra."""
    try:
        from troi.patches.lb_radio import LBRadioPatch
    except ImportError:
        print("  troi is not installed. Install with: uv sync --extra troi")
        return []

    try:
        patch = LBRadioPatch({
            "prompt": prompt,
            "mode": mode,
            "quiet": True,
            "min_recordings": 1,
        })
        result = patch.generate_playlist()
    except Exception as e:
        log.warning("troi LB-Radio failed: %s", e)
        return []

    recordings = []
    for pl in getattr(result, "playlists", None) or []:
        recordings.extend(getattr(pl, "recordings", []) or [])

    picks: list[Pick] = []
    for r in recordings[:limit]:
        mbid = getattr(r, "mbid", None) or ""
        ac = getattr(r, "artist_credit", None)
        artist = (
            getattr(ac, "name", None)
            or getattr(r, "artist_credit_name", None)
            or ""
        )
        title = getattr(r, "name", None) or getattr(r, "title", None) or ""
        if not (artist and title):
            continue
        picks.append(Pick(artist=artist, title=title, mbid=mbid, score=0.0))
    return picks
