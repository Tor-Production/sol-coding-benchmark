import fs from 'node:fs';
import path from 'node:path';
import { ROOT, config, copyTree, readJson, writeJson, hash, hashes, timestamp,
  makeHome, removeCredential, verifyFreeze, assertRuntimeMatches, toolEnvironment, efforts } from './common.mjs';
import { Rpc } from './rpc.mjs';
import { usageFromEvents, costs, creditBalances, observedBalanceDelta } from './telemetry.mjs';
import { gradeWorkspace } from './evaluate.mjs';

export async function listModels(rpc) {
  const models = [];
  let cursor = null;
  do {
    const page = await rpc.request('model/list', { includeHidden: true, limit: 100, cursor });
    models.push(...page.data);
    cursor = page.nextCursor;
  } while (cursor);
  return models;
}

export async function preflight({ rpcFactory = (rt, options) => new Rpc(rt.codex, ['app-server', '--strict-config'], options), rtOverride } = {}) {
  const frozen = verifyFreeze();
  const rt = rtOverride ?? assertRuntimeMatches(frozen);
  const scoped = makeHome('preflight', !rtOverride);
  const dir = path.join(ROOT, 'preparation', `preflight-${Date.now()}`);
  fs.mkdirSync(dir, { recursive: true });
  const probe = path.join(ROOT, '.runtime', 'probes', path.basename(scoped.home));
  fs.mkdirSync(probe, { recursive: true });
  let rpc;
  try {
    rpc = rpcFactory(rt, { cwd: probe, env: toolEnvironment(scoped.env, rt), logPath: path.join(dir, 'rpc.jsonl'), stderrPath: path.join(dir, 'stderr.txt') });
    const initialized = await rpc.initialize();
    const models = await listModels(rpc);
    const requested = config().models.map(id => {
      const model = models.find(m => m.model === id || m.id === id);
      if (!model) throw new Error(`Requested model is unavailable: ${id}. No substitution is allowed.`);
      const efforts = (model.supportedReasoningEfforts ?? []).map(x => x.reasoningEffort ?? x.effort);
      for (const level of config().reasoning_efforts) {
        if (!efforts.includes(level)) throw new Error(`${id} does not support ${level}`);
      }
      return { model: id, name: model.displayName, efforts };
    });
    // A fresh thread without turn/start performs no model inference.
    const verifiedCells = [];
    for (const model of config().models) {
      for (const effort of efforts()) {
        const effective = await rpc.request('thread/start', { model, allowProviderModelFallback: false, cwd: probe, ephemeral: true,
          approvalPolicy: 'never', permissions: ':workspace', serviceTier: null,
          config: { model_reasoning_effort: effort }, runtimeWorkspaceRoots: [probe] });
        assertEffective(effective, model, effort);
        verifiedCells.push({ model: effective.model, effort: effective.reasoningEffort, service_tier: effective.serviceTier,
          sandbox: effective.sandbox, permissions: effective.activePermissionProfile, instructions: effective.instructionSources });
      }
    }
    fs.copyFileSync(path.join(ROOT, 'harness/native-probe.py'), path.join(probe, 'native-probe.py'));
    const native = await rpc.request('command/exec', { command: [rt.python, '-B', 'native-probe.py'], cwd: probe,
      timeoutMs: 60000, permissionProfile: ':workspace' }, 75000);
    if (native.exitCode !== 0) throw new Error(`Native sandbox probe failed: ${native.stderr ?? JSON.stringify(native)}`);
    const nativeChecks = JSON.parse(native.stdout.trim());
    if (!nativeChecks.workspace_write || !nativeChecks.sqlite || !nativeChecks.loopback_http) throw new Error('Incomplete native sandbox probe');
    const result = { at: timestamp(), verified: true, simulation: !!rtOverride, inference_requests: 0, initialized, requested, native_checks: nativeChecks,
      model_access_note: 'model/list verifies the dynamic catalog; actual inference/account entitlement is checked on the first measured turn',
      effective_cells: verifiedCells, runtime: rt,
      freeze_sha256: hash(fs.readFileSync(path.join(ROOT, 'preparation/freeze.json'))), evidence: dir };
    writeJson(path.join(ROOT, 'preparation/preflight-latest.json'), result);
    return result;
  } finally {
    if (rpc) await rpc.stop();
    removeCredential(scoped.home);
  }
}

