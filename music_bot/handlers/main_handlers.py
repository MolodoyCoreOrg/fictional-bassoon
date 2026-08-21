import os
import uuid
import logging
import html
import re
import asyncio
from datetime import datetime
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext

# Импортируем состояния, клавиатуры и утилиты
from models.states import MediaStates
from utils.config import FFMPEG_EXECUTABLE, TELEGRAM_LOCAL_API, TELEGRAM_MAX_UPLOAD_MB, TEMP_DIR
from utils.keyboard import (
    get_welcome_menu, get_back_keyboard, 
    get_about_guchi_keyboard, get_video_quality_keyboard,
    get_extract_format_keyboard, get_skip_channel_keyboard
)
from utils.video_downloader import (
    get_video_formats, download_video, download_audio_from_video, 
    detect_platform, extract_audio_from_local_video
)
from utils.music_downloader import download_from_url, get_album_tracks
from utils.audio_processor import add_cover_to_mp3, cleanup_temp_files
from utils.album_cache import get_album
from utils.media_request_cache import get_media_request, save_media_request

router = Router()
logger = logging.getLogger(__name__)

# Регулярное выражение для мгновенного перехвата ссылок из любых соцсетей (работает без кнопок и меню)
VIDEO_REGEX = r'(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be|instagram\.com|tiktok\.com|vk\.com/video|vk\.ru/video|rutube\.ru|pinterest\.com|pin\.it|x\.com|twitter\.com|facebook\.com|fb\.watch)[^\s]*'
AUDIO_REGEX = r'(https?://)?(www\.|m\.)?(soundcloud\.com|on\.soundcloud\.com|vk\.com/(audio|music)|vk\.ru/(audio|music)|music\.yandex\.(ru|com)|music\.youtube\.com|open\.spotify\.com|music\.apple\.com|deezer\.com|promodj\.com|mixcloud\.com|bandcamp\.com|audiomack\.com)[^\s]*'

def extract_url(text: str) -> str:
    """Извлекает первую ссылку из текста сообщения"""
    if not text:
        return ""
    match = re.search(r'https?://[^\s]+', text)
    return match.group(0).rstrip('.,;!?)]}>"\'') if match else text.strip()


def is_audio_url(url: str) -> bool:
    """Определяет ссылки на аудиоплощадки и прямые аудиофайлы."""
    if re.search(AUDIO_REGEX, url, re.IGNORECASE):
        return True
    clean_url = url.lower().split("?", 1)[0].split("#", 1)[0]
    return clean_url.endswith((".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"))


async def resolve_video_request(callback: CallbackQuery, state: FSMContext, request_id: str | None) -> str:
    """Возвращает URL, привязанный именно к нажатой клавиатуре."""
    media_request = get_media_request(request_id)
    if media_request:
        return media_request.url

    # URL дублируется в сообщении с кнопками: это позволяет старым кнопкам
    # пережить очистку FSM и даже перезапуск процесса бота.
    message_text = callback.message.caption or callback.message.text or ""
    message_urls = re.findall(r"https?://[^\s]+", message_text, re.IGNORECASE)
    if message_urls:
        return message_urls[-1].rstrip('.,;!?)]}>"\'')

    user_data = await state.get_data()
    return user_data.get("video_url") or user_data.get("extract_url") or ""

# --- ОБЩИЕ КОМАНДЫ ---

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    command_parts = (message.text or "").split(maxsplit=1)
    start_parameter = command_parts[1].strip() if len(command_parts) > 1 else ""

    if start_parameter.startswith("album_"):
        await send_cached_album(message, start_parameter.removeprefix("album_"))
        return

    user_name = message.from_user.first_name
    
    welcome_text = (
        f"Привет, {user_name}! 👋\n\n"
        "Это бот музыкального объединения <b>ГУЧИГЕНГОВО</b>. "
        "Я помогаю скачивать видео, фото и аудио из популярных социальных сетей и музыкальных площадок.\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Зайди в нужную соцсеть или приложение.\n"
        "2. Найди интересное видео, фото или трек.\n"
        "3. Нажми кнопку «Скопировать ссылку».\n"
        "4. Отправь ссылку мне (или выбери пункт в меню ниже), и я пришлю тебе готовый файл! 👇\n\n"
        "💡 <i>А ещё ты можешь просто прислать мне любую ссылку на видео в чат, без нажатий кнопок!</i>"
    )
    
    await message.answer(welcome_text, reply_markup=get_welcome_menu(), parse_mode="HTML")


