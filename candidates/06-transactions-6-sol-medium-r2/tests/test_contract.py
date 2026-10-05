import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_empty_scan_detects_insert_then_delete(self):
        engine = Engine()
        reader = engine.begin()
        self.assertEqual(reader.scan("job/"), {})
        writer = engine.begin()
        writer.put("job/1", 3)
        writer.commit()
        eraser = engine.begin()
        eraser.delete("job/1")
        eraser.commit()
        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.abort()

    def test_read_after_savepoint_stays_tracked(self):
        engine = Engine({"a": 1})
        reader = engine.begin()
        reader.savepoint("s")
        self.assertEqual(reader.get("a"), 1)
        reader.rollback_to("s")
        writer = engine.begin()
        writer.put("a", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()

    def test_rolled_back_write_no_longer_conflicts(self):
        engine = Engine()
        first = engine.begin()
        first.savepoint("s")
        first.put("a", 1)
        first.rollback_to("s")
        second = engine.begin()
        second.put("a", 2)
        second.commit()
        self.assertEqual(first.commit(), 1)
        self.assertEqual(engine.checkpoint()["data"], {"a": 2})

    def test_snapshot_and_recovery_copies(self):
        initial = {"z": 1}
        engine = Engine(initial)
        initial["z"] = 9
        snapshot = engine.begin()
        base = engine.checkpoint()
        writer = engine.begin()
        writer.put("a", -10**100)
        writer.commit()
        exported = engine.log_since(0)
        restored = Engine.restore(base, exported)
        exported[0]["writes"][0][1] = 0
        base["data"]["z"] = 0
        self.assertEqual(snapshot.scan(), {"z": 1})
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        self.assertEqual(restored.log_since(0)[0]["writes"][0][1], -10**100)

    def test_restore_base_and_malformed_records(self):
        checkpoint = {"version": 7, "data": {"x": 1}}
        records = [{"version": 8, "writes": [["x", None], ["y", 2]]}]
        restored = Engine.restore(checkpoint, records)
        self.assertEqual(restored.version, 8)
        self.assertEqual(restored.log_since(7), records)
        with self.assertRaises(ValueError):
            restored.log_since(6)
        for bad in (
            [{"version": 9, "writes": [["x", 2]]}],
            [{"version": 8, "writes": []}],
            [{"version": 8, "writes": [["z", 2], ["a", 3]]}],
            [{"version": 8, "writes": [["x", True]]}],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Engine.restore(checkpoint, bad)

    def test_invalid_calls_do_not_change_transaction(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("a", 1)
        with self.assertRaises(ValueError):
            transaction.put("a", True)
        with self.assertRaises(ValueError):
            transaction.savepoint("")
        self.assertEqual(transaction.get("a"), 1)
        transaction.abort()
        with self.assertRaises(RuntimeError):
            transaction.get(None)


if __name__ == "__main__":
    unittest.main()
