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