async def send_cached_album(message: Message, album_key: str):
    """Sends every cached album track to the private chat in album order."""
    album = get_album(album_key)
    if not album:
        await message.answer(
            "❌ Данные альбома устарели. Вернитесь в чат, заново выберите трек через inline-поиск и нажмите кнопку альбома ещё раз."
        )
        return

    tracks = album.get("tracks") or []
    album_title = album.get("album") or "Альбом"
    artist = album.get("artist") or "Неизвестно"

    status_msg = await message.answer(
        f"💿 <b>{html.escape(album_title)}</b>\n"
        "⏳ Получаю состав альбома...",
        parse_mode="HTML",
    )

    # Состав альбома загружается только после перехода по deep-link. Раньше
    # каждый альбом извлекался прямо во время inline-поиска и Telegram не
    # успевал получить результаты.
    if not tracks and album.get("album_url"):
        tracks = await get_album_tracks(
            album["album_url"],
            fallback_artist=artist,
        )
        album["tracks"] = tracks

    if not tracks:
        await status_msg.edit_text(
            "❌ В этом альбоме не удалось найти треки для загрузки."
        )
        return

    total = len(tracks)
    await status_msg.edit_text(
        f"💿 <b>{html.escape(album_title)}</b> — нашёл {total} трек(ов).\n"
        "Начинаю загружать по порядку альбома...",
        parse_mode="HTML",
    )

    for index, track in enumerate(tracks, start=1):
        user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
        os.makedirs(user_temp_dir, exist_ok=True)
        try:
            await status_msg.edit_text(
                f"💿 <b>{html.escape(album_title)}</b>\n"
                f"⏳ Загружаю {index}/{total}: {html.escape(track.get('title') or 'Неизвестно')}",
                parse_mode="HTML"
            )

            result = await download_from_url(track.get("url"), user_temp_dir)
            if not result["success"]:
                await message.answer(
                    f"⚠️ Не удалось загрузить {index}/{total}: {html.escape(track.get('title') or 'Неизвестно')}\n"
                    f"Причина: {html.escape(result.get('error') or 'неизвестная ошибка')}",
                    parse_mode="HTML"
                )
                continue

            title = result.get("title") or track.get("title") or "Неизвестно"
            performer = result.get("artist") or track.get("artist") or artist
            audio_path = result["audio_path"]
            cover_path = result.get("thumbnail_path")

            if cover_path and os.path.exists(cover_path):
                audio_path = await add_cover_to_mp3(audio_path, cover_path, title, performer)

            caption = (
                f"💿 <b>{html.escape(album_title)}</b>\n"
                f"{index}/{total}. 🎵 {html.escape(title)}\n"
                f"👤 {html.escape(performer)}\n\n"
                f"❤️ @GG_Loader_bot"
            )
            await message.answer_audio(
                audio=FSInputFile(audio_path),
                title=title,
                performer=performer,
                caption=caption,
                parse_mode="HTML",
                thumb=FSInputFile(cover_path) if cover_path and os.path.exists(cover_path) else None,
            )
        except Exception as e:
            logger.error(f"Error sending album track {index}/{total}: {e}")
            await message.answer(f"⚠️ Ошибка при загрузке трека {index}/{total}.")
        finally:
            await cleanup_temp_files(user_temp_dir)

    await status_msg.edit_text(f"✅ Альбом <b>{html.escape(album_title)}</b> загружен.", parse_mode="HTML")

