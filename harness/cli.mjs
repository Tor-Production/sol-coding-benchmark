import fs from 'node:fs';
import path from 'node:path';
import { ROOT, config, readJson, writeJson, schedule, runtime, experimentHashes, verifyFreeze, command, timestamp, efforts } from './common.mjs';
import { qualify } from './evaluate.mjs';
import { preflight, runAttempt, gradeAttempt } from './run.mjs';
import { report } from './report.mjs';

const action = (process.argv[2] ?? 'status').toLowerCase();
const execute = process.argv.includes('--execute');
const runIdIndex = process.argv.indexOf('--run-id');
const runId = runIdIndex < 0 ? null : process.argv[runIdIndex + 1];
const effortIndex = process.argv.indexOf('--effort');
const effort = effortIndex < 0 ? null : process.argv[effortIndex + 1];
const selectedSchedule = () => schedule().filter(e => effort === null || e.reasoning_effort === effort);
const findEntry = () => {
  const entry = schedule().find(e => e.run_id === runId);
  if (!entry || (effort !== null && entry.reasoning_effort !== effort)) throw new Error('Use a matching run_id from preparation/schedule.json');
  return entry;
};
let lock;
function acquireLock() {
  const target = path.join(ROOT, '.runtime', 'run.lock');
  fs.mkdirSync(path.dirname(target), { recursive: true });
  try { lock = fs.openSync(target, 'wx'); }
  catch { throw new Error('A benchmark process already holds .runtime/run.lock. If it crashed, verify its PID is gone before removing this exact file.'); }
  fs.writeFileSync(lock, JSON.stringify({ pid: process.pid, started_at: timestamp(), action }));
}
function releaseLock() {
  if (lock !== undefined) {
    fs.closeSync(lock);
    fs.unlinkSync(path.join(ROOT, '.runtime', 'run.lock'));
    lock = undefined;
  }
}
try {
  if (effortIndex >= 0 && !efforts().includes(effort)) throw new Error('Use a configured --effort: low, medium, high, xhigh, max, ultra');
  if (effort !== null && !['run', 'runall', 'status'].includes(action)) throw new Error('--effort is supported only for Run, RunAll and Status');
  if (['run', 'runall'].includes(action) && !execute) throw new Error('Measured model execution requires --execute (PowerShell: -Execute).');
  if (['run', 'runall', 'prepare', 'preflight', 'grade'].includes(action)) acquireLock();
  if (action === 'prepare') {
    if (fs.existsSync(path.join(ROOT, 'runs')) && fs.readdirSync(path.join(ROOT, 'runs')).length) throw new Error('Measured attempts exist. Prepare a new experiment directory instead of changing this one.');
    const rt = runtime();
    const tests = command(process.execPath, ['--test', path.join(ROOT, 'harness/telemetry.test.mjs'), path.join(ROOT, 'harness/rpc.test.mjs'), path.join(ROOT, 'harness/matrix.test.mjs')], { timeout: 60000 });
    fs.mkdirSync(path.join(ROOT, 'preparation'), { recursive: true });
    fs.writeFileSync(path.join(ROOT, 'preparation/unit-tests.txt'), tests.stdout + tests.stderr);
    if (tests.status !== 0) throw new Error('Harness tests failed; see preparation/unit-tests.txt');
    const qualified = qualify(rt.python);
    const complexQualification = command(rt.python, ['-B', path.join(ROOT, 'evaluator/qualify_complex.py'), '--output', path.join(ROOT, 'preparation/complex-qualification.json')], { timeout: 180000 });
    fs.writeFileSync(path.join(ROOT, 'preparation/complex-qualification.txt'), complexQualification.stdout + complexQualification.stderr);
    if (complexQualification.status !== 0) throw new Error('Complex evaluator qualification failed; see preparation/complex-qualification.txt');
    const strengthTests = command(rt.python, ['-B', path.join(ROOT, 'evaluator/test_strength.test.py')], { timeout: 60000 });
    fs.writeFileSync(path.join(ROOT, 'preparation/test-strength-tests.txt'), strengthTests.stdout + strengthTests.stderr);
    if (strengthTests.status !== 0) throw new Error('Test-strength policy verification failed; see preparation/test-strength-tests.txt');
    writeJson(path.join(ROOT, 'preparation/schedule.json'), schedule());
    writeJson(path.join(ROOT, 'preparation/freeze.json'), { at: timestamp(), runtime: rt, hashes: experimentHashes(),
      qualification: qualified.folder, total_acceptance_checks: qualified.tasks.reduce((a, t) => a + t.checks, 0),
      total_scored_checks: qualified.tasks.reduce((a, t) => a + t.scored_checks, 0) });
    const pipeline = command(process.execPath, ['--test', path.join(ROOT, 'harness/integration.test.mjs')], { timeout: 90000 });
    fs.writeFileSync(path.join(ROOT, 'preparation/pipeline-test.txt'), pipeline.stdout + pipeline.stderr);
    if (pipeline.status !== 0) {
      fs.unlinkSync(path.join(ROOT, 'preparation/freeze.json'));
      throw new Error('Offline pipeline test failed; see preparation/pipeline-test.txt');
    }
    report();
    console.log(`Prepared ${schedule().length} attempts; ${qualified.tasks.reduce((a, t) => a + t.checks, 0)} diagnostic checks, ${qualified.tasks.reduce((a, t) => a + t.scored_checks, 0)} scored. No model inference submitted.`);
  } else if (action === 'preflight') {
    const result = await preflight();
    console.log(JSON.stringify({ verified: result.verified, requested: result.requested, inference_requests: 0 }, null, 2));
  } else if (action === 'run' || action === 'runall') {
    const selected = action === 'run' ? [findEntry()] : selectedSchedule();
    const rt = verifyFreeze().runtime;
    for (const entry of selected) {
      const resultPath = path.join(ROOT, 'runs', entry.run_id, 'result.json');
      if (action === 'runall' && fs.existsSync(resultPath)) {
        const previous = readJson(resultPath);
        if (['initializing', 'running'].includes(previous.status)) throw new Error(`Interrupted attempt ${entry.run_id}; inspect its process and evidence before continuation.`);
        if (!previous.grading && previous.snapshot) gradeAttempt(entry, rt.python);
        console.log(`Preserved existing attempt: ${entry.run_id}`);
        continue;
      }
      console.log(`Starting ${selected.indexOf(entry) + 1}/${selected.length}: ${entry.run_id}`);
      const result = await runAttempt(entry);
      const graded = result.snapshot ? gradeAttempt(entry, rt.python) : result;
      report();
      console.log(`${entry.run_id}: ${result.status}; quality=${graded.grading?.score ?? 'unknown'}; API_USD=${result.costs?.api_usd_estimated ?? 'unknown'}; seconds=${result.model_elapsed_seconds ?? 'unknown'}`);
      if (result.status === 'harness_failed' || graded.invalid_reasons.length) throw new Error('Stopped after a harness/protocol failure; preserve evidence and investigate before more model spending.');
    }
    report();
  } else if (action === 'grade') {
    const result = gradeAttempt(findEntry(), verifyFreeze().runtime.python);
    report();
    console.log(JSON.stringify(result.grading, null, 2));
  } else if (action === 'report') {
    const result = report();
    console.log(`Reports: ${result.reports}`);
  } else if (action === 'status') {
    const entries = selectedSchedule();
    const rows = entries.map(e => {
      const p = path.join(ROOT, 'runs', e.run_id, 'result.json');
      return { ...e, status: fs.existsSync(p) ? readJson(p).status : 'pending' };
    });
    const finished = r => !['pending', 'initializing', 'running'].includes(r.status);
    const cells = config().models.flatMap(model => (effort ? [effort] : efforts()).map(reasoning_effort => {
      const selected = rows.filter(r => r.model === model && r.reasoning_effort === reasoning_effort);
      return { model, reasoning_effort, planned: selected.length, finished: selected.filter(finished).length,
        pending: selected.filter(r => r.status === 'pending').length,
        failed: selected.filter(r => finished(r) && r.status !== 'completed').length };
    }));
    console.log(JSON.stringify({ experiment: config().experiment_id, planned_attempts: entries.length,
      finished_attempts: rows.filter(finished).length,
      cells, active_attempts: rows.filter(r => ['initializing', 'running'].includes(r.status)),
      schedule_file: path.join(ROOT, 'preparation/schedule.json') }, null, 2));
  } else throw new Error(`Unknown action: ${action}`);
} catch (e) {
  console.error(e.message);
  process.exitCode = 1;
} finally {
  releaseLock();
}
