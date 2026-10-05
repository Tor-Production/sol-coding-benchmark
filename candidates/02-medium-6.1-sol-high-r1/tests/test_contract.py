import unittest

from cache import TTLCache


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.cache = TTLCache(2, 10, lambda: self.now)

    def test_constructor_validation(self):
        for capacity in (True, False, 0, -1, 1.0, "2", None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 10, lambda: 0)
        for ttl in (True, False, 0, -1, "10", None,
                    float("inf"), float("-inf"), float("nan")):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(2, ttl, lambda: 0)
        for clock in (None, 0, "clock"):
            with self.subTest(clock=clock):
                with self.assertRaises(ValueError):
                    TTLCache(2, 10, clock)

    def test_positive_fractional_ttl_and_inclusive_expiry(self):
        cache = TTLCache(1, 0.5, lambda: self.now)
        cache.put("key", "value")
        self.now = 0.49
        self.assertEqual(cache.get("key"), "value")
        self.now = 0.5
        self.assertEqual(cache.get("key", "expired"), "expired")

    def test_read_does_not_extend_ttl(self):
        self.cache.put("a", 1)
        self.now = 9
        self.assertEqual(self.cache.get("a"), 1)
        self.now = 10
        self.assertIsNone(self.cache.get("a"))

    def test_expired_mru_does_not_evict_live_lru(self):
        self.cache.put("a", 1)
        self.now = 5
        self.cache.put("b", 2)
        self.now = 6
        self.assertEqual(self.cache.get("a"), 1)
        self.now = 10
        self.cache.put("c", 3)
        self.assertEqual(self.cache.get("b"), 2)
        self.assertEqual(self.cache.get("c"), 3)
        self.assertEqual(len(self.cache), 2)

    def test_replacement_resets_ttl_and_promotes_key(self):
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        self.now = 5
        self.cache.put("a", 4)
        self.cache.put("c", 3)
        self.assertIsNone(self.cache.get("b"))
        self.now = 10
        self.assertEqual(self.cache.get("a"), 4)
        self.now = 15
        self.assertIsNone(self.cache.get("a"))

    def test_none_and_arbitrary_objects_are_values(self):
        missing = object()
        value = object()
        key = ("tuple", 1)
        self.cache.put(key, None)
        self.cache.put("object", value)
        self.assertIsNone(self.cache.get(key, missing))
        self.assertIs(self.cache.get("object"), value)
        self.assertIs(self.cache.get("missing", missing), missing)
        self.assertTrue(self.cache.delete(key))

    def test_delete_returns_true_only_for_live_entries(self):
        self.cache.put("a", None)
        self.assertTrue(self.cache.delete("a"))
        self.assertFalse(self.cache.delete("a"))
        self.cache.put("b", 2)
        self.now = 10
        self.assertFalse(self.cache.delete("b"))
        self.assertEqual(len(self.cache), 0)

    def test_len_counts_all_live_entries_without_changing_recency(self):
        self.cache.put("a", 1)
        self.now = 5
        self.cache.put("b", 2)
        self.assertEqual(len(self.cache), 2)
        self.cache.put("c", 3)
        self.assertIsNone(self.cache.get("a"))
        self.now = 15
        self.assertEqual(len(self.cache), 0)

    def test_instances_have_independent_state(self):
        other = TTLCache(1, 20, lambda: self.now)
        self.cache.put("a", 1)
        other.put("a", 2)
        self.now = 10
        self.assertEqual(len(self.cache), 0)
        self.assertEqual(other.get("a"), 2)
        self.assertTrue(other.delete("a"))


if __name__ == "__main__":
    unittest.main()
