import unittest

from cache import TTLCache


class CacheContractTests(unittest.TestCase):
    def test_validation(self):
        for capacity in (True, False, 0, -1, 1.5, "2", None):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 10, lambda: 0)
        for ttl in (True, False, 0, -1, float("inf"), float("-inf"),
                    float("nan"), "10", None):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(2, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(2, 10, None)

    def test_expired_mru_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("old", 1)
        now[0] = 5
        cache.put("live", 2)
        cache.get("old")
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_read_does_not_extend_ttl_and_none_is_live(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("key", None)
        now[0] = 9
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 10
        self.assertEqual(cache.get("key", "missing"), "missing")
        self.assertFalse(cache.delete("key"))

    def test_replacement_resets_ttl_and_recency(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("a", 1)
        cache.put("b", 2)
        now[0] = 5
        cache.put("a", 3)
        cache.put("c", 4)
        self.assertIsNone(cache.get("b"))
        now[0] = 10
        self.assertEqual(cache.get("a"), 3)
        now[0] = 15
        self.assertFalse(cache.delete("a"))
        self.assertEqual(len(cache), 0)

    def test_delete_and_instance_isolation(self):
        first = TTLCache(1, 0.5, lambda: 0)
        second = TTLCache(1, 0.5, lambda: 0)
        first.put("key", None)
        self.assertEqual(len(second), 0)
        self.assertTrue(first.delete("key"))
        self.assertFalse(first.delete("key"))
        self.assertEqual(len(first), 0)
