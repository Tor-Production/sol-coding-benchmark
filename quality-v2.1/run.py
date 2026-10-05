"""Versioned offline evaluator. Original experiment and snapshots stay untouched."""
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

from mutants import MUTANTS, apply

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUTPUT = ROOT / 'analysis' / 'quality-v2.1'
RUBRIC = json.loads((HERE / 'rubric.json').read_text(encoding='utf-8'))
SOURCE = ROOT / 'results' / 'results.json'
MANIFESTS = ROOT / 'results' / 'candidate-manifests.json'
FREEZE = OUTPUT / 'freeze.json'
TEMP = ROOT / 'tmp' / 'quality-v2.1'
CODE = {'01-easy': ['intervals.py'], '02-medium': ['cache.py'], '03-hard': ['dag.py'],
        '04-components': ['reservation/__init__.py', 'reservation/store.py', 'reservation/http_api.py', 'reservation/__main__.py']}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def manifest_sha(path):
    # Original harness hashes JSON.stringify(manifest), without pretty-print whitespace.
    return hashlib.sha256(json.dumps(read(path), ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.tmp')
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(pending, path)


def archive_manifest(snapshot):
    for entry in read(MANIFESTS).values():
        if (ROOT / entry['directory']).resolve() == snapshot.resolve():
            return entry['files']
    raise ValueError('Unknown archived snapshot: ' + str(snapshot))


def source_rows():
    data = read(SOURCE)
    rows = [r for r in data['runs'] + data.get('historical_runs', [])
            if r['task'] in CODE and r.get('snapshot') and r.get('grading')]
    return sorted([{**r, 'snapshot': str(ROOT / r['snapshot'])} for r in rows], key=lambda r: r['run_id'])


def frozen_inputs():
    # Public manifest mapping is independently bound by the archive verifier.
    # Verify exact file contents and membership before executing candidate code.
    for row in source_rows():
        folder = Path(row['snapshot']).resolve()
        if not folder.is_relative_to((ROOT / 'candidates').resolve()) or folder.is_symlink():
            raise ValueError('Candidate path escaped public archive')
        manifest = archive_manifest(folder)
        actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts}
        if actual != set(manifest):
            raise ValueError('Candidate membership changed: ' + row['run_id'])
        for name, expected in manifest.items():
            target = (folder / name).resolve()
            if not target.is_relative_to(folder) or (folder / name).is_symlink() or sha(target) != expected:
                raise ValueError(f'Candidate changed: {row["run_id"]}/{name}')


def layer_hashes():
    return {p.name: sha(p) for p in sorted(HERE.iterdir()) if p.suffix in ['.py', '.json', '.md']
            and p.name != 'freeze.json'}


def frozen_layer():
    manifest = read(FREEZE)
    if (manifest['files'] != layer_hashes() or manifest['source_sha256'] != sha(SOURCE)
            or manifest['candidate_manifests_sha256'] != sha(MANIFESTS)):
        raise ValueError('Portable quality evaluator or archive changed after local freeze. Use a new output directory.')
    return manifest


def copy_files(source, target, names):
    for name in names:
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, out)


def reference(target, task):
    copy_files(ROOT / 'tasks' / task, target, CODE[task])
    control = ROOT / 'evaluator' / 'controls' / task
    copy_files(control, target, [p.relative_to(control).as_posix() for p in control.rglob('*.py')])
    if task == '02-medium':
        path = target / 'cache.py'
        content = path.read_text(encoding='utf-8')
        path.write_text(content.replace('or not math.isfinite(ttl)', 'or (isinstance(ttl, float) and not math.isfinite(ttl))'), encoding='utf-8')
    if task == '03-hard':
        path = target / 'dag.py'
        content = path.read_text(encoding='utf-8')
        path.write_text('from collections.abc import Mapping\n' + content.replace('isinstance(tasks, dict)', 'isinstance(tasks, Mapping)'), encoding='utf-8')


