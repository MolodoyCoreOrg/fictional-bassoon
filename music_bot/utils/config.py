import os
import shutil

try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None
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

# Путь к файлу cookies.txt для обхода блокировки YouTube ("Sign in to confirm you’re not a bot")
COOKIES_FILE = os.getenv("COOKIES_FILE", os.path.join(os.path.dirname(__file__), "..", "cookies.txt"))
if not os.path.exists(COOKIES_FILE):
    if os.path.exists("cookies.txt"):
        COOKIES_FILE = "cookies.txt"
    else:
        COOKIES_FILE = None

def _dir_has_ffmpeg_pair(path: str) -> bool:
    """Проверяет, что в директории есть и ffmpeg, и ffprobe."""
    if not path or not os.path.isdir(path):
        return False
    ffmpeg_found = any(os.path.exists(os.path.join(path, exe)) for exe in ["ffmpeg", "ffmpeg.exe"])
    ffprobe_found = any(os.path.exists(os.path.join(path, exe)) for exe in ["ffprobe", "ffprobe.exe"])
    return ffmpeg_found and ffprobe_found


def get_ffmpeg_location():
    """
    Автоматический поиск пути к директории с исполняемыми файлами ffmpeg и ffprobe.
    Сначала проверяет переменную окружения FFMPEG_LOCATION в .env, 
    затем системный PATH, затем стандартные директории установки в разных ОС.
    """
    # 1. Проверяем переменную из .env
    env_path = os.getenv("FFMPEG_LOCATION")
    if env_path and os.path.exists(env_path):
        candidate = os.path.dirname(env_path) if os.path.isfile(env_path) else env_path
        if _dir_has_ffmpeg_pair(candidate):
            return candidate

    # 2. Поиск через системный PATH
    which_ffmpeg = shutil.which("ffmpeg")
    which_ffprobe = shutil.which("ffprobe")
    if which_ffmpeg and which_ffprobe:
        ffmpeg_dir = os.path.dirname(which_ffmpeg)
        ffprobe_dir = os.path.dirname(which_ffprobe)
        if ffmpeg_dir == ffprobe_dir:
            return ffmpeg_dir
        return ffmpeg_dir

    # 3. Запасной бинарник из Python-пакета imageio-ffmpeg
    if imageio_ffmpeg:
        try:
            bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            bundled_dir = os.path.dirname(bundled_ffmpeg) if bundled_ffmpeg and os.path.exists(bundled_ffmpeg) else None
            if bundled_dir and _dir_has_ffmpeg_pair(bundled_dir):
                return bundled_dir
        except Exception:
            pass

    # 4. Популярные пути установки в Linux, macOS и Windows
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
        if _dir_has_ffmpeg_pair(path):
            return path
    return None

# Определяем путь к ffmpeg для yt-dlp
FFMPEG_LOCATION = get_ffmpeg_location()

def has_ffmpeg():
    """Проверяет, доступны ли ffmpeg и ffprobe для склейки и конвертации медиа."""
    if FFMPEG_LOCATION and _dir_has_ffmpeg_pair(FFMPEG_LOCATION):
        return True
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

def get_anti_block_opts():
    """
    Возвращает единый набор настроек для yt-dlp, помогающий обойти блокировки YouTube 
    и других сервисов (ошибка Sign in to confirm you’re not a bot).
    Автоматически подключает cookies.txt, если файл существует.
    """
    opts = {
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android', 'tv', 'web'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        },
        'nocheckcertificate': True,
        'quiet': True,
        'no_warnings': True,
    }
    cookies_from_browser = os.getenv('COOKIES_FROM_BROWSER')
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    elif cookies_from_browser:
        parts = [part.strip() for part in cookies_from_browser.split(':') if part.strip()]
        if parts:
            opts['cookiesfrombrowser'] = tuple(parts)
    return opts
