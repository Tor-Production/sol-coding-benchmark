import unittest

from cache import TTLCache


class ContractTests(unittest.TestCase):
    def test_constructor_rejects_invalid_arguments(self):
        for capacity in (0, -1, True, 1.0, "2"):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 1, lambda: 0)

        for ttl in (0, -1, True, float("nan"), float("inf"), float("-inf"), "1"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(1, ttl, lambda: 0)

        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

        # Large integers are finite even when they cannot be converted to float.
        TTLCache(1, 10**1000, lambda: 0)

    def test_expiry_is_inclusive_and_reads_do_not_extend_it(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("key", None)
        now[0] = 9
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 10
        self.assertEqual(cache.get("key", "missing"), "missing")
        self.assertEqual(len(cache), 0)

    def test_put_resets_expiry_and_recency(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("a", 1)
        cache.put("b", 2)
        now[0] = 5
        cache.put("a", 3)
        cache.put("c", 4)
        self.assertEqual(cache.get("b", "missing"), "missing")
        now[0] = 10
        self.assertEqual(cache.get("a"), 3)
        now[0] = 15
        self.assertEqual(cache.get("a", "missing"), "missing")

    def test_expired_most_recent_key_does_not_evict_live_key(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("expired", 1)
        now[0] = 5
        cache.put("live", 2)
        cache.get("expired")  # Expired will be most recently used.
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_delete_reports_only_live_removals(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("live", 1)
        self.assertTrue(cache.delete("live"))
        self.assertFalse(cache.delete("live"))
        cache.put("expired", 2)
        now[0] = 10
        self.assertFalse(cache.delete("expired"))
        self.assertEqual(len(cache), 0)

    def test_instances_are_independent(self):
        first = TTLCache(1, 10, lambda: 0)
        second = TTLCache(1, 10, lambda: 0)
        first.put("key", 1)
        self.assertEqual(second.get("key", "missing"), "missing")
        second.put("key", 2)
        self.assertEqual(first.get("key"), 1)
        self.assertEqual(second.get("key"), 2)
