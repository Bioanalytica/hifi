# hifi Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI tool that downloads highest-quality audio from yt-dlp-supported sites, tags via MusicBrainz, and tracks history in SQLite.

**Architecture:** Single Python package (`src/hifi/`) with 6 modules: cli, config, db, cleaner, downloader, tagger. Uses yt-dlp as a Python library for audio extraction and metadata. SQLite for dedup/history/retry. MusicBrainz + mutagen for tagging and album art.

**Tech Stack:** Python 3.12+, uv, yt-dlp, musicbrainzngs, mutagen, ffmpeg (system), SQLite3 (stdlib)

---

## File Structure

```
~/tools/hifi/
  pyproject.toml              # project metadata, dependencies, [project.scripts] entry point
  src/
    hifi/
      __init__.py             # version string
      config.py               # default settings (output dir, formats, DB path, thresholds)
      db.py                   # SQLite schema init, CRUD, dedup check, retry query, status report
      cleaner.py              # URL param stripping, YouTube normalization
      downloader.py           # yt-dlp wrapper, format selection, progress hooks, metadata extraction
      tagger.py               # MusicBrainz search, Cover Art Archive fetch, mutagen tag embedding
      cli.py                  # argparse, orchestration pipeline, --retry/--status/--dry-run
  tests/
    __init__.py
    test_config.py
    test_db.py
    test_cleaner.py
    test_downloader.py
    test_tagger.py
    test_cli.py
```

---

### Task 1: Project scaffolding with uv

**Files:**
- Create: `pyproject.toml`
- Create: `src/hifi/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Initialize uv project**

```bash
cd ~/tools/hifi
uv init --lib --name hifi
```

This creates `pyproject.toml` and `src/hifi/__init__.py`. If uv created a `hello.py` or other sample file, delete it.

- [ ] **Step 2: Edit pyproject.toml**

Replace the contents of `pyproject.toml` with:

```toml
[project]
name = "hifi"
version = "0.1.0"
description = "High-fidelity audio downloader with MusicBrainz tagging"
requires-python = ">=3.12"
dependencies = [
    "yt-dlp>=2025.0.0",
    "musicbrainzngs>=0.7",
    "mutagen>=1.47",
]

[project.scripts]
hifi = "hifi.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/hifi"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Set __init__.py version**

Write `src/hifi/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Create tests package**

```bash
touch ~/tools/hifi/tests/__init__.py
```

- [ ] **Step 5: Install dependencies and verify**

```bash
cd ~/tools/hifi
uv sync
uv run python -c "import hifi; print(hifi.__version__)"
```

Expected: `0.1.0`

- [ ] **Step 6: Add pytest dev dependency**

```bash
cd ~/tools/hifi
uv add --dev pytest
```

- [ ] **Step 7: Commit**

```bash
cd ~/tools/hifi
git add pyproject.toml src/ tests/ uv.lock
git commit -m "feat: scaffold hifi project with uv"
```

---

### Task 2: config module

**Files:**
- Create: `src/hifi/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write test for config defaults**

Write `tests/test_config.py`:

```python
import os
from hifi.config import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_FORMAT,
    DB_PATH,
    MUSICBRAINZ_CONFIDENCE_THRESHOLD,
    COVER_ART_SIZE,
    PREFERRED_CODECS,
)


def test_output_dir_expands_home():
    assert DEFAULT_OUTPUT_DIR == os.path.expanduser("~/Music")


def test_default_format():
    assert DEFAULT_FORMAT == "best"


def test_db_path_expands_home():
    assert DB_PATH == os.path.expanduser("~/tools/hifi/hifi.db")


def test_confidence_threshold_in_range():
    assert 0 <= MUSICBRAINZ_CONFIDENCE_THRESHOLD <= 100


def test_cover_art_size():
    assert COVER_ART_SIZE == 500


def test_preferred_codecs_order():
    assert PREFERRED_CODECS == ("opus", "flac", "vorbis")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/tools/hifi && uv run pytest tests/test_config.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'hifi.config'`

- [ ] **Step 3: Write config.py**

Write `src/hifi/config.py`:

```python
import os

DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Music")
DEFAULT_FORMAT = "best"
DB_PATH = os.path.expanduser("~/tools/hifi/hifi.db")
MUSICBRAINZ_CONFIDENCE_THRESHOLD = 80
COVER_ART_SIZE = 500
PREFERRED_CODECS = ("opus", "flac", "vorbis")

TRACKING_PARAMS = frozenset({
    "si", "feature", "list", "index", "t", "pp",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "affiliate", "ref", "fbclid", "gclid", "region",
})

FILENAME_UNSAFE = str.maketrans({
    "/": "_", "\\": "_", ":": "_", "*": "_",
    "?": "_", '"': "_", "<": "_", ">": "_", "|": "_",
})
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ~/tools/hifi && uv run pytest tests/test_config.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/tools/hifi
git add src/hifi/config.py tests/test_config.py
git commit -m "feat: add config module with defaults"
```

