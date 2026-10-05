import copy
import random
import unittest

from mvcc import Conflict, Engine


def write(engine, key, value):
    transaction = engine.begin()
    if value is None:
        transaction.delete(key)
    else:
        transaction.put(key, value)
    return transaction.commit()


class Contract(unittest.TestCase):
    def test_initial_data_values_and_defensive_copy(self):
        initial = {"z": -(10 ** 200), "a": 10 ** 200, " ": 0, "é": -1}
        engine = Engine(initial)
        expected = initial.copy()
        initial.clear()
        self.assertEqual(engine.version, 0)
        checkpoint = engine.checkpoint()
        self.assertEqual(checkpoint, {"version": 0, "data": expected})
        self.assertEqual(list(checkpoint["data"]), sorted(expected))
        checkpoint["data"].clear()
        checkpoint["version"] = 100
        self.assertEqual(engine.checkpoint(), {"version": 0, "data": expected})
        self.assertEqual(Engine().checkpoint(), {"version": 0, "data": {}})

    def test_invalid_initial_data(self):
        cases = [
            [], (), "", 0, False, {"": 1}, {1: 2}, {None: 3},
            {"x": None}, {"x": True}, {"x": False}, {"x": 1.0},
            {"x": "1"}, {"x": []},
        ]
        for initial in cases:
            with self.subTest(initial=initial):
                before = copy.deepcopy(initial)
                with self.assertRaises(ValueError):
                    Engine(initial)
                self.assertEqual(initial, before)

    def test_snapshot_visibility_and_own_writes(self):
        engine = Engine({"p/z": 1, "p/a": 2, "outside": 3})
        transaction = engine.begin()
        write(engine, "p/z", 50)
        write(engine, "p/new", 60)
        self.assertEqual(transaction.get("p/z"), 1)
        self.assertIsNone(transaction.get("p/new"))
        self.assertIsNone(transaction.put("p/z", -5))
        self.assertIsNone(transaction.put("p/b", 10 ** 100))
        self.assertIsNone(transaction.delete("p/a"))
        self.assertIsNone(transaction.delete("absent"))
        self.assertEqual(transaction.get("p/z"), -5)
        self.assertIsNone(transaction.get("p/a"))
        result = transaction.scan("p/")
        self.assertEqual(result, {"p/b": 10 ** 100, "p/z": -5})
        self.assertEqual(list(result), ["p/b", "p/z"])
        result.clear()
        self.assertEqual(transaction.scan("p/"), {"p/b": 10 ** 100, "p/z": -5})
        self.assertEqual(transaction.scan(), {"outside": 3, "p/b": 10 ** 100, "p/z": -5})
        self.assertIsNone(transaction.abort())
        self.assertEqual(engine.version, 2)

    def test_repeated_and_noop_writes_create_one_record(self):
        engine = Engine({"same": 1})
        transaction = engine.begin()
        transaction.put("same", 1)
        transaction.put("z", 4)
        transaction.delete("z")
        transaction.delete("missing")
        transaction.put("a", 5)
        transaction.put("a", -6)
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0), [{
            "version": 1,
            "writes": [["a", -6], ["missing", None], ["same", 1], ["z", None]],
        }])
        self.assertEqual(engine.checkpoint()["data"], {"a": -6, "same": 1})
        self.assertEqual(write(engine, "missing", None), 2)
        self.assertEqual(write(engine, "same", 1), 3)

    def test_write_free_commit_returns_current_version(self):
        engine = Engine({"a": 1})
        reader, untouched = engine.begin(), engine.begin()
        reader.get("a")
        write(engine, "b", 2)
        self.assertEqual(reader.commit(), 1)
        self.assertEqual(untouched.commit(), 1)
        self.assertEqual(engine.version, 1)
        self.assertEqual(len(engine.log_since(0)), 1)

    def test_key_reads_conflict_with_full_write_history(self):
        for initial, updates in [(1, [2, 1]), (1, [1]), (None, [1, None]), (None, [None])]:
            with self.subTest(initial=initial, updates=updates):
                engine = Engine({} if initial is None else {"watch": initial})
                reader = engine.begin()
                self.assertEqual(reader.get("watch"), initial)
                for value in updates:
                    write(engine, "watch", value)
                before = engine.checkpoint()
                records = engine.log_since(0)
                with self.assertRaises(Conflict):
                    reader.commit()
                self.assertEqual(engine.checkpoint(), before)
                self.assertEqual(engine.log_since(0), records)
                with self.assertRaises(RuntimeError):
                    reader.abort()

    def test_empty_prefix_scans_detect_deleted_phantoms(self):
        for prefix in ["job/", ""]:
            with self.subTest(prefix=prefix):
                engine = Engine()
                reader = engine.begin()
                self.assertEqual(reader.scan(prefix), {})
                write(engine, "job/new", 1)
                write(engine, "job/new", None)
                with self.assertRaises(Conflict):
                    reader.commit()
                self.assertEqual(engine.checkpoint(), {"version": 2, "data": {}})

    def test_scans_conflict_on_same_value_and_absent_deletion(self):
        for initial, value in [({"job/a": 7}, 7), ({}, None)]:
            with self.subTest(initial=initial, value=value):
                engine = Engine(initial)
                reader = engine.begin()
                reader.scan("job/")
                write(engine, "job/a", value)
                with self.assertRaises(Conflict):
                    reader.commit()

    def test_unrelated_writes_do_not_conflict(self):
        engine = Engine({"a": 1, "job/existing": 2})
        transaction = engine.begin()
        transaction.get("a")
        transaction.get("missing")
        transaction.scan("job/")
        transaction.put("own", 3)
        write(engine, "jobs/other", 4)
        write(engine, "b", 5)
        self.assertEqual(transaction.commit(), 3)
        self.assertEqual(engine.checkpoint()["data"], {
            "a": 1, "b": 5, "job/existing": 2, "jobs/other": 4, "own": 3,
        })

    def test_conflicting_writer_is_atomic_and_closed(self):
        engine = Engine()
        first, second = engine.begin(), engine.begin()
        first.put("x", 1)
        second.put("x", 2)
        second.put("unrelated", 3)
        first.commit()
        before = engine.checkpoint()
        records = engine.log_since(0)
        with self.assertRaises(Conflict):
            second.commit()
        self.assertEqual(engine.checkpoint(), before)
        self.assertEqual(engine.log_since(0), records)
        with self.assertRaises(RuntimeError):
            second.put("x", 4)

    def test_new_transactions_ignore_earlier_history(self):
        engine = Engine({"x": 1})
        write(engine, "x", 2)
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 2)
        self.assertEqual(reader.commit(), 1)
        writer = engine.begin()
        writer.put("x", 3)
        self.assertEqual(writer.commit(), 2)

    def test_savepoint_rollback_keeps_target_and_copies_writes(self):
        engine = Engine({"a": 1, "b": 2})
        transaction = engine.begin()
        transaction.put("a", 10)
        self.assertIsNone(transaction.savepoint("first"))
        transaction.delete("b")
        transaction.savepoint("second")
        transaction.put("a", 20)
        transaction.put("c", 30)
        self.assertIsNone(transaction.rollback_to("first"))
        self.assertEqual(transaction.scan(), {"a": 10, "b": 2})
        with self.assertRaises(ValueError):
            transaction.rollback_to("second")
        with self.assertRaises(ValueError):
            transaction.savepoint("first")
        transaction.put("a", 40)
        transaction.rollback_to("first")
        self.assertEqual(transaction.get("a"), 10)
        transaction.rollback_to("first")
        transaction.savepoint("second")
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0)[0]["writes"], [["a", 10]])

    def test_release_discards_target_and_newer_but_retains_writes(self):
        transaction = Engine().begin()
        transaction.savepoint("first")
        transaction.put("a", 1)
        transaction.savepoint("second")
        transaction.put("b", 2)
        transaction.savepoint("third")
        transaction.put("c", 3)
        self.assertIsNone(transaction.release("second"))
        self.assertEqual(transaction.scan(), {"a": 1, "b": 2, "c": 3})
        for name in ["second", "third"]:
            with self.assertRaises(ValueError):
                transaction.release(name)
            transaction.savepoint(name)
        transaction.rollback_to("first")
        self.assertEqual(transaction.scan(), {})
        transaction.release("first")
        transaction.savepoint("first")
        self.assertEqual(transaction.commit(), 0)

    def test_reads_after_savepoint_survive_rollback(self):
        for kind in ["existing", "absent", "own_put", "own_delete", "range"]:
            with self.subTest(kind=kind):
                engine = Engine({"watch": 1})
                transaction = engine.begin()
                transaction.savepoint("before")
                target = "watch"
                if kind == "absent":
                    target = "missing"
                    transaction.get(target)
                elif kind == "range":
                    target = "job/new"
                    transaction.scan("job/")
                else:
                    if kind == "own_put":
                        transaction.put(target, 10)
                    elif kind == "own_delete":
                        transaction.delete(target)
                    transaction.get(target)
                transaction.rollback_to("before")
                write(engine, target, 20)
                with self.assertRaises(Conflict):
                    transaction.commit()

    def test_release_preserves_key_and_range_reads(self):
        for ranged in [False, True]:
            with self.subTest(ranged=ranged):
                engine = Engine()
                transaction = engine.begin()
                transaction.savepoint("before")
                if ranged:
                    transaction.scan("watch/")
                else:
                    transaction.get("watch/new")
                transaction.release("before")
                write(engine, "watch/new", 5)
                with self.assertRaises(Conflict):
                    transaction.commit()

    def test_rolled_back_unread_writes_are_discarded(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("empty")
        transaction.put("x", 1)
        transaction.delete("y")
        transaction.rollback_to("empty")
        write(engine, "x", 2)
        write(engine, "y", 3)
        self.assertEqual(transaction.commit(), 2)
        self.assertEqual(len(engine.log_since(0)), 2)

    def test_invalid_transaction_operations_leave_writes_and_savepoints(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("first")
        transaction.put("b", 2)
        transaction.savepoint("second")
        operations = [
            ("get", (None,)), ("get", ("",)), ("get", ([],)),
            ("scan", (None,)), ("scan", (1,)), ("scan", ([],)),
            ("put", ("", 3)), ("put", (1, 3)), ("put", ("b", None)),
            ("put", ("b", True)), ("put", ("b", 3.0)),
            ("delete", (None,)), ("delete", ("",)),
            ("savepoint", ("first",)), ("savepoint", ("",)),
            ("savepoint", (False,)), ("rollback_to", ("unknown",)),
            ("rollback_to", ([],)), ("release", ("unknown",)), ("release", (None,)),
        ]
        for method, args in operations:
            with self.subTest(method=method, args=args):
                with self.assertRaises(ValueError):
                    getattr(transaction, method)(*args)
                self.assertEqual(transaction.scan(), {"a": 1, "b": 2})
                self.assertEqual(engine.checkpoint(), {"version": 0, "data": {}})
        transaction.rollback_to("second")
        self.assertEqual(transaction.scan(), {"a": 1, "b": 2})
        transaction.rollback_to("first")
        self.assertEqual(transaction.scan(), {"a": 1})
        self.assertEqual(transaction.commit(), 1)

    def test_invalid_operations_do_not_create_dependencies(self):
        engine = Engine()
        transaction = engine.begin()
        for value in [None, True, False, 1.0, "1", [], {}]:
            with self.assertRaises(ValueError):
                transaction.put("watch", value)
        for prefix in [None, False, 1, [], {}]:
            with self.assertRaises(ValueError):
                transaction.scan(prefix)
        for key in [None, False, 1, [], {}, ""]:
            with self.assertRaises(ValueError):
                transaction.get(key)
        write(engine, "watch", 2)
        self.assertEqual(transaction.commit(), 1)

    def test_closed_methods_check_lifecycle_before_arguments(self):
        self.assertTrue(issubclass(Conflict, RuntimeError))
        operations = [
            ("get", (None,)), ("get", ("a",)), ("scan", ([],)),
            ("put", (None, True)), ("delete", ("",)),
            ("savepoint", (None,)), ("rollback_to", ([],)),
            ("release", ("",)), ("commit", ()), ("abort", ()),
        ]
        for ending in ["abort", "empty_commit", "write_commit", "conflict"]:
            with self.subTest(ending=ending):
                engine = Engine()
                transaction = engine.begin()
                if ending == "abort":
                    self.assertIsNone(transaction.abort())
                elif ending == "empty_commit":
                    transaction.commit()
                else:
                    transaction.put("a", 1)
                    if ending == "conflict":
                        write(engine, "a", 2)
                        with self.assertRaises(Conflict):
                            transaction.commit()
                    else:
                        transaction.commit()
                before = engine.checkpoint()
                for method, args in operations:
                    with self.assertRaises(RuntimeError):
                        getattr(transaction, method)(*args)
                    self.assertEqual(engine.checkpoint(), before)

    def test_checkpoint_excludes_active_and_aborted_writes(self):
        engine = Engine({"z": 1, "a": 2})
        transaction = engine.begin()
        transaction.delete("a")
        transaction.put("b", 3)
        self.assertEqual(engine.checkpoint(), {"version": 0, "data": {"a": 2, "z": 1}})
        self.assertEqual(list(engine.checkpoint()["data"]), ["a", "z"])
        self.assertEqual(engine.log_since(0), [])
        transaction.abort()
        self.assertEqual(engine.version, 0)
        self.assertEqual(engine.begin().scan(), {"a": 2, "z": 1})

    def test_log_bounds_order_and_defensive_nested_copies(self):
        engine = Engine()
        write(engine, "a", 1)
        write(engine, "b", 2)
        records = engine.log_since(0)
        self.assertEqual([record["version"] for record in records], [1, 2])
        self.assertEqual(engine.log_since(1), [records[1]])
        self.assertEqual(engine.log_since(2), [])
        records[0]["writes"][0][:] = ["tampered", 100]
        records[0]["version"] = 999
        records[1]["writes"].clear()
        records.clear()
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["a", 1]]},
            {"version": 2, "writes": [["b", 2]]},
        ])
        for version in [-1, 3, False, True, None, 1.0, "1", [], {}]:
            with self.subTest(version=version):
                before = engine.checkpoint()
                with self.assertRaises(ValueError):
                    engine.log_since(version)
                self.assertEqual(engine.checkpoint(), before)

    def test_restore_retains_base_history_and_continues_versions(self):
        checkpoint = {"version": 8, "data": {"z": 1, "a": 2}}
        records = [
            {"version": 9, "writes": [["a", None], ["missing", None], ["z", 3]]},
            {"version": 10, "writes": [["a", 4]]},
        ]
        engine = Engine.restore(checkpoint, records)
        self.assertEqual(engine.checkpoint(), {"version": 10, "data": {"a": 4, "z": 3}})
        self.assertEqual(engine.log_since(8), records)
        self.assertEqual(engine.log_since(9), records[1:])
        self.assertEqual(engine.log_since(10), [])
        with self.assertRaises(ValueError):
            engine.log_since(7)
        reader = engine.begin()
        reader.get("z")
        self.assertEqual(reader.commit(), 10)
        reader = engine.begin()
        reader.scan("z")
        self.assertEqual(write(engine, "z", 3), 11)
        with self.assertRaises(Conflict):
            reader.commit()
        self.assertEqual(engine.log_since(10), [{"version": 11, "writes": [["z", 3]]}])
        replayed = Engine.restore(checkpoint, engine.log_since(8))
        self.assertEqual(replayed.checkpoint(), engine.checkpoint())
        checkpoint_only = Engine.restore(engine.checkpoint(), [])
        self.assertEqual(checkpoint_only.version, 11)
        self.assertEqual(checkpoint_only.log_since(11), [])
        with self.assertRaises(ValueError):
            checkpoint_only.log_since(10)

    def test_restore_detaches_all_input_and_export_containers(self):
        checkpoint = {"version": 3, "data": {"x": 1}}
        records = [{"version": 4, "writes": [["x", 2], ["y", 3]]}]
        engine = Engine.restore(checkpoint, records)
        checkpoint["data"].clear()
        checkpoint["version"] = 100
        records[0]["writes"][0][1] = 200
        records[0]["writes"].append(["z", 300])
        records[0]["version"] = 101
        records.clear()
        self.assertEqual(engine.checkpoint(), {"version": 4, "data": {"x": 2, "y": 3}})
        export = engine.log_since(3)
        export[0]["writes"][0][1] = 400
        self.assertEqual(engine.log_since(3), [{"version": 4, "writes": [["x", 2], ["y", 3]]}])

    def test_restore_rejects_malformed_checkpoints_without_mutating_inputs(self):
        cases = [
            None, [], {}, {"version": 0}, {"data": {}},
            {"version": 0, "data": {}, "extra": 1},
            {"version": -1, "data": {}}, {"version": True, "data": {}},
            {"version": 0.0, "data": {}}, {"version": "0", "data": {}},
            {"version": 0, "data": []}, {"version": 0, "data": None},
            {"version": 0, "data": {"": 1}}, {"version": 0, "data": {1: 2}},
            {"version": 0, "data": {"x": None}}, {"version": 0, "data": {"x": False}},
            {"version": 0, "data": {"x": 1.5}},
        ]
        for checkpoint in cases:
            with self.subTest(checkpoint=checkpoint):
                before = copy.deepcopy(checkpoint)
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, [])
                self.assertEqual(checkpoint, before)

    def test_restore_rejects_malformed_logs_without_mutating_inputs(self):
        checkpoint = {"version": 0, "data": {"original": 1}}
        invalid_records = [None, [], {}, {"version": 1}, {"writes": [["a", 1]]}]
        invalid_records.append({"version": 1, "writes": [["a", 1]], "extra": 0})
        for version in [0, 2, -1, True, False, None, 1.0, "1"]:
            invalid_records.append({"version": version, "writes": [["a", 1]]})
        for writes in [
            [], None, {}, (), (("a", 1),), [("a", 1)], [[]], [["a"]],
            [["a", 1, 2]], [["", 1]], [[1, 2]], [[None, 1]],
            [["a", True]], [["a", False]], [["a", 1.0]], [["a", "1"]],
            [["a", []]], [["b", 1], ["a", 2]], [["a", 1], ["a", None]],
        ]:
            invalid_records.append({"version": 1, "writes": writes})
        cases = [None, {}, (), "", ( {"version": 1, "writes": [["a", 1]]}, )]
        cases.extend([[record] for record in invalid_records])
        valid = {"version": 1, "writes": [["a", 1]]}
        cases.extend([
            [valid, {"version": 1, "writes": [["b", 2]]}],
            [valid, {"version": 3, "writes": [["b", 2]]}],
            [valid, {"version": 2, "writes": [["b", False]]}],
        ])
        for records in cases:
            with self.subTest(records=records):
                before = copy.deepcopy(records)
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, records)
                self.assertEqual(records, before)
                self.assertEqual(checkpoint, {"version": 0, "data": {"original": 1}})

    def test_deterministic_interleaving_and_recovery_round_trips(self):
        rng = random.Random(739)
        expected = {"data/0": 1}
        checkpoint = {"version": 12, "data": expected.copy()}
        engine = Engine.restore(checkpoint, [])
        for step in range(60):
            transaction = engine.begin()
            transaction.scan("data/")
            transaction.savepoint("start")
            transaction.put("discarded", step)
            transaction.rollback_to("start")
            pending = {}
            for _ in range(4):
                key = "data/" + str(rng.randrange(6))
                value = rng.choice([None, -10 ** 80, 0, 10 ** 80, step])
                pending[key] = value
                if value is None:
                    transaction.delete(key)
                else:
                    transaction.put(key, value)
            transaction.savepoint("keep")
            transaction.delete("data/temporary")
            transaction.rollback_to("keep")
            transaction.release("start")
            write(engine, "external", step)
            expected["external"] = step
            version = transaction.commit()
            for key, value in pending.items():
                if value is None:
                    expected.pop(key, None)
                else:
                    expected[key] = value
            self.assertEqual(version, 12 + 2 * (step + 1))
            self.assertEqual(engine.checkpoint(), {"version": version, "data": expected})
            self.assertEqual(engine.log_since(version - 1), [{
                "version": version,
                "writes": [[key, pending[key]] for key in sorted(pending)],
            }])
            records = engine.log_since(checkpoint["version"])
            restored = Engine.restore(checkpoint, records)
            self.assertEqual(restored.checkpoint(), engine.checkpoint())
            self.assertEqual(restored.log_since(checkpoint["version"]), records)
            if step % 7 == 0:
                checkpoint = restored.checkpoint()
                engine = Engine.restore(checkpoint, [])
            else:
                engine = restored


if __name__ == "__main__":
    unittest.main()
