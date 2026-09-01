import aiohttp
import yt_dlp
import os
import re
import logging
import asyncio
from difflib import SequenceMatcher
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlparse
from utils.config import COOKIES_FILE, FFMPEG_LOCATION, get_anti_block_opts, has_ffmpeg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Public catalogues often expose metadata but not the original media stream.
# Prefer SoundCloud so a YouTube anti-bot challenge does not break every link.
SUPPORTED_SEARCH_SOURCES = (
    'scsearch',
    'yandexsearch',
    'vksearch',
    'deezersearch',
    'itunessearch',
    'ytsearch',
)


def _configured_search_sources() -> tuple[str, ...]:
    configured = os.getenv(
        'AUDIO_SEARCH_SOURCES',
        'scsearch,yandexsearch,vksearch,deezersearch,itunessearch,ytsearch',
    )
    requested = {
        source.strip().lower()
        for source in configured.split(',')
        if source.strip().lower() in SUPPORTED_SEARCH_SOURCES
    }
    public_catalogs_enabled = os.getenv(
        'AUDIO_ENABLE_PUBLIC_CATALOGS',
        'true',
    ).strip().lower() in {'1', 'true', 'yes', 'on'}
    if public_catalogs_enabled:
        requested.update({'yandexsearch', 'deezersearch', 'itunessearch'})

    sources = tuple(
        source for source in SUPPORTED_SEARCH_SOURCES if source in requested
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


CATALOG_SEARCH_SCHEME = 'catalogsearch'
SOUNDCLOUD_HOSTS = {'soundcloud.com', 'm.soundcloud.com', 'on.soundcloud.com'}


CATALOG_HOSTS = {
    'open.spotify.com',
    'music.apple.com',
    'music.yandex.ru',
    'music.yandex.com',
    'deezer.com',
    'deezer.page.link',
    'tidal.com',
    'listen.tidal.com',
    'qobuz.com',
    'play.qobuz.com',
    'pandora.com',
    'napster.com',
}

SERVICE_METADATA_VALUES = {
    'яндекс музыка',
    'yandex music',
    'собираем музыку для вас',
    'музыка для вас',
    'vk',
    'vk музыка',
    'vk music',
    'вконтакте',
    'spotify',
    'apple music',
    'deezer',
    'tidal',
    'qobuz',
    'amazon music',
    'pandora',
    'napster',
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


def is_soundcloud_collection_url(url: str) -> bool:
    """Recognizes public SoundCloud Set links, including short share links."""
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower().removeprefix('www.')
    return (
        host in SOUNDCLOUD_HOSTS
        and (
            host == 'on.soundcloud.com'
            or '/sets/' in parsed.path.lower()
        )
    )


def _is_vk_audio_url(url: str) -> bool:
    host = _url_host(url)
    path = urlparse(url).path.lower()
    return host in {'vk.com', 'vk.ru', 'm.vk.com', 'm.vk.ru'} and (
        path.startswith('/audio') or path.startswith('/music')
    )


def _is_catalog_reference(url: str) -> bool:
    """True for catalogue pages that need exact metadata before media lookup."""
    host = _url_host(url)
    return (
        host in CATALOG_HOSTS
        or host.startswith('music.amazon.')
        or _is_vk_audio_url(url)
    )


def _catalog_search_url(title: str, artist: str) -> str:
    return f'{CATALOG_SEARCH_SCHEME}:?{urlencode({"title": title, "artist": artist})}'


def _catalog_search_metadata(url: str) -> dict | None:
    parsed = urlparse(url)
    if parsed.scheme != CATALOG_SEARCH_SCHEME:
        return None
    params = parse_qs(parsed.query)
    title = _clean_metadata_value((params.get('title') or [None])[0])
    artist = _clean_metadata_value((params.get('artist') or [None])[0])
    return {'title': title, 'artist': artist, 'thumbnail': None} if title else None


def _uses_site_cookies(url: str) -> bool:
    # A shared cookies.txt can make Yandex Music return HTTP 431. The yt-dlp
    # extractor then raises the misleading "argument of type bool is not
    # iterable". Public track pages do not require these cookies.
    return not _url_host(url).startswith('music.yandex.')


def _clean_metadata_value(value: str | None) -> str | None:
    if not value:
        return None
    without_markup = re.sub(r'<[^>]+>', ' ', unescape(str(value)))
    cleaned = re.sub(r'\s+', ' ', without_markup).strip(' \t\r\n-|')
    return cleaned or None


def _metadata_key(value: str | None) -> str:
    value = _clean_metadata_value(value) or ''
    return re.sub(r'[^\w]+', ' ', value.casefold(), flags=re.UNICODE).strip()


def _metadata_is_usable(metadata: dict | None) -> bool:
    """Rejects provider landing-page copy that must never become a search query."""
    if not metadata:
        return False

    title = _metadata_key(metadata.get('title'))
    artist = _metadata_key(metadata.get('artist'))
    if len(title) < 2 or title in SERVICE_METADATA_VALUES:
        return False
    if artist in SERVICE_METADATA_VALUES:
        return False

    combined = f'{title} {artist}'.strip()
    placeholder_phrases = (
        'собираем музыку для вас',
        'слушайте музыку',
        'music for everyone',
        'listen to music',
    )
    return not any(phrase in combined for phrase in placeholder_phrases)


def _split_catalog_title(label: str | None, description: str | None, host: str) -> tuple[str | None, str | None]:
    """Extracts title/artist from common music-catalogue labels."""
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
        # Russian Apple Music pages use a localized page title such as:
        # Песня «Track (feat. Guest)» (Artist & Artist) в Apple Music.
        match = re.match(
            r'^(?:Песня|Song)\s+[«“"](.+?)[»”"]\s+\((.+?)\)\s+(?:в|on)\s+Apple Music$',
            label,
            flags=re.IGNORECASE,
        )
        if match:
            return _clean_metadata_value(match.group(1)), _clean_metadata_value(match.group(2))

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

    service_name = r'(?:Spotify|Apple Music|Яндекс Музыка|Yandex Music|VK Музыка|VK Music|Deezer|TIDAL|Qobuz|Amazon Music|Pandora|Napster)'
    label = re.sub(rf'\s*[|·]\s*{service_name}\s*$', '', label, flags=re.IGNORECASE)
    match = re.match(
        rf'^(.*?)\s+(?:by|от)\s+(.+?)(?:\s+on\s+{service_name})?$',
        label,
        flags=re.IGNORECASE,
    )
    if match:
        return _clean_metadata_value(match.group(1)), _clean_metadata_value(match.group(2))

    artist = None
    if description:
        match = re.search(r'(?:song|track|песн(?:я|ю))\s+(?:by|от)\s+([^|.]+)', description, re.IGNORECASE)
        if match:
            artist = _clean_metadata_value(match.group(1))

    title = label if _metadata_key(label) not in SERVICE_METADATA_VALUES else None
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


def _yandex_track_ids(url: str) -> tuple[str | None, str | None]:
    path = urlparse(url).path
    track_match = re.search(r'/track/(\d+)', path)
    album_match = re.search(r'/album/(\d+)', path)
    return (
        track_match.group(1) if track_match else None,
        album_match.group(1) if album_match else None,
    )


def _iter_yandex_track_candidates(payload):
    """Yields track dictionaries from all currently used Yandex response shapes."""
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_yandex_track_candidates(item)
        return
    if not isinstance(payload, dict):
        return

    looks_like_track = (
        payload.get('title')
        and isinstance(payload.get('artists'), list)
        and (
            'durationMs' in payload
            or 'albums' in payload
            or 'available' in payload
            or 'realId' in payload
        )
    )
    if looks_like_track:
        yield payload

    for key in ('result', 'track', 'tracks', 'items', 'results', 'volumes'):
        nested = payload.get(key)
        if nested is not None and nested is not payload:
            yield from _iter_yandex_track_candidates(nested)


def _metadata_from_yandex_payload(payload, track_id: str | None = None) -> dict | None:
    candidates = list(_iter_yandex_track_candidates(payload))
    if not candidates:
        return None

    track = None
    if track_id is not None:
        wanted_id = str(track_id)
        track = next(
            (
                item for item in candidates
                if str(item.get('id') or item.get('realId') or '') == wanted_id
            ),
            None,
        )
        if track is None:
            return None
    else:
        track = candidates[0]

    artists = ', '.join(
        name
        for name in (
            _clean_metadata_value(artist.get('name'))
            for artist in (track.get('artists') or [])
            if isinstance(artist, dict)
        )
        if name
    ) or None
    albums = [album for album in (track.get('albums') or []) if isinstance(album, dict)]
    album = albums[0] if albums else {}
    cover_uri = track.get('coverUri') or album.get('coverUri')
    thumbnail = None
    if cover_uri:
        thumbnail = str(cover_uri).replace('%%', '1000x1000')
        if thumbnail.startswith('//'):
            thumbnail = f'https:{thumbnail}'
        elif not thumbnail.startswith(('http://', 'https://')):
            thumbnail = f'https://{thumbnail.lstrip("/")}'

    album_id = album.get('id')
    metadata = {
        'title': _clean_metadata_value(track.get('title')),
        'artist': artists,
        'thumbnail': thumbnail if _is_http_url(thumbnail) else None,
        'album': _clean_metadata_value(album.get('title')),
        'album_url': (
            f'https://music.yandex.ru/album/{album_id}'
            if album_id is not None
            else None
        ),
    }
    return metadata if _metadata_is_usable(metadata) else None


async def _fetch_yandex_track_metadata(url: str) -> dict | None:
    track_id, album_id = _yandex_track_ids(url)
    if not track_id:
        return None

    origin_host = _url_host(url)
    handler_hosts = [origin_host]
    alternate_host = (
        'music.yandex.com'
        if origin_host == 'music.yandex.ru'
        else 'music.yandex.ru'
    )
    if alternate_host not in handler_hosts:
        handler_hosts.append(alternate_host)

    base_headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; GG-Loader/1.0)',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    }
    ajax_headers = {
        **base_headers,
        'Referer': url,
        'X-Requested-With': 'XMLHttpRequest',
        'X-Retpath-Y': url,
    }
    requests = [
        (
            f'https://api.music.yandex.net/tracks/{track_id}',
            None,
            base_headers,
        ),
    ]
    for host in handler_hosts:
        base_url = f'https://{host}'
        common_params = {
            'lang': 'ru',
            'external-domain': host,
            'overembed': 'false',
        }
        requests.extend([
            (
                f'{base_url}/handlers/track-entries.jsx',
                {
                    **common_params,
                    'entries': track_id,
                    'strict': 'true',
                },
                ajax_headers,
            ),
            (
                f'{base_url}/handlers/track.jsx',
                {
                    **common_params,
                    'track': f'{track_id}:{album_id}' if album_id else track_id,
                },
                ajax_headers,
            ),
        ])
        if album_id:
            requests.append((
                f'{base_url}/handlers/album.jsx',
                {
                    **common_params,
                    'album': album_id,
                },
                ajax_headers,
            ))

    timeout = aiohttp.ClientTimeout(total=10)
    errors = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for endpoint, params, request_headers in requests:
            try:
                async with session.get(
                    endpoint,
                    params=params,
                    headers=request_headers,
                ) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                metadata = _metadata_from_yandex_payload(payload, track_id)
                if metadata:
                    return metadata
                errors.append(f'{endpoint}: пустые метаданные')
            except Exception as error:
                errors.append(f'{endpoint}: {error}')

    raise RuntimeError('; '.join(errors))


def _vk_audio_reference(url: str) -> str | None:
    match = re.search(
        r'/audio(-?\d+)_(\d+)(?:_([A-Za-z0-9]+))?',
        urlparse(url).path,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return '_'.join(part for part in match.groups() if part)


async def _fetch_vk_track_metadata(url: str) -> dict | None:
    reference = _vk_audio_reference(url)
    access_token = os.getenv('VK_ACCESS_TOKEN', '').strip()
    if not reference or not access_token:
        return None

    params = {
        'audios': reference,
        'access_token': access_token,
        'v': os.getenv('VK_API_VERSION', '5.199').strip() or '5.199',
    }
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            'https://api.vk.com/method/audio.getById',
            params=params,
        ) as response:
            payload = await response.json(content_type=None)

    if payload.get('error'):
        error = payload['error']
        raise RuntimeError(
            f"VK API {error.get('error_code')}: {error.get('error_msg')}"
        )

    items = payload.get('response') or []
    item = next((entry for entry in items if isinstance(entry, dict)), None)
    if not item:
        return None

    album = item.get('album') or {}
    thumb = album.get('thumb') or {}
    thumbnail = next(
        (
            thumb.get(name)
            for name in ('photo_1200', 'photo_600', 'photo_300', 'photo_270')
            if _is_http_url(thumb.get(name))
        ),
        None,
    )
    metadata = {
        'title': _clean_metadata_value(item.get('title')),
        'artist': _clean_metadata_value(item.get('artist')),
        'thumbnail': thumbnail,
        'album': _clean_metadata_value(album.get('title')),
        'album_url': None,
        'download_url': item.get('url') if _is_http_url(item.get('url')) else None,
    }
    return metadata if _metadata_is_usable(metadata) else None


def _apple_track_id(url: str) -> str | None:
    """Extracts a song ID without mistaking an Apple album ID for a track."""
    parsed = urlparse(url)
    query_id = (parse_qs(parsed.query).get('i') or [None])[0]
    if query_id and str(query_id).isdigit():
        return str(query_id)

    match = re.search(
        r'/(?:song|music-video)/(?:[^/]+/)?(\d+)/?$',
        parsed.path,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def _metadata_from_itunes_payload(
    payload: dict,
    track_id: str | None = None,
) -> dict | None:
    results = payload.get('results') if isinstance(payload, dict) else None
    candidates = [
        item for item in (results or [])
        if isinstance(item, dict)
        and _clean_metadata_value(item.get('trackName'))
        and _clean_metadata_value(item.get('artistName'))
    ]
    if track_id is not None:
        wanted_id = str(track_id)
        exact = next(
            (
                item for item in candidates
                if str(item.get('trackId') or '') == wanted_id
            ),
            None,
        )
        if not exact:
            return None
        candidates = [exact]
    if not candidates:
        return None

    item = candidates[0]
    artwork = item.get('artworkUrl100')
    if _is_http_url(artwork):
        artwork = re.sub(r'/\d+x\d+bb\.', '/600x600bb.', artwork)
    collection_id = item.get('collectionId')
    metadata = {
        'title': _clean_metadata_value(item.get('trackName')),
        'artist': _clean_metadata_value(item.get('artistName')),
        'thumbnail': artwork if _is_http_url(artwork) else None,
        'album': _clean_metadata_value(item.get('collectionName')),
        'album_url': (
            f'https://itunes.apple.com/lookup?id={collection_id}&entity=song&limit=200'
            if collection_id is not None
            else None
        ),
    }
    return metadata if _metadata_is_usable(metadata) else None


async def _fetch_apple_track_metadata(url: str) -> dict | None:
    track_id = _apple_track_id(url)
    if not track_id:
        return None

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            'https://itunes.apple.com/lookup',
            params={
                'id': track_id,
                'entity': 'song',
                'country': os.getenv('ITUNES_COUNTRY', 'RU').strip() or 'RU',
            },
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
    return _metadata_from_itunes_payload(payload, track_id)


async def _fetch_provider_metadata(url: str) -> dict | None:
    host = _url_host(url)
    if host.startswith('music.yandex.'):
        return await _fetch_yandex_track_metadata(url)
    if host == 'music.apple.com':
        return await _fetch_apple_track_metadata(url)
    if _is_vk_audio_url(url):
        return await _fetch_vk_track_metadata(url)
    return None


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
    artist = artist or _clean_metadata_value(extra.get('author_name'))
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
    """Resolves exact track metadata and rejects generic provider landing pages."""
    errors = []

    try:
        provider_metadata = await _fetch_provider_metadata(url)
        if _metadata_is_usable(provider_metadata):
            return provider_metadata
    except Exception as error:
        logger.warning('Provider metadata loading error for %s: %s', url, error)
        errors.append(str(error))

    # The current yt-dlp Yandex extractor calls the same AJAX endpoint and
    # can turn a non-fatal 404 into "argument of type bool is not iterable".
    # Our provider resolver above already tries the API plus both regional AJAX
    # hosts, so avoid repeating the broken extractor path for Yandex links.
    if not _url_host(url).startswith('music.yandex.'):
        opts = {
            **get_anti_block_opts(use_cookies=_uses_site_cookies(url)),
            'skip_download': True,
            'noplaylist': True,
            'socket_timeout': 15,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = await asyncio.wait_for(
                    asyncio.to_thread(_extract_info_sync, ydl, url, False),
                    timeout=25,
                )
            extracted = _metadata_from_info(info)
            if _metadata_is_usable(extracted):
                return extracted
            errors.append('экстрактор вернул служебные метаданные площадки')
        except Exception as error:
            logger.info('yt-dlp metadata extraction failed for %s: %s', url, error)
            errors.append(str(error))

    try:
        page_metadata = await _fetch_page_metadata(url)
        if _metadata_is_usable(page_metadata):
            return page_metadata
        errors.append('страница не содержит точного названия трека')
    except Exception as error:
        logger.warning('Catalogue metadata loading error for %s: %s', url, error)
        errors.append(str(error))

    vk_hint = (
        ' Для VK Audio укажите пользовательский VK_ACCESS_TOKEN с доступом '
        'к audio.getById либо добавьте актуальный cookies.txt.'
        if _is_vk_audio_url(url) and not os.getenv('VK_ACCESS_TOKEN', '').strip()
        else ''
    )
    details = '; '.join(message for message in errors if message)
    raise ValueError(
        'Не удалось получить точные название и исполнителя по этой ссылке; '
        'случайный похожий трек отправлен не будет.'
        f'{vk_hint} Подробности: {details}'
    )


def _catalog_search_query(metadata: dict) -> str:
    """Builds a provider-neutral query without source-specific filler words."""
    return ' - '.join(
        value for value in (metadata.get('artist'), metadata.get('title')) if value
    )


def _normalize_match_text(value: str | None) -> str:
    value = _clean_metadata_value(value) or ''
    return re.sub(r'[^\w]+', ' ', value.casefold(), flags=re.UNICODE).strip()


def _text_match_score(wanted: str | None, candidate: str | None) -> float:
    wanted_text = _normalize_match_text(wanted)
    candidate_text = _normalize_match_text(candidate)
    if not wanted_text or not candidate_text:
        return 0.0
    if wanted_text == candidate_text:
        return 1.0

    sequence_score = SequenceMatcher(None, wanted_text, candidate_text).ratio()
    wanted_tokens = set(wanted_text.split())
    candidate_tokens = set(candidate_text.split())
    containment_score = (
        len(wanted_tokens & candidate_tokens) / len(wanted_tokens)
        if wanted_tokens
        else 0.0
    )
    return max(sequence_score, containment_score)


def _candidate_match_score(metadata: dict, entry: dict) -> float:
    """Scores title and artist separately so unrelated search hits are rejected."""
    wanted_title = metadata.get('title')
    wanted_artist = metadata.get('artist')
    candidate_title = entry.get('track') or entry.get('title')
    candidate_artist = (
        entry.get('artist') or entry.get('uploader') or entry.get('channel')
    )
    candidate_combined = ' '.join(
        value for value in (candidate_artist, candidate_title) if value
    )

    title_score = max(
        _text_match_score(wanted_title, candidate_title),
        _text_match_score(wanted_title, candidate_combined),
    )
    if wanted_artist:
        artist_score = max(
            _text_match_score(wanted_artist, candidate_artist),
            _text_match_score(wanted_artist, candidate_combined),
        )
        score = (title_score * 0.7) + (artist_score * 0.3)
    else:
        score = title_score

    wanted_key = _metadata_key(f'{wanted_artist or ""} {wanted_title or ""}')
    candidate_key = _metadata_key(candidate_combined)
    variant_markers = {
        'remix', 'cover', 'karaoke', 'instrumental', 'live',
        'sped up', 'slowed', 'nightcore', 'ремикс', 'кавер', 'караоке',
        'инструментал', 'концерт',
    }
    if any(
        marker in candidate_key and marker not in wanted_key
        for marker in variant_markers
    ):
        score -= 0.2
    return max(0.0, min(score, 1.0))


def _configured_min_match() -> float:
    try:
        value = float(os.getenv('AUDIO_FALLBACK_MIN_MATCH', '0.78'))
    except ValueError:
        value = 0.78
    return max(0.5, min(value, 1.0))


def _candidate_download_target(entry: dict, source: str) -> str | None:
    direct_url = entry.get('download_url')
    if _is_http_url(direct_url):
        return direct_url

    target = entry.get('webpage_url') or entry.get('original_url') or entry.get('url')
    video_id = entry.get('id')
    if source.startswith('yt') and video_id and not _is_http_url(target):
        target = f'https://www.youtube.com/watch?v={video_id}'
    return target if _is_http_url(target) else None


async def _download_catalog_match(metadata: dict, temp_dir: str) -> dict:
    """
    Searches several candidates per provider and downloads only a sufficiently
    close title/artist match. A failed lookup must never become a random track.
    """
    query = _catalog_search_query(metadata)
    if not query:
        raise ValueError('Не удалось определить название трека для резервного поиска.')

    errors = {}
    seen_targets = set()
    minimum_score = _configured_min_match()
    try:
        candidate_limit = int(os.getenv('AUDIO_FALLBACK_CANDIDATES', '3'))
    except ValueError:
        candidate_limit = 3
    candidate_limit = max(1, min(candidate_limit, 10))

    for source in SEARCH_SOURCES:
        source_name = source.replace('search', '')
        try:
            entries = await _search_source(source, query, candidate_limit)
        except Exception as error:
            logger.warning('Catalog search failed in %s: %s', source_name, error)
            errors[source_name] = str(error)
            continue

        if not entries:
            errors[source_name] = 'ничего не найдено'
            continue

        scored_entries = sorted(
            (
                (_candidate_match_score(metadata, entry), entry)
                for entry in entries
                if entry
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        eligible_entries = [
            (score, entry)
            for score, entry in scored_entries
            if score >= minimum_score
        ]
        if not eligible_entries:
            best_score = scored_entries[0][0] if scored_entries else 0.0
            errors[source_name] = (
                f'нет точного совпадения '
                f'(лучшее {best_score:.2f}, минимум {minimum_score:.2f})'
            )
            continue

        for score, entry in eligible_entries:
            target = _candidate_download_target(entry, source)
            if not target or target in seen_targets:
                continue
            seen_targets.add(target)
            logger.info(
                'Downloading verified %s candidate with match score %.2f',
                source_name,
                score,
            )
            try:
                info = await _download_info(
                    target,
                    temp_dir,
                    use_cookies=source.startswith('yt'),
                )
                if info:
                    return info
                errors[source_name] = 'источник не вернул данные о треке'
            except Exception as error:
                logger.warning(
                    'Catalog candidate failed in %s (%s): %s',
                    source_name,
                    target,
                    error,
                )
                errors[source_name] = str(error)

    details = '; '.join(
        f'{source}: {message}' for source, message in errors.items()
    ) or 'источники поиска не настроены'
    raise RuntimeError(
        'Не удалось найти достаточно точное совпадение; '
        f'случайный трек отправлен не будет. {details}'
    )


def _unwrap_download_info(info: dict | None) -> dict:
    if info and info.get('entries') is not None:
        return next((entry for entry in info.get('entries') or [] if entry), {})
    return info or {}



def _metadata_from_info(info: dict | None) -> dict:
    info = _unwrap_download_info(info)
    raw_title = _clean_metadata_value(info.get('track') or info.get('title'))
    artist = _clean_metadata_value(
        info.get('artist')
        or info.get('creator')
        or info.get('uploader')
        or info.get('channel')
    )
    title = raw_title

    if title and not info.get('track'):
        for separator in (' - ', ' — ', ' – ', ' ~ '):
            if separator in title:
                possible_artist, possible_title = title.split(separator, 1)
                if not artist or artist.casefold() in possible_artist.casefold():
                    artist = _clean_metadata_value(possible_artist) or artist
                    title = _clean_metadata_value(possible_title)
                break

    thumbnail = info.get('thumbnail')
    if not thumbnail and info.get('thumbnails'):
        thumbnails = info.get('thumbnails') or []
        if thumbnails:
            thumbnail = thumbnails[-1].get('url')

    return {
        'title': title,
        'artist': artist,
        'thumbnail': thumbnail if _is_http_url(thumbnail) else None,
    }


async def _extract_reference_metadata(url: str) -> dict:
    """Extracts trustworthy metadata for a failed direct media URL."""
    return await _resolve_catalog_metadata(url)


def _is_youtube_target(target: str) -> bool:
    host = _url_host(target)
    return (
        target.startswith('ytsearch')
        or host == 'youtu.be'
        or host.endswith('youtube.com')
    )


def _merge_extractor_args(base: dict | None, extra: dict | None) -> dict:
    merged = {
        extractor: dict(arguments)
        for extractor, arguments in (base or {}).items()
    }
    for extractor, arguments in (extra or {}).items():
        merged.setdefault(extractor, {}).update(arguments)
    return merged


def _youtube_download_profiles(target: str) -> list[dict]:
    profiles = [{}]
    if not _is_youtube_target(target):
        return profiles

    # web_safari exposes HLS audio that currently does not require a GVS PO token.
    # android_vr is a final cookie-free client fallback for public videos.
    profiles.extend([
        {
            'format': 'bestaudio[protocol^=m3u8]/bestaudio/best',
            'extractor_args': {
                'youtube': {'player_client': ['web_safari']},
            },
        },
        {
            '_use_cookies': False,
            'extractor_args': {
                'youtube': {'player_client': ['android_vr']},
            },
        },
    ])
    return profiles


def _build_download_opts(
    temp_dir: str,
    use_cookies: bool,
    overrides: dict | None = None,
) -> dict:
    opts = {
        **get_anti_block_opts(use_cookies=use_cookies),
        'format': 'bestaudio/best',
        'socket_timeout': 20,
        'noplaylist': True,
        'continuedl': False,
        'overwrites': True,
        # Keep ranges below YouTube's documented 10 MB throttling threshold.
        'http_chunk_size': 8 * 1024 * 1024,
        'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            },
        ],
    }

    overrides = dict(overrides or {})
    extractor_args = overrides.pop('extractor_args', None)
    if extractor_args:
        opts['extractor_args'] = _merge_extractor_args(
            opts.get('extractor_args'),
            extractor_args,
        )
    opts.update(overrides)

    if FFMPEG_LOCATION:
        opts['ffmpeg_location'] = FFMPEG_LOCATION
    return opts


async def _download_info(target: str, temp_dir: str, use_cookies: bool) -> dict:
    last_error = None
    for attempt, raw_profile in enumerate(_youtube_download_profiles(target), start=1):
        profile = dict(raw_profile)
        profile_use_cookies = profile.pop('_use_cookies', use_cookies)
        if attempt > 1:
            logger.info('Retrying YouTube download with network profile %s', attempt)
        try:
            with yt_dlp.YoutubeDL(
                _build_download_opts(temp_dir, profile_use_cookies, profile)
            ) as ydl:
                info = await asyncio.to_thread(_extract_info_sync, ydl, target, True)
            return _unwrap_download_info(info)
        except Exception as error:
            last_error = error
            if not _is_youtube_target(target):
                raise
            logger.warning('YouTube download profile %s failed: %s', attempt, error)

    if last_error:
        raise last_error
    return {}


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
    are resolved to exact provider metadata and matched against an accessible
    audio source only when title/artist similarity passes the safety threshold.
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
        internal_metadata = _catalog_search_metadata(url)
        if internal_metadata:
            metadata = internal_metadata
            info = await _download_catalog_match(metadata, temp_dir)
        elif _is_catalog_reference(url):
            metadata = await _resolve_catalog_metadata(url)
            direct_target = metadata.get('download_url')
            if direct_target:
                try:
                    info = await _download_info(
                        direct_target,
                        temp_dir,
                        use_cookies=False,
                    )
                except Exception as direct_error:
                    logger.warning(
                        'Exact provider URL failed, using verified fallback: %s',
                        direct_error,
                    )
                    info = await _download_catalog_match(metadata, temp_dir)
            else:
                info = await _download_catalog_match(metadata, temp_dir)
        else:
            try:
                info = await _download_info(
                    url,
                    temp_dir,
                    use_cookies=_uses_site_cookies(url),
                )
                if not info:
                    raise ValueError('источник не вернул данные о треке')
            except Exception as direct_error:
                logger.warning(
                    'Direct download failed, using cross-provider fallback: %s',
                    direct_error,
                )
                try:
                    metadata = await _extract_reference_metadata(url)
                    info = await _download_catalog_match(metadata, temp_dir)
                except Exception as fallback_error:
                    raise RuntimeError(
                        'Прямая загрузка не удалась: '
                        f'{direct_error}. Резервный поиск: {fallback_error}'
                    ) from fallback_error

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
        result['album'] = metadata.get('album') or info.get('album')
        result['album_url'] = metadata.get('album_url') or info.get('album_url')
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


def _configured_collection_limit() -> int:
    """Bounds one requested collection to avoid unbounded server work."""
    try:
        value = int(os.getenv('AUDIO_COLLECTION_MAX_TRACKS', '100'))
    except ValueError:
        value = 100
    return max(1, min(value, 200))


def _normalize_soundcloud_collection_track(
    entry: dict,
    index: int,
    fallback_artist: str,
) -> dict | None:
    if not isinstance(entry, dict):
        return None

    url = (
        entry.get('webpage_url')
        or entry.get('original_url')
        or entry.get('url')
    )
    if not _is_http_url(url):
        return None

    title = _clean_metadata_value(entry.get('track') or entry.get('title'))
    if not title:
        return None

    artist = _clean_metadata_value(
        entry.get('artist')
        or entry.get('uploader')
        or entry.get('channel')
        or fallback_artist
    ) or 'Неизвестно'
    thumbnail = entry.get('thumbnail')
    if not thumbnail and entry.get('thumbnails'):
        thumbnails = entry.get('thumbnails') or []
        if thumbnails:
            thumbnail = thumbnails[-1].get('url')

    return {
        'title': title,
        'artist': artist,
        'url': url,
        'duration': entry.get('duration'),
        'thumbnail': thumbnail if _is_http_url(thumbnail) else None,
        'track_number': entry.get('playlist_index') or index,
    }


def _soundcloud_collection_from_info(
    info: dict | None,
    source_url: str,
    limit: int,
) -> dict | None:
    """Converts yt-dlp Set metadata without changing the platform order."""
    if not isinstance(info, dict):
        return None

    raw_entries = list(info.get('entries') or [])
    extractor_name = str(
        info.get('extractor_key') or info.get('extractor') or ''
    ).casefold()
    resolved_url = info.get('webpage_url') or info.get('original_url') or source_url
    is_collection = (
        info.get('_type') == 'playlist'
        or 'soundcloudset' in extractor_name
        or '/sets/' in urlparse(str(resolved_url)).path.lower()
    )
    if not is_collection or not raw_entries:
        return None

    fallback_artist = _clean_metadata_value(
        info.get('artist') or info.get('uploader') or info.get('channel')
    ) or 'Неизвестно'
    tracks = []
    for index, entry in enumerate(raw_entries[:limit], start=1):
        track = _normalize_soundcloud_collection_track(
            entry,
            index=index,
            fallback_artist=fallback_artist,
        )
        if track:
            tracks.append(track)

    if not tracks:
        return None

    declared_total = info.get('playlist_count') or info.get('n_entries')
    try:
        total = max(int(declared_total), len(raw_entries))
    except (TypeError, ValueError):
        total = len(raw_entries)
    total = max(total, len(tracks))

    thumbnail = info.get('thumbnail')
    if not thumbnail and info.get('thumbnails'):
        thumbnails = info.get('thumbnails') or []
        if thumbnails:
            thumbnail = thumbnails[-1].get('url')

    return {
        'title': _clean_metadata_value(
            info.get('playlist_title') or info.get('title')
        ) or 'SoundCloud Set',
        'artist': fallback_artist,
        'url': resolved_url,
        'thumbnail': thumbnail if _is_http_url(thumbnail) else None,
        'tracks': tracks,
        'total': total,
        'unavailable': max(0, total - len(tracks)),
        'truncated': total > limit or len(raw_entries) > limit,
        'limit': limit,
    }


async def get_soundcloud_collection(
    url: str,
    limit: int | None = None,
) -> dict | None:
    """Reads a SoundCloud Set as ordered lightweight track references."""
    if not is_soundcloud_collection_url(url):
        return None

    collection_limit = limit or _configured_collection_limit()
    ydl_opts = {
        **get_anti_block_opts(),
        # SoundCloudSetIE flat entries can contain only an API URL and track ID.
        # Resolve track metadata, but never download media during this first pass.
        'extract_flat': False,
        'skip_download': True,
        'playlistend': collection_limit + 1,
        'noplaylist': False,
        'socket_timeout': 20,
    }
    if FFMPEG_LOCATION:
        ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.wait_for(
                asyncio.to_thread(_extract_info_sync, ydl, url, False),
                timeout=max(45, min(collection_limit * 3, 300)),
            )
    except Exception as error:
        logger.warning('SoundCloud collection loading error for %s: %s', url, error)
        raise RuntimeError(
            f'Не удалось прочитать сборник SoundCloud: {error}'
        ) from error

    collection = _soundcloud_collection_from_info(
        info,
        source_url=url,
        limit=collection_limit,
    )
    if not collection:
        # A short on.soundcloud.com link can also point to one ordinary track.
        # In that case the caller must continue through the single-track path.
        if _url_host(url) == 'on.soundcloud.com':
            return None
        raise RuntimeError(
            'SoundCloud не вернул состав сборника. '
            'Проверьте, что Set публичный и ссылка доступна без входа.'
        )
    return collection


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
            # Resolve through the configured catalogue source chain at download time.
            "url": _catalog_search_url(title, artist),
            "duration": entry.get("duration"),
            "thumbnail": None,
            "track_number": entry.get("track_position") or index,
        })
    return tracks


async def _get_yandex_album_tracks(
    album_url: str,
    fallback_artist: str,
    limit: int,
) -> list:
    album_match = re.search(r'/album/(\d+)', urlparse(album_url).path)
    if not album_match:
        return []

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f'https://api.music.yandex.net/albums/{album_match.group(1)}/with-tracks'
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)

    result = payload.get('result') or {}
    raw_volumes = result.get('volumes') or []
    items = [
        item
        for volume in raw_volumes
        for item in (volume if isinstance(volume, list) else [])
        if isinstance(item, dict)
    ]
    tracks = []
    for index, item in enumerate(items[:limit], start=1):
        track = _yandex_search_entry(item)
        if not track:
            continue
        track['artist'] = track.get('artist') or fallback_artist
        track['track_number'] = track.get('track_number') or index
        tracks.append({
            'title': track['title'],
            'artist': track['artist'],
            'url': track['webpage_url'],
            'duration': track.get('duration'),
            'thumbnail': track.get('thumbnail'),
            'track_number': track.get('track_number'),
        })
    return tracks


async def _get_itunes_album_tracks(
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
    for item in payload.get('results') or []:
        if len(tracks) >= limit:
            break
        if item.get('wrapperType') != 'track' or item.get('kind') != 'song':
            continue
        track = _itunes_search_entry(item)
        if not track:
            continue
        tracks.append({
            'title': track['title'],
            'artist': track.get('artist') or fallback_artist,
            'url': track['webpage_url'],
            'duration': track.get('duration'),
            'thumbnail': track.get('thumbnail'),
            'track_number': track.get('track_number') or len(tracks) + 1,
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
        host = _url_host(album_url)
        if host == "api.deezer.com":
            return await _get_deezer_album_tracks(
                album_url,
                fallback_artist=fallback_artist,
                limit=limit,
            )
        if host.startswith("music.yandex."):
            return await _get_yandex_album_tracks(
                album_url,
                fallback_artist=fallback_artist,
                limit=limit,
            )
        if host == "itunes.apple.com":
            return await _get_itunes_album_tracks(
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


def _yandex_search_entry(item: dict) -> dict | None:
    track_id = item.get('id')
    title = _clean_metadata_value(item.get('title'))
    artists = ', '.join(
        name
        for name in (
            _clean_metadata_value(artist.get('name'))
            for artist in (item.get('artists') or [])
            if isinstance(artist, dict)
        )
        if name
    ) or 'Неизвестно'
    albums = [album for album in (item.get('albums') or []) if isinstance(album, dict)]
    album = albums[0] if albums else {}
    album_id = album.get('id')
    if not title or track_id is None:
        return None

    if album_id is not None:
        webpage_url = f'https://music.yandex.ru/album/{album_id}/track/{track_id}'
        album_url = f'https://music.yandex.ru/album/{album_id}'
    else:
        webpage_url = f'https://music.yandex.ru/track/{track_id}'
        album_url = None

    cover_uri = item.get('coverUri') or album.get('coverUri')
    thumbnail = None
    if cover_uri:
        thumbnail = str(cover_uri).replace('%%', '1000x1000')
        if thumbnail.startswith('//'):
            thumbnail = f'https:{thumbnail}'
        elif not thumbnail.startswith(('http://', 'https://')):
            thumbnail = f'https://{thumbnail.lstrip("/")}'

    duration_ms = item.get('durationMs')
    return {
        'id': str(track_id),
        'title': title,
        'artist': artists,
        'duration': int(duration_ms / 1000) if duration_ms else None,
        'webpage_url': webpage_url,
        'thumbnail': thumbnail if _is_http_url(thumbnail) else None,
        'album': _clean_metadata_value(album.get('title')),
        'album_url': album_url,
        'track_number': item.get('trackPosition', {}).get('index')
        if isinstance(item.get('trackPosition'), dict)
        else None,
    }


async def _search_yandex_source(query: str, limit: int) -> list:
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            'https://api.music.yandex.net/search',
            params={
                'text': query,
                'type': 'track',
                'page': 0,
                'nocorrect': 'false',
            },
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)

    result = payload.get('result') or {}
    tracks = result.get('tracks') or {}
    items = tracks.get('results') or tracks.get('items') or []
    normalized = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        entry = _yandex_search_entry(item)
        if entry:
            normalized.append(entry)
    return normalized


def _deezer_search_entry(item: dict) -> dict | None:
    title = _clean_metadata_value(item.get('title'))
    artist = _clean_metadata_value((item.get('artist') or {}).get('name'))
    track_id = item.get('id')
    if not title or not artist or track_id is None:
        return None

    album = item.get('album') or {}
    album_id = album.get('id')
    return {
        'id': str(track_id),
        'title': title,
        'artist': artist,
        'duration': item.get('duration'),
        'webpage_url': (
            item.get('link')
            if _is_http_url(item.get('link'))
            else f'https://www.deezer.com/track/{track_id}'
        ),
        'thumbnail': next(
            (
                album.get(name)
                for name in ('cover_xl', 'cover_big', 'cover_medium')
                if _is_http_url(album.get(name))
            ),
            None,
        ),
        'album': _clean_metadata_value(album.get('title')),
        'album_url': (
            f'https://api.deezer.com/album/{album_id}/tracks'
            if album_id is not None
            else None
        ),
        'track_number': item.get('track_position'),
    }


async def _search_deezer_source(query: str, limit: int) -> list:
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            'https://api.deezer.com/search',
            params={'q': query, 'limit': limit},
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)

    normalized = []
    for item in (payload.get('data') or [])[:limit]:
        if not isinstance(item, dict):
            continue
        entry = _deezer_search_entry(item)
        if entry:
            normalized.append(entry)
    return normalized


def _itunes_search_entry(item: dict) -> dict | None:
    title = _clean_metadata_value(item.get('trackName'))
    artist = _clean_metadata_value(item.get('artistName'))
    track_url = item.get('trackViewUrl')
    if not title or not artist or not _is_http_url(track_url):
        return None

    artwork = item.get('artworkUrl100')
    if _is_http_url(artwork):
        artwork = re.sub(r'/\d+x\d+bb\.', '/600x600bb.', artwork)

    collection_id = item.get('collectionId')
    duration_ms = item.get('trackTimeMillis')
    return {
        'id': str(item.get('trackId') or track_url),
        'title': title,
        'artist': artist,
        'duration': int(duration_ms / 1000) if duration_ms else None,
        'webpage_url': track_url,
        'thumbnail': artwork if _is_http_url(artwork) else None,
        'album': _clean_metadata_value(item.get('collectionName')),
        'album_url': (
            f'https://itunes.apple.com/lookup?id={collection_id}&entity=song&limit=200'
            if collection_id is not None
            else None
        ),
        'track_number': item.get('trackNumber'),
    }


async def _search_itunes_source(query: str, limit: int) -> list:
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            'https://itunes.apple.com/search',
            params={
                'term': query,
                'entity': 'song',
                'media': 'music',
                'limit': limit,
                'country': os.getenv('ITUNES_COUNTRY', 'RU').strip() or 'RU',
            },
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)

    normalized = []
    for item in (payload.get('results') or [])[:limit]:
        if not isinstance(item, dict):
            continue
        entry = _itunes_search_entry(item)
        if entry:
            normalized.append(entry)
    return normalized


async def _search_vk_source(query: str, limit: int) -> list:
    """Searches VK Music through the official API when a user token is configured."""
    access_token = os.getenv('VK_ACCESS_TOKEN', '').strip()
    if not access_token:
        logger.info('VK search skipped: VK_ACCESS_TOKEN is not configured')
        return []

    params = {
        'q': query,
        'count': max(1, min(limit, 100)),
        'sort': 2,
        'auto_complete': 1,
        'access_token': access_token,
        'v': os.getenv('VK_API_VERSION', '5.199').strip() or '5.199',
    }
    timeout = aiohttp.ClientTimeout(total=7)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            'https://api.vk.com/method/audio.search',
            params=params,
        ) as response:
            payload = await response.json(content_type=None)

    if payload.get('error'):
        error = payload['error']
        logger.warning(
            'VK audio search failed (%s): %s',
            error.get('error_code'),
            error.get('error_msg'),
        )
        return []

    entries = []
    for item in (payload.get('response') or {}).get('items') or []:
        direct_url = item.get('url')
        if not _is_http_url(direct_url):
            continue

        owner_id = item.get('owner_id')
        audio_id = item.get('id')
        stable_url = (
            f'https://vk.com/audio{owner_id}_{audio_id}'
            if owner_id is not None and audio_id is not None
            else direct_url
        )
        album = item.get('album') or {}
        thumb = album.get('thumb') or {}
        thumbnail = next(
            (
                thumb.get(name)
                for name in ('photo_1200', 'photo_600', 'photo_300', 'photo_270')
                if _is_http_url(thumb.get(name))
            ),
            None,
        )
        entries.append({
            'id': f'{owner_id}_{audio_id}',
            'title': item.get('title') or 'Неизвестно',
            'artist': item.get('artist') or 'Неизвестно',
            'duration': item.get('duration'),
            'webpage_url': stable_url,
            'download_url': direct_url,
            'thumbnail': thumbnail,
            'album': album.get('title'),
        })
    return entries


async def _search_source(prefix: str, query: str, limit: int) -> list:
    """Returns lightweight search entries without resolving media streams."""
    if prefix == 'yandexsearch':
        return await _search_yandex_source(query, limit)
    if prefix == 'vksearch':
        return await _search_vk_source(query, limit)
    if prefix == 'deezersearch':
        return await _search_deezer_source(query, limit)
    if prefix == 'itunessearch':
        return await _search_itunes_source(query, limit)
    ydl_opts = {
        **get_anti_block_opts(use_cookies=prefix.startswith('yt')),
        'extract_flat': 'in_playlist',
        'playlistend': limit,
        'noplaylist': True,
        'socket_timeout': 10,
        'quiet': True,
    }
    if FFMPEG_LOCATION:
        ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION

    search_query = f"{prefix}{limit}:{query}"
    logger.info("Searching metadata in %s: %s", prefix, search_query)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract_info_sync, ydl, search_query, False),
            timeout=15,
        )
    return (info or {}).get('entries') or []


