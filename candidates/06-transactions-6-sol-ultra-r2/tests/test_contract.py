import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_snapshot_own_writes_and_sorted_scan(self):
        initial = {"p/z": 9, "p/a": -1}
        engine = Engine(initial)
        initial["p/a"] = 100
        reader = engine.begin()
        snapshot_reader = engine.begin()
        writer = engine.begin()
        writer.put("q/b", 10**100)
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(snapshot_reader.get("q/b"), None)
        snapshot_reader.abort()
        self.assertEqual(reader.scan("p/"), {"p/a": -1, "p/z": 9})
        reader.put("p/c", 3)
        reader.delete("p/z")
        self.assertEqual(reader.scan("p/"), {"p/a": -1, "p/c": 3})
        self.assertEqual(reader.commit(), 2)
        self.assertEqual(engine.checkpoint(), {
            "version": 2, "data": {"p/a": -1, "p/c": 3, "q/b": 10**100}
        })
        self.assertEqual(engine.log_since(1), [
            {"version": 2, "writes": [["p/c", 3], ["p/z", None]]}
        ])

    def test_history_conflicts_even_when_state_returns_to_original(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 1)
        first = engine.begin()
        first.put("x", 2)
        first.commit()
        second = engine.begin()
        second.put("x", 1)
        second.commit()
        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.abort()
        self.assertEqual(engine.version, 2)

    def test_empty_range_read_conflicts_with_insert_then_delete(self):
        engine = Engine()
        reader = engine.begin()
        self.assertEqual(reader.scan("job/"), {})
        creator = engine.begin()
        creator.put("job/1", 5)
        creator.commit()
        remover = engine.begin()
        remover.delete("job/1")
        remover.commit()
        self.assertEqual(engine.checkpoint()["data"], {})
        with self.assertRaises(Conflict):
            reader.commit()

    def test_reads_survive_rollback_and_release(self):
        engine = Engine({"tracked": 1})
        tx = engine.begin()
        tx.put("a", 1)
        tx.savepoint("first")
        tx.put("a", 2)
        tx.savepoint("second")
        tx.get("tracked")
        tx.scan("new/")
        tx.rollback_to("first")
        tx.savepoint("second")
        tx.put("b", 3)
        tx.release("first")
        self.assertEqual(tx.scan("a"), {"a": 1})
        with self.assertRaises(ValueError):
            tx.rollback_to("second")
        writer = engine.begin()
        writer.put("new/item", 8)
        writer.commit()
        with self.assertRaises(Conflict):
            tx.commit()
        self.assertEqual(engine.checkpoint()["data"], {"new/item": 8, "tracked": 1})

    def test_noop_writes_are_versions_and_read_only_commit_is_not(self):
        engine = Engine({"x": 1})
        tx = engine.begin()
        tx.put("x", 1)
        tx.delete("missing")
        self.assertEqual(tx.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["missing", None], ["x", 1]]}
        ])
        read_only = engine.begin()
        read_only.get("x")
        unrelated = engine.begin()
        unrelated.put("y", 2)
        unrelated.commit()
        self.assertEqual(read_only.commit(), 2)
        self.assertEqual(engine.version, 2)

    def test_restore_retains_base_and_copies_exports(self):
        engine = Engine()
        tx = engine.begin()
        tx.put("a", 1)
        tx.commit()
        checkpoint = engine.checkpoint()
        tx = engine.begin()
        tx.put("b", 2)
        tx.commit()
        records = engine.log_since(1)
        restored = Engine.restore(checkpoint, records)
        checkpoint["data"]["a"] = 100
        records[0]["writes"][0][1] = 100
        self.assertEqual(restored.checkpoint(), {
            "version": 2, "data": {"a": 1, "b": 2}
        })
        exported = restored.log_since(1)
        exported[0]["writes"][0][1] = 200
        self.assertEqual(restored.log_since(1), [
            {"version": 2, "writes": [["b", 2]]}
        ])
        with self.assertRaises(ValueError):
            restored.log_since(0)
        tx = restored.begin()
        tx.delete("a")
        self.assertEqual(tx.commit(), 3)
        self.assertEqual([r["version"] for r in restored.log_since(1)], [2, 3])

    def test_invalid_inputs_do_not_stage_writes(self):
        for initial in ({"": 1}, {"x": True}, {1: 2}, []):
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                Engine(initial)
        engine = Engine()
        tx = engine.begin()
        for call in (
            lambda: tx.put("x", True),
            lambda: tx.put("", 1),
            lambda: tx.delete(None),
            lambda: tx.get(""),
            lambda: tx.scan(None),
            lambda: tx.savepoint(""),
            lambda: tx.rollback_to("missing"),
            lambda: tx.release("missing"),
        ):
            with self.subTest(call=call), self.assertRaises(ValueError):
                call()
        tx.put("x", -3)
        tx.savepoint("s")
        with self.assertRaises(ValueError):
            tx.savepoint("s")
        self.assertEqual(tx.commit(), 1)
        self.assertEqual(engine.log_since(0), [
            {"version": 1, "writes": [["x", -3]]}
        ])

    def test_closed_methods_check_closed_state_first(self):
        tx = Engine().begin()
        tx.abort()
        for call in (
            lambda: tx.get(None),
            lambda: tx.scan(None),
            lambda: tx.put("", True),
            lambda: tx.delete(None),
            lambda: tx.savepoint(None),
            lambda: tx.rollback_to(None),
            lambda: tx.release(None),
            tx.commit,
            tx.abort,
        ):
            with self.subTest(call=call), self.assertRaises(RuntimeError):
                call()

    def test_restore_rejects_malformed_checkpoint_and_log(self):
        malformed = [
            ({"version": True, "data": {}}, []),
            ({"version": -1, "data": {}}, []),
            ({"version": 0, "data": {}, "extra": 1}, []),
            ({"version": 0, "data": {"x": None}}, []),
            ({"version": 0, "data": {}}, ()),
            ({"version": 0, "data": {}}, [{"version": 2, "writes": [["x", 1]]}]),
            ({"version": 0, "data": {}}, [{"version": 1, "writes": []}]),
            ({"version": 0, "data": {}}, [{"version": 1, "writes": [("x", 1)]}]),
            ({"version": 0, "data": {}}, [{"version": 1, "writes": [["b", 1], ["a", 2]]}]),
            ({"version": 0, "data": {}}, [{"version": 1, "writes": [["a", 1], ["a", 2]]}]),
            ({"version": 0, "data": {}}, [{"version": 1, "writes": [["a", True]]}]),
            ({"version": 0, "data": {}}, [{"version": 1, "writes": [["a", 1]], "extra": 0}]),
        ]
        for checkpoint, records in malformed:
            with self.subTest(checkpoint=checkpoint, records=records):
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, records)


if __name__ == "__main__":
    unittest.main()
