import unittest

from cache import TTLCache


class TTLCacheContractTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.cache = TTLCache(2, 10, lambda: self.now)

    def test_constructor_validation(self):
        for capacity in (True, False, 0, -1, 1.0, "2", None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 10, lambda: 0)
        for ttl in (True, False, 0, -1, float("inf"), float("-inf"),
                    float("nan"), "10", None):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(2, ttl, lambda: 0)
        for clock in (None, 0, "clock"):
            with self.subTest(clock=clock):
                with self.assertRaises(ValueError):
                    TTLCache(2, 10, clock)

    def test_large_integer_ttl_is_finite(self):
        cache = TTLCache(1, 10 ** 400, lambda: 0)
        cache.put("key", "value")
        self.assertEqual(cache.get("key"), "value")

    def test_reads_do_not_extend_expiration(self):
        self.cache.put("key", "value")
        self.now = 9
        self.assertEqual(self.cache.get("key"), "value")
        self.now = 10
        sentinel = object()
        self.assertIs(self.cache.get("key", sentinel), sentinel)
        self.assertEqual(len(self.cache), 0)

    def test_replacement_resets_ttl_and_updates_recency(self):
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        self.now = 5
        self.cache.put("a", 3)
        self.cache.put("c", 4)
        self.assertIsNone(self.cache.get("b"))
        self.now = 10
        self.assertEqual(self.cache.get("a"), 3)
        self.now = 15
        self.assertEqual(len(self.cache), 0)

    def test_expired_most_recent_key_does_not_evict_live_key(self):
        self.cache.put("old", 1)
        self.now = 5
        self.cache.put("live", 2)
        self.cache.get("old")
        self.now = 10
        self.cache.put("new", 3)
        self.assertEqual(self.cache.get("live"), 2)
        self.assertEqual(self.cache.get("new"), 3)
        self.assertEqual(len(self.cache), 2)

    def test_none_and_object_values_are_stored_by_identity(self):
        value = object()
        sentinel = object()
        self.cache.put("none", None)
        self.cache.put("object", value)
        self.assertIsNone(self.cache.get("none", sentinel))
        self.assertIs(self.cache.get("object"), value)
        self.assertIs(self.cache.get("missing", sentinel), sentinel)

    def test_delete_returns_true_only_for_live_entries(self):
        self.assertFalse(self.cache.delete("missing"))
        self.cache.put("live", None)
        self.assertTrue(self.cache.delete("live"))
        self.assertFalse(self.cache.delete("live"))
        self.cache.put("expired", 1)
        self.now = 10
        self.assertFalse(self.cache.delete("expired"))
        self.assertEqual(len(self.cache), 0)

    def test_length_counts_only_live_entries_without_changing_recency(self):
        self.cache.put("a", 1)
        self.now = 5
        self.cache.put("b", 2)
        self.assertEqual(len(self.cache), 2)
        self.cache.put("c", 3)
        self.assertIsNone(self.cache.get("a"))
        self.now = 10
        self.assertEqual(len(self.cache), 2)
        self.now = 15
        self.assertEqual(len(self.cache), 0)

    def test_instances_have_independent_storage_and_expiration(self):
        other = TTLCache(1, 20, lambda: self.now)
        self.cache.put("key", "first")
        other.put("key", "second")
        self.now = 10
        self.assertIsNone(self.cache.get("key"))
        self.assertEqual(other.get("key"), "second")
        self.assertTrue(other.delete("key"))
        self.assertEqual(len(self.cache), 0)

    def test_fractional_time_and_capacity_one(self):
        cache = TTLCache(1, 0.5, lambda: self.now)
        self.now = 1.25
        cache.put(("tuple", 1), "first")
        self.now = 1.5
        cache.put(None, "second")
        self.assertIsNone(cache.get(("tuple", 1)))
        self.now = 1.999
        self.assertEqual(cache.get(None), "second")
        self.now = 2.0
        self.assertIsNone(cache.get(None))
