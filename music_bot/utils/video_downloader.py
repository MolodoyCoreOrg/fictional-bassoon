import yt_dlp
import os
import re
import aiohttp
import html
import json
import logging
import asyncio
import subprocess
import shutil
import uuid
from typing import Dict, Optional
from urllib.parse import quote, urlparse
from utils.config import (
    COOKIES_FILE,
    FFMPEG_EXECUTABLE,
    FFMPEG_LOCATION,
    TELEGRAM_MAX_UPLOAD_MB,
    get_anti_block_opts,
    has_ffmpeg,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Максимальный размер файла для отправки в Telegram.
# По умолчанию рассчитано на локальный Bot API/увеличенные лимиты; для обычного
# Bot API можно задать TELEGRAM_MAX_UPLOAD_MB=50, чтобы не показывать слишком
# большие варианты как доступные для отправки.
MAX_FILE_SIZE_BYTES = TELEGRAM_MAX_UPLOAD_MB * 1024 * 1024
# yt-dlp often reports an approximate bitrate rather than a byte-accurate size.
# Keep a margin for MP4 container overhead and provider metadata inaccuracies.
FORMAT_SIZE_SAFETY_FACTOR = 1.10

# Поддерживаемые платформы
SUPPORTED_PLATFORMS = [
    'youtube',
    'instagram',
    'rutube',
    'vk',
    'pinterest',
    'tiktok',
    'twitter',
    'facebook'
]

STANDARD_QUALITY_LABELS = {
    2160: "4K",
    1440: "2K",
    1080: "1080p",
    720: "720p",
    480: "480p",
    360: "360p",
    240: "240p",
    144: "144p",
}


def format_date(date_str: str) -> str:
    """Форматирует дату из YYYYMMDD в DD.MM.YYYY"""
    if not date_str or len(date_str) != 8:
        return "Неизвестно"
    return f"{date_str[6:8]}.{date_str[4:6]}.{date_str[0:4]}"


def format_duration(seconds: int) -> str:
    """Форматирует секунды в H:MM:SS или M:SS"""
    if not seconds:
        return "0:00"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _quality_axis(width: Optional[int], height: Optional[int]) -> Optional[int]:
    """
    Returns the user-facing quality axis.

    Landscape, portrait and square media all use the shorter frame edge, so a
    1080x1920 Reel is correctly shown as 1080p instead of the misleading 1920p.
    """
    dimensions = [
        value
        for value in (_as_positive_int(width), _as_positive_int(height))
        if value
    ]
    return min(dimensions) if dimensions else None


def quality_label(
    width: Optional[int],
    height: Optional[int] = None,
) -> str:
    """Builds a label from real frame dimensions without inventing a tier."""
    # Keep compatibility with callers that historically passed only height.
    if height is None:
        height = width
        width = None

    quality = _quality_axis(width, height)
    if not quality:
        return "Лучшее качество"
    if quality in STANDARD_QUALITY_LABELS:
        return STANDARD_QUALITY_LABELS[quality]

    actual_width = _as_positive_int(width)
    actual_height = _as_positive_int(height)
    if actual_width and actual_height:
        return f"{actual_width}×{actual_height}"
    return f"{quality}p"


def _has_audio(fmt: Dict) -> bool:
    return bool(fmt.get('acodec') and fmt.get('acodec') != 'none')


def _has_video(fmt: Dict) -> bool:
    video_codec = str(fmt.get('vcodec') or '').lower()
    return bool(
        video_codec not in {'', 'none', 'images'}
        and (fmt.get('height') or fmt.get('width'))
    )


def _build_download_format(
    width: Optional[int],
    height: Optional[int] = None,
) -> str:
    """Returns a short callback-safe marker for exact source dimensions."""
    actual_width = _as_positive_int(width)
    actual_height = _as_positive_int(height)
    if actual_width and actual_height:
        return f"r{actual_width}x{actual_height}"
    if actual_height:
        return f"h{actual_height}"
    if actual_width:
        # Compatibility for the former single-height argument.
        return f"h{actual_width}"
    return "best"


def _resolve_download_format(format_id: str, prefer_hls: bool = False) -> str:
    """
    Converts a callback marker into a yt-dlp selector.

    Resolution markers retain both source dimensions. This is essential for
    portrait media: the 1080p option for a 1080x1920 Reel must allow 1920px
    height while still preventing yt-dlp from silently selecting a larger tier.
    """
    resolution_match = re.fullmatch(r"r(\d+)x(\d+)", format_id or "")
    legacy_height_match = re.fullmatch(r"h(\d+)", format_id or "")
    if not resolution_match and not legacy_height_match:
        return format_id

    if resolution_match:
        width = int(resolution_match.group(1))
        height = int(resolution_match.group(2))
        limits = f"[width<={width}][height<={height}]"
    else:
        height = int(legacy_height_match.group(1))
        limits = f"[height<={height}]"

    if prefer_hls:
        if has_ffmpeg():
            return (
                f"bestvideo{limits}[protocol^=m3u8]+bestaudio[protocol^=m3u8]/"
                f"best{limits}[protocol^=m3u8][vcodec!=none][acodec!=none]"
            )
        return (
            f"best{limits}[protocol^=m3u8][vcodec!=none][acodec!=none]"
        )

    if has_ffmpeg():
        return (
            f"bestvideo{limits}[ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo{limits}+bestaudio/"
            f"best{limits}[vcodec!=none][acodec!=none]/"
            f"best{limits}"
        )
    return (
        f"best{limits}[vcodec!=none][acodec!=none]/best{limits}"
    )


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == 'youtu.be' or host == 'youtube.com' or host.endswith('.youtube.com')


def _is_pinterest_url(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return (
        host == "pin.it"
        or host == "pinterest.com"
        or host.endswith(".pinterest.com")
    )


def _decode_embedded_json(raw_script: str):
    """Extracts a JSON value from a script tag, including assignment wrappers."""
    decoder = json.JSONDecoder()
    raw_values = [raw_script.strip()]
    unescaped = html.unescape(raw_script).strip()
    if unescaped and unescaped != raw_values[0]:
        raw_values.append(unescaped)

    for raw_value in raw_values:
        if not raw_value:
            continue

        starts = [0]
        starts.extend(
            match.start()
            for match in re.finditer(r"[\{\[]", raw_value)
        )
        for start in dict.fromkeys(starts):
            try:
                value, _ = decoder.raw_decode(raw_value[start:])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, (dict, list)):
                return value
    return None


def _extract_pinterest_json_documents(page_html: str) -> list:
    documents = []
    for match in re.finditer(
        r"<script\b[^>]*>(.*?)</script\s*>",
        page_html or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        value = _decode_embedded_json(match.group(1))
        if value is not None:
            documents.append(value)
    return documents


def _as_positive_int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _format_dimensions(fmt: Dict) -> tuple[Optional[int], Optional[int]]:
    """Reads real width/height values reported by a platform."""
    width = _as_positive_int(fmt.get('width'))
    height = _as_positive_int(fmt.get('height'))
    if width and height:
        return width, height

    resolution = str(fmt.get('resolution') or '')
    match = re.fullmatch(r"\s*(\d+)\s*[x×]\s*(\d+)\s*", resolution)
    if match:
        width = width or int(match.group(1))
        height = height or int(match.group(2))
    return width, height


def _fits_dimensions(
    fmt: Dict,
    max_width: Optional[int],
    max_height: Optional[int],
) -> bool:
    width, height = _format_dimensions(fmt)
    if not width and not height:
        return False
    if max_width and width and width > max_width:
        return False
    if max_height and height and height > max_height:
        return False
    return True


def _as_positive_float(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _estimated_format_size(fmt: Dict, duration: Optional[float]) -> Optional[int]:
    """Returns the best available byte estimate for a single yt-dlp format."""
    for key in ('filesize', 'filesize_approx'):
        exact_or_approx = _as_positive_int(fmt.get(key))
        if exact_or_approx:
            return exact_or_approx

    duration_seconds = _as_positive_float(duration)
    if not duration_seconds:
        return None

    total_bitrate = _as_positive_float(fmt.get('tbr'))
    if not total_bitrate:
        bitrate_parts = [
            bitrate
            for bitrate in (
                _as_positive_float(fmt.get('vbr')),
                _as_positive_float(fmt.get('abr')),
            )
            if bitrate
        ]
        total_bitrate = sum(bitrate_parts) if bitrate_parts else None
    if not total_bitrate:
        return None

    return int(
        duration_seconds
        * total_bitrate
        * 1000
        / 8
        * FORMAT_SIZE_SAFETY_FACTOR
    )


def _maximum_candidate_size(
    formats: list[Dict],
    duration: Optional[float],
) -> Optional[int]:
    """Returns a conservative bound, or None if any candidate is unknown."""
    if not formats:
        return None
    estimates = [
        _estimated_format_size(fmt, duration)
        for fmt in formats
    ]
    if any(estimate is None for estimate in estimates):
        return None
    return max(estimates)


def _estimate_choice_size(
    formats: list[Dict],
    max_width: int,
    max_height: int,
    duration: Optional[float],
) -> Optional[int]:
    """
    Estimates the file selected by the same video+audio/muxed fallbacks used by
    _resolve_download_format. Unknown sizes are deliberately not treated as
    safe: a quality button is shown only when it can be kept under the limit.
    """
    matching_video = [
        fmt
        for fmt in formats
        if _has_video(fmt) and _fits_dimensions(fmt, max_width, max_height)
    ]
    if not matching_video:
        return None

    # Mirror selector order precisely. If any separate video stream exists,
    # yt-dlp tries bestvideo+bestaudio before the muxed fallback, even when the
    # best separate video happens to be below the button's upper resolution.
    video_only = [fmt for fmt in matching_video if not _has_audio(fmt)]
    muxed = [fmt for fmt in matching_video if _has_audio(fmt)]
    audio_only = [
        fmt
        for fmt in formats
        if _has_audio(fmt) and not _has_video(fmt)
    ]

    if has_ffmpeg() and video_only and audio_only:
        video_size = _maximum_candidate_size(video_only, duration)
        audio_size = _maximum_candidate_size(audio_only, duration)
        if video_size is not None and audio_size is not None:
            return int((video_size + audio_size) * FORMAT_SIZE_SAFETY_FACTOR)
        return None

    muxed_size = _maximum_candidate_size(muxed, duration)
    if muxed_size is not None:
        return int(muxed_size * FORMAT_SIZE_SAFETY_FACTOR)
    return None


def _format_size_label(filesize: int) -> str:
    size_mb = filesize / (1024 * 1024)
    if size_mb >= 1024:
        return f"{size_mb / 1024:.1f} ГБ"
    return f"{size_mb:.1f} МБ"


def _build_format_choices(info: Dict, url: str) -> list[Dict]:
    """
    Builds buttons from real variants that have a defensible size estimate and
    fit the configured Telegram upload limit.

    This prevents a quality from being offered when its selected video/audio
    streams would exceed TELEGRAM_MAX_UPLOAD_MB. The final downloaded-size check
    remains as a last line of defence against providers changing the stream.
    """
    formats = info.get('formats') or []
    video_formats = [fmt for fmt in formats if _has_video(fmt)]
    variants_by_quality = {}

    for fmt in video_formats:
        width, height = _format_dimensions(fmt)
        if not width or not height:
            continue
        quality = _quality_axis(width, height)
        pixels = width * height
        current = variants_by_quality.get(quality)
        current_pixels = current[0] * current[1] if current else 0
        if not current or pixels > current_pixels:
            variants_by_quality[quality] = (width, height)

    if not variants_by_quality:
        width, height = _format_dimensions(info)
        quality = _quality_axis(width, height)
        if width and height and quality:
            variants_by_quality[quality] = (width, height)

    duration = info.get('duration')
    formats_list = []
    variants = sorted(
        variants_by_quality.items(),
        key=lambda item: (
            item[0],
            item[1][0] * item[1][1],
        ),
        reverse=True,
    )

    for _, (width, height) in variants:
        estimated_size = _estimate_choice_size(
            formats,
            width,
            height,
            duration,
        )
        if estimated_size is None or estimated_size > MAX_FILE_SIZE_BYTES:
            continue

        matching_formats = [
            fmt
            for fmt in video_formats
            if _fits_dimensions(fmt, width, height)
        ]
        dimensions = f"{width}x{height}"
        formats_list.append({
            'format_id': _build_download_format(width, height),
            'height': height,
            'width': width,
            'ext': 'mp4',
            'quality_label': quality_label(width, height),
            'size_str': _format_size_label(estimated_size),
            'filesize': estimated_size,
            'too_large': False,
            'has_audio': any(_has_audio(fmt) for fmt in matching_formats),
            'url': url,
            'format_note': f'Best up to actual source {dimensions}'
        })

    return formats_list


def _video_dimensions_from_info(
    info: Dict,
) -> tuple[Optional[int], Optional[int]]:
    """Returns dimensions of the video stream yt-dlp actually selected."""
    for key in ('requested_formats', 'requested_downloads'):
        for fmt in info.get(key) or []:
            if fmt.get('vcodec') == 'none':
                continue
            width, height = _format_dimensions(fmt)
            if width or height:
                return width, height
    return _format_dimensions(info)


def _pinterest_video_formats(documents: list) -> list[Dict]:
    """Finds both ordinary Pin and Idea Pin/Story Pin video variants."""
    formats_by_url = {}

    def visit(value, path=()):
        if isinstance(value, dict):
            raw_url = value.get("url") or value.get("src")
            if isinstance(raw_url, str):
                media_url = html.unescape(raw_url).replace(r"\/", "/")
                parsed = urlparse(media_url)
                path_lower = parsed.path.lower()
                context = "/".join(str(part).lower() for part in path)
                mime_type = str(
                    value.get("mime_type")
                    or value.get("content_type")
                    or value.get("mimeType")
                    or ""
                ).lower()
                extension = os.path.splitext(path_lower)[1]
                image_extension = extension in {
                    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"
                }
                is_video = (
                    mime_type.startswith("video/")
                    or extension in {".mp4", ".m4v", ".mov", ".m3u8"}
                    or "/videos/" in path_lower
                    or "video_list" in context
                )

                if (
                    parsed.scheme in {"http", "https"}
                    and is_video
                    and not image_extension
                    and media_url not in formats_by_url
                ):
                    label = str(path[-1] if path else value.get("format") or "video")
                    height = _as_positive_int(
                        value.get("height") or value.get("original_height")
                    )
                    width = _as_positive_int(
                        value.get("width") or value.get("original_width")
                    )
                    if not height:
                        height_match = re.search(r"(\d{3,4})p", label, re.IGNORECASE)
                        if height_match:
                            height = int(height_match.group(1))

                    is_hls = extension == ".m3u8" or "hls" in label.lower()
                    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-")
                    fmt = {
                        "format_id": f"pinterest-{safe_label or len(formats_by_url) + 1}",
                        "url": media_url,
                        "ext": "mp4",
                        "protocol": "m3u8_native" if is_hls else "https",
                        "vcodec": "h264",
                        "acodec": "aac",
                        "http_headers": {
                            "Referer": "https://www.pinterest.com/",
                        },
                    }
                    if height:
                        fmt["height"] = height
                    if width:
                        fmt["width"] = width

                    filesize = _as_positive_int(
                        value.get("filesize")
                        or value.get("file_size")
                        or value.get("size")
                    )
                    if filesize:
                        fmt["filesize"] = filesize
                    formats_by_url[media_url] = fmt

            for key, child in value.items():
                visit(child, path + (key,))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + (str(index),))

    for document in documents:
        visit(document)

    return sorted(
        formats_by_url.values(),
        key=lambda fmt: (
            fmt.get("height") or 0,
            fmt.get("protocol") != "m3u8_native",
        ),
        reverse=True,
    )


def _find_json_text(documents: list, keys: tuple[str, ...]) -> Optional[str]:
    def find(value, wanted_key: str):
        if isinstance(value, dict):
            candidate = value.get(wanted_key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
            for child in value.values():
                found = find(child, wanted_key)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find(child, wanted_key)
                if found:
                    return found
        return None

    for key in keys:
        for document in documents:
            found = find(document, key)
            if found:
                return found
    return None


def _read_ydl_url(ydl: yt_dlp.YoutubeDL, url: str) -> tuple[str, str]:
    response = ydl.urlopen(url)
    try:
        raw_body = response.read()
        charset = None
        headers = getattr(response, "headers", None)
        if headers and hasattr(headers, "get_content_charset"):
            charset = headers.get_content_charset()
        page_text = raw_body.decode(charset or "utf-8", errors="replace")
        resolved_url = getattr(response, "url", None)
        if not resolved_url and hasattr(response, "geturl"):
            resolved_url = response.geturl()
        return page_text, str(resolved_url or url)
    finally:
        response.close()


def _pinterest_pin_id(url: str) -> Optional[str]:
    match = re.search(r"/pin/(?:[^/?#]+--)?(\d+)", url or "")
    return match.group(1) if match else None


def _extract_pinterest_fallback_info(
    url: str,
    ydl: yt_dlp.YoutubeDL,
) -> Dict:
    """
    Extracts Pinterest Story/Idea Pin videos that the stock extractor can
    currently identify as a pin but sometimes exposes without formats.
    """
    page_html, resolved_url = _read_ydl_url(ydl, url)
    documents = _extract_pinterest_json_documents(page_html)
    pin_id = _pinterest_pin_id(resolved_url) or _pinterest_pin_id(url)

    formats = _pinterest_video_formats(documents)
    if not formats and pin_id:
        api_data = quote(
            json.dumps(
                {
                    "options": {
                        "id": pin_id,
                        "field_set_key": "auth_web_main_pin",
                        "noCache": True,
                    },
                    "context": {},
                },
                separators=(",", ":"),
            )
        )
        api_url = (
            "https://www.pinterest.com/resource/PinResource/get/"
            f"?source_url=/pin/{pin_id}/&data={api_data}"
        )
        try:
            api_text, _ = _read_ydl_url(ydl, api_url)
            api_document = json.loads(api_text)
        except Exception as error:
            logger.warning("Pinterest PinResource fallback failed: %s", error)
        else:
            documents.append(api_document)
            formats = _pinterest_video_formats(documents)

    if not formats:
        raise RuntimeError(
            "Pinterest pin does not expose downloadable video formats"
        )

    fallback_id = pin_id or uuid.uuid5(
        uuid.NAMESPACE_URL,
        resolved_url,
    ).hex[:16]
    title = _find_json_text(
        documents,
        ("grid_title", "title", "closeup_description", "description"),
    ) or f"Pinterest {fallback_id}"
    thumbnail = _find_json_text(
        documents,
        ("image_large_url", "thumbnail_url"),
    )
    uploader = _find_json_text(
        documents,
        ("full_name", "username"),
    )

    known_heights = [fmt["height"] for fmt in formats if fmt.get("height")]
    known_widths = [fmt["width"] for fmt in formats if fmt.get("width")]
    return {
        "_type": "video",
        "id": fallback_id,
        "title": title,
        "uploader": uploader or "Pinterest",
        "webpage_url": resolved_url,
        "original_url": url,
        "thumbnail": thumbnail,
        "height": max(known_heights, default=720),
        "width": max(known_widths, default=0) or None,
        "formats": formats,
        "extractor": "Pinterest fallback",
        "extractor_key": "PinterestFallback",
    }


def _extract_info_with_pinterest_fallback(
    ydl: yt_dlp.YoutubeDL,
    url: str,
    download: bool,
) -> Dict:
    try:
        return ydl.extract_info(url, download=download)
    except Exception as error:
        lower = str(error).lower()
        if (
            not _is_pinterest_url(url)
            or "no video formats found" not in lower
        ):
            raise

        logger.warning(
            "yt-dlp returned no Pinterest formats; trying embedded pin data"
        )
        info = _extract_pinterest_fallback_info(url, ydl)
        if download:
            return ydl.process_ie_result(info, download=True)
        return info


def _merge_extractor_args(base: Dict | None, extra: Dict | None) -> Dict:
    merged = {
        extractor: dict(arguments)
        for extractor, arguments in (base or {}).items()
    }
    for extractor, arguments in (extra or {}).items():
        merged.setdefault(extractor, {}).update(arguments)
    return merged


def _youtube_video_download_profiles(url: str) -> list[Dict]:
    """Returns current, isolated YouTube playback profiles for a fresh retry."""
    profiles = [{'_label': 'default'}]
    if not _is_youtube_url(url):
        return profiles

    # Do not globally pin a player client. Current yt-dlp releases deliberately
    # rotate their defaults as YouTube changes enforcement. A provider-backed
    # mweb request is still valuable, but only as an isolated retry.
    if os.getenv('YOUTUBE_POT_PROVIDER_URL', '').strip():
        profiles.append({
            '_label': 'mweb-po-token',
            'extractor_args': {
                'youtube': {
                    'player_client': ['mweb'],
                    # Ask the provider even during partial/rolling enforcement.
                    'fetch_pot': ['always'],
                },
            },
        })

    profiles.extend([
        {
            '_label': 'visionos-cookie-free',
            '_use_cookies': False,
            'extractor_args': {
                'youtube': {'player_client': ['visionos']},
            },
        },
        {
            '_label': 'web_safari-hls',
            '_prefer_hls': True,
            'extractor_args': {
                'youtube': {'player_client': ['web_safari']},
            },
        },
        {
            '_label': 'web_embedded-cookie-free',
            '_use_cookies': False,
            'extractor_args': {
                'youtube': {'player_client': ['web_embedded']},
            },
        },
    ])
    return profiles


def _build_video_download_opts(
    temp_dir: str,
    format_id: str,
    raw_profile: Dict | None = None,
) -> Dict:
    profile = dict(raw_profile or {})
    profile.pop('_label', None)
    use_cookies = profile.pop('_use_cookies', True)
    prefer_hls = profile.pop('_prefer_hls', False)

    opts = {
        **get_anti_block_opts(use_cookies=use_cookies),
        'format': _resolve_download_format(format_id, prefer_hls=prefer_hls),
        'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
        'writethumbnail': True,
        'thumbnail_format': 'jpg',
        'noplaylist': True,
        'socket_timeout': 20,
        'continuedl': False,
        'overwrites': True,
        # Smaller ranges avoid YouTube CDN 403 responses on long media and
        # make each retry obtain fresh signed playback URLs.
        'http_chunk_size': 8 * 1024 * 1024,
    }

    extractor_args = profile.pop('extractor_args', None)
    if extractor_args:
        opts['extractor_args'] = _merge_extractor_args(
            opts.get('extractor_args'),
            extractor_args,
        )
    opts.update(profile)

    if has_ffmpeg():
        opts.update({
            'merge_output_format': 'mp4',
            'postprocessors': [
                {
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }
            ]
        })
    if FFMPEG_LOCATION:
        opts['ffmpeg_location'] = FFMPEG_LOCATION
    return opts


def _extract_video_info_sync(url: str, ydl_opts: Dict) -> Dict:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return _extract_info_with_pinterest_fallback(
            ydl,
            url,
            download=True,
        )


async def _download_video_info(url: str, temp_dir: str, format_id: str) -> Dict:
    last_error = None
    profiles = _youtube_video_download_profiles(url)
    for attempt, profile in enumerate(profiles, start=1):
        label = profile.get('_label', str(attempt))
        if attempt > 1:
            logger.info("Retrying YouTube video download with profile %s", label)
        try:
            opts = _build_video_download_opts(temp_dir, format_id, profile)
            return await asyncio.to_thread(_extract_video_info_sync, url, opts)
        except Exception as error:
            last_error = error
            if not _is_youtube_url(url):
                raise
            logger.warning("YouTube video profile %s failed: %s", label, error)

    if last_error:
        raise last_error
    raise RuntimeError("yt-dlp did not return video information")


def _human_error(error: Exception, url: str | None = None) -> str:
    error_str = str(error)
    lower = error_str.lower()
    if "ffprobe and ffmpeg not found" in lower or "ffmpeg not found" in lower or "ffmpeg is not installed" in lower:
        return "В системе не найден FFmpeg. Установите ffmpeg или задайте FFMPEG_LOCATION в .env. Для Docker пересоберите образ; в requirements также добавлен imageio-ffmpeg как запасной вариант."
    if "instagram sent an empty media response" in lower or "without being logged-in" in lower:
        return "Instagram не отдал медиа без авторизации. Проверьте, что пост публичный; для приватных/ограниченных постов добавьте cookies.txt и укажите COOKIES_FILE в .env или настройте COOKIES_FROM_BROWSER."
    is_forbidden = (
        "http error 403" in lower
        or "403: forbidden" in lower
        or "unable to download video data" in lower
    )
    if is_forbidden and _is_youtube_url(url or ""):
        if os.getenv('YOUTUBE_POT_PROVIDER_URL', '').strip():
            return (
                "YouTube отклонил все актуальные профили загрузки (HTTP 403), "
                "включая PO-token и HLS. Проверьте /ping и логи настроенного "
                "PO-token provider, пересоберите образ с актуальным yt-dlp и при "
                "необходимости обновите Netscape cookies.txt через COOKIES_FILE."
            )
        return (
            "YouTube отклонил все актуальные профили загрузки (HTTP 403). "
            "Запустите проект через docker compose с PO-token provider либо "
            "задайте YOUTUBE_POT_PROVIDER_URL; при необходимости добавьте свежий "
            "Netscape cookies.txt через COOKIES_FILE."
        )
    if "sign in to confirm" in lower or "not a bot" in lower:
        if COOKIES_FILE or os.getenv("COOKIES_FROM_BROWSER"):
            return (
                "YouTube отклонил текущую авторизацию. Обновите cookies из отдельного "
                "приватного окна. Для серверного IP запустите динамический PO-token "
                "provider и задайте YOUTUBE_POT_PROVIDER_URL."
            )
        return (
            "YouTube заблокировал неавторизованный IP бота. Добавьте свежий Netscape "
            "cookies.txt через COOKIES_FILE. Для серверного IP также запустите "
            "динамический PO-token provider и задайте YOUTUBE_POT_PROVIDER_URL."
        )
    if _is_pinterest_url(url or "") and (
        "no video formats found" in lower
        or "does not expose downloadable video formats" in lower
    ):
        return (
            "Pinterest не отдал видеофайл для этого пина. Убедитесь, что пин "
            "публичный и содержит видео, а не только изображение."
        )
    if "unsupported url" in lower:
        return "Площадка или формат ссылки не поддержаны текущей версией yt-dlp. Обновите yt-dlp и проверьте ссылку."
    return error_str


def get_video_formats(url: str) -> Dict:
    """Получает доступные разрешения видео для кнопок выбора качества."""
    result = {
        'success': False,
        'formats': [],
        'title': None,
        'duration': None,
        'thumbnail': None,
        'error': None
    }

    try:
        ydl_opts = {
            **get_anti_block_opts(),
            'extract_flat': False,
            'skip_download': True,
        }
        if FFMPEG_LOCATION:
            ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = _extract_info_with_pinterest_fallback(
                ydl,
                url,
                download=False,
            )

            result['title'] = info.get('title', 'Неизвестно')
            result['duration'] = info.get('duration', 0)

            thumbnail = info.get('thumbnail')
            if not thumbnail and info.get('thumbnails'):
                thumbnails = info.get('thumbnails', [])
                if thumbnails:
                    thumbnail = thumbnails[-1].get('url')
            result['thumbnail'] = thumbnail

            result['formats'] = _build_format_choices(info, url)
            if not result['formats']:
                result['error'] = (
                    "Нет доступного качества, которое можно гарантированно отправить "
                    f"в пределах лимита Telegram {TELEGRAM_MAX_UPLOAD_MB} МБ. "
                    "Для этого видео сервис не сообщил достаточно данных о размере "
                    "либо даже минимальное качество превышает лимит."
                )
                return result
            result['success'] = True

    except Exception as e:
        logger.error(f"Error getting video formats: {e}")
        result['error'] = _human_error(e, url)

    return result


async def download_video(url: str, temp_dir: str, format_id: str) -> Dict:
    """
    Скачивает видео с жесткой конвертацией в MP4 (H.264/AAC), чтобы оно идеально воспроизводилось в Telegram без сжатия
    """
    result = {
        'success': False,
        'video_path': None,
        'title': None,
        'thumbnail_path': None,
        'filesize': 0,
        'error': None,
        'author': None,
        'upload_date': None,
        'duration_str': None,
        'quality': None,
        'url': None,
        'width': 0,
        'height': 0
    }
    
    try:
        info = await _download_video_info(url, temp_dir, format_id)
            
        result['title'] = info.get('title', 'Неизвестно')
        result['thumbnail_path'] = os.path.join(temp_dir, f"{info.get('id')}.jpg")
        result['author'] = info.get('uploader', 'Неизвестно')
        raw_date = info.get('upload_date')
        result['upload_date'] = format_date(raw_date) if raw_date else "Неизвестно"
            
        duration = info.get('duration', 0)
        result['duration_str'] = format_duration(duration)
            
        width, height = _video_dimensions_from_info(info)

        result['height'] = height or 720
        result['width'] = width or int(result['height'] * 16 / 9)
        result['quality'] = quality_label(width, height) if (width and height) else "MP4"
        result['url'] = info.get('webpage_url') or url
            
        video_id = info.get('id')
        possible_extensions = ['mp4', 'mkv', 'webm', 'avi', 'mov']
            
        for ext in possible_extensions:
            path = os.path.join(temp_dir, f"{video_id}.{ext}")
            if os.path.exists(path):
                result['video_path'] = path
                result['filesize'] = os.path.getsize(path)
                break
            
        if not result['video_path']:
            for file in os.listdir(temp_dir):
                if file.endswith(('.mp4', '.mkv', '.webm', '.avi', '.mov')) and not file.endswith('.jpg'):
                    result['video_path'] = os.path.join(temp_dir, file)
                    result['filesize'] = os.path.getsize(result['video_path'])
                    break

        if not result['video_path'] or not os.path.exists(result['video_path']):
            result['error'] = "Видео было скачано, но итоговый файл не найден."
            return result

        if result['filesize'] > MAX_FILE_SIZE_BYTES:
            max_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
            size_mb = result['filesize'] / (1024 * 1024)
            result['error'] = (
                f"Файл получился слишком большим для отправки через Telegram: "
                f"{size_mb:.1f} МБ при лимите {max_mb:.0f} МБ. "
                "Выберите качество ниже или увеличьте TELEGRAM_MAX_UPLOAD_MB при использовании локального Bot API."
            )
            return result

        result['success'] = True
            
    except Exception as e:
        logger.error(f"Download error: {e}")
        result['error'] = _human_error(e, url)
    
    return result


async def download_audio_from_video(url: str, temp_dir: str, output_format: str = 'mp3') -> Dict:
    """
    Извлекает аудио из видео по ссылке с поддержкой выбора формата:
    - 'mp3': обычный музыкальный файл
    - 'voice': голосовое сообщение для Telegram (кодек OPUS в контейнере OGG)
    """
    result = {
        'success': False,
        'audio_path': None,
        'title': None,
        'artist': None,
        'thumbnail_path': None,
        'error': None
    }
    
    try:
        ydl_opts = {
            **get_anti_block_opts(),
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
        }
        if FFMPEG_LOCATION:
            ydl_opts['ffmpeg_location'] = FFMPEG_LOCATION
            
        if output_format == 'voice':
            ydl_opts['postprocessors'] = [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'opus',
                }
            ]
        else:
            ydl_opts['postprocessors'] = [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '320',
                },
                {
                    'key': 'FFmpegThumbnailsConvertor',
                    'format': 'jpg',
                }
            ]
            ydl_opts['writethumbnail'] = True
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            raw_title = info.get('title', 'Неизвестно')
            raw_artist = info.get('artist')
            uploader = info.get('uploader') or 'Неизвестно'
            
            if not raw_artist or raw_artist == uploader:
                for sep in [" - ", " — ", " ~ ", " – "]:
                    if sep in raw_title:
                        parts = raw_title.split(sep, 1)
                        raw_artist = parts[0].strip()
                        raw_title = parts[1].strip()
                        break
                if not raw_artist:
                    raw_artist = uploader
            else:
                for sep in [" - ", " — ", " – "]:
                    if sep in raw_title and raw_title.lower().startswith(raw_artist.lower() + sep.strip()):
                        raw_title = raw_title.split(sep, 1)[1].strip()
                        break

            clean_title = re.sub(r'\s*[\(\[]\s*(Official|Music|Lyric|Video|Audio|HD|4K|HQ|Visualizer|Live|Prod\..*?|with lyrics).*?[\)\]]', '', raw_title, flags=re.IGNORECASE).strip()
            if not clean_title:
                clean_title = raw_title

            result['title'] = clean_title
            result['artist'] = raw_artist
            
            audio_id = info.get('id', 'audio')
            
            if output_format == 'voice':
                valid_exts = ('.opus', '.ogg', '.m4a', '.mp3', '.wav')
            else:
                valid_exts = ('.mp3',)
            
            for ext in valid_exts:
                path = os.path.join(temp_dir, f"{audio_id}{ext}")
                if os.path.exists(path):
                    result['audio_path'] = path
                    break
            
            if not result['audio_path']:
                for file in os.listdir(temp_dir):
                    if file.lower().endswith(valid_exts) and not file.lower().endswith(('.jpg', '.png', '.webp', '.jpeg')):
                        result['audio_path'] = os.path.join(temp_dir, file)
                        break

            if output_format == 'voice' and result['audio_path'] and result['audio_path'].endswith('.opus'):
                ogg_path = result['audio_path'][:-5] + '.ogg'
                try:
                    os.rename(result['audio_path'], ogg_path)
                    result['audio_path'] = ogg_path
                except Exception as e:
                    logger.warning(f"Не удалось переименовать .opus в .ogg: {e}")

            if output_format == 'mp3':
                jpg_path = os.path.join(temp_dir, f"{audio_id}.jpg")
                if os.path.exists(jpg_path):
                    result['thumbnail_path'] = jpg_path
                else:
                    for file in os.listdir(temp_dir):
                        if file.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')) and not file.endswith('.mp3'):
                            result['thumbnail_path'] = os.path.join(temp_dir, file)
                            break

                if not result['thumbnail_path']:
                    thumb_url = info.get('thumbnail')
                    if not thumb_url and info.get('thumbnails'):
                        thumbs = info.get('thumbnails', [])
                        if thumbs:
                            thumb_url = thumbs[-1].get('url')
                    
                    if thumb_url:
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.get(thumb_url) as resp:
                                    if resp.status == 200:
                                        cover_path = os.path.join(temp_dir, "cover.jpg")
                                        with open(cover_path, "wb") as f:
                                            f.write(await resp.read())
                                        result['thumbnail_path'] = cover_path
                        except Exception as e:
                            logger.warning(f"Не удалось скачать обложку: {e}")

            if result['audio_path'] and os.path.exists(result['audio_path']):
                result['success'] = True
            else:
                result['error'] = "Файл аудио не был создан."
            
    except Exception as e:
        logger.error(f"Audio extraction error: {e}")
        result['error'] = _human_error(e, url)
    
    return result


async def extract_audio_from_local_video(video_path: str, temp_dir: str, output_format: str = 'mp3', title: str = "Аудио из видео", artist: str = "GG_Loader") -> Dict:
    """
    Извлекает аудио напрямую из локально загруженного видеофайла с помощью FFmpeg
    """
    result = {
        'success': False,
        'audio_path': None,
        'title': title or "Аудио из видео",
        'artist': artist or "GG_Loader",
        'thumbnail_path': None,
        'error': None
    }
    
    try:
        if not os.path.exists(video_path):
            result['error'] = "Локальный видеофайл не найден на сервере."
            return result

        ffmpeg_exe = FFMPEG_EXECUTABLE or shutil.which("ffmpeg")
        if not ffmpeg_exe:
            result['error'] = "В системе не найден FFmpeg. Установите пакет ffmpeg или укажите в .env FFMPEG_LOCATION на файл ffmpeg/папку с ffmpeg."
            return result

        file_id = str(uuid.uuid4())[:8]
        
        if output_format == 'voice':
            output_path = os.path.join(temp_dir, f"voice_{file_id}.ogg")
            cmd = [ffmpeg_exe, '-y', '-i', video_path, '-vn', '-c:a', 'libopus', '-b:a', '64k', output_path]
        else:
            output_path = os.path.join(temp_dir, f"audio_{file_id}.mp3")
            cmd = [ffmpeg_exe, '-y', '-i', video_path, '-vn', '-ar', '44100', '-ac', '2', '-b:a', '320k', output_path]

        def run_ffmpeg():
            return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        process = await asyncio.to_thread(run_ffmpeg)
        
        if process.returncode == 0 and os.path.exists(output_path):
            result['audio_path'] = output_path
            result['success'] = True
        else:
            logger.error(f"FFmpeg stderr: {process.stderr}")
            stderr_tail = (process.stderr or "").strip()[-1000:]
            result['error'] = f"FFmpeg не смог извлечь аудио из видео. Подробности: {stderr_tail}"
            
    except Exception as e:
        logger.error(f"Local video audio extraction error: {e}")
        error_str = str(e)
        if "No such file or directory: 'ffmpeg'" in error_str or "not found" in error_str or "WinError 2" in error_str:
            result['error'] = "В системе не найден FFmpeg. Установите пакет ffmpeg или укажите в .env FFMPEG_LOCATION на файл ffmpeg/папку с ffmpeg."
        else:
            result['error'] = str(e)
            
    return result


def detect_platform(url: str) -> Optional[str]:
    """
    Определяет платформу по URL
    """
    url_lower = url.lower()
    
    platform_map = {
        'youtube.com': 'YouTube',
        'youtu.be': 'YouTube',
        'instagram.com': 'Instagram',
        'rutube.ru': 'RuTube',
        'vk.com/video': 'VK Video',
        'vk.ru/video': 'VK Video',
        'pinterest.com': 'Pinterest',
        'pin.it': 'Pinterest',
        'tiktok.com': 'TikTok',
        'twitter.com': 'Twitter',
        'x.com': 'Twitter',
        'facebook.com': 'Facebook',
        'fb.watch': 'Facebook',
        'soundcloud.com': 'SoundCloud',
        'music.yandex': 'Yandex Music',
        'spotify.com': 'Spotify'
    }
    
    for domain, platform in platform_map.items():
        if domain in url_lower:
            return platform
    
    return None
