import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from aiogram.types import InlineQueryResultArticle
from handlers import inline_handlers


class InlineSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_returns_artwork_articles_with_download_buttons(self):
        query = SimpleNamespace(
            query="Artist Track",
            id="inline-1",
            answer=AsyncMock(),
        )
        tracks = [{
            "title": "Track",
            "artist": "Artist",
            "url": "https://www.youtube.com/watch?v=track-id",
            "duration": 125,
            "thumbnail": "https://i.ytimg.com/vi/track-id/hqdefault.jpg",
        }]

        with patch.object(
            inline_handlers,
            "search_music",
            AsyncMock(return_value=tracks),
        ):
            await inline_handlers.inline_search(query)

        answer_args, answer_kwargs = query.answer.await_args
        results = answer_args[0]
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], InlineQueryResultArticle)
        self.assertEqual(results[0].thumbnail_url, tracks[0]["thumbnail"])
        self.assertEqual(results[0].description, "2:05 • Artist")
        self.assertTrue(
            results[0].reply_markup.inline_keyboard[0][0].callback_data.startswith("dl:")
        )
        self.assertTrue(answer_kwargs["is_personal"])


if __name__ == "__main__":
    unittest.main()
