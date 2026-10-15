const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync('tam/web/app.js','utf8');
const els=Object.fromEntries(['#log','#logRaw','#logPin'].map(k=>[k,{textContent:'',style:{}}]));
const ctx=vm.createContext({accounts:[{id:7,label:'测试账号'}],fmt:x=>String(x),fmtPhone:x=>x,countryOf:()=>'', $:s=>els[s]});
vm.runInContext('let logPinned=false;\n'+src.slice(src.indexOf('const ACTION_CN'),src.indexOf('/* ---------- 手机号 / 国家识别')),ctx);
const h=d=>ctx.humanize(d);
test('新操作及工具名中文化',()=>{
 for (const [action,word] of [['twofa_capture','二验'],['kick_retry','重试'],['session_authorization','会话'],['tools.convert','转换'],['tools.passkey','通行密钥'],['ai.chat','对话']]) assert.match(h([{action,ok:1,created_at:1}]),new RegExp(word));
 assert.match(h([{action:'toolbox.one',ok:1,detail:'quick_alive',created_at:1}]),/快速验活/);
});
test('字符串0不误报成功；字符串账号ID匹配',()=>{
 const s=h([{action:'health_check',ok:'0',account_id:'7',detail:'AuthKeyUnregisteredError',created_at:1}]);assert.match(s,/✗/);assert.match(s,/测试账号/);assert.match(s,/会话.*失效/);
});
test('结构化详情翻译字段、保留数值与错误标识',()=>{
 const s=h([{action:'sign_in',ok:1,created_at:1,detail:'user_id=123 code_len=5'}]);assert.match(s,/用户 ID.*123/);assert.match(s,/验证码长度.*5/);
 assert.match(h({error:'RuntimeError: TimeoutError'}),/超时/);
 assert.match(h({error:'PasswordHashInvalidError'}),/二验.*不匹配/);
 assert.match(h({error:'FloodWaitError: A wait of 60 seconds is required'}),/60/);
});
test('有详情不等于失败',()=>{assert.doesNotMatch(h({detail:'操作已排队'}),/出错了/);});
test('体检尊重status并展示修复与建议',()=>{
 const s=h({ok:false,checks:[{name:'Python',status:'ok'},{name:'端口',status:'warn',hint:'检查占用'},{name:'依赖',status:'fail',fixed:true,fix_log:['安装成功']}]});assert.match(s,/✓ Python/);assert.match(s,/! 端口/);assert.match(s,/检查占用/);assert.match(s,/已修复/);
});
test('批量结果区分失败、跳过、排队与未知',()=>{
 const s=h({results:[{id:7,ok:0},{id:8,status:'skipped'},{id:9,status:'pending'},{id:10,result:{ok:false,error:'PhoneCodeExpiredError'}},{id:11}]});assert.match(s,/失败 2/);assert.match(s,/跳过 1/);assert.match(s,/待处理 1/);assert.match(s,/未确认 1/);assert.match(s,/验证码已过期/);
});
test('嵌套二验结果保留细节',()=>{assert.match(h({op:'twofa_status',account_id:7,result:{has_password:true,has_recovery:false}}),/已开启二步验证/);});
test('任务阶段、计数、目标失败详情',()=>{
 const s=h({one_stop_task:12,status:'running',total:4,done_count:2,ok_count:1,fail_count:1,skip_count:0,phases:{ready:1,changing:2,uncertain:1},targets:[{account_id:7,status:'fail',detail:'PasswordHashInvalidError'}]});assert.match(s,/运行中/);assert.match(s,/可导出.*1/);assert.match(s,/修改二验.*2/);assert.match(s,/待核对.*1/);assert.match(s,/二验.*不匹配/);
});
test('任务列表不误判最近会话',()=>{const s=h([{id:1,title:'示例任务',kind:'auto_login',status:'running',total:3}]);assert.match(s,/任务/);assert.doesNotMatch(s,/最近会话/);});
test('数字与数组导入计数兼容',()=>{for(const d of [{added:2,updated:1,errors:0},{added:[1,2],updated:[3],errors:[]}]) {const s=h(d);assert.match(s,/新增 2/);assert.match(s,/更新 1/);assert.doesNotMatch(s,/undefined/);}});
test('上传导入有文件名且保留目录子账号',()=>{
 const s=h({filename:'fixture.zip',succeeded:1,total:2,items:[{path:'fixture',accounts:[{label:'A',ok:true},{label:'B',ok:false,error:'TimeoutError'}]}]});assert.match(s,/fixture.zip/);assert.match(s,/A/);assert.match(s,/B/);assert.match(s,/超时/);
});
test('普通简短ok对象不吞掉计数',()=>{const s=h({ok:true,deleted:3,locked:2});assert.match(s,/删除.*3/);assert.match(s,/锁定.*2/);});
test('未知结构翻译已知字段，未知键保留',()=>{const s=h({concurrency:8,results_extra:{retry_after_s:60},future_field:'custom'});assert.match(s,/并发.*8/);assert.match(s,/重试.*60/);assert.match(s,/future_field/);});
test('空值与异构数组不崩溃',()=>{for(const d of [null,[],[null,{id:1,ok:1}],[{name:'A'},null],{errors:2},{checks:[null,{status:'ok'}]}])assert.doesNotThrow(()=>h(d));});
test('摘要遮盖敏感字段，保留原始数据且不修改输入',()=>{
 const d={ok:true,password:'SENSITIVE_PASSWORD',proxy:'socks5://user:SECRET@localhost:1080',code_url:'https://example.invalid/SECRET_TOKEN/GetHTML'};const before=JSON.stringify(d);ctx.out(d,true);assert.doesNotMatch(els['#log'].textContent,/SENSITIVE_PASSWORD|SECRET/);assert.equal(els['#logRaw'].textContent,JSON.stringify(d,null,2));assert.equal(JSON.stringify(d),before);
});
test('文本详情敏感键遮盖',()=>{assert.doesNotMatch(h([{action:'twofa',created_at:1,ok:1,detail:'password=TOP_SECRET token=OTHER_SECRET'}]),/TOP_SECRET|OTHER_SECRET/);});
test('超长列表有截断提示，不误报完整明细',()=>{const s=h({results:Array.from({length:10000},(_,id)=>({id,ok:true}))});assert.match(s,/10000/);assert.match(s,/另有 9960/);assert.ok(s.length<12000);});
test('常用旧模板保留',()=>{assert.match(h([{app:'Desktop',current:true}]),/当前会话/);assert.match(h({remaining_others:0,verified:true}),/清理其它设备/);assert.match(h({path:'output.zip',ok:true}),/文件已生成/);});
test('所有已声明工具都有中文名称',()=>{
 const py=fs.readFileSync('tam/toolbox.py','utf8');for(const m of py.matchAll(/"op":\s*"([a-z_]+)"/g)){assert.notEqual(vm.runInContext(`ACTION_CN[${JSON.stringify(m[1])}]`,ctx),undefined,m[1]);}
});
test('体检无总体ok时不武断报错',()=>{assert.doesNotMatch(h({checks:[{name:'Python',status:'ok'}]}),/有问题/);});
test('非设备验证结果不误标清设备',()=>{assert.doesNotMatch(h({verified:true,op:'code_source_check'}),/清理其它设备/);});
test('结果字段对象无object Object，失败导出不报完成',()=>{assert.doesNotMatch(h({ok:false,output:'x.zip',files:[{name:'a'}],error:'TimeoutError'}),/工具完成|\[object Object\]/);});
test('批量失败嵌套与待核对不算成功',()=>{
 const s=h({results:[{id:7,ok:true,result:{ok:0}},{id:8,ok:true,result:{alive:false,reason:'会话失效'}},{id:9,ok:true,result:{status:'uncertain'}}]});assert.match(s,/成功 0/);assert.match(s,/失败 2/);assert.match(s,/待核对 1/);
});
test('混合类型明细不丢失空项',()=>{assert.match(h([null,{name:'A'}]),/A/);});
test('未知扩展操作保留代号，已知操作有中文',()=>{assert.match(h([{action:'future.operation',created_at:1,ok:1}]),/future.operation/);assert.match(h([{action:'toolbox.batch',created_at:1,ok:1,detail:'quick_alive x20'}]),/快速验活.*20/);});
test('配置密钥和JSON详情也遮盖',()=>{
 for(const d of [{values:{TAM_API_HASH:'HIDDEN_SECRET',TAM_WEB_TOKEN:'HIDDEN_SECRET'}},[{action:'test',ok:1,created_at:1,detail:JSON.stringify({password:'HIDDEN_SECRET'})}]]) assert.doesNotMatch(h(d),/HIDDEN_SECRET/);
});
test('锁定日志阻止后台覆盖，解除后可刷新',()=>{ctx.out({ok:true,deleted:1},true);const previous=els['#log'].textContent;ctx.out({ok:false},false);assert.equal(els['#log'].textContent,previous);});
test('非导入更新结果和普通验证保留语义',()=>{assert.doesNotMatch(h({ok:true,updated:2}),/导入完成/);assert.doesNotMatch(h({verified:true}),/清理其它设备/);});
test('失败结果优先于验活与二验成功模板',()=>{
 for(const d of [{ok:false,alive:true,error:'TimeoutError'},{ok:false,has_password:true,had_password:true,error:'TimeoutError'},{ok:false,added:2,error:'TimeoutError'}]){const s=h(d);assert.match(s,/超时/);assert.doesNotMatch(s,/验活结果：通过|二验变更：已开启|导入完成/);}
});
test('账号详情翻译错误，字符串标签兼容',()=>{const s=h({label:'测试',status:'error',status_note:'TimeoutError',tags:'测试标签'});assert.match(s,/超时/);assert.match(s,/测试标签/);assert.doesNotMatch(s,/未授权/);});
test('旧工具模板兼容对象错误及数字完成计数',()=>{const s=h({done:2,errors:[{error:'TimeoutError'}]});assert.match(s,/超时/);assert.doesNotMatch(s,/\[object Object\]/);});
test('导出字符串摘要不丢类型，旧授权状态不猜测',()=>{assert.match(h({session:'A'.repeat(100)}),/StringSession/);assert.doesNotMatch(h({old_logged_out:true}),/重生失败/);});
test('直接日志入口同样遮盖链接与含空格密码',()=>{const s=ctx.logLines([{action:'test',ok:1,detail:'password="SECRET VALUE" https://example.invalid/PRIVATE/GetHTML',created_at:1}]);assert.doesNotMatch(s,/SECRET|VALUE|PRIVATE/);});
test('重复摘要化不重复扩写错误名',()=>{const s=ctx.detailText('TimeoutError');assert.equal(ctx.detailText(s),s);});
test('日志详情补齐常见键值和接口错误',()=>{
 const s=h([{action:'list_groups',ok:1,created_at:1,detail:'kind=group count=3 label=测试'}]);assert.match(s,/数量=3/);assert.match(s,/别名=测试/);assert.match(h({error:'Method Not Allowed'}),/接口不支持此方法/);assert.match(h({error:'Not Found'}),/资源不存在/);
});
test('二验状态布尔字段不是密码，不能被遮盖误判',()=>{assert.match(h({has_password:true,had_password:false}),/已开启二步验证/);assert.match(h({has_password:false,has_recovery:false}),/未开启二步验证/);assert.match(h({has_twofa_saved:true}),/已保存二验：是/);});
test('日志缺少结果字段标记为未确认，不武断显示失败',()=>{const s=ctx.logLines([{action:'health_check',account_id:7,created_at:1,detail:'检查已入队'}]);assert.match(s, /\?/);assert.doesNotMatch(s, /✗/);});
