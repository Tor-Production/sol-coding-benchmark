import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const config = () => JSON.parse(fs.readFileSync(path.join(ROOT, 'config/experiment.json'), 'utf8'));
export const readJson = p => JSON.parse(fs.readFileSync(p, 'utf8'));
export const hash = data => crypto.createHash('sha256').update(data).digest('hex');
export const timestamp = () => new Date().toISOString();
export function writeJson(p, value) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const temp = `${p}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value, null, 2) + '\n');
  fs.renameSync(temp, p);
}
export function command(exe, args, options = {}) {
  const r = spawnSync(exe, args, { windowsHide: true, encoding: 'utf8', timeout: 60000, ...options });
  if (r.error) throw r.error;
  return { status: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '' };
}
export function executable(name) {
  const override = process.env[`BENCH_${name.toUpperCase()}_EXE`];
  if (override) return path.resolve(override);
  const freezePath = path.join(ROOT, 'preparation/freeze.json');
  if (fs.existsSync(freezePath)) {
    const pinned = readJson(freezePath).runtime?.[name];
    if (pinned && fs.existsSync(pinned)) return pinned;
  }
  const bootstrapPath = path.join(ROOT, 'config/runtime.json');
  if (fs.existsSync(bootstrapPath)) {
    const pinned = readJson(bootstrapPath)[name];
    if (pinned && fs.existsSync(pinned)) return pinned;
  }
  const r = command(process.platform === 'win32' ? 'where.exe' : 'which', [process.platform === 'win32' && name === 'codex' ? 'codex.exe' : name]);
  if (r.status !== 0) throw new Error(`Executable missing: ${name}`);
  return r.stdout.trim().split(/\r?\n/)[0];
}
export function runtime() {
  const codex = executable('codex');
  const python = executable('python');
  const actualPython = command(python, ['-c', 'import sys; print(sys.executable)']).stdout.trim();
  const cli = command(codex, ['--version']);
  const py = command(actualPython, ['--version']);
  if (cli.status || py.status) throw new Error('Unable to read runtime versions');
  return { node: process.execPath, node_version: process.version, codex, codex_version: cli.stdout.trim(),
    codex_sha256: hash(fs.readFileSync(codex)), python: actualPython, python_version: py.stdout.trim(),
    shell: process.env.BENCH_PWSH_EXE ?? null };
}
const excluded = new Set(['__pycache__', '.git', '.pytest_cache', 'node_modules']);
export function files(dir, relative = '') {
  let result = [];
  for (const item of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (excluded.has(item.name)) continue;
    const rel = path.posix.join(relative, item.name);
    const full = path.join(dir, item.name);
    if (item.isSymbolicLink()) throw new Error(`Symlinks are unsupported in benchmark artifacts: ${full}`);
    if (item.isDirectory()) result.push(...files(full, rel));
    else if (item.isFile()) result.push(rel);
  }
  return result;
}
export function hashes(dir) {
  return Object.fromEntries(files(dir).map(rel => [rel, hash(fs.readFileSync(path.join(dir, rel)))]));
}
export function copyTree(source, destination) {
  // Check for symlinks before copying; preserve every substantive candidate file.
  const listing = files(source);
  fs.mkdirSync(destination, { recursive: true });
  for (const rel of listing) {
    const target = path.join(destination, rel);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(path.join(source, rel), target);
  }
}
export function experimentHashes() {
  const result = {};
  for (const folder of ['config', 'tasks', 'evaluator', 'harness']) {
    for (const [rel, sum] of Object.entries(hashes(path.join(ROOT, folder)))) result[`${folder}/${rel}`] = sum;
  }
  for (const name of ['Run-Benchmark.ps1', 'Run-Test-Strength.ps1', 'README.md', 'TASKS_AND_SCORING.md']) {
    const full = path.join(ROOT, name);
    if (fs.existsSync(full)) result[name] = hash(fs.readFileSync(full));
  }
  return result;
}
export function verifyFreeze() {
  const frozen = readJson(path.join(ROOT, 'preparation/freeze.json'));
  const actual = experimentHashes();
  const names = new Set([...Object.keys(frozen.hashes), ...Object.keys(actual)]);
  const changed = [...names].filter(k => frozen.hashes[k] !== actual[k]);
  if (changed.length) throw new Error(`Frozen experiment changed: ${changed.join(', ')}. Prepare a new experiment before measured runs.`);
  return frozen;
}
export function efforts(cfg = config()) {
  const values = cfg.reasoning_efforts;
  if (!Array.isArray(values) || !values.length || new Set(values).size !== values.length
    || values.some(e => !['low', 'medium', 'high', 'xhigh', 'max', 'ultra'].includes(e))) {
    throw new Error('reasoning_efforts must contain unique supported effort values');
  }
  return values;
}
export function schedule(cfg = config()) {
  const levels = efforts(cfg);
  const result = [];
  const cells = levels.flatMap((_, i) => cfg.models.map((model, j) => ({ model,
    reasoning_effort: levels[(i + j) % levels.length] })));
  for (let rep = 1; rep <= cfg.repetitions; rep++) {
    cfg.tasks.forEach((task, index) => {
      const offset = (index * 2) % cells.length;
      const rotation = [...cells.slice(offset), ...cells.slice(0, offset)];
      const order = rep % 2 === 1 ? rotation : rotation.reverse();
      for (const cell of order) result.push({ index: result.length + 1, task: task.id, ...cell,
        repetition: rep, run_id: `${task.id}-${cell.model.replace('gpt-', '')}-${cell.reasoning_effort}-r${rep}`,
        timeout_seconds: task.timeout_seconds });
    });
  }
  return result;
}
export function makeHome(tag, withAuth = false, effort = efforts()[0]) {
  if (!efforts().includes(effort)) throw new Error(`Unconfigured reasoning effort: ${effort}`);
  const home = path.join(ROOT, '.runtime', 'homes', `${tag}-${crypto.randomUUID()}`);
  fs.mkdirSync(home, { recursive: true });
  fs.writeFileSync(path.join(home, 'config.toml'), `model_provider = "openai"\nmodel_reasoning_effort = "${effort}"\napproval_policy = "never"\ndefault_permissions = ":workspace"\nweb_search = "disabled"\n\n[windows]\nsandbox = "unelevated"\n\n[features]\nmulti_agent = false\napps = false\nplugins = false\nfast_mode = false\n`);
  if (withAuth) {
    const sourceHome = process.env.BENCH_AUTH_HOME || process.env.CODEX_HOME || path.join(os.homedir(), '.codex');
    const source = path.join(sourceHome, 'auth.json');
    if (!fs.existsSync(source)) throw new Error(`No ChatGPT authentication in ${sourceHome}. Set BENCH_AUTH_HOME to the existing authenticated Codex home.`);
    // Credentials are never printed or copied into reports/snapshots.
    const auth = readJson(source);
    if (auth.auth_mode === 'apikey' || (!auth.tokens && auth.OPENAI_API_KEY)) {
      throw new Error('This experiment requires ChatGPT authentication, not an API key.');
    }
    fs.copyFileSync(source, path.join(home, 'auth.json'));
    // Preserve the account's current dynamic model catalog. A fresh 0.159.0
    // home otherwise falls back to the bundled catalog, which predates Sol 6.1.
    const sourceCatalog = path.join(sourceHome, 'models_cache.json');
    if (fs.existsSync(sourceCatalog)) fs.copyFileSync(sourceCatalog, path.join(home, 'models_cache.json'));
  }
  const env = { ...process.env, CODEX_HOME: home };
  delete env.OPENAI_API_KEY;
  delete env.OPENAI_BASE_URL;
  // Config credentials belong to ChatGPT; never use an ambient API endpoint.
  return { home, env };
}
export function toolEnvironment(env, rt) {
  const toolDirs = [rt.codex, rt.python, rt.node, rt.shell].filter(Boolean).map(p => path.dirname(p));
  return { ...env, PATH: [...new Set([...toolDirs, ...(env.PATH ?? env.Path ?? '').split(path.delimiter)])].join(path.delimiter) };
}
export function removeCredential(home) {
  const target = path.join(home, 'auth.json');
  const expectedRoot = path.resolve(ROOT, '.runtime', 'homes') + path.sep;
  if (!path.resolve(target).startsWith(expectedRoot)) throw new Error('Credential cleanup escaped runtime directory');
  if (fs.existsSync(target)) fs.unlinkSync(target);
}
export function assertRuntimeMatches(frozen) {
  const now = runtime();
  for (const key of ['codex_sha256', 'codex_version', 'python_version', 'node_version']) {
    if (now[key] !== frozen.runtime[key]) throw new Error(`Runtime changed: ${key}. Refreeze as a separate experiment.`);
  }
  return now;
}
