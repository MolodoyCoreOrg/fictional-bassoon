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


def _existing_executable(path: str) -> str | None:
    """Возвращает путь к существующему исполняемому файлу или None."""
    if not path or not os.path.isfile(path):
        return None
    return path if os.access(path, os.X_OK) or os.name == "nt" else None


def _find_executable_in_dir(path: str, names: list[str]) -> str | None:
    """Ищет исполняемый файл в указанной директории."""
    if not path or not os.path.isdir(path):
        return None
    for name in names:
        executable = _existing_executable(os.path.join(path, name))
        if executable:
            return executable
    return None


def _resolve_executable(path: str, names: list[str]) -> str | None:
    """Принимает путь к файлу или директории и возвращает найденный бинарник."""
    if not path:
        return None
    expanded_path = os.path.expanduser(os.path.expandvars(path.strip().strip('"').strip("'")))
    if os.path.isfile(expanded_path):
        basename = os.path.basename(expanded_path).lower()
        valid_names = {name.lower() for name in names}
        if basename in valid_names or basename.split(".")[0] in {name.split(".")[0].lower() for name in names}:
            return _existing_executable(expanded_path)
        return None
    return _find_executable_in_dir(expanded_path, names)


def _get_imageio_ffmpeg_executable() -> str | None:
    """Возвращает bundled FFmpeg из imageio-ffmpeg, если пакет установлен."""
    if not imageio_ffmpeg:
        return None
    try:
        bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    return _existing_executable(bundled_ffmpeg)


def get_ffmpeg_executable() -> str | None:
    """
    Находит исполняемый файл ffmpeg.

    Важно: пакет imageio-ffmpeg поставляет только ffmpeg без ffprobe. Для локального
    извлечения аудио и большинства postprocessor-операций достаточно ffmpeg, поэтому
    бот не должен считать установку сломанной только из-за отсутствия ffprobe.
    """
    env_path = os.getenv("FFMPEG_LOCATION")
    env_ffmpeg = _resolve_executable(env_path, ["ffmpeg", "ffmpeg.exe"])
    if env_ffmpeg:
        return env_ffmpeg

    path_ffmpeg = shutil.which("ffmpeg")
    if path_ffmpeg:
        return path_ffmpeg

    bundled_ffmpeg = _get_imageio_ffmpeg_executable()
    if bundled_ffmpeg:
        return bundled_ffmpeg

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
        ffmpeg = _find_executable_in_dir(path, ["ffmpeg", "ffmpeg.exe"])
        if ffmpeg:
            return ffmpeg
    return None


def get_ffprobe_executable() -> str | None:
    """Находит ffprobe рядом с FFMPEG_LOCATION, в PATH или стандартных директориях."""
    env_path = os.getenv("FFMPEG_LOCATION")
    env_ffprobe = _resolve_executable(env_path, ["ffprobe", "ffprobe.exe"])
    if env_ffprobe:
        return env_ffprobe

    ffmpeg = get_ffmpeg_executable()
    if ffmpeg:
        sibling = _find_executable_in_dir(os.path.dirname(ffmpeg), ["ffprobe", "ffprobe.exe"])
        if sibling:
            return sibling

    path_ffprobe = shutil.which("ffprobe")
    if path_ffprobe:
        return path_ffprobe

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
        ffprobe = _find_executable_in_dir(path, ["ffprobe", "ffprobe.exe"])
        if ffprobe:
            return ffprobe
    return None


def get_ffmpeg_location():
    """
    Возвращает значение для yt-dlp ffmpeg_location.

    Если задан FFMPEG_LOCATION, поддерживаются оба варианта: путь к папке с бинарниками
    и прямой путь к ffmpeg. Без переменной окружения бот использует системный ffmpeg
    или bundled ffmpeg из imageio-ffmpeg.
    """
    ffmpeg = get_ffmpeg_executable()
    if not ffmpeg:
        return None

    env_path = os.getenv("FFMPEG_LOCATION")
    if env_path:
        expanded_path = os.path.expanduser(os.path.expandvars(env_path.strip().strip('"').strip("'")))
        if os.path.isdir(expanded_path):
            return expanded_path
        if os.path.isfile(expanded_path):
            return expanded_path

    ffprobe = get_ffprobe_executable()
    if ffprobe and os.path.dirname(ffprobe) == os.path.dirname(ffmpeg):
        return os.path.dirname(ffmpeg)
    return ffmpeg


# Определяем путь к ffmpeg для yt-dlp
FFMPEG_LOCATION = get_ffmpeg_location()
FFMPEG_EXECUTABLE = get_ffmpeg_executable()
FFPROBE_EXECUTABLE = get_ffprobe_executable()


def has_ffmpeg():
    """Проверяет, доступен ли ffmpeg для склейки и конвертации медиа."""
    return bool(FFMPEG_EXECUTABLE)


def has_ffprobe():
    """Проверяет, доступен ли ffprobe для анализа медиа."""
    return bool(FFPROBE_EXECUTABLE)


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
