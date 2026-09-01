import os
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from handlers import main_handlers
from utils import music_downloader


def soundcloud_set_info(track_count: int = 7) -> dict:
    return {
        "_type": "playlist",
        "extractor_key": "SoundcloudSet",
        "title": "TAGOZACZIYA Vol.1",
        "uploader": "GUCHIGENGOVO",
        "playlist_count": track_count,
        "webpage_url": "https://soundcloud.com/guchigengovo/sets/tagozacziya-vol-1",
        "entries": [
            {
                "id": str(index),
                "title": f"Track {index}",
                "uploader": "GUCHIGENGOVO",
                "webpage_url": f"https://soundcloud.com/guchigengovo/track-{index}",
                "playlist_index": index,
                "duration": 120 + index,
            }
            for index in range(1, track_count + 1)
        ],
    }


class SoundCloudCollectionMetadataTests(unittest.TestCase):
    def test_set_and_short_links_are_detected(self):
        self.assertTrue(
            music_downloader.is_soundcloud_collection_url(
                "https://soundcloud.com/guchigengovo/sets/tagozacziya-vol-1"
            )
        )
        self.assertTrue(
            music_downloader.is_soundcloud_collection_url(
                "https://on.soundcloud.com/example"
            )
        )
        self.assertTrue(
            music_downloader.is_soundcloud_collection_url(
                "https://m.soundcloud.com/guchigengovo/sets/tagozacziya-vol-1"
            )
        )
        self.assertFalse(
            music_downloader.is_soundcloud_collection_url(
                "https://soundcloud.com/guchigengovo/single-track"
            )
        )

    def test_platform_order_is_preserved(self):
        collection = music_downloader._soundcloud_collection_from_info(
            soundcloud_set_info(7),
            source_url="https://soundcloud.com/guchigengovo/sets/tagozacziya-vol-1",
            limit=100,
        )

        self.assertEqual(collection["title"], "TAGOZACZIYA Vol.1")
        self.assertEqual(collection["artist"], "GUCHIGENGOVO")
        self.assertEqual(collection["total"], 7)
        self.assertFalse(collection["truncated"])
        self.assertEqual(
            [track["title"] for track in collection["tracks"]],
            [f"Track {index}" for index in range(1, 8)],
        )
        self.assertEqual(
            [track["track_number"] for track in collection["tracks"]],
            list(range(1, 8)),
        )

    def test_oversized_set_is_reported_without_silent_partial_delivery(self):
        collection = music_downloader._soundcloud_collection_from_info(
            soundcloud_set_info(11),
            source_url="https://soundcloud.com/example/sets/large",
            limit=10,
        )

        self.assertTrue(collection["truncated"])
        self.assertEqual(collection["total"], 11)
        self.assertEqual(len(collection["tracks"]), 10)


class SoundCloudCollectionExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_yt_dlp_playlist_mode_is_enabled(self):
        info = soundcloud_set_info(7)
        with (
            patch.object(music_downloader.yt_dlp, "YoutubeDL") as ydl_class,
            patch.object(
                music_downloader,
                "_extract_info_sync",
                return_value=info,
            ),
        ):
            collection = await music_downloader.get_soundcloud_collection(
                "https://soundcloud.com/guchigengovo/sets/tagozacziya-vol-1"
            )

        options = ydl_class.call_args.args[0]
        self.assertFalse(options["noplaylist"])
        self.assertFalse(options["extract_flat"])
        self.assertTrue(options["skip_download"])
        self.assertEqual(options["playlistend"], 101)
        self.assertEqual(len(collection["tracks"]), 7)


class TelegramAudioGroupBatchTests(unittest.TestCase):
    def test_seven_tracks_stay_in_one_group(self):
        tracks = [{"index": index} for index in range(1, 8)]
        batches = main_handlers._audio_group_batches(tracks)

        self.assertEqual(len(batches), 1)
        self.assertEqual(
            [item["index"] for item in batches[0]],
            list(range(1, 8)),
        )

    def test_large_collection_is_split_by_ten_without_reordering(self):
        tracks = [{"index": index} for index in range(1, 22)]
        batches = main_handlers._audio_group_batches(tracks)

        self.assertEqual([len(batch) for batch in batches], [10, 10, 1])
        self.assertEqual(
            [item["index"] for batch in batches for item in batch],
            list(range(1, 22)),
        )


if __name__ == "__main__":
    unittest.main()
