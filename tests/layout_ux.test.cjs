const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../tam/web/index.src.html'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, '../tam/web/app.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../tam/web/styles.css'), 'utf8');
for (const id of ['appShell', 'sideNav', 'workspace', 'bulkGroupLogin', 'bulkGroupProxy', 'bulkGroupExport', 'bulkGroupTools', 'accountDetailDrawer']) {
  assert.ok(html.includes(`id="${id}"`), `missing ${id}`);
}
assert.match(html, /data-nav="accounts"/);
assert.match(html, /data-nav="tasks"/);
assert.match(html, /id="accountDetailDrawer"[^>]*aria-hidden="true"/);
assert.match(css, /\.side-nav/);
assert.match(css, /\.nav-collapsed/);
assert.match(css, /\.bulk-group/);
assert.match(css, /\.account-detail-drawer|\.drawer/);
assert.match(source, /data-task-id/);
assert.match(source, /aria-live="polite"/);

const navBlock = source.slice(source.indexOf('/* ---------- 工作台导航'), source.indexOf('/* ---------- 布局引擎'));
const shell = {classList:{toggle(name, on){this[name] = on;}}};
const navNodes = ['accounts','tasks','toolbox','ziptools','logs','settings'].map(view => ({dataset:{nav:view}, classList:{toggle(){}}, setAttribute(name, value){this[name] = value;}}));
const viewNodes = ['accounts','tasks','toolbox','ziptools','logs','settings'].map(view => ({dataset:{view}, classList:{toggle(name, on){this[name] = on;}}, setAttribute(name, value){this[name] = value;}}));
const storage = new Map();
const $ = selector => selector === '#appShell' ? shell : null;
const ctx = vm.createContext({
  $, document:{querySelector(selector){return selector === '#appShell' ? shell : null;}, querySelectorAll(selector){return selector === '[data-nav]' ? navNodes : viewNodes;}},
  localStorage:{getItem:key => storage.get(key) || null, setItem:(key, value) => storage.set(key, value)},
  window:{innerWidth:1280}, loadTasks:()=>{}, loadOps:()=>{}, renderZt:()=>{}, loadSettings:()=>{}, renderLayout:()=>{},
});
vm.runInContext(navBlock, ctx);
vm.runInContext("switchView('tasks')", ctx);
assert.equal(vm.runInContext('currentView', ctx), 'tasks');
assert.equal(storage.get('tam.view'), 'tasks');
assert.equal(viewNodes.find(x => x.dataset.view === 'accounts').classList['view-hidden'], true);
vm.runInContext('toggleSideNav()', ctx);
assert.equal(vm.runInContext('navCollapsed', ctx), true);
console.log('layout shell contract passed');
