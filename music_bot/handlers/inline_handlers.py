import asyncio
import html
import logging

from aiogram import Bot, Router
from aiogram.types import (
    ChosenInlineResult,
    FSInputFile,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultAudio,
    InlineQueryResultCachedAudio,
    InputTextMessageContent,
)

from utils.album_cache import cache_album
from utils.config import INLINE_STORAGE_CHAT_ID
from utils.inline_media import (
    create_inline_media_url,
    ensure_inline_media_file,
    get_inline_media_request,
    register_inline_media_request,
)
from utils.keyboard import get_inline_album_keyboard, get_inline_result_keyboard
from utils.music_downloader import search_music
from utils.track_history import (
    get_cached_audio,
    get_user_history,
    remember_audio_reference,
)

logger = logging.getLogger(__name__)
router = Router()


def _caption(track: dict) -> str:
    return (
        f"🎵 <b>{html.escape(track.get('title') or 'Неизвестно')}</b>\n"
        f"👤 {html.escape(track.get('artist') or 'Неизвестно')}\n\n"
        "❤️ @GG_Loader_bot"
    )


def _album_reference(track: dict) -> tuple[str | None, str | None]:
    album_title = (track.get("album") or "").strip()
    album_url = (track.get("album_url") or "").strip()
    if not album_title or not album_url:
        return None, None

    album_key = cache_album({
        "album": album_title,
        "artist": track.get("artist") or "Неизвестно",
        "album_url": album_url,
        "thumbnail": track.get("thumbnail"),
        "tracks": [],
    })
    return album_title, album_key


def _album_markup(track: dict):
    album_title, album_key = _album_reference(track)
    if not album_key:
        return None
    return get_inline_album_keyboard(album_title, album_key)


def _cached_result(track: dict, prefix: str = "h") -> InlineQueryResultCachedAudio:
    request_key = register_inline_media_request(track)
    return InlineQueryResultCachedAudio(
        id=f"{prefix}_{request_key}",
        audio_file_id=track["file_id"],
        caption=_caption(track),
        parse_mode="HTML",
        reply_markup=_album_markup(track),
    )


def _article(
    result_id: str,
    title: str,
    description: str,
    text: str,
    thumbnail_url: str | None = None,
    reply_markup=None,
) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=result_id,
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            message_text=text,
            parse_mode="HTML",
        ),
        thumbnail_url=(
            thumbnail_url
            or "https://cdn-icons-png.flaticon.com/512/1384/1384060.png"
        ),
        reply_markup=reply_markup,
    )


def _download_article(track: dict) -> InlineQueryResultArticle:
    """Keeps search useful when the public MP3 gateway is not configured."""
    track_key = cache_album({
        "album": track.get("album") or "Результат поиска",
        "artist": track.get("artist") or "Неизвестно",
        "album_url": track.get("album_url"),
        "thumbnail": track.get("thumbnail"),
        "tracks": [dict(track)],
    })
    album_title, album_key = _album_reference(track)
    title = track.get("title") or "Неизвестно"
    artist = track.get("artist") or "Неизвестно"
    duration = track.get("duration")
    duration_text = ""
    if duration:
        minutes, seconds = divmod(int(duration), 60)
        duration_text = f"{minutes}:{seconds:02d} · "

    return _article(
        f"d_{track_key}",
        title,
        f"{duration_text}{artist}",
        (
            f"🎵 <b>{html.escape(title)}</b>\n"
            f"👤 {html.escape(artist)}\n\n"
            "Нажмите кнопку ниже, чтобы скачать трек в личном чате с ботом."
        ),
        thumbnail_url=track.get("thumbnail"),
        reply_markup=get_inline_result_keyboard(
            track_key,
            album_title=album_title,
            album_key=album_key,
        ),
    )


