const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'tam/web/app.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'tam/web/styles.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'tam/web/index.src.html'), 'utf8');

function button(key, label, on, quick) {
  const start = js.indexOf('function renderFilterButton(');
  assert.ok(start >= 0, 'shared filter button renderer is missing');
  const end = js.indexOf('\nfunction ', start + 1);
  const context = vm.createContext({esc: value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]))});
  vm.runInContext(js.slice(start, end), context);
  context.args = [key, label, on, quick];
  return vm.runInContext('renderFilterButton(...args)', context);
}

test('status buttons retain native keyboard activation and announce selection', () => {
  const out = button('active', 'active 2', true, false);
  assert.match(out, /type="button"/);
  assert.match(out, /class="chip on"/);
  assert.match(out, /aria-pressed="true"/);
  assert.match(out, /setFilter\(&quot;active&quot;\)/);
  assert.match(out, /<span class="inner">active 2<\/span>/);
});

test('quick filters retain toggling, numeric counts and long names', () => {
  const out = button('twofa_uncertain', '改密待核对 10000', false, true);
  assert.match(out, /aria-pressed="false"/);
  assert.match(out, /setQuickFilter\(&quot;twofa_uncertain&quot;\)/);
  assert.match(out, /<span class="inner">改密待核对 10000<\/span>/);
  assert.match(js, /qf\.map\(\(\[k, lab\]\) => renderFilterButton\(/);
  assert.match(js, /\.map\(\(\[k, t\]\) => renderFilterButton\(/);
});

test('filter label and handler arguments remain HTML escaped', () => {
  const out = button('custom"\'<', '<b>零个</b>', false, false);
  assert.doesNotMatch(out, /<b>/);
  assert.match(out, /&lt;b&gt;零个&lt;\/b&gt;/);
  assert.match(out, /&quot;/);
  assert.match(out, /&#39;/);
  const encoded = out.match(/onclick="([^"]*)"/)[1];
  const handler = encoded.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  let called;
  vm.runInNewContext(handler, {setFilter: value => {called = value;}});
  assert.equal(called, 'custom"\'<');
});

test('status and advanced filters use theme tokens with native focus states', () => {
  assert.match(css, /\.seg \.chip\.on\{[^}]*var\(--accent-text\)/);
  assert.match(css, /\.filter-pop[\s\S]*var\(--line\)/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(html, /id="chips"[^>]*role="group"/);
  assert.match(html, /id="qfilters"[^>]*role="group"/);
});