export function assertEffective(started, model, effort) {
  if (started.model !== model) throw new Error(`Model mismatch: requested ${model}, got ${started.model}`);
  if (started.reasoningEffort !== effort) throw new Error(`Reasoning effort mismatch: requested ${effort}, got ${started.reasoningEffort}`);
  if (![null, undefined, 'default', 'standard'].includes(started.serviceTier)) throw new Error(`Unexpected speed tier: ${started.serviceTier}`);
  if (started.sandbox?.type !== 'workspaceWrite' || started.sandbox.networkAccess !== false) throw new Error('Effective sandbox must be workspaceWrite with network disabled');
  if (started.approvalPolicy !== 'never') throw new Error('Effective approval policy must be never');
  if (started.activePermissionProfile?.id !== ':workspace') throw new Error('Effective named permission profile must be :workspace');
}

async function balances(rpc) {
  try { return creditBalances(await rpc.request('account/rateLimits/read', {}, 10000)); }
  catch { return null; }
}

export async function runAttempt(entry, { rpcFactory = (rt, options) => new Rpc(rt.codex, ['app-server', '--strict-config'], options), rtOverride, allowWithoutPreflight = false,
  outputRoot = path.join(ROOT, 'runs'), workspaceRoot = path.join(ROOT, 'workspaces') } = {}) {
  const cfg = config();
  const effort = entry.reasoning_effort;
  if (!efforts(cfg).includes(effort)) throw new Error(`Unconfigured attempt effort: ${effort}`);
  const frozen = verifyFreeze();
  const rt = rtOverride ?? assertRuntimeMatches(frozen);
  if (!allowWithoutPreflight) {
    const pre = readJson(path.join(ROOT, 'preparation/preflight-latest.json'));
    if (!pre.verified || pre.simulation || pre.freeze_sha256 !== hash(fs.readFileSync(path.join(ROOT, 'preparation/freeze.json')))) throw new Error('Run a real Preflight against the current freeze first');
    if (!pre.effective_cells?.some(c => c.model === entry.model && c.effort === effort)) throw new Error('Preflight did not verify this model/effort cell');
  }
  const folder = path.join(outputRoot, entry.run_id);
  if (fs.existsSync(folder)) throw new Error(`Attempt ${entry.run_id} already exists. No overwrite or automatic retry.`);
  fs.mkdirSync(folder, { recursive: true });
  const workspace = path.join(workspaceRoot, entry.run_id);
  if (fs.existsSync(workspace)) throw new Error(`Workspace already exists: ${workspace}`);
  copyTree(path.join(ROOT, 'tasks', entry.task), workspace);
  const startedAt = timestamp();
  const hostStart = performance.now();
  const result = { ...entry, experiment_id: cfg.experiment_id, started_at: startedAt, status: 'initializing',
    runtime: rt, requested_effort: effort, requested_speed: cfg.speed, source: 'current', workspace,
    inference_turns_submitted: 0, snapshot: null, grading: null, invalid_reasons: [],
    measured_model: null, model_elapsed_seconds: null, first_output_seconds: null, first_tool_seconds: null,
    credit_balance_observed_delta: null, billed_credits_attributed: null,
    usage: { complete: false, totals: null, requests: [] }, costs: null,
    freeze_sha256: hash(fs.readFileSync(path.join(ROOT, 'preparation/freeze.json'))) };
  writeJson(path.join(folder, 'result.json'), result);
  let rpc, scoped, turnStart = null, threadId, turnId, completion;
  try {
    scoped = makeHome(entry.run_id, !rtOverride, effort);
    rpc = rpcFactory(rt, { cwd: workspace, env: toolEnvironment(scoped.env, rt), logPath: path.join(folder, 'rpc.jsonl'), stderrPath: path.join(folder, 'stderr.txt') });
    await rpc.initialize();
    const models = await listModels(rpc);
    const available = models.find(m => m.model === entry.model || m.id === entry.model);
    if (!available) throw new Error('Requested model is no longer available');
    if (!(available.supportedReasoningEfforts ?? []).some(e => (e.reasoningEffort ?? e.effort) === effort)) {
      throw new Error(`Requested model no longer supports ${effort}`);
    }
    const started = await rpc.request('thread/start', { model: entry.model, allowProviderModelFallback: false, cwd: workspace, ephemeral: false,
      approvalPolicy: 'never', permissions: ':workspace', serviceTier: null,
      config: { model_reasoning_effort: effort }, runtimeWorkspaceRoots: [workspace],
      developerInstructions: 'Complete this task in the current workspace. Do not read parent directories, other attempts, harnesses, graders, or reference implementations. Do not use subagents, skills, MCP, web search, or external services. Preserve the supplied tests and TASK.md. You may add your own tests. Do not commit, push, publish, or change global configuration.' });
    assertEffective(started, entry.model, effort);
    result.measured_model = started.model;
    result.effective = { effort: started.reasoningEffort, service_tier: started.serviceTier, sandbox: started.sandbox,
      permissions: started.activePermissionProfile,
      instruction_sources: started.instructionSources };
    threadId = started.thread.id;
    result.thread_id = threadId;
    result.session_id = started.thread.sessionId;
    result.credit_balances_before = await balances(rpc);
    rpc.on('interactiveRequest', () => result.invalid_reasons.push('interactive_request'));
    rpc.on('notification', e => {
      if (e.params?.threadId !== threadId) return;
      if (e.method === 'thread/settings/updated') {
        const settings = e.params.threadSettings;
        result.turn_settings = { model: settings?.model, effort: settings?.effort, service_tier: settings?.serviceTier };
        if (settings?.model !== entry.model) result.invalid_reasons.push('turn_model_mismatch');
        if (settings?.effort !== effort) result.invalid_reasons.push('turn_effort_mismatch');
        if (![null, undefined, 'default', 'standard'].includes(settings?.serviceTier)) result.invalid_reasons.push('turn_speed_mismatch');
      }
      if (e.method === 'model/rerouted') result.invalid_reasons.push(`model_rerouted:${e.params.toModel}`);
      if (turnStart !== null && e.method === 'item/agentMessage/delta' && result.first_output_seconds === null) result.first_output_seconds = (performance.now() - turnStart) / 1000;
      if (turnStart !== null && e.method === 'item/started' && ['commandExecution', 'fileChange'].includes(e.params?.item?.type) && result.first_tool_seconds === null) result.first_tool_seconds = (performance.now() - turnStart) / 1000;
      if (e.method === 'item/started' && /collab|agentTool/i.test(e.params?.item?.type ?? '')) result.invalid_reasons.push('subagent_activity');
    });
    const taskText = fs.readFileSync(path.join(workspace, 'TASK.md'), 'utf8');
    fs.writeFileSync(path.join(folder, 'prompt.md'), taskText);
    result.prompt_sha256 = hash(taskText);
    const waiting = rpc.waitFor('turn/completed', p => p.threadId === threadId, entry.timeout_seconds * 1000);
    // Install the rejection handler immediately, even if turn/start itself fails.
    waiting.catch(() => {});
    turnStart = performance.now();
    result.status = 'running';
    result.inference_turns_submitted = 1;
    writeJson(path.join(folder, 'result.json'), result);
    const turn = await rpc.request('turn/start', { threadId, model: entry.model, effort,
      serviceTier: null, input: [{ type: 'text', text: taskText }], cwd: workspace, runtimeWorkspaceRoots: [workspace] });
    turnId = turn.turn.id;
    result.turn_id = turnId;
    completion = await waiting;
    result.model_elapsed_seconds = (performance.now() - turnStart) / 1000;
    result.status = completion.turn.status === 'completed' ? 'completed' : 'model_failed';
    result.turn_error = completion.turn.error ?? null;
    result.credit_balances_after = await balances(rpc);
    result.credit_balance_observed_delta = observedBalanceDelta(result.credit_balances_before, result.credit_balances_after);
    const final = rpc.messages.filter(e => e.method === 'item/completed' && e.params.threadId === threadId
      && e.params.item?.type === 'agentMessage').map(e => e.params.item.text).join('\n\n');
    fs.writeFileSync(path.join(folder, 'final.txt'), final);
  } catch (error) {
    result.error = error.message;
    result.status = /Notification timeout/.test(error.message) ? 'timeout' : (turnStart === null ? 'harness_failed' : 'model_failed');
    if (turnStart !== null) result.model_elapsed_seconds = (performance.now() - turnStart) / 1000;
    if (result.status === 'harness_failed') result.invalid_reasons.push('failure_before_model_execution');
    if (rpc && threadId && turnId && !completion) {
      try { await rpc.request('turn/interrupt', { threadId, turnId }, 5000); } catch { /* Preserve the partial attempt. */ }
    }
  } finally {
    if (rpc) {
      try { await rpc.request('thread/backgroundTerminals/clean', { threadId }, 5000); } catch { /* Best-effort cleanup. */ }
      await rpc.stop();
      result.usage = usageFromEvents(rpc.messages, threadId, result.status === 'completed' ? turnId : null);
    }
    if (scoped) removeCredential(scoped.home);
    result.costs = costs(result.usage, entry.model, cfg);
    if (result.invalid_reasons.some(reason => reason.startsWith('model_rerouted'))) {
      result.costs = { credits_estimated: null, api_usd_estimated: null, api_usd_low: null, api_usd_high: null, note: 'Unknown per-model attribution after rerouting' };
    }
    result.finished_at = timestamp();
    result.host_execution_seconds = (performance.now() - hostStart) / 1000;
    try {
      result.snapshot = path.join(folder, 'candidate');
      copyTree(workspace, result.snapshot);
      const manifest = hashes(result.snapshot);
      writeJson(path.join(folder, 'candidate-hashes.json'), manifest);
      result.candidate_manifest_sha256 = hash(JSON.stringify(manifest));
    } catch (e) {
      result.snapshot = null;
      result.invalid_reasons.push('candidate_snapshot_failed');
      result.snapshot_error = e.message;
    }
    writeJson(path.join(folder, 'result.json'), result);
  }
  return result;
}

export function gradeAttempt(entry, python) {
  verifyFreeze();
  const folder = path.join(ROOT, 'runs', entry.run_id);
  const result = readJson(path.join(folder, 'result.json'));
  if (result.grading) return result;
  if (!result.snapshot) throw new Error('No frozen candidate to grade');
  const expected = readJson(path.join(folder, 'candidate-hashes.json'));
  if (hash(JSON.stringify(hashes(result.snapshot))) !== result.candidate_manifest_sha256
    || hash(JSON.stringify(expected)) !== result.candidate_manifest_sha256) throw new Error('Frozen candidate was modified after execution');
  const copy = path.join(folder, 'evaluation-copy');
  if (fs.existsSync(copy)) throw new Error('Previous incomplete grading exists; inspect it before retrying grading');
  copyTree(result.snapshot, copy);
  try {
    result.grading = gradeWorkspace(entry.task, copy, path.join(folder, 'grade'), python);
  } catch (e) {
    result.grading = { score: null, accepted: false, harness_error: e.message };
    result.invalid_reasons.push('grading_harness_failed');
  }
  result.comparable = result.invalid_reasons.length === 0;
  writeJson(path.join(folder, 'result.json'), result);
  return result;
}
