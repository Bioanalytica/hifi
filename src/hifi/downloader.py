import os
from typing import Any

import yt_dlp

from hifi.config import DEFAULT_OUTPUT_DIR, FILENAME_UNSAFE, PREFERRED_CODECS


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
    artist = info.get("artist") or info.get("uploader")
    title = info.get("track") or info.get("title")
    album = info.get("album")
    source = info.get("extractor_key")

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
