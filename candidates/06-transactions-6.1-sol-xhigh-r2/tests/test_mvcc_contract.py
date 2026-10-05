import copy
import random
import unittest

from mvcc import Conflict, Engine


def write(engine, changes):
    transaction = engine.begin()
    for key, value in changes.items():
        if value is None:
            transaction.delete(key)
        else:
            transaction.put(key, value)
    return transaction.commit()


class ContractCase(unittest.TestCase):
    def assert_closed(self, transaction):
        calls = [
            lambda: transaction.get([]),
            lambda: transaction.scan(None),
            lambda: transaction.put(None, False),
            lambda: transaction.delete(""),
            lambda: transaction.savepoint(None),
            lambda: transaction.rollback_to([]),
            lambda: transaction.release(""),
            transaction.commit,
            transaction.abort,
        ]
        for call in calls:
            with self.assertRaises(RuntimeError) as caught:
                call()
            self.assertIs(type(caught.exception), RuntimeError)

    def assert_conflict_atomic(self, engine, transaction):
        before = engine.checkpoint()
        records = engine.log_since(before["version"])
        with self.assertRaises(Conflict):
            transaction.commit()
        self.assertEqual(engine.checkpoint(), before)
        self.assertEqual(engine.log_since(before["version"]), records)
        self.assert_closed(transaction)


class Validation(ContractCase):
    def test_initial_validation_and_large_values(self):
        invalid = [False, 0, [], (), "", {"": 1}, {1: 1},
                   {"x": True}, {"x": None}, {"x": 1.0}, {"x": "1"}]
        for initial in invalid:
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                Engine(initial)
        initial = {"z": -(10 ** 300), " ": 10 ** 300, "α": 0}
        engine = Engine(initial)
        initial["z"] = 1
        initial["new"] = 2
        self.assertEqual(engine.version, 0)
        self.assertEqual(engine.checkpoint()["data"]["z"], -(10 ** 300))
        self.assertEqual(list(engine.checkpoint()["data"]), [" ", "z", "α"])
        self.assertNotIn("new", engine.checkpoint()["data"])

    def test_invalid_operations_do_not_change_writes_or_savepoints(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        self.assertIsNone(transaction.put("x", 2))
        self.assertIsNone(transaction.savepoint("keep"))
        calls = [
            lambda: transaction.get(""),
            lambda: transaction.get([]),
            lambda: transaction.scan(False),
            lambda: transaction.put("x", True),
            lambda: transaction.put("x", None),
            lambda: transaction.put("x", 2.0),
            lambda: transaction.put("", 3),
            lambda: transaction.delete(None),
            lambda: transaction.savepoint("keep"),
            lambda: transaction.savepoint(""),
            lambda: transaction.rollback_to("missing"),
            lambda: transaction.rollback_to([]),
            lambda: transaction.release("missing"),
            lambda: transaction.release(None),
        ]
        for call in calls:
            with self.assertRaises(ValueError):
                call()
            self.assertEqual(engine.checkpoint(), {"version": 0, "data": {"x": 1}})
        transaction.put("x", 3)
        transaction.rollback_to("keep")
        self.assertEqual(transaction.get("x"), 2)
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.checkpoint()["data"], {"x": 2})

    def test_invalid_operations_do_not_add_dependencies(self):
        engine = Engine()
        transaction = engine.begin()
        for call in [lambda: transaction.put("x", False),
                     lambda: transaction.scan(None),
                     lambda: transaction.get(["x"])]:
            with self.assertRaises(ValueError):
                call()
        write(engine, {"x": 1})
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0), [{"version": 1, "writes": [["x", 1]]}])

    def test_log_version_validation_is_atomic(self):
        engine = Engine()
        write(engine, {"x": 1})
        before = engine.checkpoint()
        for version in [True, False, None, 0.0, "0", [], -1, 2]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                engine.log_since(version)
            self.assertEqual(engine.checkpoint(), before)
        self.assertEqual(engine.log_since(1), [])

    def test_closed_method_precedence_after_success_abort_and_conflict(self):
        engine = Engine()
        empty = engine.begin()
        self.assertEqual(empty.commit(), 0)
        self.assert_closed(empty)
        committed = engine.begin()
        committed.put("x", 1)
        committed.commit()
        self.assert_closed(committed)
        aborted = engine.begin()
        aborted.put("x", 2)
        self.assertIsNone(aborted.abort())
        self.assert_closed(aborted)
        stale = engine.begin()
        stale.get("x")
        write(engine, {"x": 2})
        self.assert_conflict_atomic(engine, stale)


