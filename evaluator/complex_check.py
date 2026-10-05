"""Private deterministic checks. No candidate receives evaluator feedback."""
import argparse
import copy
import importlib
import itertools
import json
import random
import subprocess
import sys
import unittest
from pathlib import Path

WORKSPACE = None
WEIGHTS = {
    '05-optimizer': {'validation': 20, 'constraints': 25, 'optimality': 35, 'scale': 20},
    '06-transactions': {'semantics': 25, 'isolation': 35, 'recovery': 25, 'validation': 15},
}

def project(name, value, cost, requires=(), excludes=()):
    return dict(id=name, value=value, cost=list(cost), requires=list(requires), excludes=list(excludes))

def oracle(projects, budget, required=()):
    """Independent exhaustive subset oracle: no pruning or reference code."""
    winner = rank = None
    for bits in itertools.product((False, True), repeat=len(projects)):
        members = {p['id'] for p, bit in zip(projects, bits) if bit}
        if not set(required) <= members:
            continue
        chosen = [p for p, bit in zip(projects, bits) if bit]
        if any(not set(p['requires']) <= members or set(p['excludes']) & members for p in chosen):
            continue
        costs = [sum(p['cost'][d] for p in chosen) for d in range(len(budget))]
        if any(c > b for c, b in zip(costs, budget)):
            continue
        value = sum(p['value'] for p in chosen)
        candidate_rank = (-value, tuple(costs), tuple(sorted(members)))
        if rank is None or candidate_rank < rank:
            rank = candidate_rank
            winner = dict(selected=sorted(members), value=value, cost=costs)
    return winner

def scale_fixture(name):
    if name == 'positive_bound':
        ps = [project(f'p{i:02}', i+1, [1]) for i in range(31)]
        ps += [project('trap', 10000, [32])]
        return ps, [31], [], dict(selected=[f'p{i:02}' for i in range(31)], value=496, cost=[31])
    if name == 'dependency_chain':
        ps = [project(f'p{i:02}', -1 if i < 31 else 100, [1], [f'p{i-1:02}'] if i else []) for i in range(32)]
        return list(reversed(ps)), [32], [], dict(selected=[f'p{i:02}' for i in range(32)], value=69, cost=[32])
    if name == 'conflict_pairs':
        ps = []
        for i in range(16):
            ps += [project(f'a{i:02}', i+2, [1, 1], excludes=[f'b{i:02}']), project(f'b{i:02}', 1, [1, 1])]
        return ps, [3, 3], [], dict(selected=['a13', 'a14', 'a15'], value=48, cost=[3, 3])
    raise ValueError(name)

