import unittest

from cache import TTLCache


class CacheEdgeTests(unittest.TestCase):
    def test_expired_mru_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("old", 1)
        now[0] = 5
        cache.put("live", 2)
        cache.get("old")  # The first entry is now most recently used.
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertIsNone(cache.get("old"))

    def test_replacement_resets_ttl_but_read_does_not(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("key", None)
        now[0] = 9
        self.assertIsNone(cache.get("key", "missing"))
        cache.put("key", 2)
        now[0] = 18
        self.assertEqual(cache.get("key"), 2)
        now[0] = 19
        self.assertEqual(cache.get("key", "missing"), "missing")
        self.assertEqual(len(cache), 0)
        self.assertFalse(cache.delete("key"))

    def test_delete_reports_only_live_entries(self):
        now = [0]
        cache = TTLCache(1, 1, lambda: now[0])
        cache.put("key", None)
        self.assertTrue(cache.delete("key"))
        cache.put("key", 1)
        now[0] = 1
        self.assertFalse(cache.delete("key"))

    def test_invalid_arguments(self):
        for capacity in (0, -1, True, 1.5, "2"):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 1, lambda: 0)
        for ttl in (0, -1, True, float("nan"), float("inf"), "1"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(1, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)
