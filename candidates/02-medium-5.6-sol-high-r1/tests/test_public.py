import unittest
from cache import TTLCache


class PublicTests(unittest.TestCase):
    def test_expiry(self):
        now = [0]
        c = TTLCache(2, 10, lambda: now[0])
        c.put("x", 4)
        self.assertEqual(c.get("x"), 4)
        now[0] = 10
        self.assertIsNone(c.get("x"))
        self.assertEqual(len(c), 0)

    def test_lru(self):
        c = TTLCache(2, 10, lambda: 0)
        c.put("a", 1)
        c.put("b", 2)
        c.get("a")
        c.put("c", 3)
        self.assertEqual(c.get("b", "missing"), "missing")
