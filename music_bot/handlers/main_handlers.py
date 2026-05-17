from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, 
)
from music_bot.utils.config import GUCHI_LINK, TEMP_DIR
from music_bot.utils.audio_processor import add_cover_to_mp3, cleanup_temp_files
from music_bot.utils.keyboard import (
    get_main_menu_keyboard,
    get_back_keyboard,
    get_about_guchi_keyboard,
    get_upload_song_keyboard,
    get_download_music_keyboard,
    get_download_video_keyboard,
    get_video_quality_keyboard,
    create_button
)
import os
import uuid

router = Router()

user_states = {}


def is_video_url(text: str) -> bool:
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
    await message.answer(
        "👋 Привет! Вы используете бота GG_Loader.\n\nВыберите функцию из меню ниже:",
        reply_markup=get_main_menu_keyboard()
    )


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "👋 Главное меню GG_Loader\n\nВыберите функцию:",
        reply_markup=get_main_menu_keyboard()
    )


@router.callback_query(F.data == "about_guchi")
async def about_guchi(callback: CallbackQuery):
    await callback.message.edit_text(
        "ℹ️ **О ГУЧИГЕНГОВО!**\n\n"
        "ГУЧИГЕНГОВО — это музыкальное объединение, создающее уникальный контент.\n\n"
        "Следите за нами в социальных сетях и слушайте нашу музыку!",
        reply_markup=get_about_guchi_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "upload_own_song")
async def process_upload_own(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎵 **Загрузка своей песни**\n\n"
        "Отправьте мне MP3 файл, а затем обложку к нему (квадратное изображение).\n"
        "После этого я спрошу название трека и исполнителя.\n\n"
        "Или нажмите кнопку «Назад» для возврата в меню.",
        reply_markup=get_upload_song_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "download_music")
async def process_download_music(callback: CallbackQuery):
    await callback.message.edit_text(
        "📥 **Загрузить музыку в Telegram**\n\n"
        "Отправьте мне ссылку на трек из:\n"
        "• SoundCloud\n• ВКонтакте\n• Spotify\n• Яндекс Музыка\n• И других платформ\n\n"
        "Я скачаю его и отправлю вам с обложкой.\n\n"
        "Или нажмите кнопку «Назад» для возврата в меню.",
        reply_markup=get_download_music_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "download_video")
async def process_download_video(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎬 **Скачать видео**\n\n"
        "Отправьте мне ссылку на видео из:\n"
        "• YouTube\n• TikTok\n• Instagram\n• ВКонтакте\n• RuTube\n"
        "• Pinterest\n• Twitter/X\n• Facebook\n\n"
        "Я предложу выбрать качество или извлечь аудио.\n\n"
        "Или нажмите кнопку «Назад» для возврата в меню.",
        reply_markup=get_download_video_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("video_quality:"))
async def process_video_quality_selection(callback: CallbackQuery):
    await callback.answer()
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
        filesize = result['filesize']
        if filesize > 2 * 1024 * 1024 * 1024:
            await callback.message.answer("⚠️ Размер файла превышает 2 ГБ.")
            await cleanup_temp_files(user_temp_dir)
            return
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
    await callback.answer("Отменено")
    await callback.message.edit_text("❌ Скачивание видео отменено")


@router.message(F.audio)
async def handle_audio_file(message: Message):
    audio = message.audio
    if not audio.file_id:
        return
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    file = await message.bot.get_file(audio.file_id)
    audio_path = os.path.join(user_temp_dir, f"{audio.file_unique_id}.mp3")
    await message.bot.download_file(file.file_path, audio_path)
    user_states[message.from_user.id] = {
        'audio_path': audio_path,
        'temp_dir': user_temp_dir,
        'step': 'waiting_cover'
    }
    await message.answer("✅ Аудиофайл получен!\n\nТеперь отправьте обложку (квадратное изображение).")


