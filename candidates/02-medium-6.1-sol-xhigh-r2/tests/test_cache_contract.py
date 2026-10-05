import unittest

from cache import TTLCache


class ManualClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class CacheContractTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.cache = TTLCache(2, 10, self.clock)

    def test_read_does_not_extend_ttl(self):
        self.cache.put("key", "value")
        self.clock.now = 9.5
        self.assertEqual(self.cache.get("key"), "value")
        self.clock.now = 10
        self.assertEqual(self.cache.get("key", "expired"), "expired")
        self.assertEqual(len(self.cache), 0)

    def test_replacement_resets_ttl_and_updates_recency(self):
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        self.clock.now = 5
        self.cache.put("a", 3)
        self.assertEqual(len(self.cache), 2)
        self.cache.put("c", 4)
        self.assertEqual(self.cache.get("b", "missing"), "missing")
        self.clock.now = 10
        self.assertEqual(self.cache.get("a"), 3)
        self.clock.now = 15
        self.assertEqual(self.cache.get("a", "expired"), "expired")

    def test_expired_most_recent_key_does_not_evict_live_key(self):
        self.cache.put("old", 1)
        self.clock.now = 5
        self.cache.put("live", 2)
        self.assertEqual(self.cache.get("old"), 1)
        self.clock.now = 10
        self.cache.put("new", 3)
        self.assertEqual(self.cache.get("live"), 2)
        self.assertEqual(self.cache.get("new"), 3)
        self.assertEqual(self.cache.get("old", "expired"), "expired")
        self.assertEqual(len(self.cache), 2)

    def test_len_purges_expired_entries_at_boundary(self):
        self.cache.put("first", 1)
        self.clock.now = 5
        self.cache.put("second", 2)
        self.clock.now = 10
        self.assertEqual(len(self.cache), 1)
        self.assertEqual(self.cache.get("second"), 2)
        self.clock.now = 15
        self.assertEqual(len(self.cache), 0)

    def test_delete_reports_only_live_removals(self):
        self.assertIs(self.cache.delete("missing"), False)
        self.cache.put("live", None)
        self.assertIs(self.cache.delete("live"), True)
        self.assertIs(self.cache.delete("live"), False)
        self.cache.put("expired", 1)
        self.clock.now = 10
        self.assertIs(self.cache.delete("expired"), False)
        self.assertEqual(len(self.cache), 0)

    def test_arbitrary_values_and_hashable_keys(self):
        marker = object()
        self.cache.put(None, None)
        self.cache.put((1, "tuple"), marker)
        self.assertIsNone(self.cache.get(None, marker))
        self.assertIs(self.cache.get((1, "tuple")), marker)
        self.assertIs(self.cache.get("missing", marker), marker)
        self.assertEqual(len(self.cache), 2)
        self.cache.put("new", [])
        self.assertEqual(self.cache.get("new"), [])
        self.assertIs(self.cache.get(None, marker), marker)

    def test_single_entry_capacity(self):
        cache = TTLCache(1, 0.5, self.clock)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)
        self.clock.now = 0.5
        self.assertEqual(len(cache), 0)

    def test_instances_have_independent_entries_and_recency(self):
        other = TTLCache(2, 20, self.clock)
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        other.put("a", "other")
        other.get("a")
        self.cache.put("c", 3)
        self.assertIsNone(self.cache.get("a"))
        self.assertEqual(other.get("a"), "other")
        self.clock.now = 10
        self.assertEqual(len(self.cache), 0)
        self.assertEqual(len(other), 1)


class ValidationTests(unittest.TestCase):
    def test_invalid_capacities(self):
        for capacity in (0, -1, 1.0, True, False, "2", None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 10, lambda: 0)

    def test_invalid_ttls(self):
        for ttl in (
            0, -1, -0.5, True, False, float("inf"), float("-inf"),
            float("nan"), "10", None, 1 + 0j,
        ):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(2, ttl, lambda: 0)

    def test_invalid_clocks(self):
        for clock in (None, 0, True, "clock", object()):
            with self.subTest(clock=clock):
                with self.assertRaises(ValueError):
                    TTLCache(2, 10, clock)

    def test_large_integer_ttl_is_finite(self):
        cache = TTLCache(1, 10 ** 400, lambda: 0)
        cache.put("key", "value")
        self.assertEqual(cache.get("key"), "value")


if __name__ == "__main__":
    unittest.main()
