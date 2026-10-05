import unittest

from cache import TTLCache


class CacheContractTests(unittest.TestCase):
    def test_invalid_constructor_arguments(self):
        for capacity in (0, -1, True, 1.0, "2"):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 10, lambda: 0)
        for ttl in (0, -1, True, float("inf"), float("-inf"), float("nan"), "10"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(2, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(2, 10, None)

    def test_expired_most_recent_key_cannot_evict_live_key(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("old", 1)
        now[0] = 5
        cache.put("live", 2)
        self.assertEqual(cache.get("old"), 1)
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_read_does_not_extend_ttl_and_replacement_does(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("key", "first")
        now[0] = 9
        self.assertEqual(cache.get("key"), "first")
        now[0] = 10
        self.assertEqual(cache.get("key", "missing"), "missing")
        cache.put("key", "second")
        now[0] = 19
        cache.put("key", "third")
        now[0] = 20
        self.assertEqual(cache.get("key"), "third")
        now[0] = 29
        self.assertEqual(len(cache), 0)

    def test_delete_none_value_and_independent_instances(self):
        now = [0]
        first = TTLCache(1, 2, lambda: now[0])
        second = TTLCache(1, 2, lambda: now[0])
        first.put("key", None)
        self.assertIsNone(first.get("key", "missing"))
        self.assertEqual(second.get("key", "missing"), "missing")
        self.assertTrue(first.delete("key"))
        self.assertFalse(first.delete("key"))
        first.put("key", None)
        now[0] = 2
        self.assertFalse(first.delete("key"))
        self.assertEqual(len(first), 0)

    def test_arbitrarily_large_integer_ttl(self):
        ttl = 10**400
        now = [0.5]
        cache = TTLCache(1, ttl, lambda: now[0])
        cache.put("key", "value")
        self.assertEqual(cache.get("key"), "value")
        now[0] = ttl
        self.assertEqual(cache.get("key"), "value")
        now[0] = ttl + 1
        self.assertEqual(cache.get("key", "missing"), "missing")


if __name__ == "__main__":
    unittest.main()
