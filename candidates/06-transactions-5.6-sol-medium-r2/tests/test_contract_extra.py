import unittest

from mvcc import Conflict, Engine


class ContractExtras(unittest.TestCase):
    def test_empty_scan_conflicts_with_insert_then_delete(self):
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

    def test_read_only_validation_and_closed_precedence(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        reader.get("x")
        writer = engine.begin()
        writer.put("x", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.put("", True)

    def test_savepoint_does_not_rollback_reads(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        transaction.savepoint("s")
        transaction.get("x")
        transaction.rollback_to("s")
        writer = engine.begin()
        writer.put("x", 2)
        writer.commit()
        with self.assertRaises(Conflict):
            transaction.commit()

    def test_recovery_retention_and_defensive_exports(self):
        engine = Engine({"a": 1})
        checkpoint = engine.checkpoint()
        transaction = engine.begin()
        transaction.delete("a")
        transaction.put("b", 2)
        transaction.commit()
        records = engine.log_since(0)
        restored = Engine.restore(checkpoint, records)
        records[0]["writes"][0][0] = "changed"
        self.assertEqual(restored.checkpoint(), {"version": 1, "data": {"b": 2}})
        self.assertEqual(restored.log_since(0)[0]["writes"], [["a", None], ["b", 2]])

        later_checkpoint = restored.checkpoint()
        later = Engine.restore(later_checkpoint, [])
        with self.assertRaises(ValueError):
            later.log_since(0)

    def test_restore_rejects_noncanonical_log(self):
        checkpoint = {"version": 3, "data": {}}
        bad_records = [
            {"version": 4, "writes": [["b", 1], ["a", 2]]},
        ]
        with self.assertRaises(ValueError):
            Engine.restore(checkpoint, bad_records)


if __name__ == "__main__":
    unittest.main()
