import unittest

from mvcc import Conflict, Engine


class ContractTests(unittest.TestCase):
    def test_history_conflicts_include_aba_and_empty_ranges(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 1)
        range_reader = engine.begin()
        self.assertEqual(range_reader.scan("job/"), {})

        writer = engine.begin()
        writer.put("x", 2)
        writer.put("job/1", 1)
        writer.commit()
        writer = engine.begin()
        writer.put("x", 1)
        writer.delete("job/1")
        writer.commit()

        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(Conflict):
            range_reader.commit()

    def test_write_free_commit_is_validated(self):
        engine = Engine()
        reader = engine.begin()
        self.assertIsNone(reader.get("missing"))
        writer = engine.begin()
        writer.delete("missing")
        writer.commit()

        with self.assertRaises(Conflict):
            reader.commit()
        self.assertEqual(engine.version, 1)

    def test_savepoints_restore_only_writes_and_release_newer(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("outer")
        transaction.put("a", 2)
        transaction.savepoint("inner")
        transaction.put("b", 3)
        transaction.rollback_to("outer")
        transaction.put("c", 4)
        transaction.release("outer")
        transaction.savepoint("outer")
        transaction.commit()

        self.assertEqual(engine.checkpoint()["data"], {"a": 1, "c": 4})

    def test_recovery_validation_and_defensive_exports(self):
        checkpoint = {"version": 4, "data": {"b": 2, "a": 1}}
        records = [
            {"version": 5, "writes": [["a", None], ["c", 3]]},
            {"version": 6, "writes": [["b", 2]]},
        ]
        engine = Engine.restore(checkpoint, records)
        checkpoint["data"]["a"] = 99
        records[0]["writes"][1][1] = 99

        self.assertEqual(
            engine.checkpoint(), {"version": 6, "data": {"b": 2, "c": 3}}
        )
        exported = engine.log_since(4)
        exported[0]["writes"][1][1] = 99
        self.assertEqual(engine.log_since(4)[0]["writes"][1], ["c", 3])
        with self.assertRaises(ValueError):
            engine.log_since(3)

        bad_records = [
            {"version": 5, "writes": [["a", 1]]},
            {"version": 7, "writes": [["b", 2]]},
        ]
        with self.assertRaises(ValueError):
            Engine.restore({"version": 4, "data": {}}, bad_records)

    def test_closed_precedes_argument_validation(self):
        transaction = Engine().begin()
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
            with self.assertRaises(RuntimeError):
                operation()

    def test_same_value_and_missing_delete_are_logged_writes(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        transaction.put("x", 1)
        transaction.delete("absent")
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(
            engine.log_since(0),
            [{"version": 1, "writes": [["absent", None], ["x", 1]]}],
        )


if __name__ == "__main__":
    unittest.main()
