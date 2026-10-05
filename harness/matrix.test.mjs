import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { ROOT, config, schedule, efforts, command, readJson } from './common.mjs';
import { aggregate } from './report.mjs';
import { applyQualityPolicy } from './quality.mjs';

test('216 fresh unique attempts: six tasks, exact three models, six efforts, two repetitions', () => {
  const cfg = config(), entries = schedule();
  assert.deepEqual(cfg.models, ['gpt-5.6-sol', 'gpt-6-sol', 'gpt-6.1-sol']);
  assert.deepEqual(efforts(), ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']);
  assert.equal(cfg.tasks.length, 6);
  assert.equal(cfg.repetitions, 2);
  assert.equal(cfg.speed, 'standard');
  assert.equal(cfg.historical_baseline, undefined);
  assert.equal(cfg.new_task_ids, undefined);
  assert.equal(entries.length, 216);
  assert.equal(new Set(entries.map(e => e.run_id)).size, 216);
  for (const effort of efforts()) {
    assert.equal(entries.filter(e => e.reasoning_effort === effort).length, 36);
    for (const model of cfg.models) for (const { id: task } of cfg.tasks) {
      assert.deepEqual(entries.filter(e => e.model === model && e.task === task && e.reasoning_effort === effort).map(e => e.repetition), [1, 2]);
    }
  }
  for (const { id: task } of cfg.tasks) {
    const cells = rep => entries.filter(e => e.task === task && e.repetition === rep).map(e => `${e.model}/${e.reasoning_effort}`);
    assert.deepEqual(cells(2), cells(1).toReversed());
  }
  assert.throws(() => efforts({ reasoning_efforts: ['low', 'low'] }), /unique/);
  assert.throws(() => efforts({ reasoning_efforts: ['ligh'] }), /supported/);
});

test('An empty fresh report cannot import archived evidence or invent a winner', () => {
  const summaries = aggregate([]);
  assert.equal(summaries.length, 18);
  assert.ok(summaries.every(s => s.expected === 12 && s.attempted === 0 && !s.full_quality_coverage));
  assert.ok(summaries.every(s => s.quality_macro_mean === null && s.api_usd_total === null));
  const result = command(process.execPath, [path.join(ROOT, 'harness/cli.mjs'), 'report']);
  assert.equal(result.status, 0, result.stderr);
  const report = readJson(path.join(ROOT, 'analysis/results.json'));
  assert.equal(report.runs.length, 216);
  assert.ok(report.runs.every(r => r.status === 'pending'));
  assert.equal(report.historical_runs, undefined);
});

test('Complex weighted scores are preserved rather than replaced by raw test counts', () => {
  const grade = { score: 83.333333, accepted: false, public_passed: true, protected_files_intact: true,
    acceptance: { score: 83.333333, tests: [{ test: 'validation', passed: false }, { test: 'optimality', passed: true }] } };
  assert.equal(applyQualityPolicy('05-optimizer', grade).score, 83.333333);
  assert.equal(applyQualityPolicy('05-optimizer', grade).accepted, false);
});

test('Ambiguous reservation retry test remains diagnostic and excluded for all cells', () => {
  const grade = { score: 50, accepted: false, public_passed: true, protected_files_intact: true,
    acceptance: { tests: [{ test: 'Service.test_retry_after_release', passed: false }, { test: 'Service.test_capacity', passed: true }] } };
  const result = applyQualityPolicy('04-components', grade);
  assert.equal(result.score, 100);
  assert.equal(result.accepted, true);
  assert.equal(result.raw_score, 50);
  assert.deepEqual(result.diagnostic_failed_tests, ['Service.test_retry_after_release']);
});

test('Missing Execute and misspelled effort stop before inference', () => {
  const script = path.join(ROOT, 'harness/cli.mjs');
  const missing = command(process.execPath, [script, 'runall', '--effort', 'low']);
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /requires --execute/);
  const single = command(process.execPath, [script, 'run', '--run-id', schedule()[0].run_id]);
  assert.equal(single.status, 1);
  assert.match(single.stderr, /requires --execute/);
  const invalid = command(process.execPath, [script, 'runall', '--effort', 'ligh', '--execute']);
  assert.equal(invalid.status, 1);
  assert.match(invalid.stderr, /configured --effort/);
});

test('Status includes all eighteen cells without inference', () => {
  const result = command(process.execPath, [path.join(ROOT, 'harness/cli.mjs'), 'status']);
  assert.equal(result.status, 0, result.stderr);
  const status = JSON.parse(result.stdout);
  assert.equal(status.planned_attempts, 216);
  assert.equal(status.cells.length, 18);
  assert.ok(status.cells.every(c => c.planned === 12));
});
