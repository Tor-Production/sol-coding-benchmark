import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_history_conflicts_even_when_value_returns_to_snapshot(self):
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
        with self.assertRaises(RuntimeError):
            reader.abort()
        self.assertEqual(engine.version, 2)

    def test_empty_scan_conflicts_with_insert_then_delete(self):
        engine = Engine()
        reader = engine.begin()
        self.assertEqual(reader.scan("job/"), {})
        insert = engine.begin()
        insert.put("job/1", 4)
        insert.commit()
        remove = engine.begin()
        remove.delete("job/1")
        remove.commit()
        with self.assertRaises(Conflict):
            reader.commit()

    def test_savepoint_rollback_retains_reads(self):
        engine = Engine({"a": 1})
        reader = engine.begin()
        reader.savepoint("s")
        reader.get("a")
        reader.put("b", 2)
        reader.rollback_to("s")
        writer = engine.begin()
        writer.put("a", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()
        self.assertEqual(engine.checkpoint()["data"], {"a": 1})

    def test_write_free_commit_checks_conflicts_and_does_not_log(self):
        engine = Engine()
        idle = engine.begin()
        other = engine.begin()
        other.put("x", 1)
        other.commit()
        self.assertEqual(idle.commit(), 1)
        self.assertEqual(len(engine.log_since(0)), 1)

    def test_recovery_copies_inputs_and_exports(self):
        checkpoint = {"version": 7, "data": {"z": 1}}
        records = [{"version": 8, "writes": [["a", 2], ["z", None]]}]
        engine = Engine.restore(checkpoint, records)
        checkpoint["data"]["z"] = 9
        records[0]["writes"][0][1] = 99
        exported = engine.log_since(7)
        exported[0]["writes"][0][1] = 42
        self.assertEqual(engine.checkpoint(), {"version": 8, "data": {"a": 2}})
        self.assertEqual(engine.log_since(7)[0]["writes"], [["a", 2], ["z", None]])
        with self.assertRaises(ValueError):
            engine.log_since(6)

    def test_invalid_arguments_leave_transaction_usable(self):
        engine = Engine()
        tx = engine.begin()
        tx.put("x", 1)
        for operation in (lambda: tx.put("x", True),
                          lambda: tx.scan(None),
                          lambda: tx.savepoint(""),
                          lambda: tx.rollback_to("missing")):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(tx.get("x"), 1)
        self.assertEqual(tx.commit(), 1)
        with self.assertRaises(RuntimeError):
            tx.put("", None)

    def test_restore_rejects_malformed_records(self):
        checkpoint = {"version": 2, "data": {}}
        for records in ([{"version": 4, "writes": [["a", 1]]}],
                        [{"version": 3, "writes": [["b", 1], ["a", 2]]}],
                        [{"version": 3, "writes": [["a", True]]}],
                        [{"version": 3, "writes": []}]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                Engine.restore(checkpoint, records)


if __name__ == "__main__":
    unittest.main()
