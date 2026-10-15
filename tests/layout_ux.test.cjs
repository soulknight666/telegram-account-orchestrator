const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('tam/web/index.src.html','utf8');
const source=fs.readFileSync('tam/web/app.js','utf8');
const css=fs.readFileSync('tam/web/styles.css','utf8');
for(const id of ['appShell','mainNav','workspace','accountOverview','filterPop','bulkMenu','accountDetailDrawer'])assert.ok(html.includes(`id="${id}"`),id);
for(const v of ['accounts','tasks','leads','toolbox','logs','settings'])assert.ok(html.includes(`data-view="${v}"`),v);
assert.match(html,/data-widget="leads" data-view="leads"/);
assert.match(css,/height:100dvh;overflow:hidden/);
assert.match(css,/\.content\{[^}]*min-width:0;min-height:0;overflow:auto/);
assert.match(source,/data-task-id/);assert.match(source,/aria-live="polite"/);
for(const id of ['mImport','mTdata','mSession','mChats'])assert.match(html,new RegExp(`<div class="modal modal-wide" id="${id}"`));
const start=source.indexOf('function loadViewPrefs()'),end=source.indexOf('/* ---------- 渲染 ---------- */');
const nodes=['accounts','tasks','leads','toolbox','logs','settings'].map(v=>({dataset:{view:v,nav:v},classList:{toggle(n,on){this[n]=on}},setAttribute(n,v){this[n]=v}}));
const storage=new Map([['tam.view','ziptools']]);let motionCount=0;
const ctx=vm.createContext({
 VIEW_NAMES:{accounts:'账号清单',tasks:'任务中心',leads:'线索库',toolbox:'工具',logs:'日志',settings:'设置'},currentView:'accounts',viewKey:'tam.view',MORE_VIEWS:['logs','settings'],
 document:{querySelectorAll:s=>s==='[data-nav-group="more"]'?[]:nodes,body:{classList:{toggle(){}}}},
 localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},$:()=>({scrollTop:9}),
 loadTasks(){},loadLeads(){},loadOps(){},renderZt(){},loadSettings(){},closeMenus(){},TAO_MOTION:{view(){motionCount++}}
});
vm.runInContext(source.slice(start,end),ctx);
vm.runInContext('loadViewPrefs()',ctx);assert.equal(ctx.currentView,'toolbox');
vm.runInContext("switchView('tasks')",ctx);assert.equal(ctx.currentView,'tasks');assert.equal(storage.get('tam.view'),'tasks');
assert.equal(nodes.find(n=>n.dataset.view==='accounts').classList['view-hidden'],true);
vm.runInContext("switchView('ziptools')",ctx);assert.equal(ctx.currentView,'toolbox');assert.equal(motionCount,2);
console.log('responsive workspace navigation contract passed');