class Isolation(ContractCase):
    def test_snapshot_and_disjoint_commits(self):
        engine = Engine({"x": 1})
        old = engine.begin()
        writer = engine.begin()
        writer.put("x", 2)
        writer.put("other", 3)
        writer.commit()
        self.assertEqual(old.get("x"), 1)
        self.assertIsNone(old.get("other"))
        self.assert_conflict_atomic(engine, old)
        a, b = engine.begin(), engine.begin()
        a.put("a", 4)
        b.put("b", 5)
        self.assertEqual(a.commit(), 2)
        self.assertEqual(b.commit(), 3)
        self.assertEqual(engine.checkpoint()["data"], {"a": 4, "b": 5, "other": 3, "x": 2})

    def test_scans_overlay_writes_and_return_sorted_copies(self):
        engine = Engine({"p/z": 3, "p/a": 1, "outside": 0})
        transaction = engine.begin()
        transaction.put("p/m", 2)
        transaction.delete("p/a")
        transaction.delete("p/absent")
        transaction.put("outside", 9)
        self.assertIsNone(transaction.get("p/a"))
        result = transaction.scan("p/")
        self.assertEqual(list(result), ["p/m", "p/z"])
        self.assertEqual(result, {"p/m": 2, "p/z": 3})
        result["p/m"] = 999
        result["p/new"] = 888
        self.assertEqual(transaction.scan(), {"outside": 9, "p/m": 2, "p/z": 3})
        self.assertEqual(engine.checkpoint()["data"], {"outside": 0, "p/a": 1, "p/z": 3})
        transaction.abort()
        self.assertEqual(engine.version, 0)
        self.assertEqual(engine.log_since(0), [])

    def test_point_reads_and_blind_writes_conflict(self):
        for observation in ["get", "put", "delete"]:
            for change in [1, 2, None]:
                with self.subTest(observation=observation, change=change):
                    engine = Engine({"x": 1})
                    transaction = engine.begin()
                    if observation == "get":
                        transaction.get("x")
                    elif observation == "put":
                        transaction.put("x", 99)
                    else:
                        transaction.delete("x")
                    write(engine, {"x": change})
                    self.assert_conflict_atomic(engine, transaction)

    def test_absent_key_read_conflicts_with_missing_key_deletion(self):
        engine = Engine()
        transaction = engine.begin()
        self.assertIsNone(transaction.get("missing"))
        self.assertEqual(write(engine, {"missing": None}), 1)
        self.assert_conflict_atomic(engine, transaction)

    def test_aba_and_insert_delete_history_are_not_lost(self):
        for initial in [{"x": 1}, {}]:
            with self.subTest(initial=initial):
                engine = Engine(initial)
                transaction = engine.begin()
                transaction.get("x")
                write(engine, {"x": 2})
                write(engine, {"x": initial.get("x")})
                self.assertEqual(engine.checkpoint()["data"], initial)
                self.assert_conflict_atomic(engine, transaction)

    def test_empty_scan_detects_insert_delete_and_tombstone_phantoms(self):
        for prefix, key in [("job/", "job/1"), ("", "anything"), ("α", "αβ")]:
            for changes in [[1, None], [None]]:
                with self.subTest(prefix=prefix, changes=changes):
                    engine = Engine()
                    transaction = engine.begin()
                    self.assertEqual(transaction.scan(prefix), {})
                    for value in changes:
                        write(engine, {key: value})
                    self.assertEqual(engine.checkpoint()["data"], {})
                    self.assert_conflict_atomic(engine, transaction)

    def test_read_only_validation_allows_unrelated_writes(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        transaction.get("x")
        transaction.scan("job/")
        write(engine, {"jobs/1": 2, "y": 3})
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(len(engine.log_since(0)), 1)
        unconstrained = engine.begin()
        write(engine, {"x": 8})
        self.assertEqual(unconstrained.commit(), 2)

    def test_writes_are_events_and_only_the_last_value_is_logged(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        transaction.put("z", 9)
        transaction.delete("z")
        transaction.delete("a")
        transaction.put("x", 2)
        transaction.put("x", 1)
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["a", None], ["x", 1], ["z", None]]}
        ])
        self.assertEqual(write(engine, {"x": 1}), 2)
        self.assertEqual(write(engine, {"absent": None}), 3)
        self.assertEqual(engine.checkpoint()["data"], {"x": 1})


