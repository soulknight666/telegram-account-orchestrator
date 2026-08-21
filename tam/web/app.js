const $ = s => document.querySelector(s);
const DEMO = location.protocol === 'file:' || new URLSearchParams(location.search).has('demo');
let accounts = [], stats = {total:0, by_status:{}}, sel = new Set(), filter = '', loginId = null, readonly = false;
let kick = {enabled:false, hours:24, watched:0, due_now:0, next_at:null};

/* ---------- 演示数据 ---------- */
const MOCK = {
  tasks: [
    {id: 12, kind: 'send', title: '群发 128 个目标', status: 'running', status_cn: '运行中',
     total: 128, ok_count: 74, fail_count: 3, skip_count: 0, done_count: 77, percent: 60.2,
     created_at: Date.now() / 1000 - 600, live: true, params: {html: true, concurrency: 2}},
    {id: 11, kind: 'collect_speakers', title: '采集最近 7 天发言人·2 个群',
     status: 'done', status_cn: '已完成', total: 2, ok_count: 2, fail_count: 0, skip_count: 0,
     done_count: 2, percent: 100, created_at: Date.now() / 1000 - 5400, live: false, params: {days: 7}},
    {id: 10, kind: 'send', title: '群发 40 个目标', status: 'stopped', status_cn: '已停止',
     total: 40, ok_count: 18, fail_count: 2, skip_count: 20, done_count: 40, percent: 100,
     created_at: Date.now() / 1000 - 86400, live: false, params: {}}],
  targets: [
    {id: 1, seq: 0, target: '@alice', account_id: 2, status: 'ok', status_cn: '成功', detail: '账号#2 已发送 message_id=8812'},
    {id: 2, seq: 1, target: '@bob', account_id: 3, status: 'fail', status_cn: '失败', detail: 'FloodWaitError: A wait of 300 seconds is required'},
    {id: 3, seq: 2, target: '778812345', account_id: 2, status: 'pending', status_cn: '待处理', detail: ''}],
  chats: [
    {id: -1001, title: 'Crypto Talk 中文群', username: 'cryptotalkcn', peer: '@cryptotalkcn',
     members: 12840, is_group: true, is_channel: false, megagroup: true, broadcast: false,
     unread: 12, last_msg_at: Date.now() / 1000 - 900},
    {id: -1002, title: 'Web3 Builders', username: null, peer: '-1002',
     members: 862, is_group: true, is_channel: false, megagroup: true, broadcast: false,
     unread: 0, last_msg_at: Date.now() / 1000 - 26000},
    {id: -1003, title: 'NFT 二级市场交流', username: 'nftsecond', peer: '@nftsecond',
     members: 3410, is_group: true, is_channel: false, megagroup: true, broadcast: false,
     unread: 3, last_msg_at: Date.now() / 1000 - 4 * 86400},
    {id: -1004, title: '老群（已冷）', username: null, peer: '-1004',
     members: 220, is_group: true, is_channel: false, megagroup: false, broadcast: false,
     unread: 0, last_msg_at: Date.now() / 1000 - 40 * 86400}],
  leadSources: [
    {source: 'Crypto Talk 中文群', item_count: 316, with_username: 251, last_seen: Date.now() / 1000 - 3600},
    {source: 'Web3 Builders', item_count: 88, with_username: 62, last_seen: Date.now() / 1000 - 90000}],
  leads: [
    {user_id: 60123, username: 'alice_w3', name: 'Alice', source: 'Crypto Talk 中文群', msg_count: 12, last_msg_at: Date.now() / 1000 - 4000, tags: ['高活跃']},
    {user_id: 60124, username: null, name: 'Bob', source: 'Crypto Talk 中文群', msg_count: 3, last_msg_at: Date.now() / 1000 - 40000, tags: []},
    {user_id: 60125, username: 'carol', name: 'Carol', source: 'Web3 Builders', msg_count: 7, last_msg_at: Date.now() / 1000 - 100000, tags: []}],

  accounts: [
    {id:1,label:'主号',phone:'+8613800138000',username:'gladys',status:'active',proxy:'socks5://127.0.0.1:1080',code_url:null,tags:['main'],authorized:true,premium:1,auto_kick:1,has_2fa:1,has_twofa_saved:true,twofa:'Demo2fa!',login_at:Math.floor(Date.now()/1000)-72000,adopted_at:Math.floor(Date.now()/1000)-72000},
    {id:2,label:'US-A',phone:'+10000000000',username:'coldstart_a',status:'active',proxy:'socks5://us1:1080',code_url:'https://example.invalid/code/account-a/GetHTML',tags:['batch1','us'],authorized:true,auto_kick:1,has_2fa:1,has_twofa_saved:false,login_at:Math.floor(Date.now()/1000)-90000,last_kick_at:null},
    {id:3,label:'US-B',phone:'+10000000001',username:null,status:'new',proxy:'socks5://us2:1080',code_url:'https://example.invalid/code/account-b/GetHTML',tags:['batch1','us'],authorized:false,has_2fa:0},
    {id:4,label:'US-C',phone:'+10000000002',username:null,status:'unauthorized',proxy:null,code_url:'https://example.invalid/code/account-c/GetHTML',tags:['batch1'],authorized:false,has_2fa:null},
    {id:5,label:'tdata-01',phone:'+447700900123',username:'desk_one',status:'restricted',proxy:'socks5://uk1:1080',code_url:null,tags:['tdata'],authorized:true,has_2fa:1,has_twofa_saved:true,twofa:'uk-pass'},
    {id:6,label:'tdata-02',phone:'+447700900124',username:null,status:'banned',proxy:'socks5://uk1:1080',code_url:null,tags:['tdata'],authorized:false,has_2fa:0}
  ],
  stats: {total:6, by_status:{active:2, new:1, unauthorized:1, restricted:1, banned:1}},
  autokick: {enabled:true, hours:24, watched:2, due_now:1, scan_interval_s:600,
            retry_after_s:3600, retry_after_text:'1 小时', retry_source:'env', retry_min_s:10,
             server_now: Math.floor(Date.now()/1000), max_overdue_s: 0,
             next_at: Math.floor(Date.now()/1000) + 5400},
  logs: [
    {id:42,account_id:2,action:'auto_login',ok:1,detail:'user_id=708812345',created_at:1753432740},
    {id:41,account_id:2,action:'fetch_code',ok:1,detail:'code_len=5',created_at:1753432712},
    {id:40,account_id:5,action:'health_check',ok:1,detail:'restricted: spam block until 2026-07-28',created_at:1753431980},
    {id:39,account_id:6,action:'health_check',ok:0,detail:'UserDeactivatedBanError',created_at:1753431975},
    {id:38,account_id:3,action:'import_accounts',ok:1,detail:'added=3 updated=0',created_at:1753431020}
  ]
};

/* ---------- 基础设施 ---------- */
const tokenKey = 'tam_token';
$('#token').value = localStorage.getItem(tokenKey) || '';
async function applyToken() {
  const v = $('#token').value.trim();
  $('#token').value = v;
  localStorage.setItem(tokenKey, v);
  $('#mode').textContent = '连接中…'; $('#mode').className = 'pill off';
  await probeMode();
  if ($('#mode').className.indexOf('off') < 0) { toast('令牌已生效，已连接', 'ok'); await refresh(); }
  else toast('令牌无效或服务未启动', 'err');
}
$('#token').addEventListener('change', applyToken);
$('#token').addEventListener('keydown', e => { if (e.key === 'Enter') applyToken(); });

function toast(msg, kind='') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 4200);
}
let busy = 0;
function progress(on) {
  busy = Math.max(0, busy + (on ? 1 : -1));
  $('#prog').style.width = busy ? '70%' : '0';
}
async function api(path, opts = {}) {
  if (DEMO) return demoApi(path, opts);
  progress(true);
  try {
    const res = await fetch(path, {...opts, headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + ($('#token').value || ''),
      ...(opts.headers || {})}});
    if (!res.ok) {
      const raw = await res.text();
      let msg = raw;
      try { const j = JSON.parse(raw); msg = j.detail || j.message || raw; } catch {}
      if (res.status === 401) msg = '令牌无效（401）：请核对 .env 里的 TAM_WEB_TOKEN';
      throw new Error(String(msg).slice(0, 400));
    }
    return res.json();
  } finally { progress(false); }
}
async function demoApi(path, opts) {
  await new Promise(r => setTimeout(r, 220));
  if (path.startsWith('/api/stats')) return MOCK.stats;
  if (path.startsWith('/api/accounts?') || path === '/api/accounts') return MOCK.accounts.map(a => {
    const o = Object.assign({}, a); delete o.twofa; return o; // 列表不带明文，与真实 API 一致
  });
  if (/^\/api\/accounts\/\d+\/twofa$/.test(path.split('?')[0]) && (!opts.method || opts.method === 'GET')) {
    const id = +path.split('/')[3];
    const a = MOCK.accounts.find(x => x.id === id) || {};
    return {ok:true, id, has_2fa: a.has_2fa, saved: !!a.has_twofa_saved, twofa: a.twofa || ''};
  }
  if (/^\/api\/accounts\/\d+\/twofa$/.test(path.split('?')[0]) && (opts.method || '').toUpperCase() === 'POST') {
    return {ok:true, saved:true, has_2fa:1};
  }
  if (path.startsWith('/api/logs')) return MOCK.logs;
  if (path.startsWith('/api/tools')) return {readonly:false, dry_run:false, tools:[]};
  if (path === '/api/tasks' || path.startsWith('/api/tasks?')) return MOCK.tasks;
  if (/^\/api\/tasks\/\d+$/.test(path.split('?')[0]))
    return Object.assign({}, MOCK.tasks[0], {targets: MOCK.targets});
  if (path.startsWith('/api/chats')) return {count: MOCK.chats.length, items: MOCK.chats};
  if (path === '/api/leads/sources') return MOCK.leadSources;

  if (path.startsWith('/api/leads/messages')) return {
    messages: [
      {user_id: 60123, username: 'alice_w3', name: 'Alice', source: 'Crypto Talk 中文群',
       text: '有人看过最新的白皮书吗？', date: Math.floor(Date.now()/1000) - 7200, reply_to: null},
      {user_id: 60124, username: null, name: 'Bob', source: 'Crypto Talk 中文群',
       text: '看了，第三节有点意思', date: Math.floor(Date.now()/1000) - 7000, reply_to: null},
      {user_id: 60123, username: 'alice_w3', name: 'Alice', source: 'Crypto Talk 中文群',
       text: '同意，周末整理一版笔记', date: Math.floor(Date.now()/1000) - 6800, reply_to: null}],
    stats: {total: 3, speakers: 2}};
  if (path.startsWith('/api/leads')) return {count: MOCK.leads.length, items: MOCK.leads};
  if (path === '/api/autokick') return MOCK.autokick;
  if (path === '/api/autokick/run') return {enabled:true, hours:24, due:1, retry_after_s:3600, results:[{account_id:2, ok:true, verified:true}]};
  if (path === '/api/autokick/retry') return {ok:true, retry_after_s:600, retry_after_text:'10 分钟', retry_source:'web'};
  return {ok:true, demo:true, path, body: opts.body ? JSON.parse(opts.body) : null};
}
/* ---------- 任务中心 ---------- */
const TASK_KIND_CN = {send: '群发消息', collect_speakers: '采集发言人'};
const TASK_TONE = {running: 'ok', pending: '', stopping: 'warn', stopped: 'warn', done: 'ok', failed: 'err'};
let taskTimer = null;

function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function taskCard(t) {
  const pct = Math.max(0, Math.min(100, t.percent || 0));
  const tone = TASK_TONE[t.status] || '';
  const canStop = t.status === 'running' || t.status === 'pending';
  return `<div class="taskrow">
    <div class="taskhead">
      <b>#${t.id} ${esc(t.title)}</b>
      <span class="pill ${tone}">${esc(t.status_cn || t.status)}</span>
      <span class="muted">${esc(TASK_KIND_CN[t.kind] || t.kind)} · ${rel(t.created_at)}</span>
      <span style="flex:1"></span>
      <button class="sm fit" onclick="taskDetail(${t.id})">明细</button>
      ${canStop ? `<button class="sm fit warn" onclick="stopTask(${t.id})">停止</button>`
                : `<button class="sm fit" onclick="delTask(${t.id})">删除</button>`}
    </div>
    <div class="bar"><i style="width:${pct}%"></i></div>
    <div class="muted small">进度 ${t.done_count || 0}/${t.total || 0}（${pct}%）
      · 成功 ${t.ok_count || 0} · 失败 ${t.fail_count || 0} · 已跳过 ${t.skip_count || 0}</div>
  </div>`;
}

async function loadTasks(manualClick) {
  try {
    const rows = await api('/api/tasks?limit=10');
    const el = $('#taskList');
    if (!el) return;
    if (!rows.length) {
      el.innerHTML = '<span class="muted">还没有任务。可以先采集发言人，再对采集结果群发。</span>';
      $('#taskHint').textContent = '暂无任务';
    } else {
      el.innerHTML = rows.map(taskCard).join('');
      const live = rows.filter(t => t.status === 'running' || t.status === 'pending').length;
      $('#taskHint').textContent = live ? `${live} 个进行中 · 共 ${rows.length} 个` : `最近 ${rows.length} 个`;
      clearTimeout(taskTimer);
      if (live) taskTimer = setTimeout(loadTasks, 3000);
    }
    if (manualClick) toast('任务列表已刷新', 'ok');
  } catch (e) { if (manualClick) toast('任务列表加载失败：' + e.message, 'err'); }
}

async function taskDetail(id) {
  try {
    const t = await api(`/api/tasks/${id}?target_limit=200`);
    const head = `任务 #${t.id}：${t.title}\n类型：${TASK_KIND_CN[t.kind] || t.kind}`
      + `\n状态：${t.status_cn || t.status}\n进度：${t.done_count}/${t.total}（${t.percent}%）`
      + `\n成功 ${t.ok_count} · 失败 ${t.fail_count} · 已跳过 ${t.skip_count}`
      + `\n创建于 ${fmt(t.created_at)}`
      + (t.finished_at ? `\n结束于 ${fmt(t.finished_at)}` : '');
    const bad = (t.targets || []).filter(x => x.status === 'fail');
    const lines = (t.targets || []).slice(0, 60).map(x =>
      `${x.status === 'ok' ? '✓' : (x.status === 'fail' ? '✗' : '·')} ${x.target}`
      + `${x.account_id ? '  由账号 #' + x.account_id : ''}  ${x.status_cn}`
      + `${x.detail ? '：' + x.detail : ''}`);
    const tail = (t.targets || []).length > 60 ? `\n…共 ${t.targets.length} 条，仅显示前 60 条` : '';
    const fail = bad.length ? `\n\n失败原因归类：\n` + Object.entries(
      bad.reduce((m, x) => { const k = (x.detail || '未知').split(':')[0]; m[k] = (m[k] || 0) + 1; return m; }, {})
    ).map(([k, v]) => `• ${k}：${v} 个`).join('\n') : '';
    $('#log').textContent = head + fail + '\n\n逐目标明细：\n' + lines.join('\n') + tail;
    $('#logRaw').textContent = JSON.stringify(t, null, 2);
    logPinned = true; $('#logPin').style.display = '';
  } catch (e) { toast('读取任务失败：' + e.message, 'err'); }
}

async function stopTask(id) {
  if (!await uiConfirm({title:'停止任务', message:'确定停止任务 #' + id + '？已发出的不会撤回，剩余目标会标为已跳过。', danger:true, okText:'停止'})) return;
  await guard(() => api(`/api/tasks/${id}/stop`, {method: 'POST'}), '已请求停止');
  loadTasks();
}

async function delTask(id) {
  if (!await uiConfirm({title:'删除任务', message:'删除任务 #' + id + ' 及其执行记录？', danger:true, okText:'删除'})) return;
  await guard(() => api(`/api/tasks/${id}`, {method: 'DELETE'}), '任务已删除');
  loadTasks();
}

let chatRows = [];
const chatPicked = new Set();

async function newCollectTask() {
  const sel = $('#cAcc');
  const usable = accounts.filter(a => a.status === 'active' || a.status === 'restricted');
  const pool = usable.length ? usable : accounts;
  sel.innerHTML = pool.map(a =>
    `<option value="${a.id}">${a.label}（${fmtPhone(a.phone) || '未知号码'}）</option>`).join('');
  if (!pool.length) { toast('还没有可用账号，先导入并登录', 'err'); return; }
  chatPicked.clear();
  openModal('mChats');
  loadChats();
}

async function loadChats(force) {
  const el = $('#chatList');
  el.innerHTML = '<span class="muted">正在拉取群组…群多时需等几秒</span>';
  try {
    const aid = $('#cAcc').value;
    const kind = $('#cKind').value;
    const res = await api(`/api/chats?account_id=${aid}&kind=${kind}&limit=200`);
    chatRows = res.items || [];
    if (force) toast(`已拉到 ${chatRows.length} 个会话`, 'ok');
    renderChats();
  } catch (e) {
    el.innerHTML = `<span class="muted">拉取失败：${e.message}</span>`;
  }
}

function chatVisible() {
  const key = ($('#cSearch').value || '').trim().toLowerCase();
  const onlyActive = $('#cActive').checked;
  const cut = Date.now() / 1000 - 7 * 86400;
  return chatRows.filter(c => {
    if (onlyActive && !(c.last_msg_at && c.last_msg_at >= cut)) return false;
    if (!key) return true;
    return (c.title || '').toLowerCase().indexOf(key) >= 0
      || ('@' + (c.username || '')).toLowerCase().indexOf(key) >= 0;
  });
}

function renderChats() {
  const rows = chatVisible();
  const el = $('#chatList');
  if (!rows.length) {
    el.innerHTML = '<span class="muted">没有匹配的群。可取消“只看 7 天内有新消息的”再看看。</span>';
  } else {
    el.innerHTML = rows.map(c => {
      const tag = c.broadcast ? '频道' : (c.megagroup ? '超级群' : '群');
      const mem = c.members ? `${c.members} 人` : '人数未知';
      return `<label class="chatrow">
        <input type="checkbox" ${chatPicked.has(c.peer) ? 'checked' : ''}
          onchange="toggleChat('${String(c.peer).replace(/'/g, "\\'")}', this.checked)" />
        <span class="t"><b>${esc(c.title)}</b>${c.username ? ' <span class="muted">@' + esc(c.username) + '</span>' : ''}</span>
        <span class="muted">${tag} · ${mem} · ${c.last_msg_at ? rel(c.last_msg_at) : '无消息'}</span>
      </label>`;
    }).join('');
  }
  $('#cCount').textContent = `已选 ${chatPicked.size} 个群 · 当前显示 ${rows.length}/${chatRows.length}`;
}

function toggleChat(peer, on) {
  if (on) chatPicked.add(peer); else chatPicked.delete(peer);
  $('#cCount').textContent = `已选 ${chatPicked.size} 个群 · 当前显示 ${chatVisible().length}/${chatRows.length}`;
}

function pickChats(all) {
  if (all) chatVisible().forEach(c => chatPicked.add(c.peer));
  else chatPicked.clear();
  renderChats();
}

async function startCollect() {
  if (!chatPicked.size) { toast('至少选一个群', 'err'); return; }
  const body = {
    chats: Array.from(chatPicked),
    account_id: parseInt($('#cAcc').value, 10),
    days: parseFloat($('#cDays').value) || 7,
    limit: parseInt($('#cLimit').value, 10) || 300,
    scan: parseInt($('#cScan').value, 10) || 3000,
    skip_bots: $('#cSkipBot').checked,
    skip_premium: $('#cSkipPrem').checked,
    capture_messages: $('#cCapture').checked,
    text_limit: parseInt($('#cTextLimit').value, 10) || 4000,
    tags: ($('#cTags').value || '').split(/[,，]/).map(x => x.trim()).filter(Boolean),
  };
  closeAll();
  await guard(() => api('/api/tasks/collect', {method: 'POST', body: JSON.stringify(body)}),
    `采集任务已创建（${body.chats.length} 个群）`);
  loadTasks();
}

async function newMessageTask() {
  const src = await uiPrompt({
    title: '发送目标',
    message: '• 直接填目标，逗号分隔（@name 或 user_id）\n• 或填 lead:线索来源名',
    value: 'lead:',
    label: '目标',
  });
  if (src === null || !String(src).trim()) return;
  const text = await uiPrompt({
    title: '发送内容',
    message: '支持 {a|b} 变体、{name} 变量、HTML 超链接。',
    label: '正文',
  });
  if (text === null || !text) return;
  let html = false;
  if (/<[a-z]/i.test(text)) {
    html = await uiConfirm({title: '富文本', message: '检测到 HTML 标签，是否按富文本发送（超链接可点击）？', okText: '富文本发送'});
  }
  const body = {text, html, spintax: true, concurrency: 1, delay: 0};
  if (src.indexOf('lead:') === 0) body.lead_source = src.slice(5).trim();
  else body.peers = src.split(/[,\uff0c]/).map(x => x.trim()).filter(Boolean);
  await guard(() => api('/api/tasks/message', {method: 'POST', body: JSON.stringify(body)}),
    '群发任务已创建');
  loadTasks();
}

/* ---------- 线索库 ---------- */
let leadRows = [];

async function loadLeads() {
  try {
    const [srcs, data] = await Promise.all([api('/api/leads/sources'), api('/api/leads?limit=500')]);
    leadRows = data.items || [];
    const el = $('#leadList');
    if (!el) return;
    $('#leadHint').textContent = leadRows.length
      ? `共 ${data.count} 人 · ${srcs.length} 个来源` : '';
    if (!leadRows.length) {
      el.innerHTML = '<span class="muted">还没有采集到线索。先在任务中心点“采集发言人”。</span>';
      return;
    }
    const bySrc = srcs.map(x => `<div class="leadsrc">
      <b>${esc(x.source)}</b><span class="muted">${x.item_count} 人·有用户名 ${x.with_username || 0}</span>
      <span style="flex:1"></span>
      <button class="sm fit" onclick="viewLeadMsgs(null, '${esc(x.source).replace(/'/g, "\\'")}')">看对话</button>
      <button class="sm fit write" onclick="sendToSource('${esc(x.source).replace(/'/g, "\\'")}')">向它群发</button>
      </div>`).join('');
    const list = leadRows.slice(0, 50).map(x =>
      `<div class="leadrow"><span>${x.username ? '@' + esc(x.username) : esc(x.name || x.user_id)}</span>
       <span class="muted">${esc(x.source)} · 发言 ${x.msg_count || 0} 条 · ${rel(x.last_msg_at)}</span>
       <span style="flex:1"></span>
       <button class="sm fit" onclick="viewLeadMsgs(${Number(x.user_id)}, '${esc(x.source).replace(/'/g, "\\'")}')">对话</button></div>`).join('');
    el.innerHTML = bySrc + '<div class="hr"></div>' + list
      + (leadRows.length > 50 ? `<div class="muted small">…共 ${leadRows.length} 条，导出 CSV 看全部</div>` : '');
  } catch (e) { /* 未登录时静默 */ }
}

async function sendToSource(source) {
  const text = await uiPrompt({
    title: '向线索群发',
    message: '向「' + source + '」的全部线索发送（支持 {a|b} 与 {name} 变量）。',
    label: '正文',
  });
  if (text === null || !text) return;
  await guard(() => api('/api/tasks/message', {method: 'POST', body: JSON.stringify(
    {lead_source: source, text, spintax: true})}), '群发任务已创建');
  loadTasks();
}

function toggleSessMode() {
  const mode = (document.querySelector('input[name="sessMode"]:checked') || {}).value || 'upload';
  $('#sessUploadBox').style.display = mode === 'upload' ? '' : 'none';
  $('#sessFileBox').style.display = mode === 'file' ? '' : 'none';
  $('#sessTextBox').style.display = mode === 'text' ? '' : 'none';
}

document.addEventListener('change', function (e) {
  if (!e.target || e.target.id !== 'sfUpload') return;
  const files = e.target.files || [];
  const el = $('#sfUploadHint');
  if (!el) return;
  if (!files.length) { el.textContent = '尚未选择文件'; return; }
  const names = Array.from(files).map(f => f.name + ' (' + Math.round(f.size / 1024) + 'KB)');
  el.textContent = '已选 ' + files.length + ' 个：' + names.slice(0, 5).join('、')
    + (names.length > 5 ? '…' : '');
});

async function _sessionUploadOne(file, label, proxy, tags) {
  const q = new URLSearchParams();
  if (label) q.set('label', label);
  if (proxy) q.set('proxy', proxy);
  if (tags && tags.length) q.set('tags', tags.join(','));
  const path = '/api/accounts/import-session-upload' + (q.toString() ? '?' + q.toString() : '');
  const headers = {'X-Filename': file.name};
  if (!DEMO) {
    const tok = ($('#token') && $('#token').value) || localStorage.getItem('tam_token') || '';
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
  }
  if (DEMO) {
    return {ok: true, total: 1, succeeded: 1, failed: 0,
            items: [{ok: true, label: file.name, user_id: 900001}], filename: file.name};
  }
  const res = await fetch(path, {method: 'POST', body: file, headers});
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch (_) { data = {detail: text}; }
  if (!res.ok) throw new Error(data.detail || data.error || res.statusText || '上传失败');
  return data;
}

async function doSessionImport() {
  const mode = (document.querySelector('input[name="sessMode"]:checked') || {}).value || 'upload';
  const tags = $('#sfTags').value ? $('#sfTags').value.split(',').map(s => s.trim()).filter(Boolean) : [];
  const proxy = $('#sfProxy').value || null;
  const label = $('#sfLabel').value || null;
  try {
    let r;
    if (mode === 'upload') {
      const input = $('#sfUpload');
      const files = input && input.files ? Array.from(input.files) : [];
      if (!files.length) { toast('请选择要上传的 .session 或 zip', 'err'); return; }
      const merged = {ok: true, total: 0, succeeded: 0, failed: 0, items: []};
      for (let fi = 0; fi < files.length; fi++) {
        const f = files[fi];
        const q = new URLSearchParams();
        if (label) q.set('label', label);
        if (proxy) q.set('proxy', proxy);
        if (tags && tags.length) q.set('tags', tags.join(','));
        const path = '/api/accounts/import-session-upload' + (q.toString() ? '?' + q.toString() : '');
        const headers = {'X-Filename': f.name};
        const title = files.length > 1
          ? ('session 导入 文件 ' + (fi + 1) + '/' + files.length + ' · ' + f.name)
          : ('session 导入 · ' + f.name);
        const one = await fetchImportStream(path, {method: 'POST', body: f, headers: headers}, title);
        merged.total += one.total || (one.items || []).length || 0;
        merged.succeeded += one.succeeded || 0;
        merged.failed += one.failed || 0;
        merged.items = merged.items.concat(one.items || []);
      }
      r = merged;
    } else if (mode === 'file') {
      const path = ($('#sfPath').value || '').trim();
      if (!path) { toast('请填写服务端本地路径', 'err'); return; }
      r = await fetchImportStream('/api/accounts/import-sessions', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path, scan: $('#sfScan').checked, label, proxy, tags}),
      }, 'session 路径导入');
    } else {
      const text = ($('#sfText').value || '').trim();
      if (!text) { toast('请粘贴 StringSession 文本', 'err'); return; }
      r = await fetchImportStream('/api/accounts/import-session-strings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text, label, proxy, tags}),
      }, 'StringSession 导入');
    }
    const items = r.items || [];
    const okN = r.succeeded != null ? r.succeeded : items.filter(x => x.ok).length;
    const badN = r.failed != null ? r.failed : items.length - okN;
    const lines = items.map(it => it.ok
      ? ('  ✓ ' + (it.label || it.file || '') + ' user_id=' + (it.user_id || (it.info && it.info.user_id) || '?'))
      : ('  ✗ ' + (it.label || it.file || '') + ' ' + (it.error || '失败'))).join('\n');
    out('session 导入完成：成功 ' + okN + '，失败 ' + badN + '\n' + (lines || JSON.stringify(r, null, 2)), true);
    if (okN) { toast('导入成功 ' + okN + ' 个' + (badN ? '，失败 ' + badN : ''), badN ? '' : 'ok'); closeAll(); }
    else toast('导入失败 0 成功，详情见日志区', 'err');
    refresh();
  } catch (e) { toast('失败：' + e.message, 'err'); out({error: String(e)}, true); }
}

async function viewLeadMsgs(userId, source) {
  try {
    const q = new URLSearchParams();
    if (userId != null && userId !== '') q.set('user_id', String(userId));
    if (source) q.set('source', source);
    q.set('limit', '200');
    const r = await api('/api/leads/messages?' + q.toString());
    const msgs = r.messages || [];
    const st = r.stats || {};
    const titleBits = [];
    if (userId) titleBits.push('用户 ' + userId);
    if (source) titleBits.push(source);
    const title = titleBits.join(' · ') || '对话记录';
    closeAll();
    $('#dTitle').textContent = '对话 · ' + title;
    if (!msgs.length) {
      $('#dBody').innerHTML = '<div class="chat-empty">没有对话记录。<br/>采集发言人时请勾选「同时存对话记录」。</div>';
      $('#drawer').classList.add('on'); $('#bd').classList.add('on');
      toast('没有对话记录', 'err');
      return;
    }
    const stats = '<div class="chat-stats">本页 ' + msgs.length + ' 条'
      + (st.total != null ? ' · 库内共 ' + st.total + ' 条 / ' + (st.speakers || '?') + ' 人' : '')
      + '</div>';
    const rows = msgs.map(function (m) {
      const t = m.date ? new Date(m.date * 1000).toLocaleString() : '';
      const who = m.username ? ('@' + m.username) : (m.name || ('#' + (m.user_id || '')));
      const reply = m.reply_to ? (' · 回复 #' + m.reply_to) : '';
      return '<div class="msg-row">'
        + '<div class="msg-meta">' + esc(who) + ' · ' + esc(t) + esc(reply) + '</div>'
        + '<div class="msg-bubble">' + esc(m.text || '（无文本）') + '</div>'
        + '</div>';
    }).join('');
    $('#dBody').innerHTML = stats + '<div class="chat">' + rows + '</div>';
    $('#drawer').classList.add('on'); $('#bd').classList.add('on');
    toast('已加载 ' + msgs.length + ' 条对话', 'ok');
  } catch (e) { toast('加载对话失败：' + e.message, 'err'); }
}


