import unittest

from mvcc import Conflict, Engine


class ContractTests(unittest.TestCase):
    def test_historical_phantom_conflicts_after_insert_and_delete(self):
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
        self.assertEqual(engine.version, 2)

    def test_write_free_commit_validates_reads(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 1)
        writer = engine.begin()
        writer.put("x", 1)
        writer.commit()

        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.get("")

    def test_unrelated_write_and_snapshot_visibility(self):
        engine = Engine({"a": 1})
        unrelated_reader = engine.begin()
        old_view = engine.begin()
        writer = engine.begin()
        writer.put("b", 2)
        writer.commit()

        self.assertEqual(unrelated_reader.get("a"), 1)
        self.assertEqual(unrelated_reader.commit(), 1)
        self.assertIsNone(old_view.get("b"))
        with self.assertRaises(Conflict):
            old_view.commit()

    def test_savepoint_reads_survive_rollback_and_release(self):
        engine = Engine({"observed": 1})
        transaction = engine.begin()
        transaction.put("kept", 1)
        transaction.savepoint("outer")
        transaction.get("observed")
        transaction.put("discarded", 2)
        transaction.savepoint("inner")
        transaction.rollback_to("outer")
        transaction.release("outer")

        writer = engine.begin()
        writer.put("observed", 2)
        writer.commit()
        with self.assertRaises(Conflict):
            transaction.commit()
        self.assertEqual(engine.checkpoint()["data"], {"observed": 2})

    def test_commit_records_sorted_noop_writes(self):
        engine = Engine({"same": 1})
        transaction = engine.begin()
        transaction.put("same", 1)
        transaction.delete("missing")
        transaction.put("a", -10)
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(
            engine.log_since(0),
            [{
                "version": 1,
                "writes": [["a", -10], ["missing", None], ["same", 1]],
            }],
        )

    def test_recovery_boundaries_and_defensive_copies(self):
        checkpoint = {"version": 4, "data": {"a": 1}}
        records = [
            {"version": 5, "writes": [["a", None], ["b", 2]]},
            {"version": 6, "writes": [["b", 3]]},
        ]
        engine = Engine.restore(checkpoint, records)
        checkpoint["data"]["a"] = 99
        records[0]["writes"][1][1] = 99

        self.assertEqual(engine.checkpoint(), {"version": 6, "data": {"b": 3}})
        with self.assertRaises(ValueError):
            engine.log_since(3)
        exported = engine.log_since(4)
        exported[0]["writes"][1][1] = 100
        self.assertEqual(engine.log_since(4)[0]["writes"][1], ["b", 2])

    def test_invalid_inputs_do_not_change_transaction(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("x", 1)
        transaction.savepoint("s")
        for operation in (
            lambda: transaction.put("x", True),
            lambda: transaction.delete(""),
            lambda: transaction.scan(None),
            lambda: transaction.rollback_to("missing"),
            lambda: transaction.savepoint("s"),
        ):
            with self.assertRaises(ValueError):
                operation()

        self.assertEqual(transaction.get("x"), 1)
        self.assertEqual(transaction.commit(), 1)

    def test_restore_rejects_unsorted_or_noncontiguous_records(self):
        checkpoint = {"version": 2, "data": {}}
        invalid_logs = [
            [{"version": 4, "writes": [["a", 1]]}],
            [{"version": 3, "writes": [["b", 1], ["a", 2]]}],
            [{"version": 3, "writes": [["a", True]]}],
            [{"version": 3, "writes": []}],
        ]
        for records in invalid_logs:
            with self.subTest(records=records), self.assertRaises(ValueError):
                Engine.restore(checkpoint, records)


if __name__ == "__main__":
    unittest.main()
