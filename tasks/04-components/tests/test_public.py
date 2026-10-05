import tempfile
import unittest
from pathlib import Path
from reservation.store import Store, Conflict


class PublicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(str(Path(self.tmp.name) / "stock.db"))

    def test_reserve_and_release(self):
        self.store.add_item("sku", 5)
        r = self.store.reserve("key", "sku", 2)
        self.assertEqual(self.store.get_item("sku")["available"], 3)
        self.store.release(r["reservation_id"])
        self.assertEqual(self.store.get_item("sku")["available"], 5)

    def test_no_oversell(self):
        self.store.add_item("sku", 1)
        with self.assertRaises(Conflict):
            self.store.reserve("key", "sku", 2)
        self.assertEqual(self.store.get_item("sku")["available"], 1)
