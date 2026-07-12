import os
import shutil
import logging
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, APIC
from PIL import Image
from io import BytesIO

logger = logging.getLogger(__name__)

async def add_cover_to_mp3(mp3_path: str, cover_path: str, title: str = None, artist: str = None) -> str:
    """
    Добавляет обложку и метаданные к MP3 файлу
    
    :param mp3_path: Путь к MP3 файлу
    :param cover_path: Путь к изображению обложки
    :param title: Название трека
    :param artist: Исполнитель
    :return: Путь к обработанному файлу
    """
    if not os.path.exists(mp3_path):
        return mp3_path

    img_data = None
    if cover_path and os.path.exists(cover_path):
        try:
            # Читаем изображение и конвертируем в RGB (поддержка webp/png/rgba)
            img = Image.open(cover_path)
            img = img.convert('RGB')
            
            # Делаем изображение квадратным (обрезаем по центру)
            min_side = min(img.size)
            left = (img.width - min_side) // 2
            top = (img.height - min_side) // 2
            img = img.crop((left, top, left + min_side, top + min_side))
            
            # Ресайз до оптимального размера 600x600 для Telegram
            img = img.resize((600, 600), Image.Resampling.LANCZOS)
            
            # Сохраняем в bytes
            img_bytes = BytesIO()
            img.save(img_bytes, format='JPEG', quality=95)
            img_data = img_bytes.getvalue()
        except Exception as e:
            logger.warning(f"Ошибка при обработке изображения обложки {cover_path}: {e}")

    try:
        # Работа с MP3 тегами через mutagen
        audio = MP3(mp3_path, ID3=ID3)
        try:
            audio.add_tags()
        except Exception:
            pass
        
        # Добавляем обложку в теги
        if img_data:
            audio.tags['APIC'] = APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,  # Front cover
                desc='Cover',
                data=img_data
            )
        
        # Добавляем название
        if title:
            audio.tags['TIT2'] = TIT2(encoding=3, text=title)
        
        # Добавляем исполнителя
        if artist:
            audio.tags['TPE1'] = TPE1(encoding=3, text=artist)
        
        audio.save()
    except Exception as e:
        logger.error(f"Ошибка при сохранении ID3 тегов для {mp3_path}: {e}")
    
    return mp3_path


async def cleanup_temp_files(*paths):
    """Удаляет временные файлы и папки"""
    for path in paths:
        if path and os.path.exists(path):
            try:
                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
            except Exception as e:
                logger.warning(f"Error cleaning up {path}: {e}")