---

### Task 3: db module (SQLite schema, CRUD, dedup, retry, status)

**Files:**
- Create: `src/hifi/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write tests for db module**

Write `tests/test_db.py`:

```python
import os
import sqlite3
import tempfile

import pytest

from hifi.db import Database


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    yield database
    database.close()


def test_schema_created(db):
    """Table exists after init."""
    cursor = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='downloads'"
    )
    assert cursor.fetchone() is not None


def test_add_and_get(db):
    row_id = db.add(url="https://youtube.com/watch?v=abc", original_url="https://youtube.com/watch?v=abc&si=xyz")
    row = db.get_by_url("https://youtube.com/watch?v=abc")
    assert row is not None
    assert row["id"] == row_id
    assert row["status"] == "pending"
    assert row["original_url"] == "https://youtube.com/watch?v=abc&si=xyz"


def test_duplicate_url_rejected(db):
    db.add(url="https://youtube.com/watch?v=abc")
    duplicate_id = db.add(url="https://youtube.com/watch?v=abc")
    assert duplicate_id is None


def test_update_status(db):
    row_id = db.add(url="https://example.com/1")
    db.update_status(row_id, "downloading")
    row = db.get_by_url("https://example.com/1")
    assert row["status"] == "downloading"


def test_mark_complete(db):
    row_id = db.add(url="https://example.com/1")
    db.mark_complete(row_id, output_path="/tmp/song.opus", fmt="opus",
                     title="Song", artist="Artist", album="Album",
                     musicbrainz_id="mb-123")
    row = db.get_by_url("https://example.com/1")
    assert row["status"] == "complete"
    assert row["output_path"] == "/tmp/song.opus"
    assert row["artist"] == "Artist"
    assert row["completed_at"] is not None


def test_mark_failed(db):
    row_id = db.add(url="https://example.com/1")
    db.mark_failed(row_id, "Connection timeout")
    row = db.get_by_url("https://example.com/1")
    assert row["status"] == "failed"
    assert row["error"] == "Connection timeout"
    assert row["attempts"] == 1


def test_mark_failed_increments_attempts(db):
    row_id = db.add(url="https://example.com/1")
    db.mark_failed(row_id, "err1")
    db.mark_failed(row_id, "err2")
    row = db.get_by_url("https://example.com/1")
    assert row["attempts"] == 2
    assert row["error"] == "err2"


def test_get_failed(db):
    id1 = db.add(url="https://example.com/1")
    id2 = db.add(url="https://example.com/2")
    id3 = db.add(url="https://example.com/3")
    db.mark_failed(id1, "err")
    db.mark_complete(id2, output_path="/tmp/x.opus", fmt="opus",
                     title="X", artist="A")
    db.mark_failed(id3, "err")
    failed = db.get_failed()
    assert len(failed) == 2
    urls = {r["url"] for r in failed}
    assert urls == {"https://example.com/1", "https://example.com/3"}


def test_get_stats(db):
    id1 = db.add(url="https://example.com/1")
    id2 = db.add(url="https://example.com/2")
    id3 = db.add(url="https://example.com/3")
    db.mark_complete(id1, output_path="/tmp/a.opus", fmt="opus",
                     title="A", artist="A")
    db.mark_failed(id2, "err")
    stats = db.get_stats()
    assert stats["complete"] == 1
    assert stats["failed"] == 1
    assert stats["pending"] == 1
    assert stats["total"] == 3


def test_is_duplicate(db):
    assert db.is_duplicate("https://example.com/1") is False
    db.add(url="https://example.com/1")
    assert db.is_duplicate("https://example.com/1") is True


