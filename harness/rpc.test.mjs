import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { ROOT } from './common.mjs';
import { Rpc } from './rpc.mjs';

const fixture = () => new Rpc(process.execPath, [path.join(ROOT, 'harness/fixtures/fake-server.mjs')], { cwd: ROOT });
test('RPC handshake, fresh thread, completion before acknowledgement', async () => {
  const rpc = fixture();
  try {
    assert.equal((await rpc.initialize()).userAgent, 'offline_fixture');
    const thread = await rpc.request('thread/start', { cwd: ROOT, model: 'gpt-6-sol' });
    const waiting = rpc.waitFor('turn/completed', p => p.threadId === thread.thread.id);
    const turn = await rpc.request('turn/start', { threadId: thread.thread.id });
    assert.equal((await waiting).turn.id, turn.turn.id);
  } finally { await rpc.stop(); }
});
test('RPC errors and request timeouts reject cleanly', async () => {
  const rpc = fixture();
  try {
    await rpc.initialize();
    await assert.rejects(rpc.request('fixture/error'), /fixture failure/);
    await assert.rejects(rpc.request('fixture/hang', {}, 30), /RPC timeout/);
    await assert.rejects(rpc.waitFor('unknown', () => true, 30), /Notification timeout/);
  } finally { await rpc.stop(); }
});
test('Unexpected server requests are rejected and exposed', async () => {
  const rpc = fixture();
  let seen = false;
  rpc.on('interactiveRequest', () => { seen = true; });
  try {
    await rpc.initialize();
    await rpc.request('fixture/request');
    assert.equal(seen, true);
  } finally { await rpc.stop(); }
});
test('Process death rejects outstanding RPC requests', async () => {
  const rpc = fixture();
  try {
    await rpc.initialize();
    await assert.rejects(rpc.request('fixture/exit'), /App Server exited/);
  } finally { await rpc.stop(); }
});
