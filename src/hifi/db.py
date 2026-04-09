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
