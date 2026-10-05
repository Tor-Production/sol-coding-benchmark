import unittest

from cache import TTLCache


class TTLCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.cache = TTLCache(2, 10, lambda: self.now)

    def test_invalid_capacity(self):
        for capacity in (True, False, 0, -1, 1.0, "2", None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 10, lambda: 0)

    def test_invalid_ttl(self):
        for ttl in (
            True, False, 0, -1, -0.5, float("inf"), float("-inf"),
            float("nan"), "10", None, 1 + 0j,
        ):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(2, ttl, lambda: 0)

    def test_invalid_clock(self):
        for clock in (None, 0, "clock", object()):
            with self.subTest(clock=clock):
                with self.assertRaises(ValueError):
                    TTLCache(2, 10, clock)

    def test_positive_finite_ttls(self):
        for ttl in (1, 0.5, 10**1000):
            with self.subTest(ttl=ttl):
                cache = TTLCache(1, ttl, lambda: self.now)
                cache.put("key", "value")
                self.assertEqual(cache.get("key"), "value")
                self.now = ttl
                self.assertEqual(len(cache), 0)
                self.now = 0

    def test_read_does_not_extend_ttl(self):
        self.cache.put("key", "value")
        self.now = 9.5
        self.assertEqual(self.cache.get("key"), "value")
        self.now = 10
        self.assertEqual(self.cache.get("key", "expired"), "expired")

    def test_replace_refreshes_expiration(self):
        self.cache.put("key", "old")
        self.now = 5
        self.cache.put("key", "new")
        self.now = 10
        self.assertEqual(self.cache.get("key"), "new")
        self.assertEqual(len(self.cache), 1)
        self.now = 15
        self.assertEqual(self.cache.get("key", "expired"), "expired")

    def test_replace_makes_key_most_recent(self):
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        self.cache.put("a", 3)
        self.cache.put("c", 4)
        self.assertEqual(self.cache.get("b", "missing"), "missing")
        self.assertEqual(self.cache.get("a"), 3)
        self.assertEqual(self.cache.get("c"), 4)

    def test_expired_most_recent_key_does_not_evict_live_key(self):
        self.cache.put("expired", 1)
        self.now = 5
        self.cache.put("live", 2)
        self.cache.get("expired")
        self.now = 10
        self.cache.put("new", 3)
        self.assertEqual(self.cache.get("live"), 2)
        self.assertEqual(self.cache.get("new"), 3)
        self.assertEqual(self.cache.get("expired", "missing"), "missing")

    def test_delete_live_missing_and_expired(self):
        self.assertFalse(self.cache.delete("missing"))
        self.cache.put("live", None)
        self.assertTrue(self.cache.delete("live"))
        self.assertFalse(self.cache.delete("live"))
        self.cache.put("expired", 1)
        self.now = 10
        self.assertFalse(self.cache.delete("expired"))
        self.assertEqual(len(self.cache), 0)

    def test_len_counts_only_live_entries(self):
        self.cache.put("old", 1)
        self.now = 5
        self.cache.put("new", 2)
        self.now = 10
        self.assertEqual(len(self.cache), 1)
        self.now = 15
        self.assertEqual(len(self.cache), 0)

    def test_none_and_arbitrary_values(self):
        sentinel = object()
        self.cache.put(None, None)
        self.cache.put((1, 2), sentinel)
        self.assertIsNone(self.cache.get(None, sentinel))
        self.assertIs(self.cache.get((1, 2)), sentinel)
        self.assertIs(self.cache.get("missing", sentinel), sentinel)

    def test_instances_are_independent(self):
        other = TTLCache(2, 10, lambda: self.now)
        self.cache.put("same", 1)
        other.put("same", 2)
        self.assertEqual(self.cache.get("same"), 1)
        self.assertEqual(other.get("same"), 2)
        self.cache.delete("same")
        self.assertEqual(other.get("same"), 2)

    def test_capacity_one(self):
        cache = TTLCache(1, 10, lambda: self.now)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertEqual(cache.get("a", "missing"), "missing")
        self.assertEqual(cache.get("b"), 2)
        self.assertEqual(len(cache), 1)


if __name__ == "__main__":
    unittest.main()
