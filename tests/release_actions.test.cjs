const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '../tam/web');
const page = fs.readFileSync(path.join(root, 'index.min.html'), 'utf8');
const appName = page.match(/src="\/release\/(app\.min\.[0-9a-f]{8}\.js)"/i)?.[1];
assert.ok(appName, 'hashed app asset reference missing');
const cssName = page.match(/href="\/release\/(styles\.min\.[0-9a-f]{8}\.css)"/i)?.[1];
const touchName = page.match(/href="\/release\/(tao-touch\.[0-9a-f]{8}\.png)"/i)?.[1];
assert.ok(cssName && touchName, 'hashed style/icon asset references missing');
const app = fs.readFileSync(path.join(root, 'release', appName), 'utf8');

function runtimeSource(source) {
  const start = source.indexOf('const TAO_ACT=');
  assert.ok(start >= 0, 'minified TAO_ACT missing');
  const end = source.indexOf('})();', start);
  assert.ok(end > start, 'minified TAO_ACT closure missing');
  return source.slice(start, end + 5);
}

function harness() {
  const ctx = vm.createContext({
    window: {addEventListener() {}},
    document: {
      documentElement: {classList: {toggle() {}}},
      querySelector() { return null; },
      addEventListener() {},
    },
    MutationObserver: class { observe() {} },
    __taoLexHas: new Set(['tableLimit']),
    __taoLexGet: n => n === 'tableLimit' ? 100 : undefined,
    __taoLexSet: () => true,
    setTimeout, setInterval, clearTimeout, clearInterval,
    console,
  });
  vm.runInContext(runtimeSource(app), ctx);
  return ctx;
}

test('release data-on handlers parse with the shipped TAO_ACT runtime', () => {
  const ctx = harness();
  const act = vm.runInContext('TAO_ACT', ctx);
  const handlers = [...page.matchAll(/data-on(?:click|change|input|load)="((?:\\.|[^"\\])*)"/g)];
  assert.ok(handlers.length > 100);
  for (const [, code] of handlers) assert.doesNotThrow(() => act.parse(code));
});

test('release lexical bridge retains top-level names used by TAO_ACT', () => {
  for (const name of ['check', 'saveAccount', 'addAccount', 'tableLimit']) {
    assert.match(app, new RegExp(`"${name}"`));
  }
  assert.match(app, /const __taoLexHas=/);
  assert.match(app, /function __taoLexGet\(/);
  assert.match(app, /function __taoLexSet\(/);
});

test('every release handler root resolves to a global function or lexical bridge name', () => {
  const lex = app.match(/const __taoLexHas=new Set\(\[(.*?)\]\)/s);
  assert.ok(lex, 'lexical bridge set missing');
  const lexicalNames = new Set(JSON.parse(`[${lex[1]}]`));
  const globalFunctions = new Set([
    ...[...app.matchAll(/function ([A-Za-z_$][\w$]*)\(/g)].map(m => m[1]),
    ...[...page.matchAll(/function ([A-Za-z_$][\w$]*)\(/g)].map(m => m[1]),
  ]);
  // Browser-provided roots are resolved by the page runtime, not by app.js.
  const reserved = new Set(['this', 'event', 'true', 'false', 'null', 'undefined', 'return', 'window', 'document', 'location', 'history', 'e']);
  const handlers = [...page.matchAll(/data-on(?:click|change|input|load)="((?:\\.|[^"\\])*)"/g)];
  for (const [, code] of handlers) {
    const stripped = code
      .replace(/'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g, '')
      // Arrow callback parameters are local names, not TAO_ACT roots.
      .replace(/(?:\([A-Za-z_$][\w$]*(?:\s*,\s*[A-Za-z_$][\w$]*)*\)|[A-Za-z_$][\w$]*)\s*=>/g, '');
    const roots = [...stripped.matchAll(/\b[A-Za-z_$][\w$]*\b/g)]
      .filter(m => m.index === 0 || stripped[m.index - 1] !== '.')
      .map(m => m[0])
      .filter(name => !reserved.has(name));
    for (const name of roots) {
      assert.ok(globalFunctions.has(name) || lexicalNames.has(name), `${name} missing from min release`);
    }
  }
  assert.doesNotMatch(app, /\bgsap\s*\./i);
  assert.doesNotMatch(app, /ScrollTrigger/);
});
