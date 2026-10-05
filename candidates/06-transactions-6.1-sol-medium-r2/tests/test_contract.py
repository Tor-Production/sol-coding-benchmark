import copy
import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_snapshot_overlay_and_order(self):
        initial = {"z": 3, "a": 1}
        engine = Engine(initial)
        initial["a"] = 99
        reader = engine.begin()
        writer = engine.begin()
        writer.put("a", 2)
        writer.commit()
        self.assertEqual(reader.get("a"), 1)
        reader.delete("z")
        reader.put("b", -(10 ** 100))
        visible = reader.scan()
        self.assertEqual(list(visible), ["a", "b"])
        visible["a"] = 100
        self.assertEqual(reader.get("a"), 1)
        self.assertEqual(engine.checkpoint()["data"], {"a": 2, "z": 3})

    def test_aba_and_empty_range_conflict(self):
        for ranged in (False, True):
            with self.subTest(ranged=ranged):
                engine = Engine()
                reader = engine.begin()
                if ranged:
                    self.assertEqual(reader.scan("job/"), {})
                else:
                    self.assertIsNone(reader.get("job/1"))
                writer = engine.begin()
                writer.put("job/1", 1)
                writer.commit()
                writer = engine.begin()
                writer.delete("job/1")
                writer.commit()
                before = engine.checkpoint()
                with self.assertRaises(Conflict):
                    reader.commit()
                self.assertEqual(engine.checkpoint(), before)
                with self.assertRaises(RuntimeError):
                    reader.abort()

    def test_same_value_missing_delete_and_write_conflicts(self):
        engine = Engine({"x": 1})
        waiting = engine.begin()
        waiting.put("x", 7)
        writer = engine.begin()
        writer.put("x", 1)
        writer.delete("missing")
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["missing", None], ["x", 1]]}])
        with self.assertRaises(Conflict):
            waiting.commit()
        self.assertEqual(engine.begin().commit(), 1)

    def test_unrelated_commits_and_last_write(self):
        engine = Engine({"a": 1})
        reader = engine.begin()
        reader.get("a")
        reader.scan("jobs/")
        writer = engine.begin()
        writer.put("b", 1)
        writer.delete("b")
        writer.put("b", 3)
        writer.commit()
        self.assertEqual(reader.commit(), 1)
        self.assertEqual(engine.log_since(0)[0]["writes"], [["b", 3]])

    def test_savepoint_stack_and_retained_reads(self):
        engine = Engine()
        tx = engine.begin()
        tx.put("a", 1)
        tx.savepoint("first")
        tx.put("a", 2)
        tx.savepoint("second")
        tx.get("read")
        tx.scan("range/")
        tx.rollback_to("first")
        self.assertEqual(tx.get("a"), 1)
        with self.assertRaises(ValueError):
            tx.release("second")
        with self.assertRaises(ValueError):
            tx.savepoint("first")
        tx.put("a", 4)
        tx.rollback_to("first")
        self.assertEqual(tx.get("a"), 1)
        writer = engine.begin()
        writer.put("range/new", 3)
        writer.commit()
        with self.assertRaises(Conflict):
            tx.commit()

    def test_release_keeps_writes_and_reuses_names(self):
        engine = Engine()
        tx = engine.begin()
        tx.savepoint("one")
        tx.savepoint("two")
        tx.put("a", 2)
        tx.release("one")
        tx.savepoint("two")
        tx.put("a", 3)
        tx.rollback_to("two")
        tx.commit()
        self.assertEqual(engine.checkpoint()["data"], {"a": 2})

    def test_invalid_operations_do_not_track_reads_or_writes(self):
        engine = Engine({"a": 1})
        tx = engine.begin()
        tx.savepoint("s")
        calls = [lambda: tx.get(""), lambda: tx.scan(None),
                 lambda: tx.put("a", True), lambda: tx.put("a", None),
                 lambda: tx.delete(1), lambda: tx.savepoint("s"),
                 lambda: tx.rollback_to("absent"), lambda: tx.release([])]
        for call in calls:
            with self.assertRaises(ValueError):
                call()
        writer = engine.begin()
        writer.put("a", 2)
        writer.commit()
        self.assertEqual(tx.commit(), 1)
        self.assertEqual(engine.checkpoint()["data"], {"a": 2})

    def test_closed_precedence(self):
        for closing in ("commit", "abort", "conflict"):
            engine = Engine()
            tx = engine.begin()
            if closing == "conflict":
                tx.get("a")
                writer = engine.begin()
                writer.put("a", 1)
                writer.commit()
                with self.assertRaises(Conflict):
                    tx.commit()
            else:
                getattr(tx, closing)()
            for method, args in [("get", ([],)), ("scan", (None,)),
                                 ("put", (None, True)), ("delete", (None,)),
                                 ("savepoint", (None,)), ("rollback_to", (None,)),
                                 ("release", (None,)), ("commit", ()), ("abort", ())]:
                with self.subTest(closing=closing, method=method):
                    with self.assertRaises(RuntimeError):
                        getattr(tx, method)(*args)

    def test_recovery_base_and_defensive_copies(self):
        checkpoint = {"version": 7, "data": {"z": 1, "a": 2}}
        records = [{"version": 8, "writes": [["gone", None], ["z", 3]]},
                   {"version": 9, "writes": [["a", None]]}]
        engine = Engine.restore(checkpoint, records)
        checkpoint["data"]["z"] = 99
        records[0]["writes"][1][1] = 99
        self.assertEqual(engine.checkpoint(), {"version": 9, "data": {"z": 3}})
        exported = engine.log_since(7)
        exported[0]["writes"].clear()
        self.assertEqual(len(engine.log_since(7)[0]["writes"]), 2)
        for invalid in (6, 10, True, 7.0, None):
            with self.assertRaises(ValueError):
                engine.log_since(invalid)
        self.assertEqual(engine.log_since(9), [])
        tx = engine.begin()
        tx.get("z")
        self.assertEqual(tx.commit(), 9)
        tx = engine.begin()
        tx.put("b", 4)
        self.assertEqual(tx.commit(), 10)
        restored = Engine.restore({"version": 7, "data": {"z": 1, "a": 2}},
                                  engine.log_since(7))
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        self.assertEqual(list(engine.checkpoint()["data"]), ["b", "z"])

    def test_recovery_validation(self):
        cp = {"version": 0, "data": {}}
        invalid_checkpoints = [None, {}, {**cp, "extra": 1},
                               {"version": True, "data": {}},
                               {"version": -1, "data": {}},
                               {"version": 0, "data": {"": 1}},
                               {"version": 0, "data": {"a": None}}]
        for checkpoint in invalid_checkpoints:
            with self.assertRaises(ValueError):
                Engine.restore(checkpoint, [])
        invalid_records = [None, (), [{}], [{"version": True, "writes": [["a", 1]]}],
                           [{"version": 2, "writes": [["a", 1]]}]]
        for writes in ([], (), [["a", True]], [["a", 1], ["a", 2]],
                       [["b", 1], ["a", 2]], [("a", 1)], [["a"]], [["", 1]]):
            invalid_records.append([{"version": 1, "writes": writes}])
        invalid_records.append([{"version": 1, "writes": [["a", 1]]},
                                {"version": 3, "writes": [["b", 2]]}])
        for records in invalid_records:
            before = copy.deepcopy(records)
            with self.assertRaises(ValueError):
                Engine.restore(cp, records)
            self.assertEqual(records, before)
        for initial in ([], {"": 1}, {"a": True}, {"a": None}, {1: 2}):
            with self.assertRaises(ValueError):
                Engine(initial)


if __name__ == "__main__":
    unittest.main()
