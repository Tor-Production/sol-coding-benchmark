import unittest

from cache import TTLCache


class FakeClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


class CacheContractTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def test_invalid_capacity(self):
        invalid = (True, False, 0, -1, 1.0, float("inf"), "2", None, [])
        for capacity in invalid:
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 10, self.clock)

    def test_invalid_ttl(self):
        invalid = (
            True, False, 0, 0.0, -1, -0.5,
            float("inf"), float("-inf"), float("nan"),
            "10", None, [], 1j,
        )
        for ttl in invalid:
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(2, ttl, self.clock)

    def test_invalid_clock(self):
        for clock in (None, 0, True, "clock", [], object()):
            with self.subTest(clock=clock):
                with self.assertRaises(ValueError):
                    TTLCache(2, 10, clock)

    def test_large_positive_integers_are_valid(self):
        cache = TTLCache(10 ** 1000, 10 ** 1000, self.clock)
        cache.put("key", "value")
        self.assertEqual(cache.get("key"), "value")

    def test_fractional_ttl_and_inclusive_expiry(self):
        self.clock.now = 2.5
        cache = TTLCache(1, 0.25, self.clock)
        cache.put("key", "value")
        self.clock.now = 2.74
        self.assertEqual(cache.get("key"), "value")
        self.clock.now = 2.75
        self.assertEqual(cache.get("key", "expired"), "expired")
        self.assertEqual(len(cache), 0)

    def test_read_does_not_extend_ttl(self):
        cache = TTLCache(1, 10, self.clock)
        cache.put("key", "value")
        self.clock.now = 9
        self.assertEqual(cache.get("key"), "value")
        self.clock.now = 10
        self.assertEqual(cache.get("key", "expired"), "expired")

    def test_replacement_resets_expiry_and_recency(self):
        cache = TTLCache(2, 10, self.clock)
        cache.put("a", "old")
        cache.put("b", "value")
        self.clock.now = 5
        cache.put("a", "new")
        cache.put("c", "value")
        self.assertEqual(len(cache), 2)
        self.assertEqual(cache.get("b", "missing"), "missing")
        self.clock.now = 10
        self.assertEqual(cache.get("a"), "new")
        self.clock.now = 15
        self.assertIsNone(cache.get("a"))

    def test_expired_recent_key_cannot_evict_live_key(self):
        cache = TTLCache(2, 10, self.clock)
        cache.put("old", 1)
        self.clock.now = 5
        cache.put("live", 2)
        cache.get("old")
        self.clock.now = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertIsNone(cache.get("old"))
        self.assertEqual(len(cache), 2)

    def test_purge_multiple_expired_keys_before_eviction(self):
        cache = TTLCache(3, 10, self.clock)
        cache.put("old1", 1)
        cache.put("old2", 2)
        self.clock.now = 5
        cache.put("live", 3)
        cache.get("old1")
        cache.get("old2")
        self.clock.now = 10
        cache.put("new1", 4)
        cache.put("new2", 5)
        self.assertEqual(cache.get("live"), 3)
        self.assertEqual(cache.get("new1"), 4)
        self.assertEqual(cache.get("new2"), 5)
        self.assertEqual(len(cache), 3)

    def test_len_counts_only_live_entries_without_changing_recency(self):
        cache = TTLCache(3, 10, self.clock)
        cache.put("old", 0)
        self.clock.now = 5
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("old")
        self.clock.now = 10
        self.assertEqual(len(cache), 2)
        cache.put("c", 3)
        self.assertEqual(len(cache), 3)
        cache.put("d", 4)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)

    def test_delete_live_expired_and_missing_entries(self):
        cache = TTLCache(2, 10, self.clock)
        self.assertIs(cache.delete("missing"), False)
        cache.put("live", None)
        self.assertIs(cache.delete("live"), True)
        self.assertIs(cache.delete("live"), False)
        self.assertEqual(len(cache), 0)
        cache.put("expired", "value")
        self.clock.now = 10
        self.assertIs(cache.delete("expired"), False)
        self.assertEqual(len(cache), 0)

    def test_none_and_object_values_are_preserved(self):
        cache = TTLCache(2, 10, self.clock)
        value = []
        default = object()
        cache.put(None, None)
        cache.put((1, "key"), value)
        self.assertIsNone(cache.get(None, default))
        self.assertIs(cache.get((1, "key")), value)
        self.assertIs(cache.get("missing", default), default)
        self.clock.now = 10
        self.assertIs(cache.get(None, default), default)
        self.assertEqual(len(cache), 0)

    def test_capacity_one_and_reinserting_expired_key(self):
        cache = TTLCache(1, 10, self.clock)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertIsNone(cache.get("a"))
        self.clock.now = 10
        cache.put("b", 3)
        self.assertEqual(cache.get("b"), 3)
        self.assertEqual(len(cache), 1)

    def test_instances_do_not_share_state(self):
        other_clock = FakeClock(100)
        first = TTLCache(1, 10, self.clock)
        second = TTLCache(1, 20, other_clock)
        first.put("key", "first")
        second.put("key", "second")
        self.assertEqual(first.get("key"), "first")
        self.assertEqual(second.get("key"), "second")
        self.clock.now = 10
        self.assertEqual(len(first), 0)
        self.assertEqual(second.get("key"), "second")
        first.put("key", "replacement")
        self.assertIs(first.delete("key"), True)
        self.assertEqual(len(second), 1)


if __name__ == "__main__":
    unittest.main()
