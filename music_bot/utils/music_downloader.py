import aiohttp
import yt_dlp
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Приоритетные источники для поиска
SEARCH_SOURCES = [
    'soundcloud',
    'vk',
]

# Антиблокировка
ANTI_BLOCK_OPTS = {
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'ios', 'web'],
        }
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    },
    'nocheckcertificate': True,
    'quiet': True,
    'no_warnings': True,
}


async def download_from_url(url: str, temp_dir: str) -> dict:
    """
    Скачивает аудио из указанного URL
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
            **ANTI_BLOCK_OPTS,
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
            
            result['title'] = info.get('title', 'Неизвестно')
            result['artist'] = info.get('artist') or info.get('uploader', 'Неизвестно')
            
            audio_id = info.get('id')
            result['audio_path'] = os.path.join(temp_dir, f"{audio_id}.mp3")
            result['thumbnail_path'] = os.path.join(temp_dir, f"{audio_id}.jpg")
            result['success'] = True
            
    except Exception as e:
        logger.error(f"Download error: {e}")
        result['error'] = str(e)
    
    return result


async def search_music(query: str, limit: int = 5) -> list:
    """
    Ищет музыку по запросу через приоритетные источники
    """
    results = []
    
    for source in SEARCH_SOURCES:
        if len(results) >= limit:
            break
            
        try:
            ydl_opts = {
                **ANTI_BLOCK_OPTS,
                'format': 'bestaudio/best',
                'extract_flat': 'in_playlist',
            }
            
            if source == 'vk':
                search_query = f"vksearch{limit}:{query}"
            elif source == 'soundcloud':
                search_query = f"scsearch{limit}:{query}"
            else:
                continue
                
            logger.info(f"Searching in {source}: {search_query}")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                
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