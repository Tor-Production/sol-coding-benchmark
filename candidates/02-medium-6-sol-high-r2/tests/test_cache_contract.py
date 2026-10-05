import unittest

from cache import TTLCache


class ContractTests(unittest.TestCase):
    def test_invalid_arguments(self):
        for capacity in (0, -1, True, 1.0, "2", None):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 1, lambda: 0)
        for ttl in (0, -1, True, float("nan"), float("inf"), float("-inf"), "2", None):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(1, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_read_does_not_extend_ttl_and_expiry_is_inclusive(self):
        now = [0]
        cache = TTLCache(1, 5, lambda: now[0])
        cache.put("key", None)
        now[0] = 4
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 5
        self.assertEqual(cache.get("key", "missing"), "missing")
        self.assertEqual(len(cache), 0)

    def test_expired_recent_key_does_not_evict_live_key(self):
        now = [0]
        cache = TTLCache(2, 5, lambda: now[0])
        cache.put("old", 1)
        now[0] = 1
        cache.put("live", 2)
        now[0] = 4
        self.assertEqual(cache.get("old"), 1)
        now[0] = 5
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_replace_resets_expiry_and_delete_reports_live_only(self):
        now = [0]
        cache = TTLCache(1, 5, lambda: now[0])
        cache.put("key", 1)
        now[0] = 4
        cache.put("key", 2)
        now[0] = 5
        self.assertEqual(cache.get("key"), 2)
        self.assertTrue(cache.delete("key"))
        self.assertFalse(cache.delete("key"))
        cache.put("key", 3)
        now[0] = 10
        self.assertFalse(cache.delete("key"))

    def test_instances_are_independent(self):
        first = TTLCache(1, 5, lambda: 0)
        second = TTLCache(1, 5, lambda: 0)
        first.put("key", 1)
        self.assertEqual(len(second), 0)
        second.put("key", 2)
        self.assertEqual(first.get("key"), 1)
        self.assertEqual(second.get("key"), 2)


if __name__ == "__main__":
    unittest.main()