@router.callback_query(F.data == "back_to_menu")
@router.callback_query(F.data == "cancel_action")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    for temp_dir_key in ("temp_dir", "local_temp_dir"):
        temp_dir = user_data.get(temp_dir_key)
        if temp_dir:
            await cleanup_temp_files(temp_dir)

    await state.clear()
    await callback.message.edit_text(
        "Вы вернулись в главное меню. Что будем делать? 👇",
        reply_markup=get_welcome_menu(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "about_guchi")
async def about_guchi(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Шуточное объединение из России, положившее своё начало 3 ноября 2024 года</b>",
        reply_markup=get_about_guchi_keyboard(),
        parse_mode="HTML"
    )


# --- 1. УНИВЕРСАЛЬНОЕ СКАЧИВАНИЕ ВИДЕО (РАБОТАЕТ ВСЕГДА И СРАЗУ) ---

@router.callback_query(F.data == "download_video")
async def process_download_video(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🎥 <b>Скачивание видео</b>\n\nОтправьте мне ссылку на видео (YouTube, VK, Instagram, TikTok и др.):",
        reply_markup=get_back_keyboard(), parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_video_link)

@router.message(StateFilter(MediaStates.waiting_for_video_link), F.text.regexp(r'https?://[^\s]+'))
async def handle_video_link(message: Message, state: FSMContext):
    url = extract_url(message.text)
    msg = await message.answer("⏳ Анализирую ссылку и ищу доступные форматы...")
    
    # FSM остаётся запасным вариантом для старых сообщений без request_id.
    await state.update_data(
        video_url=url,
        extract_url=url,
        local_video_path=None,
        local_temp_dir=None,
    )
    
    formats_result = await asyncio.to_thread(get_video_formats, url)
    if not formats_result['success'] or not formats_result['formats']:
        await msg.edit_text(f"❌ Ошибка или форматы не найдены.\n{formats_result.get('error', '')}")
        await state.clear()
        return

    request_id = save_media_request(url, formats_result['title'])
    keyboard = get_video_quality_keyboard(
        url, formats_result['formats'], formats_result['title'], request_id=request_id
    )
    
    info_text = (
        f"🍿 <b>{html.escape(formats_result['title'])}</b>\n"
        f"🔗 <code>{html.escape(url)}</code>\n\n"
        "Выберите качество. Кнопки останутся активными — можно скачать несколько вариантов."
    )
    await state.set_state(None)
    
    if formats_result.get('thumbnail'):
        try:
            await message.answer_photo(
                photo=formats_result['thumbnail'],
                caption=info_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await msg.delete()
            return
        except Exception as e:
            logger.warning(f"Не удалось отправить фото: {e}")
            
    await msg.edit_text(info_text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("viddl_"))
@router.callback_query(F.data.startswith("viddl:"))
async def download_selected_video(callback: CallbackQuery, state: FSMContext):
    if callback.data.startswith("viddl:"):
        _, request_id, format_id = callback.data.split(":", 2)
    else:
        request_id = None
        format_id = callback.data.split("_", 1)[1]
    video_url = await resolve_video_request(callback, state, request_id)
    
    if not video_url:
        await callback.answer("❌ Не удалось прочитать ссылку из сообщения с кнопками.", show_alert=True)
        return

    await callback.answer()
    status_msg = await callback.message.answer("⏳ Загружаю видео в выбранном качестве... Пожалуйста, подождите.")
    
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    try:
        result = await download_video(video_url, user_temp_dir, format_id)
        if result['success']:
            video_file = FSInputFile(result['video_path'])
            
            caption = (
                f"🍿 {html.escape(result['title'])}\n\n"
                f"🔗 {html.escape(result.get('url', video_url))}\n\n"
                f"🎥 {html.escape(result.get('quality', '1080p'))}\n\n"
                f"❤️ @GG_Loader_bot"
            )
            
            thumb_file = FSInputFile(result['thumbnail_path']) if result['thumbnail_path'] and os.path.exists(result['thumbnail_path']) else None
            
            # Сначала отправляем как video, чтобы Telegram показывал плеер. Если Bot API
            # откажется принимать большой/нестандартный файл, пробуем отправить документом
            # и показываем пользователю реальную причину вместо общего "непредвиденная ошибка".
            try:
                await callback.message.answer_video(
                    video=video_file,
                    caption=caption,
                    width=result.get('width', 1920),
                    height=result.get('height', 1080),
                    cover=thumb_file,
                    supports_streaming=True,
                    parse_mode="HTML"
                )
            except Exception as send_video_error:
                logger.warning(f"Video send failed, trying document fallback: {send_video_error}")
                try:
                    await callback.message.answer_document(
                        document=FSInputFile(result['video_path']),
                        caption=caption,
                        parse_mode="HTML"
                    )
                except Exception as send_document_error:
                    logger.error(f"Document send failed: {send_document_error}")
                    size_mb = (result.get('filesize') or os.path.getsize(result['video_path'])) / (1024 * 1024)
                    api_hint = (
                        "Проверьте TELEGRAM_API_BASE_URL и запуск telegram-bot-api с --local."
                        if TELEGRAM_LOCAL_API
                        else
                        "Сейчас используется облачный Bot API с лимитом 50 МБ. "
                        "Для файлов до 2000 МБ задайте TELEGRAM_API_BASE_URL локального "
                        "telegram-bot-api, запущенного с --local."
                    )
                    await status_msg.edit_text(
                        "❌ Telegram не принял файл для отправки.\n"
                        f"Размер файла: {size_mb:.1f} МБ.\n"
                        f"Причина: {html.escape(str(send_document_error))}\n\n"
                        f"Настроенный лимит: {TELEGRAM_MAX_UPLOAD_MB} МБ. {api_hint}"
                    )
                    return
            await status_msg.delete()
        else:
            await status_msg.edit_text(f"❌ Ошибка при скачивании: {result['error']}")
    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ Произошла ошибка при отправке.\n{html.escape(str(e))}")
    finally:
        await cleanup_temp_files(user_temp_dir)

@router.callback_query(F.data == "vid_audio_extract")
@router.callback_query(F.data.startswith("vid_audio:"))
async def download_audio_from_video_btn(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки «🎵 Audio» прямо из меню выбора разрешения видео"""
    request_id = callback.data.partition(":")[2] or None
    video_url = await resolve_video_request(callback, state, request_id)
    
    if not video_url:
        await callback.answer("❌ Не удалось прочитать ссылку из сообщения с кнопками.", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(
        f"🍿 Видео: <code>{html.escape(video_url)}</code>\n\n"
        "🎵 <b>В каком формате вы хотите получить аудиодорожку?</b>\n\n"
        "• <b>MP3</b> — музыкальный трек с обложкой и тегами.\n"
        "• <b>Голосовое сообщение</b> — аудиосообщение для быстрой прослушки и пересылки.",
        reply_markup=get_extract_format_keyboard(request_id=request_id or ""),
        parse_mode="HTML"
    )


# --- 2. СКАЧИВАНИЕ АУДИО ПО ССЫЛКЕ ---

@router.callback_query(F.data == "download_audio")
async def process_download_audio(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🎵 <b>Загрузка аудио</b>\n\nОтправьте мне ссылку на трек (SoundCloud, VK, Yandex Music, YouTube Music, Spotify и др.):",
        reply_markup=get_back_keyboard(), parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_audio_link)

@router.message(StateFilter(MediaStates.waiting_for_audio_link), F.text.regexp(r'https?://[^\s]+'))
async def handle_audio_link(message: Message, state: FSMContext):
    url = extract_url(message.text)
    msg = await message.answer("🎵 Вижу ссылку на аудио! Начинаю загрузку с обложкой и метаданными...")
    
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    try:
        result = await download_from_url(url, user_temp_dir)
        if result['success']:
            audio_path = result['audio_path']
            title = result['title']
            artist = result['artist']
            cover_path = result.get('thumbnail_path')
            
            if cover_path and os.path.exists(cover_path):
                processed_path = await add_cover_to_mp3(audio_path, cover_path, title, artist)
            else:
                processed_path = audio_path
                
            audio_file = FSInputFile(processed_path)
            thumb_file = FSInputFile(cover_path) if cover_path and os.path.exists(cover_path) else None
            
            current_date = datetime.now().strftime("%d/%m/%Y")
            caption = (
                f"🎵 {html.escape(title)}\n"
                f"👤 {html.escape(artist)}\n"
                f"📅 {current_date}\n\n"
                f"❤️ @GG_Loader_bot"
            )
            
            await message.answer_audio(
                audio=audio_file, title=title, performer=artist,
                caption=caption,
                parse_mode="HTML", thumb=thumb_file
            )
            await msg.delete()
        else:
            await msg.edit_text(f"❌ Ошибка загрузки: {result['error']}")
    except Exception as e:
        logger.error(f"Error handling audio link: {e}")
        await msg.edit_text("❌ Произошла ошибка при загрузке трека. Проверьте ссылку или попробуйте позже.")
    finally:
        await cleanup_temp_files(user_temp_dir)
        await state.clear()



# --- ЗАГРУЗКА ВИДЕО В ФОРМАТЕ КРУЖОЧКА ---

@router.callback_query(F.data == "upload_video_note")
async def process_upload_video_note(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "⭕ <b>Загрузка видео кружочком</b>\n\n"
        "Отправьте видео прямо в этот чат.\n\n"
        "⚠️ <b>Важно:</b> видео должно быть <b>квадратным</b> и длиться "
        "<b>не более 1 минуты</b>. Иначе Telegram не позволит сделать из него кружочек.\n\n"
        "Отправляйте ролик именно как видео, а не как файл.",
        reply_markup=get_back_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_video_note)


@router.message(StateFilter(MediaStates.waiting_for_video_note), F.video)
async def handle_video_note_upload(message: Message, state: FSMContext):
    video = message.video

    if video.duration > 60:
        await message.answer(
            "❌ Видео длится больше 1 минуты. Telegram поддерживает кружочки "
            "длительностью не более 60 секунд. Пришлите более короткое видео.",
            reply_markup=get_back_keyboard()
        )
        return

    if video.width != video.height:
        await message.answer(
            f"❌ Видео должно быть квадратным. Сейчас размер: {video.width}×{video.height}. "
            "Обрежьте ролик до формата 1:1 и отправьте его снова.",
            reply_markup=get_back_keyboard()
        )
        return

    if not FFMPEG_EXECUTABLE:
        await message.answer(
            "❌ На сервере недоступен FFmpeg, поэтому сейчас подготовить кружочек не получится. "
            "Сообщите администратору бота.",
            reply_markup=get_back_keyboard()
        )
        await state.clear()
        return

    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    input_path = os.path.join(user_temp_dir, f"input_{video.file_unique_id}.mp4")
    output_path = os.path.join(user_temp_dir, "video_note.mp4")
    status_msg = await message.answer("⏳ Готовлю видео-кружочек...")

    try:
        file = await message.bot.get_file(video.file_id)
        await message.bot.download_file(file.file_path, input_path)

        command = [
            FFMPEG_EXECUTABLE,
            "-y",
            "-nostdin",
            "-i", input_path,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-vf", "scale=480:480,setsar=1",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "24",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "96k",
            "-movflags", "+faststart",
            "-t", "60",
            output_path,
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0 or not os.path.exists(output_path):
            error_text = stderr.decode("utf-8", errors="replace")[-2000:]
            logger.error("FFmpeg video note conversion failed: %s", error_text)
            raise RuntimeError("Не удалось преобразовать видео в формат кружочка.")

        await message.answer_video_note(
            video_note=FSInputFile(output_path),
            duration=video.duration,
            length=480,
        )
        await status_msg.delete()
    except Exception as e:
        logger.exception("Error creating video note: %s", e)
        await status_msg.edit_text(
            "❌ Не удалось сделать кружочек из этого видео. "
            "Проверьте, что ролик квадратный, длится не более минуты и попробуйте снова."
        )
    finally:
        await cleanup_temp_files(user_temp_dir)
        await state.clear()


@router.message(StateFilter(MediaStates.waiting_for_video_note))
async def handle_invalid_video_note_upload(message: Message):
    await message.answer(
        "❌ Отправьте квадратный ролик длительностью не более 1 минуты именно как видео.",
        reply_markup=get_back_keyboard()
    )


# --- 3. ИЗВЛЕЧЕНИЕ АУДИО ИЗ ВИДЕО ---

@router.callback_query(F.data == "extract_audio")
async def process_extract_audio_btn(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔊 <b>Извлечение аудио</b>\n\nОтправьте мне ссылку на видео <b>ИЛИ</b> просто прикрепите и пришлите сам видеофайл в этот чат, а я достану из него звук:",
        reply_markup=get_back_keyboard(), parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_extract_link)

@router.message(StateFilter(MediaStates.waiting_for_extract_link), F.text.regexp(r'https?://[^\s]+'))
async def handle_extract_link(message: Message, state: FSMContext):
    url = extract_url(message.text)
    await state.update_data(extract_url=url, local_video_path=None)
    await message.answer(
        f"🔗 Вижу ссылку: <code>{html.escape(url)}</code>\n\n"
        "🎵 <b>В каком формате вы хотите получить аудиодорожку?</b>\n\n"
        "• <b>MP3</b> — полноценный музыкальный файл с обложкой и тегами (удобно слушать в плеере).\n"
        "• <b>Голосовое сообщение</b> — аудиосообщение в чате (удобно быстро переслать или послушать х2).",
        reply_markup=get_extract_format_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_extract_format)

@router.message(F.video | F.document)
async def handle_video_file_for_audio(message: Message, state: FSMContext):
    """Перехватывает видеофайлы для быстрого извлечения звука"""
    video_obj = message.video if message.video else message.document
    
    if message.document:
        mime = getattr(message.document, 'mime_type', '') or ''
        fname = getattr(message.document, 'file_name', '') or ''
        if not (mime.startswith('video/') or fname.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))):
            return

    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    video_path = os.path.join(user_temp_dir, f"input_{video_obj.file_unique_id}.mp4")
    status_msg = await message.answer("⏳ Сохраняю ваше видео на сервере для извлечения аудио...")
    
    try:
        file = await message.bot.get_file(video_obj.file_id)
        await message.bot.download_file(file.file_path, video_path)
        
        title = "Аудио из видео"
        if getattr(video_obj, 'file_name', None):
            title = os.path.splitext(video_obj.file_name)[0]
        elif message.caption:
            title = message.caption[:30].strip()
            
        await state.update_data(
            local_video_path=video_path,
            local_temp_dir=user_temp_dir,
            video_title=title,
            video_artist="GG_Loader",
            extract_url=None,
            video_url=None
        )
        
        await status_msg.edit_text(
            f"🍿 <b>Видео успешно получено!</b>\n\n"
            "🎵 <b>В каком формате вы хотите получить аудиодорожку?</b>\n\n"
            "• <b>MP3</b> — полноценный музыкальный файл с тегами (удобно слушать в плеере).\n"
            "• <b>Голосовое сообщение</b> — аудиосообщение в чате (удобно быстро переслать или послушать х2).",
            reply_markup=get_extract_format_keyboard(),
            parse_mode="HTML"
        )
        await state.set_state(MediaStates.waiting_for_extract_format)
    except Exception as e:
        logger.error(f"Error downloading video file from tg: {e}")
        await status_msg.edit_text("❌ Произошла ошибка при загрузке видеофайла из Telegram. Попробуйте отправить видео меньшего размера или ссылку.")
        await cleanup_temp_files(user_temp_dir)
        await state.clear()

@router.callback_query(F.data.startswith("ext_fmt_mp3:"))
@router.callback_query(F.data.startswith("ext_fmt_voice:"))
@router.callback_query(StateFilter(MediaStates.waiting_for_extract_format, None), F.data.in_({"ext_fmt_mp3", "ext_fmt_voice"}))
async def process_extract_format_selection(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    request_id = callback.data.partition(":")[2] or None
    url = await resolve_video_request(callback, state, request_id) if request_id else (
        user_data.get("extract_url") or user_data.get("video_url")
    )
    local_video_path = user_data.get("local_video_path")
    local_temp_dir = user_data.get("local_temp_dir")
    if request_id:
        # Callback с request_id всегда относится к удалённой ссылке конкретной
        # клавиатуры и не должен случайно использовать старый локальный файл из FSM.
        local_video_path = None
        local_temp_dir = None
    
    if not url and not local_video_path:
        await callback.answer("❌ Ошибка: ссылка или файл потеряны. Отправьте видео заново.", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    is_voice = callback.data.startswith("ext_fmt_voice")
    fmt_name = "голосовое сообщение" if is_voice else "MP3 файл"
    
    status_msg = await callback.message.answer(f"⏳ Извлекаю аудио как {fmt_name}... Пожалуйста, подождите.")
    
    user_temp_dir = local_temp_dir if local_temp_dir else os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    try:
        output_format = 'voice' if is_voice else 'mp3'
        
        if local_video_path and os.path.exists(local_video_path):
            title = user_data.get("video_title", "Аудио из видео")
            artist = user_data.get("video_artist", "GG_Loader")
            result = await extract_audio_from_local_video(local_video_path, user_temp_dir, output_format=output_format, title=title, artist=artist)
        else:
            result = await download_audio_from_video(url, user_temp_dir, output_format=output_format)
        
        if result['success']:
            audio_path = result['audio_path']
            
            if is_voice:
                voice_file = FSInputFile(audio_path)
                caption = (
                    f"🎙 <b>{html.escape(result['title'])}</b>\n"
                    f"👤 {html.escape(result['artist'])}\n\n"
                    f"❤️ @GG_Loader_bot"
                )
                await callback.message.answer_voice(
                    voice=voice_file,
                    caption=caption,
                    parse_mode="HTML"
                )
            else:
                cover_path = result.get('thumbnail_path')
                if cover_path and os.path.exists(cover_path):
                    processed_path = await add_cover_to_mp3(audio_path, cover_path, result['title'], result['artist'])
                else:
                    processed_path = audio_path
                    
                audio_file = FSInputFile(processed_path)
                thumb_file = FSInputFile(cover_path) if cover_path and os.path.exists(cover_path) else None
                
                current_date = datetime.now().strftime("%d/%m/%Y")
                caption = (
                    f"🎵 {html.escape(result['title'])}\n"
                    f"👤 {html.escape(result['artist'])}\n"
                    f"📅 {current_date}\n\n"
                    f"❤️ @GG_Loader_bot"
                )
                await callback.message.answer_audio(
                    audio=audio_file, title=result['title'], performer=result['artist'],
                    caption=caption,
                    parse_mode="HTML", thumb=thumb_file
                )
            
            await status_msg.delete()
            if not request_id:
                try:
                    await callback.message.delete()
                except Exception:
                    pass
        else:
            await status_msg.edit_text(f"❌ Ошибка при извлечении: {result['error']}")
    except Exception as e:
        logger.error(f"Error extracting audio format: {e}")
        await status_msg.edit_text("❌ Произошла непредвиденная ошибка при обработке.")
    finally:
        await cleanup_temp_files(user_temp_dir)
        if not request_id:
            await state.clear()


# --- 4. НАЛОЖЕНИЕ КАСТОМНОЙ ОБЛОЖКИ ---

@router.callback_query(F.data == "upload_cover")
async def process_upload_cover(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🖼 <b>Кастомная обложка</b>\n\nДля начала отправьте мне сам <b>MP3-файл</b>:",
        reply_markup=get_back_keyboard(), parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_audio_file)

@router.message(StateFilter(MediaStates.waiting_for_audio_file), F.audio)
async def handle_custom_audio(message: Message, state: FSMContext):
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    audio_path = os.path.join(user_temp_dir, f"{message.audio.file_unique_id}.mp3")
    file = await message.bot.get_file(message.audio.file_id)
    await message.bot.download_file(file.file_path, audio_path)
    
    await state.update_data(audio_path=audio_path, temp_dir=user_temp_dir)
    await message.answer(
        "✅ Аудио получено! Теперь отправьте картинку для обложки (желательно квадратную):",
        reply_markup=get_back_keyboard()
    )
    await state.set_state(MediaStates.waiting_for_cover)

@router.message(StateFilter(MediaStates.waiting_for_cover), F.photo)
async def handle_custom_cover(message: Message, state: FSMContext):
    data = await state.get_data()
    user_temp_dir = data['temp_dir']
    
    cover_path = os.path.join(user_temp_dir, "cover.jpg")
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    await message.bot.download_file(file.file_path, cover_path)
    
    await state.update_data(cover_path=cover_path)
    await message.answer(
        "✅ Обложка загружена!\n\nТеперь отправьте название трека и исполнителя в формате:\n<code>Название - Исполнитель</code>",
        reply_markup=get_back_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_track_info)

@router.message(StateFilter(MediaStates.waiting_for_track_info), F.text)
async def handle_custom_track_info(message: Message, state: FSMContext):
    text = message.text.strip()
    if " - " in text:
        title, artist = map(str.strip, text.split(" - ", 1))
    else:
        title, artist = text, "Неизвестно"
        
    await state.update_data(title=title, artist=artist)
    
    await message.answer(
        "✅ Название и автор сохранены!\n\n"
        "😉 <b>Опционально:</b> Отправьте ссылку на ваш канал (например, @guchigengovo), "
        "чтобы она отображалась в сообщении с треком.\n"
        "Если не хотите, нажмите кнопку «Пропустить»:",
        reply_markup=get_skip_channel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_channel_link)

@router.callback_query(StateFilter(MediaStates.waiting_for_channel_link), F.data == "skip_channel_link")
async def skip_channel_link(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await process_final_audio(callback.message, state, channel_link=None)

@router.message(StateFilter(MediaStates.waiting_for_channel_link), F.text)
async def handle_custom_channel_link(message: Message, state: FSMContext):
    channel_link = message.text.strip()
    await process_final_audio(message, state, channel_link)

async def process_final_audio(message: Message, state: FSMContext, channel_link: str = None):
    data = await state.get_data()
    audio_path = data.get('audio_path')
    cover_path = data.get('cover_path')
    user_temp_dir = data.get('temp_dir')
    title = data.get('title')
    artist = data.get('artist')
    
    msg = await message.answer("🛠 Свожу трек и обложку...")
    
    try:
        processed_path = await add_cover_to_mp3(audio_path, cover_path, title, artist)
        audio_file = FSInputFile(processed_path)
        thumb_file = FSInputFile(cover_path) if os.path.exists(cover_path) else None
        
        current_date = datetime.now().strftime("%d/%m/%Y")
        caption = (
            f"🎵 {html.escape(title)}\n"
            f"👤 {html.escape(artist)}\n"
        )
        if channel_link:
            caption += f"😉 {html.escape(channel_link)}\n"
            
        caption += (
            f"📅 {current_date}\n\n"
            f"❤️ @GG_Loader_bot"
        )
        
        await message.answer_audio(
            audio=audio_file, title=title, performer=artist,
            caption=caption,
            parse_mode="HTML", thumb=thumb_file
        )
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка обработки: {str(e)}")
    finally:
        await cleanup_temp_files(user_temp_dir)
        await state.clear()


@router.message(StateFilter(None), F.text.regexp(r'https?://[^\s]+'))
async def handle_direct_media_link(message: Message, state: FSMContext):
    """Обрабатывает ссылку, отправленную напрямую, без выбора пункта меню."""
    url = extract_url(message.text)
    if is_audio_url(url):
        await handle_audio_link(message, state)
    else:
        await handle_video_link(message, state)
