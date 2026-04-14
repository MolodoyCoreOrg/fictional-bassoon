import os
import shutil
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, APIC
from PIL import Image
from io import BytesIO

async def add_cover_to_mp3(mp3_path: str, cover_path: str, title: str = None, artist: str = None) -> str:
    """
    Добавляет обложку и метаданные к MP3 файлу
    
    :param mp3_path: Путь к MP3 файлу
    :param cover_path: Путь к изображению обложки
    :param title: Название трека
    :param artist: Исполнитель
    :return: Путь к обработанному файлу
    """
    # Читаем изображение и конвертируем в квадратное (если нужно)
    img = Image.open(cover_path)
    img = img.convert('RGB')
    
    # Делаем изображение квадратным
    min_side = min(img.size)
    left = (img.width - min_side) // 2
    top = (img.height - min_side) // 2
    img = img.crop((left, top, left + min_side, top + min_side))
    
    # Ресайз до оптимального размера (600x600)
    img = img.resize((600, 600), Image.Resampling.LANCZOS)
    
    # Сохраняем в bytes
    img_bytes = BytesIO()
    img.save(img_bytes, format='JPEG', quality=95)
    img_data = img_bytes.getvalue()
    
    # Работа с MP3
    audio = MP3(mp3_path, ID3=ID3)
    
    # Создаем ID3 тег если нет
    try:
        audio.add_tags()
    except:
        pass
    
    # Добавляем обложку
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
    
    return mp3_path


async def cleanup_temp_files(*paths):
    """Удаляет временные файлы"""
    for path in paths:
        if path and os.path.exists(path):
            try:
                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
            except Exception as e:
                print(f"Error cleaning up {path}: {e}")
