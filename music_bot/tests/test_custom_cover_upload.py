import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from handlers import main_handlers
from models.states import MediaStates


class CustomCoverUploadTests(unittest.IsolatedAsyncioTestCase):
    def test_prefers_telegram_audio_payload(self):
        audio = SimpleNamespace(file_id="audio")
        document = SimpleNamespace(
            file_id="document",
            file_name="track.mp3",
            mime_type="audio/mpeg",
        )
        message = SimpleNamespace(audio=audio, document=document)

        self.assertIs(main_handlers._get_custom_mp3_upload(message), audio)

    def test_accepts_mp3_document_by_extension(self):
        document = SimpleNamespace(
            file_id="document",
            file_name="TRACK.MP3",
            mime_type="application/octet-stream",
        )
        message = SimpleNamespace(audio=None, document=document)

        self.assertIs(main_handlers._get_custom_mp3_upload(message), document)

    def test_accepts_mp3_document_by_mime_type(self):
        document = SimpleNamespace(
            file_id="document",
            file_name="track.bin",
            mime_type="Audio/MPEG; charset=binary",
        )
        message = SimpleNamespace(audio=None, document=document)

        self.assertIs(main_handlers._get_custom_mp3_upload(message), document)

    def test_rejects_non_mp3_document(self):
        document = SimpleNamespace(
            file_id="document",
            file_name="notes.pdf",
            mime_type="application/pdf",
        )
        message = SimpleNamespace(audio=None, document=document)

        self.assertIsNone(main_handlers._get_custom_mp3_upload(message))

    async def test_mp3_document_continues_cover_workflow(self):
        document = SimpleNamespace(
            file_id="telegram-file",
            file_unique_id="upload-unique",
            file_name="track.mp3",
            mime_type="audio/mpeg",
        )
        bot = SimpleNamespace(
            get_file=AsyncMock(
                return_value=SimpleNamespace(file_path="telegram/track.mp3")
            ),
            download_file=AsyncMock(),
        )
        message = SimpleNamespace(
            audio=None,
            document=document,
            bot=bot,
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            update_data=AsyncMock(),
            set_state=AsyncMock(),
            clear=AsyncMock(),
        )
        status = SimpleNamespace()

        with (
            patch.object(main_handlers, "TEMP_DIR", "temp"),
            patch(
                "handlers.main_handlers.download_telegram_file",
                new=AsyncMock(),
            ) as download_telegram_file,
            patch("handlers.main_handlers.os.makedirs"),
            patch(
                "handlers.main_handlers.send_media_progress",
                new=AsyncMock(return_value=status),
            ),
            patch(
                "handlers.main_handlers.close_media_status",
                new=AsyncMock(),
            ) as close_media_status,
            patch(
                "handlers.main_handlers.send_media_error",
                new=AsyncMock(),
            ) as send_media_error,
        ):
            await main_handlers.handle_custom_audio(message, state)

        download_telegram_file.assert_awaited_once()
        download_args = download_telegram_file.await_args.args
        self.assertIs(download_args[0], bot)
        self.assertEqual(download_args[1], "telegram-file")

        saved = state.update_data.await_args.kwargs
        self.assertEqual(download_args[2], saved["audio_path"])
        self.assertEqual(
            saved["audio_path"],
            os.path.join(saved["temp_dir"], "upload-unique.mp3"),
        )
        close_media_status.assert_awaited_once_with(status)
        state.set_state.assert_awaited_once_with(
            MediaStates.waiting_for_cover
        )
        message.answer.assert_awaited_once()
        send_media_error.assert_not_awaited()
        state.clear.assert_not_awaited()

    async def test_wrong_document_gets_visible_error(self):
        document = SimpleNamespace(
            file_id="document",
            file_unique_id="not-mp3",
            file_name="notes.pdf",
            mime_type="application/pdf",
        )
        message = SimpleNamespace(audio=None, document=document)
        state = SimpleNamespace()

        with patch(
            "handlers.main_handlers.send_media_error",
            new=AsyncMock(),
        ) as send_media_error:
            await main_handlers.handle_custom_audio(message, state)

        send_media_error.assert_awaited_once()
        self.assertIn(
            "MP3",
            send_media_error.await_args.args[3],
        )


if __name__ == "__main__":
    unittest.main()
