import fs from 'node:fs';
import path from 'node:path';
import { ROOT, config, readJson, schedule, writeJson, timestamp, efforts, verifyFreeze, hash } from './common.mjs';
import { applyQualityPolicy } from './quality.mjs';

const mean = xs => xs.length && xs.every(Number.isFinite) ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
const sum = xs => xs.length && xs.every(Number.isFinite) ? xs.reduce((a, b) => a + b, 0) : null;
const format = (n, digits = 3) => Number.isFinite(n) ? n.toFixed(digits) : 'â€”';
const csv = value => value === null || value === undefined ? '' : `"${String(value).replaceAll('"', '""')}"`;
const effortOf = r => r.reasoning_effort ?? r.requested_effort;

export function aggregate(rows, cfg = config(), levels = efforts(cfg)) {
  return cfg.models.flatMap(model => levels.map(reasoning_effort => {
    const attempts = rows.filter(r => r.model === model && effortOf(r) === reasoning_effort && r.status !== 'pending')
      .map(r => ({ ...r, grading: applyQualityPolicy(r.task, r.grading, cfg) }));
    const eligible = attempts.filter(r => r.comparable && r.inference_turns_submitted === 1
      && (r.requested_effort === undefined || r.requested_effort === reasoning_effort)
      && (r.effective?.effort === undefined || r.effective.effort === reasoning_effort));
    const taskSummaries = cfg.tasks.map(t => {
      const runs = eligible.filter(r => r.task === t.id);
      const scores = runs.map(r => r.grading?.score);
      const raw = runs.map(r => r.grading?.raw_score ?? r.grading?.score);
      return { task: t.id, evaluated_repetitions: scores.filter(Number.isFinite).length,
        repetition_ids: runs.map(r => r.repetition), quality_mean: mean(scores), raw_quality_mean: mean(raw),
        quality_min: scores.length && scores.every(Number.isFinite) ? Math.min(...scores) : null,
        quality_max: scores.length && scores.every(Number.isFinite) ? Math.max(...scores) : null,
        model_seconds_mean: mean(runs.map(r => r.model_elapsed_seconds)),
        credits_mean: mean(runs.map(r => r.costs?.credits_estimated)),
        api_usd_mean: mean(runs.map(r => r.costs?.api_usd_estimated)) };
    });
    const complete = taskSummaries.every(t => t.evaluated_repetitions === cfg.repetitions
      && new Set(t.repetition_ids).size === cfg.repetitions
      && t.repetition_ids.every(r => Number.isInteger(r) && r >= 1 && r <= cfg.repetitions));
    const accepted = eligible.filter(r => r.grading?.accepted && r.status === 'completed').length;
    const completeCosts = complete && eligible.every(r => r.usage?.complete && Number.isFinite(r.costs?.credits_estimated));
    const totalCredits = completeCosts ? sum(eligible.map(r => r.costs.credits_estimated)) : null;
    return { model, reasoning_effort, source: 'current',
      attempted: attempts.length, comparable: eligible.length, expected: cfg.tasks.length * cfg.repetitions,
      invalid_attempts: attempts.length - eligible.length, full_quality_coverage: complete,
      quality_macro_mean: complete ? mean(taskSummaries.map(t => t.quality_mean)) : null,
      raw_quality_macro_mean: complete ? mean(taskSummaries.map(t => t.raw_quality_mean)) : null,
      accepted, raw_accepted: eligible.filter(r => (r.grading?.raw_accepted ?? r.grading?.accepted) && r.status === 'completed').length,
      diagnostic_failures: eligible.reduce((n, r) => n + (r.grading?.diagnostic_failed_tests?.length ?? 0), 0),
      excluded_checks: eligible.reduce((n, r) => n + (r.grading?.excluded_acceptance_tests?.length ?? 0), 0),
      model_seconds_mean: mean(eligible.map(r => r.model_elapsed_seconds)),
      credits_total: totalCredits, credits_per_accepted: accepted > 0 && totalCredits !== null ? totalCredits / accepted : null,
      known_credits_subtotal: sum(eligible.map(r => r.costs?.credits_estimated ?? r.costs?.credits_observed_partial).filter(Number.isFinite)),
      api_usd_total: complete && eligible.every(r => r.costs?.api_estimate_complete) ? sum(eligible.map(r => r.costs.api_usd_estimated)) : null,
      api_usd_low_observed: sum(eligible.map(r => r.costs?.api_usd_low)),
      api_usd_high_observed: sum(eligible.map(r => r.costs?.api_usd_high)), tasks: taskSummaries };
  }));
}

