import aiohttp
import yt_dlp
import os

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
            'extract_flat': True,
            'default_search': 'ytsearch',
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_query = f"ytsearch{limit}:{query}"
            info = ydl.extract_info(search_query, download=False)
            
            if info and 'entries' in info:
                for entry in info['entries']:
                    if entry:
                        results.append({
                            'title': entry.get('title', 'Неизвестно'),
                            'artist': entry.get('uploader', 'Неизвестно'),
                            'url': f"https://www.youtube.com/watch?v={entry.get('id')}",
                            'duration': entry.get('duration'),
                            'thumbnail': entry.get('thumbnail')
                        })
                        
    except Exception as e:
        print(f"Search error: {e}")
    
    return results
