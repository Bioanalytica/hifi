"""Genre neighborhood expansion via ListenBrainz tag-similarity.

Given a genre tag like ``"future bass"``, return the local neighborhood
(future bass + co-occurring tags) so the recommend post-filter can
target a whole subgenre cluster instead of just one exact tag string.

Two views of the expansion:

- ``expand_genre`` returns a ``set[str]`` of every separator variant
  (``"future bass"``, ``"future-bass"``, ...) — the right shape for the
  set-intersection match in ``filter_picks_by_genre``.
- ``expand_genre_canonical`` returns a ``list[str]`` ordered by LB
  co-occurrence count, deduped and without separator variants — the
  right shape for forming a Troi LB-Radio prompt where artist-name and
  decade tags shouldn't crowd out genre tags.

v1 backend: LB Labs ``/tag-similarity/json`` (anonymous, real-time,
co-occurrence-based). Results are filtered by minimum co-occurrence
count, the recommender's default-exclude denylist, and a top-N cutoff,
then cached to ``~/.cache/hifi/genre_graph.json`` for 30 days.

v2 will add a Neo4j/Kùzu backend with curated MusicBrainz subgenre /
influenced-by / fusion-of edges; this module will then dispatch on a
``backend=`` knob. v1 callers don't need changes for that.
"""

import json
import logging
import os
import time

from hifi.listenbrainz import tag_similarity
from hifi.recommender import _DEFAULT_EXCLUDE_GENRES

log = logging.getLogger(__name__)

CACHE_PATH = os.path.expanduser("~/.cache/hifi/genre_graph.json")
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60


def _normalize_tag(tag: str) -> str:
    return tag.strip().lower()


def _tag_variants(tag: str) -> set[str]:
    """Return separator variants of ``tag``.

    MB tag data is user-submitted, so the same genre can appear as
    ``"future bass"`` or ``"future-bass"`` on different artists. The
    post-filter does exact lowercased string match, so we emit both
    forms whenever a tag has either separator. Single-word tags pass
    through unchanged.
    """
    norm = _normalize_tag(tag)
    if not norm:
        return set()
    variants = {norm}
    if " " in norm:
        variants.add(norm.replace(" ", "-"))
    if "-" in norm:
        variants.add(norm.replace("-", " "))
    return variants


def _cache_key(tag: str, top_n: int, min_count: int) -> str:
    return f"{_normalize_tag(tag)}|{top_n}|{min_count}"


def _load_cache() -> dict:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("genre_graph cache load failed: %s", e)
        return {}


def _save_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except OSError as e:
        log.warning("genre_graph cache save failed: %s", e)


def clear_cache() -> None:
    """Remove the cache file. For tests and a future ``--no-cache`` flag."""
    if os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)


def _expand_canonical(tag: str, top_n: int, min_count: int) -> list[str]:
    """Return ordered canonical tag list for ``tag``.

    1. Normalize the input.
    2. JSON cache hit on ``(tag, top_n, min_count)`` with 30-day TTL.
    3. Else hit LB Labs ``tag_similarity``.
    4. Drop entries with ``count < min_count`` (kills LB noise like
       ``"vancouver"``, ``"2010s"``, ``"brony"``, ``"remix"``).
    5. Drop entries in ``_DEFAULT_EXCLUDE_GENRES`` (so ``"future bass"``
       never pulls in ``"pop"``).
    6. Take top ``top_n`` survivors by count.
    7. Always include the input tag itself first.

    Returns a list of canonical (non-variant) tag strings in
    descending-count order; the input tag is always at index 0.
    """
    norm = _normalize_tag(tag)
    if not norm:
        return []

    cache = _load_cache()
    key = _cache_key(norm, top_n, min_count)
    entry = cache.get(key)
    if entry and (time.time() - entry.get("ts", 0)) < CACHE_TTL_SECONDS:
        return list(entry.get("canonical", []))

    similars = tag_similarity([norm]).get(norm, [])

    survivors: list[tuple[str, int]] = []
    for item in similars:
        sim = item.get("similar_tag", "")
        cnt = int(item.get("count", 0))
        if not sim or cnt < min_count:
            continue
        if sim in _DEFAULT_EXCLUDE_GENRES:
            continue
        if sim == norm:
            continue
        survivors.append((sim, cnt))
    survivors.sort(key=lambda p: -p[1])
    canonical = [norm] + [s[0] for s in survivors[:top_n]]

    # Build the variant set alongside so the cache is one-shot for both
    # callers and we never have to decide which to compute first.
    variants: set[str] = set()
    for t in canonical:
        variants |= _tag_variants(t)

    cache[key] = {
        "ts": time.time(),
        "canonical": canonical,
        "variants": sorted(variants),
    }
    _save_cache(cache)
    return canonical


def expand_genre(tag: str, top_n: int = 15,
                 min_count: int = 5) -> set[str]:
    """Expand a genre tag to its variant-inclusive neighborhood set.

    Used as the genre allowlist for ``filter_picks_by_genre``. Includes
    every separator variant of every kept tag so MB's mixed
    ``"future bass"`` / ``"future-bass"`` spellings both match.
    """
    canonical = _expand_canonical(tag, top_n=top_n, min_count=min_count)
    if not canonical:
        return set()
    # Re-read the cache entry rather than recomputing — _expand_canonical
    # already wrote the variant set so we just lift it.
    cache = _load_cache()
    key = _cache_key(_normalize_tag(tag), top_n, min_count)
    entry = cache.get(key) or {}
    cached_variants = entry.get("variants")
    if cached_variants:
        return set(cached_variants)
    out: set[str] = set()
    for t in canonical:
        out |= _tag_variants(t)
    return out


def expand_genre_canonical(tag: str, top_n: int = 15,
                           min_count: int = 5) -> list[str]:
    """Ordered canonical tag list (no separator variants).

    Use this when forming a Troi LB-Radio prompt: the order reflects LB
    co-occurrence count so the most-relevant neighbors land in the
    prompt's leading clauses, and there are no separator-variant
    duplicates to crowd out distinct genres.
    """
    return _expand_canonical(tag, top_n=top_n, min_count=min_count)


def expand_genres(tags: list[str], top_n: int = 15,
                  min_count: int = 5) -> set[str]:
    """Union of ``expand_genre`` across multiple tags."""
    out: set[str] = set()
    for t in tags:
        out |= expand_genre(t, top_n=top_n, min_count=min_count)
    return out


def expand_genres_canonical(tags: list[str], top_n: int = 15,
                            min_count: int = 5) -> list[str]:
    """Concat of ``expand_genre_canonical`` across multiple tags, deduped.

    Order: each input tag's expansion is appended in turn, preserving
    that expansion's count order and putting all of the first input's
    neighbors before the second's.
    """
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        for c in expand_genre_canonical(t, top_n=top_n, min_count=min_count):
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out