class Planner(unittest.TestCase):
    def setUp(self):
        self.solve = importlib.import_module('optimizer').solve

    def test_validation_containers(self):
        for ps, b, r in [(None, [1], ()), ((), [1], ()), ([], [], ()), ([], (1,), ()), ([], [True], ()),
                         ([], [-1], ()), ([], [1, 2, 3, 4], ()), ([], [1], 'x'), ([], [1], ['missing'])]:
            with self.subTest(ps=ps, b=b, r=r), self.assertRaises(ValueError):
                self.solve(ps, b, r)
        valid = project('x', 2, [1])
        for field, value in [('id', ''), ('id', 4), ('value', True), ('value', 2.1), ('cost', [True]),
                             ('cost', [1, 2]), ('cost', [-1]), ('cost', (1,)), ('requires', ()), ('excludes', None)]:
            p = copy.deepcopy(valid); p[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.solve([p], [1])
        for p in [dict(valid, extra=1), {k: v for k, v in valid.items() if k != 'value'}]:
            with self.assertRaises(ValueError):
                self.solve([p], [1])

    def test_validation_references_and_cycles(self):
        cases = [[project('a', 1, [1]), project('a', 2, [1])],
                 [project('a', 1, [1], ['a'])], [project('a', 1, [1], excludes=['a'])],
                 [project('a', 1, [1], ['missing'])], [project('a', 1, [1], excludes=['missing'])],
                 [project('a', 1, [1], ['b', 'b']), project('b', 1, [1])],
                 [project('a', 1, [1], excludes=['b', 'b']), project('b', 1, [1])],
                 [project('a', 1, [1], ['b']), project('b', 1, [1], ['a'])]]
        for ps in cases:
            with self.subTest(ps=ps), self.assertRaises(ValueError):
                self.solve(ps, [0])
        with self.assertRaises(ValueError):
            self.solve([project('a', 1, [1])], [1], ['a', 'a'])
        with self.assertRaises(ValueError):
            self.solve([project(f'p{i}', 1, [1]) for i in range(33)], [100])

    def test_validation_purity_and_full_validation(self):
        ps = [project('b', -3, [1]), project('a', 8, [1], ['b'])]
        b, r = [2], ['a']; before = copy.deepcopy((ps, b, r))
        self.solve(ps, b, r)
        self.assertEqual((ps, b, r), before)
        bad = [project('too-big', 1, [100], ['missing'])]
        before = copy.deepcopy(bad)
        with self.assertRaises(ValueError):
            self.solve(bad, [0])
        self.assertEqual(bad, before)

    def test_constraints_dependencies_and_required(self):
        ps = [project('a', 20, [1], ['b']), project('b', -8, [1], ['c']), project('c', -5, [1])]
        self.assertEqual(self.solve(ps, [3]), dict(selected=['a', 'b', 'c'], value=7, cost=[3]))
        self.assertEqual(self.solve(ps, [2]), dict(selected=[], value=0, cost=[0]))
        self.assertIsNone(self.solve(ps, [2], ['a']))
        self.assertEqual(self.solve(ps, [3], ('b',)), dict(selected=['a', 'b', 'c'], value=7, cost=[3]))

    def test_constraints_one_sided_exclusions(self):
        ps = [project('z', 9, [1]), project('a', 8, [1], excludes=['z'])]
        for inputs in [ps, list(reversed(ps))]:
            self.assertEqual(self.solve(inputs, [2])['selected'], ['z'])
            self.assertIsNone(self.solve(inputs, [2], ['a', 'z']))
        contradictory = [project('a', 100, [0], ['b'], ['b']), project('b', 1, [0])]
        self.assertEqual(self.solve(contradictory, [0])['selected'], ['b'])

    def test_constraints_multiple_resources_and_zero_cost(self):
        ps = [project('a', 5, [0, 2, 0]), project('b', 6, [2, 0, 0]), project('c', 7, [0, 0, 0])]
        self.assertEqual(self.solve(ps, [0, 2, 0]), dict(selected=['a', 'c'], value=12, cost=[0, 2, 0]))
        self.assertEqual(self.solve([], [0, 0]), dict(selected=[], value=0, cost=[0, 0]))
        self.assertEqual(self.solve([project('x', -10**100, [0])], [0], ['x'])['value'], -10**100)

    def test_optimality_greedy_traps(self):
        cases = [([project('a', 10, [6]), project('b', 8, [5]), project('c', 8, [5])], [10]),
                 ([project('a', 100, [1], ['b']), project('b', -99, [1]), project('c', 8, [2])], [2]),
                 ([project('a', 9, [1], excludes=['b', 'c']), project('b', 6, [1]), project('c', 6, [1])], [2])]
        for ps, b in cases:
            self.assertEqual(self.solve(ps, b), oracle(ps, b))

    def test_optimality_all_tie_breaks(self):
        cases = [([project('z', 4, [1, 9]), project('a', 4, [2, 0])], [2, 9]),
                 ([project('z', 4, [2]), project('a', 4, [2])], [2]),
                 ([project('b', 1, [0]), project('a', 0, [0])], [0]),
                 ([project('a', 0, [0]), project('b', 0, [0])], [0])]
        for ps, b in cases:
            self.assertEqual(self.solve(ps, b), oracle(ps, b))
            self.assertEqual(self.solve(list(reversed(ps)), b), oracle(ps, b))

    def test_optimality_seeded_exhaustive_oracle(self):
        rng = random.Random(610205)
        for case in range(64):
            n = rng.randrange(4, 12); dimensions = 1 + case % 3
            ps = []
            for i in range(n):
                ps.append(project(f'p{i:02}', rng.randrange(-5, 21), [rng.randrange(0, 7) for _ in range(dimensions)],
                                  [f'p{j:02}' for j in range(i) if rng.random() < .13],
                                  [f'p{j:02}' for j in range(i) if rng.random() < .10]))
            budget = [rng.randrange(3, 15) for _ in range(dimensions)]
            required = [p['id'] for p in ps if rng.random() < .12]
            expected = oracle(ps, budget, required)
            rng.shuffle(ps)
            with self.subTest(case=case):
                self.assertEqual(self.solve(ps, budget, required), expected)

    def scale(self, name):
        command = [sys.executable, '-B', __file__, '--workspace', WORKSPACE, '--scale-case', name]
        try:
            r = subprocess.run(command, capture_output=True, text=True, timeout=8)
        except subprocess.TimeoutExpired:
            self.fail('Scale fixture exceeded 8 seconds')
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2500:])

    def test_scale_positive_bound(self):
        self.scale('positive_bound')

    def test_scale_dependency_chain(self):
        self.scale('dependency_chain')

    def test_scale_conflict_pairs(self):
        self.scale('conflict_pairs')

