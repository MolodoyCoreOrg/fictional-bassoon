import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from utils import music_downloader


class CatalogueMetadataTests(unittest.TestCase):
    def test_spotify_title_and_artist_are_parsed(self):
        title, artist = music_downloader._split_catalog_title(
            "Track Name - song and lyrics by Artist Name | Spotify",
            None,
            "open.spotify.com",
        )

        self.assertEqual(title, "Track Name")
        self.assertEqual(artist, "Artist Name")

    def test_vk_artist_and_title_are_parsed(self):
        title, artist = music_downloader._split_catalog_title(
            "Artist Name — Track Name",
            None,
            "vk.com",
        )

        self.assertEqual(title, "Track Name")
        self.assertEqual(artist, "Artist Name")

    def test_yandex_does_not_receive_shared_cookies(self):
        self.assertFalse(
            music_downloader._uses_site_cookies(
                "https://music.yandex.ru/album/41769045/track/150686486"
            )
        )


class DownloadFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_yandex_bool_error_falls_back_across_catalogue_sources(self):
        calls = []

        async def fake_download(target, temp_dir, use_cookies):
            calls.append((target, use_cookies))
            if len(calls) < 3:
                raise TypeError("source unavailable")

            audio_path = os.path.join(temp_dir, "matched.mp3")
            with open(audio_path, "wb") as audio_file:
                audio_file.write(b"test audio")
            return {
                "id": "matched",
                "title": "Matched video title",
                "uploader": "Matched uploader",
            }

        metadata = {
            "title": "Track Name",
            "artist": "Artist Name",
            "thumbnail": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(music_downloader, "has_ffmpeg", return_value=True),
                patch.object(
                    music_downloader,
                    "_resolve_catalog_metadata",
                    AsyncMock(return_value=metadata),
                ),
                patch.object(music_downloader, "SEARCH_SOURCES", ("scsearch", "ytsearch")),
                patch.object(
                    music_downloader,
                    "_download_info",
                    side_effect=fake_download,
                ),
                patch.object(
                    music_downloader,
                    "_download_thumbnail",
                    AsyncMock(return_value=None),
                ),
            ):
                result = await music_downloader.download_from_url(
                    "https://music.yandex.ru/album/41769045/track/150686486",
                    temp_dir,
                )

        self.assertTrue(result["success"])
        self.assertEqual(result["title"], "Track Name")
        self.assertEqual(result["artist"], "Artist Name")
        self.assertFalse(calls[0][1])
        self.assertTrue(calls[1][0].startswith("scsearch1:Artist Name - Track Name"))
        self.assertFalse(calls[1][1])
        self.assertTrue(calls[2][0].startswith("ytsearch1:Artist Name - Track Name"))
        self.assertTrue(calls[2][1])

    def test_soundcloud_is_the_default_catalogue_source(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUDIO_SEARCH_SOURCES", None)
            self.assertEqual(
                music_downloader._configured_search_sources(),
                ("scsearch", "ytsearch"),
            )


if __name__ == "__main__":
    unittest.main()
