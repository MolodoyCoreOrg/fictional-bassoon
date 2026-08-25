import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from aiogram.types import InlineQueryResultAudio, InlineQueryResultCachedAudio
from handlers import inline_handlers


def make_query(text: str = ""):
    return SimpleNamespace(
        query=text,
        id="inline-1",
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
    )


class InlineSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_query_returns_download_history_in_order(self):
        query = make_query()
        history = [
            {
                "title": "Newest",
                "artist": "Artist",
                "file_id": "file-new",
                "file_unique_id": "unique-new",
                "duration": 120,
                "source_url": "https://soundcloud.com/artist/newest",
            },
            {
                "title": "Older",
                "artist": "Artist",
                "file_id": "file-old",
                "file_unique_id": "unique-old",
                "duration": 100,
                "source_url": "https://www.youtube.com/watch?v=older",
            },
        ]

        with patch.object(
            inline_handlers,
            "get_user_history",
            AsyncMock(return_value=history),
        ):
            await inline_handlers.inline_search(query)

        results = query.answer.await_args.args[0]
        self.assertEqual([result.audio_file_id for result in results], ["file-new", "file-old"])
        self.assertTrue(all(isinstance(result, InlineQueryResultCachedAudio) for result in results))
        self.assertTrue(all(result.reply_markup is None for result in results))

    async def test_remote_search_results_are_direct_audio_without_download_button(self):
        query = make_query("Artist Track")
        tracks = [{
            "title": "Track",
            "artist": "Artist",
            "url": "https://www.youtube.com/watch?v=track-id",
            "duration": 125,
            "thumbnail": "https://i.ytimg.com/vi/track-id/hqdefault.jpg",
            "source": "yt",
        }]

        with (
            patch.object(
                inline_handlers,
                "get_user_history",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                inline_handlers,
                "search_music",
                AsyncMock(return_value=tracks),
            ),
            patch.object(
                inline_handlers,
                "get_cached_audio",
                AsyncMock(return_value=None),
            ),
            patch.object(
                inline_handlers,
                "create_inline_media_url",
                return_value=("request-key", "https://media.example/inline/audio/request-key.mp3"),
            ),
        ):
            await inline_handlers.inline_search(query)

        results = query.answer.await_args.args[0]
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], InlineQueryResultAudio)
        self.assertEqual(
            results[0].audio_url,
            "https://media.example/inline/audio/request-key.mp3",
        )
        self.assertEqual(results[0].performer, "Artist")
        self.assertIsNone(results[0].reply_markup)
        self.assertTrue(query.answer.await_args.kwargs["is_personal"])
        self.assertEqual(query.answer.await_args.kwargs["cache_time"], 0)


if __name__ == "__main__":
    unittest.main()
