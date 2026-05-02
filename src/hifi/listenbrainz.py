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
    LISTENBRAINZ_BASE,
    LISTENBRAINZ_LABS_BASE,
    LISTENBRAINZ_DEFAULT_ALGORITHM,
    LISTENBRAINZ_METADATA_CHUNK,
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
    """Read the user's LB token from env, accepting either spelling.

    LB doesn't standardize a name; ``LISTENBRAINZ_USER_TOKEN`` is the
    convention used by the official CLI and most docs, but some users
    have ``LISTENBRAINZ_TOKEN`` for brevity. We honor either, preferring
    the longer one when both are set.
    """
    return (os.environ.get("LISTENBRAINZ_USER_TOKEN")
            or os.environ.get("LISTENBRAINZ_TOKEN"))


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


def validate_token() -> dict | None:
    """Validate the configured LB user token via ``/1/validate-token``.

    Returns ``{"valid": bool, "user_name": str | None, "message": str}``
    on success (HTTP 200, regardless of validity), or ``None`` when the
    request itself failed (no token, network error, non-200 response).
    """
    tok = _token()
    if not tok:
        return None
    url = f"{LISTENBRAINZ_BASE}/validate-token"
    raw = _get_json(url)
    if not isinstance(raw, dict):
        return None
    return {
        "valid": bool(raw.get("valid")),
        "user_name": raw.get("user_name"),
        "message": raw.get("message", ""),
    }


def metadata_recording(mbids: list[str],
                       includes: tuple[str, ...] = ("artist", "tag"),
                       ) -> dict[str, dict]:
    """Bulk-resolve recording MBIDs via the LB *Core* API.

    Hits ``/1/metadata/recording/?recording_mbids=...&inc=artist+tag``,
    which is the production endpoint (not the flaky Labs research API).
    Returns inline tags directly so callers can skip per-artist MB
    lookups in the hot path of the genre filter.

    Returns ``{mbid: {artist_credit_name, recording_name, release_name,
    length, artist_mbids: list[str], inline_tags: set[str],
    canonical_recording_mbid: None}}``. The ``canonical_recording_mbid``
    field is always ``None`` because the Core API doesn't surface it;
    callers that need canonicalization should use ``recording_lookup``
    or ``lookup_with_retry`` instead.

    Auth-optional: if ``LISTENBRAINZ_TOKEN`` is set, it's attached as
    ``Authorization: Token ...`` (helps with future personalized
    endpoints; doesn't gate this anonymous one).
    """
    if not mbids:
        return {}
    inc = "+".join(includes) if includes else ""
    out: dict[str, dict] = {}
    for i in range(0, len(mbids), LISTENBRAINZ_METADATA_CHUNK):
        chunk = mbids[i:i + LISTENBRAINZ_METADATA_CHUNK]
        params = "recording_mbids=" + ",".join(chunk)
        if inc:
            params += "&inc=" + inc
        url = f"{LISTENBRAINZ_BASE}/metadata/recording/?{params}"
        raw = _get_json(url)
        if not isinstance(raw, dict):
            continue
        for mbid, info in raw.items():
            if not isinstance(info, dict):
                continue
            out[mbid] = _parse_metadata_recording(mbid, info)
    return out


def _parse_metadata_recording(mbid: str, info: dict) -> dict:
    artist_block = info.get("artist") or {}
    recording_block = info.get("recording") or {}
    release_block = info.get("release") or {}
    tag_block = info.get("tag") or {}

    artist_mbids: list[str] = []
    for a in artist_block.get("artists") or []:
        amb = a.get("artist_mbid")
        if amb and amb not in artist_mbids:
            artist_mbids.append(amb)

    inline_tags: set[str] = set()
    for entry in tag_block.get("artist") or []:
        t = (entry.get("tag") or "").strip().lower()
        if t:
            inline_tags.add(t)

    return {
        "artist_credit_name": artist_block.get("name"),
        "recording_name": recording_block.get("name"),
        "release_name": release_block.get("name"),
        "length": recording_block.get("length"),
        "canonical_recording_mbid": None,
        "artist_mbids": artist_mbids,
        "inline_tags": inline_tags,
    }


def canonicalize_mbids(mbids: list[str]) -> dict[str, str]:
    """Map input MBIDs -> their canonical recording MBIDs."""
    out: dict[str, str] = {}
    for input_mbid, info in lookup_with_retry(mbids).items():
        canon = info.get("canonical_recording_mbid")
        if canon:
            out[input_mbid] = canon
    return out
