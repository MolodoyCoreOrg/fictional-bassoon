import os
import uuid
import logging
import html
import re
from datetime import datetime
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

# Импортируем состояния, клавиатуры и утилиты
from models.states import MediaStates
from utils.config import TEMP_DIR
from utils.keyboard import (
    get_welcome_menu, get_back_keyboard, 
    get_about_guchi_keyboard, get_video_quality_keyboard,
    get_extract_format_keyboard
)
from utils.video_downloader import get_video_formats, download_video, download_audio_from_video, detect_platform
from utils.music_downloader import download_from_url
from utils.audio_processor import add_cover_to_mp3, cleanup_temp_files

router = Router()
logger = logging.getLogger(__name__)

def extract_url(text: str) -> str:
    """Извлекает первую ссылку из текста сообщения (на случай, если пользователь отправил текст вместе со ссылкой)"""
    if not text:
        return ""
    match = re.search(r'https?://[^\s]+', text)
    return match.group(0) if match else text.strip()

# --- ОБЩИЕ КОМАНДЫ ---

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # Сбрасываем любые зависшие состояния
    user_name = message.from_user.first_name
    
    welcome_text = (
        f"Привет, {user_name}! 👋\n\n"
        "Это бот музыкального объединения <b>ГУЧИГЕНГОВО</b>. "
        "Я помогаю скачивать видео, фото и аудио из популярных социальных сетей и музыкальных площадок.\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Зайди в нужную соцсеть или приложение.\n"
        "2. Найди интересное видео, фото или трек.\n"
        "3. Нажми кнопку «Скопировать ссылку».\n"
        "4. Отправь ссылку мне (или выбери пункт в меню ниже), и я пришлю тебе готовый файл! 👇"
    )
    
    await message.answer(welcome_text, reply_markup=get_welcome_menu(), parse_mode="HTML")

@router.callback_query(F.data == "back_to_menu")
@router.callback_query(F.data == "cancel_action")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
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


