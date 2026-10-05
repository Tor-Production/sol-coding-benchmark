import math
import unittest

from cache import TTLCache


class EdgeCaseTests(unittest.TestCase):
    def test_constructor_validation(self):
        for capacity in (True, 0, -1, 1.5, None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 1, lambda: 0)

        for ttl in (True, 0, -1, math.inf, -math.inf, math.nan, None):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(1, ttl, lambda: 0)

        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_none_is_a_stored_value(self):
        cache = TTLCache(1, 10, lambda: 0)
        cache.put("key", None)
        fallback = object()
        self.assertIsNone(cache.get("key", fallback))

    def test_read_does_not_extend_ttl(self):
        now = [0]
        cache = TTLCache(1, 5, lambda: now[0])
        cache.put("key", "value")
        now[0] = 4
        self.assertEqual(cache.get("key"), "value")
        now[0] = 5
        self.assertIsNone(cache.get("key"))

    def test_replacing_resets_ttl_and_recency(self):
        now = [0]
        cache = TTLCache(2, 5, lambda: now[0])
        cache.put("a", 1)
        cache.put("b", 2)
        now[0] = 4
        cache.put("a", 3)
        cache.put("c", 4)

        self.assertEqual(cache.get("a"), 3)
        self.assertEqual(cache.get("b", "missing"), "missing")
        now[0] = 9
        self.assertEqual(cache.get("a", "expired"), "expired")

    def test_expired_mru_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 5, lambda: now[0])
        cache.put("old", 1)
        now[0] = 4
        cache.put("live", 2)
        cache.get("old")  # "old" is now MRU, but it expires first.

        now[0] = 5
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_delete_reports_only_live_removals(self):
        now = [0]
        cache = TTLCache(2, 5, lambda: now[0])
        cache.put("live", 1)
        self.assertTrue(cache.delete("live"))
        self.assertFalse(cache.delete("missing"))

        cache.put("expired", 2)
        now[0] = 5
        self.assertFalse(cache.delete("expired"))
        self.assertEqual(len(cache), 0)

    def test_instances_do_not_share_entries(self):
        first = TTLCache(1, 5, lambda: 0)
        second = TTLCache(1, 5, lambda: 0)
        first.put("key", "value")
        self.assertEqual(second.get("key", "missing"), "missing")


if __name__ == "__main__":
    unittest.main()
