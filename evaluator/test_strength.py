"""Separate optional-test evidence; never mutate a saved candidate snapshot."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from qualify_complex import FILES, MUTANTS, mutated

ROOT = Path(__file__).resolve().parents[1]

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))

def worker(workspace, output):
    sys.path.insert(0, str(workspace))
    loader = unittest.TestLoader(); suite = unittest.TestSuite()
    for p in sorted((workspace / 'tests').rglob('test*.py')):
        if p.name == 'test_public.py' or '__pycache__' in p.parts:
            continue
        import importlib.util
        spec = importlib.util.spec_from_file_location('strength_' + digest(p)[:12], p)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    result = unittest.TestResult(); suite.run(result)
    report = dict(tests_run=result.testsRun, passed=result.wasSuccessful() and not result.skipped,
                  skipped=len(result.skipped), failures=len(result.failures), errors=len(result.errors),
                  detail=[detail[-2000:] for _, detail in result.failures + result.errors])
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')

def probe(workspace, target):
    try:
        r = subprocess.run([sys.executable, '-B', __file__, '--worker', str(workspace), '--output', str(target)],
                           cwd=workspace, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return dict(passed=False, reason='timeout', usable=False)
    if not target.exists():
        return dict(passed=False, reason='import_or_execution_error', detail=r.stderr[-2000:], usable=False)
    report = read(target)
    report['usable'] = report['tests_run'] > 0 and report['skipped'] == 0
    return report

def evaluate(row, folder, archive_files=None):
    task = row['task']; snapshot = Path(row['snapshot'])
    manifest = archive_files if archive_files is not None else read(snapshot.parent / 'candidate-hashes.json')
    serialized = json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode()
    if archive_files is None and hashlib.sha256(serialized).hexdigest() != row['candidate_manifest_sha256']:
        raise ValueError('candidate manifest changed: ' + row['run_id'])
    for name, expected in manifest.items():
        target = (snapshot / name).resolve()
        if not target.is_relative_to(snapshot.resolve()) or (snapshot / name).is_symlink() or digest(target) != expected:
            raise ValueError('candidate changed: ' + row['run_id'] + '/' + name)
    tests = sorted(n for n in manifest if n.startswith('tests/') and Path(n).name.startswith('test')
                   and n.endswith('.py') and Path(n).name != 'test_public.py')
    result = dict(run_id=row['run_id'], task=task, candidate_manifest_sha256=row['candidate_manifest_sha256'],
                  test_files=tests, score=None, status='unavailable', controls={})
    if not tests:
        result.update(score=0, status='no_added_tests'); return result
    candidate = folder / 'candidate'
    shutil.copytree(snapshot, candidate, ignore=shutil.ignore_patterns('__pycache__', '.git'))
    result['candidate_tests'] = probe(candidate, folder / 'candidate-tests.json')
    reference = folder / 'reference'
    shutil.copytree(candidate, reference)
    shutil.copyfile(ROOT / 'evaluator/controls' / task / FILES[task], reference / FILES[task])
    result['reference_tests'] = probe(reference, folder / 'reference-tests.json')
    if not all(result[name].get('passed') and result[name].get('usable') for name in ['candidate_tests', 'reference_tests']):
        result['reason'] = 'Tests must run without skips and pass on both candidate and positive control.'
        return result
    killed = []
    for name in MUTANTS[task]:
        mutant = folder / name; shutil.copytree(reference, mutant)
        (mutant / FILES[task]).write_text(mutated(task, name), encoding='utf-8')
        outcome = probe(mutant, folder / (name + '-tests.json')); result['controls'][name] = outcome
        # A crash/hang is inconclusive evidence; only executed assertion failures kill.
        if not outcome.get('usable') or outcome.get('errors') or outcome.get('skipped'):
            result['reason'] = 'Mutant test execution was inconclusive.'; return result
        if outcome.get('failures', 0) > 0:
            killed.append(name)
    result.update(status='usable', killed=killed, total_mutants=len(MUTANTS[task]), score=100*len(killed)/len(MUTANTS[task]))
    return result

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--worker'); parser.add_argument('--output')
    parser.add_argument('--run-id'); parser.add_argument('--archive', action='store_true'); args = parser.parse_args()
    if args.worker:
        worker(Path(args.worker), Path(args.output)); return
    if args.archive:
        manifests = read(ROOT / 'results/candidate-manifests.json')
        source = read(ROOT / 'results/results.json')['runs']
    else:
        frozen = read(ROOT / 'preparation/freeze.json')
        for name, expected in frozen['hashes'].items():
            if digest(ROOT / name) != expected: raise ValueError('Frozen input changed: ' + name)
        source = [read(p) for p in sorted((ROOT / 'runs').glob('*/result.json'))]
    rows = []
    for row in source:
        if row['task'] in FILES and row.get('snapshot') and row.get('grading'):
            if args.run_id is None or row['run_id'] == args.run_id:
                if args.archive:
                    row = {**row, 'snapshot': str(ROOT / manifests[row['run_id']]['directory'])}
                    candidate = Path(row['snapshot']).resolve()
                    if not candidate.is_relative_to((ROOT / 'candidates').resolve()):
                        raise ValueError('Candidate escaped archive')
                    actual = {p.relative_to(candidate).as_posix() for p in candidate.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
                    if actual != set(manifests[row['run_id']]['files']): raise ValueError('Candidate membership changed')
                rows.append(row)
    if args.run_id and not rows: raise ValueError('Unknown complex-task run_id: ' + args.run_id)
    output = ROOT / 'analysis' / ('complex-test-strength-replay' if args.archive else 'complex-test-strength'); output.mkdir(parents=True, exist_ok=True)
    records = []
    for row in rows:
        folder = output / 'evidence' / (row['run_id'] + '-' + str(uuid.uuid4())); folder.mkdir(parents=True)
        grade = evaluate(row, folder, manifests[row['run_id']]['files'] if args.archive else None); grade['evidence_directory'] = str(folder); records.append(grade)
        print(row['run_id'], grade['status'], grade['score'])
    report = dict(criterion='contract_mutation_sensitivity', experiment_freeze_sha256=None if args.archive else digest(ROOT / 'preparation/freeze.json'),
                  available_snapshots=len(rows), planned_attempts=sum(t["id"] in FILES for t in read(ROOT / "config/experiment.json")["tasks"]) * len(read(ROOT / "config/experiment.json")["models"]) * len(read(ROOT / "config/experiment.json")["reasoning_efforts"]) * read(ROOT / "config/experiment.json")["repetitions"], evaluations=records,
                  note='Separate evidence; no full code-quality score without manual review.')
    if args.archive:
        report.update(archive_results_sha256=digest(ROOT / 'results/results.json'), candidate_manifests_sha256=digest(ROOT / 'results/candidate-manifests.json'))
    (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote {len(rows)} evaluations to {output}')

if __name__ == '__main__':
    main()
