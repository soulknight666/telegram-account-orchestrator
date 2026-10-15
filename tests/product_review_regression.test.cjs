const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'tam/web/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'tam/web/index.src.html'), 'utf8');

function count(pattern, text) { return (text.match(pattern) || []).length; }

test('only one batchSummary formatter remains so enhanced log states stay active', () => {
  assert.equal(count(/\bfunction\s+batchSummary\s*\(/g, app), 1);
  assert.match(app, /function batchSummary\(rows, meta = \{\}\)/);
  assert.match(app, /batchSummary\(d\.results,d\)/);
});

test('dialog surfaces expose modal semantics and the global manager has focus lifecycle hooks', () => {
  assert.match(html, /<div class="modal" id="mUi"[^>]*role="dialog"[^>]*aria-modal="true"/);
  assert.match(html, /<button[^>]+id="aiFab"[^>]+aria-label="打开 AI 助手"/);
  assert.match(app, /function focusDialog/);
  assert.match(app, /document\.activeElement/);
  assert.match(app, /focus\(\)/);
  assert.match(app, /fab\.addEventListener\('click'/);
  assert.match(app, /function uiClose\(\)[\s\S]*_dialogEl === m[\s\S]*releaseDialogFocus\(\)/);
  assert.match(app, /function releaseDialogFocus\(restoreFocus = true\)[\s\S]*setTimeout\(\(\) => \{ try \{ previous\.focus/);
  const leadBlock = app.slice(app.indexOf('async function viewLeadMsgs'), app.indexOf('function exportLeads'));
  const drawerBlock = app.slice(app.indexOf('function openDrawer'), app.indexOf('/* ---------- 动作 ---------- */'));
  assert.match(leadBlock, /focusDialog\(detail/);
  assert.match(drawerBlock, /focusDialog\(detail/);
});

test('bulk delete explicitly separates locked accounts and reports protected rows', () => {
  assert.match(app, /const lockedIds\s*=\s*ids\.filter/);
  assert.match(app, /锁定账号.*跳过|跳过.*锁定账号/);
  assert.match(app, /r\.failed.*err|r\.failed.*'err'/);
});

test('AI approval does not leave the assistant panel open behind a modal', () => {
  const start = app.indexOf('function aiConfirmTools');
  const end = app.indexOf('function aiChatOnce', start);
  assert.ok(start >= 0 && end > start);
  assert.match(app.slice(start, end), /closeAiPanel\(\)/);
});
