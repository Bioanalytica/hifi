import os
from unittest.mock import MagicMock, patch

import pytest

from hifi.cli import parse_args, read_url_file


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
