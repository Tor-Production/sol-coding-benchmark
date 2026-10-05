import copy
import random
import unittest

from mvcc import Conflict, Engine


class VisibilityAndLifecycle(unittest.TestCase):
    def test_snapshot_own_writes_sorting_and_large_integers(self):
        huge = 10 ** 200
        initial = {"z": huge, "a": -huge, "job/2": 2}
        engine = Engine(initial)
        initial["a"] = 0
        old = engine.begin()
        writer = engine.begin()
        writer.put("a", 99)
        writer.put("job/1", 1)
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(old.get("a"), -huge)
        self.assertIsNone(old.get("job/1"))
        self.assertIsNone(old.put("job/3", -huge))
        self.assertIsNone(old.delete("job/2"))
        self.assertEqual(old.scan("job/"), {"job/3": -huge})
        self.assertEqual(list(old.scan()), ["a", "job/3", "z"])
        with self.assertRaises(Conflict):
            old.commit()
        self.assertEqual(engine.checkpoint(), {
            "version": 1,
            "data": {"a": 99, "job/1": 1, "job/2": 2, "z": huge},
        })
        self.assertEqual(list(engine.checkpoint()["data"]),
                         ["a", "job/1", "job/2", "z"])

    def test_last_write_wins_and_noop_writes_still_make_versions(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        transaction.put("z", 7)
        transaction.delete("z")
        transaction.put("a", 1)
        transaction.put("a", 2)
        transaction.delete("a")
        transaction.put("a", -3)
        transaction.put("x", 1)
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0), [{
            "version": 1, "writes": [["a", -3], ["x", 1], ["z", None]],
        }])
        transaction = engine.begin()
        transaction.delete("missing")
        self.assertEqual(transaction.commit(), 2)
        self.assertEqual(engine.log_since(1), [{
            "version": 2, "writes": [["missing", None]],
        }])
        empty = engine.begin()
        self.assertEqual(empty.commit(), 2)
        self.assertEqual(engine.version, 2)

    def test_closed_methods_check_lifecycle_before_arguments(self):
        for ending in ("commit", "abort", "conflict"):
            with self.subTest(ending=ending):
                engine = Engine()
                transaction = engine.begin()
                if ending == "conflict":
                    transaction.get("x")
                    writer = engine.begin()
                    writer.delete("x")
                    writer.commit()
                    before = engine.checkpoint(), engine.log_since(0)
                    with self.assertRaises(Conflict):
                        transaction.commit()
                    self.assertEqual((engine.checkpoint(), engine.log_since(0)), before)
                elif ending == "abort":
                    self.assertIsNone(transaction.abort())
                else:
                    self.assertEqual(transaction.commit(), 0)
                methods = [
                    ("get", (None,)), ("scan", (None,)),
                    ("put", (None, None)), ("delete", (None,)),
                    ("savepoint", (None,)), ("rollback_to", (None,)),
                    ("release", (None,)), ("commit", ()), ("abort", ()),
                ]
                for method, arguments in methods:
                    with self.subTest(method=method):
                        with self.assertRaises(RuntimeError):
                            getattr(transaction, method)(*arguments)

    def test_exports_are_independent_of_engine_and_each_other(self):
        engine = Engine({"b": 2, "a": 1})
        reader = engine.begin()
        view = reader.scan()
        view["a"] = 999
        view.clear()
        checkpoint = engine.checkpoint()
        checkpoint["version"] = 999
        checkpoint["data"].clear()
        writer = engine.begin()
        writer.put("c", 3)
        writer.commit()
        records = engine.log_since(0)
        other_export = engine.log_since(0)
        records[0]["version"] = 999
        records[0]["writes"][0][0] = "bad"
        records[0]["writes"][0][1] = 999
        records[0]["writes"].append(["extra", 4])
        records.clear()
        self.assertEqual(other_export, [{"version": 1, "writes": [["c", 3]]}])
        self.assertEqual(engine.log_since(0), other_export)
        self.assertEqual(reader.get("a"), 1)
        self.assertEqual(reader.scan(), {"a": 1, "b": 2})
        self.assertEqual(engine.checkpoint(), {
            "version": 1, "data": {"a": 1, "b": 2, "c": 3},
        })
        reader.abort()


