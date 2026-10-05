import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_snapshot_writes_versions_and_defensive_exports(self):
        initial = {"z": 1, "a": 2}
        engine = Engine(initial)
        initial["a"] = 99
        reader = engine.begin()
        writer = engine.begin()
        writer.put("a", 2)
        writer.delete("missing")
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(reader.get("a"), 2)
        self.assertEqual(list(reader.scan()), ["a", "z"])
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["a", 2], ["missing", None]]}
        ])
        with self.assertRaises(Conflict):
            reader.commit()

        checkpoint = engine.checkpoint()
        self.assertEqual(list(checkpoint["data"]), ["a", "z"])
        checkpoint["data"]["a"] = -1
        exported_log = engine.log_since(0)
        exported_log[0]["writes"][0][1] = -1
        self.assertEqual(engine.checkpoint()["data"]["a"], 2)
        self.assertEqual(engine.log_since(0)[0]["writes"][0][1], 2)

    def test_empty_range_scan_conflicts_after_insert_and_delete(self):
        engine = Engine()
        reader = engine.begin()
        self.assertEqual(reader.scan("job/"), {})
        writer = engine.begin()
        writer.put("job/1", 4)
        writer.commit()
        remover = engine.begin()
        remover.delete("job/1")
        remover.commit()
        self.assertEqual(reader.scan("job/"), {})
        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.abort()

    def test_savepoints_preserve_reads_and_restore_writes(self):
        engine = Engine({"x": 1})
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("first")
        transaction.put("a", 2)
        transaction.savepoint("second")
        self.assertEqual(transaction.get("x"), 1)
        transaction.put("b", 3)
        transaction.rollback_to("first")
        self.assertEqual(transaction.scan("a"), {"a": 1})
        with self.assertRaises(ValueError):
            transaction.rollback_to("second")
        with self.assertRaises(ValueError):
            transaction.savepoint("first")
        transaction.release("first")
        transaction.savepoint("first")
        other = engine.begin()
        other.put("x", 1)
        other.commit()
        with self.assertRaises(Conflict):
            transaction.commit()
        self.assertEqual(engine.checkpoint()["data"], {"x": 1})

    def test_invalid_operations_are_atomic_and_closed_precedes_validation(self):
        engine = Engine({"a": 1})
        transaction = engine.begin()
        transaction.put("b", 2)
        for operation in (
            lambda: transaction.put("c", True),
            lambda: transaction.put("", 3),
            lambda: transaction.delete(1),
            lambda: transaction.get(None),
            lambda: transaction.scan(None),
            lambda: transaction.savepoint(""),
            lambda: transaction.release("missing"),
            lambda: transaction.rollback_to("missing"),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(transaction.scan(), {"a": 1, "b": 2})
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["b", 2]]}
        ])
        for operation in (
            lambda: transaction.get(None),
            lambda: transaction.scan(None),
            lambda: transaction.put(None, None),
            lambda: transaction.delete(None),
            lambda: transaction.savepoint(None),
            lambda: transaction.rollback_to(None),
            lambda: transaction.release(None),
            transaction.commit,
            transaction.abort,
        ):
            with self.assertRaises(RuntimeError):
                operation()

    def test_restore_round_trip_and_retained_base(self):
        original = Engine({"a": 1})
        first = original.begin()
        first.put("b", 2)
        first.commit()
        checkpoint = original.checkpoint()
        second = original.begin()
        second.delete("a")
        second.commit()
        third = original.begin()
        third.put("b", 3)
        third.commit()
        records = original.log_since(checkpoint["version"])
        restored = Engine.restore(checkpoint, records)
        self.assertEqual(restored.checkpoint(), original.checkpoint())
        self.assertEqual(restored.log_since(1), records)
        with self.assertRaises(ValueError):
            restored.log_since(0)
        checkpoint["data"]["a"] = 99
        records[0]["writes"][0][1] = 99
        self.assertEqual(restored.checkpoint()["data"], {"b": 3})
        self.assertEqual(restored.log_since(1)[0]["writes"], [["a", None]])
        next_transaction = restored.begin()
        next_transaction.put("c", 4)
        self.assertEqual(next_transaction.commit(), 4)
        self.assertEqual(restored.log_since(3), [
            {"version": 4, "writes": [["c", 4]]}
        ])

    def test_restore_rejects_malformed_records(self):
        checkpoint = {"version": 5, "data": {"a": 1}}
        invalid_records = (
            [{"version": 7, "writes": [["b", 2]]}],
            [{"version": True, "writes": [["b", 2]]}],
            [{"version": 6, "writes": []}],
            [{"version": 6, "writes": [["b", 2], ["a", 3]]}],
            [{"version": 6, "writes": [["a", 2], ["a", 3]]}],
            [{"version": 6, "writes": [("b", 2)]}],
            [{"version": 6, "writes": [["b", False]]}],
            [{"version": 6, "writes": [["b", 2]], "extra": 1}],
            [{"version": 6, "writes": [["b", 2]]},
             {"version": 8, "writes": [["c", 3]]}],
        )
        for records in invalid_records:
            with self.subTest(records=records), self.assertRaises(ValueError):
                Engine.restore(checkpoint, records)
        with self.assertRaises(ValueError):
            Engine.restore({"version": 0, "data": {"a": None}}, [])
        with self.assertRaises(ValueError):
            Engine.restore({"version": 0, "data": {}, "extra": 1}, [])

    def test_read_only_commit_and_integer_subclass_copy(self):
        class TaggedInt(int):
            pass

        class TaggedStr(str):
            pass

        source_key = TaggedStr("key")
        source_value = TaggedInt(10)
        engine = Engine({source_key: source_value})
        self.assertIs(type(next(iter(engine.checkpoint()["data"]))), str)
        self.assertIs(type(engine.checkpoint()["data"]["key"]), int)
        reader = engine.begin()
        self.assertEqual(reader.get("key"), 10)
        unrelated = engine.begin()
        unrelated.put("elsewhere", 20)
        unrelated.commit()
        self.assertEqual(reader.commit(), 1)
        self.assertEqual(engine.version, 1)
        with self.assertRaises(ValueError):
            engine.log_since(True)


if __name__ == "__main__":
    unittest.main()
