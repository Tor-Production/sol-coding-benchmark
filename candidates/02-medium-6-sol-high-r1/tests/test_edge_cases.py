import unittest

from cache import TTLCache


class EdgeCaseTests(unittest.TestCase):
    def test_expired_most_recent_key_does_not_evict_live_key(self):
        now = [0]
        cache = TTLCache(2, 3, lambda: now[0])
        cache.put("old", 1)
        now[0] = 1
        cache.put("live", 2)
        now[0] = 2
        cache.get("old")
        now[0] = 3
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("old", "missing"), "missing")
        self.assertEqual(len(cache), 2)

    def test_read_does_not_extend_ttl_and_none_is_a_value(self):
        now = [0]
        cache = TTLCache(1, 2, lambda: now[0])
        cache.put("key", None)
        now[0] = 1
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 2
        self.assertEqual(cache.get("key", "missing"), "missing")
        self.assertFalse(cache.delete("key"))

    def test_replace_resets_expiry_and_delete_checks_liveness(self):
        now = [0]
        cache = TTLCache(1, 2, lambda: now[0])
        cache.put("key", 1)
        now[0] = 1
        cache.put("key", 2)
        now[0] = 2
        self.assertEqual(cache.get("key"), 2)
        self.assertTrue(cache.delete("key"))
        self.assertEqual(len(cache), 0)

    def test_invalid_constructor_arguments(self):
        invalid = [
            (0, 1, lambda: 0),
            (True, 1, lambda: 0),
            (1.0, 1, lambda: 0),
            (1, False, lambda: 0),
            (1, 0, lambda: 0),
            (1, float("nan"), lambda: 0),
            (1, float("inf"), lambda: 0),
            (1, 1, None),
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                TTLCache(*args)

    def test_instances_have_independent_entries(self):
        first = TTLCache(1, 2, lambda: 0)
        second = TTLCache(1, 2, lambda: 0)
        first.put("key", 1)
        self.assertEqual(len(second), 0)
        self.assertEqual(first.get("key"), 1)
