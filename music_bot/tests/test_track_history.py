import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from utils import track_history


class TrackHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_is_personal_searchable_and_newest_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "history.sqlite3"
            with patch.object(track_history, "HISTORY_DB_PATH", database):
                await track_history.remember_audio_reference(
                    user_id=1,
                    file_id="file-1",
                    file_unique_id="unique-1",
                    title="First Track",
                    artist="Artist",
                    duration=100,
                    source_url="https://soundcloud.com/artist/first",
                    cache_globally=True,
                )
                await track_history.remember_audio_reference(
                    user_id=1,
                    file_id="file-2",
                    file_unique_id="unique-2",
                    title="Second Track",
                    artist="Another",
                    duration=120,
                    source_url="https://www.youtube.com/watch?v=second",
                )

                history = await track_history.get_user_history(1)
                self.assertEqual(
                    [track["file_id"] for track in history],
                    ["file-2", "file-1"],
                )

                matches = await track_history.get_user_history(1, query="artist")
                self.assertEqual([track["file_id"] for track in matches], ["file-1"])
                self.assertEqual(await track_history.get_user_history(2), [])

                cached = await track_history.get_cached_audio(
                    "https://soundcloud.com/artist/first"
                )
                self.assertEqual(cached["file_id"], "file-1")


if __name__ == "__main__":
    unittest.main()
