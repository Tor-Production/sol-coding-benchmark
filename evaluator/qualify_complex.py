"""Reject plausible defective controls before any measured inference."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTANTS = {
    '05-optimizer': {
        'missing_dependency': ('if any(x not in chosen for x in p["requires"]):', 'if False:'),
        'one_sided_conflict': ('conflicts[indexes[y]] |= 1 << indexes[x]', 'pass # missed reverse conflict'),
        'ignored_required': ('if name not in must:', 'if True:'),
        'wrong_cost_tie': ('key = (-value, tuple(spent), tuple(ids))', 'key = (-value, tuple(reversed(spent)), tuple(ids))'),
        'wrong_id_tie': ('ids = sorted(chosen)', 'ids = sorted(chosen, reverse=True)'),
    },
    '06-transactions': {
        'write_skew': ('key in self._reads or key in self._writes', 'key in self._writes'),
        'no_phantom_check': (' or any(key.startswith(p) for p in self._ranges)', ''),
        'read_current_state': ('self._writes.get(name, self._snapshot.get(name))', 'self._writes.get(name, self._engine._data.get(name))'),
        'forget_reads_on_rollback': ('self._writes = dict(self._points[i][1])', 'self._writes = dict(self._points[i][1]); self._reads.clear(); self._ranges.clear()'),
        'recovery_accepts_gaps': ("r['version'] != version + 1", "r['version'] <= version"),
        'checkpoint_alias': ('data=dict(sorted(self._data.items()))', 'data=self._data'),
    },
}
FILES = {'05-optimizer': 'optimizer.py', '06-transactions': 'mvcc.py'}

def mutated(task, name):
    text = (ROOT / 'evaluator/controls' / task / FILES[task]).read_text(encoding='utf-8')
    before, after = MUTANTS[task][name]
    if text.count(before) != 1:
        raise ValueError((task, name, 'mutation anchor is not unique'))
    return text.replace(before, after)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    args = parser.parse_args()
    folder = ROOT / 'preparation' / ('complex-qualification-' + str(uuid.uuid4()))
    results = []
    for task, mutants in MUTANTS.items():
        for name in ['reference', 'stub'] + list(mutants):
            workspace = folder / task / name; workspace.mkdir(parents=True)
            source = ROOT / ('tasks' if name == 'stub' else 'evaluator/controls') / task / FILES[task]
            if name in mutants:
                (workspace / FILES[task]).write_text(mutated(task, name), encoding='utf-8')
            else:
                shutil.copyfile(source, workspace / FILES[task])
            output = workspace.parent / (name + '-grade.json')
            command = [sys.executable, '-B', str(ROOT / 'evaluator/complex_check.py'), '--task', task,
                       '--workspace', str(workspace), '--output', str(output)]
            r = subprocess.run(command, capture_output=True, text=True, timeout=90)
            if not output.exists():
                raise RuntimeError(r.stderr[-2000:])
            grade = json.loads(output.read_text())
            verified = (grade['accepted'] and grade['score'] == 100) if name == 'reference' else (not grade['accepted'] and grade['score'] < 100)
            if name == 'stub': verified = verified and grade['score'] == 0
            results.append(dict(task=task, control=name, score=grade['score'], verified=verified,
                                failed_checks=[t['test'] for t in grade['tests'] if not t['passed']],
                                source_sha256=hashlib.sha256((workspace / FILES[task]).read_bytes()).hexdigest()))
            print(f'{task} / {name}: {grade["score"]:.2f}, verified={verified}')
    report = dict(verified=all(r['verified'] for r in results), controls=results, evidence_directory=str(folder))
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    raise SystemExit(0 if report['verified'] else 1)

if __name__ == '__main__':
    main()
