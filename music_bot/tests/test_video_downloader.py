import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from utils import config, video_downloader


class YouTubeVideoProfileTests(unittest.TestCase):
    def _profiles(self, provider_url=""):
        with patch.dict(
            os.environ,
            {"YOUTUBE_POT_PROVIDER_URL": provider_url},
        ):
            return video_downloader._youtube_video_download_profiles(
                "https://www.youtube.com/watch?v=example"
            )

    def test_youtube_profiles_follow_current_supported_clients(self):
        profiles = self._profiles()

        self.assertEqual([profile["_label"] for profile in profiles], [
            "default",
            "visionos-cookie-free",
            "web_safari-hls",
            "web_embedded-cookie-free",
        ])
        self.assertFalse(profiles[1]["_use_cookies"])
        self.assertEqual(
            profiles[1]["extractor_args"]["youtube"]["player_client"],
            ["visionos"],
        )
        self.assertTrue(profiles[2]["_prefer_hls"])
        self.assertEqual(
            profiles[2]["extractor_args"]["youtube"]["player_client"],
            ["web_safari"],
        )
        self.assertNotIn("android_vr-cookie-free", {
            profile["_label"] for profile in profiles
        })

    def test_provider_adds_isolated_mweb_retry_with_forced_token_fetch(self):
        profiles = self._profiles("http://bgutil-provider:4416")
        mweb = next(
            profile for profile in profiles
            if profile["_label"] == "mweb-po-token"
        )

        self.assertEqual(
            mweb["extractor_args"]["youtube"]["player_client"],
            ["mweb"],
        )
        self.assertEqual(
            mweb["extractor_args"]["youtube"]["fetch_pot"],
            ["always"],
        )

    def test_provider_configuration_does_not_pin_every_request_to_mweb(self):
        with patch.dict(os.environ, {
            "YOUTUBE_POT_PROVIDER_URL": "http://bgutil-provider:4416",
            "YOUTUBE_PLAYER_CLIENT": "",
            "YOUTUBE_PO_TOKEN": "",
            "YOUTUBE_VISITOR_DATA": "",
        }):
            opts = config.get_anti_block_opts()

        self.assertNotIn("youtube", opts["extractor_args"])
        self.assertEqual(
            opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"],
            ["http://bgutil-provider:4416"],
        )

    def test_hls_profile_keeps_provider_configuration(self):
        anti_block_opts = {
            "extractor_args": {
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://bgutil-provider:4416"],
                },
            },
        }
        profile = next(
            item for item in self._profiles("http://bgutil-provider:4416")
            if item["_label"] == "web_safari-hls"
        )

        with (
            patch.object(
                video_downloader,
                "get_anti_block_opts",
                return_value=anti_block_opts,
            ),
            patch.object(video_downloader, "has_ffmpeg", return_value=True),
        ):
            opts = video_downloader._build_video_download_opts(
                "temp",
                "h720",
                profile,
            )

        self.assertIn("protocol^=m3u8", opts["format"])
        self.assertEqual(opts["http_chunk_size"], 8 * 1024 * 1024)
        self.assertEqual(
            opts["extractor_args"]["youtube"]["player_client"],
            ["web_safari"],
        )
        self.assertEqual(
            opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"],
            ["http://bgutil-provider:4416"],
        )



