import unittest

from cache import TTLCache


class TTLCacheBehaviorTests(unittest.TestCase):
    def test_expired_most_recent_entry_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("early", 1)
        now[0] = 5
        cache.put("late", 2)
        cache.get("early")  # Expiring first does not mean least recently used.
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("late"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_read_does_not_extend_expiry_and_replacement_does(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("key", None)
        now[0] = 9
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 10
        self.assertEqual(cache.get("key", "missing"), "missing")
        cache.put("key", 1)
        now[0] = 19
        cache.put("key", 2)
        now[0] = 20
        self.assertEqual(cache.get("key"), 2)

    def test_delete_reports_only_live_removals(self):
        now = [0]
        cache = TTLCache(2, 1, lambda: now[0])
        cache.put("old", 1)
        now[0] = 1
        self.assertFalse(cache.delete("old"))
        cache.put("new", 2)
        self.assertTrue(cache.delete("new"))
        self.assertFalse(cache.delete("new"))

    def test_invalid_arguments(self):
        for capacity in (0, -1, True, 1.0, "1"):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 1, lambda: 0)
        for ttl in (0, -1, True, float("nan"), float("inf"), float("-inf"), "1"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(1, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_instances_are_independent(self):
        first = TTLCache(1, 10, lambda: 0)
        second = TTLCache(1, 10, lambda: 0)
        first.put("a", 1)
        self.assertEqual(len(second), 0)
        second.put("a", 2)
        self.assertEqual(first.get("a"), 1)
        self.assertEqual(second.get("a"), 2)

    def test_very_large_integer_ttl_is_supported(self):
        ttl = 10 ** 400
        now = [0.5]
        cache = TTLCache(1, ttl, lambda: now[0])
        cache.put("key", 1)
        now[0] = ttl
        self.assertEqual(cache.get("key"), 1)
        now[0] = ttl + 1
        self.assertEqual(len(cache), 0)
