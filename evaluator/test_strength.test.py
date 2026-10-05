import json
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from test_strength import ROOT, evaluate

class StrengthPolicy(unittest.TestCase):
    def fixture(self, own=None):
        folder = Path(tempfile.mkdtemp(prefix='strength-policy-', dir=ROOT / 'preparation'))
        snapshot = folder / 'snapshot'
        shutil.copytree(ROOT / 'tasks/06-transactions', snapshot, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT / 'evaluator/controls/06-transactions/mvcc.py', snapshot / 'mvcc.py')
        if own is not None:
            (snapshot / 'tests/test_added.py').write_text(own, encoding='utf-8')
        manifest = {p.relative_to(snapshot).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(snapshot.rglob('*')) if p.is_file()}
        encoded = json.dumps(manifest, ensure_ascii=False, separators=(',', ':'))
        (folder / 'candidate-hashes.json').write_text(encoded, encoding='utf-8')
        row = dict(task='06-transactions', run_id='offline-policy-only', snapshot=str(snapshot),
                   candidate_manifest_sha256=hashlib.sha256(encoded.encode()).hexdigest())
        evidence = folder / 'evidence'; evidence.mkdir()
        return row, evidence

    def test_no_added_tests_is_zero_only_for_test_strength(self):
        row, folder = self.fixture()
        result = evaluate(row, folder)
        self.assertEqual(result['status'], 'no_added_tests')
        self.assertEqual(result['score'], 0)

    def test_contract_assertions_produce_usable_partial_sensitivity(self):
        row, folder = self.fixture((ROOT / 'tasks/06-transactions/tests/test_public.py').read_text())
        result = evaluate(row, folder)
        self.assertEqual(result['status'], 'usable', result.get('reason'))
        self.assertIn('write_skew', result['killed'])
        self.assertLess(result['score'], 100)
        self.assertGreater(result['score'], 0)

    def test_invalid_tests_are_unavailable(self):
        row, folder = self.fixture('import unittest\nclass Invalid(unittest.TestCase):\n    def test_wrong_contract(self):\n        self.assertEqual(1, 2)\n')
        result = evaluate(row, folder)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['score'])

    def test_empty_test_files_are_unavailable(self):
        row, folder = self.fixture('# No executable assertions\n')
        result = evaluate(row, folder)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['score'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
