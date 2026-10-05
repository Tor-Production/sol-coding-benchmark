import unittest

from mvcc import Conflict, Engine


class ContractEdgeCases(unittest.TestCase):
    def test_empty_range_tracks_insert_then_delete(self):
        engine = Engine()
        observer = engine.begin()
        self.assertEqual(observer.scan("job/"), {})

        insert = engine.begin()
        insert.put("job/1", 10)
        insert.commit()
        delete = engine.begin()
        delete.delete("job/1")
        delete.commit()

        with self.assertRaises(Conflict):
            observer.commit()
        with self.assertRaises(RuntimeError):
            observer.abort()

    def test_read_only_transaction_is_validated(self):
        engine = Engine({"x": 1})
        reader = engine.begin()
        self.assertEqual(reader.get("x"), 1)
        writer = engine.begin()
        writer.put("x", 1)
        writer.commit()
        with self.assertRaises(Conflict):
            reader.commit()

    def test_release_keeps_writes_and_reads(self):
        engine = Engine({"watched": 1})
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("outer")
        transaction.get("watched")
        transaction.savepoint("inner")
        transaction.put("b", 2)
        transaction.release("outer")

        writer = engine.begin()
        writer.put("watched", 2)
        writer.commit()
        with self.assertRaises(Conflict):
            transaction.commit()
        self.assertEqual(engine.checkpoint()["data"], {"watched": 2})

    def test_recovery_and_exports_are_defensive(self):
        checkpoint = {"version": 7, "data": {"b": 2, "a": 1}}
        records = [
            {"version": 8, "writes": [["a", None], ["c", 3]]},
            {"version": 9, "writes": [["missing", None]]},
        ]
        engine = Engine.restore(checkpoint, records)
        checkpoint["data"]["a"] = 99
        records[0]["writes"][1][1] = 99

        self.assertEqual(
            engine.checkpoint(), {"version": 9, "data": {"b": 2, "c": 3}}
        )
        exported = engine.log_since(7)
        exported[0]["writes"][1][1] = 100
        self.assertEqual(engine.log_since(7)[0]["writes"][1], ["c", 3])
        with self.assertRaises(ValueError):
            engine.log_since(6)

    def test_invalid_operation_and_closed_precedence(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put("good", 1)
        with self.assertRaises(ValueError):
            transaction.put("bad", True)
        self.assertEqual(transaction.get("good"), 1)
        transaction.abort()
        with self.assertRaises(RuntimeError):
            transaction.put("", None)


if __name__ == "__main__":
    unittest.main()
