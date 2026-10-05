import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_empty_scan_conflicts_after_insert_and_delete(self):
        engine = Engine()
        reader = engine.begin()
        self.assertEqual(reader.scan("job/"), {})
        writer = engine.begin()
        writer.put("job/1", 7)
        writer.commit()
        deleter = engine.begin()
        deleter.delete("job/1")
        deleter.commit()
        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.abort()
        self.assertEqual(engine.checkpoint(), {"version": 2, "data": {}})

    def test_reads_survive_savepoint_rollback(self):
        engine = Engine({"x": 1})
        tx = engine.begin()
        tx.savepoint("before_read")
        self.assertEqual(tx.get("x"), 1)
        tx.put("temp", 3)
        tx.rollback_to("before_read")
        other = engine.begin()
        other.put("x", 1)
        other.commit()
        with self.assertRaises(Conflict):
            tx.commit()
        self.assertEqual(engine.version, 1)

    def test_snapshot_and_write_free_validation(self):
        engine = Engine({"a": 1})
        first = engine.begin()
        second = engine.begin()
        first.delete("a")
        first.commit()
        self.assertEqual(second.scan(), {"a": 1})
        with self.assertRaises(Conflict):
            second.commit()

    def test_export_and_input_copies(self):
        initial = {"b": 2, "a": 1}
        engine = Engine(initial)
        initial["a"] = 99
        self.assertEqual(list(engine.checkpoint()["data"]), ["a", "b"])
        checkpoint = engine.checkpoint()
        tx = engine.begin()
        tx.put("c", 3)
        tx.commit()
        records = engine.log_since(0)
        restored = Engine.restore(checkpoint, records)
        checkpoint["data"]["a"] = 99
        records[0]["writes"][0][1] = 99
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        exported = restored.log_since(0)
        exported[0]["writes"].clear()
        self.assertEqual(restored.log_since(0), [{"version": 1, "writes": [["c", 3]]}])

    def test_restored_base_and_contiguous_log(self):
        engine = Engine.restore(
            {"version": 4, "data": {"x": 1}},
            [
                {"version": 5, "writes": [["x", None], ["y", 2]]},
                {"version": 6, "writes": [["y", 3]]},
            ],
        )
        self.assertEqual(engine.checkpoint(), {"version": 6, "data": {"y": 3}})
        with self.assertRaises(ValueError):
            engine.log_since(3)
        self.assertEqual([r["version"] for r in engine.log_since(4)], [5, 6])
        self.assertEqual([r["version"] for r in engine.log_since(5)], [6])

    def test_invalid_restore_records(self):
        checkpoint = {"version": 0, "data": {}}
        invalid = [
            {"version": 2, "writes": [["x", 1]]},
            {"version": 1, "writes": []},
            {"version": 1, "writes": [["b", 1], ["a", 2]]},
            {"version": 1, "writes": [["a", 1], ["a", 2]]},
            {"version": 1, "writes": [["a", True]]},
            {"version": 1, "writes": [("a", 1)]},
        ]
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(ValueError):
                Engine.restore(checkpoint, [record])

    def test_invalid_transaction_operation_leaves_state(self):
        engine = Engine()
        tx = engine.begin()
        tx.put("x", 4)
        tx.savepoint("s")
        with self.assertRaises(ValueError):
            tx.put("x", True)
        with self.assertRaises(ValueError):
            tx.rollback_to("missing")
        with self.assertRaises(ValueError):
            tx.savepoint("s")
        self.assertEqual(tx.get("x"), 4)
        tx.rollback_to("s")
        self.assertEqual(tx.commit(), 1)
        self.assertEqual(engine.checkpoint()["data"], {"x": 4})

    def test_release_and_repeated_writes(self):
        engine = Engine()
        tx = engine.begin()
        tx.put("z", 1)
        tx.savepoint("first")
        tx.put("z", 2)
        tx.savepoint("second")
        tx.delete("z")
        tx.release("first")
        with self.assertRaises(ValueError):
            tx.rollback_to("second")
        tx.savepoint("first")
        self.assertEqual(tx.commit(), 1)
        self.assertEqual(engine.log_since(0), [{"version": 1, "writes": [["z", None]]}])

    def test_write_only_conflict_and_unrelated_write(self):
        engine = Engine()
        first = engine.begin()
        second = engine.begin()
        first.delete("missing")
        self.assertEqual(first.commit(), 1)
        second.put("other", 4)
        self.assertEqual(second.commit(), 2)
        old = engine.begin()
        competing = engine.begin()
        old.delete("absent")
        competing.delete("absent")
        competing.commit()
        with self.assertRaises(Conflict):
            old.commit()
        self.assertEqual(engine.version, 3)


if __name__ == "__main__":
    unittest.main()
