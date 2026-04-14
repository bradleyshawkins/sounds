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

from sounds.models import Section


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

            CREATE TABLE IF NOT EXISTS loops (
                id           INTEGER PRIMARY KEY,
                track_id     INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                name         TEXT NOT NULL,
                start_sample INTEGER NOT NULL,
                end_sample   INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sections (
                id           INTEGER PRIMARY KEY,
                track_id     INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                start_sample INTEGER NOT NULL,
                end_sample   INTEGER NOT NULL,
                label        TEXT NOT NULL,
                color        TEXT NOT NULL
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

    def get_or_create_track(self, uri: str) -> int:
        """Return the track id for uri, creating a minimal row if needed."""
        row = self._conn.execute(
            "SELECT id FROM tracks WHERE uri = ?", (uri,)
        ).fetchone()
        if row:
            return row["id"]
        cur = self._conn.execute(
            "INSERT INTO tracks (uri) VALUES (?)", (uri,)
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def save_loop(
        self, uri: str, name: str, start_sample: int, end_sample: int
    ) -> int:
        """Save a named loop for the track, returning the new loop id."""
        track_id = self.get_or_create_track(uri)
        cur = self._conn.execute(
            "INSERT INTO loops (track_id, name, start_sample, end_sample) "
            "VALUES (?, ?, ?, ?)",
            (track_id, name, start_sample, end_sample),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_loops(self, uri: str) -> list[sqlite3.Row]:
        """Return all saved loops for a track, ordered by name."""
        return self._conn.execute(
            """
            SELECT loops.id, loops.name, loops.start_sample, loops.end_sample
            FROM loops
            JOIN tracks ON tracks.id = loops.track_id
            WHERE tracks.uri = ?
            ORDER BY loops.name
            """,
            (uri,),
        ).fetchall()

    def delete_loop(self, loop_id: int) -> None:
        self._conn.execute("DELETE FROM loops WHERE id = ?", (loop_id,))
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

    def save_sections(self, uri: str, sections: list[Section]) -> None:
        """Replace all sections for a track with the given list."""
        track_id = self.get_or_create_track(uri)
        self._conn.execute("DELETE FROM sections WHERE track_id = ?", (track_id,))
        self._conn.executemany(
            "INSERT INTO sections (track_id, start_sample, end_sample, label, color) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (track_id, s.start_sample, s.end_sample, s.label, s.color)
                for s in sections
            ],
        )
        self._conn.commit()

    def get_sections(self, uri: str) -> list[Section]:
        """Return saved sections for a track, ordered by start position."""
        rows = self._conn.execute(
            """
            SELECT sections.start_sample, sections.end_sample,
                   sections.label, sections.color
            FROM sections
            JOIN tracks ON tracks.id = sections.track_id
            WHERE tracks.uri = ?
            ORDER BY sections.start_sample
            """,
            (uri,),
        ).fetchall()
        return [
            Section(
                start_sample=r["start_sample"],
                end_sample=r["end_sample"],
                label=r["label"],
                color=r["color"],
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
