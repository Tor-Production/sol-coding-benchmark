import unittest

from mvcc import Conflict, Engine


class ContractEdges(unittest.TestCase):
    def test_empty_scan_detects_insert_then_delete(self):
        engine = Engine()
        observer = engine.begin()
        self.assertEqual(observer.scan("job/"), {})

        insert = engine.begin()
        insert.put("job/1", 1)
        insert.commit()
        delete = engine.begin()
        delete.delete("job/1")
        delete.commit()

        with self.assertRaises(Conflict):
            observer.commit()
        self.assertEqual(engine.checkpoint()["data"], {})

    def test_write_free_commit_is_validated(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        reader.get("x")
        writer = engine.begin()
        writer.put("x", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()

        unrelated = engine.begin()
        unrelated.get("other")
        later = engine.begin()
        later.put("x", 2)
        self.assertEqual(later.commit(), 2)
        self.assertEqual(unrelated.commit(), 2)

    def test_savepoint_stack_and_closed_precedence(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("one")
        transaction.put("b", 2)
        transaction.savepoint("two")
        transaction.delete("a")
        transaction.release("one")
        transaction.savepoint("two")  # The newer savepoint was discarded.
        transaction.rollback_to("two")
        transaction.commit()
        self.assertEqual(engine.checkpoint()["data"], {"b": 2})

        with self.assertRaises(RuntimeError):
            transaction.put("", True)
        with self.assertRaises(RuntimeError):
            transaction.abort()

    def test_recovery_and_exports_do_not_alias_inputs(self):
        checkpoint = {"version": 4, "data": {"z": 9}}
        records = [
            {"version": 5, "writes": [["a", 1], ["z", None]]},
        ]
        engine = Engine.restore(checkpoint, records)
        checkpoint["data"]["z"] = 100
        records[0]["writes"][0][1] = 100
        self.assertEqual(engine.checkpoint(), {"version": 5, "data": {"a": 1}})

        exported = engine.log_since(4)
        exported[0]["writes"][0][1] = 200
        self.assertEqual(engine.log_since(4)[0]["writes"][0], ["a", 1])
        with self.assertRaises(ValueError):
            engine.log_since(3)

    def test_restore_rejects_noncanonical_records(self):
        checkpoint = {"version": 0, "data": {}}
        bad_records = (
            [{"version": 2, "writes": [["a", 1]]}],
            [{"version": 1, "writes": []}],
            [{"version": 1, "writes": [["b", 1], ["a", 2]]}],
            [{"version": 1, "writes": [("a", 1)]}],
            [{"version": True, "writes": [["a", 1]]}],
        )
        for records in bad_records:
            with self.subTest(records=records), self.assertRaises(ValueError):
                Engine.restore(checkpoint, records)


if __name__ == "__main__":
    unittest.main()
