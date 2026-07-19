import yt_dlp
import os
import re
import aiohttp
import logging
import asyncio
import subprocess
import shutil
import uuid
from typing import List, Dict, Optional
from utils.config import FFMPEG_LOCATION, get_anti_block_opts

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
    Получает все доступные разрешения видео (4K, 1080p, 720p, 480p, 360p, 240p, 144p)
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
            **get_anti_block_opts(),
            'extract_flat': False,
            'quiet': True,
            'no_warnings': True
        }
        if FFMPEG_LOCATION:
            ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            result['title'] = info.get('title', 'Неизвестно')
            result['duration'] = info.get('duration', 0)
            
            thumbnail = info.get('thumbnail')
            if not thumbnail and info.get('thumbnails'):
                thumbnails = info.get('thumbnails', [])
                if thumbnails:
                    thumbnail = thumbnails[-1].get('url')
            result['thumbnail'] = thumbnail
            
            formats_list = []
            seen_resolutions = set()
            formats = info.get('formats', [])
            
            # Собираем все форматы, у которых есть видео
            for fmt in formats:
                vcodec = fmt.get('vcodec', 'none')
                if not vcodec or vcodec == 'none':
                    continue
                
                format_id = fmt.get('format_id', '')
                height = fmt.get('height', 0)
                width = fmt.get('width', 0)
                ext = fmt.get('ext', 'mp4')
                filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
                
                if not height:
                    continue
                
                if height >= 2160:
                    quality_label = "4K"
                elif height >= 1440:
                    quality_label = "2K"
                elif height >= 1080:
                    quality_label = "1080p"
                elif height >= 720:
                    quality_label = "720p"
                elif height >= 480:
                    quality_label = "480p"
                elif height >= 360:
                    quality_label = "360p"
                elif height >= 240:
                    quality_label = "240p"
                elif height >= 144:
                    quality_label = "144p"
                else:
                    quality_label = f"{height}p"
                
                # Фильтруем дубликаты по высоте экрана, чтобы предложить лучшее качество для каждого разрешения
                if height in seen_resolutions:
                    continue
                seen_resolutions.add(height)
                
                size_mb = filesize / (1024 * 1024) if filesize else None
                too_large = size_mb and size_mb > MAX_FILE_SIZE_BYTES / (1024 * 1024)
                
                if size_mb:
                    size_str = f"{size_mb/1024:.1f} ГБ" if size_mb >= 1024 else f"{size_mb:.1f} МБ"
                else:
                    size_str = "⌛"
                
                # Используем селектор загрузки: видео указанной высоты + лучшее аудио
                dl_format = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
                
                formats_list.append({
                    'format_id': dl_format,
                    'height': height,
                    'width': width,
                    'ext': 'mp4',
                    'quality_label': quality_label,
                    'size_str': size_str,
                    'filesize': filesize,
                    'too_large': too_large,
                    'has_audio': True,
                    'url': fmt.get('url'),
                    'format_note': fmt.get('format_note', '')
                })
            
            formats_list.sort(key=lambda x: x['height'], reverse=True)
            filtered_formats = [f for f in formats_list if not f['too_large'] or f['filesize'] is None]
            
            if not filtered_formats and formats_list:
                filtered_formats = [formats_list[-1]]
            
            # Если по какой-то причине список пуст, даем стандартный выбор best
            if not filtered_formats:
                filtered_formats = [{
                    'format_id': 'bestvideo+bestaudio/best',
                    'height': 1080,
                    'width': 1920,
                    'ext': 'mp4',
                    'quality_label': 'Лучшее качество',
                    'size_str': '⌛',
                    'filesize': None,
                    'too_large': False,
                    'has_audio': True,
                    'url': url,
                    'format_note': 'Best'
                }]
            
            result['formats'] = filtered_formats
            result['success'] = True
            
    except Exception as e:
        logger.error(f"Error getting video formats: {e}")
        result['error'] = str(e)
    
    return result


