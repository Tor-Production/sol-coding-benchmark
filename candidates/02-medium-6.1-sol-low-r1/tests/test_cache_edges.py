import unittest

from cache import TTLCache


class CacheEdgeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.cache = TTLCache(2, 10, lambda: self.now)

    def test_expired_mru_does_not_evict_live_lru(self):
        self.cache.put("old", 1)
        self.now = 5
        self.cache.put("live", 2)
        self.cache.get("old")
        self.now = 10
        self.cache.put("new", 3)
        self.assertEqual(self.cache.get("live"), 2)
        self.assertEqual(self.cache.get("new"), 3)
        self.assertEqual(len(self.cache), 2)

    def test_read_does_not_extend_ttl(self):
        self.cache.put("key", 1)
        self.now = 9
        self.assertEqual(self.cache.get("key"), 1)
        self.now = 10
        self.assertEqual(self.cache.get("key", "missing"), "missing")

    def test_replacement_resets_ttl_and_recency(self):
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        self.now = 5
        self.cache.put("a", None)
        self.cache.put("c", 3)
        self.assertEqual(self.cache.get("b", "missing"), "missing")
        self.now = 10
        self.assertIsNone(self.cache.get("a", "missing"))
        self.now = 15
        self.assertEqual(len(self.cache), 0)

    def test_delete_live_expired_and_missing(self):
        self.cache.put("a", None)
        self.assertTrue(self.cache.delete("a"))
        self.assertFalse(self.cache.delete("a"))
        self.cache.put("b", 2)
        self.now = 10
        self.assertFalse(self.cache.delete("b"))
        self.assertEqual(len(self.cache), 0)

    def test_instances_are_independent(self):
        other = TTLCache(2, 10, lambda: self.now)
        self.cache.put("a", 1)
        self.assertEqual(len(other), 0)

    def test_invalid_arguments(self):
        for capacity in (True, False, 0, -1, 1.5, None, "2"):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 10, lambda: 0)
        for ttl in (True, False, 0, -1, float("inf"), float("-inf"),
                    float("nan"), None, "10"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(2, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(2, 10, None)


if __name__ == "__main__":
    unittest.main()
