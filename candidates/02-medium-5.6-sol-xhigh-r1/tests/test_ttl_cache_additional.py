import math
import unittest

from cache import TTLCache


class TTLCacheAdditionalTests(unittest.TestCase):
    def test_constructor_validation(self):
        for capacity in (True, False, 0, -1, 1.0, "1", None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 1, lambda: 0)

        for ttl in (True, False, 0, -1, math.inf, -math.inf, math.nan, "1", None):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(1, ttl, lambda: 0)

        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_read_does_not_extend_ttl_and_expiry_is_inclusive(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("key", "value")

        now[0] = 9
        self.assertEqual(cache.get("key"), "value")
        now[0] = 10
        self.assertEqual(cache.get("key", "default"), "default")

    def test_expired_mru_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("expires-first", 1)
        now[0] = 5
        cache.put("still-live", 2)
        cache.get("expires-first")  # Make the earlier-expiring item MRU.

        now[0] = 10
        cache.put("new", 3)

        self.assertEqual(cache.get("still-live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertIsNone(cache.get("expires-first"))

    def test_replacement_resets_expiry_and_updates_recency(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("a", 1)
        cache.put("b", 2)
        now[0] = 5
        cache.put("a", 3)
        cache.put("c", 4)

        self.assertEqual(cache.get("a"), 3)
        self.assertIsNone(cache.get("b"))
        now[0] = 15
        self.assertIsNone(cache.get("a"))

    def test_none_values_delete_len_and_instance_isolation(self):
        now = [0]
        first = TTLCache(2, 5, lambda: now[0])
        second = TTLCache(2, 5, lambda: now[0])
        sentinel = object()

        first.put("none", None)
        self.assertIsNone(first.get("none", sentinel))
        self.assertIs(second.get("none", sentinel), sentinel)
        self.assertEqual(len(first), 1)
        self.assertTrue(first.delete("none"))
        self.assertFalse(first.delete("none"))

        first.put("expired", 1)
        now[0] = 5
        self.assertFalse(first.delete("expired"))
        self.assertEqual(len(first), 0)


if __name__ == "__main__":
    unittest.main()
