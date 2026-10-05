import copy
import random
import unittest

from mvcc import Conflict, Engine


class VisibilityAndHistory(unittest.TestCase):
    def test_initial_copy_sorted_exports_and_large_integers(self):
        large = 10 ** 1000
        initial = {"z": -large, "a": large, "middle": 0}
        engine = Engine(initial)
        initial.clear()
        self.assertEqual(engine.version, 0)
        checkpoint = engine.checkpoint()
        self.assertEqual(list(checkpoint), ["version", "data"])
        self.assertEqual(list(checkpoint["data"]), ["a", "middle", "z"])
        self.assertEqual(checkpoint["data"], {"z": -large, "a": large, "middle": 0})
        self.assertEqual(engine.log_since(0), [])

    def test_snapshot_and_own_writes(self):
        engine = Engine({"pre/z": 3, "pre/a": 1, "other": 5})
        old, writer = engine.begin(), engine.begin()
        writer.put("pre/a", 10)
        writer.delete("pre/z")
        writer.put("pre/new", 11)
        writer.commit()
        self.assertEqual(old.get("pre/a"), 1)
        self.assertEqual(old.get("pre/z"), 3)
        self.assertIsNone(old.get("pre/new"))
        self.assertIsNone(old.put("pre/b", 2))
        old.put("pre/b", 20)
        self.assertIsNone(old.delete("pre/a"))
        self.assertIsNone(old.get("pre/a"))
        self.assertEqual(old.scan("pre/"), {"pre/b": 20, "pre/z": 3})
        self.assertEqual(list(old.scan("pre/")), ["pre/b", "pre/z"])
        self.assertEqual(engine.checkpoint()["data"], {"other": 5, "pre/a": 10, "pre/new": 11})
        self.assertIsNone(old.abort())

    def test_nonempty_writes_always_create_versions(self):
        engine = Engine({"same": 7})
        transaction = engine.begin()
        transaction.put("z", 1)
        transaction.put("z", 2)
        transaction.delete("absent")
        transaction.put("same", 7)
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0), [{
            "version": 1,
            "writes": [["absent", None], ["same", 7], ["z", 2]],
        }])
        self.assertEqual(engine.begin().commit(), 1)
        deletion = engine.begin()
        deletion.delete("absent")
        self.assertEqual(deletion.commit(), 2)
        self.assertEqual(engine.log_since(1), [{"version": 2, "writes": [["absent", None]]}])
        self.assertEqual(engine.log_since(2), [])

    def test_unrelated_commits_merge_without_losing_data(self):
        engine = Engine({"old": 1})
        left, right, observer = engine.begin(), engine.begin(), engine.begin()
        self.assertEqual(observer.get("old"), 1)
        left.put("left", 2)
        right.put("right", 3)
        self.assertEqual(left.commit(), 1)
        self.assertEqual(right.commit(), 2)
        self.assertEqual(observer.commit(), 2)
        self.assertEqual(engine.checkpoint(), {"version": 2, "data": {"left": 2, "old": 1, "right": 3}})

    def test_exports_are_defensive_at_every_container_level(self):
        engine = Engine({"a": 1})
        checkpoint = engine.checkpoint()
        checkpoint["version"] = 100
        checkpoint["data"]["a"] = 100
        checkpoint["data"]["b"] = 2
        transaction = engine.begin()
        transaction.put("a", 3)
        view = transaction.scan()
        view.clear()
        self.assertEqual(transaction.get("a"), 3)
        transaction.commit()
        records = engine.log_since(0)
        records[0]["writes"][0][0] = "wrong"
        records[0]["writes"][0][1] = 100
        records[0]["writes"].append(["extra", 8])
        records[0]["version"] = 100
        records.append({"version": 101, "writes": [["a", None]]})
        self.assertEqual(engine.checkpoint(), {"version": 1, "data": {"a": 3}})
        self.assertEqual(engine.log_since(0), [{"version": 1, "writes": [["a", 3]]}])


