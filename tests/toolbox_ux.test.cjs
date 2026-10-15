const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../tam/web/app.js'), 'utf8');
const block = source.slice(source.indexOf('let TB_OPS ='), source.indexOf('/* ---------- ZIP 工具'));
const elements = {};
const box = {innerHTML:'', querySelectorAll:()=>[]};
elements['#tbBody'] = box;
const ctx = vm.createContext({
  console, Map, Set, readonly:false, sel:new Set(), accounts:[],
  $: id => elements[id] || null,
  esc: s => String(s).replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
  toast:()=>{}, document:{querySelector:()=>null},
});
vm.runInContext(block, ctx);
vm.runInContext(`
TB_OPS = [
 {op:'alive',label:'账号状态检测',desc:'检查状态',params:[{name:'timeout',label:'超时',type:'float',default:10}]},
 {op:'twofa',label:'修改二验',danger:true,params:[{name:'new_password',label:'新密码',type:'password'},{name:'timeout',label:'超时',type:'float'}]},
 {op:'custom_tool',label:'新工具',params:[]}
];
TB_CUR='alive'; renderTb();
`, ctx);
assert.match(box.innerHTML, /选择账号/);
assert.match(box.innerHTML, /高级设置/);
assert.match(box.innerHTML, /tbRun/);
assert.equal(vm.runInContext("tbGroup(TB_OPS[0])", ctx), 'check');
assert.equal(vm.runInContext("tbGroup(TB_OPS[1])", ctx), 'more');
assert.equal(vm.runInContext("tbGroup(TB_OPS[2])", ctx), 'more');
assert.equal(vm.runInContext("tbAdvanced({name:'new_password',type:'password'})", ctx), false);
assert.equal(vm.runInContext("tbAdvanced({name:'timeout',required:true})", ctx), false);
assert.equal(vm.runInContext("tbAdvanced({name:'timeout'})", ctx), true);
elements['#tbSel']={textContent:''}; elements['#tbRun']={disabled:false};
vm.runInContext('syncTbSelection()',ctx);
assert.equal(elements['#tbRun'].disabled,true);
ctx.sel.add(123); vm.runInContext('syncTbSelection()',ctx);
assert.equal(elements['#tbRun'].disabled,false);
assert.match(elements['#tbSel'].textContent,/1/);
vm.runInContext("TB_CUR='twofa';renderTb()",ctx);
assert.ok(box.innerHTML.indexOf('tbp_new_password') < box.innerHTML.indexOf('<details'));
assert.match(box.innerHTML,/执行前/);
// Blank mandatory inputs fail locally before any network request.
elements['#tbp_target']={value:'',focus:()=>{}};
assert.throws(()=>vm.runInContext("tbCollect({params:[{name:'target',label:'目标',type:'str',required:true}]})",ctx));
console.log('Toolbox UX assertions passed');
