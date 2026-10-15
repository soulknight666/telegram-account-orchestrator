const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const src=fs.readFileSync('tam/web/app.js','utf8');
function palette(recent='[]') {
 const c=vm.createContext({localStorage:{getItem:()=>recent},accounts:[],readonly:false,sel:new Set(),ACTIONS:[
  {id:'export',g:'账号',label:'导出',kw:'export'}, {id:'proxy',g:'账号',label:'代理',kw:'proxy'}
 ]});
 vm.runInContext(src.slice(src.indexOf('const PAL ='),src.indexOf('function palRender()')),c);
 return c;
}
test('recent command history never introduces unrelated search hits',()=>{
 const c=palette('["proxy"]');
 assert.deepEqual(Array.from(vm.runInContext("palBuild('export').filter(r=>r.action).map(r=>r.action.id)",c)),['export']);
 assert.equal(vm.runInContext("palBuild('nothing-matches').length",c),0);
});
test('invalid saved recent-use data gracefully becomes an empty list',()=>{
 for(const raw of ['{}','null','invalid'])assert.equal(vm.runInContext('palRecent().length',palette(raw)),0);
});
test('selected-account actions announce readonly and selection prerequisites',()=>{
 const c=palette();assert.equal(vm.runInContext('palActionState({sel:true})',c),'需先勾选账号');
 c.sel.add(1);assert.equal(vm.runInContext('palActionState({sel:true})',c),'已选 1 个');
 c.readonly=true;assert.equal(vm.runInContext('palActionState({sel:true,write:true})',c),'只读模式下不可用');
});
test('popup flips within viewport, focuses first option, returns focus on close',()=>{
 const active=new Set();let focused='';
 const cls={add:n=>active.add(n),remove:n=>active.delete(n)};
 const first={getClientRects:()=>[{}],focus:()=>focused='item'};
 const menu={style:{},classList:cls,offsetWidth:320,offsetHeight:280,querySelectorAll:()=>[first]};
 const trigger={isConnected:true,setAttribute(n,v){this[n]=v},focus:()=>focused='trigger',getBoundingClientRect:()=>({left:890,right:990,top:640,bottom:680})};
 const c=vm.createContext({$:s=>s==='#menuScrim'?{classList:cls}:menu,window:{innerWidth:1000,innerHeight:700,addEventListener:()=>{}},document:{addEventListener:()=>{},activeElement:null}});
 vm.runInContext(src.slice(src.indexOf('let MENU_OPEN ='),src.indexOf("window.addEventListener('resize', closeMenus")),c);
 c.trigger=trigger;vm.runInContext("toggleMenu(trigger,'testMenu')",c);
 assert.equal(focused,'item');assert.equal(menu.style.left,'670px');assert.equal(menu.style.top,'352px');assert.equal(trigger['aria-expanded'],'true');
 vm.runInContext('closeMenus()',c);assert.equal(focused,'trigger');assert.equal(trigger['aria-expanded'],'false');
});
