import unittest

from cache import TTLCache


class CacheContractTests(unittest.TestCase):
    def test_constructor_rejects_invalid_arguments(self):
        for capacity in (0, -1, True, 1.5, "2"):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 1, lambda: 0)
        for ttl in (0, -1, True, float("inf"), float("-inf"), float("nan"), "1"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(1, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_expired_mru_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("old", 1)
        now[0] = 5
        cache.put("live", 2)
        now[0] = 6
        self.assertEqual(cache.get("old"), 1)  # old is now most recently used
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("old", "missing"), "missing")
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)

    def test_read_does_not_extend_expiry_and_boundary_is_inclusive(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("x", None)
        now[0] = 9
        self.assertIsNone(cache.get("x", "missing"))
        now[0] = 10
        self.assertEqual(cache.get("x", "missing"), "missing")
        self.assertEqual(len(cache), 0)

    def test_replacement_resets_expiry_and_recency(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("a", 1)
        cache.put("b", 2)
        now[0] = 5
        cache.put("a", 3)
        now[0] = 10
        self.assertEqual(cache.get("a"), 3)
        cache.put("c", 4)
        self.assertEqual(cache.get("b", "missing"), "missing")

    def test_delete_reports_only_live_removals(self):
        now = [0]
        cache = TTLCache(2, 2, lambda: now[0])
        cache.put(("key", 1), None)
        self.assertTrue(cache.delete(("key", 1)))
        self.assertFalse(cache.delete(("key", 1)))
        cache.put("expired", 1)
        now[0] = 2
        self.assertFalse(cache.delete("expired"))
        self.assertEqual(len(cache), 0)

    def test_instances_are_independent(self):
        first = TTLCache(1, 1, lambda: 0)
        second = TTLCache(1, 1, lambda: 0)
        first.put("a", 1)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)


if __name__ == "__main__":
    unittest.main()
