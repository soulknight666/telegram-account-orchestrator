const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const s=fs.readFileSync(require('node:path').join(__dirname,'../tam/web/app.js'),'utf8');
test('2FA outcome filters are independent of authorization',()=>{
 const c=vm.createContext({});
 vm.runInContext(s.slice(s.indexOf('function accountMatches('),s.indexOf('function visible()')),c);
 for(const status of ['success','failed','uncertain','running']){
   const a={status:'active',twofa_result:{status}};
   assert.equal(c.accountMatches(a,'','twofa_success',''),status==='success');
   assert.equal(c.accountMatches(a,'','twofa_failed',''),status==='failed');
   assert.equal(c.accountMatches(a,'','twofa_uncertain',''),status==='uncertain');
 }
 assert.equal(c.accountMatches({status:'active',has_2fa:1},'','twofa_success',''),false);
});
test('only-success selection replaces previous hidden failures',async()=>{
 let selected;
 const c=vm.createContext({sel:new Set([99]),filter:'error',quickFilter:'failed',tableLimit:300,
   refreshLoginAccounts:async()=>{},visible:()=>[{id:1,twofa_result:{status:'success'}},{id:2,twofa_result:{status:'uncertain'}}],
   render:()=>{},toast:()=>{}});
 const a=s.indexOf('async function selectTwofaSuccess()');
 assert.ok(a>=0);
 vm.runInContext(s.slice(a,s.indexOf('\nfunction clearSel()',a)),c);
 await c.selectTwofaSuccess();
 assert.deepEqual([...c.sel],[1]);
 assert.equal(c.quickFilter,'twofa_success');
});
