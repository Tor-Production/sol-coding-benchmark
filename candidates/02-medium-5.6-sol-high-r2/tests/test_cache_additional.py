import unittest

from cache import TTLCache


class AdditionalTests(unittest.TestCase):
    def test_invalid_constructor_arguments(self):
        invalid_capacities = (True, False, 0, -1, 1.0, "1", None)
        for capacity in invalid_capacities:
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 1, lambda: 0)

        invalid_ttls = (True, False, 0, -1, float("inf"), float("-inf"), float("nan"), "1", None)
        for ttl in invalid_ttls:
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(1, ttl, lambda: 0)

        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_expired_mru_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 5, lambda: now[0])
        cache.put("expires-first", 1)
        now[0] = 1
        cache.put("live-lru", 2)
        cache.get("expires-first")

        now[0] = 5
        cache.put("new", 3)

        self.assertEqual(cache.get("live-lru"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_get_does_not_extend_ttl(self):
        now = [0]
        cache = TTLCache(1, 5, lambda: now[0])
        cache.put("key", "value")
        now[0] = 4
        self.assertEqual(cache.get("key"), "value")
        now[0] = 5
        self.assertEqual(cache.get("key", "missing"), "missing")

    def test_replace_resets_expiry_and_recency(self):
        now = [0]
        cache = TTLCache(2, 5, lambda: now[0])
        cache.put("a", 1)
        cache.put("b", 2)
        now[0] = 4
        cache.put("a", 10)
        cache.put("c", 3)

        self.assertEqual(cache.get("a"), 10)
        self.assertEqual(cache.get("b", "missing"), "missing")
        now[0] = 8
        self.assertEqual(cache.get("a"), 10)

    def test_none_value_and_delete_liveness(self):
        now = [0]
        cache = TTLCache(2, 2, lambda: now[0])
        cache.put("none", None)
        cache.put("expired", 1)

        self.assertIsNone(cache.get("none", "missing"))
        self.assertTrue(cache.delete("none"))
        self.assertFalse(cache.delete("none"))
        now[0] = 2
        self.assertFalse(cache.delete("expired"))

    def test_instances_do_not_share_state(self):
        first = TTLCache(1, 1, lambda: 0)
        second = TTLCache(1, 1, lambda: 0)
        first.put("key", "value")

        self.assertEqual(first.get("key"), "value")
        self.assertEqual(second.get("key", "missing"), "missing")


if __name__ == "__main__":
    unittest.main()
