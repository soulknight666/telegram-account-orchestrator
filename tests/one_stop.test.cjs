const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname, '../tam/web/app.js'), 'utf8');
const block = source.slice(source.indexOf('async function oneStopWizard()'), source.indexOf('let proxyCapacityTimer'));
async function scenario({password='fixture-secret', failExport=false, cancel=false}={}) {
 const calls=[], downloads=[], updates=[], notes=[];
 const prompts=['fixture','socks5://localhost:1080','5','2','8','60', cancel?null:password,'pack'];
 const ctx=vm.createContext({
   uiPrompt:async()=>prompts.shift(),uiConfirm:async()=>true,parseProxyPoolInput:()=>['socks5://localhost:1080'],
   toast:(...v)=>notes.push(v),refresh:()=>{},out:()=>{},setTimeout:fn=>{fn();return 1;},
   uiTask:()=>({update:v=>updates.push(v),close:()=>{}}),
   api:async(url,opts={})=>{
     const body=opts.body?JSON.parse(opts.body):{};calls.push({url,body});
     if(url==='/api/accounts/import') return {parsed:3,parsed_input:3,added:[{id:1},{id:2},{id:3}],updated:[],existing:[],duplicates:[],errors:[],capacity_blocked:0};
     if(url==='/api/tasks/one-stop' && opts.method==='POST') return {id:77};
     if(url==='/api/tasks/one-stop/77') return {id:77,status:'done',live:false,total:3,done_count:3,ok_count:2,fail_count:1,phases:{ready:2,uncertain:1}};
     throw Error('unexpected endpoint '+url);
   },
   _downloadBlob:async(url,body)=>{downloads.push({url,body});if(failExport)throw Error('download fixture failure');return 'fixture.zip';},
 });
 vm.runInContext(block,ctx);
 await ctx.oneStopWizard();
 return {calls,downloads,updates,notes};
}
test('one-stop delegates durable pipeline, capacity and timing before polling',async()=>{
 const r=await scenario();
 const start=r.calls.find(x=>x.url==='/api/tasks/one-stop');
 assert.ok(start);
 assert.equal(start.body.new_password,'fixture-secret');
 assert.equal(start.body.concurrency,2);
 assert.equal(start.body.query_delay,8);
 assert.equal(start.body.poll_interval,60);
 assert.equal(r.calls.find(x=>x.url==='/api/accounts/import').body.links_per_proxy,5);
 assert.deepEqual(r.downloads.map(x=>x.url),['/api/tasks/one-stop/77/export']);
 assert.ok(r.updates.some(x=>x.current===3 && x.total===3));
 assert.ok(!r.calls.some(x=>x.url.includes('/auto-login') || x.url.includes('/toolbox/twofa')));
});
test('export failure keeps server task id visible and does not repeat mutation',async()=>{
 const r=await scenario({failExport:true});
 assert.equal(r.downloads.length,1);
 assert.equal(r.calls.filter(x=>x.url==='/api/tasks/one-stop').length,1);
 assert.ok(r.notes.some(x=>String(x[0]).includes('77')));
});
test('cancel configuration stops before data import or mutations',async()=>{
 const r=await scenario({cancel:true});
 assert.equal(r.calls.length,0);
});
const parser=source.slice(source.indexOf('function parseProxyPoolInput('),source.indexOf('// 仅导出手机号'));
function parse(s){const c=vm.createContext({});vm.runInContext(parser,c);return Array.from(c.parseProxyPoolInput(s));}
test('CSV credentials named user/password are not mistaken for headers',()=>{
 assert.deepEqual(parse('127.0.0.1,1080,user,password'),['socks5://user:password@127.0.0.1:1080']);
});
test('CSV IPv6 protocol and invalid rows are explicit',()=>{
 assert.deepEqual(parse('host,port,username,password,protocol\n2001:db8::1,8080,a,b,http'),['http://a:b@[2001:db8::1]:8080']);
 assert.throws(()=>parse('host,port\nlocalhost,nope'));
});