function writeResultsCsv(target, rows) {
  const columns = ['index', 'run_id', 'source', 'task', 'model', 'reasoning_effort', 'repetition', 'status', 'comparable',
    'quality_score', 'raw_quality_score', 'accepted', 'raw_accepted', 'excluded_checks', 'diagnostic_failed_tests',
    'model_seconds', 'first_output_seconds', 'grading_seconds', 'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
    'output_tokens', 'reasoning_output_tokens', 'usage_complete', 'credits_estimated', 'credits_observed_partial',
    'credit_balance_observed_delta', 'billed_credits_attributed', 'api_usd_estimated', 'api_usd_low', 'api_usd_high', 'invalid_reasons'];
  const records = rows.map(r => [r.index, r.run_id, r.source, r.task, r.model, effortOf(r), r.repetition, r.status, r.comparable,
    r.grading?.score, r.grading?.raw_score, r.grading?.accepted, r.grading?.raw_accepted,
    r.grading?.excluded_acceptance_tests?.length, r.grading?.diagnostic_failed_tests?.join('; '),
    r.model_elapsed_seconds, r.first_output_seconds, r.grading?.grading_seconds,
    r.usage?.totals?.inputTokens, r.usage?.totals?.cachedInputTokens, r.usage?.totals?.cacheWriteInputTokens,
    r.usage?.totals?.outputTokens, r.usage?.totals?.reasoningOutputTokens, r.usage?.complete,
    r.costs?.credits_estimated, r.costs?.credits_observed_partial, r.credit_balance_observed_delta, r.billed_credits_attributed,
    r.costs?.api_usd_estimated, r.costs?.api_usd_low, r.costs?.api_usd_high, r.invalid_reasons?.join('; ')]);
  fs.writeFileSync(target, '\uFEFF' + [columns.join(','), ...records.map(v => v.map(csv).join(','))].join('\n') + '\n');
}

export function report() {
  const cfg = config();
  if (fs.existsSync(path.join(ROOT, 'preparation/freeze.json'))) verifyFreeze();
  const entries = schedule(cfg);
  const rows = entries.map(e => {
    const target = path.join(ROOT, 'runs', e.run_id, 'result.json');
    const r = fs.existsSync(target) ? readJson(target) : { ...e, status: 'pending', source: 'current' };
    return { ...r, grading: applyQualityPolicy(r.task, r.grading, cfg) };
  });
  const summary = aggregate(rows, cfg);
  const comparison = summary;
  const output = path.join(ROOT, 'analysis');
  fs.mkdirSync(output, { recursive: true });
  writeJson(path.join(output, 'results.json'), { generated_at: timestamp(), experiment: cfg.experiment_id,
    quality_policy: cfg.quality_policy, summary, comparison_summary: comparison,
    runs: rows });
  writeResultsCsv(path.join(output, 'results.csv'), rows);
  writeResultsCsv(path.join(output, 'comparison.csv'), rows);
  const completed = rows.filter(r => !['pending', 'initializing', 'running'].includes(r.status)).length;
  const lines = ['# Sol coding benchmark', '',
    `Attempt progress: ${completed}/${entries.length}. Three models, six tasks, six efforts, two independent repetitions. Standard speed.`, '',
    '| Model | Effort | Attempts | Accepted | Functional / 100 | Mean time, s | Mean API USD / attempt | Total API USD |',
    '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |',
    ...comparison.map(s => `| ${s.model} | ${s.reasoning_effort} | ${s.attempted}/${s.expected} | ${s.accepted} | ${format(s.quality_macro_mean, 2)} | ${format(s.model_seconds_mean, 1)} | ${format(s.api_usd_total === null ? null : s.api_usd_total / s.expected, 4)} | ${format(s.api_usd_total, 4)} |`), '',
    'USD is a counterfactual Standard API token cost for observed Codex usage, not an actual API or subscription bill. Missing values are unavailable, not zero.', '',
    'Time includes model reasoning, tools and self-tests; external grading is excluded. Two repetitions support descriptive comparisons only.', '',
    'The first four tasks use scored-check pass proportions; the two complex tasks use predeclared category weights and independent qualification. The combined score is an equal macro average over all six tasks; incomplete coverage is unavailable.', '',
    'Expanded correctness, test effectiveness and runtime measurements are evaluated separately. Manual review remains unscored until completed.', '',
    '[API pricing](https://developers.openai.com/api/docs/pricing). Rates verified 2026-10-01.', ''];
  fs.writeFileSync(path.join(output, 'REPORT_EN.md'), lines.join('\n'));
  return { summary, reports: output };
}
