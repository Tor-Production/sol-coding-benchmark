import { spawn, spawnSync } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { createInterface } from 'node:readline';
import fs from 'node:fs';

export class Rpc extends EventEmitter {
  constructor(executable, args, { cwd, env, logPath, stderrPath } = {}) {
    super();
    this.sequence = 0;
    this.pending = new Map();
    this.messages = [];
    this.closed = false;
    this.logPath = logPath;
    this.child = spawn(executable, args, { cwd, env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child.stdin.on('error', e => this.fail(e));
    const reader = createInterface({ input: this.child.stdout });
    reader.on('line', line => {
      let message;
      try { message = JSON.parse(line); } catch { return this.emit('nonJson', line); }
      this.log('in', message);
      if (message.id !== undefined && !message.method) {
        const request = this.pending.get(message.id);
        if (!request) return;
        clearTimeout(request.timer);
        this.pending.delete(message.id);
        if (message.error) request.reject(new Error(JSON.stringify(message.error)));
        else request.resolve(message.result);
      } else if (message.method) {
        this.messages.push(message);
        this.emit('notification', message);
        if (message.id !== undefined) {
          // Benchmarks never ask a human or approve an escape from their sandbox.
          this.emit('interactiveRequest', message);
          this.send({ id: message.id, error: { code: -32601, message: 'Interactive requests are disabled for this benchmark' } });
        }
      }
    });
    this.child.stderr.on('data', chunk => {
      if (stderrPath) fs.appendFileSync(stderrPath, chunk);
    });
    this.child.on('error', e => this.fail(e));
    this.child.on('exit', (code, signal) => {
      this.closed = true;
      this.fail(new Error(`App Server exited (${code ?? signal})`));
      this.emit('closed', { code, signal });
    });
  }
  log(direction, message) {
    if (this.logPath) fs.appendFileSync(this.logPath, JSON.stringify({ at: new Date().toISOString(), direction, message }) + '\n');
  }
  fail(error) {
    for (const request of this.pending.values()) {
      clearTimeout(request.timer);
      request.reject(error);
    }
    this.pending.clear();
    this.emit('connectionFailure', error);
  }
  send(message) {
    if (this.closed) throw new Error('App Server connection is closed');
    this.log('out', message);
    this.child.stdin.write(JSON.stringify(message) + '\n');
  }
  request(method, params = {}, timeout = 30000) {
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`RPC timeout: ${method}`));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      try { this.send({ id, method, params }); } catch (e) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(e);
      }
    });
  }
  waitFor(method, predicate = () => true, timeout = 30000) {
    const found = this.messages.find(m => m.method === method && predicate(m.params));
    if (found) return Promise.resolve(found.params);
    return new Promise((resolve, reject) => {
      const clean = () => {
        clearTimeout(timer);
        this.off('notification', onMessage);
        this.off('connectionFailure', onFailure);
      };
      const onMessage = message => {
        if (message.method === method && predicate(message.params)) { clean(); resolve(message.params); }
      };
      const onFailure = error => { clean(); reject(error); };
      const timer = setTimeout(() => { clean(); reject(new Error(`Notification timeout: ${method}`)); }, timeout);
      this.on('notification', onMessage);
      this.on('connectionFailure', onFailure);
    });
  }
  async initialize() {
    const result = await this.request('initialize', { clientInfo: { name: 'sol_benchmark', title: 'Sol Benchmark', version: '1.0.0' }, capabilities: { experimentalApi: true } });
    this.send({ method: 'initialized', params: {} });
    return result;
  }
  async stop() {
    if (this.closed) return;
    const done = new Promise(resolve => this.child.once('exit', resolve));
    this.child.stdin.end();
    const timer = setTimeout(() => {
      if (process.platform === 'win32' && Number.isInteger(this.child.pid)) {
        spawnSync('taskkill.exe', ['/PID', String(this.child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore', timeout: 5000 });
      } else this.child.kill('SIGKILL');
    }, 1500);
    await done;
    clearTimeout(timer);
  }
}
