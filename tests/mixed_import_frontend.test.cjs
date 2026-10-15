const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../tam/web/app.js'), 'utf8');
const mixedStart = source.indexOf('function openMixed(files)');
const mixedEnd = source.indexOf('/* =================== 严格审查后的修补', mixedStart);
assert.ok(mixedStart > 0 && mixedEnd > mixedStart, 'mixed import function marker missing');
const mixedSource = source.slice(mixedStart, mixedEnd);

class Node {
  constructor(kind = 'div') { this.kind = kind; this.id = ''; this.value = ''; this.innerHTML = ''; this.textContent = ''; this.disabled = false; this.onclick = null; this.oninput = null; this.children = []; this.parts = Object.create(null); this.dataset = {}; }
  setAttribute(k, v) { this[k] = String(v); }
  appendChild(x) { this.children.push(x); if (x.id) this.parts['#' + x.id] = x; return x; }
  querySelector(sel) {
    if (sel === '.fx-mx-res') return this.parts.res || (this.parts.res = new Node());
    if (sel === '.fx-mx-go') return this.parts.btn || (this.parts.btn = new Node('button'));
    if (sel === '.fx-mx-files') return this.parts.files || (this.parts.files = new Node());
    return this.parts[sel] || null;
  }
  querySelectorAll(sel) {
    if (sel === 'input') return ['mxTags', 'mxProxy', 'mxLabel', 'mxPass', 'mxTwofa'].map(id => this.parts['#' + id] || (this.parts['#' + id] = Object.assign(new Node('input'), {id})));
    return [];
  }
}

function makeContext(fetchImpl) {
  const root = new Node('body');
  const modal = new Node('div'); modal.id = 'mMixed';
  const token = Object.assign(new Node('input'), {id: 'token', value: 'TEST_TOKEN'});
  const calls = [], refreshes = [], toasts = [];
  const document = {
    body: root,
    createElement: () => new Node(),
    getElementById: id => id === 'mMixed' ? (document._modal || null) : id === 'token' ? token : (document._modal && document._modal.parts['#' + id]) || null,
    _modal: null,
  };
  const originalAppend = root.appendChild.bind(root);
  root.appendChild = el => { if (el.id === 'mMixed') document._modal = el; return originalAppend(el); };
  const FormDataMock = function () { this.fields = []; this.append = (name, value) => this.fields.push({name, value}); };
  const ctx = vm.createContext({
    document, FormData: FormDataMock, DEMO: false, fetch: async (...args) => { calls.push(args); return fetchImpl(...args); },
    closeAll: () => {}, openModal: () => {}, refresh: async () => { refreshes.push(true); },
    toast: (...args) => toasts.push(args), TAO_ERR: {describe: (code, detail) => String(detail || code || '导入失败')},
    console, Promise, String, Number, Object, Array, JSON, Math, setTimeout: fn => { fn(); return 1; }, clearTimeout: () => {},
  });
  vm.runInContext('function $(s){ return s === "#token" ? document.getElementById("token") : null; }', ctx);
  vm.runInContext(mixedSource, ctx);
  return {ctx, document, modal, calls, refreshes, toasts};
}

function response(status, body) {
  return {status, ok: status >= 200 && status < 300, text: async () => JSON.stringify(body)};
}

test('混合导入先 dry_run 分析，再以相同标签正式导入并刷新', async () => {
  const queue = [
    response(200, {dry_run: true, tdata: {found: 1}, session: {found: 1}, items: [{kind: 'tdata', status: 'ready'}, {kind: 'session', status: 'ready'}], skipped: []}),
    response(200, {dry_run: false, tdata: {imported: 1}, session: {imported: 1}, items: [{kind: 'tdata', status: 'imported'}, {kind: 'session', status: 'imported'}], skipped: []}),
  ];
  const c = makeContext(async () => queue.shift());
  c.ctx.openMixed([{name: 'accounts.zip', size: 1024}]);
  const m = c.document._modal;
  m.parts['#mxTags'].value = 'batch-a';
  m.parts['#mxProxy'].value = 'socks5://proxy:1080';
  m.parts['#mxLabel'].value = 'imported-';
  m.parts['#mxPass'].value = 'local-pass';
  m.parts['#mxTwofa'].value = 'cloud-pass';
  await m.parts.btn.onclick();
  assert.equal(c.calls[0][0], '/api/import/mixed');
  assert.equal(c.calls[0][1].method, 'POST');
  assert.equal(c.calls[0][1].body.fields.find(x => x.name === 'dry_run').value, 'true');
  assert.equal(c.calls[0][1].body.fields.find(x => x.name === 'tags').value, 'batch-a');
  assert.equal(c.calls[0][1].body.fields.find(x => x.name === 'proxy').value, 'socks5://proxy:1080');
  assert.equal(c.calls[0][1].body.fields.find(x => x.name === 'label').value, 'imported-');
  assert.equal(c.calls[0][1].body.fields.find(x => x.name === 'tdata_passcode').value, 'local-pass');
  assert.equal(c.calls[0][1].body.fields.find(x => x.name === 'twofa').value, 'cloud-pass');
  assert.match(m.parts.res.innerHTML, /可导入/);
  await m.parts.btn.onclick();
  assert.equal(c.calls[1][1].body.fields.find(x => x.name === 'dry_run').value, 'false');
  assert.equal(c.refreshes.length, 1);
  assert.match(m.parts.res.innerHTML, /已导入/);
});

test('混合导入后修改选项会要求重新分析', async () => {
  const c = makeContext(async () => response(200, {tdata: {found: 1}, session: {found: 0}, items: [{kind: 'tdata', status: 'ready'}], skipped: []}));
  c.ctx.openMixed([{name: 'a.zip', size: 1}]);
  const m = c.document._modal;
  await m.parts.btn.onclick();
  assert.match(m.parts.btn.textContent, /导入/);
  m.parts['#mxProxy'].value = 'socks5://proxy:1080';
  m.parts['#mxProxy'].oninput();
  assert.equal(m.parts.btn.textContent, '重新分析');
  assert.match(m.parts.res.innerHTML, /选项已变化/);
});

test('后端 404/405 显示更新后端或拆包提示', async () => {
  const c = makeContext(async () => response(404, {detail: 'Not Found'}));
  c.ctx.openMixed([{name: 'a.zip', size: 1}]);
  const m = c.document._modal;
  await m.parts.btn.onclick();
  assert.match(m.parts.res.innerHTML, /后端还不支持混合导入/);
  assert.match(m.parts.res.innerHTML, /ZIP 拆包/);
});

test('413 提示可调大混合 ZIP 上传上限', async () => {
  const c = makeContext(async () => response(413, {detail: 'too large'}));
  c.ctx.openMixed([{name: 'a.zip', size: 1}]);
  await c.document._modal.parts.btn.onclick();
  assert.match(c.document._modal.parts.res.innerHTML, /可调大 TAM_MIXED_MAX_UPLOAD_MB/);
});

test('工具箱步骤条没有选中工具时显示未选且不抛异常', () => {
  const start = source.indexOf('function steps()');
  const end = source.indexOf('\n  const box = document.getElementById(\'tbBody\')', start);
  const step = source.slice(start, end);
  const el = {classList: {add() {}}, innerHTML: ''};
  const ctx = vm.createContext({
    document: {querySelector: () => el, getElementById: id => id === 'tbOp' ? {selectedOptions: []} : null},
    sel: new Set(), TAO_FX5: {E: x => String(x)},
  });
  vm.runInContext(step + '\nsteps();', ctx);
  assert.match(el.innerHTML, /未选/);
});
