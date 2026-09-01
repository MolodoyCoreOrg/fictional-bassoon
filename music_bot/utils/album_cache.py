import hashlib
from collections import OrderedDict

MAX_ALBUM_CACHE_SIZE = 200
ALBUM_CACHE = OrderedDict()


def cache_album(album: dict) -> str:
    """Stores album payload in memory and returns a short Telegram deep-link key."""
    tracks = album.get("tracks") or []
    track_sources = "|".join(
        str(track.get("url") or track.get("download_url") or "")
        for track in tracks
        if isinstance(track, dict)
    )
    source = "|".join([
        album.get("album") or "",
        album.get("artist") or "",
        album.get("album_url") or "",
        str(len(tracks)),
        track_sources,
    ])
    cache_key = hashlib.sha256(source.encode()).hexdigest()[:24]
    ALBUM_CACHE[cache_key] = album
    ALBUM_CACHE.move_to_end(cache_key)

    while len(ALBUM_CACHE) > MAX_ALBUM_CACHE_SIZE:
        ALBUM_CACHE.popitem(last=False)

    return cache_key


def get_album(cache_key: str) -> dict | None:
    """Returns an album payload by key and refreshes its LRU position."""
    album = ALBUM_CACHE.get(cache_key)
    if album:
        ALBUM_CACHE.move_to_end(cache_key)
    return album
