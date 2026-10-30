// node bridge/ComfyUI-CreationCanvas/tests/check_connection.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const path = require('node:path');
const timers = new Map(), listeners = new Map();
let serial = 0, queued = 0, extension, fetcher;
const graph = {nodes: [{id: 1, type: 'Test'}]};
const manager = {activeWorkflow: {path: 'original'}, createNewTemporary(name, document) {
  return {path: 'temporary/' + name, isTemporary: true};
}};
const app = {graph: {serialize: () => structuredClone(graph)}, extensionManager: {workflow: manager},
  registerExtension(value) {extension = value;},
  async loadGraphData(document, a, b, workflow) {manager.activeWorkflow = workflow; return true;},
  async graphToPrompt() {return {output: {1: {class_type: 'Test'}}};}
};
const context = {app, api: {fetchApi: (...args) => fetcher(...args), addEventListener() {},
    async queuePrompt() {queued++; return {prompt_id: 'once', number: 1};}},
  crypto: webcrypto, AbortController, structuredClone, console: {warn() {}},
  localStorage: {setItem() {}}, window: {addEventListener(type, callback) {listeners.set(type, callback);}},
  setTimeout(callback, ms) {const id = ++serial; timers.set(id, {callback, ms}); return id;},
  clearTimeout(id) {timers.delete(id);},
  setInterval(callback, ms) {const id = ++serial; timers.set(id, {callback, ms, interval: true}); return id;},
  clearInterval(id) {timers.delete(id);}
};
vm.createContext(context);
const source = fs.readFileSync(path.join(__dirname, '../web/creation_canvas_bridge.js'), 'utf8').replace(/^import .*;\r?\n/gm, '');
vm.runInContext(source, context);
const evaluate = source => vm.runInContext(source, context);
const successful = async route => ({ok: true, async json() {
  return route.includes('commands?') ? {commands: []} : {revision: 1, workflowId: null};
}});
const hanging = (route, options) => new Promise((resolve, reject) => {
  options.signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
});
async function expire() {
  for (let i = 0; i < 5; i++) await Promise.resolve();
  const entry = [...timers.entries()].find(([, value]) => value.ms === 8000);
  assert.ok(entry, 'an in-flight bridge request must have a deadline');
  timers.delete(entry[0]); entry[1].callback();
}
async function check() {
  fetcher = hanging;
  const stuck = evaluate('tick()');
  assert.equal(evaluate('busy'), true);
  await expire(); await stuck;
  assert.equal(evaluate('busy'), false, 'timeout must release the sync lock');
  assert.ok([...timers.values()].some(timer => timer.ms === 1500), 'sync must schedule a retry');
  fetcher = successful;
  await evaluate('tick()');
  assert.equal(evaluate('initial'), false, 'the following attempt must recover');

  evaluate('busy=true'); fetcher = hanging;
  const heartbeat = evaluate('keepAlive()');
  await expire(); await heartbeat;
  assert.equal(evaluate('heartbeatBusy'), false, 'timeout must release the heartbeat lock');
  evaluate('busy=false');

  // Deadline also covers a response whose JSON body never finishes.
  fetcher = async (route, options) => ({ok: true, json: () => hanging(route, options)});
  const body = evaluate('request("state")');
  await expire(); await assert.rejects(body, /超时/);

  fetcher = (route, options) => route.endsWith('/result') ? hanging(route, options) : successful(route);
  context.command = {opId: 'same-operation', type: 'execute', workflow: graph, workflowId: 'card'};
  const executing = evaluate('applyCommand(command)');
  // Loading, prompt compilation, and queueing each resolve asynchronously.
  for (let i = 0; i < 15; i++) await Promise.resolve();
  await expire(); await assert.rejects(executing, /超时/);
  assert.equal(queued, 1);
  assert.equal(evaluate('unacknowledged.opId'), 'same-operation');
  const acknowledgements = [];
  fetcher = async (route, options) => {
    if (route.endsWith('/result')) acknowledgements.push(JSON.parse(options.body).opId);
    return successful(route);
  };
  await evaluate('flushAcknowledgement()');
  assert.deepEqual(acknowledgements, ['same-operation']);
  assert.equal(evaluate('unacknowledged'), null);
  assert.equal(queued, 1, 'retrying the original acknowledgement must never queue twice');

  timers.clear(); await extension.setup();
  listeners.get('pagehide')();
  assert.equal(evaluate('paused'), true);
  assert.equal(timers.size, 0);
  listeners.get('pageshow')({persisted: true});
  listeners.get('pageshow')({persisted: true});
  assert.equal(evaluate('paused'), false);
  assert.equal([...timers.values()].filter(timer => timer.interval).length, 1);
  assert.equal([...timers.values()].filter(timer => !timer.interval).length, 1);
  console.log('桥接连接检查通过：请求超时、恢复同步、心跳解锁、回执幂等与页面返回恢复。');
}
check().catch(error => {console.error(error); process.exitCode = 1;});