function exportLeads() {
  if (!leadRows.length) { toast('现在没有线索可导出', 'err'); return; }
  const head = ['user_id', 'username', 'name', 'source', 'msg_count', 'last_msg_at'];
  const csv = [head.join(',')].concat(leadRows.map(r => head.map(k => {
    const v = k === 'last_msg_at' ? fmt(r[k]) : (r[k] === null || r[k] === undefined ? '' : r[k]);
    return '"' + String(v).replace(/"/g, '""') + '"';
  }).join(','))).join('\n');
  const blob = new Blob(['\ufeff' + csv], {type: 'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'tam-leads.csv';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  toast(`已导出 ${leadRows.length} 条线索`, 'ok');
}

async function runDoctor() {
  toast('正在体检并自动修复，可能需要一分钟…');
  try {
    const r = await api('/api/doctor/fix', {method: 'POST'});
    const lines = (r.checks || []).map(c => {
      const mark = c.status === 'ok' ? '✓' : (c.status === 'warn' ? '!' : '✗');
      const fixed = c.fixed ? '（已自动修复）' : '';
      const hint = c.hint && c.status !== 'ok' ? `\n     → ${c.hint}` : '';
      return `${mark} ${c.name}：${c.detail || ''}${fixed}${hint}`;
    });
    out(lines.join('\n'), true);
    if (r.ok) toast(r.fixed && r.fixed.length ? `体检通过，已自动修复 ${r.fixed.length} 项` : '体检全部通过', 'ok');
    else toast(`${r.failed.length} 项未通过，详情见下方日志区`, 'err');
    refresh();
  } catch (e) { toast('体检失败：' + e.message, 'err'); out({error: String(e)}, true); }
}

async function hotReload() {
  if (!await uiConfirm({
    title: '热重载（真实重启）',
    message: '将真实重启后端进程（os.execv），约 1–3 秒服务不可用。\\n进行中的导入/任务会中断。\\n确认继续？',
    okText: '重启',
    danger: true,
  })) return;
  try {

    uiProgress({title: '热重载', current: 0, total: 1, text: '正在请求重启…'});
    const r = await api('/api/system/restart', {
      method: 'POST',
      body: JSON.stringify({confirm: true}),
    });
    out(r, true);
    toast(r.message || '正在重启…', 'ok');
    const ok = await waitForRestart(r.instance_id || null);
    uiProgress(null);
    if (ok) {
      toast('服务已恢复', 'ok');
      refresh();
    } else {
      toast('等待超时：请手动刷新页面或检查进程是否拉起', 'err');
    }
  } catch (e) {
    uiProgress(null);
    // 重启瞬间请求可能被掐断，也当作进入轮询
    toast('连接中断，正在等待重启…', '');
    const ok = await waitForRestart(null);
    uiProgress(null);
    if (ok) { toast('服务已恢复', 'ok'); refresh(); }
    else toast('重启可能失败：' + e.message, 'err');
  }
}

async function waitForRestart(previousInstanceId) {
  let sawUnavailable = false;
  for (let i = 0; i < 40; i++) {
    await new Promise(res => setTimeout(res, 800));
    uiProgress({
      title: '热重载',
      current: i + 1,
      total: 40,
      text: '等待新进程启动… (' + (i + 1) + '/40)',
    });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1500);
    try {
      const res = await fetch('/api/system/status', {
        cache: 'no-store',
        signal: controller.signal,
        headers: {'Authorization': 'Bearer ' + (($('#token') && $('#token').value) || '')},
      });
      if (!res.ok) {
        sawUnavailable = true;
        continue;
      }
      const status = await res.json();
      if (previousInstanceId && status.instance_id !== previousInstanceId) return true;
      if (!previousInstanceId && sawUnavailable) return true;
    } catch (_) {
      sawUnavailable = true;
    } finally {
      clearTimeout(timer);
    }
  }
  return false;
}

function openErrorLog() {
  openModal('mErrors');
  loadErrorLog();
}

async function loadErrorLog() {
  const pre = $('#errList');
  const st = $('#errStats');
  if (pre) pre.textContent = '加载中…';
  try {
    const r = await api('/api/system/errors?limit=80');
    const stats = r.stats || {};
    if (st) {
      st.textContent = '共 ' + (stats.total || 0) + ' 条'
        + (stats.latest_at ? ' · 最近 ' + rel(stats.latest_at) : '');
    }
    const items = r.items || [];
    if (!items.length) {
      if (pre) pre.textContent = '暂无错误记录。';
      return;
    }
    const lines = items.map(function (e) {
      const t = e.created_at ? fmt(e.created_at) : '';
      const head = '[' + t + '] ' + (e.level || 'error') + '/' + (e.source || '')
        + (e.path ? ' ' + e.path : '') + (e.id != null ? ' #' + e.id : '');
      const body = e.message || '';
      const tb = e.traceback ? ('\\n' + String(e.traceback).split('\\n').slice(-6).join('\\n')) : '';
      return head + '\\n  ' + body + tb;
    });
    if (pre) pre.textContent = lines.join('\\n\\n');
  } catch (e) {
    if (pre) pre.textContent = '加载失败：' + e.message;
  }
}

async function exportErrorLog() {
  try {
    const name = await _downloadBlob('/api/system/errors/export?limit=200', null, 'tam-error-report.json', 'GET');
    toast('已下载 ' + (name || '错误报告'), 'ok');
  } catch (e) {
    // _downloadBlob may be POST-only — fallback fetch
    try {
      const res = await fetch('/api/system/errors/export?limit=200', {
        headers: {'Authorization': 'Bearer ' + (($('#token') && $('#token').value) || '')},
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'tam-error-report.json';
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 800);
      toast('已下载错误报告', 'ok');
    } catch (e2) {
      toast('导出失败：' + e2.message, 'err');
    }
  }
}

async function clearErrorLog() {
  if (!await uiConfirm({title: '清空错误日志', message: '确定清空全部错误记录？', danger: true, okText: '清空'})) return;
  try {
    const r = await api('/api/system/errors', {method: 'DELETE'});
    toast('已删除 ' + (r.deleted || 0) + ' 条', 'ok');
    loadErrorLog();
  } catch (e) {
    toast('清空失败：' + e.message, 'err');
  }
}

function reportClientError(message, extra) {
  try {
    const body = {
      message: String(message || 'unknown').slice(0, 4000),
      source: 'client',
      path: location.pathname,
      href: location.href,
      stack: (extra && extra.stack) ? String(extra.stack).slice(0, 8000) : undefined,
      extra: extra || undefined,
    };
    // 不走 api() 以免再触发 progress；失败静默
    fetch('/api/system/errors', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + (($('#token') && $('#token').value) || localStorage.getItem('tam_token') || ''),
      },
      body: JSON.stringify(body),
    }).catch(function () {});
  } catch (_) {}
}

window.addEventListener('error', function (ev) {
  reportClientError(ev.message || 'window.error', {
    stack: ev.error && ev.error.stack,
    file: ev.filename,
    line: ev.lineno,
    col: ev.colno,
  });
});
window.addEventListener('unhandledrejection', function (ev) {
  const r = ev.reason;
  reportClientError(
    (r && r.message) ? r.message : String(r || 'unhandledrejection'),
    {stack: r && r.stack}
  );
});



/* pin=true 时锁定日志区，自动刷新不再覆盖，直到用户点“恢复自动日志” */
let logPinned = false;

/* ---------- 人性化中文渲染 ---------- */
const ACTION_CN = {
  import_session:'导入会话', import_session_file:'导入会话文件', import_sessions:'导入 session 文件',
  import_session_strings:'导入 StringSession', import_session_upload:'上传导入 session',
  import_accounts:'批量导入账号', import_tdata:'tdata 导入', import_tdata_upload:'上传导入 tdata',
  health_check:'健康检查', send_code:'发送验证码', sign_in:'验证码登录',
  auto_login:'自动取码登录', qr_login_start:'扫码登录发起', qr_login:'扫码登录', fetch_code:'抓取验证码', send_message:'发送消息',
  update_profile:'修改资料', terminate_sessions:'清理其它设备', logout:'退出登录', delete:'删除账号',
  spam_check:'限制检测', okpay_balance:'OKPay 余额', regenerate_session:'重生会话', twofa_status:'二验状态', twofa_reset:'发起2FA重置', twofa_reset_cancel:'取消2FA重置', twofa:'改二步验证', search_public:'搜索公开', join_chat:'加入群聊', list_members:'群成员', read_messages:'读群消息', list_contacts:'通讯录', add_contact:'加联系人', delete_contact:'删联系人', send_media:'发文件', download_media:'下媒体', create_channel:'建频道', set_username:'设用户名', interact_bot:'机器人交互', privacy:'隐私设置', contacts_clear:'清空通讯录', dialogs_clear:'清理会话', profile_clear:'防找回清理', logout:'退出登录', delete_tg_account:'注销TG账号', alive:'筛活',
  export_session_string:'导出 StringSession', export_session_file:'导出 .session',
  export_session_pack:'导出号包', export_tdata:'导出 tdata',
  warmup:'养号', warmup_chat:'养号互聊', auto_kick:'自动清设备',
  list_groups:'列出群组', collect_recent:'采集发言人', sync_login_at:'同步登录时间',
  'toolbox.one':'工具箱单号', 'toolbox.batch':'工具箱批量',
  'tools.unpack':'ZIP 拆包', 'tools.merge':'ZIP 合并', 'tools.regtime':'按注册时间分类',
  'settings.save':'保存参数', 'settings.reset':'重置参数',
  'tools.call':'Agent 工具调用'
};
const STATUS_CN = {new:'待登录', active:'正常', spam_block:'临时限制', spam_block_perm:'永久限制',
  frozen:'冻结', unauthorized:'会话失效', restricted:'受限', banned:'已封禁', error:'异常',
  flood_wait:'触发频率限制'};
const cnStatus = s => STATUS_CN[s] ? `${STATUS_CN[s]}（${s}）` : (s || '未知');
function whoIs(id) {
  if (id === null || id === undefined) return '系统';
  const a = accounts.find(x => x.id === id);
  return a ? `${a.label}${a.phone ? ' ' + fmtPhone(a.phone) : ''}` : `账号 #${id}`;
}
function logLines(rows) {
  return rows.map(r => `${fmt(r.created_at)}  ${r.ok ? '✓' : '✗'} ${ACTION_CN[r.action] || r.action}`
    + `  ${whoIs(r.account_id)}${r.detail ? '  ' + r.detail : ''}`).join('\n');
}
function humanize(d) {
  if (d === null || d === undefined) return null;
  if (Array.isArray(d)) {
    if (!d.length) return '（空列表）';
    const f = d[0];
    if (f && f.action !== undefined && f.created_at !== undefined)
      return `操作日志 ${d.length} 条（自新到旧）\n` + logLines(d);
    if (f && (f.device !== undefined || f.platform !== undefined || f.app !== undefined))
      return `该账号当前登录设备 ${d.length} 台：\n` + d.map(x =>
        `• ${x.app || x.device || '未知客户端'}${x.platform ? ' · ' + x.platform : ''}`
        + `${x.country ? ' · ' + x.country : ''}${x.current ? ' · 本机（当前会话）' : ''}`
        + `${x.date_created ? ' · 登录于 ' + String(x.date_created).replace('T', ' ').slice(0, 16) : ''}`
        + `${x.date_active ? ' · 最近活跃 ' + String(x.date_active).replace('T', ' ').slice(0, 16) : ''}`).join('\n')
        + (d.length > 1 ? '\n提示：可用「清理其他设备 / 批量踢出设备」踢掉除本机外的登录。' : '');
    if (f && (f.name !== undefined || f.title !== undefined))
      return `最近会话 ${d.length} 个：\n` + d.map(x =>
        `• ${x.name || x.title}${x.unread ? `（未读 ${x.unread}）` : ''}`).join('\n');
    // tdata/session 导入：[{path, accounts:[{ok,...}]}]
    if (f && (f.accounts !== undefined || f.path !== undefined) && (f.accounts || f.path)) {
      return d.map(e => {
        const accs = e.accounts || [];
        const okN = accs.filter(a => a.ok).length;
        const lines = accs.map(a => a.ok
          ? `  ✓ ${a.label || ''} user_id=${a.user_id || '?'}`
          : `  ✗ ${a.label || a.error || '失败'}${a.error && a.label ? ' · ' + a.error : ''}`);
        return `目录：${e.path || '—'}\n成功 ${okN}/${accs.length}\n` + (lines.join('\n') || '  （无账号明细）');
      }).join('\n\n');
    }
    // 批量账号结果
    if (f && (f.account_id !== undefined || f.id !== undefined) && (f.ok !== undefined || f.error !== undefined || f.spam_status !== undefined || f.balances !== undefined)) {
      const ok = d.filter(x => x.ok !== false && !x.error).length;
      return `批量结果：共 ${d.length} 个，约成功 ${ok}，失败 ${d.length - ok}\n` + d.map(x => {
        const id = x.account_id ?? x.id;
        let extra = '';
        if (x.error) extra = '  ' + x.error;
        else if (x.spam_status) extra = '  ' + cnStatus(x.spam_status) + (x.reply ? '\n    回复：' + String(x.reply).slice(0, 120) : '');
        else if (x.balances && Object.keys(x.balances).length)
          extra = '  ' + Object.entries(x.balances).map(([k,v]) => k + '=' + v).join(' · ');
        else if (x.status) extra = '  ' + cnStatus(x.status);
        else if (x.device_model) extra = '  新设备 ' + x.device_model;
        return `${x.ok === false || x.error ? '✗' : '✓'} ${whoIs(id)}${extra}`;
      }).join('\n');
    }
    // 通用对象数组
    try {
      return `列表 ${d.length} 条：\n` + d.slice(0, 30).map((x, i) => {
        if (typeof x !== 'object' || !x) return `• ${String(x)}`;
        const keys = Object.keys(x).slice(0, 6);
        return `• [${i + 1}] ` + keys.map(k => `${k}=${typeof x[k] === 'object' ? JSON.stringify(x[k]).slice(0, 40) : x[k]}`).join(' · ');
      }).join('\n') + (d.length > 30 ? `\n…另有 ${d.length - 30} 条` : '');
    } catch (_) { return null; }
  }
  if (typeof d !== 'object') return String(d);
  if (d.error && !d.ok && Object.keys(d).length <= 3) return `出错了：${typeof d.error === 'object' ? JSON.stringify(d.error) : d.error}`;
  if (d.detail && !d.ok && typeof d.detail === 'string' && Object.keys(d).length <= 4)
    return `出错了：${d.detail}`;

  // 批量汇总 {items, succeeded, failed, total}
  if (Array.isArray(d.items) && (d.succeeded !== undefined || d.total !== undefined || d.failed !== undefined)) {
    const items = d.items;
    const ok = d.succeeded != null ? d.succeeded : items.filter(x => x.ok).length;
    const fail = d.failed != null ? d.failed : (items.length - ok);
    const head = `批量完成：成功 ${ok}，失败 ${fail}` + (d.total != null ? `，共 ${d.total}` : `，共 ${items.length}`);
    const lines = items.slice(0, 40).map(x => {
      const id = x.account_id ?? x.id;
      if (x.ok === false || x.error) return `✗ ${whoIs(id)}  ${x.error || '失败'}`;
      if (x.balances) return `✓ ${whoIs(id)}  ` + Object.entries(x.balances).map(([k,v]) => k + '=' + v).join(' · ');
      if (x.spam_status) return `✓ ${whoIs(id)}  ${cnStatus(x.spam_status)}`;
      if (x.device_model) return `✓ ${whoIs(id)}  新设备 ${x.device_model}` + (x.old_logged_out ? ' · 旧授权已注销' : '');
      if (x.remaining_others !== undefined) return `✓ ${whoIs(id)}  踢设备 · 剩余其它 ${x.remaining_others}`;
      return `✓ ${whoIs(id)}` + (x.label ? '  ' + x.label : '');
    });
    return head + (lines.length ? '\n' + lines.join('\n') : '') + (items.length > 40 ? `\n…另有 ${items.length - 40} 条` : '');
  }

  if (Array.isArray(d.results)) {
    const rs = d.results, ok = rs.filter(x => x.ok).length;
    const head = d.hours !== undefined
      ? `自动清设备：登录满 ${d.hours} 小时的账号共 ${d.due ?? rs.length} 个，成功 ${ok}，失败 ${rs.length - ok}`
      : `批量结果：共 ${rs.length} 个账号，成功 ${ok}，失败 ${rs.length - ok}`;
    return head + (rs.length ? '\n' + rs.map(x =>
      `${x.ok ? '✓' : '✗'} ${whoIs(x.account_id ?? x.id)}${x.error ? '  ' + x.error : ''}`).join('\n') : '\n本轮没有到期的账号');
  }

  // 重生会话
  if (d.old_logged_out !== undefined || (d.ok && d.device_model && d.note && String(d.note).indexOf('新会话') >= 0))
    return `${d.ok ? '✓ 会话已重生' : '✗ 重生失败'}`
      + (d.label ? ` · ${d.label}` : '')
      + (d.user_id ? ` · user_id=${d.user_id}` : '')
      + (d.device_model ? `\n新设备指纹：${d.device_model} / ${d.app_version || ''} / ${d.system_version || ''}` : '')
      + (d.old_logged_out !== undefined ? `\n旧授权注销：${d.old_logged_out ? '已执行' : '未确认（可能仍有效）'}` : '')
      + (d.note ? `\n${d.note}` : '');

  // OKPay 余额
  if (d.balances !== undefined || (d.bot && d.reply !== undefined && d.account_id !== undefined)) {
    const b = d.balances || {};
    const keys = Object.keys(b);
    return `OKPay 余额查询 · 机器人 @${d.bot || 'Okpay'}`
      + (d.account_id != null ? ` · ${whoIs(d.account_id)}` : '')
      + (keys.length ? `\n解析到：` + keys.map(k => `${k} = ${b[k]}`).join('，') : '\n未能自动解析金额')
      + (d.reply ? `\n原文摘录：\n${String(d.reply).slice(0, 500)}` : '');
  }

  // 二验状态 / 发起重置（工具箱单号或详情按钮）
  if (d.result && d.op === 'twofa_status' && d.result.has_password !== undefined)
    d = Object.assign({account_id: d.account_id}, d.result);
  if (d.result && (d.op === 'twofa_reset' || d.op === 'twofa_reset_cancel') && d.result.note)
    d = Object.assign({account_id: d.account_id}, d.result);
  if (d.has_recovery !== undefined && (d.has_password !== undefined || d.pending_reset !== undefined) && d.spam_status === undefined && !d.items) {
    const lines = [];
    lines.push(d.has_password ? '已开启二步验证' : '未开启二步验证');
    lines.push(d.has_recovery ? '辅助邮箱：已绑定' : '辅助邮箱：未绑定');
    if (d.hint) lines.push('提示语：' + d.hint);
    if (d.pending_reset) lines.push('重置等待中' + ((d.pending_reset_iso || d.pending_reset_date) ? (' · 至 ' + (d.pending_reset_iso || fmt(d.pending_reset_date))) : ''));
    if (d.action === 'reset' || d.result === 'waiting' || d.result === 'ok' || d.result === 'failed_wait') {
      lines.push('重置结果：' + (d.result || d.action));
      if (d.until_iso || d.until_date) lines.push('等待至：' + (d.until_iso || fmt(d.until_date)));
      if (d.retry_iso || d.retry_date) lines.push('可重试时间：' + (d.retry_iso || fmt(d.retry_date)));
    }
    if (d.note) lines.push(d.note);
    return lines.join('\n');
  }

  // 限制检测
  if (d.spam_status !== undefined)
    return `限制检测：${cnStatus(d.spam_status)}`
      + (d.account_id != null ? ` · ${whoIs(d.account_id)}` : '')
      + (d.until ? `\n预计相关时间：${fmt(d.until)}` : '')
      + (d.reply ? `\nSpamBot 回复：\n${String(d.reply).slice(0, 400)}` : '');

  // 踢设备单号
  if (d.remaining_others !== undefined || d.verified !== undefined || (d.before !== undefined && d.ok !== undefined && d.account_id === undefined && !d.items))
    return `清理其它设备：${d.ok || d.verified ? '完成' : '未完全确认'}`
      + (d.before != null ? ` · 踢前共 ${d.before} 个授权` : '')
      + (d.remaining_others != null ? ` · 仍残留其它设备 ${d.remaining_others}` : '')
      + (d.detail ? `\n${d.detail}` : '');

  // 导出
  if (d.session && typeof d.session === 'string' && d.session.length > 20)
    return `已导出 StringSession` + (d.label ? ` · ${d.label}` : '') + (d.user_id ? ` · user_id=${d.user_id}` : '')
      + `\n（完整字符串见右侧原始数据，请勿泄露）`;
  if (d.path && (d.ok || d.label) && !d.accounts)
    return `文件已生成` + (d.label ? ` · ${d.label}` : '') + `\n路径：${d.path}`;

  // 健康 / 检查类
  if (d.status !== undefined && (d.authorized !== undefined || d.user_id !== undefined) && d.label === undefined) {
    return `检查结果：${cnStatus(d.status)}`
      + (d.authorized !== undefined ? (d.authorized ? ' · 已授权' : ' · 未授权') : '')
      + (d.username ? ` · @${d.username}` : '')
      + (d.user_id ? ` · id=${d.user_id}` : '')
      + (d.note || d.status_note ? `\n${d.note || d.status_note}` : '');
  }

  if (d.enabled !== undefined && d.hours !== undefined)
    return `自动清设备：${d.enabled ? '已开启' : '已关闭'} · 周期 ${d.hours} 小时 · 监控 ${d.watched} 个号`
      + ` · 待处理 ${d.due_now} 个 · 扫描间隔 ${Math.round((d.scan_interval_s || 0) / 60)} 分钟`
      + (d.next_at ? ` · 下次 ${fmt(d.next_at)}` : '');

  if (d.label !== undefined && d.status !== undefined) {
    const L = [`账号：${d.label}${d.phone ? ' ' + fmtPhone(d.phone) + '（' + countryOf(d.phone) + '）' : ''}`,
      `状态：${cnStatus(d.status)}${d.authorized ? ' · 已授权' : ' · 未授权'}`];
    if (d.username) L.push(`用户名：@${d.username}`);
    if (d.user_id) L.push(`Telegram ID：${d.user_id}`);
    if (d.premium) L.push('会员：Telegram Premium');
    if (d.spam_until) L.push(`限制解除时间：${fmt(d.spam_until)}`);
    if (d.proxy) L.push(`代理：${d.proxy}`);
    if (d.status_note) L.push(`备注：${d.status_note}`);
    if (d.last_check_at) L.push(`上次检查：${fmt(d.last_check_at)}`);
    if (d.login_at) L.push(`登录时间：${fmt(d.login_at)}`);
    if (d.adopted_at) L.push(`本机接管：${fmt(d.adopted_at)}`);
    if (d.last_kick_at) L.push(`上次清设备：${fmt(d.last_kick_at)}`);
    if ((d.tags || []).length) L.push(`标签：${d.tags.join('、')}`);
    return L.join('\n');
  }

  if (d.added !== undefined || d.updated !== undefined)
    return `导入完成：新增 ${(d.added || []).length} 个，更新 ${(d.updated || []).length} 个`
      + (d.skipped && d.skipped.length ? `，跳过 ${d.skipped.length} 个` : '')
      + (d.dry_run ? '（试运行，未真正写库）' : '');

  // doctor
  if (d.checks || d.report || (d.ok !== undefined && d.fixes !== undefined))
    return `一键体检：${d.ok ? '通过' : '有问题'}`
      + (Array.isArray(d.checks) ? `\n` + d.checks.slice(0, 20).map(c =>
        `${c.ok ? '✓' : '✗'} ${c.name || c.item || ''}${c.detail || c.message ? ' · ' + (c.detail || c.message) : ''}`).join('\n') : '')
      + (d.summary ? `\n${d.summary}` : '');

  // stats
  if (d.total !== undefined && d.active !== undefined && !d.items)
    return `统计：共 ${d.total} 个账号` + (d.active != null ? ` · 正常 ${d.active}` : '')
      + (d.unauthorized != null ? ` · 失效 ${d.unauthorized}` : '')
      + Object.keys(d).filter(k => !['total','active','unauthorized','ok'].includes(k)).slice(0, 8)
        .map(k => ` · ${k}=${d[k]}`).join('');

  // settings object
  if (d.values || (d.ok && d.saved))
    return d.saved ? `参数已保存：${Array.isArray(d.saved) ? d.saved.join('、') : d.saved}`
      : `当前参数：\n` + Object.entries(d.values || d).slice(0, 20).map(([k,v]) => `• ${k} = ${v}`).join('\n');

  // toolbox single
  if (d.done || d.errors || d.logged_out !== undefined || d.remaining_others !== undefined && d.before !== undefined) {
    const parts = [];
    if (d.done) parts.push('完成：' + (Array.isArray(d.done) ? d.done.join('；') : d.done));
    if (d.errors && d.errors.length) parts.push('问题：' + d.errors.join('；'));
    if (d.before != null) parts.push(`踢前授权 ${d.before}，剩余其它 ${d.remaining_others}`);
    if (d.logged_out !== undefined) parts.push(d.logged_out ? '已退出登录' : '退出未确认');
    if (parts.length) return parts.join('\n');
  }

  // ZIP / 文件任务
  if (d.output || d.out_dir || d.packs !== undefined || d.files !== undefined)
    return `ZIP 工具完成`
      + (d.output || d.out_dir ? `\n输出：${d.output || d.out_dir}` : '')
      + (d.packs != null ? `\n包数：${d.packs}` : '')
      + (d.files != null ? `\n文件：${d.files}` : '')
      + (d.message || d.note ? `\n${d.message || d.note}` : '');

  // 上传导入汇总
  if (d.filename && (d.succeeded !== undefined || d.tdata_dirs !== undefined))
    return `上传导入：${d.filename}`
      + (d.tdata_dirs != null ? ` · 发现 tdata ${d.tdata_dirs} 处` : '')
      + (d.succeeded != null ? ` · 成功账号 ${d.succeeded}` : '')
      + (Array.isArray(d.items) ? '\n' + humanize(d.items) : '');

  // 简单 ok
  if (d.ok !== undefined && Object.keys(d).length <= 5)
    return d.ok ? ('操作成功' + (d.message || d.note ? '：' + (d.message || d.note) : ''))
      : `操作失败${d.error ? '：' + d.error : d.detail ? '：' + d.detail : ''}`;

  // 最后兜底：把主要字段译成可读行，而不是直接放弃
  try {
    const skip = new Set(['trace', 'stack']);
    const lines = Object.keys(d).filter(k => !skip.has(k)).slice(0, 25).map(k => {
      let v = d[k];
      if (v === null || v === undefined) v = '—';
      else if (typeof v === 'object') v = JSON.stringify(v).slice(0, 120);
      else v = String(v).slice(0, 200);
      const kn = ACTION_CN[k] || k;
      return `• ${kn}：${v}`;
    });
    if (lines.length) return `结果摘要：\n` + lines.join('\n');
  } catch (_) {}
  return null;
}

function out(data, pin) {
  if (logPinned && !pin) return;                 // 已锁定：忽略自动刷新写入
  let human, raw;
  if (typeof data === 'string') { human = data; raw = '—'; }
  else {
    raw = JSON.stringify(data, null, 2);
    try { human = humanize(data); } catch (e) { human = null; }
    if (!human) human = '已记录结果。结构化字段见右侧原始数据；若经常出现可再补充中文模板。';
  }
  $('#log').textContent = human;
  $('#logRaw').textContent = raw;
  if (pin) { logPinned = true; $('#logPin').style.display = ''; }
}
function unpinLog() { logPinned = false; $('#logPin').style.display = 'none'; loadLogs(); }

/* ---------- 手机号 / 国家识别 ---------- */
const DIAL = {
  '1':'美国/加拿大 \ud83c\uddfa\ud83c\uddf8','7':'俄罗斯/哈萨克 \ud83c\uddf7\ud83c\uddfa','20':'埃及 \ud83c\uddea\ud83c\uddec','27':'南非 \ud83c\uddff\ud83c\udde6',
  '30':'希腊 \ud83c\uddec\ud83c\uddf7','31':'荷兰 \ud83c\uddf3\ud83c\uddf1','32':'比利时 \ud83c\udde7\ud83c\uddea','33':'法国 \ud83c\uddeb\ud83c\uddf7','34':'西班牙 \ud83c\uddea\ud83c\uddf8',
  '36':'匈牙利 \ud83c\udded\ud83c\uddfa','39':'意大利 \ud83c\uddee\ud83c\uddf9','40':'罗马尼亚 \ud83c\uddf7\ud83c\uddf4','41':'瑞士 \ud83c\udde8\ud83c\udded','43':'奥地利 \ud83c\udde6\ud83c\uddf9',
  '44':'英国 \ud83c\uddec\ud83c\udde7','45':'丹麦 \ud83c\udde9\ud83c\uddf0','46':'瑞典 \ud83c\uddf8\ud83c\uddea','47':'挪威 \ud83c\uddf3\ud83c\uddf4','48':'波兰 \ud83c\uddf5\ud83c\uddf1',
  '49':'德国 \ud83c\udde9\ud83c\uddea','51':'秘鲁 \ud83c\uddf5\ud83c\uddea','52':'墨西哥 \ud83c\uddf2\ud83c\uddfd','53':'古巴 \ud83c\udde8\ud83c\uddfa','54':'阿根廷 \ud83c\udde6\ud83c\uddf7',
  '55':'巴西 \ud83c\udde7\ud83c\uddf7','56':'智利 \ud83c\udde8\ud83c\uddf1','57':'哥伦比亚 \ud83c\udde8\ud83c\uddf4','58':'委内瑞拉 \ud83c\uddfb\ud83c\uddea','60':'马来西亚 \ud83c\uddf2\ud83c\uddfe',
  '61':'澳大利亚 \ud83c\udde6\ud83c\uddfa','62':'印度尼西亚 \ud83c\uddee\ud83c\udde9','63':'菲律宾 \ud83c\uddf5\ud83c\udded','64':'新西兰 \ud83c\uddf3\ud83c\uddff','65':'新加坡 \ud83c\uddf8\ud83c\uddec',
  '66':'泰国 \ud83c\uddf9\ud83c\udded','81':'日本 \ud83c\uddef\ud83c\uddf5','82':'韩国 \ud83c\uddf0\ud83c\uddf7','84':'越南 \ud83c\uddfb\ud83c\uddf3','86':'中国 \ud83c\udde8\ud83c\uddf3',
  '90':'土耳其 \ud83c\uddf9\ud83c\uddf7','91':'印度 \ud83c\uddee\ud83c\uddf3','92':'巴基斯坦 \ud83c\uddf5\ud83c\uddf0','93':'阿富汗 \ud83c\udde6\ud83c\uddeb','94':'斯里兰卡 \ud83c\uddf1\ud83c\uddf0',
  '95':'缅甸 \ud83c\uddf2\ud83c\uddf2','98':'伊朗 \ud83c\uddee\ud83c\uddf7','212':'摩洛哥 \ud83c\uddf2\ud83c\udde6','213':'阿尔及利亚 \ud83c\udde9\ud83c\uddff','216':'突尼斯 \ud83c\uddf9\ud83c\uddf3',
  '218':'利比亚 \ud83c\uddf1\ud83c\uddfe','220':'冈比亚 \ud83c\uddec\ud83c\uddf2','221':'塞内加尔 \ud83c\uddf8\ud83c\uddf3','233':'加纳 \ud83c\uddec\ud83c\udded','234':'尼日利亚 \ud83c\uddf3\ud83c\uddec',
  '237':'喀麦隆 \ud83c\udde8\ud83c\uddf2','249':'苏丹 \ud83c\uddf8\ud83c\udde9','251':'埃塞俄比亚 \ud83c\uddea\ud83c\uddf9','254':'肯尼亚 \ud83c\uddf0\ud83c\uddea','255':'坦桑尼亚 \ud83c\uddf9\ud83c\uddff',
  '256':'乌干达 \ud83c\uddfa\ud83c\uddec','260':'赞比亚 \ud83c\uddff\ud83c\uddf2','263':'津巴布韦 \ud83c\uddff\ud83c\uddfc','351':'葡萄牙 \ud83c\uddf5\ud83c\uddf9','352':'卢森堡 \ud83c\uddf1\ud83c\uddfa',
  '353':'爱尔兰 \ud83c\uddee\ud83c\uddea','354':'冰岛 \ud83c\uddee\ud83c\uddf8','355':'阿尔巴尼亚 \ud83c\udde6\ud83c\uddf1','358':'芬兰 \ud83c\uddeb\ud83c\uddee','359':'保加利亚 \ud83c\udde7\ud83c\uddec',
  '370':'立陶宛 \ud83c\uddf1\ud83c\uddf9','371':'拉脱维亚 \ud83c\uddf1\ud83c\uddfb','372':'爱沙尼亚 \ud83c\uddea\ud83c\uddea','373':'摩尔多瓦 \ud83c\uddf2\ud83c\udde9','374':'亚美尼亚 \ud83c\udde6\ud83c\uddf2',
  '375':'白俄罗斯 \ud83c\udde7\ud83c\uddfe','380':'乌克兰 \ud83c\uddfa\ud83c\udde6','381':'塞尔维亚 \ud83c\uddf7\ud83c\uddf8','385':'克罗地亚 \ud83c\udded\ud83c\uddf7','386':'斯洛文尼亚 \ud83c\uddf8\ud83c\uddee',
  '420':'捷克 \ud83c\udde8\ud83c\uddff','421':'斯洛伐克 \ud83c\uddf8\ud83c\uddf0','593':'厄瓜多尔 \ud83c\uddea\ud83c\udde8','595':'巴拉圭 \ud83c\uddf5\ud83c\uddfe','598':'乌拉圭 \ud83c\uddfa\ud83c\uddfe',
  '852':'中国香港 \ud83c\udded\ud83c\uddf0','853':'中国澳门 \ud83c\uddf2\ud83c\uddf4','855':'柬埔寨 \ud83c\uddf0\ud83c\udded','856':'老挝 \ud83c\uddf1\ud83c\udde6','880':'孟加拉国 \ud83c\udde7\ud83c\udde9',
  '886':'中国台湾 \ud83c\uddf9\ud83c\uddfc','960':'马尔代夫 \ud83c\uddf2\ud83c\uddfb','961':'黎巴嫩 \ud83c\uddf1\ud83c\udde7','962':'约旦 \ud83c\uddef\ud83c\uddf4','963':'叙利亚 \ud83c\uddf8\ud83c\uddfe',
  '964':'伊拉克 \ud83c\uddee\ud83c\uddf6','965':'科威特 \ud83c\uddf0\ud83c\uddfc','966':'沙特 \ud83c\uddf8\ud83c\udde6','967':'也门 \ud83c\uddfe\ud83c\uddea','968':'阿曼 \ud83c\uddf4\ud83c\uddf2',
  '971':'阿联酋 \ud83c\udde6\ud83c\uddea','972':'以色列 \ud83c\uddee\ud83c\uddf1','973':'巴林 \ud83c\udde7\ud83c\udded','974':'卡塔尔 \ud83c\uddf6\ud83c\udde6','975':'不丹 \ud83c\udde7\ud83c\uddf9',
  '976':'蒙古 \ud83c\uddf2\ud83c\uddf3','977':'尼泊尔 \ud83c\uddf3\ud83c\uddf5','992':'塔吉克斯坦 \ud83c\uddf9\ud83c\uddef','993':'土库曼 \ud83c\uddf9\ud83c\uddf2','994':'阿塞拜疆 \ud83c\udde6\ud83c\uddff',
  '995':'格鲁吉亚 \ud83c\uddec\ud83c\uddea','996':'吉尔吉斯斯坦 \ud83c\uddf0\ud83c\uddec','998':'乌兹别克斯坦 \ud83c\uddfa\ud83c\uddff'};
/* 长前缀优先：先试3 位，再 2 位，最后 1 位 */
function dialOf(phone) {
  const d = String(phone || '').replace(/[^0-9]/g, '');
  if (!d) return null;
  for (const n of [3, 2, 1]) {
    const code = d.slice(0, n);
    if (DIAL[code]) return {code, rest: d.slice(n), country: DIAL[code]};
  }
  return null;
}
/* 显示为 +91 7299333330 */
function fmtPhone(phone) {
  if (!phone) return '—';
  const hit = dialOf(phone);
  const d = String(phone).replace(/[^0-9]/g, '');
  return hit ? `+${hit.code} ${hit.rest}` : '+' + d;
}
function countryOf(phone) {
  const hit = dialOf(phone);
  return hit ? hit.country : '未识别';
}
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fmt(ts) { return ts ? new Date(ts * 1000).toLocaleString('zh-CN', {hour12:false}) : '—'; }
/* 服务端与本机的时钟差（毫秒）。本机时间不准时，相对时间会算出很离谱的值，所以统一以服务端为准 */
let skewMs = 0;
const nowMs = () => Date.now() + skewMs;
function syncClock(serverNow) {
  if (!serverNow) return;
  const d = serverNow * 1000 - Date.now();
  skewMs = Math.abs(d) > 5000 ? d : 0;   // 5 秒以内视为正常抖动，不做修正
}
function rel(ts) {
  if (!ts || !isFinite(ts)) return '—';
  const d = ts * 1000 - nowMs(), m = Math.round(Math.abs(d) / 60000);
  const s = m < 60 ? m + ' 分钟' : (m < 1440 ? (m / 60).toFixed(1) + ' 小时' : (m / 1440).toFixed(1) + ' 天');
  return d >= 0 ? s + '后' : s + '前';
}
/* 抽屉里那行说明：这个号什么时候会被自动清一次其它设备 */
/* 列表里的紧凑显示：还有多久自动清一次 */
/* 与后端 autokick.plan 同一套规则。优先用后端下发的 a.kick，老后端才走本地兜底 */
const KICK_SKIP_STATUS = new Set(['banned', 'unauthorized', 'frozen', 'spam_block_perm']);
const KICK_STATE_CN = {disabled:'—', no_session:'—', off:'关', skipped:'不参与', no_base:'待接管'};
const KICK_BASE_CN = {kick_retry_at:'上次未踢成功的重试', last_kick_at:'上次清设备',
                      adopted_at:'本机接管时间', login_at:'会话创建时间', created_at:'建号时间'};
function kickPlan(a) {
  if (a.kick && a.kick.state) return a.kick;
  const hrs = (a.auto_kick_hours != null && a.auto_kick_hours > 0)
    ? Number(a.auto_kick_hours) : (kick.hours || 24);
  const loop = (a.auto_kick_loop == null) ? true : !!a.auto_kick_loop;
  if (!kick.enabled && !(a.auto_kick_hours > 0))
    return {state:'disabled', reason:'全局未开启自动清设备', hours: hrs, loop: loop};
  if (!a.authorized) return {state:'no_session', reason:'未授权，暂不参与', hours: hrs, loop: loop};
  if (!a.auto_kick) return {state:'off', reason:'该账号已单独关闭', hours: hrs, loop: loop};
  if (KICK_SKIP_STATUS.has(a.status)) return {state:'skipped', reason:`状态 ${a.status}，不再尝试`, hours: hrs, loop: loop};
  if (!loop && a.last_kick_at && !a.kick_retry_at)
    return {state:'done', reason:'单次清设备已完成（未开启循环）', hours: hrs, loop: false};
  if (a.kick_retry_at) {
    return {state: a.kick_retry_at * 1000 <= nowMs() ? 'due' : 'waiting',
            due_at: a.kick_retry_at, base_from:'kick_retry_at', retrying: true,
            reason:'上次未踢成功，等待重试', hours: hrs, loop: loop};
  }
  const from = a.last_kick_at ? 'last_kick_at' : (a.adopted_at ? 'adopted_at'
             : (a.login_at ? 'login_at' : (a.created_at ? 'created_at' : null)));
  if (!from) return {state:'no_base', reason:'尚无接管时间', hours: hrs, loop: loop};
  const due = a[from] + hrs * 3600;
  return {state: due * 1000 <= nowMs() ? 'due' : 'waiting', due_at: due,
          base_from: from, retrying: false, hours: hrs, loop: loop};
}
function kickCell(a) {
  const p = kickPlan(a);
  if (p.state === 'waiting') return (p.retrying ? '重试 ' : '') + rel(p.due_at)
    + (p.hours ? (' ·' + p.hours + 'h') : '') + (p.loop === false ? ' ·单次' : '');
  if (p.state === 'due') return p.retrying ? '待重试' : '已到期 · 待执行';
  if (p.state === 'done') return '已完成·单次';
  return KICK_STATE_CN[p.state] || '—';
}
function kickHint(a) {
  const p = kickPlan(a);
  const last = a.last_kick_at ? '上次 ' + fmt(a.last_kick_at) + '，' : '';
  const scanMin = Math.max(1, Math.round((kick.scan_interval_s || 600) / 60));
  const from = p.base_from ? '，按' + (KICK_BASE_CN[p.base_from] || p.base_from) + '计时' : '';
  if (p.retrying)
    return last + '上次清设备校验未通过，将于 ' + fmt(p.due_at) + '（' + rel(p.due_at) + '）重试，详情看日志';
  if (p.state === 'waiting') return last + '下次 ' + fmt(p.due_at) + '（' + rel(p.due_at) + '）' + from;
  if (p.state === 'due')
    return last + '已到期（应于 ' + fmt(p.due_at) + '），后台每 ' + scanMin + ' 分钟扫一轮，稍后自动执行';
  return last + (p.reason || KICK_STATE_CN[p.state] || '不参与');
}
function kickHintLegacy(a) {
  if (!a.auto_kick) return '已关闭';
  if (!a.authorized) return '未登录，暂不参与';
  const hrs = (kick.hours || 24) * 3600;
  const base = a.last_kick_at || a.login_at || a.created_at;
  if (!base) return '登录后开始计时';
  const at = base + hrs;
  return (a.last_kick_at ? '上次 ' + fmt(a.last_kick_at) + '，' : '') + '下次 ' + fmt(at) + '（' + rel(at) + '）';
}

/* ---------- 弹层 ---------- */

/* ---------- 站内对话框（替代 prompt / confirm） ---------- */
let _uiResolver = null;
let _uiMode = 'confirm'; // confirm | prompt | progress

function uiClose() {
  const m = $('#mUi');
  if (m) m.classList.remove('on');
  // 仅当没有其它 modal 时关遮罩
  const any = [...document.querySelectorAll('.modal.on')].length;
  if (!any) $('#bd').classList.remove('on');
}

function uiResolve(ok) {
  if (_uiMode === 'progress') return; // 进度中不可点确定取消走 resolve
  const r = _uiResolver;
  _uiResolver = null;
  uiClose();
  if (!r) return;
  if (_uiMode === 'prompt') {
    if (!ok) { r(null); return; }
    r(($('#uiInput') && $('#uiInput').value) || '');
  } else {
    r(!!ok);
  }
}

function uiOpenShell(title, msg) {
  closeAll(); // 关掉其它弹窗，避免叠两层逻辑混乱
  $('#uiTitle').textContent = title || '提示';
  $('#uiMsg').textContent = msg || '';
  $('#uiInputWrap').style.display = 'none';
  $('#uiProgWrap').style.display = 'none';
  $('#uiActions').style.display = '';
  $('#uiCancel').style.display = '';
  $('#uiOk').style.display = '';
  $('#uiOk').className = 'primary';
  $('#uiOk').textContent = '确定';
  $('#uiCancel').textContent = '取消';
  $('#mUi').classList.add('on');
  $('#bd').classList.add('on');
}

/** @returns {Promise<boolean>} */
function uiConfirm(opts) {
  opts = opts || {};
  _uiMode = 'confirm';
  uiOpenShell(opts.title || '请确认', opts.message || '');
  if (opts.danger) {
    $('#uiOk').className = 'danger-solid';
    $('#uiOk').textContent = opts.okText || '确定执行';
  } else {
    $('#uiOk').textContent = opts.okText || '确定';
  }
  if (opts.cancelText) $('#uiCancel').textContent = opts.cancelText;
  return new Promise(resolve => { _uiResolver = resolve; });
}

/**
 * 危险操作：需输入确认词（默认「确认」）
 * @returns {Promise<boolean>}
 */
async function uiConfirmDanger(opts) {
  opts = opts || {};
  const word = opts.word || '确认';
  const msg = (opts.message || '') + '\n\n请在下方输入「' + word + '」后确定：';
  const typed = await uiPrompt({
    title: opts.title || '危险操作',
    message: msg,
    placeholder: word,
    okText: opts.okText || '确定执行',
    danger: true,
  });
  if (typed === null) return false;
  if (String(typed).trim() !== word) {
    toast('未输入正确的确认词，已取消', 'err');
    return false;
  }
  return true;
}

/** @returns {Promise<string|null>} null=取消 */
function uiPrompt(opts) {
  opts = opts || {};
  _uiMode = 'prompt';
  uiOpenShell(opts.title || '请输入', opts.message || '');
  $('#uiInputWrap').style.display = '';
  $('#uiInputLabel').textContent = opts.label || '内容';
  const inp = $('#uiInput');
  inp.type = opts.password ? 'password' : 'text';
  inp.value = opts.value != null ? String(opts.value) : '';
  inp.placeholder = opts.placeholder || '';
  if (opts.danger) {
    $('#uiOk').className = 'danger-solid';
  }
  $('#uiOk').textContent = opts.okText || '确定';
  setTimeout(() => { try { inp.focus(); inp.select(); } catch (_) {} }, 30);
  inp.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); uiResolve(true); }
    if (e.key === 'Escape') { e.preventDefault(); uiResolve(false); }
  };
  return new Promise(resolve => { _uiResolver = resolve; });
}

