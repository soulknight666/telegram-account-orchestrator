const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../tam/web/app.js'), 'utf8');

test('TAO_HOOK 保持 FX 调用顺序、上下文、Promise 与返回值语义', async () => {
  const events = [];
  const ctx = vm.createContext({
    console,
    events,
    render(v) { events.push('render'); return v; },
    openDrawer(v) { events.push('drawer'); return v; },
    openModal(v) { events.push('modal'); return v; },
    uiTask(v) { events.push('task'); return v; },
    switchView(v) { events.push('view:' + v); return v; },
    toggleTheme(v) { events.push('theme'); return v; },
    api(v) { events.push('api'); return Promise.resolve(v); },
    asyncFn(v) { events.push('async'); return Promise.resolve(v); },
    afterFn(v) { events.push('after'); if (v === 'kept') { this._kept ||= Promise.resolve(v); return this._kept; } return Promise.resolve(v); },
    throws() { events.push('throws'); throw new Error('base'); },
    globalThis: null,
  });
  ctx.globalThis = ctx;
  const start = source.indexOf('const TAO_HOOK = (() => {');
  const end = source.indexOf('// Expose the registry', start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end) + '\nglobalThis.__hook = TAO_HOOK;', ctx);
  const hook = ctx.__hook;

  hook.after('render', function (value) { events.push('render.after'); return value; }, 'fx2:view');
  hook.after('openDrawer', function (value) { events.push('drawer.after'); return value; }, 'fx5:drawer');
  hook.around('openModal', function (next, args) { events.push('modal.before'); const out = next(...args); events.push('modal.after'); return out; }, 'fx6:modal');
  hook.result('uiTask', function (value) { events.push('task.result'); return value + ':updated'; }, 'fx4:task');
  hook.after('uiTask', function (value) { events.push('task.after:' + value); }, 'fx5:task-time');
  hook.after('switchView', function (value, args) { events.push('view.after:' + args[0]); return value; }, 'fx8:view');
  hook.around('toggleTheme', function (next, args) { events.push('theme.before'); return next(...args); }, 'fx2:theme');
  hook.around('api', function (next, args) { events.push('api.before'); return next(...args); }, 'fx7:busy');
  hook.after('asyncFn', function () { events.push('async.after'); }, 'async:after');
  hook.result('asyncFn', function (value) { return value + ':result'; }, 'async:result');
  hook.after('afterFn', function () { events.push('after.side-effect'); }, 'after:identity');
  hook.around('throws', function (next) { next(); }, 'throws:once');

  assert.equal(ctx.render('r'), 'r');
  assert.equal(ctx.openDrawer('d'), 'd');
  assert.equal(ctx.openModal('m'), 'm');
  assert.equal(ctx.uiTask('t'), 't:updated');
  assert.equal(ctx.switchView('settings'), 'settings');
  assert.equal(ctx.toggleTheme('dark'), 'dark');
  const apiPromise = ctx.api('request');
  const asyncPromise = ctx.asyncFn('work');
  const afterPromise = ctx.afterFn('kept');
  assert.equal(await apiPromise, 'request');
  assert.equal(await asyncPromise, 'work:result');
  const sameAfterPromise = ctx.afterFn('kept');
  assert.equal(afterPromise, sameAfterPromise, 'after hook 返回原始 Promise');
  await afterPromise;
  await sameAfterPromise;
  assert.throws(() => ctx.throws(), /base/);
  assert.equal(events.filter(x => x === 'throws').length, 1);

  assert.deepEqual(events.slice(0, 14), [
    'render', 'render.after', 'drawer', 'drawer.after',
    'modal.before', 'modal', 'modal.after',
    'task', 'task.result', 'task.after:t:updated',
    'view:settings', 'view.after:settings', 'theme.before', 'theme',
  ]);
  assert.ok(events.indexOf('api.before') < events.indexOf('api'));
  assert.ok(events.indexOf('async') < events.indexOf('async.after'));
  assert.equal(events.filter(x => x === 'after.side-effect').length, 2);
  assert.equal(hook.list().find(x => x.name === 'uiTask').hooks.length, 2);
  assert.equal(hook.after('render', () => {}, 'fx2:view'), true, '同一 tag 不重复包裹');
  assert.equal(hook.list().find(x => x.name === 'render').hooks.length, 1);
});

test('FX 层不再通过 const _x = x 叠加替换目标函数', () => {
  const direct = source.match(/(?:const _\w+\s*=\s*(?:api|bulk|check|render|syncSelectionUi|openModal|switchView|openDrawer|closeAll|emptyRowHtml)|\b(?:api|bulk|check|render|syncSelectionUi|openModal|switchView|openDrawer|closeAll|emptyRowHtml)\s*=\s*function\s+(?:wrapped|accountsApi))/g) || [];
  assert.deepEqual(direct, []);
  assert.match(source, /list\(log = false\)/);
});
