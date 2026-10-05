const counters = ['inputTokens', 'cachedInputTokens', 'outputTokens', 'reasoningOutputTokens', 'totalTokens'];
function valid(b) {
  return b && counters.every(k => Number.isSafeInteger(b[k]) && b[k] >= 0)
    && b.cachedInputTokens <= b.inputTokens && b.reasoningOutputTokens <= b.outputTokens
    && b.totalTokens === b.inputTokens + b.outputTokens
    && (b.cacheWriteInputTokens === undefined || (Number.isSafeInteger(b.cacheWriteInputTokens)
      && b.cacheWriteInputTokens >= 0 && b.cacheWriteInputTokens <= b.inputTokens - b.cachedInputTokens));
}
export function usageFromEvents(events, threadId, completedTurnId = null) {
  const updates = events.filter(e => e.method === 'thread/tokenUsage/updated' && e.params.threadId === threadId);
  if (!updates.length) return { complete: false, reason: 'No token usage received', totals: null, requests: [] };
  let previous = Object.fromEntries(counters.map(k => [k, 0]));
  previous.cacheWriteInputTokens = 0;
  let final = null;
  let requestCoverage = true;
  const requests = [];
  for (const event of updates) {
    const total = event.params.tokenUsage?.total;
    const last = event.params.tokenUsage?.last;
    if (!valid(total)) return { complete: false, reason: 'Invalid token counters', totals: null, requests: [] };
    if (counters.some(k => total[k] < previous[k])) return { complete: false, reason: 'Cumulative counters decreased', totals: null, requests: [] };
    if (total.totalTokens !== previous.totalTokens) {
      const delta = Object.fromEntries(counters.map(k => [k, total[k] - previous[k]]));
      if (total.cacheWriteInputTokens !== undefined) delta.cacheWriteInputTokens = total.cacheWriteInputTokens - (previous.cacheWriteInputTokens ?? 0);
      if (!valid(last) || counters.some(k => last[k] !== delta[k])) requestCoverage = false;
      requests.push(delta);
    }
    previous = total;
    final = event;
  }
  const complete = completedTurnId !== null && final.params.turnId === completedTurnId;
  return { complete, reason: complete ? null : 'Usage is partial or is not tied to the completed turn',
    totals: previous, requests, request_breakdowns_complete: requestCoverage, snapshots: updates.length };
}
function apiCost(b, rate, long, writeKnown, maximizeUnknownWrites = false) {
  const writes = writeKnown ? b.cacheWriteInputTokens : (maximizeUnknownWrites ? b.inputTokens - b.cachedInputTokens : 0);
  return ((b.inputTokens - b.cachedInputTokens - writes) * rate.input * (long ? 2 : 1)
    + b.cachedInputTokens * rate.cached_input * (long ? 2 : 1)
    + writes * rate.cache_write * (long ? 2 : 1)
    + b.outputTokens * rate.output * (long ? 1.5 : 1)) / 1e6;
}
export function costs(usage, model, cfg) {
  const price = cfg.pricing[model];
  if (!usage.totals || !price) return { credits_estimated: null, credits_observed_partial: null, api_usd_estimated: null, api_usd_low: null, api_usd_high: null, note: 'Missing valid usage or model rate' };
  const b = usage.totals;
  const rate = price.credits_per_million;
  const credits = ((b.inputTokens - b.cachedInputTokens) * rate.input + b.cachedInputTokens * rate.cached_input + b.outputTokens * rate.output) / 1e6;
  const threshold = cfg.api_estimate.long_context_threshold_input_tokens_per_request;
  const shortGuaranteed = b.inputTokens <= threshold;
  const requestKnown = usage.request_breakdowns_complete;
  const parts = requestKnown ? usage.requests : [b];
  let low = 0, high = 0;
  for (const part of parts) {
    const writeKnown = Number.isSafeInteger(part.cacheWriteInputTokens);
    const long = requestKnown && part.inputTokens > threshold;
    low += apiCost(part, price.api_usd_per_million, long, writeKnown, false);
    high += apiCost(part, price.api_usd_per_million, long || (!requestKnown && !shortGuaranteed), writeKnown, true);
  }
  const exact = usage.complete && Math.abs(high - low) < 1e-12;
  return { credits_estimated: usage.complete ? credits : null,
    credits_observed_partial: usage.complete ? null : credits,
    api_usd_estimated: exact ? low : null, api_usd_low: low, api_usd_high: high,
    api_estimate_complete: exact,
    note: exact ? 'Counterfactual Standard API token cost; no hosted-tool or regional fees' :
      'Bounds reflect missing request/cache-write telemetry; incomplete runs are partial observations, not total cost' };
}
export function creditBalances(result) {
  const buckets = result?.rateLimitsByLimitId ?? { [result?.rateLimits?.limitId ?? 'codex']: result?.rateLimits };
  return Object.fromEntries(Object.entries(buckets).map(([id, value]) => [id, value?.credits ?? null]));
}
export function observedBalanceDelta(before, after) {
  const left = before?.codex, right = after?.codex;
  if (!left || !right || left.unlimited || right.unlimited || left.balance === null || right.balance === null) return null;
  const a = Number(left.balance), b = Number(right.balance);
  return Number.isFinite(a) && Number.isFinite(b) && a >= b ? a - b : null;
}
