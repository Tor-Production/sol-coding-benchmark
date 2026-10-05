import { config } from './common.mjs';

// Keep the evaluator's full diagnostic output. Apply one frozen scoring policy
// to all freshly collected sessions.
export function applyQualityPolicy(task, grading, cfg = config()) {
  if (!grading || grading.harness_error || !grading.acceptance?.tests) return grading;
  const ignored = cfg.quality_policy?.ignored_acceptance_tests?.[task] ?? [];
  const tests = grading.acceptance.tests;
  for (const name of ignored) {
    if (!tests.some(t => t.test === name)) throw new Error(`Scoring policy test is missing: ${task}/${name}`);
  }
  const excluded = tests.filter(t => ignored.includes(t.test));
  const scored = tests.filter(t => !ignored.includes(t.test));
  if (!scored.length) throw new Error(`No scored checks remain for ${task}`);
  const weighted = cfg.quality_policy?.weighted_tasks?.includes(task);
  const score = weighted ? grading.acceptance.score : Number((100 * scored.filter(t => t.passed).length / scored.length).toFixed(6));
  if (weighted && !Number.isFinite(score)) throw new Error(`Missing weighted score: ${task}`);
  return { ...grading, raw_score: grading.raw_score ?? grading.score,
    raw_accepted: grading.raw_accepted ?? grading.accepted,
    score, accepted: scored.every(t => t.passed) && grading.public_passed && grading.protected_files_intact,
    scored_checks: scored.length, excluded_acceptance_tests: excluded.map(t => ({ test: t.test, passed: t.passed })),
    diagnostic_failed_tests: tests.filter(t => !t.passed).map(t => t.test) };
}
