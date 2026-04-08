# hifi - High-Fidelity Audio Downloader

## Purpose

CLI tool that downloads the highest quality audio from YouTube and other yt-dlp-supported sites, automatically tags tracks via MusicBrainz, embeds album art, and tracks downloads in SQLite for deduplication and retry.

## Requirements

- Download best available audio from any yt-dlp-supported source
- Prefer lossless/high-quality codecs (opus, flac, vorbis); convert via ffmpeg when only lower-quality formats are available
- Accept URLs as CLI arguments or from a text file (one per line)
- Strip tracking/affiliate parameters from URLs before processing
- Fully automated MusicBrainz tagging with album art embedding
- SQLite-backed download history for deduplication and retry
- Output to `~/Music/` by default, named `Artist - Title.ext`

## Dependencies

- **yt-dlp** - audio extraction (used as Python library, not CLI)
- **musicbrainzngs** - MusicBrainz API client
- **mutagen** - audio tag reading/writing (supports opus, flac, m4a, mp3)
- **ffmpeg** - system dependency for audio conversion (already installed)
- **uv** - project/dependency management

## Architecture

### Project structure

```
~/tools/hifi/
  pyproject.toml
  src/
    hifi/
      __init__.py
      cli.py          # argparse entry point, orchestration
      downloader.py   # yt-dlp wrapper, format selection, progress hooks
      cleaner.py      # URL sanitization and normalization
      tagger.py       # MusicBrainz lookup, Cover Art Archive, mutagen embedding
      db.py           # SQLite schema, dedup, history, retry
      config.py       # defaults (output dir, formats, quality)
```

### Data flow

```
URL input -> clean URL -> check SQLite (dedup) -> yt-dlp extract info
-> download best audio -> convert if needed (ffmpeg) -> query MusicBrainz
-> fetch cover art -> embed tags via mutagen -> mark complete in DB
```

### Module responsibilities

**cli.py** - Entry point. Parses arguments, reads URL file if provided, iterates URLs through the pipeline. Handles `--retry` and `--status` commands.

**downloader.py** - Wraps `yt_dlp.YoutubeDL`. Format string: `ba[acodec=opus]/ba[acodec=flac]/ba[acodec=vorbis]/ba/best`. Uses `FFmpegExtractAudio` postprocessor to convert non-preferred formats to opus. Returns download path and metadata from `info_dict`.

**cleaner.py** - Strips tracking parameters from URLs (`si`, `feature`, `list`, `index`, `t`, `region`, `affiliate`, `utm_*`, `fbclid`, `ref`, etc.). Normalizes YouTube URLs to canonical `https://www.youtube.com/watch?v=VIDEO_ID` form. Handles youtu.be short links, music.youtube.com, and other variants.

**tagger.py** - Queries MusicBrainz by artist + title extracted from yt-dlp metadata. Takes the best match above a confidence threshold. Fetches release info (album, year, track number) and front cover art from Cover Art Archive (500px preferred). Embeds tags via mutagen using format-specific APIs:
- **OggOpus**: `mutagen.oggopus.OggOpus`, cover art as base64-encoded FLAC Picture block in `metadata_block_picture`
- **FLAC**: `mutagen.flac.FLAC`, cover art as `Picture` object via `add_picture()`
- **MP4/M4A**: `mutagen.mp4.MP4`, tags use `©nam`/`©ART`/`©alb` keys, cover art as `MP4Cover`
- **MP3**: `mutagen.id3.ID3`, standard ID3v2 frames

Rate-limits MusicBrainz queries to 1 request per second.

**db.py** - SQLite database at `~/tools/hifi/hifi.db`. Schema:

```sql
CREATE TABLE downloads (
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
```

Status lifecycle: `pending` -> `downloading` -> `tagging` -> `complete` (or `failed` at any step).

**config.py** - Default settings:
- `OUTPUT_DIR`: `~/Music/`
- `PREFERRED_FORMAT`: `"best"` (keep original if opus/flac/vorbis, otherwise convert to opus)
- `DB_PATH`: `~/tools/hifi/hifi.db`
- `MUSICBRAINZ_CONFIDENCE_THRESHOLD`: `80` (0-100 scale)
- `COVER_ART_SIZE`: `500`

### CLI interface

```
# Single URL
hifi https://youtube.com/watch?v=dQw4w9WgXcQ

# Multiple URLs
hifi URL1 URL2 URL3

# From file
hifi -f playlist.txt

# Options
hifi --format opus|flac|m4a|best   # preferred output format (default: best)
hifi --output ~/Music/             # output directory
hifi --no-tag                      # skip MusicBrainz tagging
hifi --retry                       # retry all failed downloads
hifi --status                      # show download history/stats
hifi --dry-run                     # preview without downloading
```

### URL cleaning rules

**Strip these query parameters:**
- YouTube tracking: `si`, `feature`, `list`, `index`, `t`, `pp`
- General tracking: `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`
- Affiliate/referral: `affiliate`, `ref`, `fbclid`, `gclid`, `region`

**Normalize:**
- `youtu.be/ID` -> `https://www.youtube.com/watch?v=ID`
- `music.youtube.com/watch?v=ID` -> `https://www.youtube.com/watch?v=ID`
- `youtube.com/shorts/ID` -> `https://www.youtube.com/watch?v=ID`
- Strip fragment identifiers (`#...`)

**Non-YouTube URLs:** Strip known tracking params, keep everything else intact.

### File naming

Template: `{artist} - {title}.{ext}`

Fallback when artist is unknown: `{title}.{ext}` (using yt-dlp's extracted title).

Sanitize filenames: replace `/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`, `|` with `_`. Trim whitespace.

### Error handling

- **Network errors**: Mark as `failed` in DB, increment `attempts`, continue to next URL
- **yt-dlp extraction failure**: Log error, mark failed, continue
- **MusicBrainz no match**: Download succeeds, tag with yt-dlp metadata only (artist/title from video), log a warning
- **Cover art 404**: Tag without cover art, log warning
- **Duplicate URL**: Skip with message, don't re-download
- **ffmpeg conversion failure**: Keep original format, mark status, log warning

### Progress and output

- yt-dlp progress hooks for download progress bar
- Status messages per URL: cleaning -> checking DB -> downloading -> tagging -> done
- Summary at end: X downloaded, Y skipped (dupes), Z failed

## Verification

1. `uv run hifi https://www.youtube.com/watch?v=dQw4w9WgXcQ` - downloads, converts, tags, saves to ~/Music/
2. Run same URL again - should skip as duplicate
3. `uv run hifi --status` - shows the download in history
4. `uv run hifi -f test_urls.txt` - batch download from file
5. `uv run hifi --retry` - retries any failed entries
6. `uv run hifi --dry-run URL` - shows what would happen without downloading
7. Verify tags: `mutagen-inspect ~/Music/Artist\ -\ Title.opus` (or use a media player)