def test_reset_for_retry(db):
    row_id = db.add(url="https://example.com/1")
    db.mark_failed(row_id, "err")
    db.reset_for_retry(row_id)
    row = db.get_by_url("https://example.com/1")
    assert row["status"] == "pending"
    assert row["error"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/tools/hifi && uv run pytest tests/test_db.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'hifi.db'`

- [ ] **Step 3: Write db.py**

Write `src/hifi/db.py`:

```python
import sqlite3
from datetime import datetime, timezone

from hifi.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    original_url TEXT,
    source TEXT,
    title TEXT,
    artist TEXT,
    album TEXT,
    output_path TEXT,
    format TEXT,
    status TEXT DEFAULT 'pending',
    error TEXT,
    attempts INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    musicbrainz_id TEXT
);
"""


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def add(self, url: str, original_url: str | None = None,
            source: str | None = None) -> int | None:
        try:
            cursor = self.conn.execute(
                "INSERT INTO downloads (url, original_url, source) VALUES (?, ?, ?)",
                (url, original_url, source),
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None

    def get_by_url(self, url: str) -> sqlite3.Row | None:
        cursor = self.conn.execute(
            "SELECT * FROM downloads WHERE url = ?", (url,)
        )
        return cursor.fetchone()

    def is_duplicate(self, url: str) -> bool:
        return self.get_by_url(url) is not None

    def update_status(self, row_id: int, status: str):
        self.conn.execute(
            "UPDATE downloads SET status = ? WHERE id = ?",
            (status, row_id),
        )
        self.conn.commit()

    def mark_complete(self, row_id: int, output_path: str, fmt: str,
                      title: str, artist: str, album: str | None = None,
                      musicbrainz_id: str | None = None):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE downloads
               SET status = 'complete', output_path = ?, format = ?,
                   title = ?, artist = ?, album = ?,
                   musicbrainz_id = ?, completed_at = ?
               WHERE id = ?""",
            (output_path, fmt, title, artist, album, musicbrainz_id, now, row_id),
        )
        self.conn.commit()

    def mark_failed(self, row_id: int, error: str):
        self.conn.execute(
            """UPDATE downloads
               SET status = 'failed', error = ?, attempts = attempts + 1
               WHERE id = ?""",
            (error, row_id),
        )
        self.conn.commit()

    def get_failed(self) -> list[sqlite3.Row]:
        cursor = self.conn.execute(
            "SELECT * FROM downloads WHERE status = 'failed'"
        )
        return cursor.fetchall()

    def reset_for_retry(self, row_id: int):
        self.conn.execute(
            "UPDATE downloads SET status = 'pending', error = NULL WHERE id = ?",
            (row_id,),
        )
        self.conn.commit()

    def get_stats(self) -> dict[str, int]:
        cursor = self.conn.execute(
            """SELECT status, COUNT(*) as cnt FROM downloads GROUP BY status"""
        )
        stats = {row["status"]: row["cnt"] for row in cursor}
        stats["total"] = sum(stats.values())
        return stats
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/tools/hifi && uv run pytest tests/test_db.py -v
```

Expected: all 12 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/tools/hifi
git add src/hifi/db.py tests/test_db.py
git commit -m "feat: add SQLite database module with dedup and retry"
```

---

### Task 4: cleaner module (URL sanitization and normalization)

**Files:**
- Create: `src/hifi/cleaner.py`
- Create: `tests/test_cleaner.py`

- [ ] **Step 1: Write tests for cleaner**

Write `tests/test_cleaner.py`:

```python
from hifi.cleaner import clean_url


def test_strip_youtube_si_param():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc123"
    assert clean_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_strip_multiple_tracking_params():
    url = "https://www.youtube.com/watch?v=abc&si=x&feature=share&utm_source=twitter"
    assert clean_url(url) == "https://www.youtube.com/watch?v=abc"


def test_normalize_youtu_be():
    url = "https://youtu.be/dQw4w9WgXcQ?si=xyz"
    assert clean_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_normalize_music_youtube():
    url = "https://music.youtube.com/watch?v=abc123&feature=share"
    assert clean_url(url) == "https://www.youtube.com/watch?v=abc123"


def test_normalize_shorts():
    url = "https://youtube.com/shorts/abc123?feature=share"
    assert clean_url(url) == "https://www.youtube.com/watch?v=abc123"


def test_strip_fragment():
    url = "https://www.youtube.com/watch?v=abc#t=30"
    assert clean_url(url) == "https://www.youtube.com/watch?v=abc"


def test_non_youtube_strips_tracking():
    url = "https://soundcloud.com/artist/track?utm_source=twitter&ref=share"
    assert clean_url(url) == "https://soundcloud.com/artist/track"


def test_non_youtube_preserves_essential_params():
    url = "https://bandcamp.com/track?id=12345&utm_source=twitter"
    result = clean_url(url)
    assert "id=12345" in result
    assert "utm_source" not in result


def test_preserves_youtube_v_param():
    url = "https://www.youtube.com/watch?v=abc&list=PLxyz&index=3"
    result = clean_url(url)
    assert result == "https://www.youtube.com/watch?v=abc"


def test_handles_bare_url_no_params():
    url = "https://www.youtube.com/watch?v=abc"
    assert clean_url(url) == "https://www.youtube.com/watch?v=abc"


def test_strips_affiliate_params():
    url = "https://example.com/song?affiliate=XT&region=US"
    result = clean_url(url)
    assert "affiliate" not in result
    assert "region" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/tools/hifi && uv run pytest tests/test_cleaner.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write cleaner.py**

Write `src/hifi/cleaner.py`:

```python
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
    """If this is a YouTube URL in a non-canonical form, return canonical form.
    Returns None if not a YouTube URL needing normalization."""

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
        "",  # strip fragment
    ))


