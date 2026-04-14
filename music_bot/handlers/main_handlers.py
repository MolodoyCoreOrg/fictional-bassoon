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
    # Устанавливаем состояние для FSM (будет реализовано отдельно)


@router.callback_query(F.data == "download_from_platform")
async def process_download_platform(callback: CallbackQuery):
    """Обработка запроса на загрузку с площадок"""
    await callback.message.edit_text(
        "📥 **Загрузка с музыкальных площадок**\n\n"
        "Отправьте мне ссылку на трек (VK Музыка, Яндекс Музыка, YouTube и др.)\n"
        "Я скачаю его и отправлю вам с обложкой.\n\n"
        "Или отправьте /cancel для отмены."
    )


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
    
    # Сохраняем информацию для следующего шага
    # (в реальном боте нужно использовать FSM states)
    await message.answer(
        "✅ Аудиофайл получен!\n\n"
        "Теперь отправьте обложку (квадратное изображение) для этого трека."
    )


@router.message(F.photo)
async def handle_cover_photo(message: Message):
    """Обработка обложки"""
    photo = message.photo[-1]  # Берем фото лучшего качества
    
    user_temp_dir = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    file = await message.bot.get_file(photo.file_id)
    cover_path = os.path.join(user_temp_dir, "cover.jpg")
    await message.bot.download_file(file.file_path, cover_path)
    
    await message.answer(
        "✅ Обложка получена!\n\n"
        "Теперь отправьте название трека и исполнителя в формате:\n"
        "`Название - Исполнитель`\n\n"
        "Или просто название, если исполнитель неизвестен."
    )


@router.message(F.text)
async def handle_track_info(message: Message):
    """Обработка названия и исполнителя"""
    text = message.text.strip()
    
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
    
    # Здесь будет логика обработки файла
    # Для демо просто подтверждаем
    await message.answer(
        "✅ Готово! Ваш трек обработан и готов к отправке.\n\n"
        "В полной версии бот применит обложку и метаданные к файлу."
    )
