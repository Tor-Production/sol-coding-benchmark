import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_history_and_empty_range(self):
        engine = Engine({'x': 1})
        reader, scanner = engine.begin(), engine.begin()
        reader.get('x')
        self.assertEqual(scanner.scan('job/'), {})
        for value in (2, 1):
            writer = engine.begin()
            writer.put('x', value)
            writer.commit()
        writer = engine.begin()
        writer.put('job/a', 3)
        writer.commit()
        writer = engine.begin()
        writer.delete('job/a')
        writer.commit()
        for transaction in (reader, scanner):
            with self.assertRaises(Conflict):
                transaction.commit()
            with self.assertRaises(RuntimeError):
                transaction.get(None)
        self.assertEqual(engine.version, 4)

    def test_savepoint_reads_and_lifetime(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.savepoint('a')
        transaction.put('x', 1)
        transaction.savepoint('b')
        transaction.get('missing')
        transaction.rollback_to('a')
        with self.assertRaises(ValueError):
            transaction.release('b')
        transaction.savepoint('b')
        transaction.release('a')
        transaction.savepoint('a')
        writer = engine.begin()
        writer.delete('missing')
        writer.commit()
        with self.assertRaises(Conflict):
            transaction.commit()

    def test_recovery_copies_and_base(self):
        checkpoint = {'version': 7, 'data': {'z': 1}}
        records = [{'version': 8, 'writes': [['a', 2], ['z', None]]}]
        engine = Engine.restore(checkpoint, records)
        checkpoint['data']['z'] = 9
        records[0]['writes'][0][1] = 99
        exported = engine.log_since(7)
        exported[0]['writes'][0][1] = 44
        self.assertEqual(engine.checkpoint(), {'version': 8, 'data': {'a': 2}})
        self.assertEqual(engine.log_since(7)[0]['writes'][0], ['a', 2])
        with self.assertRaises(ValueError):
            engine.log_since(6)
        transaction = engine.begin()
        transaction.get('a')
        self.assertEqual(transaction.commit(), 8)

    def test_invalid_operations_and_closed_precedence(self):
        engine = Engine()
        transaction = engine.begin()
        transaction.put('a', 1)
        for value in (True, None, 1.0, '1'):
            with self.assertRaises(ValueError):
                transaction.put('a', value)
        with self.assertRaises(ValueError):
            transaction.scan(None)
        self.assertEqual(transaction.commit(), 1)
        self.assertEqual(engine.checkpoint()['data'], {'a': 1})
        for method, args in ((transaction.put, (None, None)),
                             (transaction.delete, (None,)),
                             (transaction.scan, (None,)),
                             (transaction.savepoint, (None,)),
                             (transaction.rollback_to, (None,)),
                             (transaction.release, (None,)),
                             (transaction.abort, ()),
                             (transaction.commit, ())):
            with self.assertRaises(RuntimeError):
                method(*args)

    def test_invalid_recovery(self):
        checkpoint = {'version': 0, 'data': {}}
        bad_writes = ([], [['b', 1], ['a', 2]], [['a', 1], ['a', 2]],
                      [['a', True]], [('a', 1)], [['', 1]])
        for writes in bad_writes:
            with self.assertRaises(ValueError):
                Engine.restore(checkpoint, [{'version': 1, 'writes': writes}])
        with self.assertRaises(ValueError):
            Engine.restore(checkpoint, [{'version': True, 'writes': [['a', 1]]}])

    def test_snapshot_and_unrelated_writes(self):
        initial = {'z': 1, 'a': 2}
        engine = Engine(initial)
        initial['a'] = 99
        transaction = engine.begin()
        writer = engine.begin()
        writer.put('z', 3)
        writer.commit()
        self.assertEqual(transaction.get('a'), 2)
        transaction.put('b', -10**100)
        transaction.delete('absent')
        self.assertEqual(transaction.commit(), 2)
        self.assertEqual(list(engine.checkpoint()['data']), ['a', 'b', 'z'])
        self.assertEqual(engine.log_since(1)[0]['writes'],
                         [['absent', None], ['b', -10**100]])


if __name__ == '__main__':
    unittest.main()
