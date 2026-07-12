import aiohttp
import yt_dlp
import os
import re
import logging
import asyncio
from utils.config import FFMPEG_LOCATION, get_anti_block_opts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Приоритетные источники для поиска (ytsearch на первом месте для 100% нахождения)
SEARCH_SOURCES = [
    'ytsearch',
    'scsearch',
    'vksearch',
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
        if "ffprobe and ffmpeg not found" in error_str or "ffmpeg not found" in error_str:
            result['error'] = "В системе не найдены утилиты FFmpeg и ffprobe. Пожалуйста, установите их или укажите путь в файле .env (переменная FFMPEG_LOCATION)."
        else:
            result['error'] = error_str
    
    return result


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
            }
            if FFMPEG_LOCATION:
                ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION
            
            search_query = f"{prefix}{limit}:{query}"
            logger.info(f"Searching in {prefix}: {search_query}")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Асинхронный вызов поиска в отдельном потоке
                info = await asyncio.to_thread(_extract_info_sync, ydl, search_query, False)
                
                if info and 'entries' in info:
                    for entry in info['entries']:
                        if entry and len(results) < limit:
                            thumbnail = entry.get('thumbnail')
                            if not thumbnail and entry.get('thumbnails'):
                                thumbnails = entry.get('thumbnails', [])
                                if thumbnails:
                                    thumbnail = thumbnails[-1].get('url')
                            
                            video_id = entry.get('id', '')
                            url = entry.get('url', '')
                            
                            if not url and video_id:
                                if prefix.startswith('vk'):
                                    url = f"https://vk.com/audio{video_id}"
                                elif prefix.startswith('sc'):
                                    url = f"https://soundcloud.com/{video_id}"
                                elif prefix.startswith('yt'):
                                    url = f"https://www.youtube.com/watch?v={video_id}"
                            
                            # Очищаем название для красоты
                            raw_title = entry.get('title', 'Неизвестно')
                            clean_title = re.sub(r'\s*[\(\[]\s*(Official|Music|Lyric|Video|Audio|HD|4K|HQ|Visualizer|Live).*?[\)\]]', '', raw_title, flags=re.IGNORECASE).strip()
                            
                            results.append({
                                'title': clean_title or raw_title,
                                'artist': entry.get('uploader') or entry.get('artist', 'Неизвестно'),
                                'url': url,
                                'duration': entry.get('duration'),
                                'thumbnail': thumbnail,
                                'source': prefix.replace('search', '')
                            })
                    
                    logger.info(f"Found {len(results)} results from {prefix}")
                        
        except Exception as e:
            logger.warning(f"Search error in {prefix}: {e}")
            continue
    
    logger.info(f"Total found {len(results)} results for query: {query}")
    return results