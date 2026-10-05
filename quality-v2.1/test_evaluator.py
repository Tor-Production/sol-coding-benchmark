"""Meaningful checks of scoring and failure classification, not candidate code."""
import tempfile
from pathlib import Path
import unittest

import run
from worker import Recorded


class EvaluationPolicy(unittest.TestCase):
    def test_category_weighting_is_not_test_count_weighting(self):
        extended = {'tests': [{'criterion': 'properties', 'passed': True}] * 9 +
                             [{'criterion': 'boundaries', 'passed': False}]}
        self.assertAlmostEqual(run.expanded_score(extended), 100 * 40 / 70)

    def test_missing_or_timed_out_evaluation_is_not_perfect_or_na(self):
        self.assertIsNone(run.expanded_score({'harness_error': 'timeout'}))
        self.assertIsNone(run.expanded_score({'tests': []}))
        self.assertIsNone(run.expanded_score({'task': '01-easy', 'count': 5, 'tests': []}))

    def test_only_a_previously_passing_test_can_kill_a_mutant(self):
        positive = {'tests': [{'test': 'a', 'passed': True}, {'test': 'b', 'passed': False}]}
        mutated = {'tests': [{'test': 'a', 'status': 'failed'}, {'test': 'b', 'status': 'failed'}]}
        self.assertEqual(run.kills(positive, mutated), ['a'])
        self.assertEqual(run.kills(positive, {**mutated, 'loader_errors': ['import failure']}), [])
        self.assertEqual(run.kills(positive, {'harness_error': 'timeout'}), [])

    def test_subtest_failure_marks_the_parent_failed(self):
        class Cases(unittest.TestCase):
            def test_case(self):
                with self.subTest(i=1):
                    self.assertEqual(1, 2)
        result = Recorded()
        unittest.defaultTestLoader.loadTestsFromTestCase(Cases).run(result)
        self.assertFalse(result.records[0]['passed'])
        self.assertEqual(result.records[0]['status'], 'failed')

    def test_missing_attempt_never_creates_complete_group_average(self):
        rows = [{'model': 'gpt-6-sol', 'reasoning_effort': 'low', 'original_score': 100,
                 'expanded_score': 100, 'test_effectiveness': {'score': 100},
                 'automated_evidence_score': 100, 'extended': {'passed': 1, 'count': 1, 'green': True}}] * 7
        summary = run.summarize(rows)[0]
        self.assertIsNone(summary['expanded_score'])
        self.assertIsNone(summary['automated_evidence_score'])

    def test_supplied_public_tests_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tests').mkdir()
            (root / 'tests/test_public.py').write_text('')
            (root / 'tests/test_extra.py').write_text('')
            self.assertEqual(run.added_tests(root), ['tests/test_extra.py'])

    def test_corrected_references_cover_valid_large_ttl_and_readonly_mapping(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run.reference(root, '02-medium')
            run.reference(root, '03-hard')
            code = "from cache import TTLCache; from dag import run_graph; from types import MappingProxyType; c=TTLCache(1,10**400,lambda:0); c.put('a',1); assert c.get('a')==1; assert run_graph(MappingProxyType({}))=={}"
            result = subprocess.run([sys.executable, '-B', '-c', code], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
