"""Additional contract checks, independent of candidate tests and model identity."""
import copy
import http.client
import importlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import unittest
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote


def criterion(group):
    def decorate(fn):
        fn.criterion = group
        return fn
    return decorate


class IntervalsV2(unittest.TestCase):
    def setUp(self):
        self.merge = importlib.import_module('intervals').merge_intervals

    @criterion('properties')
    def test_random_union_1200_cases(self):
        rng = random.Random(20261001)
        for case in range(1200):
            rows = [sorted(rng.sample(range(-60, 61), 2)) for _ in range(rng.randrange(35))]
            points = sorted({x for a, b in rows for x in range(a, b + 1)})
            expected = []
            for x in points:
                if expected and x == expected[-1][1] + 1:
                    expected[-1][1] = x
                else:
                    expected.append([x, x])
            with self.subTest(case=case):
                self.assertEqual(self.merge(rows), expected)

    @criterion('properties')
    def test_permutation_duplication_and_idempotence(self):
        rng = random.Random(17171)
        for _ in range(100):
            rows = [sorted(rng.sample(range(-1000, 1000), 2)) for _ in range(30)]
            expected = self.merge(rows)
            shuffled = rows * 2
            rng.shuffle(shuffled)
            self.assertEqual(self.merge(shuffled), expected)
            self.assertEqual(self.merge(expected), expected)

    @criterion('properties')
    def test_translation_and_split_adjacency(self):
        offset = 10 ** 150
        rows = [[-10, -7], [-6, -3], [1, 4], [5, 7]]
        expected = [[-10, -3], [1, 7]]
        self.assertEqual(self.merge(rows), expected)
        self.assertEqual(self.merge([[a + offset, b + offset] for a, b in rows]),
                         [[a + offset, b + offset] for a, b in expected])

    @criterion('boundaries')
    def test_invalid_tail_preserves_all_input(self):
        for tail in ([True, 2], [3, False], [1.0, 2], [4, 3], None, [1], '12'):
            rows = [[9, 10], [1, 2], tail]
            before = copy.deepcopy(rows)
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                self.merge(rows)
            self.assertEqual(rows, before)

    @criterion('boundaries')
    def test_tuple_outer_output_alias_and_order(self):
        a = [7, 8]
        b = [1, 2]
        rows = (a, b, [3, 4])
        before = copy.deepcopy(rows)
        result = self.merge(rows)
        self.assertEqual(result, [[1, 4], [7, 8]])
        self.assertEqual(rows, before)
        result[1][0] = -99
        self.assertEqual(a, [7, 8])
        mutable = [[8, 9], [1, 2]]
        self.merge(mutable)
        self.assertEqual(mutable, [[8, 9], [1, 2]])

    @criterion('boundaries')
    def test_large_disjoint_30000(self):
        rows = [[i * 3, i * 3] for i in reversed(range(30000))]
        self.assertEqual(self.merge(rows), list(reversed(rows)))


