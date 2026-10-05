import unittest

from cache import TTLCache


class AdditionalTests(unittest.TestCase):
    def test_expired_mru_is_purged_before_lru_eviction(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("old", 1)
        now[0] = 5
        cache.put("live", 2)
        cache.get("old")  # Make the entry that expires first the MRU.

        now[0] = 10
        cache.put("new", 3)

        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_get_does_not_extend_ttl_and_none_is_a_value(self):
        now = [0]
        cache = TTLCache(1, 2, lambda: now[0])
        cache.put("key", None)
        now[0] = 1
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 2
        self.assertEqual(cache.get("key", "missing"), "missing")

    def test_replacement_resets_expiry(self):
        now = [0]
        cache = TTLCache(1, 2, lambda: now[0])
        cache.put("key", "first")
        now[0] = 1
        cache.put("key", "second")
        now[0] = 2
        self.assertEqual(cache.get("key"), "second")
        now[0] = 3
        self.assertIsNone(cache.get("key"))

    def test_delete_reports_only_live_entries(self):
        now = [0]
        cache = TTLCache(2, 1, lambda: now[0])
        cache.put("live", 1)
        self.assertTrue(cache.delete("live"))
        self.assertFalse(cache.delete("missing"))
        cache.put("expired", 2)
        now[0] = 1
        self.assertFalse(cache.delete("expired"))
        self.assertEqual(len(cache), 0)

    def test_constructor_validation(self):
        invalid_capacities = (True, False, 0, -1, 1.0, "1", None)
        invalid_ttls = (True, False, 0, -1, 0.0, float("inf"), float("-inf"),
                        float("nan"), "1", None)

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

        # Python integers are finite regardless of their size.
        TTLCache(1, 10 ** 1000, lambda: 0)


if __name__ == "__main__":
    unittest.main()