class SerializableValidation(unittest.TestCase):
    def test_key_dependencies_include_absent_reads_and_blind_writes(self):
        for operation in ("get", "put", "delete"):
            with self.subTest(operation=operation):
                engine = Engine()
                transaction = engine.begin()
                if operation == "put":
                    transaction.put("x", 1)
                else:
                    getattr(transaction, operation)("x")
                writer = engine.begin()
                writer.delete("x")
                writer.commit()
                before = engine.checkpoint(), engine.log_since(0)
                with self.assertRaises(Conflict):
                    transaction.commit()
                self.assertEqual((engine.checkpoint(), engine.log_since(0)), before)

    def test_history_detects_aba_same_value_and_phantoms(self):
        scenarios = [
            ({"x": 1}, "get", "x", [("put", "x", 2), ("put", "x", 1)]),
            ({"x": 1}, "get", "x", [("put", "x", 1)]),
            ({}, "get", "x", [("put", "x", 1), ("delete", "x", None)]),
            ({}, "scan", "job/", [("put", "job/1", 1), ("delete", "job/1", None)]),
            ({}, "scan", "job/", [("delete", "job/missing", None)]),
            ({}, "scan", "", [("delete", "anything", None)]),
        ]
        for initial, read, target, writes in scenarios:
            with self.subTest(read=read, target=target, writes=writes):
                engine = Engine(initial)
                transaction = engine.begin()
                getattr(transaction, read)(target)
                for operation, key, value in writes:
                    writer = engine.begin()
                    if operation == "put":
                        writer.put(key, value)
                    else:
                        writer.delete(key)
                    writer.commit()
                with self.assertRaises(Conflict):
                    transaction.commit()
                self.assertEqual(engine.version, len(writes))

    def test_unrelated_writes_and_read_free_commits(self):
        engine = Engine({"a": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("a"), 1)
        self.assertEqual(reader.scan("job/"), {})
        empty = engine.begin()
        left, right = engine.begin(), engine.begin()
        left.put("left", 1)
        right.put("right", 2)
        self.assertEqual(left.commit(), 1)
        self.assertEqual(right.commit(), 2)
        self.assertEqual(reader.commit(), 2)
        self.assertEqual(empty.commit(), 2)
        self.assertEqual(len(engine.log_since(0)), 2)

    def test_write_skew_rejection_is_atomic(self):
        engine = Engine({"x": 1, "y": 1})
        a, b = engine.begin(), engine.begin()
        a.get("y")
        a.put("x", 0)
        b.get("x")
        b.put("y", 0)
        b.put("unrelated", 7)
        a.commit()
        with self.assertRaises(Conflict):
            b.commit()
        self.assertEqual(engine.checkpoint(), {
            "version": 1, "data": {"x": 0, "y": 1},
        })
        self.assertEqual(engine.log_since(0), [{
            "version": 1, "writes": [["x", 0]],
        }])


class Savepoints(unittest.TestCase):
    def test_nested_rollback_release_and_name_reuse(self):
        engine = Engine({"original": 7})
        transaction = engine.begin()
        transaction.put("a", 1)
        self.assertIsNone(transaction.savepoint("outer"))
        transaction.delete("original")
        transaction.put("a", 2)
        transaction.savepoint("middle")
        transaction.put("b", 3)
        transaction.savepoint("inner")
        transaction.put("a", 4)
        with self.assertRaises(ValueError):
            transaction.savepoint("outer")
        with self.assertRaises(ValueError):
            transaction.rollback_to("unknown")
        with self.assertRaises(ValueError):
            transaction.release("unknown")
        self.assertEqual(transaction.get("a"), 4)
        self.assertIsNone(transaction.rollback_to("middle"))
        self.assertEqual(transaction.scan(), {"a": 2})
        with self.assertRaises(ValueError):
            transaction.release("inner")
        transaction.savepoint("inner")
        transaction.put("a", 9)
        transaction.rollback_to("middle")
        self.assertEqual(transaction.get("a"), 2)
        self.assertIsNone(transaction.release("middle"))
        transaction.savepoint("middle")
        transaction.put("b", 8)
        transaction.release("outer")
        for name in ("outer", "middle"):
            with self.assertRaises(ValueError):
                transaction.rollback_to(name)
        self.assertEqual(transaction.scan(), {"a": 2, "b": 8})
        transaction.savepoint("outer")
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0), [{
            "version": 1,
            "writes": [["a", 2], ["b", 8], ["original", None]],
        }])

    def test_rollback_keeps_named_savepoint_and_its_original_writes(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("empty")
        transaction.put("x", 1)
        transaction.rollback_to("empty")
        self.assertEqual(transaction.scan(), {})
        transaction.put("x", 2)
        transaction.rollback_to("empty")
        transaction.delete("x")
        transaction.rollback_to("empty")
        self.assertEqual(transaction.commit(), 0)
        self.assertEqual(engine.log_since(0), [])

    def test_reads_survive_rollback_and_release_including_own_writes(self):
        for cleanup in ("rollback_to", "release"):
            for read in ("absent", "range", "own_write"):
                with self.subTest(cleanup=cleanup, read=read):
                    engine = Engine()
                    transaction = engine.begin()
                    transaction.savepoint("s")
                    if read == "range":
                        self.assertEqual(transaction.scan("job/"), {})
                        key = "job/new"
                    else:
                        key = "x"
                        if read == "own_write":
                            transaction.put(key, 1)
                        transaction.get(key)
                    getattr(transaction, cleanup)("s")
                    writer = engine.begin()
                    writer.put(key, 2)
                    writer.commit()
                    with self.assertRaises(Conflict):
                        transaction.commit()


class ArgumentValidation(unittest.TestCase):
    def test_invalid_initial_data(self):
        invalid = [[], (), 0, False, "", {"": 1}, {1: 1},
                   {"x": True}, {"x": False}, {"x": None},
                   {"x": 1.0}, {"x": "1"}, {"x": []}]
        for initial in invalid:
            with self.subTest(initial=initial):
                before = copy.deepcopy(initial)
                with self.assertRaises(ValueError):
                    Engine(initial)
                self.assertEqual(initial, before)

    def test_invalid_transaction_operations_do_not_change_state(self):
        invalid_names = [None, "", 0, True, [], {}, b"x"]
        operations = []
        for key in invalid_names:
            for method in ("get", "delete", "savepoint", "rollback_to", "release"):
                operations.append((method, (key,)))
            operations.append(("put", (key, 1)))
        for prefix in [None, 0, True, [], {}, b"x"]:
            operations.append(("scan", (prefix,)))
        for value in [None, True, False, 1.0, "1", [], {}]:
            operations.append(("put", ("x", value)))
        operations.extend([("savepoint", ("s",)),
                           ("rollback_to", ("missing",)),
                           ("release", ("missing",))])
        for method, arguments in operations:
            with self.subTest(method=method, arguments=arguments):
                engine = Engine({"committed": 5})
                transaction = engine.begin()
                transaction.put("x", 1)
                transaction.savepoint("s")
                transaction.put("x", 2)
                with self.assertRaises(ValueError):
                    getattr(transaction, method)(*arguments)
                self.assertEqual(engine.checkpoint(), {
                    "version": 0, "data": {"committed": 5},
                })
                self.assertEqual(transaction.get("x"), 2)
                transaction.rollback_to("s")
                writer = engine.begin()
                writer.put("missing", 9)
                writer.commit()
                # Invalid reads/ranges must not create dependencies.
                self.assertEqual(transaction.commit(), 2)
                self.assertEqual(engine.checkpoint()["data"],
                                 {"committed": 5, "missing": 9, "x": 1})

    def test_invalid_log_versions_preserve_state(self):
        engine = Engine.restore({"version": 4, "data": {}}, [
            {"version": 5, "writes": [["x", 1]]},
        ])
        before = engine.checkpoint(), engine.log_since(4)
        for version in [None, True, False, 4.0, "4", [], {}, -1, 0, 3, 6]:
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    engine.log_since(version)
                self.assertEqual((engine.checkpoint(), engine.log_since(4)), before)
        self.assertEqual(engine.log_since(5), [])


class Recovery(unittest.TestCase):
    def test_replay_retained_base_copying_and_future_commits(self):
        checkpoint = {"version": 40, "data": {"z": 9, "a": 1}}
        records = [
            {"version": 41, "writes": [["a", None], ["missing", None], ["x", -10 ** 100]]},
            {"version": 42, "writes": [["x", 7], ["z", 9]]},
            {"version": 43, "writes": [["x", None]]},
        ]
        expected_records = copy.deepcopy(records)
        engine = Engine.restore(checkpoint, records)
        self.assertEqual(engine.checkpoint(), {"version": 43, "data": {"z": 9}})
        self.assertEqual(engine.log_since(40), expected_records)
        self.assertEqual(engine.log_since(41), expected_records[1:])
        with self.assertRaises(ValueError):
            engine.log_since(39)
        checkpoint["data"]["z"] = -1
        checkpoint["version"] = 0
        records[0]["writes"][0][0] = "corrupted"
        records[1]["writes"].clear()
        records.clear()
        self.assertEqual(engine.log_since(40), expected_records)
        # Earlier replayed writes must not conflict with a newly begun reader.
        reader = engine.begin()
        self.assertEqual(reader.get("z"), 9)
        self.assertIsNone(reader.get("x"))
        self.assertEqual(reader.commit(), 43)
        writer = engine.begin()
        writer.put("b", 2)
        self.assertEqual(writer.commit(), 44)
        self.assertEqual(engine.log_since(43), [{
            "version": 44, "writes": [["b", 2]],
        }])
        self.assertEqual(list(engine.checkpoint()["data"]), ["b", "z"])

    def test_invalid_checkpoints(self):
        invalid = [None, [], (), {}, {"version": 0}, {"data": {}},
                   {"version": 0, "data": {}, "extra": 1}]
        for version in [None, True, False, -1, 0.0, "0", [], {}]:
            invalid.append({"version": version, "data": {}})
        for data in [None, [], (), {"": 1}, {0: 1}, {"a": None},
                     {"a": True}, {"a": False}, {"a": 1.0}, {"a": []}]:
            invalid.append({"version": 0, "data": data})
        for checkpoint in invalid:
            with self.subTest(checkpoint=checkpoint):
                before = copy.deepcopy(checkpoint)
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, [])
                self.assertEqual(checkpoint, before)

    def test_invalid_records_are_rejected_without_mutating_input(self):
        valid = {"version": 8, "writes": [["a", 1], ["b", None]]}
        invalid = [None, (), {}, "records", [None], [[]], [{}],
                   [{"version": 8}], [{"writes": [["a", 1]]}],
                   [dict(valid, extra=0)]]
        for version in [None, True, False, 8.0, "8", 7, 9, -1, [], {}]:
            invalid.append([{"version": version, "writes": [["a", 1]]}])
        for writes in [None, [], {}, (), [("a", 1)], [None], [[]],
                       [["a"]], [["a", 1, 2]], [["", 1]], [[0, 1]],
                       [["a", True]], [["a", False]], [["a", 1.0]],
                       [["a", "1"]], [["a", []]], [["a", 1], ["a", 2]],
                       [["b", 1], ["a", 2]]]:
            invalid.append([{"version": 8, "writes": writes}])
        invalid.extend([
            [valid, {"version": 8, "writes": [["c", 3]]}],
            [valid, {"version": 10, "writes": [["c", 3]]}],
            [valid, {"version": 9, "writes": [["c", True]]}],
        ])
        for records in invalid:
            with self.subTest(records=records):
                checkpoint = {"version": 7, "data": {"original": 1}}
                before = copy.deepcopy((checkpoint, records))
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, records)
                self.assertEqual((checkpoint, records), before)

    def test_empty_recovery_with_large_base_version(self):
        version = 10 ** 100
        engine = Engine.restore({"version": version, "data": {"a": -version}}, [])
        self.assertEqual(engine.version, version)
        self.assertEqual(engine.log_since(version), [])
        transaction = engine.begin()
        transaction.delete("absent")
        self.assertEqual(transaction.commit(), version + 1)
        self.assertEqual(engine.log_since(version), [{
            "version": version + 1, "writes": [["absent", None]],
        }])

    def test_deterministic_mixed_trace_and_checkpoint_log_roundtrips(self):
        rng = random.Random(20261003)
        initial = {"root": 1}
        engine = Engine(initial)
        base = engine.checkpoint()
        expected = initial.copy()
        expected_version = 0
        anchor = base
        successes = conflicts = 0
        for cycle in range(80):
            prefix = "job/" if cycle % 2 else "other/"
            watched = prefix + "watched"
            observer = engine.begin()
            observer.scan(prefix)
            observer.get("root")
            observer.put("result/" + str(cycle), cycle)
            observer.savepoint("keep")
            observer.put("discarded", rng.randrange(-100, 100))
            observer.savepoint("newer")
            observer.delete("root")
            observer.rollback_to("keep")
            observer.release("keep")

            conflict = cycle % 3 == 0
            key = watched if conflict else "unrelated/" + str(cycle)
            value = rng.randrange(-10 ** 30, 10 ** 30)
            writer = engine.begin()
            writer.put(key, value)
            writer.delete("missing/" + str(cycle))
            self.assertEqual(writer.commit(), expected_version + 1)
            expected_version += 1
            expected[key] = value
            if conflict:
                deleter = engine.begin()
                deleter.delete(key)
                deleter.commit()
                expected_version += 1
                expected.pop(key)
                with self.assertRaises(Conflict):
                    observer.commit()
                conflicts += 1
            else:
                self.assertEqual(observer.commit(), expected_version + 1)
                expected_version += 1
                expected["result/" + str(cycle)] = cycle
                successes += 1

            discarded = engine.begin()
            discarded.put("root", rng.randrange(1000))
            discarded.abort()
            self.assertEqual(engine.checkpoint(), {
                "version": expected_version,
                "data": dict(sorted(expected.items())),
            })
            replay = Engine.restore(base, engine.log_since(0))
            self.assertEqual(replay.checkpoint(), engine.checkpoint())
            self.assertEqual(replay.log_since(0), engine.log_since(0))
            incremental = Engine.restore(anchor, engine.log_since(anchor["version"]))
            self.assertEqual(incremental.checkpoint(), engine.checkpoint())
            self.assertEqual(incremental.log_since(anchor["version"]),
                             engine.log_since(anchor["version"]))
            if cycle % 7 == 0:
                anchor = engine.checkpoint()
        self.assertGreater(successes, 0)
        self.assertGreater(conflicts, 0)


if __name__ == "__main__":
    unittest.main()
