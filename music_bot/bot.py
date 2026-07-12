from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from handlers.main_handlers import router as main_router
from handlers.inline_handlers import router as inline_router
from utils.config import BOT_TOKEN, TEMP_DIR
import logging
import os

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Создаем директорию для временных файлов
os.makedirs(TEMP_DIR, exist_ok=True)


async def on_startup(bot: Bot):
    """Вызывается при запуске бота"""
    logging.info("Бот запущен!")
    
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
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
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
