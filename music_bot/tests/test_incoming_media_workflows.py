import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mutagen.mp3 import MP3
from PIL import Image

os.environ.setdefault("BOT_TOKEN", "test-token")

from handlers import main_handlers
from models.states import MediaStates
from utils import telegram_files


class IncomingMediaWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_data = {}

        async def update_data(**kwargs):
            self.state_data.update(kwargs)

        self.state = SimpleNamespace(
            update_data=AsyncMock(side_effect=update_data),
            get_data=AsyncMock(side_effect=lambda: dict(self.state_data)),
            set_state=AsyncMock(), clear=AsyncMock(),
        )
        self.bot = SimpleNamespace(get_file=AsyncMock(), download_file=AsyncMock())
        self.message = SimpleNamespace(
            bot=self.bot, answer=AsyncMock(), answer_video_note=AsyncMock(),
            audio=None, document=None, photo=None,
        )
        for name, value in (
            ("TEMP_DIR", str(self.root / "jobs")),
            ("TELEGRAM_LOCAL_FILE_MODE", False),
            ("TELEGRAM_UPLOAD_TIMEOUT_SECONDS", 420),
            ("send_media_progress", AsyncMock()),
            ("close_media_status", AsyncMock()),
            ("send_media_error", AsyncMock()),
        ):
            patcher = patch.object(main_handlers, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("TELEGRAM_API_FILE_ROOT", "TELEGRAM_BOT_FILE_ROOT"):
            patcher = patch.object(telegram_files, name, "")
            patcher.start()
            self.addCleanup(patcher.stop)

    def incoming_file(self, path):
        self.bot.get_file.return_value = SimpleNamespace(
            file_path=str(path), file_size=path.stat().st_size,
        )

    async def test_mp3_and_cover_are_downloaded_and_advance_state(self):
        audio = self.root / "track.mp3"
        audio.write_bytes(b"ID3-test-upload")
        self.incoming_file(audio)
        self.message.document = SimpleNamespace(
            file_id="audio-id", file_unique_id="audio-unique",
            file_name="TRACK.MP3", mime_type="application/octet-stream",
        )
        await main_handlers.handle_custom_audio(self.message, self.state)
        saved_audio = Path(self.state_data["audio_path"])
        self.assertEqual(saved_audio.read_bytes(), audio.read_bytes())
        self.state.set_state.assert_awaited_with(MediaStates.waiting_for_cover)

        cover = self.root / "image.jpg"
        cover.write_bytes(b"test-cover-upload")
        self.incoming_file(cover)
        self.message.photo = [SimpleNamespace(file_id="photo-id")]
        await main_handlers.handle_custom_cover(self.message, self.state)
        self.assertEqual(Path(self.state_data["cover_path"]).read_bytes(), cover.read_bytes())
        self.state.set_state.assert_awaited_with(MediaStates.waiting_for_track_info)
        self.bot.download_file.assert_not_awaited()
        main_handlers.send_media_error.assert_not_awaited()
        self.assertTrue(audio.exists())
        self.assertTrue(cover.exists())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed to create a real MP3")
    async def test_real_mp3_cover_is_embedded_before_sending(self):
        audio = self.root / "source.mp3"
        subprocess.run([
            shutil.which("ffmpeg"), "-y", "-nostdin", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
            "-c:a", "libmp3lame", str(audio),
        ], check=True, capture_output=True, timeout=30)
        cover = self.root / "source.jpg"
        Image.new("RGB", (640, 360), "red").save(cover)
        self.incoming_file(audio)
        self.message.audio = SimpleNamespace(file_id="audio", file_unique_id="track")
        await main_handlers.handle_custom_audio(self.message, self.state)
        self.incoming_file(cover)
        self.message.photo = [SimpleNamespace(file_id="photo")]
        await main_handlers.handle_custom_cover(self.message, self.state)
        self.state_data.update(title="Test track", artist="Test artist")
        sent_paths = []

        async def inspect_sent_audio(**kwargs):
            path = Path(kwargs["audio"].path)
            sent_paths.append(path)
            tags = MP3(path).tags
            self.assertEqual(str(tags["TIT2"]), "Test track")
            self.assertEqual(str(tags["TPE1"]), "Test artist")
            self.assertEqual(len(tags.getall("APIC")), 1)
            self.assertTrue(tags.getall("APIC")[0].data)
            self.assertEqual(kwargs["request_timeout"], 420)
            self.assertTrue(Path(kwargs["thumbnail"].path).exists())
            return SimpleNamespace()

        self.message.answer_audio = AsyncMock(side_effect=inspect_sent_audio)
        with patch.object(main_handlers, "remember_audio_message", new=AsyncMock()):
            await main_handlers.process_final_audio(self.message, self.state, user_id=123)
        main_handlers.send_media_error.assert_not_awaited()
        self.assertEqual(len(sent_paths), 1)
        self.assertFalse(sent_paths[0].exists())
        self.assertTrue(audio.exists())
        self.assertTrue(cover.exists())
        self.state.clear.assert_awaited_once()

    async def test_audio_download_failure_allows_retry_in_same_mode(self):
        with patch.object(main_handlers, "download_telegram_file", new=AsyncMock(
            side_effect=telegram_files.TelegramDownloadError("❌ Сбой связи"),
        )):
            self.message.audio = SimpleNamespace(
                file_id="id", file_unique_id="unique",
            )
            await main_handlers.handle_custom_audio(self.message, self.state)
        self.state.set_state.assert_awaited_with(MediaStates.waiting_for_audio_file)
        self.state.clear.assert_not_awaited()
        main_handlers.send_media_error.assert_awaited_once()

    async def test_cover_failure_preserves_audio_for_retry(self):
        audio = self.root / "track.mp3"
        audio.write_bytes(b"ID3")
        self.state_data.update(temp_dir=str(self.root), audio_path=str(audio))
        self.message.photo = [SimpleNamespace(file_id="id")]
        with patch.object(main_handlers, "download_telegram_file", new=AsyncMock(
            side_effect=telegram_files.TelegramDownloadError("❌ Сбой связи"),
        )):
            await main_handlers.handle_custom_cover(self.message, self.state)
        self.assertTrue(audio.exists())
        self.state.set_state.assert_awaited_with(MediaStates.waiting_for_cover)
        self.state.clear.assert_not_awaited()

    async def test_video_download_error_does_not_blame_duration(self):
        self.message.video = SimpleNamespace(
            duration=1, file_id="id", file_unique_id="unique",
        )
        with (
            patch.object(main_handlers, "FFMPEG_EXECUTABLE", "ffmpeg"),
            patch.object(main_handlers, "download_telegram_file", new=AsyncMock(
                side_effect=telegram_files.TelegramDownloadError("❌ Сбой связи"),
            )),
            patch.object(main_handlers.asyncio, "create_subprocess_exec") as convert,
        ):
            await main_handlers.handle_video_note_upload(self.message, self.state)
        self.assertEqual(main_handlers.send_media_error.await_args.args[3], "❌ Сбой связи")
        convert.assert_not_called()
        self.state.clear.assert_not_awaited()
        self.message.answer_video_note.assert_not_awaited()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg and ffprobe are needed for real media conversion")
    async def test_real_video_note_conversion_with_and_without_audio(self):
        for dimensions, with_audio in (("640x360", True), ("360x640", False)):
            with self.subTest(dimensions=dimensions, audio=with_audio):
                source = self.root / f"source-{dimensions}.mp4"
                command = [
                    shutil.which("ffmpeg"), "-y", "-nostdin", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c=red:s={dimensions}:r=25:d=0.4",
                ]
                if with_audio:
                    command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.4"]
                command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
                if with_audio:
                    command += ["-c:a", "aac", "-shortest"]
                command += [str(source)]
                subprocess.run(command, check=True, capture_output=True, timeout=30)
                self.incoming_file(source)
                self.message.video = SimpleNamespace(
                    duration=1, file_id="video-id", file_unique_id="video-unique",
                )
                sent_paths = []

                async def inspect_sent_video(**kwargs):
                    output = Path(kwargs["video_note"].path)
                    sent_paths.append(output)
                    result = subprocess.run([
                        shutil.which("ffprobe"), "-v", "error", "-show_streams",
                        "-show_format", "-of", "json", str(output),
                    ], check=True, capture_output=True, text=True, timeout=30)
                    media = json.loads(result.stdout)
                    video = next(s for s in media["streams"] if s["codec_type"] == "video")
                    self.assertEqual((video["width"], video["height"]), (480, 480))
                    self.assertEqual(video["codec_name"], "h264")
                    self.assertEqual(video["pix_fmt"], "yuv420p")
                    self.assertLessEqual(float(media["format"]["duration"]), 60)
                    audio = [s for s in media["streams"] if s["codec_type"] == "audio"]
                    self.assertEqual(bool(audio), with_audio)
                    if audio:
                        self.assertEqual(audio[0]["codec_name"], "aac")
                    self.assertEqual(kwargs["request_timeout"], 420)

                self.message.answer_video_note.side_effect = inspect_sent_video
                with patch.object(main_handlers, "FFMPEG_EXECUTABLE", shutil.which("ffmpeg")):
                    await main_handlers.handle_video_note_upload(self.message, self.state)
                main_handlers.send_media_error.assert_not_awaited()
                self.assertEqual(len(sent_paths), 1)
                self.assertFalse(sent_paths[0].exists())
                self.assertTrue(source.exists())
        self.assertEqual(self.state.clear.await_count, 2)


if __name__ == "__main__":
    unittest.main()
