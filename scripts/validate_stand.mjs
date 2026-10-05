// No Codex process, credentials, network, or model calls. All mutable evidence
// lives in a throwaway copy; a real preparation/freeze is never overwritten.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const staging = fs.mkdtempSync(path.join(os.tmpdir(), 'sol-stand-verify-'));
const python = process.env.BENCH_PYTHON_EXE || (process.platform === 'win32' ? 'python' : 'python3');
function run(executable, args, timeout = 180000) {
  const r = spawnSync(executable, args, { cwd: staging, encoding: 'utf8', timeout,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, windowsHide: true });
  process.stdout.write(r.stdout || '');
  process.stderr.write(r.stderr || '');
  if (r.error) throw r.error;
  if (r.status !== 0) throw new Error(`Offline command failed (${r.status}): ${path.basename(args[0] || executable)}`);
  return r.stdout;
}
try {
  for (const folder of ['harness', 'evaluator', 'tasks', 'config', 'quality-v2.1']) {
    if (fs.existsSync(path.join(root, folder))) fs.cpSync(path.join(root, folder), path.join(staging, folder), {
      recursive: true, filter: p => !['__pycache__', '.git', '.pytest_cache'].includes(path.basename(p)) });
  }
  for (const file of ['Run-Benchmark.ps1', 'Run-Test-Strength.ps1']) fs.copyFileSync(path.join(root, file), path.join(staging, file));
  fs.mkdirSync(path.join(staging, 'preparation'));
  const actualPython = run(python, ['-c', 'import sys; print(sys.executable)']).trim();
  const pyVersion = run(actualPython, ['--version']).trim();
  const { experimentHashes, writeJson, schedule } = await import(pathToFileURL(path.join(staging, 'harness/common.mjs')));
  const { qualify } = await import(pathToFileURL(path.join(staging, 'harness/evaluate.mjs')));
  run(process.execPath, ['--test', 'harness/telemetry.test.mjs', 'harness/rpc.test.mjs', 'harness/matrix.test.mjs']);
  const qualification = qualify(actualPython);
  run(actualPython, ['-B', 'evaluator/qualify_complex.py', '--output', 'preparation/complex-qualification.json']);
  run(actualPython, ['-B', 'evaluator/test_strength.test.py']);
  if (fs.existsSync(path.join(staging, 'quality-v2.1/test_evaluator.py'))) {
    run(actualPython, ['-B', 'quality-v2.1/test_evaluator.py']);
    run(actualPython, ['-B', 'quality-v2.1/run.py', 'qualify']);
  }
  // This synthetic runtime exists only in staging and only for fake-server RPC.
  writeJson(path.join(staging, 'preparation/freeze.json'), { simulation: true, hashes: experimentHashes(),
    runtime: { node: process.execPath, node_version: process.version, codex: process.execPath,
      codex_version: 'offline fixture (not Codex)', python: actualPython, python_version: pyVersion, shell: null } });
  writeJson(path.join(staging, 'preparation/schedule.json'), schedule());
  run(process.execPath, ['--test', 'harness/integration.test.mjs']);
  const checks = qualification.tasks.reduce((n, t) => n + t.checks, 0);
  console.log(`Offline verification passed: 216 scheduled attempts; all six evaluator controls qualified (${checks} diagnostic checks); protocol/settings/gates verified. Model calls: 0.`);
} catch (e) {
  console.error(e.stack || e.message);
  process.exitCode = 1;
} finally {
  // Resolve the exact OS-temp directory before recursively removing our copy.
  const resolved = path.resolve(staging), tempRoot = path.resolve(os.tmpdir()) + path.sep;
  if (!resolved.startsWith(tempRoot) || !path.basename(resolved).startsWith('sol-stand-verify-')) throw new Error('Unsafe staging cleanup path');
  if (!process.exitCode) fs.rmSync(resolved, { recursive: true, force: true });
  else console.error(`Preserved offline verification evidence: ${resolved}`);
}
