import unittest

from cache import TTLCache


class CacheEdgeCases(unittest.TestCase):
    def test_expired_mru_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("expired", 1)
        now[0] = 5
        cache.put("live", 2)
        cache.get("expired")
        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)

    def test_read_does_not_extend_expiry_and_none_is_value(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("key", None)
        now[0] = 9
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 10
        self.assertEqual(cache.get("key", "missing"), "missing")
        self.assertFalse(cache.delete("key"))

    def test_invalid_arguments(self):
        for capacity, ttl, clock in [
            (True, 1, lambda: 0), (0, 1, lambda: 0),
            (1, False, lambda: 0), (1, float("inf"), lambda: 0),
            (1, 0, lambda: 0), (1, 1, None),
        ]:
            with self.subTest(capacity=capacity, ttl=ttl, clock=clock):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, ttl, clock)


if __name__ == "__main__":
    unittest.main()