/** 显示/更新/关闭进度。传 null 关闭。 */
function uiProgress(opts) {
  if (!opts) {
    if (_uiMode === 'progress') {
      _uiMode = 'confirm';
      uiClose();
    }
    return;
  }
  _uiMode = 'progress';
  _uiResolver = null;
  if (!$('#mUi').classList.contains('on')) {
    closeAll();
    $('#mUi').classList.add('on');
    $('#bd').classList.add('on');
  }
  $('#uiTitle').textContent = opts.title || '进行中';
  $('#uiMsg').textContent = opts.message || '请稍候，不要关闭页面…';
  $('#uiInputWrap').style.display = 'none';
  $('#uiProgWrap').style.display = '';
  $('#uiActions').style.display = 'none';
  const cur = opts.current || 0;
  const total = opts.total || 1;
  const pct = Math.max(0, Math.min(100, Math.round(cur / total * 100)));
  $('#uiProgBar').style.width = pct + '%';
  $('#uiProgText').textContent = (opts.text != null)
    ? opts.text
    : (`第 ${cur} / ${total} 个` + (opts.detail ? ' · ' + opts.detail : ''));
}

/** 从参数面板缓存读取配置（未加载则用默认） */
function cfgInt(key, defVal) {
  const raw = (typeof SET_ORIG !== 'undefined' && SET_ORIG && SET_ORIG[key] != null)
    ? SET_ORIG[key] : '';
  const n = parseInt(String(raw).trim(), 10);
  return Number.isFinite(n) ? n : defVal;
}
function batchConc(defVal) {
  let n = cfgInt('TAM_BATCH_CONCURRENCY', defVal != null ? defVal : 3);
  if (n < 1) n = 1;
  if (n > 32) n = 32;
  return n;
}
function regenConcDefault() {
  let n = cfgInt('TAM_REGEN_CONCURRENCY', 1);
  if (n < 1) n = 1;
  if (n > 8) n = 8;
  return n;
}
function opTimeoutMs() {
  let s = cfgInt('TAM_UI_OP_TIMEOUT', 120);
  if (s < 15) s = 15;
  if (s > 600) s = 600;
  return s * 1000;
}
/** Promise 超时：超时抛错，供批量自动跳过 */
function withTimeout(promise, ms, label) {
  let timer = null;
  const sec = Math.round(ms / 1000);
  const timeout = new Promise(function (_, reject) {
    timer = setTimeout(function () {
      reject(new Error('超时自动跳过（' + sec + 's）' + (label ? '：' + label : '')));
    }, ms);
  });
  return Promise.race([Promise.resolve(promise), timeout]).finally(function () {
    if (timer) clearTimeout(timer);
  });
}

/** 账号显示名 */
function accLabel(id) {
  const a = accounts.find(x => String(x.id) === String(id));
  return (a && (a.label || a.phone)) ? (a.label || a.phone) : ('#' + id);
}

/** 串行处理 id 列表，带进度；返回 {total,ok,failed,results} */
async function runSerial(ids, worker, title) {
  return runPool(ids, worker, title, 1);
}

/**
 * 并行池处理 id 列表，带进度条。
 * concurrency=1 即串行。返回 {total,ok,failed,results}
 */
async function runPool(ids, worker, title, concurrency) {
  const list = (ids || []).map(Number).filter(n => Number.isFinite(n));
  const total = list.length;
  const conc = Math.max(1, Math.min(Number(concurrency) || 1, 8));
  const results = new Array(total);
  let done = 0, ok = 0, fail = 0;
  let cursor = 0;
  const title0 = title || '批量处理';

  function paint(currentLabel, extra) {
    const cur = Math.min(done + (currentLabel ? 1 : 0), total) || (total ? 1 : 0);
    uiProgress({
      title: title0 + (conc > 1 ? (' ×' + conc) : ''),
      current: done,
      total: total || 1,
      text: (total ? (done + ' / ' + total) : '…')
        + ' · 成功 ' + ok + ' · 失败 ' + fail
        + (currentLabel ? (' · ' + currentLabel) : '')
        + (extra ? (' · ' + extra) : ''),
      detail: currentLabel || '',
    });
  }

  if (!total) {
    uiProgress(null);
    return {total: 0, ok: 0, failed: 0, results: []};
  }
  paint('准备中…');

  async function one(idx, id) {
    const lab = accLabel(id);
    const tmo = opTimeoutMs();
    paint(lab + ' 进行中（限' + Math.round(tmo / 1000) + 's）');
    try {
      const r = await withTimeout(worker(id, idx), tmo, lab);
      const row = (r && typeof r === 'object')
        ? Object.assign({ok: true, id: id}, r)
        : {ok: true, id: id, result: r};
      if (row.ok === false) {
        fail++;
        results[idx] = row;
      } else {
        ok++;
        results[idx] = row;
      }
    } catch (e) {
      fail++;
      results[idx] = {ok: false, id: id, error: String(e.message || e), skipped: /超时/.test(String(e.message || e))};
    } finally {
      done++;
      paint(lab + ' 完成');
    }
  }

  async function workerLoop() {
    while (true) {
      const idx = cursor++;
      if (idx >= total) return;
      await one(idx, list[idx]);
    }
  }

  const n = Math.min(conc, total);
  await Promise.all(Array.from({length: n}, () => workerLoop()));
  uiProgress(null);
  return {
    total: total,
    ok: ok,
    failed: fail,
    results: results.filter(Boolean),
  };
}

/** 长时间单次请求：不确定进度时也给出明确等待条；可整体超时 */

/** 流式导入：读 NDJSON，uiProgress 显示真实 当前/总数。返回最终 done 对象。 */
async function fetchImportStream(url, opts, title) {
  opts = opts || {};
  const headers = Object.assign({}, opts.headers || {});
  headers['Accept'] = 'application/x-ndjson';
  if (!DEMO) {
    const tok = ($('#token') && $('#token').value) || localStorage.getItem('tam_token') || '';
    if (tok && !headers['Authorization']) headers['Authorization'] = 'Bearer ' + tok;
  }
  const u = url + (url.indexOf('?') >= 0 ? '&' : '?') + 'stream=1';
  uiProgress({title: title || '导入中', current: 0, total: 1, text: '连接中…'});
  let final = null;
  let okN = 0, failN = 0, total = 1;
  try {
    if (DEMO) {
      await new Promise(r => setTimeout(r, 350));
      final = {event:'done', total:1, succeeded:1, failed:0, items:[{ok:true,label:'demo'}]};
      uiProgress({title: title || '导入中', current:1, total:1, text:'完成 1/1'});
      return final;
    }
    const res = await fetch(u, Object.assign({}, opts, {headers: headers}));
    if (!res.ok) {
      const raw = await res.text();
      let msg = raw;
      try { const j = JSON.parse(raw); msg = j.detail || j.error || raw; } catch (_) {}
      throw new Error(String(msg).slice(0, 400));
    }
    const ct = (res.headers.get('content-type') || '');
    if (ct.indexOf('ndjson') < 0 && ct.indexOf('json') >= 0) {
      const data = await res.json();
      const items = data.items || [];
      const t = data.total || items.length || 1;
      uiProgress({title: title || '导入中', current: t, total: t,
        text: '完成 ' + (data.succeeded || 0) + '/' + t});
      return Object.assign({event:'done'}, data);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (_) { continue; }
        if (ev.event === 'start') {
          total = ev.total || 1;
          uiProgress({title: title || '导入中', current: 0, total: total, text: '开始 0/' + total});
        } else if (ev.event === 'item') {
          total = ev.total || total;
          if (ev.ok) okN++; else failN++;
          const lab = (ev.item && (ev.item.label || ev.item.file || ev.item.user_id)) || '';
          uiProgress({
            title: title || '导入中',
            current: ev.index || (okN + failN),
            total: total,
            text: (ev.ok ? '✓' : '✗') + ' ' + (ev.index || (okN + failN)) + '/' + total
              + (lab ? ' · ' + lab : '')
              + '（成功 ' + okN + ' / 失败 ' + failN + '）',
          });
        } else if (ev.event === 'done') {
          final = ev;
          total = ev.total || total;
          uiProgress({
            title: title || '导入中',
            current: total,
            total: total || 1,
            text: '完成：成功 ' + (ev.succeeded != null ? ev.succeeded : okN)
              + ' / 失败 ' + (ev.failed != null ? ev.failed : failN),
          });
        } else if (ev.event === 'error') {
          throw new Error(ev.error || '导入失败');
        }
      }
    }
    if (!final) final = {event:'done', total: total, succeeded: okN, failed: failN, items: []};
    return final;
  } finally {
    await new Promise(r => setTimeout(r, 280));
    uiProgress(null);
  }
}

async function withWaitProgress(title, message, fn, timeoutMs) {
  const tmo = timeoutMs != null ? timeoutMs : Math.max(opTimeoutMs() * 3, 180000);
  uiProgress({
    title: title || '处理中',
    current: 0,
    total: 1,
    text: (message || '请求进行中，请稍候…') + '（限 ' + Math.round(tmo / 1000) + 's）',
  });
  try {
    return await withTimeout(fn(), tmo, title || '整批任务');
  } finally {
    uiProgress(null);
  }
}

function exportTwofaHint(idOrIds) {
  const ids = Array.isArray(idOrIds) ? idOrIds : [idOrIds];
  let saved = 0, unsavedHas = 0;
  ids.forEach(id => {
    const a = accounts.find(x => x.id === id);
    if (!a) return;
    if (a.has_twofa_saved || a.twofa) saved++;
    else if (a.has_2fa === 1) unsavedHas++;
  });
  if (saved && !unsavedHas) return ' · 含已保存二验（2fa.txt）';
  if (saved && unsavedHas) return ` · ${saved} 个含二验文件，${unsavedHas} 个有二验但未保存密码`;
  if (!saved && unsavedHas) return ' · 有二验但未保存密码，包内无真实 2fa.txt';
  return '';
}


function openModal(id) {
  closeAll();
  $('#' + id).classList.add('on');
  $('#bd').classList.add('on');
  document.body.classList.add('modal-open');
}

function openHelp() {
  openModal('mHelp');
}
function helpJump(a) {
  try {
    document.querySelectorAll('#mHelp .help-nav a').forEach(function (x) {
      x.classList.toggle('on', x === a);
    });
    const id = (a.getAttribute('href') || '').replace(/^#/, '');
    const el = id && document.getElementById(id);
    const main = document.querySelector('#mHelp .help-main');
    if (el && main) {
      main.scrollTo({top: Math.max(0, el.offsetTop - 8), behavior: 'smooth'});
    }
  } catch (_) {}
  return false;
}

function closeAll() {
  document.querySelectorAll('.modal').forEach(m => m.classList.remove('on'));
  const dr = $('#drawer');
  if (dr) dr.classList.remove('on');
  $('#bd').classList.remove('on');
  document.body.classList.remove('modal-open');
  if (_uiResolver && _uiMode !== 'progress') {
    const r = _uiResolver; _uiResolver = null; r(_uiMode === 'prompt' ? null : false);
  }
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeAll(); });

/* ---------- 数据加载 ---------- */
async function refresh() {
  try {
    const [s, a] = await Promise.all([api('/api/stats'), api('/api/accounts')]);
    stats = s; accounts = a;
    if (a.length && a[0].server_now) syncClock(a[0].server_now);
    render(); loadLogs(); loadKick(); loadTasks(); loadLeads();
    loadSettings(); loadOps(); renderZt();
  } catch (e) { toast('加载失败：' + e.message, 'err'); out({error: String(e)}, true); }
}
async function loadKick() {
  try {
    kick = await api('/api/autokick');
    syncClock(kick.server_now);
    if (!kick.enabled) { $('#kickbar').style.display = 'none'; return; }
    $('#kickbar').style.display = 'flex';
    $('#kickText').textContent =
      '自动清设备：本机接管满 ' + kick.hours + ' 小时踢出其它设备（踢完回查校验） · 监控 ' + kick.watched + ' 个号'
      + (kick.due_now ? ' · ' + kick.due_now + ' 个待处理' : '')
      + (kick.retrying ? ' · ' + kick.retrying + ' 个踢失败待重试' : '')
      + (kick.retry_after_text ? ' · 失败后 ' + kick.retry_after_text + ' 重试' : '')
      + (kick.next_at ? ' · 下次 ' + fmt(kick.next_at) + '（' + rel(kick.next_at) + '）' : '');
    // 输入框只在用户没在编辑时回填，避免把手里正在敲的字冲掉
    const ri = $('#kickRetry');
    if (ri && document.activeElement !== ri) ri.value = kick.retry_after_text ? retryToInput(kick.retry_after_s) : '';
    if (kick.due_now && kick.max_overdue_s > 3 * (kick.scan_interval_s || 600))
      $('#kickText').textContent += ' · ⚠ 最久一个已逾期 ' + (kick.max_overdue_s / 86400).toFixed(1)
        + ' 天未执行，请看日志排查';
    render();   // 列表的“清设备”列依赖 kick.hours，拿到配置后重画一次
  } catch (e) { $('#kickbar').style.display = 'none'; }
}
/* 把秒数变回紧凑的输入写法：90 -> 90s，900 -> 15m，3600 -> 1h */
function retryToInput(sec) {
  if (!isFinite(sec) || sec <= 0) return '';
  if (sec % 3600 === 0) return (sec / 3600) + 'h';
  if (sec % 60 === 0) return (sec / 60) + 'm';
  return sec + 's';
}
const saveKickRetry = () => guard(async () => {
  const v = ($('#kickRetry').value || '').trim();
  const r = await api('/api/autokick/retry', {method:'POST', body: JSON.stringify({value: v})});
  loadKick(); return r;
}, '重试间隔已保存');
const kickNow = () => guard(async () => {
  const r = await api('/api/autokick/run', {method:'POST'});
  loadKick(); return r;
}, '已扫描一轮');
async function loadLogs() {
  if (logPinned) return;
  try { out(await api('/api/logs?limit=20')); } catch (e) { out({error: String(e)}); }
}
/* 把 tdata 诊断报告渲染成人话 */
function renderTdataReport(rep) {
  const lines = [`目录：${rep.path}`];
  (rep.steps || []).forEach(s => lines.push(`  ${s.ok ? '✓' : '✗'} ${s.name}${s.detail ? '：' + s.detail : ''}`));
  (rep.accounts || []).forEach(a => lines.push(`  → 账号 user_id=${a.user_id} 主DC=${a.main_dc} 已授权DC=[${(a.dcs||[]).join(', ')}]`));
  if (rep.error) lines.push(`  ✗ 失败：${rep.error}`);
  return lines.join('\n');
}
async function probeMode() {
  try {
    const t = await api('/api/tools');
    readonly = !!t.readonly;
    $('#mode').textContent = readonly ? '只读模式' : (t.dry_run ? '干跑模式' : '完整权限');
    $('#mode').className = 'pill' + (readonly || t.dry_run ? ' ro' : '');
    document.querySelectorAll('.write').forEach(b => b.disabled = readonly);
  } catch (e) {
    $('#mode').textContent = String(e.message || '').indexOf('401') >= 0 ? '令牌无效' : '未连接';
    $('#mode').className = 'pill off';
  }
}

/* ---------- 布局引擎（二维矩阵 + AABB 碰撞 + 下推 + 向上收紧） ---------- */
const STAT_CN = {total: '账号总数', active: '已登录', new: '新建', unauthorized: '未授权',
  restricted: '受限', banned: '封禁', frozen: '冻结', error: '错误',
  spam_block: '限制中', spam_block_perm: '永久限制', flood_wait: '限流等待'};
