const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const cssPath = path.join(__dirname, '..', 'tam', 'web', 'styles.css');

function fxLayer(source) {
  const start = source.indexOf('@layer fx {');
  assert.notEqual(start, -1, 'styles.css must contain the fx layer');
  let depth = 0;
  let open = -1;
  for (let i = start; i < source.length; i += 1) {
    if (source[i] === '{') {
      if (open < 0) open = i;
      depth += 1;
    } else if (source[i] === '}') {
      depth -= 1;
      if (open >= 0 && depth === 0) return source.slice(open + 1, i);
    }
  }
  throw new Error('unterminated @layer fx block');
}

function namedLayer(source, name, nextName) {
  const start = source.indexOf(`@layer ${name} {`);
  assert.notEqual(start, -1, `styles.css must contain the ${name} layer`);
  const open = source.indexOf('{', start);
  const endMarker = nextName ? source.indexOf(`@layer ${nextName} {`, open) : source.length;
  assert.ok(open >= 0 && endMarker > open, `unable to isolate ${name} layer`);
  let depth = 0;
  for (let i = open; i < endMarker; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(open + 1, i);
    }
  }
  throw new Error(`unterminated @layer ${name} block`);
}

test('fx layer keeps generic element selectors out of component cascade', () => {
  const fx = fxLayer(fs.readFileSync(cssPath, 'utf8'));
  // ::selection, scrollbar pseudo-elements, view transitions and keyframes are
  // intentionally global. Generic element rules belong in core instead.
  const generic = /(?:^|[}\n])\s*(?:body(?:\s*>|::(?:before|after)|\s*\{)|html::after\s*\{|\*\s*\{|button\s*\{|input\s*\{|select\s*\{|textarea\s*\{|th\s*\{|td(?:\s+b)?\s*\{|tr(?:\s|:|\{)|tbody\s+tr[^,{]*\{|pre\s*\{)/m;
  assert.doesNotMatch(fx, generic);
});

test('preference layer is last and fx has no important declarations', () => {
  const source = fs.readFileSync(cssPath, 'utf8');
  assert.match(source, /^\s*@layer core, fx, prefs;/m);
  const fx = fxLayer(source);
  const core = namedLayer(source, 'core', 'fx');
  assert.equal((fx.match(/!important/g) || []).length, 0);
  assert.equal((core.match(/!important/g) || []).length, 28);
  assert.doesNotMatch(fs.readFileSync(path.join(__dirname, '..', 'tam', 'web', 'app.js'), 'utf8'), /fx-sr/);
});
