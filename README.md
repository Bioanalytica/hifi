# hifi

High-fidelity audio downloader with MusicBrainz tagging and ListenBrainz-powered recommendations.

`hifi` accepts three kinds of input and runs them through a single download → tag → rename → log pipeline:

1. **URLs** (YouTube and anything else `yt-dlp` understands).
2. **Search queries** in `Artist - Title` form. `hifi` searches YouTube, ranks the candidates by likely source quality, and downloads the best one.
3. **Seed-based recommendations** via the `recommend` subcommand. Given seeds (a directory of music, a list of titles, or a saved playlist), `hifi` queries the ListenBrainz Labs API for similar recordings and produces a ranked playlist that can be auto-downloaded.

Every download is tagged from MusicBrainz when possible (cover art, album, year, MBID) and recorded in a local SQLite DB so you never re-download the same track twice.

## Installation

The project uses [`uv`](https://github.com/astral-sh/uv).

```sh
git clone <repo> ~/tools/hifi
cd ~/tools/hifi
uv sync
```

Optional extras:

- `uv sync --extra llm` — adds the Anthropic SDK so the YouTube candidate ranker can use Claude Haiku as a tiebreaker on close calls.
- `uv sync --extra troi` — adds [Troi](https://github.com/metabrainz/troi-recommendation-playground) for `--lb-radio` prompt-based playlist generation.

`yt-dlp` and `ffmpeg` must be available on `PATH` for downloads to work.

## Usage

### Download from URL

```sh
hifi https://www.youtube.com/watch?v=dQw4w9WgXcQ
hifi -f urls.txt          # one URL per line, # comments ignored
```

### Download from search query

```sh
hifi "Oceanlab - Satellite (Arkasia Remix)"
hifi --search "Coldplay - Yellow" --search "Above & Beyond - Sun & Moon"
```

The ranker scores each YouTube candidate on uploader (`Artist - Topic`, label channels, VEVO, "Official"), MusicBrainz duration match, title similarity, view count, and negative keywords (live, cover, nightcore, fanmake, etc.). When the top two scores are within 1.5 points and `ANTHROPIC_API_KEY` is set, Claude Haiku breaks the tie.

`--dry-run` prints the candidate table without downloading.

### Recommendations

```sh
# From explicit seeds
hifi recommend --seed "Coldplay - Yellow" --limit 20

# From a directory of files
hifi recommend --seed-dir /mnt/intranet/Music --seed-sample 10 --limit 30

# From a saved playlist
hifi recommend --seed-file ~/seeds.m3u --limit 30

# Multiple seeds aggregate (consensus boost when a track is similar to several seeds)
hifi recommend --seed "Coldplay - Yellow" --seed "Radiohead - Creep"

# Write a playlist file
hifi recommend --seed "Coldplay - Yellow" --out ~/jams.m3u
hifi recommend --seed "Coldplay - Yellow" --out ~/jams.jspf

# Auto-download the top N picks through the search pipeline
hifi recommend --seed-dir /mnt/intranet/Music --download 10
```

#### How recommend works

1. Each seed is resolved to a MusicBrainz Recording MBID (DB cache → `tagger.search_musicbrainz`).
2. MBIDs are canonicalized via the LB Labs `recording-mbid-lookup` endpoint (similar-recordings only matches canonical IDs).
3. `similar-recordings/json` is queried with the canonical MBIDs and a session-based collaborative-filter algorithm.
4. Hits are aggregated across seeds (a track that's similar to multiple seeds gets a consensus boost), filtered to drop the seeds themselves and anything already in your local hifi library, and ranked by score.
5. The top N are printed as a table, optionally written to `.m3u`/`.jspf`, and optionally fed back into the search-and-download pipeline.

#### Coverage caveats

ListenBrainz's collaborative filter has thin coverage for niche EDM/electronic tracks — single-seed runs on those may return empty. Multi-seed runs (or `--seed-dir` sampling) tend to work fine because any one well-listened seed in the batch carries the result.

#### LB-Radio prompt mode (optional)

With `hifi[troi]` installed:

```sh
hifi recommend --lb-radio "artist:Oceanlab tag:trance" --limit 30
hifi recommend --lb-radio "artist:Coldplay" --lb-radio-mode hard
```

See the [Troi LB-Radio docs](https://troi.readthedocs.io/en/latest/lb_radio.html) for the full prompt syntax.

## Other commands

```sh
hifi --status           # show download history and stats
hifi --retry            # retry all failed downloads
hifi --dry-run <url>    # see what would happen without downloading
```

## Configuration

Defaults live in `src/hifi/config.py`:

- `DEFAULT_OUTPUT_DIR = "/mnt/intranet/Music"` (override per-call with `--output`)
- `DB_PATH = ~/tools/hifi/hifi.db`
- `MUSICBRAINZ_CONFIDENCE_THRESHOLD = 80`

### Environment variables

- `ANTHROPIC_API_KEY` — enables Claude Haiku as a tiebreaker for ambiguous YouTube candidate sets.
- `LISTENBRAINZ_TOKEN` — currently unused (the Labs endpoints `recommend` uses are anonymous), but threaded through the LB client for future personalised features (Daily Jams, Weekly Discovery).

## Output

Files land in `--output` named `Artist - Title.<ext>` with embedded tags:

- Vorbis comments for FLAC, Opus, OGG
- iTunes atoms for M4A/MP4
- ID3 frames for MP3
- Front cover art at 500px when available from Cover Art Archive

Each download is logged in `hifi.db` with cleaned URL, format, status, MBID, and timestamps. Use `--status` to see counts and any failed rows.

## Development

```sh
uv run pytest               # 79 tests
uv run pytest -x -k searcher
```

Code layout:

```
src/hifi/
  cli.py           # argparse + run_pipeline + run_recommend
  cleaner.py       # URL normalization
  config.py        # constants and paths
  db.py            # SQLite downloads table
  downloader.py    # yt-dlp wrapper
  searcher.py      # YouTube candidate ranking + LLM tiebreak
  tagger.py        # MusicBrainz lookup + tag/cover-art embedding
  listenbrainz.py  # LB Labs API client (similar-recordings, mbid-lookup)
  library.py       # local library scanner + seed-file parser
  recommender.py   # seeds → MBIDs → similar → ranked picks
  playlist.py      # M3U / JSPF writers
```
