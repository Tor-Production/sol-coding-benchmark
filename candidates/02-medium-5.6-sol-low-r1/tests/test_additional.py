import math
import unittest

from cache import TTLCache


class AdditionalTests(unittest.TestCase):
    def test_invalid_arguments(self):
        invalid = [
            (0, 1, lambda: 0),
            (True, 1, lambda: 0),
            (1, 0, lambda: 0),
            (1, True, lambda: 0),
            (1, math.inf, lambda: 0),
            (1, math.nan, lambda: 0),
            (1, 1, None),
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                TTLCache(*args)

    def test_expired_non_lru_entry_does_not_cause_live_eviction(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("old", 1)
        now[0] = 5
        cache.put("live", 2)
        cache.get("old")  # Make the soon-to-expire entry most recently used.
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)

    def test_replacement_resets_expiry_and_none_is_a_value(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("key", None)
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 9
        cache.put("key", "updated")
        now[0] = 10
        self.assertEqual(cache.get("key"), "updated")
        now[0] = 19
        self.assertFalse(cache.delete("key"))

    def test_arbitrarily_large_integer_ttl_is_valid(self):
        cache = TTLCache(1, 10**1000, lambda: 0)
        cache.put("key", "value")
        self.assertEqual(cache.get("key"), "value")


if __name__ == "__main__":
    unittest.main()
