"""YouTube search + candidate ranking for 'Artist - Title' queries.

Given a text query like "Oceanlab - Satellite (Arkasia Remix)", search YouTube
and pick the candidate most likely to be a canonical, high-quality source.

Since YouTube re-encodes every upload to similar opus/AAC tiers, "quality" here
means source-material quality. We proxy it with: uploader channel type, duration
match to MusicBrainz canonical length, title similarity, view count, and
negative keyword filters for live/cover/nightcore/etc.
"""

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any

import yt_dlp
from rapidfuzz import fuzz

log = logging.getLogger(__name__)

DEFAULT_SEARCH_COUNT = 15
LLM_TIEBREAK_TOP_N = 5
LLM_TIEBREAK_MARGIN = 1.5
LLM_MODEL = "claude-haiku-4-5-20251001"

_NEGATIVE_KEYWORDS = (
    "live", "cover", "karaoke", "8d audio", "8d",
    "nightcore", "sped up", "slowed", "reverb",
    "instrumental", "acapella", "a cappella",
    "fanmake", "fan made", "fan-made", "fanmade",
    "with lyrics", "lyrics video",
)
_POSITIVE_KEYWORDS = ("hq", "320", "flac", "lossless", "hi-fi", "hifi")
_LABEL_TOKENS = (
    "records", "recordings", "entertainment",
    "anjunabeats", "anjunadeep", "monstercat", "ultra",
    "armada", "spinnin", "mau5trap", "revealed", "protocol",
    "astralwerks", "ninja tune", "warp", "defected",
)
_VEVO_SUFFIX = re.compile(r"vevo$", re.IGNORECASE)
_TOPIC_SUFFIX = re.compile(r"\s*-\s*topic$", re.IGNORECASE)
_OFFICIAL_RE = re.compile(r"\bofficial\b", re.IGNORECASE)
_REMASTERED_RE = re.compile(r"\bremaster(?:ed)?\b", re.IGNORECASE)
_REMIX_CLAUSE_RE = re.compile(r"\(\s*([^()]+?)\s+remix\s*\)", re.IGNORECASE)


@dataclass
class Candidate:
    video_id: str
    title: str
    uploader: str
    duration: int | None
    view_count: int | None
    url: str
    channel: str | None = None
    channel_id: str | None = None
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)


def search_candidates(artist: str, title: str,
                      n: int = DEFAULT_SEARCH_COUNT) -> list[Candidate]:
    """Run yt-dlp YouTube search, return flat candidate metadata."""
    query = f"{artist} {title}" if artist else title
    search_url = f"ytsearch{n}:{query}"

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(search_url, download=False)

    entries = info.get("entries", []) if info else []
    candidates: list[Candidate] = []
    for e in entries:
        if not e:
            continue
        vid = e.get("id")
        if not vid:
            continue
        dur = e.get("duration")
        duration = int(dur) if dur else None
        views = e.get("view_count")
        view_count = int(views) if views else None
        candidates.append(Candidate(
            video_id=vid,
            title=e.get("title") or "",
            uploader=e.get("uploader") or e.get("channel") or "",
            duration=duration,
            view_count=view_count,
            url=e.get("url") or f"https://www.youtube.com/watch?v={vid}",
            channel=e.get("channel"),
            channel_id=e.get("channel_id"),
        ))
    return candidates


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(n in low for n in needles)


def _uploader_score(uploader: str, artist: str, remixer: str | None) -> float:
    """Boost official-looking uploaders. For remixes, also boost remixer channel."""
    if not uploader:
        return 0.0
    up = uploader.strip()
    low = up.lower()
    score = 0.0

    if _TOPIC_SUFFIX.search(up):
        score += 3.0
        if artist and artist.lower() in low:
            score += 0.5

    if _VEVO_SUFFIX.search(up):
        score += 2.5

    if artist and low == artist.lower():
        score += 2.5

    if _OFFICIAL_RE.search(up):
        score += 2.0

    if remixer and remixer.lower() in low:
        score += 1.5

    if any(tok in low for tok in _LABEL_TOKENS):
        score += 1.5

    return score


def _duration_score(cand_duration: int | None, mb_duration: int | None) -> float:
    """Linear falloff: perfect = 2.0, +/- 30s = 0, no ref = 0."""
    if not cand_duration or not mb_duration:
        return 0.0
    delta = abs(cand_duration - mb_duration)
    if delta > 30:
        return 0.0
    return 2.0 * (1.0 - delta / 30.0)


def _title_score(cand_title: str, query: str) -> float:
    if not cand_title or not query:
        return 0.0
    # Blend strict Levenshtein with token-sort (order-invariant but not
    # bag-of-words). token_set would treat different remixes as equivalent.
    strict = fuzz.ratio(cand_title, query)
    sorted_ratio = fuzz.token_sort_ratio(cand_title, query)
    return 2.0 * max(strict, sorted_ratio * 0.9) / 100.0


def _remix_match_score(cand_title: str, remixer: str | None) -> float:
    """If query specified a remixer, penalize candidates that don't mention them."""
    if not remixer:
        return 0.0
    if remixer.lower() in cand_title.lower():
        return 0.0
    return -4.0


