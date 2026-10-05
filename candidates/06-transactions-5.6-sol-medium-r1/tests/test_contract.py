import unittest

from mvcc import Conflict, Engine


class ContractTests(unittest.TestCase):
    def test_history_detects_empty_scan_phantom_after_delete(self):
        engine = Engine()
        scanning = engine.begin()
        self.assertEqual(scanning.scan("job/"), {})

        inserting = engine.begin()
        inserting.put("job/1", 1)
        inserting.commit()
        deleting = engine.begin()
        deleting.delete("job/1")
        deleting.commit()

        with self.assertRaises(Conflict):
            scanning.commit()
        self.assertEqual(engine.version, 2)

    def test_write_free_commit_validates_point_read(self):
        engine = Engine()
        reader = engine.begin()
        self.assertIsNone(reader.get("missing"))
        writer = engine.begin()
        writer.put("missing", 3)
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()

    def test_closed_check_precedes_argument_validation(self):
        transaction = Engine().begin()
        transaction.abort()
        with self.assertRaises(RuntimeError):
            transaction.put("", True)
        with self.assertRaises(RuntimeError):
            transaction.scan(None)

    def test_recovery_base_and_defensive_log_copy(self):
        records = [{"version": 6,
                    "writes": [["a", None], ["b", 2]]}]
        engine = Engine.restore({"version": 5, "data": {"a": 1}}, records)
        self.assertEqual(engine.checkpoint(), {"version": 6,
                                               "data": {"b": 2}})
        with self.assertRaises(ValueError):
            engine.log_since(4)
        exported = engine.log_since(5)
        exported[0]["writes"][0][0] = "changed"
        self.assertEqual(engine.log_since(5), records)

    def test_reads_survive_rollback(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("s")
        transaction.get("x")
        transaction.rollback_to("s")
        writer = engine.begin()
        writer.put("x", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            transaction.commit()

    def test_bad_restore_does_not_mutate_inputs(self):
        checkpoint = {"version": 2, "data": {"x": 1}}
        records = [{"version": 3, "writes": [["z", 1], ["a", 2]]}]
        with self.assertRaises(ValueError):
            Engine.restore(checkpoint, records)
        self.assertEqual(checkpoint, {"version": 2, "data": {"x": 1}})
        self.assertEqual(records,
                         [{"version": 3,
                           "writes": [["z", 1], ["a", 2]]}])


if __name__ == "__main__":
    unittest.main()
