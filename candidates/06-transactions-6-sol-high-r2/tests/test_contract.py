import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_history_conflicts_even_when_final_value_matches(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 1)
        writer = engine.begin()
        writer.put("x", 2)
        writer.commit()
        writer = engine.begin()
        writer.put("x", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.abort()

    def test_empty_scan_sees_insert_then_delete_conflict(self):
        engine = Engine()
        reader = engine.begin()
        self.assertEqual(reader.scan("job/"), {})
        writer = engine.begin()
        writer.put("job/1", 1)
        writer.commit()
        writer = engine.begin()
        writer.delete("job/1")
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()

    def test_rollback_keeps_reads_but_restores_writes(self):
        engine = Engine({"a": 1})
        tx = engine.begin()
        tx.savepoint("s")
        tx.put("a", 2)
        self.assertEqual(tx.get("a"), 2)
        tx.rollback_to("s")
        self.assertEqual(tx.scan(), {"a": 1})
        other = engine.begin()
        other.put("a", 3)
        other.commit()
        with self.assertRaises(Conflict):
            tx.commit()

    def test_write_free_and_noop_writes(self):
        engine = Engine({"a": 1})
        self.assertEqual(engine.begin().commit(), 0)
        tx = engine.begin()
        tx.put("a", 1)
        tx.delete("missing")
        self.assertEqual(tx.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["a", 1], ["missing", None]]}])

    def test_recovery_and_defensive_exports(self):
        engine = Engine({"b": 2, "a": 1})
        cp = engine.checkpoint()
        tx = engine.begin()
        tx.put("c", 3)
        tx.commit()
        log = engine.log_since(0)
        restored = Engine.restore(cp, log)
        cp["data"]["a"] = 99
        log[0]["writes"][0][1] = 99
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        exported = restored.log_since(0)
        exported[0]["writes"][0][1] = 99
        self.assertEqual(restored.log_since(0)[0]["writes"][0][1], 3)
        self.assertEqual(list(engine.checkpoint()["data"]), ["a", "b", "c"])

    def test_restore_validates_shape_and_retained_base(self):
        cp = {"version": 5, "data": {"a": 1}}
        for bad in (
            [{"version": 7, "writes": [["a", 2]]}],
            [{"version": 6, "writes": [["b", 2], ["a", 3]]}],
            [{"version": 6, "writes": [["a", True]]}],
            [{"version": 6, "writes": []}],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Engine.restore(cp, bad)
        restored = Engine.restore(cp, [{"version": 6, "writes": [["a", None]]}])
        with self.assertRaises(ValueError):
            restored.log_since(4)
        self.assertEqual(restored.log_since(5),
                         [{"version": 6, "writes": [["a", None]]}])

    def test_invalid_arguments_and_closed_precedence(self):
        engine = Engine()
        tx = engine.begin()
        with self.assertRaises(ValueError):
            tx.put("x", True)
        with self.assertRaises(ValueError):
            tx.scan(None)
        with self.assertRaises(ValueError):
            tx.savepoint("")
        self.assertEqual(tx.commit(), 0)
        self.assertEqual(engine.checkpoint(), {"version": 0, "data": {}})
        with self.assertRaises(RuntimeError):
            tx.put("", True)
        with self.assertRaises(RuntimeError):
            tx.scan(None)


if __name__ == "__main__":
    unittest.main()
