import unittest

from cache import TTLCache


class CacheContractTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.cache = TTLCache(2, 10, lambda: self.now)

    def test_validation(self):
        for capacity in (True, False, 0, -1, 2.0, "2", None):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 10, lambda: 0)
        for ttl in (True, False, 0, -1, float("inf"), float("-inf"),
                    float("nan"), "10", None):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(2, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(2, 10, None)
        TTLCache(1, 0.5, lambda: 0)
        TTLCache(1, 10 ** 400, lambda: 0)

    def test_read_does_not_extend_ttl(self):
        self.cache.put("a", 1)
        self.now = 9
        self.assertEqual(self.cache.get("a"), 1)
        self.now = 10
        self.assertEqual(self.cache.get("a", "missing"), "missing")

    def test_replacement_resets_expiry_and_recency(self):
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        self.now = 5
        self.cache.put("a", 3)
        self.cache.put("c", 4)
        self.assertIsNone(self.cache.get("b"))
        self.now = 10
        self.assertEqual(self.cache.get("a"), 3)
        self.now = 15
        self.assertEqual(len(self.cache), 0)

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

    def test_none_values_and_delete(self):
        sentinel = object()
        self.cache.put("a", None)
        self.assertIsNone(self.cache.get("a", sentinel))
        self.assertTrue(self.cache.delete("a"))
        self.assertFalse(self.cache.delete("a"))
        self.assertIs(self.cache.get("a", sentinel), sentinel)
        self.cache.put("b", None)
        self.now = 10
        self.assertFalse(self.cache.delete("b"))
        self.assertEqual(len(self.cache), 0)

    def test_instances_are_independent(self):
        other = TTLCache(1, 20, lambda: self.now)
        value = object()
        self.cache.put((1, 2), value)
        other.put((1, 2), None)
        self.assertIs(self.cache.get((1, 2)), value)
        self.now = 10
        self.assertEqual(len(self.cache), 0)
        self.assertEqual(len(other), 1)