def clean_url(url: str) -> str:
    url = url.strip()

    normalized = _normalize_youtube(url)
    if normalized is not None:
        return _strip_params(normalized)

    return _strip_params(url)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/tools/hifi && uv run pytest tests/test_cleaner.py -v
```

Expected: all 11 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/tools/hifi
git add src/hifi/cleaner.py tests/test_cleaner.py
git commit -m "feat: add URL cleaner with tracking param stripping and YouTube normalization"
```

---

### Task 5: downloader module (yt-dlp wrapper)

**Files:**
- Create: `src/hifi/downloader.py`
- Create: `tests/test_downloader.py`

- [ ] **Step 1: Write tests for downloader**

The downloader wraps yt-dlp, so we test our configuration logic and metadata extraction, not yt-dlp itself. We mock `yt_dlp.YoutubeDL` to avoid actual network calls.

Write `tests/test_downloader.py`:

```python
import os
from unittest.mock import MagicMock, patch

import pytest

from hifi.downloader import build_ydl_opts, extract_metadata, sanitize_filename


def test_build_ydl_opts_default():
    opts = build_ydl_opts(output_dir="/tmp/music", preferred_format="best")
    assert opts["format"] == "ba[acodec=opus]/ba[acodec=flac]/ba[acodec=vorbis]/ba/best"
    assert opts["paths"]["home"] == "/tmp/music"
    assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"
    assert opts["postprocessors"][0]["preferredcodec"] == "opus"
    assert opts["postprocessors"][0]["preferredquality"] == "0"


def test_build_ydl_opts_specific_format():
    opts = build_ydl_opts(output_dir="/tmp", preferred_format="flac")
    assert opts["postprocessors"][0]["preferredcodec"] == "flac"


def test_build_ydl_opts_best_keeps_opus_default():
    opts = build_ydl_opts(output_dir="/tmp", preferred_format="best")
    assert opts["postprocessors"][0]["preferredcodec"] == "opus"


def test_extract_metadata_full():
    info = {
        "id": "abc123",
        "title": "Never Gonna Give You Up",
        "artist": "Rick Astley",
        "track": "Never Gonna Give You Up",
        "album": "Whenever You Need Somebody",
        "uploader": "RickAstleyVEVO",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "extractor_key": "Youtube",
    }
    meta = extract_metadata(info)
    assert meta["artist"] == "Rick Astley"
    assert meta["title"] == "Never Gonna Give You Up"
    assert meta["album"] == "Whenever You Need Somebody"
    assert meta["source"] == "Youtube"


def test_extract_metadata_fallback_to_uploader():
    info = {
        "id": "abc",
        "title": "Some Song",
        "uploader": "SomeChannel",
        "webpage_url": "https://www.youtube.com/watch?v=abc",
        "extractor_key": "Youtube",
    }
    meta = extract_metadata(info)
    assert meta["artist"] == "SomeChannel"
    assert meta["title"] == "Some Song"


def test_extract_metadata_track_over_title():
    info = {
        "id": "abc",
        "title": "Artist - Song (Official Video)",
        "track": "Song",
        "artist": "Artist",
        "extractor_key": "Youtube",
    }
    meta = extract_metadata(info)
    assert meta["title"] == "Song"


def test_sanitize_filename():
    assert sanitize_filename('AC/DC - Back: In "Black"') == "AC_DC - Back_ In _Black_"


def test_sanitize_filename_strips_whitespace():
    assert sanitize_filename("  hello  ") == "hello"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/tools/hifi && uv run pytest tests/test_downloader.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write downloader.py**

Write `src/hifi/downloader.py`:

```python
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
    """Download audio and return (file_path, metadata).

    Raises yt_dlp.utils.DownloadError on failure.
    """
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
        # yt-dlp writes to outtmpl; we need to find the actual file
        # After postprocessing, the file is at the output path with the new extension
        raw_path = ydl.prepare_filename(info)
        # The postprocessor changes the extension
        base = os.path.splitext(raw_path)[0]
        downloaded_path = f"{base}.{ext}"

        # Rename to our desired naming convention
        final_path = os.path.join(output_dir, f"{final_name}.{ext}")

        if os.path.exists(downloaded_path) and downloaded_path != final_path:
            os.rename(downloaded_path, final_path)
        elif not os.path.exists(downloaded_path):
            # Postprocessor may have changed ext, try common audio extensions
            for try_ext in ("opus", "flac", "m4a", "mp3", "ogg", "webm"):
                candidate = f"{base}.{try_ext}"
                if os.path.exists(candidate):
                    ext = try_ext
                    final_path = os.path.join(output_dir, f"{final_name}.{ext}")
                    os.rename(candidate, final_path)
                    break

        meta["ext"] = ext
        return final_path, meta
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/tools/hifi && uv run pytest tests/test_downloader.py -v
```

Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/tools/hifi
git add src/hifi/downloader.py tests/test_downloader.py
git commit -m "feat: add downloader module wrapping yt-dlp"
```

