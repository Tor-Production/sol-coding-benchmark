import unittest

from mvcc import Conflict, Engine


class ContractTests(unittest.TestCase):
    def test_sorted_defensive_exports_and_real_writes(self):
        initial = {"z": 3, "a": 1}
        engine = Engine(initial)
        initial["a"] = 99
        self.assertEqual(list(engine.checkpoint()["data"]), ["a", "z"])

        transaction = engine.begin()
        transaction.delete("missing")
        transaction.put("a", 1)
        self.assertEqual(transaction.commit(), 1)
        expected = {
            "version": 1,
            "writes": [["a", 1], ["missing", None]],
        }
        exported = engine.log_since(0)
        self.assertEqual(exported, [expected])
        exported[0]["writes"][0][1] = 500
        self.assertEqual(engine.log_since(0), [expected])

    def test_key_range_and_aba_conflicts(self):
        engine = Engine({"x": 1})
        key_reader = engine.begin()
        self.assertEqual(key_reader.get("x"), 1)
        scanner = engine.begin()
        self.assertEqual(scanner.scan("job/"), {})

        first = engine.begin()
        first.put("x", 2)
        first.put("job/1", 1)
        first.commit()
        second = engine.begin()
        second.put("x", 1)
        second.delete("job/1")
        second.commit()

        with self.assertRaises(Conflict):
            key_reader.commit()
        with self.assertRaises(Conflict):
            scanner.commit()

    def test_write_free_validation_and_unrelated_writes(self):
        engine = Engine({"a": 1})
        transaction = engine.begin()
        transaction.get("a")
        writer = engine.begin()
        writer.put("b", 2)
        writer.commit()
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.version, 1)

    def test_savepoints_restore_writes_but_not_reads(self):
        engine = Engine({"observed": 1})
        transaction = engine.begin()
        transaction.put("a", 1)
        transaction.savepoint("outer")
        transaction.put("a", 2)
        transaction.get("observed")
        transaction.savepoint("inner")
        transaction.put("b", 3)
        transaction.rollback_to("outer")
        transaction.savepoint("inner")

        writer = engine.begin()
        writer.put("observed", 2)
        writer.commit()
        with self.assertRaises(Conflict):
            transaction.commit()

    def test_closed_methods_precede_argument_validation(self):
        transaction = Engine().begin()
        transaction.abort()
        calls = [
            lambda: transaction.get(None),
            lambda: transaction.scan(None),
            lambda: transaction.put(None, None),
            lambda: transaction.delete(None),
            lambda: transaction.savepoint(None),
            lambda: transaction.rollback_to(None),
            lambda: transaction.release(None),
            transaction.commit,
            transaction.abort,
        ]
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(RuntimeError):
                    call()

    def test_recovery_base_and_round_trip(self):
        engine = Engine({"a": 1})
        first = engine.begin()
        first.put("b", 2)
        first.commit()
        checkpoint = engine.checkpoint()
        second = engine.begin()
        second.delete("a")
        second.put("c", -10**100)
        second.commit()

        restored = Engine.restore(checkpoint, engine.log_since(1))
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        with self.assertRaises(ValueError):
            restored.log_since(0)
        self.assertEqual(restored.log_since(1), engine.log_since(1))

    def test_restore_rejects_bad_streams(self):
        checkpoint = {"version": 4, "data": {"a": 1}}
        bad_records = [
            [{"version": 6, "writes": [["b", 2]]}],
            [{"version": 5, "writes": []}],
            [{"version": 5, "writes": [("b", 2)]}],
            [{"version": 5, "writes": [["b", 2], ["a", 3]]}],
            [{"version": 5, "writes": [["b", True]]}],
        ]
        for records in bad_records:
            with self.subTest(records=records):
                with self.assertRaises(ValueError):
                    Engine.restore(checkpoint, records)


if __name__ == "__main__":
    unittest.main()
