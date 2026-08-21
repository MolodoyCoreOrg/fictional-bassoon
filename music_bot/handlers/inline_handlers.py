from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile,
)
from utils.music_downloader import search_music, download_from_url, find_track_album
from utils.audio_processor import add_cover_to_mp3, cleanup_temp_files
from utils.album_cache import cache_album
from utils.keyboard import get_inline_album_keyboard
import hashlib
import os
import uuid
import aiohttp
import logging
import html
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

# Telegram ограничивает callback_data 64 байтами. Поэтому в кнопку кладём только короткий ключ,
# а полные данные трека временно храним в памяти процесса.
INLINE_TRACK_CACHE = {}
MAX_INLINE_CACHE_SIZE = 500


def _cache_inline_track(track: dict) -> str:
    cache_key = hashlib.sha256(f"{track.get('url', '')}|{track.get('title', '')}".encode()).hexdigest()[:24]
    INLINE_TRACK_CACHE[cache_key] = track
    if len(INLINE_TRACK_CACHE) > MAX_INLINE_CACHE_SIZE:
        oldest_key = next(iter(INLINE_TRACK_CACHE))
        INLINE_TRACK_CACHE.pop(oldest_key, None)
    return cache_key


@router.inline_query()
async def inline_search(inline_query: InlineQuery):
    """Показывает быстрый список треков с обложками в inline-режиме."""
    query = inline_query.query.strip()

    if not query:
        results = [
            InlineQueryResultArticle(
                id="welcome_cloud",
                title="Привет! 💙",
                description="Напиши артиста или название песни, и я найду её.",
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "🎵 <b>Поиск музыки ГУЧИГЕНГОВО</b>\n\n"
                        "Чтобы найти трек, напишите в любом чате:\n"
                        "<code>@GG_Loader_bot название песни</code>"
                    ),
                    parse_mode="HTML",
                ),
                thumbnail_url="https://cdn-icons-png.flaticon.com/512/1163/1163624.png",
            )
        ]
    else:
        logger.info("Inline search query: %s", query)
        try:
            # Inline-ответ должен быть быстрым: здесь получаем только метаданные.
            # Сам файл скачивается после выбора результата.
            search_results = await search_music(query, limit=10)
            logger.info("Found %s results for query: %s", len(search_results), query)
        except Exception as error:
            logger.exception("Inline search failed for %s: %s", query, error)
            search_results = []

        results = []
        for idx, track in enumerate(search_results[:10]):
            source_url = track.get("url") or ""
            if not source_url:
                continue

            cache_key = _cache_inline_track(track)
            duration = track.get("duration")
            if duration:
                minutes, seconds = divmod(int(duration), 60)
                duration_text = f"{minutes}:{seconds:02d}"
            else:
                duration_text = "🎵"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="📥 Загрузить песню",
                    callback_data=f"dl:{cache_key}",
                )
            ]])
            result_id = f"{idx}_{hashlib.md5(source_url.encode()).hexdigest()[:8]}"

            results.append(
                InlineQueryResultArticle(
                    id=result_id,
                    title=track.get("title") or "Неизвестно",
                    description=f"{duration_text} • {track.get('artist') or 'Неизвестно'}",
                    thumbnail_url=track.get("thumbnail")
                    or "https://cdn-icons-png.flaticon.com/512/1384/1384060.png",
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            f"🎵 <b>{html.escape(track.get('title') or 'Неизвестно')}</b>\n"
                            f"👤 {html.escape(track.get('artist') or 'Неизвестно')}\n"
                            f"⏱ {duration_text}\n\n"
                            "Нажмите кнопку ниже, чтобы загрузить песню в чат."
                        ),
                        parse_mode="HTML",
                    ),
                    reply_markup=keyboard,
                )
            )

        if not results:
            results = [
                InlineQueryResultArticle(
                    id="no_results",
                    title="❌ Ничего не найдено",
                    description=f"По запросу «{query}» треков нет. Попробуйте изменить запрос.",
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            f"❌ По запросу <b>«{html.escape(query)}»</b> ничего не найдено.\n"
                            "Попробуйте написать название трека или автора иначе."
                        ),
                        parse_mode="HTML",
                    ),
                    thumbnail_url="https://cdn-icons-png.flaticon.com/512/1384/1384060.png",
                )
            ]

    try:
        await inline_query.answer(
            results,
            cache_time=5,
            is_personal=True,
            switch_pm_text="Открыть личные сообщения 💬",
            switch_pm_parameter="from_inline_search",
        )
    except Exception as error:
        logger.exception("Failed to answer inline query %s: %s", inline_query.id, error)


