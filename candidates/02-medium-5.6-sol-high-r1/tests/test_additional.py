import unittest

from cache import TTLCache


class AdditionalTests(unittest.TestCase):
    def test_none_is_a_stored_value_and_reads_do_not_extend_ttl(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("key", None)

        now[0] = 9
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 10
        self.assertEqual(cache.get("key", "missing"), "missing")

    def test_expired_mru_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("old", 1)
        now[0] = 5
        cache.put("live", 2)
        cache.get("old")  # Make the soon-to-expire entry most recently used.

        now[0] = 10
        cache.put("new", 3)
        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)

    def test_replacement_resets_ttl_and_recency(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("a", 1)
        cache.put("b", 2)
        now[0] = 5
        cache.put("a", 3)
        cache.put("c", 4)

        self.assertEqual(cache.get("b", "missing"), "missing")
        now[0] = 10
        self.assertEqual(cache.get("a"), 3)

    def test_delete_only_reports_live_entries(self):
        now = [0]
        cache = TTLCache(2, 2, lambda: now[0])
        cache.put("live", 1)
        self.assertTrue(cache.delete("live"))
        self.assertFalse(cache.delete("live"))
        cache.put("expired", 2)
        now[0] = 2
        self.assertFalse(cache.delete("expired"))

    def test_constructor_validation(self):
        for capacity in (0, -1, True, False, 1.0, "1", None):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    TTLCache(capacity, 1, lambda: 0)

        for ttl in (
            0,
            -1,
            True,
            False,
            float("inf"),
            float("-inf"),
            float("nan"),
            "1",
            None,
        ):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    TTLCache(1, ttl, lambda: 0)

        with self.assertRaises(ValueError):
            TTLCache(1, 1, None)

    def test_instances_do_not_share_entries(self):
        first = TTLCache(1, 1, lambda: 0)
        second = TTLCache(1, 1, lambda: 0)
        first.put("key", "value")
        self.assertEqual(second.get("key", "missing"), "missing")


if __name__ == "__main__":
    unittest.main()
