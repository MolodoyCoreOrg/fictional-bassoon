from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from handlers.main_handlers import router as main_router
from handlers.inline_handlers import router as inline_router
from utils.inline_media import start_inline_media_server, stop_inline_media_server
from utils.config import (
    BOT_TOKEN,
    FFMPEG_LOCATION,
    TELEGRAM_API_BASE_URL,
    TELEGRAM_LOCAL_FILE_MODE,
    TELEGRAM_MAX_UPLOAD_MB,
    TEMP_DIR,
)
import logging
import os

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Создаем директорию для временных файлов
os.makedirs(TEMP_DIR, exist_ok=True)


async def on_startup(bot: Bot):
    """Вызывается при запуске бота"""
    logging.info("Бот запущен!")
    await start_inline_media_server()
    logging.info(
        "Telegram Bot API: %s, лимит загрузки: %s МБ",
        TELEGRAM_API_BASE_URL or "https://api.telegram.org",
        TELEGRAM_MAX_UPLOAD_MB,
    )
    
    if FFMPEG_LOCATION:
        logging.info(f"✅ FFmpeg успешно обнаружен по пути: {FFMPEG_LOCATION}")
    else:
        logging.warning("⚠️ ВНИМАНИЕ: FFmpeg не найден в системе или в PATH! Для скачивания и конвертации аудио установите ffmpeg или укажите путь FFMPEG_LOCATION в .env")

    # Устанавливаем описание бота
    await bot.set_my_description(
        description="🎵 Бот для загрузки и обработки музыки\n"
                   "• Добавляет обложки к MP3\n"
                   "• Скачивает музыку с площадок\n"
                   "• Inline-режим для быстрого поиска"
    )
    
    # Устанавливаем текст для inline-режима
    await bot.set_my_short_description(
        short_description="🎵 Загрузка и обработка музыки"
    )


async def on_shutdown(bot: Bot):
    """Вызывается при остановке бота"""
    logging.info("Бот останавливается...")
    await stop_inline_media_server()
    await bot.session.close()


def create_dispatcher():
    """Создаёт и настраивает диспетчер"""
    dp = Dispatcher()
    
    # Регистрируем роутеры
    dp.include_router(main_router)
    dp.include_router(inline_router)
    
    # Регистрируем хуки старта/остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    return dp


async def run_bot():
    """Запуск бота"""
    session = None
    if TELEGRAM_API_BASE_URL:
        api_server = TelegramAPIServer.from_base(
            TELEGRAM_API_BASE_URL,
            is_local=TELEGRAM_LOCAL_FILE_MODE,
        )
        session = AiohttpSession(api=api_server)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    
    dp = create_dispatcher()
    
    try:
        # Запускаем polling
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logging.info("Остановка бота пользователем")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_bot())