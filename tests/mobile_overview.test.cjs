const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const web = path.join(__dirname, '..', 'tam', 'web');
const css = fs.readFileSync(path.join(web, 'styles.css'), 'utf8');
const html = fs.readFileSync(path.join(web, 'index.src.html'), 'utf8');
const mobile = css.slice(css.indexOf('@media (max-width:720px){'), css.indexOf('@media (prefers-reduced-motion:reduce)'));
const rule = (selector) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const matches = [...mobile.matchAll(new RegExp(escaped + '\\{([^}]+)\\}', 'g'))];
  assert.ok(matches.length, 'Missing mobile rule: ' + selector);
  return matches.map(m => m[1]).join(';');
};

test('mobile overview is a native horizontal scroller, not squeezed columns', () => {
  const rail = rule('.rail');
  assert.match(rail, /display:flex/);
  assert.match(rail, /flex-direction:row/);
  assert.match(rail, /overflow-x:auto/);
  assert.match(rail, /overflow-y:hidden/);
  assert.match(rail, /scroll-snap-type:x mandatory/);
  assert.match(rail, /overscroll-behavior-x:contain/);
});

test('mobile cards retain their existing size and snap one at a time', () => {
  assert.match(css, /\.rail>\*,\.rail #accountOverview>\*\{flex:0 0 min\(300px,84vw\);scroll-snap-align:start/);
  const cards = rule('.rail>*,.rail #accountOverview>*');
  assert.match(cards, /scroll-snap-stop:always/);
  assert.doesNotMatch(cards, /(?:width|height|flex-basis|font-size):/);
});

test('mobile scroller exposes a visible scrolling affordance', () => {
  assert.match(rule('.rail'), /scrollbar-width:thin/);
  assert.match(rule('.rail::-webkit-scrollbar'), /display:block/);
  assert.match(rule('.rail::-webkit-scrollbar'), /height:6px/);
  assert.match(rule('.rail::-webkit-scrollbar-thumb'), /background:var\(--accent-ring\)/);
});

test('overview scrolling is focusable and described for keyboard users', () => {
  assert.match(html, /<aside class="rail"[^>]*tabindex="0"[^>]*aria-describedby="overviewSwipeHint"/);
  assert.match(html, /id="overviewSwipeHint"[^>]*class="sr-only"/);
  assert.match(html, /左右滑动[^<]*方向键/);
  assert.match(css, /\.rail:focus-visible\{[^}]*outline:2px solid var\(--accent\)/);
});
