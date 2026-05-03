from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, 
    InlineKeyboardMarkup, InlineKeyboardButton
)
from utils.config import GUCHI_LINK, TEMP_DIR
from utils.audio_processor import add_cover_to_mp3, cleanup_temp_files
import os
import uuid

router = Router()

# Хранилище состояний для пользователей (в памяти для простоты)
user_states = {}


def is_video_url(text: str) -> bool:
    """Проверяет, является ли текст ссылкой на видео"""
    if not text.startswith("http"):
        return False
    
    video_domains = [
        'youtube.com', 'youtu.be', 'instagram.com', 'rutube.ru',
        'vk.com/video', 'vk.ru/video', 'pinterest.com', 'pin.it',
        'tiktok.com', 'twitter.com', 'x.com', 'facebook.com', 'fb.watch'
    ]
    
    text_lower = text.lower()
    return any(domain in text_lower for domain in video_domains)


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Приветственное сообщение с кнопками"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ℹ️ Информация о ГУЧИГЕНГОВО", url=GUCHI_LINK)],
        [InlineKeyboardButton(text="🎵 Загрузить свою песню", callback_data="upload_own")],
        [InlineKeyboardButton(text="📥 Загрузить с площадок", callback_data="download_from_platform")],
    ])
    
    await message.answer(
        f"👋 Привет! Вы используете бота GG_Loader.\n\n"
        f"Нажмите на кнопки ниже для выбора нужных вам функций:\n\n"
        f"1️⃣ Информация об объединении ГУЧИГЕНГОВО\n"
        f"2️⃣ Загрузить свою песню в Telegram\n"
        f"3️⃣ Загрузить песню с площадок",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "upload_own")
async def process_upload_own(callback: CallbackQuery):
    """Обработка запроса на загрузку своей песни"""
    await callback.message.edit_text(
        "🎵 **Загрузка своей песни**\n\n"
        "Отправьте мне MP3 файл, а затем обложку к нему (квадратное изображение).\n"
        "После этого я спрошу название трека и исполнителя.\n\n"
        "Или отправьте /cancel для отмены."
    )


@router.callback_query(F.data == "download_from_platform")
async def process_download_platform(callback: CallbackQuery):
    """Обработка запроса на загрузку с площадок"""
    await callback.message.edit_text(
        "📥 **Загрузка с музыкальных площадок**\n\n"
        "Отправьте мне ссылку на трек (VK Музыка, Яндекс Музыка, SoundCloud и др.)\n"
        "Я скачаю его и отправлю вам с обложкой.\n\n"
        "Или отправьте /cancel для отмены."
    )


@router.callback_query(F.data.startswith("video_quality:"))
async def process_video_quality_selection(callback: CallbackQuery):
    """Обработка выбора качества видео"""
    await callback.answer()
    
    # Парсим данные
    data = callback.data.split(":", 1)[1]
    parts = data.split("|")
    if len(parts) < 3:
        await callback.message.answer("❌ Ошибка: неверные данные")
        return
    
    url = parts[0]
    format_id = parts[1]
    title = parts[2]
    
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    try:
        from utils.video_downloader import download_video
        
        await callback.message.answer("🎬 Скачиваю видео...")
        
        result = await download_video(url, user_temp_dir, format_id)
        
        if not result['success']:
            await callback.message.answer(f"❌ Ошибка при скачивании: {result['error']}")
            await cleanup_temp_files(user_temp_dir)
            return
        
        video_path = result['video_path']
        
        # Проверяем размер файла
        filesize = result['filesize']
        if filesize > 2 * 1024 * 1024 * 1024:  # 2 GB
            await callback.message.answer(
                "⚠️ Размер файла превышает 2 ГБ и не может быть отправлен через Telegram.\n"
                "Попробуйте выбрать качество ниже."
            )
            await cleanup_temp_files(user_temp_dir)
            return
        
        # Отправляем видео
        video_file = FSInputFile(video_path)
        
        await callback.message.answer_video(
            video=video_file,
            caption=f"🎬 {title}\n\n_Скачано с помощью @GG_Loader_bot_",
            parse_mode="Markdown",
            supports_streaming=True
        )
        
        await callback.message.answer("✅ Видео успешно загружено!")
        
    except Exception as e:
        await callback.message.answer(f"❌ Произошла ошибка: {str(e)}")
    finally:
        await cleanup_temp_files(user_temp_dir)


