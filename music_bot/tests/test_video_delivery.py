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

    async def test_video_note_crops_rectangular_video_from_center(self):
        video = SimpleNamespace(
            duration=30,
            width=1920,
            height=1080,
            file_unique_id="unique-video",
            file_id="telegram-file",
        )
        bot = SimpleNamespace(
            get_file=AsyncMock(
                return_value=SimpleNamespace(file_path="telegram/video.mp4")
            ),
            download_file=AsyncMock(),
        )
        message = SimpleNamespace(
            video=video,
            bot=bot,
            answer_video_note=AsyncMock(),
        )
        state = SimpleNamespace(clear=AsyncMock())
        status = SimpleNamespace()
        process = SimpleNamespace(
            returncode=0,
            communicate=AsyncMock(return_value=(b"", b"")),
        )

        with (
            patch.object(main_handlers, "FFMPEG_EXECUTABLE", "ffmpeg"),
            patch.object(main_handlers, "TEMP_DIR", "temp"),
            patch("handlers.main_handlers.os.makedirs"),
            patch("handlers.main_handlers.os.path.exists", return_value=True),
            patch(
                "handlers.main_handlers.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ) as create_process,
            patch(
                "handlers.main_handlers.send_media_progress",
                new=AsyncMock(return_value=status),
            ),
            patch(
                "handlers.main_handlers.close_media_status",
                new=AsyncMock(),
            ),
            patch(
                "handlers.main_handlers.cleanup_temp_files",
                new=AsyncMock(),
            ),
            patch(
                "handlers.main_handlers.send_media_error",
                new=AsyncMock(),
            ) as send_media_error,
        ):
            await main_handlers.handle_video_note_upload(message, state)

        command = create_process.await_args.args
        video_filter = command[command.index("-vf") + 1]
        self.assertEqual(
            video_filter,
            "crop='min(iw,ih)':'min(iw,ih)':"
            "'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
            "scale=480:480,setsar=1",
        )
        message.answer_video_note.assert_awaited_once()
        send_media_error.assert_not_awaited()
        state.clear.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
