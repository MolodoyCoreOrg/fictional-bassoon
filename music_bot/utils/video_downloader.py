import yt_dlp
import os
import logging
from typing import List, Dict, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Максимальный размер файла для Telegram (2 ГБ)
MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

# Поддерживаемые платформы
SUPPORTED_PLATFORMS = [
    'youtube',
    'instagram', 
    'rutube',
    'vk',
    'pinterest',
    'tiktok',
    'twitter',
    'facebook'
]

def format_date(date_str: str) -> str:
    """Форматирует дату из YYYYMMDD в DD.MM.YYYY"""
    if not date_str or len(date_str) != 8:
        return "Неизвестно"
    return f"{date_str[6:8]}.{date_str[4:6]}.{date_str[0:4]}"

def format_duration(seconds: int) -> str:
    """Форматирует секунды в H:MM:SS или M:SS"""
    if not seconds:
        return "0:00"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def get_video_formats(url: str) -> Dict:
    """
    Получает доступные форматы видео для указанной ссылки
    
    :param url: Ссылка на видео
    :return: dict со списком форматов и информацией о видео
    """
    result = {
        'success': False,
        'formats': [],
        'title': None,
        'duration': None,
        'thumbnail': None,
        'error': None
    }
    
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            result['title'] = info.get('title', 'Неизвестно')
            result['duration'] = info.get('duration', 0)
            result['thumbnail'] = info.get('thumbnail')
            
            formats_list = []
            
            # Получаем все форматы
            formats = info.get('formats', [])
            
            # Фильтруем только видео с аудиодорожкой или комбинируем
            seen_formats = set()
            
            for fmt in formats:
                # Пропускаем форматы без видео
                if not fmt.get('vcodec') or fmt.get('vcodec') == 'none':
                    continue
                
                # Получаем информацию о формате
                format_id = fmt.get('format_id', '')
                height = fmt.get('height', 0)
                width = fmt.get('width', 0)
                ext = fmt.get('ext', 'mp4')
                filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
                quality_name = fmt.get('format_note', '') or fmt.get('quality', '')
                
                # Определяем качество
                if height >= 2160:
                    quality_label = "4K (2160p)"
                elif height >= 1440:
                    quality_label = "2K (1440p)"
                elif height >= 1080:
                    quality_label = "Full HD (1080p)"
                elif height >= 720:
                    quality_label = "HD (720p)"
                elif height >= 480:
                    quality_label = "SD (480p)"
                elif height >= 360:
                    quality_label = "360p"
                else:
                    quality_label = f"{height}p"
                
                # Создаем уникальный ключ для избегания дубликатов
                format_key = f"{height}_{ext}"
                if format_key in seen_formats:
                    continue
                seen_formats.add(format_key)
                
                # Проверяем размер файла (если известен)
                size_mb = filesize / (1024 * 1024) if filesize else None
                too_large = size_mb and size_mb > MAX_FILE_SIZE_BYTES / (1024 * 1024)
                
                # Формируем отображаемый размер
                if size_mb:
                    if size_mb >= 1024:
                        size_str = f"{size_mb/1024:.1f} ГБ"
                    else:
                        size_str = f"{size_mb:.1f} МБ"
                else:
                    size_str = "размер неизвестен"
                
                formats_list.append({
                    'format_id': format_id,
                    'height': height,
                    'width': width,
                    'ext': ext,
                    'quality_label': quality_label,
                    'size_str': size_str,
                    'filesize': filesize,
                    'too_large': too_large,
                    'has_audio': fmt.get('acodec') is not None and fmt.get('acodec') != 'none',
                    'url': fmt.get('url'),
                    'format_note': quality_name
                })
            
            # Сортируем по качеству (от высокого к низкому)
            formats_list.sort(key=lambda x: x['height'], reverse=True)
            
            # Оставляем только те, что помещаются в лимит Telegram (или все, если размер неизвестен)
            filtered_formats = [f for f in formats_list if not f['too_large'] or f['filesize'] is None]
            
            # Если все форматы отфильтровались, берем хотя бы один с наименьшим качеством
            if not filtered_formats and formats_list:
                filtered_formats = [formats_list[-1]]
            
            result['formats'] = filtered_formats
            result['success'] = True
            
    except Exception as e:
        logger.error(f"Error getting video formats: {e}")
        result['error'] = str(e)
    
    return result


