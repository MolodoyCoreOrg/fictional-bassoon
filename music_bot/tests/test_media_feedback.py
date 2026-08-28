import os
import unittest
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
