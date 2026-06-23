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
        [create_button("🎵 BandLink", url="https://band.link/guchigengovo")],
        [create_button("🔙 Назад в меню", callback_data="back_to_menu")]
    ]
    return create_keyboard(buttons)

def get_video_quality_keyboard(url: str, formats: list, title: str) -> InlineKeyboardMarkup:
    buttons = []
    # Берем до 8 лучших форматов
    for fmt in formats[:8]:
        quality_text = f"{fmt['quality_label']} ({fmt['size_str']})"
        callback_data = f"viddl_{fmt['format_id']}"
        buttons.append([create_button(quality_text, callback_data=callback_data)])
    
    buttons.append([create_button("❌ Отмена", callback_data="cancel_action")])
    return create_keyboard(buttons)