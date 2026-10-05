import unittest

from cache import TTLCache


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.now = 0

    def cache(self, capacity=2, ttl=10):
        return TTLCache(capacity, ttl, lambda: self.now)

    def test_validation(self):
        for capacity in (True, False, 0, -1, 1.0, None, "2"):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                self.cache(capacity=capacity)
        for ttl in (True, False, 0, -1, float("inf"), float("-inf"),
                    float("nan"), None, "10"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                self.cache(ttl=ttl)
        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)
        self.assertEqual(len(self.cache(ttl=0.5)), 0)
        self.assertEqual(len(self.cache(ttl=10 ** 1000)), 0)

    def test_read_does_not_extend_expiration(self):
        cache = self.cache()
        cache.put("a", 1)
        self.now = 9
        self.assertEqual(cache.get("a"), 1)
        self.now = 10
        self.assertEqual(cache.get("a", "missing"), "missing")

    def test_expired_mru_does_not_evict_live_lru(self):
        cache = self.cache()
        cache.put("old", 1)
        self.now = 5
        cache.put("live", 2)
        cache.get("old")
        self.now = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_replacement_resets_ttl_and_recency(self):
        cache = self.cache()
        cache.put("a", 1)
        cache.put("b", 2)
        self.now = 5
        cache.put("a", 3)
        cache.put("c", 4)
        self.assertIsNone(cache.get("b"))
        self.now = 10
        self.assertEqual(cache.get("a"), 3)
        self.now = 15
        self.assertEqual(len(cache), 0)

    def test_none_values_and_delete(self):
        cache = self.cache()
        cache.put(None, None)
        self.assertIsNone(cache.get(None, "missing"))
        self.assertTrue(cache.delete(None))
        self.assertFalse(cache.delete(None))
        cache.put("a", 1)
        self.now = 10
        self.assertFalse(cache.delete("a"))
        self.assertEqual(len(cache), 0)

    def test_len_purges_all_expired_entries(self):
        cache = self.cache(capacity=3)
        cache.put("a", 1)
        self.now = 5
        cache.put("b", 2)
        cache.get("a")
        self.now = 10
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.get("b"), 2)

    def test_instances_are_independent_and_capacity_one(self):
        first = self.cache(capacity=1)
        second = self.cache(capacity=1)
        value = object()
        first.put("a", value)
        second.put("a", None)
        self.assertIs(first.get("a"), value)
        first.put("b", 2)
        self.assertIsNone(first.get("a"))
        self.assertTrue(second.delete("a"))
        self.assertEqual(first.get("b"), 2)