const FIXED = {
  kickbar: {name: '自动清设备提示条'},
  accounts: {name: '账号列表'},
  tasks: {name: '任务中心'},
  leads: {name: '线索库'},
  logText: {name: '操作日志 · 中文摘要'},
  logJson: {name: '操作日志 · 原始 JSON'},
  toolbox: {name: '工具箱 · 批量操作'},
  ziptools: {name: 'ZIP 工具 · 拆包/合并/注册时间/转 API'},
};
const GAP = 12;          // 网格间距
const RH = 40;           // 行高
const layoutKey = 'tam.layout';
const BPS = [{n: 'lg', min: 1180, cols: 12}, {n: 'md', min: 760, cols: 8}, {n: 'sm', min: 0, cols: 4}];
const BP_CN = {lg: '宽屏', md: '中屏', sm: '窄屏'};

let LAY = {v: 3, bp: {}};
let BP = 'lg';
let editing = false;
let drag = null;
let rsz = null;
let rafPending = false;

function board() { return $('#board'); }
function bpFor(w) { return (BPS.find(b => w >= b.min) || BPS[BPS.length - 1]).n; }
function cols() { return (BPS.find(b => b.n === BP) || BPS[0]).cols; }
function page() {
  if (!LAY.bp[BP]) LAY.bp[BP] = {items: {}, hidden: []};
  const p = LAY.bp[BP];
  if (!p.items) p.items = {};
  if (!Array.isArray(p.hidden)) p.hidden = [];
  if (p.auto === undefined) p.auto = true;
  return p;
}
function colW() {
  const b = board(); if (!b) return 100;
  return (b.clientWidth - GAP * (cols() - 1)) / cols();
}
function wname(k) {
  if (k.indexOf('stat:') === 0) { const t = k.slice(5); return '统计 · ' + (STAT_CN[t] || t); }
  return (FIXED[k] || {}).name || k;
}
function minSize(k) { return k.indexOf('stat:') === 0 ? {w: 1, h: 2} : {w: 2, h: 3}; }
function defSize(k) {
  const n = cols();
  if (k.indexOf('stat:') === 0) return {w: n >= 12 ? 2 : (n >= 8 ? 2 : 2), h: 2};
  if (k === 'kickbar') return {w: n, h: 2};
  if (k === 'settings') return {w: n, h: 10};
  if (k === 'toolbox') return {w: n, h: 11};
  if (k === 'ziptools') return {w: n, h: 10};
  if (k === 'accounts') return {w: n, h: 12};
  if (k === 'tasks') return {w: n, h: 9};
  if (k === 'leads') return {w: Math.max(2, Math.round(n / 2)), h: 8};
  if (k === 'logText' || k === 'logJson') return {w: Math.max(2, Math.round(n / 2)), h: 9};
  return {w: n, h: 6};
}
function domKeys() { return [...board().children].map(n => n.dataset && n.dataset.widget).filter(Boolean); }
function visibleKeys() { return domKeys().filter(k => page().hidden.indexOf(k) < 0); }
function items() { return visibleKeys().map(k => itemOf(k)); }

function itemOf(k) {
  const p = page();
  if (!p.items[k]) {
    const d = defSize(k);
    p.items[k] = {k, x: 0, y: bottomY(), w: d.w, h: d.h};
    packItem(p.items[k]);
  }
  const it = p.items[k];
  it.k = k;
  it.w = Math.max(1, Math.min(cols(), it.w | 0 || 1));
  it.h = Math.max(1, it.h | 0 || 1);
  it.x = Math.max(0, Math.min(cols() - it.w, it.x | 0));
  it.y = Math.max(0, it.y | 0);
  return it;
}
const STAT_ORDER = ['total', 'active', 'new', 'unauthorized', 'restricted', 'banned', 'frozen',
  'spam_block', 'spam_block_perm', 'flood_wait', 'error'];
function rank(k) {
  if (k.indexOf('stat:') === 0) {
    const i = STAT_ORDER.indexOf(k.slice(5));
    return 0 + (i < 0 ? 90 : i) * 0.01;
  }
  const R = {kickbar: 1, accounts: 2, tasks: 3, leads: 4, logText: 5, logJson: 6};
  return R[k] !== undefined ? R[k] : 9;
}
/* 默认自动排列：用户一旦手动调整过（auto=false）就不再介入 */
function autoArrange() {
  const p = page();
  const keys = visibleKeys().slice().sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : 1));
  const placed = [];
  keys.forEach(k => {
    const d = defSize(k);
    const it = p.items[k] || (p.items[k] = {k, x: 0, y: 0, w: d.w, h: d.h});
    it.k = k; it.w = d.w; it.h = d.h; it.x = 0; it.y = 0;
    outer:
    for (let y = 0; y < 400; y++) {
      for (let x = 0; x + it.w <= cols(); x++) {
        const t = {k, x, y, w: it.w, h: it.h};
        if (!placed.some(o => hits(o, t))) { it.x = x; it.y = y; break outer; }
      }
    }
    placed.push(it);
  });
}
function manual() { page().auto = false; }
function bottomY() {
  const p = page();
  return Object.keys(p.items).reduce((m, k) => Math.max(m, (p.items[k].y | 0) + (p.items[k].h | 0)), 0);
}

/* --- AABB 碰撞 --- */
function hits(a, b) {
  if (a === b || a.k === b.k) return false;
  return !(a.x + a.w <= b.x || a.x >= b.x + b.w || a.y + a.h <= b.y || a.y >= b.y + b.h);
}
function firstHit(list, it) { return list.find(o => hits(o, it)); }

/* --- 新控件落位：从上到下找第一个能放下的空位 --- */
function packItem(it) {
  const others = items().filter(o => o.k !== it.k);
  for (let y = 0; y < 200; y++) {
    for (let x = 0; x + it.w <= cols(); x++) {
      const t = {k: it.k, x, y, w: it.w, h: it.h};
      if (!firstHit(others, t)) { it.x = x; it.y = y; return; }
    }
  }
}

/* --- 被撞的控件向下推（递归链式） --- */
function pushDown(list, moved, guard) {
  guard = guard || {n: 0};
  let hit;
  while ((hit = firstHit(list.filter(o => o.k !== moved.k), moved)) && guard.n++ < 400) {
    hit.y = moved.y + moved.h;
    pushDown(list, hit, guard);
  }
}

/* --- 向上收紧（可固定某个控件） --- */
function compact(fixedKey) {
  const list = items().slice().sort((a, b) => a.y - b.y || a.x - b.x);
  const placed = [];
  list.forEach(it => {
    if (it.k !== fixedKey) {
      while (it.y > 0) {
        it.y--;
        if (placed.some(o => hits(o, it))) { it.y++; break; }
      }
    }
    placed.push(it);
  });
}
function compactNow() { manual(); compact(); renderLayout(); saveLayout(); toast('已向上收紧，消除空隙', 'ok'); }

/* --- 持久化 --- */
function loadLayout() {
  try {
    const o = JSON.parse(localStorage.getItem(layoutKey) || '{}');
    if (o && o.v === 3 && o.bp) LAY = o;
  } catch (e) {}
  BP = bpFor(window.innerWidth);
  ensureWx();
  renderLayout();
}
function saveLayout() {
  try { localStorage.setItem(layoutKey, JSON.stringify(LAY)); } catch (e) {}
}
function applyLayout() { ensureWx(); renderLayout(); saveLayout(); }

/* --- 注入 × 与尺寸手柄 --- */
function setInner(el, html) {
  let inner = el.querySelector(':scope > .inner');
  if (!inner) {
    inner = document.createElement('div');
    inner.className = 'inner';
    el.insertBefore(inner, el.firstChild);
  }
  inner.innerHTML = html;
}
function ensureWx() {
  const b = board(); if (!b) return;
  [...b.children].forEach(el => {
    const k = el.dataset && el.dataset.widget;
    if (!k) return;
    if (!el.querySelector(':scope > .wx')) {
      const btn = document.createElement('button');
      btn.className = 'wx';
      btn.textContent = '×';
      btn.title = '隐藏【' + wname(k) + '】';
      btn.onclick = ev => { ev.stopPropagation(); hideWidget(k); };
      el.appendChild(btn);
    }
    ['e', 's', 'se'].forEach(dir => {
      if (el.querySelector(':scope > .wr-' + dir)) return;
      const h = document.createElement('div');
      h.className = 'wr wr-' + dir;
      h.dataset.dir = dir;
      h.title = dir === 's' ? '拖动调高（吸附到行）'
        : dir === 'e' ? '拖动调宽（吸附到列）' : '拖动调大小，双击恢复默认';
      if (dir === 'se') h.ondblclick = ev => {
        ev.stopPropagation();
        manual();
        const d = defSize(k), it = itemOf(k);
        it.w = d.w; it.h = d.h;
        pushDown(items(), it); compact(k); applyLayout();
        toast('【' + wname(k) + '】已恢复默认大小', 'ok');
      };
      el.appendChild(h);
    });
  });
}

/* --- 渲染 --- */
function place(el, it, cw) {
  el.style.transform = `translate3d(${Math.round(it.x * (cw + GAP))}px,${it.y * (RH + GAP)}px,0)`;
  el.style.width = Math.round(it.w * cw + (it.w - 1) * GAP) + 'px';
  el.style.height = (it.h * RH + (it.h - 1) * GAP) + 'px';
}
function renderLayout() {
  const b = board(); if (!b) return;
  if (page().auto) autoArrange();
  const p = page(), cw = colW();
  [...b.children].forEach(el => {
    const k = el.dataset && el.dataset.widget;
    if (!k) return;
    const hidden = p.hidden.indexOf(k) >= 0;
    el.classList.toggle('whidden', hidden);
    if (hidden) return;
    const it = itemOf(k);
    if (!drag || drag.k !== k) place(el, it, cw);
  });
  b.style.height = Math.max(0, bottomVisible() * (RH + GAP) - GAP) + 'px';
  const box = $('#restoreList');
  if (box) box.innerHTML = p.hidden.length
    ? p.hidden.map(k => `<button class="sm fit" onclick="showWidget('${k}')">+ ${esc(wname(k))}</button>`).join('')
    : '<span class="muted">无，当前全部显示</span>';
  const tip = $('#bpTip');
  if (tip) tip.textContent = `当前断点：${BP_CN[BP]}（${cols()} 列）· 各断点分开保存`;
}
function bottomVisible() {
  return items().reduce((m, it) => Math.max(m, it.y + it.h), 0);
}

/* --- 显示 / 隐藏 --- */
function hideWidget(k) {
  manual();
  const p = page();
  if (p.hidden.indexOf(k) < 0) p.hidden.push(k);
  compact(); applyLayout();
  toastUndo('已隐藏【' + wname(k) + '】', () => showWidget(k));
}
function showWidget(k) {
  manual();
  const p = page();
  p.hidden = p.hidden.filter(x => x !== k);
  const it = p.items[k];
  if (it) { it.y = bottomVisible(); packItem(it); }
  compact(); applyLayout();
  if (k === 'kickbar') loadKick();
  if (k === 'settings') loadSettings();
  if (k === 'toolbox') loadOps();
  if (k === 'ziptools') renderZt();
}
function resetSizes() {
  manual();
  const p = page();
  Object.keys(p.items).forEach(k => {
    const d = defSize(k);
    p.items[k].w = d.w; p.items[k].h = d.h;
  });
  Object.keys(p.items).forEach(k => packItem(p.items[k]));
  compact(); applyLayout();
  toast('已恢复所有控件的默认大小', 'ok');
}
function resetLayout() {
  try { localStorage.removeItem(layoutKey); } catch (e) {}
  location.reload();
}
function toggleEdit() {
  editing = !editing;
  document.body.classList.toggle('editing', editing);
  $('#editBtn').textContent = editing ? '完成' : '编辑布局';
  if (editing) toast('编辑模式：拖中间移位（碰到的控件会被推开），拖右/下/右下角改大小，点 × 隐藏');
  else { compact(); applyLayout(); }
}
function toastUndo(msg, undo) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg + '  ';
  const b = document.createElement('button');
  b.className = 'sm fit';
  b.textContent = '撤销';
  b.onclick = () => { undo(); el.remove(); };
  el.appendChild(b);
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

/* --- 导出 / 导入 --- */
function exportLayout() {
  const blob = new Blob([JSON.stringify(LAY, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'tam-layout.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 3000);
  toast('已导出 tam-layout.json', 'ok');
}
function importLayout() {
  const f = $('#layFile');
  f.value = '';
  f.onchange = () => {
    const file = f.files && f.files[0];
    if (!file) return;
    const r = new FileReader();
    r.onload = () => {
      try {
        const o = JSON.parse(r.result);
        if (!o || o.v !== 3 || !o.bp) throw new Error('格式不对');
        LAY = o;
        compact(); applyLayout();
        toast('布局已导入', 'ok');
      } catch (e) { toast('导入失败：' + e.message, 'err'); }
    };
    r.readAsText(file);
  };
  f.click();
}

/* --- 占位框 --- */
function showPh(it, cw) {
  const ph = $('#ph');
  ph.classList.add('on');
  place(ph, it, cw);
}
function hidePh() { $('#ph').classList.remove('on'); }

/* --- 拖拽移位 --- */
function dragStart(e) {
  if (!editing || e.button > 0) return;
  if (e.target.closest('.wx') || e.target.closest('.wr')) return;
  const el = e.target.closest('[data-widget]');
  if (!el || el.parentElement !== board()) return;
  const k = el.dataset.widget;
  const r = el.getBoundingClientRect();
  const br = board().getBoundingClientRect();
  manual();
  drag = {
    k, el, it: itemOf(k),
    offX: e.clientX - r.left, offY: e.clientY - r.top,
    br, moved: false, px: 0, py: 0, ev: null,
    touch: e.pointerType === 'touch', ready: e.pointerType !== 'touch',
  };
  if (drag.touch) drag.timer = setTimeout(() => { if (drag) drag.ready = true; }, 220);
  try { el.setPointerCapture && el.setPointerCapture(e.pointerId); } catch (err) {}
}

function dragMove(e) {
  if (!drag) return;
  if (!drag.ready) {
    if (Math.abs(e.clientX - (drag.br.left + drag.offX)) > 24) { clearTimeout(drag.timer); drag = null; }
    return;
  }
  drag.ev = e;
  if (!drag.moved) {
    drag.moved = true;
    drag.el.classList.add('dragging');
    document.body.classList.add('dragging-on');
  }
  if (!rafPending) {
    rafPending = true;
    requestAnimationFrame(dragFrame);
  }
}

function dragFrame() {
  rafPending = false;
  if (!drag || !drag.ev) return;
  const e = drag.ev, cw = colW();
  const br = board().getBoundingClientRect();
  const px = e.clientX - br.left - drag.offX;
  const py = e.clientY - br.top - drag.offY;
  drag.el.style.transform = `translate3d(${px}px,${py}px,0)`;
  // 像素 -> 网格
  const gx = Math.max(0, Math.min(cols() - drag.it.w, Math.round(px / (cw + GAP))));
  const gy = Math.max(0, Math.round(py / (RH + GAP)));
  if (gx !== drag.it.x || gy !== drag.it.y) {
    drag.it.x = gx; drag.it.y = gy;
    pushDown(items(), drag.it);
    compact(drag.k);
    renderLayout();
  }
  showPh(drag.it, cw);
  // 边缘自动滚动
  const vy = e.clientY;
  if (vy < 90) window.scrollBy(0, -14);
  else if (vy > window.innerHeight - 90) window.scrollBy(0, 14);
}

function dragEnd() {
  if (!drag) return;
  const d = drag; drag = null;
  clearTimeout(d.timer);
  d.el.classList.remove('dragging');
  document.body.classList.remove('dragging-on');
  hidePh();
  if (d.moved) {
    manual();
    compact();
    applyLayout();
    toast(`【${wname(d.k)}】已放到 ${d.it.x + 1} 列 / ${d.it.y + 1} 行`, 'ok');
  } else {
    renderLayout();
  }
}

/* --- 边缘改尺寸 --- */
function resizeStart(e) {
  if (!editing || e.button > 0) return;
  const h = e.target.closest('.wr');
  if (!h) return;
  const el = h.closest('[data-widget]');
  if (!el || el.parentElement !== board()) return;
  e.preventDefault(); e.stopPropagation();
  const k = el.dataset.widget;
  manual();
  rsz = {k, el, dir: h.dataset.dir, it: itemOf(k), x: e.clientX, y: e.clientY, ev: null};
  el.classList.add('resizing');
  try { h.setPointerCapture && h.setPointerCapture(e.pointerId); } catch (err) {}
}
function resizeMove(e) {
  if (!rsz) return;
  rsz.ev = e;
  if (!rafPending) { rafPending = true; requestAnimationFrame(resizeFrame); }
}
function resizeFrame() {
  rafPending = false;
  if (!rsz || !rsz.ev) return;
  const e = rsz.ev, cw = colW(), mn = minSize(rsz.k);
  const br = board().getBoundingClientRect();
  let changed = false;
  if (rsz.dir !== 's') {
    const w = Math.max(mn.w, Math.min(cols() - rsz.it.x,
      Math.round((e.clientX - br.left - rsz.it.x * (cw + GAP) + GAP) / (cw + GAP))));
    if (w !== rsz.it.w) { rsz.it.w = w; changed = true; }
  }
  if (rsz.dir !== 'e') {
    const h = Math.max(mn.h, Math.min(60,
      Math.round((e.clientY - br.top - rsz.it.y * (RH + GAP) + GAP) / (RH + GAP))));
    if (h !== rsz.it.h) { rsz.it.h = h; changed = true; }
  }
  if (changed) {
    pushDown(items(), rsz.it);
    compact(rsz.k);
    renderLayout();
    showPh(rsz.it, cw);
  }
}
function resizeEnd() {
  if (!rsz) return;
  const r = rsz; rsz = null;
  r.el.classList.remove('resizing');
  hidePh();
  manual();
  compact();
  applyLayout();
  toast(`【${wname(r.k)}】${r.it.w}列 × ${r.it.h}行`, 'ok');
}

document.addEventListener('pointerdown', resizeStart, true);
document.addEventListener('pointerdown', dragStart);
document.addEventListener('pointermove', e => { resizeMove(e); dragMove(e); });
document.addEventListener('pointerup', () => { resizeEnd(); dragEnd(); });
document.addEventListener('pointercancel', () => { resizeEnd(); dragEnd(); });

let bpTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(bpTimer);
  bpTimer = setTimeout(() => {
    const nb = bpFor(window.innerWidth);
    if (nb !== BP) { BP = nb; ensureWx(); compact(); applyLayout(); }
    else renderLayout();
  }, 120);
});

/* ---------- 渲染 ---------- */
function visible() {
  const q = $('#q').value.trim().toLowerCase();
  return accounts.filter(a => {
    if (filter && a.status !== filter) return false;
    if (quickFilter === 'noauth' && a.authorized) return false;
    if (quickFilter === 'has2fa' && a.has_2fa !== 1) return false;
    if (quickFilter === 'nosaved2fa' && !(a.has_2fa === 1 && !a.has_twofa_saved && !a.twofa)) return false;
    if (quickFilter === 'noproxy' && a.proxy) return false;
    if (!q) return true;
    return [a.label, a.phone, a.username, (a.tags || []).join(',')].join(' ').toLowerCase().includes(q);
  });
}

const twofaShown = new Set();
let quickFilter = '';  // '' | noauth | has2fa | nosaved2fa | noproxy

function twofaMask(pwd) {
  const n = Math.min(Math.max((pwd || '').length, 4), 16);
  return '*'.repeat(n);
}
function twofaCell(a) {
  if (a.has_2fa !== 1) {
    if (a.has_2fa === 0) return '<span class="muted">-</span>';
    return '<span class="muted" title="尚未检查，点「检查」同步有无二验">-</span>';
  }
  const saved = !!(a.has_twofa_saved || a.twofa);
  if (!saved) {
    return `<span class="twofa-miss" title="有二验，密码未保存在本工具。点击补录（只写本地）"
      onclick="promptSaveTwofa(${a.id}, event)">有·未存</span>`;
  }
  const pwd = a.twofa || '';
  const shown = twofaShown.has(a.id) && pwd;
  const text = shown ? esc(pwd) : twofaMask(pwd || '******');
  const eye = shown ? '🙈' : '👁';
  const tip = shown ? '隐藏密码' : '显示密码';
  return `<span class="twofa-wrap">` +
    `<code class="twofa-text" id="t2fa_${a.id}">${text}</code>` +
    `<button type="button" class="twofa-eye" title="${tip}" aria-label="${tip}" ` +
    `onclick="toggleTwofa(${a.id}, event)">${eye}</button></span>`;
}
async function toggleTwofa(id, ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const a = accounts.find(x => x.id === id);
  const el = document.getElementById('t2fa_' + id);
  if (!a || !el) return;
  const btn = el.parentElement && el.parentElement.querySelector('.twofa-eye');
  if (twofaShown.has(id) && a.twofa) {
    twofaShown.delete(id);
    el.textContent = twofaMask(a.twofa);
    if (btn) { btn.textContent = '👁'; btn.title = '显示密码'; }
    return;
  }
  // 按需向后端揭密（列表接口不再下发明文）
  if (!a.twofa) {
    try {
      if (DEMO) {
        a.twofa = a.twofa || 'Demo2fa!';
      } else {
        const r = await api('/api/accounts/' + id + '/twofa');
        a.twofa = r.twofa || '';
        a.has_twofa_saved = !!r.saved;
      }
    } catch (e) {
      toast('无法读取二验：' + e.message, 'err');
      return;
    }
    if (!a.twofa) {
      toast('库内没有保存的二验密码，请点「有·未存」补录或用工具箱改二验', 'err');
      return;
    }
  }
  twofaShown.add(id);
  el.textContent = a.twofa;
  if (btn) { btn.textContent = '🙈'; btn.title = '隐藏密码'; }
}
async function promptSaveTwofa(id, ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const pwd = await uiPrompt({
    title: '补录二验密码',
    message: '只保存到本工具（加密入库），不会修改 Telegram 上的云密码。留空取消。',
    label: '二验密码',
    password: true,
    placeholder: '输入当前云密码以便导出号包',
  });
  if (pwd === null || !String(pwd).trim()) return;
  try {
    if (DEMO) {
      const a = accounts.find(x => x.id === id);
      if (a) { a.twofa = String(pwd).trim(); a.has_twofa_saved = true; a.has_2fa = 1; }
      toast('演示模式：已写入示例数据', 'ok');
      render();
      return;
    }
    await api('/api/accounts/' + id + '/twofa', {
      method: 'POST', body: JSON.stringify({password: String(pwd).trim()})
    });
    toast('二验密码已加密保存', 'ok');
    refresh();
  } catch (e) {
    toast('保存失败：' + e.message, 'err');
  }
}

/** 从批量结果里抽出成功/失败摘要，便于扫一眼 */
function batchSummary(r) {
  if (!r || typeof r !== 'object') return '';
  if (typeof r.ok === 'number' && typeof r.total === 'number') {
    const fail = r.failed != null ? r.failed : (r.total - r.ok);
    let s = `成功 ${r.ok} / 共 ${r.total}` + (fail ? `，失败 ${fail}` : '');
    if (Array.isArray(r.results)) {
      const bad = r.results.filter(x => x && x.ok === false).slice(0, 5);
      if (bad.length) {
        s += '\n失败摘要：' + bad.map(x => {
          const id = x.id != null ? x.id : x.account_id;
          const err = x.error || x.detail || x.message || '失败';
          return `#${id} ${err}`;
        }).join('；');
      }
    }
    return s;
  }
  if (Array.isArray(r.results)) {
    const ok = r.results.filter(x => x && x.ok !== false).length;
    const total = r.results.length;
    return `完成 ${ok}/${total}`;
  }
  return '';
}


function clearFilters() {
  filter = '';
  quickFilter = '';
  const q = $('#q');
  if (q) q.value = '';
  render();
}
function emptyRowHtml() {
  let inner;
  if (accounts.length === 0) {
    inner = '<div>还没有账号</div>'
      + '<div class="empty-actions">'
      + "<button class=\"fit\" onclick=\"openModal('mTdata')\">导入 tdata</button>"
      + "<button class=\"fit\" onclick=\"openModal('mSession')\">导入 session</button>"
      + "<button class=\"fit\" onclick=\"openModal('mImport')\">手机号清单</button>"
      + "<button class=\"fit\" onclick=\"openModal('mAdd')\">手动添加</button>"
      + '</div>'
      + '<div class="empty-hint">导入后建议先「健康检查」同步状态与二验，再养号或改二验</div>';
  } else if (quickFilter || filter || ($('#q') && $('#q').value)) {
    inner = '没有符合条件的账号 <button class="sm" onclick="clearFilters()">清除筛选</button>';
  } else {
    inner = '没有符合条件的账号';
  }
  return '<tr><td colspan="12"><div class="empty">' + inner + '</div></td></tr>';
}

function setQuickFilter(k) {
  quickFilter = (quickFilter === k) ? '' : k;
  render();
}

function render() {
  renderStats();

  const counts = stats.by_status || {};
  $('#chips').innerHTML = [['', '全部']].concat(Object.keys(counts).map(k => [k, `${k} ${counts[k]}`]))
    .map(([k, t]) => `<button class="chip${filter === k ? ' on' : ''}" onclick="setFilter('${k}')">${esc(t)}</button>`).join('');
  const qf = [
    ['noauth', '未授权'],
    ['has2fa', '有二验'],
    ['nosaved2fa', '二验未存'],
    ['noproxy', '无代理'],
  ];
  const qfe = $('#qfilters');
  if (qfe) qfe.innerHTML = qf.map(([k, lab]) =>
    `<button class="chip${quickFilter === k ? ' on' : ''}" onclick="setQuickFilter('${k}')">${lab}</button>`
  ).join('');

  const rows = visible();
  $('#tb').innerHTML = rows.length ? rows.map(a => `
    <tr class="${sel.has(a.id) ? 'sel' : ''}" onclick="openDrawer(${a.id})">
      <td onclick="event.stopPropagation()"><input type="checkbox" style="width:16px;min-height:0" ${sel.has(a.id) ? 'checked' : ''} onclick="pick(${a.id},this.checked)" /></td>
      <td><b>${esc(a.label)}</b></td>
      <td>${esc(fmtPhone(a.phone))}</td>
      <td class="hide-s">${esc(countryOf(a.phone))}</td>
      <td class="hide-s">${a.username ? '@' + esc(a.username) : '—'}</td>
      <td><span class="tag s-${esc(a.status)}">${esc(a.status)}</span>${a.premium ? ' <span class="tag">Premium</span>' : ''}${a.spam_until && a.spam_until * 1000 > Date.now() ? ' <span class="tag s-restricted">spam</span>' : ''}</td>
      <td class="hide-s" onclick="event.stopPropagation()">${twofaCell(a)}</td>
      <td class="hide-s">${a.proxy ? '已配置' : '<span class="muted">直连</span>'}</td>
      <td class="hide-s">${a.code_url ? '✓' : '—'}</td>
      <td class="hide-s">${(a.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join(' ') || '—'}</td>
      <td class="hide-s muted">${esc(kickCell(a))}</td>
      <td onclick="event.stopPropagation()">
        ${a.authorized ? '' : `<button class="sm write" onclick="startLogin(${a.id})">登录</button>`}
        <button class="sm write fit" onclick="startQrLogin(${a.id})" title="手机 Telegram 扫码授权">扫码登录</button>
        <button class="sm" onclick="check(${a.id})">检查</button>
      </td>
    </tr>`).join('') : emptyRowHtml();

  $('#selN').textContent = `已选 ${sel.size} 个`;
  $('#bulk').classList.toggle('on', sel.size > 0);
  $('#all').checked = rows.length > 0 && rows.every(a => sel.has(a.id));
  if (readonly) document.querySelectorAll('.write').forEach(b => b.disabled = true);
  ensureWx();
  applyLayout();
}

function renderStats() {
  const b = board(); if (!b) return;
  const keys = ['total'].concat(Object.keys(stats.by_status || {})).map(k => 'stat:' + k);
  [...b.children].forEach(n => {
    const k = n.dataset.widget;
    if (k && k.indexOf('stat:') === 0 && keys.indexOf(k) < 0) n.remove();
  });
  keys.slice().reverse().forEach(k => {
    const t = k.slice(5);
    const v = t === 'total' ? (stats.total || 0) : (stats.by_status || {})[t];
    let el = b.querySelector(`[data-widget="${k}"]`);
    if (!el) {
      el = document.createElement('div');
      el.className = 'card stat';
      el.dataset.widget = k;
      b.appendChild(el);
    }
    setInner(el, `<b>${v || 0}</b><span>${esc(wname(k).replace('统计 · ', ''))}</span>`);
  });
}
function setFilter(k) { filter = k; render(); }
function pick(id, on) { on ? sel.add(id) : sel.delete(id); render(); }
function toggleAll(on) { visible().forEach(a => on ? sel.add(a.id) : sel.delete(a.id)); render(); }
function clearSel() { sel.clear(); render(); }

/* ---------- 详情抽屉 ---------- */
function openDrawer(id) {
  const a = accounts.find(x => x.id === id);
  if (!a) return;
  closeAll();
  $('#dTitle').textContent = a.label;
  $('#dBody').innerHTML = `
    <div class="kv">
      <span>ID</span><div>${a.id}</div>
      <span>状态</span><div><span class="tag s-${esc(a.status)}">${esc(a.status)}</span> ${a.authorized ? '已授权' : '<span class="muted">未授权</span>'}${a.premium ? ' · Premium' : ''}</div>
      <span>二验</span><div>${
        a.has_2fa === 1
          ? (a.twofa || a.has_twofa_saved
              ? ('已开启' + (a.twofa ? (' · <code>' + esc(a.twofa) + '</code>') : ' · 已保存（列表点眼睛查看）'))
              : '已开启 · <a href="#" onclick="promptSaveTwofa(' + a.id + ', event);return false">补录密码</a>')
          : (a.has_2fa === 0 ? '-' : '未知 · 点检查同步')
      }</div>
      <span>手机号</span><div>${esc(fmtPhone(a.phone))}</div>
      <span>国家/地区</span><div>${esc(countryOf(a.phone))}</div>
      <span>本机接管</span><div>${a.adopted_at ? esc(fmt(a.adopted_at)) + '（' + esc(rel(a.adopted_at)) + '）' : '<span class=\'muted\'>未记录</span>'}</div>
      <span>会话创建</span><div>${a.login_at ? esc(fmt(a.login_at)) + '（' + esc(rel(a.login_at)) + '）<span class="muted"> · 服务端 date_created，仅供参考</span>' : '<span class=\'muted\'>未记录，点“查看设备”可从 Telegram 拉取</span>'}</div>
      <span>用户名</span><div>${a.username ? '@' + esc(a.username) : '—'}</div>
    </div>
    <label class="f"><span>别名</span><input id="eLabel" value="${esc(a.label)}" /></label>
    <label class="f"><span>手机号</span><input id="ePhone" value="${esc(a.phone || '')}" /></label>
    <label class="f"><span>代理</span><input id="eProxy" value="${esc(a.proxy || '')}" placeholder="socks5://host:1080" /></label>
    <label class="f"><span>取码链接</span><input id="eCode" value="${esc(a.code_url || '')}" /></label>
    <div class="row" style="gap:8px;margin:-6px 0 12px;align-items:center">
      <button type="button" class="sm fit" id="eCodeTest" onclick="testCodeSource('eCode','eProxy','eCodeStatus')">测试取码链接</button>
      <span class="muted" id="eCodeStatus">只读测试，不发送 Telegram 验证码</span>
    </div>
    <label class="f"><span>标签（逗号分隔）</span><input id="eTags" value="${esc((a.tags || []).join(','))}" /></label>
    <div class="f">
      <span class="lbl">自动清设备</span>
      <label class="row" style="gap:8px;cursor:pointer">
        <input type="checkbox" id="eKick" style="width:16px;min-height:0;flex:0 0 auto" ${a.auto_kick ? 'checked' : ''} />
        <span class="muted" style="flex:1">参与自动踢其它设备 · ${esc(kickHint(a))}</span>
      </label>
    </div>
    <label class="f"><span>清设备周期（小时）</span>
      <input id="eKickHours" type="number" min="0" step="0.5" placeholder="留空=用全局 ${kick.hours||24} 小时"
        value="${a.auto_kick_hours != null && a.auto_kick_hours !== '' ? esc(String(a.auto_kick_hours)) : ''}" />
    </label>
    <div class="f">
      <span class="lbl">循环清设备</span>
      <label class="row" style="gap:8px;cursor:pointer">
        <input type="checkbox" id="eKickLoop" style="width:16px;min-height:0;flex:0 0 auto" ${(a.auto_kick_loop == null || a.auto_kick_loop) ? 'checked' : ''} />
        <span class="muted" style="flex:1">开启后：踢成功再按周期计时；关闭则只踢一次</span>
      </label>
    </div>
    <div class="row" style="margin-bottom:20px">
      <button class="primary write fit" onclick="saveAccount(${a.id})">保存修改</button>
      <button class="fit" onclick="check(${a.id})">健康检查</button>
      <button class="fit write" onclick="spamCheck(${a.id})">限制检测</button>
      <button class="fit" onclick="twofaStatus(${a.id})" title="有无云密码、是否绑定辅助邮箱、重置等待期">二验状态</button>
      <button class="fit" onclick="devices(${a.id})">查看设备</button>
      <button class="fit" onclick="dialogs(${a.id})">最近会话</button>
    </div>
    <div class="row" style="margin-bottom:8px;flex-wrap:wrap;gap:6px">
      <button class="danger write fit" onclick="twofaReset(${a.id})" title="发起官方二验重置（通常约7天等待期）">发起2FA重置</button>
      <button class="write fit" onclick="twofaResetCancel(${a.id})" title="取消进行中的二验重置等待">取消2FA重置</button>
      <button class="write fit" onclick="exportAcc(${a.id},'string')" title="显示/复制 StringSession 文本">导出 StringSession</button>
      <button class="write fit" onclick="exportAcc(${a.id},'session')" title="下载 Telethon .session 文件">导出 .session</button>
      <button class="write fit" onclick="exportAcc(${a.id},'pack')" title="下载 session+json 号包 zip">导出号包 zip</button>
      <button class="write fit" onclick="exportAcc(${a.id},'tdata')" title="下载 Telegram Desktop tdata zip（需服务器已装 opentele）">导出 tdata</button>
      <button class="danger write fit" onclick="regenSession(${a.id})" title="新设备指纹重新登录，旧 session 作废（防找回）">重生会话</button>
    </div>
    <div class="row">
      ${a.authorized ? '' : `<button class="write fit" onclick="startLogin(${a.id})">登录</button>`}
      <button class="write fit" onclick="startQrLogin(${a.id})" title="手机 Telegram 扫码授权，无需短信">扫码登录</button>
      <button class="write fit" onclick="terminate(${a.id})">清理其他设备</button>
      <button class="write fit" onclick="logout(${a.id})">退出登录</button>
      <button class="fit danger write" onclick="deleteTelegram(${a.id})">注销TG账号</button>
      <button class="danger write fit" onclick="delAccount(${a.id})">删除账号</button>
    </div>
    <h2 style="font-size:14px;margin:20px 0 8px">该账号日志</h2>
    <pre id="dLog" style="max-height:320px">加载中…</pre>`;
  $('#drawer').classList.add('on'); $('#bd').classList.add('on');
  if (readonly) document.querySelectorAll('.write').forEach(b => b.disabled = true);
  api(`/api/logs?account_id=${id}&limit=20`)
    .then(l => $('#dLog').textContent = (l.length ? logLines(l) : '暂无日志'))
    .catch(e => $('#dLog').textContent = String(e));
}

/* ---------- 动作 ---------- */
async function guard(fn, okMsg) {
  // 任何“用户主动触发”的结果都锁定日志区，
  // 否则紧接着的 refresh() → loadLogs() 会把它刷掉（就是“一闪而过”）。
  try {
    const r = await fn();
    out(r, true);
    const sum = batchSummary(r);
    if (sum) toast((okMsg ? okMsg + ' · ' : '') + sum.replace(/\n/g, ' '), 'ok');
    else if (okMsg) toast(okMsg, 'ok');
    refresh();
    return r;
  } catch (e) {
    toast('失败：' + e.message, 'err');
    out({error: String(e)}, true);
  }
}
const addAccount = () => guard(async () => {
  const r = await api('/api/accounts', {method:'POST', body: JSON.stringify({
    label: $('#aLabel').value, phone: $('#aPhone').value || null,
    proxy: $('#aProxy').value || null,
    tags: $('#aTags').value ? $('#aTags').value.split(',').map(s => s.trim()) : []})});
  if ($('#aCode').value) await api(`/api/accounts/${r.id}`, {method:'PATCH', body: JSON.stringify({code_url: $('#aCode').value})});
  ['#aLabel','#aPhone','#aCode','#aProxy','#aTags'].forEach(s => $(s).value = '');
  closeAll(); return r;
}, '账号已添加');

const saveAccount = id => guard(async () => {
  const hoursRaw = ($('#eKickHours') && $('#eKickHours').value || '').trim();
  let auto_kick_hours = null;
  if (hoursRaw !== '') {
    const n = parseFloat(hoursRaw);
    if (!Number.isFinite(n) || n < 0) throw new Error('清设备周期须为非负数字（小时）');
    auto_kick_hours = n === 0 ? null : n;
  }
  const r = await api(`/api/accounts/${id}`, {method:'PATCH', body: JSON.stringify({
    label: $('#eLabel').value, phone: $('#ePhone').value || null,
    proxy: $('#eProxy').value || null, code_url: $('#eCode').value || null,
    auto_kick: $('#eKick').checked ? 1 : 0,
    auto_kick_loop: ($('#eKickLoop') && $('#eKickLoop').checked) ? 1 : 0,
    auto_kick_hours: auto_kick_hours,
    tags: $('#eTags').value ? $('#eTags').value.split(',').map(s => s.trim()) : []})});
  closeAll(); return r;
}, '已保存');

function codeProbeSummary(r) {
  const type = {json: 'JSON', html: 'HTML', text: '纯文本'}[r.response_type] || r.response_type;
  return `${r.provider_label} · ${type} · ${r.code_present ? '当前检测到验证码' : '当前暂无验证码'}`
    + (r.requires_prepare ? ' · 登录时会先启动监控' : '');
}

async function testCodeSource(urlId, proxyId, statusId, explicitUrl) {
  const urlEl = $('#' + urlId);
  const proxyEl = proxyId ? $('#' + proxyId) : null;
  const status = $('#' + statusId);
  const url = (explicitUrl || (urlEl && urlEl.value) || '').trim();
  if (!url) {
    if (status) status.textContent = '请先填写取码链接';
    toast('请先填写取码链接', 'err');
    return null;
  }
  if (status) status.textContent = '正在只读探测…';
  try {
    const r = await api('/api/code-sources/probe', {
      method: 'POST',
      body: JSON.stringify({url, proxy: (proxyEl && proxyEl.value) || null, timeout: 15}),
    });
    const summary = codeProbeSummary(r);
    if (status) status.textContent = summary;
    toast('取码链接可访问：' + summary, 'ok');
    return r;
  } catch (e) {
    if (status) status.textContent = '探测失败：' + e.message;
    toast('取码链接探测失败：' + e.message, 'err');
    return null;
  }
}


const check = id => guard(() => api(`/api/accounts/${id}/check`, {method:'POST'}), '检查完成');

async function twofaStatus(id) {
  return guard(() => api('/api/accounts/' + id + '/toolbox/twofa_status', {
    method: 'POST', body: JSON.stringify({params: {}})
  }), '二验状态已查询');
}
async function twofaReset(id) {
  if (!confirm('确认对该账号发起 2FA 重置？\n将进入官方等待期（通常约 7 天），到期后云密码失效。\n若绑定了辅助邮箱，原持有人仍可能通过邮箱恢复。')) return;
  return guard(() => api('/api/accounts/' + id + '/toolbox/twofa_reset', {
    method: 'POST', body: JSON.stringify({params: {confirm: true}})
  }), '已提交 2FA 重置');
}
async function twofaResetCancel(id) {
  if (!confirm('确认取消该账号进行中的 2FA 重置等待？')) return;
  return guard(() => api('/api/accounts/' + id + '/toolbox/twofa_reset_cancel', {
    method: 'POST', body: JSON.stringify({params: {confirm: true}})
  }), '已取消 2FA 重置');
}

const devices = id => guard(() => api(`/api/accounts/${id}/devices`));
const dialogs = id => guard(() => api(`/api/accounts/${id}/dialogs?limit=20`));
const terminate = async id => { if (!await uiConfirm({title:'踢出其他设备', message:'将踢掉该账号的其他所有登录设备，只保留本机会话。', danger:true, okText:'踢出'})) return; return guard(async () => {
  const r = await api(`/api/accounts/${id}/devices/terminate`, {method:'POST'});
  // 后端会踢完再拉一次会话列表校验，这里把“真的踢掉了没”直接告诉人
  if (r && r.verified === false)
    toast('校验未通过：还剩 ' + (r.after_others || 0) + ' 个外部会话'
      + (r.error ? '（' + r.error + '）' : '') + (r.reappeared && r.reappeared.length ? '，有设备踢完又重登' : ''), 'err');
  else if (r && r.verified)
    toast('已校验：踢掉 ' + ((r.removed || []).length) + ' 个，现只剩本机', 'ok');
  refresh(); return r;
}, '已执行清理'); };
const logout = async id => { if (!await uiConfirm({title:'退出登录', message:'退出后需要重新验证码登录，确定？', danger:true, okText:'退出'})) return; return guard(() => api(`/api/accounts/${id}/logout`, {method:'POST'}), '已退出'); };
const delAccount = async id => { if (!await uiConfirmDanger({title:'删除本地记录', message:'删除本地记录（不影响 Telegram 账号本身），但本工具内会话将不可恢复。', word:'确认'})) return; return guard(async () => { const r = await api(`/api/accounts/${id}`, {method:'DELETE'}); closeAll(); sel.delete(id); return r; }, '已删除'); };

/* ---------- 导出会话（敏感：会解密出本地会话） ---------- */
function _exportAuthHeaders() {
  const h = {};
  const tok = ($('#token') && $('#token').value) || localStorage.getItem('tam_token') || '';
  if (tok) h['Authorization'] = 'Bearer ' + tok;
  return h;
}

async function _downloadBlob(path, body, fallbackName) {
  const res = await fetch(path, {
    method: 'POST',
    headers: Object.assign({'Content-Type': 'application/json'}, _exportAuthHeaders()),
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    const raw = await res.text();
    let msg = raw;
    try { msg = (JSON.parse(raw).detail) || raw; } catch (_) {}
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  const disp = res.headers.get('Content-Disposition') || '';
  let name = fallbackName;
  const m = /filename="?([^";]+)"?/i.exec(disp);
  if (m) name = m[1];
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  return name;
}

async function exportAcc(id, fmt) {
  fmt = fmt || 'pack';
  let msg = '将导出该账号会话文件（敏感凭证）。继续？';
  if (fmt === 'string') msg = '将解密并显示该账号的 StringSession。会话等同登录凭证，请勿泄露。继续？';
  else if (fmt === 'tdata') msg = '导出 tdata 需要服务器已安装 opentele，且会话有效。继续？';
  else if (fmt === 'pack') msg = '将导出 session+json 号包 zip（敏感凭证）。' + exportTwofaHint(id).replace(' · ', '') + '\n继续？';
  if (!await uiConfirm({title: '导出确认', message: msg, danger: true, okText: '导出'})) return;
  try {
    progress(true);
    if (fmt === 'string') {
      const r = await api('/api/accounts/' + id + '/export', {
        method: 'POST', body: JSON.stringify({format: 'string'})});
      out(r, true);
      const sess = r.session || '';
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(sess);
          toast('StringSession 已复制到剪贴板', 'ok');
        } else {
          toast('已导出 StringSession（见下方日志）', 'ok');
        }
      } catch (_) {
        toast('已导出 StringSession（见下方日志，复制失败可手动选）', 'ok');
      }
    } else {
      const name = await _downloadBlob(
        '/api/accounts/' + id + '/export',
        {format: fmt},
        fmt === 'session' ? ('account-' + id + '.session')
          : (fmt === 'tdata' ? ('account-' + id + '-tdata.zip') : ('account-' + id + '.zip'))
      );
      toast('已下载 ' + name + exportTwofaHint(id), 'ok');
    }
  } catch (e) {
    const msg = String(e.message || e);
    out({error: msg}, true);
    if (fmt === 'tdata' && /opentele/i.test(msg)) {
      const go = await uiConfirm({
        title: '需要安装 opentele',
        message: '导出 tdata 依赖 opentele。\n\n' + msg +
          '\n\n是否现在一键安装？（pip install opentele，可能需要 1–2 分钟）',
        okText: '一键安装',
        danger: false,
      });
      if (go) {
        try {
          progress(true);
          const r = await api('/api/system/install-opentele', {method: 'POST', body: '{}'});
          toast(r.message || '安装完成', 'ok');
          out(r, true);
          if (r.installed && await uiConfirm({title: '安装成功', message: '是否立即重新导出 tdata？', okText: '重新导出'})) {
            progress(false);
            return exportAcc(id, 'tdata');
          }
        } catch (e2) {
          toast('安装失败：' + e2.message, 'err');
          out({error: String(e2)}, true);
        }
      }
    } else {
      toast('导出失败：' + msg, 'err');
    }
  } finally {
    progress(false);
  }
}

