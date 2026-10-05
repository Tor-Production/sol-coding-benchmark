import unittest

from cache import TTLCache


class CacheContractTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.cache = TTLCache(2, 10, lambda: self.now)

    def test_invalid_arguments(self):
        for capacity in (True, False, 0, -1, 1.0, "2", None):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                TTLCache(capacity, 10, lambda: 0)
        for ttl in (True, False, 0, -1, -0.5, float("nan"),
                    float("inf"), float("-inf"), "10", None):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                TTLCache(2, ttl, lambda: 0)
        for clock in (None, 0, "clock"):
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                TTLCache(2, 10, clock)

    def test_read_does_not_extend_ttl(self):
        self.cache.put("a", 1)
        self.now = 9
        self.assertEqual(self.cache.get("a"), 1)
        self.now = 10
        self.assertEqual(self.cache.get("a", "missing"), "missing")

    def test_replace_resets_ttl_and_recency(self):
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        self.now = 5
        self.cache.put("a", 3)
        self.cache.put("c", 4)
        self.assertIsNone(self.cache.get("b"))
        self.now = 10
        self.assertEqual(self.cache.get("a"), 3)
        self.now = 15
        self.assertIsNone(self.cache.get("a"))

    def test_expired_mru_is_purged_before_eviction(self):
        self.cache.put("old", 1)
        self.now = 5
        self.cache.put("live", 2)
        self.cache.get("old")
        self.now = 10
        self.cache.put("new", 3)
        self.assertEqual(self.cache.get("live"), 2)
        self.assertEqual(self.cache.get("new"), 3)
        self.assertEqual(len(self.cache), 2)

    def test_none_and_arbitrary_objects(self):
        missing = object()
        value = object()
        self.cache.put("none", None)
        self.cache.put((1, 2), value)
        self.assertIsNone(self.cache.get("none", missing))
        self.assertIs(self.cache.get((1, 2)), value)
        self.assertIs(self.cache.get("missing", missing), missing)
        self.assertTrue(self.cache.delete("none"))
        self.assertFalse(self.cache.delete("none"))

    def test_delete_and_length_at_expiry(self):
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        self.now = 10
        self.assertFalse(self.cache.delete("a"))
        self.assertEqual(len(self.cache), 0)
        self.assertFalse(self.cache.delete("missing"))

    def test_instances_are_independent(self):
        other = TTLCache(1, 1, lambda: self.now)
        self.cache.put("a", 1)
        other.put("a", 2)
        other.put("b", 3)
        self.assertEqual(self.cache.get("a"), 1)
        self.now = 1
        self.assertEqual(len(other), 0)
        self.assertEqual(len(self.cache), 1)

    def test_float_ttl_and_single_clock_sample_per_operation(self):
        calls = []

        def clock():
            calls.append(self.now)
            return self.now

        cache = TTLCache(1, 0.5, clock)
        self.assertEqual(calls, [])
        cache.put("a", 1)
        cache.get("a")
        len(cache)
        cache.delete("a")
        self.assertEqual(calls, [0, 0, 0, 0])
        cache.put("a", 2)
        self.now = 0.5
        self.assertIsNone(cache.get("a"))


if __name__ == "__main__":
    unittest.main()
