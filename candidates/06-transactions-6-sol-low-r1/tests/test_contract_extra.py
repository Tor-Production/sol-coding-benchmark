import unittest

from mvcc import Conflict, Engine


class ContractExtra(unittest.TestCase):
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

    def test_reads_survive_rollback(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        reader.savepoint("s")
        reader.get("x")
        reader.rollback_to("s")
        writer = engine.begin()
        writer.put("x", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()

    def test_recovery_copies_and_validates_log(self):
        checkpoint = {"version": 4, "data": {"a": 1}}
        records = [{"version": 5, "writes": [["a", None], ["b", 2]]}]
        engine = Engine.restore(checkpoint, records)
        records[0]["writes"][1][1] = 99
        checkpoint["data"]["a"] = 99
        self.assertEqual(engine.checkpoint(), {"version": 5, "data": {"b": 2}})
        self.assertEqual(engine.log_since(4),
                         [{"version": 5, "writes": [["a", None], ["b", 2]]}])
        with self.assertRaises(ValueError):
            engine.log_since(3)
        with self.assertRaises(ValueError):
            Engine.restore({"version": 0, "data": {}},
                           [{"version": 1, "writes": [["b", 2], ["a", 1]]}])


if __name__ == "__main__":
    unittest.main()
