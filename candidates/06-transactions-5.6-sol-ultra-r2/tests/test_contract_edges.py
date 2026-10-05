import unittest

from mvcc import Conflict, Engine


class ContractEdges(unittest.TestCase):
    def test_empty_range_read_conflicts_with_insert_then_delete(self):
        engine = Engine()
        observer = engine.begin()
        self.assertEqual(observer.scan("job/"), {})

        insert = engine.begin()
        insert.put("job/1", 10)
        insert.commit()
        remove = engine.begin()
        remove.delete("job/1")
        remove.commit()

        with self.assertRaises(Conflict):
            observer.commit()
        self.assertEqual(engine.checkpoint()["data"], {})

    def test_write_free_commit_validates_point_reads(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 1)
        writer = engine.begin()
        writer.put("x", 1)  # Same-value writes are still writes.
        writer.commit()

        with self.assertRaises(Conflict):
            reader.commit()
        with self.assertRaises(RuntimeError):
            reader.abort()

    def test_unrelated_blind_writes_merge(self):
        engine = Engine()
        first = engine.begin()
        second = engine.begin()
        first.put("a", 1)
        second.put("b", 2)
        self.assertEqual(first.commit(), 1)
        self.assertEqual(second.commit(), 2)
        self.assertEqual(engine.checkpoint()["data"], {"a": 1, "b": 2})

    def test_reads_survive_savepoint_rollback(self):
        engine = Engine({"watched": 1})
        transaction = engine.begin()
        transaction.savepoint("before-read")
        transaction.get("watched")
        transaction.put("temporary", 1)
        transaction.rollback_to("before-read")

        writer = engine.begin()
        writer.put("watched", 2)
        writer.commit()
        with self.assertRaises(Conflict):
            transaction.commit()

    def test_log_exports_and_restore_inputs_are_detached(self):
        engine = Engine({"a": 1})
        transaction = engine.begin()
        transaction.put("b", 2)
        transaction.delete("a")
        transaction.commit()

        exported = engine.log_since(0)
        self.assertEqual(exported[0]["writes"], [["a", None], ["b", 2]])
        exported[0]["writes"][0][0] = "changed"
        self.assertEqual(
            engine.log_since(0)[0]["writes"], [["a", None], ["b", 2]]
        )

        checkpoint = {"version": 4, "data": {"base": 3}}
        records = [{"version": 5, "writes": [["base", None], ["z", -9]]}]
        restored = Engine.restore(checkpoint, records)
        checkpoint["data"]["base"] = 99
        records[0]["writes"][1][1] = 100
        self.assertEqual(restored.checkpoint(), {"version": 5, "data": {"z": -9}})
        with self.assertRaises(ValueError):
            restored.log_since(3)

    def test_checkpoint_log_round_trip_from_nonzero_version(self):
        engine = Engine({"keep": 4, "remove": 8})
        first = engine.begin()
        first.put("early", 1)
        first.commit()
        checkpoint = engine.checkpoint()

        second = engine.begin()
        second.put("z", 9)
        second.put("a", -2)
        second.commit()
        third = engine.begin()
        third.delete("remove")
        third.delete("missing")
        third.commit()

        records = engine.log_since(checkpoint["version"])
        restored = Engine.restore(checkpoint, records)
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        self.assertEqual(restored.log_since(checkpoint["version"]), records)
        self.assertEqual(restored.log_since(restored.version), [])
        with self.assertRaises(ValueError):
            restored.log_since(checkpoint["version"] - 1)

    def test_invalid_operations_do_not_replace_staged_writes(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("x", 1)
        with self.assertRaises(ValueError):
            transaction.put("x", True)
        with self.assertRaises(ValueError):
            transaction.rollback_to("missing")
        transaction.commit()
        self.assertEqual(engine.checkpoint()["data"], {"x": 1})

    def test_restore_rejects_noncanonical_records(self):
        checkpoint = {"version": 0, "data": {}}
        invalid_records = [
            [{"version": 2, "writes": [["a", 1]]}],
            [{"version": 1, "writes": []}],
            [{"version": 1, "writes": [("a", 1)]}],
            [{"version": 1, "writes": [["b", 1], ["a", 2]]}],
            [{"version": 1, "writes": [["a", True]]}],
        ]
        for records in invalid_records:
            with self.subTest(records=records):
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, records)


if __name__ == "__main__":
    unittest.main()
