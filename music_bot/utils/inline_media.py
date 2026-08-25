import asyncio
import hashlib
import logging
import os
import secrets
import shutil
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from aiohttp import web

from utils.audio_processor import add_cover_to_mp3, cleanup_temp_files
from utils.config import (
    INLINE_MEDIA_BASE_URL,
    INLINE_MEDIA_CACHE_DIR,
    INLINE_MEDIA_HOST,
    INLINE_MEDIA_PORT,
)
from utils.music_downloader import download_from_url

logger = logging.getLogger(__name__)

_REQUEST_TTL_SECONDS = 20 * 60
_MAX_REQUESTS = 3000
_REQUESTS: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_DOWNLOAD_LOCKS: dict[str, asyncio.Lock] = {}
_RUNNER: web.AppRunner | None = None


def _prune_requests() -> None:
    now = time.monotonic()
    expired = [
        key
        for key, (expires_at, _) in _REQUESTS.items()
        if expires_at <= now
    ]
    for key in expired:
        _REQUESTS.pop(key, None)
    while len(_REQUESTS) > _MAX_REQUESTS:
        _REQUESTS.popitem(last=False)


def register_inline_media_request(track: dict) -> str:
    """Stores a short-lived, unguessable mapping used by result IDs and HTTP URLs."""
    _prune_requests()
    key = secrets.token_urlsafe(18)
    _REQUESTS[key] = (
        time.monotonic() + _REQUEST_TTL_SECONDS,
        dict(track),
    )
    return key


def get_inline_media_request(key: str) -> dict | None:
    _prune_requests()
    cached = _REQUESTS.get(key)
    if not cached:
        return None
    expires_at, track = cached
    if expires_at <= time.monotonic():
        _REQUESTS.pop(key, None)
        return None
    _REQUESTS.move_to_end(key)
    return dict(track)


def create_inline_media_url(track: dict) -> tuple[str, str] | tuple[None, None]:
    """Returns a short public MP3 gateway URL for a newly found track."""
    if not INLINE_MEDIA_BASE_URL:
        return None, None
    key = register_inline_media_request(track)
    return key, f"{INLINE_MEDIA_BASE_URL}/inline/audio/{key}.mp3"


def _track_source_url(track: dict) -> str:
    source_url = track.get("download_url") or track.get("url") or ""
    if not isinstance(source_url, str) or not source_url.startswith(("https://", "http://")):
        raise ValueError("Track does not contain a downloadable HTTP URL")
    return source_url


def _cache_path(source_url: str) -> Path:
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return Path(INLINE_MEDIA_CACHE_DIR) / f"{digest}.mp3"


async def ensure_inline_media_file(track: dict) -> Path:
    """Downloads/converts a search result once and returns a reusable MP3 path."""
    source_url = _track_source_url(track)
    destination = _cache_path(source_url)
    if destination.is_file() and destination.stat().st_size > 0:
        os.utime(destination, None)
        return destination

    lock_key = destination.stem
    lock = _DOWNLOAD_LOCKS.setdefault(lock_key, asyncio.Lock())
    try:
        async with lock:
            if destination.is_file() and destination.stat().st_size > 0:
                os.utime(destination, None)
                return destination

            destination.parent.mkdir(parents=True, exist_ok=True)
            work_dir = destination.parent / f".work-{lock_key}-{uuid.uuid4().hex}"
            work_dir.mkdir(parents=True, exist_ok=True)
            temp_destination = destination.with_suffix(f".{uuid.uuid4().hex}.tmp")

            try:
                result = await download_from_url(source_url, str(work_dir))
                if not result.get("success"):
                    raise RuntimeError(result.get("error") or "audio download failed")

                audio_path = result.get("audio_path")
                cover_path = result.get("thumbnail_path")
                if not audio_path or not os.path.isfile(audio_path):
                    raise RuntimeError("downloader did not produce an audio file")

                processed_path = audio_path
                if cover_path and os.path.isfile(cover_path):
                    processed_path = await add_cover_to_mp3(
                        audio_path,
                        cover_path,
                        result.get("title") or track.get("title") or "Неизвестно",
                        result.get("artist") or track.get("artist") or "Неизвестно",
                    )

                shutil.copyfile(processed_path, temp_destination)
                os.replace(temp_destination, destination)
                return destination
            finally:
                if temp_destination.exists():
                    temp_destination.unlink(missing_ok=True)
                await cleanup_temp_files(str(work_dir))
    finally:
        _DOWNLOAD_LOCKS.pop(lock_key, None)


async def _serve_inline_audio(request: web.Request) -> web.StreamResponse:
    key = request.match_info["key"]
    track = get_inline_media_request(key)
    if not track:
        raise web.HTTPNotFound(text="Inline audio request expired")

    try:
        audio_path = await ensure_inline_media_file(track)
    except Exception as error:
        logger.exception("Inline media gateway failed for %s: %s", key, error)
        raise web.HTTPBadGateway(text="Unable to prepare audio") from error

    return web.FileResponse(
        audio_path,
        headers={
            "Content-Type": "audio/mpeg",
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": 'inline; filename="track.mp3"',
        },
    )


async def start_inline_media_server() -> None:
    global _RUNNER
    if _RUNNER or not INLINE_MEDIA_BASE_URL:
        if not INLINE_MEDIA_BASE_URL:
            logger.warning(
                "INLINE_MEDIA_BASE_URL is not configured; new inline search results "
                "cannot be inserted directly. Personal Telegram history remains available."
            )
        return

    application = web.Application()
    application.router.add_get("/inline/audio/{key}.mp3", _serve_inline_audio)
    _RUNNER = web.AppRunner(application)
    await _RUNNER.setup()
    site = web.TCPSite(_RUNNER, INLINE_MEDIA_HOST, INLINE_MEDIA_PORT)
    await site.start()
    logger.info(
        "Inline media gateway listening on %s:%s (public URL: %s)",
        INLINE_MEDIA_HOST,
        INLINE_MEDIA_PORT,
        INLINE_MEDIA_BASE_URL,
    )


async def stop_inline_media_server() -> None:
    global _RUNNER
    if not _RUNNER:
        return
    await _RUNNER.cleanup()
    _RUNNER = None
