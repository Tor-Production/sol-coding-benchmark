import math
import unittest

from cache import TTLCache


class FakeClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


class AdditionalTests(unittest.TestCase):
    def test_expired_mru_does_not_evict_live_lru(self):
        clock = FakeClock()
        cache = TTLCache(2, 10, clock)
        cache.put("a", 1)
        clock.now = 1
        cache.put("b", 2)

        clock.now = 5
        cache.put("a", 3)  # Refresh a's TTL.
        clock.now = 6
        self.assertEqual(cache.get("b"), 2)  # Make b MRU without refreshing it.

        clock.now = 11
        cache.put("c", 4)
        self.assertEqual(cache.get("a"), 3)
        self.assertEqual(cache.get("b", "missing"), "missing")
        self.assertEqual(cache.get("c"), 4)

    def test_get_does_not_extend_ttl_and_expiry_is_inclusive(self):
        clock = FakeClock()
        cache = TTLCache(1, 10, clock)
        cache.put("key", "value")
        clock.now = 9
        self.assertEqual(cache.get("key"), "value")
        clock.now = 10
        self.assertEqual(cache.get("key", "missing"), "missing")

    def test_none_value_delete_and_instance_isolation(self):
        clock = FakeClock()
        first = TTLCache(2, 10, clock)
        second = TTLCache(2, 10, clock)
        first.put("key", None)
        self.assertIsNone(first.get("key", "missing"))
        self.assertEqual(len(second), 0)
        self.assertTrue(first.delete("key"))
        self.assertFalse(first.delete("key"))

    def test_delete_expired_entry_returns_false(self):
        clock = FakeClock()
        cache = TTLCache(1, 1, clock)
        cache.put("key", "value")
        clock.now = 1
        self.assertFalse(cache.delete("key"))
        self.assertEqual(len(cache), 0)

    def test_invalid_constructor_arguments(self):
        invalid_capacities = (True, False, 0, -1, 1.5, "1", None)
        invalid_ttls = (
            True,
            False,
            0,
            -1,
            math.inf,
            -math.inf,
            math.nan,
            "1",
            None,
        )
        for capacity in invalid_capacities:
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 1, lambda: 0)
        for ttl in invalid_ttls:
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(1, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_arbitrarily_large_integer_ttl_is_valid(self):
        cache = TTLCache(1, 10**1000, lambda: 0)
        cache.put("key", "value")
        self.assertEqual(cache.get("key"), "value")


if __name__ == "__main__":
    unittest.main()
