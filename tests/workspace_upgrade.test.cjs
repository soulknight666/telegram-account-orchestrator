const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..', 'tam', 'web');
const html = fs.readFileSync(path.join(root,'index.src.html'),'utf8');
const css = fs.readFileSync(path.join(root,'styles.css'),'utf8');
const js = fs.readFileSync(path.join(root,'app.js'),'utf8');

test('unified workspace puts navigation and command search in header', () => {
  assert.match(html, /id="mainNav"[^>]*aria-label="主导航"/);
  assert.match(html, /onclick="openPalette\(\)"/);
  assert.match(html, /id="themeBtn"/);
  assert.doesNotMatch(html, /id="navWheel"|id="navCollapse"/);
});
test('advanced filters are disclosed without occupying account rows', () => {
  assert.match(html, /id="filterPop"[^>]*role="dialog"/);
  assert.match(html, /id="qfilters"/);
  assert.match(js, /syncFilterBadge\(\)/);
  assert.match(css, /\.accounts-panel[\s\S]*min-height:0/);
});
test('command actions retain selection and readonly prerequisites', () => {
  assert.match(js, /const ACTIONS = \[/);
  assert.match(js, /id:'b-export-links'[^\n]*sel:true/);
  assert.match(js, /function palActionState/);
  assert.match(js, /需先勾选账号/);
});
test('menus and palette restore focus and trap keyboard navigation', () => {
  assert.match(js, /function focusMenu/);
  assert.match(js, /MENU_OPEN[\s\S]*e.key === 'Tab'/);
  assert.match(js, /PAL.trigger/);
  assert.match(js, /function closePalette[\s\S]*trigger.focus/);
});
test('file workspace aliases old saved route and navigates before jumping', () => {
  assert.match(js, /saved === 'ziptools'/);
  assert.match(js, /function toolboxJump[\s\S]*switchView\('toolbox'\)/);
});
test('palette uses selected options and preserves focus after view switch', () => {
  assert.match(js, /aria-selected=/);
  assert.match(html, /id="pal"[^>]*role="dialog"[^>]*aria-modal="true"/);
  assert.match(js, /TAO_MOTION.view\(.*defer:true/);
});
test('intermediate windows wrap the toolbar and keep overview counts uncropped', () => {
  assert.match(css, /@media \(min-width:721px\) and \(max-width:1180px\)/);
  assert.match(css, /\.toolbar\{flex-wrap:wrap/);
  assert.match(css, /\.rail \.stat-card\.ink[^}]*max-height:none/);
});
test('small desktop header compresses branding before controls can overlap', () => {
  assert.match(css, /@media \(min-width:721px\) and \(max-width:900px\)/);
  assert.match(css, /\.brand b\{display:none\}/);
});
