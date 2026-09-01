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

    def test_russian_apple_title_and_artist_are_parsed(self):
        title, artist = music_downloader._split_catalog_title(
            "Песня «Плачут Небеса (feat. Доминик Джокер)» "
            "(OG Buda & Егор Крид) в Apple Music",
            None,
            "music.apple.com",
        )

        self.assertEqual(title, "Плачут Небеса (feat. Доминик Джокер)")
        self.assertEqual(artist, "OG Buda & Егор Крид")

    def test_apple_track_id_handles_song_and_album_track_links(self):
        self.assertEqual(
            music_downloader._apple_track_id(
                "https://music.apple.com/ru/song/1724932498"
            ),
            "1724932498",
        )
        self.assertEqual(
            music_downloader._apple_track_id(
                "https://music.apple.com/ru/song/track/1724932498"
            ),
            "1724932498",
        )
        self.assertEqual(
            music_downloader._apple_track_id(
                "https://music.apple.com/ru/album/album/1724932497?i=1724932498"
            ),
            "1724932498",
        )
        self.assertIsNone(
            music_downloader._apple_track_id(
                "https://music.apple.com/ru/album/album/1724932497"
            )
        )

    def test_itunes_lookup_chooses_the_requested_track(self):
        metadata = music_downloader._metadata_from_itunes_payload(
            {
                "results": [
                    {
                        "trackId": 1,
                        "trackName": "Wrong",
                        "artistName": "Wrong Artist",
                    },
                    {
                        "trackId": 1724932498,
                        "trackName": "Плачут Небеса (feat. Доминик Джокер)",
                        "artistName": "OG BUDA & Egor Kreed",
                        "collectionId": 1724932497,
                        "collectionName": "Плачут Небеса - Single",
                        "artworkUrl100": "https://example.com/100x100bb.jpg",
                    },
                ]
            },
            "1724932498",
        )

        self.assertEqual(
            metadata["title"],
            "Плачут Небеса (feat. Доминик Джокер)",
        )
        self.assertEqual(metadata["artist"], "OG BUDA & Egor Kreed")
        self.assertEqual(
            metadata["album_url"],
            "https://itunes.apple.com/lookup?id=1724932497&entity=song&limit=200",
        )

    def test_provider_payloads_never_fall_back_to_a_different_track_id(self):
        self.assertIsNone(
            music_downloader._metadata_from_yandex_payload(
                {
                    "result": [{
                        "id": 1,
                        "title": "Другой трек",
                        "durationMs": 1000,
                        "artists": [{"name": "Другой артист"}],
                        "albums": [{"id": 2, "title": "Album"}],
                    }]
                },
                "38072589",
            )
        )
        self.assertIsNone(
            music_downloader._metadata_from_itunes_payload(
                {
                    "results": [{
                        "trackId": 1,
                        "trackName": "Wrong",
                        "artistName": "Wrong Artist",
                    }]
                },
                "1724932498",
            )
        )

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

    def test_service_placeholder_metadata_is_rejected(self):
        self.assertFalse(
            music_downloader._metadata_is_usable(
                {
                    "title": "Яндекс Музыка",
                    "artist": "собираем музыку для вас",
                }
            )
        )
        self.assertFalse(
            music_downloader._metadata_is_usable(
                {"title": "VK Музыка", "artist": None}
            )
        )

    def test_yandex_track_payload_returns_exact_metadata(self):
        metadata = music_downloader._metadata_from_yandex_payload(
            {
                "result": [{
                    "title": "Паук",
                    "artists": [{"name": "oracle"}],
                    "coverUri": "avatars.yandex.net/get-music-content/123/%%",
                    "albums": [{"id": 41769045, "title": "Album"}],
                }]
            }
        )

        self.assertEqual(metadata["title"], "Паук")
        self.assertEqual(metadata["artist"], "oracle")
        self.assertEqual(
            metadata["thumbnail"],
            "https://avatars.yandex.net/get-music-content/123/1000x1000",
        )
        self.assertEqual(
            metadata["album_url"],
            "https://music.yandex.ru/album/41769045",
        )

    def test_yandex_album_payload_finds_the_requested_nested_track(self):
        metadata = music_downloader._metadata_from_yandex_payload(
            {
                "volumes": [[
                    {
                        "id": 1,
                        "title": "Другой трек",
                        "durationMs": 1000,
                        "artists": [{"name": "Другой артист"}],
                        "albums": [{"id": 4847824, "title": "Album"}],
                    },
                    {
                        "id": 38072589,
                        "title": "Нужный трек",
                        "durationMs": 2000,
                        "artists": [{"name": "Нужный артист"}],
                        "albums": [{"id": 4847824, "title": "Album"}],
                    },
                ]]
            },
            "38072589",
        )

        self.assertEqual(metadata["title"], "Нужный трек")
        self.assertEqual(metadata["artist"], "Нужный артист")

    def test_vk_audio_reference_keeps_access_hash(self):
        self.assertEqual(
            music_downloader._vk_audio_reference(
                "https://vk.ru/audio309568744_456240004_5f5df67fe30ddda104"
            ),
            "309568744_456240004_5f5df67fe30ddda104",
        )

    def test_yandex_and_vk_are_catalog_references(self):
        self.assertTrue(
            music_downloader._is_catalog_reference(
                "https://music.yandex.ru/album/41769045/track/150686486"
            )
        )
        self.assertTrue(
            music_downloader._is_catalog_reference(
                "https://vk.ru/audio309568744_456240004_5f5df67fe30ddda104"
            )
        )


class YandexMetadataFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_ajax_track_entries_is_used_after_api_451(self):
        calls = []

        class FakeResponse:
            def __init__(self, payload=None, error=None):
                self.payload = payload
                self.error = error

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            def raise_for_status(self):
                if self.error:
                    raise self.error

            async def json(self, content_type=None):
                return self.payload

        class FakeSession:
            def __init__(self, *args, **kwargs):
                self.responses = [
                    FakeResponse(error=RuntimeError("451")),
                    FakeResponse(payload=[{
                        "id": 38072589,
                        "title": "Нужный трек",
                        "durationMs": 2000,
                        "artists": [{"name": "Нужный артист"}],
                        "albums": [{"id": 4847824, "title": "Album"}],
                    }]),
                ]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            def get(self, endpoint, **kwargs):
                calls.append((endpoint, kwargs))
                return self.responses.pop(0)

        with patch.object(
            music_downloader.aiohttp,
            "ClientSession",
            FakeSession,
        ):
            metadata = await music_downloader._fetch_yandex_track_metadata(
                "https://music.yandex.ru/album/4847824/track/38072589"
            )

        self.assertEqual(metadata["title"], "Нужный трек")
        self.assertIn("/handlers/track-entries.jsx", calls[1][0])
        self.assertEqual(calls[1][1]["params"]["entries"], "38072589")
        self.assertEqual(
            calls[1][1]["headers"]["X-Requested-With"],
            "XMLHttpRequest",
        )


class PublicCatalogueSearchTests(unittest.TestCase):
    def test_yandex_search_item_preserves_album_metadata(self):
        entry = music_downloader._yandex_search_entry({
            "id": 150686486,
            "title": "Паук",
            "durationMs": 125000,
            "artists": [{"name": "oracle"}],
            "albums": [{"id": 41769045, "title": "Album"}],
            "coverUri": "avatars.yandex.net/get-music-content/123/%%",
        })

        self.assertEqual(entry["artist"], "oracle")
        self.assertEqual(entry["duration"], 125)
        self.assertEqual(
            entry["album_url"],
            "https://music.yandex.ru/album/41769045",
        )

    def test_deezer_search_item_has_album_tracks_endpoint(self):
        entry = music_downloader._deezer_search_entry({
            "id": 10,
            "title": "Track",
            "duration": 180,
            "link": "https://www.deezer.com/track/10",
            "artist": {"name": "Artist"},
            "album": {
                "id": 20,
                "title": "Album",
                "cover_big": "https://example.com/cover.jpg",
            },
        })

        self.assertEqual(
            entry["album_url"],
            "https://api.deezer.com/album/20/tracks",
        )

    def test_itunes_search_item_has_downloadable_catalog_reference(self):
        entry = music_downloader._itunes_search_entry({
            "trackId": 10,
            "trackName": "Track",
            "artistName": "Artist",
            "trackViewUrl": "https://music.apple.com/ru/album/album/20?i=10",
            "collectionId": 20,
            "collectionName": "Album",
            "trackTimeMillis": 181000,
            "artworkUrl100": "https://example.com/100x100bb.jpg",
        })

        self.assertEqual(entry["duration"], 181)
        self.assertEqual(
            entry["album_url"],
            "https://itunes.apple.com/lookup?id=20&entity=song&limit=200",
        )


class DownloadFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_yandex_uses_exact_metadata_and_tries_multiple_candidates(self):
        calls = []

        async def fake_download(target, temp_dir, use_cookies):
            calls.append((target, use_cookies))
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
                    "_resolve_catalog_metadata",
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
        self.assertEqual(calls[0][0], "https://soundcloud.com/artist/blocked")
        self.assertEqual(calls[1][0], "https://soundcloud.com/artist/working")
        self.assertFalse(calls[0][1])
        self.assertFalse(calls[1][1])

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

    async def test_low_confidence_candidate_is_never_downloaded(self):
        download = AsyncMock()
        with (
            patch.object(music_downloader, "SEARCH_SOURCES", ("scsearch",)),
            patch.object(
                music_downloader,
                "_search_source",
                AsyncMock(return_value=[{
                    "title": "Completely Different Song",
                    "artist": "Another Artist",
                    "webpage_url": "https://soundcloud.com/another/wrong",
                }]),
            ),
            patch.object(music_downloader, "_download_info", download),
            patch.dict(os.environ, {"AUDIO_FALLBACK_MIN_MATCH": "0.78"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "случайный трек"):
                await music_downloader._download_catalog_match(
                    {"title": "Паук", "artist": "oracle"},
                    "unused",
                )

        download.assert_not_awaited()

    async def test_vk_exact_api_url_is_preferred_over_search(self):
        fallback = AsyncMock()
        metadata = {
            "title": "Track",
            "artist": "Artist",
            "thumbnail": None,
            "download_url": "https://cs.example/exact.mp3",
        }

        async def fake_download(target, temp_dir, use_cookies):
            self.assertEqual(target, "https://cs.example/exact.mp3")
            audio_path = os.path.join(temp_dir, "exact.mp3")
            with open(audio_path, "wb") as audio_file:
                audio_file.write(b"test audio")
            return {"id": "exact", "title": "Track", "artist": "Artist"}

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(music_downloader, "has_ffmpeg", return_value=True),
                patch.object(
                    music_downloader,
                    "_resolve_catalog_metadata",
                    AsyncMock(return_value=metadata),
                ),
                patch.object(music_downloader, "_download_info", side_effect=fake_download),
                patch.object(music_downloader, "_download_catalog_match", fallback),
                patch.object(
                    music_downloader,
                    "_download_thumbnail",
                    AsyncMock(return_value=None),
                ),
            ):
                result = await music_downloader.download_from_url(
                    "https://vk.ru/audio1_2_hash",
                    temp_dir,
                )

        self.assertTrue(result["success"])
        self.assertEqual(result["title"], "Track")
        self.assertEqual(result["artist"], "Artist")
        fallback.assert_not_awaited()

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
                (
                    "scsearch",
                    "yandexsearch",
                    "vksearch",
                    "deezersearch",
                    "itunessearch",
                    "ytsearch",
                ),
            )

    def test_public_catalogues_extend_legacy_source_configuration(self):
        with patch.dict(
            os.environ,
            {
                "AUDIO_SEARCH_SOURCES": "scsearch,vksearch,ytsearch",
                "AUDIO_ENABLE_PUBLIC_CATALOGS": "true",
            },
        ):
            self.assertEqual(
                music_downloader._configured_search_sources(),
                (
                    "scsearch",
                    "yandexsearch",
                    "vksearch",
                    "deezersearch",
                    "itunessearch",
                    "ytsearch",
                ),
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