---

### Task 6: tagger module (MusicBrainz + mutagen)

**Files:**
- Create: `src/hifi/tagger.py`
- Create: `tests/test_tagger.py`

- [ ] **Step 1: Write tests for tagger**

Write `tests/test_tagger.py`:

```python
import base64
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from mutagen.flac import Picture

from hifi.tagger import (
    search_musicbrainz,
    fetch_cover_art,
    embed_tags,
    tag_file,
)


@patch("hifi.tagger.mb")
def test_search_musicbrainz_good_match(mock_mb):
    mock_mb.search_recordings.return_value = {
        "recording-list": [
            {
                "id": "rec-1",
                "title": "Never Gonna Give You Up",
                "artist-credit": [{"artist": {"name": "Rick Astley"}}],
                "release-list": [
                    {
                        "id": "rel-1",
                        "title": "Whenever You Need Somebody",
                        "date": "1987",
                    }
                ],
                "ext:score": "100",
            }
        ]
    }

    result = search_musicbrainz("Rick Astley", "Never Gonna Give You Up")
    assert result is not None
    assert result["recording_id"] == "rec-1"
    assert result["artist"] == "Rick Astley"
    assert result["title"] == "Never Gonna Give You Up"
    assert result["album"] == "Whenever You Need Somebody"
    assert result["year"] == "1987"
    assert result["release_id"] == "rel-1"


@patch("hifi.tagger.mb")
def test_search_musicbrainz_low_score_returns_none(mock_mb):
    mock_mb.search_recordings.return_value = {
        "recording-list": [
            {
                "id": "rec-1",
                "title": "Wrong Song",
                "artist-credit": [{"artist": {"name": "Wrong Artist"}}],
                "release-list": [],
                "ext:score": "30",
            }
        ]
    }

    result = search_musicbrainz("Rick Astley", "Never Gonna Give You Up")
    assert result is None


@patch("hifi.tagger.mb")
def test_search_musicbrainz_no_results(mock_mb):
    mock_mb.search_recordings.return_value = {"recording-list": []}
    result = search_musicbrainz("Unknown", "Unknown")
    assert result is None


@patch("hifi.tagger.urllib.request.urlopen")
def test_fetch_cover_art_success(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b"\x89PNG fake image data"
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_response

    data = fetch_cover_art("release-123")
    assert data == b"\x89PNG fake image data"


@patch("hifi.tagger.urllib.request.urlopen")
def test_fetch_cover_art_404_returns_none(mock_urlopen):
    from urllib.error import HTTPError
    mock_urlopen.side_effect = HTTPError(
        url="http://example.com", code=404, msg="Not Found",
        hdrs=None, fp=None
    )
    data = fetch_cover_art("release-123")
    assert data is None


def test_embed_tags_opus(tmp_path):
    """Create a minimal opus file and verify tags are embedded."""
    # Generate a tiny valid opus file via ffmpeg
    opus_path = str(tmp_path / "test.opus")
    os.system(
        f'ffmpeg -y -f lavfi -i anullsrc=r=48000:cl=mono -t 0.1 '
        f'-c:a libopus "{opus_path}" 2>/dev/null'
    )
    if not os.path.exists(opus_path):
        pytest.skip("ffmpeg not available or cannot create opus")

    embed_tags(opus_path, title="Test Song", artist="Test Artist",
               album="Test Album", year="2024")

    from mutagen.oggopus import OggOpus
    audio = OggOpus(opus_path)
    assert audio["title"] == ["Test Song"]
    assert audio["artist"] == ["Test Artist"]
    assert audio["album"] == ["Test Album"]
    assert audio["date"] == ["2024"]


def test_embed_tags_m4a(tmp_path):
    """Create a minimal m4a file and verify tags are embedded."""
    m4a_path = str(tmp_path / "test.m4a")
    os.system(
        f'ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono -t 0.1 '
        f'-c:a aac "{m4a_path}" 2>/dev/null'
    )
    if not os.path.exists(m4a_path):
        pytest.skip("ffmpeg not available or cannot create m4a")

    embed_tags(m4a_path, title="Test Song", artist="Test Artist",
               album="Test Album", year="2024")

    from mutagen.mp4 import MP4
    audio = MP4(m4a_path)
    assert audio["\xa9nam"] == ["Test Song"]
    assert audio["\xa9ART"] == ["Test Artist"]
    assert audio["\xa9alb"] == ["Test Album"]
    assert audio["\xa9day"] == ["2024"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/tools/hifi && uv run pytest tests/test_tagger.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write tagger.py**

Write `src/hifi/tagger.py`:

```python
import base64
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