class Savepoints(ContractCase):
    def test_rollback_keeps_target_discards_newer_and_copies_saved_writes(self):
        engine = Engine({"deleted": 5})
        transaction = engine.begin()
        transaction.put("x", 1)
        transaction.delete("deleted")
        transaction.savepoint("root")
        transaction.put("x", 2)
        transaction.savepoint("middle")
        transaction.delete("x")
        transaction.savepoint("leaf")
        transaction.put("new", 3)
        self.assertIsNone(transaction.rollback_to("middle"))
        self.assertEqual(transaction.get("x"), 2)
        self.assertIsNone(transaction.get("new"))
        with self.assertRaises(ValueError):
            transaction.release("leaf")
        with self.assertRaises(ValueError):
            transaction.savepoint("middle")
        transaction.put("x", 999)
        transaction.rollback_to("middle")
        self.assertEqual(transaction.get("x"), 2)
        transaction.rollback_to("root")
        transaction.savepoint("middle")
        transaction.savepoint("leaf")
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["deleted", None], ["x", 1]]}
        ])

    def test_release_discards_target_and_newer_but_keeps_writes(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("root")
        transaction.put("x", 1)
        transaction.savepoint("middle")
        transaction.put("x", 2)
        transaction.savepoint("leaf")
        transaction.put("y", 3)
        self.assertIsNone(transaction.release("middle"))
        for name in ["middle", "leaf"]:
            with self.assertRaises(ValueError):
                transaction.rollback_to(name)
            transaction.savepoint(name)
        self.assertEqual(transaction.get("x"), 2)
        transaction.rollback_to("root")
        self.assertEqual(transaction.scan(), {})
        self.assertEqual(transaction.commit(), 0)

    def test_rollback_and_release_keep_key_and_range_reads(self):
        for action in ["rollback_to", "release"]:
            for read in ["point", "range", "own_write"]:
                with self.subTest(action=action, read=read):
                    engine = Engine()
                    transaction = engine.begin()
                    transaction.savepoint("s")
                    if read == "point":
                        transaction.get("job/1")
                    elif read == "range":
                        transaction.scan("job/")
                    else:
                        transaction.put("job/1", 1)
                        self.assertEqual(transaction.get("job/1"), 1)
                    getattr(transaction, action)("s")
                    write(engine, {"job/1": 2})
                    self.assert_conflict_atomic(engine, transaction)

    def test_rolled_back_blind_writes_are_no_longer_pending(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint("s")
        transaction.put("x", 1)
        transaction.delete("y")
        transaction.rollback_to("s")
        write(engine, {"x": 2, "y": 3})
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.checkpoint()["data"], {"x": 2, "y": 3})


class Recovery(ContractCase):
    def test_checkpoint_and_log_exports_do_not_alias_engine_or_transactions(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        transaction.put("pending", 7)
        checkpoint = engine.checkpoint()
        self.assertEqual(checkpoint, {"version": 0, "data": {"x": 1}})
        checkpoint["data"]["x"] = 999
        checkpoint["version"] = 99
        write(engine, {"y": 2, "gone": None})
        records = engine.log_since(0)
        records[0]["writes"][0][0] = "corrupted"
        records[0]["writes"][1][1] = 999
        records[0]["writes"].append(["new", 1])
        records[0]["version"] = 99
        records.append({})
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["gone", None], ["y", 2]]}
        ])
        self.assertEqual(transaction.get("x"), 1)
        self.assertEqual(transaction.commit(), 2)
        self.assertEqual(engine.checkpoint()["data"], {"pending": 7, "x": 1, "y": 2})

    def test_replay_tombstones_retention_and_new_transactions(self):
        checkpoint = {"version": 50, "data": {"z": -7, "a": 1}}
        records = [
            {"version": 51, "writes": [["a", None], ["missing", None], ["z", -7]]},
            {"version": 52, "writes": [["a", 10 ** 200], ["z", None]]},
        ]
        engine = Engine.restore(checkpoint, records)
        self.assertEqual(engine.checkpoint(), {"version": 52, "data": {"a": 10 ** 200}})
        self.assertEqual(engine.log_since(50), records)
        self.assertEqual(engine.log_since(51), records[1:])
        self.assertEqual(engine.log_since(52), [])
        with self.assertRaises(ValueError):
            engine.log_since(49)
        checkpoint["data"]["a"] = 999
        records[0]["writes"][0][0] = "bad"
        records[1]["writes"][0][1] = 999
        transaction = engine.begin()
        transaction.scan()
        transaction.put("a", -1)
        self.assertEqual(transaction.commit(), 53)
        self.assertEqual(engine.checkpoint(), {"version": 53, "data": {"a": -1}})
        self.assertEqual(engine.log_since(52), [{"version": 53, "writes": [["a", -1]]}])
        self.assertEqual(engine.log_since(50)[1]["writes"][0], ["a", 10 ** 200])

    def test_empty_recovery_and_arbitrarily_large_base_version(self):
        base = 10 ** 100
        engine = Engine.restore({"version": base, "data": {}}, [])
        self.assertEqual(engine.begin().commit(), base)
        self.assertEqual(engine.log_since(base), [])
        self.assertEqual(write(engine, {"x": -1}), base + 1)
        self.assertEqual(engine.log_since(base), [{"version": base + 1, "writes": [["x", -1]]}])

    def test_malformed_checkpoints_are_rejected_without_mutating_inputs(self):
        invalid = [None, [], {}, {"version": 0}, {"data": {}},
                   {"version": 0, "data": {}, "extra": 1}]
        invalid += [{"version": version, "data": {}} for version in [-1, True, False, 0.0, "0", None]]
        invalid += [{"version": 0, "data": data} for data in [None, [], {"": 1}, {1: 2}, {"x": None}, {"x": False}]]
        for checkpoint in invalid:
            before = copy.deepcopy(checkpoint)
            with self.subTest(checkpoint=checkpoint), self.assertRaises(ValueError):
                Engine.restore(checkpoint, [])
            self.assertEqual(checkpoint, before)

    def test_malformed_logs_are_rejected_including_late_failures(self):
        checkpoint = {"version": 0, "data": {"x": 1}}
        invalid = [None, (), {}, [None], [{}],
                   [{"version": 1}], [{"writes": [["x", 1]]}],
                   [{"version": 1, "writes": [["x", 1]], "extra": 0}]]
        invalid += [[{"version": version, "writes": [["x", 2]]}]
                    for version in [True, 1.0, "1", None, -1, 0, 2]]
        bad_writes = [None, (), {}, [], [("x", 1)], [[]], [["x"]], [["x", 1, 2]],
                      [{"x": 1}], [["", 1]], [[None, 1]], [[1, 1]], [["x", True]],
                      [["x", 1.0]], [["x", "1"]], [["x", []]],
                      [["x", 1], ["x", 2]], [["z", 1], ["a", 2]]]
        invalid += [[{"version": 1, "writes": writes}] for writes in bad_writes]
        invalid += [[{"version": 1, "writes": [["x", 2]]},
                     {"version": version, "writes": [["y", 3]]}]
                    for version in [1, 3, True]]
        invalid.append([{"version": 1, "writes": [["x", 2]]},
                        {"version": 2, "writes": [["y", False]]}])
        for records in invalid:
            before = copy.deepcopy(records)
            with self.subTest(records=records), self.assertRaises(ValueError):
                Engine.restore(checkpoint, records)
            self.assertEqual(records, before)
            self.assertEqual(checkpoint, {"version": 0, "data": {"x": 1}})

    def test_checkpoint_at_any_commit_reconstructs_final_state(self):
        engine = Engine({"start": 1})
        checkpoints = [engine.checkpoint()]
        for changes in [{"a": 2}, {"a": 2, "start": None},
                        {"gone": None, "z": -(10 ** 100)}, {"a": None}]:
            write(engine, changes)
            checkpoints.append(engine.checkpoint())
        for checkpoint in checkpoints:
            version = checkpoint["version"]
            restored = Engine.restore(checkpoint, engine.log_since(version))
            self.assertEqual(restored.checkpoint(), engine.checkpoint())
            self.assertEqual(restored.log_since(version), engine.log_since(version))
            self.assertEqual(restored.begin().scan(), engine.checkpoint()["data"])


