"""
Модуль для управления клавиатурами и кнопками бота
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def create_button(text: str, callback_data: str = None, url: str = None) -> InlineKeyboardButton:
    """
    Создает одну кнопку
    
    :param text: Текст кнопки
    :param callback_data: Данные для callback (если это не URL-кнопка)
    :param url: URL для кнопки (если это URL-кнопка)
    :return: InlineKeyboardButton
    """
    if url:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def create_keyboard(buttons: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру из списка кнопок
    
    :param buttons: Список списков кнопок (каждый внутренний список - это ряд)
    :return: InlineKeyboardMarkup
    """
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Возвращает клавиатуру главного меню
    
    :return: InlineKeyboardMarkup с кнопками меню
    """
    buttons = [
        [create_button("ℹ️ О ГУЧИГЕНГОВО!", callback_data="about_guchi")],
        [create_button("🎵 Загрузить свою песню", callback_data="upload_own_song")],
        [create_button("📥 Загрузить музыку в Telegram", callback_data="download_music")],
        [create_button("🎬 Скачать видео", callback_data="download_video")],
    ]
    return create_keyboard(buttons)


def get_back_keyboard() -> InlineKeyboardMarkup:
    """
    Возвращает клавиатуру с кнопкой "Назад"
    
    :return: InlineKeyboardMarkup с кнопкой назад
    """
    buttons = [
        [create_button("🔙 Назад в меню", callback_data="back_to_menu")]
    ]
    return create_keyboard(buttons)


def get_about_guchi_keyboard() -> InlineKeyboardMarkup:
    """
    Возвращает клавиатуру для раздела "О ГУЧИГЕНГОВО"
    
    :return: InlineKeyboardMarkup с кнопками соцсетей и назад
    """
    buttons = [
        [create_button("🎵 BandLink", url="https://band.link/guchigengovo")],
        [create_button("🔙 Назад в меню", callback_data="back_to_menu")]
    ]
    return create_keyboard(buttons)


def get_upload_song_keyboard() -> InlineKeyboardMarkup:
    """
    Возвращает клавиатуру для загрузки своей песни
    
    :return: InlineKeyboardMarkup с инструкцией и кнопкой назад
    """
    buttons = [
        [create_button("🔙 Назад в меню", callback_data="back_to_menu")]
    ]
    return create_keyboard(buttons)


def get_download_music_keyboard() -> InlineKeyboardMarkup:
    """
    Возвращает клавиатуру для загрузки музыки
    
    :return: InlineKeyboardMarkup с инструкцией и кнопкой назад
    """
    buttons = [
        [create_button("🔙 Назад в меню", callback_data="back_to_menu")]
    ]
    return create_keyboard(buttons)


def get_download_video_keyboard() -> InlineKeyboardMarkup:
    """
    Возвращает клавиатуру для скачивания видео
    
    :return: InlineKeyboardMarkup с кнопкой назад
    """
    buttons = [
        [create_button("🔙 Назад в меню", callback_data="back_to_menu")]
    ]
    return create_keyboard(buttons)


def get_video_quality_keyboard(url: str, formats: list, title: str) -> InlineKeyboardMarkup:
    """
    Возвращает клавиатуру с выбором качества видео
    
    :param url: Ссылка на видео
    :param formats: Список форматов видео
    :param title: Название видео
    :return: InlineKeyboardMarkup с кнопками выбора качества
    """
    buttons = []
    
    # Добавляем кнопки с форматами (максимум 10)
    for fmt in formats[:10]:
        quality_text = f"{fmt['quality_label']} ({fmt['size_str']})"
        callback_data = f"video_quality:{url}|{fmt['format_id']}|{title[:50]}"
        buttons.append([create_button(quality_text, callback_data=callback_data)])
    
    # Кнопка извлечения аудио
    buttons.append([create_button("🎵 Извлечь аудио", callback_data=f"extract_audio:{url}")])
    
    # Кнопка отмены
    buttons.append([create_button("❌ Отмена", callback_data="cancel_video")])
    
    return create_keyboard(buttons)


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """
    Возвращает клавиатуру с кнопкой отмены
    
    :return: InlineKeyboardMarkup с кнопкой отмены
    """
    buttons = [
        [create_button("❌ Отмена", callback_data="cancel_video")]
    ]
    return create_keyboard(buttons)
