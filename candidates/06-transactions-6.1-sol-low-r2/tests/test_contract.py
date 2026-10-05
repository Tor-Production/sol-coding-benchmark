import copy
import unittest

from mvcc import Conflict, Engine


class Contract(unittest.TestCase):
    def test_history_and_read_only_conflicts(self):
        for mode in ('get', 'scan', 'write'):
            with self.subTest(mode=mode):
                engine = Engine()
                reader = engine.begin()
                if mode == 'get':
                    self.assertIsNone(reader.get('job/a'))
                elif mode == 'scan':
                    self.assertEqual(reader.scan('job/'), {})
                else:
                    reader.delete('job/a')
                writer = engine.begin()
                writer.put('job/a', 1)
                writer.commit()
                writer = engine.begin()
                writer.delete('job/a')
                writer.commit()
                before = engine.checkpoint()
                with self.assertRaises(Conflict):
                    reader.commit()
                self.assertEqual(engine.checkpoint(), before)
                with self.assertRaises(RuntimeError):
                    reader.abort()

    def test_savepoint_reads_survive_and_writes_restore(self):
        engine = Engine({'a': 1})
        tx = engine.begin()
        tx.put('a', 2)
        tx.savepoint('first')
        tx.put('a', 3)
        tx.savepoint('second')
        tx.get('missing')
        tx.scan('range/')
        tx.rollback_to('first')
        self.assertEqual(tx.get('a'), 2)
        tx.savepoint('second')
        tx.release('first')
        tx.savepoint('first')
        writer = engine.begin()
        writer.put('range/x', 4)
        writer.commit()
        with self.assertRaises(Conflict):
            tx.commit()

    def test_snapshots_order_and_defensive_exports(self):
        initial = {'z': 9, 'a': 1}
        engine = Engine(initial)
        initial['a'] = 99
        old = engine.begin()
        writer = engine.begin()
        writer.put('z', 9)
        writer.delete('missing')
        writer.put('b', -10 ** 100)
        self.assertEqual(writer.commit(), 1)
        self.assertEqual(old.scan(), {'a': 1, 'z': 9})
        cp = engine.checkpoint()
        self.assertEqual(list(cp['data']), ['a', 'b', 'z'])
        records = engine.log_since(0)
        self.assertEqual([p[0] for p in records[0]['writes']], ['b', 'missing', 'z'])
        cp['data'].clear()
        records[0]['writes'][0][1] = 0
        self.assertEqual(engine.begin().get('b'), -10 ** 100)
        self.assertEqual(engine.log_since(0)[0]['writes'][0][1], -10 ** 100)

    def test_recovery_base_and_aliases(self):
        cp = {'version': 7, 'data': {'x': 1}}
        records = [{'version': 8, 'writes': [['absent', None], ['x', 1]]},
                   {'version': 9, 'writes': [['x', None]]}]
        expected = copy.deepcopy(records)
        engine = Engine.restore(cp, records)
        cp['data']['x'] = 100
        records[0]['writes'].clear()
        self.assertEqual(engine.checkpoint(), {'version': 9, 'data': {}})
        self.assertEqual(engine.log_since(7), expected)
        with self.assertRaises(ValueError):
            engine.log_since(6)
        self.assertEqual(engine.begin().commit(), 9)
        writer = engine.begin()
        writer.delete('absent')
        self.assertEqual(writer.commit(), 10)

    def test_invalid_calls_and_closed_precedence(self):
        engine = Engine()
        tx = engine.begin()
        tx.put('x', 1)
        tx.savepoint('s')
        invalid = [(tx.get, ('',)), (tx.scan, (None,)),
                   (tx.put, ('y', True)), (tx.delete, (3,)),
                   (tx.savepoint, ('s',)), (tx.rollback_to, ('unknown',)),
                   (tx.release, ('unknown',))]
        for method, args in invalid:
            with self.assertRaises(ValueError):
                method(*args)
        tx.rollback_to('s')
        self.assertEqual(tx.scan(), {'x': 1})
        tx.commit()
        for method, args in invalid:
            with self.assertRaises(RuntimeError):
                method(*args)
        for method in (tx.commit, tx.abort):
            with self.assertRaises(RuntimeError):
                method()
        for version in (True, -1, 2, '0', None):
            with self.assertRaises(ValueError):
                engine.log_since(version)

    def test_malformed_recovery(self):
        checkpoint = {'version': 0, 'data': {}}
        bad_records = [None, (), [{}],
                       [{'version': True, 'writes': [['x', 1]]}],
                       [{'version': 2, 'writes': [['x', 1]]}],
                       [{'version': 1, 'writes': []}],
                       [{'version': 1, 'writes': [('x', 1)]}],
                       [{'version': 1, 'writes': [['x', True]]}],
                       [{'version': 1, 'writes': [['', 1]]}],
                       [{'version': 1, 'writes': [['z', 1], ['a', 2]]}],
                       [{'version': 1, 'writes': [['x', 1], ['x', 2]]}]]
        for records in bad_records:
            with self.subTest(records=records), self.assertRaises(ValueError):
                Engine.restore(checkpoint, records)
        for cp in (None, {}, {'version': False, 'data': {}},
                   {'version': -1, 'data': {}}, {'version': 0, 'data': {'x': None}},
                   {'version': 0, 'data': {}, 'extra': 1}):
            with self.subTest(checkpoint=cp), self.assertRaises(ValueError):
                Engine.restore(cp, [])


if __name__ == '__main__':
    unittest.main()
