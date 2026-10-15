const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const src = fs.readFileSync('tam/web/index.src.html','utf8');
const css = fs.readFileSync('tam/web/styles.css','utf8');
const app = fs.readFileSync('tam/web/app.js','utf8');
test('顶栏按品牌、导航和全局操作分组',()=>{
 assert.match(src,/class="topbar app-header"/); assert.match(src,/class="brand"/);
 assert.match(src,/class="pillnav"/); assert.match(src,/class="top-actions"/);
});
test('紧凑列表仅内容区滚动，快捷筛选收纳在弹出菜单',()=>{
 assert.match(css,/body\.accounts-workspace \.content\{overflow:hidden/);
 assert.match(css,/\.accounts-panel \.scroll\{[^}]*flex:1;min-height:0/);
 assert.match(src,/id="filterPop"[^>]*role="dialog"/);
});
test('AI 浮动入口拒绝越界位置并在视口变化时纠偏',()=>{
  assert.match(app,/restoreFabPosition/);
  assert.match(app,/saved\.left >= 4/);
  assert.match(app,/addEventListener\('resize', \(\) =>/);
});
test('账号渲染后同步导航徽标，避免演示或刷新短暂显示 0',()=>{
  assert.match(app,/syncFilterBadge\(\);\s*\/\/ 账号列表与导航徽标[\s\S]*?updateNavBadges\(\);/);
});
test('线索库独立于任务中心，避免工作区信息堆叠',()=>{
  assert.match(src,/data-nav="leads"[\s\S]*?switchView\('leads'\)/);
  assert.match(src,/data-widget="leads" data-view="leads"/);
  assert.match(app,/VIEW_NAMES\s*=\s*\{[^}]*leads:'线索库'/);
  assert.match(app,/if \(view === 'leads'\) loadLeads\(\);/);
});
test('演示页提供真实可读的设置与工具样例，不显示空壳加载态',()=>{
  assert.match(app,/path === '\/api\/settings'/);
  assert.match(app,/TAM_BATCH_CONCURRENCY/);
  assert.match(app,/path === '\/api\/toolbox\/ops'/);
  assert.match(app,/quick_alive/);
});
