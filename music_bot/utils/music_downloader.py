import aiohttp
import yt_dlp
import os
import re
import logging
import asyncio
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse
from utils.config import COOKIES_FILE, FFMPEG_LOCATION, get_anti_block_opts, has_ffmpeg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Public catalogues often expose metadata but not the original media stream.
# Prefer SoundCloud so a YouTube anti-bot challenge does not break every link.
SUPPORTED_SEARCH_SOURCES = ('scsearch', 'ytsearch')


def _configured_search_sources() -> tuple[str, ...]:
    configured = os.getenv('AUDIO_SEARCH_SOURCES', 'scsearch,ytsearch')
    sources = tuple(
        source.strip().lower()
        for source in configured.split(',')
        if source.strip().lower() in SUPPORTED_SEARCH_SOURCES
    )
    return sources or SUPPORTED_SEARCH_SOURCES


SEARCH_SOURCES = _configured_search_sources()


def _is_http_url(value) -> bool:
    """Returns True for direct HTTP(S) media URLs accepted by Telegram."""
    return isinstance(value, str) and value.startswith(('https://', 'http://'))


def _inline_thumbnail_url(entry: dict, source: str) -> str | None:
    """Returns a stable JPEG thumbnail suitable for Telegram inline results."""
    video_id = entry.get('id')
    if source.startswith('yt') and video_id:
        # yt-dlp may select a WebP thumbnail. Telegram inline audio thumbnails
        # are most reliable as JPEG, so use YouTube's stable JPEG endpoint.
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    thumbnail = entry.get('thumbnail')
    if not thumbnail and entry.get('thumbnails'):
        thumbnails = entry.get('thumbnails', [])
        if thumbnails:
            thumbnail = thumbnails[-1].get('url')
    return thumbnail if _is_http_url(thumbnail) else None


def _extract_info_sync(ydl, url_or_query, download=False):
    """Синхронная функция для вызова yt-dlp в отдельном потоке"""
    return ydl.extract_info(url_or_query, download=download)


CATALOG_HOSTS = {
    'open.spotify.com',
    'music.apple.com',
    'music.yandex.ru',
    'music.yandex.com',
}


class _PageMetadataParser(HTMLParser):
    """Collects OpenGraph/Twitter metadata without an extra dependency."""

    def __init__(self):
        super().__init__()
        self.metadata = {}
        self.page_title = ''
        self._inside_title = False

    def handle_starttag(self, tag, attrs):
        attributes = {str(key).lower(): value for key, value in attrs if value is not None}
        if tag.lower() == 'meta':
            key = (attributes.get('property') or attributes.get('name') or '').lower()
            value = attributes.get('content')
            if key and value:
                self.metadata.setdefault(key, unescape(value).strip())
        elif tag.lower() == 'title':
            self._inside_title = True

    def handle_endtag(self, tag):
        if tag.lower() == 'title':
            self._inside_title = False

    def handle_data(self, data):
        if self._inside_title:
            self.page_title += data


def _url_host(url: str) -> str:
    return (urlparse(url).hostname or '').lower().removeprefix('www.')


def _is_vk_audio_url(url: str) -> bool:
    host = _url_host(url)
    path = urlparse(url).path.lower()
    return host in {'vk.com', 'vk.ru', 'm.vk.com', 'm.vk.ru'} and (
        path.startswith('/audio') or path.startswith('/music')
    )


def _is_catalog_reference(url: str) -> bool:
    """True for catalogue pages that do not expose their original media file."""
    return _url_host(url) in CATALOG_HOSTS or _is_vk_audio_url(url)


def _uses_site_cookies(url: str) -> bool:
    # A shared cookies.txt can make Yandex Music return HTTP 431. The yt-dlp
    # extractor then raises the misleading "argument of type bool is not
    # iterable". Public track pages do not require these cookies.
    return not _url_host(url).startswith('music.yandex.')


def _clean_metadata_value(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r'\s+', ' ', unescape(value)).strip(' \t\r\n-|')
    return cleaned or None


