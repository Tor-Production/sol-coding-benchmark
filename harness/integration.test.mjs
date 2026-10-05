import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, readJson, efforts, config } from './common.mjs';
import { runAttempt, preflight } from './run.mjs';
import { Rpc } from './rpc.mjs';
import { gradeWorkspace } from './evaluate.mjs';

const rt = () => readJson(path.join(ROOT, 'preparation/freeze.json')).runtime;
function fixtureFactory(env = {}) {
  return (_rt, options) => new Rpc(process.execPath, [path.join(ROOT, 'harness/fixtures/fake-server.mjs')],
    { ...options, env: { ...options.env, BENCH_FIXTURE_CONTROL: path.join(ROOT, 'evaluator/controls/01-easy/intervals.py'), ...env } });
}
function fixtureOptions(env = {}) {
  const fixtureRoot = fs.mkdtempSync(path.join(ROOT, 'preparation', 'pipeline-fixture-'));
  return { fixtureRoot, options: { rtOverride: rt(), allowWithoutPreflight: true,
    outputRoot: path.join(fixtureRoot, 'runs'), workspaceRoot: path.join(fixtureRoot, 'workspaces'), rpcFactory: fixtureFactory(env) } };
}
const entryFor = effort => ({ index: 0, run_id: `offline-only-${effort}`, task: '01-easy', model: 'gpt-6.1-sol',
  reasoning_effort: effort, repetition: 0, timeout_seconds: 5 });
const rpcMessages = (root, id) => fs.readFileSync(path.join(root, 'runs', id, 'rpc.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);

for (const effort of efforts()) {
  test(`Offline full pipeline ${effort}: one turn, exact usage, snapshot, grading, no overwrite`, async () => {
    const { fixtureRoot, options } = fixtureOptions();
    const entry = entryFor(effort);
    const result = await runAttempt(entry, options);
    assert.equal(result.status, 'completed', result.error);
    assert.equal(result.requested_effort, effort);
    assert.equal(result.effective.effort, effort);
    assert.equal(result.turn_settings.effort, effort);
    assert.equal(result.inference_turns_submitted, 1);
    assert.equal(result.usage.complete, true);
    assert.equal(result.costs.credits_estimated, 0.056);
    assert.equal(result.billed_credits_attributed, null);
    assert.equal(result.invalid_reasons.length, 0);
    assert.ok(result.model_elapsed_seconds >= 0);
    assert.ok(result.first_output_seconds >= 0);
    const grade = gradeWorkspace(entry.task, result.snapshot, path.join(fixtureRoot, 'grade'), rt().python);
    assert.equal(grade.score, 100);
    assert.equal(grade.accepted, true);
    const messages = rpcMessages(fixtureRoot, entry.run_id);
    const outbound = messages.filter(e => e.direction === 'out');
    assert.equal(outbound.filter(e => e.message.method === 'turn/start').length, 1);
    assert.equal(outbound.find(e => e.message.method === 'thread/start').message.params.config.model_reasoning_effort, effort);
    assert.equal(outbound.find(e => e.message.method === 'turn/start').message.params.effort, effort);
    await assert.rejects(runAttempt(entry, options), /already exists/);
    assert.equal(fs.existsSync(path.join(ROOT, 'runs', entry.run_id)), false);
  });
}

test('Wrong effective thread effort stops before any model turn', async () => {
  const { fixtureRoot, options } = fixtureOptions({ BENCH_FIXTURE_WRONG_EFFORT: 'high' });
  const entry = entryFor('max');
  const result = await runAttempt(entry, options);
  assert.equal(result.status, 'harness_failed');
  assert.equal(result.inference_turns_submitted, 0);
  assert.match(result.error, /requested max, got high/);
  assert.equal(rpcMessages(fixtureRoot, entry.run_id).filter(e => e.direction === 'out' && e.message.method === 'turn/start').length, 0);
});

test('Unsupported effort in fresh catalog stops before model spending', async () => {
  const { fixtureRoot, options } = fixtureOptions({ BENCH_FIXTURE_AVAILABLE_EFFORTS: 'max' });
  const result = await runAttempt(entryFor('ultra'), options);
  assert.equal(result.inference_turns_submitted, 0);
  assert.match(result.error, /no longer supports ultra/);
  assert.equal(rpcMessages(fixtureRoot, 'offline-only-ultra').filter(e => e.message.method === 'turn/start').length, 0);
});

test('Effort change during a turn marks the completed attempt invalid', async () => {
  const { options } = fixtureOptions({ BENCH_FIXTURE_WRONG_TURN_EFFORT: 'high' });
  const result = await runAttempt(entryFor('ultra'), options);
  assert.equal(result.status, 'completed');
  assert.ok(result.invalid_reasons.includes('turn_effort_mismatch'));
});

test('Offline preflight covers all eighteen cells without any model turns', async () => {
  const result = await preflight({ rtOverride: rt(), rpcFactory: fixtureFactory() });
  assert.equal(result.verified, true);
  assert.equal(result.simulation, true);
  assert.equal(result.effective_cells.length, 18);
  assert.equal(new Set(result.effective_cells.map(c => `${c.model}/${c.effort}`)).size, 18);
  const messages = fs.readFileSync(path.join(result.evidence, 'rpc.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(messages.filter(e => e.direction === 'out' && e.message.method === 'thread/start').length, 18);
  assert.equal(messages.filter(e => e.message.method === 'turn/start').length, 0);
  await assert.rejects(runAttempt(entryFor('max'), { ...fixtureOptions().options, allowWithoutPreflight: false }), /real Preflight/);
});

for (const { id: task } of config().tasks) {
  test(`Task ${task}: offline simulated turn, protected snapshot and correct grading`, async () => {
    const { fixtureRoot, options } = fixtureOptions({ BENCH_FIXTURE_CONTROL_FOLDER: path.join(ROOT, 'evaluator/controls', task) });
    const entry = { ...entryFor('xhigh'), task, run_id: `offline-${task}-xhigh` };
    const result = await runAttempt(entry, options);
    assert.equal(result.status, 'completed', result.error);
    assert.equal(result.inference_turns_submitted, 1);
    const grade = gradeWorkspace(task, result.snapshot, path.join(fixtureRoot, 'grade'), rt().python);
    assert.equal(grade.score, 100);
    assert.equal(grade.accepted, true);
    assert.equal(grade.protected_files_intact, true);
    if (config().quality_policy.weighted_tasks.includes(task)) assert.equal(Object.keys(grade.acceptance.categories).length, 4);
    assert.equal(fs.existsSync(path.join(ROOT, 'runs', entry.run_id)), false);
  });
}