from hifi.config import MUSICBRAINZ_CONFIDENCE_THRESHOLD, COVER_ART_SIZE

mb.set_useragent("hifi", "0.1.0", "https://github.com/hifi-audio/hifi")

_last_mb_call = 0.0


def _rate_limit():
    global _last_mb_call
    elapsed = time.time() - _last_mb_call
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_mb_call = time.time()


def search_musicbrainz(artist: str, title: str) -> dict[str, str] | None:
    _rate_limit()
    try:
        results = mb.search_recordings(artist=artist, recording=title, limit=5)
    except Exception:
        return None

    recordings = results.get("recording-list", [])
    if not recordings:
        return None

    best = recordings[0]
    score = int(best.get("ext:score", 0))
    if score < MUSICBRAINZ_CONFIDENCE_THRESHOLD:
        return None

    artist_credit = best.get("artist-credit", [])
    mb_artist = artist_credit[0]["artist"]["name"] if artist_credit else artist

    releases = best.get("release-list", [])
    album = releases[0]["title"] if releases else None
    year = releases[0].get("date", "")[:4] if releases else None
    release_id = releases[0]["id"] if releases else None

    return {
        "recording_id": best["id"],
        "artist": mb_artist,
        "title": best["title"],
        "album": album,
        "year": year,
        "release_id": release_id,
    }