class Transactions(unittest.TestCase):
    def setUp(self):
        module = importlib.import_module('mvcc')
        self.Engine, self.Conflict = module.Engine, module.Conflict

    def test_semantics_snapshot_and_own_writes(self):
        e = self.Engine({'a': 1, 'b': 2}); a, b = e.begin(), e.begin()
        b.put('a', 9); b.delete('b'); b.put('c', 3); b.commit()
        self.assertEqual(a.scan(), {'a': 1, 'b': 2})
        self.assertIsNone(a.get('c'))
        a.put('a', -10**300); a.delete('b'); a.put('z', 4)
        self.assertEqual(a.scan(), {'a': -10**300, 'z': 4})
        self.assertEqual(e.checkpoint(), {'version': 1, 'data': {'a': 9, 'c': 3}})
        a.abort()

    def test_semantics_versions_and_atomic_writes(self):
        e = self.Engine(); self.assertEqual(e.begin().commit(), 0)
        t = e.begin(); t.put('b', 2); t.put('a', 1); t.put('b', 3); t.delete('ghost')
        self.assertEqual(t.commit(), 1)
        self.assertEqual(e.log_since(0), [{'version': 1, 'writes': [['a', 1], ['b', 3], ['ghost', None]]}])
        t = e.begin(); t.put('a', 1); self.assertEqual(t.commit(), 2)
        t = e.begin(); t.delete('missing'); self.assertEqual(t.commit(), 3)
        self.assertEqual(e.begin().commit(), 3)

    def test_semantics_nested_savepoints(self):
        e = self.Engine({'a': 0}); t = e.begin()
        t.put('a', 1); t.savepoint('outer'); t.delete('a'); t.savepoint('inner'); t.put('b', 2)
        t.rollback_to('inner'); self.assertEqual(t.scan(), {})
        t.put('c', 3); t.rollback_to('outer'); self.assertEqual(t.scan(), {'a': 1})
        with self.assertRaises(ValueError): t.release('inner')
        t.put('d', 4); t.rollback_to('outer'); self.assertEqual(t.scan(), {'a': 1})
        t.savepoint('inner'); t.put('a', 5); t.release('outer')
        self.assertEqual(t.get('a'), 5)
        with self.assertRaises(ValueError): t.rollback_to('inner')
        t.savepoint('outer'); self.assertEqual(t.commit(), 1)

    def test_semantics_defensive_copies_and_sorted_output(self):
        initial = {'z': 9, 'a': 1}; e = self.Engine(initial); initial['a'] = 8
        t = e.begin(); result = t.scan(); result['a'] = 99
        self.assertEqual(list(t.scan()), ['a', 'z']); self.assertEqual(t.get('a'), 1); t.abort()
        cp = e.checkpoint(); cp['data']['a'] = 7
        t = e.begin(); t.put('b', 2); t.commit()
        records = e.log_since(0); records[0]['writes'][0][1] = 9; records.clear()
        self.assertEqual(e.log_since(0)[0]['writes'], [['b', 2]])
        self.assertEqual(e.checkpoint()['data']['a'], 1)

    def test_isolation_read_write_and_write_skew(self):
        e = self.Engine({'x': 1, 'y': 1}); a, b = e.begin(), e.begin()
        a.get('y'); a.put('x', 0); b.get('x'); b.put('y', 0); a.commit()
        before = e.checkpoint(), e.log_since(0)
        with self.assertRaises(self.Conflict): b.commit()
        self.assertEqual((e.checkpoint(), e.log_since(0)), before)
        with self.assertRaises(RuntimeError): b.abort()

    def test_isolation_blind_writes_and_unrelated_progress(self):
        e = self.Engine(); a, b = e.begin(), e.begin(); a.put('x', 1); b.put('x', 2); a.commit()
        with self.assertRaises(self.Conflict): b.commit()
        a, b = e.begin(), e.begin(); a.get('x'); b.put('y', 3); b.commit()
        a.put('z', 4); self.assertEqual(a.commit(), 3)
        self.assertEqual(e.checkpoint()['data'], {'x': 1, 'y': 3, 'z': 4})

    def test_isolation_phantom_and_empty_scan(self):
        e = self.Engine({'other': 1}); reader = e.begin(); self.assertEqual(reader.scan('job/'), {})
        t = e.begin(); t.put('job/1', 1); t.commit()
        t = e.begin(); t.delete('job/1'); t.commit()
        with self.assertRaises(self.Conflict): reader.commit()
        reader = e.begin(); reader.scan('job/'); t = e.begin(); t.put('unrelated', 2); t.commit()
        self.assertEqual(reader.commit(), 3)

    def test_isolation_absent_reads_aba_and_same_value(self):
        for missing in (False, True):
            e = self.Engine({} if missing else {'a': 1}); reader = e.begin(); reader.get('a')
            t = e.begin(); t.put('a', 2); t.commit()
            t = e.begin(); t.delete('a') if missing else t.put('a', 1); t.commit()
            with self.assertRaises(self.Conflict): reader.commit()
        e = self.Engine({'a': 1}); r = e.begin(); r.get('a'); t = e.begin(); t.put('a', 1); t.commit()
        with self.assertRaises(self.Conflict): r.commit()

    def test_isolation_reads_survive_savepoint_rollback(self):
        for range_read in (False, True):
            e = self.Engine(); reader = e.begin(); reader.savepoint('s')
            reader.scan('x') if range_read else reader.get('x')
            reader.put('temporary', 8); reader.rollback_to('s'); reader.release('s')
            writer = e.begin(); writer.put('x', 1); writer.commit()
            with self.assertRaises(self.Conflict): reader.commit()
            self.assertNotIn('temporary', e.checkpoint()['data'])

    def test_isolation_full_history_and_prefix_precision(self):
        e = self.Engine({'x': 1}); reader = e.begin(); reader.get('x')
        t = e.begin(); t.put('x', 2); t.commit()
        t = e.begin(); t.put('unrelated', 3); t.commit()
        with self.assertRaises(self.Conflict): reader.commit()
        reader = e.begin(); reader.scan('job/')
        t = e.begin(); t.put('job', 1); t.commit()
        self.assertEqual(reader.commit(), 3)
        reader = e.begin(); reader.scan('')
        t = e.begin(); t.delete('never-existed'); t.commit()
        with self.assertRaises(self.Conflict): reader.commit()

    def test_isolation_seeded_interleaving_oracle(self):
        # Compare observable behavior to a declarative snapshot/history model.
        rng = random.Random(610216); e = self.Engine({'k0': 1}); committed = {'k0': 1}
        history = []; active = []; version = 0
        for step in range(480):
            if not active or (len(active) < 5 and rng.random() < .23):
                active.append(dict(tx=e.begin(), snapshot=dict(committed), start=version, reads=set(), prefixes=set(), writes={}))
                continue
            state = rng.choice(active); tx = state['tx']; k = f'k{rng.randrange(8)}'; choice = rng.randrange(6)
            visible = dict(state['snapshot'])
            for name, value in state['writes'].items():
                if value is None: visible.pop(name, None)
                else: visible[name] = value
            if choice == 0:
                self.assertEqual(tx.get(k), visible.get(k)); state['reads'].add(k)
            elif choice == 1:
                prefix = rng.choice(['', 'k', 'k1', 'missing/'])
                self.assertEqual(tx.scan(prefix), dict(sorted((n, v) for n, v in visible.items() if n.startswith(prefix))))
                state['prefixes'].add(prefix)
            elif choice == 2:
                value = rng.randrange(-10, 11); tx.put(k, value); state['writes'][k] = value
            elif choice == 3:
                tx.delete(k); state['writes'][k] = None
            elif choice == 4:
                tx.abort(); active.remove(state)
            else:
                intervening = {name for v, names in history if v > state['start'] for name in names}
                touched = state['reads'] | set(state['writes'])
                conflict = bool(intervening & touched) or any(n.startswith(p) for n in intervening for p in state['prefixes'])
                if conflict:
                    with self.assertRaises(self.Conflict): tx.commit()
                else:
                    if state['writes']:
                        version += 1; history.append((version, set(state['writes'])))
                        for name, value in state['writes'].items():
                            if value is None: committed.pop(name, None)
                            else: committed[name] = value
                    self.assertEqual(tx.commit(), version)
                active.remove(state)
            self.assertEqual(e.checkpoint(), {'version': version, 'data': dict(sorted(committed.items()))})
        for state in active: state['tx'].abort()

    def test_recovery_every_prefix_and_retained_base(self):
        e = self.Engine({'a': 1}); checkpoints = [e.checkpoint()]
        for i in range(8):
            t = e.begin(); t.put(f'k{i}', i); t.delete('a') if i == 2 else t.put('a', i)
            t.commit(); checkpoints.append(e.checkpoint())
        records = e.log_since(0)
        for base in range(9):
            for end in range(base, 9):
                r = self.Engine.restore(checkpoints[base], records[base:end])
                self.assertEqual(r.checkpoint(), checkpoints[end])
                self.assertEqual(r.log_since(base), records[base:end])
                if base:
                    with self.assertRaises(ValueError): r.log_since(base-1)
        restored = self.Engine.restore(checkpoints[3], records[3:])
        t = restored.begin(); t.put('after', 1); self.assertEqual(t.commit(), 9)

    def test_recovery_validation_and_input_purity(self):
        cp = {'version': 0, 'data': {'a': 1}}
        good = [{'version': 1, 'writes': [['b', 2]]}]
        invalid = [None, (), [dict(good[0], extra=1)], [{'version': True, 'writes': [['b', 2]]}],
                   [{'version': 2, 'writes': [['b', 2]]}], [{'version': 1, 'writes': []}],
                   [{'version': 1, 'writes': [['b', 2], ['a', 1]]}],
                   [{'version': 1, 'writes': [['a', 1], ['a', 2]]}],
                   [{'version': 1, 'writes': [['b', True]]}], [{'version': 1, 'writes': [('', 1)]}],
                   good + [{'version': 3, 'writes': [['c', 3]]}]]
        for records in invalid:
            before = copy.deepcopy((cp, records))
            with self.subTest(records=records), self.assertRaises(ValueError): self.Engine.restore(cp, records)
            self.assertEqual((cp, records), before)
        for bad in [None, {}, dict(cp, extra=1), {'version': -1, 'data': {}}, {'version': True, 'data': {}},
                    {'version': 0, 'data': {'a': None}}]:
            with self.assertRaises(ValueError): self.Engine.restore(bad, [])

    def test_recovery_defensive_restore_and_conflicts(self):
        cp = {'version': 10, 'data': {'a': 1}}; records = [{'version': 11, 'writes': [['b', 2]]}]
        e = self.Engine.restore(cp, records); cp['data']['a'] = 99; records[0]['writes'][0][1] = 99
        self.assertEqual(e.checkpoint(), {'version': 11, 'data': {'a': 1, 'b': 2}})
        reader = e.begin(); reader.get('a'); writer = e.begin(); writer.put('a', 2); writer.commit()
        with self.assertRaises(self.Conflict): reader.commit()
        self.assertEqual(e.log_since(10)[0]['writes'], [['b', 2]])

    def test_recovery_seeded_model_trace(self):
        # A sequential dictionary replay oracle, independent of MVCC internals.
        rng = random.Random(610206); expected = {}; version = 0; log = []
        e = self.Engine(); origin = e.checkpoint()
        for step in range(180):
            t = e.begin(); pending = {}
            for _ in range(rng.randrange(1, 7)):
                k = f'key/{rng.randrange(12):02}'; value = rng.randrange(-100, 100)
                if rng.random() < .3: t.delete(k); pending[k] = None
                else: t.put(k, value); pending[k] = value
            if step % 7 == 0:
                t.abort()
            else:
                for k, value in pending.items():
                    if value is None: expected.pop(k, None)
                    else: expected[k] = value
                version += 1; log.append({'version': version, 'writes': [[k, v] for k, v in sorted(pending.items())]})
                self.assertEqual(t.commit(), version)
            self.assertEqual(e.checkpoint(), {'version': version, 'data': dict(sorted(expected.items()))})
            self.assertEqual(e.log_since(0), log)
            if step % 19 == 0:
                e = self.Engine.restore(origin, log)

    def test_validation_initial_and_versions(self):
        for initial in [[], {'': 1}, {1: 2}, {'x': True}, {'x': None}, {'x': 1.5}]:
            with self.assertRaises(ValueError): self.Engine(initial)
        e = self.Engine()
        for version in [True, -1, 1, '0', None]:
            with self.assertRaises(ValueError): e.log_since(version)
        self.assertTrue(issubclass(self.Conflict, RuntimeError))

    def test_validation_operations_are_atomic(self):
        e = self.Engine({'a': 1}); t = e.begin(); t.put('b', 2); t.savepoint('s')
        for method, args in [('put', ('a', True)), ('put', ('', 4)), ('delete', (None,)), ('get', (1,)),
                             ('scan', (None,)), ('savepoint', ('s',)), ('savepoint', ('',)),
                             ('rollback_to', ('missing',)), ('release', ('missing',))]:
            with self.subTest(method=method), self.assertRaises(ValueError): getattr(t, method)(*args)
        self.assertEqual(t.scan(), {'a': 1, 'b': 2})
        t.rollback_to('s'); self.assertEqual(t.commit(), 1)
        self.assertEqual(e.checkpoint()['data'], {'a': 1, 'b': 2})

    def test_validation_failed_requests_do_not_track_reads_or_writes(self):
        e = self.Engine({'a': 1}); reader = e.begin()
        for method, args in [('put', ('a', True)), ('get', (None,)), ('scan', (None,))]:
            with self.assertRaises(ValueError): getattr(reader, method)(*args)
        writer = e.begin(); writer.put('a', 2); self.assertIsNone(writer.delete('absent'))
        writer.commit()
        self.assertEqual(reader.commit(), 1)
        t = e.begin(); self.assertIsNone(t.savepoint('s')); self.assertIsNone(t.put('b', 2))
        self.assertIsNone(t.rollback_to('s')); self.assertIsNone(t.release('s')); self.assertIsNone(t.abort())

    def test_validation_closed_transactions(self):
        for end in ('commit', 'abort', 'conflict'):
            e = self.Engine({'a': 1}); t = e.begin()
            if end == 'conflict':
                t.get('a'); writer = e.begin(); writer.put('a', 2); writer.commit()
                with self.assertRaises(self.Conflict): t.commit()
            else: getattr(t, end)()
            for method, args in [('get', ('',)), ('scan', (None,)), ('put', ('a', True)), ('delete', ('',)),
                                 ('savepoint', ('',)), ('rollback_to', ('',)), ('release', ('',)), ('commit', ()), ('abort', ())]:
                with self.subTest(end=end, method=method), self.assertRaises(RuntimeError): getattr(t, method)(*args)

