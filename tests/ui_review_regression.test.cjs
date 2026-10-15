const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const src=fs.readFileSync('tam/web/app.js','utf8');
const css=fs.readFileSync('tam/web/styles.css','utf8');
const section=(start,end)=>src.slice(src.indexOf(start),src.indexOf(end,src.indexOf(start)));
function classes(...initial){const s=new Set(initial);return {add:n=>s.add(n),remove:n=>s.delete(n),contains:n=>s.has(n)};}

test('desktop popup has a viewport height limit and its own scroll area',()=>{
  const base=css.match(/\.menu\{([^}]+)\}/)[1];
  assert.match(base,/max-height:\s*calc\(100dvh\s*-\s*24px\)/);
  assert.match(base,/overflow-y:\s*auto/);
  assert.match(css,/\.menu\s*>\s*\*\s*\{[^}]*flex-shrink:\s*0/);
});
test('popup below a low trigger clamps to the viewport bottom instead of overflowing',()=>{
  const menu={style:{},classList:classes(),offsetWidth:320,offsetHeight:660};
  const trigger={getBoundingClientRect:()=>({left:900,right:1100,top:50,bottom:80})};
  const c=vm.createContext({window:{innerWidth:1280,innerHeight:720},menu,trigger});
  vm.runInContext(section('function isSheetLayout()','function toggleMenu('),c);
  vm.runInContext('placeMenu(menu,trigger)',c);
  assert.equal(menu.style.top,'48px');
  assert.ok(parseInt(menu.style.top)+menu.offsetHeight<=708);
});
function keyboardMenu(){
  const doc={activeElement:null,addEventListener:(type,fn)=>{doc.handler=fn;}};
  const item=(name,visible)=>({name,getClientRects:()=>visible?[{}]:[],matches:()=>true,focus(){doc.activeElement=this;}});
  const first=item('first',true),last=item('last',true),hidden=item('mobile-only',false);
  const menu={querySelectorAll:()=>[first,last,hidden],getAttribute:()=> 'menu'};
  const c=vm.createContext({document:doc,window:{addEventListener(){}},TAO_KEYS:{esc(){}}});
  vm.runInContext(section('let MENU_OPEN =','/* ---------- 操作清单'),c);
  c.menu=menu;vm.runInContext('MENU_OPEN={menu,trigger:null}',c);
  return {doc,first,last,press(key){let prevented=false;doc.handler({key,preventDefault(){prevented=true;}});assert.ok(prevented);}};
}
test('ArrowDown wraps from last visible menu option and skips hidden mobile items',()=>{
  const k=keyboardMenu();k.last.focus();k.press('ArrowDown');assert.equal(k.doc.activeElement,k.first);
});
test('ArrowUp wraps from first visible menu option and skips hidden mobile items',()=>{
  const k=keyboardMenu();k.first.focus();k.press('ArrowUp');assert.equal(k.doc.activeElement,k.last);
});

