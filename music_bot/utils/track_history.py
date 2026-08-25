import asyncio
import hashlib
import logging
import sqlite3
import time
from pathlib import Path

from utils.config import TRACK_HISTORY_DB

logger = logging.getLogger(__name__)

HISTORY_DB_PATH = Path(TRACK_HISTORY_DB)


def _source_key(source_url: str) -> str:
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(HISTORY_DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS downloaded_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            file_unique_id TEXT NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            source_url TEXT,
            duration INTEGER,
            downloaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            download_order INTEGER NOT NULL,
            UNIQUE(user_id, file_unique_id)
        );

        CREATE INDEX IF NOT EXISTS idx_downloaded_tracks_user_order
        ON downloaded_tracks(user_id, download_order DESC, id DESC);

        CREATE TABLE IF NOT EXISTS cached_audio (
            source_key TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_unique_id TEXT NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            duration INTEGER,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    return connection


def _row_to_track(row: sqlite3.Row) -> dict:
    return {
        "file_id": row["file_id"],
        "file_unique_id": row["file_unique_id"],
        "title": row["title"],
        "artist": row["artist"],
        "source_url": row["source_url"],
        "duration": row["duration"],
    }


def _remember_audio_reference_sync(
    user_id: int,
    file_id: str,
    file_unique_id: str,
    title: str,
    artist: str,
    duration: int | None,
    source_url: str | None,
    cache_globally: bool,
) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO downloaded_tracks (
                user_id, file_id, file_unique_id, title, artist,
                source_url, duration, downloaded_at, download_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(user_id, file_unique_id) DO UPDATE SET
                file_id = excluded.file_id,
                title = excluded.title,
                artist = excluded.artist,
                source_url = COALESCE(excluded.source_url, downloaded_tracks.source_url),
                duration = excluded.duration,
                downloaded_at = CURRENT_TIMESTAMP,
                download_order = excluded.download_order
            """,
            (
                user_id,
                file_id,
                file_unique_id,
                title,
                artist,
                source_url,
                duration,
                time.time_ns(),
            ),
        )

        if cache_globally and source_url:
            connection.execute(
                """
                INSERT INTO cached_audio (
                    source_key, source_url, file_id, file_unique_id,
                    title, artist, duration, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_key) DO UPDATE SET
                    file_id = excluded.file_id,
                    file_unique_id = excluded.file_unique_id,
                    title = excluded.title,
                    artist = excluded.artist,
                    duration = excluded.duration,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    _source_key(source_url),
                    source_url,
                    file_id,
                    file_unique_id,
                    title,
                    artist,
                    duration,
                ),
            )


async def remember_audio_reference(
    *,
    user_id: int,
    file_id: str,
    file_unique_id: str,
    title: str,
    artist: str,
    duration: int | None = None,
    source_url: str | None = None,
    cache_globally: bool = False,
) -> bool:
    """Stores a reusable Telegram audio file_id in the user's personal history."""
    if not (user_id and file_id and file_unique_id):
        return False

    try:
        await asyncio.to_thread(
            _remember_audio_reference_sync,
            int(user_id),
            file_id,
            file_unique_id,
            title or "Неизвестно",
            artist or "Неизвестно",
            int(duration) if duration else None,
            source_url,
            cache_globally,
        )
        return True
    except Exception:
        logger.exception("Failed to store audio history for user %s", user_id)
        return False


async def remember_audio_message(
    user_id: int,
    message,
    *,
    source_url: str | None = None,
    cache_globally: bool = False,
) -> bool:
    audio = getattr(message, "audio", None)
    if not audio:
        return False

    return await remember_audio_reference(
        user_id=user_id,
        file_id=audio.file_id,
        file_unique_id=audio.file_unique_id,
        title=getattr(audio, "title", None) or "Неизвестно",
        artist=getattr(audio, "performer", None) or "Неизвестно",
        duration=getattr(audio, "duration", None),
        source_url=source_url,
        cache_globally=cache_globally,
    )


def _get_user_history_sync(user_id: int, query: str, limit: int) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT file_id, file_unique_id, title, artist, source_url, duration
            FROM downloaded_tracks
            WHERE user_id = ?
            ORDER BY download_order DESC, id DESC
            LIMIT 500
            """,
            (user_id,),
        ).fetchall()

    needle = query.strip().casefold()
    tracks = []
    for row in rows:
        track = _row_to_track(row)
        haystack = f"{track['artist']} {track['title']}".casefold()
        if needle and needle not in haystack:
            continue
        tracks.append(track)
        if len(tracks) >= limit:
            break
    return tracks


async def get_user_history(user_id: int, query: str = "", limit: int = 25) -> list[dict]:
    return await asyncio.to_thread(
        _get_user_history_sync,
        int(user_id),
        query,
        max(1, min(int(limit), 50)),
    )


def _get_cached_audio_sync(source_url: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT file_id, file_unique_id, title, artist, source_url, duration
            FROM cached_audio
            WHERE source_key = ?
            """,
            (_source_key(source_url),),
        ).fetchone()
    return _row_to_track(row) if row else None


async def get_cached_audio(source_url: str | None) -> dict | None:
    if not source_url:
        return None
    return await asyncio.to_thread(_get_cached_audio_sync, source_url)
