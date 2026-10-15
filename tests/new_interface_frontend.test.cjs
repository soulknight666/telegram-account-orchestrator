const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../tam/web/app.js'), 'utf8');
const START = source.indexOf('/* ---------- 后端新接口兼容层（P0/P1） ---------- */');
const END = source.indexOf('/* ---------- 账号检查结果显示增强 ---------- */', START);

function hookStub(ctx) {
  const records = new Map();
  const get = name => name.split('.').reduce((obj, key) => obj == null ? undefined : obj[key], ctx);
  const set = (name, value) => {
    const parts = name.split('.'); let obj = ctx;
    parts.slice(0, -1).forEach(key => { obj = obj[key]; }); obj[parts.at(-1)] = value;
  };
  const register = (mode, name, fn) => {
    const previous = get(name); if (typeof previous !== 'function') return false;
    records.set(name, {mode, fn});
    const wrapped = function (...args) {
      const self = this;
      if (mode === 'around') return fn.call(self, (...next) => previous.apply(self, next.length ? next : args), args, self);
      const value = previous.apply(self, args);
      if (value && typeof value.then === 'function') {
        if (mode === 'result') return value.then(v => fn.call(self, v, args, self));
        value.then(v => { fn.call(self, v, args, self); }, () => {}); return value;
      }
      const next = fn.call(self, value, args, self);
      return mode === 'result' && next !== undefined ? next : value;
    };
    set(name, wrapped); return true;
  };
  return {
    bind() { return this; },
    after: (name, fn) => register('after', name, fn),
    around: (name, fn) => register('around', name, fn),
    result: (name, fn) => register('result', name, fn),
  };
}

function makeContext(apiImpl, {demo = false} = {}) {
  const calls = [];
  const notes = [];
  const tasks = [];
  const ctx = {
    calls, notes, tasks,
    DEMO: demo,
    accounts: [{id: 1, label: 'A'}, {id: 2, label: 'B'}, {id: 3, label: 'C'}],
    sel: new Set(),
    api: async (path, opts = {}) => {
      calls.push({path, opts});
      return apiImpl(path, opts);
    },
    uiConfirm: async () => true,
    toast: (...v) => notes.push(v),
    uiTask: title => {
      const item = {title, updates: [], closed: false};
      tasks.push(item);
      return {update: v => item.updates.push(v), close: () => { item.closed = true; }};
    },
    accLabel: id => ({1: 'A', 2: 'B', 3: 'C'}[id] || '#' + id),
    setTimeout: fn => { fn(); return 1; },
    clearTimeout: () => {},
    document: {},
    window: {},
    console,
    Date,
    Map, Set, Number, String, Object, Array, Math, Promise, JSON,
    isFinite,
  };
  vm.createContext(ctx);
  ctx.TAO_HOOK = hookStub(ctx);
  vm.runInContext(`
    function tbCollect() { return {}; }
    async function runPool(ids) { return {total: ids.length, ok: ids.length, failed: 0, results: ids.map(id => ({account_id:id, ok:true}))}; }
  `, ctx);
  if (START < 0) throw new Error('compatibility block marker missing');
  const end = END > START ? END : source.length;
  vm.runInContext(source.slice(START, end), ctx);
  return ctx;
}

test('toolbox batch sends account_ids only and chunks requests', async () => {
  const calls = [];
  const c = makeContext((path, opts) => {
    calls.push({path, body: JSON.parse(opts.body)});
    return {results: JSON.parse(opts.body).account_ids.map(id => ({account_id:id, status:'ok'}))};
  });
  c.tbCollect({op: 'quick_alive'});
  const r = await c.runPool(Array.from({length: 23}, (_, i) => i + 1), async () => ({}), '工具箱 · 快速验活', 3);
  assert.equal(r.total, 23);
  const batchCalls = calls.filter(x => x.path.includes('/toolbox/'));
  assert.equal(batchCalls.length, 2);
  assert.deepEqual(batchCalls.map(x => x.body.account_ids.length), [12, 11]);
  for (const x of batchCalls) {
    assert.ok('account_ids' in x.body);
    assert.equal('ids' in x.body, false);
    assert.equal(x.body.concurrency, 3);
  }
});