function overlay(){
  const timers=[],doc={activeElement:null,body:{classList:classes('modal-open')},removeEventListener(){},querySelectorAll:()=>[els['#mAdd']]};
  const el=id=>({id,isConnected:true,classList:classes('on'),value:id==='aLabel'?'draft stays':'',setAttribute(n,v){this[n]=v;},focus(){doc.activeElement=this;}});
  const els=Object.fromEntries(['#mAdd','#bd','#pal','#palBackdrop','#palInput','#aLabel','#addTrigger','#workspace','#accountDetailDrawer','#aiPanel','#aiFab'].map(id=>[id,el(id.slice(1))]));
  doc.activeElement=els['#palInput'];
  const c=vm.createContext({document:doc,$:s=>els[s],setTimeout:fn=>timers.push(fn),PAL:{open:true,trigger:els['#aLabel'],items:[]},sel:new Set(),closeMenus(){},clearOfficialCode(){},TAO_KEYS:{esc(){}},TAO_MOTION:{close(){}},palActionState:a=>a.write&&c.readonly?'只读':'',palRemember(){},toast(){},switchView:v=>{c.view=v;},openDrawer:()=>{els['#bd'].classList.add('on');els['#accountDetailDrawer'].classList.add('on');els['#accountDetailDrawer'].focus();},_dialogReturnFocus:els['#addTrigger'],_dialogEl:els['#mAdd'],_dialogKeyHandler:()=>{},_uiResolver:null,_uiMode:'confirm'});
  vm.runInContext(section('function releaseDialogFocus(','// 独立任务进度'),c);
  vm.runInContext(section('function closeAll(','TAO_KEYS.esc(\'close-all\''),c);
  vm.runInContext(section('function closePalette(','(function bindPalette()'),c);
  return {c,els,doc,flush(){timers.splice(0).forEach(fn=>fn());},run(row){c.PAL.items=[row];vm.runInContext('palRun(0)',c);}};
}
test('palette view navigation closes the old modal and shade without losing its draft or stealing focus',()=>{
  const o=overlay();o.run({action:{id:'go-settings',run:()=>o.c.switchView('settings')}});o.flush();
  assert.equal(o.c.view,'settings');assert.equal(o.els['#mAdd'].classList.contains('on'),false);
  assert.equal(o.els['#bd'].classList.contains('on'),false);assert.equal(o.doc.body.classList.contains('modal-open'),false);
  assert.equal(o.els['#aLabel'].value,'draft stays');assert.equal(o.doc.activeElement,o.els['#workspace']);
});
test('palette account detail navigation cannot be overridden by stale dialog return-focus',()=>{
  const o=overlay();o.run({account:{id:1}});o.flush();
  assert.equal(o.els['#mAdd'].classList.contains('on'),false);assert.equal(o.els['#bd'].classList.contains('on'),true);
  assert.equal(o.doc.activeElement,o.els['#accountDetailDrawer']);
});
test('a missing-selection command leaves an unobstructed account view instead of the old modal',()=>{
  const o=overlay();let ran=false;o.run({action:{id:'b-check',sel:true,label:'检查',run:()=>ran=true}});o.flush();
  assert.equal(o.c.view,'accounts');assert.equal(ran,false);assert.equal(o.els['#mAdd'].classList.contains('on'),false);
});
test('cancelling the palette keeps the underlying dialog and returns focus to its field',()=>{
  const o=overlay();vm.runInContext('closePalette()',o.c);o.flush();
  assert.equal(o.els['#mAdd'].classList.contains('on'),true);assert.equal(o.doc.activeElement,o.els['#aLabel']);
});
test('a readonly rejected palette action keeps the underlying dialog unchanged',()=>{
  const o=overlay();o.c.readonly=true;let ran=false;o.run({action:{id:'b-auto',write:true,run:()=>ran=true}});o.flush();
  assert.equal(ran,false);assert.equal(o.c.PAL.open,true);assert.equal(o.els['#mAdd'].classList.contains('on'),true);
});
test('normal dialog dismissal still restores its original trigger',()=>{
  const o=overlay();vm.runInContext('closeAll()',o.c);o.flush();assert.equal(o.doc.activeElement,o.els['#addTrigger']);
});
test('successful refresh clears the previous connection failure banner',async()=>{
  const els={'#connBar':{classList:classes()},'#connMsg':{textContent:''},'#connSettings':{style:{}}};
  let fail=true,rendered=0;
  const c=vm.createContext({$:s=>els[s],api:async p=>{if(fail)throw new Error('network failure');return p==='/api/accounts'?[]:{total:0};},setAccounts(){},render(){rendered++;},syncClock(){},toast(){},out(){},loadLogs(){},loadKick(){},loadTasks(){},loadLeads(){},loadSettings(){},loadOps(){},renderZt(){}});
  vm.runInContext(section('async function refresh()','async function loadKick()')+section('function showConnError(','function retryConnect()'),c);
  await vm.runInContext('refresh()',c);assert.equal(els['#connBar'].classList.contains('on'),true);
  fail=false;await vm.runInContext('refresh()',c);assert.equal(rendered,1);assert.equal(els['#connBar'].classList.contains('on'),false);
});
