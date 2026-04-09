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
