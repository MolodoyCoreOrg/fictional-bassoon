import os
import shutil
from dotenv import load_dotenv

load_dotenv()

# Токен бота от BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "8635732122:AAFltEPy2-CjI-p3o1RM5V013pJyhjbBQRo")

# ID админов (можно добавить свои)
ADMINS = [int(id.strip()) for id in os.getenv("ADMINS", "").split(",") if id.strip()]

# Путь к временным файлам
TEMP_DIR = os.path.join(os.path.dirname(__file__), "..", "temp")

# Ссылка на ГУЧИГЕНГОВО
GUCHI_LINK = "https://band.link/guchigengovo"

def get_ffmpeg_location():
    """
    Автоматический поиск пути к директории с исполняемыми файлами ffmpeg и ffprobe.
    Сначала проверяет переменную окружения FFMPEG_LOCATION в .env, 
    затем системный PATH, затем стандартные директории установки в разных ОС.
    """
    # 1. Проверяем переменную из .env
    env_path = os.getenv("FFMPEG_LOCATION")
    if env_path and os.path.exists(env_path):
        if os.path.isfile(env_path):
            return os.path.dirname(env_path)
        return env_path

    # 2. Поиск через системный PATH
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        return os.path.dirname(which_ffmpeg)

    # 3. Популярные пути установки в Linux, macOS и Windows
    common_paths = [
        "/usr/bin",
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/var/www/music_bot/venv/bin",
        "/usr/lib/ffmpeg",
        r"C:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
        r"C:\Program Files (x86)\ffmpeg\bin",
        r"C:\tools\ffmpeg\bin",
        r"D:\ffmpeg\bin",
    ]
    for path in common_paths:
        if os.path.exists(path):
            for exe in ["ffmpeg", "ffmpeg.exe"]:
                if os.path.exists(os.path.join(path, exe)):
                    return path
    return None

# Определяем путь к ffmpeg для yt-dlp
FFMPEG_LOCATION = get_ffmpeg_location()