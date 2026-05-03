import aiohttp
import yt_dlp
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Приоритетные источники для поиска (YouTube исключен для работы в РФ)
# Используем только проверенные экстракторы yt_dlp
SEARCH_SOURCES = [
    'soundcloud',   # SoundCloud - международный, стабильно работает
    'vk',           # ВКонтакте - может требовать дополнительной настройки
]


async def download_from_url(url: str, temp_dir: str) -> dict:
    """
    Скачивает аудио из указанного URL (VK, Яндекс Музыка, SoundCloud и др.)
    
    :param url: Ссылка на трек
    :param temp_dir: Директория для временных файлов
    :return: dict с путями к файлам и метаданными
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
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'writethumbnail': True,
            'thumbnail_format': 'jpg',
            'quiet': True,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Получаем информацию о треке
            result['title'] = info.get('title', 'Неизвестно')
            result['artist'] = info.get('artist') or info.get('uploader', 'Неизвестно')
            
            # Путь к аудиофайлу
            audio_id = info.get('id')
            result['audio_path'] = os.path.join(temp_dir, f"{audio_id}.mp3")
            
            # Путь к обложке
            result['thumbnail_path'] = os.path.join(temp_dir, f"{audio_id}.jpg")
            
            result['success'] = True
            
    except Exception as e:
        logger.error(f"Download error: {e}")
        result['error'] = str(e)
    
    return result


async def search_music(query: str, limit: int = 5) -> list:
    """
    Ищет музыку по запросу через приоритетные источники (VK, Яндекс Музыка, SoundCloud)
    
    :param query: Поисковый запрос
    :param limit: Количество результатов
    :return: Список найденных треков
    """
    results = []
    
    for source in SEARCH_SOURCES:
        if len(results) >= limit:
            break
            
        try:
            ydl_opts = {
                'format': 'bestaudio/best',
                'extract_flat': 'in_playlist',
                'quiet': True,
                'no_warnings': True,
            }
            
            # Формируем поисковой запрос для конкретного источника
            # Используем правильный синтаксис yt_dlp для каждого экстрактора
            if source == 'vk':
                # VK: поиск через vksearch (может требовать авторизации)
                search_query = f"vksearch{limit}:{query}"
            elif source == 'soundcloud':
                # SoundCloud: scsearch работает стабильно
                search_query = f"scsearch{limit}:{query}"
            else:
                continue
                
            logger.info(f"Searching in {source}: {search_query}")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                
                if info and 'entries' in info:
                    for entry in info['entries']:
                        if entry and len(results) < limit:
                            # Получаем thumbnail из разных источников
                            thumbnail = entry.get('thumbnail')
                            if not thumbnail and entry.get('thumbnails'):
                                thumbnails = entry.get('thumbnails', [])
                                if thumbnails:
                                    thumbnail = thumbnails[-1].get('url')
                            
                            # Формируем URL в зависимости от источника
                            video_id = entry.get('id', '')
                            url = entry.get('url', '')
                            
                            # Если URL не получен, пробуем сформировать сами
                            if not url and video_id:
                                if source == 'vk':
                                    url = f"https://vk.com/audio{video_id}"
                                elif source == 'soundcloud':
                                    url = f"https://soundcloud.com/{video_id}"
                            
                            results.append({
                                'title': entry.get('title', 'Неизвестно'),
                                'artist': entry.get('uploader') or entry.get('artist', 'Неизвестно'),
                                'url': url,
                                'duration': entry.get('duration'),
                                'thumbnail': thumbnail,
                                'source': source
                            })
                    
                    logger.info(f"Found {len(results)} results from {source}")
                        
        except Exception as e:
            logger.warning(f"Search error in {source}: {e}")
            continue
    
    logger.info(f"Total found {len(results)} results for query: {query}")
    return results
