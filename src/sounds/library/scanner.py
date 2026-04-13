"""Background folder scanner.

Walks a directory tree, reads audio metadata via mutagen, and upserts
results into the library database. Runs in a QThread so the UI stays
responsive. Skips files whose mtime hasn't changed since last scan.
"""

import hashlib
from pathlib import Path

from mutagen import File as MutagenFile
from PyQt6.QtCore import QThread, pyqtSignal

from .db import Database

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".m4a", ".opus"}

# Bytes to hash for content fingerprinting (first 1 MB is plenty)
HASH_BYTES = 1024 * 1024


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(HASH_BYTES))
    return h.hexdigest()


def _first(tags: dict, *keys: str) -> str | None:
    """Return the first non-empty value found among the given tag keys."""
    for key in keys:
        val = tags.get(key)
        if val:
            return str(val[0]) if isinstance(val, list) else str(val)
    return None


def _read_metadata(path: Path) -> dict:
    """Read tags and duration from an audio file. Returns empty strings on failure."""
    result: dict = {
        "title": None,
        "artist": None,
        "album": None,
        "year": None,
        "genre": None,
        "duration": None,
    }
    try:
        f = MutagenFile(str(path), easy=True)
        if f is None:
            return result
        tags = f.tags or {}
        result["title"] = _first(tags, "title") or path.stem
        result["artist"] = _first(tags, "artist", "albumartist")
        result["album"] = _first(tags, "album")
        result["year"] = _first(tags, "date", "year")
        result["genre"] = _first(tags, "genre")
        if f.info:
            result["duration"] = f.info.length
    except Exception:  # noqa: BLE001 — malformed files should not crash the scan
        result["title"] = path.stem
    return result


class FolderScanner(QThread):
    """Scans a folder recursively and populates the library database.

    Signals
    -------
    progress(current, total, filename)
        Emitted for each file processed so the UI can show a progress bar.
    finished(added, skipped)
        Emitted when the scan is complete.
    error(message)
        Emitted if an unexpected error stops the scan.
    """

    progress = pyqtSignal(int, int, str)   # current, total, filename
    finished = pyqtSignal(int, int)        # added/updated, skipped
    error = pyqtSignal(str)

    def __init__(self, folder: str, db: Database) -> None:
        super().__init__()
        self._folder = Path(folder)
        self._db = db

    def run(self) -> None:
        try:
            files = [
                p for p in self._folder.rglob("*")
                if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
            ]
            total = len(files)
            added = 0
            skipped = 0

            for i, path in enumerate(files):
                self.progress.emit(i + 1, total, path.name)

                uri = str(path)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    skipped += 1
                    continue

                # Skip if we've already indexed this exact version of the file
                if self._db.get_mtime(uri) == mtime:
                    skipped += 1
                    continue

                content_hash = _hash_file(path)
                meta = _read_metadata(path)

                self._db.upsert_track(
                    uri=uri,
                    mtime=mtime,
                    content_hash=content_hash,
                    title=meta["title"],
                    artist=meta["artist"],
                    album=meta["album"],
                    year=meta["year"],
                    genre=meta["genre"],
                    duration=meta["duration"],
                )
                added += 1

            self.finished.emit(added, skipped)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
