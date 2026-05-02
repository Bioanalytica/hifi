"""ListenBrainz Labs + Core API client.

Anonymous access works for the Labs endpoints we use. If
``LISTENBRAINZ_TOKEN`` is set in the environment, it's attached as an
``Authorization: Token ...`` header — currently a no-op for the Labs
endpoints, but lets us reuse this client for personalised Core endpoints
later (Daily Jams, Weekly Discovery) when an account exists.
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from hifi.config import (
    LISTENBRAINZ_LABS_BASE,
    LISTENBRAINZ_DEFAULT_ALGORITHM,
)

log = logging.getLogger(__name__)

_TIMEOUT = 15
_USER_AGENT = "hifi/0.1.0 (https://github.com/Bioanalytica/hifi)"


@dataclass
class SimilarRec:
    seed_mbid: str
    recording_mbid: str
    score: float
    artist_credit_name: str | None = None
    recording_name: str | None = None


def _token() -> str | None:
    return os.environ.get("LISTENBRAINZ_TOKEN")


def _post_json(url: str, body: Any) -> Any:
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    tok = _token()
    if tok:
        headers["Authorization"] = f"Token {tok}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log.warning("LB %s -> HTTP %s: %s", url, e.code, e.reason)
        return None
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        log.warning("LB %s -> %s", url, e)
        return None


def _get_json(url: str) -> Any:
    headers = {"User-Agent": _USER_AGENT}
    tok = _token()
    if tok:
        headers["Authorization"] = f"Token {tok}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log.warning("LB %s -> HTTP %s: %s", url, e.code, e.reason)
        return None
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        log.warning("LB %s -> %s", url, e)
        return None


def similar_recordings(seed_mbids: list[str],
                       algorithm: str | None = None,
                       count: int = 100) -> list[SimilarRec]:
    """Fetch similar recordings for one or more seeds.

    Returns a flat list of (seed_mbid, recording_mbid, score) entries.
    The seed_mbid is preserved so callers can do consensus aggregation.
    """
    if not seed_mbids:
        return []
    algo = algorithm or LISTENBRAINZ_DEFAULT_ALGORITHM
    body = [{"recording_mbids": seed_mbids, "algorithm": algo}]
    url = f"{LISTENBRAINZ_LABS_BASE}/similar-recordings/json"
    raw = _post_json(url, body)
    if not raw or not isinstance(raw, list):
        return []

    seed_set = set(seed_mbids)
    out: list[SimilarRec] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rec_mbid = item.get("recording_mbid")
        if not rec_mbid:
            continue
        # The endpoint surfaces 'reference_mbid' to tie back to which seed
        # produced this hit when multiple seeds were passed.
        ref = item.get("reference_mbid")
        seed = ref if ref in seed_set else (seed_mbids[0] if len(seed_mbids) == 1 else "")
        out.append(SimilarRec(
            seed_mbid=seed,
            recording_mbid=rec_mbid,
            score=float(item.get("score") or 0),
            artist_credit_name=item.get("artist_credit_name") or item.get("artist_name"),
            recording_name=item.get("recording_name") or item.get("name"),
        ))
    return out


def recording_lookup(mbids: list[str]) -> dict[str, dict]:
    """Resolve recording MBIDs to {artist_credit_name, recording_name, ...}.

    Returns a dict keyed by MBID. Missing MBIDs are simply absent.
    """
    if not mbids:
        return {}
    body = [{"recording_mbid": m} for m in mbids]
    url = f"{LISTENBRAINZ_LABS_BASE}/recording-mbid-lookup/json"
    raw = _post_json(url, body)
    if not raw or not isinstance(raw, list):
        return {}

    out: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        # Key by the input MBID; the endpoint also returns a possibly-different
        # canonical_recording_mbid we don't need.
        mbid = item.get("original_recording_mbid") or item.get("recording_mbid")
        if not mbid:
            continue
        artist_mbids = item.get("artist_credit_mbids") or []
        if not artist_mbids:
            for a in item.get("artists") or []:
                am = a.get("artist_mbid")
                if am:
                    artist_mbids.append(am)
        out[mbid] = {
            "artist_credit_name": item.get("artist_credit_name"),
            "recording_name": item.get("recording_name"),
            "release_name": item.get("release_name"),
            "length": item.get("length"),
            "canonical_recording_mbid": item.get("canonical_recording_mbid"),
            "artist_mbids": list(artist_mbids),
        }
    return out


def lookup_with_retry(mbids: list[str]) -> dict[str, dict]:
    """Resolve MBIDs to metadata via recording-mbid-lookup, with fallback.

    The Labs lookup endpoint currently 500s on certain inputs when batched.
    Try the batch first; for any MBIDs missing from the response, retry
    one at a time so a single bad MBID doesn't sink the whole query.
    """
    out = recording_lookup(mbids)
    missing = [m for m in mbids if m not in out]
    for m in missing:
        single = recording_lookup([m])
        if m in single:
            out[m] = single[m]
    return out


def tag_similarity(tags: list[str]) -> dict[str, list[dict]]:
    """Fetch co-occurrence-similar tags from LB Labs.

    POSTs ``[{"tag": t}, ...]`` to ``/tag-similarity/json``. The Labs
    endpoint returns a flat list of ``{similar_tag, count}`` entries
    *per query tag* — when a single tag is queried the response shape is
    a flat list, not a nested dict, so per-tag GET fallbacks are how we
    disambiguate which result belongs to which input.

    On batch failure (POST 5xx, empty response) we fall back to a GET
    per tag so a single bad tag doesn't sink the whole query.

    Returns ``{input_tag_lowercased: [{"similar_tag": str, "count": int},
    ...]}``. Missing tags simply absent.
    """
    if not tags:
        return {}
    norm = [t.strip().lower() for t in tags if t.strip()]
    if not norm:
        return {}
    url = f"{LISTENBRAINZ_LABS_BASE}/tag-similarity/json"

    out: dict[str, list[dict]] = {}
    if len(norm) > 1:
        body = [{"tag": t} for t in norm]
        raw = _post_json(url, body)
        # The Labs endpoint returns one flat list whose entries don't
        # carry the originating query tag, so a multi-tag batch can't be
        # unambiguously demuxed. Treat any batch hit as a hint and still
        # fall through to the per-tag GETs below; they're cheap enough
        # at 1-2 calls per recommend invocation.
        if not raw:
            log.debug("tag-similarity batch returned empty; per-tag fallback")

    for t in norm:
        single = _get_json(f"{url}?tag={urllib.parse.quote_plus(t)}")
        if not isinstance(single, list):
            continue
        clean: list[dict] = []
        for item in single:
            if not isinstance(item, dict):
                continue
            sim = item.get("similar_tag")
            cnt = item.get("count")
            if not sim or cnt is None:
                continue
            clean.append({"similar_tag": str(sim).strip().lower(),
                          "count": int(cnt)})
        out[t] = clean
    return out


def canonicalize_mbids(mbids: list[str]) -> dict[str, str]:
    """Map input MBIDs -> their canonical recording MBIDs."""
    out: dict[str, str] = {}
    for input_mbid, info in lookup_with_retry(mbids).items():
        canon = info.get("canonical_recording_mbid")
        if canon:
            out[input_mbid] = canon
    return out
