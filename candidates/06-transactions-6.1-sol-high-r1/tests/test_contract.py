"""Behavioral checks for isolation, validation, savepoints, and recovery."""

import copy
import random
import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_snapshot_and_own_writes(self):
        initial = {"z": 9, "a": 1, "p/b": 2, "p/a": 3}
        engine = Engine(initial)
        initial["a"] = 99
        old = engine.begin()
        writer = engine.begin()
        writer.put("a", 5)
        writer.delete("p/a")
        writer.put("new", 7)
        writer.commit()
        self.assertEqual(old.get("a"), 1)
        self.assertIsNone(old.get("new"))
        old.put("p/c", -(10 ** 200))
        old.delete("p/b")
        self.assertEqual(old.scan("p/"), {"p/a": 3, "p/c": -(10 ** 200)})
        self.assertEqual(list(old.scan()), ["a", "p/a", "p/c", "z"])
        old.abort()
        self.assertEqual(list(engine.checkpoint()["data"]), ["a", "new", "p/b", "z"])

    def test_each_pending_write_creates_a_version(self):
        engine = Engine({"x": 1})
        empty = engine.begin()
        self.assertEqual(empty.commit(), 0)
        writer = engine.begin()
        writer.put("x", 2)
        writer.put("x", 1)
        writer.delete("absent")
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["absent", None], ["x", 1]]}])
        empty = engine.begin()
        later = engine.begin()
        later.delete("absent")
        later.commit()
        self.assertEqual(empty.commit(), 2)

    def test_conflicts_use_write_history(self):
        for read_kind in ("get", "scan", "put", "delete"):
            for changes in ((2, 1), (None, 1), (1,), (None, None)):
                with self.subTest(read_kind=read_kind, changes=changes):
                    engine = Engine({"x": 1})
                    old = engine.begin()
                    if read_kind == "get":
                        old.get("x")
                    elif read_kind == "scan":
                        old.scan("x")
                    elif read_kind == "put":
                        old.put("x", 7)
                    else:
                        old.delete("x")
                    for value in changes:
                        writer = engine.begin()
                        if value is None:
                            writer.delete("x")
                        else:
                            writer.put("x", value)
                        writer.commit()
                    before = engine.checkpoint(), engine.log_since(0)
                    with self.assertRaises(Conflict):
                        old.commit()
                    self.assertEqual((engine.checkpoint(), engine.log_since(0)), before)
                    with self.assertRaises(RuntimeError):
                        old.abort()

    def test_absent_reads_and_empty_ranges(self):
        for prefix in (None, "job/", ""):
            with self.subTest(prefix=prefix):
                engine = Engine()
                reader = engine.begin()
                if prefix is None:
                    self.assertIsNone(reader.get("job/1"))
                else:
                    self.assertEqual(reader.scan(prefix), {})
                writer = engine.begin()
                writer.put("job/1", 4)
                writer.commit()
                remover = engine.begin()
                remover.delete("job/1")
                remover.commit()
                with self.assertRaises(Conflict):
                    reader.commit()

    def test_unrelated_writes_and_prefix_boundaries(self):
        engine = Engine({"a": 1})
        reader = engine.begin()
        reader.get("a")
        reader.scan("job/")
        reader.put("private", 2)
        writer = engine.begin()
        writer.put("job", 3)
        writer.put("jobs/1", 4)
        writer.put("other", 5)
        writer.commit()
        self.assertEqual(reader.commit(), 2)
        self.assertEqual(engine.checkpoint()["data"]["private"], 2)

    def test_savepoint_stack_and_repeated_rollback(self):
        engine = Engine({"a": 0})
        tx = engine.begin()
        tx.put("a", 1)
        tx.savepoint("outer")
        tx.delete("a")
        tx.savepoint("inner")
        tx.put("b", 2)
        tx.rollback_to("inner")
        self.assertEqual(tx.scan(), {})
        tx.put("c", 3)
        tx.rollback_to("inner")
        self.assertEqual(tx.scan(), {})
        tx.rollback_to("outer")
        self.assertEqual(tx.scan(), {"a": 1})
        with self.assertRaises(ValueError):
            tx.release("inner")
        with self.assertRaises(ValueError):
            tx.savepoint("outer")
        tx.savepoint("inner")
        tx.put("b", 2)
        tx.release("outer")
        self.assertEqual(tx.scan(), {"a": 1, "b": 2})
        for name in ("outer", "inner"):
            with self.assertRaises(ValueError):
                tx.rollback_to(name)
            tx.savepoint(name)
        self.assertEqual(tx.commit(), 1)

    def test_reads_survive_savepoint_operations(self):
        for read_kind in ("absent", "own_write", "range"):
            for operation in ("rollback_to", "release"):
                with self.subTest(read_kind=read_kind, operation=operation):
                    engine = Engine()
                    tx = engine.begin()
                    tx.savepoint("s")
                    if read_kind == "absent":
                        tx.get("target")
                    elif read_kind == "own_write":
                        tx.put("target", 8)
                        self.assertEqual(tx.get("target"), 8)
                    else:
                        tx.scan("tar")
                    getattr(tx, operation)("s")
                    writer = engine.begin()
                    writer.put("target", 9)
                    writer.commit()
                    with self.assertRaises(Conflict):
                        tx.commit()

    def test_rollback_restores_pending_write_set(self):
        engine = Engine()
        tx = engine.begin()
        tx.savepoint("s")
        tx.put("discarded", 1)
        tx.rollback_to("s")
        other = engine.begin()
        other.put("discarded", 2)
        other.commit()
        self.assertEqual(tx.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["discarded", 2]]}])

    def test_invalid_arguments_leave_no_dependencies_or_writes(self):
        engine = Engine()
        tx = engine.begin()
        tx.savepoint("s")
        for bad in (None, True, 2, "", [], {}):
            for method in (tx.get, tx.delete, tx.savepoint, tx.rollback_to, tx.release):
                with self.subTest(method=method.__name__, bad=bad):
                    with self.assertRaises(ValueError):
                        method(bad)
            with self.assertRaises(ValueError):
                tx.put(bad, 1)
        for bad in (None, True, False, 1.0, "1", [], {}):
            with self.assertRaises(ValueError):
                tx.put("key", bad)
        for bad in (None, True, 1, [], {}):
            with self.assertRaises(ValueError):
                tx.scan(bad)
        for name in ("missing",):
            with self.assertRaises(ValueError):
                tx.rollback_to(name)
            with self.assertRaises(ValueError):
                tx.release(name)
        with self.assertRaises(ValueError):
            tx.savepoint("s")
        writer = engine.begin()
        writer.put("key", 1)
        writer.commit()
        # The invalid put did not stage a write or register a read of key.
        self.assertEqual(tx.commit(), 1)
        self.assertEqual(engine.checkpoint(), {"version": 1, "data": {"key": 1}})

    def test_closed_methods_check_state_before_arguments(self):
        for closing in ("commit", "abort", "conflict"):
            engine = Engine()
            tx = engine.begin()
            tx.put("x", 1)
            if closing == "conflict":
                writer = engine.begin()
                writer.put("x", 2)
                writer.commit()
                with self.assertRaises(Conflict):
                    tx.commit()
            else:
                getattr(tx, closing)()
            before = engine.checkpoint(), engine.log_since(0)
            calls = ((tx.get, (None,)), (tx.scan, (None,)),
                     (tx.put, (None, None)), (tx.delete, (None,)),
                     (tx.savepoint, (None,)), (tx.rollback_to, (None,)),
                     (tx.release, (None,)), (tx.commit, ()), (tx.abort, ()))
            for method, arguments in calls:
                with self.subTest(closing=closing, method=method.__name__):
                    with self.assertRaises(RuntimeError):
                        method(*arguments)
            self.assertEqual((engine.checkpoint(), engine.log_since(0)), before)

    def test_exports_and_restore_inputs_are_detached(self):
        initial = {"z": 1}
        engine = Engine(initial)
        checkpoint = engine.checkpoint()
        pending = engine.begin()
        pending.put("pending", 4)
        tx = engine.begin()
        tx.put("a", 2)
        tx.delete("missing")
        tx.commit()
        log = engine.log_since(0)
        restored = Engine.restore(checkpoint, log)
        expected = engine.checkpoint()
        initial.clear()
        checkpoint["version"] = 100
        checkpoint["data"].clear()
        log[0]["version"] = 100
        log[0]["writes"][0][1] = 99
        log[0]["writes"].append(["injected", 7])
        log.clear()
        exported = restored.log_since(0)
        exported[0]["writes"].clear()
        exported.clear()
        view = restored.begin().scan()
        view.clear()
        cp = restored.checkpoint()
        cp["data"].clear()
        self.assertEqual(engine.checkpoint(), expected)
        self.assertEqual(restored.checkpoint(), expected)
        self.assertEqual(restored.log_since(0), engine.log_since(0))
        pending.abort()

    def test_recovery_base_and_post_recovery_conflicts(self):
        checkpoint = {"version": 10 ** 50, "data": {"x": 1}}
        base = checkpoint["version"]
        records = [
            {"version": base + 1, "writes": [["missing", None], ["x", 1]]},
            {"version": base + 2, "writes": [["x", None], ["z", -5]]},
        ]
        engine = Engine.restore(checkpoint, records)
        self.assertEqual(engine.version, base + 2)
        self.assertEqual(engine.checkpoint()["data"], {"z": -5})
        self.assertEqual(engine.log_since(base), records)
        self.assertEqual(engine.log_since(base + 1), records[1:])
        self.assertEqual(engine.log_since(base + 2), [])
        for invalid in (base - 1, base + 3, True, None, 1.0, "1"):
            with self.assertRaises(ValueError):
                engine.log_since(invalid)
        fresh = engine.begin()
        fresh.get("x")
        self.assertEqual(fresh.commit(), base + 2)
        old = engine.begin()
        old.scan("z")
        writer = engine.begin()
        writer.put("z", -5)
        self.assertEqual(writer.commit(), base + 3)
        with self.assertRaises(Conflict):
            old.commit()
        roundtrip = Engine.restore(engine.checkpoint(), [])
        self.assertEqual(roundtrip.checkpoint(), engine.checkpoint())
        self.assertEqual(roundtrip.log_since(base + 3), [])
        with self.assertRaises(ValueError):
            roundtrip.log_since(base + 2)

    def test_invalid_initial_and_checkpoint_shapes(self):
        for invalid in ([], (), True, 1, "data", {"": 1}, {1: 2},
                        {"x": None}, {"x": True}, {"x": 2.0}):
            with self.subTest(initial=invalid):
                with self.assertRaises(ValueError):
                    Engine(invalid)
        invalid_checkpoints = [None, [], {}, {"version": 0}, {"data": {}},
                               {"version": 0, "data": {}, "extra": 1}]
        invalid_checkpoints += [{"version": v, "data": {}}
                                for v in (-1, True, None, 0.0, "0")]
        invalid_checkpoints += [{"version": 0, "data": d}
                                for d in (None, [], {"": 1}, {"x": True})]
        for checkpoint in invalid_checkpoints:
            with self.subTest(checkpoint=checkpoint):
                before = copy.deepcopy(checkpoint)
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, [])
                self.assertEqual(checkpoint, before)

    def test_invalid_recovery_logs(self):
        cp = {"version": 0, "data": {"x": 1}}
        invalid = [None, {}, (), [None], [{}], [{"version": 1}],
                   [{"version": 1, "writes": [], "extra": 1}]]
        invalid += [[{"version": v, "writes": [["x", 2]]}]
                    for v in (0, 2, -1, True, None, 1.0)]
        invalid += [[{"version": 1, "writes": writes}]
                    for writes in (None, {}, (), [], [("x", 1)], [["x"]],
                                   [["x", 1, 2]], [[None, 1]], [["", 1]],
                                   [["x", True]], [["x", "1"]],
                                   [["z", 1], ["a", 2]], [["x", 1], ["x", 2]])]
        first = {"version": 1, "writes": [["x", 2]]}
        invalid += [[first, {"version": 3, "writes": [["y", 3]]}],
                    [first, {"version": 2, "writes": [["y", False]]}]]
        for records in invalid:
            with self.subTest(records=records):
                before = copy.deepcopy(records)
                with self.assertRaises(ValueError):
                    Engine.restore(cp, records)
                self.assertEqual(records, before)
                self.assertEqual(cp, {"version": 0, "data": {"x": 1}})

    def test_deterministic_interleaved_trace_and_roundtrips(self):
        # This oracle validates against per-key last-write versions rather than
        # the engine's retained records, including writes with no data change.
        rng = random.Random(8128)
        engine = Engine({"a": 0, "p/1": 1})
        committed = {"a": 0, "p/1": 1}
        version = 0
        changed = {}
        active = []
        keys = ["a", "b", "p/1", "p/2", "q/1"]
        prefixes = ["", "p/", "q/", "missing/", "a"]

        def finish(entry, abort=False):
            nonlocal version
            tx, state = entry
            if abort:
                tx.abort()
                return
            conflict = any(
                touched_version > state["version"]
                and (key in state["reads"] or key in state["writes"]
                     or any(key.startswith(prefix) for prefix in state["ranges"]))
                for key, touched_version in changed.items())
            if conflict:
                with self.assertRaises(Conflict):
                    tx.commit()
            else:
                if state["writes"]:
                    version += 1
                    for key, value in state["writes"].items():
                        changed[key] = version
                        if value is None:
                            committed.pop(key, None)
                        else:
                            committed[key] = value
                self.assertEqual(tx.commit(), version)
            self.assertEqual(engine.checkpoint(), {"version": version, "data": committed})

        for step in range(600):
            if not active or (len(active) < 6 and rng.randrange(5) == 0):
                active.append((engine.begin(), {"snapshot": dict(committed),
                              "version": version, "writes": {}, "reads": set(),
                              "ranges": set(), "saves": []}))
            tx, state = entry = rng.choice(active)
            operation = rng.choice(["get", "scan", "put", "delete", "save",
                                    "rollback", "release", "commit", "abort"])
            key = rng.choice(keys)
            visible = dict(state["snapshot"])
            for written, value in state["writes"].items():
                if value is None:
                    visible.pop(written, None)
                else:
                    visible[written] = value
            if operation == "get":
                self.assertEqual(tx.get(key), visible.get(key))
                state["reads"].add(key)
            elif operation == "scan":
                prefix = rng.choice(prefixes)
                expected = {k: visible[k] for k in sorted(visible) if k.startswith(prefix)}
                actual = tx.scan(prefix)
                self.assertEqual(actual, expected)
                self.assertEqual(list(actual), list(expected))
                state["ranges"].add(prefix)
            elif operation == "put":
                value = rng.choice([0, -1, 10 ** 100, visible.get(key, 7)])
                tx.put(key, value)
                state["writes"][key] = value
            elif operation == "delete":
                tx.delete(key)
                state["writes"][key] = None
            elif operation == "save":
                name = "s" + str(step)
                tx.savepoint(name)
                state["saves"].append((name, dict(state["writes"])))
            elif operation in ("rollback", "release") and state["saves"]:
                index = rng.randrange(len(state["saves"]))
                name, writes = state["saves"][index]
                if operation == "rollback":
                    tx.rollback_to(name)
                    state["writes"] = dict(writes)
                    del state["saves"][index + 1:]
                else:
                    tx.release(name)
                    del state["saves"][index:]
            elif operation in ("commit", "abort"):
                finish(entry, abort=operation == "abort")
                active.remove(entry)
            if step % 30 == 0:
                checkpoint = engine.checkpoint()
                for remaining in active:
                    finish(remaining, abort=True)
                active.clear()
                writer = engine.begin()
                writer.put("recovery", step)
                version += 1
                committed["recovery"] = step
                changed["recovery"] = version
                self.assertEqual(writer.commit(), version)
                log = engine.log_since(checkpoint["version"])
                engine = Engine.restore(checkpoint, log)
                self.assertEqual(engine.log_since(checkpoint["version"]), log)
                self.assertEqual(engine.checkpoint()["data"], committed)
        for entry in active:
            finish(entry)


if __name__ == "__main__":
    unittest.main()
