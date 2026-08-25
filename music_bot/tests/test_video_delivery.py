import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from aiogram.types import FSInputFile
from handlers import main_handlers


class TelegramVideoDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_shared_filesystem_uses_file_uri(self):
        path = os.path.join("temp", "large video.mp4")

        with patch.object(main_handlers, "TELEGRAM_LOCAL_FILE_MODE", True):
            media = main_handlers._telegram_media_input(path)

        self.assertEqual(media, Path(path).resolve().as_uri())

    def test_multipart_mode_uses_fs_input_file(self):
        with patch.object(main_handlers, "TELEGRAM_LOCAL_FILE_MODE", False):
            media = main_handlers._telegram_media_input("temp/video.mp4")

        self.assertIsInstance(media, FSInputFile)

    async def test_video_upload_uses_configured_timeout(self):
        bot = SimpleNamespace(
            send_video=AsyncMock(),
            send_document=AsyncMock(),
        )
        message = SimpleNamespace(chat=SimpleNamespace(id=123), bot=bot)
        result = {
            "video_path": "temp/video.mp4",
            "thumbnail_path": None,
            "width": 3840,
            "height": 2160,
        }

        with (
            patch.object(main_handlers, "TELEGRAM_LOCAL_FILE_MODE", False),
            patch.object(main_handlers, "TELEGRAM_UPLOAD_TIMEOUT_SECONDS", 7200),
        ):
            await main_handlers._send_video_to_chat(message, result, "caption")

        kwargs = bot.send_video.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 123)
        self.assertEqual(kwargs["request_timeout"], 7200)
        self.assertTrue(kwargs["supports_streaming"])
        self.assertIsInstance(kwargs["video"], FSInputFile)
        bot.send_document.assert_not_awaited()

    async def test_video_error_is_not_retried_as_document(self):
        error = RuntimeError("Request timeout error")
        bot = SimpleNamespace(
            send_video=AsyncMock(side_effect=error),
            send_document=AsyncMock(),
        )
        message = SimpleNamespace(chat=SimpleNamespace(id=123), bot=bot)
        result = {
            "video_path": "temp/video.mp4",
            "thumbnail_path": None,
            "width": 1920,
            "height": 1080,
        }

        with patch.object(main_handlers, "TELEGRAM_LOCAL_FILE_MODE", False):
            with self.assertRaisesRegex(RuntimeError, "Request timeout"):
                await main_handlers._send_video_to_chat(message, result, "caption")

        bot.send_document.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