test('old toolbox endpoint falls back to original per-account pool', async () => {
  const c = makeContext(() => { throw new Error('404 Not Found'); });
  c.tbCollect({op: 'quick_alive'});
  const r = await c.runPool([1, 2, 3], async () => ({}), '工具箱 · 快速验活', 2);
  assert.equal(r.total, 3);
  assert.equal(r.ok, 3);
});

test('preview groups skipped accounts and only returns runnable ids', async () => {
  let confirms = 0;
  let confirmArgs = null;
  const c = makeContext(path => {
    if (path === '/api/batch/preview') return {
      run: [1],
      skip: [
        {id: 2, reason_code: 'not_authorized', reason: '未登录'},
        {id: 3, reason_code: 'not_authorized', reason: '未登录'},
      ],
    };
    throw new Error('unexpected call');
  });
  c.uiConfirm = async args => { confirms++; confirmArgs = args; return true; };
  const g = await c.TAO_BE.gate('check', [1, 2, 3], {}, '健康检查');
  assert.deepEqual(g.ids, [1]);
  assert.equal(g.skip.length, 2);
  assert.equal(confirms, 1);
  assert.match(confirmArgs.title, /将跳过 2 个/);
  assert.match(confirmArgs.message, /未登录 × 2/);
  assert.equal(c.notes.length, 0);
});

test('all skipped preview aborts without confirmation', async () => {
  let confirms = 0;
  const c = makeContext(() => ({run: [], skip: [{id: 1, reason_code: 'not_authorized', reason: '未登录'}]}));
  c.uiConfirm = async () => { confirms++; return true; };
  const g = await c.TAO_BE.gate('check', [1], {}, '健康检查');
  assert.equal(g, null);
  assert.equal(confirms, 0);
  assert.equal(c.notes[0][0].includes('都不满足条件'), true);
});

test('incremental account refresh merges changes and deletions', async () => {
  let n = 0;
  const c = makeContext(path => {
    if (path === '/api/accounts') return [{id: 1, label: 'A'}, {id: 2, label: 'B'}];
    if (path.startsWith('/api/accounts?updated_since=')) {
      n++;
      return {accounts: [{id: 2, label: 'B2'}, {id: 3, label: 'C'}], deleted_ids: [1], server_now: 200};
    }
    throw new Error('unexpected path ' + path);
  });
  // 模拟首次启动时内存尚未有账号，第一次请求必须拉全量。
  c.accounts = [];
  const first = await c.api('/api/accounts');
  const second = await c.api('/api/accounts');
  assert.deepEqual(first.map(x => x.id), [1, 2]);
  assert.deepEqual(second.map(x => x.id), [2, 3]);
  assert.equal(second.find(x => x.id === 2).label, 'B2');
  assert.equal(n, 1);
});

test('health result metadata is human-readable and keeps the error detail', () => {
  const ctx = vm.createContext({
    accounts: [],
    document: {querySelector: () => null},
    Date,
    Number, String, Math,
    esc: value => String(value),
    setTimeout: () => 1,
    TAO_HOOK: {after: () => true, around: () => true, result: () => true, bind: () => true},
  });
  const healthEnd = source.indexOf('/* ===== UX polish pass', END);
  vm.runInContext(source.slice(END, healthEnd > END ? healthEnd : source.length), ctx);
  const text = ctx.TAO_CHECK_UI.checkText({last_check_at: Math.floor(Date.now() / 1000) - 7200, last_check_result: 'fail', last_check_error: 'AuthKeyUnregisteredError'});
  assert.match(text, /上次检查：2 小时前 · 失败/);
  assert.match(text, /AuthKeyUnregisteredError/);
});
