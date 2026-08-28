from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_USERNAME = "GG_Loader_bot"

def create_button(text: str, callback_data: str = None, url: str = None) -> InlineKeyboardButton:
    if url:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=callback_data)

def create_keyboard(buttons: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_welcome_menu() -> InlineKeyboardMarkup:
    """Главное стартовое меню бота"""
    buttons = [
        [create_button("ℹ️ О ГУЧИГЕНГОВО", callback_data="about_guchi")],
        [create_button("🖼 Загрузить обложку для аудио", callback_data="upload_cover")],
        [create_button("🎥 Скачать видео из соцсетей", callback_data="download_video")],
        [create_button("⭕ Загрузить видео кружочком", callback_data="upload_video_note")],
        [create_button("🎵 Загрузить аудио по ссылке", callback_data="download_audio")],
        [create_button("🔊 Извлечь аудио из видео", callback_data="extract_audio")]
    ]
    return create_keyboard(buttons)

def get_back_keyboard() -> InlineKeyboardMarkup:
    return create_keyboard([[create_button("⬅️ Назад в меню", callback_data="back_to_menu")]])

def get_about_guchi_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [create_button("🔗 наши соц. сети", url="https://band.link/guchigengovo")],
        [create_button("🍒 СИСЬКИ", url="https://t.me/CuCbKu_gg_bot")],
        [create_button("📢 Основной канал ГУЧИГЕНГОВО", url="https://t.me/guchigengovo")],
        [create_button("👥 Участники Гучигенгово", url="https://t.me/guchigengovo/70")],
        [create_button("⬅️ Назад в меню", callback_data="back_to_menu")]
    ]
    return create_keyboard(buttons)

def get_video_quality_keyboard(url: str, formats: list, title: str, request_id: str = "") -> InlineKeyboardMarkup:
    """
    Генерирует кнопки только для разрешений, найденных у исходного видео
    (по 3 кнопки в каждом ряду + кнопка Audio внизу).
    """
    buttons = []
    current_row = []
    
    for fmt in formats:
        quality_text = f"🎬 {fmt['quality_label']}"
            
        callback_data = (
            f"viddl:{request_id}:{fmt['format_id']}"
            if request_id else f"viddl_{fmt['format_id']}"
        )
        current_row.append(create_button(quality_text, callback_data=callback_data))
        
        if len(current_row) == 3:
            buttons.append(current_row)
            current_row = []
            
    if current_row:
        buttons.append(current_row)
    
    audio_callback = f"vid_audio:{request_id}" if request_id else "vid_audio_extract"
    buttons.append([create_button("🎵 Audio", callback_data=audio_callback)])
    buttons.append([create_button("⬅️ Назад в меню", callback_data="back_to_menu")])
    
    return create_keyboard(buttons)

def get_extract_format_keyboard(request_id: str = "") -> InlineKeyboardMarkup:
    """Клавиатура выбора формата при извлечении аудио из видео (MP3 или Voice)"""
    suffix = f":{request_id}" if request_id else ""
    buttons = [
        [create_button("🎶 MP3 файл (с обложкой и тегами)", callback_data=f"ext_fmt_mp3{suffix}")],
        [create_button("🎙 Голосовое сообщение (Voice / OGG)", callback_data=f"ext_fmt_voice{suffix}")],
        [create_button("⬅️ Назад в меню", callback_data="back_to_menu")]
    ]
    return create_keyboard(buttons)

def get_skip_channel_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [create_button("Пропустить ⏭", callback_data="skip_channel_link")],
        [create_button("⬅️ Назад в меню", callback_data="back_to_menu")]
    ]
    return create_keyboard(buttons)


def get_inline_album_keyboard(album_title: str, album_key: str) -> InlineKeyboardMarkup:
    """Кнопка под inline-аудио, которая открывает альбом в личном чате с ботом."""
    safe_title = album_title.strip() or "Альбом"
    return create_keyboard([
        [create_button(f"💿 {safe_title}", url=f"https://t.me/{BOT_USERNAME}?start=album_{album_key}")]
    ])
