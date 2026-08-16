import aiohttp
import yt_dlp
import os
import re
import logging
import asyncio
from utils.config import FFMPEG_LOCATION, get_anti_block_opts, has_ffmpeg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Приоритетные источники для поиска (ytsearch на первом месте для 100% нахождения)
SEARCH_SOURCES = [
    'ytsearch',
    'scsearch',
]


def _extract_info_sync(ydl, url_or_query, download=False):
    """Синхронная функция для вызова yt-dlp в отдельном потоке"""
    return ydl.extract_info(url_or_query, download=download)


async def download_from_url(url: str, temp_dir: str) -> dict:
    """
    Скачивает аудио из указанного URL (SoundCloud, YouTube, VK, Yandex Music и др.)
    и подготавливает корректные метаданные (название, исполнитель, обложка).
    """
    result = {
        'success': False,
        'audio_path': None,
        'title': None,
        'artist': None,
        'thumbnail_path': None,
        'error': None
    }
    
    try:
        ydl_opts = {
            **get_anti_block_opts(),
            'format': 'bestaudio/best',
            'socket_timeout': 15,
            'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '320',
                },
                {
                    'key': 'FFmpegThumbnailsConvertor',
                    'format': 'jpg',
                }
            ],
            'writethumbnail': True,
        }
        
        if FFMPEG_LOCATION:
            ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION

        if not has_ffmpeg():
            result['error'] = 'В системе не найден FFmpeg. Установите пакет ffmpeg или укажите в .env FFMPEG_LOCATION на файл ffmpeg/папку с ffmpeg.'
            return result

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Выполняем синхронную загрузку в отдельном потоке, чтобы не блокировать бота
            info = await asyncio.to_thread(_extract_info_sync, ydl, url, True)
            
            raw_title = info.get('title', 'Неизвестно')
            raw_artist = info.get('artist')
            uploader = info.get('uploader') or 'Неизвестно'
            
            # Умный парсинг названия и исполнителя, если трек в формате "Artist - Title"
            if not raw_artist or raw_artist == uploader:
                for sep in [" - ", " — ", " ~ ", " – "]:
                    if sep in raw_title:
                        parts = raw_title.split(sep, 1)
                        raw_artist = parts[0].strip()
                        raw_title = parts[1].strip()
                        break
                if not raw_artist:
                    raw_artist = uploader
            else:
                for sep in [" - ", " — ", " – "]:
                    if sep in raw_title and raw_title.lower().startswith(raw_artist.lower() + sep.strip()):
                        raw_title = raw_title.split(sep, 1)[1].strip()
                        break

            # Очистка названия от лишних тегов
            clean_title = re.sub(r'\s*[\(\[]\s*(Official|Music|Lyric|Video|Audio|HD|4K|HQ|Visualizer|Live|Prod\..*?|with lyrics).*?[\)\]]', '', raw_title, flags=re.IGNORECASE).strip()
            if not clean_title:
                clean_title = raw_title

            result['title'] = clean_title
            result['artist'] = raw_artist
            
            audio_id = info.get('id', 'track')
            
            # Надежный поиск скачанного MP3 файла
            mp3_path = os.path.join(temp_dir, f"{audio_id}.mp3")
            if os.path.exists(mp3_path):
                result['audio_path'] = mp3_path
            else:
                for file in os.listdir(temp_dir):
                    if file.endswith('.mp3'):
                        result['audio_path'] = os.path.join(temp_dir, file)
                        break

            # Надежный поиск скачанной обложки
            jpg_path = os.path.join(temp_dir, f"{audio_id}.jpg")
            if os.path.exists(jpg_path):
                result['thumbnail_path'] = jpg_path
            else:
                for file in os.listdir(temp_dir):
                    if file.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')) and not file.endswith('.mp3'):
                        result['thumbnail_path'] = os.path.join(temp_dir, file)
                        break

            # Если yt-dlp не сохранил обложку на диск, скачиваем её по ссылке из метаданных
            if not result['thumbnail_path']:
                thumb_url = info.get('thumbnail')
                if not thumb_url and info.get('thumbnails'):
                    thumbs = info.get('thumbnails', [])
                    if thumbs:
                        thumb_url = thumbs[-1].get('url')
                
                if thumb_url:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(thumb_url) as resp:
                                if resp.status == 200:
                                    cover_path = os.path.join(temp_dir, "cover.jpg")
                                    with open(cover_path, "wb") as f:
                                        f.write(await resp.read())
                                    result['thumbnail_path'] = cover_path
                    except Exception as e:
                        logger.warning(f"Не удалось скачать обложку по ссылке {thumb_url}: {e}")

            if result['audio_path'] and os.path.exists(result['audio_path']):
                result['success'] = True
            else:
                result['error'] = "Файл аудио не был создан после загрузки."
            
    except Exception as e:
        logger.error(f"Download error: {e}")
        error_str = str(e)
        lower_error = error_str.lower()
        if "ffprobe and ffmpeg not found" in lower_error or "ffmpeg not found" in lower_error or "ffmpeg is not installed" in lower_error:
            result['error'] = "В системе не найден FFmpeg. Установите пакет ffmpeg или укажите в .env FFMPEG_LOCATION на файл ffmpeg/папку с ffmpeg."
        elif "unsupported url" in lower_error and "vk.com/audio" in lower_error:
            result['error'] = "VK Audio по прямым ссылкам не отдаёт файлы через yt-dlp без официального доступа/авторизации. Пришлите название трека в inline-поиск или используйте ссылку на VK Video/другой открытый источник."
        elif "unsupported url" in lower_error:
            result['error'] = "Эта ссылка не поддерживается текущей версией yt-dlp или площадка требует авторизацию/cookies. Обновите yt-dlp, проверьте публичность ссылки или добавьте cookies.txt."
        else:
            result['error'] = error_str
    
    return result


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


