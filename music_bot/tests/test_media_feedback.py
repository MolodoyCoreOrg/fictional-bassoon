import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from utils import media_feedback
from utils.keyboard import get_video_quality_keyboard


class MediaFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_uses_animation_asset(self):
        sent = SimpleNamespace(message_id=1)
        message = SimpleNamespace(
            answer_animation=AsyncMock(return_value=sent),
            answer=AsyncMock(),
        )

        with patch("utils.media_feedback.Path.is_file", return_value=True):
            result = await media_feedback.send_media_progress(
                message,
                "⏳ Загружаю...",
            )

        self.assertIs(result, sent)
        message.answer_animation.assert_awaited_once()
        self.assertEqual(
            message.answer_animation.await_args.kwargs["caption"],
            "⏳ Загружаю...",
        )
        message.answer.assert_not_awaited()

    async def test_progress_falls_back_to_text_when_asset_is_missing(self):
        sent = SimpleNamespace(message_id=2)
        message = SimpleNamespace(
            answer_animation=AsyncMock(),
            answer=AsyncMock(return_value=sent),
        )

        with patch("utils.media_feedback.Path.is_file", return_value=False):
            result = await media_feedback.send_media_progress(message, "Загрузка")

        self.assertIs(result, sent)
        message.answer_animation.assert_not_awaited()
        message.answer.assert_awaited_once_with("Загрузка", parse_mode="HTML")

    async def test_animation_progress_is_edited_via_caption(self):
        status = SimpleNamespace(
            caption="⏳ Загружаю...",
            animation=object(),
            edit_caption=AsyncMock(),
            edit_text=AsyncMock(),
        )

        await media_feedback.edit_media_status(status, "Готово")

        status.edit_caption.assert_awaited_once_with(
            caption="Готово",
            parse_mode="HTML",
            reply_markup=None,
        )
        status.edit_text.assert_not_awaited()

    async def test_text_progress_is_edited_via_text(self):
        status = SimpleNamespace(
            caption=None,
            animation=None,
            edit_caption=AsyncMock(),
            edit_text=AsyncMock(),
        )

        await media_feedback.edit_media_status(status, "Готово")

        status.edit_text.assert_awaited_once_with(
            "Готово",
            parse_mode="HTML",
            reply_markup=None,
        )
        status.edit_caption.assert_not_awaited()

    async def test_error_replaces_status_with_matching_image(self):
        sent = SimpleNamespace(message_id=3)
        status = SimpleNamespace(delete=AsyncMock())
        message = SimpleNamespace(
            answer_photo=AsyncMock(return_value=sent),
            answer=AsyncMock(),
        )

        with patch("utils.media_feedback.Path.is_file", return_value=True):
            result = await media_feedback.send_media_error(
                message,
                status,
                "audio",
                "❌ Ошибка загрузки",
            )

        self.assertIs(result, sent)
        status.delete.assert_awaited_once()
        message.answer_photo.assert_awaited_once()
        photo = message.answer_photo.await_args.kwargs["photo"]
        self.assertTrue(str(photo.path).endswith("Ошибка загрузки аудио.png"))
        message.answer.assert_not_awaited()

    async def test_long_error_still_sends_matching_image(self):
        sent_photo = SimpleNamespace(message_id=4)
        sent_text = SimpleNamespace(message_id=5)
        message = SimpleNamespace(
            answer_photo=AsyncMock(return_value=sent_photo),
            answer=AsyncMock(return_value=sent_text),
        )
        long_error = "❌ " + ("Ошибка загрузки. " * 100)

        with patch("utils.media_feedback.Path.is_file", return_value=True):
            result = await media_feedback.send_media_error(
                message,
                None,
                "video",
                long_error,
            )

        self.assertIs(result, sent_text)
        message.answer_photo.assert_awaited_once()
        photo_kwargs = message.answer_photo.await_args.kwargs
        self.assertTrue(str(photo_kwargs["photo"].path).endswith("Ошибка загрузки видео.png"))
        self.assertEqual(photo_kwargs["caption"], "❌ Ошибка загрузки видео")
        message.answer.assert_awaited_once_with(
            long_error,
            reply_markup=None,
            parse_mode="HTML",
        )

    def test_user_visible_handler_errors_use_media_error_helper(self):
        handlers_path = (
            Path(__file__).resolve().parents[1]
            / "handlers"
            / "main_handlers.py"
        )
        source = handlers_path.read_text(encoding="utf-8")
        direct_error_response = re.compile(
            r"await\s+(?:message|status_msg|callback)\."
            r"(?:answer|edit_text)\(\s*f?[\"'](?:❌|⚠️)"
        )

        self.assertIsNone(direct_error_response.search(source))


class VideoQualityKeyboardTests(unittest.TestCase):
    def test_safe_qualities_are_ordered_from_low_to_high(self):
        keyboard = get_video_quality_keyboard(
            "https://example.com/video",
            [
                {
                    "quality_label": "1080p",
                    "format_id": "r1920x1080",
                    "width": 1920,
                    "height": 1080,
                },
                {
                    "quality_label": "360p",
                    "format_id": "r640x360",
                    "width": 640,
                    "height": 360,
                },
                {
                    "quality_label": "720p",
                    "format_id": "r1280x720",
                    "width": 1280,
                    "height": 720,
                },
            ],
            "Video",
            request_id="request",
        )

        quality_buttons = keyboard.inline_keyboard[0]
        self.assertEqual(
            [button.text for button in quality_buttons],
            ["🎬 360p", "🎬 720p", "🎬 1080p"],
        )



if __name__ == "__main__":
    unittest.main()