class Ledger(unittest.TestResult):
    def __init__(self):
        super().__init__(); self.records = {}

    def startTest(self, test):
        super().startTest(test)
        method = test._testMethodName
        self.records[test.id()] = dict(test=f'{type(test).__name__}.{method}', category=method.split('_')[1], passed=True, detail=None)

    def failed(self, test, err):
        parent = test.test_case if isinstance(test, unittest.case._SubTest) else test
        self.records[parent.id()].update(passed=False, detail=self._exc_info_to_string(err, test)[-2500:])

    def addFailure(self, test, err):
        super().addFailure(test, err); self.failed(test, err)

    def addError(self, test, err):
        super().addError(test, err); self.failed(test, err)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err: self.failed(test, err)

def main():
    global WORKSPACE
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', choices=list(WEIGHTS))
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--output')
    parser.add_argument('--scale-case')
    args = parser.parse_args(); WORKSPACE = str(Path(args.workspace).resolve()); sys.path.insert(0, WORKSPACE)
    if args.scale_case:
        ps, budget, required, expected = scale_fixture(args.scale_case)
        actual = importlib.import_module('optimizer').solve(ps, budget, required)
        if actual != expected: raise AssertionError((actual, expected))
        return
    if not args.task or not args.output: parser.error('--task and --output required')
    klass = Planner if args.task == '05-optimizer' else Transactions
    result = Ledger(); unittest.defaultTestLoader.loadTestsFromTestCase(klass).run(result)
    records = list(result.records.values()); categories = {}
    for category, weight in WEIGHTS[args.task].items():
        cases = [r for r in records if r['category'] == category]
        passed = sum(r['passed'] for r in cases)
        categories[category] = dict(weight=weight, passed=passed, total=len(cases), score=100*passed/len(cases))
    passed = sum(r['passed'] for r in records)
    report = dict(task=args.task, tests=records, passed=passed, total=len(records), categories=categories,
                  score=round(sum(c['score'] * c['weight'] / 100 for c in categories.values()), 6), accepted=passed == len(records))
    Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'tests'}))
    raise SystemExit(0 if report['accepted'] else 1)

if __name__ == '__main__':
    main()
