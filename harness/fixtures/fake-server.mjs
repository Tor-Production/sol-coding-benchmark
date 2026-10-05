// Deterministic protocol simulator. Never contacts a model or external service.
import { createInterface } from 'node:readline';
import fs from 'node:fs';
import path from 'node:path';

let cwd, effort = 'low', model;
const send = message => process.stdout.write(JSON.stringify(message) + '\n');
const usage = { inputTokens: 1000, cachedInputTokens: 400, cacheWriteInputTokens: 100,
  outputTokens: 100, reasoningOutputTokens: 50, totalTokens: 1100 };
const read = createInterface({ input: process.stdin });
read.on('line', line => {
  const m = JSON.parse(line);
  const reply = result => send({ id: m.id, result });
  if (m.method === 'initialize') reply({ userAgent: 'offline_fixture' });
  else if (m.method === 'model/list') reply({ data: ['gpt-5.6-sol', 'gpt-6-sol', 'gpt-6.1-sol'].map(model => ({ id: model, model, displayName: model,
    supportedReasoningEfforts: (process.env.BENCH_FIXTURE_AVAILABLE_EFFORTS ?? 'low,medium,high,xhigh,max,ultra').split(',').map(reasoningEffort => ({ reasoningEffort })) })), nextCursor: null });
  else if (m.method === 'thread/start') {
    cwd = m.params.cwd;
    effort = m.params.config?.model_reasoning_effort ?? 'low';
    model = m.params.model;
    if (m.params.config && !m.params.ephemeral) {
      const homeConfig = fs.readFileSync(path.join(process.env.CODEX_HOME, 'config.toml'), 'utf8');
      if (!homeConfig.includes(`model_reasoning_effort = "${effort}"`)) {
        send({ id: m.id, error: { code: -1, message: 'home/thread effort mismatch' } });
        return;
      }
    }
    reply({ model, reasoningEffort: process.env.BENCH_FIXTURE_WRONG_EFFORT ?? effort, serviceTier: null, cwd,
      approvalPolicy: 'never', sandbox: { type: 'workspaceWrite', networkAccess: false },
      activePermissionProfile: { id: ':workspace' },
      instructionSources: [], thread: { id: 'fixture-thread', sessionId: 'fixture-thread' } });
  } else if (m.method === 'turn/start') {
    if (m.params.effort !== undefined && m.params.effort !== effort) {
      send({ id: m.id, error: { code: -1, message: 'thread/turn effort mismatch' } });
      return;
    }
    send({ method: 'thread/settings/updated', params: { threadId: 'fixture-thread',
      threadSettings: { model, effort: process.env.BENCH_FIXTURE_WRONG_TURN_EFFORT ?? effort, serviceTier: 'default' } } });
    if (process.env.BENCH_FIXTURE_CONTROL) {
      fs.copyFileSync(process.env.BENCH_FIXTURE_CONTROL, path.join(cwd, 'intervals.py'));
    }
    if (process.env.BENCH_FIXTURE_CONTROL_FOLDER) {
      fs.cpSync(process.env.BENCH_FIXTURE_CONTROL_FOLDER, cwd, { recursive: true });
    }
    // Completion before acknowledgement checks the runner's race handling.
    send({ method: 'item/agentMessage/delta', params: { threadId: 'fixture-thread', turnId: 'fixture-turn', delta: 'done' } });
    send({ method: 'thread/tokenUsage/updated', params: { threadId: 'fixture-thread', turnId: 'fixture-turn', tokenUsage: { total: usage, last: usage } } });
    send({ method: 'item/completed', params: { threadId: 'fixture-thread', turnId: 'fixture-turn', item: { type: 'agentMessage', text: 'Offline fixture done.' } } });
    send({ method: 'turn/completed', params: { threadId: 'fixture-thread', turn: { id: 'fixture-turn', status: 'completed', error: null } } });
    reply({ turn: { id: 'fixture-turn', status: 'inProgress' } });
  } else if (m.method === 'command/exec') {
    reply({ exitCode: 0, stderr: '', stdout: JSON.stringify({ workspace_write: true, sqlite: true, loopback_http: true }) });
  } else if (m.method === 'fixture/request') {
    send({ id: 999, method: 'item/tool/requestUserInput', params: { threadId: 'fixture-thread' } });
    reply({ ok: true });
  } else if (m.method === 'fixture/exit') process.exit(7);
  else if (m.method === 'fixture/error') send({ id: m.id, error: { code: -1, message: 'fixture failure' } });
  else if (m.method === 'fixture/hang') { /* Used only for timeout testing. */ }
  else if (m.id !== undefined && m.method) reply({});
});
read.on('close', () => process.exit(0));