async def search_music(query: str, limit: int = 10) -> list:
    """
    Ищет музыку по запросу через приоритетные источники в асинхронном режиме
    """
    results = []
    
    for prefix in SEARCH_SOURCES:
        if len(results) >= limit:
            break
            
        try:
            ydl_opts = {
                **get_anti_block_opts(),
                'format': 'bestaudio/best',
                'extract_flat': 'in_playlist',
                'noplaylist': True,
                'socket_timeout': 10,
            }
            if FFMPEG_LOCATION:
                ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION
            
            search_query = f"{prefix}{limit}:{query}"
            logger.info(f"Searching in {prefix}: {search_query}")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Асинхронный вызов поиска в отдельном потоке
                info = await asyncio.wait_for(
                    asyncio.to_thread(_extract_info_sync, ydl, search_query, False),
                    timeout=12
                )
                
                if info and 'entries' in info:
                    for entry in info['entries']:
                        if entry and len(results) < limit:
                            thumbnail = entry.get('thumbnail')
                            if not thumbnail and entry.get('thumbnails'):
                                thumbnails = entry.get('thumbnails', [])
                                if thumbnails:
                                    thumbnail = thumbnails[-1].get('url')
                            
                            video_id = entry.get('id', '')
                            url = entry.get('webpage_url') or entry.get('original_url') or entry.get('url', '')

                            if prefix.startswith('yt') and video_id and not str(url).startswith('http'):
                                url = f"https://www.youtube.com/watch?v={video_id}"
                            elif prefix.startswith('sc') and entry.get('webpage_url'):
                                url = entry['webpage_url']
                            elif not url and video_id:
                                url = f"https://www.youtube.com/watch?v={video_id}"
                            
                            # Очищаем название для красоты
                            raw_title = entry.get('title', 'Неизвестно')
                            clean_title = re.sub(r'\s*[\(\[]\s*(Official|Music|Lyric|Video|Audio|HD|4K|HQ|Visualizer|Live).*?[\)\]]', '', raw_title, flags=re.IGNORECASE).strip()
                            artist = entry.get('artist') or entry.get('uploader') or 'Неизвестно'
                            album = entry.get('album') or entry.get('playlist')
                            album_url = entry.get('album_url') or entry.get('playlist_url')
                            
                            results.append({
                                'title': clean_title or raw_title,
                                'artist': artist,
                                'url': url,
                                'duration': entry.get('duration'),
                                'thumbnail': thumbnail,
                                'source': prefix.replace('search', ''),
                                'album': album,
                                'album_url': album_url,
                                'track_number': entry.get('track_number') or entry.get('playlist_index')
                            })
                    
                    logger.info(f"Found {len(results)} results from {prefix}")
                        
        except Exception as e:
            logger.warning(f"Search error in {prefix}: {e}")
            continue
    
    logger.info(f"Total found {len(results)} results for query: {query}")
    return results