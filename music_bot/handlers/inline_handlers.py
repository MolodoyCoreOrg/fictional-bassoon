from aiogram import Router, F
from aiogram.types import InlineQuery, InlineQueryResultAudio, InlineQueryResultArticle
from utils.music_downloader import search_music
import hashlib

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
                input_message_content={"message_text": "🎵 Введите название песни для поиска"},
                thumbnail_url="https://cdn-icons-png.flaticon.com/512/1384/1384060.png",
            )
        ]
    else:
        # Ищем музыку
        search_results = await search_music(query, limit=5)
        
        results = []
        for idx, track in enumerate(search_results):
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
            
            results.append(
                InlineQueryResultArticle(
                    id=result_id,
                    title=f"🎵 {track['title']}",
                    description=f"👤 {track['artist']} | ⏱ {duration_str}",
                    input_message_content={
                        "message_text": f"🎵 **{track['title']}**\n"
                                       f"👤 Исполнитель: {track['artist']}\n"
                                       f"🔗 Источник: {track['url']}\n\n"
                                       f"_Скачано с помощью @GG_Loader_bot_"
                    },
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
                    input_message_content={"message_text": f"❌ По запросу \"{query}\" ничего не найдено"},
                    thumbnail_url="https://cdn-icons-png.flaticon.com/512/1384/1384060.png",
                )
            ]
    
    await inline_query.answer(results, cache_time=30)