class ConflictContract(unittest.TestCase):
    def test_conflict_type(self):
        self.assertTrue(issubclass(Conflict, RuntimeError))

    def test_write_write_conflicts_are_atomic_and_close_transaction(self):
        for value in (1, 2, None):
            with self.subTest(value=value):
                engine = Engine({"key": 1})
                losing, winner = engine.begin(), engine.begin()
                losing.put("key", 9)
                losing.put("unrelated", 10)
                if value is None:
                    winner.delete("key")
                else:
                    winner.put("key", value)
                winner.commit()
                before, records = engine.checkpoint(), engine.log_since(0)
                with self.assertRaises(Conflict):
                    losing.commit()
                self.assertEqual(engine.checkpoint(), before)
                self.assertEqual(engine.log_since(0), records)
                with self.assertRaises(RuntimeError):
                    losing.abort()

    def test_read_only_aba_conflicts(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 1)
        for value in (2, 1):
            writer = engine.begin()
            writer.put("x", value)
            writer.commit()
        self.assertEqual(reader.get("x"), 1)
        with self.assertRaises(Conflict):
            reader.commit()
        self.assertEqual(engine.version, 2)

    def test_absent_key_and_empty_range_track_insert_then_delete(self):
        for use_scan in (False, True):
            with self.subTest(use_scan=use_scan):
                engine = Engine()
                reader = engine.begin()
                if use_scan:
                    self.assertEqual(reader.scan("job/"), {})
                else:
                    self.assertIsNone(reader.get("job/a"))
                writer = engine.begin()
                writer.put("job/a", 1)
                writer.commit()
                deleter = engine.begin()
                deleter.delete("job/a")
                deleter.commit()
                self.assertEqual(engine.checkpoint()["data"], {})
                with self.assertRaises(Conflict):
                    reader.commit()

    def test_matching_noop_tombstone_conflicts_with_range(self):
        engine = Engine({"outside": 7})
        reader, writer = engine.begin(), engine.begin()
        self.assertEqual(reader.scan("job/"), {})
        writer.delete("job/missing")
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()

    def test_empty_prefix_protects_all_keys_and_multiple_prefixes_remain(self):
        for prefix in ("", "job/", "task/"):
            with self.subTest(prefix=prefix):
                engine = Engine()
                reader, writer = engine.begin(), engine.begin()
                if prefix == "":
                    self.assertEqual(reader.scan(), {})
                    key = "anything"
                else:
                    reader.scan("job/")
                    reader.scan("task/")
                    key = prefix + "x"
                writer.put(key, 1)
                writer.commit()
                with self.assertRaises(Conflict):
                    reader.commit()

    def test_unrelated_write_does_not_conflict_with_empty_range(self):
        engine = Engine()
        reader, writer = engine.begin(), engine.begin()
        reader.scan("job/")
        reader.get("absent")
        writer.put("jobs/x", 1)
        writer.delete("different")
        writer.commit()
        self.assertEqual(reader.commit(), 1)


