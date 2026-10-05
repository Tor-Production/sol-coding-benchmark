import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { ROOT, config, command, copyTree, hashes, writeJson } from './common.mjs';
import { applyQualityPolicy } from './quality.mjs';

export function gradeWorkspace(task, workspace, outputDir, python) {
  fs.mkdirSync(outputDir, { recursive: true });
  const output = path.join(outputDir, 'acceptance.json');
  const started = performance.now();
  const checked = command(python, ['-B', path.join(ROOT, config().quality_policy?.weighted_tasks?.includes(task) ? 'evaluator/complex_check.py' : 'evaluator/check.py'), '--task', task, '--workspace', workspace, '--output', output],
    { cwd: workspace, timeout: config().execution.grading_timeout_seconds * 1000, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } });
  fs.writeFileSync(path.join(outputDir, 'acceptance.stdout.txt'), checked.stdout);
  fs.writeFileSync(path.join(outputDir, 'acceptance.stderr.txt'), checked.stderr);
  if (!fs.existsSync(output)) throw new Error(`Evaluator returned no report (${checked.status}): ${checked.stderr.slice(-1500)}`);
  const acceptance = JSON.parse(fs.readFileSync(output, 'utf8'));
  const publicResult = command(python, ['-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_public.py', '-v'],
    { cwd: workspace, timeout: config().execution.grading_timeout_seconds * 1000, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } });
  fs.writeFileSync(path.join(outputDir, 'public.stdout.txt'), publicResult.stdout);
  fs.writeFileSync(path.join(outputDir, 'public.stderr.txt'), publicResult.stderr);
  const baseline = hashes(path.join(ROOT, 'tasks', task));
  const candidate = hashes(workspace);
  const protectedFiles = Object.keys(baseline).filter(p => p === 'TASK.md' || p === 'AGENTS.md' || p.startsWith('tests/'));
  const modifiedProtected = protectedFiles.filter(p => baseline[p] !== candidate[p]);
  return applyQualityPolicy(task, { acceptance, public_passed: publicResult.status === 0, protected_files_intact: modifiedProtected.length === 0,
    modified_protected_files: modifiedProtected, score: acceptance.score,
    accepted: acceptance.accepted && publicResult.status === 0 && modifiedProtected.length === 0,
    grading_seconds: (performance.now() - started) / 1000 });
}

export function qualify(python) {
  const folder = path.join(ROOT, 'preparation', 'qualification', crypto.randomUUID());
  const results = [];
  for (const task of config().tasks) {
    const baseline = path.join(folder, task.id, 'baseline');
    copyTree(path.join(ROOT, 'tasks', task.id), baseline);
    const baselineResult = gradeWorkspace(task.id, baseline, path.join(folder, task.id, 'baseline-grade'), python);
    const positive = path.join(folder, task.id, 'positive');
    copyTree(path.join(ROOT, 'tasks', task.id), positive);
    copyTree(path.join(ROOT, 'evaluator', 'controls', task.id), positive);
    const positiveResult = gradeWorkspace(task.id, positive, path.join(folder, task.id, 'positive-grade'), python);
    const mutant = path.join(folder, task.id, 'mutant');
    copyTree(positive, mutant);
    const mutations = {
      '01-easy': ['intervals.py', 'lo <= result[-1][1] + 1', 'lo <= result[-1][1]'],
      '02-medium': ['cache.py', 'now >= expiry', 'now > expiry'],
      '03-hard': ['dag.py', 'max_workers=max_workers', 'max_workers=1'],
      '04-components': ['reservation/store.py', 'if row["status"] == "active":', 'if True:'],
      '05-optimizer': ['optimizer.py', 'if any(x not in chosen for x in p["requires"]):', 'if False:'],
      '06-transactions': ['mvcc.py', 'key in self._reads or key in self._writes', 'key in self._writes']
    };
    const [file, before, after] = mutations[task.id];
    const source = fs.readFileSync(path.join(mutant, file), 'utf8');
    if (!source.includes(before)) throw new Error(`Mutant anchor missing: ${task.id}`);
    fs.writeFileSync(path.join(mutant, file), source.replace(before, after));
    const mutantResult = gradeWorkspace(task.id, mutant, path.join(folder, task.id, 'mutant-grade'), python);
    const verified = baselineResult.score === 0 && !baselineResult.accepted && positiveResult.score === 100
      && positiveResult.accepted && !mutantResult.accepted && mutantResult.score < 100;
    results.push({ task: task.id, baseline_score: baselineResult.score, positive_score: positiveResult.score,
      public_positive_passed: positiveResult.public_passed, mutant_score: mutantResult.score,
      checks: positiveResult.acceptance.total, scored_checks: positiveResult.scored_checks, verified });
    console.log(`Qualification ${task.id}: baseline ${baselineResult.score}, reference ${positiveResult.score}, mutant ${mutantResult.score}`);
  }
  const result = { at: new Date().toISOString(), folder, verified: results.every(r => r.verified), tasks: results };
  writeJson(path.join(ROOT, 'preparation/qualification-latest.json'), result);
  if (!result.verified) throw new Error('Evaluator qualification failed; see preparation/qualification-latest.json');
  return result;
}
