import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_empty_scan_conflicts_after_insert_and_delete(self):
        engine = Engine()
        reader = engine.begin()
        self.assertEqual(reader.scan("job/"), {})
        writer = engine.begin()
        writer.put("job/1", 1)
        writer.commit()
        deleter = engine.begin()
        deleter.delete("job/1")
        deleter.commit()
        with self.assertRaises(Conflict):
            reader.commit()
        self.assertEqual(engine.version, 2)
        with self.assertRaises(RuntimeError):
            reader.abort()

    def test_reads_survive_rollback_and_write_free_commit(self):
        engine = Engine()
        reader = engine.begin()
        reader.savepoint("s")
        self.assertIsNone(reader.get("x"))
        reader.put("x", 5)
        reader.rollback_to("s")
        writer = engine.begin()
        writer.put("x", 5)
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()

    def test_snapshot_and_exports_are_independent(self):
        initial = {"a": 1}
        engine = Engine(initial)
        old = engine.begin()
        initial["a"] = 99
        writer = engine.begin()
        writer.put("b", 2)
        writer.commit()
        self.assertEqual(old.scan(), {"a": 1})
        checkpoint = engine.checkpoint()
        log = engine.log_since(0)
        checkpoint["data"]["a"] = 99
        log[0]["writes"][0][1] = 99
        self.assertEqual(engine.checkpoint()["data"], {"a": 1, "b": 2})
        self.assertEqual(engine.log_since(0)[0]["writes"], [["b", 2]])

    def test_recovery_base_and_validation(self):
        checkpoint = {"version": 7, "data": {"a": 1}}
        records = [{"version": 8, "writes": [["a", None], ["b", 2]]}]
        engine = Engine.restore(checkpoint, records)
        records[0]["writes"][1][1] = 99
        self.assertEqual(engine.checkpoint(), {"version": 8, "data": {"b": 2}})
        self.assertEqual(engine.log_since(7),
                         [{"version": 8, "writes": [["a", None], ["b", 2]]}])
        with self.assertRaises(ValueError):
            engine.log_since(6)
        bad = [{"version": 8, "writes": [["b", 2], ["a", 1]]}]
        with self.assertRaises(ValueError):
            Engine.restore(checkpoint, bad)

    def test_invalid_operation_keeps_state_and_closed_precedes_validation(self):
        engine = Engine()
        txn = engine.begin()
        txn.put("x", 1)
        with self.assertRaises(ValueError):
            txn.put("x", True)
        with self.assertRaises(ValueError):
            txn.savepoint("")
        self.assertEqual(txn.get("x"), 1)
        txn.abort()
        with self.assertRaises(RuntimeError):
            txn.put("", True)
        self.assertEqual(engine.version, 0)


if __name__ == "__main__":
    unittest.main()
