"""SQLite-backed library database.

Stored at:
  macOS   ~/Library/Application Support/sounds/library.db
  Linux   ~/.local/share/sounds/library.db
  Windows %APPDATA%/sounds/library.db
"""

import os
import sqlite3
import sys
from pathlib import Path


def _data_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "sounds"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / "sounds"
    else:
        base = Path.home() / ".local" / "share" / "sounds"
    base.mkdir(parents=True, exist_ok=True)
    return base


class Database:
    def __init__(self) -> None:
        self._path = _data_dir() / "library.db"
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
                id           INTEGER PRIMARY KEY,
                uri          TEXT UNIQUE NOT NULL,
                mtime        REAL,
                content_hash TEXT,
                title        TEXT,
                artist       TEXT,
                album        TEXT,
                year         TEXT,
                genre        TEXT,
                duration     REAL
            );
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert_track(
        self,
        uri: str,
        mtime: float,
        content_hash: str,
        title: str | None,
        artist: str | None,
        album: str | None,
        year: str | None,
        genre: str | None,
        duration: float | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO tracks (uri, mtime, content_hash, title, artist, album,
                                year, genre, duration)
            VALUES (:uri, :mtime, :hash, :title, :artist, :album,
                    :year, :genre, :duration)
            ON CONFLICT(uri) DO UPDATE SET
                mtime        = excluded.mtime,
                content_hash = excluded.content_hash,
                title        = excluded.title,
                artist       = excluded.artist,
                album        = excluded.album,
                year         = excluded.year,
                genre        = excluded.genre,
                duration     = excluded.duration
            """,
            {
                "uri": uri,
                "mtime": mtime,
                "hash": content_hash,
                "title": title,
                "artist": artist,
                "album": album,
                "year": year,
                "genre": genre,
                "duration": duration,
            },
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_mtime(self, uri: str) -> float | None:
        row = self._conn.execute(
            "SELECT mtime FROM tracks WHERE uri = ?", (uri,)
        ).fetchone()
        return row["mtime"] if row else None

    def find_by_hash(self, content_hash: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM tracks WHERE content_hash = ?", (content_hash,)
        ).fetchone()

    def all_tracks(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM tracks ORDER BY artist, album, title"
        ).fetchall()

    def close(self) -> None:
        self._conn.close()