async def download_thumbnail(thumbnail_url: str, temp_dir: str) -> str:
    """Скачивает обложку по URL"""
    if not thumbnail_url:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(thumbnail_url) as response:
                if response.status == 200:
                    cover_path = os.path.join(temp_dir, "cover.jpg")
                    with open(cover_path, 'wb') as f:
                        f.write(await response.read())
                    return cover_path
    except Exception as e:
        logger.error(f"Error downloading thumbnail: {e}")
    return None


@router.callback_query(F.data.startswith("dl:"))
async def process_inline_download(callback: CallbackQuery):
    """Обработка кнопки скачивания трека из inline-режима."""
    await callback.answer("⏳ Скачиваю трек с обложкой...")

    cache_key = callback.data.split(":", 1)[1]
    track = INLINE_TRACK_CACHE.get(cache_key)
    if not track:
        await callback.message.answer("❌ Данные трека устарели. Выполните inline-поиск заново и нажмите кнопку скачивания ещё раз.")
        return

    title = track.get('title') or 'Неизвестно'
    artist = track.get('artist') or 'Неизвестно'
    url = track.get('url')
    thumbnail_url = track.get('thumbnail') or ''

    if not url:
        await callback.message.answer("❌ Ошибка: ссылка трека не найдена")
        return

    user_temp_dir = os.path.join("/tmp/music_bot", str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)

    try:
        status_msg = await callback.message.answer("🎵 Скачиваю аудио и обложку в лучшем качестве...")
        download_result = await download_from_url(url, user_temp_dir)

        if not download_result['success']:
            await status_msg.edit_text(f"❌ Ошибка при скачивании: {download_result['error']}")
            await cleanup_temp_files(user_temp_dir)
            return

        audio_path = download_result['audio_path']

        cover_path = None
        if thumbnail_url:
            cover_path = await download_thumbnail(thumbnail_url, user_temp_dir)

        if not cover_path and download_result.get('thumbnail_path'):
            cover_path = download_result['thumbnail_path']

        if cover_path and os.path.exists(cover_path):
            processed_path = await add_cover_to_mp3(audio_path, cover_path, title, artist)
        else:
            processed_path = audio_path

        audio_file = FSInputFile(processed_path)
        thumb_file = FSInputFile(cover_path) if cover_path and os.path.exists(cover_path) else None

        current_date = datetime.now().strftime("%d/%m/%Y")
        caption = (
            f"🎵 <b>{html.escape(title)}</b>\n"
            f"👤 {html.escape(artist)}\n"
            f"📅 {current_date}\n\n"
            f"❤️ @GG_Loader_bot"
        )

        reply_markup = None
        album_title = download_result.get("album") or track.get("album")
        album_url = download_result.get("album_url") or track.get("album_url")
        if not (album_title and album_url):
            album_metadata = await find_track_album(title, artist)
            if album_metadata:
                album_title = album_metadata["album"]
                album_url = album_metadata["album_url"]

        if album_title and album_url:
            album_key = cache_album({
                "album": album_title,
                "artist": artist,
                "album_url": album_url,
                "tracks": [],
            })
            reply_markup = get_inline_album_keyboard(album_title, album_key)

        await callback.message.answer_audio(
            audio=audio_file,
            title=title,
            performer=artist,
            caption=caption,
            parse_mode="HTML",
            thumb=thumb_file,
            reply_markup=reply_markup,
        )

        await status_msg.delete()

    except Exception as e:
        logger.error(f"Error in inline download: {e}")
        await callback.message.answer(f"❌ Произошла ошибка: {str(e)}")
    finally:
        await cleanup_temp_files(user_temp_dir)
