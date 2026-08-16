from aiogram import Router, F
from aiogram.types import InlineQuery, InlineQueryResultArticle, InlineQueryResultAudio, InputTextMessageContent, CallbackQuery, FSInputFile
from utils.music_downloader import search_music, download_from_url
from utils.audio_processor import add_cover_to_mp3, cleanup_temp_files
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
    """
    Обработка inline-запросов для поиска музыки.
    Пользователь вводит @GG_Loader_bot название песни.
    """
    query = inline_query.query.strip()

    if not query:
        results = [
            InlineQueryResultArticle(
                id="welcome_cloud",
                title="Привет! 💙",
                description="Просто напиши артиста или название песни, и я найду их!",
                input_message_content=InputTextMessageContent(
                    message_text="🎵 **Поиск музыки ГУЧИГЕНГОВО**\n\nЧтобы найти трек, напишите в любом чате:\n`@GG_Loader_bot название песни`",
                    parse_mode="Markdown"
                ),
                thumbnail_url="https://cdn-icons-png.flaticon.com/512/1163/1163624.png",
            )
        ]
    else:
        logger.info(f"Inline search query: {query}")
        try:
            search_results = await search_music(query, limit=10)
            logger.info(f"Found {len(search_results)} results for query: {query}")
        except Exception as e:
            logger.error(f"Search failed: {e}")
            search_results = []

        results = []
        for idx, track in enumerate(search_results[:10]):
            track_url = track.get('url') or ''
            if not track_url:
                continue

            result_id = f"{idx}_{hashlib.md5(track_url.encode()).hexdigest()[:8]}"
            duration = track.get('duration')

            # В inline-режиме пользователь ожидает, что выбранный трек сразу
            # попадёт в чат, без дополнительной кнопки под служебным сообщением.
            # Поэтому отдаём результат как audio: Telegram сам вставит аудио в чат
            # сразу после выбора строки в списке inline-результатов.
            results.append(
                InlineQueryResultAudio(
                    id=result_id,
                    audio_url=track_url,
                    title=track['title'],
                    performer=track['artist'],
                    audio_duration=int(duration) if duration else None,
                    caption=(
                        f"🎵 <b>{html.escape(track['title'])}</b>\n"
                        f"👤 {html.escape(track['artist'])}\n\n"
                        f"❤️ @GG_Loader_bot"
                    ),
                    parse_mode="HTML",
                    thumbnail_url=track.get('thumbnail') or "https://cdn-icons-png.flaticon.com/512/1384/1384060.png",
                )
            )

        if not results:
            results = [
                InlineQueryResultArticle(
                    id="no_results",
                    title="❌ Ничего не найдено",
                    description=f"По запросу «{query}» треков нет. Попробуйте изменить запрос.",
                    input_message_content=InputTextMessageContent(
                        message_text=f"❌ По запросу <b>«{html.escape(query)}»</b> ничего не найдено.\nПопробуйте написать название трека или автора иначе.",
                        parse_mode="HTML"
                    ),
                    thumbnail_url="https://cdn-icons-png.flaticon.com/512/1384/1384060.png",
                )
            ]

    await inline_query.answer(
        results,
        cache_time=5,
        is_personal=True,
        switch_pm_text="Открыть личные сообщения 💬",
        switch_pm_parameter="from_inline_search"
    )


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

        await callback.message.answer_audio(
            audio=audio_file,
            title=title,
            performer=artist,
            caption=caption,
            parse_mode="HTML",
            thumb=thumb_file
        )

        await status_msg.delete()

    except Exception as e:
        logger.error(f"Error in inline download: {e}")
        await callback.message.answer(f"❌ Произошла ошибка: {str(e)}")
    finally:
        await cleanup_temp_files(user_temp_dir)