class SavepointContract(unittest.TestCase):
    def test_nested_rollback_release_and_name_reuse(self):
        engine = Engine({"x": 1, "y": 2})
        transaction = engine.begin()
        transaction.delete("x")
        transaction.put("a", 3)
        self.assertIsNone(transaction.savepoint("outer"))
        transaction.put("a", 4)
        transaction.delete("y")
        transaction.savepoint("inner")
        transaction.put("b", 5)
        transaction.delete("a")
        transaction.savepoint("tip")
        transaction.put("c", 6)
        self.assertIsNone(transaction.rollback_to("inner"))
        self.assertEqual(transaction.scan(), {"a": 4})
        with self.assertRaises(ValueError):
            transaction.rollback_to("tip")
        transaction.put("a", 8)
        transaction.rollback_to("inner")
        self.assertEqual(transaction.get("a"), 4)
        transaction.rollback_to("outer")
        self.assertEqual(transaction.scan(), {"a": 3, "y": 2})
        with self.assertRaises(ValueError):
            transaction.release("inner")
        transaction.savepoint("inner")
        transaction.put("d", 7)
        self.assertIsNone(transaction.release("outer"))
        with self.assertRaises(ValueError):
            transaction.release("inner")
        transaction.savepoint("outer")
        transaction.put("a", 9)
        transaction.rollback_to("outer")
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.checkpoint()["data"], {"a": 3, "d": 7, "y": 2})
        self.assertEqual(engine.log_since(0)[0]["writes"], [["a", 3], ["d", 7], ["x", None]])

    def test_duplicate_and_unknown_names_leave_writes_and_stack_unchanged(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("first")
        transaction.put("a", 2)
        transaction.savepoint("second")
        transaction.put("a", 3)
        for operation, name in (("savepoint", "first"), ("rollback_to", "missing"), ("release", "missing")):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    getattr(transaction, operation)(name)
                self.assertEqual(transaction.get("a"), 3)
        transaction.rollback_to("second")
        self.assertEqual(transaction.get("a"), 2)
        transaction.rollback_to("first")
        self.assertEqual(transaction.get("a"), 1)
        transaction.commit()

    def test_reads_survive_rollback_and_release(self):
        for operation in ("rollback_to", "release"):
            for read_kind in ("key", "range", "own_write"):
                with self.subTest(operation=operation, read_kind=read_kind):
                    engine = Engine()
                    reader, writer = engine.begin(), engine.begin()
                    reader.savepoint("s")
                    if read_kind == "key":
                        reader.get("watched")
                        key = "watched"
                    elif read_kind == "range":
                        reader.scan("watched/")
                        key = "watched/new"
                    else:
                        reader.put("watched", 9)
                        self.assertEqual(reader.get("watched"), 9)
                        key = "watched"
                    getattr(reader, operation)("s")
                    writer.put(key, 1)
                    writer.commit()
                    with self.assertRaises(Conflict):
                        reader.commit()

    def test_rollback_removes_unread_writes_and_can_produce_empty_commit(self):
        engine = Engine()
        reader, writer = engine.begin(), engine.begin()
        reader.savepoint("s")
        reader.put("discarded", 9)
        reader.rollback_to("s")
        writer.put("discarded", 1)
        writer.commit()
        self.assertEqual(reader.commit(), 1)
        self.assertEqual(engine.log_since(0), [{"version": 1, "writes": [["discarded", 1]]}])


class ValidationContract(unittest.TestCase):
    def test_invalid_initial_data(self):
        invalid = [[], (), False, 1, "data", {"": 1}, {None: 1}, {1: 2}]
        invalid.extend({"key": value} for value in (True, False, None, 1.0, "1", [], {}))
        for initial in invalid:
            with self.subTest(initial=initial):
                with self.assertRaises(ValueError):
                    Engine(initial)

    def test_invalid_operations_leave_pending_writes_and_savepoints_unchanged(self):
        engine = Engine({"seed": 1})
        transaction = engine.begin()
        transaction.put("keep", 99)
        transaction.savepoint("stable")
        calls = []
        for bad in ("", None, True, 3, 1.0, [], {}):
            calls.extend((method, args) for method, args in (
                ("get", (bad,)), ("delete", (bad,)), ("put", (bad, 1)),
                ("savepoint", (bad,)), ("rollback_to", (bad,)), ("release", (bad,)),
            ))
        for bad in (True, False, None, 1.0, "1", [], {}):
            calls.append(("put", ("keep", bad)))
        for bad in (None, False, 1, 1.0, [], {}):
            calls.append(("scan", (bad,)))
        calls.extend((("savepoint", ("stable",)), ("rollback_to", ("missing",)), ("release", ("missing",))))
        for method, args in calls:
            with self.subTest(method=method, args=args):
                with self.assertRaises(ValueError):
                    getattr(transaction, method)(*args)
                self.assertEqual(engine.checkpoint(), {"version": 0, "data": {"seed": 1}})
                self.assertEqual(engine.log_since(0), [])
                self.assertEqual(transaction.scan(), {"keep": 99, "seed": 1})
        transaction.put("keep", 100)
        transaction.rollback_to("stable")
        transaction.commit()
        self.assertEqual(engine.log_since(0), [{"version": 1, "writes": [["keep", 99]]}])

    def test_invalid_calls_do_not_add_dependencies(self):
        engine = Engine()
        reader, writer = engine.begin(), engine.begin()
        for method, args in (("scan", (None,)), ("get", (None,)), ("put", ("key", True)), ("delete", ([],))):
            with self.assertRaises(ValueError):
                getattr(reader, method)(*args)
        writer.put("key", 1)
        writer.commit()
        self.assertEqual(reader.commit(), 1)

    def test_closed_methods_check_lifecycle_before_arguments(self):
        for closure in ("abort", "empty_commit", "writing_commit", "conflict"):
            with self.subTest(closure=closure):
                engine = Engine()
                transaction = engine.begin()
                if closure == "abort":
                    transaction.abort()
                elif closure == "empty_commit":
                    transaction.commit()
                elif closure == "writing_commit":
                    transaction.put("key", 1)
                    transaction.commit()
                else:
                    transaction.get("key")
                    writer = engine.begin()
                    writer.put("key", 1)
                    writer.commit()
                    with self.assertRaises(Conflict):
                        transaction.commit()
                before = engine.checkpoint()
                for method, args in (
                    ("get", (None,)), ("get", ("key",)), ("scan", (None,)), ("scan", ()),
                    ("put", (None, None)), ("delete", (None,)), ("savepoint", (None,)),
                    ("rollback_to", (None,)), ("release", (None,)), ("commit", ()), ("abort", ()),
                ):
                    with self.subTest(method=method, args=args):
                        with self.assertRaises(RuntimeError) as raised:
                            getattr(transaction, method)(*args)
                        self.assertIs(type(raised.exception), RuntimeError)
                self.assertEqual(engine.checkpoint(), before)

    def test_invalid_log_versions(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.delete("missing")
        transaction.commit()
        for version in (-1, 2, True, False, 0.0, "0", None, [], {}):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    engine.log_since(version)
        self.assertEqual(engine.version, 1)


class RecoveryContract(unittest.TestCase):
    def test_restore_nonzero_base_and_defensive_inputs(self):
        checkpoint = {"version": 10, "data": {"x": 1}}
        records = [
            {"version": 11, "writes": [["absent", None], ["x", 1]]},
            {"version": 12, "writes": [["x", None], ["y", -10 ** 1000]]},
        ]
        original_records = copy.deepcopy(records)
        restored = Engine.restore(checkpoint, records)
        checkpoint["data"].clear()
        checkpoint["version"] = 100
        records[0]["writes"][1][1] = 99
        records[1]["writes"].clear()
        records.clear()
        self.assertEqual(restored.version, 12)
        self.assertEqual(restored.checkpoint()["data"], {"y": -10 ** 1000})
        self.assertEqual(restored.log_since(10), original_records)
        self.assertEqual(restored.log_since(11), original_records[1:])
        self.assertEqual(restored.log_since(12), [])
        with self.assertRaises(ValueError):
            restored.log_since(9)
        reader = restored.begin()
        reader.get("x")
        reader.scan("absent")
        self.assertEqual(reader.commit(), 12)
        writer = restored.begin()
        writer.put("z", 3)
        self.assertEqual(writer.commit(), 13)
        self.assertEqual(restored.log_since(12), [{"version": 13, "writes": [["z", 3]]}])

    def test_restore_latest_checkpoint_without_logs(self):
        engine = Engine.restore({"version": 25, "data": {"z": 1, "a": 2}}, [])
        self.assertEqual(engine.version, 25)
        self.assertEqual(list(engine.checkpoint()["data"]), ["a", "z"])
        self.assertEqual(engine.log_since(25), [])
        with self.assertRaises(ValueError):
            engine.log_since(24)
        stale, writer = engine.begin(), engine.begin()
        stale.get("a")
        writer.put("a", 2)
        self.assertEqual(writer.commit(), 26)
        with self.assertRaises(Conflict):
            stale.commit()

    def test_invalid_checkpoints(self):
        invalid = [None, [], {}, {"version": 0}, {"data": {}}, {"version": 0, "data": {}, "extra": 1}]
        invalid.extend({"version": version, "data": {}} for version in (-1, True, False, 1.0, "0", None))
        invalid.extend({"version": 0, "data": data} for data in (None, [], (), {"": 1}, {1: 1}, {"a": True}, {"a": None}))
        for checkpoint in invalid:
            with self.subTest(checkpoint=checkpoint):
                before = copy.deepcopy(checkpoint)
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, [])
                self.assertEqual(checkpoint, before)

    def test_invalid_logs_are_rejected_without_mutating_inputs(self):
        checkpoint = {"version": 3, "data": {"original": 7}}
        invalid = [None, (), {}, "records", [None], [[]], [{}], [{"version": 4}], [{"writes": [["a", 1]]}]]
        invalid.append([{"version": 4, "writes": [["a", 1]], "extra": 1}])
        invalid.extend([{"version": version, "writes": [["a", 1]]}] for version in (True, False, 4.0, "4", None, -1, 3, 5))
        bad_writes = [
            [], (), None, {}, "writes", [("a", 1)], [None], ["a"], [["a"]], [["a", 1, 2]],
            [["", 1]], [[None, 1]], [[1, 1]], [[[], 1]], [["a", True]], [["a", False]],
            [["a", 1.0]], [["a", "1"]], [["a", []]], [["a", {}]],
            [["b", 1], ["a", 2]], [["a", 1], ["a", None]],
        ]
        invalid.extend([{"version": 4, "writes": writes}] for writes in bad_writes)
        invalid.extend([
            [{"version": 4, "writes": [["a", 1]]}, {"version": 4, "writes": [["b", 2]]}],
            [{"version": 4, "writes": [["a", 1]]}, {"version": 6, "writes": [["b", 2]]}],
            [{"version": 4, "writes": [["a", 1]]}, {"version": 5, "writes": [["b", False]]}],
        ])
        for records in invalid:
            with self.subTest(records=records):
                before = copy.deepcopy(records)
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, records)
                self.assertEqual(records, before)
                self.assertEqual(checkpoint, {"version": 3, "data": {"original": 7}})

    def test_deterministic_mixed_traces_and_recovery_round_trips(self):
        rng = random.Random(271828)
        cases = list(range(5)) * 10
        rng.shuffle(cases)
        engine = Engine({"seed": -1})
        expected_data = {"seed": -1}
        for index, case in enumerate(cases):
            checkpoint = engine.checkpoint()
            base = engine.version
            reader = engine.begin()
            local = "local/" + str(index)
            missing = "missing/" + str(index)
            watched = "watched/" + str(index)
            prefix = "range/" + str(index) + "/"
            scratch = "scratch/" + str(index)
            reader.put(local, index)
            reader.delete(missing)
            reader.savepoint("base")
            reader.put(local, -index)
            reader.put(scratch, 99)
            reader.savepoint("newer")
            self.assertIsNone(reader.get(watched))
            self.assertEqual(reader.scan(prefix), {})
            if case == 2:
                self.assertEqual(reader.get(scratch), 99)
            reader.release("newer")
            reader.rollback_to("base")
            self.assertEqual(reader.get(local), index)
            remote = [watched, prefix + "child", scratch, scratch, "other/" + str(index)][case]
            records = []
            for value in (index, None):
                writer = engine.begin()
                if value is None:
                    writer.delete(remote)
                else:
                    writer.put(remote, value)
                version = writer.commit()
                records.append({"version": version, "writes": [[remote, value]]})
            if case < 3:
                with self.assertRaises(Conflict):
                    reader.commit()
            else:
                version = reader.commit()
                expected_data[local] = index
                records.append({"version": version, "writes": [[local, index], [missing, None]]})
            self.assertEqual(engine.checkpoint()["data"], expected_data)
            self.assertEqual(engine.log_since(base), records)
            engine = Engine.restore(checkpoint, records)
            self.assertEqual(engine.checkpoint(), {"version": base + len(records), "data": expected_data})
            self.assertEqual(engine.log_since(base), records)
            if base:
                with self.assertRaises(ValueError):
                    engine.log_since(base - 1)


if __name__ == "__main__":
    unittest.main()