class MixedTrace(ContractCase):
    def test_deterministic_interleaving_with_periodic_recovery(self):
        rng = random.Random(731)
        engine = Engine({"anchor": 7})
        checkpoint = engine.checkpoint()
        expected_data = {"anchor": 7}
        expected_records = []

        def expect_record(changes):
            expected_records.append({
                "version": len(expected_records) + 1,
                "writes": [[key, changes[key]] for key in sorted(changes)],
            })
            for key, value in changes.items():
                if value is None:
                    expected_data.pop(key, None)
                else:
                    expected_data[key] = value

        for index in range(72):
            prefix = f"batch/{index}/"
            key = prefix + "x"
            value = rng.randrange(-(10 ** 80), 10 ** 80)
            stale = engine.begin()
            writer = engine.begin()
            scenario = index % 6
            if scenario == 0:
                stale.get("anchor")
                stale.savepoint("s")
                stale.put(prefix + "discard", value)
                stale.rollback_to("s")
            elif scenario == 1:
                self.assertEqual(stale.scan(prefix), {})
            elif scenario == 2:
                self.assertIsNone(stale.get(key))
                value = None
            elif scenario == 3:
                stale.put(prefix + "kept", -value)
                stale.savepoint("s")
                stale.put(key, value + 1)
                stale.rollback_to("s")
            elif scenario == 4:
                stale.get("anchor")
                key, value = "anchor", 7
            else:
                stale.savepoint("s")
                stale.put(key, value + 1)
                self.assertEqual(stale.get(key), value + 1)
                stale.rollback_to("s")
            if value is None:
                writer.delete(key)
            else:
                writer.put(key, value)
            self.assertEqual(writer.commit(), len(expected_records) + 1)
            expect_record({key: value})
            if scenario in [1, 2, 4, 5]:
                self.assert_conflict_atomic(engine, stale)
            elif scenario == 3:
                self.assertEqual(stale.commit(), len(expected_records) + 1)
                expect_record({prefix + "kept": -value})
            else:
                self.assertEqual(stale.commit(), len(expected_records))
            self.assertEqual(engine.version, len(expected_records))
            self.assertEqual(engine.checkpoint()["data"], expected_data)
            self.assertEqual(engine.log_since(checkpoint["version"]),
                             expected_records[checkpoint["version"]:])
            if index % 9 == 8:
                engine = Engine.restore(checkpoint, engine.log_since(checkpoint["version"]))
                self.assertEqual(engine.checkpoint()["data"], expected_data)
                checkpoint = engine.checkpoint()


if __name__ == "__main__":
    unittest.main()