async function bulkExport(fmt) {
  fmt = fmt || 'pack';
  const ids = [...sel];
  if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  const msg = '将导出选中的 ' + ids.length + ' 个账号会话（敏感凭证）为 zip。'
    + exportTwofaHint(ids) + '\n继续？';
  if (!await uiConfirm({title: '批量导出', message: msg, danger: true, okText: '导出'})) return;
  try {
    progress(true);
    const name = await _downloadBlob(
      '/api/accounts/export',
      {ids: ids, format: fmt},
      'accounts-export.zip'
    );
    toast('已下载 ' + name + '（' + ids.length + ' 个）' + exportTwofaHint(ids), 'ok');
  } catch (e) {
    toast('批量导出失败：' + e.message, 'err');
    out({error: String(e)}, true);
  } finally {
    progress(false);
  }
}

async function regenSession(id) {
  if (!await uiConfirm({
    title: '重生会话',
    message: '将用新设备指纹重新登录该号：\n· 旧 session 会尝试注销（前手难再用）\n· 库内换成新会话\n· 需要能收 Telegram 官方验证码\n· 短时间不要反复重生，否则会触发登录限流（PhonePasswordFlood）',
    danger: true,
    okText: '继续',
  })) return;
  const password = await uiPrompt({
    title: '两步验证密码',
    message: '若该号开启了云密码请填写；没有可留空直接确定。',
    label: '二验密码（可选）',
    password: true,
  });
  if (password === null) return;
  try {
    progress(true);
    const r = await api('/api/accounts/' + id + '/regenerate-session', {
      method: 'POST',
      body: JSON.stringify({password: password || null, code_wait: 8})
    });
    out(r, true);
    toast(r.ok ? ('会话已重生' + (r.old_logged_out ? '，旧授权已注销' : '（旧授权注销未确认）')) : '重生失败', r.ok ? 'ok' : 'err');
    refresh();
    if (r.ok && await uiConfirm({title: '导出新号包？', message: '是否继续导出新的号包 zip？', okText: '导出'})) {
      await exportAcc(id, 'pack');
    }
  } catch (e) {
    const msg = String(e.message || e);
    const flood = /PhonePasswordFlood|登录.?次数过多|FloodWait|too many/i.test(msg);
    toast((flood ? '重生被限流：' : '重生失败：') + msg, 'err');
    out({error: msg, flood: flood}, true);
  } finally {
    progress(false);
  }
}

async function bulkRegen() {
  const ids = [...sel];
  if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  if (!await uiConfirmDanger({
    title: '批量重生会话',
    message: '将对选中的 ' + ids.length + ' 个号重生会话（旧授权作废）。\n任务在服务端后台执行：关掉浏览器也不中断，可在「任务中心」查看进度或停止。\n下一步可选择并行数（1=串行更稳，2~5 更快但易触发限流）。',
    word: '确认',
    okText: '下一步',
  })) return;
  const password = await uiPrompt({
    title: '统一两步验证密码',
    message: '若各号密码相同可在此填写；没有或各不相同请留空。',
    label: '二验密码（可选）',
    password: true,
  });
  if (password === null) return;
  const concStr = await uiPrompt({
    title: '并行数',
    message: '1 = 串行（最稳）\n2~5 = 并行加快，可能触发 FloodWait\n最大 8\n默认来自参数面板 TAM_REGEN_CONCURRENCY',
    label: '同时重生几个号',
    value: String(regenConcDefault()),
  });
  if (concStr === null) return;
  let concurrency = parseInt(String(concStr).trim(), 10);
  if (!Number.isFinite(concurrency) || concurrency < 1) concurrency = 1;
  if (concurrency > 8) concurrency = 8;
  try {
    uiProgress({title: '批量重生会话 ×' + concurrency, current: 0, total: ids.length, text: '提交任务…'});
    // 异步作业：立刻返回 task id，后台跑；轮询任务中心，不依赖长连接
    const created = await api('/api/accounts/regenerate-session?async=1', {
      method: 'POST',
      body: JSON.stringify({ids: ids, password: password || null, code_wait: 8, concurrency: concurrency}),
    });
    const taskId = (created.task && created.task.id) || created.job;
    if (!taskId) throw new Error('未返回任务 id');
    out(created, true);
    toast('已提交重生任务 #' + taskId + '，后台执行中', 'ok');
    if (typeof loadTasks === 'function') loadTasks(true);

    const terminal = {done:1, failed:1, stopped:1};
    let last = created.task || {};
    let stagnant = 0;
    let lastDone = -1;
    const maxStagnant = 100; // 约 100*2s ≈ 3.5 分钟无进展则提示并结束轮询
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      let t;
      try {
        t = await api('/api/tasks/' + taskId + '?target_limit=50');
      } catch (e) {
        // 短暂网络抖动不中止；继续轮询
        continue;
      }
      last = t;
      const done = (t.ok_count || 0) + (t.fail_count || 0) + (t.skip_count || 0);
      const total = t.total || ids.length;
      const targets = t.targets || [];
      const latest = targets.slice().reverse().find(x => x.status === 'ok' || x.status === 'fail' || x.status === 'running');
      let detail = t.status_cn || t.status || '';
      if (latest) {
        const a = accounts.find(x => String(x.id) === String(latest.target));
        const lab = (a && a.label) || ('#' + latest.target);
        detail = lab + ' · ' + (latest.status_cn || latest.status) + (latest.detail ? ' · ' + latest.detail : '');
      }
      if (done === lastDone) stagnant++;
      else { stagnant = 0; lastDone = done; }
      uiProgress({
        title: '批量重生会话 ×' + concurrency + ' · 任务 #' + taskId,
        current: done,
        total: total,
        detail: detail,
        text: (t.percent != null ? t.percent + '%' : '') + ' 成功' + (t.ok_count||0) + ' 失败' + (t.fail_count||0)
          + (stagnant > 15 ? ' · 当前号耗时较长…' : ''),
      });
      if (terminal[t.status]) break;
      if (!t.live && done >= total && total > 0) break;
      if (!t.live && terminal[t.status]) break;
      // 服务端已不在跑，但状态未终态：避免死循环
      if (!t.live && stagnant > 5) break;
      if (stagnant >= maxStagnant) {
        toast('任务长时间无进展，已停止等待。请打开任务中心查看或点停止后重试', 'err');
        break;
      }
    }
    uiProgress(null);
    const r = {
      ok: true,
      task_id: taskId,
      total: last.total || ids.length,
      succeeded: last.ok_count || 0,
      failed: last.fail_count || 0,
      skipped: last.skip_count || 0,
      status: last.status,
      status_cn: last.status_cn,
    };
    out(r, true);
    toast(
      '重生结束：成功 ' + r.succeeded + ' / 失败 ' + r.failed
        + (r.skipped ? ' / 跳过 ' + r.skipped : '')
        + '（任务 #' + taskId + '）',
      r.failed ? 'err' : 'ok'
    );
    if (typeof loadTasks === 'function') loadTasks(true);
    refresh();
  } catch (e) {
    uiProgress(null);
    toast('批量重生失败：' + e.message, 'err');
    out({error: String(e)}, true);
  }
}




/* 扫码登录 */
let qrLoginId = null;
let qrWaitAbort = false;

async function startQrLogin(id) {
  qrLoginId = id;
  qrWaitAbort = false;
  const a = accounts.find(x => x.id === id);
  const who = a ? ((a.label || '') + ' ' + (a.phone || '')).trim() : ('#' + id);
  if ($('#qrWho')) $('#qrWho').textContent = who;
  if ($('#qrPass')) $('#qrPass').value = '';
  if ($('#qrPassWrap')) $('#qrPassWrap').style.display = 'none';
  if ($('#qrSubmitPass')) $('#qrSubmitPass').style.display = 'none';
  if ($('#qrStatus')) $('#qrStatus').textContent = '正在获取二维码…';
  if ($('#qrImg')) $('#qrImg').removeAttribute('src');
  openModal('mQrLogin');
  try {
    await qrLoginRefresh();
    qrLoginPoll();
  } catch (e) {
    if ($('#qrStatus')) $('#qrStatus').textContent = '失败：' + e.message;
    toast('扫码登录失败：' + e.message, 'err');
  }
}

async function qrLoginRefresh() {
  if (!qrLoginId) return;
  if ($('#qrStatus')) $('#qrStatus').textContent = '刷新二维码…';
  const r = await api('/api/accounts/' + qrLoginId + '/login/qr', {method: 'POST'});
  if (r.qr_png_base64 && $('#qrImg')) {
    $('#qrImg').src = 'data:image/png;base64,' + r.qr_png_base64;
  }
  const exp = r.expires_in != null ? r.expires_in : '';
  if ($('#qrStatus')) {
    $('#qrStatus').textContent = '请扫码…' + (exp !== '' ? ('（约 ' + exp + 's 内有效）') : '');
  }
  out(r, true);
  return r;
}

async function qrLoginPoll() {
  if (!qrLoginId || qrWaitAbort) return;
  try {
    const r = await api('/api/accounts/' + qrLoginId + '/login/qr/wait', {
      method: 'POST',
      body: JSON.stringify({timeout: 50}),
    });
    if (qrWaitAbort) return;
    if (r.ok) {
      if ($('#qrStatus')) $('#qrStatus').textContent = '登录成功';
      toast('扫码登录成功', 'ok');
      out(r, true);
      closeAll();
      refresh();
      return;
    }
    if (r.need_password) {
      if ($('#qrPassWrap')) $('#qrPassWrap').style.display = '';
      if ($('#qrSubmitPass')) $('#qrSubmitPass').style.display = '';
      if ($('#qrStatus')) $('#qrStatus').textContent = r.note || '请填写两步验证密码';
      toast(r.note || '需要两步验证密码', 'err');
      return;
    }
    if (r.pending) {
      if (r.qr_png_base64 && $('#qrImg')) {
        $('#qrImg').src = 'data:image/png;base64,' + r.qr_png_base64;
      }
      if ($('#qrStatus')) {
        const exp = r.expires_in != null ? r.expires_in : '';
        $('#qrStatus').textContent = '等待扫码…' + (exp !== '' ? ('（剩余约 ' + exp + 's）') : '');
      }
      if (!qrWaitAbort) setTimeout(qrLoginPoll, 400);
      return;
    }
    if ($('#qrStatus')) $('#qrStatus').textContent = r.note || r.error || '未完成';
  } catch (e) {
    if (qrWaitAbort) return;
    if ($('#qrStatus')) $('#qrStatus').textContent = '等待中断：' + e.message + '（可点刷新）';
    // 短暂重试，避免网络抖动直接放弃
    if (!qrWaitAbort) setTimeout(qrLoginPoll, 2000);
  }
}

async function qrLoginSubmitPass() {
  if (!qrLoginId) return;
  const password = ($('#qrPass') && $('#qrPass').value) || '';
  if (!password) { toast('请填写两步验证密码', 'err'); return; }
  try {
    progress(true);
    const r = await api('/api/accounts/' + qrLoginId + '/login/qr/wait', {
      method: 'POST',
      body: JSON.stringify({password: password, timeout: 30}),
    });
    if (r.ok) {
      toast('扫码登录成功', 'ok');
      closeAll();
      refresh();
    } else if (r.need_password) {
      toast(r.note || r.error || '密码错误', 'err');
    } else {
      toast(r.note || '未完成', 'err');
    }
    out(r, true);
  } catch (e) {
    toast('提交失败：' + e.message, 'err');
  } finally {
    progress(false);
  }
}

async function qrLoginCancel() {
  qrWaitAbort = true;
  const id = qrLoginId;
  qrLoginId = null;
  try {
    if (id) await api('/api/accounts/' + id + '/login/qr', {method: 'DELETE'});
  } catch (_) {}
  closeAll();
}


async function autoLogin(id, closeOnSuccess) {
  const password = loginId === id && $('#lPass') ? ($('#lPass').value || null) : null;
  const r = await guard(() => api(`/api/accounts/${id}/login/auto`, {method:'POST',
    body: JSON.stringify({timeout:120, password})}), '自动接码登录完成');
  if (r && closeOnSuccess) closeAll();
  return r;
}

