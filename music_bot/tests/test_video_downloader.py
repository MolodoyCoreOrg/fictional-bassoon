import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from utils import video_downloader


class YouTubeVideoProfileTests(unittest.TestCase):
    def test_youtube_profiles_include_hls_and_cookie_free_fallbacks(self):
        profiles = video_downloader._youtube_video_download_profiles(
            "https://www.youtube.com/watch?v=example"
        )

        self.assertEqual([profile["_label"] for profile in profiles], [
            "default",
            "web_safari-hls",
            "android_vr-cookie-free",
        ])
        self.assertTrue(profiles[1]["_prefer_hls"])
        self.assertEqual(
            profiles[1]["extractor_args"]["youtube"]["player_client"],
            ["web_safari"],
        )
        self.assertFalse(profiles[2]["_use_cookies"])
        self.assertEqual(
            profiles[2]["extractor_args"]["youtube"]["player_client"],
            ["android_vr"],
        )

    def test_hls_profile_keeps_provider_token_configuration(self):
        anti_block_opts = {
            "extractor_args": {
                "youtube": {
                    "player_client": ["mweb"],
                    "po_token": ["mweb.gvs+token"],
                },
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://bgutil-provider:4416"],
                },
            },
        }
        profile = video_downloader._youtube_video_download_profiles(
            "https://youtu.be/example"
        )[1]

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
            opts["extractor_args"]["youtube"]["po_token"],
            ["mweb.gvs+token"],
        )
        self.assertEqual(
            opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"],
            ["http://bgutil-provider:4416"],
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
    async def test_download_reaches_cookie_free_profile_after_403(self):
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
        self.assertEqual(cookie_calls, [True, True, False])
        self.assertEqual(
            calls[1]["extractor_args"]["youtube"]["player_client"],
            ["web_safari"],
        )
        self.assertIn("protocol^=m3u8", calls[1]["format"])
        self.assertEqual(
            calls[2]["extractor_args"]["youtube"]["player_client"],
            ["android_vr"],
        )

    async def test_all_403_profiles_return_actionable_error(self):
        error = RuntimeError(
            "ERROR: unable to download video data: HTTP Error 403: Forbidden"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
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
        self.assertEqual(extract.call_count, 3)
        self.assertIn("PO-token provider", result["error"])
        self.assertIn("YOUTUBE_POT_PROVIDER_URL", result["error"])


if __name__ == "__main__":
    unittest.main()
