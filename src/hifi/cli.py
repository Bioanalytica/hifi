import argparse
import os
import re
import sys

from hifi import __version__
from hifi.config import (
    AUDIO_EXTENSIONS,
    DEFAULT_FORMAT, DEFAULT_OUTPUT_DIR, DB_PATH,
    RECOMMEND_LIMIT_DEFAULT, SEED_SAMPLE_DEFAULT,
)
from hifi.cleaner import clean_url
from hifi.db import Database
from hifi.downloader import download, sanitize_filename
from hifi.searcher import find_best
from hifi.tagger import search_musicbrainz, tag_file

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _extract_profile_name(argv: list[str]) -> str | None:
    """Pull ``--profile NAME`` (or ``--profile=NAME``) out of argv.

    Done as a pre-pass so the named profile can supply argparse
    defaults for every flag — argparse can't do that itself because it
    parses one pass over argv.
    """
    for i, a in enumerate(argv):
        if a == "--profile" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--profile="):
            return a.split("=", 1)[1]
    return None


def _parse_search_query(query: str) -> tuple[str, str]:
    """Split 'Artist - Title' for search input.

    Unlike the YouTube-title parser in downloader.py, we do NOT treat trailing
    parens as the artist. For user-typed queries, '(Arkasia Remix)' is part of
    the track title, not a separate artist field.
    """
    q = query.strip()
    if " - " in q:
        artist, title = q.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", q


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from hifi import userconfig
    cfg = userconfig.load()

    parser = argparse.ArgumentParser(
        prog="hifi",
        description="High-fidelity audio downloader with MusicBrainz tagging",
    )
    parser.add_argument(
        "urls", nargs="*",
        help="URLs to download, or 'Artist - Title' search queries",
    )
    parser.add_argument("-f", "--file", help="Text file with URLs (one per line)")
    parser.add_argument(
        "--search", action="append", default=[],
        metavar="ARTIST_TITLE",
        help="Explicit 'Artist - Title' search (may be repeated)",
    )
    parser.add_argument(
        "--format", default=cfg.get("format", DEFAULT_FORMAT),
        choices=["best", "opus", "flac", "m4a"],
        help="Preferred output format (default: best)",
    )
    parser.add_argument(
        "--output", default=cfg.get("output", DEFAULT_OUTPUT_DIR),
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument("--no-tag", action="store_true",
                        default=bool(cfg.get("no-tag", False)),
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


def _print_candidates(ranked, winner_id: str | None):
    print(f"  {'#':>3}  {'score':>6}  {'dur':>5}  {'views':>10}  uploader / title")
    print(f"  {'-' * 3}  {'-' * 6}  {'-' * 5}  {'-' * 10}  " + "-" * 40)
    for i, c in enumerate(ranked):
        dur = f"{(c.duration or 0)//60}:{(c.duration or 0)%60:02d}" if c.duration else "?"
        views = f"{c.view_count:,}" if c.view_count else "?"
        marker = " *" if c.video_id == winner_id else "  "
        upload_title = f"{c.uploader} | {c.title}"
        if len(upload_title) > 70:
            upload_title = upload_title[:67] + "..."
        print(f"  {i:>3}{marker}{c.score:>6.2f}  {dur:>5}  {views:>10}  {upload_title}")


def resolve_search_query(query: str, dry_run: bool) -> str | None:
    """Parse a search query, rank candidates, return winner URL.
    In dry-run mode, prints the candidate table and returns None."""
    artist, title = _parse_search_query(query)
    print(f"  searching: artist={artist!r} title={title!r}")

    mb_duration = None
    if artist and title:
        mb_data = search_musicbrainz(artist, title)
        if mb_data:
            from rapidfuzz import fuzz as _fuzz
            mb_query = f"{mb_data.get('artist', '')} {mb_data.get('title', '')}"
            user_query = f"{artist} {title}"
            sim = _fuzz.token_set_ratio(mb_query, user_query)
            if sim >= 60 and mb_data.get("duration"):
                mb_duration = mb_data["duration"]
                dm = mb_duration // 60
                ds = mb_duration % 60
                print(f"  musicbrainz: {mb_data['artist']} - {mb_data['title']}"
                      f" ({dm}:{ds:02d})")
            else:
                print(f"  musicbrainz: ignoring weak match "
                      f"({mb_data.get('artist')} - {mb_data.get('title')}, sim={sim})")

    pick = find_best(artist, title, mb_duration=mb_duration)
    if pick is None:
        print(f"  no candidates found for: {query}")
        return None

    top_n = min(10, len(pick.ranked))
    _print_candidates(pick.ranked[:top_n], pick.winner.video_id)
    print(f"  picked (via {pick.strategy}): {pick.winner.url}")

    if dry_run:
        return None
    return pick.winner.url


def process_url(url: str, db: Database, output_dir: str,
                preferred_format: str, skip_tag: bool,
                dry_run: bool,
                query_artist: str | None = None,
                query_title: str | None = None) -> str:
    """Process a single URL through the pipeline.

    When ``query_artist`` / ``query_title`` are supplied (search-mode
    downloads), they're treated as the user's source-of-truth intent:
    MB is queried with them (not yt-dlp's parsed video metadata, which
    is often broken on remixes / feat. credits), and if MB doesn't
    return a high-confidence + similar-enough match the file is tagged
    with the original query verbatim.

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

        artist = query_artist or meta.get("artist", "Unknown")
        title = query_title or meta.get("title", "Unknown")
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

        raw_items: list[tuple[str, str]] = []
        if args.retry:
            failed_rows = db.get_failed()
            if not failed_rows:
                print("  no failed downloads to retry")
                return
            print(f"  retrying {len(failed_rows)} failed download(s)...\n")
            for row in failed_rows:
                db.reset_for_retry(row["id"])
            raw_items = [("url", row["url"]) for row in failed_rows]
        else:
            # Explicit --search args are always queries
            for q in args.search:
                raw_items.append(("query", q))
            # Positional args: classify URL vs query
            for item in args.urls:
                kind = "url" if _URL_RE.match(item) else "query"
                raw_items.append((kind, item))
            if args.file:
                for u in read_url_file(args.file):
                    raw_items.append(("url", u))

        if not raw_items:
            print("  no inputs provided. Use hifi URL, hifi 'Artist - Title', or hifi -f file.txt")
            return

        downloaded = 0
        skipped = 0
        failed = 0

        for i, (kind, item) in enumerate(raw_items, 1):
            print(f"\n[{i}/{len(raw_items)}] {item}")
            q_artist: str | None = None
            q_title: str | None = None
            if kind == "query":
                url = resolve_search_query(item, args.dry_run)
                if url is None:
                    skipped += 1
                    continue
                q_artist, q_title = _parse_search_query(item)
            else:
                url = item

            result = process_url(
                url, db, args.output, args.format,
                args.no_tag, args.dry_run,
                query_artist=q_artist,
                query_title=q_title,
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


def parse_recommend_args(argv: list[str]) -> argparse.Namespace:
    from hifi import userconfig
    cfg = dict(userconfig.section("recommend"))

    profile_name = _extract_profile_name(argv)
    if profile_name:
        profile = userconfig.load_profile(profile_name)
        # Profile keys win over the base recommend section. CLI flags
        # still win over both because argparse parses argv last.
        cfg.update(profile)

    parser = argparse.ArgumentParser(
        prog="hifi recommend",
        description="Generate a similar-tracks playlist from seed songs.",
    )
    parser.add_argument(
        "--profile", metavar="NAME", default=profile_name,
        help="Load defaults from ~/.config/hifi/profiles/<NAME>.yml",
    )
    # Repeatable flags use config as base; CLI invocations extend it
    # (so a user with `owned-dirs:` in config still gets to add ad-hoc
    # dirs on the command line).
    parser.add_argument(
        "--seed", action="append", default=list(cfg.get("seeds", []) or []),
        metavar="ARTIST_TITLE",
        help="Explicit 'Artist - Title' seed (may be repeated)",
    )
    parser.add_argument("--seed-dir", default=cfg.get("seed-dir"),
                        help="Scan a music directory for seeds")
    parser.add_argument(
        "--seed-sample", type=int,
        default=cfg.get("seed-sample", SEED_SAMPLE_DEFAULT),
        help=f"How many files to sample from --seed-dir (default: {SEED_SAMPLE_DEFAULT})",
    )
    parser.add_argument("--seed-file", default=cfg.get("seed-file"),
                        help="Read seeds from a M3U or text file")
    parser.add_argument(
        "--lb-radio", metavar="PROMPT",
        help="Use Troi LB-Radio prompt syntax (requires hifi[troi])",
    )
    parser.add_argument(
        "--lb-radio-from-seeds", action="store_true",
        help="Derive an LB-Radio prompt from seed artist tags (requires hifi[troi])",
    )
    parser.add_argument(
        "--lb-radio-mode", default=cfg.get("lb-radio-mode", "medium"),
        choices=["easy", "medium", "hard"],
        help="LB-Radio relevance tier (default: medium)",
    )
    parser.add_argument(
        "--genre", action="append", default=list(cfg.get("genres", []) or []),
        metavar="TAG",
        help="Restrict picks to this MB tag (repeatable). Overrides auto-derivation.",
    )
    parser.add_argument(
        "--seed-genre", action="append",
        default=list(cfg.get("seed-genres", []) or []), metavar="TAG",
        help="Genre target to expand into its neighborhood via LB Labs "
             "tag-similarity (repeatable). Works alone (genre-only mode, "
             "uses Troi LB-Radio) or with track seeds (locks the genre "
             "filter to the expanded neighborhood). Overrides --genre and "
             "the seed-derived allowlist.",
    )
    parser.add_argument(
        "--genre-top-n", type=int,
        default=cfg.get("genre-top-n", 15), metavar="N",
        help="How many neighbors to keep per --seed-genre (default: 15)",
    )
    parser.add_argument(
        "--genre-min-count", type=int,
        default=cfg.get("genre-min-count", 5), metavar="N",
        help="Drop neighbors with co-occurrence count below N (default: 5)",
    )
    parser.add_argument(
        "--no-genre-filter", action="store_true",
        default=bool(cfg.get("no-genre-filter", False)),
        help="Disable the seed-derived genre post-filter",
    )
    parser.add_argument(
        "--strict-genre", action="store_true",
        default=bool(cfg.get("strict-genre", False)),
        help="Drop picks with no known tags (default: keep them)",
    )
    parser.add_argument(
        "--exclude-genre", action="append",
        default=list(cfg.get("exclude-genres", []) or []), metavar="TAG",
        help="Hard-reject picks tagged with TAG even if they match the "
             "allowlist (repeatable). Adds to the built-in defaults: "
             "pop, country, classical, jazz, blues, christmas, etc.",
    )
    parser.add_argument(
        "--require-tag", action="append",
        default=list(cfg.get("require-tags", []) or []), metavar="TAG",
        help="Pick must have at least one of these tags (repeatable). "
             "Used by profiles to demand a topic signal — e.g. the "
             "piano profile requires at least one of {piano, pianist, "
             "neoclassical, modern classical, ...}.",
    )
    parser.add_argument(
        "--forbid-tag", action="append",
        default=list(cfg.get("forbid-tags", []) or []), metavar="TAG",
        help="Pick must have NONE of these tags (repeatable). Stricter "
             "than --exclude-genre; intended for profile-supplied "
             "instrument or non-genre signals.",
    )
    parser.add_argument(
        "--owned-dir", action="append",
        default=list(cfg.get("owned-dirs", []) or []), metavar="PATH",
        help="Music directory to dedup against (repeatable). Picks already "
             "present (by MBID or by Artist|Title) are dropped.",
    )
    parser.add_argument(
        "--limit", type=int,
        default=cfg.get("limit", RECOMMEND_LIMIT_DEFAULT),
        help=f"Max picks to return (default: {RECOMMEND_LIMIT_DEFAULT})",
    )
    parser.add_argument(
        "--out", default=cfg.get("out"), metavar="PATH",
        help="Write playlist to PATH (.m3u or .jspf)",
    )
    parser.add_argument(
        "--download", type=int, default=cfg.get("download"), metavar="N",
        help="Auto-download top N picks via the existing search pipeline",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        default=bool(cfg.get("confirm", False)),
        help="Show the picks table and prompt to download all of them. "
             "Sets download=limit on confirmation; cancels cleanly on no.",
    )
    parser.add_argument(
        "--format", default=cfg.get("format", DEFAULT_FORMAT),
        choices=["best", "opus", "flac", "m4a"],
    )
    parser.add_argument("--output",
                        default=cfg.get("output", DEFAULT_OUTPUT_DIR))
    parser.add_argument("--no-tag", action="store_true",
                        default=bool(cfg.get("no-tag", False)))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _print_picks(picks):
    print(f"  {'#':>3}  {'score':>6}  {'seeds':>5}  artist - title")
    print(f"  {'-' * 3}  {'-' * 6}  {'-' * 5}  " + "-" * 50)
    for i, p in enumerate(picks):
        line = f"{p.artist} - {p.title}"
        if len(line) > 60:
            line = line[:57] + "..."
        print(f"  {i:>3}  {p.score:>6.2f}  {p.seed_count:>5}  {line}")


def _gather_seeds(args: argparse.Namespace):
    import random
    from hifi.library import Seed, read_seed_file, scan
    seeds: list[Seed] = []
    for s in args.seed:
        s = s.strip()
        if " - " not in s:
            print(f"  ignoring seed (no ' - '): {s!r}")
            continue
        artist, title = s.split(" - ", 1)
        seeds.append(Seed(artist=artist.strip(), title=title.strip()))
    if args.seed_file:
        file_seeds = read_seed_file(args.seed_file)
        if args.seed_sample and args.seed_sample < len(file_seeds):
            file_seeds = random.sample(file_seeds, args.seed_sample)
        seeds.extend(file_seeds)
    if args.seed_dir:
        seeds.extend(scan(args.seed_dir, sample=args.seed_sample))
    return seeds


def run_recommend(args: argparse.Namespace):
    from hifi.genre_graph import expand_genres, expand_genres_canonical
    from hifi.library import collect_owned
    from hifi.playlist import PlaylistEntry, write
    from hifi.recommender import (
        _DEFAULT_EXCLUDE_GENRES,
        filter_picks_by_owned, lb_radio_from_genres, lb_radio_from_seeds,
        recommend, troi_lb_radio,
    )

    db = Database()
    try:
        owned_mbids: set[str] = set()
        owned_titles: set[str] = set()
        if args.owned_dir:
            print(f"  scanning owned dir(s): {', '.join(args.owned_dir)}")
            owned_mbids, owned_titles = collect_owned(args.owned_dir)
            print(f"  owned: {len(owned_mbids)} MBIDs, {len(owned_titles)} titles")

        # If the user (via CLI or profile/config) supplied any exclude
        # genres at all, treat that list as the full exclude set so a
        # rock profile that omits "rock" actually lets rock through.
        # Otherwise fall back to the EDM-default denylist.
        if args.exclude_genre:
            exclude_genres = {g.strip().lower() for g in args.exclude_genre}
        else:
            exclude_genres = set(_DEFAULT_EXCLUDE_GENRES)

        require_tags = {t.strip().lower() for t in (args.require_tag or [])
                        if t and t.strip()}
        forbid_tags = {t.strip().lower() for t in (args.forbid_tag or [])
                       if t and t.strip()}

        expanded: set[str] = set()
        canonical: list[str] = []
        if args.seed_genre:
            canonical = expand_genres_canonical(
                args.seed_genre,
                top_n=args.genre_top_n,
                min_count=args.genre_min_count,
            )
            expanded = expand_genres(
                args.seed_genre,
                top_n=args.genre_top_n,
                min_count=args.genre_min_count,
            )
            print(f"  expanded {args.seed_genre} -> "
                  f"{len(canonical)} canonical tags ({len(expanded)} with variants)")
            print(f"    {', '.join(canonical[:12])}"
                  f"{' ...' if len(canonical) > 12 else ''}")

        has_track_seeds = bool(args.seed or args.seed_dir or args.seed_file)
        genre_only_mode = bool(args.seed_genre) and not has_track_seeds

        if args.seed_genre and args.lb_radio_from_seeds:
            print("  --seed-genre takes precedence over --lb-radio-from-seeds")

        picks: list = []

        if genre_only_mode:
            if not expanded:
                print("  could not expand --seed-genre into any tags")
                return
            prompt, picks = lb_radio_from_genres(
                canonical, expanded, db,
                mode=args.lb_radio_mode, limit=args.limit,
                strict_genre=args.strict_genre,
                exclude_genres=exclude_genres,
                require_tags=require_tags or None,
                forbid_tags=forbid_tags or None,
                owned_mbids=owned_mbids,
                owned_titles=owned_titles,
            )
            if not prompt:
                print("  could not form an LB-Radio prompt from expansion")
                return
            print(f"  troi LB-Radio: {prompt!r} (mode={args.lb_radio_mode})")
        elif args.lb_radio_from_seeds and not args.seed_genre:
            seeds = _gather_seeds(args)
            if not seeds:
                print("  no seeds provided. Use --seed, --seed-dir, or --seed-file.")
                return
            print(f"  seeds: {len(seeds)} (deriving LB-Radio prompt from artist tags)")
            prompt, picks = lb_radio_from_seeds(
                seeds, db,
                mode=args.lb_radio_mode, limit=args.limit,
                filter_genre=not args.no_genre_filter,
                strict_genre=args.strict_genre,
                exclude_genres=exclude_genres,
                require_tags=require_tags or None,
                forbid_tags=forbid_tags or None,
                owned_mbids=owned_mbids,
                owned_titles=owned_titles,
            )
            if not prompt:
                print("  could not derive any tags from seed artists")
                return
            print(f"  troi LB-Radio: {prompt!r} (mode={args.lb_radio_mode})")
        elif args.lb_radio:
            print(f"  troi LB-Radio: {args.lb_radio!r} (mode={args.lb_radio_mode})")
            picks = troi_lb_radio(args.lb_radio, args.lb_radio_mode, limit=args.limit)
            if owned_mbids or owned_titles:
                picks = filter_picks_by_owned(picks, owned_mbids, owned_titles)
        else:
            seeds = _gather_seeds(args)
            if not seeds:
                print("  no seeds provided. Use --seed, --seed-dir, --seed-file, "
                      "--seed-genre, or --lb-radio")
                return
            print(f"  seeds: {len(seeds)}")
            for s in seeds[:5]:
                marker = f" [{s.mbid[:8]}]" if s.mbid else ""
                print(f"    {s.artist} - {s.title}{marker}")
            if len(seeds) > 5:
                print(f"    ... and {len(seeds) - 5} more")
            # Allowlist precedence: --seed-genre > --genre > seed-derived.
            if expanded:
                genres_arg: set[str] | None = expanded
            elif args.genre:
                genres_arg = set(args.genre)
            else:
                genres_arg = None
            picks = recommend(
                seeds, db, limit=args.limit,
                genres=genres_arg,
                filter_genre=not args.no_genre_filter,
                strict_genre=args.strict_genre,
                exclude_genres=exclude_genres,
                require_tags=require_tags or None,
                forbid_tags=forbid_tags or None,
                owned_mbids=owned_mbids or None,
                owned_titles=owned_titles or None,
            )

        if not picks:
            print("  no recommendations found")
            return

        print()
        _print_picks(picks)
        print()

        if args.out:
            entries = [
                PlaylistEntry(artist=p.artist, title=p.title, mbid=p.mbid)
                for p in picks
            ]
            write(entries, args.out)
            print(f"  wrote playlist: {args.out}")

        # --confirm: prompt to download all picks. Defaults to the full
        # picks list when --download isn't explicitly set, so the typical
        # flow becomes a single command with a yes/no at the end.
        if args.confirm and not args.dry_run:
            n = args.download if args.download else len(picks)
            n = min(n, len(picks))
            answer = input(f"  Download {n} tracks to {args.output}? [y/N]: ").strip().lower()
            if answer in ("y", "yes"):
                args.download = n
            else:
                print("  cancelled")
                return

        if args.download and not args.dry_run:
            n = min(args.download, len(picks))
            print(f"\n  auto-downloading top {n}...")
            os.makedirs(args.output, exist_ok=True)
            for i, p in enumerate(picks[:n], 1):
                query = f"{p.artist} - {p.title}"
                print(f"\n[{i}/{n}] {query}")
                url = resolve_search_query(query, dry_run=False)
                if url is None:
                    continue
                process_url(
                    url, db, args.output, args.format,
                    args.no_tag, dry_run=False,
                    query_artist=p.artist,
                    query_title=p.title,
                )
    finally:
        db.close()


def parse_tags_args(argv: list[str]) -> argparse.Namespace:
    from hifi import userconfig
    cfg = dict(userconfig.section("recommend"))  # reuses recommend's seed config

    profile_name = _extract_profile_name(argv)
    if profile_name:
        cfg.update(userconfig.load_profile(profile_name))

    parser = argparse.ArgumentParser(
        prog="hifi tags",
        description="Print the MB tags associated with seed songs.",
    )
    parser.add_argument(
        "--profile", metavar="NAME", default=profile_name,
        help="Load defaults from ~/.config/hifi/profiles/<NAME>.yml",
    )
    parser.add_argument(
        "--seed", action="append", default=list(cfg.get("seeds", []) or []),
        metavar="ARTIST_TITLE",
        help="Explicit 'Artist - Title' seed (may be repeated)",
    )
    parser.add_argument("--seed-dir", default=cfg.get("seed-dir"),
                        help="Scan a music directory for seeds")
    parser.add_argument("--seed-file", default=cfg.get("seed-file"),
                        help="Read seeds from a M3U or text file")
    parser.add_argument(
        "--seed-sample", type=int,
        default=cfg.get("seed-sample", SEED_SAMPLE_DEFAULT),
        help=f"How many files to sample from --seed-dir/-file "
             f"(default: {SEED_SAMPLE_DEFAULT})",
    )
    parser.add_argument(
        "--top", type=int, default=30, metavar="N",
        help="How many tags to show in the aggregate frequency table "
             "(default: 30)",
    )
    parser.add_argument(
        "--no-per-track", action="store_true",
        help="Skip the per-track tag listing; show only the aggregate.",
    )
    return parser.parse_args(argv)


def run_tags(args: argparse.Namespace):
    from collections import Counter
    from hifi.listenbrainz import metadata_recording
    from hifi.recommender import resolve_seed_mbids

    db = Database()
    try:
        seeds = _gather_seeds(args)
        if not seeds:
            print("  no seeds provided. Use --seed, --seed-dir, or --seed-file.")
            return

        print(f"  seeds: {len(seeds)}")
        resolved = resolve_seed_mbids(seeds, db)
        if not resolved:
            print("  none of the seeds resolved to MBIDs")
            return

        mbids = [s.mbid for s in resolved if s.mbid]
        meta = metadata_recording(mbids)

        # Aggregate tag frequency across all seeds.
        counter: Counter[str] = Counter()
        per_track: list[tuple[str, str, set[str]]] = []
        for s in resolved:
            tags = set((meta.get(s.mbid) or {}).get("inline_tags") or set())
            counter.update(tags)
            per_track.append((s.artist, s.title, tags))

        if not counter:
            print("  no tags found for any seed")
            return

        # Aggregate view.
        print(f"\n  aggregate tag frequency (top {args.top}):")
        print(f"  {'count':>5}  tag")
        print(f"  {'-' * 5}  " + "-" * 50)
        for tag, n in counter.most_common(args.top):
            print(f"  {n:>5}  {tag}")

        if args.no_per_track:
            return

        # Per-track view.
        print("\n  per-track tags:")
        for artist, title, tags in per_track:
            line = f"{artist} - {title}"
            if len(line) > 70:
                line = line[:67] + "..."
            tag_str = ", ".join(sorted(tags)) if tags else "(none)"
            if len(tag_str) > 100:
                tag_str = tag_str[:97] + "..."
            print(f"  {line}")
            print(f"      {tag_str}")
    finally:
        db.close()


def run_lb_status():
    """Show LB token health, cache username if valid."""
    from hifi import userconfig
    from hifi.listenbrainz import _token, validate_token

    tok = _token()
    if not tok:
        print("  no LB token. Set LISTENBRAINZ_USER_TOKEN in your env or "
              f"~/tools/hifi/.env, then re-run.")
        print(f"  config dir: {os.path.dirname(userconfig.config_path())}")
        return

    print(f"  token: {tok[:8]}... ({len(tok)} chars)")
    result = validate_token()
    if result is None:
        print("  could not validate (network error or unexpected response)")
        return

    if not result["valid"]:
        print(f"  INVALID: {result.get('message') or 'unknown reason'}")
        return

    user = result["user_name"]
    print(f"  valid for user: {user!r}")

    state = userconfig.load_state()
    state["lb_user_name"] = user
    userconfig.save_state(state)
    print(f"  cached at {userconfig.state_path()}")


def parse_retag_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hifi retag",
        description="Re-tag album + cover art on existing audio files "
                    "using a canonical MusicBrainz album lookup.",
    )
    parser.add_argument(
        "paths", nargs="+",
        help="Audio files or directories to retag (walks dirs recursively).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing tags.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-tag even when the current album already matches MB's pick.",
    )
    return parser.parse_args(argv)


def run_retag(args: argparse.Namespace):
    """Walk ``paths``, MB-look-up each file by artist+title, and overwrite
    album + year + cover with the canonical-album pick from
    :func:`hifi.tagger.pick_canonical_release`.

    Existing artist/title tags are the lookup keys and are never modified.
    Files without an artist+title in their tags, or where MB can't return
    a confident hit, are skipped silently. Use --dry-run first to eyeball.
    """
    from mutagen import File as MutagenFile
    from hifi.tagger import embed_tags, fetch_cover_art, search_musicbrainz

    files: list[str] = []
    for p in args.paths:
        if os.path.isfile(p):
            if p.lower().endswith(AUDIO_EXTENSIONS):
                files.append(p)
            else:
                print(f"  skip (not an audio file): {p}")
        elif os.path.isdir(p):
            for root, _, names in os.walk(p):
                for n in names:
                    if n.lower().endswith(AUDIO_EXTENSIONS):
                        files.append(os.path.join(root, n))
        else:
            print(f"  skip (does not exist): {p}")

    if not files:
        print("  no audio files found")
        return

    print(f"  found {len(files)} audio files")
    updated = 0
    skipped = 0
    failed = 0

    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path)
        try:
            f = MutagenFile(path, easy=True)
            if f is None:
                raise ValueError("mutagen returned None")
            artist = (f.get("artist") or [None])[0]
            title = (f.get("title") or [None])[0]
            old_album = (f.get("album") or [""])[0]
        except Exception as e:
            print(f"  [{i}/{len(files)}] read FAILED: {rel}: {e}")
            failed += 1
            continue

        if not artist or not title:
            print(f"  [{i}/{len(files)}] skip (no artist/title): {rel}")
            skipped += 1
            continue

        mb_data = search_musicbrainz(artist, title)
        if not mb_data:
            print(f"  [{i}/{len(files)}] skip (no MB match): {artist} - {title}")
            skipped += 1
            continue

        new_album = mb_data.get("album")
        if not new_album:
            print(f"  [{i}/{len(files)}] skip (no album in MB): {artist} - {title}")
            skipped += 1
            continue

        if not args.force and old_album.strip().lower() == new_album.strip().lower():
            print(f"  [{i}/{len(files)}] ok: {artist} - {title} [{old_album}]")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  [{i}/{len(files)}] [dry] {artist} - {title}")
            print(f"      [{old_album or '(none)'}] -> [{new_album}]")
            updated += 1
            continue

        cover = fetch_cover_art(
            mb_data.get("release_id"),
            mb_data.get("release_group_id"),
        )
        try:
            embed_tags(
                path, title=title, artist=artist,
                album=new_album, year=mb_data.get("year"),
                cover_data=cover,
            )
        except Exception as e:
            print(f"  [{i}/{len(files)}] write FAILED: {rel}: {e}")
            failed += 1
            continue

        print(f"  [{i}/{len(files)}] tagged: {artist} - {title}")
        print(f"      [{old_album or '(none)'}] -> [{new_album}]")
        updated += 1

    print(f"\n  done: {updated} {'would update' if args.dry_run else 'updated'}, "
          f"{skipped} skipped, {failed} failed")


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "recommend":
        args = parse_recommend_args(argv[1:])
        run_recommend(args)
        return
    if argv and argv[0] == "lb-status":
        run_lb_status()
        return
    if argv and argv[0] == "tags":
        args = parse_tags_args(argv[1:])
        run_tags(args)
        return
    if argv and argv[0] == "retag":
        args = parse_retag_args(argv[1:])
        run_retag(args)
        return
    args = parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