@router.inline_query()
async def inline_search(inline_query: InlineQuery):
    """Shows personal history first and sends every selected track as audio."""
    query = inline_query.query.strip()
    user_id = inline_query.from_user.id

    try:
        history_limit = 25 if not query else 12
        history = await get_user_history(user_id, query=query, limit=history_limit)
    except Exception:
        logger.exception("Cannot load inline history for user %s", user_id)
        history = []

    results = []
    seen_file_ids = set()
    seen_source_urls = set()

    for track in history:
        if not track.get("file_id"):
            continue
        results.append(_cached_result(track))
        seen_file_ids.add(track["file_id"])
        if track.get("source_url"):
            seen_source_urls.add(track["source_url"])

    if not query:
        if not results:
            results = [
                _article(
                    "empty_history",
                    "Здесь появятся ваши песни 🎵",
                    "Скачайте трек через бота или начните поиск по названию.",
                    (
                        "🎵 <b>История пока пуста.</b>\n\n"
                        "Напишите после имени бота исполнителя и название песни:\n"
                        "<code>@GG_Loader_bot название песни</code>"
                    ),
                )
            ]
    else:
        logger.info("Inline search query from %s: %s", user_id, query)
        try:
            search_results = await search_music(query, limit=24)
        except Exception as error:
            logger.exception("Inline search failed for %s: %s", query, error)
            search_results = []

        candidates = [
            track
            for track in search_results
            if track.get("url") and track.get("url") not in seen_source_urls
        ]
        cached_candidates = await asyncio.gather(
            *(get_cached_audio(track.get("url")) for track in candidates),
            return_exceptions=True,
        )

        for track, cached in zip(candidates, cached_candidates):
            if len(results) >= 30:
                break
            if isinstance(cached, dict) and cached.get("file_id"):
                merged = {**track, **cached}
                if merged["file_id"] in seen_file_ids:
                    continue
                results.append(_cached_result(merged, prefix="c"))
                seen_file_ids.add(merged["file_id"])
                seen_source_urls.add(track["url"])
                continue

            request_key, audio_url = create_inline_media_url(track)
            if not request_key or not audio_url:
                results.append(_download_article(track))
                seen_source_urls.add(track["url"])
                continue

            duration = track.get("duration")
            results.append(
                InlineQueryResultAudio(
                    id=f"r_{request_key}",
                    audio_url=audio_url,
                    title=track.get("title") or "Неизвестно",
                    performer=track.get("artist") or "Неизвестно",
                    audio_duration=int(duration) if duration else None,
                    caption=_caption(track),
                    parse_mode="HTML",
                    reply_markup=_album_markup(track),
                )
            )
            seen_source_urls.add(track["url"])

        if not results:
            results = [
                _article(
                    "no_results",
                    "❌ Ничего не найдено",
                    f"По запросу «{query}» нет доступных треков.",
                    (
                        f"❌ По запросу <b>«{html.escape(query)}»</b> ничего не найдено.\n"
                        "Попробуйте изменить название или добавить исполнителя."
                    ),
                )
            ]

    try:
        await inline_query.answer(
            results[:50],
            cache_time=0,
            is_personal=True,
            switch_pm_text="Открыть личные сообщения 💬",
            switch_pm_parameter="from_inline_search",
        )
    except Exception as error:
        logger.exception("Failed to answer inline query %s: %s", inline_query.id, error)


@router.chosen_inline_result()
async def remember_chosen_track(chosen: ChosenInlineResult, bot: Bot):
    """Turns selected remote results into reusable Telegram file_ids for history."""
    if len(chosen.result_id) < 3 or chosen.result_id[1] != "_":
        return

    request_key = chosen.result_id[2:]
    track = get_inline_media_request(request_key)
    if not track:
        return

    user_id = chosen.from_user.id
    if track.get("file_id") and track.get("file_unique_id"):
        await remember_audio_reference(
            user_id=user_id,
            file_id=track["file_id"],
            file_unique_id=track["file_unique_id"],
            title=track.get("title") or "Неизвестно",
            artist=track.get("artist") or "Неизвестно",
            duration=track.get("duration"),
            source_url=track.get("source_url") or track.get("url"),
        )
        return

    stable_source_url = track.get("url")
    cached = await get_cached_audio(stable_source_url)
    if cached:
        await remember_audio_reference(
            user_id=user_id,
            file_id=cached["file_id"],
            file_unique_id=cached["file_unique_id"],
            title=cached.get("title") or track.get("title") or "Неизвестно",
            artist=cached.get("artist") or track.get("artist") or "Неизвестно",
            duration=cached.get("duration") or track.get("duration"),
            source_url=stable_source_url,
        )
        return

    storage_chat_id = INLINE_STORAGE_CHAT_ID or user_id
    storage_message = None
    try:
        audio_path = await ensure_inline_media_file(track)
        storage_message = await bot.send_audio(
            chat_id=storage_chat_id,
            audio=FSInputFile(audio_path),
            title=track.get("title") or "Неизвестно",
            performer=track.get("artist") or "Неизвестно",
            disable_notification=True,
        )
        await remember_audio_reference(
            user_id=user_id,
            file_id=storage_message.audio.file_id,
            file_unique_id=storage_message.audio.file_unique_id,
            title=storage_message.audio.title or track.get("title") or "Неизвестно",
            artist=storage_message.audio.performer or track.get("artist") or "Неизвестно",
            duration=storage_message.audio.duration or track.get("duration"),
            source_url=stable_source_url,
            cache_globally=True,
        )
    except Exception as error:
        logger.exception(
            "Cannot cache chosen inline track %s for user %s: %s",
            chosen.result_id,
            user_id,
            error,
        )
    finally:
        if storage_message and not INLINE_STORAGE_CHAT_ID:
            try:
                await bot.delete_message(
                    chat_id=storage_message.chat.id,
                    message_id=storage_message.message_id,
                )
            except Exception:
                pass
