import unittest
from unittest.mock import patch

from utils import media_request_cache


class MediaRequestCacheTests(unittest.TestCase):
    def setUp(self):
        media_request_cache._requests.clear()

    def tearDown(self):
        media_request_cache._requests.clear()

    def test_each_keyboard_keeps_its_own_url(self):
        first_id = media_request_cache.save_media_request("https://example.com/first", "First")
        second_id = media_request_cache.save_media_request("https://example.com/second", "Second")

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(
            media_request_cache.get_media_request(first_id).url,
            "https://example.com/first",
        )
        self.assertEqual(
            media_request_cache.get_media_request(second_id).url,
            "https://example.com/second",
        )

    def test_cache_is_bounded_and_evicts_the_oldest_request(self):
        with patch.object(media_request_cache, "MAX_MEDIA_REQUESTS", 2):
            first_id = media_request_cache.save_media_request("https://example.com/first")
            second_id = media_request_cache.save_media_request("https://example.com/second")
            third_id = media_request_cache.save_media_request("https://example.com/third")

        self.assertIsNone(media_request_cache.get_media_request(first_id))
        self.assertIsNotNone(media_request_cache.get_media_request(second_id))
        self.assertIsNotNone(media_request_cache.get_media_request(third_id))


if __name__ == "__main__":
    unittest.main()
