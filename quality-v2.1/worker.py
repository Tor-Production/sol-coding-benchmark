"""Run one local evaluation in a disposable copy; emits structured evidence."""
import argparse
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import traceback
import tracemalloc
import unittest

from suite import SUITES


class Recorded(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.records = []
        self.details = {}
        self.statuses = {}

    def startTest(self, test):
        super().startTest(test)
        self.started = time.perf_counter()

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.statuses[test.id()] = 'failed'
        self.details[test.id()] = self._exc_info_to_string(err, test)[-3500:]

    def addError(self, test, err):
        super().addError(test, err)
        self.statuses[test.id()] = 'error'
        self.details[test.id()] = self._exc_info_to_string(err, test)[-3500:]

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.statuses[test.id()] = 'skipped'
        self.details[test.id()] = reason

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err:
            self.statuses[test.id()] = 'failed'
            self.details.setdefault(test.id(), self._exc_info_to_string(err, test)[-3500:])

    def stopTest(self, test):
        name = test.id()
        group = getattr(getattr(test, test._testMethodName, None), 'criterion', 'own_tests')
        status = self.statuses.get(name, 'passed')
        self.records.append({'test': name, 'criterion': group, 'status': status,
                             'passed': status == 'passed', 'seconds': time.perf_counter() - self.started,
                             'detail': self.details.get(name)})
        super().stopTest(test)


def execute_tests(args):
    if args.mode == 'extended':
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SUITES[args.task])
    else:
        suite = unittest.defaultTestLoader.discover(str(args.workspace / 'tests'), pattern='test*.py')
    count = suite.countTestCases()
    result = Recorded()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        suite.run(result)
    return {'tests': result.records, 'count': count, 'passed': sum(r['passed'] for r in result.records),
            'green': count > 0 and result.wasSuccessful() and not result.skipped,
            'loader_errors': unittest.defaultTestLoader.errors}


def perf_workload(task):
    if task == '01-easy':
        merge = importlib.import_module('intervals').merge_intervals
        rows = [[i * 3, i * 3 + 1] for i in reversed(range(30000))]
        def work():
            result = merge(rows)
            assert len(result) == 30000 and result[0] == [0, 1] and result[-1] == [89997, 89998]
        return work, '30,000 disjoint intervals, sorting and merging'
    if task == '02-medium':
        cls = importlib.import_module('cache').TTLCache
        def work():
            c = cls(1000, 10, lambda: 0)
            for i in range(1000):
                c.put(i, i)
            for i in range(3000):
                assert c.get(i % 1000) == i % 1000
            assert len(c) == 1000
        return work, 'capacity 1,000; 1,000 writes + 3,000 live reads'
    if task == '03-hard':
        run = importlib.import_module('dag').run_graph
        tasks = {f'n{i:04}': {'deps': [] if i == 0 else [f'n{i-1:04}'], 'fn': lambda i=i: i} for i in range(1500)}
        def work():
            result = run(tasks, 3)
            assert result['n1499'] == {'status': 'completed', 'value': 1499}
        return work, '1,500-task dependency chain; three worker limit'
    cls = importlib.import_module('reservation.store').Store
    def work():
        with tempfile.TemporaryDirectory() as directory:
            s = cls(str(Path(directory) / 'perf.db'))
            s.add_item('a', 100)
            for i in range(40):
                row = s.reserve(str(i), 'a', 1)
                s.release(row['reservation_id'])
            assert s.report()['items'] == [{'sku': 'a', 'available': 100}]
    return work, 'SQLite init + 40 reserve/release pairs on a fresh database'


def performance(task):
    fn, workload = perf_workload(task)
    fn()  # warm-up, never included in measured samples
    samples = []
    for _ in range(3):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    tracemalloc.start()
    try:
        fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {'workload': workload, 'samples_seconds': samples, 'median_seconds': statistics.median(samples),
            'peak_python_bytes': peak, 'memory_note': 'tracemalloc peak, not RSS or SQLite/native memory'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', required=True, type=Path)
    p.add_argument('--task', required=True, choices=SUITES)
    p.add_argument('--mode', required=True, choices=['extended', 'own', 'perf'])
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    args.workspace = args.workspace.resolve()
    args.output = args.output.resolve()
    os.chdir(args.workspace)
    sys.path.insert(0, str(args.workspace))
    os.environ['PYTHONPATH'] = str(args.workspace)
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    started = time.perf_counter()
    try:
        result = performance(args.task) if args.mode == 'perf' else execute_tests(args)
    except Exception:
        result = {'harness_error': traceback.format_exc()[-3500:]}
    result.update(task=args.task, mode=args.mode, elapsed_seconds=time.perf_counter() - started,
                  python=sys.version, workspace=str(args.workspace))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
