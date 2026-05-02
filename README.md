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

The MusicBrainz tagger only accepts a hit when its score is ≥ `MUSICBRAINZ_CONFIDENCE_THRESHOLD` (default 95) AND the candidate's `Artist Title` is a close fuzzy match for the user's query (token-set ratio ≥ 75, plus a stricter ≥ 90 ratio on the primary artist alone after `feat.` clauses are stripped). When MB doesn't return a confident match, the user's original `Artist - Title` query is used verbatim for tagging and filename so a wrong MB hit can't override your intent.

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
2. MBIDs are canonicalized via LB Labs `recording-mbid-lookup` (similar-recordings matches best on canonical IDs). The lookup also supplies seed artist MBIDs for the genre filter below.
3. `similar-recordings/json` is queried with the canonical MBIDs and a session-based collaborative-filter algorithm.
4. Hits are aggregated across seeds (a track that's similar to multiple seeds gets a consensus boost), filtered to drop the seeds themselves and anything already in your local hifi library.
5. **Genre post-filter**: an allowlist is built from your seeds' MusicBrainz artist tags (any tag that recurs across two seeds, plus the top 10 by frequency). Picks whose artists carry no overlapping tag are dropped. Picks with no known tags are kept by default (lenient); pass `--strict-genre` to drop those instead. Override the auto-derived allowlist with explicit `--genre TAG` flags, or disable the filter with `--no-genre-filter`. When a pick's LB Labs metadata is missing artist info, we fall back to a direct MusicBrainz recording lookup so legitimate picks aren't dropped due to a transient API outage.
6. **Anti-genre filter** (always on by default): a built-in denylist hard-rejects picks tagged with explicit non-EDM genres (`pop`, `dance-pop`, `hip hop`, `rap`, `country`, `rock`, `christmas`, etc.) even when they ALSO carry a matching allowlist tag. Without this, EDM-crossover artists like Major Lazer / Calvin Harris / Lil Nas X (tagged both `edm` and `pop`/`hip hop`) leak through on the `edm` match. Extend with `--exclude-genre TAG` (repeatable). When the denylist is active, picks with no known tags are also dropped — when the user is explicit about exclusions we don't trust unknowns.
7. **Owned-dir dedup** (optional): pass `--owned-dir PATH` (repeatable) to scan music directories you already have on disk; picks already present (matched by MBID first, then by lowercased `Artist|Title`) are dropped. Tag reads use a thread pool, and results are cached at `~/.cache/hifi/owned.json` keyed by `(path, mtime)`, so cold scans of ~12k files take ~60s and warm runs are sub-second.
8. The top N are printed as a table, optionally written to `.m3u`/`.jspf`, and optionally fed back into the search-and-download pipeline.

Without the genre filter, LB's session-based collaborative filter leaks into mainstream tracks that share listening sessions with EDM (Jonas Blue, MØ, Clean Bandit, etc.) — useful as a discovery tool for some users, but usually wrong if your library is genre-focused.

#### Micro-targeting a genre with `--seed-genre`

Want "future bass and adjacent" instead of "more of what's in my library"? Pass `--seed-genre TAG` (repeatable). hifi expands the tag to its co-occurrence neighborhood via the LB Labs `tag-similarity/json` endpoint, applies the same anti-genre denylist (so a future-bass query never pulls in `pop`), and uses the result as the genre allowlist verbatim — overriding any seed-derived allowlist.

Two operating modes:

- **Genre-only mode** (no track seeds): hifi formats the expansion as a Troi LB-Radio prompt (`tag:future-bass tag:melodic-dubstep ...`) and uses Troi to source the candidate pool, then post-filters back to the expansion. Requires `hifi[troi]`.
- **Mixed mode** (track seeds present): track seeds drive `similar-recordings` as usual; the expansion just locks the genre filter.

```sh
# Genre-only: 30 future-bass-and-adjacent tracks from cold.
hifi recommend --seed-genre "future bass" --limit 30 \
  --owned-dir /mnt/intranet/Music --owned-dir /mnt/c/Users/bioan/Music \
  --download 30

# Mixed: track seeds drive picks, filter is locked to the future-bass cluster.
hifi recommend --seed-file /mnt/intranet/Music/Electr0.m3u8 \
  --seed-sample 20 --seed-genre "future bass" --limit 30
```

Tune the expansion with `--genre-top-n N` (default 15) for a wider/narrower neighborhood and `--genre-min-count N` (default 5) to set the co-occurrence floor. Results are cached at `~/.cache/hifi/genre_graph.json` for 30 days, so warm runs are sub-millisecond. Each cache entry stores both a count-ordered canonical list (for the Troi prompt) and a variant-inclusive set (for the post-filter), so the LB tag-similarity endpoint is hit at most once per `(tag, top-n, min-count)` per month.

The expansion handles separator variants — passing `"future bass"` matches MB tags `future bass`, `future-bass`, and vice versa — so picks tagged either way are kept.

##### Worked example: 30 future-bass tracks from cold

```sh
# 1. Preview: dump the expansion + 30-pick table without running Troi to completion.
#    --dry-run prints the expansion and skips downloads.
hifi recommend \
  --seed-genre "future bass" \
  --owned-dir /mnt/intranet/Music --owned-dir /mnt/c/Users/bioan/Music \
  --limit 30 --out /tmp/fb.m3u --dry-run

# Output begins with:
#   expanded ['future bass'] -> 15 canonical tags (22 with variants)
#     future bass, trap, edm, future garage, wave, melodic house, ...
#   troi LB-Radio: 'tag:future-bass tag:trap tag:future-garage tag:wave
#                   tag:melodic-house tag:melodic-techno tag:melodic-trance
#                   tag:melodic-bass' (mode=medium)

# 2. If the table looks right, take the *exact* picks from the dry-run and
#    download them deterministically (Troi's pick set varies per call, so
#    re-running step 1 with --download 30 might pick a different 30).
queries=()
while IFS= read -r line; do
  [[ $line == "# search:"* ]] && queries+=(--search "${line#\# search:}")
done < /tmp/fb.m3u
hifi "${queries[@]}" --output /mnt/intranet/Music/Recommended

# (Or skip determinism and re-run step 1 with --download 30.)
```

##### Worked example: lock an existing seed playlist to a subgenre

```sh
# Track seeds from your library drive picks; --seed-genre overrides the
# seed-derived allowlist so the filter is locked to the future-bass cluster
# regardless of what the seed sample's tags say.
hifi recommend \
  --seed-file /mnt/intranet/Music/Electr0.m3u8 --seed-sample 20 \
  --seed-genre "future bass" \
  --owned-dir /mnt/intranet/Music --owned-dir /mnt/c/Users/bioan/Music \
  --limit 30 --download 30 \
  --output /mnt/intranet/Music/Recommended

# Multiple genres union: future bass + melodic dubstep + colour bass neighborhoods
hifi recommend \
  --seed-genre "future bass" --seed-genre "melodic dubstep" --seed-genre "colour bass" \
  --owned-dir /mnt/intranet/Music \
  --limit 30 --dry-run

# Tighten or widen the neighborhood:
#   --genre-top-n 25       -> wider net (more neighbors per --seed-genre)
#   --genre-min-count 20   -> tighter floor (drops low-count noise like artist-name tags)
hifi recommend --seed-genre "future bass" \
  --genre-top-n 25 --genre-min-count 20 \
  --limit 30 --dry-run
```

#### Coverage caveats

ListenBrainz's collaborative filter has thin coverage for niche EDM/electronic tracks — single-seed runs on those may return empty. Multi-seed runs (or `--seed-dir` sampling) tend to work fine because any one well-listened seed in the batch carries the result. The LB Labs `recording-mbid-lookup` endpoint currently 500s on certain MBIDs; hifi falls back to per-MBID retries and ultimately to raw (non-canonical) MBIDs so the pipeline doesn't stall.

#### LB-Radio prompt mode (optional)

With `hifi[troi]` installed (`uv sync --extra troi`):

```sh
# Hand-written prompt
hifi recommend --lb-radio "artist:Oceanlab tag:trance" --limit 30
hifi recommend --lb-radio "tag:dubstep tag:melodic-dubstep" --lb-radio-mode hard

# Or auto-derive the prompt from your seeds' artist tags
hifi recommend --seed-file ~/playlist.m3u --lb-radio-from-seeds --limit 30
```

`--lb-radio-from-seeds` runs the same seed-resolution + tag-derivation logic the genre filter uses, then formats the most-frequent *specific* subgenre tags (umbrella tags like `electronic`, `pop`, regional/decade tags are stripped) as `tag:X tag:Y …` and hands the prompt to Troi. The genre post-filter is applied to Troi's output too — Troi gives a broader candidate pool than `similar-recordings`, the post-filter narrows it back to the seed-derived genre family. See the [Troi LB-Radio docs](https://troi.readthedocs.io/en/latest/lb_radio.html) for the full prompt syntax.

Option 1 (`recommend` without `--lb-radio*`) and option 2 (`--lb-radio-from-seeds`) lean different ways: option 1 follows the collaborative-filter signal closely (more of your exact neighbours — for an EDM seed set, expect lots of melodic dubstep / Illenium-adjacent tracks), option 2 follows the tag signal (broader electronic neighbourhood — trip-hop, breakbeat, downtempo). Run both and compare.

#### Worked example: from a Poweramp playlist to 30 new EDM tracks

`read_seed_file` parses Poweramp m3u8 exports — bare file-path lines like `4DCA-B7D3/Music/Electronica/Artist - Title - Album.flac` are read by extracting `Artist - Title` from the basename. Combined with library dedup, the round-trip looks like this:

```sh
# 1. Preview: scan the existing library, sample 20 seeds, print a 30-pick table.
#    --dry-run skips the download and writes a playlist file you can eyeball.
hifi recommend \
  --seed-file /mnt/intranet/Music/Electr0.m3u8 \
  --seed-sample 20 \
  --owned-dir /mnt/c/Users/bioan/Music \
  --owned-dir /mnt/intranet/Music \
  --limit 30 \
  --out /tmp/recs.m3u \
  --dry-run

# 2. If the table looks good, take the *exact* picks from the dry-run and
#    download them deterministically (random.sample is non-deterministic, so
#    re-running step 1 with --download 30 might pick a different 30).
queries=()
while IFS= read -r line; do
  [[ $line == "# search:"* ]] && queries+=(--search "${line#\# search:}")
done < /tmp/recs.m3u
hifi "${queries[@]}" --output /mnt/intranet/Music/Recommended

# (Or, if you don't care about reproducibility, just re-run with --download.)
hifi recommend \
  --seed-file /mnt/intranet/Music/Electr0.m3u8 \
  --seed-sample 20 \
  --owned-dir /mnt/c/Users/bioan/Music \
  --owned-dir /mnt/intranet/Music \
  --limit 30 --download 30 \
  --output /mnt/intranet/Music/Recommended
```

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
- `MUSICBRAINZ_CONFIDENCE_THRESHOLD = 95` (MB ext:score below this, MB hit is dropped)
- `MUSICBRAINZ_QUERY_SIMILARITY = 75` (token-set ratio between MB hit and user query — combined with a stricter ≥ 90 ratio on the primary artist alone)
- `SEED_SAMPLE_DEFAULT = 10` and `RECOMMEND_LIMIT_DEFAULT = 30`

### Environment variables

A project-local `.env` is auto-loaded on `hifi` startup (via `python-dotenv`), so these can live in `~/tools/hifi/.env` instead of being exported in every shell:

- `ANTHROPIC_API_KEY` — enables Claude Haiku as a tiebreaker for ambiguous YouTube candidate sets.
- `LISTENBRAINZ_USER_TOKEN` (or `LISTENBRAINZ_TOKEN`) — attached as `Authorization: Token ...` on every LB API call. The endpoints `recommend` uses today are anonymous, so this is currently a no-op for those, but it's threaded through so future personalised features (Daily Jams, Weekly Discovery, personal recommendations) just work without re-plumbing.

### LB Core API vs Labs

`recommend` talks to two distinct LB API tiers:

- **Core API** (`api.listenbrainz.org/1`) is the production endpoint. We use it for `metadata/recording` (bulk MBID → artist + inline tags). It's stable and returns artist tags directly, so the genre filter doesn't need to fan out to per-artist MusicBrainz lookups (which are rate-limited at 1 req/sec).
- **Labs API** (`labs.api.listenbrainz.org`) is the research/experimental endpoint. We use it for `similar-recordings/json` (the only place to get session-based collaborative-filter neighbors), `tag-similarity/json` (powers `--seed-genre` expansion), and `recording-mbid-lookup/json` (the only endpoint that returns canonical recording MBIDs for cross-version dedup before `similar-recordings`). The Labs API intermittently 500s on individual MBIDs and on batched lookups; `recommend` falls back gracefully — Core API for tags/artists when Labs is down, and raw (non-canonical) MBIDs when Labs can't canonicalize.

## Output

Files land in `--output` named `Artist - Title.<ext>` with embedded tags:

- Vorbis comments for FLAC, Opus, OGG
- iTunes atoms for M4A/MP4
- ID3 frames for MP3
- Front cover art at 500px when available from Cover Art Archive

Each download is logged in `hifi.db` with cleaned URL, format, status, MBID, and timestamps. Use `--status` to see counts and any failed rows.

## Development

```sh
uv run pytest               # 107 tests
uv run pytest -x -k searcher
uv run pytest -x -k genre   # genre filter + genre-graph unit tests
uv run pytest -x -k listenbrainz   # LB API client tests
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
  listenbrainz.py  # LB API client — Core (metadata/recording) + Labs (similar-recordings, tag-similarity, recording-mbid-lookup)
  library.py       # local library scanner + seed-file parser
  recommender.py   # seeds → MBIDs → similar → ranked picks
  genre_graph.py   # tag → neighborhood expansion via LB tag-similarity
  playlist.py      # M3U / JSPF writers
```
