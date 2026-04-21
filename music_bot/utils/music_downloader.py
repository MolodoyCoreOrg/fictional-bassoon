import aiohttp
import yt_dlp
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def download_from_url(url: str, temp_dir: str) -> dict:
    """
    Скачивает аудио из указанного URL (VK, Яндекс Музыка, YouTube и др.)
    
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
    Ищет музыку по запросу (использует YouTube как источник)
    
    :param query: Поисковый запрос
    :param limit: Количество результатов
    :return: Список найденных треков
    """
    results = []
    
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'extract_flat': 'in_playlist',  # Изменено для получения thumbnail
            'default_search': 'ytsearch',
            'quiet': True,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_query = f"ytsearch{limit}:{query}"
            logger.info(f"Searching: {search_query}")
            info = ydl.extract_info(search_query, download=False)
            
            if info and 'entries' in info:
                for entry in info['entries']:
                    if entry:
                        # Получаем thumbnail из разных источников
                        thumbnail = entry.get('thumbnail')
                        if not thumbnail and entry.get('thumbnails'):
                            # Берем лучшую миниатюру из списка
                            thumbnails = entry.get('thumbnails', [])
                            if thumbnails:
                                thumbnail = thumbnails[-1].get('url')  # Последняя обычно лучшего качества
                        
                        results.append({
                            'title': entry.get('title', 'Неизвестно'),
                            'artist': entry.get('uploader', 'Неизвестно'),
                            'url': f"https://www.youtube.com/watch?v={entry.get('id')}",
                            'duration': entry.get('duration'),
                            'thumbnail': thumbnail
                        })
                logger.info(f"Found {len(results)} results")
                        
    except Exception as e:
        logger.error(f"Search error: {e}")
    
    return results
