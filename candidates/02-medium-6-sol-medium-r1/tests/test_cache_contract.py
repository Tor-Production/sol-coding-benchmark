import unittest

from cache import TTLCache


class CacheContractTests(unittest.TestCase):
    def test_expired_recent_entry_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("old", "expired")
        now[0] = 5
        cache.put("recent", "live")
        now[0] = 9
        cache.get("old")
        now[0] = 10
        cache.put("new", "value")
        self.assertEqual(cache.get("old", "missing"), "missing")
        self.assertEqual(cache.get("recent"), "live")
        self.assertEqual(cache.get("new"), "value")

    def test_reads_do_not_extend_ttl_and_none_is_a_value(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("key", None)
        now[0] = 9
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 10
        self.assertEqual(cache.get("key", "missing"), "missing")

    def test_delete_and_instance_isolation(self):
        now = [0]
        first = TTLCache(1, 2, lambda: now[0])
        second = TTLCache(1, 2, lambda: now[0])
        first.put("key", 1)
        self.assertEqual(len(second), 0)
        self.assertTrue(first.delete("key"))
        self.assertFalse(first.delete("key"))
        first.put("key", 2)
        now[0] = 2
        self.assertFalse(first.delete("key"))
        self.assertEqual(len(first), 0)

    def test_invalid_constructor_arguments(self):
        invalid = [
            (0, 1, lambda: 0),
            (True, 1, lambda: 0),
            (1.0, 1, lambda: 0),
            (1, 0, lambda: 0),
            (1, True, lambda: 0),
            (1, float("inf"), lambda: 0),
            (1, float("nan"), lambda: 0),
            (1, 1, None),
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                TTLCache(*args)


if __name__ == "__main__":
    unittest.main()