async function autoLoginFromModal() {
  if (!loginId) return;
  const btn = $('#lAutoBtn');
  const hint = $('#lHint');
  if (btn) btn.disabled = true;
  if (hint) hint.textContent = '正在启动取码监控、发送验证码并等待新码…';
  try {
    const r = await autoLogin(loginId, true);
    if (!r && hint) hint.textContent = '自动接码失败，请查看错误日志后重试。';
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* 登录（内联表单，替代 prompt） */
async function startLogin(id) {
  loginId = id;
  const a = accounts.find(x => x.id === id);
  $('#lWho').textContent = a ? `${a.label} ${a.phone || ''}` : '';
  $('#lCode').value = $('#lPass').value = '';
  $('#lHint').textContent = a && a.code_url
    ? '尚未发送验证码。可手动发码后填写验证码，或点击自动接码登录。'
    : '尚未发送验证码。点击“发送验证码”后手动填写收到的验证码。';
  $('#lSendBtn').textContent = '发送验证码';
  $('#lAutoBtn').style.display = a && a.code_url ? '' : 'none';
  openModal('mLogin');
}
async function resend() {
  if (!loginId) return;
  const btn = $('#lSendBtn');
  if (btn) btn.disabled = true;
  try {
    const r = await guard(
      () => api(`/api/accounts/${loginId}/login/code`, {method:'POST'}),
      '验证码已发送',
    );
    if (r) {
      $('#lHint').textContent = '验证码已发送，请填写收到的验证码后确认登录。';
      btn.textContent = '重新发送验证码';
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}
const verify = () => guard(async () => {
  const body = {code: $('#lCode').value, password: $('#lPass').value || null};
  const r = await api(`/api/accounts/${loginId}/login/verify`, {method:'POST', body: JSON.stringify(body)});
  if (r.need_password) { toast('该号开启了两步验证，请填写密码', 'err'); return r; }
  closeAll(); return r;
}, '登录成功');

/* 批量（逐号进度条；可并行） */
async function bulk(kind) {
  const ids = [...sel];
  if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  if (kind === 'check') {
    const r = await runPool(ids, async (id) => {
      return await api('/api/accounts/' + id + '/check', {method: 'POST'});
    }, '健康检查', batchConc(3));
    out(r, true);
    toast('健康检查 · 成功 ' + r.ok + ' / 共 ' + r.total + (r.failed ? '，失败 ' + r.failed : ''), r.failed ? 'err' : 'ok');
    refresh();
    return r;
  }
  if (kind === 'auto') {
    if (!await uiConfirm({title:'批量自动登录', message:'将对 ' + ids.length + ' 个账号串行执行「发码 → 自动取码 → 登录」。', danger:true, okText:'开始'})) return;
    const r = await runSerial(ids, async (id) => {
      return await api('/api/accounts/' + id + '/login/auto', {
        method: 'POST', body: JSON.stringify({timeout: 120})});
    }, '批量自动登录');
    out(r, true);
    toast('自动登录 · 成功 ' + r.ok + ' / 共 ' + r.total + (r.failed ? '，失败 ' + r.failed : ''), r.failed ? 'err' : 'ok');
    refresh();
    return r;
  }
}
async function bulkWarmup() {
  const ids = [...sel];
  if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  if (!await uiConfirm({title:'养号', message:'将对选中的健康账号执行养号（保活在线 + 随机已读 + 账号互聊）。\n服务端批量执行，完成后统一返回结果。', okText:'开始'})) return;
  const r = await withWaitProgress(
    '养号 · ' + ids.length + ' 个号',
    '服务端执行中（在线/已读/互聊），请稍候…',
    () => api('/api/batch/warmup', {
      method:'POST',
      body: JSON.stringify({account_ids: ids, concurrency: 2, rounds: 1}),
    })
  );
  out(r, true);
  toast('养号任务完成', 'ok');
  refresh();
  return r;
}
async function proxyAudit() {
  const r = await withWaitProgress('代理体检', '正在探测各账号代理连通性…',
    () => api('/api/proxies/audit'));
  out(r, true);
  toast('代理体检完成', 'ok');
  return r;
}
async function spamCheck(id) {
  const r = await withWaitProgress('限制检测 · ' + accLabel(id), '正在与 @SpamBot 对话…',
    () => api('/api/accounts/' + id + '/spam-check', {method:'POST'}));
  out(r, true);
  toast('限制检测完成', 'ok');
  return r;
}
async function okpayBalance(id) {
  const r = await withWaitProgress('OKPay 余额 · ' + accLabel(id), '点选币种菜单并解析余额…',
    () => api('/api/accounts/' + id + '/okpay-balance', {
      method:'POST', body: JSON.stringify({bot: 'Okpay', wait: 4})}));
  out(r, true);
  toast('OKPay 余额查询完成', 'ok');
  return r;
}
async function bulkOkpay() {
  const ids = [...sel];
  if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  if (!await uiConfirm({title:'OKPay 余额', message:'将对选中的 ' + ids.length + ' 个号查询 OKPay 余额（会向 @Okpay 发消息并点选币种）。', okText:'查询'})) return;
  const r = await runPool(ids, async (id) => {
    return await api('/api/accounts/' + id + '/okpay-balance', {
      method: 'POST', body: JSON.stringify({bot: 'Okpay', wait: 4})});
  }, 'OKPay 余额', batchConc(2));
  out(r, true);
  toast('OKPay · 成功 ' + r.ok + ' / 共 ' + r.total + (r.failed ? '，失败 ' + r.failed : ''), r.failed ? 'err' : 'ok');
  return r;
}
async function bulkTerminate() {
  const ids = [...sel];
  if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  if (!await uiConfirm({
    title: '批量踢出设备',
    message: '将踢掉选中 ' + ids.length + ' 个账号的【其他所有登录设备】，只保留本机会话。',
    danger: true,
    okText: '开始踢出',
  })) return;
  const r = await runPool(ids, async (id) => {
    return await api('/api/accounts/' + id + '/devices/terminate', {method: 'POST'});
  }, '批量踢出设备', batchConc(2));
  out(r, true);
  toast('踢设备完成 · 成功 ' + r.ok + ' / 共 ' + r.total + (r.failed ? '，失败 ' + r.failed : ''), r.failed ? 'err' : 'ok');
  refresh();
}
let spinTimer = null;
function spinPreview() {
  clearTimeout(spinTimer);
  spinTimer = setTimeout(async () => {
    const text = $('#sText').value;
    if (!text.includes('{')) { $('#spinN').textContent = '变体数 1'; $('#spinPrev').textContent = ''; return; }
    try {
      const r = await api('/api/spintax/preview', {method:'POST', body: JSON.stringify({text})});
      if (!r.ok) { $('#spinN').textContent = '语法错误'; $('#spinPrev').textContent = r.error || ''; return; }
      $('#spinN').textContent = `变体数 ${r.variants}`;
      $('#spinPrev').textContent = (r.preview || []).join('\n') + (r.warning ? '\n⚠ ' + r.warning : '');
    } catch (e) { $('#spinN').textContent = '预览失败'; }
  }, 400);
}
const doSend = () => guard(async () => {
  const ids = [...sel];
  const body = {account_ids: ids, peer: $('#sPeer').value, text: $('#sText').value, concurrency: 2,
                spintax: $('#sSpin').checked, healthy_only: $('#sHealthy').checked};
  const r = await api('/api/batch/message', {method:'POST', body: JSON.stringify(body)});
  closeAll(); return r;
}, '发送任务完成');
async function bulkTag() {
  const ids = [...sel];
  if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  const t = await uiPrompt({
    title: '追加标签',
    message: '给选中的 ' + ids.length + ' 个账号追加标签，多个用逗号分隔。',
    label: '标签',
    placeholder: 'batch2, us',
  });
  if (t === null || !String(t).trim()) return;
  const add = String(t).split(',').map(s => s.trim()).filter(Boolean);
  const r = await runPool(ids, async (id) => {
    const a = accounts.find(x => x.id === id);
    const tags = [...new Set([...(a && a.tags || []), ...add])];
    await api('/api/accounts/' + id, {method: 'PATCH', body: JSON.stringify({tags})});
    return {ok: true};
  }, '批量打标签', batchConc(5));
  out(r, true);
  toast('标签已更新 · ' + r.ok + '/' + r.total, 'ok');
  refresh();
}

async function deleteTelegram(id) {
  if (!await uiConfirmDanger({
    title: '注销 Telegram 账号',
    message: '将向 Telegram 官方申请【永久删除】账号 #' + id + '。\n此操作不可逆：手机号绑定的 TG 账号会被注销，不只是退出登录。\n请输入「注销」确认。',
    word: '注销',
    okText: '永久注销',
  })) return;
  const reason = await uiPrompt({
    title: '注销原因（可选）',
    message: '将提交给 Telegram 的原因说明，可留空用默认。',
    label: '原因',
    value: 'User requested deletion',
  });
  if (reason === null) return;
  try {
    uiProgress({title: '注销账号 · ' + accLabel(id), current: 0, total: 1, text: '提交中…'});
    const r = await api('/api/accounts/' + id + '/delete-telegram', {
      method: 'POST',
      body: JSON.stringify({
        confirm: true,
        reason: String(reason || 'User requested deletion').trim() || 'User requested deletion',
        purge_local: true,
      }),
    });
    uiProgress(null);
    out(r, true);
    toast(r.ok ? '已提交注销并清理本地会话' : ('注销返回：' + (r.note || '')), r.ok ? 'ok' : 'err');
    refresh();
  } catch (e) {
    uiProgress(null);
    toast('注销失败：' + e.message, 'err');
    out({error: String(e)}, true);
  }
}

async function bulkDeleteTelegram() {
  const ids = [...sel];
  if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  if (!await uiConfirmDanger({
    title: '批量注销 Telegram 账号',
    message: '将对选中的 ' + ids.length + ' 个号向官方申请【永久删除】。\n不可逆。请输入「批量注销」确认。',
    word: '批量注销',
    okText: '开始注销',
  })) return;
  const r = await runPool(ids, async (id) => {
    return await api('/api/accounts/' + id + '/delete-telegram', {
      method: 'POST',
      body: JSON.stringify({confirm: true, reason: 'User requested deletion', purge_local: true}),
    });
  }, '注销 Telegram 账号', 1);
  out(r, true);
  toast('注销完成 · 成功 ' + r.ok + ' / 共 ' + r.total + (r.failed ? '，失败 ' + r.failed : ''), r.failed ? 'err' : 'ok');
  refresh();
}

async function bulkDelete() {
  const n = sel.size;
  if (!n) { toast('请先勾选账号', 'err'); return; }
  if (!await uiConfirmDanger({
    title: '删除本地记录',
    message: '将删除选中的 ' + n + ' 条本地记录（不影响 Telegram 账号本身，但本工具内会话密文会消失）。',
    word: '确认',
    okText: '删除',
  })) return;
  const ids = [...sel];
  const r = await runPool(ids, async (id) => {
    await api('/api/accounts/' + id, {method: 'DELETE'});
    return {ok: true};
  }, '批量删除', batchConc(5));
  sel.clear();
  out(r, true);
  toast('已删除 · 成功 ' + r.ok + ' / 共 ' + r.total, 'ok');
  refresh();
}

/* 批量导入 */
function cleanImportPart(part) {
  let value = part.trim();
  if (value.length >= 2 && value.startsWith('`') && value.endsWith('`')) {
    value = value.replace(/^`+|`+$/g, '').trim();
  }
  if (value.length >= 4 && ((value.startsWith('**') && value.endsWith('**')) ||
      (value.startsWith('__') && value.endsWith('__')))) {
    value = value.slice(2, -2).trim();
  }
  if (value.length >= 2 && value.startsWith('<') && value.endsWith('>')) {
    value = value.slice(1, -1).trim();
  }
  return value;
}

function isMarkdownTableMeta(parts) {
  if (parts.length && parts.every(p => /^:?-{3,}:?$/.test(p.replace(/\s/g, '')))) return true;
  const names = new Set(parts.map(p => p.replace(/[\s_\/-]/g, '').toLowerCase()));
  const hasPhone = ['phone', 'phonenumber', 'mobile', '手机号', '号码'].some(x => names.has(x));
  const hasUrl = ['url', 'link', 'codeurl', '取码链接', '链接'].some(x => names.has(x));
  return hasPhone && hasUrl;
}

function parseLines(text) {
  const ok = [], bad = [];
  text.split('\n').forEach((raw, i) => {
    const line = raw.trim().replace(/^\ufeff/, '');
    if (!line || line.startsWith('#')) return;
    const parts = line.split(/\s*(?:\\\||\||｜|\t|,|;)\s*/)
      .map(cleanImportPart).filter(Boolean);
    if (isMarkdownTableMeta(parts)) return;
    let phone = null, url = null, label = null;
    parts.forEach(p => {
      if (/^https?:\/\//i.test(p)) url = url || p;
      else if (/^\+?\d[\d\s\-()]{5,}$/.test(p)) {
        const digitCount = p.replace(/\D/g, '').length;
        if (digitCount >= 7 && digitCount <= 15) phone = phone || ('+' + p.replace(/\D/g, ''));
      }
      else if (/^(?:#\s*)?\d+[.)]?$/i.test(p)) return;
      else label = label || p;
    });
    phone ? ok.push({phone, url, label}) : bad.push({line: i + 1, raw: line});
  });
  return {ok, bad};
}
function previewImport() {
  const {ok, bad} = parseLines($('#iText').value);
  const withUrl = ok.filter(x => x.url).length;
  $('#iPrev').innerHTML = ok.length || bad.length
    ? `解析预览：<b>${ok.length}</b> 条有效（其中 ${withUrl} 条带取码链接）` +
      (bad.length ? `，<span style="color:#a13f36">${bad.length} 行无法识别：第 ${bad.slice(0,5).map(b => b.line).join('、')} 行</span>` : '')
    : '解析预览：等待输入';
}

async function testFirstImportedCodeSource() {
  const {ok} = parseLines($('#iText').value);
  const first = ok.find(item => item.url);
  if (!first) {
    $('#iCodeStatus').textContent = '没有识别到取码链接';
    toast('没有识别到取码链接', 'err');
    return;
  }
  return testCodeSource('', 'iProxy', 'iCodeStatus', first.url);
}

const doImport = dry => guard(async () => {
  const r = await withWaitProgress(
    dry ? '试运行导入清单' : '批量导入账号',
    dry ? '解析中…' : '写入数据库中…',
    () => api('/api/accounts/import', {method:'POST', body: JSON.stringify({
      text: $('#iText').value,
      tags: $('#iTags').value ? $('#iTags').value.split(',').map(s => s.trim()) : [],
      proxy: $('#iProxy').value || null, dry_run: dry})}),
    120000);
  if (!dry) closeAll();
  return r;
}, null).then(r => r && toast(dry ? '试运行完成，结果见日志区' : `导入完成：新增 ${(r.added||[]).length}，更新 ${(r.updated||[]).length}`, 'ok'));

function toggleTdataMode() {
  const mode = (document.querySelector('input[name="tdataMode"]:checked') || {}).value || 'upload';
  $('#tdataUploadBox').style.display = mode === 'upload' ? '' : 'none';
  $('#tdataPathBox').style.display = mode === 'path' ? '' : 'none';
}

document.addEventListener('change', function (e) {
  if (!e.target || e.target.id !== 'tUpload') return;
  const files = e.target.files || [];
  const el = $('#tUploadHint');
  if (!el) return;
  if (!files.length) { el.textContent = '尚未选择文件'; return; }
  const names = Array.from(files).map(f => f.name);
  el.textContent = '已选 ' + files.length + ' 个：' + names.slice(0, 5).join('、')
    + (names.length > 5 ? '…' : '');
});

const tdataBody = () => JSON.stringify({
  path: $('#tPath').value, scan: $('#tScan') ? $('#tScan').checked : false,
  label: $('#tLabel').value || null, password: $('#tPass').value || null,
  proxy: $('#tProxy').value || null, debug: $('#tDebug').checked,
  tags: $('#tTags').value ? $('#tTags').value.split(',').map(s => s.trim()).filter(Boolean) : []});

async function _tdataUploadOne(file, extra) {
  const q = new URLSearchParams();
  if (extra.label) q.set('label', extra.label);
  if (extra.password) q.set('password', extra.password);
  if (extra.proxy) q.set('proxy', extra.proxy);
  if (extra.tags && extra.tags.length) q.set('tags', extra.tags.join(','));
  if (extra.debug) q.set('debug', 'true');
  q.set('scan', 'true');
  const path = '/api/accounts/import-tdata-upload' + (q.toString() ? '?' + q.toString() : '');
  const headers = {'X-Filename': file.name};
  if (!DEMO) {
    const tok = ($('#token') && $('#token').value) || localStorage.getItem('tam_token') || '';
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
  }
  if (DEMO) {
    return {ok: true, filename: file.name, tdata_dirs: 1, succeeded: 1,
            items: [{path: 'demo/tdata', accounts: [{ok: true, label: file.name, user_id: 1}]}]};
  }
  const res = await fetch(path, {method: 'POST', body: file, headers});
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch (_) { data = {detail: text}; }
  if (!res.ok) throw new Error(data.detail || data.message || text || ('HTTP ' + res.status));
  return data;
}

async function doTdata() {
  const mode = (document.querySelector('input[name="tdataMode"]:checked') || {}).value || 'upload';
  const extra = {
    label: $('#tLabel').value || null,
    password: $('#tPass').value || null,
    proxy: $('#tProxy').value || null,
    tags: $('#tTags').value ? $('#tTags').value.split(',').map(s => s.trim()).filter(Boolean) : [],
    debug: $('#tDebug').checked,
  };
  try {
    uiProgress({title: 'tdata 导入', current: 0, total: 1, text: '准备中…'});
    let r;
    if (mode === 'upload') {
      const input = $('#tUpload');
      const files = input && input.files ? Array.from(input.files) : [];
      if (!files.length) { toast('请选择要上传的 tdata zip', 'err'); return; }
      const items = [];
      let okN = 0, badN = 0;
      for (let fi = 0; fi < files.length; fi++) {
        const f = files[fi];
        uiProgress({
          title: 'tdata 导入',
          current: fi,
          total: files.length,
          text: '上传解析 ' + (fi + 1) + '/' + files.length + ' · ' + f.name,
        });
        const one = await _tdataUploadOne(f, extra);
        for (const e of (one.items || [])) {
          items.push(e);
          for (const a of (e.accounts || [])) {
            if (a.ok) okN++; else badN++;
          }
        }
      }
      r = items;
      const blocks = items.map(e => {
        const per = (e.accounts || []).map(a => a.ok
          ? ('  ✓ ' + (a.label || '') + ' user_id=' + (a.user_id || '?'))
          : ('  ✗ ' + (a.label || '') + ' ' + (a.error || '未知错误'))).join('\n');
        return '目录：' + e.path + '\n' + per + (e.debug ? '\n' + renderTdataReport(e.debug) : '');
      });
      out(blocks.join('\n\n') || JSON.stringify(items, null, 2), true);
      if (okN) { toast('导入成功 ' + okN + ' 个账号' + (badN ? '，失败 ' + badN : ''), badN ? '' : 'ok'); closeAll(); }
      else toast('导入失败：0 个账号，详情见下方日志区', 'err');
    } else {
      const path = ($('#tPath').value || '').trim();
      if (!path) { toast('请填写服务端本地路径', 'err'); return; }
      r = await withWaitProgress('tdata 路径导入', '正在解析并验证 tdata（号多时较久）…',
        () => api('/api/accounts/import-tdata', {method:'POST', body: tdataBody()}), 600000);
      const accs = [].concat(...(r || []).map(e => e.accounts || []));
      const okN = accs.filter(a => a.ok).length, badN = accs.length - okN;
      const blocks = (r || []).map(e => {
        const per = (e.accounts || []).map(a => a.ok
          ? ('  ✓ ' + (a.label || '') + ' user_id=' + (a.user_id || '?'))
          : ('  ✗ ' + (a.label || '') + ' ' + (a.error || '未知错误'))).join('\n');
        return '目录：' + e.path + '\n' + per + (e.debug ? '\n' + renderTdataReport(e.debug) : '');
      });
      out(blocks.join('\n\n') || JSON.stringify(r, null, 2), true);
      if (okN) { toast('导入成功 ' + okN + ' 个账号' + (badN ? '，失败 ' + badN : ''), badN ? '' : 'ok'); closeAll(); }
      else toast('导入失败：0 个账号，详情见下方日志区', 'err');
    }
    refresh();
  } catch (e) {
    toast('失败：' + e.message, 'err');
    out({error: String(e)}, true);
  } finally {
    uiProgress(null);
  }
}

async function doTdataInspect() {
  const mode = (document.querySelector('input[name="tdataMode"]:checked') || {}).value || 'upload';
  if (mode === 'upload') {
    toast('「先体检」仅支持服务端路径；上传请直接导入（失败会带诊断）', 'err');
    return;
  }
  try {
    const r = await api('/api/tdata/inspect', {method:'POST', body: tdataBody()});
    const reps = r.reports || [];
    out(reps.map(renderTdataReport).join('\n\n') || '没有可诊断的目录', true);
    const good = reps.filter(x => x.ok).length;
    toast(good ? ('体检完成：' + good + '/' + reps.length + ' 个目录可用') : '体检未通过，详情见日志区', good ? 'ok' : 'err');
  } catch (e) {
    toast('体检失败：' + e.message, 'err');
    out({error: String(e)}, true);
  }
}

/* 自动刷新 */
let timer = null;
$('#auto').addEventListener('change', e => {
  clearInterval(timer);
  if (e.target.checked) timer = setInterval(refresh, 10000);
});

/* 启动 */
loadLayout();
if (DEMO) {
  $('#demoBar').classList.add('on');
  $('#mode').textContent = '演示模式';
  $('#mode').className = 'pill ro';
  accounts = MOCK.accounts; stats = MOCK.stats;
  render();
  loadKick();
  loadTasks();
  loadLeads();
  loadSettings();
  loadOps();
  renderZt();
  out(MOCK.logs);
} else {
  probeMode();
  refresh();
}
/* ---------- 工具箱：把 GAF 那批功能直接作用于库里选中的号 ---------- */
/* 字段全靠 /api/toolbox/ops 下发的 OP_SPECS 动态渲染，后端加新 op 前端一行都不用改 */
let TB_OPS = [];
let TB_CUR = '';

const TB_PRIV_KEYS = [
  ['phone', '手机号可见'], ['last_seen', '最后上线'], ['invite', '被拉进群'],
  ['avatar', '头像可见'], ['call', '来电'], ['forward', '转发时署名'],
];
const TB_PRIV_VALS = [['', '不改动'], ['everybody', '所有人'], ['contacts', '联系人'], ['nobody', '没有人']];

async function loadOps() {
  const box = $('#tbBody');
  if (!box) return;
  try {
    const r = await api('/api/toolbox/ops');
    TB_OPS = r.ops || [];
    if (!TB_CUR && TB_OPS.length) TB_CUR = TB_OPS[0].op;
    renderTb();
  } catch (e) {
    box.innerHTML = '<span class="muted">加载失败：' + esc(e.message) + '</span>';
  }
}

function tbSpec() { return TB_OPS.find(o => o.op === TB_CUR) || null; }

function tbField(p) {
  const id = 'tbp_' + p.name;
  const req = p.required ? ' <span class="tag s-restricted">必填</span>' : '';
  const dv = (p.default === undefined || p.default === null) ? '' : String(p.default);

  if (p.type === 'bool') {
    const on = p.default === true ? ' checked' : '';
    return '<label class="row" style="gap:8px;cursor:pointer;margin:6px 0">' +
      '<input type="checkbox" id="' + id + '" style="width:16px;min-height:0"' + on + ' />' +
      '<span>' + esc(p.label) + req + '</span></label>';
  }
  if (p.type === 'privacy') {
    const rows = TB_PRIV_KEYS.map(function (kv) {
      const opts = TB_PRIV_VALS.map(function (vt) {
        return '<option value="' + vt[0] + '">' + esc(vt[1]) + '</option>';
      }).join('');
      return '<label class="f"><span>' + esc(kv[1]) + '</span>' +
        '<select id="tbpriv_' + kv[0] + '">' + opts + '</select></label>';
    }).join('');
    return '<div class="muted" style="margin:6px 0">' + esc(p.label) +
      '（留「不改动」的项不会被碰）</div>' + rows;
  }
  if (p.type === 'textarea') {
    return '<label class="f"><span>' + esc(p.label) + req + '</span>' +
      '<textarea id="' + id + '" rows="5" placeholder="每行一个"></textarea></label>';
  }
  const t = p.type === 'password' ? 'password'
    : ((p.type === 'int' || p.type === 'float') ? 'number' : 'text');
  const step = p.type === 'float' ? ' step="any"' : '';
  return '<label class="f"><span>' + esc(p.label) + req + '</span>' +
    '<input id="' + id + '" type="' + t + '"' + step +
    ' placeholder="' + esc(dv ? '默认 ' + dv : '') + '" /></label>';
}

function renderTb() {
  const box = $('#tbBody');
  if (!box) return;
  const sp = tbSpec();
  const opts = TB_OPS.map(function (o) {
    return '<option value="' + esc(o.op) + '"' + (o.op === TB_CUR ? ' selected' : '') +
      '>' + (o.danger ? '⚠ ' : '') + esc(o.label) + '</option>';
  }).join('');

  const warn = (sp && sp.danger)
    ? '<div class="empty" style="border-color:#a33;color:#e88;margin:8px 0">' +
      '⚠ 不可逆操作，执行前会再确认一次。建议先拿一个号试。</div>' : '';

  box.innerHTML =
    '<label class="f"><span>操作</span>' +
    '<select id="tbOp" onchange="TB_CUR=this.value;renderTb()">' + opts + '</select></label>' +
    '<div class="muted" style="margin:4px 0 8px">' + (sp ? esc(sp.desc) : '') + '</div>' +
    warn +
    (sp ? sp.params.map(tbField).join('') : '') +
    '<label class="f"><span>并发度</span>' +
    '<input id="tbConc" type="number" min="1" max="32" ' +
    'placeholder="留空 = 用参数面板里的 TAM_BATCH_CONCURRENCY" /></label>' +
    '<div class="row" style="gap:8px;margin-top:8px">' +
    '<button class="sm write" onclick="runTb()">对选中的号执行</button>' +
    '<span class="muted" id="tbSel">已选 ' + sel.size + ' 个</span></div>' +
    '<div id="tbOut" style="margin-top:10px"></div>';

  if (readonly) box.querySelectorAll('.write').forEach(function (b) { b.disabled = true; });
}

/* 把表单收成 params。数字留空就不传，让后端用默认值——
   不能自作主张填 0，那跟「没填」完全是两回事。 */
function tbCollect(sp) {
  const out = {};
  for (const p of sp.params) {
    if (p.type === 'privacy') {
      const items = {};
      for (const kv of TB_PRIV_KEYS) {
        const el = $('#tbpriv_' + kv[0]);
        const v = el ? (el.value || '') : '';
        if (v) items[kv[0]] = v;
      }
      if (Object.keys(items).length) out[p.name] = items;
      continue;
    }
    const el = $('#tbp_' + p.name);
    if (!el) continue;
    if (p.type === 'bool') { out[p.name] = el.checked; continue; }
    const raw = String(el.value || '').trim();
    if (!raw) continue;
    if (p.type === 'int' || p.type === 'float') {
      const n = Number(raw);
      if (!isFinite(n)) throw new Error(p.label + ' 填的不是数字：' + raw);
      out[p.name] = p.type === 'int' ? Math.trunc(n) : n;
    } else {
      out[p.name] = raw;
    }
  }
  return out;
}

function tbLabel(id) {
  const a = accounts.find(function (x) { return x.id === id; });
  return a ? a.label : ('#' + id);
}

async function runTb() {
  const sp = tbSpec();
  if (!sp) return;
  const ids = [...sel];
  if (!ids.length) { toast('先在账号列表里勾选要操作的号', 'err'); return; }

  let params;
  try { params = tbCollect(sp); }
  catch (e) { toast(e.message, 'err'); return; }

  if (sp.danger) {
    const msg = '【' + sp.label + '】是不可逆操作，将对 ' + ids.length +
      ' 个号执行。\n\n' + sp.desc + '\n\n确定继续？';
    if (!await uiConfirm({title:'请确认', message: msg, danger:true})) return;
  }

  const cel = $('#tbConc');
  const cv = cel ? String(cel.value || '').trim() : '';
  let conc = cv ? Number(cv) : batchConc(3);
  if (!Number.isFinite(conc) || conc < 1) conc = 1;
  if (conc > 32) conc = 32;

  const box = $('#tbOut');
  box.innerHTML = '<span class="muted">执行中…</span>';
  try {
    const r = await runPool(ids, async (id) => {
      const one = await api('/api/accounts/' + id + '/toolbox/' + encodeURIComponent(sp.op), {
        method: 'POST',
        body: JSON.stringify({params: params}),
      });
      // 统一成 batch 结果行结构
      if (one && typeof one === 'object' && ('ok' in one || 'result' in one || 'error' in one)) {
        return Object.assign({account_id: id}, one);
      }
      return {ok: true, account_id: id, result: one};
    }, '工具箱 · ' + sp.label, conc);
    // 适配 renderTbResult
    const adapted = {
      total: r.total,
      ok: r.ok,
      failed: r.failed,
      results: (r.results || []).map(function (x) {
        return {
          account_id: x.account_id != null ? x.account_id : x.id,
          ok: x.ok !== false && !x.error,
          result: x.result != null ? x.result : x,
          error: x.error,
        };
      }),
    };
    renderTbResult(adapted);
    out(adapted, true);
    toast(sp.label + '：成功 ' + adapted.ok + ' / 共 ' + adapted.total, adapted.failed ? 'warn' : 'ok');
    refresh();
  } catch (e) {
    box.innerHTML = '<span class="muted">失败：' + esc(e.message) + '</span>';
    toast('失败：' + e.message, 'err');
    uiProgress(null);
  }
}

function renderTbResult(r) {
  const rows = (r.results || []).map(function (x) {
    const tone = x.ok ? 'ok' : 'err';
    const txt = x.ok ? tbBrief(x.result) : String(x.error || '失败');
    return '<tr><td>' + esc(tbLabel(x.account_id)) + '</td>' +
      '<td><span class="tag s-' + tone + '">' + (x.ok ? '成功' : '失败') + '</span></td>' +
      '<td class="muted">' + esc(txt) + '</td></tr>';
  }).join('');
  $('#tbOut').innerHTML =
    '<div class="muted" style="margin-bottom:6px">共 ' + r.total + ' 个，成功 ' +
    r.ok + '，失败 ' + r.failed + '</div>' +
    '<table><thead><tr><th>账号</th><th>结果</th><th>说明</th></tr></thead><tbody>' +
    rows + '</tbody></table>';
}

/* 把返回的字典压成一行人话。每个 op 返回的键不一样，挑常见的显示，
   实在认不出来就原样 JSON，总比什么都不显示强。 */
function tbBrief(v) {
  if (v === null || v === undefined) return '完成';
  if (typeof v !== 'object') return String(v);
  const pick = ['note', 'status', 'message', 'detail', 'action', 'path', 'username',
    'title', 'reply_count', 'deleted', 'total', 'returned', 'alive', 'has_password'];
  for (const k of pick) {
    if (v[k] !== undefined && v[k] !== null && v[k] !== '') {
      if (k === 'alive') return v[k] ? '存活' : ('失效 ' + (v.reason || ''));
      if (k === 'has_password') return v[k] ? '已设二验' : '无二验';
      if (k === 'reply_count') return '回复 ' + v[k] + ' 条';
      if (k === 'deleted' && v.total !== undefined) return '删除 ' + v.deleted + '/' + v.total;
      return String(v[k]);
    }
  }
  const parts = Object.keys(v)
    .filter(function (k) { return typeof v[k] !== 'object'; })
    .slice(0, 6)
    .map(function (k) { return k + '=' + v[k]; });
  return parts.length ? parts.join('  ') : JSON.stringify(v);
}
/* ---------- ZIP 工具：拆包 / 合并 / 按注册时间分类 ---------- */
/* 上传走原始请求体（后端没引 python-multipart，它跟同名的 multipart 包冲突），
   所以不能用 api()——那个 helper 会强制套 JSON 头并 stringify。 */

let ZT_JOB = null;      // 当前拆包/分类作业
let ZT_MERGE_JOB = null; // 当前合并作业（要分次上传，得单独记）
let ZT_MERGE_N = 0;

function ztAuth() {
  const el = $('#token');
  return el && el.value ? {Authorization: 'Bearer ' + el.value} : {};
}

async function ztFetch(path, opts) {
  const o = opts || {};
  o.headers = Object.assign({}, ztAuth(), o.headers || {});
  const res = await fetch(path, o);
  const raw = await res.text();
  let j = {};
  try { j = raw ? JSON.parse(raw) : {}; } catch (e) { j = {}; }
  if (!res.ok) {
    if (res.status === 401) throw new Error('令牌不对或已过期，先在上方填好令牌');
    throw new Error(j.detail || j.message || raw || ('HTTP ' + res.status));
  }
  return j;
}

/* 下载路由是带鉴权的，普通 <a href> 不会带 Authorization 头，
   所以得先 fetch 成 blob 再触发保存。 */
async function ztDownload(url, filename) {
  try {
    const res = await fetch(url, {headers: ztAuth()});
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      URL.revokeObjectURL(a.href);
      a.remove();
    }, 1000);
  } catch (e) {
    toast('下载失败：' + e.message, 'err');
  }
}

function ztFile(id) {
  const el = $(id);
  return el && el.files && el.files.length ? el.files : null;
}

function ztConc(id) {
  const el = $(id);
  const v = el ? String(el.value || '').trim() : '';
  return v ? ('?workers=' + encodeURIComponent(v)) : '';
}

function ztSay(id, html) {
  const el = $(id);
  if (el) el.innerHTML = html;
}

function renderZt() {
  const box = $('#ztBody');
  if (!box) return;
  box.innerHTML =
    '<div class="muted" style="margin-bottom:8px">这三个工具处理的是你上传的号包文件，' +
    '不碰库里已托管的号。结果拿完请点「用完删除」——号包是敏感物，' +
    '不要长期赖在服务器上。</div>' +

    '<h4 style="margin:10px 0 4px">一、拆包</h4>' +
    '<label class="f"><span>选择号包</span>' +
    '<input type="file" id="ztUpFile" accept=".zip" /></label>' +
    '<label class="f"><span>拆分格式</span>' +
    '<input id="ztFmt" placeholder="-9-  表示每包 9 个；5,5,5 表示逐包指定" /></label>' +
    '<label class="f"><span>并发度</span>' +
    '<input id="ztUpConc" type="number" min="1" max="32" ' +
    'placeholder="留空 = 用参数面板里的 TAM_WORKERS" /></label>' +
    '<div class="row" style="gap:8px">' +
    '<button class="sm" onclick="ztAnalyze()">先看看有多少个号</button>' +
    '<button class="sm write" onclick="ztUnpack()">拆包</button></div>' +
    '<div id="ztUnpackOut" style="margin:6px 0 12px"></div>' +

    '<h4 style="margin:10px 0 4px">二、合并</h4>' +
    '<div class="muted" style="margin-bottom:4px">可一次多选，也可分多次添加（攒到同一个作业）。' +
    '同名的号会自动改名，不会静默覆盖。</div>' +
    '<label class="f"><span>选择号包（可多选）</span>' +
    '<input type="file" id="ztMergeFiles" accept=".zip" multiple /></label>' +
    '<label class="f"><span>并发度</span>' +
    '<input id="ztMergeConc" type="number" min="1" max="32" ' +
    'placeholder="留空 = 用参数面板里的 TAM_WORKERS" /></label>' +
    '<div class="row" style="gap:8px">' +
    '<button class="sm" onclick="ztMergeAdd()">添加到合并作业</button>' +
    '<button class="sm write" onclick="ztMergeRun()">开始合并</button>' +
    '<span class="muted" id="ztMergeN">已攒 0 个包</span></div>' +
    '<div id="ztMergeOut" style="margin:6px 0 12px"></div>' +

    '<h4 style="margin:10px 0 4px">三、按注册时间分类</h4>' +
    '<div class="muted" style="margin-bottom:4px">默认<b>完全离线</b>：只用包里 json 已有的' +
    '日期字段，一个字节不外发。只有配了 TAM_REGTIME_ENDPOINT 才会联网查。</div>' +
    '<label class="f"><span>选择号包</span>' +
    '<input type="file" id="ztRegFile" accept=".zip" /></label>' +
    '<label class="f"><span>并发度</span>' +
    '<input id="ztRegConc" type="number" min="1" max="32" ' +
    'placeholder="留空 = 用参数面板里的 TAM_WORKERS" /></label>' +
    '<div class="row" style="gap:8px">' +
    '<button class="sm write" onclick="ztRegtime()">开始分类</button></div>' +
    '<div id="ztRegOut" style="margin:6px 0"></div>' +

    '<h4 style="margin:14px 0 4px">四、转 API</h4>' +
    '<div class="muted" style="margin-bottom:4px">上传 session / tdata 号包，生成重命名 session、' +
    '<code>api.json</code> 与取码链接清单（对齐机器人「转 API」；源自 GAFBot MIT）。' +
    '取码前缀默认读环境变量 <code>TAM_TOAPI_BASE</code> / <code>DM</code> / <code>SERVER_IP:API_PORT</code>。</div>' +
    '<label class="f"><span>选择号包 ZIP</span>' +
    '<input type="file" id="ztToapiFile" accept=".zip" /></label>' +
    '<label class="f"><span>2FA 处理</span>' +
    '<select id="ztToapiMode" style="min-height:0;padding:4px 8px">' +
    '<option value="from_json">从 JSON / 2fa.txt 提取</option>' +
    '<option value="no_2fa">无二验</option>' +
    '<option value="manual">统一手动输入 2FA</option>' +
    '</select></label>' +
    '<label class="f"><span>统一 2FA（仅 manual）</span>' +
    '<input id="ztToapiPass" type="password" placeholder="manual 模式必填" autocomplete="off" /></label>' +
    '<label class="f"><span>取码 API 前缀（可选）</span>' +
    '<input id="ztToapiBase" placeholder="https://example.com 或 http://ip:port" /></label>' +
    '<label class="f"><span>tdata 本地密码 passcode（可选）</span>' +
    '<input id="ztToapiTdataPass" type="password" placeholder="桌面端设了本地密码才填" autocomplete="off" /></label>' +
    '<div class="row" style="gap:8px">' +
    '<button class="sm write" onclick="ztToapi()">开始转 API</button></div>' +
    '<div id="ztToapiOut" style="margin:6px 0"></div>' +

    '<h4 style="margin:14px 0 4px">五、格式互转</h4>' +
    '<div class="muted" style="margin-bottom:4px">对齐机器人 /convert：Session → Tdata（需 opentele）或 Tdata → Session（内置解析）。</div>' +
    '<label class="f"><span>方向</span>' +
    '<select id="ztCvtMode">' +
    '<option value="session_to_tdata">Session → Tdata</option>' +
    '<option value="tdata_to_session">Tdata → Session</option>' +
    '</select></label>' +
    '<label class="f"><span>号包 zip</span>' +
    '<input type="file" id="ztCvtFile" accept=".zip" /></label>' +
    '<label class="f"><span>tdata 本地密码（仅 Tdata→Session 可选）</span>' +
    '<input id="ztCvtPass" type="password" placeholder="有 passcode 才填" autocomplete="off" /></label>' +
    '<div class="row" style="gap:8px">' +
    '<button class="sm write" onclick="ztConvert()">开始互转</button></div>' +
    '<div id="ztCvtOut" style="margin:6px 0 12px"></div>' +

    '<h4 style="margin:10px 0 4px">六、Passkey</h4>' +
    '<div class="muted" style="margin-bottom:4px">对齐机器人 /passkey 创建方向：上传 session 号包，初始化 Passkey 注册并下载凭证 JSON。</div>' +
    '<label class="f"><span>session 号包 zip</span>' +
    '<input type="file" id="ztPkFile" accept=".zip" /></label>' +
    '<div class="row" style="gap:8px">' +
    '<button class="sm write" onclick="ztPasskey()">创建 Passkey 凭证包</button></div>' +
    '<div id="ztPkOut" style="margin:6px 0"></div>';

  if (readonly) box.querySelectorAll('.write').forEach(function (b) { b.disabled = true; });
}
/* ---------- ZIP 工具：动作 ---------- */

function ztBtns(job, url, filename) {
  return '<div class="row" style="gap:8px;margin-top:6px">' +
    '<button class="sm" onclick="ztDownload(\'' + url + '\',\'' + filename + '\')">' +
    '下载 ' + filename + '</button>' +
    '<button class="sm" onclick="ztCleanup(\'' + job + '\')">用完删除</button></div>';
}

async function ztCleanup(job) {
  try {
    await ztFetch('/api/tools/unpack/' + encodeURIComponent(job), {method: 'DELETE'});
    toast('服务器上的临时文件已删干净', 'ok');
    renderZt();
  } catch (e) {
    toast(e.message, 'err');
  }
}

async function ztAnalyze() {
  const fs = ztFile('#ztUpFile');
  if (!fs) { toast('先选个号包', 'err'); return; }
  ztSay('#ztUnpackOut', '<span class="muted">分析中…</span>');
  try {
    const r = await ztFetch('/api/tools/unpack/analyze', {method: 'POST', body: fs[0]});
    ztSay('#ztUnpackOut', '<span class="ok">包里共 <b>' + r.total +
      '</b> 个号</span><span class="muted">（还没拆，只是看一眼）</span>');
  } catch (e) {
    ztSay('#ztUnpackOut', '<span class="err">' + esc(e.message) + '</span>');
  }
}

async function ztUnpack() {
  const fs = ztFile('#ztUpFile');
  if (!fs) { toast('先选个号包', 'err'); return; }
  const fmt = String(($('#ztFmt') || {}).value || '').trim();
  if (!fmt) { toast('填下拆分格式，比如 -9- 或 5,5,5', 'err'); return; }
  ztSay('#ztUnpackOut', '<span class="muted">拆包中，包大的话会慢一点…</span>');
  try {
    // ztConc 返回的是 ?workers=，这里已经有 ?fmt= 了，换成 &
    const r = await ztFetch('/api/tools/unpack?fmt=' + encodeURIComponent(fmt) +
      ztConc('#ztUpConc').replace('?', '&'), {method: 'POST', body: fs[0]});
    ZT_JOB = r.job;
    let h = '<span class="ok">' + r.total + ' 个号 → ' + r.pack_count + ' 个包</span>' +
      '<span class="muted">（并发度 ' + r.workers + '）</span>';
    h += '<div class="row" style="gap:8px;flex-wrap:wrap;margin-top:6px">';
    (r.packs || []).forEach(function (pk) {
      h += '<button class="sm" onclick="ztDownload(\'' + pk.url + '\',\'' +
        pk.filename + '\')">' + esc(pk.filename) + ' (' + pk.size + ' 个)</button>';
    });
    h += '</div><div class="row" style="margin-top:6px">' +
      '<button class="sm" onclick="ztCleanup(\'' + r.job + '\')">用完删除</button></div>';
    ztSay('#ztUnpackOut', h);
  } catch (e) {
    ztSay('#ztUnpackOut', '<span class="err">' + esc(e.message) + '</span>');
  }
}

async function ztMergeAdd() {
  const fs = ztFile('#ztMergeFiles');
  if (!fs) { toast('先选号包', 'err'); return; }
  ztSay('#ztMergeOut', '<span class="muted">上传中…</span>');
  try {
    // 一次一个地传，攒到同一个 job 里
    for (let i = 0; i < fs.length; i++) {
      const q = ZT_MERGE_JOB ? ('?job=' + encodeURIComponent(ZT_MERGE_JOB)) : '';
      const r = await ztFetch('/api/tools/merge/add' + q, {method: 'POST', body: fs[i]});
      ZT_MERGE_JOB = r.job;
      ZT_MERGE_N = r.count;
      ztSay('#ztMergeOut', '<span class="muted">已传 ' + r.count + ' 个…</span>');
    }
    ztSay('#ztMergeN', '已攒 ' + ZT_MERGE_N + ' 个包');
    ztSay('#ztMergeOut', '<span class="ok">已攒 ' + ZT_MERGE_N +
      ' 个包</span><span class="muted">，还可以接着选文件再添加，攒齐了再点开始合并</span>');
    const el = $('#ztMergeFiles');
    if (el) el.value = '';
  } catch (e) {
    ztSay('#ztMergeOut', '<span class="err">' + esc(e.message) + '</span>');
  }
}

async function ztMergeRun() {
  if (!ZT_MERGE_JOB) { toast('先把要合并的包添加进来', 'err'); return; }
  ztSay('#ztMergeOut', '<span class="muted">合并中…</span>');
  try {
    const r = await ztFetch('/api/tools/merge/' + encodeURIComponent(ZT_MERGE_JOB) +
      '/run' + ztConc('#ztMergeConc'), {method: 'POST'});
    let h = '<span class="ok">' + r.sources + ' 个包 → ' + r.total + ' 个号</span>';
    if (r.renamed) h += '<span class="muted">，其中 ' + r.renamed +
      ' 个重名已自动改名（没丢号）</span>';
    if (r.skipped && r.skipped.length) h += '<div class="err">有 ' + r.skipped.length +
      ' 个包没能读：' + esc(r.skipped.map(function (s) {
        return (s.src || '?') + ' ' + (s.reason || '');
      }).join('；')) + '</div>';
    h += '<div class="muted">并发度 ' + r.workers + '，源包已删</div>';
    h += ztBtns(r.job, r.url, 'merged.zip');
    ztSay('#ztMergeOut', h);
    ZT_MERGE_JOB = null; ZT_MERGE_N = 0;
    ztSay('#ztMergeN', '已攒 0 个包');
  } catch (e) {
    ztSay('#ztMergeOut', '<span class="err">' + esc(e.message) + '</span>');
  }
}

async function ztRegtime() {
  const fs = ztFile('#ztRegFile');
  if (!fs) { toast('先选个号包', 'err'); return; }
  ztSay('#ztRegOut', '<span class="muted">分类中…</span>');
  try {
    const r = await ztFetch('/api/tools/regtime' + ztConc('#ztRegConc'),
      {method: 'POST', body: fs[0]});
    let h = '<span class="ok">' + r.total + ' 个号，解出 ' + r.resolved + ' 个</span>';
    if (r.unknown) h += '<span class="muted">，' + r.unknown +
      ' 个日期不明（已归到 unknown 目录，没丢）</span>';
    h += '<div class="muted">' + (r.online ? '本次联网查了' : '全程离线，未外发任何数据') +
      '，并发度 ' + r.workers + '</div>';
    const g = r.groups || {};
    const keys = Object.keys(g).sort();
    if (keys.length) {
      h += '<div class="row" style="gap:6px;flex-wrap:wrap;margin-top:4px">';
      keys.forEach(function (k) {
        h += '<span class="pill">' + esc(k) + ' × ' + g[k] + '</span>';
      });
      h += '</div>';
    }
    h += ztBtns(r.job, r.url, 'regtime.zip');
    ztSay('#ztRegOut', h);
  } catch (e) {
    ztSay('#ztRegOut', '<span class="err">' + esc(e.message) + '</span>');
  }
}

async function ztToapi() {
  const fs = ztFile('#ztToapiFile');
  if (!fs) { toast('先选个号包', 'err'); return; }
  const mode = ($('#ztToapiMode') && $('#ztToapiMode').value) || 'from_json';
  const password = ($('#ztToapiPass') && $('#ztToapiPass').value) || '';
  const apiBase = ($('#ztToapiBase') && $('#ztToapiBase').value) || '';
  const tdataPass = ($('#ztToapiTdataPass') && $('#ztToapiTdataPass').value) || '';
  if (mode === 'manual' && !String(password).trim()) {
    toast('manual 模式请填写统一 2FA', 'err'); return;
  }
  ztSay('#ztToapiOut', '<span class="muted">转换中…</span>');
  try {
    let q = '?mode=' + encodeURIComponent(mode);
    if (password) q += '&password=' + encodeURIComponent(password);
    if (apiBase) q += '&api_base=' + encodeURIComponent(apiBase);
    if (tdataPass) q += '&tdata_passcode=' + encodeURIComponent(tdataPass);
    const r = await ztFetch('/api/tools/toapi' + q, {method: 'POST', body: fs[0]});
    let h = '<span class="ok">成功 ' + r.total + ' 个</span>';
    if (r.failed) h += '<span class="muted">，失败 ' + r.failed + '</span>';
    h += '<div class="muted">取码前缀 ' + esc(r.api_base || '') + ' · 模式 ' + esc(r.mode || mode) + '</div>';
    if (r.errors && r.errors.length) {
      h += '<div class="err" style="font-size:12px">' + esc(r.errors.map(function (e) {
        return (e.source || '?') + ': ' + (e.error || '');
      }).join('；')) + '</div>';
    }
    h += ztBtns(r.job, r.url, 'toapi.zip');
    ztSay('#ztToapiOut', h);
  } catch (e) {
    ztSay('#ztToapiOut', '<span class="err">' + esc(e.message) + '</span>');
  }
}

async function ztConvert() {
  const files = ztFile('ztCvtFile');
  if (!files || !files.length) { toast('请选择号包 zip', 'err'); return; }
  const mode = ($('#ztCvtMode') && $('#ztCvtMode').value) || 'session_to_tdata';
  const pass = ($('#ztCvtPass') && $('#ztCvtPass').value) || '';
  const q = new URLSearchParams({mode: mode});
  if (pass) q.set('password', pass);
  ztSay('ztCvtOut', '<span class="muted">互转中…</span>');
  try {
    progress(true);
    const r = await ztFetch('/api/tools/convert?' + q.toString(), {
      method: 'POST', body: files[0],
      headers: Object.assign({'X-Filename': files[0].name}, ztAuth()),
    });
    const html = '完成：成功 ' + (r.succeeded || 0) + ' / 共 ' + (r.total || 0) +
      (r.url ? ztBtns(r.job, r.url, r.filename || 'convert.zip') : '');
    ztSay('ztCvtOut', html);
    out(r, true);
    toast('格式互转完成', 'ok');
  } catch (e) {
    const msg = String(e.message || e);
    ztSay('ztCvtOut', '<span style="color:#e88">' + esc(msg) + '</span>');
    if (/opentele/i.test(msg)) {
      const go = await uiConfirm({
        title: '需要 opentele',
        message: msg + '\\n\\n是否一键安装 opentele？',
        okText: '一键安装',
      });
      if (go) {
        try {
          const r = await api('/api/system/install-opentele', {method:'POST', body:'{}'});
          toast(r.message || '已安装', 'ok');
        } catch (e2) { toast('安装失败：' + e2.message, 'err'); }
      }
    } else toast('互转失败：' + msg, 'err');
  } finally { progress(false); }
}

async function ztPasskey() {
  const files = ztFile('ztPkFile');
  if (!files || !files.length) { toast('请选择 session 号包 zip', 'err'); return; }
  ztSay('ztPkOut', '<span class="muted">处理中…</span>');
  try {
    progress(true);
    const r = await ztFetch('/api/tools/passkey?mode=create', {
      method: 'POST', body: files[0],
      headers: Object.assign({'X-Filename': files[0].name}, ztAuth()),
    });
    ztSay('ztPkOut', '完成：成功 ' + (r.succeeded || 0) + ' / 共 ' + (r.total || 0) +
      (r.url ? ztBtns(r.job, r.url, r.filename || 'passkey.zip') : ''));
    out(r, true);
    toast('Passkey 凭证包已生成', 'ok');
  } catch (e) {
    ztSay('ztPkOut', '<span style="color:#e88">' + esc(String(e.message || e)) + '</span>');
    toast('Passkey 失败：' + e.message, 'err');
  } finally { progress(false); }
}




/* ---------- AI 助手面板 ---------- */
let AI_CFG = null;
let AI_MSGS = []; // {role, content}
const AI_HIST_KEY = 'tam_ai_chat_history_v1';
const AI_HIST_MAX = 80;

function aiSaveHistory() {
  try {
    const slim = (AI_MSGS || []).slice(-AI_HIST_MAX).map(function (m) {
      return {role: m.role, content: String(m.content || '').slice(0, 12000)};
    });
    localStorage.setItem(AI_HIST_KEY, JSON.stringify(slim));
  } catch (_) {}
}

function aiLoadHistory() {
  try {
    const raw = localStorage.getItem(AI_HIST_KEY);
    if (!raw) return;
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr) || !arr.length) return;
    AI_MSGS = arr.filter(function (m) {
      return m && (m.role === 'user' || m.role === 'assistant') && m.content;
    }).slice(-AI_HIST_MAX);
  } catch (_) {
    AI_MSGS = [];
  }
}

function aiRenderHistory() {
  const box = $('#aiMsgs');
  if (!box) return;
  box.innerHTML = '';
  if (!AI_MSGS.length) {
    box.innerHTML = '<div class="ai-row sys"><div class="ai-bubble sys">暂无对话。在「配置与权限」启用 AI 后即可提问。历史保存在本机浏览器。</div></div>';
    return;
  }
  AI_MSGS.forEach(function (m) {
    aiAppendBubble(m.role === 'user' ? 'user' : 'bot', m.content);
  });
  const tip = document.createElement('div');
  tip.className = 'ai-row sys';
  tip.innerHTML = '<div class="ai-bubble sys">已恢复本机历史 ' + AI_MSGS.length + ' 条（仅此浏览器）</div>';
  box.appendChild(tip);
  box.scrollTop = box.scrollHeight;
}

async function aiClearHistory() {
  if (!confirm('清空当前 AI 对话历史？（仅本机浏览器）')) return;
  AI_MSGS = [];
  try { localStorage.removeItem(AI_HIST_KEY); } catch (_) {}
  aiRenderHistory();
  toast('已清空对话历史', 'ok');
}




function aiProviderChange() {
  const sel = $('#aiProvider');
  const hint = $('#aiProviderHint');
  const base = $('#aiBase');
  if (!sel || !AI_CFG || !AI_CFG.providers) return;
  const meta = AI_CFG.providers[sel.value] || {};
  if (hint) hint.textContent = meta.hint || '';
  // 仅当 base 为空或仍是某个默认值时自动填
  if (base && meta.default_base) {
    const cur = (base.value || '').trim();
    const defaults = Object.keys(AI_CFG.providers).map(function (k) {
      return (AI_CFG.providers[k] && AI_CFG.providers[k].default_base) || '';
    });
    if (!cur || defaults.indexOf(cur) >= 0) base.value = meta.default_base;
  }
}

function aiApplyPromptPreset() {
  const sel = $('#aiPromptPreset');
  const ta = $('#aiSys');
  if (!sel || !ta || !AI_CFG || !AI_CFG.prompt_presets) return;
  const k = sel.value;
  if (k === '_custom') return;
  const item = AI_CFG.prompt_presets[k];
  if (item && item.text) ta.value = item.text;
}

function initAiFabDrag() {
  const fab = $('#aiFab');
  if (!fab || fab._dragBound) return;
  fab._dragBound = true;
  const KEY = 'tam_ai_fab_pos';
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (saved && typeof saved.left === 'number' && typeof saved.top === 'number') {
      fab.style.left = saved.left + 'px';
      fab.style.top = saved.top + 'px';
      fab.style.right = 'auto';
      fab.style.bottom = 'auto';
    }
  } catch (_) {}

  let dragging = false, moved = false, sx = 0, sy = 0, ox = 0, oy = 0;

  function point(ev) {
    if (ev.touches && ev.touches[0]) return {x: ev.touches[0].clientX, y: ev.touches[0].clientY};
    return {x: ev.clientX, y: ev.clientY};
  }
  function onStart(ev) {
    const p = point(ev);
    const rect = fab.getBoundingClientRect();
    dragging = true;
    moved = false;
    sx = p.x; sy = p.y;
    ox = rect.left; oy = rect.top;
    fab.classList.add('dragging');
    ev.preventDefault();
  }
  function onMove(ev) {
    if (!dragging) return;
    const p = point(ev);
    const dx = p.x - sx, dy = p.y - sy;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
    let left = ox + dx, top = oy + dy;
    const maxL = window.innerWidth - fab.offsetWidth - 4;
    const maxT = window.innerHeight - fab.offsetHeight - 4;
    left = Math.max(4, Math.min(left, maxL));
    top = Math.max(4, Math.min(top, maxT));
    fab.style.left = left + 'px';
    fab.style.top = top + 'px';
    fab.style.right = 'auto';
    fab.style.bottom = 'auto';
    ev.preventDefault();
  }
  function onEnd(ev) {
    if (!dragging) return;
    dragging = false;
    fab.classList.remove('dragging');
    try {
      localStorage.setItem(KEY, JSON.stringify({
        left: parseFloat(fab.style.left) || 0,
        top: parseFloat(fab.style.top) || 0,
      }));
    } catch (_) {}
    if (!moved) openAiPanel();
  }
  fab.addEventListener('mousedown', onStart);
  fab.addEventListener('touchstart', onStart, {passive: false});
  window.addEventListener('mousemove', onMove, {passive: false});
  window.addEventListener('touchmove', onMove, {passive: false});
  window.addEventListener('mouseup', onEnd);
  window.addEventListener('touchend', onEnd);
}
document.addEventListener('DOMContentLoaded', initAiFabDrag);
if (document.readyState !== 'loading') try { initAiFabDrag();
try { initAiPanelResize(); } catch (_) {} } catch (_) {}



function initAiPanelResize() {
  const panel = $('#aiPanel');
  const handle = $('#aiResize');
  if (!panel || !handle || handle._bound) return;
  handle._bound = true;
  const KEY = 'tam_ai_panel_width';
  const MIN = 320;
  const MAX_RATIO = 0.92;

  function maxW() { return Math.floor(window.innerWidth * MAX_RATIO); }

  function applyWidth(w) {
    w = Math.max(MIN, Math.min(maxW(), Math.round(w)));
    panel.style.width = w + 'px';
    try { localStorage.setItem(KEY, String(w)); } catch (_) {}
    return w;
  }

  function restore() {
    try {
      const n = parseInt(localStorage.getItem(KEY) || '', 10);
      if (n >= MIN) applyWidth(n);
    } catch (_) {}
  }
  restore();

  let dragging = false, startX = 0, startW = 0;
  function onDown(ev) {
    const p = ev.touches ? ev.touches[0] : ev;
    dragging = true;
    startX = p.clientX;
    startW = panel.getBoundingClientRect().width;
    panel.classList.add('resizing');
    ev.preventDefault();
  }
  function onMove(ev) {
    if (!dragging) return;
    const p = ev.touches ? ev.touches[0] : ev;
    // 面板在右侧：向左拖 = 变宽
    const dx = startX - p.clientX;
    applyWidth(startW + dx);
    ev.preventDefault();
  }
  function onUp() {
    if (!dragging) return;
    dragging = false;
    panel.classList.remove('resizing');
  }
  handle.addEventListener('mousedown', onDown);
  handle.addEventListener('touchstart', onDown, {passive: false});
  window.addEventListener('mousemove', onMove);
  window.addEventListener('touchmove', onMove, {passive: false});
  window.addEventListener('mouseup', onUp);
  window.addEventListener('touchend', onUp);
  window.addEventListener('resize', function () {
    const cur = panel.getBoundingClientRect().width;
    if (cur > maxW()) applyWidth(maxW());
  });
}

function openAiPanel() {
  const p = $('#aiPanel');
  if (!p) return;
  p.classList.add('on');
  p.setAttribute('aria-hidden', 'false');
  document.body.classList.add('modal-open');
  loadAiConfig();
  try { bindAiInput(); } catch (_) {}
}
function closeAiPanel() {
  const p = $('#aiPanel');
  if (p) {
    p.classList.remove('on');
    p.setAttribute('aria-hidden', 'true');
  }
  // 不关其他 modal 时仅当没有 modal 才解锁 body
  if (!document.querySelector('.modal.on')) document.body.classList.remove('modal-open');
}
function aiShowTab(name) {
  const chat = name === 'chat';
  $('#aiTabChat').classList.toggle('on', chat);
  $('#aiTabCfg').classList.toggle('on', !chat);
  $('#aiPaneChat').classList.toggle('on', chat);
  $('#aiPaneCfg').classList.toggle('on', !chat);
  if (!chat) renderAiConfigForm();
}

async function loadAiConfig() {
  try {
    AI_CFG = await api('/api/ai/config');
    const st = $('#aiStatus');
    if (st) {
      st.textContent = AI_CFG.enabled ? ('已启用 · ' + (AI_CFG.preset || '')) : '未启用';
      st.className = 'pill' + (AI_CFG.enabled ? ' s-ok' : '');
    }
    if ($('#aiPaneCfg') && $('#aiPaneCfg').classList.contains('on')) renderAiConfigForm();
  } catch (e) {
    toast('加载 AI 配置失败：' + e.message, 'err');
  }
}

function renderAiConfigForm() {
  const box = $('#aiCfgForm');
  if (!box || !AI_CFG) return;
  const c = AI_CFG;
  const presets = c.presets || {};
  const presetOpts = Object.keys(presets).map(function (k) {
    return '<option value="' + esc(k) + '"' + (c.preset === k ? ' selected' : '') + '>' +
      esc(presets[k] || k) + '</option>';
  }).join('');
  const catalog = c.catalog || [];
  const tools = c.tools || {};
  let enabledCount = 0;
  catalog.forEach(function (t) { if (tools[t.name]) enabledCount++; });

  let perm = '';
  let last = '';
  catalog.forEach(function (t) {
    if (t.danger !== last) {
      last = t.danger;
      const title = last === 'read' ? '只读工具' : (last === 'write' ? '写入工具' : '危险工具（对外可见）');
      perm += '<div class="ai-perm-group">' + title + '</div>';
    }
    const on = tools[t.name] ? ' checked' : '';
    perm += '<div class="ai-perm-row">' +
      '<input type="checkbox" id="ai_t_' + esc(t.name) + '"' + on +
      ' onchange="aiUpdatePermCount()" />' +
      '<label for="ai_t_' + esc(t.name) + '"><code>' + esc(t.name) + '</code>' +
      '<span class="ai-badge d-' + esc(t.danger) + '">' + esc(t.danger) + '</span>' +
      '<span class="ai-perm-desc">' + esc(t.description || '') + '</span></label></div>';
  });

  const keyHint = c.api_key_set
    ? ('已保存 ' + esc(c.api_key_masked || '****') + '，留空则保持不变')
    : '尚未保存 Key';

  box.innerHTML =
    '<div class="ai-card">' +
      '<div class="ai-switch-row">' +
        '<div><div class="ai-switch-label">启用 AI 助手</div>' +
        '<div class="ai-switch-sub">关闭并保存后对话无法发送；权限与 Key 仍保留</div></div>' +
        '<label class="row" style="gap:6px;margin:0"><input type="checkbox" id="aiEnabled"' +
        (c.enabled ? ' checked' : '') + ' /></label>' +
      '</div>' +
    '</div>' +

    '<div class="ai-card">' +
      '<h4><span class="ai-step">1</span>上游与密钥</h4>' +
      '<label class="f"><span>接口格式</span><select id="aiProvider" onchange="aiProviderChange()">' +
      Object.keys(c.providers || {openai_compatible:{label:'OpenAI 兼容'}}).map(function (k) {
        const lab = (c.providers[k] && c.providers[k].label) || k;
        return '<option value="' + esc(k) + '"' +
          ((c.provider || 'openai_compatible') === k ? ' selected' : '') + '>' + esc(lab) + '</option>';
      }).join('') + '</select></label>' +
      '<div class="ai-hint" id="aiProviderHint">选择与你的 API 文档一致的格式</div>' +
      '<label class="f"><span>API Base URL</span>' +
      '<input id="aiBase" value="' + esc(c.base_url || '') + '" placeholder="https://api.openai.com/v1" /></label>' +
      '<label class="f"><span>API Key · ' + keyHint + '</span>' +
      '<input id="aiKey" type="password" placeholder="粘贴密钥，不会回显明文" autocomplete="off" /></label>' +
      '<label class="row muted" style="gap:6px;margin:0 0 8px;font-size:12px">' +
      '<input type="checkbox" id="aiClearKey" /> 清除已保存的 Key</label>' +
      '<label class="f"><span>模型名</span>' +
      '<input id="aiModel" value="' + esc(c.model || '') + '" placeholder="gpt-4o-mini / claude-… / gemini-…" /></label>' +
    '</div>' +

    '<div class="ai-card">' +
      '<h4><span class="ai-step">2</span>上下文与生成</h4>' +
      '<label class="f"><span>提示词预设</span><select id="aiPromptPreset" onchange="aiApplyPromptPreset()">' +
      Object.keys(c.prompt_presets || {}).map(function (k) {
        const lab = (c.prompt_presets[k] && c.prompt_presets[k].label) || k;
        return '<option value="' + esc(k) + '"' + (c.prompt_preset === k ? ' selected' : '') + '>' + esc(lab) + '</option>';
      }).join('') +
      '<option value="_custom"' + ((!c.prompt_preset || c.prompt_preset === '_custom') ? ' selected' : '') + '>自定义</option>' +
      '</select></label>' +
      '<label class="f"><span>系统提示</span><textarea id="aiSys" rows="3">' + esc(c.system_prompt || '') + '</textarea></label>' +
      '<div class="ai-grid2">' +
        '<label class="f"><span>工具轮次</span>' +
        '<input id="aiRounds" type="number" min="1" max="12" value="' + esc(String(c.max_tool_rounds || 6)) + '" /></label>' +
        '<label class="f"><span>温度</span>' +
        '<input id="aiTemp" type="number" step="0.1" min="0" max="2" value="' + esc(String(c.temperature ?? 0.2)) + '" /></label>' +
      '</div>' +
      '<div style="margin:10px 0 6px;padding-top:8px;border-top:1px solid var(--border)">' +
        '<div style="font-size:12.5px;font-weight:650;margin-bottom:8px">上下文上限（可自定义）</div>' +
        '<label class="row" style="gap:8px;margin:0 0 8px;font-size:12.5px">' +
        '<input type="checkbox" id="aiAutoCompress"' + (c.auto_compress !== false ? ' checked' : '') + ' />' +
        '<span>超出上限时自动压缩更早对话</span></label>' +
        '<div class="ai-grid2">' +
          '<label class="f"><span>保留最近消息条数</span>' +
          '<input id="aiKeepRecent" type="number" min="2" max="80" value="' + esc(String(c.context_keep_recent || 8)) + '" /></label>' +
          '<label class="f"><span>字符上限（约）</span>' +
          '<input id="aiMaxChars" type="number" min="2000" max="200000" step="1000" value="' + esc(String(c.context_max_chars || 14000)) + '" /></label>' +
        '</div>' +
        '<div class="ai-hint">默认 14000 字符。可按模型上下文改大/改小；保存后立即生效。压缩只影响发给模型的内容，界面仍可见完整气泡。</div>' +
      '</div>' +
    '</div>' +

    '<div class="ai-card">' +
      '<h4><span class="ai-step">3</span>权限与范围</h4>' +
      '<label class="f"><span>权限预设</span><select id="aiPreset">' + presetOpts + '</select></label>' +
      '<div class="ai-perm-toolbar">' +
        '<button type="button" class="sm write" onclick="applyAiPresetOnly()">套用预设到勾选</button>' +
        '<button type="button" class="sm" onclick="aiPermSetAll(true)">全选</button>' +
        '<button type="button" class="sm" onclick="aiPermSetAll(false)">全不选</button>' +
        '<button type="button" class="sm" onclick="aiPermSetDanger(\'read\')">仅只读</button>' +
      '</div>' +
      '<div class="ai-hint">已开启 <strong id="aiPermCount">' + enabledCount + '</strong> / ' + catalog.length +
      ' 项工具。改勾选后点「保存」生效；登录/导入导出 session 永不开放。</div>' +
      '<div class="ai-perm-list">' + perm + '</div>' +
      '<label class="f" style="margin-top:12px"><span>允许的账号 ID（逗号分隔，空=全部）</span>' +
      '<input id="aiAccounts" value="' + esc((c.allow_account_ids || []).join(',')) + '" placeholder="例如 1,2,5" /></label>' +
      '<label class="row" style="gap:8px;margin:4px 0 0;font-size:12.5px">' +
      '<input type="checkbox" id="aiReqConfirm"' + (c.require_confirm_destructive !== false ? ' checked' : '') + ' />' +
      '<span>危险工具(注销/退出/删会话)执行前需确认</span></label>' +
      '<div class="ai-hint" style="color:#d32f2f;font-size:11.5px;margin:2px 0 6px 24px;line-height:1.4">' +
      '⚠️ <strong>关闭此项后,AI 可直接执行注销、删号等不可逆操作,无需人工确认。</strong>请仅在完全信任模型判断时关闭。</div>' +
      '<label class="row" style="gap:8px;margin:4px 0 0;font-size:12.5px">' +
      '<input type="checkbox" id="aiReqWrite"' + (c.confirm_write !== false ? ' checked' : '') + ' />' +
      '<span>改动类工具(改二验/资料/隐私/发消息等)执行前需确认</span></label>' +
      '<div class="ai-hint" style="color:#d32f2f;font-size:11.5px;margin:2px 0 0 24px;line-height:1.4">' +
      '⚠️ <strong>关闭此项后,AI 可直接修改账号设置、发送消息,无需人工确认。</strong>建议保持开启。</div>' +
    '</div>' +

    '<div class="ai-actions">' +
      '<button class="primary write" onclick="saveAiConfig()">保存配置</button>' +
      '<button class="sm" onclick="loadAiConfig()">重新加载</button>' +
    '</div>' +
    '<p class="ai-footnote">配置保存在服务端数据库。支持 OpenAI 兼容、Anthropic Messages、Gemini、Azure OpenAI。</p>';
  try { aiProviderChange(); } catch (_) {}
  try { aiUpdatePermCount(); } catch (_) {}
}

function aiUpdatePermCount() {
  const el = $('#aiPermCount');
  if (!el || !AI_CFG) return;
  let n = 0;
  (AI_CFG.catalog || []).forEach(function (t) {
    const cb = $('#ai_t_' + t.name);
    if (cb && cb.checked) n++;
  });
  el.textContent = String(n);
}

function aiPermSetAll(on) {
  (AI_CFG && AI_CFG.catalog || []).forEach(function (t) {
    const cb = $('#ai_t_' + t.name);
    if (cb) cb.checked = !!on;
  });
  aiUpdatePermCount();
}

function aiPermSetDanger(level) {
  (AI_CFG && AI_CFG.catalog || []).forEach(function (t) {
    const cb = $('#ai_t_' + t.name);
    if (cb) cb.checked = (t.danger === level);
  });
  aiUpdatePermCount();
}

function aiPresetChange() {
  /* 仅 UI 提示；真正应用在保存时传 preset 且不提交 tools，或提交全部勾选状态为 custom */
}

function collectAiConfigBody() {
  // 表单未渲染时不要把 enabled 误写成 false
  const formReady = !!($('#aiEnabled'));
  const tools = {};
  const catalog = (AI_CFG && AI_CFG.catalog) || [];
  catalog.forEach(function (t) {
    const el = $('#ai_t_' + t.name);
    if (el) tools[t.name] = !!el.checked;
    else if (AI_CFG && AI_CFG.tools) tools[t.name] = !!AI_CFG.tools[t.name];
  });
  const accRaw = ($('#aiAccounts') && $('#aiAccounts').value || '').trim();
  const allow = accRaw ? accRaw.split(/[,，\s]+/).map(function (x) { return parseInt(x, 10); }).filter(function (n) { return n > 0; }) : [];
  const prev = AI_CFG || {};
  const body = {
    enabled: formReady ? !!$('#aiEnabled').checked : !!prev.enabled,
    provider: ($('#aiProvider') && $('#aiProvider').value) || prev.provider || 'openai_compatible',
    base_url: ($('#aiBase') && $('#aiBase').value || '').trim() || (prev.base_url || ''),
    model: ($('#aiModel') && $('#aiModel').value || '').trim() || (prev.model || ''),
    system_prompt: ($('#aiSys') && $('#aiSys').value != null) ? $('#aiSys').value : (prev.system_prompt || ''),
    prompt_preset: (function () {
      const v = ($('#aiPromptPreset') && $('#aiPromptPreset').value) || '_custom';
      return v === '_custom' ? '_custom' : v;
    })(),
    max_tool_rounds: parseInt(($('#aiRounds') && $('#aiRounds').value) || prev.max_tool_rounds || '6', 10),
    temperature: parseFloat(($('#aiTemp') && $('#aiTemp').value) || prev.temperature || '0.2'),
    auto_compress: $('#aiAutoCompress') ? !!$('#aiAutoCompress').checked : (prev.auto_compress !== false),
    context_keep_recent: parseInt(($('#aiKeepRecent') && $('#aiKeepRecent').value) || prev.context_keep_recent || '8', 10),
    context_max_chars: parseInt(($('#aiMaxChars') && $('#aiMaxChars').value) || prev.context_max_chars || '14000', 10),
    allow_account_ids: allow.length ? allow : (prev.allow_account_ids || []),
    require_confirm_destructive: $('#aiReqConfirm') ? !!$('#aiReqConfirm').checked : (prev.require_confirm_destructive !== false),
    confirm_write: $('#aiReqWrite') ? !!$('#aiReqWrite').checked : (prev.confirm_write !== false),
    tools: tools,
    // 勾选状态始终以界面为准 → custom；预设一键套用走 applyAiPresetOnly
    preset: 'custom',
  };
  const key = ($('#aiKey') && $('#aiKey').value || '').trim();
  if (key) body.api_key = key;
  if ($('#aiClearKey') && $('#aiClearKey').checked) body.clear_api_key = true;
  return body;
}

async function saveAiConfig() {
  try {
    // 若配置表单尚未渲染（例如还在对话页），先渲染再采集，避免 enabled 被误写成 false
    if (!$('#aiEnabled')) {
      try { renderAiConfigForm(); } catch (_) {}
    }
    if (!$('#aiEnabled')) {
      toast('请先打开「配置」页再保存', 'err');
      return;
    }
    const body = collectAiConfigBody();
    // 始终提交当前勾选 + enabled，所见即所得
    const res = await api('/api/ai/config', {
      method: 'PUT',
      body: JSON.stringify(body),
    });
    AI_CFG = res;
    // 以服务端回写为准，再兜底本地 body（防止字段丢失）
    if (typeof AI_CFG.enabled === 'undefined') AI_CFG.enabled = body.enabled;
    const on = !!AI_CFG.enabled;
    const st = $('#aiStatus');
    if (st) {
      st.textContent = on ? ('已启用 · ' + (AI_CFG.preset || 'custom')) : '未启用';
      st.className = 'pill' + (on ? ' s-ok' : '');
    }
    toast(on ? 'AI 配置已保存（已启用）' : 'AI 配置已保存（已关闭助手）', 'ok');
    try { renderAiConfigForm(); } catch (e2) {
      console.warn('renderAiConfigForm', e2);
    }
  } catch (e) {
    toast('保存失败：' + e.message, 'err');
  }
}

async function applyAiPresetOnly() {
  const sel = ($('#aiPreset') && $('#aiPreset').value) || 'readonly';
  if (sel === 'custom') { toast('请选择只读/安全/标准/完整等预设', ''); return; }
  try {
    if (!$('#aiEnabled')) {
      try { renderAiConfigForm(); } catch (_) {}
    }
    const body = {
      enabled: $('#aiEnabled') ? !!$('#aiEnabled').checked : !!(AI_CFG && AI_CFG.enabled),
      preset: sel,
      provider: ($('#aiProvider') && $('#aiProvider').value) || 'openai_compatible',
      base_url: ($('#aiBase') && $('#aiBase').value || '').trim(),
      model: ($('#aiModel') && $('#aiModel').value || '').trim(),
      system_prompt: ($('#aiSys') && $('#aiSys').value) || '',
      max_tool_rounds: parseInt(($('#aiRounds') && $('#aiRounds').value) || '6', 10),
      temperature: parseFloat(($('#aiTemp') && $('#aiTemp').value) || '0.2'),
      auto_compress: $('#aiAutoCompress') ? !!$('#aiAutoCompress').checked : true,
      require_confirm_destructive: $('#aiReqConfirm') ? !!$('#aiReqConfirm').checked : true,
      confirm_write: $('#aiReqWrite') ? !!$('#aiReqWrite').checked : true,
    };
    const key = ($('#aiKey') && $('#aiKey').value || '').trim();
    if (key) body.api_key = key;
    // 不传 tools：服务端按 preset 重算全部工具开关（含新增工具）
    AI_CFG = await api('/api/ai/config', {method: 'PUT', body: JSON.stringify(body)});
    toast('已应用预设：' + sel + (AI_CFG.enabled ? '（助手已启用）' : '（助手未启用）'), 'ok');
    const st = $('#aiStatus');
    if (st) {
      st.textContent = AI_CFG.enabled ? ('已启用 · ' + (AI_CFG.preset || sel)) : '未启用';
      st.className = 'pill' + (AI_CFG.enabled ? ' s-ok' : '');
    }
    renderAiConfigForm();
  } catch (e) {
    toast('应用失败：' + e.message, 'err');
  }
}

function aiEscapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 轻量 Markdown → HTML（表格 / 代码 / 列表 / 标题 / 引用） */
function aiRenderMd(src) {
  let s = String(src == null ? '' : src).replace(/\r\n/g, '\n');
  const blocks = [];
  // fenced code
  s = s.replace(/```([\w-]*)\n?([\s\S]*?)```/g, function (_, lang, code) {
    const i = blocks.length;
    blocks.push(
      '<pre><code class="lang-' + aiEscapeHtml(lang || '') + '">' +
      aiEscapeHtml(code.replace(/\n$/, '')) + '</code></pre>'
    );
    return '\n%%BLK' + i + '%%\n';
  });
  // tables (GFM)
  s = s.replace(/(?:(?:^|\n)(?:\|.+\|(?:\n|$))+)/g, function (block) {
    const lines = block.trim().split('\n').filter(Boolean);
    if (lines.length < 2 || !/^\|?\s*:?-+:?\s*\|/.test(lines[1].replace(/\s/g, '')) &&
        !/^\|?[\s|:-]+$/.test(lines[1])) {
      // second line must be separator
      if (lines.length < 2 || lines[1].indexOf('-') < 0) return block;
    }
    function splitRow(line) {
      let t = line.trim();
      if (t.startsWith('|')) t = t.slice(1);
      if (t.endsWith('|')) t = t.slice(0, -1);
      return t.split('|').map(function (c) { return c.trim(); });
    }
    const head = splitRow(lines[0]);
    let bodyStart = 1;
    if (/^[\s|:-]+$/.test(lines[1])) bodyStart = 2;
    let html = '<table><thead><tr>';
    head.forEach(function (c) { html += '<th>' + aiInlineMd(c) + '</th>'; });
    html += '</tr></thead><tbody>';
    for (let i = bodyStart; i < lines.length; i++) {
      if (!lines[i].includes('|')) continue;
      const cells = splitRow(lines[i]);
      html += '<tr>';
      for (let j = 0; j < head.length; j++) {
        html += '<td>' + aiInlineMd(cells[j] == null ? '' : cells[j]) + '</td>';
      }
      html += '</tr>';
    }
    html += '</tbody></table>';
    const i = blocks.length;
    blocks.push(html);
    return '\n%%BLK' + i + '%%\n';
  });

  const lines = s.split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const blk = line.match(/^%%BLK(\d+)%%$/);
    if (blk) {
      out.push(blocks[parseInt(blk[1], 10)]);
      i++;
      continue;
    }
    if (/^---+$/.test(line.trim()) || /^\*\*\*+$/.test(line.trim())) {
      out.push('<hr/>');
      i++;
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.+)$/);
    if (h) {
      const n = h[1].length;
      out.push('<h' + n + '>' + aiInlineMd(h[2]) + '</h' + n + '>');
      i++;
      continue;
    }
    if (/^>\s?/.test(line)) {
      const qs = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        qs.push(lines[i].replace(/^>\s?/, ''));
        i++;
      }
      out.push('<blockquote>' + aiInlineMd(qs.join(' ')) + '</blockquote>');
      continue;
    }
    if (/^(\s*[-*+]\s+)/.test(line)) {
      const items = [];
      while (i < lines.length && /^(\s*[-*+]\s+)/.test(lines[i])) {
        items.push('<li>' + aiInlineMd(lines[i].replace(/^\s*[-*+]\s+/, '')) + '</li>');
        i++;
      }
      out.push('<ul>' + items.join('') + '</ul>');
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push('<li>' + aiInlineMd(lines[i].replace(/^\s*\d+\.\s+/, '')) + '</li>');
        i++;
      }
      out.push('<ol>' + items.join('') + '</ol>');
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const paras = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !/^%%BLK/.test(lines[i]) &&
           !/^#{1,4}\s/.test(lines[i]) && !/^>\s?/.test(lines[i]) &&
           !/^(\s*[-*+]\s+)/.test(lines[i]) && !/^\s*\d+\.\s+/.test(lines[i]) &&
           !/^---+$/.test(lines[i].trim())) {
      if (lines[i].includes('|') && i + 1 < lines.length && /-/.test(lines[i + 1] || '')) break;
      paras.push(lines[i]);
      i++;
    }
    out.push('<p>' + aiInlineMd(paras.join(' ')) + '</p>');
  }
  return out.join('') || '<p></p>';
}