def _views_score(view_count: int | None) -> float:
    if not view_count or view_count < 10:
        return 0.0
    return min(1.0, math.log10(view_count) / 7.0)


def _keyword_scores(cand_title: str, query: str) -> tuple[float, dict[str, float]]:
    breakdown: dict[str, float] = {}
    total = 0.0
    cand_low = cand_title.lower()
    query_low = query.lower()

    negative_hits = [kw for kw in _NEGATIVE_KEYWORDS
                     if kw in cand_low and kw not in query_low]
    if negative_hits:
        penalty = -5.0 * len(negative_hits)
        total += penalty
        breakdown["negative_keywords"] = penalty

    positive_hits = [kw for kw in _POSITIVE_KEYWORDS if kw in cand_low]
    if positive_hits:
        boost = 0.3 * len(positive_hits)
        total += boost
        breakdown["positive_keywords"] = boost

    if _REMASTERED_RE.search(cand_title):
        total += 0.8
        breakdown["remastered"] = 0.8

    return total, breakdown


def _extract_remixer(query: str) -> str | None:
    m = _REMIX_CLAUSE_RE.search(query)
    return m.group(1).strip() if m else None


def score_candidate(c: Candidate, artist: str, title: str,
                    mb_duration: int | None) -> Candidate:
    query = f"{artist} - {title}" if artist else title
    remixer = _extract_remixer(title)

    uploader = _uploader_score(c.uploader, artist, remixer)
    duration = _duration_score(c.duration, mb_duration)
    title_sim = _title_score(c.title, query)
    remix_match = _remix_match_score(c.title, remixer)
    views = _views_score(c.view_count)
    kw_total, kw_breakdown = _keyword_scores(c.title, query)

    c.score_breakdown = {
        "uploader": uploader,
        "duration": duration,
        "title_sim": title_sim,
        "remix_match": remix_match,
        "views": views,
        **kw_breakdown,
    }
    c.score = uploader + duration + title_sim + remix_match + views + kw_total
    return c


def rank_candidates(candidates: list[Candidate], artist: str, title: str,
                    mb_duration: int | None = None) -> list[Candidate]:
    scored = [score_candidate(c, artist, title, mb_duration) for c in candidates]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


def llm_tiebreak(candidates: list[Candidate], artist: str, title: str) -> int | None:
    """Ask Claude Haiku to pick the most canonical upload index.
    Returns None if API unavailable or call fails."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.info("llm_tiebreak: ANTHROPIC_API_KEY not set, skipping")
        return None

    try:
        import anthropic
    except ImportError:
        log.info("llm_tiebreak: anthropic SDK not installed (pip install 'hifi[llm]')")
        return None

    lines = []
    for i, c in enumerate(candidates):
        dur_min = (c.duration or 0) // 60
        dur_sec = (c.duration or 0) % 60
        views = f"{c.view_count:,}" if c.view_count else "?"
        lines.append(
            f"[{i}] title={c.title!r} uploader={c.uploader!r} "
            f"duration={dur_min}:{dur_sec:02d} views={views}"
        )
    candidate_block = "\n".join(lines)

    query = f"{artist} - {title}" if artist else title
    prompt = (
        f"I'm searching YouTube for a canonical, high-quality upload of:\n"
        f"  {query}\n\n"
        f"Here are the top candidates (index in brackets):\n"
        f"{candidate_block}\n\n"
        f"Pick the ONE index most likely to be the canonical source. "
        f"Prefer official artist channels, '<Artist> - Topic' auto-uploads, "
        f"and label channels (e.g. Anjunabeats). Avoid covers, live versions, "
        f"nightcore, and hobbyist compilations unless the query asks for them. "
        f"Reply with just the index number, nothing else."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=LLM_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip() if resp.content else ""
        m = re.search(r"\d+", text)
        if not m:
            log.warning(f"llm_tiebreak: unparseable response {text!r}")
            return None
        idx = int(m.group(0))
        if idx < 0 or idx >= len(candidates):
            log.warning(f"llm_tiebreak: out-of-range index {idx}")
            return None
        return idx
    except Exception as e:
        log.warning(f"llm_tiebreak: API call failed: {e}")
        return None


@dataclass
class PickResult:
    winner: Candidate
    ranked: list[Candidate]
    strategy: str  # "heuristic" or "llm_tiebreak"


def find_best(artist: str, title: str, mb_duration: int | None = None,
              n: int = DEFAULT_SEARCH_COUNT) -> PickResult | None:
    """End-to-end: search, rank, tiebreak, return the winner + full ranking."""
    candidates = search_candidates(artist, title, n=n)
    if not candidates:
        return None

    ranked = rank_candidates(candidates, artist, title, mb_duration)
    top = ranked[0]
    strategy = "heuristic"

    if len(ranked) >= 2 and (top.score - ranked[1].score) < LLM_TIEBREAK_MARGIN:
        llm_pick = llm_tiebreak(ranked[:LLM_TIEBREAK_TOP_N], artist, title)
        if llm_pick is not None:
            top = ranked[llm_pick]
            strategy = "llm_tiebreak"

    return PickResult(winner=top, ranked=ranked, strategy=strategy)
