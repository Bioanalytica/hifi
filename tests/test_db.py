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