async def search_music(query: str, limit: int = 10) -> list:
    """Searches public music catalogues and interleaves deduplicated results."""
    if not query.strip() or limit < 1:
        return []

    per_source_limit = max(3, min(limit, 10))
    try:
        search_timeout = float(os.getenv('AUDIO_SEARCH_TIMEOUT_SECONDS', '8'))
    except ValueError:
        search_timeout = 8
    search_timeout = max(2, min(search_timeout, 20))
    batches = await asyncio.gather(
        *(
            asyncio.wait_for(
                _search_source(prefix, query, per_source_limit),
                timeout=search_timeout,
            )
            for prefix in SEARCH_SOURCES
        ),
        return_exceptions=True,
    )

    normalized_batches = []
    for prefix, entries in zip(SEARCH_SOURCES, batches):
        if isinstance(entries, Exception):
            logger.warning("Search error in %s: %s", prefix, entries)
            normalized_batches.append([])
            continue

        source_results = []
        for entry in entries:
            if not entry:
                continue

            video_id = entry.get('id', '')
            source_url = (
                entry.get('webpage_url')
                or entry.get('original_url')
                or entry.get('url')
                or ''
            )
            if prefix.startswith('yt') and video_id and not _is_http_url(source_url):
                source_url = f"https://www.youtube.com/watch?v={video_id}"
            if not _is_http_url(source_url):
                continue

            raw_title = entry.get('title') or 'Неизвестно'
            clean_title = re.sub(
                r'\s*[\(\[]\s*(Official|Music|Lyric|Video|Audio|HD|4K|HQ|Visualizer|Live).*?[\)\]]',
                '',
                raw_title,
                flags=re.IGNORECASE,
            ).strip()
            artist = (
                entry.get('artist')
                or entry.get('uploader')
                or entry.get('channel')
                or 'Неизвестно'
            )
            source_results.append({
                'title': clean_title or raw_title,
                'artist': artist,
                'url': source_url,
                'download_url': entry.get('download_url'),
                'duration': entry.get('duration'),
                'thumbnail': _inline_thumbnail_url(entry, prefix),
                'source': prefix.replace('search', ''),
                'album': entry.get('album'),
                'album_url': entry.get('album_url'),
                'track_number': entry.get('track_number') or entry.get('playlist_index'),
            })
        normalized_batches.append(source_results)

    results = []
    seen_urls = set()
    seen_tracks = {}
    max_batch_size = max((len(batch) for batch in normalized_batches), default=0)
    for item_index in range(max_batch_size):
        for batch in normalized_batches:
            if item_index >= len(batch):
                continue
            track = batch[item_index]
            if track['url'] in seen_urls:
                continue
            seen_urls.add(track['url'])

            metadata_key = (
                _metadata_key(track.get('title')),
                _metadata_key(track.get('artist')),
            )
            existing_index = seen_tracks.get(metadata_key)
            if existing_index is not None and all(metadata_key):
                existing = results[existing_index]
                for field in ('album', 'album_url', 'thumbnail', 'duration'):
                    if not existing.get(field) and track.get(field):
                        existing[field] = track[field]
                continue

            seen_tracks[metadata_key] = len(results)
            results.append(track)
            if len(results) >= limit:
                logger.info("Total found %s results for query: %s", len(results), query)
                return results

    logger.info("Total found %s results for query: %s", len(results), query)
    return results
