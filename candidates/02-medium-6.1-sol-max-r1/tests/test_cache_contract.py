import unittest

from cache import TTLCache


class ManualClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


class TTLCacheContractTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()

    def test_invalid_constructor_arguments(self):
        for capacity in (True, False, 0, -1, 1.0, "2", None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 10, self.clock)
        for ttl in (
            True, False, 0, -1, 0.0, -0.5, float("nan"),
            float("inf"), float("-inf"), "10", None, 1j,
        ):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(2, ttl, self.clock)
        for clock in (None, 0, True, "clock", object()):
            with self.subTest(clock=clock):
                with self.assertRaises(ValueError):
                    TTLCache(2, 10, clock)

    def test_positive_integer_ttl_does_not_require_float_conversion(self):
        cache = TTLCache(1, 10 ** 400, self.clock)
        cache.put("key", "value")
        self.assertEqual(cache.get("key"), "value")
        self.clock.now = 10 ** 400
        self.assertIsNone(cache.get("key"))

    def test_reads_do_not_extend_fractional_ttl(self):
        cache = TTLCache(1, 0.5, self.clock)
        cache.put("key", "value")
        self.clock.now = 0.25
        self.assertEqual(cache.get("key"), "value")
        self.clock.now = 0.499
        self.assertEqual(cache.get("key"), "value")
        self.clock.now = 0.5
        missing = object()
        self.assertIs(cache.get("key", missing), missing)
        self.assertEqual(len(cache), 0)

    def test_replacement_refreshes_ttl_and_recency(self):
        cache = TTLCache(2, 10, self.clock)
        cache.put("a", "old")
        self.clock.now = 1
        cache.put("b", "other")
        self.clock.now = 5
        cache.put("a", "new")
        self.assertEqual(len(cache), 2)
        cache.put("c", "third")
        self.assertIsNone(cache.get("b"))
        self.clock.now = 10
        self.assertEqual(cache.get("a"), "new")
        self.clock.now = 15
        self.assertIsNone(cache.get("a"))
        self.assertEqual(len(cache), 0)

    def test_expired_mru_is_purged_before_live_lru_eviction(self):
        cache = TTLCache(2, 10, self.clock)
        cache.put("expires_first", 1)
        self.clock.now = 1
        cache.put("live", 2)
        self.clock.now = 2
        self.assertEqual(cache.get("expires_first"), 1)
        self.clock.now = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertIsNone(cache.get("expires_first"))
        self.assertEqual(len(cache), 2)

    def test_len_purges_all_expired_entries_without_changing_recency(self):
        cache = TTLCache(3, 10, self.clock)
        cache.put("old_a", 1)
        cache.put("old_b", 2)
        self.clock.now = 1
        cache.put("live_a", 3)
        self.clock.now = 10
        self.assertEqual(len(cache), 1)
        cache.put("live_b", 4)
        cache.put("live_c", 5)
        self.assertEqual(len(cache), 3)
        cache.put("live_d", 6)
        self.assertIsNone(cache.get("live_a"))
        self.assertEqual(cache.get("live_b"), 4)
        self.assertEqual(cache.get("live_c"), 5)
        self.assertEqual(cache.get("live_d"), 6)

    def test_delete_reports_only_live_entries(self):
        cache = TTLCache(2, 10, self.clock)
        self.assertIs(cache.delete("missing"), False)
        cache.put("live", None)
        self.assertIs(cache.delete("live"), True)
        self.assertIs(cache.delete("live"), False)
        cache.put("expired", 1)
        self.clock.now = 10
        self.assertIs(cache.delete("expired"), False)
        self.assertEqual(len(cache), 0)

    def test_values_and_hashable_keys(self):
        cache = TTLCache(5, 10, self.clock)
        arbitrary_value = object()
        items = (
            (None, None), (0, False), (("tuple", 1), 0),
            (frozenset({1, 2}), ""), (object(), arbitrary_value),
        )
        missing = object()
        for key, value in items:
            cache.put(key, value)
        self.assertEqual(len(cache), len(items))
        for key, value in items:
            self.assertIs(cache.get(key, missing), value)
        self.assertIs(cache.get("missing", missing), missing)

    def test_capacity_one_replacement_and_expired_reinsertion(self):
        cache = TTLCache(1, 10, self.clock)
        cache.put("a", 1)
        cache.put("a", 2)
        self.assertEqual(cache.get("a"), 2)
        self.assertEqual(len(cache), 1)
        self.clock.now = 10
        cache.put("a", 3)
        self.assertEqual(cache.get("a"), 3)
        cache.put("b", 4)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 4)

    def test_instances_have_independent_storage_and_clocks(self):
        other_clock = ManualClock(100)
        first = TTLCache(1, 10, self.clock)
        second = TTLCache(2, 20, other_clock)
        first.put("shared", "first")
        second.put("shared", "second")
        second.put("extra", "extra")
        self.assertEqual(first.get("shared"), "first")
        self.assertEqual(second.get("shared"), "second")
        self.clock.now = 10
        self.assertEqual(len(first), 0)
        self.assertEqual(len(second), 2)
        self.assertEqual(second.get("shared"), "second")


if __name__ == "__main__":
    unittest.main()
