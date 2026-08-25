import importlib.util
import os
import shutil
from pathlib import Path

try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
# systemd может запускать процесс из произвольного WorkingDirectory, поэтому
# загружаем .env относительно каталога приложения, а не текущего каталога.
load_dotenv(dotenv_path=PROJECT_DIR / ".env")

# Токен нельзя хранить в репозитории: публично раскрытый токен нужно отозвать.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в .env или переменных окружения")

# ID админов (можно добавить свои)
ADMINS = [int(id.strip()) for id in os.getenv("ADMINS", "").split(",") if id.strip()]

# Путь к временным файлам
TEMP_DIR = os.path.join(os.path.dirname(__file__), "..", "temp")

# Inline-аудио отдаётся Telegram через публичный HTTPS gateway этого же
# приложения. Reverse proxy должен направлять INLINE_MEDIA_BASE_URL на порт ниже.
INLINE_MEDIA_BASE_URL = os.getenv("INLINE_MEDIA_BASE_URL", "").strip().rstrip("/")
if INLINE_MEDIA_BASE_URL and not INLINE_MEDIA_BASE_URL.startswith("https://"):
    raise ValueError("INLINE_MEDIA_BASE_URL должен быть публичным HTTPS URL")
INLINE_MEDIA_HOST = os.getenv("INLINE_MEDIA_HOST", "0.0.0.0").strip() or "0.0.0.0"
INLINE_MEDIA_PORT = int(os.getenv("INLINE_MEDIA_PORT", "8080"))
if INLINE_MEDIA_PORT < 1 or INLINE_MEDIA_PORT > 65535:
    raise ValueError("INLINE_MEDIA_PORT должен быть в диапазоне 1..65535")
INLINE_MEDIA_CACHE_DIR = (
    os.getenv("INLINE_MEDIA_CACHE_DIR", "").strip()
    or os.path.join(TEMP_DIR, "inline_media_cache")
)
TRACK_HISTORY_DB = (
    os.getenv("TRACK_HISTORY_DB", "").strip()
    or str(PROJECT_DIR / "data" / "track_history.sqlite3")
)
_inline_storage_chat_id = os.getenv("INLINE_STORAGE_CHAT_ID", "").strip()
INLINE_STORAGE_CHAT_ID = int(_inline_storage_chat_id) if _inline_storage_chat_id else None

# Ссылка на ГУЧИГЕНГОВО
GUCHI_LINK = "https://band.link/guchigengovo"

# Облачный Bot API принимает загружаемые ботом файлы только до 50 МБ. Лимит
# 2000 МБ доступен исключительно через собственный telegram-bot-api в --local.
TELEGRAM_API_BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "").strip().rstrip("/") or None
TELEGRAM_LOCAL_API = bool(TELEGRAM_API_BASE_URL)
# Передача локального пути допустима только при общей ФС у бота и Bot API.
TELEGRAM_LOCAL_FILE_MODE = os.getenv("TELEGRAM_LOCAL_FILE_MODE", "").strip().lower() in {
    "1", "true", "yes", "on"
}
_telegram_upload_limit = os.getenv("TELEGRAM_MAX_UPLOAD_MB", "").strip()
TELEGRAM_MAX_UPLOAD_MB = int(
    _telegram_upload_limit or ("2000" if TELEGRAM_LOCAL_API else "50")
)
if TELEGRAM_MAX_UPLOAD_MB < 1 or TELEGRAM_MAX_UPLOAD_MB > 2000:
    raise ValueError("TELEGRAM_MAX_UPLOAD_MB должен быть в диапазоне 1..2000")
if TELEGRAM_LOCAL_FILE_MODE and not TELEGRAM_LOCAL_API:
    raise ValueError("TELEGRAM_LOCAL_FILE_MODE требует TELEGRAM_API_BASE_URL")


def _find_cookies_file() -> str | None:
    """Ищет cookies-файл независимо от текущей рабочей директории."""
    configured = os.getenv("COOKIES_FILE") or os.getenv("YOUTUBE_COOKIES_FILE")
    candidates = [
        configured,
        PROJECT_DIR / "cookies.txt",
        PROJECT_DIR / "data" / "cookies.txt",
        Path.cwd() / "cookies.txt",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(os.path.expandvars(os.path.expanduser(str(candidate)))).resolve()
        if path.is_file():
            return str(path)
    return None


COOKIES_FILE = _find_cookies_file()


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


def _split_env_list(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_anti_block_opts(use_cookies: bool = True):
    """
    Returns shared yt-dlp networking options and optional YouTube auth/POT data.
    Browser impersonation is enabled automatically when curl_cffi is installed.
    """
    opts = {
        'http_headers': {'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'},
        'nocheckcertificate': True,
        'quiet': True,
        'no_warnings': True,
        'retries': 5,
        'fragment_retries': 5,
        'extractor_retries': 5,
        'file_access_retries': 3,
    }

    if _env_flag('YTDLP_FORCE_IPV4', default=True):
        opts['source_address'] = '0.0.0.0'

    impersonate = os.getenv('YTDLP_IMPERSONATE')
    if impersonate is None and importlib.util.find_spec('curl_cffi') is not None:
        impersonate = 'chrome'
    if impersonate and impersonate.strip():
        opts['impersonate'] = impersonate.strip()

    pot_provider_url = os.getenv('YOUTUBE_POT_PROVIDER_URL', '').strip().rstrip('/')
    youtube_args = {}
    player_clients = _split_env_list('YOUTUBE_PLAYER_CLIENT')
    if not player_clients and pot_provider_url:
        # The current yt-dlp recommendation for provider-issued GVS tokens.
        player_clients = ['mweb']
    if player_clients:
        youtube_args['player_client'] = player_clients

    po_tokens = _split_env_list('YOUTUBE_PO_TOKEN')
    if po_tokens:
        youtube_args['po_token'] = po_tokens
    visitor_data = os.getenv('YOUTUBE_VISITOR_DATA', '').strip()
    if visitor_data:
        youtube_args['visitor_data'] = [visitor_data]

    extractor_args = {}
    if youtube_args:
        extractor_args['youtube'] = youtube_args
    if pot_provider_url:
        extractor_args['youtubepot-bgutilhttp'] = {'base_url': [pot_provider_url]}
    if extractor_args:
        opts['extractor_args'] = extractor_args

    js_runtime = os.getenv('YTDLP_JS_RUNTIME', '').strip()
    if js_runtime:
        opts['js_runtimes'] = {js_runtime: {}}

    cookies_from_browser = os.getenv('COOKIES_FROM_BROWSER')
    if use_cookies and COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    elif use_cookies and cookies_from_browser:
        parts = [part.strip() for part in cookies_from_browser.split(':') if part.strip()]
        if parts:
            opts['cookiesfrombrowser'] = tuple(parts)
    return opts
