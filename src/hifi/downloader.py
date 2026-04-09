import os
import re
from typing import Any

import yt_dlp

from hifi.config import DEFAULT_OUTPUT_DIR, FILENAME_UNSAFE, PREFERRED_CODECS

# Noise words stripped before MusicBrainz search
_TITLE_NOISE = re.compile(
    r"\b(?:official\s+(?:music\s+)?video|official\s+audio|lyric\s+video"
    r"|lyrics?|audio|hd|hq|remaster(?:ed)?|ost|original\s+soundtrack"
    r"|full\s+version|extended)\b",
    re.IGNORECASE,
)
# All square brackets: typically noise on YouTube titles
_BRACKETS = re.compile(r"\[[^\]]*\]")
# Paren noise: (Official Video), (Lyrics), (Audio), etc.
_PAREN_NOISE = re.compile(r"\((?:official|lyric|audio|hd|hq|full)[^\)]*\)", re.IGNORECASE)
# Parenthesized artist: "Song Title (Artist Name)"
_PAREN_ARTIST = re.compile(r"\(([^)]+)\)\s*$")


def _parse_title_for_artist(title: str) -> tuple[str | None, str]:
    """Try to extract artist and clean title from a YouTube video title.

    Handles patterns like:
      - "Artist - Song Title"
      - "Song Title (Artist Name)"
      - "Game OST - Song Title (Artist Name)"
      - "Artist - Song Title (Official Video)"

    Returns (artist, cleaned_title). artist is None if not parseable.
    """
    # First strip all square bracket content like [Official Video], [Theme], etc.
    cleaned = _BRACKETS.sub("", title).strip()

    # Check for parenthesized artist at the end: "Song (Artist)"
    # But skip if the parens contain noise words
    paren_match = _PAREN_ARTIST.search(cleaned)
    paren_artist = None
    if paren_match:
        candidate = paren_match.group(1).strip()
        # Only treat as artist if it doesn't look like noise
        if not _TITLE_NOISE.search(candidate):
            paren_artist = candidate
            cleaned = cleaned[:paren_match.start()].strip()

    # Strip remaining paren noise like (Official Video)
    cleaned = _PAREN_NOISE.sub("", cleaned).strip()

    # Now try "Artist - Title" split (use first " - " delimiter)
    if " - " in cleaned:
        parts = cleaned.split(" - ", 1)
        left = parts[0].strip()
        right = parts[1].strip()

        # If we found a paren artist, it takes priority over the left side
        # because "Game OST - Song Title (Mick Gordon)" means:
        #   left = "Game OST" (not the artist)
        #   paren_artist = "Mick Gordon" (the real artist)
        #   right = "Song Title" (the track)
        if paren_artist:
            # Use the right side as title (it's closer to the track name)
            return paren_artist, _clean_title(right)
        else:
            return left, _clean_title(right)

    # No dash split, but we have a paren artist
    if paren_artist:
        return paren_artist, _clean_title(cleaned)

    # Nothing parseable
    return None, _clean_title(cleaned)


def _clean_title(title: str) -> str:
    """Remove noise words and extra whitespace from a title."""
    cleaned = _TITLE_NOISE.sub("", title)
    cleaned = _BRACKETS.sub("", cleaned)
    cleaned = _PAREN_NOISE.sub("", cleaned)
    # Collapse multiple spaces/dashes at edges
    cleaned = re.sub(r"\s*-\s*$", "", cleaned)
    cleaned = re.sub(r"^\s*-\s*", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def sanitize_filename(name: str) -> str:
    return name.strip().translate(FILENAME_UNSAFE)


def build_ydl_opts(output_dir: str = DEFAULT_OUTPUT_DIR,
                   preferred_format: str = "best") -> dict[str, Any]:
    codec = "opus" if preferred_format == "best" else preferred_format

    format_parts = [f"ba[acodec={c}]" for c in PREFERRED_CODECS]
    format_parts.extend(["ba", "best"])
    format_str = "/".join(format_parts)

    return {
        "format": format_str,
        "paths": {"home": output_dir},
        "outtmpl": {"default": "%(title)s.%(ext)s"},
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": codec,
            "preferredquality": "0",
        }],
        "quiet": True,
        "no_warnings": True,
        "noprogress": False,
    }


def extract_metadata(info: dict[str, Any]) -> dict[str, str | None]:
    # Prefer yt-dlp's structured fields (present for official music uploads)
    artist = info.get("artist")
    title = info.get("track")
    album = info.get("album")
    source = info.get("extractor_key")

    # If yt-dlp didn't provide structured artist/track, parse the video title
    if not artist or not title:
        raw_title = info.get("title", "")
        parsed_artist, parsed_title = _parse_title_for_artist(raw_title)

        if not artist:
            artist = parsed_artist or info.get("creator") or info.get("uploader")
        if not title:
            title = parsed_title or raw_title

    return {
        "artist": artist,
        "title": title,
        "album": album,
        "source": source,
        "video_id": info.get("id"),
        "webpage_url": info.get("webpage_url"),
    }


def download(url: str, output_dir: str = DEFAULT_OUTPUT_DIR,
             preferred_format: str = "best",
             progress_hook: Any = None) -> tuple[str, dict[str, str | None]]:
    opts = build_ydl_opts(output_dir, preferred_format)

    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        meta = extract_metadata(info)

        artist_part = sanitize_filename(meta["artist"]) if meta["artist"] else None
        title_part = sanitize_filename(meta["title"]) if meta["title"] else info.get("id", "unknown")

        if artist_part:
            final_name = f"{artist_part} - {title_part}"
        else:
            final_name = title_part

        ext = info.get("ext", "opus")
        raw_path = ydl.prepare_filename(info)
        base = os.path.splitext(raw_path)[0]
        downloaded_path = f"{base}.{ext}"

        final_path = os.path.join(output_dir, f"{final_name}.{ext}")

        if os.path.exists(downloaded_path) and downloaded_path != final_path:
            os.rename(downloaded_path, final_path)
        elif not os.path.exists(downloaded_path):
            for try_ext in ("opus", "flac", "m4a", "mp3", "ogg", "webm"):
                candidate = f"{base}.{try_ext}"
                if os.path.exists(candidate):
                    ext = try_ext
                    final_path = os.path.join(output_dir, f"{final_name}.{ext}")
                    os.rename(candidate, final_path)
                    break

        meta["ext"] = ext
        return final_path, meta
