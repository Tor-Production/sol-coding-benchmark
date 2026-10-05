import unittest

from cache import TTLCache


class ContractTests(unittest.TestCase):
    def test_constructor_validation(self):
        for capacity in (0, -1, True, 1.5, "2", None):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 1, lambda: 0)
        for ttl in (0, -1, True, float("nan"), float("inf"), float("-inf"), "1", None):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(1, ttl, lambda: 0)
        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)
        self.assertEqual(len(TTLCache(1, 10**400, lambda: 0)), 0)

    def test_read_does_not_extend_ttl(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("x", None)
        now[0] = 9
        self.assertIsNone(cache.get("x", "missing"))
        now[0] = 10
        self.assertEqual(cache.get("x", "missing"), "missing")

    def test_replacement_resets_expiration(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("x", 1)
        now[0] = 5
        cache.put("x", 2)
        now[0] = 10
        self.assertEqual(cache.get("x"), 2)
        now[0] = 15
        self.assertEqual(len(cache), 0)

    def test_expired_recent_entry_cannot_evict_live_old_entry(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("expired", 1)
        now[0] = 1
        cache.put("live", 2)
        cache.get("expired")
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(len(cache), 2)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)

    def test_delete_reports_only_live_removal(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("x", 1)
        self.assertTrue(cache.delete("x"))
        self.assertFalse(cache.delete("x"))
        cache.put("x", 2)
        now[0] = 10
        self.assertFalse(cache.delete("x"))
        self.assertEqual(len(cache), 0)

    def test_instances_do_not_share_entries(self):
        first = TTLCache(1, 10, lambda: 0)
        second = TTLCache(1, 10, lambda: 0)
        first.put("x", 1)
        self.assertEqual(len(second), 0)
        self.assertEqual(second.get("x", "missing"), "missing")


if __name__ == "__main__":
    unittest.main()
