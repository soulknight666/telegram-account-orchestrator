const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../tam/web/app.js'), 'utf8');

function loadKeys() {
  const listeners = [];
  const document = {
    addEventListener(type, fn, capture) { listeners.push({type, fn, capture}); },
  };
  const ctx = vm.createContext({document, globalThis: null});
  ctx.globalThis = ctx;
  const start = source.indexOf('const TAO_KEYS = (() => {');
  const end = source.indexOf('function setAccounts', start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end) + '\nglobalThis.__keys = TAO_KEYS;', ctx);
  return {keys: ctx.__keys, listeners};
}

test('TAO_KEYS 按优先级只关闭最上层，并阻止同次 Esc 穿透', () => {
  const {keys, listeners} = loadKeys();
  const events = [];
  let menu = true, drawer = true;
  keys.esc('drawer', 10, () => drawer, () => { drawer = false; events.push('drawer'); });
  keys.esc('menu', 90, () => menu, () => { menu = false; events.push('menu'); });
  const event = {key: 'Escape', prevented: false, immediate: false,
    preventDefault() { this.prevented = true; },
    stopImmediatePropagation() { this.immediate = true; }};
  const dispatch = listeners.find(x => x.type === 'keydown' && x.capture).fn;
  dispatch(event);
  assert.deepEqual(Array.from(events), ['menu']);
  assert.equal(drawer, true);
  assert.equal(event.prevented, true);
  assert.equal(event.immediate, true);
  dispatch({key: 'Escape', preventDefault() {}, stopImmediatePropagation() {}});
  assert.deepEqual(Array.from(events), ['menu', 'drawer']);
  assert.deepEqual(Array.from(keys.list(), x => x.name), ['menu', 'drawer']);
});

test('Esc 监听集中到 TAO_KEYS，输入控件和旧直接监听不再重复触发', () => {
  const directEscape = source.match(/document\.addEventListener\(['"]keydown['"][^\n]*(?:Escape|e\.key)/g) || [];
  assert.deepEqual(directEscape, [], '不得再有带 Escape 判断的 document 监听');
  assert.equal((source.match(/document\.addEventListener\('keydown', dispatch, true\)/g) || []).length, 1);
  for (const name of ['ui-dialog', 'more-menu', 'menu', 'command-palette', 'task-detail', 'star-map', 'ai-panel', 'close-all']) {
    assert.match(source, new RegExp("TAO_KEYS\\.esc\\('" + name + "'"));
  }
});