class CacheV2(unittest.TestCase):
    def setUp(self):
        self.cls = importlib.import_module('cache').TTLCache

    @criterion('properties')
    def test_differential_traces_6000_operations(self):
        for seed in range(12):
            rng = random.Random(90210 + seed)
            now = [0.0]
            capacity, ttl = 1 + seed % 5, .25 + seed % 3
            c = self.cls(capacity, ttl, lambda: now[0])
            ref = OrderedDict()
            for step in range(500):
                now[0] += rng.choice([0, 0, .125, .25, 2])
                for k in list(ref):
                    if now[0] >= ref[k][1]:
                        del ref[k]
                key = rng.randrange(9)
                op = rng.choice(['put', 'put', 'get', 'delete', 'len'])
                with self.subTest(seed=seed, step=step, op=op):
                    if op == 'put':
                        value = rng.choice([None, 0, '', [step]])
                        c.put(key, value)
                        ref[key] = (value, now[0] + ttl)
                        ref.move_to_end(key)
                        while len(ref) > capacity:
                            ref.popitem(last=False)
                    elif op == 'get':
                        sentinel = object()
                        got = c.get(key, sentinel)
                        expected = ref[key][0] if key in ref else sentinel
                        if key in ref:
                            ref.move_to_end(key)
                        self.assertEqual(got, expected)
                    elif op == 'delete':
                        self.assertEqual(c.delete(key), key in ref)
                        ref.pop(key, None)
                    else:
                        self.assertEqual(len(c), len(ref))

    @criterion('boundaries')
    def test_arbitrary_large_finite_integer_ttl(self):
        ttl = 10 ** 400
        now = [0]
        c = self.cls(2, ttl, lambda: now[0])
        c.put('a', 1)
        now[0] = ttl - 1
        self.assertEqual(c.get('a'), 1)
        now[0] = ttl
        self.assertEqual(c.get('a', 'expired'), 'expired')

    @criterion('boundaries')
    def test_fractional_expiry_read_and_overwrite(self):
        now = [0.0]
        c = self.cls(2, .25, lambda: now[0])
        c.put('a', None)
        now[0] = .125
        self.assertIsNone(c.get('a', 'missing'))
        now[0] = .25
        self.assertEqual(c.get('a', 'expired'), 'expired')
        c.put('a', 2)
        now[0] = .375
        c.put('a', 3)
        now[0] = .5
        self.assertEqual(c.get('a'), 3)
        now[0] = .625
        self.assertFalse(c.delete('a'))

    @criterion('boundaries')
    def test_expired_mru_and_lru_under_capacity_pressure(self):
        now = [0.0]
        c = self.cls(2, 10, lambda: now[0])
        c.put('old', 1)
        now[0] = 1
        c.put('new', 2)
        c.get('old')
        now[0] = 10
        c.put('next', 3)
        self.assertEqual(c.get('new'), 2)
        self.assertEqual(c.get('next'), 3)
        c.put('last', 4)
        self.assertEqual(c.get('new', 'missing'), 'missing')

    @criterion('properties')
    def test_hash_collisions_and_object_identity(self):
        class Key:
            def __init__(self, value):
                self.value = value
            def __hash__(self):
                return 7
            def __eq__(self, other):
                return isinstance(other, Key) and self.value == other.value
        c = self.cls(3, 10, lambda: 0)
        values = [object() for _ in range(3)]
        for i, value in enumerate(values):
            c.put(Key(i), value)
        for i, value in enumerate(values):
            self.assertIs(c.get(Key(i)), value)
        self.assertEqual(len(c), 3)

    @criterion('boundaries')
    def test_constructor_nonfinite_bool_and_default(self):
        for cap, ttl in [(True, 1), (1, True), (1, float('nan')), (1, float('inf')),
                         (1, float('-inf')), (1, 0), (0, 1)]:
            with self.subTest(cap=cap, ttl=ttl), self.assertRaises(ValueError):
                self.cls(cap, ttl, lambda: 0)
        now = [0]
        c = self.cls(1, 1, lambda: now[0])
        sentinel = object()
        self.assertIs(c.get('absent', sentinel), sentinel)
        c.put('a', 1)
        now[0] = 1
        self.assertEqual(len(c), 0)

    @criterion('properties')
    def test_lru_reads_and_instance_independence(self):
        c = self.cls(2, 10, lambda: 0)
        other = self.cls(2, 10, lambda: 0)
        c.put('a', 1)
        c.put('b', 2)
        c.get('a')
        c.put('c', 3)
        self.assertEqual(c.get('a'), 1)
        self.assertIsNone(c.get('b'))
        self.assertEqual(len(other), 0)


