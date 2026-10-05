import copy
import unittest

from mvcc import Conflict, Engine


class TransactionContract(unittest.TestCase):
    def test_snapshot_overlay_sorted_results_and_no_op_writes(self):
        source = {"z": 9, "a": 1}
        engine = Engine(source)
        source["a"] = 100
        first = engine.begin()
        second = engine.begin()
        second.put("a", 2)
        self.assertEqual(second.commit(), 1)

        self.assertEqual(first.get("a"), 1)
        first.delete("z")
        first.put("b", -10**100)
        self.assertEqual(first.scan(), {"a": 1, "b": -10**100})
        with self.assertRaises(Conflict):
            first.commit()

        writer = engine.begin()
        writer.put("a", 2)
        writer.delete("missing")
        self.assertEqual(writer.commit(), 2)
        self.assertEqual(engine.log_since(1), [
            {"version": 2, "writes": [["a", 2], ["missing", None]]}
        ])
        self.assertEqual(engine.checkpoint(), {"version": 2, "data": {"a": 2, "z": 9}})

        empty = engine.begin()
        self.assertEqual(empty.commit(), 2)
        self.assertEqual(engine.version, 2)

    def test_history_conflicts_even_when_current_value_is_unchanged(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 1)
        same_value = engine.begin()
        same_value.put("x", 1)
        same_value.commit()
        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.abort()

        scanner = engine.begin()
        self.assertEqual(scanner.scan("job/"), {})
        insert = engine.begin()
        insert.put("job/1", 5)
        insert.commit()
        remove = engine.begin()
        remove.delete("job/1")
        remove.commit()
        with self.assertRaises(Conflict):
            scanner.commit()

        unrelated = engine.begin()
        unrelated.get("x")
        other = engine.begin()
        other.put("y", 7)
        other.commit()
        self.assertEqual(unrelated.commit(), engine.version)

    def test_savepoints_keep_reads_but_restore_writes(self):
        engine = Engine({"x": 1})
        tx = engine.begin()
        tx.put("a", 1)
        tx.savepoint("first")
        tx.put("a", 2)
        tx.savepoint("second")
        tx.delete("a")
        tx.get("x")
        tx.rollback_to("first")
        self.assertEqual(tx.get("a"), 1)
        with self.assertRaises(ValueError):
            tx.release("second")
        with self.assertRaises(ValueError):
            tx.savepoint("first")
        tx.savepoint("second")
        tx.put("b", 3)
        tx.release("first")
        self.assertEqual(tx.scan("b"), {"b": 3})
        with self.assertRaises(ValueError):
            tx.rollback_to("second")

        other = engine.begin()
        other.put("x", 4)
        other.commit()
        with self.assertRaises(Conflict):
            tx.commit()
        self.assertEqual(engine.checkpoint()["data"], {"x": 4})

    def test_rollback_discards_write_conflict_but_not_range_read(self):
        engine = Engine()
        tx = engine.begin()
        tx.savepoint("before")
        tx.put("x", 1)
        tx.rollback_to("before")
        other = engine.begin()
        other.put("x", 2)
        other.commit()
        self.assertEqual(tx.commit(), 1)

        tx = engine.begin()
        tx.savepoint("before")
        tx.scan("p/")
        tx.rollback_to("before")
        other = engine.begin()
        other.delete("p/absent")
        other.commit()
        with self.assertRaises(Conflict):
            tx.commit()

    def test_invalid_calls_do_not_change_staged_writes_and_closed_precedes_validation(self):
        engine = Engine()
        tx = engine.begin()
        tx.put("a", 1)
        tx.savepoint("s")
        invalid_calls = [
            lambda: tx.get(""),
            lambda: tx.scan(None),
            lambda: tx.put("a", True),
            lambda: tx.put("", 2),
            lambda: tx.delete(3),
            lambda: tx.savepoint("s"),
            lambda: tx.rollback_to("missing"),
            lambda: tx.release("missing"),
        ]
        for call in invalid_calls:
            with self.subTest(call=call), self.assertRaises(ValueError):
                call()
        self.assertEqual(tx.commit(), 1)
        self.assertEqual(engine.checkpoint()["data"], {"a": 1})
        for call in [
            lambda: tx.get(None), lambda: tx.scan(None),
            lambda: tx.put(None, None), lambda: tx.delete(None),
            lambda: tx.savepoint(None), lambda: tx.rollback_to(None),
            lambda: tx.release(None), tx.commit, tx.abort,
        ]:
            with self.subTest(call=call), self.assertRaises(RuntimeError):
                call()


