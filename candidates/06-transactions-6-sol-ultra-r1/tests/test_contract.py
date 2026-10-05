import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_snapshot_and_sorted_own_view(self):
        initial = {"z": 3, "a": 1}
        engine = Engine(initial)
        initial["a"] = 99
        reader = engine.begin()
        writer = engine.begin()
        writer.put("b", 2)
        self.assertEqual(writer.commit(), 1)

        self.assertEqual(reader.get("a"), 1)
        self.assertIsNone(reader.get("b"))
        reader.put("c", -10**100)
        reader.delete("z")
        view = reader.scan()
        self.assertEqual(view, {"a": 1, "c": -10**100})
        self.assertEqual(list(view), ["a", "c"])
        view["a"] = 100
        self.assertEqual(reader.get("a"), 1)
        self.assertEqual(engine.checkpoint(),
                         {"version": 1, "data": {"a": 1, "b": 2, "z": 3}})

    def test_history_conflicts_with_read_only_and_aba(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 1)
        for value in (2, 1):
            writer = engine.begin()
            writer.put("x", value)
            writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.abort()
        self.assertEqual(engine.version, 2)

    def test_empty_range_conflicts_with_insert_then_delete(self):
        engine = Engine()
        watcher = engine.begin()
        self.assertEqual(watcher.scan("job/"), {})
        for operation in ("put", "delete"):
            writer = engine.begin()
            if operation == "put":
                writer.put("job/1", 1)
            else:
                writer.delete("job/1")
            writer.commit()
        with self.assertRaises(Conflict):
            watcher.commit()
        self.assertEqual(engine.checkpoint(), {"version": 2, "data": {}})

    def test_unrelated_writes_and_write_free_commit(self):
        engine = Engine()
        reader = engine.begin()
        reader.scan("a/")
        writer = engine.begin()
        writer.put("b/1", 1)
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(reader.commit(), 1)
        self.assertEqual(len(engine.log_since(0)), 1)

    def test_savepoint_stack_and_preserved_reads(self):
        engine = Engine({"watch": 1})
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("first")
        transaction.put("a", 2)
        transaction.savepoint("second")
        transaction.get("watch")
        transaction.scan("empty/")
        transaction.rollback_to("first")
        self.assertEqual(transaction.get("a"), 1)
        with self.assertRaises(ValueError):
            transaction.release("second")
        with self.assertRaises(ValueError):
            transaction.savepoint("first")
        transaction.savepoint("second")
        transaction.release("first")
        transaction.savepoint("first")

        writer = engine.begin()
        writer.put("watch", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            transaction.commit()
        self.assertEqual(engine.checkpoint(),
                         {"version": 1, "data": {"watch": 1}})

    def test_invalid_arguments_do_not_change_state(self):
        for initial in ({"": 1}, {"x": True}, {"x": None}, []):
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                Engine(initial)
        engine = Engine()
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("s")
        for call in (
            lambda: transaction.get(""),
            lambda: transaction.scan(None),
            lambda: transaction.put("a", True),
            lambda: transaction.put("a", None),
            lambda: transaction.delete(3),
            lambda: transaction.savepoint("s"),
            lambda: transaction.rollback_to("missing"),
            lambda: transaction.release("missing"),
        ):
            with self.assertRaises(ValueError):
                call()
        transaction.rollback_to("s")
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.log_since(0),
                         [{"version": 1, "writes": [["a", 1]]}])

    def test_closed_methods_check_before_arguments(self):
        transaction = Engine().begin()
        transaction.abort()
        for call in (
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
                call()

    def test_log_recovery_and_defensive_copies(self):
        engine = Engine({"z": 9})
        checkpoint = engine.checkpoint()
        transaction = engine.begin()
        transaction.delete("missing")
        transaction.put("a", -2**200)
        transaction.commit()
        transaction = engine.begin()
        transaction.delete("z")
        transaction.commit()
        records = engine.log_since(checkpoint["version"])
        self.assertEqual(records, [
            {"version": 1, "writes": [["a", -2**200], ["missing", None]]},
            {"version": 2, "writes": [["z", None]]},
        ])
        recovered = Engine.restore(checkpoint, records)
        self.assertEqual(recovered.checkpoint(), engine.checkpoint())
        self.assertEqual(recovered.log_since(0), records)

        checkpoint["data"]["z"] = 100
        records[0]["writes"][0][1] = 100
        exported = engine.log_since(0)
        exported[0]["writes"][0][1] = 100
        self.assertEqual(recovered.checkpoint()["data"], {"a": -2**200})
        self.assertEqual(engine.log_since(0)[0]["writes"][0][1], -2**200)

        later = recovered.begin()
        later.put("next", 3)
        self.assertEqual(later.commit(), 3)
        self.assertEqual([record["version"] for record in recovered.log_since(1)],
                         [2, 3])

    def test_restore_validates_complete_shape_and_base(self):
        checkpoint = {"version": 7, "data": {"x": 1}}
        invalid_checkpoints = [
            None,
            {"version": 7},
            {"version": 7, "data": {}, "extra": 1},
            {"version": True, "data": {}},
            {"version": -1, "data": {}},
            {"version": 7, "data": {"x": False}},
        ]
        for candidate in invalid_checkpoints:
            with self.subTest(checkpoint=candidate), self.assertRaises(ValueError):
                Engine.restore(candidate, [])

        invalid_records = [
            None,
            [{"version": 9, "writes": [["x", 2]]}],
            [{"version": True, "writes": [["x", 2]]}],
            [{"version": 8, "writes": []}],
            [{"version": 8, "writes": [("x", 2)]}],
            [{"version": 8, "writes": [["b", 2], ["a", 1]]}],
            [{"version": 8, "writes": [["a", 2], ["a", 1]]}],
            [{"version": 8, "writes": [["x", True]]}],
            [{"version": 8, "writes": [["x", 2]], "extra": 0}],
            [{"version": 8, "writes": [["x", 2]]},
             {"version": 10, "writes": [["y", 3]]}],
        ]
        for candidate in invalid_records:
            with self.subTest(records=candidate), self.assertRaises(ValueError):
                Engine.restore(checkpoint, candidate)

        recovered = Engine.restore(checkpoint, [
            {"version": 8, "writes": [["x", None]]}
        ])
        self.assertEqual(recovered.checkpoint(), {"version": 8, "data": {}})
        with self.assertRaises(ValueError):
            recovered.log_since(6)
        for invalid_version in (True, -1, 9, 1.0):
            with self.assertRaises(ValueError):
                recovered.log_since(invalid_version)
        self.assertEqual(recovered.log_since(7),
                         [{"version": 8, "writes": [["x", None]]}])


if __name__ == "__main__":
    unittest.main()
