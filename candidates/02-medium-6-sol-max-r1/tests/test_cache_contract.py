import math
import unittest

from cache import TTLCache


class CacheContractTests(unittest.TestCase):
    def test_constructor_rejects_invalid_arguments(self):
        for capacity in (0, -1, True, 1.5, "2", None):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 1, lambda: 0)
        for ttl in (0, -1, True, math.inf, -math.inf, math.nan, "1", None):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(1, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_read_does_not_extend_ttl_and_expiry_is_inclusive(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("key", None)
        now[0] = 9
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 10
        self.assertEqual(cache.get("key", "missing"), "missing")
        self.assertEqual(len(cache), 0)

    def test_replacement_resets_expiry_and_recency(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("a", 1)
        cache.put("b", 2)
        now[0] = 5
        cache.put("a", 3)
        cache.put("c", 4)
        self.assertEqual(cache.get("b", "missing"), "missing")
        now[0] = 10
        self.assertEqual(cache.get("a"), 3)
        now[0] = 15
        self.assertEqual(cache.get("a", "missing"), "missing")

    def test_expired_recent_key_does_not_evict_live_oldest(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("expired", 1)
        now[0] = 5
        cache.put("live", 2)
        cache.get("expired")  # The expired key becomes most recently used.
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_delete_and_length_only_count_live_entries(self):
        now = [0]
        cache = TTLCache(2, 5, lambda: now[0])
        cache.put("old", 1)
        now[0] = 2
        cache.put("new", 2)
        now[0] = 5
        self.assertFalse(cache.delete("old"))
        self.assertEqual(len(cache), 1)
        self.assertTrue(cache.delete("new"))
        self.assertFalse(cache.delete("new"))
        self.assertEqual(len(cache), 0)

    def test_instances_have_separate_entries(self):
        first = TTLCache(1, 10, lambda: 0)
        second = TTLCache(1, 10, lambda: 0)
        first.put("a", 1)
        self.assertEqual(second.get("a", "missing"), "missing")
        second.put("b", 2)
        self.assertEqual(first.get("a"), 1)

    def test_very_large_integer_ttl_is_supported(self):
        now = [0.0]
        ttl = 10 ** 1000
        cache = TTLCache(1, ttl, lambda: now[0])
        cache.put("a", 1)
        now[0] = ttl - 1
        self.assertEqual(cache.get("a"), 1)
        now[0] = ttl
        self.assertIsNone(cache.get("a"))

    def test_float_ttl_with_large_integer_clock(self):
        start = 10 ** 1000
        now = [start]
        cache = TTLCache(1, 1.5, lambda: now[0])
        cache.put("a", 1)
        now[0] = start + 1
        self.assertEqual(cache.get("a"), 1)
        now[0] = start + 2
        self.assertIsNone(cache.get("a"))


if __name__ == "__main__":
    unittest.main()
