const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const app = fs.readFileSync(path.join(__dirname, '../tam/web/app.js'), 'utf8');
const start = app.indexOf('const TAO_ACT = (() => {');
assert.ok(start >= 0);
const end = app.indexOf('\n})();', start) + '\n})();'.length;
assert.ok(end > start);
const runtime = app.slice(start, end);

class Element {
  constructor(attrs = {}) { this.nodeType = 1; this.attrs = {...attrs}; this.listeners = {}; this.children = []; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  dispatch(name, event = {}) { for (const fn of this.listeners[name] || []) fn.call(this, event); }
  matches(selector) { return /\[data-on(?:click|change|input|load)\]/.test(selector) && Object.keys(this.attrs).some(k => k.startsWith('data-on')); }
  querySelectorAll() { return this.children; }
}

function harness() {
  const root = new Element();
  const document = {
    documentElement: root,
    addEventListener() {},
    querySelector() { return null; },
  };
  const observers = [];
  const window = {};
  let tableLimit = 100;
  const calls = [];
  const ctx = vm.createContext({
    window, document, console,
    MutationObserver: class { constructor(fn) { this.fn = fn; observers.push(this); } observe() {} },
    __taoLexHas: new Set(['tableLimit']),
    __taoLexGet: name => name === 'tableLimit' ? tableLimit : undefined,
    __taoLexSet: (name, value) => { if (name !== 'tableLimit') return false; tableLimit = value; return true; },
    setTimeout, setInterval, clearTimeout, clearInterval,
  });
  vm.runInContext(runtime, ctx);
  return {ctx, observers, calls, table: () => tableLimit};
}

test('all generated data-on attributes parse with the allowlisted interpreter', () => {
  const {ctx} = harness();
  const act = vm.runInContext('TAO_ACT', ctx);
  const source = fs.readFileSync(path.join(__dirname, '../tam/web/index.src.html'), 'utf8');
  const handlers = [...source.matchAll(/data-on(?:click|change|input|load)="((?:\\.|[^"\\])*)"/g)];
  assert.ok(handlers.length > 100);
  for (const [, code] of handlers) assert.doesNotThrow(() => act.parse(code));
});

test('row click invokes drawer, checkbox handler stops propagation, and return false prevents default', () => {
  const {ctx} = harness();
  const act = vm.runInContext('TAO_ACT', ctx);
  const row = new Element();
  let opened = 0;
  ctx.window.openDrawer = id => { opened = id; };
  act.run('openDrawer(7)', row, {});
  assert.equal(opened, 7);
  let stopped = false;
  row.setAttribute('data-onclick', 'event.stopPropagation()');
  const event = {stopPropagation: () => { stopped = true; }};
  act.run(row.getAttribute('data-onclick'), row, event);
  assert.equal(stopped, true);
  let prevented = false;
  act.run('return false', row, {preventDefault: () => { prevented = true; }});
  assert.equal(prevented, true);
});

test('lexical bridge setter makes tableLimit += 300 observable to action handlers', () => {
  const {ctx, table} = harness();
  const act = vm.runInContext('TAO_ACT', ctx);
  let rendered = 0;
  ctx.window.render = () => { rendered += 1; };
  act.run('tableLimit += 300;render()', new Element(), {});
  assert.equal(table(), 400);
  assert.equal(rendered, 1);
});

test('MutationObserver binding attaches data-on handlers to dynamically inserted elements', () => {
  const {ctx, observers} = harness();
  const act = vm.runInContext('TAO_ACT', ctx);
  let opened = 0;
  ctx.window.openDrawer = id => { opened = id; };
  const el = new Element({'data-onclick': 'openDrawer(9)'});
  observers[0].fn([{type: 'childList', addedNodes: [el]}]);
  el.dispatch('click', {});
  assert.equal(opened, 9);
});
