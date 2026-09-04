import os
import unittest

from aiogram.types import Message

os.environ.setdefault("BOT_TOKEN", "test-token")

from handlers import main_handlers
from models.states import MediaStates


class IncomingMediaRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def first_handler(self, payload, state):
        message = Message.model_validate({
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123, "is_bot": False, "first_name": "Test"},
            **payload,
        })
        # Exercise the registered filters in dispatch order. Calling the upload
        # handler directly missed the earlier catch-all swallowing MP3 documents.
        for handler in main_handlers.router.message.handlers:
            matched, _ = await handler.check(message, raw_state=state)
            if matched:
                return handler.callback.__name__
        return None

    async def test_mp3_document_reaches_cover_handler(self):
        handler = await self.first_handler({
            "document": {
                "file_id": "id", "file_unique_id": "unique",
                "file_name": "TRACK.MP3", "mime_type": "application/octet-stream",
            },
        }, MediaStates.waiting_for_audio_file.state)
        self.assertEqual(handler, "handle_custom_audio")

    async def test_audio_reaches_cover_handler(self):
        handler = await self.first_handler({
            "audio": {"file_id": "id", "file_unique_id": "unique", "duration": 1},
        }, MediaStates.waiting_for_audio_file.state)
        self.assertEqual(handler, "handle_custom_audio")

    async def test_video_note_state_wins_over_audio_extraction(self):
        handler = await self.first_handler({
            "video": {
                "file_id": "id", "file_unique_id": "unique",
                "duration": 15, "width": 640, "height": 360, "file_size": 935936,
            },
        }, MediaStates.waiting_for_video_note.state)
        self.assertEqual(handler, "handle_video_note_upload")

    async def test_video_extraction_still_accepts_video_and_document(self):
        video = {"file_id": "id", "file_unique_id": "unique",
                 "duration": 1, "width": 640, "height": 360}
        document = {"file_id": "id", "file_unique_id": "unique",
                    "file_name": "video.mp4", "mime_type": "video/mp4"}
        for state in (None, MediaStates.waiting_for_extract_link.state):
            for payload in ({"video": video}, {"document": document}):
                with self.subTest(state=state, payload=payload):
                    handler = await self.first_handler(payload, state)
                    self.assertEqual(handler, "handle_video_file_for_audio")

    async def test_photo_reaches_cover_image_handler(self):
        handler = await self.first_handler({
            "photo": [{"file_id": "id", "file_unique_id": "unique",
                       "width": 480, "height": 480}],
        }, MediaStates.waiting_for_cover.state)
        self.assertEqual(handler, "handle_custom_cover")


if __name__ == "__main__":
    unittest.main()