class GraphV2(unittest.TestCase):
    def setUp(self):
        self.run = importlib.import_module('dag').run_graph

    @criterion('properties')
    def test_random_failure_graphs_80_cases(self):
        rng = random.Random(44123)
        for case in range(80):
            count = rng.randrange(5, 40)
            tasks, statuses, seen = {}, {}, []
            lock = threading.Lock()
            for i in range(count):
                key = f'n{i:03}'
                deps = [f'n{j:03}' for j in range(i) if rng.random() < .13]
                fails = rng.random() < .12
                blocked = any(statuses[d] != 'completed' for d in deps)
                statuses[key] = 'skipped' if blocked else ('failed' if fails else 'completed')
                def fn(key=key, fails=fails, deps=deps):
                    with lock:
                        self.assertTrue(all(d in seen for d in deps))
                        seen.append(key)
                    if fails:
                        raise RuntimeError('expected:' + key)
                    return key
                tasks[key] = {'deps': deps, 'fn': fn}
            before = {k: v['deps'][:] for k, v in tasks.items()}
            result = self.run(tasks, 1 + case % 4)
            with self.subTest(case=case):
                self.assertEqual(list(result), sorted(tasks))
                self.assertEqual({k: v['status'] for k, v in result.items()}, statuses)
                self.assertEqual(len(seen), len(set(seen)))
                self.assertEqual(set(seen), {k for k, s in statuses.items() if s != 'skipped'})
                self.assertEqual(before, {k: v['deps'] for k, v in tasks.items()})
                for key, status in statuses.items():
                    expected = ({'status': 'completed', 'value': key} if status == 'completed' else
                                {'status': 'failed', 'error': 'expected:' + key} if status == 'failed' else
                                {'status': 'skipped'})
                    self.assertEqual(result[key], expected)

    @criterion('boundaries')
    def test_invalid_disconnected_graphs_have_no_side_effects(self):
        for mode in ['cycle', 'duplicate', 'unknown', 'self', 'bad_fn']:
            seen = []
            tasks = {'root': {'deps': [], 'fn': lambda: seen.append('root')},
                     'a': {'deps': [], 'fn': lambda: 1}, 'b': {'deps': ['a'], 'fn': lambda: 2}}
            if mode == 'cycle':
                tasks['a']['deps'] = ['b']
            elif mode == 'duplicate':
                tasks['b']['deps'] = ['a', 'a']
            elif mode == 'unknown':
                tasks['b']['deps'] = ['nope']
            elif mode == 'self':
                tasks['b']['deps'] = ['b']
            else:
                tasks['b']['fn'] = None
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.run(tasks, 4)
            self.assertEqual(seen, [])

    @criterion('concurrency')
    def test_exact_worker_bound_and_parallel_progress(self):
        for workers in [2, 4]:
            barrier = threading.Barrier(workers)
            active, peak = [0], [0]
            lock = threading.Lock()
            def fn():
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                try:
                    barrier.wait(timeout=4)
                    return 'ok'
                finally:
                    with lock:
                        active[0] -= 1
            result = self.run({f'n{i}': {'deps': [], 'fn': fn} for i in range(workers * 3)}, workers)
            self.assertEqual(peak[0], workers)
            self.assertTrue(all(r == {'status': 'completed', 'value': 'ok'} for r in result.values()))
            self.assertEqual(active[0], 0)

    @criterion('concurrency')
    def test_eager_fanin_and_unrelated_slow_work(self):
        child_started = threading.Event()
        def slow():
            if not child_started.wait(4):
                raise RuntimeError('ready child blocked by unrelated task')
            return 'slow'
        def child():
            child_started.set()
            return 'child'
        tasks = {'a_slow': {'deps': [], 'fn': slow}, 'b_parent': {'deps': [], 'fn': lambda: 'p'},
                 'c_child': {'deps': ['b_parent'], 'fn': child},
                 'd_join': {'deps': ['c_child', 'a_slow'], 'fn': lambda: 'join'}}
        result = self.run(tasks, 2)
        self.assertTrue(all(r['status'] == 'completed' for r in result.values()))
        self.assertEqual(result['d_join']['value'], 'join')

    @criterion('properties')
    def test_deep_failure_chain_1500_and_return_order(self):
        seen = []
        def fail():
            raise LookupError('deep failure')
        tasks = {f'n{i:04}': {'deps': [] if i == 0 else [f'n{i-1:04}'],
                            'fn': fail if i == 0 else lambda: seen.append(1)} for i in range(1500)}
        result = self.run(tasks, 3)
        self.assertEqual(seen, [])
        self.assertEqual(result['n0000'], {'status': 'failed', 'error': 'deep failure'})
        self.assertTrue(all(result[f'n{i:04}'] == {'status': 'skipped'} for i in range(1, 1500)))
        result = self.run({'z': {'deps': [], 'fn': lambda: None}, 'a': {'deps': [], 'fn': lambda: False}})
        self.assertEqual(list(result), ['a', 'z'])
        self.assertIs(result['a']['value'], False)