@router.callback_query(F.data.startswith("extract_audio:"))
async def process_extract_audio(callback: CallbackQuery):
    """Обработка запроса на извлечение аудио из видео"""
    await callback.answer()
    
    url = callback.data.split(":", 1)[1]
    
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    try:
        from utils.video_downloader import download_audio_from_video
        
        await callback.message.answer("🎵 Извлекаю аудио...")
        
        result = await download_audio_from_video(url, user_temp_dir)
        
        if not result['success']:
            await callback.message.answer(f"❌ Ошибка при извлечении: {result['error']}")
            await cleanup_temp_files(user_temp_dir)
            return
        
        audio_path = result['audio_path']
        title = result['title']
        artist = result['artist']
        cover_path = result.get('thumbnail_path')
        
        # Добавляем обложку если есть
        if cover_path and os.path.exists(cover_path):
            processed_path = await add_cover_to_mp3(audio_path, cover_path, title, artist)
        else:
            processed_path = audio_path
        
        audio_file = FSInputFile(processed_path)
        
        await callback.message.answer_audio(
            audio=audio_file,
            title=title,
            performer=artist,
            caption=f"🎵 {title}\n👤 {artist}\n\n_Скачано с помощью @GG_Loader_bot_",
            parse_mode="Markdown",
            thumb=FSInputFile(cover_path) if cover_path and os.path.exists(cover_path) else None
        )
        
        await callback.message.answer("✅ Аудио успешно извлечено!")
        
    except Exception as e:
        await callback.message.answer(f"❌ Произошла ошибка: {str(e)}")
    finally:
        await cleanup_temp_files(user_temp_dir)


@router.callback_query(F.data == "cancel_video")
async def cancel_video_download(callback: CallbackQuery):
    """Отмена скачивания видео"""
    await callback.answer("Отменено")
    await callback.message.edit_text("❌ Скачивание видео отменено")


@router.message(F.audio)
async def handle_audio_file(message: Message):
    """Обработка загруженного аудиофайла"""
    audio = message.audio
    
    if not audio.file_id:
        return
    
    # Создаем уникальную директорию для файлов пользователя
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    # Скачиваем аудиофайл
    file = await message.bot.get_file(audio.file_id)
    audio_path = os.path.join(user_temp_dir, f"{audio.file_unique_id}.mp3")
    await message.bot.download_file(file.file_path, audio_path)
    
    # Сохраняем информацию о состоянии пользователя
    user_states[message.from_user.id] = {
        'audio_path': audio_path,
        'temp_dir': user_temp_dir,
        'step': 'waiting_cover'
    }
    
    await message.answer(
        "✅ Аудиофайл получен!\n\n"
        "Теперь отправьте обложку (квадратное изображение) для этого трека."
    )


@router.message(F.photo)
async def handle_cover_photo(message: Message):
    """Обработка обложки"""
    user_id = message.from_user.id
    
    if user_id not in user_states or user_states[user_id].get('step') != 'waiting_cover':
        return
    
    photo = message.photo[-1]  # Берем фото лучшего качества
    
    user_temp_dir = user_states[user_id]['temp_dir']
    
    file = await message.bot.get_file(photo.file_id)
    cover_path = os.path.join(user_temp_dir, "cover.jpg")
    await message.bot.download_file(file.file_path, cover_path)
    
    # Обновляем состояние
    user_states[user_id]['cover_path'] = cover_path
    user_states[user_id]['step'] = 'waiting_title'
    
    await message.answer(
        "✅ Обложка получена!\n\n"
        "Теперь отправьте название трека и исполнителя в формате:\n"
        "`Название - Исполнитель`\n\n"
        "Или просто название, если исполнитель неизвестен."
    )