async def download_video(url: str, temp_dir: str, format_id: str) -> Dict:
    """
    Скачивает видео с жесткой конвертацией в MP4 (H.264/AAC), чтобы оно идеально воспроизводилось в Telegram без сжатия
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
        'url': None,
        'width': 0,
        'height': 0
    }
    
    try:
        ydl_opts = {
            **get_anti_block_opts(),
            'format': format_id,
            'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
            'writethumbnail': True,
            'thumbnail_format': 'jpg',
            'merge_output_format': 'mp4',
            'postprocessors': [
                {
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }
            ]
        }
        if FFMPEG_LOCATION:
            ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            result['title'] = info.get('title', 'Неизвестно')
            result['thumbnail_path'] = os.path.join(temp_dir, f"{info.get('id')}.jpg")
            result['author'] = info.get('uploader', 'Неизвестно')
            raw_date = info.get('upload_date')
            result['upload_date'] = format_date(raw_date) if raw_date else "Неизвестно"
            
            duration = info.get('duration', 0)
            result['duration_str'] = format_duration(duration)
            
            height = info.get('height', 0)
            width = info.get('width', 0)
            if not height or not width:
                for f in info.get('requested_formats', []):
                    if f.get('height'):
                        height = f.get('height')
                        width = f.get('width', int(height * 16 / 9))
                        break
            
            result['height'] = height or 720
            result['width'] = width or int(result['height'] * 16 / 9)
            result['quality'] = f"{height}p" if height else "MP4"
            result['url'] = info.get('webpage_url') or url
            
            video_id = info.get('id')
            possible_extensions = ['mp4', 'mkv', 'webm', 'avi', 'mov']
            
            for ext in possible_extensions:
                path = os.path.join(temp_dir, f"{video_id}.{ext}")
                if os.path.exists(path):
                    result['video_path'] = path
                    result['filesize'] = os.path.getsize(path)
                    break
            
            if not result['video_path']:
                for file in os.listdir(temp_dir):
                    if file.endswith(('.mp4', '.mkv', '.webm', '.avi', '.mov')) and not file.endswith('.jpg'):
                        result['video_path'] = os.path.join(temp_dir, file)
                        result['filesize'] = os.path.getsize(result['video_path'])
                        break
            
            result['success'] = True
            
    except Exception as e:
        logger.error(f"Download error: {e}")
        error_str = str(e)
        if "ffprobe and ffmpeg not found" in error_str or "ffmpeg not found" in error_str:
            result['error'] = "В системе не найдены утилиты FFmpeg и ffprobe. Пожалуйста, установите их или укажите путь в файле .env."
        else:
            result['error'] = error_str
    
    return result


async def download_audio_from_video(url: str, temp_dir: str, output_format: str = 'mp3') -> Dict:
    """
    Извлекает аудио из видео по ссылке с поддержкой выбора формата:
    - 'mp3': обычный музыкальный файл
    - 'voice': голосовое сообщение для Telegram (кодек OPUS в контейнере OGG)
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
        }
        if FFMPEG_LOCATION:
            ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION
            
        if output_format == 'voice':
            ydl_opts['postprocessors'] = [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'opus',
                }
            ]
        else:
            ydl_opts['postprocessors'] = [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '320',
                },
                {
                    'key': 'FFmpegThumbnailsConvertor',
                    'format': 'jpg',
                }
            ]
            ydl_opts['writethumbnail'] = True
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            raw_title = info.get('title', 'Неизвестно')
            raw_artist = info.get('artist')
            uploader = info.get('uploader') or 'Неизвестно'
            
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

            clean_title = re.sub(r'\s*[\(\[]\s*(Official|Music|Lyric|Video|Audio|HD|4K|HQ|Visualizer|Live|Prod\..*?|with lyrics).*?[\)\]]', '', raw_title, flags=re.IGNORECASE).strip()
            if not clean_title:
                clean_title = raw_title

            result['title'] = clean_title
            result['artist'] = raw_artist
            
            audio_id = info.get('id', 'audio')
            
            if output_format == 'voice':
                valid_exts = ('.opus', '.ogg', '.m4a', '.mp3', '.wav')
            else:
                valid_exts = ('.mp3',)
            
            for ext in valid_exts:
                path = os.path.join(temp_dir, f"{audio_id}{ext}")
                if os.path.exists(path):
                    result['audio_path'] = path
                    break
            
            if not result['audio_path']:
                for file in os.listdir(temp_dir):
                    if file.lower().endswith(valid_exts) and not file.lower().endswith(('.jpg', '.png', '.webp', '.jpeg')):
                        result['audio_path'] = os.path.join(temp_dir, file)
                        break

            if output_format == 'voice' and result['audio_path'] and result['audio_path'].endswith('.opus'):
                ogg_path = result['audio_path'][:-5] + '.ogg'
                try:
                    os.rename(result['audio_path'], ogg_path)
                    result['audio_path'] = ogg_path
                except Exception as e:
                    logger.warning(f"Не удалось переименовать .opus в .ogg: {e}")

            if output_format == 'mp3':
                jpg_path = os.path.join(temp_dir, f"{audio_id}.jpg")
                if os.path.exists(jpg_path):
                    result['thumbnail_path'] = jpg_path
                else:
                    for file in os.listdir(temp_dir):
                        if file.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')) and not file.endswith('.mp3'):
                            result['thumbnail_path'] = os.path.join(temp_dir, file)
                            break

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
                            logger.warning(f"Не удалось скачать обложку: {e}")

            if result['audio_path'] and os.path.exists(result['audio_path']):
                result['success'] = True
            else:
                result['error'] = "Файл аудио не был создан."
            
    except Exception as e:
        logger.error(f"Audio extraction error: {e}")
        error_str = str(e)
        if "ffprobe and ffmpeg not found" in error_str or "ffmpeg not found" in error_str:
            result['error'] = "В системе не найдены утилиты FFmpeg и ffprobe. Пожалуйста, установите их или укажите путь в файле .env."
        else:
            result['error'] = error_str
    
    return result


