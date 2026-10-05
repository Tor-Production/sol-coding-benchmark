import unittest

from cache import TTLCache


class ManualClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


class TTLCacheContractTests(unittest.TestCase):
    def test_invalid_constructor_arguments(self):
        for capacity in (True, False, 0, -1, 1.0, "2", None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 10, ManualClock())
        for ttl in (True, False, 0, -1, -0.5, float("inf"),
                    float("-inf"), float("nan"), "10", None, 1j):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(2, ttl, ManualClock())
        for clock in (None, 0, "clock", object()):
            with self.subTest(clock=clock):
                with self.assertRaises(ValueError):
                    TTLCache(2, 10, clock)

    def test_valid_ttl_types_including_large_integer(self):
        for ttl in (1, 0.5, 10 ** 400):
            with self.subTest(ttl=ttl):
                cache = TTLCache(1, ttl, ManualClock())
                cache.put("key", "value")
                self.assertEqual(cache.get("key"), "value")

    def test_none_and_arbitrary_objects_are_values(self):
        cache = TTLCache(2, 10, ManualClock())
        fallback = object()
        value = [1, {"nested": None}]
        cache.put(None, None)
        cache.put((1, "key"), value)
        self.assertIsNone(cache.get(None, fallback))
        self.assertIs(cache.get((1, "key")), value)
        self.assertIs(cache.get("missing", fallback), fallback)
        self.assertEqual(len(cache), 2)

    def test_reads_promote_without_extending_ttl(self):
        clock = ManualClock()
        cache = TTLCache(2, 10, clock)
        cache.put("a", 1)
        clock.now = 2
        cache.put("b", 2)
        clock.now = 9
        self.assertEqual(cache.get("a"), 1)
        cache.put("c", 3)
        self.assertEqual(cache.get("b", "missing"), "missing")
        clock.now = 10
        self.assertEqual(cache.get("a", "expired"), "expired")
        self.assertEqual(cache.get("c"), 3)

    def test_replacement_resets_ttl_and_promotes(self):
        clock = ManualClock()
        cache = TTLCache(2, 10, clock)
        cache.put("a", "old")
        clock.now = 2
        cache.put("b", 2)
        clock.now = 4
        cache.put("a", "new")
        self.assertEqual(len(cache), 2)
        clock.now = 5
        cache.put("c", 3)
        self.assertIsNone(cache.get("b"))
        clock.now = 13
        self.assertEqual(cache.get("a"), "new")
        clock.now = 14
        self.assertIsNone(cache.get("a"))
        self.assertEqual(len(cache), 1)

    def test_expired_mru_never_evicts_live_lru(self):
        clock = ManualClock()
        cache = TTLCache(2, 10, clock)
        cache.put("expires_first", 1)
        clock.now = 5
        cache.put("live", 2)
        clock.now = 6
        cache.get("expires_first")
        clock.now = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertIsNone(cache.get("expires_first"))
        self.assertEqual(len(cache), 2)

    def test_length_counts_all_live_entries_and_preserves_recency(self):
        clock = ManualClock()
        cache = TTLCache(3, 10, clock)
        cache.put("old", 0)
        clock.now = 5
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("old")
        clock.now = 10
        self.assertEqual(len(cache), 2)
        cache.put("c", 3)
        self.assertEqual(len(cache), 3)
        cache.get("missing")
        cache.put("d", 4)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)
        clock.now = 20
        self.assertEqual(len(cache), 0)

    def test_delete_only_reports_live_removals(self):
        clock = ManualClock()
        cache = TTLCache(2, 0.5, clock)
        self.assertIs(cache.delete("missing"), False)
        cache.put("live", None)
        cache.put("expired", object())
        clock.now = 0.499
        self.assertIs(cache.delete("live"), True)
        self.assertIs(cache.delete("live"), False)
        clock.now = 0.5
        self.assertIs(cache.delete("expired"), False)
        self.assertEqual(len(cache), 0)

    def test_capacity_one_and_instance_isolation(self):
        first_clock = ManualClock()
        second_clock = ManualClock(100)
        first = TTLCache(1, 10, first_clock)
        second = TTLCache(1, 20, second_clock)
        first.put("key", "first")
        second.put("key", "second")
        first.put("key", "replacement")
        self.assertEqual(len(first), 1)
        self.assertEqual(first.get("key"), "replacement")
        first.put("new", 1)
        self.assertIsNone(first.get("key"))
        self.assertEqual(second.get("key"), "second")
        first_clock.now = 10
        self.assertEqual(len(first), 0)
        self.assertEqual(len(second), 1)


if __name__ == "__main__":
    unittest.main()
