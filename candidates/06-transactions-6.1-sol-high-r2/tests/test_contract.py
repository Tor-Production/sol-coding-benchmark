import copy
import random
import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def write(self, engine, key, value):
        transaction = engine.begin()
        if value is None:
            transaction.delete(key)
        else:
            transaction.put(key, value)
        return transaction.commit()

    def test_snapshot_visibility_and_sorted_defensive_outputs(self):
        initial = {"z": 9, "a": 1}
        engine = Engine(initial)
        initial["a"] = 99
        old = engine.begin()
        self.write(engine, "a", 2)
        self.assertEqual(old.get("a"), 1)
        old.put("b", -(10 ** 200))
        old.delete("z")
        visible = old.scan()
        self.assertEqual(list(visible), ["a", "b"])
        visible["a"] = 88
        self.assertEqual(old.get("a"), 1)
        checkpoint = engine.checkpoint()
        self.assertEqual(list(checkpoint["data"]), ["a", "z"])
        checkpoint["data"].clear()
        self.assertEqual(engine.begin().get("a"), 2)
        old.abort()

    def test_last_write_and_noop_writes_create_versions(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        self.assertIsNone(transaction.put("z", 7))
        self.assertIsNone(transaction.delete("z"))
        transaction.put("x", 8)
        transaction.put("x", 1)
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["x", 1], ["z", None]]},
        ])
        self.assertEqual(engine.begin().commit(), 1)
        self.assertEqual(self.write(engine, "missing", None), 2)

    def test_history_conflicts_even_if_values_return_to_snapshot(self):
        for changes in ([2, 1], [1], [None, 1]):
            with self.subTest(changes=changes):
                engine = Engine({"x": 1})
                reader = engine.begin()
                reader.get("x")
                for value in changes:
                    self.write(engine, "x", value)
                before = engine.checkpoint(), engine.log_since(0)
                with self.assertRaises(Conflict):
                    reader.commit()
                self.assertEqual((engine.checkpoint(), engine.log_since(0)), before)
                with self.assertRaises(RuntimeError):
                    reader.abort()

    def test_absent_reads_and_empty_scans_detect_deleted_phantoms(self):
        for operation in (lambda t: t.get("job/a"),
                          lambda t: t.scan("job/"),
                          lambda t: t.scan("")):
            engine = Engine()
            reader = engine.begin()
            operation(reader)
            self.write(engine, "job/a", 1)
            self.write(engine, "job/a", None)
            with self.assertRaises(Conflict):
                reader.commit()

    def test_blind_writes_conflict_and_unrelated_writes_do_not(self):
        engine = Engine()
        writer = engine.begin()
        writer.delete("x")
        self.write(engine, "x", None)
        with self.assertRaises(Conflict):
            writer.commit()
        reader = engine.begin()
        reader.get("x")
        reader.scan("job/")
        self.write(engine, "other", 5)
        self.assertEqual(reader.commit(), 2)
        self.assertEqual(len(engine.log_since(0)), 2)

    def test_savepoints_restore_copies_and_discard_correct_suffix(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("x", 1)
        self.assertIsNone(transaction.savepoint("a"))
        transaction.put("x", 2)
        transaction.savepoint("b")
        transaction.delete("x")
        transaction.put("y", 3)
        self.assertIsNone(transaction.rollback_to("a"))
        self.assertEqual(transaction.scan(), {"x": 1})
        with self.assertRaises(ValueError):
            transaction.release("b")
        with self.assertRaises(ValueError):
            transaction.savepoint("a")
        transaction.put("x", 4)
        transaction.rollback_to("a")
        self.assertEqual(transaction.get("x"), 1)
        transaction.savepoint("b")
        transaction.put("y", 5)
        self.assertIsNone(transaction.release("a"))
        for name in ("a", "b"):
            with self.assertRaises(ValueError):
                transaction.rollback_to(name)
        transaction.savepoint("a")
        transaction.commit()
        self.assertEqual(engine.checkpoint()["data"], {"x": 1, "y": 5})

    def test_rollback_and_release_preserve_all_read_dependencies(self):
        for finish in ("rollback_to", "release"):
            for read in (lambda t: t.get("x"), lambda t: t.scan("x")):
                with self.subTest(finish=finish, read=read):
                    engine = Engine()
                    transaction = engine.begin()
                    transaction.savepoint("s")
                    transaction.put("x", 7)
                    read(transaction)
                    getattr(transaction, finish)("s")
                    self.write(engine, "x", 7)
                    with self.assertRaises(Conflict):
                        transaction.commit()

    def test_rolled_back_unread_write_is_no_longer_a_dependency(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("s")
        transaction.put("x", 7)
        transaction.rollback_to("s")
        self.write(engine, "x", 8)
        self.assertEqual(transaction.commit(), 1)

    def test_invalid_arguments_have_no_side_effects(self):
        for initial in ([], 1, False, {"": 1}, {1: 2}, {"x": True},
                        {"x": None}, {"x": 1.0}):
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                Engine(initial)
        engine = Engine()
        transaction = engine.begin()
        transaction.put("keep", 1)
        transaction.savepoint("s")
        invalid = [
            ("get", ("",)), ("get", ([],)), ("scan", (None,)),
            ("put", ("bad", True)), ("put", ("bad", None)),
            ("put", ("bad", 1.5)), ("put", ("", 3)),
            ("delete", (False,)), ("savepoint", ("",)),
            ("savepoint", ("s",)), ("rollback_to", ("unknown",)),
            ("release", ([],)),
        ]
        for method, arguments in invalid:
            with self.subTest(method=method, arguments=arguments):
                with self.assertRaises(ValueError):
                    getattr(transaction, method)(*arguments)
        # Invalid put must not stage a write or register a read dependency.
        self.write(engine, "bad", 2)
        self.assertEqual(transaction.commit(), 2)
        self.assertEqual(engine.checkpoint()["data"], {"bad": 2, "keep": 1})
        for version in (-1, 3, True, None, "0", 0.0):
            with self.subTest(version=version), self.assertRaises(ValueError):
                engine.log_since(version)

    def test_closed_methods_check_lifecycle_before_arguments(self):
        for close in ("abort", "commit", "conflict"):
            engine = Engine()
            transaction = engine.begin()
            if close == "conflict":
                transaction.get("x")
                self.write(engine, "x", 1)
                with self.assertRaises(Conflict):
                    transaction.commit()
            else:
                getattr(transaction, close)()
            for method, arguments in [
                ("get", (None,)), ("scan", (None,)),
                ("put", (None, None)), ("delete", (None,)),
                ("savepoint", (None,)), ("rollback_to", (None,)),
                ("release", (None,)), ("commit", ()), ("abort", ()),
            ]:
                with self.subTest(close=close, method=method):
                    with self.assertRaises(RuntimeError):
                        getattr(transaction, method)(*arguments)

    def test_recovery_base_replay_and_defensive_log(self):
        checkpoint = {"version": 10, "data": {"z": 9, "x": 1}}
        records = [
            {"version": 11, "writes": [["absent", None], ["x", None]]},
            {"version": 12, "writes": [["x", -(10 ** 100)], ["z", 9]]},
        ]
        original = copy.deepcopy(records)
        engine = Engine.restore(checkpoint, records)
        self.assertEqual(engine.version, 12)
        self.assertEqual(engine.log_since(10), original)
        self.assertEqual(engine.log_since(11), original[1:])
        self.assertEqual(engine.log_since(12), [])
        with self.assertRaises(ValueError):
            engine.log_since(9)
        checkpoint["data"].clear()
        records[0]["writes"][0][0] = "corrupt"
        exported = engine.log_since(10)
        exported[1]["writes"].clear()
        exported[0]["version"] = -1
        self.assertEqual(engine.log_since(10), original)
        reader = engine.begin()
        self.assertEqual(reader.get("x"), -(10 ** 100))
        self.assertEqual(reader.commit(), 12)
        self.assertEqual(self.write(engine, "new", 3), 13)
        restored = Engine.restore(engine.checkpoint(), [])
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        with self.assertRaises(ValueError):
            restored.log_since(12)
        self.assertEqual(self.write(restored, "after", 4), 14)
        self.assertEqual(restored.log_since(13), [
            {"version": 14, "writes": [["after", 4]]},
        ])

    def test_restore_rejects_malformed_whole_inputs(self):
        checkpoint = {"version": 0, "data": {}}
        for bad in (None, [], {}, {"version": 0, "data": {}, "extra": 1},
                    {"version": -1, "data": {}}, {"version": True, "data": {}},
                    {"version": 0.0, "data": {}}, {"version": 0, "data": []},
                    {"version": 0, "data": {"": 1}},
                    {"version": 0, "data": {"x": None}}):
            with self.subTest(checkpoint=bad), self.assertRaises(ValueError):
                Engine.restore(bad, [])
        bad_logs = [
            None, (), {}, [None], [{}],
            [{"version": 1, "writes": [], "extra": 1}],
            [{"version": True, "writes": [["x", 1]]}],
            [{"version": 1.0, "writes": [["x", 1]]}],
            [{"version": 2, "writes": [["x", 1]]}],
            [{"version": 1, "writes": []}],
            [{"version": 1, "writes": (("x", 1),)}],
            [{"version": 1, "writes": [("x", 1)]}],
            [{"version": 1, "writes": [["x"]]}],
            [{"version": 1, "writes": [["x", 1, 2]]}],
            [{"version": 1, "writes": [["", 1]]}],
            [{"version": 1, "writes": [[1, 1]]}],
            [{"version": 1, "writes": [["x", False]]}],
            [{"version": 1, "writes": [["x", 1.2]]}],
            [{"version": 1, "writes": [["x", 1], ["x", None]]}],
            [{"version": 1, "writes": [["z", 1], ["a", 2]]}],
            [{"version": 1, "writes": [["x", 1]]},
             {"version": 3, "writes": [["y", 2]]}],
        ]
        for records in bad_logs:
            with self.subTest(records=records):
                before = copy.deepcopy((checkpoint, records))
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, records)
                self.assertEqual((checkpoint, records), before)

    def test_deterministic_interleaved_traces_and_recovery(self):
        rng = random.Random(28173)
        engine = Engine({"a": 1})
        committed = {"a": 1}
        history = []  # Independent oracle: sets of touched keys per commit.
        active = []
        keys = ["a", "b", "job/1", "job/2", "other"]
        checkpoint = engine.checkpoint()
        for step in range(800):
            if not active or (len(active) < 8 and rng.randrange(5) == 0):
                active.append({"tx": engine.begin(), "snapshot": committed.copy(),
                               "start": len(history), "writes": {}, "reads": set(),
                               "ranges": set(), "saves": []})
            state = rng.choice(active)
            tx = state["tx"]
            operation = rng.choice(["get", "scan", "put", "delete", "save",
                                    "rollback", "release", "commit", "abort"])
            key = rng.choice(keys)
            if operation == "get":
                expected = state["writes"].get(key, state["snapshot"].get(key))
                self.assertEqual(tx.get(key), expected)
                state["reads"].add(key)
            elif operation == "scan":
                prefix = rng.choice(["", "job/", "a", "missing/"])
                visible = {key: state["writes"].get(key, state["snapshot"].get(key))
                           for key in keys}
                expected = {key: visible[key] for key in sorted(keys)
                            if key.startswith(prefix) and visible[key] is not None}
                self.assertEqual(tx.scan(prefix), expected)
                state["ranges"].add(prefix)
            elif operation in ("put", "delete"):
                value = rng.randrange(-5, 6) if operation == "put" else None
                if operation == "put":
                    tx.put(key, value)
                else:
                    tx.delete(key)
                state["writes"][key] = value
            elif operation == "save":
                name = "s" + str(step)
                tx.savepoint(name)
                state["saves"].append((name, state["writes"].copy()))
            elif operation in ("rollback", "release") and state["saves"]:
                index = rng.randrange(len(state["saves"]))
                name, writes = state["saves"][index]
                if operation == "rollback":
                    tx.rollback_to(name)
                    state["writes"] = writes.copy()
                    state["saves"] = state["saves"][:index + 1]
                else:
                    tx.release(name)
                    state["saves"] = state["saves"][:index]
            elif operation == "commit":
                touched = set().union(*history[state["start"]:])
                conflict = bool(touched & (state["reads"] | set(state["writes"])))
                conflict |= any(key.startswith(prefix) for key in touched
                                for prefix in state["ranges"])
                if conflict:
                    with self.assertRaises(Conflict):
                        tx.commit()
                else:
                    if state["writes"]:
                        history.append(set(state["writes"]))
                        for written_key, value in state["writes"].items():
                            if value is None:
                                committed.pop(written_key, None)
                            else:
                                committed[written_key] = value
                    self.assertEqual(tx.commit(), len(history))
                active.remove(state)
            elif operation == "abort":
                tx.abort()
                active.remove(state)
            self.assertEqual(engine.checkpoint(),
                             {"version": len(history), "data": committed})
            if step % 41 == 0:
                recovered = Engine.restore(checkpoint,
                                           engine.log_since(checkpoint["version"]))
                self.assertEqual(recovered.checkpoint(), engine.checkpoint())
                checkpoint = engine.checkpoint()
        for state in active:
            state["tx"].abort()


if __name__ == "__main__":
    unittest.main()