function aiInlineMd(s) {
  let t = aiEscapeHtml(s);
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/(^|[^*])\*([^*]+)\*(?![*])/g, '$1<em>$2</em>');
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return t;
}

function aiAppendTrace(div, trace) {
  if (!trace || !trace.length) return;
  const t = document.createElement('div');
  t.className = 'ai-trace';
  trace.forEach(function (x) {
    const ok = x.result && x.result.ok;
    const chip = document.createElement('span');
    chip.className = 'ai-chip ' + (ok ? 'ok' : 'err');
    let label = (ok ? '✓ ' : '✗ ') + (x.tool || '');
    if (!ok && x.result && x.result.error) {
      label += ' · ' + (x.result.error.message || x.result.error.code || '');
    }
    chip.textContent = label;
    chip.title = label;
    t.appendChild(chip);
  });
  div.appendChild(t);
}

function aiAppendBubble(role, text, trace) {
  const box = $('#aiMsgs');
  if (!box) return;
  const row = document.createElement('div');
  row.className = 'ai-row ' + (role === 'user' ? 'user' : (role === 'sys' ? 'sys' : 'bot'));
  const meta = document.createElement('div');
  meta.className = 'ai-meta';
  const now = new Date();
  const ts = now.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  meta.textContent = (role === 'user' ? '你' : (role === 'sys' ? '系统' : '助手')) + ' · ' + ts;
  const div = document.createElement('div');
  div.className = 'ai-bubble ' + (role === 'user' ? 'user' : (role === 'sys' ? 'sys' : 'bot'));
  if (role === 'bot') {
    div.classList.add('ai-md');
    div.innerHTML = aiRenderMd(text || '');
  } else {
    div.textContent = text || '';
  }
  aiAppendTrace(div, trace);
  if (role !== 'sys') row.appendChild(meta);
  row.appendChild(div);
  box.appendChild(row);
  box.scrollTop = box.scrollHeight;
  return row;
}