@router.message(F.photo)
async def handle_cover_photo(message: Message):
    user_id = message.from_user.id
    if user_id not in user_states or user_states[user_id].get('step') != 'waiting_cover':
        return
    photo = message.photo[-1]
    user_temp_dir = user_states[user_id]['temp_dir']
    file = await message.bot.get_file(photo.file_id)
    cover_path = os.path.join(user_temp_dir, "cover.jpg")
    await message.bot.download_file(file.file_path, cover_path)
    user_states[user_id]['cover_path'] = cover_path
    user_states[user_id]['step'] = 'waiting_title'
    await message.answer(
        "✅ Обложка получена!\n\n"
        "Теперь отправьте название трека и исполнителя в формате:\n"
        "`Название - Исполнитель`"
    )


@router.message(F.text)
async def handle_track_info(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    if user_id not in user_states or user_states[user_id].get('step') != 'waiting_title':
        if text.startswith("http"):
            if is_video_url(text):
                await process_video_link(message)
            else:
                await process_download_link(message)
        return
    if " - " in text:
        parts = text.split(" - ", 1)
        title = parts[0].strip()
        artist = parts[1].strip()
    else:
        title = text
        artist = "Неизвестно"
    await message.answer(f"🎵 Трек: {title}\n👤 Исполнитель: {artist}\n\nОбработка началась...")
    try:
        state = user_states[user_id]
        audio_path = state['audio_path']
        cover_path = state['cover_path']
        processed_path = await add_cover_to_mp3(audio_path, cover_path, title, artist)
        audio_file = FSInputFile(processed_path)
        await message.answer_audio(
            audio=audio_file,
            title=title,
            performer=artist,
            caption=f"🎵 {title}\n👤 {artist}",
            parse_mode="Markdown",
            thumb=FSInputFile(cover_path) if cover_path and os.path.exists(cover_path) else None
        )
        await message.answer("✅ Готово!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
    finally:
        if user_id in user_states:
            temp_dir = user_states[user_id].get('temp_dir')
            await cleanup_temp_files(temp_dir)
            del user_states[user_id]


async def process_download_link(message: Message):
    text = message.text.strip()
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    await message.answer("🎵 Скачиваю трек...")
    try:
        from utils.music_downloader import download_from_url
        result = await download_from_url(text, user_temp_dir)
        if not result['success']:
            await message.answer(f"❌ Ошибка: {result['error']}")
            await cleanup_temp_files(user_temp_dir)
            return
        audio_path = result['audio_path']
        title = result['title']
        artist = result['artist']
        cover_path = result.get('thumbnail_path')
        if cover_path and os.path.exists(cover_path):
            await message.answer("🎨 Применяю обложку...")
            processed_path = await add_cover_to_mp3(audio_path, cover_path, title, artist)
        else:
            processed_path = audio_path
        audio_file = FSInputFile(processed_path)
        await message.answer_audio(
            audio=audio_file,
            title=title,
            performer=artist,
            caption=f"🎵 {title}\n👤 {artist}",
            parse_mode="Markdown",
            thumb=FSInputFile(cover_path) if cover_path and os.path.exists(cover_path) else None
        )
        await message.answer("✅ Готово!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
    finally:
        await cleanup_temp_files(user_temp_dir)


async def process_video_link(message: Message):
    text = message.text.strip()
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    await message.answer("🎬 Определяю качества видео...")
    try:
        from utils.video_downloader import get_video_formats, detect_platform
        platform = detect_platform(text)
        formats_result = get_video_formats(text)
        if not formats_result['success']:
            await message.answer(f"❌ Ошибка: {formats_result['error']}")
            await cleanup_temp_files(user_temp_dir)
            return
        title = formats_result['title']
        duration = formats_result['duration']
        formats = formats_result['formats']
        if not formats:
            await message.answer("❌ Нет доступных форматов")
            await cleanup_temp_files(user_temp_dir)
            return
        if duration:
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            duration_str = f"{minutes}:{seconds:02d}"
        else:
            duration_str = "неизвестно"
        keyboard = get_video_quality_keyboard(text, formats, title)
        info_text = f"🎬 **Название:** {title}\n⏱ **Длительность:** {duration_str}\n📺 **Платформа:** {platform or 'Неизвестно'}\n\nВыберите качество:"
        await message.answer(info_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
    finally:
        await cleanup_temp_files(user_temp_dir)