async def extract_audio_from_local_video(video_path: str, temp_dir: str, output_format: str = 'mp3', title: str = "Аудио из видео", artist: str = "GG_Loader") -> Dict:
    """
    Извлекает аудио напрямую из локально загруженного видеофайла с помощью FFmpeg
    """
    result = {
        'success': False,
        'audio_path': None,
        'title': title or "Аудио из видео",
        'artist': artist or "GG_Loader",
        'thumbnail_path': None,
        'error': None
    }
    
    try:
        if not os.path.exists(video_path):
            result['error'] = "Локальный видеофайл не найден на сервере."
            return result

        ffmpeg_exe = "ffmpeg"
        if FFMPEG_LOCATION:
            if os.path.isfile(FFMPEG_LOCATION):
                ffmpeg_exe = FFMPEG_LOCATION
            else:
                for exe in ["ffmpeg", "ffmpeg.exe"]:
                    p = os.path.join(FFMPEG_LOCATION, exe)
                    if os.path.exists(p):
                        ffmpeg_exe = p
                        break
        else:
            ffmpeg_exe = shutil.which("ffmpeg") or "ffmpeg"

        file_id = str(uuid.uuid4())[:8]
        
        if output_format == 'voice':
            output_path = os.path.join(temp_dir, f"voice_{file_id}.ogg")
            cmd = [ffmpeg_exe, '-y', '-i', video_path, '-vn', '-c:a', 'libopus', '-b:a', '64k', output_path]
        else:
            output_path = os.path.join(temp_dir, f"audio_{file_id}.mp3")
            cmd = [ffmpeg_exe, '-y', '-i', video_path, '-vn', '-ar', '44100', '-ac', '2', '-b:a', '320k', output_path]

        def run_ffmpeg():
            return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        process = await asyncio.to_thread(run_ffmpeg)
        
        if process.returncode == 0 and os.path.exists(output_path):
            result['audio_path'] = output_path
            result['success'] = True
        else:
            logger.error(f"FFmpeg stderr: {process.stderr}")
            result['error'] = f"В системе не найдены утилиты FFmpeg и ffprobe. Пожалуйста, установите их или укажите путь в файле .env (переменная FFMPEG_LOCATION)."
            
    except Exception as e:
        logger.error(f"Local video audio extraction error: {e}")
        error_str = str(e)
        if "No such file or directory: 'ffmpeg'" in error_str or "not found" in error_str or "WinError 2" in error_str:
            result['error'] = "В системе не найдены утилиты FFmpeg и ffprobe. Пожалуйста, установите их или укажите путь в файле .env (переменная FFMPEG_LOCATION)."
        else:
            result['error'] = str(e)
            
    return result


def detect_platform(url: str) -> Optional[str]:
    """
    Определяет платформу по URL
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
        'soundcloud.com': 'SoundCloud',
        'music.yandex': 'Yandex Music',
        'spotify.com': 'Spotify'
    }
    
    for domain, platform in platform_map.items():
        if domain in url_lower:
            return platform
    
    return None