import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from reservation import Conflict, NotFound, Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "inventory.db"
        self.store = Store(self.path)

    def concurrently(self, operation, count=8):
        barrier = threading.Barrier(count)

        def run(index):
            store = Store(self.path)
            barrier.wait(timeout=15)
            return operation(store, index)

        with ThreadPoolExecutor(max_workers=count) as executor:
            return list(executor.map(run, range(count)))

    def test_add_normalization_and_sorted_report(self):
        self.assertEqual(self.store.report(), {
            "items": [], "active_reservations": 0, "reserved_units": 0
        })
        self.assertEqual(self.store.add_item(" z ", 2), {"sku": "z", "available": 2})
        self.assertEqual(self.store.add_item("z", 3), {"sku": "z", "available": 5})
        self.store.add_item("a", 4)
        self.assertEqual(self.store.get_item("\t z\n"), {"sku": "z", "available": 5})
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "a", "available": 4}, {"sku": "z", "available": 5}],
            "active_reservations": 0,
            "reserved_units": 0,
        })

    def test_persistent_idempotency_including_after_release(self):
        self.store.add_item("sku", 7)
        original = self.store.reserve(" key ", " sku ", 3)
        identifier = original["reservation_id"]
        self.assertIsInstance(identifier, int)
        self.assertGreater(identifier, 0)
        self.assertEqual(original, {
            "reservation_id": identifier, "idempotency_key": "key",
            "sku": "sku", "quantity": 3, "status": "active",
        })
        self.store = Store(self.path)
        self.assertEqual(self.store.reserve("key", "sku", 3), original)
        self.assertEqual(self.store.get_item("sku")["available"], 4)
        self.assertEqual(self.store.report()["reserved_units"], 3)

        released = self.store.release(identifier)
        self.assertEqual(released, {**original, "status": "released"})
        reopened = Store(self.path)
        self.assertEqual(reopened.release(identifier), released)
        self.assertEqual(reopened.reserve("key", "sku", 3), released)
        self.assertEqual(reopened.get_item("sku")["available"], 7)
        self.assertEqual(reopened.report()["active_reservations"], 0)
        self.assertEqual(reopened.report()["reserved_units"], 0)
        next_record = reopened.reserve("next", "sku", 1)
        self.assertGreater(next_record["reservation_id"], identifier)

    def test_failures_leave_all_state_unchanged_and_keys_reusable(self):
        self.store.add_item("sku", 3)
        before = self.store.report()
        with self.assertRaises(Conflict):
            self.store.reserve("retry", "sku", 4)
        with self.assertRaises(NotFound):
            self.store.reserve("missing", "other", 1)
        with self.assertRaises(NotFound):
            self.store.get_item("other")
        with self.assertRaises(NotFound):
            self.store.release(999)
        self.assertEqual(self.store.report(), before)
        self.store.add_item("sku", 1)
        self.store.reserve("retry", "sku", 4)
        self.store.add_item("other", 1)
        self.store.reserve("missing", "other", 1)
        self.assertEqual(self.store.report()["active_reservations"], 2)

    def test_conflicting_key_parameters_before_and_after_release(self):
        self.store.add_item("sku", 4)
        original = self.store.reserve("key", "sku", 2)
        for released in (False, True):
            if released:
                self.store.release(original["reservation_id"])
            before = self.store.report()
            for sku, quantity in (("sku", 1), ("absent", 2)):
                with self.subTest(released=released, sku=sku, quantity=quantity):
                    with self.assertRaises(Conflict):
                        self.store.reserve("key", sku, quantity)
                    self.assertEqual(self.store.report(), before)

    def test_argument_validation_is_atomic(self):
        self.store.add_item("sku", 5)
        before = self.store.report()
        for invalid in (None, True, 1, b"sku", [], "", " \t\n"):
            with self.subTest(string=invalid):
                for operation in (
                    lambda: self.store.add_item(invalid, 1),
                    lambda: self.store.get_item(invalid),
                    lambda: self.store.reserve("key", invalid, 1),
                    lambda: self.store.reserve(invalid, "sku", 1),
                ):
                    with self.assertRaises(ValueError):
                        operation()
        for invalid in (None, True, False, 0, -1, 1.0, "1", []):
            with self.subTest(integer=invalid):
                for operation in (
                    lambda: self.store.add_item("sku", invalid),
                    lambda: self.store.reserve("key", "sku", invalid),
                    lambda: self.store.release(invalid),
                ):
                    with self.assertRaises(ValueError):
                        operation()
        self.assertEqual(self.store.report(), before)

    def test_large_positive_integers_remain_exact(self):
        quantity = 10 ** 80
        self.store.add_item("sku", quantity)
        self.store.add_item("sku", 1)
        record = self.store.reserve("key", "sku", quantity)
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        self.assertEqual(self.store.report()["reserved_units"], quantity)
        self.store.release(record["reservation_id"])
        self.assertEqual(Store(self.path).get_item("sku")["available"], quantity + 1)
        with self.assertRaises(NotFound):
            self.store.release(quantity)

    def test_concurrent_adds_from_separate_instances(self):
        self.concurrently(lambda store, index: store.add_item("sku", 2))
        self.assertEqual(self.store.get_item("sku")["available"], 16)

    def test_concurrent_reservations_cannot_oversell(self):
        self.store.add_item("sku", 3)

        def reserve(store, index):
            try:
                return store.reserve(f"key-{index}", "sku", 1)
            except Conflict:
                return None

        records = [record for record in self.concurrently(reserve) if record is not None]
        self.assertEqual(len(records), 3)
        self.assertEqual(len({record["reservation_id"] for record in records}), 3)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 3, "reserved_units": 3,
        })

    def test_concurrent_same_key_creates_one_record(self):
        self.store.add_item("sku", 1)
        records = self.concurrently(lambda store, index: store.reserve("same", "sku", 1))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(self.store.report()["active_reservations"], 1)
        self.assertEqual(self.store.get_item("sku")["available"], 0)

    def test_concurrent_conflicting_keys_have_one_winner(self):
        self.store.add_item("sku", 5)

        def reserve(store, index):
            try:
                return store.reserve("same", "sku", 1 + index % 2)
            except Conflict:
                return None

        records = [record for record in self.concurrently(reserve) if record is not None]
        self.assertEqual(len(records), 4)
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(self.store.report()["active_reservations"], 1)
        self.assertEqual(self.store.get_item("sku")["available"], 5 - records[0]["quantity"])

    def test_concurrent_release_restores_units_once(self):
        self.store.add_item("sku", 5)
        original = self.store.reserve("key", "sku", 4)
        records = self.concurrently(
            lambda store, index: store.release(original["reservation_id"])
        )
        self.assertTrue(all(record == {**original, "status": "released"} for record in records))
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 5}],
            "active_reservations": 0, "reserved_units": 0,
        })

    def test_reports_remain_consistent_during_writes(self):
        self.store.add_item("sku", 5)
        barrier = threading.Barrier(2)

        def writer():
            store = Store(self.path)
            barrier.wait(timeout=15)
            for index in range(30):
                record = store.reserve(f"key-{index}", "sku", 5)
                store.release(record["reservation_id"])

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(writer)
            barrier.wait(timeout=15)
            for _ in range(60):
                report = self.store.report()
                self.assertEqual(report["items"][0]["available"] + report["reserved_units"], 5)
            future.result(timeout=30)
