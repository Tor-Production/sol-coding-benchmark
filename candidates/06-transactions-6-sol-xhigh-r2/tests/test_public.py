import unittest
from mvcc import Engine, Conflict

class Public(unittest.TestCase):
    def test_commit_and_recovery(self):
        e = Engine({'x': 1})
        cp = e.checkpoint()
        t = e.begin()
        t.put('y', 2)
        self.assertEqual(t.get('y'), 2)
        self.assertEqual(t.commit(), 1)
        restored = Engine.restore(cp, e.log_since(0))
        self.assertEqual(restored.checkpoint(), e.checkpoint())

    def test_write_skew(self):
        e = Engine({'x': 1, 'y': 1})
        a, b = e.begin(), e.begin()
        a.get('y'); a.put('x', 0)
        b.get('x'); b.put('y', 0)
        a.commit()
        with self.assertRaises(Conflict):
            b.commit()

    def test_savepoint(self):
        e = Engine()
        t = e.begin(); t.put('a', 1); t.savepoint('s'); t.put('a', 2)
        t.rollback_to('s')
        self.assertEqual(t.get('a'), 1)
        self.assertEqual(t.commit(), 1)

if __name__ == '__main__':
    unittest.main()
