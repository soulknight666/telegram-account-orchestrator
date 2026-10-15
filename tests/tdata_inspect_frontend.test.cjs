const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../tam/web/app.js'), 'utf8');
const start = source.indexOf('async function doTdataInspect()');
const end = source.indexOf('/* 自动刷新 */', start);
assert.ok(start >= 0 && end > start);
const errStart = source.indexOf('const TAO_ERR = (() => {');
const errEnd = source.indexOf('async function demoApi', errStart);

class Element {
  constructor() { this.value = ''; this.checked = false; this.files = []; this.innerHTML = ''; this.textContent = ''; this.disabled = false; this.listeners = {}; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  emit(name) { for (const fn of this.listeners[name] || []) fn({target: this}); }
  setAttribute(name, value) { this[name] = value; }
}

function reply(status, body) {
  return {status, ok: status >= 200 && status < 300, text: async () => JSON.stringify(body)};
}

function harness(fetchImpl = async () => reply(200, {items: [], skipped: []})) {
  const elements = Object.fromEntries(['tUpload', 'tPass', 'tDebug', 'token', 'tPath', 'tScan', 'tLabel', 'tProxy', 'tTags'].map(id => [id, new Element()]));
  elements.tUpload.files = [{name: 'one.zip'}];
  elements.token.value = 'TEST_TOKEN';
  const button = new Element(); button.textContent = '先体检不导入';
  button.getAttribute = () => 'doTdataInspect()';
  let box = null, mode = 'upload';
  const body = {appendChild(el) { box = el; return el; }};
  const modal = {querySelector: s => s === '.body' ? body : s === '.fx-ti-res' ? box : null, querySelectorAll: () => [button]};
  const calls = [], apiCalls = [], toasts = [], output = [];
  const document = {
    createElement: () => new Element(),
    getElementById: id => id === 'mTdata' ? modal : elements[id] || null,
    querySelector: s => s.includes('tdataMode') ? {value: mode} : s === '#mTdata .fx-ti-res' ? box : elements[s.slice(1)] || null,
  };
  const ctx = vm.createContext({
    document, $: s => document.querySelector(s), DEMO: false,
    FormData: class { constructor() { this.fields = []; } append(name, value) { this.fields.push({name, value}); } },
    fetch: async (...args) => { calls.push(args); return fetchImpl(...args); },
    localStorage: {getItem: () => ''},
    toast: (...args) => toasts.push(args), out: (...args) => output.push(args),
    renderTdataReport: x => x.path,
    tdataBody: () => JSON.stringify({path: elements.tPath.value, password: elements.tPass.value || null}),
    setTimeout, clearTimeout, console,
  });
  vm.runInContext(source.slice(errStart, errEnd), ctx);
  ctx.api = async (...args) => { apiCalls.push(args); return {reports: [{path: 'server/tdata', ok: true}]}; };
  ctx.TAO_HOOK = {
    around(name, fn) {
      const base = ctx[name];
      ctx[name] = function (...args) {
        return fn.call(this, (...next) => base.apply(this, next.length ? next : args), args, this);
      };
      return true;
    },
  };
  vm.runInContext(source.slice(start, end), ctx);
  return {ctx, elements, button, calls, apiCalls, toasts, output, box: () => box, mode: v => { mode = v; }};
}

test('上传体检依次发送 file/tdata_passcode/debug，按钮忙时禁用且禁止重复提交', async () => {
  let resolveFirst;
  const c = harness(async () => c.calls.length === 1 ? new Promise(resolve => { resolveFirst = resolve; }) : reply(200, {items: [{path: 'b/tdata', status: 'ready'}]}));
  const files = [{name: 'one.zip'}, {name: 'two.zip'}];
  c.elements.tUpload.files = files;
  c.elements.tPass.value = 'local-pass'; c.elements.tDebug.checked = true;
  const pending = c.ctx.doTdataInspect();
  assert.equal(c.button.disabled, true); assert.equal(c.button.textContent, '体检中…');
  assert.equal(c.calls.length, 1);
  await c.ctx.doTdataInspect(); assert.equal(c.calls.length, 1);
  resolveFirst(reply(200, {items: [{path: 'a/tdata', status: 'ready'}]}));
  await pending;
  assert.equal(c.calls.length, 2);
  c.calls.forEach(([url, request], i) => {
    assert.equal(url, '/api/import/tdata/inspect'); assert.equal(request.method, 'POST');
    assert.equal(request.body.fields.find(x => x.name === 'file').value, files[i]);
    assert.equal(request.body.fields.find(x => x.name === 'tdata_passcode').value, 'local-pass');
    assert.equal(request.body.fields.find(x => x.name === 'debug').value, 'true');
    assert.equal(request.headers.Authorization, 'Bearer TEST_TOKEN');
    assert.equal(request.headers['Content-Type'], undefined);
  });
  assert.match(c.box().innerHTML, /one\.zip \/ a\/tdata/); assert.match(c.box().innerHTML, /two\.zip \/ b\/tdata/);
  assert.equal(c.button.disabled, false); assert.equal(c.button.textContent, '先体检不导入');
  assert.equal(c.apiCalls.length, 0);
});

test('重复账号与本地密码错误使用统一中文提示，显示 existing_id 和重新体检指引', async () => {
  const c = harness(async () => reply(200, {items: [
    {path: 'same/tdata', phone: '+12025550123', dc: 2, status: 'duplicate', code: 'import_duplicate', params: {existing_id: 42}},
    {path: 'locked/tdata', status: 'failed', code: 'tdata_passcode', reason: 'PasswordError', needs_password: true},
  ], skipped: [{path: 'readme.txt'}]}));
  await c.ctx.doTdataInspect();
  const html = c.box().innerHTML;
  assert.match(html, /已存在 1/); assert.match(html, /#42/); assert.match(html, /不可用 1/);
  assert.match(html, /本地密码/); assert.match(html, /再体检一次/); assert.match(html, /忽略的文件 1/);
  assert.match(html, /DC2/); assert.doesNotMatch(html, /PasswordError/);
});

test('404/405 上传体检显示旧后端提示并恢复按钮', async () => {
  for (const status of [404, 405]) {
    const c = harness(async () => reply(status, {detail: 'Not Found'}));
    await c.ctx.doTdataInspect();
    assert.match(c.toasts.map(x => x[0]).join('\n'), /后端还不支持上传体检/);
    assert.equal(c.button.disabled, false);
    assert.equal(c.box().innerHTML, '');
  }
});

test('损坏 ZIP 按文件显示 import_bad_archive 中文错误，并继续处理后续 ZIP', async () => {
  const c = harness(async () => c.calls.length === 1 ? reply(400, {code: 'import_bad_archive', detail: 'BadZipFile'}) : reply(200, {items: [{path: 'ok/tdata', status: 'ready'}]}));
  c.elements.tUpload.files = [{name: '<bad>.zip'}, {name: 'good.zip'}];
  await c.ctx.doTdataInspect();
  assert.equal(c.calls.length, 2);
  const html = c.box().innerHTML;
  assert.match(html, /&lt;bad&gt;\.zip/); assert.match(html, /上传失败 1/); assert.match(html, /ZIP 损坏/);
  assert.match(html, /可导入 1/); assert.doesNotMatch(html, /BadZipFile|<bad>/);
});

test('413 明确提示上传上限，空 debug 和密码不会发送', async () => {
  const c = harness(async () => reply(413, {detail: 'too large'}));
  await c.ctx.doTdataInspect();
  assert.match(c.box().innerHTML, /超过.*上传上限/);
  assert.deepEqual(c.calls[0][1].body.fields.map(x => x.name), ['file']);
  assert.equal(c.button.disabled, false);
});

test('服务端路径模式继续调用原来的 JSON 体检接口', async () => {
  const c = harness(); c.mode('path'); c.elements.tPath.value = 'server/tdata';
  await c.ctx.doTdataInspect();
  assert.equal(c.calls.length, 0); assert.equal(c.apiCalls.length, 1);
  assert.equal(c.apiCalls[0][0], '/api/tdata/inspect');
  assert.equal(c.apiCalls[0][1].method, 'POST');
  assert.equal(JSON.parse(c.apiCalls[0][1].body).path, 'server/tdata');
  assert.match(c.output[0][0], /server\/tdata/);
});

test('更换文件和修改本地密码清空旧体检结果', async () => {
  const c = harness(async () => reply(200, {items: [{path: 'old/tdata', status: 'ready'}]}));
  await c.ctx.doTdataInspect(); c.elements.tUpload.emit('change'); assert.equal(c.box().innerHTML, '');
  await c.ctx.doTdataInspect(); c.elements.tPass.emit('input'); assert.equal(c.box().innerHTML, '');
});

test('体检中修改文件后，旧请求不回填结果，也不继续发送下一份文件', async () => {
  let resolveFirst;
  const c = harness(async () => new Promise(resolve => { resolveFirst = resolve; }));
  c.elements.tUpload.files = [{name: 'old.zip'}, {name: 'later.zip'}];
  const pending = c.ctx.doTdataInspect();
  c.elements.tUpload.files = [{name: 'new.zip'}]; c.elements.tUpload.emit('change');
  resolveFirst(reply(200, {items: [{path: 'old/tdata', status: 'ready'}]})); await pending;
  assert.equal(c.box().innerHTML, ''); assert.equal(c.calls.length, 1); assert.equal(c.button.disabled, false);
});

test('体检明细最多 300 行，所有目录及错误文本均转义', async () => {
  const c = harness(async () => reply(200, {items: Array.from({length: 305}, (_, i) => ({path: `<dir>${i}/tdata`, status: 'ready'}))}));
  await c.ctx.doTdataInspect(); const html = c.box().innerHTML;
  assert.equal((html.match(/<tr>/g) || []).length, 301);
  assert.match(html, /只显示前 300 条/); assert.match(html, /可导入 305/);
  assert.doesNotMatch(html, /<dir>/);
});
