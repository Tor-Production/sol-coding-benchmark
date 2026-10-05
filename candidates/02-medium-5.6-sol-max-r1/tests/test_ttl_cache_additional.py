import unittest

from cache import TTLCache


class ManualClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


class TTLCacheAdditionalTests(unittest.TestCase):
    def test_constructor_validation(self):
        for capacity in (0, -1, 1.0, True, None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 1, lambda: 0)

        for ttl in (0, -1, float("inf"), float("-inf"), float("nan"), True, "1", None):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(1, ttl, lambda: 0)

        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_get_does_not_extend_ttl(self):
        clock = ManualClock()
        cache = TTLCache(1, 10, clock)
        cache.put("key", "value")

        clock.now = 9
        self.assertEqual(cache.get("key"), "value")
        clock.now = 10
        self.assertEqual(cache.get("key", "expired"), "expired")

    def test_replacing_value_resets_ttl_and_recency(self):
        clock = ManualClock()
        cache = TTLCache(2, 10, clock)
        cache.put("a", 1)
        cache.put("b", 2)

        clock.now = 9
        cache.put("a", 3)
        cache.put("c", 4)

        self.assertEqual(cache.get("a"), 3)
        self.assertEqual(cache.get("b", "missing"), "missing")
        clock.now = 19
        self.assertEqual(cache.get("a", "expired"), "expired")

    def test_expired_mru_does_not_evict_live_lru(self):
        clock = ManualClock()
        cache = TTLCache(2, 10, clock)
        cache.put("expires-first", 1)

        clock.now = 5
        cache.put("still-live", 2)
        clock.now = 9
        cache.get("expires-first")

        clock.now = 10
        cache.put("new", 3)

        self.assertEqual(cache.get("still-live"), 2)
        self.assertEqual(cache.get("new"), 3)

    def test_none_value_delete_and_live_length(self):
        clock = ManualClock()
        cache = TTLCache(3, 10, clock)
        cache.put("none", None)
        cache.put("expired", 2)

        self.assertIsNone(cache.get("none", "missing"))
        clock.now = 10
        self.assertFalse(cache.delete("expired"))
        self.assertEqual(len(cache), 0)

        clock.now = 11
        cache.put("live", 3)
        self.assertTrue(cache.delete("live"))
        self.assertFalse(cache.delete("live"))

    def test_instances_do_not_share_entries(self):
        first = TTLCache(1, 10, lambda: 0)
        second = TTLCache(1, 10, lambda: 0)
        first.put("key", "first")

        self.assertEqual(first.get("key"), "first")
        self.assertIsNone(second.get("key"))


if __name__ == "__main__":
    unittest.main()
