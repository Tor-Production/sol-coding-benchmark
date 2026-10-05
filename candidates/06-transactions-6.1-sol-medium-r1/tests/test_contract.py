import copy
import random
import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_history_conflicts_even_when_values_return_to_snapshot(self):
        for dependency in ("get", "scan", "put"):
            with self.subTest(dependency=dependency):
                engine = Engine({"x": 1})
                reader = engine.begin()
                if dependency == "get":
                    reader.get("x")
                elif dependency == "scan":
                    reader.scan("x")
                else:
                    reader.put("x", 1)
                for value in (2, 1):
                    writer = engine.begin()
                    writer.put("x", value)
                    writer.commit()
                before = engine.checkpoint(), engine.log_since(0)
                with self.assertRaises(Conflict):
                    reader.commit()
                self.assertEqual(before, (engine.checkpoint(), engine.log_since(0)))
                with self.assertRaises(RuntimeError):
                    reader.abort()

    def test_empty_scan_and_absent_reads(self):
        engine = Engine()
        scan, read = engine.begin(), engine.begin()
        self.assertEqual(scan.scan("job/"), {})
        self.assertIsNone(read.get("job/one"))
        insert = engine.begin()
        insert.put("job/one", 5)
        insert.commit()
        delete = engine.begin()
        delete.delete("job/one")
        delete.commit()
        for transaction in (scan, read):
            with self.assertRaises(Conflict):
                transaction.commit()

    def test_snapshot_visibility_order_and_noop_writes(self):
        engine = Engine({"z": 3, "a": 1})
        old = engine.begin()
        writer = engine.begin()
        writer.put("a", 1)
        writer.delete("missing")
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["a", 1], ["missing", None]]}])
        old.put("b", -(10 ** 100))
        old.delete("z")
        visible = old.scan()
        self.assertEqual(list(visible), ["a", "b"])
        self.assertEqual(visible["b"], -(10 ** 100))
        visible.clear()
        self.assertEqual(old.get("a"), 1)
        with self.assertRaises(Conflict):
            old.commit()
        self.assertEqual(list(engine.checkpoint()["data"]), ["a", "z"])
        self.assertEqual(engine.begin().commit(), 1)

    def test_savepoint_stack_and_retained_reads(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("outer")
        transaction.put("a", 2)
        transaction.savepoint("inner")
        transaction.delete("a")
        transaction.get("read")
        transaction.scan("range/")
        transaction.rollback_to("outer")
        self.assertEqual(transaction.get("a"), 1)
        with self.assertRaises(ValueError):
            transaction.rollback_to("inner")
        with self.assertRaises(ValueError):
            transaction.savepoint("outer")
        transaction.put("a", 3)
        transaction.rollback_to("outer")
        self.assertEqual(transaction.get("a"), 1)
        transaction.savepoint("inner")
        transaction.put("a", 4)
        transaction.release("outer")
        transaction.savepoint("outer")
        self.assertEqual(transaction.get("a"), 4)
        writer = engine.begin()
        writer.put("range/new", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            transaction.commit()

    def test_rolled_back_writes_do_not_remain_dependencies(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("s")
        transaction.put("x", 1)
        transaction.rollback_to("s")
        other = engine.begin()
        other.put("x", 2)
        other.commit()
        self.assertEqual(transaction.commit(), 1)

    def test_invalid_operations_are_atomic_and_closed_checks_take_precedence(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        transaction.put("kept", 2)
        transaction.savepoint("s")
        operations = [
            lambda: transaction.get([]),
            lambda: transaction.get(""),
            lambda: transaction.scan(None),
            lambda: transaction.put("x", True),
            lambda: transaction.put("x", None),
            lambda: transaction.delete(3),
            lambda: transaction.savepoint("s"),
            lambda: transaction.savepoint([]),
            lambda: transaction.rollback_to("unknown"),
            lambda: transaction.release("unknown"),
        ]
        for operation in operations:
            with self.assertRaises(ValueError):
                operation()
        # Invalid get/put/scan calls must not create dependencies on x or all keys.
        writer = engine.begin()
        writer.put("x", 7)
        writer.commit()
        self.assertEqual(transaction.commit(), 2)
        self.assertEqual(engine.checkpoint()["data"], {"kept": 2, "x": 7})
        for operation in operations + [transaction.commit, transaction.abort]:
            with self.assertRaises(RuntimeError):
                operation()
        aborted = engine.begin()
        aborted.abort()
        with self.assertRaises(RuntimeError):
            aborted.abort()

    def test_recovery_and_export_defensive_copies(self):
        initial = {"z": 8, "a": 2}
        engine = Engine(initial)
        initial.clear()
        checkpoint = engine.checkpoint()
        for key, value in (("a", None), ("new", 9), ("absent", None)):
            writer = engine.begin()
            if value is None:
                writer.delete(key)
            else:
                writer.put(key, value)
            writer.commit()
        records = engine.log_since(0)
        restored = Engine.restore(checkpoint, records)
        expected = engine.checkpoint()
        checkpoint["data"].clear()
        records[0]["writes"][0][1] = 100
        records.clear()
        self.assertEqual(restored.checkpoint(), expected)
        exported = restored.log_since(0)
        exported[0]["writes"].append(["bad", 2])
        exported[1]["version"] = 100
        self.assertEqual(restored.log_since(0), engine.log_since(0))
        checkpoint2 = restored.checkpoint()
        restored2 = Engine.restore(checkpoint2, [])
        with self.assertRaises(ValueError):
            restored2.log_since(2)
        self.assertEqual(restored2.log_since(3), [])
        writer = restored2.begin()
        writer.put("new", 9)
        self.assertEqual(writer.commit(), 4)
        self.assertEqual(restored2.log_since(3), [
            {"version": 4, "writes": [["new", 9]]}])

    def test_invalid_recovery_inputs(self):
        checkpoint = {"version": 4, "data": {"x": 1}}
        invalid_checkpoints = [None, [], {}, {**checkpoint, "extra": 1},
                               {"version": True, "data": {}},
                               {"version": -1, "data": {}},
                               {"version": 0, "data": {"": 1}},
                               {"version": 0, "data": {"x": None}}]
        for invalid in invalid_checkpoints:
            with self.subTest(checkpoint=invalid), self.assertRaises(ValueError):
                Engine.restore(invalid, [])
        invalid_records = [None, (), [None], [{}],
                           [{"version": 5, "writes": []}],
                           [{"version": True, "writes": [["x", 1]]}],
                           [{"version": 6, "writes": [["x", 1]]}],
                           [{"version": 5, "writes": [("x", 1)]}],
                           [{"version": 5, "writes": [["x", True]]}],
                           [{"version": 5, "writes": [["x", 1], ["x", 2]]}],
                           [{"version": 5, "writes": [["z", 1], ["a", 2]]}],
                           [{"version": 5, "writes": [["", 1]]}],
                           [{"version": 5, "writes": [["x", 1]], "extra": 0}]]
        for invalid in invalid_records:
            original = copy.deepcopy(invalid)
            with self.subTest(records=invalid), self.assertRaises(ValueError):
                Engine.restore(checkpoint, invalid)
            self.assertEqual(invalid, original)
            self.assertEqual(checkpoint, {"version": 4, "data": {"x": 1}})
        for invalid in (False, -1, 1, 0.0, None, "0"):
            with self.assertRaises(ValueError):
                Engine().log_since(invalid)
        for invalid in ([], {"": 1}, {"x": True}, {"x": None}, {1: 2}):
            with self.assertRaises(ValueError):
                Engine(invalid)

    def test_deterministic_interleaved_trace(self):
        rng = random.Random(20261003)
        engine = Engine({"a": 1})
        data, history, active = {"a": 1}, [], []
        keys = ["a", "b", "job/a", "job/b", "other"]
        for _ in range(400):
            if not active or rng.randrange(5) == 0:
                active.append([engine.begin(), data.copy(), len(history), {}, set(), set()])
                continue
            model = rng.choice(active)
            transaction, snapshot, version, writes, reads, prefixes = model
            operation = rng.choice(["get", "scan", "put", "delete", "commit", "abort"])
            key = rng.choice(keys)
            if operation == "get":
                reads.add(key)
                self.assertEqual(transaction.get(key), writes.get(key, snapshot.get(key)))
            elif operation == "scan":
                prefix = rng.choice(["", "job/", "absent/", "a"])
                prefixes.add(prefix)
                visible = {**snapshot, **writes}
                expected = {k: visible[k] for k in sorted(visible)
                            if visible[k] is not None and k.startswith(prefix)}
                actual = transaction.scan(prefix)
                self.assertEqual(actual, expected)
                self.assertEqual(list(actual), list(expected))
            elif operation in ("put", "delete"):
                value = rng.randint(-3, 3) if operation == "put" else None
                writes[key] = value
                if value is None:
                    transaction.delete(key)
                else:
                    transaction.put(key, value)
            elif operation == "abort":
                transaction.abort()
                active.remove(model)
            else:
                touched = {k for record in history[version:] for k in record}
                conflict = bool(touched & (reads | writes.keys())) or any(
                    k.startswith(p) for k in touched for p in prefixes)
                if conflict:
                    with self.assertRaises(Conflict):
                        transaction.commit()
                else:
                    if writes:
                        history.append(writes.copy())
                        for k, value in writes.items():
                            if value is None:
                                data.pop(k, None)
                            else:
                                data[k] = value
                    self.assertEqual(transaction.commit(), len(history))
                active.remove(model)
            self.assertEqual(engine.checkpoint(), {"version": len(history), "data": data})
        recovered = Engine.restore({"version": 0, "data": {"a": 1}}, engine.log_since(0))
        self.assertEqual(recovered.checkpoint(), engine.checkpoint())


if __name__ == "__main__":
    unittest.main()