class RecoveryContract(unittest.TestCase):
    def test_round_trip_base_version_and_defensive_copies(self):
        engine = Engine({"b": 2, "a": 1})
        old = engine.begin()
        old.put("c", 3)
        old.commit()
        checkpoint = engine.checkpoint()
        tx = engine.begin()
        tx.delete("a")
        tx.put("z", 10**200)
        tx.commit()
        exported_log = engine.log_since(checkpoint["version"])
        restored = Engine.restore(checkpoint, exported_log)
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        self.assertEqual(restored.log_since(1), exported_log)
        self.assertEqual(list(restored.checkpoint()["data"]), ["b", "c", "z"])
        with self.assertRaises(ValueError):
            restored.log_since(0)

        checkpoint["data"]["b"] = 999
        exported_log[0]["writes"][0][0] = "changed"
        self.assertEqual(restored.checkpoint(), {
            "version": 2, "data": {"b": 2, "c": 3, "z": 10**200}
        })
        exported_again = restored.log_since(1)
        exported_again[0]["writes"][0][1] = 999
        exported_again.append({"version": 3, "writes": [["bad", 1]]})
        self.assertEqual(restored.log_since(1), [
            {"version": 2, "writes": [["a", None], ["z", 10**200]]}
        ])

        # New transactions use the restored current state as their snapshot.
        reader = restored.begin()
        self.assertEqual(reader.get("b"), 2)
        self.assertEqual(reader.commit(), 2)

    def test_invalid_restore_shapes(self):
        checkpoint = {"version": 3, "data": {"a": 1}}
        valid_record = {"version": 4, "writes": [["a", None], ["b", 2]]}
        bad_checkpoints = [
            None, {}, {"version": 0, "data": {}, "extra": 1},
            {"version": True, "data": {}}, {"version": -1, "data": {}},
            {"version": 0, "data": []}, {"version": 0, "data": {"": 1}},
            {"version": 0, "data": {"x": None}},
            {"version": 0, "data": {"x": False}},
        ]
        for bad in bad_checkpoints:
            with self.subTest(checkpoint=bad), self.assertRaises(ValueError):
                Engine.restore(bad, [])

        bad_logs = [
            None, (), [{}], [{"version": 4, "writes": []}],
            [{"version": True, "writes": [["a", 1]]}],
            [{"version": 5, "writes": [["a", 1]]}],
            [{"version": 4, "writes": [["a", 1]], "extra": 0}],
            [{"version": 4, "writes": [("a", 1)]}],
            [{"version": 4, "writes": [["b", 1], ["a", 2]]}],
            [{"version": 4, "writes": [["a", 1], ["a", 2]]}],
            [{"version": 4, "writes": [["a", True]]}],
            [{"version": 4, "writes": [["a", None, 1]]}],
            [valid_record, {"version": 6, "writes": [["c", 3]]}],
        ]
        original = copy.deepcopy(checkpoint)
        for bad in bad_logs:
            with self.subTest(records=bad), self.assertRaises(ValueError):
                Engine.restore(checkpoint, bad)
            self.assertEqual(checkpoint, original)

    def test_version_validation(self):
        engine = Engine()
        for version in [True, None, -1, 1, 0.0, "0"]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                engine.log_since(version)
        self.assertEqual(engine.log_since(0), [])
        for initial in [[], {"": 1}, {"a": True}, {"a": None}]:
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                Engine(initial)


if __name__ == "__main__":
    unittest.main()