# --- 1. СКАЧИВАНИЕ ВИДЕО ---

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
    
    formats_result = get_video_formats(url)
    if not formats_result['success'] or not formats_result['formats']:
        await msg.edit_text(f"❌ Ошибка или форматы не найдены.\n{formats_result.get('error', '')}")
        await state.clear()
        return

    await state.update_data(video_url=url)
    keyboard = get_video_quality_keyboard(url, formats_result['formats'], formats_result['title'])
    
    info_text = f"🍿 <b>{html.escape(formats_result['title'])}</b>"
    
    if formats_result.get('thumbnail'):
        try:
            await message.answer_photo(
                photo=formats_result['thumbnail'],
                caption=info_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await msg.delete()
            await state.set_state(None)
            return
        except Exception as e:
            logger.warning(f"Не удалось отправить фото: {e}")
            
    await msg.edit_text(info_text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(None)

@router.callback_query(F.data.startswith("viddl_"))
async def download_selected_video(callback: CallbackQuery, state: FSMContext):
    format_id = callback.data.split("_")[1]
    user_data = await state.get_data()
    video_url = user_data.get("video_url")
    
    if not video_url:
        await callback.answer("❌ Ошибка: ссылка потеряна. Отправьте её заново.", show_alert=True)
        return

    await callback.answer()
    status_msg = await callback.message.answer("⏳ Загружаю видео... Пожалуйста, подождите.")
    
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
            
            await callback.message.answer_video(
                video=video_file,
                caption=caption,
                parse_mode="HTML"
            )
            await status_msg.delete()
            try:
                await callback.message.delete()
            except:
                pass
        else:
            await status_msg.edit_text(f"❌ Ошибка при скачивании: {result['error']}")
    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text("❌ Произошла непредвиденная ошибка при отправке.")
    finally:
        await cleanup_temp_files(user_temp_dir)
        await state.clear()

@router.callback_query(F.data == "vid_audio_extract")
async def download_audio_from_video_btn(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки «🎵 Audio» прямо из меню выбора разрешения видео"""
    user_data = await state.get_data()
    video_url = user_data.get("video_url")
    
    if not video_url:
        await callback.answer("❌ Ошибка: ссылка потеряна.", show_alert=True)
        return

    await callback.answer()
    # Переводим в меню выбора формата (MP3 или Voice)
    await state.update_data(extract_url=video_url)
    await callback.message.edit_text(
        f"🍿 Видео: <code>{html.escape(video_url)}</code>\n\n"
        "🎵 <b>В каком формате вы хотите получить аудиодорожку?</b>\n\n"
        "• <b>MP3</b> — музыкальный трек с обложкой и тегами.\n"
        "• <b>Голосовое сообщение</b> — аудиосообщение для быстрой прослушки и пересылки.",
        reply_markup=get_extract_format_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_extract_format)


# --- 2. СКАЧИВАНИЕ АУДИО ПО ССЫЛКЕ ---

@router.callback_query(F.data == "download_audio")
async def process_download_audio(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🎵 <b>Загрузка аудио</b>\n\nОтправьте мне ссылку на трек (SoundCloud, VK, Yandex Music, YouTube Music, Spotify и др.):",
        reply_markup=get_back_keyboard(), parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_audio_link)

@router.message(StateFilter(MediaStates.waiting_for_audio_link), F.text.regexp(r'https?://[^\s]+'))
@router.message(StateFilter(None, MediaStates.waiting_for_audio_link), F.text.regexp(r'(https?://)?(www\.|m\.)?(soundcloud\.com|on\.soundcloud\.com|vk\.com/(audio|music|video)|vk\.ru/(audio|music|video)|music\.yandex\.(ru|com)|music\.youtube\.com|spotify\.com|deezer\.com|promodj\.com|mixcloud\.com|bandcamp\.com|audiomack\.com).*'))
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


# --- 3. ИЗВЛЕЧЕНИЕ АУДИО ИЗ ВИДЕО (С ВЫБОРОМ ФОРМАТА) ---

@router.callback_query(F.data == "extract_audio")
async def process_extract_audio_btn(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔊 <b>Извлечение аудио</b>\n\nОтправьте ссылку на видео, а я достану из него звук:",
        reply_markup=get_back_keyboard(), parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_extract_link)

@router.message(StateFilter(MediaStates.waiting_for_extract_link), F.text.regexp(r'https?://[^\s]+'))
async def handle_extract_link(message: Message, state: FSMContext):
    url = extract_url(message.text)
    await state.update_data(extract_url=url)
    await message.answer(
        f"🔗 Вижу ссылку: <code>{html.escape(url)}</code>\n\n"
        "🎵 <b>В каком формате вы хотите получить аудиодорожку?</b>\n\n"
        "• <b>MP3</b> — полноценный музыкальный файл с обложкой и тегами (удобно слушать в плеере).\n"
        "• <b>Голосовое сообщение</b> — аудиосообщение в чате (удобно быстро переслать или послушать х2).",
        reply_markup=get_extract_format_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(MediaStates.waiting_for_extract_format)

@router.callback_query(StateFilter(MediaStates.waiting_for_extract_format, None), F.data.in_({"ext_fmt_mp3", "ext_fmt_voice"}))
async def process_extract_format_selection(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    url = user_data.get("extract_url") or user_data.get("video_url")
    
    if not url:
        await callback.answer("❌ Ошибка: ссылка потеряна. Отправьте её заново.", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    is_voice = (callback.data == "ext_fmt_voice")
    fmt_name = "голосовое сообщение" if is_voice else "MP3 файл"
    
    status_msg = await callback.message.answer(f"⏳ Извлекаю аудио как {fmt_name}... Пожалуйста, подождите.")
    
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    try:
        output_format = 'voice' if is_voice else 'mp3'
        result = await download_audio_from_video(url, user_temp_dir, output_format=output_format)
        
        if result['success']:
            audio_path = result['audio_path']
            
            if is_voice:
                # Отправляем как голосовое сообщение Telegram (Voice Note)
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
                # Отправляем как полноценный MP3 трек с обложкой
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
            try:
                await callback.message.delete()
            except:
                pass
        else:
            await status_msg.edit_text(f"❌ Ошибка при извлечении: {result['error']}")
    except Exception as e:
        logger.error(f"Error extracting audio format: {e}")
        await status_msg.edit_text("❌ Произошла непредвиденная ошибка при обработке.")
    finally:
        await cleanup_temp_files(user_temp_dir)
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
    await message.answer("✅ Аудио получено! Теперь отправьте картинку для обложки (желательно квадратную):")
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
    
    skip_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Пропустить ⏭", callback_data="skip_channel_link")
    ]])
    
    await message.answer(
        "✅ Название и автор сохранены!\n\n"
        "😉 <b>Опционально:</b> Отправьте ссылку на ваш канал (например, @guchigengovo), "
        "чтобы она отображалась в сообщении с треком.\n"
        "Если не хотите, нажмите кнопку «Пропустить»:",
        reply_markup=skip_kb,
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