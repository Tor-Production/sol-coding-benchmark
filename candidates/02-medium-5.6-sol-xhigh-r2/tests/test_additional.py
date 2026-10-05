import unittest

from cache import TTLCache


class ManualClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


class AdditionalTests(unittest.TestCase):
    def test_expired_mru_does_not_evict_live_lru(self):
        clock = ManualClock()
        cache = TTLCache(2, 10, clock)
        cache.put("expired", 1)
        clock.now = 5
        cache.put("live", 2)
        clock.now = 9
        self.assertEqual(cache.get("expired"), 1)

        clock.now = 10
        cache.put("new", 3)

        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_get_does_not_extend_ttl_and_none_is_a_value(self):
        clock = ManualClock()
        cache = TTLCache(1, 10, clock)
        cache.put("key", None)
        clock.now = 9
        self.assertIsNone(cache.get("key", "missing"))
        clock.now = 10
        self.assertEqual(cache.get("key", "missing"), "missing")

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

    def test_replacement_resets_expiry_and_recency(self):
        clock = ManualClock()
        cache = TTLCache(2, 10, clock)
        cache.put("a", 1)
        clock.now = 5
        cache.put("b", 2)
        cache.put("a", 3)
        cache.put("c", 4)

        self.assertEqual(cache.get("a"), 3)
        self.assertEqual(cache.get("b", "missing"), "missing")
        clock.now = 15
        self.assertEqual(cache.get("a", "missing"), "missing")

    def test_instances_do_not_share_entries(self):
        first = TTLCache(1, 10, lambda: 0)
        second = TTLCache(1, 10, lambda: 0)
        first.put("key", "value")
        self.assertEqual(second.get("key", "missing"), "missing")

    def test_constructor_validation(self):
        invalid_capacities = (True, False, 0, -1, 1.0, "1", None)
        for capacity in invalid_capacities:
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 1, lambda: 0)

        invalid_ttls = (True, False, 0, -1, float("inf"), float("-inf"), float("nan"), "1", None)
        for ttl in invalid_ttls:
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(1, ttl, lambda: 0)

        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

        # Integers are finite regardless of whether they fit in a float.
        TTLCache(1, 10**400, lambda: 0)


if __name__ == "__main__":
    unittest.main()
