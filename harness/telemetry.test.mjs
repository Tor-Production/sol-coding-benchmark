import test from 'node:test';
import assert from 'node:assert/strict';
import { usageFromEvents, costs, observedBalanceDelta } from './telemetry.mjs';
import { config, schedule } from './common.mjs';
import { aggregate } from './report.mjs';

const breakdown = (input, cached, output, writes = 0, reasoning = 0) => ({ inputTokens: input,
  cachedInputTokens: cached, outputTokens: output, cacheWriteInputTokens: writes,
  reasoningOutputTokens: reasoning, totalTokens: input + output });
const event = (total, last = total, thread = 'root', turn = 'turn') => ({ method: 'thread/tokenUsage/updated',
  params: { threadId: thread, turnId: turn, tokenUsage: { total, last } } });

test('Uses cumulative final usage once; excludes children and duplicate notifications', () => {
  const first = breakdown(100, 20, 10, 5, 4);
  const second = breakdown(250, 70, 30, 15, 10);
  const events = [event(first), event(breakdown(999, 0, 999), undefined, 'child'),
    event(second, breakdown(150, 50, 20, 10, 6)), event(second, breakdown(150, 50, 20, 10, 6))];
  const usage = usageFromEvents(events, 'root', 'turn');
  assert.equal(usage.complete, true);
  assert.equal(usage.totals.inputTokens, 250);
  assert.equal(usage.requests.length, 2);
  assert.equal(usage.request_breakdowns_complete, true);
});
test('Missing and malformed counters are unknown, not zero', () => {
  assert.equal(usageFromEvents([], 'root', 'turn').totals, null);
  const bad = { ...breakdown(10, 2, 5), cachedInputTokens: 100 };
  assert.equal(usageFromEvents([event(bad)], 'root', 'turn').totals, null);
  assert.equal(costs(usageFromEvents([], 'root'), 'gpt-6-sol', config()).credits_estimated, null);
});
test('Rejects decreasing counters; accepts partial status without inventing totals', () => {
  assert.equal(usageFromEvents([event(breakdown(100, 0, 20)), event(breakdown(20, 0, 5))], 'root', 'turn').totals, null);
  const usage = usageFromEvents([event(breakdown(100, 0, 20))], 'root');
  assert.equal(usage.complete, false);
  const cost = costs(usage, 'gpt-6-sol', config());
  assert.equal(cost.credits_estimated, null);
  assert.equal(cost.credits_observed_partial, 0.01);
  assert.equal(cost.api_usd_estimated, null);
});
test('Output and reasoning, input and cached input are not double counted', () => {
  const usage = usageFromEvents([event(breakdown(1000000, 400000, 100000, 200000, 60000))], 'root', 'turn');
  const cost = costs(usage, 'gpt-5.6-sol', config());
  assert.equal(cost.credits_estimated, 114);
  assert.equal(cost.api_usd_estimated, (400000 * 8 + 400000 * 0.8 + 200000 * 10 + 100000 * 30) / 1e6);
});
test('API cache-write rate replaces base input for writes', () => {
  const usage = usageFromEvents([event(breakdown(1000, 400, 100, 200))], 'root', 'turn');
  const cost = costs(usage, 'gpt-6.1-sol', config());
  assert.equal(cost.api_usd_estimated, (400 * 2 + 400 * 0.1 + 200 * 2.5 + 100 * 10) / 1e6);
  assert.equal(cost.credits_estimated, (600 * 50 + 400 * 2.5 + 100 * 250) / 1e6);
});
test('Long-context classification uses each request, not session token sum', () => {
  const usage = usageFromEvents([event(breakdown(160000, 0, 10)),
    event(breakdown(320000, 0, 20), breakdown(160000, 0, 10))], 'root', 'turn');
  assert.equal(costs(usage, 'gpt-6-sol', config()).api_usd_estimated, (320000 * 2 + 20 * 10) / 1e6);
});
test('Missing write and per-request data yield bounds', () => {
  const b = breakdown(400000, 100000, 20);
  delete b.cacheWriteInputTokens;
  const usage = usageFromEvents([event(b, breakdown(100000, 0, 10))], 'root', 'turn');
  const cost = costs(usage, 'gpt-6-sol', config());
  assert.equal(cost.api_usd_estimated, null);
  assert.ok(cost.api_usd_high > cost.api_usd_low);
});
test('Credit balance deltas never claim run attribution', () => {
  assert.equal(observedBalanceDelta({}, {}), null);
  assert.equal(observedBalanceDelta({ codex: { balance: '10', unlimited: false } }, { codex: { balance: '9', unlimited: false } }), 1);
  assert.equal(observedBalanceDelta({ codex: { balance: '10', unlimited: true } }, { codex: { balance: '9' } }), null);
  assert.equal(observedBalanceDelta({ codex: { balance: null } }, { codex: { balance: '9' } }), null);
});
test('216 attempts, exactly two per model/task/effort; second cell order reverses', () => {
  const entries = schedule();
  assert.equal(entries.length, 216);
  assert.equal(new Set(entries.map(e => e.run_id)).size, 216);
  for (const task of config().tasks) {
    for (const model of config().models) {
      for (const effort of config().reasoning_efforts) assert.equal(entries.filter(e => e.task === task.id && e.model === model && e.reasoning_effort === effort).length, 2);
    }
    const cell = e => `${e.model}/${e.reasoning_effort}`;
    assert.deepEqual(entries.filter(e => e.task === task.id && e.repetition === 2).map(cell),
      entries.filter(e => e.task === task.id && e.repetition === 1).map(cell).reverse());
  }
});
test('Missing repetitions do not turn into zero quality or total spend', () => {
  const cfg = config();
  const only = { ...schedule()[0], status: 'completed', comparable: true, inference_turns_submitted: 1,
    grading: { score: 100, accepted: true }, model_elapsed_seconds: 10, usage: { complete: true },
    costs: { credits_estimated: 1, api_usd_estimated: 0.04, api_estimate_complete: true } };
  const summary = aggregate([only], cfg)[0];
  assert.equal(summary.quality_macro_mean, null);
  assert.equal(summary.credits_total, null);
  assert.equal(summary.known_credits_subtotal, 1);
  assert.equal(summary.tasks[1].quality_mean, null);
});
test('Cost per accepted includes failed attempts and equal task weights', () => {
  const rows = config().tasks.flatMap(t => [1, 2].map(repetition => ({ task: t.id, repetition, model: 'gpt-5.6-sol', reasoning_effort: 'max' }))).map((e, i) => ({ ...e,
    status: 'completed', comparable: true, inference_turns_submitted: 1,
    grading: { score: i === 0 ? 0 : 100, accepted: i !== 0 }, model_elapsed_seconds: 10,
    usage: { complete: true }, costs: { credits_estimated: 1, api_usd_estimated: 0.04, api_estimate_complete: true } }));
  const summary = aggregate(rows).find(s => s.model === 'gpt-5.6-sol' && s.reasoning_effort === 'max');
  assert.ok(Math.abs(summary.quality_macro_mean - 100 * 11 / 12) < 1e-8);
  assert.equal(summary.credits_total, 12);
  assert.equal(summary.credits_per_accepted, 12 / 11);
});
