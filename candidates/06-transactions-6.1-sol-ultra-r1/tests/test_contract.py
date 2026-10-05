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


class TransactionContract(unittest.TestCase):
    def test_snapshots_pending_writes_and_sorted_exports(self):
        large = 10**200
        initial = {"z": -large, "a": 0, "é": large}
        engine = Engine(initial)
        initial["a"] = 999
        initial["new"] = 1
        transaction = engine.begin()
        self.assertEqual(list(engine.checkpoint()["data"]), ["a", "z", "é"])
        self.assertEqual(transaction.scan(), {"a": 0, "z": -large, "é": large})
        self.assertIsNone(transaction.put("b", -large))
        transaction.put("b", large)
        self.assertIsNone(transaction.delete("z"))
        transaction.put("z", 9)
        transaction.delete("z")
        self.assertIsNone(transaction.get("z"))
        self.assertEqual(transaction.get("b"), large)
        self.assertEqual(list(transaction.scan()), ["a", "b", "é"])
        self.assertEqual(engine.checkpoint()["data"], {"a": 0, "z": -large, "é": large})
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(
            engine.log_since(0),
            [{"version": 1, "writes": [["b", large], ["z", None]]}],
        )

    def test_snapshot_does_not_change_after_concurrent_commit(self):
        engine = Engine({"x": 1, "y": 2})
        old = engine.begin()
        current = engine.begin()
        current.put("x", 9)
        current.delete("y")
        current.put("z", 3)
        current.commit()
        self.assertEqual(old.get("x"), 1)
        self.assertEqual(old.scan(), {"x": 1, "y": 2})
        fresh = engine.begin()
        self.assertEqual(fresh.scan(), {"x": 9, "z": 3})
        fresh.abort()
        with self.assertRaises(Conflict):
            old.commit()

    def test_same_value_and_absent_deletion_create_versions(self):
        engine = Engine({"x": 1})
        self.assertEqual(engine.begin().commit(), 0)
        transaction = engine.begin()
        transaction.put("x", 1)
        transaction.delete("absent")
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(
            engine.log_since(0),
            [{"version": 1, "writes": [["absent", None], ["x", 1]]}],
        )
        idle = engine.begin()
        write(engine, "other", 2)
        self.assertEqual(idle.commit(), 2)
        self.assertEqual(engine.version, 2)
        self.assertEqual(len(engine.log_since(0)), 2)

    def test_point_read_blind_write_and_delete_conflicts(self):
        for operation in ("get", "put", "delete"):
            for concurrent_value in (1, None):
                with self.subTest(operation=operation, concurrent_value=concurrent_value):
                    engine = Engine({"x": 1})
                    transaction = engine.begin()
                    if operation == "put":
                        transaction.put("x", 7)
                    else:
                        getattr(transaction, operation)("x")
                    write(engine, "x", concurrent_value)
                    before = engine.checkpoint(), engine.log_since(0)
                    with self.assertRaises(Conflict):
                        transaction.commit()
                    self.assertEqual((engine.checkpoint(), engine.log_since(0)), before)
                    with self.assertRaises(RuntimeError):
                        transaction.abort()

    def test_absent_read_conflicts_even_with_absent_deletion(self):
        engine = Engine()
        reader = engine.begin()
        self.assertIsNone(reader.get("missing"))
        write(engine, "missing", None)
        with self.assertRaises(Conflict):
            reader.commit()

    def test_aba_and_insert_delete_conflict_with_read_only_transactions(self):
        for initial_value in (None, 1):
            with self.subTest(initial_value=initial_value):
                engine = Engine({} if initial_value is None else {"x": initial_value})
                reader = engine.begin()
                self.assertEqual(reader.get("x"), initial_value)
                write(engine, "x", 99)
                write(engine, "x", initial_value)
                self.assertEqual(reader.get("x"), initial_value)
                with self.assertRaises(Conflict):
                    reader.commit()
                self.assertEqual(engine.version, 2)

    def test_empty_prefix_scan_tracks_transient_matching_writes(self):
        engine = Engine({"job": 1, "jobs/x": 2})
        reader = engine.begin()
        self.assertEqual(reader.scan("job/"), {})
        write(engine, "job/one", 3)
        write(engine, "job/one", None)
        self.assertEqual(reader.scan("job/"), {})
        with self.assertRaises(Conflict):
            reader.commit()

    def test_matching_range_conflicts_and_unrelated_writes_do_not(self):
        engine = Engine({"a/one": 1, "a/two": 2})
        reader = engine.begin()
        self.assertEqual(reader.scan("a/"), {"a/one": 1, "a/two": 2})
        reader.put("mine", 3)
        write(engine, "ab/one", 4)
        self.assertEqual(reader.commit(), 2)
        scanner = engine.begin()
        scanner.scan("")
        write(engine, "anywhere", None)
        with self.assertRaises(Conflict):
            scanner.commit()

    def test_overlapping_scans_and_own_tombstones(self):
        engine = Engine({"a/x": 1, "a/y": 2, "b/x": 3})
        transaction = engine.begin()
        transaction.delete("a/x")
        transaction.put("a/z", 4)
        transaction.put("a/y", 5)
        self.assertEqual(transaction.scan("a/"), {"a/y": 5, "a/z": 4})
        self.assertEqual(transaction.scan("a/z"), {"a/z": 4})
        self.assertEqual(transaction.scan("b/"), {"b/x": 3})
        write(engine, "a/new", 6)
        with self.assertRaises(Conflict):
            transaction.commit()

    def test_savepoint_stack_rollback_release_and_name_reuse(self):
        engine = Engine({"original": 1})
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.delete("original")
        self.assertIsNone(transaction.savepoint("outer"))
        transaction.put("a", 2)
        transaction.savepoint("middle")
        transaction.put("b", 3)
        transaction.savepoint("inner")
        transaction.put("c", 4)
        self.assertIsNone(transaction.rollback_to("middle"))
        self.assertEqual(transaction.scan(), {"a": 2})
        with self.assertRaises(ValueError):
            transaction.rollback_to("inner")
        with self.assertRaises(ValueError):
            transaction.savepoint("middle")
        transaction.put("a", 99)
        transaction.rollback_to("middle")
        self.assertEqual(transaction.get("a"), 2)
        transaction.savepoint("inner")
        transaction.put("retained", 7)
        self.assertIsNone(transaction.release("middle"))
        for name in ("middle", "inner"):
            with self.assertRaises(ValueError):
                transaction.release(name)
        self.assertEqual(transaction.get("retained"), 7)
        transaction.savepoint("middle")
        transaction.rollback_to("outer")
        self.assertEqual(transaction.scan(), {"a": 1})
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.checkpoint()["data"], {"a": 1})

    def test_rollback_and_release_preserve_point_and_range_reads(self):
        for adjustment in ("rollback_to", "release"):
            for read_kind in ("get", "scan"):
                with self.subTest(adjustment=adjustment, read_kind=read_kind):
                    engine = Engine()
                    transaction = engine.begin()
                    transaction.savepoint("s")
                    if read_kind == "get":
                        transaction.get("watched/key")
                    else:
                        transaction.scan("watched/")
                    getattr(transaction, adjustment)("s")
                    write(engine, "watched/key", 1)
                    with self.assertRaises(Conflict):
                        transaction.commit()

    def test_reads_of_own_writes_remain_after_rollback(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("s")
        transaction.put("x", 1)
        self.assertEqual(transaction.get("x"), 1)
        transaction.rollback_to("s")
        write(engine, "x", 2)
        with self.assertRaises(Conflict):
            transaction.commit()

    def test_rolled_back_blind_writes_do_not_conflict(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("s")
        transaction.put("x", 1)
        transaction.delete("y")
        transaction.rollback_to("s")
        write(engine, "x", 2)
        write(engine, "y", None)
        self.assertEqual(transaction.commit(), 2)
        self.assertEqual(engine.checkpoint()["data"], {"x": 2})

    def test_abort_discards_all_pending_writes(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        transaction.put("x", 2)
        transaction.put("new", 3)
        transaction.savepoint("s")
        self.assertIsNone(transaction.abort())
        self.assertEqual(engine.checkpoint(), {"version": 0, "data": {"x": 1}})
        self.assertEqual(engine.log_since(0), [])

    def test_closed_methods_check_lifecycle_before_arguments(self):
        for closing in ("abort", "commit", "conflict"):
            with self.subTest(closing=closing):
                engine = Engine()
                transaction = engine.begin()
                if closing == "conflict":
                    transaction.get("x")
                    write(engine, "x", 1)
                    with self.assertRaises(Conflict):
                        transaction.commit()
                else:
                    getattr(transaction, closing)()
                before = engine.checkpoint(), engine.log_since(0)
                calls = [
                    ("get", [None]), ("scan", [None]), ("put", [[], None]),
                    ("delete", [""]), ("savepoint", [None]),
                    ("rollback_to", ["unknown"]), ("release", [None]),
                    ("commit", []), ("abort", []),
                ]
                for method, arguments in calls:
                    with self.subTest(method=method):
                        with self.assertRaises(RuntimeError):
                            getattr(transaction, method)(*arguments)
                self.assertEqual((engine.checkpoint(), engine.log_since(0)), before)

    def test_invalid_arguments_do_not_modify_transaction(self):
        engine = Engine({"watched": 1})
        transaction = engine.begin()
        transaction.put("kept", 2)
        transaction.savepoint("s")
        transaction.put("kept", 3)
        invalid_names = [None, "", 0, True, [], {}, b"name"]
        for name in invalid_names:
            for method in ("get", "delete", "savepoint", "rollback_to", "release"):
                with self.subTest(method=method, name=name):
                    with self.assertRaises(ValueError):
                        getattr(transaction, method)(name)
            with self.assertRaises(ValueError):
                transaction.put(name, 1)
        for value in (None, True, False, 1.0, "1", [], {}, (1,)):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    transaction.put("watched", value)
        for prefix in (None, 1, False, [], {}, b""):
            with self.assertRaises(ValueError):
                transaction.scan(prefix)
        for method, name in (("savepoint", "s"), ("rollback_to", "missing"), ("release", "missing")):
            with self.assertRaises(ValueError):
                getattr(transaction, method)(name)
        self.assertEqual(transaction.get("kept"), 3)
        transaction.rollback_to("s")
        self.assertEqual(transaction.get("kept"), 2)
        write(engine, "watched", 7)
        self.assertEqual(transaction.commit(), 2)
        self.assertEqual(engine.checkpoint()["data"], {"kept": 2, "watched": 7})


class RecoveryContract(unittest.TestCase):
    def test_constructor_validation(self):
        invalid_initials = [[], (), "", 0, False, {"": 1}, {1: 2}]
        invalid_initials.extend({"x": value} for value in (None, True, False, 2.0, "2", [], {}))
        for initial in invalid_initials:
            with self.subTest(initial=initial):
                with self.assertRaises(ValueError):
                    Engine(initial)
        self.assertEqual(Engine().version, 0)
        self.assertEqual(Engine({}).checkpoint(), {"version": 0, "data": {}})

    def test_exports_are_defensive_at_every_mutable_level(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        scan = transaction.scan()
        scan["x"] = 99
        scan["new"] = 2
        self.assertEqual(transaction.get("x"), 1)
        self.assertIsNone(transaction.get("new"))
        transaction.abort()
        write(engine, "x", 3)
        expected = engine.checkpoint(), engine.log_since(0)
        checkpoint = engine.checkpoint()
        checkpoint["data"]["x"] = 99
        checkpoint["version"] = 99
        checkpoint["extra"] = []
        records = engine.log_since(0)
        records[0]["writes"][0][0] = "corrupt"
        records[0]["writes"][0][1] = 99
        records[0]["writes"].append(["new", 4])
        records[0]["version"] = 9
        records.append({"version": 10, "writes": [["other", 5]]})
        self.assertEqual((engine.checkpoint(), engine.log_since(0)), expected)

    def test_restore_retains_base_log_and_does_not_alias_inputs(self):
        checkpoint = {"version": 10, "data": {"z": 9, "x": 1}}
        records = [
            {"version": 11, "writes": [["absent", None], ["x", 2]]},
            {"version": 12, "writes": [["x", None], ["y", -10**100]]},
        ]
        retained = copy.deepcopy(records)
        engine = Engine.restore(checkpoint, records)
        self.assertEqual(engine.version, 12)
        self.assertEqual(engine.checkpoint()["data"], {"y": -10**100, "z": 9})
        self.assertEqual(list(engine.checkpoint()["data"]), ["y", "z"])
        self.assertEqual(engine.log_since(10), retained)
        self.assertEqual(engine.log_since(11), retained[1:])
        self.assertEqual(engine.log_since(12), [])
        checkpoint["data"].clear()
        checkpoint["version"] = 100
        records[0]["writes"][0][:] = ["corrupt", 9]
        records[1]["version"] = 999
        records.clear()
        self.assertEqual(engine.log_since(10), retained)
        with self.assertRaises(ValueError):
            engine.log_since(9)
        self.assertEqual(write(engine, "x", 3), 13)
        self.assertEqual(engine.log_since(12), [{"version": 13, "writes": [["x", 3]]}])
        self.assertEqual(engine.log_since(10)[:2], retained)

    def test_restored_history_precedes_new_snapshots(self):
        engine = Engine.restore(
            {"version": 3, "data": {"x": 1}},
            [{"version": 4, "writes": [["x", 1]]}],
        )
        transaction = engine.begin()
        transaction.get("x")
        self.assertEqual(transaction.commit(), 4)
        stale = engine.begin()
        stale.get("x")
        write(engine, "x", 1)
        with self.assertRaises(Conflict):
            stale.commit()

    def test_large_checkpoint_version_and_empty_log(self):
        version = 10**200
        engine = Engine.restore({"version": version, "data": {}}, [])
        self.assertEqual(engine.begin().commit(), version)
        self.assertEqual(engine.log_since(version), [])
        self.assertEqual(write(engine, "x", -(10**300)), version + 1)
        self.assertEqual(engine.log_since(version)[0]["version"], version + 1)
        with self.assertRaises(ValueError):
            engine.log_since(version - 1)

    def test_invalid_log_since_leaves_engine_unchanged(self):
        engine = Engine.restore({"version": 4, "data": {}}, [])
        write(engine, "x", 1)
        before = engine.checkpoint(), engine.log_since(4)
        for version in (None, True, False, 4.0, "4", [], {}, -1, 0, 3, 6):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    engine.log_since(version)
        self.assertEqual((engine.checkpoint(), engine.log_since(4)), before)

    def test_invalid_checkpoints(self):
        checkpoints = [
            None, [], (), {}, {"version": 0}, {"data": {}},
            {"version": 0, "data": {}, "extra": 1},
        ]
        checkpoints.extend({"version": version, "data": {}} for version in (-1, True, False, None, 0.0, "0", []))
        checkpoints.extend({"version": 0, "data": data} for data in (None, [], (), {"": 1}, {1: 2}, {"x": True}, {"x": None}, {"x": 1.0}))
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint):
                original = copy.deepcopy(checkpoint)
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, [])
                self.assertEqual(checkpoint, original)

    def test_invalid_record_shapes_and_types(self):
        checkpoint = {"version": 0, "data": {"x": 1}}
        invalid_records = [
            None, (), {}, "", [None], [[]], [{}],
            [{"version": 1}], [{"writes": [["x", 2]]}],
            [{"version": 1, "writes": [["x", 2]], "extra": 0}],
        ]
        invalid_records.extend(
            [{"version": version, "writes": [["x", 2]]}]
            for version in (0, 2, -1, True, False, 1.0, "1", None, [])
        )
        invalid_records.extend(
            [{"version": 1, "writes": writes}]
            for writes in (
                [], None, {}, (), (("x", 2),), [("x", 2)],
                [None], ["x2"], [["x"]], [["x", 2, 3]],
                [["", 2]], [[1, 2]], [[[], 2]], [["x", True]],
                [["x", False]], [["x", 2.0]], [["x", "2"]],
                [["x", []]], [["x", 1], ["x", 2]],
                [["z", 1], ["a", 2]],
            )
        )
        for records in invalid_records:
            with self.subTest(records=records):
                original = copy.deepcopy(records)
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, records)
                self.assertEqual(records, original)
                self.assertEqual(checkpoint, {"version": 0, "data": {"x": 1}})

    def test_invalid_later_record_rejects_entire_log(self):
        checkpoint = {"version": 5, "data": {"a": 1}}
        for later in (
            {"version": 8, "writes": [["a", 2]]},
            {"version": 6, "writes": [["a", 2]]},
            {"version": 7, "writes": []},
            {"version": 7, "writes": [["a", None], ["b", True]]},
        ):
            records = [{"version": 6, "writes": [["a", None]]}, later]
            with self.subTest(later=later):
                original = copy.deepcopy((checkpoint, records))
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, records)
                self.assertEqual((checkpoint, records), original)

    def test_deterministic_interleavings_and_repeated_recovery(self):
        rng = random.Random(937)
        engine = Engine()
        expected = {}
        version = 0
        for step in range(50):
            checkpoint = engine.checkpoint()
            base = engine.version
            key = "job/{}".format(rng.randrange(6))
            other = "other/{}".format(rng.randrange(6))
            value = rng.randrange(-(10**40), 10**40)
            stale = engine.begin()
            stale.scan("job/")
            independent = engine.begin()
            independent.get("never-written")
            transaction = engine.begin()
            transaction.put(other, value)
            transaction.savepoint("keep")
            transaction.put("rolled-back", 99)
            transaction.delete(other)
            transaction.savepoint("discard")
            transaction.rollback_to("keep")
            transaction.release("keep")
            transaction.put(key, value)
            version += 1
            self.assertEqual(transaction.commit(), version)
            expected[other] = expected[key] = value
            if step % 2 == 0:
                version += 1
                self.assertEqual(write(engine, key, None), version)
                del expected[key]
            before = engine.checkpoint()
            with self.assertRaises(Conflict):
                stale.commit()
            self.assertEqual(engine.checkpoint(), before)
            self.assertEqual(independent.commit(), version)
            self.assertEqual(engine.checkpoint(), {"version": version, "data": expected})
            records = engine.log_since(base)
            restored = Engine.restore(checkpoint, records)
            self.assertEqual(restored.checkpoint(), engine.checkpoint())
            self.assertEqual(restored.log_since(base), records)
            engine = restored


if __name__ == "__main__":
    unittest.main()
