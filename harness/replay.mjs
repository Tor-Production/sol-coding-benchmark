// Replay one published snapshot without Codex, credentials, or inference.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { ROOT, readJson, hashes, copyTree, executable, writeJson, timestamp } from './common.mjs';
import { gradeWorkspace } from './evaluate.mjs';

const runId = process.argv[2];
let temporary;
try {
  const archive = readJson(path.join(ROOT, 'results/results.json'));
  const row = archive.runs.find(r => r.run_id === runId);
  if (!row) throw new Error('Provide an exact run_id from results/results.json');
  const entry = readJson(path.join(ROOT, 'results/candidate-manifests.json'))[runId];
  if (!entry) throw new Error('Archived candidate manifest is missing');
  const snapshot = path.resolve(ROOT, entry.directory);
  const expectedRoot = path.resolve(ROOT, 'candidates') + path.sep;
  if (!snapshot.startsWith(expectedRoot)) throw new Error('Candidate path escaped archive');
  const actual = hashes(snapshot);
  if (Object.keys(actual).length !== Object.keys(entry.files).length
      || Object.entries(entry.files).some(([name, digest]) => actual[name] !== digest)) throw new Error('Archived candidate changed');
  temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'sol-main-replay-'));
  copyTree(snapshot, temporary);
  const output = path.join(ROOT, 'analysis/replay', `${runId}-${Date.now()}`);
  const grading = gradeWorkspace(row.task, temporary, output, executable('python'));
  const result = { run_id: runId, replayed_at: timestamp(), model_calls: 0,
    archive_status: row.status, archive_main_score: row.grading?.score ?? null,
    archive_accepted_and_completed: row.main_accepted_and_completed ?? (row.status === 'completed' && row.grading?.accepted === true),
    replay_grading: grading, note: 'Replayed code checks do not change the original model delivery outcome or archived measurements.' };
  writeJson(path.join(output, 'result.json'), result);
  console.log(JSON.stringify({ ...result, output }, null, 2));
} catch (e) {
  console.error(e.stack || e.message);
  process.exitCode = 1;
} finally {
  if (temporary) {
    const resolved = path.resolve(temporary), allowed = path.resolve(os.tmpdir()) + path.sep;
    if (!resolved.startsWith(allowed) || !path.basename(resolved).startsWith('sol-main-replay-')) throw new Error('Unsafe replay cleanup path');
    fs.rmSync(resolved, { recursive: true, force: true });
  }
}
