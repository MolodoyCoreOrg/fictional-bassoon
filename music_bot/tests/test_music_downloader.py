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

    def test_catalog_search_url_round_trips_unicode_metadata(self):
        url = music_downloader._catalog_search_url("Трек & ремикс", "Артист")
        self.assertEqual(
            music_downloader._catalog_search_metadata(url),
            {
                "title": "Трек & ремикс",
                "artist": "Артист",
                "thumbnail": None,
            },
        )

    def test_yandex_does_not_receive_shared_cookies(self):
        self.assertFalse(
            music_downloader._uses_site_cookies(
                "https://music.yandex.ru/album/41769045/track/150686486"
            )
        )


class DownloadFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_direct_result_tries_multiple_soundcloud_candidates(self):
        calls = []

        async def fake_download(target, temp_dir, use_cookies):
            calls.append((target, use_cookies))
            if target.startswith("https://music.yandex.ru/"):
                return {}
            if target.endswith("/blocked"):
                raise RuntimeError("HTTP Error 403: Forbidden")

            audio_path = os.path.join(temp_dir, "matched.mp3")
            with open(audio_path, "wb") as audio_file:
                audio_file.write(b"test audio")
            return {
                "id": "matched",
                "title": "Matched video title",
                "uploader": "Matched uploader",
            }

        async def fake_search(prefix, query, limit):
            self.assertEqual(prefix, "scsearch")
            self.assertEqual(query, "Artist Name - Track Name")
            self.assertEqual(limit, 3)
            return [
                {
                    "title": "Track Name",
                    "artist": "Artist Name",
                    "webpage_url": "https://soundcloud.com/artist/blocked",
                },
                {
                    "title": "Track Name",
                    "artist": "Artist Name",
                    "webpage_url": "https://soundcloud.com/artist/working",
                },
            ]

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
                    "_extract_reference_metadata",
                    AsyncMock(return_value=metadata),
                ),
                patch.object(music_downloader, "SEARCH_SOURCES", ("scsearch",)),
                patch.object(music_downloader, "_search_source", side_effect=fake_search),
                patch.object(music_downloader, "_download_info", side_effect=fake_download),
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
        self.assertEqual(calls[1][0], "https://soundcloud.com/artist/blocked")
        self.assertEqual(calls[2][0], "https://soundcloud.com/artist/working")
        self.assertFalse(calls[1][1])
        self.assertFalse(calls[2][1])

    async def test_vk_direct_url_is_used_by_catalogue_fallback(self):
        calls = []

        async def fake_search(prefix, query, limit):
            return [{
                "id": "1_2",
                "title": "Track",
                "artist": "Artist",
                "webpage_url": "https://vk.com/audio1_2",
                "download_url": "https://cs.example/track.mp3",
            }]

        async def fake_download(target, temp_dir, use_cookies):
            calls.append((target, use_cookies))
            return {"id": "1_2", "title": "Track", "artist": "Artist"}

        with (
            patch.object(music_downloader, "SEARCH_SOURCES", ("vksearch",)),
            patch.object(music_downloader, "_search_source", side_effect=fake_search),
            patch.object(music_downloader, "_download_info", side_effect=fake_download),
        ):
            info = await music_downloader._download_catalog_match(
                {"title": "Track", "artist": "Artist"},
                "unused",
            )

        self.assertEqual(info["id"], "1_2")
        self.assertEqual(calls, [("https://cs.example/track.mp3", False)])

    async def test_failed_social_download_uses_cross_provider_metadata_fallback(self):
        calls = []
        metadata = {
            "title": "Track Name",
            "artist": "Artist Name",
            "thumbnail": None,
        }

        async def fake_download(target, temp_dir, use_cookies):
            calls.append(target)
            if "instagram.com" in target:
                raise RuntimeError("HTTP Error 403: Forbidden")
            audio_path = os.path.join(temp_dir, "matched.mp3")
            with open(audio_path, "wb") as audio_file:
                audio_file.write(b"test audio")
            return {"id": "matched", "title": "Track Name", "artist": "Artist Name"}

        async def fake_search(prefix, query, limit):
            return [{
                "title": "Track Name",
                "artist": "Artist Name",
                "webpage_url": "https://soundcloud.com/artist/track",
            }]

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(music_downloader, "has_ffmpeg", return_value=True),
                patch.object(
                    music_downloader,
                    "_extract_reference_metadata",
                    AsyncMock(return_value=metadata),
                ),
                patch.object(music_downloader, "SEARCH_SOURCES", ("scsearch",)),
                patch.object(music_downloader, "_search_source", side_effect=fake_search),
                patch.object(music_downloader, "_download_info", side_effect=fake_download),
                patch.object(
                    music_downloader,
                    "_download_thumbnail",
                    AsyncMock(return_value=None),
                ),
            ):
                result = await music_downloader.download_from_url(
                    "https://www.instagram.com/reel/example/",
                    temp_dir,
                )

        self.assertTrue(result["success"])
        self.assertEqual(
            calls,
            [
                "https://www.instagram.com/reel/example/",
                "https://soundcloud.com/artist/track",
            ],
        )

    def test_all_three_inline_sources_are_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUDIO_SEARCH_SOURCES", None)
            self.assertEqual(
                music_downloader._configured_search_sources(),
                ("scsearch", "vksearch", "ytsearch"),
            )

    def test_youtube_retry_profiles_include_hls_and_cookie_free_client(self):
        profiles = music_downloader._youtube_download_profiles(
            "https://www.youtube.com/watch?v=example"
        )

        self.assertEqual(len(profiles), 3)
        self.assertIn("protocol^=m3u8", profiles[1]["format"])
        self.assertEqual(
            profiles[1]["extractor_args"]["youtube"]["player_client"],
            ["web_safari"],
        )
        self.assertFalse(profiles[2]["_use_cookies"])
        self.assertEqual(
            profiles[2]["extractor_args"]["youtube"]["player_client"],
            ["android_vr"],
        )


class MultiSourceSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_results_are_interleaved_across_soundcloud_vk_and_youtube(self):
        async def fake_search(prefix, query, limit):
            entries = {
                "scsearch": [{
                    "id": "sc-1",
                    "title": "SC Track",
                    "artist": "SC Artist",
                    "webpage_url": "https://soundcloud.com/a/sc",
                }],
                "vksearch": [{
                    "id": "1_2",
                    "title": "VK Track",
                    "artist": "VK Artist",
                    "webpage_url": "https://vk.com/audio1_2",
                    "download_url": "https://cs.example/vk.mp3",
                }],
                "ytsearch": [{
                    "id": "yt-1",
                    "title": "YT Track",
                    "channel": "YT Artist",
                    "webpage_url": "https://www.youtube.com/watch?v=yt-1",
                }],
            }
            return entries[prefix]

        with (
            patch.object(
                music_downloader,
                "SEARCH_SOURCES",
                ("scsearch", "vksearch", "ytsearch"),
            ),
            patch.object(
                music_downloader,
                "_search_source",
                side_effect=fake_search,
            ),
        ):
            results = await music_downloader.search_music("Track", limit=3)

        self.assertEqual(
            [track["source"] for track in results],
            ["sc", "vk", "yt"],
        )
        self.assertEqual(results[1]["download_url"], "https://cs.example/vk.mp3")


if __name__ == "__main__":
    unittest.main()
