import copy
import random
import unittest

from mvcc import Conflict, Engine


class TransactionContract(unittest.TestCase):
    def assert_closed(self, transaction):
        calls = [
            lambda: transaction.get("x"),
            lambda: transaction.get([]),
            lambda: transaction.scan(),
            lambda: transaction.scan(None),
            lambda: transaction.put("x", 1),
            lambda: transaction.put(None, True),
            lambda: transaction.delete("x"),
            lambda: transaction.delete([]),
            lambda: transaction.savepoint("s"),
            lambda: transaction.savepoint(None),
            lambda: transaction.rollback_to("s"),
            lambda: transaction.rollback_to([]),
            lambda: transaction.release("s"),
            lambda: transaction.release(None),
            transaction.commit,
            transaction.abort,
        ]
        for call in calls:
            with self.assertRaises(RuntimeError) as raised:
                call()
            self.assertIs(type(raised.exception), RuntimeError)

    def test_snapshot_and_own_writes(self):
        initial = {"z": 3, "a": 1, "pre/old": 2}
        engine = Engine(initial)
        old = engine.begin()
        initial["a"] = 99
        writer = engine.begin()
        writer.put("a", 5)
        writer.put("pre/new", 6)
        writer.delete("pre/old")
        writer.commit()

        self.assertEqual(old.get("a"), 1)
        self.assertIsNone(old.get("pre/new"))
        self.assertIsNone(old.put("b", -(10 ** 100)))
        self.assertIsNone(old.delete("z"))
        self.assertIsNone(old.put("pre/own", 10 ** 200))
        visible = old.scan()
        self.assertEqual(list(visible), ["a", "b", "pre/old", "pre/own"])
        self.assertEqual(visible["b"], -(10 ** 100))
        self.assertEqual(old.scan("pre/"), {"pre/old": 2, "pre/own": 10 ** 200})
        visible["a"] = 100
        self.assertEqual(old.get("a"), 1)
        self.assertEqual(engine.checkpoint()["data"], {"a": 5, "pre/new": 6, "z": 3})
        old.abort()

    def test_one_record_contains_last_write_for_each_key(self):
        engine = Engine({"same": 5})
        transaction = engine.begin()
        transaction.put("z", 1)
        transaction.put("z", 2)
        transaction.delete("z")
        transaction.put("a", -10 ** 90)
        transaction.delete("absent")
        transaction.put("same", 5)
        self.assertEqual(engine.checkpoint(), {"version": 0, "data": {"same": 5}})
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0), [{
            "version": 1,
            "writes": [["a", -10 ** 90], ["absent", None], ["same", 5], ["z", None]],
        }])
        self.assertEqual(list(engine.checkpoint()["data"]), ["a", "same"])
        self.assert_closed(transaction)

    def test_write_free_commit_returns_current_version(self):
        engine = Engine({"read": 1})
        untouched, reader = engine.begin(), engine.begin()
        reader.get("read")
        writer = engine.begin()
        writer.put("unrelated", 2)
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(untouched.commit(), 1)
        self.assertEqual(reader.commit(), 1)
        self.assertEqual(len(engine.log_since(0)), 1)
        self.assert_closed(untouched)
        self.assert_closed(reader)

    def test_abort_discards_pending_writes_and_closes(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        transaction.delete("x")
        transaction.put("y", 2)
        transaction.savepoint("s")
        self.assertIsNone(transaction.abort())
        self.assertEqual(engine.checkpoint(), {"version": 0, "data": {"x": 1}})
        self.assertEqual(engine.log_since(0), [])
        self.assert_closed(transaction)

    def test_conflict_checks_same_value_and_aba_history(self):
        for changes in ([1], [2, 1]):
            with self.subTest(changes=changes):
                engine = Engine({"x": 1})
                reader = engine.begin()
                self.assertEqual(reader.get("x"), 1)
                for value in changes:
                    writer = engine.begin()
                    writer.put("x", value)
                    writer.commit()
                before = engine.checkpoint(), engine.log_since(0)
                with self.assertRaises(Conflict):
                    reader.commit()
                self.assertEqual((engine.checkpoint(), engine.log_since(0)), before)
                self.assert_closed(reader)

    def test_missing_key_read_conflicts_with_missing_key_delete(self):
        engine = Engine()
        reader, writer = engine.begin(), engine.begin()
        self.assertIsNone(reader.get("missing"))
        writer.delete("missing")
        self.assertEqual(writer.commit(), 1)
        with self.assertRaises(Conflict):
            reader.commit()
        self.assertEqual(engine.checkpoint(), {"version": 1, "data": {}})

    def test_empty_scan_conflicts_with_insert_then_delete(self):
        for prefix in ("job/", ""):
            with self.subTest(prefix=prefix):
                engine = Engine()
                reader = engine.begin()
                self.assertEqual(reader.scan(prefix), {})
                insert = engine.begin()
                insert.put("job/one", 1)
                insert.commit()
                delete = engine.begin()
                delete.delete("job/one")
                delete.commit()
                with self.assertRaises(Conflict):
                    reader.commit()
                self.assertEqual(engine.version, 2)

    def test_range_read_conflicts_on_delete_or_same_value_write(self):
        for delete in (False, True):
            with self.subTest(delete=delete):
                engine = Engine({"job/one": 1})
                reader, writer = engine.begin(), engine.begin()
                self.assertEqual(reader.scan("job/"), {"job/one": 1})
                if delete:
                    writer.delete("job/one")
                else:
                    writer.put("job/one", 1)
                writer.commit()
                with self.assertRaises(Conflict):
                    reader.commit()

    def test_unrelated_point_and_prefix_writes_do_not_conflict(self):
        engine = Engine({"job/a": 1, "point": 2})
        reader, writer = engine.begin(), engine.begin()
        reader.get("point")
        reader.scan("job/")
        reader.put("mine", 3)
        writer.put("job", 4)
        writer.put("other", 5)
        writer.commit()
        self.assertEqual(reader.commit(), 2)
        self.assertEqual(engine.checkpoint()["data"], {
            "job": 4, "job/a": 1, "mine": 3, "other": 5, "point": 2,
        })

    def test_blind_writes_conflict_and_failed_commit_is_atomic(self):
        for delete in (False, True):
            with self.subTest(delete=delete):
                engine = Engine({"x": 1})
                first, second = engine.begin(), engine.begin()
                first.delete("x")
                if delete:
                    second.delete("x")
                else:
                    second.put("x", 1)
                second.put("must-not-appear", 99)
                first.commit()
                before = engine.checkpoint(), engine.log_since(0)
                with self.assertRaises(Conflict):
                    second.commit()
                self.assertEqual((engine.checkpoint(), engine.log_since(0)), before)
                self.assert_closed(second)

    def test_nested_savepoints_restore_writes_and_keep_named_point(self):
        engine = Engine({"x": 0})
        transaction = engine.begin()
        transaction.put("x", 1)
        self.assertIsNone(transaction.savepoint("outer"))
        transaction.delete("x")
        self.assertIsNone(transaction.savepoint("inner"))
        transaction.put("extra", 2)
        self.assertIsNone(transaction.rollback_to("inner"))
        self.assertEqual(transaction.scan(), {})
        transaction.put("extra", 3)
        self.assertIsNone(transaction.rollback_to("inner"))
        self.assertEqual(transaction.scan(), {})
        self.assertIsNone(transaction.rollback_to("outer"))
        self.assertEqual(transaction.scan(), {"x": 1})
        with self.assertRaises(ValueError):
            transaction.rollback_to("inner")
        transaction.savepoint("inner")
        with self.assertRaises(ValueError):
            transaction.savepoint("outer")
        transaction.commit()
        self.assertEqual(engine.log_since(0), [{"version": 1, "writes": [["x", 1]]}])

    def test_release_discards_named_point_and_newer_but_preserves_writes(self):
        transaction = Engine().begin()
        transaction.savepoint("one")
        transaction.put("x", 1)
        transaction.savepoint("two")
        transaction.put("y", 2)
        transaction.savepoint("three")
        transaction.delete("x")
        self.assertIsNone(transaction.release("two"))
        self.assertEqual(transaction.scan(), {"y": 2})
        for name in ("two", "three"):
            with self.assertRaises(ValueError):
                transaction.rollback_to(name)
            transaction.savepoint(name)
        transaction.rollback_to("one")
        self.assertEqual(transaction.scan(), {})
        transaction.release("one")
        transaction.savepoint("one")
        self.assertEqual(transaction.commit(), 0)

    def test_savepoint_rollback_and_release_retain_all_reads(self):
        for operation in ("rollback_to", "release"):
            for read in ("get", "scan"):
                with self.subTest(operation=operation, read=read):
                    engine = Engine()
                    transaction = engine.begin()
                    transaction.savepoint("s")
                    if read == "get":
                        transaction.put("job/one", 1)
                        self.assertEqual(transaction.get("job/one"), 1)
                    else:
                        self.assertEqual(transaction.scan("job/"), {})
                    getattr(transaction, operation)("s")
                    writer = engine.begin()
                    writer.put("job/one", 2)
                    writer.commit()
                    with self.assertRaises(Conflict):
                        transaction.commit()

    def test_rollback_removes_blind_writes_from_pending_set(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("s")
        transaction.put("discarded", 1)
        transaction.rollback_to("s")
        writer = engine.begin()
        writer.put("discarded", 2)
        writer.commit()
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.checkpoint()["data"], {"discarded": 2})

    def test_invalid_transaction_operations_leave_writes_and_savepoints_intact(self):
        invalid_names = (None, "", 0, True, [], {})
        invalid_values = (None, True, False, 1.0, "1", [], {})
        calls = []
        for name in invalid_names:
            calls.extend([
                lambda t, name=name: t.get(name),
                lambda t, name=name: t.put(name, 1),
                lambda t, name=name: t.delete(name),
                lambda t, name=name: t.savepoint(name),
                lambda t, name=name: t.rollback_to(name),
                lambda t, name=name: t.release(name),
            ])
        for value in invalid_values:
            calls.append(lambda t, value=value: t.put("kept", value))
        for prefix in (None, 0, False, [], {}):
            calls.append(lambda t, prefix=prefix: t.scan(prefix))
        calls.extend([
            lambda t: t.savepoint("live"),
            lambda t: t.rollback_to("unknown"),
            lambda t: t.release("unknown"),
        ])
        for index, call in enumerate(calls):
            with self.subTest(index=index):
                engine = Engine({"original": 1})
                transaction = engine.begin()
                transaction.put("kept", 2)
                transaction.savepoint("live")
                transaction.put("later", 3)
                transaction.delete("original")
                with self.assertRaises(ValueError):
                    call(transaction)
                self.assertEqual(transaction.scan(), {"kept": 2, "later": 3})
                transaction.rollback_to("live")
                self.assertEqual(transaction.scan(), {"kept": 2, "original": 1})
                self.assertEqual(transaction.commit(), 1)
                self.assertEqual(engine.log_since(0)[0]["writes"], [["kept", 2]])

    def test_invalid_put_does_not_create_conflict_dependency(self):
        engine = Engine()
        transaction = engine.begin()
        with self.assertRaises(ValueError):
            transaction.put("job/a", True)
        with self.assertRaises(ValueError):
            transaction.scan(None)
        writer = engine.begin()
        writer.put("job/a", 1)
        writer.commit()
        self.assertEqual(transaction.commit(), 1)


class RecoveryContract(unittest.TestCase):
    def test_initial_validation(self):
        invalid = [[], (), 0, False, "data", {"": 1}, {None: 1}, {1: 2}]
        invalid.extend({"key": value} for value in (None, True, False, 1.0, "1", [], {}))
        for initial in invalid:
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                Engine(initial)
        self.assertEqual(Engine().version, 0)
        self.assertEqual(Engine(None).checkpoint(), {"version": 0, "data": {}})

    def test_log_version_validation(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.commit()
        before = engine.checkpoint(), engine.log_since(0)
        for version in (-1, 2, True, False, None, 0.0, "0", [], {}):
            with self.subTest(version=version), self.assertRaises(ValueError):
                engine.log_since(version)
        self.assertEqual((engine.checkpoint(), engine.log_since(0)), before)
        self.assertEqual(engine.log_since(1), [])

    def test_checkpoint_shape_and_data_validation(self):
        invalid = [
            None, [], {}, {"version": 0}, {"data": {}},
            {"version": 0, "data": {}, "extra": 1},
        ]
        invalid.extend({"version": v, "data": {}} for v in (-1, True, False, None, 0.0, "0", []))
        invalid.extend({"version": 0, "data": data} for data in (
            None, [], (), {"": 1}, {1: 2}, {"x": None}, {"x": True}, {"x": 1.5},
        ))
        for checkpoint in invalid:
            with self.subTest(checkpoint=checkpoint), self.assertRaises(ValueError):
                Engine.restore(checkpoint, [])

    def test_restore_rejects_malformed_logs_including_late_records(self):
        checkpoint = {"version": 4, "data": {"initial": 1}}
        bad_records = [
            None, [], {}, {"version": 6}, {"writes": [["a", 1]]},
            {"version": 6, "writes": [["a", 1]], "extra": 0},
        ]
        bad_records.extend({"version": v, "writes": [["a", 1]]}
                           for v in (5, 7, -1, True, None, 6.0, "6", []))
        bad_records.extend({"version": 6, "writes": writes} for writes in (
            None, {}, (), [], (("a", 1),), [("a", 1)], [None], [{}],
            [[]], [["a"]], [["a", 1, 2]], [["", 1]], [[None, 1]], [[[], 1]],
            [["a", True]], [["a", False]], [["a", 1.0]], [["a", "1"]], [["a", {}]],
            [["b", 1], ["a", 2]], [["a", 1], ["a", None]],
        ))
        for bad in bad_records:
            with self.subTest(record=bad):
                records = [{"version": 5, "writes": [["initial", None], ["valid", 2]]}, bad]
                original = copy.deepcopy((checkpoint, records))
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, records)
                self.assertEqual((checkpoint, records), original)
        for records in (None, {}, (), iter([])):
            with self.subTest(records=records), self.assertRaises(ValueError):
                Engine.restore(checkpoint, records)

    def test_restore_retains_log_and_continues_versions(self):
        checkpoint = {"version": 10 ** 80, "data": {"z": 1, "a": 2}}
        base = checkpoint["version"]
        records = [
            {"version": base + 1, "writes": [["absent", None], ["b", -10 ** 120]]},
            {"version": base + 2, "writes": [["a", None], ["z", 1]]},
        ]
        engine = Engine.restore(checkpoint, records)
        self.assertEqual(engine.checkpoint(), {
            "version": base + 2, "data": {"b": -10 ** 120, "z": 1},
        })
        self.assertEqual(engine.log_since(base), records)
        self.assertEqual(engine.log_since(base + 1), records[1:])
        self.assertEqual(engine.log_since(base + 2), [])
        with self.assertRaises(ValueError):
            engine.log_since(base - 1)
        reader = engine.begin()
        self.assertEqual(reader.scan(), {"b": -10 ** 120, "z": 1})
        self.assertEqual(reader.commit(), base + 2)
        transaction = engine.begin()
        transaction.delete("still-absent")
        self.assertEqual(transaction.commit(), base + 3)
        self.assertEqual(engine.log_since(base + 2), [{
            "version": base + 3, "writes": [["still-absent", None]],
        }])

    def test_restore_with_empty_log_has_checkpoint_as_retained_base(self):
        engine = Engine.restore({"version": 7, "data": {"a": 1}}, [])
        self.assertEqual(engine.version, 7)
        self.assertEqual(engine.log_since(7), [])
        with self.assertRaises(ValueError):
            engine.log_since(6)
        writer = engine.begin()
        writer.put("a", 1)
        self.assertEqual(writer.commit(), 8)
        self.assertEqual(engine.log_since(7), [{"version": 8, "writes": [["a", 1]]}])

    def test_restored_transaction_conflicts_only_with_later_history(self):
        engine = Engine.restore({"version": 3, "data": {}}, [
            {"version": 4, "writes": [["job/one", 1]]},
            {"version": 5, "writes": [["job/one", None]]},
        ])
        unaffected = engine.begin()
        unaffected.scan("job/")
        self.assertEqual(unaffected.commit(), 5)
        reader = engine.begin()
        reader.scan("job/")
        writer = engine.begin()
        writer.delete("job/absent")
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()

    def test_exports_and_restore_inputs_are_defensive_at_every_level(self):
        initial = {"z": 1, "a": 2}
        engine = Engine(initial)
        checkpoint = engine.checkpoint()
        transaction = engine.begin()
        transaction.put("b", 3)
        transaction.commit()
        records = engine.log_since(0)
        restored = Engine.restore(checkpoint, records)
        expected = engine.checkpoint()
        expected_log = copy.deepcopy(records)

        initial.clear()
        checkpoint["version"] = -1
        checkpoint["data"].clear()
        records[0]["version"] = 99
        records[0]["writes"][0][0] = "changed"
        records[0]["writes"][0][1] = 999
        records[0]["writes"].append(["extra", None])
        records.append({"version": 100, "writes": [["another", 1]]})
        for candidate in (engine, restored):
            self.assertEqual(candidate.checkpoint(), expected)
            self.assertEqual(candidate.log_since(0), expected_log)
            exported = candidate.checkpoint()
            exported["data"].clear()
            exported.clear()
            exported_log = candidate.log_since(0)
            exported_log[0]["writes"][0].clear()
            exported_log[0]["writes"].clear()
            exported_log[0].clear()
            exported_log.clear()
            self.assertEqual(candidate.checkpoint(), expected)
            self.assertEqual(candidate.log_since(0), expected_log)

    def test_deterministic_mixed_trace_and_checkpoint_log_round_trips(self):
        rng = random.Random(2048)
        engine = Engine({"anchor": 0})
        for step in range(40):
            checkpoint = engine.checkpoint()
            stale = engine.begin()
            stale.scan("jobs/")
            success = engine.begin()
            success.get("anchor")
            success.savepoint("start")
            success.put("discarded", 123)
            success.rollback_to("start")
            own_key = "own/" + str(step)
            own_value = rng.randrange(-(10 ** 50), 10 ** 50)
            success.put(own_key, own_value)
            success.release("start")

            key = "jobs/" + str(rng.randrange(5))
            writer = engine.begin()
            writer.put(key, rng.randrange(-100, 100))
            writer.commit()
            cleanup = engine.begin()
            cleanup.delete(key)
            cleanup.commit()
            before_conflict = engine.checkpoint(), engine.log_since(checkpoint["version"])
            with self.assertRaises(Conflict):
                stale.commit()
            self.assertEqual((engine.checkpoint(), engine.log_since(checkpoint["version"])), before_conflict)
            self.assertEqual(success.commit(), checkpoint["version"] + 3)
            self.assertEqual(engine.checkpoint()["data"][own_key], own_value)
            self.assertNotIn("discarded", engine.checkpoint()["data"])
            aborted = engine.begin()
            aborted.delete("anchor")
            aborted.abort()

            log = engine.log_since(checkpoint["version"])
            recovered = Engine.restore(checkpoint, log)
            self.assertEqual(recovered.checkpoint(), engine.checkpoint())
            self.assertEqual(recovered.log_since(checkpoint["version"]), log)
            self.assertEqual(recovered.begin().scan(), engine.checkpoint()["data"])
            engine = recovered


if __name__ == "__main__":
    unittest.main()