def _split_catalog_title(label: str | None, description: str | None, host: str) -> tuple[str | None, str | None]:
    """Extracts title/artist from common Spotify, Apple, Yandex and VK labels."""
    label = _clean_metadata_value(label)
    description = _clean_metadata_value(description)
    if not label:
        return None, None

    label = re.sub(r'^\u200e', '', label)
    if host == 'open.spotify.com':
        match = re.match(
            r'^(.*?)\s+-\s+(?:song|single|album).*?\bby\s+(.+?)(?:\s*\|\s*Spotify)?$',
            label,
            flags=re.IGNORECASE,
        )
        if match:
            return _clean_metadata_value(match.group(1)), _clean_metadata_value(match.group(2))
        label = re.sub(r'\s*\|\s*Spotify\s*$', '', label, flags=re.IGNORECASE)

    if host == 'music.apple.com':
        match = re.match(r'^[\u200e]?(.*?)\s+-\s+Song by\s+(.+?)(?:\s+on Apple Music)?$', label, re.IGNORECASE)
        if match:
            return _clean_metadata_value(match.group(1)), _clean_metadata_value(match.group(2))
        match = re.match(r'^[\u200e]?(.*?)\s+by\s+(.+?)\s+on Apple Music$', label, re.IGNORECASE)
        if match:
            return _clean_metadata_value(match.group(1)), _clean_metadata_value(match.group(2))

    if host.startswith('music.yandex.'):
        label = re.sub(r'\s*[—-]\s*(?:слушать|Listen).*$', '', label, flags=re.IGNORECASE)
        parts = re.split(r'\s+[—–]\s+', label, maxsplit=2)
        if len(parts) >= 2:
            return _clean_metadata_value(parts[0]), _clean_metadata_value(parts[1])

    if host.endswith('vk.com') or host.endswith('vk.ru'):
        parts = re.split(r'\s+[—–-]\s+', label, maxsplit=1)
        if len(parts) == 2:
            return _clean_metadata_value(parts[1]), _clean_metadata_value(parts[0])

    artist = None
    if description:
        match = re.search(r'(?:song|track|песн(?:я|ю))\s+(?:by|от)\s+([^|.]+)', description, re.IGNORECASE)
        if match:
            artist = _clean_metadata_value(match.group(1))

    generic_labels = {'vk', 'вконтакте', 'spotify', 'apple music', 'яндекс музыка', 'yandex music'}
    title = label if label.lower() not in generic_labels else None
    return title, artist


def _cookies_for_url(url: str) -> dict:
    """Reads only Netscape cookies whose domain matches the requested page."""
    if not COOKIES_FILE or not os.path.isfile(COOKIES_FILE):
        return {}

    host = _url_host(url)
    cookies = {}
    try:
        with open(COOKIES_FILE, encoding='utf-8', errors='ignore') as cookie_file:
            for line in cookie_file:
                stripped = line.strip()
                if not stripped:
                    continue
                if line.startswith('#HttpOnly_'):
                    line = line[len('#HttpOnly_'):]
                elif line.lstrip().startswith('#'):
                    continue
                fields = line.rstrip('\n').split('\t')
                if len(fields) != 7:
                    continue
                domain, _, _, _, _, name, value = fields
                cookie_domain = domain.lstrip('.').lower()
                if host == cookie_domain or host.endswith(f'.{cookie_domain}'):
                    cookies[name] = value
    except OSError as error:
        logger.warning(f'Не удалось прочитать cookies-файл: {error}')
    return cookies


