from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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
        [create_button("🎵 Загрузить аудио по ссылке", callback_data="download_audio")],
        [create_button("🔊 Извлечь аудио из видео", callback_data="extract_audio")]
    ]
    return create_keyboard(buttons)

def get_back_keyboard() -> InlineKeyboardMarkup:
    return create_keyboard([[create_button("🔙 Назад в меню", callback_data="back_to_menu")]])

def get_about_guchi_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [create_button("🔗 наши соц. сети", url="https://band.link/guchigengovo")],
        [create_button("🍒 СИСЬКИ", url="https://t.me/CuCbKu_gg_bot")],
        [create_button("📢 Основной канал ГУЧИГЕНГОВО", url="https://t.me/guchigengovo")],
        [create_button("👥 Участники Гучигенгово", url="https://t.me/guchigengovo/70")],
        [create_button("🔙 Назад в меню", callback_data="back_to_menu")]
    ]
    return create_keyboard(buttons)

def get_video_quality_keyboard(url: str, formats: list, title: str) -> InlineKeyboardMarkup:
    """Генерирует клавиатуру как на скриншоте: кнопки в 2-3 ряда + кнопка Audio внизу"""
    buttons = []
    current_row = []
    
    # Берем до 6-9 лучших форматов и строим сетку по 3 кнопки в ряд
    for fmt in formats[:9]:
        # Формируем текст как на скриншоте: 1080p или 720p ( ⌛ )
        if fmt['size_str'] == "⌛":
            quality_text = f"🎬 {fmt['quality_label']} (⏳)"
        else:
            quality_text = f"🎬 {fmt['quality_label']}"
            
        callback_data = f"viddl_{fmt['format_id']}"
        current_row.append(create_button(quality_text, callback_data=callback_data))
        
        if len(current_row) == 3:
            buttons.append(current_row)
            current_row = []
            
    if current_row:
        buttons.append(current_row)
    
    # Добавляем широкую кнопку Audio внизу (как на фото 1)
    buttons.append([create_button("🎵 Audio", callback_data="vid_audio_extract")])
    buttons.append([create_button("❌ Отмена", callback_data="cancel_action")])
    
    return create_keyboard(buttons)

def get_extract_format_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора формата при извлечении аудио из видео (MP3 или Voice)"""
    buttons = [
        [create_button("🎶 MP3 файл (с обложкой и тегами)", callback_data="ext_fmt_mp3")],
        [create_button("🎙 Голосовое сообщение (Voice / OGG)", callback_data="ext_fmt_voice")],
        [create_button("❌ Отмена", callback_data="cancel_action")]
    ]
    return create_keyboard(buttons)