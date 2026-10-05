import unittest

from mvcc import Conflict, Engine


class ContractExtra(unittest.TestCase):
    def test_snapshot_visibility_sorted_scan_and_defensive_initial(self):
        initial = {"z": 3, "a": 1}
        engine = Engine(initial)
        initial["a"] = 99
        transaction = engine.begin()

        transaction.put("m", 2)
        transaction.delete("z")
        self.assertEqual(transaction.scan(), {"a": 1, "m": 2})
        self.assertEqual(list(transaction.scan()), ["a", "m"])
        self.assertEqual(engine.checkpoint(), {"version": 0, "data": {"a": 1, "z": 3}})

    def test_empty_range_detects_insert_then_delete(self):
        engine = Engine()
        reader = engine.begin()
        self.assertEqual(reader.scan("job/"), {})

        insert = engine.begin()
        insert.put("job/1", 1)
        insert.commit()
        delete = engine.begin()
        delete.delete("job/1")
        delete.commit()

        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.abort()

    def test_write_free_commit_validates_reads_but_ignores_unrelated_writes(self):
        engine = Engine({"read": 1})
        conflicting = engine.begin()
        conflicting.get("read")
        unrelated = engine.begin()
        unrelated.get("read")

        writer = engine.begin()
        writer.put("other", 2)
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(unrelated.commit(), 1)

        writer = engine.begin()
        writer.put("read", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            conflicting.commit()

    def test_savepoint_stack_and_reads_survive_rollback(self):
        engine = Engine({"watched": 1})
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("outer")
        transaction.put("a", 2)
        transaction.savepoint("inner")
        transaction.get("watched")
        transaction.put("b", 3)
        transaction.rollback_to("outer")
        self.assertEqual(transaction.scan(), {"a": 1, "watched": 1})

        writer = engine.begin()
        writer.put("watched", 2)
        writer.commit()
        before = engine.checkpoint()
        with self.assertRaises(Conflict):
            transaction.commit()
        self.assertEqual(engine.checkpoint(), before)

    def test_recovery_log_boundaries_and_defensive_exports(self):
        checkpoint = {"version": 4, "data": {"b": 2, "a": 1}}
        records = [
            {"version": 5, "writes": [["a", None], ["c", 3]]},
            {"version": 6, "writes": [["missing", None]]},
        ]
        engine = Engine.restore(checkpoint, records)
        checkpoint["data"]["a"] = 100
        records[0]["writes"][1][1] = 100

        self.assertEqual(engine.version, 6)
        self.assertEqual(engine.checkpoint(), {"version": 6, "data": {"b": 2, "c": 3}})
        with self.assertRaises(ValueError):
            engine.log_since(3)
        exported = engine.log_since(4)
        exported[0]["writes"][1][1] = 200
        self.assertEqual(engine.log_since(5), [{"version": 6, "writes": [["missing", None]]}])
        self.assertEqual(engine.log_since(4)[0]["writes"][1][1], 3)

    def test_restore_rejects_noncanonical_records(self):
        checkpoint = {"version": 0, "data": {}}
        invalid_records = (
            [{"version": 2, "writes": [["a", 1]]}],
            [{"version": 1, "writes": []}],
            [{"version": 1, "writes": [("a", 1)]}],
            [{"version": 1, "writes": [["b", 1], ["a", 2]]}],
            [{"version": 1, "writes": [["a", 1], ["a", 2]]}],
            [{"version": 1, "writes": [["a", True]]}],
            [{"version": 1, "writes": [["a", 1]], "extra": None}],
        )
        for records in invalid_records:
            with self.subTest(records=records), self.assertRaises(ValueError):
                Engine.restore(checkpoint, records)

    def test_invalid_operation_is_atomic_and_closed_check_comes_first(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("ok", 1)
        with self.assertRaises(ValueError):
            transaction.put("ok", True)
        self.assertEqual(transaction.get("ok"), 1)
        transaction.abort()

        for operation in (
            lambda: transaction.get(None),
            lambda: transaction.scan(None),
            lambda: transaction.put(None, None),
            lambda: transaction.delete(None),
            lambda: transaction.savepoint(None),
            lambda: transaction.rollback_to(None),
            lambda: transaction.release(None),
            transaction.commit,
            transaction.abort,
        ):
            with self.subTest(operation=operation), self.assertRaises(RuntimeError):
                operation()


if __name__ == "__main__":
    unittest.main()
