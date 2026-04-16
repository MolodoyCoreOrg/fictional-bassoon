from aiogram import Router, F
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from utils.music_downloader import search_music, download_from_url
from utils.audio_processor import add_cover_to_mp3, cleanup_temp_files
import hashlib
import os
import uuid
import tempfile
import aiohttp

router = Router()


@router.inline_query()
async def inline_search(inline_query: InlineQuery):
    """
    Обработка inline-запросов для поиска музыки
    Пользователь вводит @GG_Loader_bot название песни
    """
    query = inline_query.query.strip()
    
    if not query:
        # Если запрос пустой, показываем подсказку
        results = [
            InlineQueryResultArticle(
                id="help",
                title="🎵 Поиск музыки",
                description="Введите название песни или исполнителя для поиска",
                input_message_content=InputTextMessageContent(message_text="🎵 Введите название песни для поиска"),
                thumbnail_url="https://cdn-icons-png.flaticon.com/512/1384/1384060.png",
            )
        ]
    else:
        # Ищем музыку (показываем до 10 результатов для пагинации)
        search_results = await search_music(query, limit=10)
        
        results = []
        for idx, track in enumerate(search_results[:10]):  # Максимум 10 результатов
            # Создаем уникальный ID
            result_id = f"{idx}_{hashlib.md5(track['url'].encode()).hexdigest()[:8]}"
            
            # Форматируем длительность
            duration = track.get('duration')
            if duration:
                minutes = int(duration // 60)
                seconds = int(duration % 60)
                duration_str = f"{minutes}:{seconds:02d}"
            else:
                duration_str = ""
            
            # Кодируем данные для отправки в callback
            track_data = f"{track['title']}|{track['artist']}|{track['url']}|{track.get('thumbnail', '')}"
            
            # Создаем клавиатуру с кнопкой для скачивания
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📥 Скачать с обложкой", callback_data=f"download_track:{track_data}")]
            ])
            
            results.append(
                InlineQueryResultArticle(
                    id=result_id,
                    title=f"🎵 {track['title']}",
                    description=f"👤 {track['artist']} | ⏱ {duration_str}",
                    input_message_content=InputTextMessageContent(
                        message_text=f"🎵 **{track['title']}**\n"
                                   f"👤 Исполнитель: {track['artist']}\n"
                                   f"⏱ Длительность: {duration_str}\n\n"
                                   f"_Нажмите кнопку ниже, чтобы скачать трек с обложкой_",
                        parse_mode="Markdown"
                    ),
                    reply_markup=keyboard,
                    thumbnail_url=track.get('thumbnail', "https://cdn-icons-png.flaticon.com/512/1384/1384060.png"),
                    url=track['url'],
                    hide_url=True,
                )
            )
        
        # Если ничего не найдено
        if not results:
            results = [
                InlineQueryResultArticle(
                    id="no_results",
                    title="❌ Ничего не найдено",
                    description="Попробуйте другой запрос",
                    input_message_content=InputTextMessageContent(message_text=f"❌ По запросу \"{query}\" ничего не найдено"),
                    thumbnail_url="https://cdn-icons-png.flaticon.com/512/1384/1384060.png",
                )
            ]
    
    # Отправляем результаты с пагинацией (по 2-3 в ряду)
    await inline_query.answer(results, cache_time=30, is_personal=True)


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
        print(f"Error downloading thumbnail: {e}")
    return None


@router.callback_query(F.data.startswith("download_track:"))
async def process_inline_download(callback: CallbackQuery):
    """Обработка кнопки скачивания трека из inline-режима"""
    await callback.answer("⏳ Скачиваю трек с обложкой...")
    
    # Парсим данные трека
    track_data = callback.data.split(":", 1)[1]
    parts = track_data.split("|")
    if len(parts) < 3:
        await callback.message.answer("❌ Ошибка: неверные данные трека")
        return
    
    title = parts[0]
    artist = parts[1]
    url = parts[2]
    thumbnail_url = parts[3] if len(parts) > 3 else ""
    
    # Создаем временную директорию
    user_temp_dir = os.path.join("/tmp/music_bot", str(uuid.uuid4()))
    os.makedirs(user_temp_dir, exist_ok=True)
    
    try:
        # Скачиваем аудио
        await callback.message.answer("🎵 Скачиваю аудио...")
        download_result = await download_from_url(url, user_temp_dir)
        
        if not download_result['success']:
            await callback.message.answer(f"❌ Ошибка при скачивании: {download_result['error']}")
            await cleanup_temp_files(user_temp_dir)
            return
        
        audio_path = download_result['audio_path']
        
        # Скачиваем обложку если есть URL
        cover_path = None
        if thumbnail_url:
            await callback.message.answer("🖼️ Скачиваю обложку...")
            cover_path = await download_thumbnail(thumbnail_url, user_temp_dir)
        
        # Если обложка не скачалась по URL, используем ту что скачал yt_dlp
        if not cover_path and download_result.get('thumbnail_path'):
            cover_path = download_result['thumbnail_path']
        
        # Добавляем обложку и метаданные
        if cover_path and os.path.exists(cover_path):
            await callback.message.answer("🎨 Применяю обложку и метаданные...")
            processed_path = await add_cover_to_mp3(audio_path, cover_path, title, artist)
        else:
            processed_path = audio_path
        
        # Отправляем готовый файл
        from aiogram.types import FSInputFile
        await callback.message.answer_audio(
            FSInputFile(processed_path),
            title=title,
            performer=artist,
            caption=f"🎵 {title}\n👤 {artist}\n\n_Скачано с помощью @GG_Loader_bot_"
        )
        
        await callback.message.answer("✅ Трек успешно загружен с обложкой и метаданными!")
        
    except Exception as e:
        await callback.message.answer(f"❌ Произошла ошибка: {str(e)}")
    finally:
        # Очищаем временные файлы
        await cleanup_temp_files(user_temp_dir)