async def _fetch_page_metadata(url: str) -> dict:
    """Loads public catalogue metadata used to find an accessible audio source."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; GG-Loader/1.0)',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    }
    timeout = aiohttp.ClientTimeout(total=15)
    parser = _PageMetadataParser()
    extra = {}

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        if _url_host(url) == 'open.spotify.com':
            try:
                async with session.get('https://open.spotify.com/oembed', params={'url': url}) as response:
                    if response.status == 200:
                        extra = await response.json(content_type=None)
            except Exception as error:
                logger.info(f'Spotify oEmbed metadata unavailable: {error}')

        page_headers = {}
        page_cookies = _cookies_for_url(url)
        if page_cookies:
            page_headers['Cookie'] = '; '.join(
                f'{name}={value}' for name, value in page_cookies.items()
            )
        try:
            async with session.get(url, allow_redirects=True, headers=page_headers) as response:
                response.raise_for_status()
                parser.feed(await response.text(errors='ignore'))
        except Exception:
            # Spotify oEmbed is an official metadata endpoint and is sufficient
            # when the public HTML page rejects server-side requests.
            if not extra.get('title'):
                raise

    label = (
        parser.metadata.get('og:title')
        or parser.metadata.get('twitter:title')
        or extra.get('title')
        or parser.page_title
    )
    description = parser.metadata.get('og:description') or parser.metadata.get('twitter:description')
    title, artist = _split_catalog_title(label, description, _url_host(url))
    thumbnail = (
        parser.metadata.get('og:image')
        or parser.metadata.get('twitter:image')
        or extra.get('thumbnail_url')
    )
    return {
        'title': title,
        'artist': artist,
        'thumbnail': thumbnail if _is_http_url(thumbnail) else None,
    }


async def _resolve_catalog_metadata(url: str) -> dict:
    try:
        metadata = await _fetch_page_metadata(url)
    except Exception as error:
        logger.warning(f'Catalogue metadata loading error for {url}: {error}')
        metadata = {}

    if not metadata.get('title'):
        raise ValueError(
            'Не удалось прочитать название трека по этой ссылке. '
            'Убедитесь, что трек публичный; для закрытых VK-страниц добавьте актуальный cookies.txt.'
        )
    return metadata


def _catalog_search_targets(metadata: dict) -> list[str]:
    query = ' - '.join(
        value for value in (metadata.get('artist'), metadata.get('title')) if value
    )
    return [f'{source}1:{query} audio' for source in SEARCH_SOURCES]


async def _download_catalog_match(metadata: dict, temp_dir: str) -> dict:
    errors = []
    for target in _catalog_search_targets(metadata):
        try:
            # Only YouTube benefits from the shared authentication settings.
            use_cookies = target.startswith('ytsearch')
            return await _download_info(target, temp_dir, use_cookies=use_cookies)
        except Exception as error:
            source = target.partition('search')[0]
            logger.warning('Catalog match failed in %s: %s', source, error)
            errors.append(f'{source}: {error}')

    details = '; '.join(errors) if errors else 'источники поиска не настроены'
    raise RuntimeError(f'Не удалось найти доступную версию трека. {details}')


def _unwrap_download_info(info: dict | None) -> dict:
    if info and info.get('entries') is not None:
        return next((entry for entry in info.get('entries') or [] if entry), {})
    return info or {}


def _build_download_opts(temp_dir: str, use_cookies: bool) -> dict:
    opts = {
        **get_anti_block_opts(use_cookies=use_cookies),
        'format': 'bestaudio/best',
        'socket_timeout': 20,
        'noplaylist': True,
        'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            },
        ],
    }
    if FFMPEG_LOCATION:
        opts['ffmpeg_location'] = FFMPEG_LOCATION
    return opts


async def _download_info(target: str, temp_dir: str, use_cookies: bool) -> dict:
    with yt_dlp.YoutubeDL(_build_download_opts(temp_dir, use_cookies)) as ydl:
        info = await asyncio.to_thread(_extract_info_sync, ydl, target, True)
    return _unwrap_download_info(info)


async def _download_thumbnail(url: str | None, temp_dir: str) -> str | None:
    if not _is_http_url(url):
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                content_type = response.headers.get('Content-Type', '').lower()
                extension = '.png' if 'png' in content_type else '.jpg'
                cover_path = os.path.join(temp_dir, f'cover{extension}')
                with open(cover_path, 'wb') as cover:
                    cover.write(await response.read())
                return cover_path
    except Exception as error:
        logger.warning(f'Не удалось скачать обложку по ссылке {url}: {error}')
        return None


async def download_from_url(url: str, temp_dir: str) -> dict:
    """
    Downloads audio directly where a platform exposes it. Catalogue-only links
    (VK Audio, Spotify, Apple Music) are resolved to metadata and matched against
    an accessible audio provider. Yandex Music uses its direct extractor first
    and automatically falls back to matching when that extractor is unavailable.
    """
    result = {
        'success': False,
        'audio_path': None,
        'title': None,
        'artist': None,
        'thumbnail_path': None,
        'album': None,
        'album_url': None,
        'error': None,
    }

    if not has_ffmpeg():
        result['error'] = (
            'В системе не найден FFmpeg. Установите пакет ffmpeg или укажите '
            'в .env FFMPEG_LOCATION на файл ffmpeg/папку с ffmpeg.'
        )
        return result

    metadata = {}
    try:
        if _is_catalog_reference(url) and not _url_host(url).startswith('music.yandex.'):
            metadata = await _resolve_catalog_metadata(url)
            info = await _download_catalog_match(metadata, temp_dir)
        else:
            try:
                info = await _download_info(url, temp_dir, use_cookies=_uses_site_cookies(url))
            except Exception as direct_error:
                if not _is_catalog_reference(url):
                    raise
                logger.warning(f'Direct catalogue download failed, using search fallback: {direct_error}')
                metadata = await _resolve_catalog_metadata(url)
                info = await _download_catalog_match(metadata, temp_dir)

        if not info:
            raise ValueError('Источник не вернул данные о треке.')

        raw_title = metadata.get('title') or info.get('title') or 'Неизвестно'
        raw_artist = metadata.get('artist') or info.get('artist')
        uploader = info.get('uploader') or 'Неизвестно'

        if not raw_artist or raw_artist == uploader:
            for separator in (' - ', ' — ', ' ~ ', ' – '):
                if separator in raw_title:
                    raw_artist, raw_title = (part.strip() for part in raw_title.split(separator, 1))
                    break
            if not raw_artist:
                raw_artist = uploader
        else:
            for separator in (' - ', ' — ', ' – '):
                if separator in raw_title and raw_title.lower().startswith(raw_artist.lower() + separator.strip()):
                    raw_title = raw_title.split(separator, 1)[1].strip()
                    break

        clean_title = re.sub(
            r'\s*[\(\[]\s*(Official|Music|Lyric|Video|Audio|HD|4K|HQ|Visualizer|Live|Prod\..*?|with lyrics).*?[\)\]]',
            '',
            raw_title,
            flags=re.IGNORECASE,
        ).strip()
        result['title'] = clean_title or raw_title
        result['artist'] = raw_artist

        # Album metadata is resolved only after the user chooses a result. This
        # keeps inline search fast while still allowing an album deep-link on
        # the audio message.
        result['album'] = info.get('album')
        result['album_url'] = info.get('album_url')
        if result['album'] and not result['album_url']:
            playlist_url = info.get('playlist_url')
            playlist_title = info.get('playlist_title') or info.get('playlist')
            if (
                _is_http_url(playlist_url)
                and playlist_title
                and str(playlist_title).strip().lower() == str(result['album']).strip().lower()
            ):
                result['album_url'] = playlist_url

        audio_id = info.get('id', 'track')
        expected_path = os.path.join(temp_dir, f'{audio_id}.mp3')
        if os.path.exists(expected_path):
            result['audio_path'] = expected_path
        else:
            mp3_files = [
                os.path.join(temp_dir, filename)
                for filename in os.listdir(temp_dir)
                if filename.lower().endswith('.mp3')
            ]
            if mp3_files:
                result['audio_path'] = max(mp3_files, key=os.path.getmtime)

        thumbnail_url = metadata.get('thumbnail') or info.get('thumbnail')
        if not thumbnail_url and info.get('thumbnails'):
            thumbnails = info.get('thumbnails') or []
            if thumbnails:
                thumbnail_url = thumbnails[-1].get('url')
        result['thumbnail_path'] = await _download_thumbnail(thumbnail_url, temp_dir)

        if result['audio_path'] and os.path.exists(result['audio_path']):
            result['success'] = True
        else:
            result['error'] = 'Файл аудио не был создан после загрузки.'

    except Exception as error:
        logger.error(f'Download error: {error}')
        error_text = str(error)
        lower_error = error_text.lower()
        if 'ffmpeg' in lower_error and ('not found' in lower_error or 'not installed' in lower_error):
            result['error'] = (
                'В системе не найден FFmpeg. Установите пакет ffmpeg или укажите '
                'в .env FFMPEG_LOCATION на файл ffmpeg/папку с ffmpeg.'
            )
        elif 'sign in to confirm' in lower_error or "not a bot" in lower_error:
            result['error'] = (
                'YouTube отклонил запрос сервера как автоматический. '
                'Обновите cookies.txt и подключите динамический PO-token provider '
                'либо временно исключите ytsearch из AUDIO_SEARCH_SOURCES.'
            )
        elif 'unsupported url' in lower_error:
            result['error'] = (
                'Эта ссылка не поддерживается или требует авторизацию. '
                'Проверьте публичность трека и актуальность cookies.txt.'
            )
        else:
            result['error'] = error_text

    return result


async def find_track_album(title: str, artist: str) -> dict | None:
    """Finds album metadata without blocking the inline search itself."""
    if not title or not artist or artist == "Неизвестно":
        return None

    try:
        timeout = aiohttp.ClientTimeout(total=8)
        query = f'artist:"{artist}" track:"{title}"'
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://api.deezer.com/search",
                params={"q": query, "limit": 5},
            ) as response:
                if response.status != 200:
                    return None
                payload = await response.json(content_type=None)

        candidates = payload.get("data") or []
        if not candidates:
            return None

        def score(item: dict) -> int:
            item_title = str(item.get("title") or "").casefold()
            item_artist = str((item.get("artist") or {}).get("name") or "").casefold()
            return int(item_title == title.casefold()) + int(item_artist == artist.casefold())

        match = max(candidates, key=score)
        if score(match) < 1:
            return None
        album = match.get("album") or {}
        album_id = album.get("id")
        album_title = album.get("title")
        if not album_id or not album_title:
            return None

        return {
            "album": album_title,
            "album_url": f"https://api.deezer.com/album/{album_id}/tracks?limit=100",
        }
    except Exception as error:
        logger.info("Album lookup unavailable for %s - %s: %s", artist, title, error)
        return None


async def _get_deezer_album_tracks(
    album_url: str,
    fallback_artist: str,
    limit: int,
) -> list:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(album_url) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)

    tracks = []
    for index, entry in enumerate((payload.get("data") or [])[:limit], start=1):
        title = entry.get("title")
        artist = (entry.get("artist") or {}).get("name") or fallback_artist
        if not title:
            continue
        tracks.append({
            "title": title,
            "artist": artist,
            # download_from_url accepts yt-dlp search targets as well as URLs.
            "url": f"ytsearch1:{artist} - {title} audio",
            "duration": entry.get("duration"),
            "thumbnail": None,
            "track_number": entry.get("track_position") or index,
        })
    return tracks


def _normalize_album_track(entry: dict, fallback_artist: str = "Неизвестно") -> dict | None:
    """Converts a yt-dlp playlist/search entry into the bot track schema."""
    if not entry:
        return None

    video_id = entry.get('id', '')
    url = entry.get('webpage_url') or entry.get('original_url') or entry.get('url', '')
    if video_id and (not url or not str(url).startswith('http')):
        url = f"https://www.youtube.com/watch?v={video_id}"
    if not url:
        return None

    raw_title = entry.get('title') or 'Неизвестно'
    clean_title = re.sub(r'\s*[\(\[]\s*(Official|Music|Lyric|Video|Audio|HD|4K|HQ|Visualizer|Live).*?[\)\]]', '', raw_title, flags=re.IGNORECASE).strip()
    artist = entry.get('artist') or entry.get('uploader') or fallback_artist

    thumbnail = entry.get('thumbnail')
    if not thumbnail and entry.get('thumbnails'):
        thumbnails = entry.get('thumbnails', [])
        if thumbnails:
            thumbnail = thumbnails[-1].get('url')

    return {
        'title': clean_title or raw_title,
        'artist': artist,
        'url': url,
        'duration': entry.get('duration'),
        'thumbnail': thumbnail,
        'track_number': entry.get('track_number') or entry.get('playlist_index'),
    }


async def get_album_tracks(album_url: str, fallback_artist: str = "Неизвестно", limit: int = 60) -> list:
    """Loads album/playlist tracks in the original order when a source exposes an album URL."""
    if not album_url:
        return []

    try:
        if _url_host(album_url) == "api.deezer.com":
            return await _get_deezer_album_tracks(
                album_url,
                fallback_artist=fallback_artist,
                limit=limit,
            )

        ydl_opts = {
            **get_anti_block_opts(),
            'extract_flat': 'in_playlist',
            'playlistend': limit,
            'socket_timeout': 15,
        }
        if FFMPEG_LOCATION:
            ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.wait_for(
                asyncio.to_thread(_extract_info_sync, ydl, album_url, False),
                timeout=20
            )

        entries = info.get('entries') if info else []
        tracks = []
        for entry in entries or []:
            track = _normalize_album_track(entry, fallback_artist=fallback_artist)
            if track:
                tracks.append(track)

        return tracks
    except Exception as e:
        logger.warning(f"Album tracks loading error for {album_url}: {e}")
        return []


async def _search_source(prefix: str, query: str, limit: int) -> list:
    """Returns lightweight search entries without resolving media streams."""
    ydl_opts = {
        **get_anti_block_opts(),
        'extract_flat': 'in_playlist',
        'playlistend': limit,
        'noplaylist': True,
        'socket_timeout': 6,
        'quiet': True,
    }
    if FFMPEG_LOCATION:
        ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION

    search_query = f"{prefix}{limit}:{query}"
    logger.info("Searching metadata in %s: %s", prefix, search_query)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract_info_sync, ydl, search_query, False),
            timeout=8,
        )
    return (info or {}).get('entries') or []


async def search_music(query: str, limit: int = 10) -> list:
    """Searches track metadata quickly enough for Telegram inline queries."""
    if not query.strip() or limit < 1:
        return []

    batches = await asyncio.gather(
        *(_search_source(prefix, query, limit) for prefix in SEARCH_SOURCES),
        return_exceptions=True,
    )

    results = []
    seen_urls = set()
    for prefix, entries in zip(SEARCH_SOURCES, batches):
        if isinstance(entries, Exception):
            logger.warning("Search error in %s: %s", prefix, entries)
            continue

        for entry in entries:
            if not entry or len(results) >= limit:
                break

            video_id = entry.get('id', '')
            source_url = entry.get('webpage_url') or entry.get('original_url') or entry.get('url') or ''
            if prefix.startswith('yt') and video_id and not _is_http_url(source_url):
                source_url = f"https://www.youtube.com/watch?v={video_id}"
            if not _is_http_url(source_url) or source_url in seen_urls:
                continue
            seen_urls.add(source_url)

            raw_title = entry.get('title') or 'Неизвестно'
            clean_title = re.sub(
                r'\s*[\(\[]\s*(Official|Music|Lyric|Video|Audio|HD|4K|HQ|Visualizer|Live).*?[\)\]]',
                '',
                raw_title,
                flags=re.IGNORECASE,
            ).strip()
            artist = entry.get('artist') or entry.get('uploader') or entry.get('channel') or 'Неизвестно'

            results.append({
                'title': clean_title or raw_title,
                'artist': artist,
                'url': source_url,
                'duration': entry.get('duration'),
                'thumbnail': _inline_thumbnail_url(entry, prefix),
                'source': prefix.replace('search', ''),
                'album': entry.get('album'),
                'album_url': entry.get('album_url'),
                'track_number': entry.get('track_number') or entry.get('playlist_index'),
            })

        if len(results) >= limit:
            break

    logger.info("Total found %s results for query: %s", len(results), query)
    return results