def fetch_cover_art(release_id: str) -> bytes | None:
    url = f"https://coverartarchive.org/release/{release_id}/front-{COVER_ART_SIZE}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
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
    """Tag a downloaded file. Returns metadata dict with what was applied."""

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

        if mb_data.get("release_id"):
            cover_data = fetch_cover_art(mb_data["release_id"])

    embed_tags(file_path, title=final_title, artist=final_artist,
               album=final_album, year=final_year, cover_data=cover_data)

    return {
        "artist": final_artist,
        "title": final_title,
        "album": final_album,
        "year": final_year,
        "musicbrainz_id": mb_id,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/tools/hifi && uv run pytest tests/test_tagger.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/tools/hifi
git add src/hifi/tagger.py tests/test_tagger.py
git commit -m "feat: add tagger module with MusicBrainz lookup and mutagen embedding"
```

---

### Task 7: cli module (argparse, orchestration, pipeline)

**Files:**
- Create: `src/hifi/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write tests for CLI**

Write `tests/test_cli.py`:

```python
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from hifi.cli import parse_args, read_url_file, run_pipeline


def test_parse_args_single_url():
    args = parse_args(["https://youtube.com/watch?v=abc"])
    assert args.urls == ["https://youtube.com/watch?v=abc"]


def test_parse_args_multiple_urls():
    args = parse_args(["http://a.com", "http://b.com"])
    assert len(args.urls) == 2


def test_parse_args_file_flag(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("http://a.com\nhttp://b.com\n")
    args = parse_args(["-f", str(f)])
    assert args.file == str(f)


def test_parse_args_format():
    args = parse_args(["--format", "flac", "http://a.com"])
    assert args.format == "flac"


def test_parse_args_defaults():
    args = parse_args(["http://a.com"])
    assert args.format == "best"
    assert args.output == os.path.expanduser("~/Music")
    assert args.no_tag is False
    assert args.retry is False
    assert args.status is False
    assert args.dry_run is False


def test_read_url_file(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("http://a.com\n\n# comment\nhttp://b.com\n  \nhttp://c.com\n")
    urls = read_url_file(str(f))
    assert urls == ["http://a.com", "http://b.com", "http://c.com"]


def test_read_url_file_strips_whitespace(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("  http://a.com  \n")
    urls = read_url_file(str(f))
    assert urls == ["http://a.com"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/tools/hifi && uv run pytest tests/test_cli.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write cli.py**

Write `src/hifi/cli.py`:

```python
import argparse
import os
import sys

from hifi import __version__
from hifi.config import DEFAULT_FORMAT, DEFAULT_OUTPUT_DIR, DB_PATH
from hifi.cleaner import clean_url
from hifi.db import Database
from hifi.downloader import download, sanitize_filename
from hifi.tagger import tag_file


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hifi",
        description="High-fidelity audio downloader with MusicBrainz tagging",
    )
    parser.add_argument("urls", nargs="*", help="URLs to download")
    parser.add_argument("-f", "--file", help="Text file with URLs (one per line)")
    parser.add_argument(
        "--format", default=DEFAULT_FORMAT,
        choices=["best", "opus", "flac", "m4a"],
        help="Preferred output format (default: best)",
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument("--no-tag", action="store_true",
                        help="Skip MusicBrainz tagging")
    parser.add_argument("--retry", action="store_true",
                        help="Retry all failed downloads")
    parser.add_argument("--status", action="store_true",
                        help="Show download history and stats")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be downloaded without downloading")
    parser.add_argument("--version", action="version",
                        version=f"hifi {__version__}")
    return parser.parse_args(argv)


def read_url_file(path: str) -> list[str]:
    with open(path) as f:
        lines = f.readlines()
    return [
        line.strip() for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def _print_status(db: Database):
    stats = db.get_stats()
    print(f"\n  hifi download history")
    print(f"  ---------------------")
    print(f"  Complete: {stats.get('complete', 0)}")
    print(f"  Failed:   {stats.get('failed', 0)}")
    print(f"  Pending:  {stats.get('pending', 0)}")
    print(f"  Total:    {stats.get('total', 0)}")
    print()

    failed = db.get_failed()
    if failed:
        print(f"  Failed downloads:")
        for row in failed:
            print(f"    {row['url']} -- {row['error']} (attempts: {row['attempts']})")
        print()


def _progress_hook(d: dict):
    if d["status"] == "downloading":
        pct = d.get("_percent_str", "?%").strip()
        speed = d.get("_speed_str", "?").strip()
        print(f"\r  downloading: {pct} at {speed}", end="", flush=True)
    elif d["status"] == "finished":
        print(f"\r  download complete, processing...       ", flush=True)


def process_url(url: str, db: Database, output_dir: str,
                preferred_format: str, skip_tag: bool,
                dry_run: bool) -> str:
    """Process a single URL through the pipeline.
    Returns: 'downloaded', 'skipped', or 'failed'."""

    original_url = url
    cleaned = clean_url(url)

    if db.is_duplicate(cleaned):
        print(f"  skipped (already downloaded): {cleaned}")
        return "skipped"

    if dry_run:
        print(f"  [dry-run] would download: {cleaned}")
        return "skipped"

    row_id = db.add(url=cleaned, original_url=original_url)
    if row_id is None:
        print(f"  skipped (duplicate): {cleaned}")
        return "skipped"

    try:
        db.update_status(row_id, "downloading")
        print(f"  downloading: {cleaned}")

        file_path, meta = download(
            cleaned,
            output_dir=output_dir,
            preferred_format=preferred_format,
            progress_hook=_progress_hook,
        )

        artist = meta.get("artist", "Unknown")
        title = meta.get("title", "Unknown")
        ext = meta.get("ext", "opus")

        if not skip_tag:
            db.update_status(row_id, "tagging")
            print(f"  tagging: {artist} - {title}")
            tag_result = tag_file(file_path, artist, title)
            artist = tag_result.get("artist", artist)
            title = tag_result.get("title", title)
            album = tag_result.get("album")
            mb_id = tag_result.get("musicbrainz_id")
        else:
            album = meta.get("album")
            mb_id = None

        # Rename to final Artist - Title format
        safe_artist = sanitize_filename(artist) if artist else None
        safe_title = sanitize_filename(title) if title else "Unknown"
        if safe_artist:
            final_name = f"{safe_artist} - {safe_title}.{ext}"
        else:
            final_name = f"{safe_title}.{ext}"
        final_path = os.path.join(output_dir, final_name)

        if file_path != final_path and os.path.exists(file_path):
            os.rename(file_path, final_path)
            file_path = final_path

        db.mark_complete(
            row_id, output_path=file_path, fmt=ext,
            title=title, artist=artist, album=album,
            musicbrainz_id=mb_id,
        )
        print(f"  saved: {file_path}")
        return "downloaded"

    except Exception as e:
        db.mark_failed(row_id, str(e))
        print(f"  FAILED: {cleaned} -- {e}")
        return "failed"


def run_pipeline(args: argparse.Namespace):
    os.makedirs(args.output, exist_ok=True)
    db = Database()

    try:
        if args.status:
            _print_status(db)
            return

        if args.retry:
            failed = db.get_failed()
            if not failed:
                print("  no failed downloads to retry")
                return
            print(f"  retrying {len(failed)} failed download(s)...\n")
            for row in failed:
                db.reset_for_retry(row["id"])
            urls = [row["url"] for row in failed]
        else:
            urls = list(args.urls)
            if args.file:
                urls.extend(read_url_file(args.file))

        if not urls:
            print("  no URLs provided. Use hifi URL or hifi -f file.txt")
            return

        downloaded = 0
        skipped = 0
        failed = 0

        for i, url in enumerate(urls, 1):
            print(f"\n[{i}/{len(urls)}] {url}")
            result = process_url(
                url, db, args.output, args.format,
                args.no_tag, args.dry_run,
            )
            if result == "downloaded":
                downloaded += 1
            elif result == "skipped":
                skipped += 1
            elif result == "failed":
                failed += 1

        print(f"\n  done: {downloaded} downloaded, {skipped} skipped, {failed} failed")

    finally:
        db.close()


def main():
    args = parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/tools/hifi && uv run pytest tests/test_cli.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Run full test suite**

```bash
cd ~/tools/hifi && uv run pytest -v
```

Expected: all tests PASS across all modules

- [ ] **Step 6: Commit**

```bash
cd ~/tools/hifi
git add src/hifi/cli.py tests/test_cli.py
git commit -m "feat: add CLI module with full pipeline orchestration"
```

---

### Task 8: Integration test with real download

**Files:**
- None created, this is a manual verification task

- [ ] **Step 1: Create ~/Music/ directory if needed**

```bash
mkdir -p ~/Music
```

- [ ] **Step 2: Test single URL download**

```bash
cd ~/tools/hifi && uv run hifi "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Expected: Downloads audio, tags via MusicBrainz, saves to `~/Music/Rick Astley - Never Gonna Give You Up.opus` (or similar).

- [ ] **Step 3: Test duplicate detection**

```bash
cd ~/tools/hifi && uv run hifi "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Expected: `skipped (already downloaded)`

- [ ] **Step 4: Test --status**

```bash
cd ~/tools/hifi && uv run hifi --status
```

Expected: Shows 1 complete download in history.

- [ ] **Step 5: Test --dry-run**

```bash
cd ~/tools/hifi && uv run hifi --dry-run "https://www.youtube.com/watch?v=jNQXAC9IVRw"
```

Expected: Shows `[dry-run] would download:` without actually downloading.

- [ ] **Step 6: Test URL cleaning with tracking params**

```bash
cd ~/tools/hifi && uv run hifi --dry-run "https://www.youtube.com/watch?v=abc&si=xyz&utm_source=twitter&feature=share"
```

Expected: Shows cleaned URL `https://www.youtube.com/watch?v=abc` in dry-run output.

- [ ] **Step 7: Test file input**

```bash
cat > /tmp/test_urls.txt << 'EOF'
https://www.youtube.com/watch?v=jNQXAC9IVRw
# This is a comment
https://www.youtube.com/watch?v=9bZkp7q19f0
EOF
cd ~/tools/hifi && uv run hifi --dry-run -f /tmp/test_urls.txt
```

Expected: Shows 2 URLs that would be downloaded, skips the comment line.

- [ ] **Step 8: Fix any issues found, then commit**

```bash
cd ~/tools/hifi
git add -A
git commit -m "fix: address issues found during integration testing"
```

Only commit if fixes were needed. Skip if everything passed cleanly.

---

### Task 9: Final polish

**Files:**
- Modify: `src/hifi/cli.py` (if needed)

- [ ] **Step 1: Test hifi as installed command**

```bash
cd ~/tools/hifi && uv run hifi --version
```

Expected: `hifi 0.1.0`

- [ ] **Step 2: Test hifi --help**

```bash
cd ~/tools/hifi && uv run hifi --help
```

Expected: Shows usage, all flags documented.

- [ ] **Step 3: Run full test suite one final time**

```bash
cd ~/tools/hifi && uv run pytest -v
```

Expected: All tests PASS.

- [ ] **Step 4: Final commit if any polish was done**

```bash
cd ~/tools/hifi
git add -A
git commit -m "chore: final polish"
```

Only commit if changes were made. Skip otherwise.
