import argparse
import os
import re
import sys

from hifi import __version__
from hifi.config import (
    DEFAULT_FORMAT, DEFAULT_OUTPUT_DIR, DB_PATH,
    RECOMMEND_LIMIT_DEFAULT, SEED_SAMPLE_DEFAULT,
)
from hifi.cleaner import clean_url
from hifi.db import Database
from hifi.downloader import download, sanitize_filename
from hifi.searcher import find_best
from hifi.tagger import search_musicbrainz, tag_file

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


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
            if kind == "query":
                url = resolve_search_query(item, args.dry_run)
                if url is None:
                    skipped += 1
                    continue
            else:
                url = item

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


def parse_recommend_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hifi recommend",
        description="Generate a similar-tracks playlist from seed songs.",
    )
    parser.add_argument(
        "--seed", action="append", default=[], metavar="ARTIST_TITLE",
        help="Explicit 'Artist - Title' seed (may be repeated)",
    )
    parser.add_argument("--seed-dir", help="Scan a music directory for seeds")
    parser.add_argument(
        "--seed-sample", type=int, default=SEED_SAMPLE_DEFAULT,
        help=f"How many files to sample from --seed-dir (default: {SEED_SAMPLE_DEFAULT})",
    )
    parser.add_argument("--seed-file", help="Read seeds from a M3U or text file")
    parser.add_argument(
        "--lb-radio", metavar="PROMPT",
        help="Use Troi LB-Radio prompt syntax (requires hifi[troi])",
    )
    parser.add_argument(
        "--lb-radio-mode", default="medium",
        choices=["easy", "medium", "hard"],
        help="LB-Radio relevance tier (default: medium)",
    )
    parser.add_argument(
        "--limit", type=int, default=RECOMMEND_LIMIT_DEFAULT,
        help=f"Max picks to return (default: {RECOMMEND_LIMIT_DEFAULT})",
    )
    parser.add_argument(
        "--out", metavar="PATH",
        help="Write playlist to PATH (.m3u or .jspf)",
    )
    parser.add_argument(
        "--download", type=int, metavar="N",
        help="Auto-download top N picks via the existing search pipeline",
    )
    parser.add_argument(
        "--format", default=DEFAULT_FORMAT,
        choices=["best", "opus", "flac", "m4a"],
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-tag", action="store_true")
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
        seeds.extend(read_seed_file(args.seed_file))
    if args.seed_dir:
        seeds.extend(scan(args.seed_dir, sample=args.seed_sample))
    return seeds


def run_recommend(args: argparse.Namespace):
    from hifi.playlist import PlaylistEntry, write
    from hifi.recommender import recommend, troi_lb_radio

    db = Database()
    try:
        if args.lb_radio:
            print(f"  troi LB-Radio: {args.lb_radio!r} (mode={args.lb_radio_mode})")
            picks = troi_lb_radio(args.lb_radio, args.lb_radio_mode, limit=args.limit)
        else:
            seeds = _gather_seeds(args)
            if not seeds:
                print("  no seeds provided. Use --seed, --seed-dir, --seed-file, or --lb-radio")
                return
            print(f"  seeds: {len(seeds)}")
            for s in seeds[:5]:
                marker = f" [{s.mbid[:8]}]" if s.mbid else ""
                print(f"    {s.artist} - {s.title}{marker}")
            if len(seeds) > 5:
                print(f"    ... and {len(seeds) - 5} more")
            picks = recommend(seeds, db, limit=args.limit)

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
                )
    finally:
        db.close()


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "recommend":
        args = parse_recommend_args(argv[1:])
        run_recommend(args)
        return
    args = parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