def worker(python, task, folder, mode, timeout=65):
    result_path = folder / ('_quality_' + mode + '.json')
    args = [python, '-B', str(HERE / 'worker.py'), '--task', task, '--workspace', str(folder),
            '--mode', mode, '--output', str(result_path)]
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', TEMP=str(TEMP), TMP=str(TEMP))
    try:
        completed = subprocess.run(args, cwd=folder, capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {'harness_error': f'{mode} exceeded {timeout} seconds', 'timeout': True}
    if completed.returncode or not result_path.exists():
        return {'harness_error': completed.stderr.decode('utf-8', errors='replace')[-3500:],
                'exit_code': completed.returncode}
    return read(result_path)


def categories(extended):
    groups = {}
    for item in extended.get('tests', []):
        groups.setdefault(item['criterion'], []).append(item)
    return {g: {'passed': sum(t['passed'] for t in tests), 'total': len(tests),
                'score': 100 * sum(t['passed'] for t in tests) / len(tests)} for g, tests in groups.items()}


def expanded_score(extended):
    if extended.get('harness_error'):
        return None
    expected = {'01-easy': 6, '02-medium': 7, '03-hard': 5, '04-components': 8}
    if extended.get('task') in expected and (extended.get('count') != expected[extended['task']]
                                             or len(extended.get('tests', [])) != extended.get('count')):
        return None
    groups = categories(extended)
    weights = RUBRIC['expanded_category_weights']
    denominator = sum(weights[g] for g in groups)
    return sum(weights[g] * item['score'] for g, item in groups.items()) / denominator if denominator else None


def kills(positive, mutated):
    # Loader failures never count as a killed defect. Timeouts are inconclusive.
    if mutated.get('harness_error') or mutated.get('loader_errors'):
        return []
    passed = {t['test'] for t in positive.get('tests', []) if t['passed']}
    return [t['test'] for t in mutated.get('tests', [])
            if t['test'] in passed and t['status'] in ['failed', 'error']]


def static_evidence(folder, task):
    metrics = {'files': [], 'python_lines': 0, 'functions': 0, 'max_function_lines': 0,
               'max_decision_points': 0, 'broad_handlers': [], 'bare_handlers': [], 'imports': []}
    decisions = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)
    for name in CODE[task]:
        source = (folder / name).read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        nodes = list(ast.walk(tree))
        functions = [n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        spans = [n.end_lineno - n.lineno + 1 for n in functions]
        point_counts = [sum(isinstance(x, decisions) for x in ast.walk(n)) for n in functions]
        metrics['files'].append({'path': name, 'lines': len(source.splitlines()), 'sha256': sha(folder / name)})
        metrics['python_lines'] += len(source.splitlines())
        metrics['functions'] += len(functions)
        metrics['max_function_lines'] = max([metrics['max_function_lines']] + spans)
        metrics['max_decision_points'] = max([metrics['max_decision_points']] + point_counts)
        for node in nodes:
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    metrics['bare_handlers'].append(f'{name}:{node.lineno}')
                elif isinstance(node.type, ast.Name) and node.type.id in ['Exception', 'BaseException']:
                    metrics['broad_handlers'].append(f'{name}:{node.lineno} ({node.type.id})')
            if isinstance(node, ast.Import):
                metrics['imports'].extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                metrics['imports'].append(node.module)
    metrics['imports'] = sorted(set(metrics['imports']))
    metrics['scored'] = False
    metrics['note'] = 'Review signals only. Counts include scaffolding; nested scopes included in decision counts. Broad Exception is required by the DAG contract.'
    return metrics


def qualify(python):
    checks = []
    for task in CODE:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            folder = Path(directory)
            reference(folder, task)
            positive = worker(python, task, folder, 'extended')
            entry = {'task': task, 'positive': positive, 'mutants': []}
            if not positive.get('green'):
                raise ValueError(f'Positive reference failed: {task}; {json.dumps(positive, ensure_ascii=False)}')
            for name, *_ in MUTANTS[task]:
                with tempfile.TemporaryDirectory(dir=TEMP) as md:
                    mutated_folder = Path(md)
                    reference(mutated_folder, task)
                    apply(mutated_folder, task, name)
                    result = worker(python, task, mutated_folder, 'extended')
                    detected = kills(positive, result)
                    entry['mutants'].append({'name': name, 'detected_by': detected, 'evidence': result})
                    if not detected:
                        raise ValueError(f'New checks did not detect seeded defect: {task}/{name}')
            checks.append(entry)
    write(OUTPUT / 'qualification.json', {'at': datetime.now(timezone.utc).isoformat(), 'checks': checks,
                                         'evaluator_hashes': layer_hashes(), 'all_passed': True})
    print('Qualification: positive references passed; all 20 seeded defects detected.', flush=True)


def freeze(python):
    if FREEZE.exists():
        frozen_layer()
        print('Existing local quality-v2.1 replay freeze verified.')
        return
    frozen_inputs()
    q = read(OUTPUT / 'qualification.json')
    if not q['all_passed'] or q['evaluator_hashes'] != layer_hashes():
        raise ValueError('Run qualification with the current evaluator first.')
    write(FREEZE, {'version': RUBRIC['evaluation_id'], 'at': datetime.now(timezone.utc).isoformat(),
                   'files': layer_hashes(), 'source_sha256': sha(SOURCE),
                   'candidate_manifests_sha256': sha(MANIFESTS), 'python': python,
                   'python_version': sys.version, 'qualification_sha256': sha(OUTPUT / 'qualification.json')})
    print('Portable replay inputs frozen; original archived grades remain untouched.')


def added_tests(folder):
    return sorted(p.relative_to(folder).as_posix() for p in (folder / 'tests').rglob('*.py')
                  if p.name != 'test_public.py' and '__pycache__' not in p.parts)


def evaluate_row(row, python, blind_id):
    snapshot = Path(row['snapshot'])
    task = row['task']
    with tempfile.TemporaryDirectory(dir=TEMP) as directory:
        folder = Path(directory)
        copy_files(snapshot, folder, archive_manifest(snapshot))
        extended = worker(python, task, folder, 'extended')
        perf = worker(python, task, folder, 'perf')
        added = added_tests(folder)
        mutation = {'files': added, 'seeded_defects': len(MUTANTS[task]), 'killed': 0, 'score': None, 'mutants': []}
        # A separate copy prevents own tests from contaminating performance/new checks.
        if added:
            with tempfile.TemporaryDirectory(dir=TEMP) as own_dir:
                own_folder = Path(own_dir)
                copy_files(snapshot, own_folder, archive_manifest(snapshot))
                (own_folder / 'tests' / 'test_public.py').unlink(missing_ok=True)
                baseline = worker(python, task, own_folder, 'own', timeout=45)
                mutation['candidate_baseline'] = baseline
                with tempfile.TemporaryDirectory(dir=TEMP) as ref_dir:
                    ref_folder = Path(ref_dir)
                    reference(ref_folder, task)
                    copy_files(snapshot, ref_folder, added)
                    positive = worker(python, task, ref_folder, 'own', timeout=45)
                mutation['reference_baseline'] = positive
                if baseline.get('green') and positive.get('green'):
                    for name, *_ in MUTANTS[task]:
                        with tempfile.TemporaryDirectory(dir=TEMP) as md:
                            mf = Path(md)
                            reference(mf, task)
                            copy_files(snapshot, mf, added)
                            apply(mf, task, name)
                            mutated = worker(python, task, mf, 'own', timeout=30)
                            detected = kills(positive, mutated)
                            mutation['mutants'].append({'name': name, 'killed': bool(detected),
                                                        'detected_by': detected, 'evidence': mutated})
                    mutation['killed'] = sum(m['killed'] for m in mutation['mutants'])
                    if not any(m['evidence'].get('harness_error') or m['evidence'].get('loader_errors') for m in mutation['mutants']):
                        mutation['score'] = 100 * mutation['killed'] / mutation['seeded_defects']
                else:
                    mutation['unavailable_reason'] = 'Own tests not green on candidate or positive reference; see test-level evidence. No synthetic zero.'
        else:
            mutation['score'] = 0
            mutation['note'] = 'No candidate-added tests; original correctness unchanged.'
        original = row['grading']['score']
        expanded = expanded_score(extended)
        automatic = None
        if expanded is not None and mutation['score'] is not None:
            automatic = (30 * original + 35 * expanded + 20 * mutation['score']) / 85
        return {'run_id': row['run_id'], 'blind_id': blind_id, 'task': task, 'model': row['model'],
                'reasoning_effort': row['reasoning_effort'], 'repetition': row['repetition'], 'source': row.get('source', 'archive'),
                'original_score': original, 'expanded_score': expanded, 'categories': categories(extended),
                'extended': extended, 'test_effectiveness': mutation, 'performance': perf,
                'static': static_evidence(snapshot, task), 'automated_evidence_score': automatic,
                'overall_quality_score': None, 'manual_review': None,
                'snapshot_manifest_sha256': row['candidate_manifest_sha256'],
                'model_seconds': row['model_elapsed_seconds'], 'credits_estimated': (row.get('costs') or {}).get('credits_estimated')}


def summarize(rows):
    output = []
    for model in ['gpt-5.6-sol', 'gpt-6-sol', 'gpt-6.1-sol']:
        for level in ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']:
            group = [r for r in rows if r['model'] == model and r['reasoning_effort'] == level]
            if not group:
                continue
            def complete_mean(values):
                return statistics.mean(values) if len(values) == 8 and all(v is not None for v in values) else None
            output.append({'model': model, 'reasoning_effort': level, 'attempts': len(group),
                           'original_score': complete_mean([r['original_score'] for r in group]),
                           'expanded_score': complete_mean([r['expanded_score'] for r in group]),
                           'added_test_score': complete_mean([r['test_effectiveness']['score'] for r in group]),
                           'added_test_coverage': sum(r['test_effectiveness']['score'] is not None for r in group),
                           'automated_evidence_score': complete_mean([r['automated_evidence_score'] for r in group]),
                           'overall_quality_score': complete_mean([r.get('overall_quality_score') for r in group]),
                           'expanded_checks_passed': sum(r['extended'].get('passed', 0) for r in group),
                           'expanded_checks_total': sum(r['extended'].get('count', 0) for r in group),
                           'all_expanded_passed_attempts': sum(r['extended'].get('green', False) for r in group)})
    return output


def aggregate():
    rows = [read(OUTPUT / 'runs' / (row['run_id'] + '.json')) for row in source_rows()
            if (OUTPUT / 'runs' / (row['run_id'] + '.json')).exists()]
    template = OUTPUT / 'manual-review.json'
    if template.exists():
        manual = read(template)
        if manual.get('evaluation_id') != RUBRIC['evaluation_id']:
            raise ValueError('Manual review belongs to another evaluation.')
        seen = set()
        by_blind = {r['blind_id']: r for r in rows}
        for entry in manual['reviews']:
            blind_id = entry['blind_id']
            if blind_id in seen or blind_id not in by_blind:
                raise ValueError(f'Duplicate/unknown manual-review ID: {blind_id}')
            seen.add(blind_id)
            if not any(v is not None for v in entry['scores'].values()):
                continue
            if not isinstance(entry.get('reviewer'), str) or not entry['reviewer'].strip():
                raise ValueError(f'Reviewer required for {blind_id}')
            weighted, denominator = 0, 0
            for criterion, config in RUBRIC['manual_review']['criteria'].items():
                value = entry['scores'].get(criterion)
                evidence = entry['evidence'].get(criterion)
                if type(value) is not int or value not in range(5) or not isinstance(evidence, str) or not evidence.strip():
                    raise ValueError(f'Complete 0-4 scores and source/line evidence required: {blind_id}/{criterion}')
                weighted += value / 4 * config['weight']
                denominator += config['weight']
            row = by_blind[blind_id]
            row['manual_review'] = {**entry, 'score': 100 * weighted / denominator}
            if row['automated_evidence_score'] is not None:
                row['overall_quality_score'] = (30 * row['original_score'] + 35 * row['expanded_score'] +
                                                20 * row['test_effectiveness']['score'] + 15 * row['manual_review']['score']) / 100
    data = {'evaluation_id': RUBRIC['evaluation_id'], 'generated_at': datetime.now(timezone.utc).isoformat(),
            'source_sha256': sha(SOURCE), 'criteria_freeze_sha256': sha(FREEZE),
            'rubric': RUBRIC, 'runs': rows, 'summary': summarize(rows)}
    write(OUTPUT / 'results.json', data)
    if not template.exists() and len(rows) == 144:
        write(template, {'schema_version': 1, 'evaluation_id': RUBRIC['evaluation_id'],
                         'instructions': 'Review blind copies first. Each 0-4 criterion needs concrete source/line evidence. Leave unknown scores null.',
                         'reviews': [{'blind_id': r['blind_id'], 'reviewer': None,
                                      'scores': {key: None for key in RUBRIC['manual_review']['criteria']},
                                      'evidence': {key: None for key in RUBRIC['manual_review']['criteria']}} for r in rows]})
    return data


def evaluate(python, selected=None):
    frozen_inputs()
    manifest = frozen_layer()
    if manifest['python'] != python or manifest['python_version'] != sys.version:
        raise ValueError('Use the frozen Python executable.')
    # Performance is sequential and measured in the same interpreter as original candidates.
    refs = OUTPUT / 'reference-performance.json'
    if not refs.exists():
        reference_perf = {}
        for task in CODE:
            with tempfile.TemporaryDirectory(dir=TEMP) as rd:
                folder = Path(rd)
                reference(folder, task)
                reference_perf[task] = worker(python, task, folder, 'perf')
        write(refs, reference_perf)
    rows = source_rows()
    if selected and not any(row['run_id'] == selected for row in rows):
        raise ValueError('Unknown archived original-task run ID: ' + selected)
    for index, row in enumerate(rows, 1):
        if selected and row['run_id'] != selected:
            continue
        destination = OUTPUT / 'runs' / (row['run_id'] + '.json')
        if destination.exists():
            if read(destination)['criteria_freeze_sha256'] != sha(FREEZE):
                raise ValueError('Cached evaluation belongs to another criteria version.')
            continue
        result = evaluate_row(row, python, f'Q{index:03}')
        result['criteria_freeze_sha256'] = sha(FREEZE)
        write(destination, result)
        blind = OUTPUT / 'blind-review' / result['blind_id']
        review_files = CODE[row['task']] + added_tests(Path(row['snapshot']))
        if (Path(row['snapshot']) / 'README.md').exists():
            review_files.append('README.md')
        copy_files(Path(row['snapshot']), blind, review_files)
        copy_files(ROOT / 'tasks' / row['task'], blind, ['TASK.md'])
        write(blind / 'review-signals.json', result['static'])
        print(f'{index}/{len(rows)} {row["run_id"]}: expanded={result["expanded_score"]}; tests={result["test_effectiveness"]["score"]}', flush=True)
    frozen_inputs()
    aggregate()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['qualify', 'freeze', 'evaluate', 'report', 'status'])
    parser.add_argument('--run-id')
    parser.add_argument('--output', type=Path, help='New local replay output directory; never the archived results directory')
    args = parser.parse_args()
    global OUTPUT, TEMP, FREEZE
    if args.output:
        OUTPUT = args.output.resolve()
        if OUTPUT == (ROOT / 'results').resolve() or OUTPUT.is_relative_to((ROOT / 'results').resolve()) or OUTPUT.is_relative_to((ROOT / 'candidates').resolve()):
            raise ValueError('Output must not overwrite the archived results or candidates')
        TEMP = OUTPUT / 'tmp'
        FREEZE = OUTPUT / 'freeze.json'
    TEMP.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    if args.action == 'qualify':
        qualify(python)
    elif args.action == 'freeze':
        freeze(python)
    elif args.action == 'evaluate':
        evaluate(python, args.run_id)
    elif args.action == 'report':
        frozen_layer()
        aggregate()
    else:
        count = len(list((OUTPUT / 'runs').glob('*.json')))
        print(f'Offline quality-v2.1: {count}/{len(source_rows())} archived original-task snapshots evaluated; no inference calls.')


if __name__ == '__main__':
    main()