class SocialVideoQualityTests(unittest.TestCase):
    def test_preview_images_are_not_offered_as_video_quality(self):
        self.assertFalse(video_downloader._has_video({
            "width": 160,
            "height": 90,
            "vcodec": "images",
            "acodec": "none",
        }))

    def test_instagram_portrait_uses_real_short_edge_qualities(self):
        info = {
            "width": 1080,
            "height": 1920,
            "formats": [
                {
                    "format_id": "ig-1080",
                    "width": 1080,
                    "height": 1920,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": 8 * 1024 * 1024,
                },
                {
                    "format_id": "ig-720",
                    "width": 720,
                    "height": 1280,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": 4 * 1024 * 1024,
                },
            ],
        }

        choices = video_downloader._build_format_choices(
            info,
            "https://www.instagram.com/reel/example/",
        )

        self.assertEqual(
            [choice["quality_label"] for choice in choices],
            ["1080p", "720p"],
        )
        self.assertEqual(
            [choice["format_id"] for choice in choices],
            ["r1080x1920", "r720x1280"],
        )
        self.assertNotIn("2K", {choice["quality_label"] for choice in choices})
        self.assertNotIn("4K", {choice["quality_label"] for choice in choices})

    def test_standard_labels_work_for_landscape_and_portrait(self):
        self.assertEqual(video_downloader.quality_label(1920, 1080), "1080p")
        self.assertEqual(video_downloader.quality_label(1080, 1920), "1080p")
        self.assertEqual(video_downloader.quality_label(2560, 1440), "2K")
        self.assertEqual(video_downloader.quality_label(1440, 2560), "2K")
        self.assertEqual(video_downloader.quality_label(3840, 2160), "4K")
        self.assertEqual(video_downloader.quality_label(2160, 3840), "4K")

    def test_nonstandard_resolution_is_shown_exactly(self):
        self.assertEqual(
            video_downloader.quality_label(960, 1706),
            "960×1706",
        )

    def test_incomplete_dimensions_are_not_offered_without_safe_size(self):
        choices = video_downloader._build_format_choices(
            {
                "height": 1920,
                "formats": [{
                    "format_id": "ig-unknown-width",
                    "height": 1920,
                    "vcodec": "h264",
                    "acodec": "aac",
                }],
            },
            "https://www.instagram.com/reel/example/",
        )

        self.assertEqual(choices, [])

    def test_qualities_over_telegram_limit_are_hidden(self):
        gib = 1024 * 1024 * 1024
        info = {
            "duration": 3600,
            "formats": [
                {
                    "format_id": "4k",
                    "width": 3840,
                    "height": 2160,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": int(4.7 * gib),
                },
                {
                    "format_id": "1080",
                    "width": 1920,
                    "height": 1080,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": int(1.4 * gib),
                },
                {
                    "format_id": "720",
                    "width": 1280,
                    "height": 720,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": int(0.7 * gib),
                },
            ],
        }

        with patch.object(
            video_downloader,
            "MAX_FILE_SIZE_BYTES",
            2000 * 1024 * 1024,
        ):
            choices = video_downloader._build_format_choices(
                info,
                "https://example.com/video",
            )

        self.assertEqual(
            [choice["quality_label"] for choice in choices],
            ["1080p", "720p"],
        )
        self.assertNotIn("4K", {choice["quality_label"] for choice in choices})

    def test_separate_video_and_audio_sizes_are_added(self):
        info = {
            "duration": 600,
            "formats": [
                {
                    "format_id": "video-1080",
                    "width": 1920,
                    "height": 1080,
                    "vcodec": "h264",
                    "acodec": "none",
                    "filesize": 1900 * 1024 * 1024,
                },
                {
                    "format_id": "audio",
                    "vcodec": "none",
                    "acodec": "aac",
                    "filesize": 150 * 1024 * 1024,
                    "abr": 256,
                },
            ],
        }

        with (
            patch.object(video_downloader, "has_ffmpeg", return_value=True),
            patch.object(
                video_downloader,
                "MAX_FILE_SIZE_BYTES",
                2000 * 1024 * 1024,
            ),
        ):
            choices = video_downloader._build_format_choices(
                info,
                "https://example.com/video",
            )

        self.assertEqual(choices, [])

    def test_quality_is_hidden_when_any_selected_candidate_size_is_unknown(self):
        info = {
            "duration": None,
            "formats": [
                {
                    "format_id": "known-1080",
                    "width": 1920,
                    "height": 1080,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": 500 * 1024 * 1024,
                },
                {
                    "format_id": "unknown-1080",
                    "width": 1920,
                    "height": 1080,
                    "vcodec": "vp9",
                    "acodec": "aac",
                },
            ],
        }

        choices = video_downloader._build_format_choices(
            info,
            "https://example.com/video",
        )

        self.assertEqual(choices, [])

    def test_estimate_matches_separate_stream_selector_fallback_order(self):
        info = {
            "duration": 600,
            "formats": [
                {
                    "format_id": "muxed-1080",
                    "width": 1920,
                    "height": 1080,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": 500 * 1024 * 1024,
                },
                {
                    "format_id": "video-only-720",
                    "width": 1280,
                    "height": 720,
                    "vcodec": "h264",
                    "acodec": "none",
                    "filesize": 1900 * 1024 * 1024,
                },
                {
                    "format_id": "audio-only",
                    "vcodec": "none",
                    "acodec": "aac",
                    "filesize": 150 * 1024 * 1024,
                    "abr": 256,
                },
            ],
        }

        with (
            patch.object(video_downloader, "has_ffmpeg", return_value=True),
            patch.object(
                video_downloader,
                "MAX_FILE_SIZE_BYTES",
                2000 * 1024 * 1024,
            ),
        ):
            choices = video_downloader._build_format_choices(
                info,
                "https://example.com/video",
            )

        self.assertEqual(choices, [])

    def test_bitrate_estimate_filters_large_unknown_filesize(self):
        info = {
            "duration": 7200,
            "formats": [
                {
                    "format_id": "4k",
                    "width": 3840,
                    "height": 2160,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "tbr": 5000,
                },
                {
                    "format_id": "1080",
                    "width": 1920,
                    "height": 1080,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "tbr": 1200,
                },
            ],
        }

        with patch.object(
            video_downloader,
            "MAX_FILE_SIZE_BYTES",
            2000 * 1024 * 1024,
        ):
            choices = video_downloader._build_format_choices(
                info,
                "https://example.com/video",
            )

        self.assertEqual(
            [choice["quality_label"] for choice in choices],
            ["1080p"],
        )

    def test_resolution_selector_limits_both_frame_edges(self):
        with patch.object(video_downloader, "has_ffmpeg", return_value=True):
            selector = video_downloader._resolve_download_format(
                "r1080x1920"
            )

        self.assertIn("[width<=1080][height<=1920]", selector)
        self.assertFalse(selector.endswith("/best"))

    def test_downloaded_quality_uses_selected_video_stream(self):
        info = {
            "width": 2160,
            "height": 3840,
            "requested_formats": [
                {"vcodec": "none", "acodec": "aac"},
                {
                    "vcodec": "h264",
                    "acodec": "none",
                    "width": 1080,
                    "height": 1920,
                },
            ],
        }

        width, height = video_downloader._video_dimensions_from_info(info)

        self.assertEqual((width, height), (1080, 1920))
        self.assertEqual(
            video_downloader.quality_label(width, height),
            "1080p",
        )


