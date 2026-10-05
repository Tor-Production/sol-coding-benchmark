import math
import unittest

from cache import TTLCache


class ManualClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


class ValidationTests(unittest.TestCase):
    def test_rejects_invalid_capacity(self):
        for capacity in (True, False, 0, -1, 1.0, "1", None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 1, lambda: 0)

    def test_rejects_invalid_ttl(self):
        invalid_ttls = (
            True,
            False,
            0,
            -1,
            0.0,
            -1.0,
            math.inf,
            -math.inf,
            math.nan,
            "1",
            None,
        )
        for ttl in invalid_ttls:
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(1, ttl, lambda: 0)

    def test_rejects_non_callable_clock(self):
        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)


class BehaviorTests(unittest.TestCase):
    def test_none_is_a_stored_value(self):
        cache = TTLCache(1, 10, lambda: 0)
        cache.put("key", None)
        self.assertIsNone(cache.get("key", "missing"))
        self.assertEqual(len(cache), 1)

    def test_get_does_not_extend_ttl(self):
        clock = ManualClock()
        cache = TTLCache(1, 10, clock)
        cache.put("key", "value")
        clock.now = 9
        self.assertEqual(cache.get("key"), "value")
        clock.now = 10
        self.assertEqual(cache.get("key", "missing"), "missing")

    def test_replace_resets_ttl_and_recency(self):
        clock = ManualClock()
        cache = TTLCache(2, 10, clock)
        cache.put("a", 1)
        cache.put("b", 2)
        clock.now = 9
        cache.put("a", 3)
        cache.put("c", 4)
        self.assertEqual(cache.get("b", "missing"), "missing")
        self.assertEqual(cache.get("a"), 3)
        clock.now = 19
        self.assertEqual(cache.get("a", "missing"), "missing")

    def test_expired_mru_does_not_evict_live_lru(self):
        clock = ManualClock()
        cache = TTLCache(2, 10, clock)
        cache.put("expires-first", 1)
        clock.now = 5
        cache.put("live-lru", 2)
        cache.get("expires-first")

        clock.now = 10
        cache.put("new", 3)

        self.assertEqual(cache.get("live-lru"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_delete_reports_only_live_entries(self):
        clock = ManualClock()
        cache = TTLCache(2, 10, clock)
        cache.put("live", 1)
        self.assertTrue(cache.delete("live"))
        self.assertFalse(cache.delete("live"))

        cache.put("expired", 2)
        clock.now = 10
        self.assertFalse(cache.delete("expired"))
        self.assertEqual(len(cache), 0)

    def test_instances_do_not_share_entries(self):
        first = TTLCache(1, 10, lambda: 0)
        second = TTLCache(1, 10, lambda: 0)
        first.put("key", "value")
        self.assertEqual(second.get("key", "missing"), "missing")


if __name__ == "__main__":
    unittest.main()
