import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_snapshot_and_pending_writes(self):
        source = {"b": 2, "a": 1}
        engine = Engine(source)
        source["a"] = 9
        reader = engine.begin()
        writer = engine.begin()
        writer.put("c", 3)
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(reader.scan(), {"a": 1, "b": 2})
        reader.put("a", 4)
        reader.delete("b")
        self.assertEqual(reader.scan(), {"a": 4})
        with self.assertRaises(Conflict):
            reader.commit()  # Its earlier full-range scan saw the new c.

    def test_history_conflicts_despite_restored_value(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 1)
        first = engine.begin()
        first.put("x", 2)
        first.commit()
        second = engine.begin()
        second.put("x", 1)
        second.commit()
        with self.assertRaises(Conflict):
            reader.commit()
        self.assertEqual(engine.version, 2)
        with self.assertRaises(RuntimeError):
            reader.abort()

    def test_empty_range_and_unrelated_write(self):
        engine = Engine()
        reader = engine.begin()
        self.assertEqual(reader.scan("job/"), {})
        unrelated = engine.begin()
        unrelated.put("other", 1)
        unrelated.commit()
        creator = engine.begin()
        creator.put("job/1", 1)
        creator.commit()
        remover = engine.begin()
        remover.delete("job/1")
        remover.commit()
        with self.assertRaises(Conflict):
            reader.commit()

    def test_rolled_back_reads_survive_and_writes_do_not(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("s")
        transaction.put("discarded", 1)
        transaction.get("observed")
        transaction.rollback_to("s")
        writer = engine.begin()
        writer.put("discarded", 2)
        writer.commit()
        self.assertEqual(transaction.commit(), 1)

        transaction = engine.begin()
        transaction.savepoint("s")
        transaction.get("observed")
        transaction.rollback_to("s")
        writer = engine.begin()
        writer.delete("observed")
        writer.commit()
        with self.assertRaises(Conflict):
            transaction.commit()

    def test_savepoint_stack_and_argument_validation(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("first")
        transaction.put("a", 2)
        transaction.savepoint("second")
        with self.assertRaises(ValueError):
            transaction.savepoint("first")
        with self.assertRaises(ValueError):
            transaction.put("a", True)
        self.assertEqual(transaction.get("a"), 2)
        transaction.rollback_to("first")
        transaction.savepoint("second")
        transaction.release("first")
        self.assertEqual(transaction.get("a"), 1)
        self.assertEqual(transaction.commit(), 1)
        with self.assertRaises(RuntimeError):
            transaction.put("", None)

    def test_log_recovery_and_defensive_exports(self):
        engine = Engine({"b": 2, "a": 1})
        checkpoint = engine.checkpoint()
        self.assertEqual(list(checkpoint["data"]), ["a", "b"])
        transaction = engine.begin()
        transaction.delete("missing")
        transaction.put("z", -10**100)
        transaction.commit()
        records = engine.log_since(0)
        restored = Engine.restore(checkpoint, records)
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        self.assertEqual(restored.log_since(0), records)
        checkpoint["data"]["a"] = 99
        records[0]["writes"][0][0] = "changed"
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        self.assertEqual(restored.log_since(0)[0]["writes"][0][0], "missing")
        with self.assertRaises(ValueError):
            Engine.restore({"version": 1, "data": {}}, []).log_since(0)

    def test_invalid_recovery_records(self):
        checkpoint = {"version": 3, "data": {"a": 1}}
        bad_records = [
            [{"version": 5, "writes": [["a", 2]]}],
            [{"version": 4, "writes": []}],
            [{"version": 4, "writes": [["b", 2], ["a", 1]]}],
            [{"version": 4, "writes": [["a", 2], ["a", 3]]}],
            [{"version": 4, "writes": [("a", 2)]}],
            [{"version": 4, "writes": [["a", True]]}],
            [{"version": 4, "writes": [["a", 2]]},
             {"version": 6, "writes": [["b", 3]]}],
        ]
        for records in bad_records:
            with self.subTest(records=records), self.assertRaises(ValueError):
                Engine.restore(checkpoint, records)
        with self.assertRaises(ValueError):
            Engine.restore({"version": True, "data": {}}, [])


if __name__ == "__main__":
    unittest.main()