@router.message(F.text)
async def handle_track_info(message: Message):
    """Обработка названия и исполнителя"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Проверяем, есть ли активная сессия для этого пользователя
    if user_id not in user_states or user_states[user_id].get('step') != 'waiting_title':
        # Если нет активной сессии, проверяем не является ли сообщение ссылкой
        if text.startswith("http"):
            # Проверяем, ссылка на видео или на музыку
            if is_video_url(text):
                await process_video_link(message)
            else:
                await process_download_link(message)
        return
    
    # Парсим текст
    if " - " in text:
        parts = text.split(" - ", 1)
        title = parts[0].strip()
        artist = parts[1].strip()
    else:
        title = text
        artist = "Неизвестно"
    
    await message.answer(
        f"🎵 Трек: {title}\n"
        f"👤 Исполнитель: {artist}\n\n"
        "Обработка началась, ожидайте..."
    )
    
    try:
        # Получаем пути к файлам
        state = user_states[user_id]
        audio_path = state['audio_path']
        cover_path = state['cover_path']
        
        # Добавляем обложку и метаданные к MP3
        processed_path = await add_cover_to_mp3(audio_path, cover_path, title, artist)
        
        # Отправляем обработанный файл как аудио с метаданными и обложкой
        from aiogram.types import FSInputFile
        
        audio_file = FSInputFile(processed_path)
        
        await message.answer_audio(
            audio=audio_file,
            title=title,
            performer=artist,
            caption=f"🎵 {title}\n👤 {artist}\n\n_Скачано с помощью @GG_Loader_bot_",
            parse_mode="Markdown",
            thumb=FSInputFile(cover_path) if cover_path and os.path.exists(cover_path) else None
        )
        
        await message.answer("✅ Готово! Ваш трек обработан и готов к отправке.\n\nБот применил обложку и метаданные к файлу.")
        
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка при обработке: {str(e)}")
    finally:
        # Очищаем временные файлы и состояние
        if user_id in user_states:
            temp_dir = user_states[user_id].get('temp_dir')
            await cleanup_temp_files(temp_dir)
            del user_states[user_id]


async def process_download_link(message: Message):
    """Обработка ссылок на музыку в чате с ботом"""
    text = message.text.strip()
    
    # Создаем уникальную директорию для файлов пользователя
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    await message.answer("🎵 Скачиваю трек по ссылке...")
    
    try:
        from utils.music_downloader import download_from_url
        
        # Скачиваем трек
        download_result = await download_from_url(text, user_temp_dir)
        
        if not download_result['success']:
            await message.answer(f"❌ Ошибка при скачивании: {download_result['error']}")
            await cleanup_temp_files(user_temp_dir)
            return
        
        audio_path = download_result['audio_path']
        title = download_result['title']
        artist = download_result['artist']
        cover_path = download_result.get('thumbnail_path')
        
        # Добавляем обложку и метаданные если есть обложка
        if cover_path and os.path.exists(cover_path):
            await message.answer("🎨 Применяю обложку и метаданные...")
            processed_path = await add_cover_to_mp3(audio_path, cover_path, title, artist)
        else:
            processed_path = audio_path
        
        # Отправляем готовый файл с обложкой как миниатюрой
        from aiogram.types import FSInputFile
        
        # Создаем InputFile для отправки
        audio_file = FSInputFile(processed_path)
        
        # Отправляем аудио с метаданными и обложкой
        await message.answer_audio(
            audio=audio_file,
            title=title,
            performer=artist,
            caption=f"🎵 {title}\n👤 {artist}\n\n_Скачано с помощью @GG_Loader_bot_",
            parse_mode="Markdown",
            thumb=FSInputFile(cover_path) if cover_path and os.path.exists(cover_path) else None
        )
        
        await message.answer("✅ Готово! Ваш трек обработан и готов к отправке.\n\nБот применил обложку и метаданные к файлу.")
        
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка: {str(e)}")
    finally:
        # Очищаем временные файлы
        await cleanup_temp_files(user_temp_dir)


async def process_video_link(message: Message):
    """Обработка ссылок на видео в чате с ботом"""
    text = message.text.strip()
    
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    await message.answer("🎬 Определяю доступные качества видео...")
    
    try:
        from utils.video_downloader import get_video_formats, detect_platform
        
        # Определяем платформу
        platform = detect_platform(text)
        platform_text = f" ({platform})" if platform else ""
        
        # Получаем форматы
        formats_result = get_video_formats(text)
        
        if not formats_result['success']:
            await message.answer(f"❌ Ошибка при получении информации о видео: {formats_result['error']}")
            await cleanup_temp_files(user_temp_dir)
            return
        
        title = formats_result['title']
        duration = formats_result['duration']
        formats = formats_result['formats']
        
        if not formats:
            await message.answer("❌ Не удалось получить доступные форматы видео")
            await cleanup_temp_files(user_temp_dir)
            return
        
        # Форматируем длительность
        if duration:
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            duration_str = f"{minutes}:{seconds:02d}"
        else:
            duration_str = "неизвестно"
        
        # Создаем клавиатуру с выбором качества
        keyboard_buttons = []
        
        for fmt in formats[:10]:  # Максимум 10 вариантов качества
            quality_text = f"{fmt['quality_label']} ({fmt['size_str']})"
            callback_data = f"video_quality:{text}|{fmt['format_id']}|{title[:50]}"
            keyboard_buttons.append([InlineKeyboardButton(text=quality_text, callback_data=callback_data)])
        
        # Добавляем кнопку для извлечения аудио
        keyboard_buttons.append([InlineKeyboardButton(text="🎵 Извлечь только аудио", callback_data=f"extract_audio:{text}")])
        keyboard_buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_video")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Формируем сообщение с информацией о видео
        info_text = (
            f"🎬 **Название:** {title}\n"
            f"⏱ **Длительность:** {duration_str}\n"
            f"📺 **Платформа:** {platform or 'Неизвестно'}\n\n"
            f"Выберите качество видео для скачивания:\n"
            f"_Максимальный размер файла в Telegram: 2 ГБ_"
        )
        
        await message.answer(info_text, reply_markup=keyboard, parse_mode="Markdown")
        
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка: {str(e)}")
    finally:
        await cleanup_temp_files(user_temp_dir)