function aiShowTyping() {
  const box = $('#aiMsgs');
  if (!box) return null;
  const row = document.createElement('div');
  row.className = 'ai-row bot';
  row.id = 'aiTyping';
  row.innerHTML = '<div class="ai-meta">助手</div><div class="ai-bubble bot typing">正在思考与调用工具…</div>';
  box.appendChild(row);
  box.scrollTop = box.scrollHeight;
  return row;
}

function aiHideTyping() {
  const el = $('#aiTyping');
  if (el) el.remove();
}


function aiCompressLocalHistory(msgs) {
  msgs = (msgs || []).slice();
  const keep = Math.max(2, Math.min(80, parseInt((AI_CFG && AI_CFG.context_keep_recent) || 8, 10) || 8));
  const maxChars = Math.max(2000, Math.min(200000, parseInt((AI_CFG && AI_CFG.context_max_chars) || 14000, 10) || 14000));
  const auto = !AI_CFG || AI_CFG.auto_compress !== false;
  let total = 0;
  msgs.forEach(function (m) { total += String(m.content || '').length; });
  if (!auto || (msgs.length <= keep + 2 && total <= maxChars)) {
    return msgs.map(function (m) {
      const c = String(m.content || '');
      if (c.length > 8000) return Object.assign({}, m, {content: c.slice(0, 8000) + '…(已截断)'});
      return m;
    });
  }
  const old = msgs.length > keep ? msgs.slice(0, -keep) : [];
  const recent = msgs.length > keep ? msgs.slice(-keep) : msgs;
  const lines = ['[本地摘要]'];
  old.forEach(function (m) {
    const role = m.role === 'user' ? '用户' : (m.role === 'assistant' ? '助手' : m.role);
    const c = String(m.content || '').replace(/\s+/g, ' ').trim().slice(0, 120);
    if (c) lines.push('- ' + role + ': ' + c);
  });
  const budget = Math.min(3000, Math.floor(maxChars / 3));
  return [
    {role: 'user', content: lines.join('\n').slice(0, budget)},
    {role: 'assistant', content: '已了解此前摘要。'},
  ].concat(recent);
}

// 工具名 → 中文说明（取自服务端 catalog; 拿不到就用原名）
function aiToolLabel(name) {
  if (!AI_CFG || !AI_CFG.catalog) return name;
  var hit = AI_CFG.catalog.filter(function (t) { return t.name === name; })[0];
  return hit ? (hit.description || name) : name;
}

// 需确认工具清单对话框；返回非空数组=approve，返回 null=取消
function aiConfirmTools(pending) {
  return new Promise(function (resolve) {
    var modal = document.createElement('div');
    modal.className = 'modal on';
    modal.id = 'mAiConfirm';
    modal.style.width = 'min(680px,92vw)';

    var rows = pending.map(function (p, i) {
      var dangerous = p.danger === 'destructive';
      var args = (typeof p.arguments === 'object' && p.arguments) ? p.arguments : {};
      var argText = JSON.stringify(args, null, 2) || '{}';
      var row = document.createElement('label');
      row.style.cssText = 'display:flex;gap:8px;align-items:flex-start;cursor:pointer;' +
        'border:1px solid ' + (dangerous ? '#f66' : '#ddd') +
        ';border-radius:6px;padding:10px 12px;margin:6px 0;background:' + (dangerous ? '#fff1f1' : '#fbfbfb');
      row.innerHTML =
        '<input type="checkbox" class="ai-cf-cb" data-i="' + i + '" checked style="margin-top:3px">' +
        '<span><strong>' + aiToolLabel(p.tool) + '</strong>' +
        (dangerous ? ' <span style="color:#e53935;font-weight:bold">· 危险操作</span>' : '') +
        (p.danger === 'write' ? ' <span style="color:#888;font-weight:normal">· 改动类</span>' : '') +
        '<br><code style="white-space:pre-wrap;word-break:break-all;font-size:11px;color:#444;display:inline-block;margin-top:4px">' +
          aiEscapeHtml(argText) + '</code></span>';
      return row;
    });

    modal.innerHTML =
      '<h3>AI 请求执行以下操作，请勾选后再执行</h3>' +
      '<div class="body" id="aiCfBody"></div>' +
      '<div class="foot"><button type="button" class="sm" id="aiCfCancel">取消全部</button>' +
      '<button type="button" class="primary write" id="aiCfOk">执行勾选项</button></div>';

    var body = document.createElement('div');
    body.style.maxHeight = '46vh'; body.style.overflowY = 'auto';
    rows.forEach(function (r) { body.appendChild(r); });
    modal.querySelector('#aiCfBody').appendChild(body);

    var close = function () {
      document.removeEventListener('keydown', onKey);
      $('#bd').classList.remove('on');
      document.body.classList.remove('modal-open');
      closeAll();
      if (modal && modal.parentNode) modal.parentNode.removeChild(modal);
    };
    var onKey = function (e) {
      if (e.key === 'Escape') {
        e.preventDefault(); e.stopPropagation();
        close(); resolve(null);       // 用户按 Esc → 放弃本轮操作
      }
    };
    document.addEventListener('keydown', onKey);
    modal.querySelector('#aiCfCancel').onclick = function () {
      var appr = rows.map(function (row, i) {
        return {tool: pending[i].tool, arguments: pending[i].arguments || {}, approved: false};
      });
      close(); resolve(appr);      // 全部取消 → 传给服务端回灌"已取消"
    };
    modal.querySelector('#aiCfOk').onclick = function () {
      var appr = rows.map(function (row, i) {
        var cb = row.querySelector('.ai-cf-cb');
        return {tool: pending[i].tool, arguments: pending[i].arguments || {}, approved: !!(cb && cb.checked)};
      });
      close(); resolve(appr);
    };
    document.body.appendChild(modal);
    $('#bd').classList.add('on');
    document.body.classList.add('modal-open');
  });
}

// 读取 NDJSON 流并逐条 callback；返回 Promise 解析为 done 事件(含 body)。
// 至少需要 fetch + ReadableStream（现代浏览器）。返回 Promise<body>。
function aiChatOnce(approve) {
  const body = JSON.stringify({messages: AI_MSGS, approve: approve || null});
  const headers = {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer ' + ($('#token').value || ''),
  };
  return fetch('/api/ai/chat?stream=1', {method: 'POST', headers, body})
    .then(function (res) {
      if (!res.ok) {
        return res.text().then(function (raw) {
          let msg = raw;
          try { const j = JSON.parse(raw); msg = j.detail || j.message || raw; } catch (_) {}
          throw new Error(String(msg).slice(0, 400));
        });
      }
      if (!res.body || !res.body.getReader) {
        // 不支持流 → 退化为 JSON
        return res.json();
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      let liveRow = null;
      let liveDiv = null;
      let usedStream = false;              // 走过流式读取路径 → 需保留实时气泡
      function liveBubble() {
        if (liveRow) return liveRow;
        liveRow = aiShowTyping();           // 复用「正在思考」行承载流式文本
        liveRow.classList.add('streaming');
        usedStream = true;
        liveDiv = liveRow.querySelector('.ai-bubble');
        if (liveDiv) { liveDiv.classList.add('ai-md'); liveDiv.innerHTML = ''; }
        return liveRow;
      }
      return new Promise(function (resolve, reject) {
        function handle(finalEvt) {
          if (liveDiv) { liveDiv.innerHTML = aiRenderMd(liveDiv.textContent || ''); }
          if (usedStream) finalEvt = Object.assign({}, finalEvt, {_streamed: true});
          resolve(finalEvt);
        }
        function pump() {
          return reader.read().then(function (chunk) {
            if (chunk.done) {
              // 流结束，可能没收到显式 done（异常截断）→ 用累积文本兜底
              if (!finalEvt) {
                const text = (liveDiv ? liveDiv.textContent : '') || '';
                finalEvt = {event: 'done', message: {role: 'assistant', content: text},
                            pending: [], trace: [], messages: AI_MSGS, context: {compressed: false}};
                handle(finalEvt);
              }
              return;
            }
            buf += decoder.decode(chunk.value || new Uint8Array(), {stream: true});
            let idx;
            while ((idx = buf.indexOf('\n')) >= 0) {
              const line = buf.slice(0, idx).trim();
              buf = buf.slice(idx + 1);
              if (!line) continue;
              let ev;
              try { ev = JSON.parse(line); } catch (_) { continue; }
              if (ev.event === 'delta' && typeof ev.text === 'string') {
                liveBubble();
                if (liveDiv) liveDiv.textContent = (liveDiv.textContent || '') + ev.text;
              } else if (ev.event === 'done') {
                finalEvt = ev;
                handle(ev);
                return;
              } else if (ev.event === 'error' || ev.event === 'final') {
                finalEvt = ev;
                handle(ev);
                return;
              }
            }
            return pump();
          }, function (err) { reject(err); });
        }
        let finalEvt = null;
        pump();
      });
    });
}

async function aiSend() {
  const input = $('#aiInput');
  const text = (input && input.value || '').trim();
  if (!text) return;
  if (!AI_CFG || !AI_CFG.enabled) {
    toast('请先在「配置与权限」中启用 AI 并保存', 'err');
    aiShowTab('cfg');
    return;
  }
  if (input) input.value = '';
  AI_MSGS.push({role: 'user', content: text});
  aiAppendBubble('user', text);
  AI_MSGS = aiCompressLocalHistory(AI_MSGS);
  aiSaveHistory();
  const btn = $('#aiSendBtn');
  if (btn) btn.disabled = true;
  let approve = null;
  try {
    while (true) {
      aiShowTyping();
      let r = await aiChatOnce(approve);   // 流式：delta 实时进入 #aiTyping，done 后 resolve
      // 用服务端回灌的完整上下文覆盖本地，保证跨阶段连续性
      if (r && r.messages && r.messages.length) {
        AI_MSGS = r.messages;
        AI_MSGS = aiCompressLocalHistory(AI_MSGS);
        aiSaveHistory();
      }
      const pending = (r && r.pending) || [];
      if (pending && pending.length) {
        aiHideTyping();                    // 确认清单前收起打字气泡
        const decision = await aiConfirmTools(pending);
        if (decision === null) break;      // 对话框异常关闭
        approve = decision;                // [] 表示取消全部 → 服务端回灌"已取消"
        continue;                          // 再 POST，服务端执行批准项并继续
      }
      // 无 pending：最终回复
      const msg = (r && r.message && r.message.content) || '（无回复）';
      AI_MSGS.push({role: 'assistant', content: msg});
      AI_MSGS = aiCompressLocalHistory(AI_MSGS);
      aiSaveHistory();
      const typing = $('#aiTyping');
      if (r && r._streamed && typing) {
        // 流式已实时渲染：把打字气泡转正为最终气泡并补 trace
        typing.classList.remove('streaming');
        const b = typing.querySelector('.ai-bubble');
        if (b) { b.classList.add('ai-md'); b.innerHTML = aiRenderMd(msg); }
        aiAppendTrace(typing.querySelector('.ai-bubble') || typing, (r && r.trace) || []);
      } else {
        aiHideTyping();
        aiAppendBubble('bot', msg, (r && r.trace) || []);
      }
      if (r && r.context && r.context.compressed) {
        aiAppendBubble('sys', '上下文已自动压缩：' + (r.context.before_chars || '?') +
          '→' + (r.context.after_chars || '?') + ' 字符');
      }
      break;
    }
  } catch (e) {
    aiHideTyping();
    aiAppendBubble('sys', '错误：' + e.message);
    toast('AI 失败：' + e.message, 'err');
  } finally {
    if (btn) btn.disabled = false;
    const input = $('#aiInput');
    if (input) input.focus();
  }
}

function bindAiInput() {
  const input = $('#aiInput');
  if (!input || input._aiBound) return;
  input._aiBound = true;
  input.placeholder = '输入消息，Enter 发送，Shift+Enter 换行';
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      aiSend();
    }
  });
}
document.addEventListener('DOMContentLoaded', bindAiInput);
if (document.readyState !== 'loading') try { bindAiInput(); } catch (_) {}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && $('#aiPanel') && $('#aiPanel').classList.contains('on')) {
    closeAiPanel();
  }
});
