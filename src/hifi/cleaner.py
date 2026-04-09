import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from hifi.config import TRACKING_PARAMS

_YT_SHORT_RE = re.compile(r"^https?://youtu\.be/([A-Za-z0-9_-]+)")
_YT_SHORTS_RE = re.compile(r"^https?://(?:www\.)?youtube\.com/shorts/([A-Za-z0-9_-]+)")


def _is_youtube(host: str) -> bool:
    return host in (
        "youtube.com", "www.youtube.com",
        "music.youtube.com", "m.youtube.com",
    )


def _normalize_youtube(url: str) -> str | None:
    m = _YT_SHORT_RE.match(url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"

    m = _YT_SHORTS_RE.match(url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"

    parsed = urlparse(url)
    if parsed.hostname == "music.youtube.com":
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return f"https://www.youtube.com/watch?v={qs['v'][0]}"

    return None


def _strip_params(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {
        k: v for k, v in qs.items()
        if k not in TRACKING_PARAMS and not k.startswith("utm_")
    }
    new_query = urlencode(filtered, doseq=True)
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        "",
    ))


def clean_url(url: str) -> str:
    url = url.strip()

    normalized = _normalize_youtube(url)
    if normalized is not None:
        return _strip_params(normalized)

    return _strip_params(url)
