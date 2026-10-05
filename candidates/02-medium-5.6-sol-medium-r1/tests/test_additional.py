import math
import unittest

from cache import TTLCache


class AdditionalContractTests(unittest.TestCase):
    def test_invalid_constructor_arguments(self):
        invalid_arguments = [
            (0, 1, lambda: 0),
            (-1, 1, lambda: 0),
            (True, 1, lambda: 0),
            (1.0, 1, lambda: 0),
            (1, 0, lambda: 0),
            (1, -1, lambda: 0),
            (1, True, lambda: 0),
            (1, math.inf, lambda: 0),
            (1, math.nan, lambda: 0),
            (1, 1, None),
        ]
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    TTLCache(*arguments)

    def test_expired_mru_does_not_evict_live_lru(self):
        now = [0]
        cache = TTLCache(2, 10, lambda: now[0])
        cache.put("older", 1)
        now[0] = 5
        cache.put("live", 2)
        cache.get("older")  # Make the soon-to-expire entry most recent.

        now[0] = 10
        cache.put("new", 3)

        self.assertEqual(cache.get("live"), 2)
        self.assertEqual(cache.get("new"), 3)
        self.assertEqual(len(cache), 2)

    def test_get_does_not_extend_ttl_and_none_is_a_value(self):
        now = [0]
        cache = TTLCache(1, 10, lambda: now[0])
        cache.put("key", None)
        now[0] = 9
        self.assertIsNone(cache.get("key", "missing"))
        now[0] = 10
        self.assertEqual(cache.get("key", "missing"), "missing")

    def test_delete_only_reports_live_entries(self):
        now = [0]
        cache = TTLCache(2, 2, lambda: now[0])
        cache.put("live", 1)
        self.assertTrue(cache.delete("live"))
        self.assertFalse(cache.delete("live"))
        cache.put("expired", 2)
        now[0] = 2
        self.assertFalse(cache.delete("expired"))

    def test_instances_do_not_share_entries(self):
        first = TTLCache(1, 10, lambda: 0)
        second = TTLCache(1, 10, lambda: 0)
        first.put("key", "value")
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)


if __name__ == "__main__":
    unittest.main()
