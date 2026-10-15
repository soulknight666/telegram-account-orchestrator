const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('tam/web/app.js', 'utf8');
const src = fs.readFileSync('tam/web/index.src.html', 'utf8');
const css = fs.readFileSync('tam/web/styles.css', 'utf8');

function paletteContext() {
  const input = {
    id: 'set_TAM_BATCH_CONCURRENCY', value: '', focused: false,
    isConnected: true,
    focus() { this.focused = true; document.activeElement = this; },
    closest() { return row; }
  };
  const row = {
    style: {display: 'none'}, hidden: false, scrolled: null,
    classList: {values: new Set(), add(v) { this.values.add(v); }, remove(v) { this.values.delete(v); }},
    scrollIntoView(opts) { this.scrolled = opts; },
    animate(frames, options) { this.frames = frames; this.animateOptions = options; return {cancel() {}}; },
    contains() { return true; }
  };
  const search = {value: '代理', events: 0, dispatchEvent() { this.events += 1; }};
  const palInput = {value: '', attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; }};
  const palList = {innerHTML: '', querySelector() { return null; }};
  const byId = {'set_TAM_BATCH_CONCURRENCY': input};
  const document = {
    activeElement: null,
    getElementById(id) { return id === 'palInput' ? palInput : id === 'palList' ? palList : byId[id] || null; },
    querySelector(sel) { return sel === '#palInput' ? palInput : sel === '#palList' ? palList : sel === '.fx-set-search input' ? search : null; },
    addEventListener() {}
  };
  const context = vm.createContext({
    accounts: [], readonly: false, sel: new Set(), ACTIONS: [
      {id: 'proxy', g: '系统', label: '代理体检', kw: 'proxy 代理'},
      {id: 'refresh', g: '系统', label: '刷新数据', kw: 'refresh 刷新'}
    ],
    localStorage: {getItem() { return '[]'; }, setItem() {}},
    SET_SPECS: {
      TAM_BATCH_CONCURRENCY: {key: 'TAM_BATCH_CONCURRENCY', desc: '批量并发数量', group: '性能', type: 'int', value: '8'},
      TAM_PROXY_POOL: {key: 'TAM_PROXY_POOL', desc: '代理池', group: '网络', type: 'string', value: 'pool-a'},
      TAM_SECRET_TOKEN: {key: 'TAM_SECRET_TOKEN', desc: '访问令牌', group: '密钥', type: 'secret', set: true},
      TAM_RETRY: {key: 'TAM_RETRY', desc: '重试次数', group: '性能', type: 'int', value: '3'},
      TAM_TIMEOUT: {key: 'TAM_TIMEOUT', desc: '请求超时', group: '网络', type: 'int', value: '30'},
      TAM_DEBUG: {key: 'TAM_DEBUG', desc: '调试模式', group: '界面', type: 'bool', value: '0'},
      TAM_EXTRA: {key: 'TAM_EXTRA', desc: '额外参数', group: '其它', type: 'string', value: 'x'}
    },
    SET_ORIG: {TAM_BATCH_CONCURRENCY: '8', TAM_PROXY_POOL: 'pool-a', TAM_RETRY: '3', TAM_TIMEOUT: '30', TAM_DEBUG: '0', TAM_EXTRA: 'x', TAM_SECRET_TOKEN: 'DO_NOT_LEAK'},
    setInputId: k => 'set_' + k,
    esc: v => String(v ?? '').replace(/[&<>\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
    fmtPhone: v => String(v || ''), STAT_CN: {},
    $: sel => document.querySelector(sel),
    palIcon: name => '<i data-icon="' + name + '"></i>',
    document, window: {matchMedia: () => ({matches: false})},
    Event: function Event(type) { this.type = type; },
    setTimeout, clearTimeout, getComputedStyle: () => ({display: 'none'}),
    TAO_DOM: {onAdded() { return () => {}; }}, TAO_KEYS: {esc() {}},
    closeAll() {}, switchView() {}, toast() {}
  });
  const start = app.indexOf('const PAL =');
  const end = app.indexOf('(function bindPalette()', start);
  assert.ok(start > 0 && end > start);
  vm.runInContext(app.slice(start, end), context);
  context.__fixture = {input, row, search, palInput, palList, byId};
  return context;
}

test('settings specs carry groups without changing the settings form contract', () => {
  assert.match(src, /SET_SPECS\[f\.key\]\s*=\s*Object\.assign\(\{\},\s*f,\s*\{group\}\)/);
  assert.match(src, /SET_SPECS\[s\.key\]\s*=\s*\{key:\s*s\.key,\s*type:\s*['"]secret['"],\s*group:\s*['"]密钥['"],\s*set:\s*!!s\.set\}/);
});

test('parameter query adds at most six settings and empty query keeps settings out', () => {
  const c = paletteContext();
  const rows = vm.runInContext("palBuild('TAM_BATCH_CONCURRENCY')", c);
  assert.equal(rows.filter(r => r.setting).length, 1);
  assert.ok(rows.some(r => r.head === '设置项'));
  assert.ok(rows.filter(r => r.setting).length <= 6);
  assert.equal(vm.runInContext("palBuild('').some(r => r.setting)", c), false);
  assert.ok(vm.runInContext("palBuild('并发').some(r => r.setting && r.setting.key === 'TAM_BATCH_CONCURRENCY')", c));
  assert.ok(vm.runInContext("palBuild('超时').some(r => r.setting && r.setting.key === 'TAM_TIMEOUT')", c));
});

test('setting render escapes values and never leaks a secret', () => {
  const c = paletteContext();
  c.__fixture.palInput.value = 'TAM_SECRET_TOKEN';
  vm.runInContext("PAL.items = palBuild('TAM_SECRET_TOKEN').filter(r => !r.head); palRender()", c);
  const html = c.__fixture.palList.innerHTML;
  assert.match(html, /设置项/);
  assert.match(html, /已设置/);
  assert.doesNotMatch(html, /DO_NOT_LEAK/);
  assert.match(html, /TAM_SECRET_TOKEN/);
});

test('setting command clears filtered settings search, focuses and highlights target row', async () => {
  const c = paletteContext();
  const promise = vm.runInContext("PAL.items = [{setting: SET_SPECS.TAM_BATCH_CONCURRENCY}]; palRun(0)", c);
  await promise;
  assert.equal(c.__fixture.input.focused, true);
  assert.equal(c.__fixture.row.scrolled?.block, 'center');
  assert.equal(c.__fixture.row.classList.values.has('fx-set-hit'), true);
  assert.equal(c.__fixture.search.value, '');
  assert.equal(c.__fixture.search.events, 1);
  assert.match(c.__fixture.palInput.attrs['aria-activedescendant'] || '', /^$/);
  assert.equal(JSON.stringify(c.__fixture.row.frames), JSON.stringify([{opacity: 0}, {opacity: 1}, {opacity: 0}]));
  assert.equal(c.__fixture.row.animateOptions.pseudoElement, '::after');
});

test('setting jump waits for the settings rerender before focusing the live input', async () => {
  const c = paletteContext();
  let resolveLoad;
  c.SET_LOAD_P = new Promise(resolve => { resolveLoad = resolve; });
  const liveRow = {
    style: {}, hidden: false, classList: {values: new Set(), add(v) { this.values.add(v); }, remove(v) { this.values.delete(v); }},
    scrollIntoView() {},
    animate() { return {cancel() {}}; }
  };
  const liveInput = {
    id: 'set_TAM_BATCH_CONCURRENCY', isConnected: true, focused: false,
    focus() { this.focused = true; c.document.activeElement = this; },
    closest() { return liveRow; }
  };
  c.switchView = () => setTimeout(() => {
    c.__fixture.byId[liveInput.id] = liveInput;
    resolveLoad();
  }, 20);
  const promise = vm.runInContext("PAL.items = [{setting: SET_SPECS.TAM_BATCH_CONCURRENCY}]; palRun(0)", c);
  await promise;
  await new Promise(resolve => setTimeout(resolve, 1500));
  assert.equal(c.document.activeElement?.id, 'set_TAM_BATCH_CONCURRENCY');
  assert.equal(liveInput.isConnected, true);
});

test('settings search filters rows, groups, and secret hint by key and aliases', () => {
  const row = (kw, text) => ({style: {display: ''}, dataset: {kw}, textContent: text,
    matches(selector) { return selector.includes('.row') || selector.includes('.fx-set-row'); },
    classList: {contains() { return false; }}});
  const heading = text => ({textContent: text, style: {display: ''}, matches() { return true; }});
  const h1 = heading('运行参数'), h2 = heading('密钥');
  const r1 = row('TAM_BATCH_CONCURRENCY 批量并发数量 并发 并行', 'TAM_BATCH_CONCURRENCY');
  const r2 = row('TAM_REQUEST_TIMEOUT 请求超时 超时', 'TAM_REQUEST_TIMEOUT');
  const hint = {style: {display: ''}, classList: {contains(name) { return name === 'fx-set-secret-hint'; }}, matches() { return false; }};
  const secret = row('TAM_API_ID 密钥 密钥 api', 'TAM_API_ID');
  const children = [h1, r1, r2, h2, hint, secret];
  const box = {children, querySelectorAll() { return [r1, r2, secret]; }};
  const document = {getElementById(id) { return id === 'setBody' ? box : null; }, querySelector() { return null; }};
  const ctx = vm.createContext({document});
  const start = app.indexOf('function applySettingsFilter(value)');
  const end = app.indexOf('globalThis.applySettingsFilter', start);
  vm.runInContext(app.slice(start, end), ctx);
  vm.runInContext("applySettingsFilter('zzz')", ctx);
  assert.deepEqual([r1.style.display, r2.style.display, secret.style.display], ['none', 'none', 'none']);
  assert.equal(h1.style.display, 'none');
  assert.equal(h2.style.display, 'none');
  assert.equal(hint.style.display, 'none');
  vm.runInContext("applySettingsFilter('TAM_BATCH')", ctx);
  assert.deepEqual([r1.style.display, r2.style.display, secret.style.display], ['', 'none', 'none']);
  assert.equal(h1.style.display, '');
  assert.equal(h2.style.display, 'none');
  vm.runInContext("applySettingsFilter('')", ctx);
  assert.deepEqual([r1.style.display, r2.style.display, secret.style.display], ['', '', '']);
  assert.equal(h1.style.display, '');
  assert.equal(h2.style.display, '');
  assert.equal(hint.style.display, '');
});

test('settings jump uses the palette icon and fx highlight without important', () => {
  assert.match(app, /palIcon\('sliders'\)/);
  assert.match(app, /TAO_DOM\.onAdded\('\[id\]'/);
  assert.match(css, /\.fx-set-row\.fx-set-hit\{[^}]*position:relative/);
  assert.match(css, /\.fx-set-row\.fx-set-hit::after\{[^}]*box-shadow[^}]*pointer-events:none[^}]*opacity:1/);
  assert.doesNotMatch(app, /row\.animate\(\[\{boxShadow/);
  assert.doesNotMatch(css, /\.fx-set-row\.fx-set-hit\{[^}]*!important/);
  assert.match(src, /let SET_LOAD_P\s*=\s*null/);
  assert.match(app, /function applySettingsFilter\(/);
  assert.match(app, /#setBody \.row/);
  assert.match(app, /settingAliasText/);
});
