import unittest

from cache import TTLCache


class AdditionalTests(unittest.TestCase):
    def test_validation(self):
        invalid_capacities = [True, False, 0, -1, 1.0, "1", None]
        for capacity in invalid_capacities:
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 1, lambda: 0)

        invalid_ttls = [True, False, 0, -1, float("inf"), float("-inf"), float("nan"), "1", None]
        for ttl in invalid_ttls:
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(1, ttl, lambda: 0)

        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_none_is_a_stored_value(self):
        cache = TTLCache(1, 5, lambda: 0)
        cache.put("key", None)
        self.assertIsNone(cache.get("key", "missing"))
        self.assertEqual(len(cache), 1)

    def test_get_does_not_extend_ttl(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("key", "value")
        now[0] = 9
        self.assertEqual(cache.get("key"), "value")
        now[0] = 10
        self.assertEqual(cache.get("key", "missing"), "missing")

    def test_expired_non_lru_is_purged_before_eviction(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("old", 1)
        now[0] = 5
        cache.put("live", 2)
        cache.get("old")  # Expired entry is now MRU.
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_replace_resets_expiration_and_recency(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("a", 1)
        cache.put("b", 2)
        now[0] = 9
        cache.put("a", 3)
        cache.put("c", 4)
        self.assertEqual(cache.get("b", "missing"), "missing")
        now[0] = 18
        self.assertEqual(cache.get("a"), 3)

    def test_delete_reports_only_live_entries(self):
        now = [0]
        cache = TTLCache(2, 1, lambda: now[0])
        cache.put("live", 1)
        self.assertTrue(cache.delete("live"))
        self.assertFalse(cache.delete("live"))
        cache.put("expired", 2)
        now[0] = 1
        self.assertFalse(cache.delete("expired"))
        self.assertEqual(len(cache), 0)


if __name__ == "__main__":
    unittest.main()