class PinterestFallbackTests(unittest.TestCase):
    def test_pin_it_and_pinterest_urls_are_detected(self):
        self.assertTrue(
            video_downloader._is_pinterest_url("https://pin.it/2bQRQVbz4")
        )
        self.assertTrue(
            video_downloader._is_pinterest_url(
                "https://www.pinterest.com/pin/285415695111990909/"
            )
        )
        self.assertFalse(
            video_downloader._is_pinterest_url("https://example.com/pin/123")
        )

    def test_story_pin_video_list_is_extracted_from_embedded_json(self):
        page_html = """
        <script id="__PWS_DATA__" type="application/json">
        {
          "props": {
            "pin": {
              "story_pin_data": {
                "pages": [{
                  "blocks": [{
                    "video": {
                      "video_list": {
                        "V_720P": {
                          "url": "https://v1.pinimg.com/videos/example-720.mp4",
                          "width": 720,
                          "height": 1280
                        },
                        "V_HLSV4": {
                          "url": "https://v1.pinimg.com/videos/example.m3u8",
                          "width": 720,
                          "height": 1280
                        }
                      }
                    }
                  }]
                }]
              }
            }
          }
        }
        </script>
        """

        documents = video_downloader._extract_pinterest_json_documents(page_html)
        formats = video_downloader._pinterest_video_formats(documents)

        self.assertEqual(len(formats), 2)
        self.assertEqual(formats[0]["height"], 1280)
        self.assertEqual(formats[0]["protocol"], "https")
        self.assertEqual(formats[1]["protocol"], "m3u8_native")
        self.assertEqual(
            formats[0]["http_headers"]["Referer"],
            "https://www.pinterest.com/",
        )

    def test_no_formats_error_uses_pinterest_fallback_for_download(self):
        fallback_info = {
            "_type": "video",
            "id": "285415695111990909",
            "title": "Pinterest video",
            "formats": [{
                "format_id": "pinterest-V_720P",
                "url": "https://v1.pinimg.com/videos/example.mp4",
            }],
        }

        class FakeYdl:
            def __init__(self):
                self.processed = None

            def extract_info(self, url, download):
                raise RuntimeError(
                    "ERROR: [Pinterest] 285415695111990909: "
                    "No video formats found!"
                )

            def process_ie_result(self, info, download):
                self.processed = (info, download)
                return info

        fake_ydl = FakeYdl()
        with patch.object(
            video_downloader,
            "_extract_pinterest_fallback_info",
            return_value=fallback_info,
        ) as fallback:
            result = video_downloader._extract_info_with_pinterest_fallback(
                fake_ydl,
                "https://pin.it/2bQRQVbz4",
                download=True,
            )

        self.assertIs(result, fallback_info)
        self.assertEqual(fake_ydl.processed, (fallback_info, True))
        fallback.assert_called_once_with(
            "https://pin.it/2bQRQVbz4",
            fake_ydl,
        )

    def test_non_pinterest_no_formats_error_is_not_swallowed(self):
        class FakeYdl:
            def extract_info(self, url, download):
                raise RuntimeError("No video formats found!")

        with self.assertRaisesRegex(RuntimeError, "No video formats"):
            video_downloader._extract_info_with_pinterest_fallback(
                FakeYdl(),
                "https://example.com/video",
                download=False,
            )


class YouTubeVideoDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_reaches_hls_after_default_and_visionos_fail(self):
        calls = []
        cookie_calls = []

        def fake_extract(url, opts):
            calls.append(opts)
            if len(calls) == 1:
                raise RuntimeError(
                    "ERROR: unable to download video data: HTTP Error 403: Forbidden"
                )
            if len(calls) == 2:
                raise RuntimeError("Requested format is not available")

            output_path = os.path.join(temp_dir, "example.mp4")
            with open(output_path, "wb") as output:
                output.write(b"video")
            return {
                "id": "example",
                "title": "Example",
                "uploader": "Uploader",
                "duration": 10,
                "webpage_url": url,
                "requested_formats": [{"height": 360, "width": 640}],
            }

        def fake_anti_block_opts(use_cookies=True):
            cookie_calls.append(use_cookies)
            return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.dict(
                    os.environ,
                    {"YOUTUBE_POT_PROVIDER_URL": ""},
                ),
                patch.object(
                    video_downloader,
                    "get_anti_block_opts",
                    side_effect=fake_anti_block_opts,
                ),
                patch.object(video_downloader, "has_ffmpeg", return_value=False),
                patch.object(
                    video_downloader,
                    "_extract_video_info_sync",
                    side_effect=fake_extract,
                ),
            ):
                result = await video_downloader.download_video(
                    "https://www.youtube.com/watch?v=example",
                    temp_dir,
                    "h360",
                )

        self.assertTrue(result["success"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(cookie_calls, [True, False, True])
        self.assertEqual(
            calls[1]["extractor_args"]["youtube"]["player_client"],
            ["visionos"],
        )
        self.assertEqual(
            calls[2]["extractor_args"]["youtube"]["player_client"],
            ["web_safari"],
        )
        self.assertIn("protocol^=m3u8", calls[2]["format"])

    async def test_all_403_profiles_return_actionable_error(self):
        error = RuntimeError(
            "ERROR: unable to download video data: HTTP Error 403: Forbidden"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.dict(
                    os.environ,
                    {"YOUTUBE_POT_PROVIDER_URL": ""},
                ),
                patch.object(video_downloader, "get_anti_block_opts", return_value={}),
                patch.object(video_downloader, "has_ffmpeg", return_value=False),
                patch.object(
                    video_downloader,
                    "_extract_video_info_sync",
                    side_effect=error,
                ) as extract,
            ):
                result = await video_downloader.download_video(
                    "https://www.youtube.com/watch?v=example",
                    temp_dir,
                    "h360",
                )

        self.assertFalse(result["success"])
        self.assertEqual(extract.call_count, 4)
        self.assertIn("PO-token provider", result["error"])
        self.assertIn("YOUTUBE_POT_PROVIDER_URL", result["error"])


if __name__ == "__main__":
    unittest.main()