async def download_video(url: str, temp_dir: str, format_id: str) -> Dict:
    """
    Скачивает видео в указанном качестве и извлекает дополнительные данные
    
    :param url: Ссылка на видео
    :param temp_dir: Директория для сохранения
    :param format_id: ID выбранного формата
    :return: dict с путем к файлу и метаданными
    """
    result = {
        'success': False,
        'video_path': None,
        'title': None,
        'thumbnail_path': None,
        'filesize': 0,
        'error': None,
        'author': None,
        'upload_date': None,
        'duration_str': None,
        'quality': None,
        'url': None
    }
    
    try:
        ydl_opts = {
            'format': f'{format_id}+bestaudio[ext=m4a]/best',
            'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
            'writethumbnail': True,
            'thumbnail_format': 'jpg',
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            result['title'] = info.get('title', 'Неизвестно')
            result['thumbnail_path'] = os.path.join(temp_dir, f"{info.get('id')}.jpg")
            
            # Дополнительные поля для красивого Caption
            result['author'] = info.get('uploader', 'Неизвестно')
            raw_date = info.get('upload_date')
            result['upload_date'] = format_date(raw_date) if raw_date else "Неизвестно"
            
            duration = info.get('duration', 0)
            result['duration_str'] = format_duration(duration)
            
            # Пытаемся определить итоговое качество видео по загруженному или запрошенному
            height = info.get('height')
            if not height:
                for f in info.get('requested_formats', []):
                    if f.get('vcodec') and f.get('vcodec') != 'none':
                        height = f.get('height')
                        break
            result['quality'] = f"{height}p" if height else "Неизвестно"
            result['url'] = info.get('webpage_url') or url
            
            # Ищем скачанный файл
            video_id = info.get('id')
            possible_extensions = ['mp4', 'mkv', 'webm', 'avi']
            
            for ext in possible_extensions:
                path = os.path.join(temp_dir, f"{video_id}.{ext}")
                if os.path.exists(path):
                    result['video_path'] = path
                    result['filesize'] = os.path.getsize(path)
                    break
            
            # Если не нашли, пробуем найти любой видеофайл в директории
            if not result['video_path']:
                for file in os.listdir(temp_dir):
                    if file.endswith(('.mp4', '.mkv', '.webm', '.avi')) and file != f"{video_id}.jpg":
                        result['video_path'] = os.path.join(temp_dir, file)
                        result['filesize'] = os.path.getsize(result['video_path'])
                        break
            
            result['success'] = True
            
    except Exception as e:
        logger.error(f"Download error: {e}")
        result['error'] = str(e)
    
    return result


async def download_audio_from_video(url: str, temp_dir: str) -> Dict:
    """
    Извлекает аудио из видео
    
    :param url: Ссылка на видео
    :param temp_dir: Директория для сохранения
    :return: dict с путем к аудиофайлу и метаданными
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
            
            result['title'] = info.get('title', 'Неизвестно')
            result['artist'] = info.get('uploader', 'Неизвестно')
            result['thumbnail_path'] = os.path.join(temp_dir, f"{info.get('id')}.jpg")
            
            # Путь к аудиофайлу
            audio_id = info.get('id')
            result['audio_path'] = os.path.join(temp_dir, f"{audio_id}.mp3")
            
            result['success'] = True
            
    except Exception as e:
        logger.error(f"Audio extraction error: {e}")
        result['error'] = str(e)
    
    return result


def detect_platform(url: str) -> Optional[str]:
    """
    Определяет платформу по URL
    
    :param url: Ссылка
    :return: Название платформы или None
    """
    url_lower = url.lower()
    
    platform_map = {
        'youtube.com': 'YouTube',
        'youtu.be': 'YouTube',
        'instagram.com': 'Instagram',
        'rutube.ru': 'RuTube',
        'vk.com/video': 'VK Video',
        'vk.ru/video': 'VK Video',
        'pinterest.com': 'Pinterest',
        'pin.it': 'Pinterest',
        'tiktok.com': 'TikTok',
        'twitter.com': 'Twitter',
        'x.com': 'Twitter',
        'facebook.com': 'Facebook',
        'fb.watch': 'Facebook',
    }
    
    for domain, platform in platform_map.items():
        if domain in url_lower:
            return platform
    
    return None