class ServiceV2(unittest.TestCase):
    def setUp(self):
        module = importlib.import_module('reservation.store')
        self.Store, self.Conflict, self.NotFound = module.Store, module.Conflict, module.NotFound
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'service.db')
        self.store = self.Store(self.db)

    def server(self):
        server = importlib.import_module('reservation.http_api').create_server(self.db)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.addCleanup(close)
        return server.server_address

    def request(self, address, method, path, payload=None, raw=None):
        conn = http.client.HTTPConnection(*address, timeout=8)
        try:
            body = raw if raw is not None else (json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None)
            conn.request(method, path, body=body, headers={'Content-Type': 'application/json'})
            response = conn.getresponse()
            body = response.read()
            self.assertIn('application/json', response.getheader('Content-Type', ''))
            self.assertEqual(int(response.getheader('Content-Length', '-1')), len(body))
            return response.status, json.loads(body)
        finally:
            conn.close()

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', '-m', 'reservation', '--db', self.db, *args],
                              capture_output=True, text=True, encoding='utf-8', timeout=10)

    @criterion('properties')
    def test_model_trace_stock_and_release_invariants(self):
        rng = random.Random(66331)
        stock = {s: 40 for s in ['a', 'b', 'c']}
        records = {}
        for sku in stock:
            self.store.add_item(sku, stock[sku])
        for step in range(140):
            op = rng.choice(['add', 'reserve', 'release'])
            sku, quantity = rng.choice(list(stock)), rng.randrange(1, 9)
            if op == 'add':
                self.store.add_item(sku, quantity)
                stock[sku] += quantity
            elif op == 'reserve':
                if stock[sku] >= quantity:
                    row = self.store.reserve(f'k{step}', sku, quantity)
                    records[row['reservation_id']] = dict(row)
                    stock[sku] -= quantity
                    self.assertEqual(self.store.reserve(f'k{step}', sku, quantity), row)
                else:
                    before = self.store.report()
                    with self.assertRaises(self.Conflict):
                        self.store.reserve(f'k{step}', sku, quantity)
                    self.assertEqual(before, self.store.report())
            elif records:
                rid = rng.choice(list(records))
                row = records[rid]
                if row['status'] == 'active':
                    stock[row['sku']] += row['quantity']
                    row['status'] = 'released'
                self.assertEqual(self.store.release(rid), row)
            active = [r for r in records.values() if r['status'] == 'active']
            self.assertEqual(self.store.report(), {'items': [{'sku': s, 'available': stock[s]} for s in sorted(stock)],
                                                   'active_reservations': len(active),
                                                   'reserved_units': sum(r['quantity'] for r in active)})

    @criterion('concurrency')
    def test_concurrent_add_and_release_exactly_once(self):
        stores = [self.Store(self.db) for _ in range(12)]
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda s: s.add_item('a', 2), stores))
        self.assertEqual(self.store.get_item('a')['available'], 24)
        r = self.store.reserve('same', 'a', 9)
        with ThreadPoolExecutor(max_workers=12) as pool:
            released = list(pool.map(lambda s: s.release(r['reservation_id']), stores))
        self.assertTrue(all(row == released[0] for row in released))
        self.assertEqual(released[0]['status'], 'released')
        self.assertEqual(self.store.get_item('a')['available'], 24)
        self.assertEqual(self.store.report()['reserved_units'], 0)

    @criterion('concurrency')
    def test_synchronized_oversell_and_recovery(self):
        self.store.add_item('a', 9)
        stores = [self.Store(self.db) for _ in range(16)]
        barrier = threading.Barrier(16)
        def reserve(i):
            barrier.wait(5)
            try:
                return stores[i].reserve(f'key{i}', 'a', 2)
            except self.Conflict:
                return None
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(reserve, range(16)))
        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 4)
        self.assertEqual(self.store.get_item('a')['available'], 1)
        for r in successes:
            self.store.release(r['reservation_id'])
        self.assertEqual(self.store.get_item('a')['available'], 9)
        self.assertEqual(self.Store(self.db).report()['active_reservations'], 0)

    @criterion('concurrency')
    def test_same_key_conflicting_parameters_race(self):
        self.store.add_item('a', 20)
        stores = [self.Store(self.db) for _ in range(8)]
        barrier = threading.Barrier(8)
        def reserve(i):
            barrier.wait(5)
            try:
                return stores[i].reserve('key', 'a', 1 + i % 2)
            except self.Conflict:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(8)))
        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 4)
        self.assertTrue(all(r == successes[0] for r in successes))
        self.assertEqual(self.store.get_item('a')['available'], 20 - successes[0]['quantity'])
        self.assertEqual(self.store.report()['active_reservations'], 1)

    @criterion('boundaries')
    def test_http_limit_exact_and_survival_after_errors(self):
        address = self.server()
        small = json.dumps({'sku': 'a', 'quantity': 1}).encode()
        self.assertEqual(self.request(address, 'POST', '/items', raw=small + b' ' * (65536-len(small)))[0], 201)
        status, error = self.request(address, 'POST', '/items', raw=b'x' * 65537)
        self.assertEqual(status, 413)
        self.assertIsInstance(error.get('error'), str)
        for raw in [b'\xff', b'[]', b'null', b'"string"', b'1', b'{}', b'{"sku":"x","quantity":1.5}',
                    b'{"sku":"x","quantity":true}', b'{"sku":"x","quantity":0}']:
            with self.subTest(raw=raw):
                status, error = self.request(address, 'POST', '/items', raw=raw)
                self.assertEqual(status, 400)
                self.assertIsInstance(error.get('error'), str)
                self.assertEqual(self.request(address, 'GET', '/health'), (200, {'ok': True}))
        self.assertEqual(self.store.get_item('a')['available'], 1)

    @criterion('boundaries')
    def test_unicode_sql_literals_and_http_encoded_path(self):
        address = self.server()
        for i, sku in enumerate(["x'; DROP TABLE items; --", 'товар/частина + % #?', 'é漢🙂']):
            self.store.add_item(sku, 3)
            self.assertEqual(self.request(address, 'GET', '/items/' + quote(sku, safe='')), (200, {'sku': sku, 'available': 3}))
            r = self.store.reserve(f"k'{i}", sku, 1)
            self.assertEqual(self.store.release(r['reservation_id'])['status'], 'released')
        self.assertEqual(len(self.store.report()['items']), 3)

    @criterion('boundaries')
    def test_cli_parse_errors_json_and_channels(self):
        for args in [('add', '--sku', 'a', '--quantity', 'invalid'),
                     ('release', '--id', 'abc'), ('reserve', '--key', 'k', '--sku', 'a', '--quantity', '0')]:
            result = self.cli(*args)
            with self.subTest(args=args):
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                error = json.loads(result.stderr)
                self.assertEqual(set(error), {'error'})
                self.assertIsInstance(error['error'], str)
        self.assertEqual(self.cli('report').returncode, 0)

    @criterion('properties')
    def test_persisted_cli_http_store_and_release_retry_invariant(self):
        result = self.cli('add', '--sku', 'a', '--quantity', '10')
        self.assertEqual(result.returncode, 0, result.stderr)
        r = self.store.reserve(' k ', ' a ', 3)
        released = self.cli('release', '--id', str(r['reservation_id']))
        self.assertEqual(released.returncode, 0, released.stderr)
        reopened = self.Store(self.db)
        retry = reopened.reserve('k', 'a', 3)
        # Both documented interpretations of ORIGINAL response remain valid.
        self.assertEqual({k: v for k, v in retry.items() if k != 'status'},
                         {k: v for k, v in r.items() if k != 'status'})
        self.assertIn(retry['status'], ['active', 'released'])
        self.assertEqual(reopened.get_item('a')['available'], 10)
        self.assertEqual(reopened.report()['active_reservations'], 0)
        address = self.server()
        self.assertEqual(self.request(address, 'GET', '/report')[1], reopened.report())


SUITES = {'01-easy': IntervalsV2, '02-medium': CacheV2, '03-hard': GraphV2, '04-components': ServiceV2}
