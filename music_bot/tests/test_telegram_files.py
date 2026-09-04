import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile

os.environ.setdefault("BOT_TOKEN", "test-token")

from utils import telegram_files


class TelegramFileDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "job" / "media.mp4"
        self.bot = SimpleNamespace(
            get_file=AsyncMock(return_value=SimpleNamespace(
                file_path="videos/file.mp4", file_size=4,
            )),
            download_file=AsyncMock(side_effect=self.write_download),
        )
        for name in ("TELEGRAM_API_FILE_ROOT", "TELEGRAM_BOT_FILE_ROOT"):
            patcher = patch.object(telegram_files, name, "")
            patcher.start()
            self.addCleanup(patcher.stop)

    async def write_download(self, file_path, *, destination, timeout):
        Path(destination).write_bytes(b"data")

    async def test_cloud_download_has_timeout_and_complete_destination(self):
        with patch.object(telegram_files, "TELEGRAM_DOWNLOAD_TIMEOUT_SECONDS", 450):
            await telegram_files.download_telegram_file(
                self.bot, "id", str(self.destination)
            )

        self.bot.get_file.assert_awaited_once_with("id", request_timeout=450)
        kwargs = self.bot.download_file.await_args.kwargs
        self.assertEqual(kwargs["timeout"], 450)
        self.assertEqual(self.destination.read_bytes(), b"data")
        self.assertFalse(self.destination.with_suffix(".mp4.part").exists())

    async def test_local_api_path_works_with_multipart_session(self):
        # Reproduce the regression with the real aiogram API configuration:
        # is_local=False used to turn this absolute cache path into an HTTP URL.
        source = self.root / "telegram-cache" / "small-video.mp4"
        source.parent.mkdir()
        source.write_bytes(b"x" * (914 * 1024))
        session = AiohttpSession(api=TelegramAPIServer.from_base(
            "http://localhost:8081", is_local=False,
        ))
        bot = Bot("123456:TEST_TOKEN", session=session)
        self.addAsyncCleanup(session.close)
        with (
            patch.object(bot, "get_file", new=AsyncMock(
                return_value=SimpleNamespace(
                    file_path=str(source), file_size=source.stat().st_size,
                )
            )),
            patch.object(session, "stream_content") as http_download,
        ):
            await telegram_files.download_telegram_file(
                bot, "id", str(self.destination)
            )

        self.assertEqual(self.destination.read_bytes(), source.read_bytes())
        http_download.assert_not_called()
        self.assertTrue(source.is_file())

    async def test_maps_shared_cache_to_bot_mount(self):
        source = self.root / "mounted-cache" / "bot" / "music.mp3"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"data")
        self.bot.get_file.return_value.file_path = "/api-cache/bot/music.mp3"
        with (
            patch.object(telegram_files, "TELEGRAM_API_FILE_ROOT", "/api-cache"),
            patch.object(telegram_files, "TELEGRAM_BOT_FILE_ROOT", str(source.parents[1])),
        ):
            await telegram_files.download_telegram_file(
                self.bot, "id", str(self.destination)
            )
        self.assertEqual(self.destination.read_bytes(), b"data")
        self.bot.download_file.assert_not_awaited()

    async def test_unavailable_local_cache_never_becomes_http_request(self):
        self.bot.get_file.return_value.file_path = str(self.root / "missing.mp4")
        with self.assertRaisesRegex(telegram_files.TelegramDownloadError, "администратору"):
            await telegram_files.download_telegram_file(
                self.bot, "id", str(self.destination)
            )
        self.bot.download_file.assert_not_awaited()
        self.assertFalse(self.destination.exists())

    async def test_mapping_rejects_paths_outside_cache(self):
        with (
            patch.object(telegram_files, "TELEGRAM_API_FILE_ROOT", "/api-cache"),
            patch.object(telegram_files, "TELEGRAM_BOT_FILE_ROOT", str(self.root)),
        ):
            for path in ("/other/file.mp4", "/api-cache/../outside.mp4"):
                with self.subTest(path=path):
                    with self.assertRaises(telegram_files.TelegramDownloadError):
                        telegram_files._local_source(path)

    async def test_retries_timeout_and_refreshes_file_path(self):
        async def fail_after_partial(*args, **kwargs):
            Path(kwargs["destination"]).write_bytes(b"partial")
            raise asyncio.TimeoutError()

        self.bot.download_file.side_effect = fail_after_partial
        self.bot.get_file.side_effect = [
            SimpleNamespace(file_path="videos/old.mp4", file_size=4),
            SimpleNamespace(file_path="videos/fresh.mp4", file_size=4),
        ]
        async def retry_sleep(delay):
            self.bot.download_file.side_effect = self.write_download

        with patch.object(telegram_files.asyncio, "sleep", new=AsyncMock(
            side_effect=retry_sleep,
        )):
            await telegram_files.download_telegram_file(
                self.bot, "id", str(self.destination)
            )
        self.assertEqual(self.bot.get_file.await_count, 2)
        self.assertEqual(self.bot.download_file.await_args.args[0], "videos/fresh.mp4")
        self.assertEqual(self.destination.read_bytes(), b"data")

    async def test_exhausted_retries_remove_partial_file(self):
        async def fail(*args, **kwargs):
            Path(kwargs["destination"]).write_bytes(b"partial")
            raise asyncio.TimeoutError()
        self.bot.download_file.side_effect = fail
        with patch.object(telegram_files.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(telegram_files.TelegramDownloadError, "сбоя связи"):
                await telegram_files.download_telegram_file(
                    self.bot, "id", str(self.destination)
                )
        self.assertEqual(self.bot.get_file.await_count, 3)
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.destination.with_suffix(".mp4.part").exists())

    async def test_missing_path_and_incomplete_file_are_rejected(self):
        for path, size in ((None, 4), ("videos/file.mp4", 100)):
            with self.subTest(path=path):
                self.bot.get_file.return_value = SimpleNamespace(
                    file_path=path, file_size=size,
                )
                with self.assertRaises(telegram_files.TelegramDownloadError):
                    await telegram_files.download_telegram_file(
                        self.bot, "id", str(self.destination)
                    )
                self.assertFalse(self.destination.exists())
                self.assertFalse(self.destination.with_suffix(".mp4.part").exists())

    async def test_cloud_size_limit_has_specific_message(self):
        self.bot.get_file.side_effect = TelegramBadRequest(
            method=GetFile(file_id="id"), message="Bad Request: file is too big",
        )
        with self.assertRaisesRegex(telegram_files.TelegramDownloadError, "20 МБ"):
            await telegram_files.download_telegram_file(
                self.bot, "id", str(self.destination)
            )
        self.bot.get_file.assert_awaited_once()
        self.bot.download_file.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
