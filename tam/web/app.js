

const $ = s => document.querySelector(s);
// 只有 HTML 真变了才写 DOM：自动刷新时数据没变就不重建节点，保留滚动、悬停和焦点。
function setHTML(el, html) {
  if (!el) return false;
  if (el._taoHTML === html && el.childNodes.length) return false;
  el.innerHTML = html;
  el._taoHTML = html;
  return true;
}
const DEMO = location.protocol === 'file:' || new URLSearchParams(location.search).has('demo');
let accounts = [], stats = {total:0, by_status:{}}, sel = new Set(), filter = '', loginId = null, readonly = false;
let accountsRevision = 0;
let visibleCache = null;
let filterCountsCache = null;

/* ---------- 轻量动效控制器 ----------
 * 使用浏览器原生 Web Animations API（element.animate），不再内嵌 72 KB 的 GSAP；
 * 不支持时自动降级到 CSS/无动画。
 * 只处理页面容器和 overlay，不为账号行创建 tween，避免大批量账号操作掉帧。
 */
const TAO_MOTION = (() => {
  const active = new Map(), watched = new Map();
  const media = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  let bodyObserver = null;
  let viewTicket = 0;
  const reduced = () => !!media?.matches;
  const stop = el => {
    const run = active.get(el);
    if (!run) return;
    active.delete(el);
    // Revert, rather than only killTweensOf: restore pre-animation inline styles.
    try { run.anim?.cancel(); } finally { el.classList.remove(run.marker); }
  };
  const animate = (el, overlay) => {
    if (!el) return;
    stop(el);
    if (typeof el.animate !== 'function' || reduced() || !el.isConnected || document.hidden) return;
    const marker = overlay ? 'tao-motion-overlay' : 'tao-motion-view';
    const run = {anim:null,marker};
    active.set(el,run);
    el.classList.add(marker);
    try {
      // Do not touch centering/layout transforms or hide interactive controls.
      const slide = overlay && (el.classList.contains('drawer') || el.classList.contains('ai-panel'));
      const from = {opacity:overlay ? .6 : .86}, to = {opacity:1};
      if (slide) { from.translate = '18px 0'; to.translate = '0 0'; }
      run.anim = el.animate([from,to], {duration:overlay ? 180 : 150, easing:'cubic-bezier(.33,1,.68,1)'});
      run.anim.onfinish = () => queueMicrotask(() => { if (active.get(el) === run) stop(el); });
    } catch (_) { stop(el); } // Motion failures never block account operations.
  };
  const open = el => {
    if (watched.has(el)) watched.get(el).on = true;
    animate(el,true);
  };
  const close = el => {
    if (watched.has(el)) watched.get(el).on = false;
    stop(el);
  };
  const cleanup = () => { viewTicket++; [...active.keys()].forEach(stop); };
  // 视图切换可以在用户连续点击导航时被打断；只让最后一次布局帧进入动画。
  // 这样不会堆积动画，也不会为账号行创建动画。
  const view = (el, options = {}) => {
    if (!el) return;
    const ticket = ++viewTicket;
    stop(el);
    const run = () => { if (ticket === viewTicket) animate(el, false); };
    if (!options.defer) return run();
    if (typeof window.requestAnimationFrame === 'function') window.requestAnimationFrame(run);
    else if (typeof queueMicrotask === 'function') queueMicrotask(run);
    else setTimeout(run, 0);
  };
  const unwatch = el => {
    stop(el);
    watched.get(el)?.observer.disconnect();
    watched.delete(el);
  };
  const watch = el => {
    if (!el?.matches?.('.modal,.drawer,.ai-panel') || watched.has(el)) return;
    const state = {on:false,observer:null};
    const sync = () => {
      if (!el.isConnected) { unwatch(el); return; }
      const on = el.classList.contains('on');
      if (on === state.on) return;
      state.on = on;
      if (on) open(el); else close(el);
    };
    state.observer = new MutationObserver(sync);
    watched.set(el,state);
    state.observer.observe(el,{attributes:true,attributeFilter:['class']});
    sync();
  };
  const init = () => {
    // Keep animation ownership stable after tween cleanup. Removing temporary
    // markers must not restart CSS entrances or re-enable competing transitions.
    document.documentElement?.classList.toggle('tao-motion-ready', typeof document.documentElement.animate === 'function');
    if (bodyObserver || typeof MutationObserver === 'undefined' || !document.body) return;
    [...document.body.children].forEach(watch);
    bodyObserver = new MutationObserver(records => {
      for (const r of records) {
        for (const el of r.removedNodes) if (watched.has(el)) unwatch(el);
        for (const el of r.addedNodes) watch(el);
      }
    });
    // Only top-level overlays: never observe mutations in thousands of account rows.
    bodyObserver.observe(document.body,{childList:true});
  };
  const destroy = () => {
    bodyObserver?.disconnect(); bodyObserver = null;
    [...watched.keys()].forEach(unwatch);
    cleanup();
  };
  if (media?.addEventListener) media.addEventListener('change',cleanup);
  else if (media?.addListener) media.addListener(cleanup);
  window.addEventListener('pagehide',destroy);
  window.addEventListener('pageshow',init); // back-forward cache restoration
  document.addEventListener('visibilitychange',() => { if (document.hidden) cleanup(); });
  init();
  return {open,view,close,cleanup,destroy,reduced};
})();

/* ---------- 统一 Esc 分发 ----------
 * 每个层只登记自己的“打开条件”和关闭动作，document 上只保留一个
 * capture 阶段监听。这样同一按键不会同时穿透菜单、抽屉和弹窗。
 */
const TAO_KEYS = (() => {
  const entries = [];
  let seq = 0;
  const sort = () => entries.sort((a, b) => b.priority - a.priority || a.seq - b.seq);
  const dispatch = event => {
    if (event.key !== 'Escape') return;
    sort();
    const hit = entries.find(item => {
      try { return !!item.isOpen(); } catch (_) { return false; }
    });
    if (!hit) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    try { hit.close(event); } catch (_) {}
  };
  document.addEventListener('keydown', dispatch, true);
  const esc = (name, priority, isOpen, close) => {
    const key = String(name || 'esc-' + (++seq));
    const old = entries.findIndex(item => item.name === key);
    if (old >= 0) entries.splice(old, 1);
    const item = {name: key, priority: Number(priority) || 0, isOpen: isOpen || (() => false), close: close || (() => {}), seq: ++seq};
    entries.push(item); sort();
    return () => { const i = entries.indexOf(item); if (i >= 0) entries.splice(i, 1); };
  };
  return {
    esc,
    list: () => entries.slice().sort((a, b) => b.priority - a.priority || a.seq - b.seq).map(item => ({name: item.name, priority: item.priority})),
    _dispatch: dispatch,
  };
})();

function setAccounts(rows) {
  accounts = Array.isArray(rows) ? rows : [];
  accountsRevision++;
  visibleCache = null;
  filterCountsCache = null;
}
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
  el.setAttribute('role', kind === 'err' ? 'alert' : 'status');
  el.setAttribute('aria-live', kind === 'err' ? 'assertive' : 'polite');
  el.textContent = msg;
  $('#toasts').appendChild(el);
  // 同一提示只保留最新一条，避免快速点击时 toast 堆满视口。
  const previous = [...$('#toasts').querySelectorAll('.toast')].slice(0, -5);
  previous.forEach(x => x.remove());
  const timer = setTimeout(() => { el.classList.add('toast-leaving'); setTimeout(() => el.remove(), 160); }, 4200);
  el.addEventListener('click', () => { clearTimeout(timer); el.classList.add('toast-leaving'); setTimeout(() => el.remove(), 160); }, {once:true});
}
let busy = 0;
function progress(on) {
  busy = Math.max(0, busy + (on ? 1 : -1));
  // Keep loading state in the class model so CSS never has to inspect an
  // inline style attribute (which would be brittle under strict CSP/builds).
  const bar = $('#prog');
  if (bar) bar.classList.toggle('is-indet', busy > 0);
}
/* ---------- 统一错误码 -> 中文提示（后端 tam/errors.py） ---------- */
const TAO_ERR = (() => {
  const CN = {
    auth_required: '令牌无效，请核对 TAM_WEB_TOKEN',
    readonly_mode: '当前是只读模式，不能执行修改操作',
    not_found: '找不到这个账号或资源，可能已被删除',
    validation_error: '参数有误',
    conflict: '这个账号正被其他任务使用，请稍后再试',
    internal_error: '服务端出错了，详情在服务端日志里',
    session_missing: '本地没有会话，需要先登录',
    session_invalid: '会话已失效，需要重新登录',
    account_banned: '账号已被 Telegram 封禁',
    account_deleted: '账号已注销',
    account_restricted: '账号被限制（Spam），部分功能不可用',
    account_locked: '账号已锁定，已跳过',
    twofa_required: '需要两步验证密码',
    twofa_wrong: '两步验证密码错误',
    phone_invalid: '手机号无效',
    code_timeout: '取码链接在时限内没收到验证码',
    code_invalid: '验证码错误或已过期',
    code_link_missing: '这个账号没有配置取码链接',
    flood_wait: 'Telegram 限流',
    proxy_error: '代理连不上或认证失败',
    network_timeout: '连接 Telegram 超时，检查网络或代理',
    dc_migrate: '数据中心切换失败，请重试',
    import_bad_archive: 'ZIP 损坏或无法解压',
    import_unknown_format: '无法识别：既不是 tdata 也不是 .session',
    import_duplicate: '账号已存在',
    tdata_passcode: '这份 tdata 设了本地密码',
    op_unknown: '不支持的工具箱操作',
    op_param_missing: '缺少必填参数',
    task_cancelled: '任务已取消',
    peer_invalid: '用户名、群或频道不存在，或没有权限',
    privacy_restricted: '对方隐私设置不允许',
    // 后端旧版本曾返回此值，读取时仍兼容。
    not_authorized: '会话已失效，需要重新登录',
  };
  const fmtVal = detail => Array.isArray(detail)
    ? detail.map(item => {
      if (!item || typeof item !== 'object') return String(item == null ? '' : item);
      const loc = Array.isArray(item.loc) ? item.loc.filter(key => key !== 'body').join('.') : '';
      const msg = item.msg || item.message || JSON.stringify(item);
      return (loc ? loc + '：' : '') + msg;
    }).join('；')
    : (detail && typeof detail === 'object' ? JSON.stringify(detail) : String(detail == null ? '' : detail));
  const wait = seconds => {
    const value = Math.max(0, Number(seconds) || 0);
    return value >= 3600 ? Math.ceil(value / 3600) + ' 小时'
      : value >= 60 ? Math.ceil(value / 60) + ' 分钟' : value + ' 秒';
  };
  function describe(code, detail, retry_after) {
    const key = code || '';
    const base = CN[key];
    const text = fmtVal(detail).trim();
    // 英文异常标识/JSON 不直接展示；中文细节保留，但避免与固定提示重复。
    const english = /^[A-Za-z0-9_ .:()\-/{\[\]}]+$/.test(text);
    let message = !base ? text : (!text || text === key || english || text === base)
      ? base
      : (text.indexOf(base) >= 0 ? text : base.indexOf(text) >= 0 ? base : base + '：' + text);
    if (key === 'flood_wait' && Number(retry_after) > 0) {
      message += '，约 ' + wait(retry_after) + '后可重试';
    }
    return message || '请求失败';
  }
  return {CN, describe, fmtVal};
})();

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
      let msg = raw, detail = raw, payload = null;
      try {
        payload = JSON.parse(raw);
        detail = payload.detail != null ? payload.detail : (payload.message || raw);
        msg = detail;
      } catch {}
      if (res.status === 401) msg = '令牌无效（401）：请核对 .env 里的 TAM_WEB_TOKEN';
      if (res.status === 405) msg = '服务端仍在运行旧版本（405）。请关闭旧窗口后重新运行 start.bat，再按 Ctrl+F5 刷新页面';
      if (res.status !== 401 && res.status !== 405) {
        msg = TAO_ERR.describe(payload && payload.code, detail, payload && payload.retry_after);
      }
      const err = new Error(String(msg).slice(0, 400));
      err.status = res.status;
      if (payload) {
        err.code = payload.code || null;
        err.params = payload.params || null;
        err.retry_after = payload.retry_after || null;
        err.detail = payload.detail;
      } else {
        err.code = null; err.params = null; err.retry_after = null; err.detail = detail;
      }
      throw err;
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
  if (path === '/api/settings') return {
    env_path: '本地演示配置（未写入真实 .env）',
    groups: {
      '运行参数': [
        {key:'TAM_BATCH_CONCURRENCY', type:'int', value:'8', default:'8', desc:'自动登录与批量任务的并行数量'},
        {key:'TAM_REQUEST_TIMEOUT', type:'int', value:'30', default:'30', desc:'单个请求超时（秒）'},
      ],
      '接码轮询': [
        {key:'TAM_CODE_WAIT_SECONDS', type:'int', value:'8', default:'8', desc:'发码后首次查询前等待时间'},
        {key:'TAM_CODE_POLL_INTERVAL', type:'int', value:'5', default:'5', desc:'后续查询间隔（秒）'},
      ],
    },
    secrets: [{key:'TAM_API_ID', set:true}, {key:'TAM_API_HASH', set:true}],
  };
  if (path === '/api/toolbox/ops') return {ops: [
    {op:'quick_alive', label:'快速验活', group:'check', params:[{name:'timeout',label:'超时（秒）',type:'int',default:15}], danger:false},
    {op:'twofa_status', label:'查询二验状态', group:'check', params:[], danger:false},
    {op:'send_message', label:'发送消息', group:'messages', params:[{name:'message',label:'消息内容',type:'textarea',required:true}], danger:false},
    {op:'terminate_sessions', label:'踢出其它设备', group:'more', params:[], danger:true},
  ]};
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
const TASK_KIND_CN = {send: '群发消息', collect_speakers: '采集发言人', auto_login: '自动登录'};
const TASK_TONE = {running: 'ok', pending: '', stopping: 'warn', stopped: 'warn', done: 'ok', failed: 'err'};
let taskTimer = null;
const observedLoginTasks = new Set();

async function refreshLoginAccounts() {
  const [s, a] = await Promise.all([api('/api/stats'), api('/api/accounts')]);
  stats = s; setAccounts(a);
  render();
}

function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function taskCard(t) {
  const pct = Math.max(0, Math.min(100, t.percent || 0));
  const tone = TASK_TONE[t.status] || '';
  const canStop = t.status === 'running' || t.status === 'pending';
  return `<div class="taskrow" data-task-id="${t.id}" data-task-stage="${esc(t.status_cn || t.status)}">
    <div class="taskhead">
      <b>#${t.id} ${esc(t.title)}</b>
      <span class="pill ${tone}">${esc(t.status_cn || t.status)}</span>
      <span class="muted">${esc(TASK_KIND_CN[t.kind] || t.kind)} · ${rel(t.created_at)}</span>
      <span style="flex:1"></span>
      <button class="sm fit" data-onclick="taskDetail(${t.id})">明细</button>
      ${t.kind === 'auto_login' && !t.live && t.fail_count ? `<button class="sm fit" data-onclick="retryLoginTask(${t.id})">重试失败账号</button>` : ''}
      ${canStop ? `<button class="sm fit warn" data-onclick="stopTask(${t.id})">停止</button>`
                : `<button class="sm fit" data-onclick="delTask(${t.id})">删除</button>`}
    </div>
    <div class="bar"><i style="width:${pct}%"></i></div>
    <div class="muted small">进度 ${t.done_count || 0}/${t.total || 0}（${pct}%）
      · 成功 ${t.ok_count || 0} · 失败 ${t.fail_count || 0} · 已跳过 ${t.skip_count || 0}</div>
  </div>`;
}

async function loadTasks(manualClick) {
  try {
    const rows = await api('/api/tasks?limit=10');
    let loginFinished = false;
    for (const t of rows) {
      if (t.kind !== 'auto_login') continue;
      if (t.live) observedLoginTasks.add(t.id);
      else if (observedLoginTasks.has(t.id)) loginFinished = true;
    }
    if (loginFinished) {
      await refreshLoginAccounts();
      for (const t of rows) if (!t.live) observedLoginTasks.delete(t.id);
    }
    const el = $('#taskList');
    if (!el) return;
    if (!rows.length) {
      el.innerHTML = '<span class="muted">还没有任务。可以先采集发言人，再对采集结果群发。</span>';
      $('#taskHint').textContent = '暂无任务';
    } else {
      el.innerHTML = rows.map(taskCard).join('');
      const live = rows.filter(t => t.status === 'running' || t.status === 'pending' || t.status === 'stopping').length;
      $('#taskHint').textContent = live ? `${live} 个进行中 · 共 ${rows.length} 个` : `最近 ${rows.length} 个`;
      clearTimeout(taskTimer);
      if (live) taskTimer = setTimeout(loadTasks, 3000);
    }
    updateNavBadges();
    if (manualClick) toast('任务列表已刷新', 'ok');
  } catch (e) {
    if (manualClick) toast('任务列表加载失败：' + e.message, 'err');
    clearTimeout(taskTimer);
    taskTimer = setTimeout(loadTasks, 3000);
  }
}

let loginDetailTimer = null;
let taskDetailVersion = 0;
async function taskDetail(id) {
  const version = ++taskDetailVersion;
  clearTimeout(loginDetailTimer);
  try {
    const t = await api(`/api/tasks/${id}?target_limit=200`);
    if (version !== taskDetailVersion) return;
    const head = `任务 #${t.id}：${t.title}\n类型：${TASK_KIND_CN[t.kind] || t.kind}`
      + `\n状态：${t.status_cn || t.status}\n进度：${t.done_count}/${t.total}（${t.percent}%）`
      + `\n成功 ${t.ok_count} · 失败 ${t.fail_count} · 已跳过 ${t.skip_count}`
      + `\n创建于 ${fmt(t.created_at)}`
      + (t.finished_at ? `\n结束于 ${fmt(t.finished_at)}` : '');
    const bad = (t.targets || []).filter(x => x.status === 'fail');
    const targetDetail = x => x.code
      ? TAO_ERR.describe(x.code, x.detail || x.error || x.reason, x.retry_after)
      : (x.detail || x.error || x.reason || '');
    const lines = (t.targets || []).slice(0, 60).map(x =>
      `${x.status === 'ok' ? '✓' : (x.status === 'fail' ? '✗' : '·')} ${x.target}`
      + `${x.account_id ? '  由账号 #' + x.account_id : ''}  ${x.status_cn}`
      + `${targetDetail(x) ? '：' + targetDetail(x) : ''}`);
    const tail = (t.targets || []).length > 60 ? `\n…共 ${t.targets.length} 条，仅显示前 60 条` : '';
    const fail = bad.length ? `\n\n失败原因归类：\n` + Object.entries(
      bad.reduce((m, x) => { const k = x.code || (x.detail || '未知').split(':')[0]; m[k] = (m[k] || 0) + 1; return m; }, {})
    ).map(([k, v]) => `• ${TAO_ERR.CN[k] || k}：${v} 个`).join('\n') : '';
    $('#log').textContent = head + fail + '\n\n逐目标明细：\n' + lines.join('\n') + tail;
    $('#logRaw').textContent = JSON.stringify(t, null, 2);
    logPinned = true; $('#logPin').style.display = '';
    if (t.kind === 'auto_login' && t.live) loginDetailTimer = setTimeout(() => taskDetail(id), 1000);
    else if (t.kind === 'auto_login') await refreshLoginAccounts();
  } catch (e) {
    if (version !== taskDetailVersion) return;
    $('#log').textContent = '任务状态连接中断，3 秒后重试；后台任务未取消。';
    loginDetailTimer = setTimeout(() => taskDetail(id), 3000);
  }
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
          data-onchange="toggleChat('${String(c.peer).replace(/'/g, "\\'")}', this.checked)" />
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
      <button class="sm fit" data-onclick="viewLeadMsgs(null, '${esc(x.source).replace(/'/g, "\\'")}')">看对话</button>
      <button class="sm fit write" data-onclick="sendToSource('${esc(x.source).replace(/'/g, "\\'")}')">向它群发</button>
      </div>`).join('');
    const list = leadRows.slice(0, 50).map(x =>
      `<div class="leadrow"><span>${x.username ? '@' + esc(x.username) : esc(x.name || x.user_id)}</span>
       <span class="muted">${esc(x.source)} · 发言 ${x.msg_count || 0} 条 · ${rel(x.last_msg_at)}</span>
       <span style="flex:1"></span>
       <button class="sm fit" data-onclick="viewLeadMsgs(${Number(x.user_id)}, '${esc(x.source).replace(/'/g, "\\'")}')">对话</button></div>`).join('');
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
document.addEventListener('input', function (e) {
  if (e.target && (e.target.id === 'iProxyPool' || e.target.id === 'iLinksPerProxy')) previewProxyCapacity();
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
  const returnFocus = document.activeElement;
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
      const detail = $('#accountDetailDrawer');
      detail.classList.add('on'); detail.setAttribute('aria-hidden', 'false'); $('#bd').classList.add('on');
      focusDialog(detail, returnFocus);
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
    const detail = $('#accountDetailDrawer');
    detail.classList.add('on'); detail.setAttribute('aria-hidden', 'false'); $('#bd').classList.add('on');
    focusDialog(detail, returnFocus);
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
  'tools.call':'Agent 工具调用',
  'tools.convert':'格式转换', 'tools.passkey':'通行密钥处理', 'tools.toapi':'转换为 API',
  'ai.config':'AI 配置', 'ai.chat':'AI 对话', 'kick_retry':'自动清设备重试配置',
  'twofa_capture':'读取接码页二验', 'twofa_save':'保存二验', 'toolbox.twofa':'工具箱改二验',
  'session_authorization':'会话授权校验', 'disconnect_cleanup':'断线清理',
  'delete_telegram_account':'注销 Telegram 账号', 'account_lock':'锁定账号', 'account_unlock':'解锁账号',
  'proxy_check':'代理检查', 'proxy_replace':'更换代理', 'proxy_import':'导入代理',
  'batch_proxy':'批量配置代理', 'batch_lock':'批量锁定账号', 'batch_unlock':'批量解锁账号',
  'code_source_check':'取码源探测', 'official_code':'读取官方验证码', 'one_stop':'一条龙处理', check_phones:'手机号检测', delete_messages:'删除或撤回消息', edit_message:'编辑消息', forward_messages:'转发消息', reply_message:'回复消息', read_message:'标记已读', get_message:'读取单条消息', create_group:'创建群聊', leave_chat:'离开群聊', block_users:'屏蔽用户', unblock_users:'取消屏蔽用户', terminate_others:'清理其他设备', quick_alive:'快速验活', collect:'采集发言人', send:'发送消息', regenerate:'重生会话'
};
const STATUS_CN = {new:'待登录', active:'正常', spam_block:'临时限制', spam_block_perm:'永久限制',
  frozen:'冻结', unauthorized:'会话失效', restricted:'受限', banned:'已封禁', error:'异常',
  flood_wait:'触发频率限制', pending:'待处理', running:'运行中', processing:'处理中',
  ok:'成功', success:'成功', fail:'失败', failed:'失败', skipped:'已跳过', uncertain:'待核对',
  logged_in:'已登录', logged_out:'已退出', ready:'可导出', changing:'修改二验中', login:'登录中',
  no_session:'无会话', done:'已完成', stopping:'正在停止', stopped:'已停止', waiting:'等待中', warn:'提醒', unknown:'未确认', failed_wait:'等待后重试'};
const cnStatus = s => STATUS_CN[s] || (s || '未知');
const ERROR_CN = {
  AuthKeyUnregisteredError:'会话已失效（授权密钥未注册）', SessionRevokedError:'会话已被撤销',
  SessionExpiredError:'会话已过期', UserDeactivatedBanError:'账号已停用或封禁', UserDeactivatedError:'账号已停用',
  PhoneNumberInvalidError:'手机号无效', PhoneNumberBannedError:'手机号已被限制', PhoneCodeExpiredError:'验证码已过期',
  PhoneCodeInvalidError:'验证码错误', PhoneCodeEmptyError:'验证码为空', SessionPasswordNeededError:'需要二步验证密码',
  PasswordHashInvalidError:'二验密码不匹配', PasswordEmptyError:'二步验证密码为空', PasswordTooFreshError:'密码设置过于频繁',
  FloodWaitError:'触发频率限制', SlowModeWaitError:'触发慢速模式限制', TimeoutError:'连接超时',
  ConnectTimeoutError:'连接超时', asyncioTimeoutError:'连接超时', RuntimeError:'运行时错误',
  RPCError:'Telegram 接口错误', NetworkError:'网络错误', ConnectionError:'网络连接失败',
  FileNotFoundError:'文件不存在', PermissionError:'没有文件权限', ValueError:'参数格式错误',
  NotFound:'资源不存在', MethodNotAllowed:'接口不支持此方法', AttributeError:'程序字段错误',
  AuthKeyDuplicatedError:'会话密钥被重复使用', AuthKeyInvalidError:'会话密钥无效',
  FreshResetAuthorisationForbiddenError:'当前会话登录时间不足，暂时禁止清理其他设备',
  PasswordMissingError:'尚未设置二步验证密码', EmailUnconfirmedError:'辅助邮箱尚未确认',
  EmailInvalidError:'邮箱格式无效', ApiIdInvalidError:'API ID 或 API Hash 无效',
  PeerFloodError:'触发 Telegram 操作限制', UserPrivacyRestrictedError:'对方隐私设置限制此操作',
  ChatWriteForbiddenError:'当前会话没有发送权限', ChannelPrivateError:'群组或频道访问受限',
  UserNotParticipantError:'账号尚未加入该会话', UserAlreadyParticipantError:'账号已加入该会话',
  ConnectionRefusedError:'连接被拒绝', ConnectionResetError:'连接被重置',
  ProxyConnectionError:'代理连接失败', ProxyTimeoutError:'代理连接超时',
  URLError:'网络地址访问失败', OSError:'系统 I/O 异常',
  OperationalError:'数据库操作异常', TypeError:'数据类型异常'
};
const KEY_CN = {user_id:'用户 ID',account_id:'账号 ID',code_len:'验证码长度',device_model:'设备型号',
  app_version:'客户端版本',system_version:'系统版本',retry_after_s:'重试等待',concurrency:'并发数',
  total:'总数',succeeded:'成功数',failed:'失败数',skipped:'跳过数',done_count:'已完成',
  ok_count:'成功数',fail_count:'失败数',skip_count:'跳过数',capacity_blocked:'容量不足',
  retryable:'可重试',status:'状态',phase:'阶段',error_code:'错误码',note:'备注',detail:'详情'};
const isOk = v => v === true || v === 1 || v === '1' || (typeof v === 'string' && v.toLowerCase() === 'true');
function cnError(value) {
  if (value === null || value === undefined) return '';
  let s = String(value);
  Object.entries(ERROR_CN).forEach(([en, cn]) => { s = s.replace(new RegExp('\\b' + en + '\\b', 'g'), (match, offset, text) =>
    text.slice(Math.max(0, offset-cn.length-1), offset) === cn+'（' ? match : cn+'（'+en+'）'); });
  s = s.replace(/A wait of (\d+) seconds? is required/gi, '需要等待 $1 秒')
    .replace(/\bMethod Not Allowed\b/gi, '接口不支持此方法')
    .replace(/\bNot Found\b/gi, '资源不存在')
    .replace(/after (\d+) seconds?/gi, '$1 秒后重试')
    .replace(/HTTP\s+(\d{3})/gi, '接口状态 $1');
  return s;
}
function redactText(value) {
  return String(summaryClone(value)).replace(/(password|passwd|token|secret|api_hash|code_url|proxy)\s*[=:]\s*("[^"]*"|'[^']*'|[^\s,;]+)/gi,
    (_, key, val) => `${KEY_CN[key.toLowerCase()] || key}=***`);
}
function detailText(value) {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value !== 'string') return summaryValue(summaryClone(value));
  if (/^[{[]/.test(value.trim())) { try { return summaryValue(summaryClone(JSON.parse(value))); } catch (_) {} }
  const s = cnError(redactText(value));
  if (LOG_DETAIL_CN[s] || ACTION_CN[s] || STATUS_CN[s]) return LOG_DETAIL_CN[s] || ACTION_CN[s] || STATUS_CN[s];
  const batchOp = s.match(/^([a-z_]+) x(\d+)$/);
  if (batchOp && ACTION_CN[batchOp[1]]) return `${ACTION_CN[batchOp[1]]} × ${batchOp[2]}`;
  return s.replace(/\b([a-z_]+)=([^\s,;]+)/gi,
    (_, key, val) => `${KEY_CN[key.toLowerCase()] || key}=${val}`);
}
function resultStatus(x, depth = 0) {
  if (!x || typeof x !== 'object') return 'unknown';
  if (['uncertain','changing'].includes(x.phase) || x.status === 'uncertain') return 'uncertain';
  if (x.status === 'skipped' || x.skipped === true) return 'skipped';
  if (x.error || ['fail','failed','error'].includes(x.status)) return 'fail';
  if (['pending','running','processing','stopping'].includes(x.status)) return x.status === 'pending' ? 'pending' : 'running';
  if (x.ok !== undefined && x.ok !== null && [false,0,'0','false'].includes(x.ok)) return 'fail';
  if (x.alive === false || x.verified === false) return x.alive === false ? 'fail' : 'uncertain';
  if (x.result && typeof x.result === 'object' && depth < 5) {
    const nested = resultStatus(x.result, depth + 1);
    if (nested !== 'unknown') return nested;
  }
  if (isOk(x.ok) || x.alive === true || x.verified === true || ['ok','success'].includes(x.status)) return 'ok';
  return 'unknown';
}
function whoIs(id) {
  if (id === null || id === undefined) return '系统';
  const a = accounts.find(x => String(x.id) === String(id));
  return a ? `${a.label}${a.phone ? ' ' + fmtPhone(a.phone) : ''}` : `账号 #${id}`;
}
function logLines(rows) {
  return rows.map(r => {
    const st = resultStatus(r);
    const icon = {ok:'✓',fail:'✗',skipped:'↷',pending:'…',running:'…',uncertain:'!',unknown:'?'}[st] || '?';
    return `${fmt(r.created_at)}  ${icon} ${ACTION_CN[r.action] || r.action || '操作'}`
      + `  ${whoIs(r.account_id)}${r.detail ? '  ' + detailText(r.detail) : ''}`;
  }).join('\n');
}
// Summary formatting is deliberately pure: raw results remain available unchanged.
Object.assign(KEY_CN, {
  id:'编号', op:'操作', kind:'任务类型', action:'动作', title:'名称', name:'名称', label:'别名',
  message:'说明', reason:'原因', error:'错误', errors:'错误记录', result:'结果', results:'结果明细',
  items:'条目', targets:'目标明细', count:'数量', deleted:'已删除', locked:'已锁定', updated:'已更新',
  added:'已新增', existing:'已存在', duplicates:'重复项', parsed:'解析数', parsed_input:'输入条数',
  enabled:'已启用', dry_run:'试运行', alive:'验活通过', authorized:'已授权', verified:'回查通过',
  has_password:'已开启二验', had_password:'原已开启二验', has_recovery:'已绑定辅助邮箱',
  has_twofa_saved:'已保存二验', pending_reset:'等待重置', hint:'提示', before:'操作前数量',
  after:'操作后状态', remaining_others:'剩余其他设备', logged_out:'已退出登录', old_logged_out:'旧授权已退出',
  job:'作业编号', task_id:'任务编号', one_stop_task:'一条龙任务编号', live:'后台仍在运行',
  phases:'阶段统计', percent:'进度百分比', started_at:'开始时间', finished_at:'完成时间', created_at:'创建时间',
  path:'路径', filename:'文件名', output:'输出', out_dir:'输出目录', files:'文件', packs:'号包数量',
  tdata_dirs:'tdata 目录数', retry_after_s:'重试等待秒数', duration_s:'耗时秒数', elapsed:'耗时',
  scanned:'已扫描', processed:'已处理', warnings:'提醒', skip_reason:'跳过原因',
  proxy:'代理', code_url:'取码链接', password:'密码', twofa:'二验密码', token:'令牌',
  saved:'已保存', values:'配置值', balances:'余额', reply:'回复', user_id:'用户 ID',
  watched:'监控账号数', due_now:'到期待处理', retrying:'等待重试', next_at:'下次时间',
  mode:'模式', total_accounts:'账号总数', capacity:'节点容量', assigned:'已分配',
  available:'可用', unavailable:'不可用', input:'输入', failed_ids:'失败账号', success_ids:'成功账号',
  source:'来源', source_type:'来源类型', provider:'服务商', query_delay:'发码后等待秒数',
  poll_interval:'查询间隔秒数', timeout:'超时秒数', ok:'成功', ready:'可导出',
  expires:'过期时间', bot:'机器人', tools:'工具调用次数', preset:'配置预设',
  session:'StringSession', status_note:'状态说明', done:'已完成',
  changing:'修改二验', logged_in:'已登录', login:'登录中', pending:'待处理', uncertain:'待核对'
});
const LOG_DETAIL_CN = {
  dynamic_monitor_started:'已启动动态取码监控', captured_from_code_page:'已从接码页面读取二验',
  local:'已保存到本地', quick_alive:'快速验活', logged_out:'已退出登录',
  already_authorized:'会话已授权', already_logged_in:'账号已登录'
};
const SUMMARY_LIMIT = 40;
const SUMMARY_SECRET = /^(?:password|passwd|old|new|current_password|new_password|old_password|twofa|twofa_password|api_hash|api_key|token|access_token|refresh_token|linkToken|secret|session|session_string|session_enc|auth_key|code|verification_code|code_url)$/i;
function summaryClone(value, key = '', depth = 0) {
  if (value == null) return value;
  if (['has_password','had_password','has_twofa_saved'].includes(key)) return value;
  if (SUMMARY_SECRET.test(key) || /(?:^|_)(?:PASSWORD|TOKEN|SECRET|API_HASH|API_KEY|AUTH_KEY|ENCRYPTION_KEY)$/i.test(key)) return '[敏感信息已隐藏，请查看原始数据]';
  if (depth > 8) return '[嵌套内容见原始数据]';
  if (Array.isArray(value)) return value.map(x => summaryClone(x, '', depth + 1));
  if (typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,summaryClone(v,k,depth + 1)]));
  if (typeof value === 'string') return value.replace(/\b((?:socks5?|https?):\/\/)[^\s/@]+(?::[^\s/@]*)?@/gi,'$1***@')
    .replace(/https?:\/\/[^\s"'<>]*(?:GetHTML|linkToken=)[^\s"'<>]*/gi,'[取码链接已隐藏]')
    .replace(/((?:password|passwd|token|secret|api_hash|auth_key)\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s,;]+)/gi,'$1***');
  return value;
}
function summaryValue(v, key = '', depth = 0) {
  if (v == null) return '—';
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (depth > 4) return '详见原始数据';
  if (Array.isArray(v)) return v.slice(0, 8).map(x => summaryValue(x, '', depth + 1)).join('；') + (v.length > 8 ? `；另有 ${v.length - 8} 项` : '');
  if (typeof v === 'object') return Object.entries(v).slice(0, 16).map(([k,x]) => `${KEY_CN[k] || k}：${summaryValue(x,k,depth + 1)}`).join('；');
  if (['status','phase','result'].includes(key)) return cnStatus(v);
  if (['op','action','kind'].includes(key)) return ACTION_CN[v] || v;
  return detailText(String(v)).slice(0, 1500);
}
function genericSummary(d) {
  const entries = Object.entries(d).filter(([k]) => !['stack','trace'].includes(k));
  return '结果摘要：\n' + entries.slice(0, 25).map(([k,v]) => `• ${KEY_CN[k] || ACTION_CN[k] || k}：${summaryValue(v,k)}`).join('\n')
    + (entries.length > 25 ? `\n…另有 ${entries.length - 25} 个字段，详见原始数据` : '');
}
function summaryCount(v) {
  if (Array.isArray(v)) return v.length;
  if (v == null || typeof v === 'boolean') return 0;
  const n=Number(v); return Number.isFinite(n) && n >= 0 ? n : 0;
}
function summaryTail(n) { return n > SUMMARY_LIMIT ? `\n…另有 ${n - SUMMARY_LIMIT} 条，完整明细见原始数据` : ''; }
function batchSummary(rows, meta = {}) {
  const counts = {ok:0,fail:0,skipped:0,pending:0,running:0,uncertain:0,unknown:0};
  for (const row of rows) counts[resultStatus(row)]++;
  const ok = meta.succeeded ?? meta.ok_count ?? counts.ok;
  const fail = meta.failed ?? meta.fail_count ?? counts.fail;
  const skipped = meta.skipped ?? meta.skip_count ?? counts.skipped;
  const total = meta.total ?? rows.length;
  const head = `${meta.hours != null ? '自动清设备' : '批量结果'}：共 ${total} 个，成功 ${summaryCount(ok)}，失败 ${summaryCount(fail)}，跳过 ${summaryCount(skipped)}`
    + (counts.pending ? `，待处理 ${counts.pending}` : '') + (counts.running ? `，处理中 ${counts.running}` : '')
    + (counts.uncertain ? `，待核对 ${counts.uncertain}` : '') + (counts.unknown ? `，未确认 ${counts.unknown}` : '');
  const lines = rows.slice(0,SUMMARY_LIMIT).map(x => {
    if (!x || typeof x !== 'object') return '? 未确认条目：' + summaryValue(x);
    const st = resultStatus(x);
    const icon = {ok:'✓',fail:'✗',skipped:'↷',pending:'…',running:'…',uncertain:'!',unknown:'?'}[st];
    const nested = x.result && typeof x.result === 'object' ? x.result : null;
    const parts = [x.error, x.detail, x.reason, x.note].filter(Boolean).map(detailText);
    if (nested) parts.push(humanize(nested));
    else {
      const extras = Object.fromEntries(Object.entries(x).filter(([k]) => !['id','account_id','ok','error','detail','reason','note','label','status'].includes(k)));
      if (Object.keys(extras).length) parts.push(summaryValue(extras));
    }
    return `${icon} ${whoIs(x.account_id ?? x.id)} · ${cnStatus(st)}${parts.length ? ' · ' + parts.join('；') : ''}`;
  });
  return head + (rows.length ? '\n' + lines.join('\n') : '\n（无逐项明细）') + summaryTail(rows.length)
    + (summaryCount(total)>rows.length ? `\n仅返回 ${rows.length} 条明细；汇总采用服务端计数。` : '');
}
function taskSummary(d) {
  const title = ACTION_CN[d.kind] || (d.one_stop_task ? '一条龙处理' : '后台任务');
  const lines = [`${title} #${d.task_id ?? d.one_stop_task ?? d.id ?? '—'} · ${cnStatus(d.status || 'pending')}`];
  if (d.total != null) lines.push(`进度 ${d.done_count ?? (summaryCount(d.ok_count ?? d.succeeded)+summaryCount(d.fail_count ?? d.failed)+summaryCount(d.skip_count ?? d.skipped))}/${d.total} · 成功 ${d.ok_count ?? d.succeeded ?? 0} · 失败 ${d.fail_count ?? d.failed ?? 0} · 跳过 ${d.skip_count ?? d.skipped ?? 0}`);
  if (d.phases) lines.push('阶段：' + summaryValue(d.phases));
  if (d.note || d.error) lines.push(detailText(d.note || d.error));
  if (d.targets?.length) lines.push(batchSummary(d.targets));
  return lines.join('\n');
}
function humanize(value) {
  if (value == null) return '（无返回数据）';
  const d = summaryClone(value);
  if (typeof d !== 'object') return detailText(d);
  if (Array.isArray(d)) {
    if (!d.length) return '（空列表）';
    const valid = d.filter(x => x && typeof x === 'object' && !Array.isArray(x));
    const all = test => valid.length === d.length && valid.every(test);
    if (all(x => x.action !== undefined && x.created_at !== undefined)) return `操作日志 ${d.length} 条（自新到旧）\n` + logLines(d.slice(0,SUMMARY_LIMIT)) + summaryTail(d.length);
    if (all(x => x.kind != null && x.status != null)) return `任务列表 ${d.length} 个：\n` + d.slice(0,SUMMARY_LIMIT).map(taskSummary).join('\n\n') + summaryTail(d.length);
    if (all(x => Array.isArray(x.accounts))) return d.slice(0,SUMMARY_LIMIT).map(x => `目录：${x.path || '—'}\n` + batchSummary(x.accounts.map(a => ({...a,detail:[a.label,a.error].filter(Boolean).join(' · ')})))).join('\n\n') + summaryTail(d.length);
    if (valid.some(x => x.account_id != null || ('id' in x && ('ok' in x || 'error' in x || 'result' in x || 'status' in x)))) return batchSummary(d);
    if (all(x => x.device != null || x.platform != null || x.app != null)) return `当前登录设备 ${d.length} 台：\n` + d.slice(0,SUMMARY_LIMIT).map(x =>
      `• ${x.app || x.device || '未知客户端'}${x.platform ? ' · '+x.platform : ''}${x.current ? ' · 当前会话' : ''}${x.country ? ' · '+x.country : ''}${x.date_created ? ' · 登录于 '+x.date_created : ''}${x.date_active ? ' · 最近活跃 '+x.date_active : ''}`).join('\n') + summaryTail(d.length);
    if (all(x => x.name != null || x.title != null)) return `最近会话 ${d.length} 个：\n` + d.slice(0,SUMMARY_LIMIT).map(x => `• ${x.name || x.title}${x.unread ? ' · 未读 '+x.unread : ''}`).join('\n') + summaryTail(d.length);
    return `列表 ${d.length} 条：\n` + d.slice(0,SUMMARY_LIMIT).map(x => '• '+summaryValue(x)).join('\n') + summaryTail(d.length);
  }
  if (d.task && typeof d.task === 'object') return taskSummary({...d.task,task_id:d.task.id ?? d.job});
  if (d.one_stop_task || d.task_id || (d.kind && d.status && d.total != null)) return taskSummary(d);
  if (Array.isArray(d.checks)) return `一键体检：${d.ok == null ? '检查结果' : isOk(d.ok) ? '通过' : '有问题'}\n` + d.checks.slice(0,SUMMARY_LIMIT).map(c => {
    if (!c) return '? 未返回检查结果';
    const st=c.status || (c.ok === undefined ? 'unknown' : isOk(c.ok) ? 'ok':'fail');
    return `${{ok:'✓',warn:'!',fail:'✗'}[st] || '?'} ${c.name || c.item || '检查项'} · ${cnStatus(st)}${c.detail || c.message ? ' · '+detailText(c.detail || c.message) : ''}${c.fixed ? ' · 已修复' : ''}`
      + (c.hint ? '\n  建议：'+detailText(c.hint) : '') + (c.fix_log?.length ? '\n  修复记录：'+summaryValue(c.fix_log) : '');
  }).join('\n') + summaryTail(d.checks.length);
  if ((d.error || [false,0,'0','false'].includes(d.ok)) && !Array.isArray(d.results) && !Array.isArray(d.items)) return genericSummary(d);
  if (d.filename && (d.succeeded != null || d.tdata_dirs != null)) return `上传导入：${d.filename} · 成功 ${summaryCount(d.succeeded)}${d.total != null ? ' / 共 '+d.total : ''}` + (d.tdata_dirs != null ? ` · tdata 目录 ${d.tdata_dirs}` : '') + (d.items ? '\n'+humanize(d.items) : '');
  if (d.added !== undefined || (d.updated !== undefined && (d.parsed_input !== undefined || d.parsed !== undefined))) return `导入完成：解析 ${d.parsed_input ?? d.parsed ?? 0}，新增 ${summaryCount(d.added)} 个，更新 ${summaryCount(d.updated)} 个，重复 ${summaryCount(d.duplicates)}，已存在 ${summaryCount(d.existing)}，格式错误 ${summaryCount(d.errors)}，容量不足 ${summaryCount(d.capacity_blocked)}` + (d.dry_run ? '（试运行，未写入）' : '') + (Array.isArray(d.errors) && d.errors.length ? '\n错误明细：'+summaryValue(d.errors) : '');
  if (Array.isArray(d.results)) return batchSummary(d.results,d);
  if (Array.isArray(d.items) && (d.succeeded != null || d.failed != null || d.total != null)) return batchSummary(d.items,d);
  if (d.op && d.result && typeof d.result === 'object') return `${ACTION_CN[d.op] || d.op} · ${whoIs(d.account_id)}\n` + humanize(d.result);
  if (d.alive !== undefined) return `验活结果：${isOk(d.alive) ? '通过' : '未通过'}` + (d.reason ? '\n'+detailText(d.reason) : '');
  if (d.has_password !== undefined && d.had_password !== undefined) return `二验变更：${isOk(d.has_password) ? '已开启二步验证' : '已移除二步验证'}` + (d.action ? '\n动作：'+d.action : '');
  if (d.values) return genericSummary(d);
  if (d.total !== undefined && d.by_status) return `账号统计：共 ${d.total} 个\n` + Object.entries(d.by_status).map(([k,v]) => `${cnStatus(k)}：${v}`).join(' · ');
  if (d.error || [false,0,'0','false'].includes(d.ok)) return genericSummary(d);
  return humanizeLegacy(d);
}

function humanizeLegacy(d) {
  if (d === null || d === undefined) return null;
  if (typeof d !== 'object') return String(d);
  if (d.error && !isOk(d.ok) && Object.keys(d).length <= 3) return `出错了：${detailText(typeof d.error === 'object' ? JSON.stringify(d.error) : d.error)}`;
  if (d.detail && d.ok === false && typeof d.detail === 'string' && Object.keys(d).length <= 4)
    return `出错了：${detailText(d.detail)}`;

  // 重生会话
  if (d.old_logged_out !== undefined || (d.ok && d.device_model && d.note && String(d.note).indexOf('新会话') >= 0))
    return `${isOk(d.ok) ? '✓ 会话已重生' : '会话重生：结果待确认'}`
      + (d.label ? ` · ${d.label}` : '')
      + (d.user_id ? ` · 用户 ID=${d.user_id}` : '')
      + (d.device_model ? `\n新设备指纹：${d.device_model} / ${d.app_version || ''} / ${d.system_version || ''}` : '')
      + (d.old_logged_out !== undefined ? `\n旧授权注销：${isOk(d.old_logged_out) ? '已执行' : '未确认（可能仍有效）'}` : '')
      + (d.note ? `\n${detailText(d.note)}` : '');

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
    lines.push(isOk(d.has_password) ? '已开启二步验证' : '未开启二步验证');
    lines.push(isOk(d.has_recovery) ? '辅助邮箱：已绑定' : '辅助邮箱：未绑定');
    if (d.hint) lines.push('提示语：' + d.hint);
    if (d.pending_reset) lines.push('重置等待中' + ((d.pending_reset_iso || d.pending_reset_date) ? (' · 至 ' + (d.pending_reset_iso || fmt(d.pending_reset_date))) : ''));
    if (d.action === 'reset' || d.result === 'waiting' || d.result === 'ok' || d.result === 'failed_wait') {
      lines.push('重置结果：' + (d.result ? cnStatus(d.result) : '已申请重置'));
      if (d.until_iso || d.until_date) lines.push('等待至：' + (d.until_iso || fmt(d.until_date)));
      if (d.retry_iso || d.retry_date) lines.push('可重试时间：' + (d.retry_iso || fmt(d.retry_date)));
    }
    if (d.note) lines.push(detailText(d.note));
    return lines.join('\n');
  }

  // 限制检测
  if (d.spam_status !== undefined)
    return `限制检测：${cnStatus(d.spam_status)}`
      + (d.account_id != null ? ` · ${whoIs(d.account_id)}` : '')
      + (d.until ? `\n预计相关时间：${fmt(d.until)}` : '')
      + (d.reply ? `\nSpamBot 回复：\n${String(d.reply).slice(0, 400)}` : '');

  // 踢设备单号
  if (d.remaining_others !== undefined || (!d.op && d.verified !== undefined && d.before !== undefined))
    return `清理其它设备：${d.verified === false || d.ok === false ? '未完全确认' : isOk(d.verified) ? '回查完成' : '已提交，尚未回查确认'}`
      + (d.before != null ? ` · 踢前共 ${d.before} 个授权` : '')
      + (d.remaining_others != null ? ` · 仍残留其它设备 ${d.remaining_others}` : '')
      + (d.detail ? `\n${detailText(d.detail)}` : '');

  // 导出
  if (d.session && typeof d.session === 'string')
    return `已导出 StringSession` + (d.label ? ` · ${d.label}` : '') + (d.user_id ? ` · 用户 ID=${d.user_id}` : '')
      + `\n（完整字符串见右侧原始数据，请勿泄露）`;
  if (d.path && (d.ok || d.label) && !d.accounts)
    return `文件已生成` + (d.label ? ` · ${d.label}` : '') + `\n路径：${d.path}`;

  // 健康 / 检查类
  if (d.status !== undefined && (d.authorized !== undefined || d.user_id !== undefined) && d.label === undefined) {
    return `检查结果：${cnStatus(d.status)}`
      + (d.authorized !== undefined ? (d.authorized ? ' · 已授权' : ' · 未授权') : '')
      + (d.username ? ` · @${d.username}` : '')
      + (d.user_id ? ` · id=${d.user_id}` : '')
      + (d.note || d.status_note ? `\n${detailText(d.note || d.status_note)}` : '');
  }

  if (d.enabled !== undefined && d.hours !== undefined)
    return `自动清设备：${d.enabled ? '已开启' : '已关闭'} · 周期 ${d.hours} 小时 · 监控 ${d.watched} 个号`
      + ` · 待处理 ${d.due_now} 个 · 扫描间隔 ${Math.round((d.scan_interval_s || 0) / 60)} 分钟`
      + (d.next_at ? ` · 下次 ${fmt(d.next_at)}` : '');

  if (d.label !== undefined && d.status !== undefined) {
    const L = [`账号：${d.label}${d.phone ? ' ' + fmtPhone(d.phone) + '（' + countryOf(d.phone) + '）' : ''}`,
      `状态：${cnStatus(d.status)}${d.authorized == null ? '' : isOk(d.authorized) ? ' · 已授权' : ' · 未授权'}`];
    if (d.username) L.push(`用户名：@${d.username}`);
    if (d.user_id) L.push(`Telegram ID：${d.user_id}`);
    if (d.premium) L.push('会员：Telegram Premium');
    if (d.spam_until) L.push(`限制解除时间：${fmt(d.spam_until)}`);
    if (d.proxy) L.push(`代理：${d.proxy}`);
    if (d.status_note) L.push(`备注：${detailText(d.status_note)}`);
    if (d.last_check_at) L.push(`上次检查：${fmt(d.last_check_at)}`);
    if (d.login_at) L.push(`登录时间：${fmt(d.login_at)}`);
    if (d.adopted_at) L.push(`本机接管：${fmt(d.adopted_at)}`);
    if (d.last_kick_at) L.push(`上次清设备：${fmt(d.last_kick_at)}`);
    if ((d.tags || []).length) L.push(`标签：${Array.isArray(d.tags) ? d.tags.join('、') : d.tags}`);
    return L.join('\n');
  }

  // stats
  if (d.total !== undefined && d.active !== undefined && !d.items)
    return `统计：共 ${d.total} 个账号` + (d.active != null ? ` · 正常 ${d.active}` : '')
      + (d.unauthorized != null ? ` · 失效 ${d.unauthorized}` : '')
      + Object.keys(d).filter(k => !['total','active','unauthorized','ok'].includes(k)).slice(0, 8)
        .map(k => ` · ${KEY_CN[k] || STATUS_CN[k] || k}=${summaryValue(d[k],k)}`).join('');

  // settings object
  if (d.values || (d.ok && d.saved))
    return d.saved ? `参数已保存：${Array.isArray(d.saved) ? d.saved.join('、') : d.saved}`
      : `当前参数：\n` + Object.entries(d.values || d).slice(0, 20).map(([k,v]) => `• ${k} = ${v}`).join('\n');

  // toolbox single
  if (d.done || Array.isArray(d.errors) || d.logged_out !== undefined || d.remaining_others !== undefined && d.before !== undefined) {
    const parts = [];
    if (d.done) parts.push('完成：' + summaryValue(d.done));
    if (d.errors && d.errors.length) parts.push('问题：' + summaryValue(d.errors));
    if (d.before != null) parts.push(`踢前授权 ${d.before}，剩余其它 ${d.remaining_others}`);
    if (d.logged_out !== undefined) parts.push(d.logged_out ? '已退出登录' : '退出未确认');
    if (parts.length) return parts.join('\n');
  }

  // ZIP / 文件任务
  if (d.output || d.out_dir || d.packs !== undefined || d.files !== undefined)
    return `ZIP 工具完成`
      + (d.output || d.out_dir ? `\n输出：${d.output || d.out_dir}` : '')
      + (d.packs != null ? `\n包数：${summaryValue(d.packs)}` : '')
      + (d.files != null ? `\n文件：${summaryValue(d.files)}` : '')
      + (d.message || d.note ? `\n${detailText(d.message || d.note)}` : '');

  // 上传导入汇总
  if (d.filename && (d.succeeded !== undefined || d.tdata_dirs !== undefined))
    return `上传导入：${d.filename}`
      + (d.tdata_dirs != null ? ` · 发现 tdata ${d.tdata_dirs} 处` : '')
      + (d.succeeded != null ? ` · 成功账号 ${d.succeeded}` : '')
      + (Array.isArray(d.items) ? '\n' + humanize(d.items) : '');

  return genericSummary(d);
}

function out(data, pin) {
  if (logPinned && !pin) return;                 // 已锁定：忽略自动刷新写入
  let human, raw;
  if (typeof data === 'string') { human = detailText(summaryClone(data)); raw = data; }
  else {
    raw = JSON.stringify(data, null, 2);
    try { human = humanize(data); } catch (e) { human = null; }
    if (!human) human = '已记录结果，详情见原始数据。';
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
let _dialogEl = null;
let _dialogReturnFocus = null;
let _dialogKeyHandler = null;
let _dialogTitleSeq = 0;
function dialogFocusable(el) {
  if (!el) return [];
  return [...el.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])')]
    .filter(x => !x.hidden && x.offsetParent !== null);
}
function focusDialog(el, returnFocus) {
  if (!el) return;
  if (_dialogKeyHandler) document.removeEventListener('keydown', _dialogKeyHandler);
  _dialogEl = el;
  _dialogReturnFocus = returnFocus && returnFocus !== document.body ? returnFocus : null;
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  const heading = el.querySelector('h1,h2,h3');
  if (heading) {
    if (!heading.id) heading.id = 'dialog-title-' + (++_dialogTitleSeq);
    el.setAttribute('aria-labelledby', heading.id);
  }
  _dialogKeyHandler = e => {
    if (_dialogEl !== el || !el.classList.contains('on') || e.key !== 'Tab') return;
    const focusables = dialogFocusable(el);
    if (!focusables.length) { e.preventDefault(); el.focus?.(); return; }
    const first = focusables[0], last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  document.addEventListener('keydown', _dialogKeyHandler);
  queueMicrotask(() => {
    if (_dialogEl !== el || !el.classList.contains('on')) return;
    const first = dialogFocusable(el)[0];
    try { (first || el).focus(); } catch (_) {}
  });
}
function releaseDialogFocus(restoreFocus = true) {
  const previous = _dialogReturnFocus;
  if (_dialogKeyHandler) document.removeEventListener('keydown', _dialogKeyHandler);
  _dialogEl = null; _dialogReturnFocus = null; _dialogKeyHandler = null;
  if (restoreFocus && previous && previous.isConnected) setTimeout(() => { try { previous.focus(); } catch (_) {} }, 0);
}
// 独立任务进度：每个后台操作拥有自己的卡片，不再互相覆盖弹窗。
const UI_TASKS = new Map();
let UI_TASK_SEQ = 0;
function uiTask(title) {
  const id = 'task-' + (++UI_TASK_SEQ);
  const host = document.createElement('div');
  host.className = 'ui-task-card'; host.dataset.taskId = id;
  host.setAttribute('aria-live', 'polite');
  host.innerHTML = '<b></b><div class="ui-task-msg"></div><div class="ui-task-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><i></i></div><small></small><button type="button" class="ui-task-open sm" data-onclick="switchView(\'tasks\')">任务中心</button>';
  let root = document.querySelector('#uiTasks');
  if (!root) { root = document.createElement('div'); root.id='uiTasks'; root.className='ui-tasks'; document.body.appendChild(root); }
  root.appendChild(host); UI_TASKS.set(id, host);
  const api = { id, update(o){
    o=o||{};
    const total=Math.max(1, Number(o.total)||1), cur=Math.max(0, Number(o.current)||0);
    const pct=Math.max(0,Math.min(100,Math.round(cur/total*100)));
    const status=String(o.status || (pct >= 100 ? 'done' : 'running')).toLowerCase();
    host.dataset.state=status;
    host.querySelector('b').textContent=o.title||title||'进行中';
    host.querySelector('.ui-task-msg').textContent=o.message||'';
    const bar=host.querySelector('i'); bar.style.width=pct+'%'; bar.setAttribute('aria-valuenow', String(pct));
    host.querySelector('small').textContent=o.text||(''+cur+' / '+total);
    host.setAttribute('aria-label',(o.title||title||'后台任务')+'：'+(o.message||status));
  }, close(){ host.classList.add('ui-task-leaving'); setTimeout(() => { host.remove(); UI_TASKS.delete(id); }, 160); } };
  api.update({title}); return api;
}

function uiClose() {
  const m = $('#mUi');
  if (m) { m.classList.remove('on'); m.classList.remove('progress-floating'); }
  if (_dialogEl === m) releaseDialogFocus();
  // 仅当没有其它 modal 时关遮罩
  const any = [...document.querySelectorAll('.modal.on')].length;
  if (!any) $('#bd').classList.remove('on');
}

let uiFilePending = false;
function uiResolve(ok) {
  if (ok && uiFilePending) return;
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
  const returnFocus = document.activeElement;
  closeAll(); // 关掉其它弹窗，避免叠两层逻辑混乱
  $('#uiTitle').textContent = title || '提示';
  $('#uiMsg').textContent = msg || '';
  $('#uiInputWrap').style.display = 'none';
  const oldFile = $('#uiFilePick'); if (oldFile) oldFile.remove();
  $('#uiProgWrap').style.display = 'none';
  $('#uiActions').style.display = '';
  $('#uiCancel').style.display = '';
  $('#uiOk').style.display = '';
  $('#uiOk').disabled = false;
  $('#uiOk').className = 'primary';
  $('#uiOk').textContent = '确定';
  $('#uiCancel').textContent = '取消';
  const shell = $('#mUi');
  shell.classList.add('on');
  $('#bd').classList.add('on');
  document.body.classList.add('modal-open');
  focusDialog(shell, returnFocus);
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
  uiFilePending = false;
  uiOpenShell(opts.title || '请输入', opts.message || '');
  $('#uiInputWrap').style.display = '';
  $('#uiInputLabel').textContent = opts.label || '内容';
  let inp = $('#uiInput');
  // 多行输入用于批量账号/代理清单，保留统一的确认与取消逻辑。
  if (opts.multiline && inp && inp.tagName !== 'TEXTAREA') {
    const ta = document.createElement('textarea');
    ta.id = 'uiInput'; ta.autocomplete = 'off'; ta.rows = 9;
    ta.style.cssText = 'width:100%;min-height:180px;resize:vertical;font:13px/1.5 ui-monospace,Menlo,Consolas,monospace';
    inp.replaceWith(ta); inp = ta;
  } else if (!opts.multiline && inp && inp.tagName === 'TEXTAREA') {
    const input = document.createElement('input');
    input.id = 'uiInput'; input.autocomplete = 'off';
    inp.replaceWith(input); inp = input;
  }
  inp.type = opts.password ? 'password' : 'text';
  inp.value = opts.value != null ? String(opts.value) : '';
  inp.placeholder = opts.placeholder || '';
  if (opts.danger) {
    $('#uiOk').className = 'danger-solid';
  }
  if (opts.file) {
    const file = document.createElement('input');
    file.id = 'uiFilePick'; file.type = 'file'; file.accept = opts.fileAccept || '.csv,.txt';
    file.style.cssText = 'margin-top:8px;width:100%';
    let readVersion=0;
    file.onchange = async () => {
      const f=file.files&&file.files[0], version=++readVersion;
      if (!f) return;
      uiFilePending=true; $('#uiOk').disabled=true;
      try {
        const value=await f.text();
        if (version!==readVersion || document.getElementById('uiFilePick')!==file) return;
        inp.value=value;
        uiFilePending=false; $('#uiOk').disabled=false;
      } catch(_) {
        if (document.getElementById('uiFilePick')===file) {
          uiFilePending=false;
          $('#uiOk').disabled=false;
          file.value='';
          toast('文件读取失败，请重新选择文件','err');
        }
      }
    };
    $('#uiInputWrap').appendChild(file);
  }
  $('#uiOk').textContent = opts.okText || '确定';
  setTimeout(() => { try { inp.focus(); inp.select(); } catch (_) {} }, 30);
  inp.onkeydown = (e) => {
    if (e.key === 'Enter' && inp.tagName !== 'TEXTAREA') { e.preventDefault(); uiResolve(true); }
  };
  return new Promise(resolve => { _uiResolver = resolve; });
}

TAO_KEYS.esc('ui-dialog', 100, () => {
  const confirm = document.querySelector('#mAiConfirm.modal.on');
  return !!confirm || (!!_uiResolver && _uiMode !== 'progress' && !!document.querySelector('#mUi.on'));
}, () => {
  const confirm = document.querySelector('#mAiConfirm.modal.on');
  if (confirm && typeof confirm.__taoEscClose === 'function') return confirm.__taoEscClose();
  uiResolve(false);
});

/** 显示/更新/关闭进度。传 null 关闭。 */
let legacyProgressCard = null;
function uiProgress(opts) {
  if (!opts) { if (legacyProgressCard) legacyProgressCard.close(); legacyProgressCard=null; return; }
  if (!legacyProgressCard) legacyProgressCard=uiTask(opts.title);
  legacyProgressCard.update(opts);
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
async function runPool(ids, worker, title, concurrency, waitForCompletion = false, maxConcurrency = 8) {
  const list = (ids || []).map(Number).filter(n => Number.isFinite(n));
  const total = list.length;
  const conc = Math.max(1, Math.min(Math.floor(Number(concurrency)) || 1, maxConcurrency));
  const results = new Array(total);
  let done = 0, ok = 0, fail = 0;
  let cursor = 0;
  const title0 = title || '批量处理';
  const task = uiTask(title0);

  function paint(currentLabel, extra) {
    const cur = Math.min(done + (currentLabel ? 1 : 0), total) || (total ? 1 : 0);
    task.update({
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
    task.close();
    return {total: 0, ok: 0, failed: 0, results: []};
  }
  paint('准备中…');

  async function one(idx, id) {
    const lab = accLabel(id);
    const tmo = opTimeoutMs();
    paint(lab + ' 进行中（限' + Math.round(tmo / 1000) + 's）');
    try {
      const r = waitForCompletion
        ? await worker(id, idx)
        : await withTimeout(worker(id, idx), tmo, lab);
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
  task.close();
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
  const returnFocus = document.activeElement;
  closeAll();
  const modal = $('#' + id);
  modal.classList.add('on');
  $('#bd').classList.add('on');
  document.body.classList.add('modal-open');
  focusDialog(modal, returnFocus);
  TAO_MOTION.open(modal);
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

function closeAll(restoreFocus = true) {
  closeMenus(); closePalette(restoreFocus);
  clearOfficialCode();
  document.querySelectorAll('.modal').forEach(m => { m.classList.remove('on'); TAO_MOTION.close(m); });
  const dr = $('#accountDetailDrawer');
  if (dr) { dr.classList.remove('on'); TAO_MOTION.close(dr); dr.setAttribute('aria-hidden', 'true'); }
  const ai = $('#aiPanel');
  if (ai) { ai.classList.remove('on'); TAO_MOTION.close(ai); ai.setAttribute('aria-hidden', 'true'); }
  $('#aiFab')?.setAttribute('aria-expanded', 'false');
  $('#bd').classList.remove('on');
  document.body.classList.remove('modal-open');
  releaseDialogFocus(restoreFocus);
  if (_uiResolver && _uiMode !== 'progress') {
    const r = _uiResolver; _uiResolver = null; r(_uiMode === 'prompt' ? null : false);
  }
}
TAO_KEYS.esc('close-all', 0, () => !!(
  document.querySelector('.modal.on:not(#mUi):not(#mAiConfirm), #accountDetailDrawer.on')
  || (document.body && document.body.classList.contains('modal-open'))),
() => closeAll());

/* ---------- 数据加载 ---------- */
async function refresh() {
  try {
    const [s, a] = await Promise.all([api('/api/stats'), api('/api/accounts')]);
    stats = s; setAccounts(a);
    showConnError('');
    if (a.length && a[0].server_now) syncClock(a[0].server_now);
    render(); loadLogs(); loadKick(); loadTasks(); loadLeads();
    loadSettings(); loadOps(); renderZt();
  } catch (e) { toast('加载失败：' + e.message, 'err'); out({error: String(e)}, true); showConnError(e, String(e.message||'').includes('401')); }
}
async function loadKick() {
  try {
    const prevKickHours = kick && kick.hours;
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
    $('#kickText').title = $('#kickText').textContent;
    // 输入框只在用户没在编辑时回填，避免把手里正在敲的字冲掉
    const ri = $('#kickRetry');
    if (ri && document.activeElement !== ri) ri.value = kick.retry_after_text ? retryToInput(kick.retry_after_s) : '';
    if (kick.due_now && kick.max_overdue_s > 3 * (kick.scan_interval_s || 600))
      $('#kickText').textContent += ' · ⚠ 最久一个已逾期 ' + (kick.max_overdue_s / 86400).toFixed(1)
        + ' 天未执行，请看日志排查';
    if (kick.hours !== prevKickHours) render();   // 列表的“清设备”列依赖 kick.hours，只在它变化时重画
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
    showConnError('');
  } catch (e) {
    $('#mode').textContent = String(e.message || '').indexOf('401') >= 0 ? '令牌无效' : '未连接';
    $('#mode').className = 'pill off';
    showConnError(e, String(e.message||'').includes('401'));
  }
}

function showConnError(e, unauth) {
  const bar = $('#connBar'); if (!bar) return;
  if (!e) { bar.classList.remove('on'); return; }
  const raw = String((e && e.message) || e || '').trim();
  const hint = unauth ? '' : (/fetch|network|load failed/i.test(raw) ? '（后端没有响应：确认 python -m tam.cli serve 在运行，且你打开的是它的地址，如 http://127.0.0.1:8848）' : '');
  $('#connMsg').textContent = '无法加载数据：' + (raw || '未知错误') + hint;
  $('#connSettings').style.display = unauth ? '' : 'none';
  bar.classList.add('on');
}
function retryConnect() { probeMode(); refresh(); }
const viewKey = 'tam.view';
const VIEW_NAMES = {accounts:'账号清单', tasks:'任务中心', leads:'线索库', toolbox:'工具', logs:'日志', settings:'设置'};
let currentView = 'accounts';
/* ======================================================================
 * 界面层（重构新增）：主题 · 弹出菜单 · 操作清单 + 命令面板 · 左栏统计
 * 只负责"入口怎么找到"，不改任何业务函数；所有操作最终调用原有处理器。
 * ====================================================================== */

/* ---------- 主题 ---------- */
const themeKey = 'tao.theme';
function currentTheme() { return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'; }
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  const use = document.querySelector('#themeBtn use');
  if (use) use.setAttribute('href', t === 'dark' ? '#i-sun' : '#i-moon');
  const btn = $('#themeBtn');
  if (btn) btn.setAttribute('aria-label', t === 'dark' ? '切换到浅色主题' : '切换到深色主题');
}
function toggleTheme() {
  const t = currentTheme() === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem(themeKey, t); } catch (_) {}
  applyTheme(t);
}
applyTheme(currentTheme());
if (window.matchMedia) {
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  const follow = e => { let saved = null; try { saved = localStorage.getItem(themeKey); } catch (_) {} if (!saved) applyTheme(e.matches ? 'dark' : 'light'); };
  if (mq.addEventListener) mq.addEventListener('change', follow);
}
(function () {
  const k = $('#cmdkKey');
  if (k) k.textContent = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? '⌘ K' : 'Ctrl K';
})();

/* ---------- 弹出菜单：fixed 定位，按触发按钮摆放，空间不足自动翻转 ---------- */
let MENU_OPEN = null;   // {menu, trigger}
function focusMenu(menu) { return [...menu.querySelectorAll('button:not(:disabled),input:not(:disabled)')].filter(el => el.getClientRects().length); }
function isSheetLayout() { return window.innerWidth <= 720; }
function closeMenus() {
  if (!MENU_OPEN) return;
  const { menu, trigger } = MENU_OPEN;
  menu.classList.remove('open');
  if (trigger) { trigger.setAttribute('aria-expanded', 'false'); }
  $('#menuScrim').classList.remove('on');
  MENU_OPEN = null;
  if (trigger?.isConnected) trigger.focus({preventScroll:true});
}
function placeMenu(menu, trigger) {
  if (isSheetLayout()) { menu.style.left = menu.style.top = ''; return; }
  const r = trigger.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight, gap = 8;
  menu.style.visibility = 'hidden';
  menu.classList.add('open');
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  let left = r.left;
  if (left + mw > vw - 12) left = Math.max(12, r.right - mw);
  let top = r.bottom + gap;
  const above = r.top - mh - gap;
  if (top + mh > vh - 12 && above >= 12) top = above;
  top = Math.max(12, Math.min(top, vh - mh - 12));
  menu.style.left = Math.round(left) + 'px';
  menu.style.top = Math.round(top) + 'px';
  menu.style.visibility = '';
}
function toggleMenu(trigger, id) {
  const menu = $('#' + id);
  if (!menu) return;
  const wasOpen = MENU_OPEN && MENU_OPEN.menu === menu;
  closeMenus();
  if (wasOpen) return;
  placeMenu(menu, trigger);
  menu.classList.add('open');
  trigger.setAttribute('aria-expanded', 'true');
  $('#menuScrim').classList.add('on');
  MENU_OPEN = { menu, trigger };
  const first = focusMenu(menu)[0];
  if (first) { try { first.focus({ preventScroll: true }); } catch (_) {} }
}
window.addEventListener('resize', closeMenus, {passive: true});
document.addEventListener('keydown', e => {
  if (MENU_OPEN && e.key === 'Tab') {
    const items = focusMenu(MENU_OPEN.menu), first = items[0], last = items.at(-1);
    if (!first) return;
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  if (MENU_OPEN && (e.key === 'ArrowDown' || e.key === 'ArrowUp') && MENU_OPEN.menu.getAttribute('role') === 'menu') {
    const items = focusMenu(MENU_OPEN.menu).filter(el => el.matches('.menu-item'));
    if (!items.length) return;
    const i = items.indexOf(document.activeElement);
    const next = e.key === 'ArrowDown' ? (i + 1) % items.length : (i <= 0 ? items.length - 1 : i - 1);
    items[next].focus(); e.preventDefault();
  }
}, true);
TAO_KEYS.esc('menu', 90, () => !!MENU_OPEN, () => closeMenus());

/* ---------- 操作清单：所有功能的唯一登记处 ----------
 * group: 面板里的分组名；sel: 需要先在账号清单勾选账号；danger: 红色并带 ⚠；
 * write: 只读模式下不可用；kw: 额外搜索词（中英文、别名）。
 * 新增功能只需在这里加一行，就会出现在命令面板里。 */
const ACTIONS = [
  // 跳转
  {id:'go-accounts', g:'跳转', label:'账号清单', icon:'accounts', kw:'accounts 账号 列表', run:() => switchView('accounts')},
  {id:'go-tasks', g:'跳转', label:'任务中心', icon:'tasks', kw:'tasks 任务 群发 采集', run:() => switchView('tasks')},
  {id:'go-leads', g:'跳转', label:'线索库', icon:'leads', kw:'leads 线索 发言人', run:() => switchView('leads')},
  {id:'go-tools', g:'跳转', label:'工具箱', icon:'tools', kw:'tools 工具 文件转换 zip tdata session', run:() => switchView('toolbox')},
  {id:'go-logs', g:'跳转', label:'操作日志', icon:'file', kw:'logs 日志 记录', run:() => switchView('logs')},
  {id:'go-settings', g:'跳转', label:'设置', icon:'sliders', kw:'settings 设置 参数 令牌 token 连接', run:() => switchView('settings')},
  // 添加与导入
  {id:'add', g:'添加与导入', label:'添加单个账号', icon:'user-plus', kw:'add new 新增 手动', run:() => openModal('mAdd')},
  {id:'import-list', g:'添加与导入', label:'批量导入手机号清单', icon:'upload', kw:'import 导入 批量 手机号', run:() => openModal('mImport')},
  {id:'import-session', g:'添加与导入', label:'导入 session', icon:'file', kw:'session 导入', run:() => openModal('mSession')},
  {id:'import-tdata', g:'添加与导入', label:'导入 tdata', icon:'upload', kw:'tdata 导入', run:() => openModal('mTdata')},
  {id:'one-stop', g:'添加与导入', label:'一条龙处理', icon:'zap', kw:'one stop wizard 一条龙 向导', run:() => oneStopWizard()},
  {id:'one-stop-history', g:'添加与导入', label:'一条龙记录 / 继续 / 导出', icon:'history', kw:'history 记录 继续 导出', run:() => oneStopHistory().catch(e => toast(e.message, 'err'))},
  // 已选账号
  {id:'b-check', g:'已选账号', label:'健康检查', icon:'pulse', sel:true, kw:'check health 检查 状态 二验', run:() => bulk('check')},
  {id:'b-auto', g:'已选账号', label:'自动登录', icon:'key', sel:true, write:true, kw:'login auto 登录 接码 验证码', run:() => bulk('auto')},
  {id:'b-send', g:'已选账号', label:'发消息', icon:'send', sel:true, write:true, kw:'send message 消息 私信', run:() => openModal('mSend')},
  {id:'b-tag', g:'已选账号', label:'打标签', icon:'tag', sel:true, write:true, kw:'tag 标签 分组', run:() => bulkTag()},
  {id:'b-regen', g:'已选账号', label:'重生会话', icon:'refresh', sel:true, write:true, kw:'regen session 重生 会话', run:() => bulkRegen()},
  {id:'b-proxy-audit', g:'已选账号', label:'代理体检', icon:'globe', sel:true, kw:'proxy audit 代理 体检 检测', run:() => proxyAudit()},
  {id:'b-proxy-replace', g:'已选账号', label:'更换代理', icon:'globe', sel:true, write:true, kw:'proxy replace 代理 更换 节点', run:() => bulkReplaceProxy()},
  {id:'b-okpay', g:'已选账号', label:'OKPay 余额', icon:'wallet', sel:true, kw:'okpay balance 余额', run:() => bulkOkpay()},
  {id:'b-export-pack', g:'已选账号', label:'导出号包', icon:'download', sel:true, write:true, kw:'export pack 导出 号包', run:() => bulkExport('pack')},
  {id:'b-export-session', g:'已选账号', label:'导出 .session', icon:'download', sel:true, write:true, kw:'export session 导出', run:() => bulkExport('session')},
  {id:'b-export-links', g:'已选账号', label:'导出取码链接', icon:'download', sel:true, write:true, kw:'export code link 取码 链接 导出', run:() => bulkExportCodeLinks()},
  {id:'b-warmup', g:'已选账号', label:'养号', icon:'flame', sel:true, write:true, kw:'warmup 养号', run:() => bulkWarmup()},
  {id:'b-terminate', g:'已选账号', label:'踢出其它设备', icon:'phone', sel:true, write:true, kw:'terminate kick 踢出 设备 清设备', run:() => bulkTerminate()},
  {id:'b-lock', g:'已选账号', label:'批量锁定', icon:'lock', sel:true, kw:'lock 锁定 防删除', run:() => bulkLock(true)},
  {id:'b-unlock', g:'已选账号', label:'批量解锁', icon:'unlock', sel:true, kw:'unlock 解锁', run:() => bulkLock(false)},
  {id:'b-relogin', g:'已选账号', label:'退出并重登', icon:'logout', sel:true, write:true, danger:true, kw:'relogin logout 退出 重登', run:() => bulk('relogin')},
  {id:'b-delete', g:'已选账号', label:'删除本地', icon:'trash', sel:true, write:true, danger:true, kw:'delete 删除 本地', run:() => bulkDelete()},
  {id:'b-delete-tg', g:'已选账号', label:'注销 TG 账号', icon:'trash', sel:true, write:true, danger:true, kw:'delete telegram 注销 账号', run:() => bulkDeleteTelegram()},
  // 筛选
  {id:'f-twofa', g:'筛选', label:'只选改密成功的账号', icon:'check', kw:'twofa 改密 成功 二验', run:() => { switchView('accounts'); selectTwofaSuccess(); }},
  {id:'f-clear', g:'筛选', label:'清除全部筛选', icon:'x', kw:'clear filter 清除 筛选', run:() => { switchView('accounts'); clearFilters(); }},
  {id:'f-select-all', g:'筛选', label:'全选当前列表', icon:'check', kw:'select all 全选', run:() => { switchView('accounts'); toggleAll(true); }},
  // 任务
  {id:'t-collect', g:'任务', label:'采集发言人', icon:'leads', write:true, kw:'collect speakers 采集 发言人 群组', run:() => { switchView('tasks'); newCollectTask(); }},
  {id:'t-send', g:'任务', label:'新建群发', icon:'send', write:true, kw:'broadcast message task 群发', run:() => { switchView('tasks'); newMessageTask(); }},
  {id:'t-leads-export', g:'任务', label:'导出线索 CSV', icon:'download', kw:'leads csv 线索 导出', run:() => exportLeads()},
  // 系统
  {id:'s-doctor', g:'系统', label:'一键体检', icon:'pulse', kw:'doctor 体检 环境 依赖', run:() => runDoctor()},
  {id:'s-refresh', g:'系统', label:'刷新数据', icon:'refresh', kw:'refresh 刷新 重新加载', run:() => refresh()},
  {id:'s-ai', g:'系统', label:'打开 AI 助手', icon:'spark', write:true, kw:'ai assistant 助手 对话', run:() => openAiPanel()},
  {id:'s-help', g:'系统', label:'使用帮助', icon:'help', kw:'help 帮助 文档', run:() => openHelp()},
  {id:'s-errlog', g:'系统', label:'错误日志', icon:'alert', kw:'error log 错误 日志 上报', run:() => openErrorLog()},
  {id:'s-theme', g:'系统', label:'切换明暗主题', icon:'moon', kw:'theme dark light 主题 深色 浅色 夜间', run:() => toggleTheme()},
  {id:'s-reload', g:'系统', label:'热重载后端', icon:'refresh', write:true, danger:true, kw:'reload restart 热重载 重启', run:() => hotReload()},
  {id:'s-shutdown', g:'系统', label:'关闭项目', icon:'power', danger:true, kw:'shutdown 关闭 退出 停止', run:() => shutdownService()},
];

/* ---------- 命令面板 ---------- */
const PAL = { trigger:null, open:false, items:[], idx:0, recentKey:'tao.palette.recent' };
function palRecent() { try { const ids = JSON.parse(localStorage.getItem(PAL.recentKey) || '[]'); return Array.isArray(ids) ? ids.filter(x=>typeof x==='string') : []; } catch (_) { return []; } }
function palRemember(id) {
  const r = [id].concat(palRecent().filter(x => x !== id)).slice(0, 6);
  try { localStorage.setItem(PAL.recentKey, JSON.stringify(r)); } catch (_) {}
}
function palIcon(name) { return '<svg class="i"><use href="#i-' + name + '"/></svg>'; }
function palActionState(a) {
  if (a.write && typeof readonly !== 'undefined' && readonly) return '只读模式下不可用';
  if (a.sel && !sel.size) return '需先勾选账号';
  if (a.sel) return '已选 ' + sel.size + ' 个';
  return '';
}
function palScore(a, q) {
  if (!q) return 1;
  const label = a.label.toLowerCase(), hay = (a.label + ' ' + a.g + ' ' + (a.kw || '')).toLowerCase();
  let score = 0;
  for (const tok of q.split(/\s+/).filter(Boolean)) {
    if (label.startsWith(tok)) score += 6;
    else if (label.includes(tok)) score += 4;
    else if (hay.includes(tok)) score += 2;
    else return 0;
  }
  return score;
}
const SET_ALIAS = {
  CONCURRENCY: '并发 并行', PARALLEL: '并发 并行', TIMEOUT: '超时',
  PROXY: '代理', RETRY: '重试', CODE: '验证码 接码',
  POLL: '轮询 间隔', INTERVAL: '轮询 间隔', API_ID: '密钥 api', API_HASH: '密钥 api'
};
function settingAliasText(key) {
  const up = String(key || '').toUpperCase();
  return [...new Set(Object.entries(SET_ALIAS)
    .filter(([part]) => up.includes(part))
    .flatMap(([, words]) => words.split(/\s+/)))].join(' ');
}
function palSettingHits(q) {
  if (!q || typeof SET_SPECS === 'undefined' || !SET_SPECS) return [];
  return Object.values(SET_SPECS).map(spec => {
    const label = String(spec.desc || spec.key || '');
    const a = {label, g:'设置项', kw:[spec.key, spec.group, spec.desc, settingAliasText(spec.key)].filter(Boolean).join(' ')};
    return {spec, s: palScore(a, q)};
  }).filter(x => x.s > 0).sort((a, b) => b.s - a.s || String(a.spec.key).localeCompare(String(b.spec.key))).slice(0, 6);
}
function palBuild(q) {
  q = (q || '').trim().toLowerCase();
  const rows = [];
  const byId = Object.fromEntries(ACTIONS.map(a => [a.id, a]));
  if (!q) {
    const rec = palRecent().map(id => byId[id]).filter(Boolean);
    if (rec.length) { rows.push({head:'最近使用'}); rec.forEach(a => rows.push({action:a})); }
    const shown = new Set(rec.map(a => a.id));
    let last = null;
    ACTIONS.filter(a => !shown.has(a.id)).forEach(a => {
      if (a.g !== last) { rows.push({head:a.g}); last = a.g; }
      rows.push({action:a});
    });
    return rows;
  }
  const accs = (accounts || []).filter(a => [a.label, a.phone, a.username, (a.tags || []).join(' ')].join(' ').toLowerCase().includes(q)).slice(0, 6);
  if (accs.length) { rows.push({head:'账号'}); accs.forEach(a => rows.push({account:a})); }
  const rec = palRecent();
  const hit = ACTIONS.map(a => { const score = palScore(a, q); return {a, s: score ? score + (rec.includes(a.id) ? 1 : 0) : 0}; }).filter(x => x.s > 0).sort((x, y) => y.s - x.s).slice(0, 12);
  const settings = palSettingHits(q);
  const appendSettings = () => { if (settings.length) { rows.push({head:'设置项'}); settings.forEach(({spec}) => rows.push({setting:spec})); } };
  const appendActions = () => {
    let last = null;
    hit.forEach(({a}) => { if (a.g !== last) { rows.push({head:a.g}); last = a.g; } rows.push({action:a}); });
  };
  const actionMax = hit.length ? Math.max(...hit.map(({a}) => palScore(a, q))) : 0;
  if (settings.length && settings[0].s > actionMax) appendSettings();
  appendActions();
  if (!settings.length || settings[0].s <= actionMax) appendSettings();
  return rows;
}
function palSettingHint(spec) {
  if (spec.type === 'secret') return spec.set ? '已设置' : '未设置';
  const raw = Object.prototype.hasOwnProperty.call(SET_ORIG || {}, spec.key) ? SET_ORIG[spec.key] : spec.value;
  if (spec.type === 'bool') return '当前 ' + (['1', 'true', 'on', 'yes', '开', '是'].includes(String(raw).toLowerCase()) ? '开' : '关');
  const text = String(raw === null || raw === undefined ? '' : raw);
  return '当前 ' + (text.length > 18 ? text.slice(0, 17) + '…' : text);
}
function palRender() {
  const q = $('#palInput').value;
  const rows = palBuild(q);
  const list = $('#palList');
  PAL.items = rows.filter(r => !r.head);
  if (PAL.idx >= PAL.items.length) PAL.idx = Math.max(0, PAL.items.length - 1);
  if (!PAL.items.length) { $('#palInput').removeAttribute('aria-activedescendant'); list.innerHTML = '<div class="pal-empty">没有匹配的账号、操作或参数。</div>'; return; }
  let n = -1;
  list.innerHTML = rows.map(r => {
    if (r.head) return '<div class="pal-group">' + esc(r.head) + '</div>';
    n++;
    const cls = 'pal-item' + (n === PAL.idx ? ' sel' : '');
    if (r.account) {
      const a = r.account;
      const stCn = (typeof STAT_CN !== 'undefined' && STAT_CN[a.status]) || a.status;
      return '<button type="button" class="' + cls + '" role="option" id="pal-option-' + n + '" aria-selected="' + (n === PAL.idx) + '" tabindex="-1" data-i="' + n + '">' + palIcon('accounts') +
        '<span class="pal-main"><span>' + esc(a.label) + '</span><small>' + esc(fmtPhone(a.phone)) + (a.username ? ' · @' + esc(a.username) : '') + '</small></span>' +
        '<span class="pal-hint">' + esc(stCn) + ' · 查看详情</span></button>';
    }
    if (r.setting) {
      const s = r.setting;
      const small = String(s.key || '') + ' · ' + String(s.group || '设置项') + (s.restart_required ? ' ↺' : '');
      return '<button type="button" class="' + cls + '" role="option" id="pal-option-' + n + '" aria-selected="' + (n === PAL.idx) + '" tabindex="-1" data-i="' + n + '">' + palIcon('sliders') +
        '<span class="pal-main"><span>' + esc(s.desc || s.key) + '</span><small>' + esc(small) + '</small></span>' +
        '<span class="pal-hint">' + esc(palSettingHint(s)) + '</span></button>';
    }
    const a = r.action, st = palActionState(a);
    const blocked = !!st && (st.indexOf('只读') >= 0 || st.indexOf('需先') >= 0);
    return '<button type="button" class="' + cls + (a.danger ? ' danger' : '') + (blocked ? ' dim' : '') + '" role="option" id="pal-option-' + n + '" aria-selected="' + (n === PAL.idx) + '" tabindex="-1" data-i="' + n + '">' + palIcon(a.icon || 'arrow') +
      '<span class="pal-main"><span>' + (a.danger ? '⚠ ' : '') + esc(a.label) + '</span></span>' +
      (st ? '<span class="pal-hint">' + esc(st) + '</span>' : '') + '</button>';
  }).join('');
  const cur = list.querySelector('.pal-item.sel');
  if (cur) { $('#palInput').setAttribute('aria-activedescendant',cur.id); cur.scrollIntoView({block:'nearest'}); }
}
function openPalette(prefill) {
  closeMenus();
  PAL.trigger = document.activeElement;
  PAL.open = true; PAL.idx = 0;
  $('#palBackdrop').classList.add('on');
  $('#pal').classList.add('on');
  $('#pal').setAttribute('aria-hidden','false');
  $('#palInput').setAttribute('aria-expanded','true');
  const inp = $('#palInput');
  inp.value = prefill || '';
  palRender();
  if (typeof SET_SPECS !== 'undefined' && !Object.keys(SET_SPECS || {}).length && typeof loadSettings === 'function') {
    Promise.resolve().then(() => loadSettings()).catch(() => {}).then(() => { if (PAL.open && inp.value.trim()) palRender(); });
  }
  setTimeout(() => { if (PAL.open) inp.focus(); }, 0);
}
function closePalette(restoreFocus = true) {
  if (!PAL.open) return;
  PAL.open = false;
  $('#palBackdrop').classList.remove('on');
  $('#pal').classList.remove('on');
  $('#pal').setAttribute('aria-hidden','true');
  $('#palInput').setAttribute('aria-expanded','false');
  const trigger = PAL.trigger; PAL.trigger = null;
  if (restoreFocus && trigger?.isConnected) trigger.focus({preventScroll:true});
}
TAO_KEYS.esc('command-palette', 70, () => !!(typeof PAL !== 'undefined' && PAL.open), () => closePalette(true));
function waitForPaletteSetting(key) {
  const id = setInputId(key), existing = document.getElementById(id);
  if (existing) return Promise.resolve(existing);
  return new Promise(resolve => {
    let done = false, off = () => {}, timer = null;
    const finish = el => { if (done) return; done = true; if (timer) clearTimeout(timer); off(); resolve(el || null); };
    timer = setTimeout(() => finish(document.getElementById(id)), 2000);
    if (typeof TAO_DOM !== 'undefined' && typeof TAO_DOM.onAdded === 'function') {
      off = TAO_DOM.onAdded('[id]', () => { const el = document.getElementById(id); if (el) finish(el); });
      if (done) off();
    }
  });
}
async function palRunSetting(spec) {
  closeAll(false); switchView('settings');
  if (typeof SET_LOAD_P !== 'undefined' && SET_LOAD_P) {
    try { await SET_LOAD_P; } catch (_) {}
  }
  const input = await waitForPaletteSetting(spec.key);
  if (!input) { toast('参数尚未加载', 'err'); return; }
  const row = input.closest('.fx-set-row, .row');
  const search = document.querySelector('.fx-set-search input');
  const hidden = row && (row.style.display === 'none' || row.hidden || (typeof getComputedStyle === 'function' && getComputedStyle(row).display === 'none'));
  if (search && search.value.trim() && hidden) { search.value = ''; search.dispatchEvent(new Event('input', {bubbles: true})); }
  if (row?.scrollIntoView) row.scrollIntoView({block:'center'});
  try { input.focus({preventScroll:true}); } catch (_) { input.focus(); }
  if (row) {
    row.classList.add('fx-set-hit');
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let anim = null;
    if (!reduced && typeof row.animate === 'function') {
      try {
        anim = row.animate([{opacity:0}, {opacity:1}, {opacity:0}], {
          duration:1200, easing:'ease-out', pseudoElement:'::after'
        });
      } catch (_) {}
    }
    setTimeout(() => { anim?.cancel(); row.classList.remove('fx-set-hit'); }, 1200);
  }
}
function palRun(i) {
  const row = PAL.items[i];
  if (!row) return;
  if (row.account) { closeAll(false); switchView('accounts'); openDrawer(row.account.id); return; }
  if (row.setting) return palRunSetting(row.setting).catch(e => toast(e.message || String(e), 'err'));
  const a = row.action;
  const st = palActionState(a);
  if (st.indexOf('只读') >= 0) { toast('当前是只读模式，该操作不可用', 'err'); return; }
  // A committed navigation dismisses overlays, but cancelling the palette keeps drafts open.
  const navigate = a.id.startsWith('go-') || (a.sel && !sel.size);
  if (navigate) closeAll(false); else closePalette();
  if (a.sel && !sel.size) { switchView('accounts'); toast('「' + a.label + '」需要先在账号清单里勾选账号', 'err'); return; }
  palRemember(a.id);
  try {
    const r = a.run();
    if (navigate) { const ws = $('#workspace'); if (ws) { ws.setAttribute('tabindex', '-1'); ws.focus({preventScroll:true}); } }
    if (r && r.catch) r.catch(e => toast(e.message || String(e), 'err'));
  }
  catch (e) { toast(e.message || String(e), 'err'); }
}
(function bindPalette() {
  const inp = $('#palInput'), list = $('#palList');
  inp.addEventListener('input', () => { PAL.idx = 0; palRender(); });
  inp.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { PAL.idx = Math.min(PAL.items.length - 1, PAL.idx + 1); palRender(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { PAL.idx = Math.max(0, PAL.idx - 1); palRender(); e.preventDefault(); }
    else if (e.key === 'Enter') { palRun(PAL.idx); e.preventDefault(); }
    else if (e.key === 'Tab' && PAL.open) { e.preventDefault(); inp.focus(); }
  });
  list.addEventListener('click', e => { const b = e.target.closest('.pal-item'); if (b) palRun(Number(b.dataset.i)); });
  list.addEventListener('pointermove', e => {
    const b = e.target.closest('.pal-item'); if (!b) return;
    const i = Number(b.dataset.i);
    if (i !== PAL.idx) { PAL.idx = i; list.querySelectorAll('.pal-item').forEach(x => { const on = Number(x.dataset.i) === i; x.classList.toggle('sel',on); x.setAttribute('aria-selected',String(on)); }); $('#palInput').setAttribute('aria-activedescendant',b.id); }
  });
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); PAL.open ? closePalette() : openPalette(); }
  });
})();

/* ---------- 筛选按钮角标：显示有几项筛选正在生效 ---------- */
function syncFilterBadge() {
  const btn = $('#filterBtn'), badge = $('#filterBadge');
  if (!btn || !badge) return;
  const n = (quickFilter ? 1 : 0);
  badge.textContent = n ? String(n) : '';
  btn.classList.toggle('has-filter', n > 0);
}

/* ---------- 左栏：账号统计（深色总数卡 + 磨砂占比环） ---------- */
const STAT_CN = {total:'账号总数', active:'已登录', new:'新建', unauthorized:'未授权',
  restricted:'受限', banned:'封禁', frozen:'冻结', error:'错误',
  spam_block:'限制中', spam_block_perm:'永久限制', flood_wait:'限流等待'};
function statCn(k) { return STAT_CN[k] || k; }
function ringSvg(pct) {
  // 点状圆环：底圈是浅色点，已登录占比用强调色点覆盖（mask 按百分比裁出弧段）
  const R = 58, C = 2 * Math.PI * R;
  const p = Math.max(0, Math.min(100, pct));
  const dots = 'stroke-dasharray="0.01 7.2" stroke-linecap="round" fill="none"';
  return '<svg class="ring" viewBox="0 0 150 150" role="img" aria-label="已登录占比 ' + p + '%">' +
    '<defs><mask id="ringMask" maskUnits="userSpaceOnUse" x="0" y="0" width="150" height="150">' +
    '<circle cx="75" cy="75" r="' + R + '" fill="none" stroke="#fff" stroke-width="16" stroke-dasharray="' + (C * p / 100).toFixed(1) + ' ' + C.toFixed(1) + '"/></mask></defs>' +
    '<g transform="rotate(-90 75 75)">' +
    '<circle cx="75" cy="75" r="' + R + '" stroke="var(--line-strong)" stroke-width="3.6" ' + dots + '/>' +
    '<circle cx="75" cy="75" r="' + R + '" stroke="var(--accent)" stroke-width="5" ' + dots + ' mask="url(#ringMask)"/></g>' +
    '<text x="75" y="82" text-anchor="middle">' + p + '%</text>' +
    '<text x="75" y="104" text-anchor="middle" class="sub">已登录</text></svg>';
}
function renderStats() {
  const box = $('#accountOverview');
  if (!box) return;
  document.body.classList.remove('no-rail');
  const total = stats.total || 0, by = stats.by_status || {};
  const order = ['active', 'new', 'unauthorized', 'restricted', 'banned'];
  const keys = order.filter(k => by[k] != null).concat(Object.keys(by).filter(k => order.indexOf(k) < 0));
  const list = keys.map(k => '<li><button type="button" class="' + (filter === k ? 'on' : '') + '" data-status="' + esc(k) + '" title="按「' + esc(statCn(k)) + '」筛选">' +
    '<span class="dot s-' + esc(k) + '"></span>' + esc(statCn(k)) + '<b>' + (Number(by[k]) || 0) + '</b></button></li>').join('');
  const active = Number(by.active) || 0;
  const pct = total ? Math.round(active * 100 / total) : 0;
  setHTML(box,
    '<section class="stat-card ink"><div class="stat-label">账号总数</div><div class="stat-num">' + total + '</div>' +
    (list ? '<ul class="stat-list">' + list + '</ul>' : '') + '</section>' +
    '<section class="stat-card glass" aria-label="已登录占比">' + ringSvg(pct) + '<div class="ring-title">' + active + ' / ' + total + ' 个账号在线</div></section>');
}

document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('.stat-list button[data-status]');
  if (!b) return;
  const k = b.dataset.status;
  setFilter(filter === k ? '' : k);
});

/* ---------- 视图：账号 / 任务 / 线索 / 工具（含文件转换）/ 日志 / 设置 ---------- */
const MORE_VIEWS = ['logs', 'settings'];

function loadViewPrefs() {
  try { const saved = localStorage.getItem(viewKey); if (saved === 'ziptools') currentView = 'toolbox'; else if (saved && VIEW_NAMES[saved]) currentView = saved; } catch (e) {}
}
function applyView() {
  document.querySelectorAll('[data-nav]').forEach(el => {
    const on = el.dataset.nav === currentView;
    el.classList.toggle('active', on);
    el.setAttribute('aria-current', on ? 'page' : 'false');
  });
  const moreOn = MORE_VIEWS.indexOf(currentView) >= 0;
  document.querySelectorAll('[data-nav-group="more"]').forEach(el => el.classList.toggle('active', moreOn));
  document.querySelectorAll('[data-view]').forEach(el => {
    const on = !el.dataset.view || el.dataset.view === currentView;
    el.classList.toggle('view-hidden', !on);
    el.setAttribute('aria-hidden', on ? 'false' : 'true');
  });
  document.body.classList.toggle('accounts-workspace', currentView === 'accounts');
}
function switchView(view) {
  if (view === 'ziptools') view = 'toolbox';
  if (!VIEW_NAMES[view]) return;
  currentView = view;
  closeMenus();
  try { localStorage.setItem(viewKey, view); } catch (e) {}
  applyView();
  if (view === 'tasks') loadTasks();
  if (view === 'leads') loadLeads();
  if (view === 'toolbox') { loadOps(); renderZt(); }
  if (view === 'settings') loadSettings();
  const ws = $('#workspace'); if (ws) { ws.scrollTop = 0; TAO_MOTION.view(ws,{defer:true}); }
}
function updateNavBadges() {
  const a = $('#navAccountCount'); if (a) { a.textContent = String(accounts.length || 0); a.dataset.zero = accounts.length ? '0' : '1'; }
  const t = $('#navTaskCount');
  if (t && $('#taskList')) { const n = $('#taskList').querySelectorAll('.taskrow').length || 0; t.textContent = String(n); t.dataset.zero = n ? '0' : '1'; }
}

/* ---------- 渲染 ---------- */
function accountMatches(a, statusFilter = filter, smartFilter = quickFilter, query = null) {
  if (statusFilter && a.status !== statusFilter) return false;
  if (smartFilter === 'noauth' && a.authorized) return false;
  if (smartFilter === 'active' && !(a.authorized || a.status === 'active')) return false;
  if (smartFilter === 'failed' && !(a.status === 'error' || a.status === 'unauthorized')) return false;
  if (smartFilter === 'hascode' && !a.code_url) return false;
  if (smartFilter === 'nocode' && a.code_url) return false;
  if (smartFilter === 'has2fa' && a.has_2fa !== 1) return false;
  if (smartFilter === 'nosaved2fa' && !(a.has_2fa === 1 && !a.has_twofa_saved && !a.twofa)) return false;
  if (smartFilter === 'noproxy' && a.proxy) return false;
  if (smartFilter.startsWith('twofa_') && a.twofa_result?.status !== smartFilter.slice(6)) return false;
  const q = query == null ? (($('#q') && $('#q').value || '').trim().toLowerCase()) : query;
  if (!q) return true;
  const signature = [a.label, a.phone, a.username, (a.tags || []).join(',')].join(' ');
  if (a._searchSignature !== signature) {
    a._searchSignature = signature;
    a._searchText = signature.toLowerCase();
  }
  return a._searchText.includes(q);
}
function visible() {
  const q = (($('#q') && $('#q').value || '').trim().toLowerCase());
  const key = accountsRevision + '|' + filter + '|' + quickFilter + '|' + q;
  if (visibleCache && visibleCache.key === key) return visibleCache.rows;
  const rows = accounts.filter(a => accountMatches(a, filter, quickFilter, q));
  visibleCache = {key, rows};
  return rows;
}


const twofaShown = new Set();
let quickFilter = '';  // smart account predicates
let tableLimit = 300;

function twofaMask(pwd) {
  const n = Math.min(Math.max((pwd || '').length, 4), 16);
  return '*'.repeat(n);
}

async function shutdownService() {
  if (!await uiConfirm({title:'关闭项目', message:'将关闭 TAO 后端服务，当前网页会失去连接；不会自动重启。确认？', okText:'关闭项目', danger:true})) return;
  try {
    await api('/api/system/shutdown', {method:'POST', body:JSON.stringify({confirm:true})});
  } catch (_) {}
  toast('项目已关闭。需要时请从原启动终端运行 start.bat。', 'ok');
}
function twofaResultBadge(a) {
  const result=a.twofa_result;
  const labels={success:'2FA 修改成功',failed:'2FA 修改失败',uncertain:'2FA 待核对',running:'2FA 处理中'};
  if (!result || !labels[result.status]) return '';
  const tip=(result.detail||'')+(result.task_id?' · 一条龙 #'+result.task_id:'');
  return `<div><span class="tag twofa-result-${result.status}" title="${esc(tip)}">${labels[result.status]}</span></div>`;
}
function twofaCell(a) {
  if (a.has_2fa !== 1) {
    if (a.has_2fa === 0) return '<span class="muted">-</span>';
    return '<span class="muted" title="尚未检查，点「检查」同步有无二验">-</span>';
  }
  const saved = !!(a.has_twofa_saved || a.twofa);
  if (!saved) {
    return `<span class="twofa-miss" title="有二验，密码未保存在本工具。点击补录（只写本地）"
      data-onclick="promptSaveTwofa(${a.id}, event)">有·未存</span>`;
  }
  const pwd = a.twofa || '';
  const shown = twofaShown.has(a.id) && pwd;
  const text = shown ? esc(pwd) : twofaMask(pwd || '******');
  const eye = shown ? '🙈' : '👁';
  const tip = shown ? '隐藏密码' : '显示密码';
  return `<span class="twofa-wrap">` +
    `<code class="twofa-text" id="t2fa_${a.id}">${text}</code>` +
    `<button type="button" class="twofa-eye" title="${tip}" aria-label="${tip}" ` +
    `data-onclick="toggleTwofa(${a.id}, event)">${eye}</button></span>`;
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
function toastBatchSummary(r) {
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
      + "<button class=\"fit\" data-onclick=\"openModal('mTdata')\">导入 tdata</button>"
      + "<button class=\"fit\" data-onclick=\"openModal('mSession')\">导入 session</button>"
      + "<button class=\"fit\" data-onclick=\"openModal('mImport')\">手机号清单</button>"
      + "<button class=\"fit\" data-onclick=\"openModal('mAdd')\">手动添加</button>"
      + '</div>'
      + '<div class="empty-hint">导入后建议先「健康检查」同步状态与二验，再养号或改二验</div>';
  } else if (quickFilter || filter || ($('#q') && $('#q').value)) {
    inner = '没有符合条件的账号 <button class="sm" data-onclick="clearFilters()">清除筛选</button>';
  } else {
    inner = '没有符合条件的账号';
  }
  return '<tr><td colspan="12"><div class="empty">' + inner + '</div></td></tr>';
}

function setQuickFilter(k) {
  quickFilter = (quickFilter === k) ? '' : k;
  tableLimit = 300;
  render();
}
let accountRenderTimer = null;
function scheduleAccountRender() {
  clearTimeout(accountRenderTimer);
  accountRenderTimer = setTimeout(() => { tableLimit = 300; render(); }, 120);
}

// Both filter groups share the Uiverse label structure without replacing native buttons.
function renderFilterButton(key, label, selected, quick = false) {
  const action = quick ? 'setQuickFilter' : 'setFilter';
  const handler = `${action}(${JSON.stringify(String(key))})`;
  return `<button type="button" class="chip${selected ? ' on' : ''}" aria-pressed="${!!selected}" data-filter-key="${esc(key)}" data-onclick="${esc(handler)}"><span class="inner">${esc(label)}</span></button>`;
}

function render() {
  applyView();
  renderStats();

  const counts = stats.by_status || {};
  setHTML($('#chips'), [['', '全部']].concat(Object.keys(counts).map(k => [k, `${statCn(k)} ${counts[k]}`]))
    .map(([k, t]) => renderFilterButton(k, t, filter === k)).join(''));
  const qf = [
    ['active', '已登录'], ['noauth', '待登录'], ['failed', '登录失败'],
    ['hascode', '有取码链接'], ['nocode', '缺取码链接'], ['has2fa', '有二验'],
    ['nosaved2fa', '二验未存'], ['noproxy', '无代理'],
    ['twofa_success', '改密成功'], ['twofa_failed', '改密失败'],
    ['twofa_uncertain', '改密待核对'], ['twofa_running', '改密处理中'],
  ];
  const qfe = $('#qfilters');
  if (qfe) {
    const query = (($('#q') && $('#q').value || '').trim().toLowerCase());
    const countKey = accountsRevision + '|' + filter + '|' + query;
    let qcounts;
    if (filterCountsCache && filterCountsCache.key === countKey) {
      qcounts = filterCountsCache.value;
    } else {
      // 分类计数与账号数据无关时复用缓存，勾选账号只更新行状态，不再扫描全库。
      qcounts = Object.fromEntries(qf.map(([k]) => [k, 0]));
      for (const a of accounts) {
        for (const [k] of qf) if (accountMatches(a, filter, k, query)) qcounts[k]++;
      }
      filterCountsCache = {key: countKey, value: qcounts};
    }
    setHTML(qfe, qf.map(([k, lab]) => renderFilterButton(k, `${lab} ${qcounts[k]}`, quickFilter === k, true)).join(''));
  }

  const rows = visible();
  const resultHint = $('#accountResultHint');
  if (resultHint) {
    const query = (($('#q') && $('#q').value || '').trim());
    resultHint.textContent = query ? `匹配 ${rows.length} 个` : `共 ${rows.length} 个`;
  }
  const shownRows = rows.slice(0, tableLimit);
  let tbHtml = shownRows.length ? shownRows.map(a => `
    <tr class="" data-id="${a.id}" data-onclick="openDrawer(${a.id})">
      <td data-onclick="event.stopPropagation()"><input type="checkbox" class="row-pick" data-onclick="pick(${a.id},this.checked)" aria-label="选择 ${esc(a.label)}" /></td>
      <td><b>${esc(a.label)}</b>${twofaResultBadge(a)}${a.status_note ? `<div class="muted error-note" title="${esc(a.status_note)}">${esc(a.status_note)}</div>` : ''}</td>
      <td>${esc(fmtPhone(a.phone))}</td>
      <td class="hide-s">${esc(countryOf(a.phone))}</td>
      <td class="hide-s">${a.username ? '@' + esc(a.username) : '—'}</td>
      <td><span class="tag s-${esc(a.status)}">${esc(statCn(a.status))}</span>${a.premium ? ' <span class="tag">Premium</span>' : ''}${a.spam_until && a.spam_until * 1000 > Date.now() ? ' <span class="tag s-restricted">spam</span>' : ''}</td>
      <td class="hide-s" data-onclick="event.stopPropagation()">${twofaCell(a)}</td>
      <td class="hide-s">${a.proxy ? '已配置' : '<span class="muted">直连</span>'}</td>
      <td class="hide-s">${a.code_url ? '✓' : '—'}</td>
      <td class="hide-s">${(a.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join(' ') || '—'}</td>
      <td class="hide-s muted">${esc(kickCell(a))}</td>
      <td class="account-action-cell" data-onclick="event.stopPropagation()">
        <button class="icon-btn lock-toggle ${a.locked ? 'is-locked' : ''}" data-onclick="toggleAccountLock(${a.id},${!a.locked})" title="${a.locked ? '解锁账号' : '锁定账号（防止删除）'}" aria-label="${a.locked ? '解锁账号' : '锁定账号'}"><svg class="i sm"><use href="#i-${a.locked ? 'lock' : 'unlock'}"/></svg></button>
        ${a.authorized ? '' : `<button class="sm write" data-onclick="startLogin(${a.id})">登录</button>`}
        <button class="sm write fit qr-login-btn" data-onclick="startQrLogin(${a.id})" title="手机 Telegram 扫码授权">扫码登录</button>
        <button class="sm" data-onclick="check(${a.id})">检查</button>
        ${a.authorized ? `<button class="sm official-code-btn" data-onclick="openOfficialCode(${a.id})" title="用已登录会话读取实体 TG 客户端登录验证码">官方验证码</button>` : ''}
      </td>
    </tr>`).join('') : emptyRowHtml();
  if (rows.length > tableLimit) {
    tbHtml += ('<tr><td colspan="12"><button type="button" class="fit" data-onclick="tableLimit += 300;render()">加载更多（已显示 ' + tableLimit + ' / ' + rows.length + '）</button></td></tr>');
  }
  // 勾选状态单独同步，所以缓存键不含勾选；表格内容没变时不重建 300 行 DOM。
  setHTML($('#tb'), tbHtml);
  syncRowSelection();

  syncSelectionUi(rows);
  if (readonly) document.querySelectorAll('.write').forEach(b => b.disabled = true);
  else document.querySelectorAll('#tb .write').forEach(b => { b.disabled = false; });
  syncFilterBadge();
  // 账号列表与导航徽标共用同一份内存数据；每次渲染同步更新，
  // 避免演示模式/快速刷新时导航仍显示旧的 0。
  updateNavBadges();
}

function setFilter(k) { filter = k; tableLimit = 300; render(); }
function syncSelectionUi(rows = visible()) {
  $('#selN').textContent = `已选 ${sel.size} 个`;
  const bulkSummary = $('#bulkSelectionSummary');
  if (bulkSummary) bulkSummary.dataset.count = String(sel.size);
  syncTbSelection();
  $('#bulk').classList.toggle('on', sel.size > 0);
  $('#all').checked = rows.length > 0 && rows.every(a => sel.has(a.id));
}
// 表格 HTML 不随勾选变化，勾选状态在这里按行打补丁。
function syncRowSelection() {
  const tb = $('#tb');
  if (!tb) return;
  for (const tr of tb.querySelectorAll('tr[data-id]')) {
    const on = sel.has(Number(tr.dataset.id));
    tr.classList.toggle('sel', on);
    const cb = tr.querySelector('.row-pick');
    if (cb && cb.checked !== on) cb.checked = on;
  }
}
// 单个勾选只改这一行和批量栏，不再重绘整张表（原来每次勾选都重建最多 300 行）。
function pick(id, on) {
  on ? sel.add(id) : sel.delete(id);
  const tr = document.querySelector('#tb tr[data-id="' + id + '"]');
  if (tr) tr.classList.toggle('sel', on);
  syncSelectionUi();
}
function toggleAll(on) { visible().forEach(a => on ? sel.add(a.id) : sel.delete(a.id)); render(); }
async function selectTwofaSuccess() {
  try {
    // Refresh first; a failed request must not select stale successful results.
    await refreshLoginAccounts();
    filter=''; quickFilter='twofa_success'; tableLimit=300;
    sel=new Set(visible().filter(a=>a.twofa_result?.status==='success').map(a=>a.id));
    render();
    toast(`已仅选择 ${sel.size} 个改密成功账号（保留搜索条件），旧勾选已清除；可导出号包或 session`,'ok');
  } catch(e) { toast('刷新改密结果失败，未变更选择：'+e.message,'err'); }
}
function clearSel() { sel.clear(); render(); }

/* ---------- 详情抽屉 ---------- */
function openDrawer(id) {
  const a = accounts.find(x => x.id === id);
  if (!a) return;
  const returnFocus = document.activeElement;
  closeAll();
  $('#dTitle').textContent = a.label;
  $('#dBody').innerHTML = `
    <div class="kv">
      <span>ID</span><div>${a.id}</div>
      <span>状态</span><div><span class="tag s-${esc(a.status)}">${esc(statCn(a.status))}</span> ${a.authorized ? '已授权' : '<span class="muted">未授权</span>'}${a.premium ? ' · Premium' : ''}</div>
      <span>二验</span><div>${
        a.has_2fa === 1
          ? (a.twofa || a.has_twofa_saved
              ? ('已开启' + (a.twofa ? (' · <code>' + esc(a.twofa) + '</code>') : ' · 已保存（列表点眼睛查看）'))
              : '已开启 · <a href="#" data-onclick="promptSaveTwofa(' + a.id + ', event);return false">补录密码</a>')
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
      <button type="button" class="sm fit" id="eCodeTest" data-onclick="testCodeSource('eCode','eProxy','eCodeStatus')">测试取码链接</button>
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
      <button class="primary write fit" data-onclick="saveAccount(${a.id})">保存修改</button>
      <button class="fit" data-onclick="check(${a.id})">健康检查</button>
      <button class="fit write" data-onclick="spamCheck(${a.id})">限制检测</button>
      <button class="fit" data-onclick="twofaStatus(${a.id})" title="有无云密码、是否绑定辅助邮箱、重置等待期">二验状态</button>
      <button class="fit" data-onclick="devices(${a.id})">查看设备</button>
      <button class="fit" data-onclick="dialogs(${a.id})">最近会话</button>
      ${a.authorized ? `<button class="fit" data-onclick="openOfficialCode(${a.id})">官方验证码</button>` : ''}
    </div>
    <div class="row" style="margin-bottom:8px;flex-wrap:wrap">
      <button class="danger write fit" data-onclick="twofaReset(${a.id})" title="发起官方二验重置（通常约7天等待期）">发起2FA重置</button>
      <button class="write fit" data-onclick="twofaResetCancel(${a.id})" title="取消进行中的二验重置等待">取消2FA重置</button>
      <button class="write fit" data-onclick="exportAcc(${a.id},'string')" title="显示/复制 StringSession 文本">导出 StringSession</button>
      <button class="write fit" data-onclick="exportAcc(${a.id},'session')" title="下载 Telethon .session 文件">导出 .session</button>
      <button class="write fit" data-onclick="exportAcc(${a.id},'pack')" title="下载 session+json 号包 zip">导出号包 zip</button>
      <button class="write fit" data-onclick="exportAcc(${a.id},'tdata')" title="下载 Telegram Desktop tdata zip（需服务器已装 opentele）">导出 tdata</button>
      <button class="danger write fit" data-onclick="regenSession(${a.id})" title="新设备指纹重新登录，旧 session 作废（防找回）">重生会话</button>
    </div>
    <div class="row">
      ${a.authorized ? '' : `<button class="write fit" data-onclick="startLogin(${a.id})">登录</button>`}
      <button class="write fit" data-onclick="startQrLogin(${a.id})" title="手机 Telegram 扫码授权，无需短信">扫码登录</button>
      <button class="write fit" data-onclick="terminate(${a.id})">清理其他设备</button>
      <button class="write fit" data-onclick="logout(${a.id})">退出登录</button>
      <button class="fit danger write" data-onclick="deleteTelegram(${a.id})">注销TG账号</button>
      <button class="danger write fit" data-onclick="delAccount(${a.id})">删除账号</button>
    </div>
    <h2 style="font-size:14px;margin:20px 0 8px">该账号日志</h2>
    <pre id="dLog" style="max-height:320px">加载中…</pre>`;
  const detail = $('#accountDetailDrawer');
  detail.classList.add('on'); detail.setAttribute('aria-hidden', 'false'); $('#bd').classList.add('on');
  focusDialog(detail, returnFocus);
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
    const sum = toastBatchSummary(r);
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
  const urlEl = urlId ? $('#' + urlId) : null;
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
    if (fmt === 'tdata') {
      const dep = await api('/api/system/opentele');
      if (!dep.installed) {
        const install = await uiConfirm({title:'需要安装 opentele', message:'当前环境未安装 opentele。是否一键安装后继续导出？', okText:'一键安装'});
        if (!install) return;
        const ir = await api('/api/system/install-opentele', {method:'POST', body:'{}'});
        if (!ir.installed) throw new Error(ir.message || 'opentele 安装失败');
        toast('opentele 安装完成，继续导出', 'ok');
      }
    }
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
  const query_delay = Number($('#lQueryDelay').value);
  const poll_interval = Number($('#lPollInterval').value);
  const adaptive_timing = $('#lAdaptiveTiming') ? $('#lAdaptiveTiming').checked : true;
  if (!Number.isFinite(query_delay) || query_delay < 0 || query_delay > 600 ||
      !Number.isFinite(poll_interval) || poll_interval < 1 || poll_interval > 600) {
    toast('等待时间须为 0-600 秒，查询间隔须为 1-600 秒', 'err');
    return null;
  }
  const r = await submitLoginTask({account_ids:[id], timeout:120, password, query_delay, poll_interval, adaptive_timing, concurrency:1});
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

let officialCodeAccountId = null, officialCodeRequest = 0;
function clearOfficialCode() {
  officialCodeAccountId = null;
  officialCodeRequest++;
  if ($('#officialCodeValue')) $('#officialCodeValue').textContent = '';
  if ($('#officialCodeCopy')) $('#officialCodeCopy').disabled = true;
}
function openOfficialCode(id) {
  openModal('mOfficialCode');
  officialCodeAccountId = id;
  const a = accounts.find(x => x.id === id);
  $('#officialCodeWho').textContent = a ? `${a.label || ''} ${a.phone || ''}` : `账号 #${id}`;
  $('#officialCodeStatus').textContent = '请先在实体 TG 客户端发起登录，再点击读取。';
  $('#officialCodeRead').disabled = false;
}
async function readOfficialCode() {
  const id = officialCodeAccountId;
  if (!id) return;
  const request = ++officialCodeRequest;
  const btn = $('#officialCodeRead'); btn.disabled = true;
  $('#officialCodeValue').textContent = '';
  $('#officialCodeCopy').disabled = true;
  $('#officialCodeStatus').textContent = '正在通过管理器已有会话读取官方通知…';
  try {
    const r = await api(`/api/accounts/${id}/official-code`, {cache:'no-store'});
    if (request !== officialCodeRequest) return;
    $('#officialCodeValue').textContent = r.code || '';
    $('#officialCodeCopy').disabled = !r.code;
    $('#officialCodeStatus').textContent = r.code
      ? `消息时间：${new Date(r.date).toLocaleString()}。请在实体客户端填写；有效性以 Telegram 校验为准。`
      : '最近 10 分钟未找到登录验证码。请在实体 TG 客户端请求发码后，稍后再次读取。';
  } catch (e) {
    if (request === officialCodeRequest) $('#officialCodeStatus').textContent = '读取失败：' + e.message;
  } finally { if (request === officialCodeRequest) btn.disabled = false; }
}
async function copyOfficialCode() {
  const code = $('#officialCodeValue').textContent;
  if (!code) return;
  try { await navigator.clipboard.writeText(code); toast('验证码已复制', 'ok'); }
  catch (_) { toast('请选中验证码手动复制', 'err'); }
}
const verify = () => guard(async () => {
  const body = {code: $('#lCode').value, password: $('#lPass').value || null};
  const r = await api(`/api/accounts/${loginId}/login/verify`, {method:'POST', body: JSON.stringify(body)});
  if (r.need_password) { toast('该号开启了两步验证，请填写密码', 'err'); return r; }
  closeAll(); return r;
}, '登录成功');

/* 批量（逐号进度条；可并行） */
async function submitLoginTask(body) {
  const r = await guard(() => api('/api/tasks/login', {method:'POST', body:JSON.stringify(body)}), '已加入登录队列');
  if (r && r.task) {
    closeAll();
    await loadTasks();
    await taskDetail(r.task.id);
    $('#log').scrollIntoView({block:'nearest'});
  }
  return r;
}

async function retryLoginTask(id) {
  const password = await uiPrompt({title:'重试失败账号', label:'两步验证密码（可选）', password:true});
  if (password === null) return;
  const timing = await promptAutoLoginTiming();
  if (!timing) return;
  return submitLoginTask({retry_task_id:id, password:password || null, ...timing, concurrency:batchConc(3)});
}

async function promptAutoLoginTiming() {
  let query_delay = 60, poll_interval = 60, start_delay = 1;
  for (;;) {
    const pending = uiConfirm({title:'自动接码设置', okText:'开始任务'});
    const fields = document.createElement('div');
    fields.innerHTML = '<label class="f"><span>发码后等待（秒）</span><input id="batchQueryDelay" type="number" min="0" max="600"></label>' +
      '<label class="f"><span>查询间隔（秒）</span><input id="batchPollInterval" type="number" min="1" max="600"></label>' +
      '<label class="f"><span>启动抖动基准（秒，0 关闭）</span><input id="batchStartDelay" type="number" min="0" max="5" step="0.1"></label>' +
      '<label class="row" style="gap:8px"><input id="batchAdaptiveTiming" type="checkbox" checked><span>智能调整等待</span></label>' +
      '<p class="muted">首个账号立即开始，其余账号随机等待基准的 80%–120%。</p>';
    $('#uiMsg').after(fields);
    $('#batchQueryDelay').value = query_delay;
    $('#batchPollInterval').value = poll_interval;
    $('#batchStartDelay').value = start_delay;
    const confirmed = await pending;
    const delayInput = $('#batchQueryDelay');
    const intervalInput = $('#batchPollInterval');
    query_delay = delayInput.valueAsNumber;
    poll_interval = intervalInput.valueAsNumber;
    start_delay = $('#batchStartDelay').valueAsNumber;
    const adaptive_timing = !!($('#batchAdaptiveTiming') && $('#batchAdaptiveTiming').checked);
    fields.remove();
    if (!confirmed) return null;
    if (Number.isFinite(query_delay) && query_delay >= 0 && query_delay <= 600 &&
        Number.isFinite(poll_interval) && poll_interval >= 1 && poll_interval <= 600 &&
        Number.isFinite(start_delay) && start_delay >= 0 && start_delay <= 5) {
      return {query_delay, poll_interval, start_delay, adaptive_timing};
    }
    toast('等待时间须为 0-600 秒，查询间隔须为 1-600 秒', 'err');
    query_delay = 60; poll_interval = 60; start_delay = 1;
  }
}

async function bulk(kind) {
  const ids = [...sel];
  if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  if (kind === 'check') {
    const r = await runPool(ids, async (id) => {
      return await api('/api/accounts/' + id + '/check', {method: 'POST'});
    }, '健康检查', Math.min(32, Math.max(1, cfgInt('TAM_HEALTH_CONCURRENCY', 12))));
    out(r, true);
    toast('健康检查 · 成功 ' + r.ok + ' / 共 ' + r.total + (r.failed ? '，失败 ' + r.failed : ''), r.failed ? 'err' : 'ok');
    refresh();
    return r;
  }
  if (kind === 'auto') {
    const concurrency = batchConc(3);
    const safety = concurrency > 8 ? '（提示：超过 8 并发会增加限流、代理复用和账号风控风险）' : '';
    if (!await uiConfirm({title:'批量自动登录', message:'将对 ' + ids.length + ' 个账号执行「发码 → 自动取码 → 登录」（并行 ' + concurrency + '）。' + safety, danger:true, okText:'开始'})) return;
    const password = await uiPrompt({
      title: '统一两步验证密码',
      message: '各账号密码相同时可填写；留空会分别使用账号已加密保存的二验密码。',
      label: '二验密码（可选）',
      password: true,
    });
    if (password === null) return;
    const timing = await promptAutoLoginTiming();
    if (timing === null) return;
    return submitLoginTask({account_ids:ids, password:password || null, timeout:120, ...timing, concurrency});
  }
  if (kind === 'relogin') {
    if (!await uiConfirmDanger({
      title: '批量退出并重新登录',
      message: '将清除选中 ' + ids.length + ' 个账号的本地旧会话，再通过手机号和取码链接重新登录。\n远端退出失败也会清理本地会话；重登失败时旧会话无法恢复。',
      word: '重登',
      okText: '下一步',
    })) return;
    const password = await uiPrompt({
      title: '统一两步验证密码',
      message: '各账号密码相同时可填写；留空会分别使用账号已加密保存的二验密码。',
      label: '二验密码（可选）',
      password: true,
    });
    if (password === null) return;
    const timing = await promptAutoLoginTiming();
    if (timing === null) return;
    const concurrency = batchConc(3);
    return submitLoginTask({account_ids:ids, password:password || null, timeout:120,
      concurrency:concurrency, ...timing, relogin:true});
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
  const lockedIds = ids.filter(id => accounts.some(a => a.id === id && a.locked));
  const targetIds = ids.filter(id => !lockedIds.includes(id));
  if (!targetIds.length) {
    toast('选中的账号均已锁定，未执行删除', 'err');
    return;
  }
  const r = await runPool(targetIds, async (id) => {
    await api('/api/accounts/' + id, {method: 'DELETE'});
    return {ok: true};
  }, '批量删除', batchConc(5));
  sel.clear();
  lockedIds.forEach(id => sel.add(id));
  out({...r, skipped: lockedIds.length, skip_reason: lockedIds.length ? '锁定账号已跳过' : undefined}, true);
  const suffix = lockedIds.length ? '，跳过锁定 ' + lockedIds.length : '';
  toast('批量删除 · 成功 ' + r.ok + ' / 尝试 ' + r.total + suffix + (r.failed ? '，失败 ' + r.failed : ''), r.failed || lockedIds.length ? 'err' : 'ok');
  refresh();
}

async function toggleAccountLock(id, locked) {
  try { await api('/api/accounts/' + id + '/lock', {method:'POST', body:JSON.stringify({locked})});
    toast(locked ? '账号已锁定，删除操作已保护' : '账号已解锁', 'ok'); await refresh();
  } catch (e) { toast('切换锁定状态失败：' + e.message, 'err'); }
}
async function bulkLock(locked) {
  const ids = [...sel]; if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  try { const r = await api('/api/accounts/batch-lock', {method:'POST', body:JSON.stringify({ids, locked})});
    toast((locked ? '已锁定 ' : '已解锁 ') + r.changed + ' 个账号', 'ok'); await refresh();
  } catch (e) { toast('批量锁定失败：' + e.message, 'err'); }
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
  if (value.startsWith('[')) value = value.slice(1).trim();
  if (value.endsWith(']')) value = value.slice(0, -1).trim();
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
  // CSV-aware splitting: preserve commas and pipes inside quoted cells.
  const rows = [];
  let row = '', quoted = false;
  for (const ch of String(text || '').replace(/\r\n?/g, '\n')) {
    if (ch === '"') { quoted = !quoted; row += ch; }
    else if (ch === '\n' && !quoted) { rows.push(row); row = ''; }
    else row += ch;
  }
  if (row) rows.push(row);
  rows.forEach((raw, i) => {
    const line = raw.trim().replace(/^\ufeff/, '');
    if (!line || line.startsWith('#')) return;
    const parts = line.split(/\s*(?:\\\||\||｜|\t|,|;|-{2,}\s*(?=\[?https?:\/\/))\s*/)
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

// 代理节点输入同时接受纯文本和常见 CSV：host,port[,username,password]。
// 不把 CSV 的逗号直接当分隔符，避免带引号的密码/备注被拆坏。
function parseProxyPoolInput(text) {
  const raw=String(text||'').replace(/^\ufeff/,'').trim();
  if(!raw)return [];
  const rows=[];let row=[],cell='',quoted=false;
  for(let i=0;i<raw.length;i++) {
    const c=raw[i];
    if(c==='"') {if(quoted&&raw[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}
    else if(!quoted&&(c===','||c==='\n')) {row.push(cell.trim());cell='';if(c==='\n'){if(row.some(Boolean))rows.push(row);row=[];}}
    else if(c!=='\r'||quoted)cell+=c;
  }
  if(quoted)throw Error('CSV 引号未闭合');
  row.push(cell.trim());if(row.some(Boolean))rows.push(row);
  const head=rows[0].map(x=>x.toLowerCase().replace(/[ _-]/g,''));
  const hostNames=['host','ip','address','server','proxy'];
  const isHeader=head.includes('port')&&head.some(x=>hostNames.includes(x));
  const idx=(names,fallback)=>isHeader?head.findIndex(x=>names.includes(x)):fallback;
  const hi=idx(hostNames,0),pi=idx(['port'],1),ui=idx(['username','user'],2),wi=idx(['password','pass','pwd'],3),ti=idx(['protocol','type','scheme'],-1);
  const out=[],bad=[];
  function node(value) {
    value=value.trim();
    if(!/^(?:socks[45]|https?):\/\//i.test(value)) {
      const m=/^(\[[^\]]+\]|[^:\s]+):(\d+)(?::([^:]*):(.*))?$/.exec(value);
      if(!m)throw Error('节点格式');
      value='socks5://'+(m[3]?encodeURIComponent(m[3])+':'+encodeURIComponent(m[4]||'')+'@':'')+m[1]+':'+m[2];
    }
    const match=/^(socks[45]|https?):\/\/(?:[^@\s]+@)?(\[[^\]\s]+\]|[^:\/\s]+):(\d+)(?:\/)?$/i.exec(value);
    if(!match||Number(match[3])<1||Number(match[3])>65535)throw Error('端口或主机');
    return value;
  }
  for(const [n,r] of rows.entries()) {
    if(isHeader&&n===0)continue;
    try {
      if(r.length===1)out.push(node(r[0]));
      else if(r.every(x=>/^(socks[45]|https?):\/\//i.test(x)))out.push(...r.map(node));
      else {
        let host=r[hi]||'', port=r[pi]||'', user=r[ui]||'', pass=r[wi]||'', type=(r[ti]||'socks5').toLowerCase();
        if(!host||!/^\d+$/.test(port))throw Error('主机或端口');
        if(host.includes(':')&&!host.startsWith('['))host='['+host+']';
        out.push(node(type+'://'+(user?encodeURIComponent(user)+':'+encodeURIComponent(pass)+'@':'')+host+':'+port));
      }
    } catch(_) {bad.push(n+1);}
  }
  if(bad.length)throw Error('代理第 '+bad.slice(0,20).join('、')+' 行格式错误；请修正后再提交（未丢弃任何行）');
  return [...new Set(out)];
}

// 仅导出手机号与原始取码链接（不包含 session/二验），支持当前勾选的多个账号。
async function bulkExportCodeLinks() {
  const ids = [...sel];
  if (!ids.length) { toast('请先勾选账号', 'err'); return; }
  if (!await uiConfirm({title: '批量导出取码链接', message: `将导出选中的 ${ids.length} 个账号的手机号与取码链接。`, okText: '导出'})) return;
  try {
    progress(true);
    const name = await _downloadBlob('/api/accounts/export-code-links', {ids}, 'code-links.txt');
    toast('已导出 ' + name + '（' + ids.length + ' 个）', 'ok');
  } catch (e) { toast('导出取码链接失败：' + e.message, 'err'); out({error: String(e)}, true); }
  finally { progress(false); }
}

async function oneStopWizard() {
  if (oneStopWizard.busy) return;
  oneStopWizard.busy = true;
  let tid = null, stage = '配置', importCommitted = false;
  try {
    const wizardPrompt = (step, title, opts) => uiPrompt(Object.assign({}, opts || {}, {
      title: `一条龙 · ${step}/8 ${title}`,
    }));
    const text = await wizardPrompt(1, '账号清单', {label:'每行 手机号|取码链接', multiline:true});
    if (text === null || !text.trim()) return;
    const proxy = await wizardPrompt(2, '代理节点', {label:'多行节点或 CSV 文件', multiline:true, file:true});
    if (proxy === null) return;
    const nodes = parseProxyPoolInput(proxy);
    if (proxy.trim() && !nodes.length) throw Error('代理内容未解析出有效节点');
    const askNumber = async (step, title, value, min, max) => {
      let input;
      while (true) {
        input = await wizardPrompt(step, title, {label:`范围 ${min}–${max}`, value:String(value)});
        if (input === null) return null;
        const n = Number(input);
        if (input.trim() && Number.isSafeInteger(n) && n >= min && n <= max) return n;
        toast(`${title} 请输入 ${min}–${max} 的整数`, 'err');
      }
    };
    const capacity = await askNumber(3, '每节点全局容量（0 表示不限）', 0, 0, 1000000);
    if (capacity === null) return;
    const conc = await askNumber(4, '并行数（建议 8）', 8, 1, 32);
    if (conc === null) return;
    const delay = await askNumber(5, '发码后首次等待秒数', 8, 0, 600);
    if (delay === null) return;
    const interval = await askNumber(6, '查询间隔秒数（GetHTML 至少 60 秒）', 60, 1, 600);
    if (interval === null) return;
    const password = await wizardPrompt(7, '新2FA', {label:'留空保留当前密码；旧密码从已保存记录或接码页取得', password:true});
    if (password === null) return;
    let fmt;
    while (true) {
      const format = await wizardPrompt(8, '导出格式', {label:'pack（号包）或 session', value:'pack'});
      if (format === null) return;
      fmt = format.trim().toLowerCase();
      if (['pack','session'].includes(fmt)) break;
      toast('导出格式请输入 pack 或 session', 'err');
    }
    stage = '导入';
    const card = uiTask('一条龙 · 导入');
    let imported;
    try {
      card.update({text:'正在解析账号并统计全局节点容量…'});
      imported = await api('/api/accounts/import', {method:'POST',body:JSON.stringify({text,
        proxy_pool:nodes, links_per_proxy:capacity})});
      importCommitted = true;
    } finally { card.close(); }
    const added=imported.added||[], updated=imported.updated||[], existing=imported.existing||[];
    const duplicates=(imported.duplicates||[]).length, errors=(imported.errors||[]).length;
    const blocked=Number(imported.capacity_blocked||0);
    // parsed_input excludes malformed lines; duplicates are included exactly once.
    if (Number(imported.parsed_input) !== added.length+updated.length+existing.length+duplicates+blocked)
      throw Error('导入统计不一致，已停止后续操作，请查看导入结果');
    const ids=[...new Set([...added,...updated,...existing].map(x=>x.id).filter(Number.isSafeInteger))];
    out({one_stop_import:{parsed:imported.parsed_input,added:added.length,updated:updated.length,
      existing:existing.length,duplicates,errors,capacity_blocked:blocked}},true);
    if (!ids.length) throw Error('没有可处理账号');
    if (errors || blocked || duplicates) {
      if (!await uiConfirm({title:'导入结果确认',message:`有效处理 ${ids.length}，重复 ${duplicates}，格式错误 ${errors}，容量不足 ${blocked}。只继续有效账号？`})) return;
    }
    stage = '提交任务';
    const task=await api('/api/tasks/one-stop',{method:'POST',body:JSON.stringify({account_ids:ids,
      concurrency:conc,new_password:password,format:fmt,query_delay:delay,poll_interval:interval})});
    tid=task.id;
    toast(`一条龙任务 #${tid} 已创建，刷新后可从「一条龙记录」继续查看`,'ok');
    await watchOneStop(tid, true);
  } catch(e) {
    const where = `一条龙${tid ? ' #' + tid : ''} · ${stage}`;
    const detail = tid
      ? `${where}：${e.message}。任务检查点已保留，可从记录页继续。`
      : importCommitted
        ? `${where}：${e.message}。导入结果已写入，但任务尚未创建。请先查看导入结果，再决定是否重试。`
        : `${where}：${e.message}。任务尚未创建。`;
    toast(detail, 'err');
  } finally { oneStopWizard.busy=false; refresh(); }
}

const ONE_STOP_WATCHING = new Set();
async function watchOneStop(tid, autoExport=false) {
  if (ONE_STOP_WATCHING.has(tid)) return;
  ONE_STOP_WATCHING.add(tid);
  const card=uiTask('一条龙 #'+tid);
  let transientErrors = 0;
  try {
    let state;
    do {
      try {
        state=await api('/api/tasks/one-stop/'+tid);
        transientErrors = 0;
      } catch (e) {
        transientErrors++;
        if (transientErrors >= 10) throw e;
        card.update({text:`暂时无法读取任务进度（${transientErrors}/10），正在自动重试…`});
        await new Promise(resolve=>setTimeout(resolve, Math.min(5000, 500 * transientErrors)));
        continue;
      }
      card.update({current:state.done_count,total:state.total,
        text:`完成 ${state.done_count}/${state.total} · 成功 ${state.ok_count} · 失败 ${state.fail_count} · ${JSON.stringify(state.phases||{})}`});
      if (!state.live) break;
      await new Promise(resolve=>setTimeout(resolve,1000));
    } while(true);
    out({one_stop_task:tid,...state},true);
    if (autoExport && state.phases?.ready > 0) await downloadOneStop(tid);
    else toast(`一条龙 #${tid} 已结束，结果可在一条龙记录查看`);
  } catch(e) { toast(`任务 #${tid}：${e.message}；服务端检查点保留，请从一条龙记录继续。`,'err'); }
  finally { card.close(); ONE_STOP_WATCHING.delete(tid); }
}
async function downloadOneStop(tid) {
  const name=await _downloadBlob('/api/tasks/one-stop/'+tid+'/export',{},'tao-one-stop.zip');
  toast(`任务 #${tid} 已下载 ${name}；实际导出数量见包内 summary.json`,'ok');
}
async function oneStopHistory() {
  const list=await api('/api/tasks/one-stop');
  let panel=document.getElementById('oneStopHistory');
  if (!panel) { panel=document.createElement('div');panel.id='oneStopHistory';document.body.appendChild(panel); }
  panel.className='modal one-stop-history-modal';
  panel.innerHTML='<h3>一条龙记录 · 刷新后仍可继续</h3><div class="body">'
    + (list.length ? list.map(t=>{
    const id=Number(t.id);
    return `<div class="one-stop-row"><strong>#${id}</strong> ${esc(t.status_cn)} · ${t.done_count}/${t.total} · 可导出 ${t.phases?.ready||0}
      <button data-onclick="watchOneStop(${id})">进度</button>
      <button data-onclick="oneStopResults(${id})">结果清单</button>
      ${t.live?`<button data-onclick="oneStopStop(${id})">停止排队</button>`:`<button data-onclick="oneStopResume(${id})">继续未完成项</button><button data-onclick="downloadOneStop(${id}).catch(e=>toast(e.message,'err'))">仅重新导出</button>`}</div>`;
  }).join('') : '<div class="empty">暂无一条龙任务记录</div>')
    + '</div><div class="foot"><button data-onclick="closeAll()">关闭</button></div>';
  openModal('oneStopHistory');
}
async function oneStopResume(tid) {
  try { await api('/api/tasks/one-stop/'+tid+'/resume',{method:'POST'}); await watchOneStop(tid,true); }
  catch(e) {toast(e.message,'err');}
}
async function oneStopStop(tid) {
  await api('/api/tasks/'+tid+'/stop',{method:'POST'});
  toast('已停止排队；正在执行的单号将完成并保留检查点');
}
async function oneStopResults(tid) {
  const rows=[];
  for(let offset=0;;offset+=500) {
    const page=await api(`/api/tasks/one-stop/${tid}?limit=500&offset=${offset}`);
    rows.push(...page.targets);
    if(page.targets.length<500)break;
  }
  const blob=new Blob([JSON.stringify({task_id:tid,results:rows},null,2)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`one-stop-${tid}-results.json`;
  a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}
let proxyCapacityTimer = null;
async function previewProxyCapacity() {
  clearTimeout(proxyCapacityTimer);
  proxyCapacityTimer = setTimeout(async () => {
    const box = $('#iCapacity');
    const raw = ($('#iProxyPool')?.value || '').trim();
    if (!box || !raw || DEMO) return;
    const nodes = [...new Set(raw.split(/\r?\n|,/).map(s => s.trim()).filter(Boolean))];
    try {
      const params = new URLSearchParams();
      nodes.forEach(n => params.append('proxy_pool', n));
      const limit = Number($('#iLinksPerProxy')?.value || 0);
      if (limit > 0) params.set('capacity', String(limit));
      const r = await api('/api/proxy/capacity?' + params.toString());
      const used = (r.nodes || []).reduce((n, x) => n + (x.used || 0), 0);
      const remain = limit ? (r.nodes || []).reduce((n, x) => n + Math.max(0, x.remaining || 0), 0) : null;
      box.textContent = `节点 ${nodes.length} 个 · 已负责账号 ${used} 个` + (remain == null ? '' : ` · 剩余容量 ${remain}`);
    } catch (e) { box.textContent = '容量预览暂不可用，提交时仍会进行全局校验'; }
  }, 180);
}

const doImport = dry => guard(async () => {
  const proxyFile = $('#iProxyCsv') && $('#iProxyCsv').files && $('#iProxyCsv').files[0];
  let proxyPoolText = ($('#iProxyPool') && $('#iProxyPool').value || '');
  if (proxyFile) proxyPoolText += '\n' + await proxyFile.text();
  const proxyPool = parseProxyPoolInput(proxyPoolText);
  const r = await withWaitProgress(
    dry ? '试运行导入清单' : '批量导入账号',
    dry ? '解析中…' : '写入数据库中…',
    () => api('/api/accounts/import', {method:'POST', body: JSON.stringify({
      text: $('#iText').value,
      tags: $('#iTags').value ? $('#iTags').value.split(',').map(s => s.trim()) : [],
      proxy: $('#iProxy').value || null,
      proxy_pool: proxyPool,
      links_per_proxy: Math.max(0, parseInt(($('#iLinksPerProxy') && $('#iLinksPerProxy').value) || '0', 10) || 0),
      dry_run: dry})}),
    120000);
  if (!dry) closeAll();
  return r;
}, null).then(r => {
  if (!r) return;
  if (dry) return toast('试运行完成，结果见日志区');
  const dup = (r.duplicates || []).length, exist = (r.existing || []).length;
  const errors = (r.errors || []).length, blocked = Number(r.capacity_blocked || 0);
  const msg = `导入完成：解析 ${r.parsed_input ?? r.parsed ?? 0}，新增 ${(r.added||[]).length}，更新 ${(r.updated||[]).length}，重复 ${dup}，已存在 ${exist}，格式错误 ${errors}，容量不足 ${blocked}`;
  toast(msg, (errors || blocked) ? 'err' : 'ok');
});

function toggleTdataMode() {
  const mode = (document.querySelector('input[name="tdataMode"]:checked') || {}).value || 'upload';
  $('#tdataUploadBox').style.display = mode === 'upload' ? '' : 'none';
  $('#tdataPathBox').style.display = mode === 'path' ? '' : 'none';
}

document.addEventListener('change', function (e) {
  if (e.target && e.target.id === 'iProxyCsv') {
    const f = e.target.files && e.target.files[0];
    if (f) f.text().then(t => {
      const lines = t.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
      const box = $('#iPrev');
      if (box) box.textContent = `已读取代理文件：${f.name}（${lines.length} 行，提交时自动去重并统计已有占用）`;
    });
  }
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

/* ---------- tdata 上传体检 ---------- */
(() => {
  const inspectPath = doTdataInspect;
  const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[c]);
  let busy = false, revision = 0;
  const clearResult = () => {
    revision++;
    const box = document.querySelector('#mTdata .fx-ti-res');
    if (box) box.innerHTML = '';
  };
  async function post(file, options) {
    if (DEMO) {
      await new Promise(resolve => setTimeout(resolve, 400));
      return {filename:file.name, items:[
        {path:'8613800000000/tdata', phone:'+8613800000000', dc:2, status:'ready'},
        {path:'447700900001/tdata', phone:'+447700900001', dc:4, status:'duplicate', code:'import_duplicate', params:{existing_id:42}},
        {path:'locked/tdata', needs_password:true, status:'failed', code:'tdata_passcode'}
      ], skipped:[{path:'readme.txt', code:'import_unknown_format'}]};
    }
    const body = new FormData();
    body.append('file', file);
    if (options.passcode) body.append('tdata_passcode', options.passcode);
    if (options.debug) body.append('debug', 'true');
    const token = ($('#token') || {}).value || localStorage.getItem('tam_token') || '';
    const headers = token ? {Authorization:'Bearer ' + token} : {};
    const response = await fetch('/api/import/tdata/inspect', {method:'POST', body, headers});
    const raw = await response.text();
    let data = null;
    try { data = JSON.parse(raw); } catch (_) {}
    if (!response.ok) {
      const error = new Error(response.status === 413 ? '文件太大，超过后端上传上限' : TAO_ERR.describe(data && data.code, data ? data.detail : raw, data && data.retry_after));
      error.status = response.status;
      error.code = data && data.code;
      throw error;
    }
    return data || {};
  }
  const uploadInspect = async function () {
    if (busy) return;
    const mode = (document.querySelector('input[name="tdataMode"]:checked') || {}).value || 'upload';
    if (mode !== 'upload') { clearResult(); return inspectPath.apply(this, arguments); }
    const files = Array.from(($('#tUpload') || {}).files || []);
    if (!files.length) { toast('先选择含 tdata 的 zip', 'err'); return; }
    const options = {passcode:($('#tPass') || {}).value || '', debug:!!($('#tDebug') || {}).checked};
    const ticket = revision;
    busy = true;
    const modal = document.getElementById('mTdata'), body = modal && modal.querySelector('.body');
    let box = modal && modal.querySelector('.fx-ti-res');
    if (body && !box) {
      box = document.createElement('div'); box.className = 'fx-ti-res fx-mx-res';
      box.setAttribute('aria-live', 'polite'); body.appendChild(box);
    }
    const button = modal && Array.from(modal.querySelectorAll('.foot button')).find(b => /doTdataInspect/.test(b.getAttribute('data-onclick') || ''));
    const label = button ? button.textContent : '';
    if (button) { button.disabled = true; button.textContent = '体检中…'; }
    if (box) box.setAttribute('aria-busy', 'true');
    const items = [], skipped = [], errors = [];
    try {
      for (let i = 0; i < files.length; i++) {
        if (ticket !== revision) return;
        const file = files[i];
        if (box) box.innerHTML = '<p class="muted">正在体检 ' + (i + 1) + '/' + files.length + ' · ' + esc(file.name) + '</p>';
        try {
          const result = await post(file, options);
          if (ticket !== revision) return;
          (result.items || []).forEach(x => items.push({...x, file:file.name}));
          (result.skipped || []).forEach(x => skipped.push({...x, file:file.name}));
        } catch (error) {
          if (ticket !== revision) return;
          if (error.status === 404 || error.status === 405) {
            if (box) box.innerHTML = '';
            toast('后端还不支持上传体检，请更新后端；现在可直接导入（失败会带诊断）', 'err');
            return;
          }
          errors.push(file.name + '：' + (error.message || error));
        }
      }
      const ready = items.filter(x => x.status === 'ready').length;
      const duplicate = items.filter(x => x.status === 'duplicate').length;
      const failed = items.length - ready - duplicate;
      const chip = (text, count, style = '') => count ? '<span class="fx-mx-chip ' + style + '">' + text + ' ' + count + '</span>' : '';
      const status = {ready:['可导入','ok'], duplicate:['已存在','warn']};
      const why = x => (x.code ? TAO_ERR.describe(x.code, x.reason || '') : (x.reason || '')) +
        (x.code === 'import_duplicate' && x.params && x.params.existing_id != null ? '（#' + x.params.existing_id + '）' : '');
      const rows = items.slice(0, 300).map(x => {
        const state = status[x.status] || ['不可用', 'err'];
        const directory = files.length > 1 ? x.file + ' / ' + (x.path || '') : (x.path || x.file);
        return '<tr><td title="' + esc(directory) + '">' + esc(directory) + '</td><td>' + esc(x.phone || '—') + '</td><td>' + (x.dc != null ? 'DC' + esc(x.dc) : '—') + '</td><td><span class="fx-mx-st ' + state[1] + '">' + state[0] + '</span></td><td>' + esc(why(x) || (x.needs_password ? '需要本地密码' : '')) + '</td></tr>';
      }).join('');
      if (box) box.innerHTML = '<div class="fx-mx-sum"><span class="fx-mx-chip">体检结果 · 未写入</span>' + chip('可导入', ready, 'ok') + chip('已存在', duplicate, 'warn') + chip('不可用', failed, 'err') + chip('忽略的文件', skipped.length) + chip('上传失败', errors.length, 'err') + '</div>' +
        (errors.length ? '<p class="fx-mx-err">' + errors.map(esc).join('<br>') + '</p>' : '') +
        (items.length ? '<div class="fx-mx-tw" tabindex="0" aria-label="tdata 体检明细"><table class="fx-mx-t"><thead><tr><th>目录</th><th>手机号</th><th>DC</th><th>状态</th><th>说明</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : (errors.length ? '' : '<p class="muted">压缩包里没找到 tdata。</p>')) +
        (items.length > 300 ? '<p class="muted">只显示前 300 条</p>' : '') +
        (items.some(x => x.code === 'tdata_passcode' || x.needs_password) ? '<p class="muted">有 tdata 设了本地密码：填写「本地密码 passcode」后再体检一次。</p>' : '');
      toast(items.length ? '体检完成：' + ready + '/' + items.length + ' 个可导入' : '体检完成，没有可用的 tdata', ready ? 'ok' : 'err');
    } finally {
      busy = false;
      if (box) box.setAttribute('aria-busy', 'false');
      if (button) { button.disabled = false; button.textContent = label; }
    }
  };
  // TAO_HOOK is declared after the core functions. Queue this early-layer hook
  // so it is registered once the deterministic composition registry exists.
  const registerTdataHook = hooks => hooks.around('doTdataInspect', function (next, args) {
    const mode = (document.querySelector('input[name="tdataMode"]:checked') || {}).value || 'upload';
    return mode === 'upload' ? uploadInspect.apply(this, args) : next(...args);
  }, 'tdata:upload');
  if (globalThis.TAO_HOOK) registerTdataHook(globalThis.TAO_HOOK);
  else (globalThis.__TAO_HOOK_PENDING ||= []).push(registerTdataHook);
  const upload = $('#tUpload'), passcode = $('#tPass'), debug = $('#tDebug');
  if (upload) upload.addEventListener('change', clearResult);
  if (passcode) passcode.addEventListener('input', clearResult);
  if (debug) debug.addEventListener('change', clearResult);
})();

/* 自动刷新 */
let timer = null;
let stickyFrame = 0;
function scheduleStickyOffsets() {
  if (stickyFrame) return;
  stickyFrame = requestAnimationFrame(() => { stickyFrame = 0; syncStickyOffsets(); });
}
function syncStickyOffsets() {
  const root = document.documentElement;
  const header = document.querySelector('body > header');
  const toolbar = document.querySelector('.card[data-widget="accounts"] .toolbar');
  if (header) root.style.setProperty('--tao-header-height', Math.ceil(header.getBoundingClientRect().height) + 'px');
  if (toolbar) root.style.setProperty('--tao-toolbar-height', Math.ceil(toolbar.getBoundingClientRect().height) + 'px');
}
syncStickyOffsets();
if (typeof ResizeObserver !== 'undefined') {
  const header = document.querySelector('body > header');
  const toolbar = document.querySelector('.card[data-widget="accounts"] .toolbar');
  const ro = new ResizeObserver(scheduleStickyOffsets);
  if (header) ro.observe(header);
  if (toolbar) ro.observe(toolbar);
}
window.addEventListener('resize', scheduleStickyOffsets, {passive: true});
$('#auto').addEventListener('change', e => {
  clearInterval(timer);
  if (e.target.checked) timer = setInterval(() => { if (!document.hidden) refresh(); }, 10000);
});
// 标签页切回来时，如果开着自动刷新就立刻补一次，后台期间不白白拉接口。
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && $('#auto') && $('#auto').checked) refresh();
});

/* 启动 */
loadViewPrefs();
applyView();
if (DEMO) {
  $('#demoBar').classList.add('on');
  $('#mode').textContent = '演示模式';
  $('#mode').className = 'pill ro';
  setAccounts(MOCK.accounts); stats = MOCK.stats;
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
    // 自动刷新时重建工具箱前先保存当前草稿，避免并发/参数被清空。
    tbSaveDraft();
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

const TB_GROUPS = [['check', '账号检测'], ['messages', '消息与群组'], ['more', '更多工具']];
let TB_BUSY = false;
const TB_DRAFTS = new Map();
function tbGroup(op) {
  if (['quick_alive', 'alive', 'spam_check', 'twofa_status', 'okpay_balance'].includes(op.op)) return 'check';
  if (/message|group|chat|member|contact|public|block/.test(op.op)) return 'messages';
  return 'more';
}
function tbAdvanced(p) {
  return !p.required && ['timeout', 'wait', 'limit'].includes(p.name);
}
function tbSaveDraft() {
  const sp = tbSpec();
  if (!sp) return;
  const values = {};
  for (const p of sp.params) {
    if (p.type === 'password') continue;
    const el = $('#tbp_' + p.name);
    if (el) values[el.id] = {value:el.value, checked:el.checked};
    if (p.type === 'privacy') for (const [key] of TB_PRIV_KEYS) {
      const field = $('#tbpriv_' + key);
      if (field) values[field.id] = {value:field.value};
    }
  }
  const conc = $('#tbConc');
  if (conc) values.tbConc = {value:conc.value};
  TB_DRAFTS.set(sp.op, values);
}
function tbChoose(op) {
  if (TB_BUSY || !TB_OPS.some(o => o.op === op)) return;
  tbSaveDraft(); TB_CUR = op; renderTb();
}
function tbChooseGroup(group) {
  const first = TB_OPS.find(o => tbGroup(o) === group);
  if (first) tbChoose(first.op);
}
function syncTbSelection() {
  const label = $('#tbSel'), button = $('#tbRun');
  if (label) label.textContent = sel.size ? '已选择 ' + sel.size + ' 个账号' : '尚未选择账号，请先在账号列表勾选';
  if (button) button.disabled = readonly || TB_BUSY || !sel.size || !tbSpec();
}
function toolboxJump(widget) {
  switchView('toolbox');
  const el = document.querySelector('[data-widget="' + widget + '"]');
  if (!el) return;
  el.hidden = false; el.style.display = '';
  el.scrollIntoView({block:'start'});
  el.setAttribute('tabindex', '-1'); el.focus({preventScroll:true});
}
function renderTb() {
  const box = $('#tbBody');
  if (!box || TB_BUSY) return;
  const previous = $('#tbOut');
  const resultHTML = previous ? previous.innerHTML : '';
  const sp = tbSpec();
  const group = sp ? tbGroup(sp) : 'check';
  const tabs = TB_GROUPS.map(([key,label]) => '<button type="button" class="fit" aria-pressed="' +
    (group === key) + '" data-onclick="tbChooseGroup(\'' + key + '\')">' + label + '</button>').join('');
  const opts = TB_OPS.filter(o => tbGroup(o) === group).map(o => '<option value="' + esc(o.op) + '"' +
    (o.op === TB_CUR ? ' selected' : '') + '>' + (o.danger ? '⚠ ' : '') + esc(o.label) + '</option>').join('');
  const basic = sp ? sp.params.filter(p => !tbAdvanced(p)).map(tbField).join('') : '';
  const advanced = sp ? sp.params.filter(tbAdvanced).map(tbField).join('') : '';
  box.innerHTML =
    '<div class="tb-steps">① 选择账号 → ② 选择工具与参数 → ③ 确认执行</div>' +
    '<div class="tb-categories" role="group" aria-label="工具分类">' + tabs + '</div>' +
    '<label class="f"><span>选择工具</span><select id="tbOp" data-onchange="tbChoose(this.value)">' + opts + '</select></label>' +
    '<p class="muted">' + (sp ? esc(sp.desc || '') : '工具加载中，请稍后刷新') + '</p>' +
    (sp && sp.danger ? '<div class="tb-warning">⚠ 此操作会更改账号或数据，执行前请核对所选账号；下一步会再次确认。</div>' : '') +
    basic + (!basic ? '<p class="muted">无需额外填写参数，使用默认配置即可。</p>' : '') +
    '<details class="tb-advanced"><summary>高级设置 · 超时 / 数量 / 并发</summary>' + advanced +
    '<label class="f"><span>并发度</span><input id="tbConc" type="number" min="1" max="32" step="1" placeholder="留空使用全局并发设置" /></label></details>' +
    '<p class="muted small">切换工具保留普通参数；密码仅用于当前表单，不保存到浏览器。</p>' +
    '<div class="tb-actions"><span id="tbSel" role="status"></span>' +
    '<button type="button" class="sm fit" data-onclick="toolboxJump(\'accounts\')">选择账号</button>' +
    '<button id="tbRun" class="sm write" data-onclick="runTb()">' + (sp ? '开始 · ' + esc(sp.label) : '开始') + '</button></div>' +
    '<div id="tbOut" aria-live="polite" style="margin-top:10px">' + resultHTML + '</div>';
  const draft = TB_DRAFTS.get(TB_CUR) || {};
  for (const [id, values] of Object.entries(draft)) {
    const el = $('#' + id);
    if (el) { el.value = values.value; if (values.checked !== undefined) el.checked = values.checked; }
  }
  syncTbSelection();
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
    if (p.type === 'bool') {
      if (p.required && !el.checked) { el.focus(); throw new Error('请确认：' + p.label); }
      out[p.name] = el.checked; continue;
    }
    const raw = String(el.value || '').trim();
    if (!raw) {
      if (p.required) { el.focus(); throw new Error('请填写：' + p.label); }
      continue;
    }
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
  // 仅在提交瞬间防止重复点击；任务启动后释放界面，允许其它工具并行。
  if (TB_BUSY || readonly) return;
  TB_BUSY = true; syncTbSelection();
  try { await runTbAction(); }
  finally { TB_BUSY = false; syncTbSelection(); }
}
async function runTbAction() {
  const sp = tbSpec();
  if (!sp) return;
  let ids = [...sel];
  if (!ids.length) { toast('先在账号列表里勾选要操作的号', 'err'); return; }
  if (sp.op === 'twofa') {
    const before = ids.length;
    ids = ids.filter(id => { const a = accounts.find(x => x.id === id); return a && a.status === 'active' && a.authorized !== false; });
    if (!ids.length) { toast('选中账号没有可用的 active 会话', 'err'); return; }
    if (ids.length < before) toast(`已跳过 ${before - ids.length} 个非 active/未授权账号`, '');
  }

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
  // 改 2FA 主要耗时在每个账号的 Telegram 往返；默认使用独立并发值，仍可在工具箱手动覆盖。
  let conc = cv ? Number(cv) : (sp.op === 'twofa' ? cfgInt('TAM_TWOFA_CONCURRENCY', 6) : batchConc(3));
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
    }, '工具箱 · ' + sp.label, conc, sp.op === 'twofa', sp.op === 'twofa' ? 32 : 8);
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

function renderZt(force = false) {
  const box = $('#ztBody');
  if (!box) return;
  // 自动刷新只更新账号数据，不重建文件输入；否则用户已选择的 zip/tdata/session 会被浏览器清空。
  if (!force && box.dataset.rendered === '1') return;
  box.dataset.rendered = '1';
  box.innerHTML =
    '<div class="muted" style="margin-bottom:8px">以下工具处理上传文件，不使用账号列表的勾选。展开一个工具即可开始。' +
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
    '<button class="sm" data-onclick="ztAnalyze()">先看看有多少个号</button>' +
    '<button class="sm write" data-onclick="ztUnpack()">拆包</button></div>' +
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
    '<button class="sm" data-onclick="ztMergeAdd()">添加到合并作业</button>' +
    '<button class="sm write" data-onclick="ztMergeRun()">开始合并</button>' +
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
    '<button class="sm write" data-onclick="ztRegtime()">开始分类</button></div>' +
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
    '<button class="sm write" data-onclick="ztToapi()">开始转 API</button></div>' +
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
    '<button class="sm write" data-onclick="ztConvert()">开始互转</button></div>' +
    '<div id="ztCvtOut" style="margin:6px 0 12px"></div>' +

    '<h4 style="margin:10px 0 4px">六、Passkey</h4>' +
    '<div class="muted" style="margin-bottom:4px">对齐机器人 /passkey 创建方向：上传 session 号包，初始化 Passkey 注册并下载凭证 JSON。</div>' +
    '<label class="f"><span>session 号包 zip</span>' +
    '<input type="file" id="ztPkFile" accept=".zip" /></label>' +
    '<div class="row" style="gap:8px">' +
    '<button class="sm write" data-onclick="ztPasskey()">创建 Passkey 凭证包</button></div>' +
    '<div id="ztPkOut" style="margin:6px 0"></div>';

  // Collapse sections without rebuilding their inputs, keeping selected files intact.
  Array.from(box.querySelectorAll('h4')).forEach((heading, index) => {
    const panel = document.createElement('details');
    panel.className = 'zt-tool'; panel.open = index === 4;
    const summary = document.createElement('summary');
    summary.textContent = heading.textContent;
    heading.before(panel); panel.appendChild(summary);
    let next = heading.nextSibling;
    while (next && next.nodeName !== 'H4') {
      const following = next.nextSibling;
      panel.appendChild(next); next = following;
    }
    heading.remove();
  });
  if (readonly) box.querySelectorAll('.write').forEach(function (b) { b.disabled = true; });
}
/* ---------- ZIP 工具：动作 ---------- */

function ztBtns(job, url, filename) {
  return '<div class="row" style="gap:8px;margin-top:6px">' +
    '<button class="sm" data-onclick="ztDownload(\'' + url + '\',\'' + filename + '\')">' +
    '下载 ' + filename + '</button>' +
    '<button class="sm" data-onclick="ztCleanup(\'' + job + '\')">用完删除</button></div>';
}

async function ztCleanup(job) {
  try {
    await ztFetch('/api/tools/unpack/' + encodeURIComponent(job), {method: 'DELETE'});
    toast('服务器上的临时文件已删干净', 'ok');
      renderZt(true);
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
      h += '<button class="sm" data-onclick="ztDownload(\'' + pk.url + '\',\'' +
        pk.filename + '\')">' + esc(pk.filename) + ' (' + pk.size + ' 个)</button>';
    });
    h += '</div><div class="row" style="margin-top:6px">' +
      '<button class="sm" data-onclick="ztCleanup(\'' + r.job + '\')">用完删除</button></div>';
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
  function restoreFabPosition() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
      const width = fab.offsetWidth || 52, height = fab.offsetHeight || 52;
      const maxL = Math.max(4, window.innerWidth - width - 4);
      const maxT = Math.max(4, window.innerHeight - height - 4);
      if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.top)
          && saved.left >= 4 && saved.top >= 4 && saved.left <= maxL && saved.top <= maxT) {
        fab.style.left = Math.round(saved.left) + 'px';
        fab.style.top = Math.round(saved.top) + 'px';
        fab.style.right = 'auto';
        fab.style.bottom = 'auto';
        return;
      }
      localStorage.removeItem(KEY);
    } catch (_) {}
  }
  restoreFabPosition();

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
    if (moved) {
      fab._suppressClick = true;
      setTimeout(() => { fab._suppressClick = false; }, 0);
    }
  }
  fab.addEventListener('click', () => {
    if (fab._suppressClick) { fab._suppressClick = false; return; }
    openAiPanel();
  });
  fab.addEventListener('mousedown', onStart);
  fab.addEventListener('touchstart', onStart, {passive: false});
  window.addEventListener('mousemove', onMove, {passive: false});
  window.addEventListener('touchmove', onMove, {passive: false});
  window.addEventListener('resize', () => {
    if (fab.style.left === '' || fab.style.top === '') return;
    const maxL = Math.max(4, window.innerWidth - fab.offsetWidth - 4);
    const maxT = Math.max(4, window.innerHeight - fab.offsetHeight - 4);
    const left = Math.max(4, Math.min(parseFloat(fab.style.left) || 4, maxL));
    const top = Math.max(4, Math.min(parseFloat(fab.style.top) || 4, maxT));
    fab.style.left = left + 'px'; fab.style.top = top + 'px';
    try { localStorage.setItem(KEY, JSON.stringify({left, top})); } catch (_) {}
  });
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
  const active = document.activeElement;
  const returnFocus = active && active !== document.body ? active : $('#aiFab');
  closeAll();
  const p = $('#aiPanel');
  if (!p) return;
  p.classList.add('on');
  TAO_MOTION.open(p);
  p.setAttribute('aria-hidden', 'false');
  $('#aiFab')?.setAttribute('aria-expanded', 'true');
  document.body.classList.add('modal-open');
  focusDialog(p, returnFocus);
  loadAiConfig();
  try { bindAiInput(); } catch (_) {}
}
function closeAiPanel() {
  const p = $('#aiPanel');
  if (p) {
    p.classList.remove('on');
    TAO_MOTION.close(p);
    p.setAttribute('aria-hidden', 'true');
    if (_dialogEl === p) releaseDialogFocus();
  }
  $('#aiFab')?.setAttribute('aria-expanded', 'false');
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
      ' data-onchange="aiUpdatePermCount()" />' +
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
      '<label class="f"><span>接口格式</span><select id="aiProvider" data-onchange="aiProviderChange()">' +
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
      '<label class="f"><span>提示词预设</span><select id="aiPromptPreset" data-onchange="aiApplyPromptPreset()">' +
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
        '<button type="button" class="sm write" data-onclick="applyAiPresetOnly()">套用预设到勾选</button>' +
        '<button type="button" class="sm" data-onclick="aiPermSetAll(true)">全选</button>' +
        '<button type="button" class="sm" data-onclick="aiPermSetAll(false)">全不选</button>' +
        '<button type="button" class="sm" data-onclick="aiPermSetDanger(\'read\')">仅只读</button>' +
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
      '<button class="primary write" data-onclick="saveAiConfig()">保存配置</button>' +
      '<button class="sm" data-onclick="loadAiConfig()">重新加载</button>' +
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
    var returnFocus = document.activeElement;
    var modal = document.createElement('div');
    modal.className = 'modal on';
    modal.id = 'mAiConfirm';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
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
      $('#bd').classList.remove('on');
      document.body.classList.remove('modal-open');
      closeAll();
      if (modal && modal.parentNode) modal.parentNode.removeChild(modal);
    };
    modal.__taoEscClose = function () { close(); resolve(null); };
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
    closeAiPanel();
    focusDialog(modal, returnFocus);
    TAO_MOTION.open(modal);
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

TAO_KEYS.esc('ai-panel', 20, () => {
  const p = $('#aiPanel');
  return !!(p && p.classList.contains('on'));
}, () => closeAiPanel());


async function bulkReplaceProxy() {
  const ids = [...sel];
  if (!ids.length) return toast('请先勾选账号', 'err');
  const input = await uiPrompt({title:'批量更换代理', label:'节点 URL（多个节点用逗号分隔）', message:'保留 session 和二验；新连接使用新代理，已有连接不强制中断。', placeholder:'socks5://user:password@host:1080'});
  if (!input) return;
  const limit = await uiPrompt({title:'每节点全局容量', label:'0 表示不限', placeholder:'0'});
  if (limit === null) return;
  const capacity = Number(limit || 0);
  if (!Number.isSafeInteger(capacity) || capacity < 0) return toast('容量需要非负整数', 'err');
    const body = {account_ids:ids, proxy_pool:input.split(/[,，\n]+/).map(x=>x.trim()).filter(Boolean), capacity, preview:true};
    try {
      const plan = await api('/api/accounts/batch-proxy', {method:'POST',body:JSON.stringify(body)});
      const usage = (plan.usage || []).map(x=>`节点 ${x.node}：分配后 ${x.used} 个`).join('\n');
      if (!await uiConfirm({
        title: '确认更换代理',
        message: `将迁移 ${plan.count} 个账号，跳过登录中 ${plan.skipped_busy.length} 个。\n${usage}\n\n确认更换？`,
        danger: true,
        okText: '确认更换',
      })) return;
      body.preview = false;
    const result = await api('/api/accounts/batch-proxy', {method:'POST',body:JSON.stringify(body)});
    toast(`已更换 ${result.count} 个账号；登录中跳过 ${result.skipped_busy.length} 个`, 'ok');
    await refresh();
  } catch(e) { toast(e.message, 'err'); }
}

/* ========================================================================
   TAO_HOOK · deterministic function composition for FX layers
   Each target receives one registry-owned wrapper; subsequent registrations
   extend the same chain instead of creating another assignment wrapper.
   ======================================================================== */
const TAO_HOOK = (() => {
  const records = new Map();
  const accessors = new Map();

  const accessor = name => accessors.get(name) || {
    get: () => (typeof globalThis !== 'undefined' ? globalThis[name] : undefined),
    set: value => { if (typeof globalThis !== 'undefined') globalThis[name] = value; }
  };

  function bind(name, get, set) {
    if (typeof get === 'function' && typeof set === 'function') accessors.set(name, {get, set});
    return api;
  }

  function invokeHook(hook, value, args, self) {
    try {
      const next = hook.fn.call(self, value, args, self);
      return hook.mode === 'result' && next !== undefined ? next : value;
    } catch (_) {
      return value;
    }
  }

  function compose(base, hooks) {
    let composed = base;
    hooks.forEach(hook => {
      const previous = composed;
      if (hook.mode === 'around') {
        composed = function () {
          const self = this, args = [...arguments];
          let calledNext = false;
          const next = (...nextArgs) => { calledNext = true; return previous.apply(self, nextArgs.length ? nextArgs : args); };
          try { return hook.fn.call(self, next, args, self); } catch (error) {
            // If next already ran, rethrow its failure instead of running the
            // underlying function a second time. This preserves side effects.
            if (calledNext) throw error;
            return previous.apply(self, args);
          }
        };
      } else {
        composed = function () {
          const self = this, args = [...arguments];
          let value;
          try { value = previous.apply(self, args); } catch (error) { throw error; }
          if (value && typeof value.then === 'function') {
            if (hook.mode === 'result') {
              return value.then(result => invokeHook(hook, result, args, self), error => Promise.reject(error));
            }
            // after hooks are side effects: preserve the original Promise
            // identity and rejection semantics while observing the result.
            value.then(result => { invokeHook(hook, result, args, self); }, () => {});
            return value;
          }
          return invokeHook(hook, value, args, self);
        };
      }
      composed.__taoHookName = hook.name;
    });
    return composed;
  }

  function register(mode, name, fn, tag) {
    if (typeof name !== 'string' || typeof fn !== 'function') return false;
    const slot = accessor(name);
    let record = records.get(name);
    if (!record) {
      const base = slot.get();
      if (typeof base !== 'function') return false;
      record = {name, base, hooks: [], wrapper: null};
      records.set(name, record);
    }
    const hookTag = tag || mode;
    if (record.hooks.some(hook => hook.tag === hookTag)) return true;
    record.hooks.push({name, mode, fn, tag: hookTag});
    record.wrapper = compose(record.base, record.hooks);
    slot.set(record.wrapper);
    return true;
  }

  const api = {
    bind,
    after: (name, fn, tag) => register('after', name, fn, tag),
    around: (name, fn, tag) => register('around', name, fn, tag),
    result: (name, fn, tag) => register('result', name, fn, tag),
    list(log = false) {
      const rows = [...records.values()].map(record => ({
        name: record.name,
        hooks: record.hooks.map(hook => ({mode: hook.mode, tag: hook.tag}))
      }));
      if (log && typeof console !== 'undefined' && console.table) console.table(rows);
      return rows;
    },
    reset() {
      records.forEach(record => accessor(record.name).set(record.base));
      records.clear();
    }
  };
  return api;
})();
// Expose the registry for the early tdata layer and flush queued registrations.
if (typeof globalThis !== 'undefined') {
  globalThis.TAO_HOOK = TAO_HOOK;
  const pending = globalThis.__TAO_HOOK_PENDING;
  if (Array.isArray(pending)) {
    pending.splice(0).forEach(register => { try { register(TAO_HOOK); } catch (_) {} });
  }
}

/* One body observer for FX layers that need to react to inserted nodes. */
const TAO_DOM = (() => {
  const entries = new Set();
  let observer = null;
  const scan = node => {
    if (!node || node.nodeType !== 1) return;
    entries.forEach(entry => {
      try {
        if (node.matches(entry.selector)) entry.fn(node);
        node.querySelectorAll(entry.selector).forEach(entry.fn);
      } catch (_) {}
    });
  };
  const ensure = () => {
    if (observer || typeof MutationObserver === 'undefined' || !document.body) return;
    observer = new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(scan)));
    observer.observe(document.body, {childList: true, subtree: true});
  };
  const api = {
    onAdded(selector, fn) {
      if (!selector || typeof fn !== 'function') return () => {};
      const entry = {selector, fn}; entries.add(entry); ensure();
      if (document.body) document.body.querySelectorAll(selector).forEach(fn);
      return () => entries.delete(entry);
    },
    scan,
    disconnect() { if (observer) observer.disconnect(); observer = null; }
  };
  if (document.body) ensure();
  else document.addEventListener('DOMContentLoaded', ensure, {once: true});
  return api;
})();

/* =========================================================================
   创意层交互（TAO_FX）
   聚光描边 · 磁吸按钮 · 导航滑块 · 视图水印与错峰入场 · 数字滚动 · 跑马灯 · 开场 · 主题圆形扩散
   全部只读写 CSS 变量 / transform / opacity；任何一步失败都不影响业务操作。
   ========================================================================= */
const TAO_FX = (() => {
  const media = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  const reduced = () => !!media?.matches;
  const finePointer = window.matchMedia ? window.matchMedia('(hover: hover) and (pointer: fine)') : null;
  const MARKS = {accounts:'Accounts', tasks:'Tasks', leads:'Leads', toolbox:'Toolbox', logs:'Logs', settings:'Settings'};
  const safe = fn => { try { fn(); } catch (_) {} };

  /* 聚光：指针所在卡片写入 --mx/--my，一帧最多一次 */
  let spotEl = null, spotX = 0, spotY = 0, spotFrame = 0;
  const SPOT = '.card,.stat-card,.kickbar,.tb-launchers button,.sys-grid button';
  function flushSpot() {
    spotFrame = 0;
    if (!spotEl) return;
    const r = spotEl.getBoundingClientRect();
    spotEl.style.setProperty('--mx', (spotX - r.left) + 'px');
    spotEl.style.setProperty('--my', (spotY - r.top) + 'px');
  }

  /* 磁吸：主按钮和圆按钮被指针轻轻吸过去（用独立的 translate 属性，不和已有 transform 打架） */
  const MAG = 'button.primary,.round-btn,.brand';
  let magEl = null;
  function magMove(e) {
    if (!magEl) return;
    const r = magEl.getBoundingClientRect();
    const dx = (e.clientX - (r.left + r.width / 2)) / r.width;
    const dy = (e.clientY - (r.top + r.height / 2)) / r.height;
    magEl.style.translate = (dx * 8).toFixed(1) + 'px ' + (dy * 6).toFixed(1) + 'px';
  }
  function magReset(el) { if (el) el.style.translate = ''; }

  function bindPointer() {
    if (!finePointer?.matches) return;
    document.addEventListener('pointermove', e => {
      const t = e.target instanceof Element ? e.target : null;
      const s = t && t.closest(SPOT);
      if (s) {
        spotEl = s; spotX = e.clientX; spotY = e.clientY;
        if (!spotFrame) spotFrame = requestAnimationFrame(flushSpot);
      }
      if (reduced()) return;
      const m = t && t.closest(MAG);
      if (m !== magEl) { magReset(magEl); magEl = m && !m.disabled ? m : null; }
      if (magEl) magMove(e);
    }, {passive: true});
    document.addEventListener('pointerleave', () => { magReset(magEl); magEl = null; }, {passive: true});
  }

  /* 导航滑块 */
  let glider = null;
  function syncNav() {
    const nav = document.getElementById('mainNav');
    if (!nav) return;
    if (!glider) {
      glider = document.createElement('span');
      glider.className = 'nav-glider';
      glider.setAttribute('aria-hidden', 'true');
      nav.prepend(glider);
      nav.classList.add('has-glider');
    }
    const on = nav.querySelector('.nav-item.active');
    if (!on || !on.offsetWidth) { glider.style.opacity = '0'; return; }
    glider.style.width = on.offsetWidth + 'px';
    glider.style.transform = 'translateX(' + on.offsetLeft + 'px)';
    glider.style.opacity = '1';
  }

  /* 视图切换：水印 + 卡片错峰入场（只在视图真的变了时触发，render() 反复调用不会重放） */
  let lastView = null;
  function onView(view) {
    const stage = document.getElementById('appShell');
    if (stage) stage.dataset.fxMark = MARKS[view] || '';
    syncNav();
    if (view === lastView) return;
    const first = lastView === null;
    lastView = view;
    if (first || reduced()) return;
    const cards = [...document.querySelectorAll('#board > .card:not(.view-hidden), .rail:not(.view-hidden) > *')];
    cards.slice(0, 8).forEach((el, i) => {
      el.classList.remove('fx-enter');
      void el.offsetWidth;
      el.style.setProperty('--fx-i', i);
      el.classList.add('fx-enter');
      el.addEventListener('animationend', () => el.classList.remove('fx-enter'), {once: true});
    });
  }

  /* 账号总数：数值变化时滚动计数 */
  let lastTotal = null;
  function countUp() {
    const el = document.querySelector('#accountOverview .stat-num');
    if (!el) return;
    const to = Number(typeof stats === 'object' && stats ? stats.total : NaN) || 0;
    const from = lastTotal == null ? 0 : lastTotal;
    lastTotal = to;
    if (from === to || reduced()) return;
    const t0 = performance.now(), dur = Math.min(1200, 500 + Math.abs(to - from) * 4);
    const step = now => {
      if (!el.isConnected) return;
      const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 4);
      el.textContent = String(Math.round(from + (to - from) * e));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /* 演示条 → 跑马灯（内容复制一份，无缝循环） */
  function marquee() {
    const bar = document.getElementById('demoBar');
    if (!bar || bar.querySelector('.mq')) return;
    const html = bar.innerHTML;
    bar.setAttribute('aria-label', bar.textContent.trim());
    bar.innerHTML = '<div class="mq" aria-hidden="true">' + ('<span>' + html + '</span>').repeat(4) + '</div>';
  }

  /* 开场：每个会话一次，0.9 秒后自行揭开 */
  function intro() {
    if (reduced()) return;
    try { if (sessionStorage.getItem('tao.intro')) return; sessionStorage.setItem('tao.intro', '1'); } catch (_) { return; }
    if (typeof taoParticleIntro === 'function' && taoParticleIntro()) return;
    const el = document.createElement('div');
    el.className = 'fx-intro';
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = '<b><span>T</span><span>A</span><span>O</span><i>.</i></b><small>Telegram Account Orchestrator</small>';
    document.body.appendChild(el);
    el.addEventListener('animationend', e => { if (e.animationName === 'fx-intro-out') el.remove(); });
    setTimeout(() => el.remove(), 2600);
  }

  /* 主题切换：从按钮位置圆形扩散 */
  function themeTransition(apply) {
    const btn = document.getElementById('themeBtn');
    if (!document.startViewTransition || reduced() || !btn) return apply();
    const r = btn.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const R = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    const vt = document.startViewTransition(apply);
    vt.ready.then(() => {
      document.documentElement.animate(
        {clipPath: ['circle(0px at ' + x + 'px ' + y + 'px)', 'circle(' + R + 'px at ' + x + 'px ' + y + 'px)']},
        {duration: 650, easing: 'cubic-bezier(.16,1,.3,1)', pseudoElement: '::view-transition-new(root)'}
      );
    }).catch(() => {});
  }

  function init() {
    safe(bindPointer);
    safe(marquee);
    safe(intro);
    let rz = 0;
    window.addEventListener('resize', () => { if (!rz) rz = requestAnimationFrame(() => { rz = 0; syncNav(); }); }, {passive: true});
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => safe(syncNav));
  }
  return {init, onView, syncNav, countUp, themeTransition, reduced};
})();

// 挂到现有流程上：视图、统计、主题。注册顺序即执行顺序。
(() => {
  TAO_HOOK.after('applyView', () => TAO_FX.onView(currentView), 'fx2:view');
  TAO_HOOK.after('renderStats', () => TAO_FX.countUp(), 'fx2:stats');
  TAO_HOOK.around('toggleTheme', (next, args) => TAO_FX.themeTransition(() => next(...args)), 'fx2:theme');
  TAO_FX.init();
  try { TAO_FX.onView(currentView); } catch (_) {}
})();

/* =========================================================================
   创意层 · 第三轮交互（TAO_FX3）
   WebGL 流体背景 · 自定义光标 · 3D 倾斜 · 点击迸发 · 字母水印 · 实时时钟 · 彩蛋
   ========================================================================= */
const TAO_FX3 = (() => {
  const reduced = () => TAO_FX.reduced();
  const fine = window.matchMedia ? window.matchMedia('(hover: hover) and (pointer: fine)').matches : false;
  const root = document.documentElement;
  const isDark = () => root.dataset.theme === 'dark';
  const ptr = {x: innerWidth / 2, y: innerHeight / 2, nx: .5, ny: .5};

  /* ---------- WebGL 流体：域扭曲 fbm，低分辨率渲染再放大，30fps，后台标签页暂停 ---------- */
  function gl() {
    if (reduced()) return;
    /* 触屏 / 小屏 / 省流量 / 低内存设备不跑 GPU 背景，CSS 光晕兜底 */
    const nav = navigator, conn = nav.connection || {};
    if (!fine || innerWidth < 820 || conn.saveData || (nav.deviceMemory && nav.deviceMemory <= 4) || (nav.hardwareConcurrency && nav.hardwareConcurrency <= 2)) return;
    const cv = document.createElement('canvas');
    cv.id = 'fxGL'; cv.setAttribute('aria-hidden', 'true');
    const g = cv.getContext('webgl', {antialias: false, depth: false, alpha: false, powerPreference: 'low-power'});
    if (!g) return;
    const vs = 'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';
    const fs = `precision mediump float;
uniform vec2 r;uniform float t;uniform vec2 m;uniform vec3 c0,c1,c2,c3;uniform float dk;
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p=p*2.03+vec2(1.7,9.2);a*=.5;}return v;}
void main(){
  vec2 uv=gl_FragCoord.xy/r;float as=r.x/r.y;
  vec2 p=uv*vec2(as,1.)*1.5;vec2 mm=m*vec2(as,1.)*1.5;float d=length(p-mm);
  vec2 q=vec2(fbm(p+t*.035),fbm(p+vec2(5.2,1.3)-t*.03));
  vec2 w=vec2(fbm(p+2.*q+vec2(1.7,9.2)+t*.045),fbm(p+2.*q+vec2(8.3,2.8)-t*.035));
  w+=.35*exp(-d*2.4);
  float f=fbm(p+2.6*w);
  vec3 col=mix(c0,c1,smoothstep(.15,.95,f));
  col=mix(col,c2,smoothstep(.55,1.05,length(q)*f*1.6)*.9);
  col=mix(col,c3,smoothstep(.5,1.,w.y*f*1.5)*.8);
  col+=c2*.22*exp(-d*3.2);
  float v=smoothstep(.35,1.25,length(uv-.5)*1.5);
  col=mix(col,c0,v*(.35+.4*dk));
  col+=(h(gl_FragCoord.xy+t)-.5)*.018;
  gl_FragColor=vec4(col,1.);
}`;
    const sh = (type, src) => { const s = g.createShader(type); g.shaderSource(s, src); g.compileShader(s); return g.getShaderParameter(s, g.COMPILE_STATUS) ? s : null; };
    const v = sh(g.VERTEX_SHADER, vs), f = sh(g.FRAGMENT_SHADER, fs);
    if (!v || !f) return;
    const pr = g.createProgram(); g.attachShader(pr, v); g.attachShader(pr, f); g.linkProgram(pr);
    if (!g.getProgramParameter(pr, g.LINK_STATUS)) return;
    g.useProgram(pr);
    const b = g.createBuffer(); g.bindBuffer(g.ARRAY_BUFFER, b);
    g.bufferData(g.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), g.STATIC_DRAW);
    const loc = g.getAttribLocation(pr, 'p'); g.enableVertexAttribArray(loc); g.vertexAttribPointer(loc, 2, g.FLOAT, false, 0, 0);
    const U = k => g.getUniformLocation(pr, k);
    const u = {r: U('r'), t: U('t'), m: U('m'), c0: U('c0'), c1: U('c1'), c2: U('c2'), c3: U('c3'), dk: U('dk')};
    const hex = s => [1, 3, 5].map(i => parseInt(s.slice(i, i + 2), 16) / 255);
    const PAL = {
      dark: ['#09090B', '#15131C', '#4B6A12', '#3A2A86'],
      light: ['#F3F1EB', '#E4E1D6', '#DDFF8A', '#D9D0FF']
    };
    function palette() {
      const p = PAL[isDark() ? 'dark' : 'light'].map(hex);
      g.uniform3fv(u.c0, p[0]); g.uniform3fv(u.c1, p[1]); g.uniform3fv(u.c2, p[2]); g.uniform3fv(u.c3, p[3]);
      g.uniform1f(u.dk, isDark() ? 1 : 0);
    }
    const SCALE = .33;
    function size() {
      cv.width = Math.max(2, Math.round(innerWidth * SCALE));
      cv.height = Math.max(2, Math.round(innerHeight * SCALE));
      g.viewport(0, 0, cv.width, cv.height);
      g.uniform2f(u.r, cv.width, cv.height);
    }
    cv.addEventListener('webglcontextlost', e => { e.preventDefault(); root.classList.remove('fx-gl'); cv.remove(); }, {once: true});
    document.body.prepend(cv);
    size(); palette();
    new MutationObserver(palette).observe(root, {attributes: true, attributeFilter: ['data-theme']});
    let rz = 0;
    addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(size, 150); }, {passive: true});
    const t0 = performance.now(); let last = 0, mx = .5, my = .5;
    function frame(now) {
      requestAnimationFrame(frame);
      if (document.hidden || now - last < 33 || root.classList.contains('fx-calm') || document.querySelector('.fx-stars.on')) return;
      last = now;
      mx += (ptr.nx - mx) * .06; my += ((1 - ptr.ny) - my) * .06;
      g.uniform1f(u.t, (now - t0) / 1000);
      g.uniform2f(u.m, mx, my);
      g.drawArrays(g.TRIANGLES, 0, 3);
    }
    requestAnimationFrame(t => { frame(t); root.classList.add('fx-gl'); });
  }

  /* ---------- 字母水印 ---------- */
  let markEl = null, markText = '';
  function mark(text) {
    if (!markEl) {
      markEl = document.createElement('div');
      markEl.className = 'fx-mark'; markEl.setAttribute('aria-hidden', 'true');
      document.body.appendChild(markEl);
      root.classList.add('fx-js');
    }
    if (text === markText) return;
    markText = text;
    markEl.innerHTML = [...text].map((c, i) => '<span style="--i:' + i + '">' + c + '</span>').join('');
  }

  /* ---------- 光标 + 倾斜 + 视差（同一个 pointermove，一帧一次） ---------- */
  let ring, dot, rx = ptr.x, ry = ptr.y, tiltEl = null;
  const HOVER = 'button,a,[role="button"],select,label.chip,summary,.stat-list button,.nav-item';
  const TEXT = 'input:not([type=checkbox]):not([type=radio]),textarea,[contenteditable="true"]';
  const TILT = '.stat-card,.tb-launchers button';
  function cursor() {
    ring = document.createElement('div'); ring.className = 'fx-ring'; ring.setAttribute('aria-hidden', 'true');
    dot = document.createElement('div'); dot.className = 'fx-cursor'; dot.setAttribute('aria-hidden', 'true');
    document.body.append(ring, dot);
    document.addEventListener('pointerdown', () => ring.classList.add('is-down'), {passive: true});
    document.addEventListener('pointerup', () => ring.classList.remove('is-down'), {passive: true});
    document.documentElement.addEventListener('pointerleave', () => root.classList.remove('fx-cursor-on'));
    (function loop() {
      rx += (ptr.x - rx) * .2; ry += (ptr.y - ry) * .2;
      ring.style.transform = 'translate3d(' + rx.toFixed(1) + 'px,' + ry.toFixed(1) + 'px,0)';
      requestAnimationFrame(loop);
    })();
  }
  function setTilt(el, e) {
    if (tiltEl && tiltEl !== el) { tiltEl.classList.remove('is-tilting'); tiltEl.style.removeProperty('--rx'); tiltEl.style.removeProperty('--ry'); }
    tiltEl = el;
    if (!el) return;
    if (!el.querySelector(':scope>.fx-glare')) { const gl = document.createElement('span'); gl.className = 'fx-glare'; gl.setAttribute('aria-hidden', 'true'); el.appendChild(gl); }
    const r = el.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    const k = el.classList.contains('stat-card') ? 7 : 9;
    el.classList.add('is-tilting');
    el.style.setProperty('--ry', ((x - .5) * k).toFixed(2) + 'deg');
    el.style.setProperty('--rx', ((.5 - y) * k).toFixed(2) + 'deg');
    el.style.setProperty('--gx', (x * 100).toFixed(1) + '%');
    el.style.setProperty('--gy', (y * 100).toFixed(1) + '%');
  }
  function pointer() {
    let frame = 0, lastE = null;
    document.addEventListener('pointermove', e => {
      ptr.x = e.clientX; ptr.y = e.clientY; ptr.nx = e.clientX / innerWidth; ptr.ny = e.clientY / innerHeight;
      if (e.pointerType !== 'mouse') return;
      lastE = e;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0; const ev = lastE;
        const t = ev.target instanceof Element ? ev.target : null;
        if (dot) {
          root.classList.add('fx-cursor-on');
          dot.style.transform = 'translate3d(' + ev.clientX + 'px,' + ev.clientY + 'px,0)';
          const row = t && !t.closest(HOVER + ',input') && t.closest('#tb tr[data-id]');
          ring.classList.toggle('is-row', !!row);
          ring.textContent = row ? '查看' : '';
          ring.classList.toggle('is-text', !row && !!(t && t.closest(TEXT)));
          ring.classList.toggle('is-hover', !row && !!(t && t.closest(HOVER)) && !(t && t.closest(TEXT)));
        }
        setTilt(t && t.closest(TILT), ev);
        if (markEl) {
          markEl.style.setProperty('--px', ((.5 - ptr.nx) * 30).toFixed(1) + 'px');
          markEl.style.setProperty('--py', ((.5 - ptr.ny) * 16).toFixed(1) + 'px');
        }
      });
    }, {passive: true});
  }

  /* ---------- 点击迸发 ---------- */
  function burst() {
    document.addEventListener('pointerdown', e => {
      const b = e.target instanceof Element && e.target.closest('button:not(:disabled)');
      if (!b || e.button !== 0) return;
      const el = document.createElement('span');
      el.className = 'fx-burst'; el.setAttribute('aria-hidden', 'true');
      el.style.transform = 'translate3d(' + e.clientX + 'px,' + e.clientY + 'px,0)';
      let html = '';
      for (let i = 0; i < 8; i++) html += '<i style="--a:' + (i * 45 + Math.random() * 20) + 'deg;--d:' + (26 + Math.random() * 16).toFixed(0) + 'px"></i>';
      el.innerHTML = html;
      document.body.appendChild(el);
      setTimeout(() => el.remove(), 700);
    }, {passive: true});
  }

  /* ---------- 实时时钟（本机时间） ---------- */
  function clock() {
    const host = document.querySelector('.top-actions');
    if (!host) return;
    const el = document.createElement('span');
    el.className = 'fx-clock'; el.setAttribute('aria-hidden', 'true');
    host.prepend(el);
    const tz = (() => { try { const p = new Intl.DateTimeFormat('en', {timeZoneName: 'short'}).formatToParts(new Date()); return (p.find(x => x.type === 'timeZoneName') || {}).value || ''; } catch (_) { return ''; } })();
    const pad = n => String(n).padStart(2, '0');
    const tick = () => {
      if (!document.hidden) { const d = new Date(); el.innerHTML = 'LIVE <b>' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) + '</b> ' + tz; }
    };
    tick(); setInterval(tick, 1000);
  }

  /* ---------- 彩蛋：在空白处键入 t-a-o ---------- */
  function egg() {
    let buf = '';
    document.addEventListener('keydown', e => {
      const t = e.target;
      if (t && (t.closest && t.closest('input,textarea,select,[contenteditable="true"]'))) return;
      buf = (buf + (e.key || '').toLowerCase()).slice(-3);
      if (buf === 'tao') { buf = ''; confetti(); }
    });
  }
  function confetti() {
    if (reduced() || document.getElementById('fxConfetti')) return;
    const cv = document.createElement('canvas'); cv.id = 'fxConfetti';
    const dpr = Math.min(2, devicePixelRatio || 1);
    cv.width = innerWidth * dpr; cv.height = innerHeight * dpr;
    document.body.appendChild(cv);
    const c = cv.getContext('2d'); c.scale(dpr, dpr);
    const cols = ['#C6FF3D', '#F2F0EA', '#7C5CFF', '#0C0C0E', '#D8FF73'];
    const ps = Array.from({length: 160}, () => ({
      x: innerWidth / 2, y: innerHeight * .55, vx: (Math.random() - .5) * 18, vy: -Math.random() * 18 - 6,
      s: 4 + Math.random() * 6, r: Math.random() * 6, vr: (Math.random() - .5) * .4, c: cols[(Math.random() * cols.length) | 0]
    }));
    const t0 = performance.now();
    (function step(now) {
      const k = (now - t0) / 1800;
      c.clearRect(0, 0, innerWidth, innerHeight);
      ps.forEach(p => {
        p.vy += .45; p.vx *= .985; p.x += p.vx; p.y += p.vy; p.r += p.vr;
        c.save(); c.globalAlpha = Math.max(0, 1 - k); c.translate(p.x, p.y); c.rotate(p.r);
        c.fillStyle = p.c; c.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2); c.restore();
      });
      if (k < 1) requestAnimationFrame(step); else cv.remove();
    })(t0);
    try { toast('TAO 向你问好 ✦', 'ok'); } catch (_) {}
  }

  function init() {
    const run = fn => { try { fn(); } catch (_) {} };
    /* 着色器编译是同步的：放到首屏空闲后再做，不阻塞首次可交互 */
    run(() => { const go = () => run(gl); 'requestIdleCallback' in window ? requestIdleCallback(go, {timeout: 2500}) : setTimeout(go, 1200); });
    run(clock);
    run(egg);
    run(pointer);
    if (fine && !reduced()) { run(cursor); run(burst); }
  }
  return {init, mark, confetti};
})();

(() => {
  const MARKS = {accounts: 'Accounts', tasks: 'Tasks', leads: 'Leads', toolbox: 'Toolbox', logs: 'Logs', settings: 'Settings'};
  TAO_HOOK.bind('TAO_FX.onView', () => TAO_FX.onView, value => { TAO_FX.onView = value; });
  TAO_HOOK.after('TAO_FX.onView', function (value, args) { try { TAO_FX3.mark(MARKS[args[0]] || ''); } catch (_) {} return value; }, 'fx3:view-mark');
  TAO_FX3.init();
  try { TAO_FX3.mark(MARKS[currentView] || ''); } catch (_) {}
})();

/* =========================================================================
   创意层 · 第四轮（TAO_FX4）：粒子开场 · 账号星图 · 任务驾驶舱
   ========================================================================= */

/* ---------- 粒子开场（函数声明会被提升，第二轮的 intro() 会先调用它） ---------- */
function taoParticleIntro() {
  try {
    if (!document.createElement('canvas').getContext) return false;
    const dark = document.documentElement.dataset.theme === 'dark';
    const BG = dark ? [9, 9, 11] : [243, 241, 235];
    const INK = dark ? '#F2F0EA' : '#0C0C0E';
    const LIME = '#C6FF3D';
    const wrap = document.createElement('div');
    wrap.className = 'fx-pintro'; wrap.setAttribute('aria-hidden', 'true');
    wrap.innerHTML = '<canvas></canvas><small>Telegram Account Orchestrator</small><em>点击跳过</em>';
    document.body.appendChild(wrap);
    const cv = wrap.querySelector('canvas');
    const W = innerWidth, H = innerHeight, dpr = Math.min(2, devicePixelRatio || 1);
    cv.width = W * dpr; cv.height = H * dpr;
    const c = cv.getContext('2d'); c.scale(dpr, dpr);

    /* 把文字画到离屏画布，采样出粒子目标点 */
    const size = Math.min(W * .3, H * .42);
    const off = document.createElement('canvas'); off.width = W; off.height = H;
    const o = off.getContext('2d');
    o.fillStyle = '#000'; o.textAlign = 'center'; o.textBaseline = 'middle';
    const LS = Math.min(W * .62 / 1050, H * .46 / 470), LX = W / 2 - 625 * LS, LY = H / 2 - 655 * LS;
    o.save(); o.translate(LX, LY); o.scale(LS, LS); o.translate(0, 0);
    o.strokeStyle = '#000'; o.lineWidth = 82; o.lineCap = 'round'; o.lineJoin = 'round';
    o.stroke(new Path2D('M160 481H555C590 481 608 494 626 524L766 756C800 812 855 827 915 827A173 173 0 1 0 773 555')); o.stroke(new Path2D('M306 485V768C306 798 322 812 342 812C360 812 372 801 384 781L566 481'));
    const TRI = new Path2D('M583 640L534 716H632Z'); o.lineWidth = 22; o.fill(TRI); o.stroke(TRI); o.restore();
    const triHit = (x, y) => { const u = (x - LX) / LS, v = (y - LY) / LS; return v > 615 && v < 730 && Math.abs(u - 583) < (v - 625) * .72 + 14; };
    const dotX = -1e5, dotY = -1e5, dotR = 0;
    const data = o.getImageData(0, 0, W, H).data;
    let gap = Math.max(3, Math.round(size / 70));
    let pts = [];
    for (let y = 0; y < H; y += gap) for (let x = 0; x < W; x += gap) if (data[(y * W + x) * 4 + 3] > 128) pts.push([x, y]);
    while (pts.length > 2600) { gap++; pts = pts.filter((_, i) => i % 2 === 0); }
    const ps = pts.map(([x, y]) => {
      const a = Math.random() * Math.PI * 2, r = Math.max(W, H) * (.55 + Math.random() * .5);
      const isDot = triHit(x, y);
      return {tx: x, ty: y, sx: W / 2 + Math.cos(a) * r, sy: H / 2 + Math.sin(a) * r, x: 0, y: 0, ox: 0, oy: 0, vx: 0, vy: 0,
        d: Math.random() * 380, col: isDot || Math.random() < .12 ? LIME : INK, s: gap * (.55 + Math.random() * .35)};
    });

    let mx = -9999, my = -9999, phase = 0, tEnd = 0, done = false;
    const t0 = performance.now();
    const HOLD = 1900, OUT = 900;
    wrap.addEventListener('pointermove', e => { mx = e.clientX; my = e.clientY; });
    const skip = () => { if (phase < 2) { phase = 2; tEnd = performance.now(); explode(); } };
    wrap.addEventListener('click', skip);
    const onKey = () => skip();
    document.addEventListener('keydown', onKey, {once: true});
    function explode() {
      wrap.classList.add('out');
      ps.forEach(p => {
        const dx = p.x - W / 2, dy = p.y - H / 2, d = Math.hypot(dx, dy) || 1;
        const f = 6 + Math.random() * 14;
        p.vx = dx / d * f + (Math.random() - .5) * 4; p.vy = dy / d * f + (Math.random() - .5) * 4;
      });
    }
    const ease = t => t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
    function frame(now) {
      if (done) return;
      const t = now - t0;
      if (phase < 2 && t > HOLD) { phase = 2; tEnd = now; explode(); }
      let fade = 1;
      if (phase === 2) {
        const k = (now - tEnd) / OUT;
        fade = 1 - k;
        if (k >= 1) { done = true; wrap.remove(); document.removeEventListener('keydown', onKey); return; }
      }
      c.globalAlpha = 1;
      c.clearRect(0, 0, W, H);
      c.fillStyle = 'rgba(' + BG.join(',') + ',' + Math.max(0, fade * fade).toFixed(3) + ')';
      c.fillRect(0, 0, W, H);
      for (let i = 0; i < ps.length; i++) {
        const p = ps[i];
        if (phase < 2) {
          const k = ease(Math.max(0, (t - p.d) / 1100));
          let bx = p.sx + (p.tx - p.sx) * k, by = p.sy + (p.ty - p.sy) * k;
          if (k > .6) { bx += Math.sin(t / 380 + i) * .6; by += Math.cos(t / 420 + i * 1.3) * .6; }
          const dx = bx - mx, dy = by - my, dd = dx * dx + dy * dy;
          if (dd < 9000) { const f = (1 - dd / 9000) * 30; const d = Math.sqrt(dd) || 1; p.ox += (dx / d * f - p.ox) * .25; p.oy += (dy / d * f - p.oy) * .25; }
          else { p.ox *= .9; p.oy *= .9; }
          p.x = bx + p.ox; p.y = by + p.oy;
        } else {
          p.vx *= .96; p.vy *= .96; p.x += p.vx; p.y += p.vy;
        }
        c.globalAlpha = Math.max(0, fade);
        c.fillStyle = p.col;
        c.fillRect(p.x, p.y, p.s, p.s);
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    setTimeout(() => { if (!done) { done = true; wrap.remove(); } }, 6000);
    return true;
  } catch (_) { return false; }
}

const TAO_FX4 = (() => {
  const reduced = () => TAO_FX.reduced();
  const STATUS_COLOR = {active: '#C6FF3D', new: '#9BAAC6', unauthorized: '#F0736B', banned: '#B8433C', restricted: '#E5A93A', frozen: '#7AA2FF'};
  const colorOf = s => STATUS_COLOR[s] || '#E5A93A';
  const cnOf = s => { try { return statCn(s); } catch (_) { return s; } };
  const hash = s => { let h = 2166136261; s = String(s); for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0) / 4294967295; };
  const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')'; };

  /* =========================== 账号星图 2.0 ===========================
     - 每个分组是一个旋涡星系盘（黄金角螺旋排布，天然不重叠）+ 全局松弛去重
     - 实心 = 已登录，空心 = 未登录；高级会员带光环；封禁闪烁
     - 预渲染发光贴图 / 星云 / 银河带；连线最小生成树 + 流动光点；流星
     - 右侧面板：分组方式（标签 / 状态 / 国家）、搜索、星座列表（点击飞过去）
     ================================================================== */
  const S = {el: null, cv: null, c: null, tip: null, on: false, stars: [], groups: [], yaw: 0, pitch: -.18, zoom: 1, tzoom: 1,
    T: {x: 0, y: 0, z: 0}, TT: {x: 0, y: 0, z: 0}, focus: null, mode: 'tag', query: '', found: null,
    drag: null, hover: null, hoverG: null, hidden: new Set(), t0: 0, raf: 0, W: 0, H: 0, dpr: 1, sway: true, swayT: 0,
    sprites: {}, bg: null, meteors: [], nextMeteor: 0, mx: -9999, my: -9999};

  /* ---------- 预渲染贴图 ---------- */
  function sprite(color) {
    if (S.sprites[color]) return S.sprites[color];
    const n = 96, cv = document.createElement('canvas'); cv.width = cv.height = n;
    const c = cv.getContext('2d'), g = c.createRadialGradient(n / 2, n / 2, 0, n / 2, n / 2, n / 2);
    g.addColorStop(0, rgba(color, .9)); g.addColorStop(.12, rgba(color, .55)); g.addColorStop(.35, rgba(color, .14)); g.addColorStop(1, rgba(color, 0));
    c.fillStyle = g; c.fillRect(0, 0, n, n);
    return (S.sprites[color] = cv);
  }
  function spike(color) {
    const k = 'sp' + color; if (S.sprites[k]) return S.sprites[k];
    const n = 64, cv = document.createElement('canvas'); cv.width = cv.height = n; const c = cv.getContext('2d');
    const gh = c.createLinearGradient(0, 0, n, 0); gh.addColorStop(0, rgba(color, 0)); gh.addColorStop(.5, rgba(color, .95)); gh.addColorStop(1, rgba(color, 0));
    const gv = c.createLinearGradient(0, 0, 0, n); gv.addColorStop(0, rgba(color, 0)); gv.addColorStop(.5, rgba(color, .95)); gv.addColorStop(1, rgba(color, 0));
    c.fillStyle = gh; c.fillRect(0, n / 2 - .8, n, 1.6); c.fillStyle = gv; c.fillRect(n / 2 - .8, 0, 1.6, n);
    return (S.sprites[k] = cv);
  }
  function nebula(color, seed) {
    const n = 320, cv = document.createElement('canvas'); cv.width = cv.height = n;
    const c = cv.getContext('2d'); let s = seed * 9973 + 1;
    const rnd = () => { s = (s * 16807) % 2147483647; return s / 2147483647; };
    c.globalCompositeOperation = 'lighter';
    for (let i = 0; i < 26; i++) {
      const a = rnd() * Math.PI * 2, d = Math.pow(rnd(), 1.4) * n * .3, x = n / 2 + Math.cos(a) * d, y = n / 2 + Math.sin(a) * d * .75, r = n * (.08 + rnd() * .2);
      const col = rnd() < .62 ? color : (rnd() < .5 ? '#7C5CFF' : '#3FA9F5');
      const g = c.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, rgba(col, .07 + rnd() * .06)); g.addColorStop(1, rgba(col, 0));
      c.fillStyle = g; c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill();
    }
    /* 暗尘带 */
    c.globalCompositeOperation = 'destination-out';
    for (let i = 0; i < 7; i++) {
      const x = n / 2 + (rnd() - .5) * n * .5, y = n / 2 + (rnd() - .5) * n * .3, r = n * (.04 + rnd() * .07);
      const g = c.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, 'rgba(0,0,0,.35)'); g.addColorStop(1, 'rgba(0,0,0,0)');
      c.fillStyle = g; c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill();
    }
    return cv;
  }
  function background() {
    const W = Math.ceil(S.W * 1.3), H = Math.ceil(S.H * 1.3), cv = document.createElement('canvas');
    cv.width = W; cv.height = H; const c = cv.getContext('2d');
    /* 银河带：斜向的柔光 + 密集小星 */
    c.save(); c.translate(W / 2, H / 2); c.rotate(-.42);
    for (let i = 0; i < 70; i++) {
      const x = (Math.random() - .5) * W * 1.4, y = (Math.random() - .5) * H * .16 * (1 + Math.random()), r = 60 + Math.random() * 180;
      const col = Math.random() < .7 ? '#8E8AC8' : '#C6FF3D';
      const g = c.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, rgba(col, .025 + Math.random() * .03)); g.addColorStop(1, rgba(col, 0));
      c.fillStyle = g; c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill();
    }
    for (let i = 0; i < 2200; i++) {
      const x = (Math.random() - .5) * W * 1.4, y = (Math.random() + Math.random() + Math.random() - 1.5) * H * .14;
      c.globalAlpha = Math.random() * .55 + .1; c.fillStyle = Math.random() < .15 ? '#FFE9C7' : '#fff';
      const s = Math.random() < .97 ? .7 : 1.4; c.fillRect(x, y, s, s);
    }
    c.restore();
    /* 全天散星 */
    for (let i = 0; i < 900; i++) {
      c.globalAlpha = Math.random() * .5 + .08; c.fillStyle = '#fff';
      const s = Math.random() < .96 ? .8 : 1.6; c.fillRect(Math.random() * W, Math.random() * H, s, s);
    }
    c.globalAlpha = 1;
    S.bg = cv;
    /* 视差闪烁星（前景） */
    S.twk = Array.from({length: 120}, () => ({x: Math.random(), y: Math.random(), s: Math.random() * 1.6 + .6, ph: Math.random() * 6, d: Math.random() * .6 + .4}));
  }

  /* ---------- 结构 ---------- */
  function build() {
    const el = document.createElement('div');
    el.className = 'fx-stars'; el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true'); el.setAttribute('aria-label', '账号星图');
    el.innerHTML = '<canvas></canvas>' +
      '<header><small>Constellation · 账号星图</small><h2>Your sky.</h2><p></p></header>' +
      '<button type="button" class="x" aria-label="关闭星图"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>' +
      '<aside class="sky-panel">' +
        '<div class="sky-mode" role="tablist" aria-label="分组方式"><button type="button" data-m="tag" class="on">按标签</button><button type="button" data-m="status">按状态</button><button type="button" data-m="country">按国家</button></div>' +
        '<label class="sky-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg><input type="search" placeholder="找一颗星：别名 / 手机号 / @用户名" aria-label="搜索账号" /></label>' +
        '<div class="sky-list" aria-label="星座"></div>' +
        '<div class="sky-key"><span><i class="f"></i>已登录</span><span><i class="h"></i>未登录</span><span><i class="p"></i>Premium</span></div>' +
      '</aside>' +
      '<div class="legend"></div><div class="hint">拖动旋转 · 滚轮缩放 · 点星座名飞过去 · 点星星打开详情 · Esc 返回</div><div class="tip"></div>';
    document.body.appendChild(el);
    S.el = el; S.cv = el.querySelector('canvas'); S.c = S.cv.getContext('2d'); S.tip = el.querySelector('.tip'); S.hd = el.querySelector('header');
    el.querySelector('.x').addEventListener('click', close);
    el.querySelector('.legend').addEventListener('click', e => {
      const b = e.target.closest('button[data-s]'); if (!b) return;
      const k = b.dataset.s; S.hidden.has(k) ? S.hidden.delete(k) : S.hidden.add(k); b.classList.toggle('off', S.hidden.has(k));
    });
    el.querySelector('.sky-mode').addEventListener('click', e => {
      const b = e.target.closest('button[data-m]'); if (!b || b.dataset.m === S.mode) return;
      S.mode = b.dataset.m; el.querySelectorAll('.sky-mode button').forEach(x => x.classList.toggle('on', x === b));
      unfocus(); layout(); S.t0 = performance.now() - 200;
    });
    const si = el.querySelector('.sky-search input');
    si.addEventListener('input', () => { S.query = si.value.trim().toLowerCase(); findStar(); });
    si.addEventListener('keydown', e => { if (e.key === 'Enter' && S.found) { e.preventDefault(); const id = S.found.id; close(); setTimeout(() => { try { openDrawer(id); } catch (_) {} }, 260); } });
    el.querySelector('.sky-list').addEventListener('click', e => { const b = e.target.closest('button[data-g]'); if (b) focusGroup(S.groups[+b.dataset.g]); });
    el.addEventListener('pointerdown', e => {
      if (e.target.closest('button,input,aside')) return;
      S.drag = {x: e.clientX, y: e.clientY, yaw: S.yaw, pitch: S.pitch, moved: 0};
      S.sway = false; el.classList.add('dragging'); el.setPointerCapture && el.setPointerCapture(e.pointerId);
    });
    el.addEventListener('pointermove', e => {
      S.mx = e.clientX; S.my = e.clientY;
      if (!S.drag) return;
      const dx = e.clientX - S.drag.x, dy = e.clientY - S.drag.y;
      S.drag.moved = Math.max(S.drag.moved, Math.abs(dx) + Math.abs(dy));
      S.yaw = S.drag.yaw + dx * .005;
      S.pitch = Math.max(-1.35, Math.min(1.35, S.drag.pitch + dy * .005));
    });
    const up = () => {
      if (!S.drag) return;
      const click = S.drag.moved < 6;
      S.drag = null; el.classList.remove('dragging');
      if (!click) return;
      if (S.hover) { const id = S.hover.id; close(); setTimeout(() => { try { openDrawer(id); } catch (_) {} }, 260); }
      else if (S.labelHit) focusGroup(S.labelHit);
    };
    el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up);
    el.addEventListener('pointerleave', () => { S.mx = S.my = -9999; });
    el.addEventListener('wheel', e => { if (e.target.closest('aside')) return; e.preventDefault(); S.tzoom = Math.max(.4, Math.min(4, S.tzoom * Math.exp(-e.deltaY * .0012))); }, {passive: false});
    addEventListener('resize', () => { if (S.on) { size(); background(); } }, {passive: true});
  }

  function size() {
    S.W = innerWidth; S.H = innerHeight; S.dpr = Math.min(2, devicePixelRatio || 1);
    S.cv.width = S.W * S.dpr; S.cv.height = S.H * S.dpr;
    S.c.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  }

  /* ---------- 布局 ---------- */
  const STATUS_ORDER = ['active', 'new', 'unauthorized', 'restricted', 'spam_block', 'flood_wait', 'frozen', 'banned'];
  function groupKey(a) {
    if (S.mode === 'status') return cnOf(a.status || 'unknown');
    if (S.mode === 'country') { try { return (String(countryOf(a.phone) || '').replace(/[\u{1F1E6}-\u{1F1FF}]/gu, '').trim()) || '未知地区'; } catch (_) { return '未知地区'; } }
    return Array.isArray(a.tags) && a.tags.length ? String(a.tags[0]) : '未分组';
  }
  function layout() {
    const list = Array.isArray(accounts) ? accounts : [];
    const map = new Map();
    list.forEach(a => { const k = groupKey(a); if (!map.has(k)) map.set(k, []); map.get(k).push(a); });
    const names = [...map.keys()].sort((a, b) => map.get(b).length - map.get(a).length);
    /* 星系半径：按成员数，保证螺旋相邻星间距约 34 */
    S.groups = names.map((name, i) => {
      const n = map.get(name).length, r = 34 + Math.sqrt(n) * 21;
      const h = hash(name + S.mode);
      return {name, n, r, x: 0, y: 0, z: 0, tx: (h - .5) * .9, tz: (hash(name + 'z') - .5) * .7, spin: h * Math.PI * 2, members: [], links: [], neb: null, seed: Math.floor(h * 1e6)};
    });
    /* 星系中心：斐波那契球初始化 + 斥力松弛，互不重叠 */
    const G = S.groups, n = G.length;
    if (n > 1) {
      /* 中心排在 XY 平面（面向相机），只给少量深度，避免投影后叠在一起 */
      const R0 = 40 + Math.sqrt(G.reduce((s, g) => s + g.r * g.r, 0)) * .9;
      G.forEach((g, i) => {
        const u = Math.sqrt((i + .5) / n), th = i * 2.399963;
        g.x = Math.cos(th) * u * R0 * 1.5; g.y = Math.sin(th) * u * R0 * .9; g.z = (hash(g.name + 'k') - .5) * 60;
      });
      for (let it = 0; it < 220; it++) {
        for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
          const a = G[i], b = G[j]; let dx = b.x - a.x, dy = (b.y - a.y) * 1.25;
          const d = Math.sqrt(dx * dx + dy * dy) || .01, need = (a.r + b.r) * 1.3 + 70;
          if (d < need) { const k = (need - d) / d * .5; dx *= k; dy *= k / 1.25; a.x -= dx; a.y -= dy; b.x += dx; b.y += dy; }
        }
        G.forEach(g => { g.x *= .997; g.y *= .993; });
      }
      const cx0 = G.reduce((s, g) => s + g.x, 0) / n, cy0 = G.reduce((s, g) => s + g.y, 0) / n;
      G.forEach(g => { g.x -= cx0; g.y -= cy0; });
    }
    /* 星：盘面上的黄金角螺旋（按状态排序，颜色成片），再按星系倾角旋转 */
    S.stars = [];
    names.forEach((name, gi) => {
      const g = G[gi];
      const arr = map.get(name).slice().sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) || a.id - b.id);
      const cx = Math.cos(g.tx), sx = Math.sin(g.tx), cz = Math.cos(g.tz), sz = Math.sin(g.tz);
      arr.forEach((a, k) => {
        const u = Math.sqrt((k + .6) / arr.length), ang = k * 2.399963 + g.spin + u * 1.6;
        let lx = Math.cos(ang) * u * g.r, ly = Math.sin(ang) * u * g.r, lz = (hash(a.id + 'd') - .5) * 14 * (1 - u * .6);
        /* 盘面朝向相机（XY 平面），绕 X、Z 轴小角度倾斜 */
        let y1 = ly * cx - lz * sx, z1 = ly * sx + lz * cx;
        let x2 = lx * cz - y1 * sz, y2 = lx * sz + y1 * cz;
        const st = {id: a.id, a, g: gi, x: g.x + x2, y: g.y + y2, z: g.z + z1, color: colorOf(a.status), status: a.status || 'unknown',
          auth: !!a.authorized, prem: !!a.premium, size: 2.6 + (a.premium ? .9 : 0) + (a.authorized ? .5 : 0),
          phase: hash(a.id + 'p') * 10, delay: u * 700 + gi * 90};
        S.stars.push(st); g.members.push(st);
      });
      /* 组内连线：最小生成树（成员不多时） */
      const m = g.members;
      if (m.length > 1 && m.length <= 360) {
        const inT = new Array(m.length).fill(false), best = new Array(m.length).fill(Infinity), from = new Array(m.length).fill(-1);
        best[0] = 0;
        for (let step = 0; step < m.length; step++) {
          let u = -1; for (let i = 0; i < m.length; i++) if (!inT[i] && (u < 0 || best[i] < best[u])) u = i;
          inT[u] = true; if (from[u] >= 0) g.links.push([m[u], m[from[u]], hash(m[u].id + 'l')]);
          for (let i = 0; i < m.length; i++) if (!inT[i]) { const d = (m[i].x - m[u].x) ** 2 + (m[i].y - m[u].y) ** 2 + (m[i].z - m[u].z) ** 2; if (d < best[i]) { best[i] = d; from[i] = u; } }
        }
      }
      const cnt = {}; m.forEach(s => cnt[s.color] = (cnt[s.color] || 0) + 1);
      g.col = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a])[0] || '#7C5CFF';
      g.neb = nebula(g.col, g.seed);
      g.bar = STATUS_ORDER.concat(['unknown']).map(s => [s, m.filter(x => x.status === s || (s === 'unknown' && !STATUS_ORDER.includes(x.status))).length]).filter(x => x[1]);
    });
    /* 全局松弛：任意两星最小间距 22（空间哈希） */
    const MIN = 22, cell = MIN, key = (x, y, z) => Math.floor(x / cell) + ',' + Math.floor(y / cell) + ',' + Math.floor(z / cell);
    for (let it = 0; it < 4; it++) {
      const H = new Map();
      S.stars.forEach(s => { const k = key(s.x, s.y, s.z); (H.get(k) || H.set(k, []).get(k)).push(s); });
      let moved = 0;
      S.stars.forEach(s => {
        const bx = Math.floor(s.x / cell), by = Math.floor(s.y / cell), bz = Math.floor(s.z / cell);
        for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) for (let k = -1; k <= 1; k++) {
          const arr = H.get((bx + i) + ',' + (by + j) + ',' + (bz + k)); if (!arr) continue;
          for (const o of arr) {
            if (o === s) continue;
            let dx = s.x - o.x, dy = s.y - o.y, dz = s.z - o.z; const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || .01;
            if (d < MIN) { const f = (MIN - d) / d * .5; s.x += dx * f; s.y += dy * f; s.z += dz * f; o.x -= dx * f; o.y -= dy * f; o.z -= dz * f; moved++; }
          }
        }
      });
      if (!moved) break;
    }
    /* 文案、图例、面板 */
    const counts = {};
    S.stars.forEach(s => { counts[s.status] = (counts[s.status] || 0) + 1; });
    const modeCn = {tag: '按第一个标签', status: '按状态', country: '按国家'}[S.mode];
    S.el.querySelector('header p').textContent = S.stars.length + ' 颗星 · ' + S.groups.length + ' 个星座（' + modeCn + '分组）';
    S.el.querySelector('.legend').innerHTML = Object.keys(counts).sort((a, b) => STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b)).map(k =>
      '<button type="button" data-s="' + esc(k) + '" class="' + (S.hidden.has(k) ? 'off' : '') + '"><i style="background:' + colorOf(k) + ';color:' + colorOf(k) + '"></i>' + esc(cnOf(k)) + ' <b>' + counts[k] + '</b></button>').join('');
    S.el.querySelector('.sky-list').innerHTML = S.groups.map((g, i) =>
      '<button type="button" data-g="' + i + '"><span class="nm"><i style="background:' + g.col + ';color:' + g.col + '"></i>' + esc(g.name) + '</span><b>' + g.n + '</b>' +
      '<span class="bar">' + g.bar.map(([s, c]) => '<em style="flex:' + c + ';background:' + colorOf(s) + '"></em>').join('') + '</span></button>').join('');
    const old = S.el.querySelector('.empty-sky'); if (old) old.remove();
    if (!S.stars.length) { const e = document.createElement('div'); e.className = 'empty-sky'; e.textContent = '这片天空还没有星星。先添加几个账号吧。'; S.el.appendChild(e); }
    /* 自动缩放：让整片星空装进屏幕 */
    let ex = 1, ey = 1; S.stars.forEach(s => { ex = Math.max(ex, Math.abs(s.x)); ey = Math.max(ey, Math.abs(s.y)); });
    const vw = (S.W > 900 ? S.W - 380 : S.W) / 2, vh = (S.H - 220) / 2, base = Math.min(S.W, S.H) / 620;
    S.fit = Math.max(.25, Math.min(1.6, Math.min(vw / (ex + (S.W > 900 ? 40 : 90)), vh / (ey + 50)) / base));
    findStar();
  }

  function findStar() {
    const q = S.query; S.found = null;
    S.el && S.el.classList.toggle('searching', !!q);
    if (!q) { if (!S.focus) S.TT = {x: 0, y: 0, z: 0}; return; }
    const hit = S.stars.find(s => [s.a.label, s.a.phone, s.a.username && '@' + s.a.username, s.a.username].some(v => v && String(v).toLowerCase().includes(q)));
    if (hit) { S.found = hit; S.TT = {x: hit.x, y: hit.y, z: hit.z}; S.tzoom = Math.max(S.tzoom, 2.2); S.sway = false; }
  }
  function focusGroup(g) {
    if (!g) return;
    S.focus = g; S.TT = {x: g.x, y: g.y, z: g.z};
    S.tzoom = Math.max(1.2, Math.min(3.4, 150 / g.r / (S.fit || 1)));
    S.sway = false;
    S.el.querySelectorAll('.sky-list button').forEach((b, i) => b.classList.toggle('on', S.groups[i] === g));
  }
  function unfocus() {
    S.focus = null; S.TT = {x: 0, y: 0, z: 0}; S.tzoom = 1; S.sway = true;
    S.el && S.el.querySelectorAll('.sky-list button.on').forEach(b => b.classList.remove('on'));
  }

  function project(x, y, z) {
    x -= S.T.x; y -= S.T.y; z -= S.T.z;
    const cy = Math.cos(S.yaw), sy = Math.sin(S.yaw), cp = Math.cos(S.pitch), sp = Math.sin(S.pitch);
    let X = x * cy - z * sy, Z = x * sy + z * cy;
    let Y = y * cp - Z * sp; Z = y * sp + Z * cp;
    const f = 1000 / (1000 + Z) * S.zoom * (S.fit || 1) * Math.min(S.W, S.H) / 620;
    const offX = S.W > 900 ? -130 : 0;
    return {x: S.W / 2 + offX + X * f, y: S.H / 2 + Y * f, s: f, z: Z};
  }

  /* ---------- 绘制 ---------- */
  function draw(now) {
    if (!S.on) return;
    S.raf = requestAnimationFrame(draw);
    const c = S.c, t = now - S.t0, reducedM = reduced();
    if (!S.drag && S.sway && !reducedM) { S.swayT += 1 / 60; S.yaw += (Math.sin(S.swayT * .18) * .38 - S.yaw) * .02; S.pitch += (-.18 + Math.sin(S.swayT * .11) * .08 - S.pitch) * .02; }
    S.zoom += (S.tzoom - S.zoom) * .08;
    if (S.hd) S.hd.style.opacity = S.zoom > 1.35 ? '.18' : '';
    for (const k of ['x', 'y', 'z']) S.T[k] += (S.TT[k] - S.T[k]) * .08;
    c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1;
    c.clearRect(0, 0, S.W, S.H);
    /* 背景：银河带视差 */
    if (S.bg) { c.globalAlpha = Math.min(1, t / 900); c.drawImage(S.bg, -S.W * .15 - S.yaw * 60, -S.H * .15 + S.pitch * 50); }
    c.globalCompositeOperation = 'lighter';
    for (const w of S.twk || []) {
      c.globalAlpha = (.25 + .45 * Math.sin(t / 700 + w.ph) ** 2) * w.d;
      const x = ((w.x * S.W - S.yaw * 140 * w.d) % S.W + S.W) % S.W, y = ((w.y * S.H + S.pitch * 100 * w.d) % S.H + S.H) % S.H;
      c.fillStyle = '#fff'; c.fillRect(x, y, w.s, w.s);
    }
    /* 流星 */
    if (!reducedM && now > S.nextMeteor) {
      S.nextMeteor = now + 3500 + Math.random() * 5000;
      S.meteors.push({x: Math.random() * S.W * .8 + S.W * .1, y: Math.random() * S.H * .4, vx: -(5 + Math.random() * 4), vy: 2.4 + Math.random() * 2, life: 0});
    }
    S.meteors = S.meteors.filter(m => (m.life += 1) < 60);
    for (const m of S.meteors) {
      m.x += m.vx; m.y += m.vy; const a = Math.sin(m.life / 60 * Math.PI);
      const g = c.createLinearGradient(m.x, m.y, m.x - m.vx * 14, m.y - m.vy * 14);
      g.addColorStop(0, 'rgba(255,255,255,' + (.8 * a) + ')'); g.addColorStop(1, 'rgba(255,255,255,0)');
      c.globalAlpha = 1; c.strokeStyle = g; c.lineWidth = 1.4; c.beginPath(); c.moveTo(m.x, m.y); c.lineTo(m.x - m.vx * 14, m.y - m.vy * 14); c.stroke();
    }

    const ent = s => Math.max(0, Math.min(1, (t - s.delay) / 1100)), eo = k => 1 - Math.pow(1 - k, 3);
    const hv = S.hover, hg = hv ? S.groups[hv.g] : (S.labelHover || S.focus || null);
    const dimOf = gi => (hg && S.groups[gi] !== hg ? .28 : 1) * (S.found ? .35 : 1);
    for (const s of S.stars) {
      const k = eo(ent(s)), g = S.groups[s.g];
      s.p = project(g.x + (s.x - g.x) * k, g.y + (s.y - g.y) * k, g.z + (s.z - g.z) * k);
      s.k = k; s.vis = !S.hidden.has(s.status) && s.p.z > -950;
    }
    /* 星云 */
    for (const g of S.groups) {
      const p = project(g.x, g.y, g.z), rr = g.r * 2.7 * p.s;
      if (p.z < -950) continue; g.p = p;
      c.globalAlpha = .62 * Math.min(1, t / 1400) * (hg && hg !== g ? .4 : 1);
      c.save(); c.translate(p.x, p.y); c.rotate(g.spin + t / 60000); c.drawImage(g.neb, -rr, -rr * .8, rr * 2, rr * 1.6); c.restore();
      /* 核心亮斑 */
      const sp = sprite(g.col), cr = (14 + Math.sqrt(g.n) * 3) * p.s;
      c.globalAlpha = .2 * Math.min(1, t / 1400) * (hg && hg !== g ? .4 : 1); c.drawImage(sp, p.x - cr, p.y - cr, cr * 2, cr * 2);
    }
    /* 连线 + 流动光点 */
    c.lineWidth = 1;
    for (const g of S.groups) {
      const dim = (hg && hg !== g ? .25 : 1) * (S.found ? .4 : 1);
      for (const [a, b, ph] of g.links) {
        if (!a.vis || !b.vis) continue;
        const al = Math.min(a.k, b.k) * .28 * dim; if (al < .01) continue;
        c.globalAlpha = al; c.strokeStyle = '#B9C7FF';
        c.beginPath(); c.moveTo(a.p.x, a.p.y); c.lineTo(b.p.x, b.p.y); c.stroke();
        if (!reducedM && S.stars.length < 900 && a.auth && b.auth) {
          const u = ((t / 2600 + ph) % 1); const x = a.p.x + (b.p.x - a.p.x) * u, y = a.p.y + (b.p.y - a.p.y) * u;
          c.globalAlpha = Math.sin(u * Math.PI) * .9 * dim; c.fillStyle = '#E8FFB0'; c.fillRect(x - .9, y - .9, 1.8, 1.8);
        }
      }
    }
    /* 星（远到近） */
    const order = S.stars.filter(s => s.vis).sort((a, b) => b.p.z - a.p.z);
    let hit = null, hd = 20 * 20;
    for (const s of order) {
      const p = s.p, dim = s === S.found ? 1 : dimOf(s.g);
      const flick = s.status === 'banned' ? .5 + .3 * Math.sin(t / 220 + s.phase) : 1;
      const pulse = s.auth && !reducedM ? 1 + .18 * Math.sin(t / 700 + s.phase) : 1;
      const r = Math.max(1.2, s.size * Math.min(2.2, p.s) * s.k);
      const gl = r * 7 * pulse;
      c.globalAlpha = .75 * flick * dim; c.drawImage(sprite(s.color), p.x - gl, p.y - gl, gl * 2, gl * 2);
      if (s.auth) {
        /* 实心核心 + 衍射十字 */
        c.globalAlpha = flick * dim; c.fillStyle = '#fff'; c.beginPath(); c.arc(p.x, p.y, r * .7, 0, Math.PI * 2); c.fill();
        if (s.status === 'active' && r > 1.8) {
          const L = r * (3.6 + .8 * Math.sin(t / 600 + s.phase));
          c.globalAlpha = .7 * dim; c.drawImage(spike(s.color), p.x - L, p.y - L, L * 2, L * 2);
        }
      } else {
        /* 空心：未登录 */
        c.globalAlpha = .95 * flick * dim; c.strokeStyle = s.color; c.lineWidth = 1.2;
        c.beginPath(); c.arc(p.x, p.y, r * .95, 0, Math.PI * 2); c.stroke();
      }
      if (s.prem) {
        c.globalAlpha = .55 * dim; c.strokeStyle = '#FFD86B'; c.lineWidth = .8;
        c.beginPath(); c.ellipse(p.x, p.y, r * 2.6, r * 1.1, -.5 + t / 4000, 0, Math.PI * 2); c.stroke();
      }
      const dx = p.x - S.mx, dy = p.y - S.my, dd = dx * dx + dy * dy;
      if (dd < hd) { hd = dd; hit = s; }
    }
    c.globalCompositeOperation = 'source-over';
    /* 星座名：屏幕空间避让 */
    const placed = []; S.labelHit = null; let lh = null;
    c.textAlign = 'center';
    const gs = S.groups.filter(g => g.p).sort((a, b) => a.p.z - b.p.z);
    for (const g of gs) {
      const top = project(g.x, g.y - g.r * 1.05, g.z);
      const fs = Math.max(10, Math.min(15, 11 * g.p.s));
      c.font = '500 ' + fs + 'px "JetBrains Mono", ui-monospace, monospace';
      const txt = g.name.toUpperCase(), sub = g.n + ' 颗';
      const w = Math.max(c.measureText(txt).width, 40) + 24, h = fs * 2.6, x = top.x, y = top.y - 8;
      const rect = [x - w / 2, y - h, w, h];
      if (placed.some(q => rect[0] < q[0] + q[2] && rect[0] + rect[2] > q[0] && rect[1] < q[1] + q[3] && rect[1] + rect[3] > q[1])) continue;
      placed.push(rect);
      const over = S.mx > rect[0] && S.mx < rect[0] + w && S.my > rect[1] && S.my < rect[1] + h;
      if (over) lh = g;
      const al = Math.min(1, t / 1300) * (hg && hg !== g ? .35 : 1) * (over || g === S.focus ? 1 : .8);
      c.globalAlpha = al;
      if (over || g === S.focus) { c.fillStyle = 'rgba(198,255,61,.12)'; c.strokeStyle = 'rgba(198,255,61,.6)'; c.lineWidth = 1; roundRect(c, rect[0], rect[1], w, h, h / 2); c.fill(); c.stroke(); }
      c.fillStyle = over || g === S.focus ? '#C6FF3D' : '#F2F0EA'; c.fillText(txt, x, y - fs * 1.25);
      c.font = '400 ' + (fs - 1.5) + 'px "JetBrains Mono", ui-monospace, monospace'; c.fillStyle = 'rgba(242,240,234,.55)'; c.fillText(sub, x, y - fs * .2);
      /* 引线 */
      c.globalAlpha = al * .35; c.strokeStyle = '#F2F0EA'; c.lineWidth = 1; c.setLineDash([2, 3]);
      c.beginPath(); c.moveTo(x, y + 2); c.lineTo(x, Math.min(y + 16, g.p.y)); c.stroke(); c.setLineDash([]);
    }
    S.labelHover = lh; if (!hit && lh) S.labelHit = lh;
    /* 准星：悬停 / 搜索命中 */
    for (const s of [S.hover, S.found]) {
      if (!s || !s.p) continue;
      const p = s.p, R = 13 + 1.5 * Math.sin(t / 220), L = 5;
      c.globalAlpha = .95; c.strokeStyle = '#C6FF3D'; c.lineWidth = 1.2; c.beginPath();
      [[-1, -1], [1, -1], [1, 1], [-1, 1]].forEach(([sx, sy]) => { c.moveTo(p.x + sx * R, p.y + sy * (R - L)); c.lineTo(p.x + sx * R, p.y + sy * R); c.lineTo(p.x + sx * (R - L), p.y + sy * R); });
      c.stroke();
      if (s === S.found) { c.font = '500 12px ' + 'system-ui, sans-serif'; c.textAlign = 'left'; c.fillStyle = '#C6FF3D'; c.fillText(s.a.label || s.a.phone || '#' + s.id, p.x + R + 8, p.y + 4); }
    }
    S.el.classList.toggle('hovering', !!(hit || lh));
    if (!S.drag) setHover(hit);
  }
  function roundRect(c, x, y, w, h, r) { c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath(); }

  function setHover(s) {
    if (s === S.hover) { if (s) placeTip(s); return; }
    S.hover = s;
    if (!s) { S.tip.classList.remove('on'); return; }
    const a = s.a;
    const rows = [
      ['状态', cnOf(s.status)], ['登录', a.authorized ? '已登录' : '未登录'],
      ['二验', a.has_2fa === 1 ? ((a.has_twofa_saved || a.twofa) ? '有 · 已保存' : '有 · 未保存') : (a.has_2fa === 0 ? '无' : '未检查')], ['代理', a.proxy ? '已配置' : '直连']
    ];
    S.tip.innerHTML = '<b><i style="background:' + s.color + '"></i>' + esc(a.label || a.username || ('#' + a.id)) + (a.premium ? ' <sup>★</sup>' : '') + '</b>' +
      '<span>' + esc(a.phone || '') + (a.username ? ' · @' + esc(a.username) : '') + '</span>' +
      '<dl>' + rows.map(([k, v]) => '<dt>' + k + '</dt><dd>' + esc(v) + '</dd>').join('') + '</dl>' +
      (Array.isArray(a.tags) && a.tags.length ? '<em>' + a.tags.map(esc).join(' · ') + '</em>' : '') + '<small>点击打开详情</small>';
    S.tip.classList.add('on'); placeTip(s);
  }
  function placeTip(s) {
    const tw = S.tip.offsetWidth || 240, th = S.tip.offsetHeight || 140;
    let x = s.p.x + 24, y = s.p.y - th / 2;
    if (x + tw > S.W - 16) x = s.p.x - tw - 24;
    y = Math.max(16, Math.min(S.H - th - 16, y));
    S.tip.style.transform = 'translate3d(' + x.toFixed(0) + 'px,' + y.toFixed(0) + 'px,0)';
  }

  function open() {
    if (!S.el) build();
    size(); background(); layout();
    S.on = true; S.t0 = performance.now(); S.zoom = .55; S.tzoom = 1; S.hover = null; S.sway = true;
    S.T = {x: 0, y: 0, z: 0}; S.TT = {x: 0, y: 0, z: 0}; S.focus = null; S.nextMeteor = performance.now() + 1500;
    S.el.classList.add('on');
    cancelAnimationFrame(S.raf); S.raf = requestAnimationFrame(draw);
    setTimeout(() => { try { S.el.querySelector('.x').focus({preventScroll: true}); } catch (_) {} }, 50);
  }
  function close() {
    if (!S.on) return;
    S.on = false; S.el.classList.remove('on'); cancelAnimationFrame(S.raf);
    S.tip.classList.remove('on');
  }
  TAO_KEYS.esc('star-map', 40, () => !!S.on, () => {
    if (S.focus || S.query) {
      const si2 = S.el && S.el.querySelector('.sky-search input');
      if (S.query && si2) { si2.value = ''; S.query = ''; findStar(); }
      unfocus();
    } else close();
  });

  function wireStarEntrances() {
    /* 工具栏刷新按钮旁加一个星图按钮 */
    const refreshBtn = document.querySelector('.toolbar button[data-onclick="refresh()"]');
    if (refreshBtn && !document.querySelector('.fx-star-btn')) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'round-btn ghost fx-star-btn'; b.title = '账号星图（G）'; b.setAttribute('aria-label', '账号星图');
      b.innerHTML = '<svg class="i"><use href="#i-spark"/></svg>';
      b.addEventListener('click', open);
      refreshBtn.insertAdjacentElement('afterend', b);
    }
    /* 命令面板登记 */
    try {
      if (Array.isArray(ACTIONS) && !ACTIONS.some(x => x.id === 'fx-stars'))
        ACTIONS.push({id: 'fx-stars', g: '跳转', label: '账号星图', icon: 'spark', kw: 'constellation star map 星图 星空 3d 可视化', run: () => open()});
    } catch (_) {}
    /* 快捷键 G */
    document.addEventListener('keydown', e => {
      if (e.key !== 'g' && e.key !== 'G') return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target;
      if (t && t.closest && t.closest('input,textarea,select,[contenteditable="true"]')) return;
      if (document.querySelector('.backdrop.on,.pal-backdrop.on')) return;
      S.on ? close() : open();
    });
  }
  /* 左栏总数卡里的「星图」入口（renderStats 每次重绘后补上） */
  function addRailLink() {
    const card = document.querySelector('#accountOverview .stat-card.ink');
    if (!card || card.querySelector('.fx-star-link')) return;
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'fx-star-link'; b.innerHTML = '星图 ↗'; b.title = '在星图中查看全部账号';
    b.addEventListener('click', e => { e.stopPropagation(); open(); });
    card.appendChild(b);
  }

  /* =========================== 任务驾驶舱 =========================== */
  const K = {el: null, cur: null, segs: [], lit: 0, hideT: 0};
  const CX = 54, CY = 54, R = 44;
  function ensureCockpit() {
    if (K.el) return;
    const el = document.createElement('div');
    el.className = 'fx-cockpit'; el.setAttribute('aria-hidden', 'true');
    el.innerHTML = '<div class="dial"><svg viewBox="0 0 108 108"></svg><div class="core"><div><b>0%</b><small>0 / 0</small></div></div></div>' +
      '<div class="meta"><span class="k">Mission control</span><span class="t"></span><span class="m"></span></div>';
    document.body.appendChild(el); K.el = el;
  }
  function arc(a0, a1) {
    const p = a => [CX + R * Math.cos(a), CY + R * Math.sin(a)];
    const [x0, y0] = p(a0), [x1, y1] = p(a1);
    return 'M' + x0.toFixed(2) + ' ' + y0.toFixed(2) + ' A' + R + ' ' + R + ' 0 ' + (a1 - a0 > Math.PI ? 1 : 0) + ' 1 ' + x1.toFixed(2) + ' ' + y1.toFixed(2);
  }
  function buildSegs(n) {
    const svg = K.el.querySelector('svg');
    const gapA = n > 1 ? Math.min(.08, Math.PI / n * .35) : 0;
    let h = '';
    for (let i = 0; i < n; i++) {
      const a0 = -Math.PI / 2 + i / n * Math.PI * 2 + gapA / 2, a1 = -Math.PI / 2 + (i + 1) / n * Math.PI * 2 - gapA / 2;
      h += '<path class="fx-seg" d="' + arc(a0, n === 1 ? a1 - .0001 : a1) + '"/>';
    }
    svg.innerHTML = h; K.segs = [...svg.querySelectorAll('.fx-seg')]; K.lit = 0;
  }
  function dialCenter() { const r = K.el.querySelector('.dial').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }
  function cometSource() {
    const rows = [...document.querySelectorAll('#tb tr[data-id]')].filter(tr => { const r = tr.getBoundingClientRect(); return r.height && r.bottom > 0 && r.top < innerHeight && r.right > 0; });
    if (rows.length) {
      const tr = rows[(Math.random() * rows.length) | 0], r = tr.getBoundingClientRect();
      tr.classList.add('fx-ck-src'); setTimeout(() => tr.classList.remove('fx-ck-src'), 700);
      return [r.left + 40 + Math.random() * Math.min(260, r.width - 80), r.top + r.height / 2];
    }
    return [innerWidth * (.35 + Math.random() * .5), innerHeight * (.2 + Math.random() * .4)];
  }
  function comet(onArrive, fail) {
    if (reduced() || !K.el.animate) { onArrive(); return; }
    const [sx, sy] = cometSource(), [ex, ey] = dialCenter();
    const mx = (sx + ex) / 2 + (Math.random() - .5) * 200, my = Math.min(sy, ey) - 80 - Math.random() * 120;
    const el = document.createElement('i'); el.className = 'fx-comet' + (fail ? ' fail' : '');
    document.body.appendChild(el);
    const kf = [];
    for (let i = 0; i <= 12; i++) {
      const t = i / 12, u = 1 - t;
      const x = u * u * sx + 2 * u * t * mx + t * t * ex, y = u * u * sy + 2 * u * t * my + t * t * ey;
      kf.push({transform: 'translate3d(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px,0) scale(' + (1.4 - t * .6).toFixed(2) + ')', opacity: t < .1 ? t * 10 : 1});
    }
    const an = el.animate(kf, {duration: 650 + Math.random() * 250, easing: 'cubic-bezier(.5,0,.75,1)'});
    an.onfinish = () => { el.remove(); onArrive(); };
  }
  function bigBurst() {
    if (reduced()) return;
    const [x, y] = dialCenter();
    const el = document.createElement('span'); el.className = 'fx-ck-burst';
    el.style.transform = 'translate3d(' + x + 'px,' + y + 'px,0)';
    let h = '';
    for (let i = 0; i < 28; i++) h += '<i style="--a:' + (i * 360 / 28 + Math.random() * 10).toFixed(0) + 'deg;--d:' + (70 + Math.random() * 70).toFixed(0) + 'px"></i>';
    el.innerHTML = h; document.body.appendChild(el);
    setTimeout(() => el.remove(), 1100);
  }
  function track(id, o) {
    ensureCockpit();
    const total = Math.max(1, Number(o.total) || 1), cur = Math.max(0, Math.min(total, Number(o.current) || 0));
    const status = String(o.status || (cur >= total ? 'done' : 'running')).toLowerCase();
    clearTimeout(K.hideT);
    if (K.cur !== id || K.total !== total) {
      K.cur = id; K.total = total;
      buildSegs(Math.min(total, 48));
      K.el.classList.remove('done', 'fail');
    }
    const el = K.el;
    el.querySelector('.t').textContent = o.title || '后台任务';
    el.querySelector('.m').textContent = o.message || o.text || '';
    el.querySelector('.core b').textContent = Math.round(cur / total * 100) + '%';
    el.querySelector('.core small').textContent = cur + ' / ' + total;
    el.classList.add('on');
    const fail = /fail|error|err|cancel|stop/.test(status);
    const target = Math.floor(cur / total * K.segs.length);
    const spawn = Math.min(8, target - K.lit);
    for (let i = 0; i < spawn; i++) {
      const idx = K.lit + i;
      setTimeout(() => comet(() => {
        const s = K.segs[idx]; if (!s) return;
        s.classList.add('lit', 'fresh'); setTimeout(() => s.classList.remove('fresh'), 260);
        el.classList.remove('pulse'); void el.offsetWidth; el.classList.add('pulse');
      }, fail), i * 90);
    }
    if (target > K.lit + spawn) for (let i = K.lit + spawn; i < target; i++) K.segs[i] && K.segs[i].classList.add('lit');
    K.lit = Math.max(K.lit, target);
    if (status === 'done' && cur >= total) {
      setTimeout(() => { el.classList.add('done'); el.querySelector('.core b').textContent = '完成'; K.segs.forEach(s => s.classList.add('lit')); bigBurst(); }, spawn ? 900 + spawn * 90 : 0);
      K.hideT = setTimeout(() => hide(id), 3600);
    } else if (fail) {
      el.classList.add('fail');
      K.hideT = setTimeout(() => hide(id), 4500);
    }
  }
  function hide(id) {
    if (!K.el || (id && K.cur !== id)) return;
    K.el.classList.remove('on');
    const done = id;
    setTimeout(() => { if (K.cur === done) K.cur = null; }, 500);
  }
  function hookTasks() {
    TAO_HOOK.result('uiTask', (task, args) => {
      try {
        if (!task || typeof task.update !== 'function' || typeof task.close !== 'function') return task;
        const u = task.update, cl = task.close;
        let lastTitle = args[0];
        task.update = function (o) {
          u.call(task, o);
          try {
            o = o || {};
            if (o.title) lastTitle = o.title;
            if ((Number(o.total) || 0) > 1 || (K.cur === task.id)) track(task.id, Object.assign({}, o, {title: lastTitle}));
          } catch (_) {}
        };
        task.close = function () {
          cl.call(task);
          try { if (K.cur === task.id && K.el && !K.el.classList.contains('done')) { clearTimeout(K.hideT); K.hideT = setTimeout(() => hide(task.id), 1600); } } catch (_) {}
        };
      } catch (_) {}
      return task;
    }, 'fx4:task');
  }

  function init() {
    const run = fn => { try { fn(); } catch (_) {} };
    run(wireStarEntrances);
    run(hookTasks);
    run(addRailLink);
  }
  return {init, open, close, track, addRailLink, demoMission(n) {
    n = n || 12; const card = uiTask('演示 · 批量检查'); let i = 0;
    const tm = setInterval(() => { i++; card.update({current: i, total: n, message: '检查账号 ' + i + ' / ' + n}); if (i >= n) { clearInterval(tm); setTimeout(() => card.close(), 1200); } }, 450);
  }};
})();

(() => {
  TAO_FX4.init();
  try {
    TAO_HOOK.after('renderStats', () => TAO_FX4.addRailLink(), 'fx4:rail-link');
  } catch (_) {}
})();

/* =========================================================================
   结构层 · 第五轮（TAO_FX5）：任务中心 · 进度反馈 · 线索 · 日志 · 设置 · 工具箱 · 抽屉 · 弹窗
   只做“包裹 + 重排”：原函数先跑完，再在其结果上增强；所有 id、onclick、API 不变。
   ========================================================================= */
const TAO_FX5 = (() => {
  const $1 = (s, r) => (r || document).querySelector(s);
  const $all = (s, r) => [...(r || document).querySelectorAll(s)];
  const E = s => (typeof esc === 'function' ? esc(s) : String(s == null ? '' : s));
  const nowS = () => Date.now() / 1000;
  function dur(sec) {
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return sec + ' 秒';
    if (sec < 3600) return Math.floor(sec / 60) + ' 分 ' + (sec % 60 ? (sec % 60) + ' 秒' : '').trim();
    return Math.floor(sec / 3600) + ' 小时 ' + Math.round(sec % 3600 / 60) + ' 分';
  }
  return {$1, $all, E, nowS, dur};
})();

/* ---------- 1 · 任务中心：任务卡重构 ---------- */
(() => {
  const {E, nowS, dur} = TAO_FX5;
  const KIND_ICON = {send: 'send', collect_speakers: 'leads', auto_login: 'key'};
  const LIVE = new Set(['running', 'pending', 'stopping']);
  let FILTER = 'all';

  window.__fx5TaskCard = function (t) {
    const total = Math.max(0, Number(t.total) || 0), done = Number(t.done_count) || 0;
    const ok = Number(t.ok_count) || 0, fail = Number(t.fail_count) || 0, skip = Number(t.skip_count) || 0;
    const pct = Math.max(0, Math.min(100, Number(t.percent) || (total ? Math.round(done / total * 100) : 0)));
    const tone = TASK_TONE[t.status] || '';
    const live = LIVE.has(t.status);
    const canStop = t.status === 'running' || t.status === 'pending';
    const w = n => total ? (n / total * 100).toFixed(2) : 0;
    let eta = '';
    if (t.status === 'running' && done > 0 && total > done && t.created_at) {
      const el = nowS() - Number(t.created_at);
      if (el > 1) eta = '约剩 ' + dur((total - done) / (done / el));
    } else if (t.status === 'pending') eta = '排队中';
    else if (t.finished_at && t.created_at) eta = '用时 ' + dur(Number(t.finished_at) - Number(t.created_at));
    const rate = (ok + fail) ? Math.round(ok / (ok + fail) * 100) : null;
    const icon = KIND_ICON[t.kind] || 'tasks';
    return `<div class="taskrow fx-task" data-task-id="${t.id}" data-task-stage="${E(t.status_cn || t.status)}" data-status="${E(t.status)}" data-ok="${ok}" data-fail="${fail}" data-live="${live ? 1 : 0}">
    <div class="fx-task-icon k-${E(t.kind)}"><svg class="i"><use href="#i-${icon}"/></svg></div>
    <div class="fx-task-main">
      <div class="fx-task-top"><span class="fx-task-id">#${t.id}</span><b class="fx-task-title">${E(t.title)}</b>
        <span class="pill ${tone} fx-task-state">${E(t.status_cn || t.status)}</span></div>
      <div class="fx-task-meta">${E(TASK_KIND_CN[t.kind] || t.kind)} · ${rel(t.created_at)}${eta ? ' · <em>' + E(eta) + '</em>' : ''}</div>
      <div class="fx-task-bar${live ? ' is-live' : ''}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="进度 ${pct}%">
        <i class="ok" style="width:${w(ok)}%"></i><i class="fail" style="width:${w(fail)}%"></i><i class="skip" style="width:${w(skip)}%"></i>
      </div>
    </div>
    <div class="fx-task-stats">
      <div class="big"><b>${pct}<small>%</small></b><span>${done}/${total}</span></div>
      <div class="ok"><b>${ok}</b><span>成功</span></div>
      <div class="fail"><b>${fail}</b><span>失败</span></div>
      <div><b>${skip}</b><span>跳过</span></div>
      ${rate != null ? `<div class="rate"><b>${rate}<small>%</small></b><span>成功率</span></div>` : ''}
    </div>
    <div class="fx-task-actions">
      <button class="sm fit" data-onclick="taskDetail(${t.id})">明细</button>
      ${t.kind === 'auto_login' && !t.live && t.fail_count ? `<button class="sm fit" data-onclick="retryLoginTask(${t.id})">重试失败账号</button>` : ''}
      ${canStop ? `<button class="sm fit warn" data-onclick="stopTask(${t.id})">停止</button>`
                : `<button class="sm fit" data-onclick="delTask(${t.id})">删除</button>`}
    </div>
  </div>`;
  };

  /* 任务列表顶部：汇总条 + 筛选 */
  window.__fx5TaskSummary = function () {
    const el = document.getElementById('taskList');
    if (!el) return;
    const rows = [...el.querySelectorAll('.fx-task')];
    let bar = el.querySelector('.fx-task-summary');
    if (!rows.length) { if (bar) bar.remove(); return; }
    const live = rows.filter(r => r.dataset.live === '1').length;
    const failed = rows.filter(r => +r.dataset.fail > 0 || r.dataset.status === 'failed').length;
    const finished = rows.length - live;
    const ok = rows.reduce((s, r) => s + (+r.dataset.ok), 0), fl = rows.reduce((s, r) => s + (+r.dataset.fail), 0);
    const rate = ok + fl ? Math.round(ok / (ok + fl) * 100) + '%' : '—';
    const chip = (k, label, n) => `<button type="button" class="chip${FILTER === k ? ' on' : ''}" data-fx-tf="${k}" aria-pressed="${FILTER === k}">${label}<b>${n}</b></button>`;
    const html = `<div class="fx-ts-metrics">
        <div><b>${live}</b><span>进行中</span></div><div><b>${finished}</b><span>已结束</span></div>
        <div><b>${ok}</b><span>累计成功</span></div><div class="accent"><b>${rate}</b><span>整体成功率</span></div></div>
      <div class="fx-ts-filter" role="group" aria-label="筛选任务">${chip('all', '全部', rows.length)}${chip('live', '进行中', live)}${chip('done', '已结束', finished)}${chip('fail', '有失败', failed)}</div>`;
    if (!bar) { bar = document.createElement('div'); bar.className = 'fx-task-summary'; el.prepend(bar); }
    bar.innerHTML = html;
    applyFilter(el);
  };
  function applyFilter(el) {
    el.querySelectorAll('.fx-task').forEach(r => {
      const show = FILTER === 'all' || (FILTER === 'live' && r.dataset.live === '1') || (FILTER === 'done' && r.dataset.live !== '1') ||
        (FILTER === 'fail' && (+r.dataset.fail > 0 || r.dataset.status === 'failed'));
      r.hidden = !show;
    });
  }
  document.addEventListener('click', e => {
    const b = e.target.closest && e.target.closest('[data-fx-tf]');
    if (!b) return;
    FILTER = b.dataset.fxTf; window.__fx5TaskSummary();
  });
  taskCard = window.__fx5TaskCard;
  TAO_HOOK.after('loadTasks', () => { try { window.__fx5TaskSummary(); } catch (_) {} }, 'fx5:task-summary');
})();

/* ---------- 2 · 任务明细侧板（原来写进日志视图，现在就地打开） ---------- */
(() => {
  const {E} = TAO_FX5;
  let sheet = null, want = 0, tab = 'all';
  function ensure() {
    if (sheet) return sheet;
    sheet = document.createElement('aside');
    sheet.className = 'fx-sheet'; sheet.setAttribute('role', 'dialog'); sheet.setAttribute('aria-label', '任务明细');
    sheet.innerHTML = '<header><div><small>Task detail</small><h2></h2></div><button type="button" class="fx-sheet-x" aria-label="关闭">✕</button></header><div class="fx-sheet-body"></div>';
    document.body.appendChild(sheet);
    sheet.querySelector('.fx-sheet-x').addEventListener('click', close);
    sheet.addEventListener('click', e => { const b = e.target.closest('[data-fx-st]'); if (b) { tab = b.dataset.fxSt; render(); } });
    new MutationObserver(() => { if (sheet.classList.contains('on')) render(); }).observe(document.getElementById('logRaw'), {childList: true, characterData: true, subtree: true});
    return sheet;
  }
  function close() { if (sheet) sheet.classList.remove('on'); want = 0; }
  TAO_KEYS.esc('task-detail', 50, () => !!(sheet && sheet.classList.contains('on')), () => close());
  function data() { try { const t = JSON.parse(document.getElementById('logRaw').textContent); return t && t.id && Array.isArray(t.targets) ? t : (t && t.id ? t : null); } catch (_) { return null; } }
  function render() {
    const t = data(); if (!t || (want && t.id !== want)) return;
    const s = ensure();
    s.querySelector('h2').textContent = '#' + t.id + ' ' + (t.title || '');
    const total = Number(t.total) || 0, ok = Number(t.ok_count) || 0, fail = Number(t.fail_count) || 0, skip = Number(t.skip_count) || 0, done = Number(t.done_count) || 0;
    const pct = Math.round(Number(t.percent) || (total ? done / total * 100 : 0));
    const targets = t.targets || [];
    const bad = targets.filter(x => x.status === 'fail');
    const reasons = Object.entries(bad.reduce((m, x) => { const k = (x.detail || '未知').split(':')[0]; m[k] = (m[k] || 0) + 1; return m; }, {})).sort((a, b) => b[1] - a[1]);
    const maxR = reasons.length ? reasons[0][1] : 1;
    const list = targets.filter(x => tab === 'all' || x.status === tab).slice(0, 200);
    const cnt = k => targets.filter(x => x.status === k).length;
    const w = n => total ? (n / total * 100).toFixed(2) : 0;
    const chip = (k, l, n) => `<button type="button" class="chip${tab === k ? ' on' : ''}" data-fx-st="${k}">${l}<b>${n}</b></button>`;
    s.querySelector('.fx-sheet-body').innerHTML =
      `<div class="fx-sh-hero"><div class="pct"><b>${pct}</b><small>%</small></div>
        <div class="fx-sh-meta"><span class="pill">${E(t.status_cn || t.status)}</span><span>${E((TASK_KIND_CN && TASK_KIND_CN[t.kind]) || t.kind || '')}</span>
        <span>创建 ${E(fmt(t.created_at))}</span>${t.finished_at ? `<span>结束 ${E(fmt(t.finished_at))}</span>` : ''}</div></div>
       <div class="fx-task-bar${/running|pending/.test(t.status) ? ' is-live' : ''}"><i class="ok" style="width:${w(ok)}%"></i><i class="fail" style="width:${w(fail)}%"></i><i class="skip" style="width:${w(skip)}%"></i></div>
       <div class="fx-sh-stats"><div><b>${done}/${total}</b><span>已处理</span></div><div class="ok"><b>${ok}</b><span>成功</span></div><div class="fail"><b>${fail}</b><span>失败</span></div><div><b>${skip}</b><span>跳过</span></div></div>
       ${reasons.length ? `<h4>失败原因</h4><div class="fx-sh-reasons">${reasons.map(([k, v]) => `<div><span>${E(k)}</span><i style="--w:${(v / maxR * 100).toFixed(1)}%"></i><b>${v}</b></div>`).join('')}</div>` : ''}
       <h4>逐目标明细</h4>
       <div class="fx-sh-tabs">${chip('all', '全部', targets.length)}${chip('ok', '成功', cnt('ok'))}${chip('fail', '失败', cnt('fail'))}${chip('skip', '跳过', cnt('skip'))}</div>
       <div class="fx-sh-list">${list.length ? list.map(x => { const detail = x.code ? TAO_ERR.describe(x.code, x.detail || x.error || x.reason, x.retry_after) : (x.detail || x.error || x.reason || ''); return `<div class="r s-${E(x.status)}"><i></i><span class="tg">${E(x.target)}</span><span class="by">${x.account_id ? '#' + x.account_id : ''}</span><span class="st">${E(x.status_cn || x.status)}</span>${x.code ? `<span class="code">${E(x.code)}</span>` : ''}${detail ? `<span class="dt">${E(detail)}</span>` : ''}</div>`; }).join('') : '<div class="fx-empty">没有符合条件的目标</div>'}</div>
       ${targets.length > 200 ? '<p class="muted small">仅显示前 200 条，完整内容在「操作日志」视图。</p>' : ''}`;
  }
  TAO_HOOK.after('taskDetail', (_result, args) => {
    const id = args[0];
    if (typeof currentView !== 'undefined' && currentView === 'logs') return;
    want = Number(id); tab = 'all'; ensure().classList.add('on'); render();
  }, 'fx5:task-detail');
})();

/* ---------- 3 · 浮动任务卡：确定 / 不确定进度、已用时间、预计剩余 ---------- */
(() => {
  const {dur} = TAO_FX5;
  TAO_HOOK.result('uiTask', (api) => {
    try {
      const host = document.querySelector('.ui-task-card[data-task-id="' + api.id + '"]');
      const t0 = Date.now();
      let last = {current: 0, total: 1};
      if (host) {
        host.dataset.mode = 'indet';
        const el = document.createElement('span'); el.className = 'fx-ut-time'; host.appendChild(el);
        const tick = () => {
          if (!host.isConnected) return clearInterval(tm);
          const s = (Date.now() - t0) / 1000;
          let txt = '已用 ' + dur(s);
          if (host.dataset.mode === 'det' && last.current > 0 && last.current < last.total && s > 2)
            txt += ' · 约剩 ' + dur((last.total - last.current) * s / last.current);
          el.textContent = txt;
        };
        const tm = setInterval(tick, 1000); tick();
      }
      const u = api.update;
      api.update = function (o) {
        u.call(api, o);
        try {
          o = o || {};
          if (o.total != null || o.current != null) last = {current: Number(o.current) || 0, total: Math.max(1, Number(o.total) || 1)};
          if (host) {
            host.dataset.mode = last.total > 1 ? 'det' : (String(host.dataset.state) === 'done' ? 'det' : 'indet');
            // Determinate tasks keep their inline percentage.  Indeterminate
            // tasks hand width back to the stylesheet so the shimmer rule is
            // not forced through !important.
            if (host.dataset.mode === 'indet') {
              const bar = host.querySelector('.ui-task-bar i');
              if (bar) bar.style.width = '';
            }
          }
        } catch (_) {}
      };
    } catch (_) {}
    return api;
  }, 'fx5:task-time');
})();

/* ---------- 4 · 线索库：来源卡片 + 头像行 ---------- */
(() => {
  TAO_HOOK.after('loadLeads', () => {
      const box = document.getElementById('leadList'); if (!box) return;
      box.classList.add('fx-leads');
      box.querySelectorAll('.leadsrc:not([data-fx])').forEach((el, i) => {
        el.dataset.fx = '1';
        const m = (el.querySelector('.muted') || {}).textContent || '';
        const nums = m.match(/(\d+)\s*人.*?(\d+)/);
        const all = nums ? +nums[1] : 0, un = nums ? +nums[2] : 0;
        const b = el.querySelector('b');
        const head = document.createElement('div'); head.className = 'fx-ls-head';
        head.innerHTML = '<span class="fx-ls-idx">' + String(i + 1).padStart(2, '0') + '</span><span class="fx-ls-big">' + all + '<small>人</small></span>';
        el.prepend(head);
        const bar = document.createElement('div'); bar.className = 'fx-ls-ratio';
        bar.innerHTML = '<span>有用户名 <b>' + (all ? Math.round(un / all * 100) : 0) + '%</b></span><div class="tr"><i style="width:' + (all ? (un / all * 100).toFixed(1) : 0) + '%"></i></div>';
        if (b) b.after(bar);
      });
      box.querySelectorAll('.leadrow:not([data-fx])').forEach(el => {
        el.dataset.fx = '1';
        const name = (el.firstElementChild || {}).textContent || '?';
        const ch = name.replace(/^@/, '').trim().charAt(0).toUpperCase() || '?';
        let h = 0; for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
        const av = document.createElement('span'); av.className = 'fx-av'; av.textContent = ch; av.style.setProperty('--h', h);
        el.prepend(av);
      });
      if (!box.querySelector('.fx-leads-h') && box.querySelector('.leadrow')) {
        const hr = box.querySelector('.hr');
        if (hr) { const h = document.createElement('h4'); h.className = 'fx-leads-h'; h.textContent = '最近活跃的发言人'; hr.replaceWith(h); }
        const top = document.createElement('h4'); top.className = 'fx-leads-h'; top.textContent = '来源';
        const first = box.querySelector('.leadsrc'); if (first) first.before(top);
      }
  }, 'fx5:leads');
})();

/* ---------- 5 · 日志：时间线 + 可折叠高亮 JSON ---------- */
(() => {
  const {E} = TAO_FX5;
  let tf = 'all';
  function timeline() {
    const pre = document.getElementById('log'); if (!pre) return;
    const card = pre.closest('.card');
    let tl = card.querySelector('.fx-tl');
    const lines = pre.textContent.split('\n');
    const head = lines[0] || '';
    const re = /^(\d{4}\/\d{1,2}\/\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\s+([✓✗·])\s+(.+)$/;
    const rows = lines.slice(1).map(l => l.match(re)).filter(Boolean);
    if (!/^操作日志/.test(head) || !rows.length) { card.classList.remove('fx-tl-on'); if (tl) tl.remove(); return; }
    card.classList.add('fx-tl-on');
    if (!tl) { tl = document.createElement('div'); tl.className = 'fx-tl'; pre.after(tl); }
    const okN = rows.filter(r => r[2] === '✓').length, badN = rows.filter(r => r[2] === '✗').length;
    const chip = (k, l, n) => `<button type="button" class="chip${tf === k ? ' on' : ''}" data-fx-lf="${k}">${l}<b>${n}</b></button>`;
    tl.innerHTML = `<div class="fx-tl-filter">${chip('all', '全部', rows.length)}${chip('ok', '成功', okN)}${chip('bad', '失败', badN)}</div>` +
      rows.filter(r => tf === 'all' || (tf === 'ok' && r[2] === '✓') || (tf === 'bad' && r[2] === '✗')).map(r => {
        const parts = r[3].split(/\s{2,}/);
        const [d, t] = r[1].split(/\s+/);
        return `<div class="fx-tl-row ${r[2] === '✓' ? 'ok' : r[2] === '✗' ? 'bad' : ''}"><time><b>${E(t)}</b><small>${E(d)}</small></time><i></i>
          <div><b>${E(parts[0] || '')}</b>${parts[1] ? `<span class="acc">${E(parts[1])}</span>` : ''}${parts.slice(2).length ? `<p>${E(parts.slice(2).join('  '))}</p>` : ''}</div></div>`;
      }).join('');
  }
  function hl(json) {
    return E(json).replace(/(&quot;(?:\\.|[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)/gi, (m, s, colon, kw, num) => {
      if (s) return colon ? '<span class="k">' + s + '</span>' + colon : '<span class="s">' + s + '</span>';
      if (kw) return '<span class="b">' + kw + '</span>';
      return '<span class="n">' + num + '</span>';
    });
  }
  function jsonView() {
    const pre = document.getElementById('logRaw'); if (!pre) return;
    const card = pre.closest('.card');
    let v = card.querySelector('.fx-json');
    const txt = pre.textContent;
    if (!/^\s*[\[{]/.test(txt) || txt.length > 400000) { card.classList.remove('fx-json-on'); if (v) v.remove(); return; }
    if (!v) {
      v = document.createElement('div'); v.className = 'fx-json collapsed';
      v.innerHTML = '<pre class="fx-json-code"></pre><button type="button" class="sm fit fx-json-tg">展开全部</button>';
      pre.after(v);
      v.querySelector('.fx-json-tg').addEventListener('click', () => { const c = v.classList.toggle('collapsed'); v.querySelector('.fx-json-tg').textContent = c ? '展开全部' : '收起'; });
    }
    card.classList.add('fx-json-on');
    v.querySelector('.fx-json-code').innerHTML = hl(txt);
  }
  document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-fx-lf]'); if (b) { tf = b.dataset.fxLf; timeline(); } });
  const init = () => {
    const a = document.getElementById('log'), b = document.getElementById('logRaw');
    // One observer owns both representations of the log. A single mutation
    // batch refreshes the timeline and JSON view without duplicate observers.
    const observer = new MutationObserver(() => { timeline(); jsonView(); });
    const options = {childList: true, characterData: true, subtree: true};
    if (a) observer.observe(a, options);
    if (b) observer.observe(b, options);
    timeline(); jsonView();
  };
  init();
})();

/* ---------- 6 · 设置：分组卡片、改动高亮、未保存提示条 ---------- */
(() => {
  let bar = null;
  function ensureBar() {
    if (bar) return bar;
    bar = document.createElement('div'); bar.className = 'fx-savebar'; bar.setAttribute('role', 'status');
    bar.innerHTML = '<span class="dot"></span><span class="t"></span><button type="button" class="sm fit fx-sb-undo">放弃</button><button type="button" class="sm primary fx-sb-save">保存修改</button>';
    document.body.appendChild(bar);
    bar.querySelector('.fx-sb-save').addEventListener('click', () => { try { saveSettings(); } catch (_) {} });
    bar.querySelector('.fx-sb-undo').addEventListener('click', () => { try { loadSettings(true); } catch (_) {} });
    return bar;
  }
  function scan() {
    const box = document.getElementById('setBody'); if (!box) return;
    box.classList.add('fx-set');
    let n = 0;
    box.querySelectorAll('[id^="set_"]').forEach(inp => {
      const k = inp.id.slice(4); const row = inp.closest('.row'); if (!row) return;
      row.classList.add('fx-set-row');
      let changed = false;
      try {
        const sp = SET_SPECS[k];
        if (sp && sp.type === 'secret') changed = !!inp.value;
        else if (k in SET_ORIG) changed = (inp.type === 'checkbox' ? (inp.checked ? '1' : '0') : String(inp.value)) !== String(SET_ORIG[k]);
      } catch (_) {}
      row.classList.toggle('fx-changed', changed);
      if (changed) n++;
    });
    box.querySelectorAll('h4').forEach((h, i) => { h.classList.add('fx-set-h'); h.dataset.idx = String(i + 1).padStart(2, '0'); });
    const show = n > 0 && typeof currentView !== 'undefined' && currentView === 'settings';
    const b = ensureBar();
    b.querySelector('.t').textContent = '有 ' + n + ' 项参数未保存';
    b.classList.toggle('on', show);
  }
  document.addEventListener('input', e => { if (e.target.closest && e.target.closest('#setBody')) scan(); });
  document.addEventListener('change', e => { if (e.target.closest && e.target.closest('#setBody')) scan(); });
  const box = document.getElementById('setBody');
  if (box && typeof TAO_DOM !== 'undefined') TAO_DOM.onAdded('#setBody .row', () => setTimeout(scan, 0));
  setInterval(() => { if (bar && bar.classList.contains('on')) scan(); }, 1500);
  TAO_HOOK.after('TAO_FX.onView', function (value) { try { scan(); } catch (_) {} return value; }, 'fx5:settings-scan');
  /* 系统按钮：把 title 里的说明展示成副标题 */
  document.querySelectorAll('.sys-grid button[title]').forEach(b => {
    if (b.querySelector('.fx-sub')) return;
    const s = document.createElement('small'); s.className = 'fx-sub'; s.textContent = b.title; b.appendChild(s);
  });
  scan();
})();

/* ---------- 7 · 工具箱：图标化入口 + 可视化步骤条 ---------- */
(() => {
  const ICONS = ['pulse', 'key', 'globe', 'file'];
  document.querySelectorAll('.tb-launchers button').forEach((b, i) => {
    if (b.querySelector('.fx-lc-ic')) return;
    const ic = document.createElement('span'); ic.className = 'fx-lc-ic';
    ic.innerHTML = '<svg class="i"><use href="#i-' + (ICONS[i] || 'tools') + '"/></svg>';
    const no = document.createElement('span'); no.className = 'fx-lc-no'; no.textContent = String(i + 1).padStart(2, '0');
    b.prepend(ic); b.appendChild(no);
  });
  function steps() {
    const el = document.querySelector('#tbBody .tb-steps'); if (!el) return;
    let n = 0; try { n = sel.size; } catch (_) {}
    const has = !!document.getElementById('tbOp');
    const op = has ? document.getElementById('tbOp') : null;
    const selected = op && op.selectedOptions && op.selectedOptions[0] ? op.selectedOptions[0].textContent : '未选';
    const st = [['选择账号', n ? '已选 ' + n + ' 个' : '去账号清单勾选', n ? 'done' : 'cur'], ['选择工具与参数', has ? '当前：' + selected : '未选', n ? 'cur' : ''], ['确认执行', '执行前会再次确认', '']];
    el.classList.add('fx-steps');
    el.innerHTML = st.map((s, i) => '<div class="st ' + s[2] + '"><i>' + (s[2] === 'done' ? '✓' : i + 1) + '</i><b>' + s[0] + '</b><small>' + TAO_FX5.E(s[1] || '') + '</small></div>').join('<span class="ln"></span>');
  }
  const box = document.getElementById('tbBody');
  if (box && typeof TAO_DOM !== 'undefined') TAO_DOM.onAdded('#tbBody .tb-steps:not(.fx-steps)', steps);
  try { TAO_HOOK.after('syncTbSelection', () => steps(), 'fx5:toolbox-steps'); } catch (_) {}
  document.addEventListener('change', e => { if (e.target && e.target.id === 'tbOp') setTimeout(steps, 0); });
  steps();
})();

/* ---------- 8 · 账号抽屉：身份卡 + 分区 ---------- */
(() => {
  const {E} = TAO_FX5;
  const SEC = {overview: ['01', '概览'], edit: ['02', '资料'], kick: ['03', '自动清设备'], actions: ['04', '检测与查看'], export: ['05', '导出与安全'], more: ['06', '更多']};
  function enhance(id) {
    const body = document.getElementById('dBody'); if (!body || body.dataset.fx === String(id)) return;
    const a = (accounts || []).find(x => x.id === id); if (!a) return;
    body.dataset.fx = String(id);
    const kids = [...body.children];
    const groups = {};
    let cur = 'edit';
    kids.forEach(el => {
      let g;
      if (el.classList.contains('kv')) g = 'overview';
      else if (el.querySelector('#eKick,#eKickHours,#eKickLoop')) g = 'kick';
      else if (el.querySelector('[data-onclick^="saveAccount"]')) g = 'actions';
      else if (el.querySelector('[data-onclick*="exportAcc"],[data-onclick*="twofaReset("]')) g = 'export';
      else g = (cur === 'overview' ? 'edit' : (['actions', 'export', 'more'].includes(cur) ? 'more' : cur));
      cur = g;
      (groups[g] = groups[g] || []).push(el);
    });
    const name = a.label || a.username || ('#' + a.id);
    let h = 0; for (const c of String(name)) h = (h * 31 + c.charCodeAt(0)) % 360;
    const hero = document.createElement('div'); hero.className = 'fx-dhero s-' + E(a.status);
    hero.innerHTML = '<span class="fx-av xl" style="--h:' + h + '">' + E(String(name).trim().charAt(0).toUpperCase() || '?') + '</span>' +
      '<div><b>' + E(name) + '</b><span class="mono">' + E(typeof fmtPhone === 'function' ? fmtPhone(a.phone) : (a.phone || '')) + (a.username ? ' · @' + E(a.username) : '') + '</span>' +
      '<span class="fx-dtags"><span class="tag s-' + E(a.status) + '">' + E(typeof statCn === 'function' ? statCn(a.status) : a.status) + '</span>' + (a.premium ? '<span class="tag fx-prem">Premium</span>' : '') + '</span></div>';
    const frag = document.createDocumentFragment();
    frag.appendChild(hero);
    Object.keys(SEC).forEach(k => {
      if (!groups[k]) return;
      const s = document.createElement('section'); s.className = 'fx-dsec fx-dsec-' + k;
      s.innerHTML = '<h4><span>' + SEC[k][0] + '</span>' + SEC[k][1] + '</h4>';
      groups[k].forEach(el => s.appendChild(el));
      frag.appendChild(s);
    });
    body.appendChild(frag);
  }
  TAO_HOOK.after('openDrawer', (_result, args) => {
    try { const b = document.getElementById('dBody'); if (b) delete b.dataset.fx; enhance(args[0]); } catch (_) {}
  }, 'fx5:drawer-sections');
})();

/* ---------- 9 · 弹窗：文件拖放区、文本行数、标题强调 ---------- */
(() => {
  function dropzone(inp) {
    if (inp.dataset.fxDrop || inp.closest('.fx-drop')) return;
    inp.dataset.fxDrop = '1';
    const z = document.createElement('div'); z.className = 'fx-drop';
    z.innerHTML = '<svg class="i"><use href="#i-upload"/></svg><b>拖入文件，或点击选择</b><small>' + (inp.accept ? '支持 ' + inp.accept.split(',').filter(x => x.startsWith('.')).join(' ') : '任意文件') + (inp.multiple ? ' · 可多选' : '') + '</small><span class="fx-drop-files"></span>';
    inp.parentNode.insertBefore(z, inp);
    z.appendChild(inp);
    const upd = () => {
      const f = [...(inp.files || [])];
      z.classList.toggle('has', !!f.length);
      z.querySelector('.fx-drop-files').textContent = f.length ? f.slice(0, 3).map(x => x.name).join('、') + (f.length > 3 ? ' 等 ' + f.length + ' 个' : '') : '';
    };
    inp.addEventListener('change', upd);
    ['dragenter', 'dragover'].forEach(t => z.addEventListener(t, () => z.classList.add('over')));
    ['dragleave', 'drop'].forEach(t => z.addEventListener(t, () => setTimeout(() => { z.classList.remove('over'); upd(); }, 0)));
  }
  function counter(ta) {
    if (ta.dataset.fxCount) return;
    ta.dataset.fxCount = '1';
    const c = document.createElement('span'); c.className = 'fx-count';
    ta.insertAdjacentElement('afterend', c);
    const upd = () => { const n = ta.value.split('\n').filter(s => s.trim()).length; c.textContent = n ? n + ' 行' : ''; };
    ta.addEventListener('input', upd); upd();
  }
  const run = () => {
    document.querySelectorAll('.modal input[type=file]').forEach(dropzone);
    document.querySelectorAll('.modal textarea').forEach(counter);
  };
  run();
  if (typeof TAO_DOM !== 'undefined') TAO_DOM.onAdded('.modal input[type=file], .modal textarea:not([data-fx-count])', run);
})();

/* =========================================================================
   第六轮（TAO_FX6）：所有弹窗与二级面板
   确认框 · 输入框 · 危险确认 · 结果速览 · 扫码 · 登录 · 官方验证码 · 群发 · 采集选群 · 一条龙 · AI 助手
   ========================================================================= */
(() => {
  const E = s => (typeof esc === 'function' ? esc(s) : String(s == null ? '' : s));
  const view = () => (typeof currentView !== 'undefined' ? currentView : '');

  /* ---------- 1 · 通用对话框 mUi：类型图标 + 危险态 + 确认词实时校验 ---------- */
  const mUi = document.getElementById('mUi');
  const ICON = {
    confirm: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.8-2.5 2.2-2.5 3.9"/><path d="M12 17h.01"/></svg>',
    danger: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3 2.5 20h19L12 3z"/><path d="M12 10v4.5M12 17.5h.01"/></svg>',
    prompt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="m13.5 6.5 4 4"/></svg>',
    progress: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3a9 9 0 1 1-9 9"/></svg>'
  };
  if (mUi && !mUi.querySelector('.fx-ui-ic')) {
    const ic = document.createElement('div'); ic.className = 'fx-ui-ic'; ic.setAttribute('aria-hidden', 'true');
    mUi.prepend(ic);
  }
  function setKind(k) {
    if (!mUi) return;
    mUi.dataset.kind = k;
    const ic = mUi.querySelector('.fx-ui-ic'); if (ic) ic.innerHTML = ICON[k] || ICON.confirm;
    const old = mUi.querySelector('.fx-word'); if (old) old.remove();
  }
  TAO_HOOK.around('uiOpenShell', (next, args) => {
    if (mUi) mUi.querySelectorAll('.fx-drop').forEach(d => d.remove());
    const r = next(...args);
    setKind('confirm');
    return r;
  }, 'fx6:ui-shell');
  TAO_HOOK.around('uiConfirm', (next, args) => { const p = next(...args); const opts = args[0]; setKind(opts && opts.danger ? 'danger' : 'confirm'); return p; }, 'fx6:confirm');
  TAO_HOOK.around('uiPrompt', (next, args) => { const p = next(...args); const opts = args[0]; setKind(opts && opts.danger ? 'danger' : 'prompt'); window.__fx6Scan && window.__fx6Scan(); return p; }, 'fx6:prompt');
  TAO_HOOK.around('uiConfirmDanger', (next, args) => {
    const opts = args[0];
    const word = (opts && opts.word) || '确认';
    const p = next(...args);
    setTimeout(() => {
      const inp = document.getElementById('uiInput'), ok = document.getElementById('uiOk');
      if (!inp || !ok || !mUi.classList.contains('on')) return;
      const w = document.createElement('div'); w.className = 'fx-word';
      w.innerHTML = '输入 <b>' + E(word) + '</b> 以解锁执行按钮';
      const wrap = document.getElementById('uiInputWrap'); if (wrap) wrap.before(w);
      const chk = () => { const okNow = inp.value.trim() === word; ok.disabled = !okNow; w.classList.toggle('ok', okNow); };
      inp.addEventListener('input', chk); chk();
    }, 0);
    return p;
  }, 'fx6:danger');
  try { TAO_HOOK.around('uiProgress', (next, args) => { const r = next(...args); setKind('progress'); return r; }, 'fx6:progress'); } catch (_) {}

  /* ---------- 2 · 结果速览：结果写进日志时，在当前页面弹出速览卡 ---------- */
  let peek = null, peekTimer = 0;
  function ensurePeek() {
    if (peek) return peek;
    peek = document.createElement('section'); peek.className = 'fx-peek'; peek.setAttribute('role', 'status'); peek.setAttribute('aria-live', 'polite');
    peek.innerHTML = '<header><span class="dot"></span><b>操作结果</b><span class="sp"></span><button type="button" class="fx-peek-x" aria-label="关闭">✕</button></header><pre class="fx-peek-body"></pre><footer><button type="button" class="sm fit fx-peek-copy">复制</button><button type="button" class="sm primary fx-peek-go">查看完整结果</button></footer>';
    document.body.appendChild(peek);
    peek.querySelector('.fx-peek-x').onclick = () => peek.classList.remove('on');
    peek.querySelector('.fx-peek-go').onclick = () => { peek.classList.remove('on'); try { closeAll(); } catch (_) {} switchView('logs'); };
    peek.querySelector('.fx-peek-copy').onclick = () => { try { navigator.clipboard.writeText(document.getElementById('log').textContent); toast('已复制结果', 'ok'); } catch (_) {} };
    peek.addEventListener('mouseenter', () => clearTimeout(peekTimer));
    peek.addEventListener('mouseleave', arm);
    return peek;
  }
  function arm() { clearTimeout(peekTimer); peekTimer = setTimeout(() => peek && peek.classList.remove('on'), 14000); }
  TAO_HOOK.after('out', (_result, args) => {
    const pin = args[1];
    try {
      if (pin && view() !== 'logs') {
        const txt = document.getElementById('log').textContent || '';
        const lines = txt.split('\n');
        const p = ensurePeek();
        const bad = /失败|错误|error|✗/i.test(lines.slice(0, 4).join(' '));
        p.classList.toggle('bad', bad);
        p.querySelector('b').textContent = bad ? '操作结果 · 有失败' : '操作结果';
        p.querySelector('.fx-peek-body').textContent = lines.slice(0, 12).join('\n') + (lines.length > 12 ? '\n…' : '');
        p.classList.add('on'); arm();
      }
    } catch (_) {}
  }, 'fx6:peek');

  /* ---------- 3 · 浮动任务卡：把 JSON 阶段信息翻译成人话 ---------- */
  const PHASE = {queued: '排队', pending: '等待', running: '进行中', login: '登录', logging_in: '登录中', code: '取码', check: '检查', checking: '检查中', twofa: '二验', kick: '清设备', export: '导出', ready: '可导出', done: '完成', ok: '成功', fail: '失败', failed: '失败', skip: '跳过', skipped: '跳过', error: '出错'};
  TAO_HOOK.result('uiTask', (api) => {
    const u = api.update;
    api.update = function (o) {
      if (o && typeof o.text === 'string') {
        o = Object.assign({}, o, {text: o.text.replace(/\{[^{}]*\}/g, m => {
          try { const j = JSON.parse(m); const s = Object.entries(j).filter(([, v]) => v).map(([k, v]) => (PHASE[k] || k) + ' ' + v).join(' · '); return s || '—'; } catch (_) { return m; }
        })});
      }
      return u.call(api, o);
    };
    return api;
  }, 'fx6:phase-cn');

  /* ---------- 4 · 一条龙记录：行卡片 + 进度条 + 空状态 ---------- */
  TAO_HOOK.after('oneStopHistory', () => {
      const p = document.getElementById('oneStopHistory'); if (!p) return;
      p.querySelectorAll('.one-stop-row:not([data-fx])').forEach(row => {
        row.dataset.fx = '1';
        const m = row.textContent.match(/(\d+)\s*\/\s*(\d+)/);
        const pct = m && +m[2] ? Math.round(+m[1] / +m[2] * 100) : 0;
        const btns = [...row.querySelectorAll('button')];
        const info = document.createElement('div'); info.className = 'fx-os-info';
        [...row.childNodes].forEach(n => { if (!(n.nodeType === 1 && n.tagName === 'BUTTON')) info.appendChild(n); });
        const bar = document.createElement('div'); bar.className = 'fx-os-bar'; bar.innerHTML = '<i style="width:' + pct + '%"></i>';
        const act = document.createElement('div'); act.className = 'fx-os-act';
        btns.forEach(b => { b.classList.add('sm', 'fit'); act.appendChild(b); });
        const pc = document.createElement('div'); pc.className = 'fx-os-pct'; pc.innerHTML = pct + '<small>%</small>';
        row.append(pc, info, bar, act);
      });
      const em = p.querySelector('.empty');
      if (em && !em.dataset.fx) { em.dataset.fx = '1'; em.classList.add('fx-empty-state'); em.innerHTML = '<span class="fx-es-ic">⚡</span><b>还没有一条龙记录</b><small>在账号页勾选账号后点「一条龙处理」，任务会保存在这里，刷新页面也能继续。</small>'; }
  }, 'fx6:one-stop-history');

  /* ---------- 5 · AI 助手：欢迎页 + 示例问题 ---------- */
  const SUGG = ['列出所有已登录账号', '哪些账号被限制了？', '统计各国家账号数量', '最近失败的任务是什么原因'];
  function hello() {
    const box = document.getElementById('aiMsgs'); if (!box) return;
    const has = box.querySelector('.ai-row.user,.ai-row.bot');
    let h = box.querySelector('.fx-ai-hello');
    if (has) { if (h) h.remove(); return; }
    if (h) return;
    h = document.createElement('div'); h.className = 'fx-ai-hello';
    h.innerHTML = '<div class="orb"></div><b>问我任何关于账号的事</b><small>只读工具默认可用；写入和危险操作会先请你确认。</small><div class="sugg">' +
      SUGG.map(s => '<button type="button" class="chip">' + E(s) + '</button>').join('') + '</div>';
    box.prepend(h);
    h.querySelectorAll('.chip').forEach(b => b.onclick = () => { const i = document.getElementById('aiInput'); if (i) { i.value = b.textContent; i.focus(); i.dispatchEvent(new Event('input', {bubbles: true})); } });
  }
  TAO_HOOK.after('openAiPanel', () => { try { hello(); } catch (_) {} }, 'fx6:ai-hello');
  const am = document.getElementById('aiMsgs');
  if (am && typeof TAO_DOM !== 'undefined') TAO_DOM.onAdded('#aiMsgs .ai-row.user, #aiMsgs .ai-row.bot', () => { const h = am.querySelector('.fx-ai-hello'); if (h && am.querySelector('.ai-row.user,.ai-row.bot')) h.remove(); });

  /* ---------- 6 · 扫码登录：取景框 ---------- */
  const qr = document.getElementById('qrImg');
  if (qr && !qr.closest('.fx-qr')) {
    const f = document.createElement('div'); f.className = 'fx-qr';
    f.innerHTML = '<span class="c tl"></span><span class="c tr"></span><span class="c bl"></span><span class="c br"></span><span class="scan"></span>';
    qr.parentNode.insertBefore(f, qr); f.prepend(qr);
    const st = document.getElementById('qrStatus');
    const upd = () => {
      const t = (st && st.textContent) || '';
      f.dataset.state = /成功|已授权|完成/.test(t) ? 'ok' : (/过期|失败|错误/.test(t) ? 'bad' : (/密码/.test(t) ? 'pass' : 'wait'));
      if (st) st.dataset.state = f.dataset.state;
    };
    if (st) new MutationObserver(upd).observe(st, {childList: true, characterData: true, subtree: true});
    upd();
  }

  /* ---------- 7 · 登录弹窗：三步状态 ---------- */
  const lHint = document.getElementById('lHint');
  if (lHint && !document.querySelector('.fx-lsteps')) {
    const s = document.createElement('div'); s.className = 'fx-lsteps';
    s.innerHTML = '<span data-s="1"><i>1</i>发送验证码</span><b></b><span data-s="2"><i>2</i>填写验证码</span><b></b><span data-s="3"><i>3</i>完成登录</span>';
    lHint.before(s);
    const upd = () => {
      const t = lHint.textContent || '';
      const n = /成功|已登录|完成/.test(t) ? 3 : (/已发送|已发码|请输入|等待|取码|查询/.test(t) ? 2 : 1);
      s.querySelectorAll('span').forEach(x => { const k = +x.dataset.s; x.className = k < n ? 'done' : (k === n ? 'cur' : ''); });
    };
    new MutationObserver(upd).observe(lHint, {childList: true, characterData: true, subtree: true});
    upd();
  }

  /* ---------- 8 · 弹窗扫描：拖放区与行数（含 uiPrompt 动态文件框） ---------- */
  function dropzone(inp) {
    if (inp.dataset.fxDrop || inp.closest('.fx-drop')) return;
    inp.dataset.fxDrop = '1';
    const z = document.createElement('div'); z.className = 'fx-drop';
    z.innerHTML = '<svg class="i"><use href="#i-upload"/></svg><b>拖入文件，或点击选择</b><small>' + (inp.accept ? '支持 ' + E(inp.accept.split(',').filter(x => x.startsWith('.')).join(' ')) : '任意文件') + (inp.multiple ? ' · 可多选' : '') + '</small><span class="fx-drop-files"></span>';
    inp.parentNode.insertBefore(z, inp); z.appendChild(inp);
    const upd = () => { const f = [...(inp.files || [])]; z.classList.toggle('has', !!f.length); z.querySelector('.fx-drop-files').textContent = f.length ? f.slice(0, 3).map(x => x.name).join('、') + (f.length > 3 ? ' 等 ' + f.length + ' 个' : '') : ''; };
    inp.addEventListener('change', upd);
    ['dragenter', 'dragover'].forEach(t => z.addEventListener(t, () => z.classList.add('over')));
    ['dragleave', 'drop'].forEach(t => z.addEventListener(t, () => setTimeout(() => { z.classList.remove('over'); upd(); }, 0)));
  }
  window.__fx6Scan = () => {
    document.querySelectorAll('.modal input[type=file]').forEach(dropzone);
    document.querySelectorAll('.modal textarea:not([data-fx-count])').forEach(ta => {
      ta.dataset.fxCount = '1';
      const c = document.createElement('span'); c.className = 'fx-count'; ta.insertAdjacentElement('afterend', c);
      const upd = () => { const n = ta.value.split('\n').filter(s => s.trim()).length; c.textContent = n ? n + ' 行' : ''; };
      ta.addEventListener('input', upd); upd();
    });
  };
  TAO_HOOK.around('openModal', (next, args) => { if (peek) peek.classList.remove('on'); const r = next(...args); try { window.__fx6Scan(); } catch (_) {} return r; }, 'fx6:modal');
  window.__fx6Scan();
})();

/* 第七轮：品牌图标动效 */
(() => {
  const mark = document.getElementById('taoLogo');
  if (mark && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    const play = () => { mark.classList.remove('draw'); void mark.offsetWidth; mark.classList.add('draw'); };
    const intro = document.querySelector('.fx-pintro');
    if (intro) new MutationObserver((_, ob) => { if (!document.querySelector('.fx-pintro')) { ob.disconnect(); play(); } }).observe(document.body, {childList: true});
    else play();
  }
  // 后台请求进行中：三角形轻轻跳动
  try {
    let n = 0;
    TAO_HOOK.around('api', function (next, args) {
      n++; setTimeout(() => { if (n && mark) mark.classList.add('busy'); }, 400);
      const done = () => { n = Math.max(0, n - 1); if (!n && mark) setTimeout(() => !n && mark.classList.remove('busy'), 250); };
      try { const p = next(...args); Promise.resolve(p).then(done, done); return p; } catch (e) { done(); throw e; }
    }, 'fx7:busy');
  } catch (_) {}
  const h = document.querySelector('#mHelp>h3');
  if (h && !h.querySelector('.fx-help-logo')) h.insertAdjacentHTML('afterbegin', '<svg viewBox="100 420 1050 470" aria-hidden="true" focusable="false" class="fx-help-logo"><g fill="none" stroke="currentColor" stroke-width="82" stroke-linecap="round" stroke-linejoin="round"><path class="tl-s" pathLength="1" d="M160 481H555C590 481 608 494 626 524L766 756C800 812 855 827 915 827A173 173 0 1 0 773 555"/><path class="tl-s" pathLength="1" d="M306 485V768C306 798 322 812 342 812C360 812 372 801 384 781L566 481"/></g><path class="tl-tri" d="M583 640L534 716H632Z" fill="var(--accent,#C6FF3D)" stroke="var(--accent,#C6FF3D)" stroke-width="22" stroke-linejoin="round"/></svg>');
})();

/* =========================================================================
   第八轮（TAO_FX8）：降低上手门槛
   1 智能导入  2 每行一个下一步  3 选中后的推荐操作  4 新手引导清单
   5 术语解释  6 高级参数收起    7 命令面板意图    8 专注模式
   ========================================================================= */
const TAO_FX8 = (() => {
  const $1 = s => document.querySelector(s);
  const E = s => (typeof esc === 'function' ? esc(s) : String(s == null ? '' : s));
  const LS = {get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} }};
  const accById = id => (typeof accounts !== 'undefined' ? accounts : []).find(a => a.id === id);

  /* ---------------- 状态说明（人话） ---------------- */
  const ST_HINT = {
    new: '刚导入，还没登录过。点「登录」开始。',
    active: '本地有会话，最近一次检查正常。注意：这不等于此刻一定在线，久未检查可以再查一次。',
    unauthorized: '会话已失效，需要重新登录。',
    restricted: '被 Telegram 限制了部分功能，点开查看原因。',
    spam_block: '临时被限制发消息，通常过几天自动解除。',
    spam_block_perm: '永久限制发消息给陌生人。',
    banned: '账号已被封禁，无法再使用。',
    frozen: '账号被冻结，需要在官方客户端申诉。',
    flood_wait: '操作太频繁，被要求等待一段时间。',
    error: '最近一次操作出错，点开查看详情。',
    deleted: '账号已在 Telegram 注销，只保留本地记录。'
  };

  function ago(sec) {
    if (sec < 90) return '刚刚'; if (sec < 5400) return Math.round(sec / 60) + ' 分钟前';
    if (sec < 172800) return Math.round(sec / 3600) + ' 小时前'; return Math.round(sec / 86400) + ' 天前';
  }
  function tipFor(a) {
    const parts = [ST_HINT[a.status] || a.status];
    if (a.status_note) parts.push('原因：' + String(a.status_note).slice(0, 160));
    const now = Number(a.server_now) || Math.round(Date.now() / 1000);
    const LCR = {ok: '通过', warn: '通过，但账号受限', fail: '失败'};
    parts.push(a.last_check_at ? '上次检查：' + ago(Math.max(0, now - Number(a.last_check_at))) + (LCR[a.last_check_result] ? ' · ' + LCR[a.last_check_result] : '') : '还没检查过');
    if (a.last_check_error) parts.push('检查出错：' + String(a.last_check_error).slice(0, 160));
    if (a.authorized && a.status !== 'active' && a.status !== 'new') parts.push('本地仍保留会话');
    return parts.join('\n');
  }
  /* =================== 2 · 每行一个“下一步” =================== */
  function primaryFor(a) {
    if (!a) return null;
    const st = a.status || '';
    /* 依据后端说明：authorized 只代表本地有会话；status 才是最近一次检查的结论 */
    if (st === 'banned' || st === 'frozen' || st === 'deleted') return {txt: '查看详情', sel: null, drawer: true};
    if (!a.authorized || st === 'unauthorized') return {txt: a.authorized ? '重新登录' : '登录', sel: 'button[data-onclick^="startLogin"]'};
    if (/^(restricted|spam_block|spam_block_perm|flood_wait|error)$/.test(st)) return {txt: '查看原因', sel: null, drawer: true};
    return {txt: '检查', sel: 'button[data-onclick^="check("]'};
  }
  let menu = null, menuFor = null;
  function closeMore(refocus) {
    if (menu) menu.classList.remove('on');
    if (menuFor) { menuFor.setAttribute('aria-expanded', 'false'); if (refocus === true) menuFor.focus({preventScroll: true}); }
    menuFor = null;
  }
  TAO_KEYS.esc('more-menu', 95, () => !!menuFor, () => closeMore(true));
  function openMore(btn, src) {
    if (!menu) {
      menu = document.createElement('div'); menu.className = 'fx-more-menu'; menu.setAttribute('role', 'menu');
      document.body.appendChild(menu);
      document.addEventListener('click', e => { if (menu.classList.contains('on') && !menu.contains(e.target) && !e.target.closest('.fx-more-btn')) closeMore(); }, true);
      /* 菜单内键盘：上下 / Home / End 移动，Tab 关闭 */
      menu.addEventListener('keydown', e => {
        const items = [...menu.querySelectorAll('button:not(:disabled)')]; const i = items.indexOf(document.activeElement);
        if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
        else if (e.key === 'Home') { e.preventDefault(); items[0].focus(); }
        else if (e.key === 'End') { e.preventDefault(); items[items.length - 1].focus(); }
        else if (e.key === 'Tab') closeMore(true);
      });
      addEventListener('scroll', closeMore, true); addEventListener('resize', closeMore);
    }
    if (menuFor === btn) { closeMore(); return; }
    menuFor = btn; btn.setAttribute('aria-expanded', 'true');
    menu.innerHTML = '';
    src.querySelectorAll('button').forEach(o => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'fx-more-item'; b.setAttribute('role', 'menuitem');
      b.textContent = o.textContent.trim(); b.title = o.title || ''; b.disabled = o.disabled;
      b.onclick = () => { closeMore(); o.click(); };
      menu.appendChild(b);
    });
    const id = Number(btn.closest('tr').dataset.id);
    const d = document.createElement('button'); d.type = 'button'; d.className = 'fx-more-item'; d.setAttribute('role', 'menuitem'); d.textContent = '打开详情';
    d.onclick = () => { closeMore(); openDrawer(id); }; menu.appendChild(d);
    const r = btn.getBoundingClientRect();
    menu.classList.add('on');
    const mh = menu.offsetHeight, mw = menu.offsetWidth;
    menu.style.left = Math.max(8, Math.min(innerWidth - mw - 8, r.right - mw)) + 'px';
    menu.style.top = (r.bottom + mh + 8 > innerHeight ? r.top - mh - 6 : r.bottom + 6) + 'px';
    const first = menu.querySelector('button:not(:disabled)'); if (first) first.focus({preventScroll: true});
  }
  function enhanceRows() {
    const tb = document.getElementById('tb'); if (!tb) return;
    tb.querySelectorAll('tr[data-id]').forEach(tr => {
      const cell = tr.querySelector('.account-action-cell'); if (!cell || cell.dataset.fx8) return;
      cell.dataset.fx8 = '1';
      const a = accById(Number(tr.dataset.id));
      const p = primaryFor(a);
      const btns = [...cell.querySelectorAll(':scope > button.sm')];
      if (!p || !btns.length) return;
      let prim = p.sel ? btns.find(b => b.matches(p.sel)) : null;
      const src = document.createElement('span'); src.className = 'fx-more-src'; src.hidden = true;
      btns.forEach(b => { if (b !== prim) src.appendChild(b); });
      if (!prim) {
        prim = document.createElement('button'); prim.type = 'button'; prim.className = 'sm';
        prim.textContent = p.txt; prim.onclick = () => openDrawer(a.id);
      } else prim.textContent = p.txt;
      prim.classList.add('fx-next');
      const more = document.createElement('button'); more.type = 'button'; more.className = 'sm fit fx-more-btn';
      more.setAttribute('aria-label', '更多操作'); more.setAttribute('aria-haspopup', 'menu'); more.setAttribute('aria-expanded', 'false'); more.title = '更多操作'; more.textContent = '⋯';
      more.onclick = () => openMore(more, src);
      cell.append(prim, more, src);
      const tag = tr.querySelector('.tag[class*="s-"]');
      if (tag && a && (ST_HINT[a.status] || a.status_note)) { tag.dataset.tip = tipFor(a); tag.tabIndex = 0; }
    });
  }

  /* =================== 3 · 选中后推荐操作 =================== */
  function recommend() {
    const bar = document.getElementById('bulk'); if (!bar) return;
    const ids = [...(typeof sel !== 'undefined' ? sel : [])];
    bar.querySelectorAll('.fx-rec').forEach(b => b.classList.remove('fx-rec'));
    let hint = bar.querySelector('.fx-bulk-hint');
    if (!hint) { hint = document.createElement('span'); hint.className = 'fx-bulk-hint'; const s = document.getElementById('bulkSelectionSummary'); s && s.after(hint); }
    if (!ids.length) { hint.textContent = ''; return; }
    const list = ids.map(accById).filter(Boolean);
    const noauth = list.filter(a => !a.authorized && !/^(banned|frozen|deleted)$/.test(a.status)).length, bad = list.filter(a => /^(banned|frozen|deleted)$/.test(a.status)).length;
    let pick = null, txt = '';
    if (noauth === list.length) { pick = 'auto'; txt = '都未登录 → 建议自动登录'; }
    else if (noauth) { pick = 'auto'; txt = noauth + ' 个未登录，可先自动登录'; }
    else if (bad) { txt = bad + ' 个已封禁/冻结，建议移除'; }
    else { pick = 'check'; txt = '都已登录 → 建议先健康检查'; }
    hint.textContent = txt;
    if (pick) { const b = bar.querySelector('button[data-onclick="bulk(\'' + pick + '\')"]'); if (b) b.classList.add('fx-rec'); }
  }

  /* =================== 1 · 智能导入 =================== */
  const TYPES = {
    session: {name: 'session 文件', modal: 'mSession', ic: 'key'},
    tdata: {name: 'tdata 压缩包', modal: 'mTdata', ic: 'file'},
    phones: {name: '手机号清单', modal: 'mImport', ic: 'upload'},
    strsess: {name: 'StringSession 文本', modal: 'mSession', ic: 'key'},
    single: {name: '单个手机号', modal: 'mAdd', ic: 'user-plus'},
    mixed: {name: '混合压缩包（tdata + session）', modal: 'mMixed', ic: 'file'},
    unknown: {name: '无法识别', modal: null, ic: 'help'}
  };
  async function sniffZip(f) {
    try {
      const head = new Uint8Array(await f.slice(0, Math.min(f.size, 4 << 20)).arrayBuffer());
      const tail = f.size > 4 << 20 ? new Uint8Array(await f.slice(f.size - (1 << 20)).arrayBuffer()) : new Uint8Array(0);
      const txt = new TextDecoder('latin1').decode(head) + new TextDecoder('latin1').decode(tail);
      const td = /key_data|D877F783D5D3EF8C|tdata\//i.test(txt), se = /\.session/i.test(txt);
      /* 混合 ZIP 交给统一的两阶段导入弹窗处理。 */
      if (td && se) return 'mixed';
      if (td) return 'tdata';
      if (se) return 'session';
      return 'unknown';
    } catch (_) {}
    return 'tdata';
  }
  function classifyText(t) {
    const lines = t.split(/\r?\n/).map(s => s.trim()).filter(s => s && !s.startsWith('#'));
    if (!lines.length) return null;
    const ss = lines.filter(l => /^(?:[^|\s]{1,40}\|)?1[A-Za-z0-9_\-+/=]{80,}$/.test(l)).length;
    const ph = lines.filter(l => /\+?\d[\d\s-]{6,}\d/.test(l) && (/\|/.test(l) || /https?:\/\//.test(l))).length;
    if (ss && ss >= lines.length / 2) return {type: 'strsess', n: ss};
    if (ph) return {type: 'phones', n: ph};
    if (lines.length === 1 && /^\+?\d[\d\s-]{6,}\d$/.test(lines[0])) return {type: 'single', n: 1};
    const bare = lines.filter(l => /^\+?\d[\d\s-]{6,}\d$/.test(l)).length;
    if (bare) return {type: 'phones', n: bare};
    return {type: 'unknown', n: lines.length};
  }
  let smart = null, groups = [];
  function ensureSmart() {
    if (smart) return smart;
    smart = document.createElement('div');
    smart.className = 'modal modal-wide fx-smart'; smart.id = 'mSmartImport';
    smart.setAttribute('role', 'dialog'); smart.setAttribute('aria-modal', 'true');
    smart.innerHTML = '<h3>添加账号</h3><div class="body">' +
      '<p class="muted">不用分清格式：把手里的文件拖进来，或直接粘贴文本，我会自动识别是哪一种。</p>' +
      '<div class="fx-smart-drop" tabindex="0"><svg class="i"><use href="#i-upload"/></svg><b>拖入文件，或点击选择</b>' +
      '<small>.session · tdata 的 zip · 手机号清单 txt/csv，可一次拖多个</small><input type="file" multiple hidden data-fx-drop="1" /></div>' +
      '<div class="fx-smart-or"><span>或者粘贴文本</span></div>' +
      '<textarea class="fx-smart-text" rows="5" placeholder="每行一个，例如：&#10;+10000000000|https://取码链接&#10;1BVtsOK4Bu…（StringSession）&#10;+8613800138000（单个手机号）"></textarea>' +
      '<div class="fx-smart-result" aria-live="polite"></div>' +
      '<details class="fx-smart-adv"><summary>我知道格式，直接用原来的入口</summary><div class="fx-smart-links">' +
      '<button type="button" class="sm fit" data-m="mAdd">手动添加单个</button><button type="button" class="sm fit" data-m="mImport">手机号清单</button>' +
      '<button type="button" class="sm fit" data-m="mSession">导入 session</button><button type="button" class="sm fit" data-m="mTdata">导入 tdata</button></div></details>' +
      '</div><div class="foot"><button type="button" data-onclick="closeAll()">取消</button><button type="button" class="primary fx-smart-go" disabled>继续</button></div>';
    document.body.appendChild(smart);
    const drop = smart.querySelector('.fx-smart-drop'), fi = drop.querySelector('input'), ta = smart.querySelector('.fx-smart-text');
    drop.onclick = () => fi.click();
    drop.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fi.click(); } };
    fi.onchange = () => analyze([...fi.files], ta.value);
    ['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); const f = [...(e.dataTransfer.files || [])]; smart._files = f; analyze(f, ta.value); });
    let tmr = 0; ta.addEventListener('input', () => { clearTimeout(tmr); tmr = setTimeout(() => analyze(smart._files || [...fi.files], ta.value), 250); });
    smart.querySelectorAll('.fx-smart-links button').forEach(b => b.onclick = () => { closeAll(); openModal(b.dataset.m); });
    smart.querySelector('.fx-smart-go').onclick = () => go(groups[0]);
    return smart;
  }
  async function analyze(files, text) {
    const g = {};
    const add = (type, item, n) => { (g[type] = g[type] || {type, files: [], text: '', n: 0}); if (item) g[type].files.push(item); g[type].n += n || 1; };
    for (const f of files || []) {
      const nm = f.name.toLowerCase();
      if (nm.endsWith('.session')) add('session', f);
      else if (nm.endsWith('.zip')) add(await sniffZip(f), f);
      else if (/\.(txt|csv|md)$/.test(nm)) {
        const c = classifyText(await f.text());
        if (c) { add(c.type, null, c.n); g[c.type].text += (g[c.type].text ? '\n' : '') + (await f.text()); }
        else add('unknown', f);
      } else add('unknown', f);
    }
    if (text && text.trim()) { const c = classifyText(text); if (c) { add(c.type, null, c.n); g[c.type].text += (g[c.type].text ? '\n' : '') + text; } }
    const BAD = x => x.type === 'unknown' || x.type === 'mixed';
    groups = Object.values(g).sort((a, b) => BAD(a) - BAD(b));
    const res = smart.querySelector('.fx-smart-result'), btn = smart.querySelector('.fx-smart-go');
    const valid = groups.filter(x => !BAD(x));
    if (!groups.length) { res.innerHTML = ''; btn.disabled = true; return; }
    res.innerHTML = '<div class="fx-smart-h">识别结果</div>' + groups.map((x, i) => {
      const t = TYPES[x.type];
      const unit = x.type === 'session' || x.type === 'tdata' || x.type === 'mixed' ? ' 个文件' : (x.type === 'unknown' ? ' 项' : ' 个账号');
      const note = x.type === 'mixed' ? '<small>同一个压缩包里既有 tdata 又有 session，后端一次只能按一种方式导入。请先用「工具 → ZIP 拆包」拆开，或分成两个压缩包。</small>'
        : x.type === 'unknown' ? '<small>看不出格式，可在下方选择原来的入口</small>' : (valid.length > 1 ? '<button type="button" class="sm fit" data-i="' + i + '">处理这一组</button>' : '');
      return '<div class="fx-smart-row' + (BAD(x) ? ' bad' : '') + '"><svg class="i ic"><use href="#i-' + t.ic + '"/></svg><b>' + t.name + '</b><span class="n">' + x.n + unit + '</span>' + note + '</div>';
    }).join('');
    const snap = groups.slice(); res.querySelectorAll('button[data-i]').forEach(b => b.onclick = () => go(snap[+b.dataset.i]));
    btn.disabled = !valid.length;
    btn.textContent = valid.length > 1 ? '先处理第一组' : (valid.length ? '继续：确认导入设置' : '继续');
    groups = valid.concat(groups.filter(BAD));
  }
  function setFiles(input, files) {
    if (!input || !files.length) return;
    try { const dt = new DataTransfer(); files.forEach(f => dt.items.add(f)); input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true})); } catch (_) {}
  }
  function pickRadio(name, val, fn) { const r = document.querySelector('input[name="' + name + '"][value="' + val + '"]'); if (r) { r.checked = true; try { fn && fn(); } catch (_) {} } }
  function go(gr) {
    if (!gr || gr.type === 'unknown') return;
    if (gr.type === 'mixed') { openMixed(gr.files); return; }
    const t = TYPES[gr.type];
    closeAll(); openModal(t.modal);
    setTimeout(() => {
      if (gr.type === 'session') { pickRadio('sessMode', 'upload', window.toggleSessMode); setFiles($1('#sfUpload'), gr.files); }
      else if (gr.type === 'strsess') { pickRadio('sessMode', 'text', window.toggleSessMode); const x = $1('#sfText'); if (x) { x.value = gr.text.trim(); x.dispatchEvent(new Event('input', {bubbles: true})); } }
      else if (gr.type === 'tdata') { pickRadio('tdataMode', 'upload', window.toggleTdataMode); setFiles($1('#tUpload'), gr.files); }
      else if (gr.type === 'phones') { const x = $1('#iText'); if (x) { x.value = gr.text.trim(); try { previewImport(); } catch (_) {} x.dispatchEvent(new Event('input', {bubbles: true})); } }
      else if (gr.type === 'single') { const x = $1('#aPhone'); if (x) x.value = gr.text.trim(); }
      const m = document.getElementById(t.modal);
      if (m && !m.querySelector('.fx-prefilled')) {
        const n = document.createElement('div'); n.className = 'fx-prefilled';
        n.innerHTML = '✓ 已识别为<b>' + t.name + '</b>并帮你填好，检查一下标签、代理后点导入即可';
        const body = m.querySelector('.body'); body && body.prepend(n);
      }
    }, 30);
  }
  function openSmartImport() { ensureSmart(); closeAll(); smart._files = []; smart.querySelector('.fx-smart-text').value = ''; smart.querySelector('input[type=file]').value = ''; groups = []; analyze([], ''); openModal('mSmartImport'); setTimeout(() => smart.querySelector('.fx-smart-drop').focus(), 60); }
  // 关闭弹窗时去掉“已帮你填好”提示，避免下次手动打开还显示
  TAO_HOOK.after('closeAll', function (value) {
    document.querySelectorAll('.fx-prefilled').forEach(n => n.remove());
    return value;
  }, 'fx8:close-all');

  /* =================== 4 · 新手引导清单 =================== */
  function guide() {
    const rail = document.querySelector('.rail'); if (!rail) return;
    let card = rail.querySelector('.fx-guide');
    if (LS.get('tao.guide.off') === '1') { if (card) card.remove(); return; }
    const acc = typeof accounts !== 'undefined' ? accounts : [];
    const steps = [
      {t: '连接后端', d: '启动 tam.cli serve，用它给的地址打开本页', ok: !DEMO && !(document.getElementById('connBar') || {classList: {contains: () => false}}).classList.contains('on'), run: () => openHelp()},
      {t: '导入第一个账号', d: '拖入 session / tdata，或粘贴手机号', ok: acc.length > 0 && !DEMO, run: openSmartImport},
      {t: '登录账号', d: '点行内「登录」，或勾选后批量自动登录', ok: !DEMO && acc.some(a => a.authorized), run: () => setQuickFilter('noauth')},
      {t: '做一次健康检查', d: '同步状态、二验和限制情况', ok: !DEMO && LS.get('tao.guide.checked') === '1', run: () => { if (typeof visible === 'function') { visible().forEach(a => sel.add(a.id)); render(); } }}
    ];
    const done = steps.filter(s => s.ok).length;
    if (done === steps.length) { if (card) card.remove(); return; }
    const html = '<div class="fx-guide-h"><b>上手指南</b><span>' + done + ' / ' + steps.length + '</span><button type="button" class="fx-guide-x" aria-label="不再显示">✕</button></div>' +
      '<div class="fx-guide-bar"><i style="width:' + (done / steps.length * 100) + '%"></i></div>' +
      steps.map((s, i) => '<button type="button" class="fx-guide-step' + (s.ok ? ' ok' : '') + '" data-i="' + i + '"><span class="ck">' + (s.ok ? '✓' : i + 1) + '</span><span class="tx"><b>' + s.t + '</b><small>' + s.d + '</small></span></button>').join('') +
      (DEMO ? '<div class="fx-guide-demo">当前是演示数据，连接后端后这里会按你的真实进度打勾。</div>' : '');
    if (!card) { card = document.createElement('section'); card.className = 'fx-guide'; card.setAttribute('aria-label', '上手指南'); rail.prepend(card); }
    if (card._h !== html) {
      card._h = html; card.innerHTML = html;
      card.querySelector('.fx-guide-x').onclick = () => { LS.set('tao.guide.off', '1'); card.remove(); toast('已隐藏上手指南，可在「更多」菜单重新打开'); };
      card.querySelectorAll('.fx-guide-step').forEach(b => b.onclick = () => steps[+b.dataset.i].run());
    }
  }
  try {
    TAO_HOOK.around('bulk', function (next, args) {
      if (args[0] === 'check') LS.set('tao.guide.checked', '1');
      return next(...args);
    }, 'fx8:guide-bulk');
    TAO_HOOK.around('check', function (next, args) {
      LS.set('tao.guide.checked', '1');
      return next(...args);
    }, 'fx8:guide-check');
  } catch (_) {}

  /* =================== 5 · 术语解释 =================== */
  const TERMS = [
    [/StringSession/, 'StringSession：把登录会话压成一长串文本，粘贴就能导入，不需要文件。'],
    [/tdata/i, 'tdata：电脑版 Telegram 的数据文件夹，里面保存着已登录的账号。打成 zip 上传即可。'],
    [/\.session|session/i, 'session：登录凭证文件。有它就不用重新收验证码。'],
    [/passcode|本地密码/, '本地密码：电脑版 Telegram 设置的锁屏密码，不是账号密码。没设置就留空。'],
    [/两步验证|二验|云密码/, '两步验证（二验）：Telegram 的第二层密码，登录时除了验证码还要输入它。'],
    [/取码链接/, '取码链接：接码平台提供的网址，打开能看到发给这个手机号的验证码，用于自动登录。'],
    [/代理/, '代理：让每个账号走不同的网络出口，降低关联和封号风险。格式如 socks5://host:1080。'],
    [/变体/, '变体：用 {你好|在吗} 这样的写法，每条消息随机挑一个，避免内容完全一样被判为群发。']
  ];
  function annotate(root) {
    (root || document).querySelectorAll('.modal label.f>span:first-child, .modal h3, .modal label>span').forEach(sp => {
      if (sp.dataset.fxTerm || sp.closest('.fx-smart')) return;
      sp.dataset.fxTerm = '1';
      const txt = sp.textContent;
      const hit = TERMS.find(([re]) => re.test(txt)); if (!hit) return;
      const q = document.createElement('i'); q.className = 'fx-q'; q.tabIndex = 0; q.textContent = '?'; q.dataset.tip = hit[1];
      q.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); });
      sp.appendChild(q);
    });
  }

  /* =================== 6 · 高级参数收起 =================== */
  function wrapAdvanced(parent, nodes, title) {
    nodes = nodes.filter(Boolean); if (!parent || !nodes.length || parent.querySelector('.fx-adv')) return;
    const d = document.createElement('details'); d.className = 'fx-adv';
    d.innerHTML = '<summary>' + title + '</summary><div class="fx-adv-body"></div>';
    nodes[0].before(d); const b = d.querySelector('.fx-adv-body'); nodes.forEach(n => b.appendChild(n));
  }
  const lab = id => { const el = document.getElementById(id); return el ? el.closest('label') : null; };
  wrapAdvanced($1('#mLogin .body'), [lab('lQueryDelay'), lab('lPollInterval'), lab('lAdaptiveTiming')], '高级选项 · 等待与轮询（一般不用改）');
  wrapAdvanced($1('#mImport .body'), [lab('iProxyPool'), lab('iProxyCsv'), lab('iLinksPerProxy'), document.getElementById('iCapacity')], '代理池设置（可选）');

  /* 设置页：搜索框（搜索框位置保持原样，过滤只作用于真实参数行） */
  function applySettingsFilter(value) {
    const box = document.getElementById('setBody');
    if (!box) return;
    const input = document.querySelector('.fx-set-search input');
    const q = String(value == null ? (input?.value || '') : value).trim().toLowerCase();
    const terms = q.split(/\s+/).filter(Boolean);
    const rows = [...box.querySelectorAll('.row')];
    rows.forEach(row => {
      const hay = ((row.dataset.kw || '') + ' ' + (row.textContent || '')).toLowerCase();
      row.style.display = !terms.length || terms.every(term => hay.includes(term)) ? '' : 'none';
    });
    let heading = null, groupRows = [], secretHint = null;
    const flush = () => {
      if (!heading) return;
      const shown = groupRows.some(row => row.style.display !== 'none');
      heading.style.display = shown ? '' : 'none';
      if (secretHint) secretHint.style.display = shown ? '' : 'none';
    };
    [...box.children].forEach(child => {
      if (child.matches?.('h4, .fx-set-h')) {
        flush(); heading = child; groupRows = []; secretHint = null;
      } else if (child.classList?.contains('fx-set-secret-hint')) {
        secretHint = child;
      } else if (child.matches?.('.row, .fx-set-row')) {
        groupRows.push(child);
      }
    });
    flush();
  }
  globalThis.applySettingsFilter = applySettingsFilter;
  function settingsSearch() {
    const host = document.querySelector('section[data-view="settings"], div[data-view="settings"]');
    if (!host) return;
    const existing = document.querySelector('.fx-set-search input');
    if (existing) { applySettingsFilter(existing.value); return; }
    const w = document.createElement('div'); w.className = 'fx-set-search';
    w.innerHTML = '<svg class="i"><use href="#i-search"/></svg><input type="search" placeholder="搜索参数，例如「并发」「代理」「重试」" aria-label="搜索参数" />';
    const first = host.querySelector('.fx-set-row, .set-row, label.f');
    (first ? first.parentElement : host).before(w);
    const inp = w.querySelector('input');
    inp.addEventListener('input', () => applySettingsFilter(inp.value));
    applySettingsFilter(inp.value);
  }

  /* =================== 7 · 命令面板：按意图执行 =================== */
  function selectWhere(fn) { sel.clear(); (accounts || []).filter(fn).forEach(a => sel.add(a.id)); render(); return sel.size; }
  const INTENTS = [
    {id: 'fx-smart-import', g: '常用', label: '添加账号（自动识别格式）', icon: 'plus', kw: 'add import 添加 导入 新增 session tdata 手机号 拖入', run: () => openSmartImport()},
    {id: 'fx-login-all', g: '常用', label: '登录所有未登录的账号', icon: 'key', kw: 'login all 登录 全部 未授权 未登录 失效', run: () => { if (!selectWhere(a => !a.authorized)) return toast('没有未登录的账号'); bulk('auto'); }},
    {id: 'fx-check-all', g: '常用', label: '检查所有已登录账号', icon: 'pulse', kw: 'check all 检查 体检 健康 全部', run: () => { if (!selectWhere(a => a.authorized)) return toast('没有已登录的账号'); bulk('check'); }},
    {id: 'fx-select-bad', g: '常用', label: '选中所有封禁 / 冻结账号', icon: 'trash', kw: 'banned frozen 封禁 冻结 清理 删除', run: () => { const n = selectWhere(a => /banned|frozen/.test(a.status)); toast(n ? '已选中 ' + n + ' 个，可在底部操作栏处理' : '没有封禁或冻结的账号'); }},
    {id: 'fx-calm', g: '界面', label: '专注模式：开 / 关装饰动效', icon: 'spark', kw: 'focus calm 专注 简洁 动效 关闭特效', run: () => toggleCalm()},
    {id: 'fx-guide', g: '界面', label: '显示上手指南', icon: 'help', kw: 'guide onboarding 新手 指南 引导 教程', run: () => { LS.set('tao.guide.off', '0'); switchView('accounts'); guide(); }}
  ];
  try { INTENTS.reverse().forEach(x => { if (!ACTIONS.some(a => a.id === x.id)) ACTIONS.unshift(x); }); } catch (_) {}
  const pi = document.getElementById('palInput'); if (pi) pi.placeholder = '想做什么？例如「登录所有未登录的账号」「添加账号」「+86」';

  /* =================== 8 · 专注模式 =================== */
  function setCalm(on) {
    document.documentElement.classList.toggle('fx-calm', on); LS.set('tao.calm', on ? '1' : '0');
    const b = document.querySelector('.fx-calm-item'); if (b) b.querySelector('small').textContent = on ? '已开启' : '已关闭';
  }
  function toggleCalm() { const on = !document.documentElement.classList.contains('fx-calm'); setCalm(on); toast(on ? '专注模式已开启：装饰动效已关闭' : '专注模式已关闭'); }
  setCalm(LS.get('tao.calm') === '1');
  if (LS.get('tao.calm') === '1') setTimeout(() => { const pi = document.querySelector('.fx-pintro'); if (pi) pi.click(); }, 50);

  /* =================== 入口挂载 =================== */
  // 添加账号菜单：顶部加智能入口，原入口降为“按格式导入”
  const am = document.getElementById('addMenu');
  if (am && !am.querySelector('.fx-smart-item')) {
    am.insertAdjacentHTML('afterbegin', '<button type="button" class="menu-item fx-smart-item" role="menuitem"><svg class="i"><use href="#i-plus"/></svg><span><b>添加账号</b><small>拖入文件或粘贴，自动识别格式</small></span><em>推荐</em></button><div class="menu-title fx-menu-sub">按格式导入</div>');
    am.querySelector('.fx-smart-item').onclick = () => { closeMenus(); openSmartImport(); };
  }
  // 「一条龙处理」改名，说明它做什么
  const os = document.querySelector('.tao-one-stop');
  if (os) { const tn = [...os.childNodes].find(n => n.nodeType === 3 && n.textContent.trim()); if (tn) tn.textContent = '一键处理'; os.removeAttribute('title'); os.dataset.tip = '一键完成：登录 → 检查 → 导出可用账号'; }
  // “更多”菜单：专注模式 + 上手指南
  const mm = [...document.querySelectorAll('.menu')].find(m => m.querySelector('[data-onclick*="openHelp()"]'));
  if (mm && !mm.querySelector('.fx-calm-item')) {
    const help = mm.querySelector('[data-onclick*="openHelp()"]');
    help.insertAdjacentHTML('afterend', '<button type="button" class="menu-item fx-calm-item" role="menuitem"><svg class="i"><use href="#i-spark"/></svg>专注模式<small>' + (document.documentElement.classList.contains('fx-calm') ? '已开启' : '已关闭') + '</small></button><button type="button" class="menu-item fx-guide-item" role="menuitem"><svg class="i"><use href="#i-help"/></svg>上手指南</button>');
    mm.querySelector('.fx-calm-item').onclick = () => { closeMenus(); toggleCalm(); };
    mm.querySelector('.fx-guide-item').onclick = () => { closeMenus(); LS.set('tao.guide.off', '0'); switchView('accounts'); guide(); };
  }
  // 空列表：只留一个主入口
  TAO_HOOK.result('emptyRowHtml', function (h) {
    if ((typeof accounts !== 'undefined' ? accounts : []).length) return h;
    return h.replace(/<div class="empty-actions">[\s\S]*?<\/div>/, '<div class="empty-actions"><button class="primary" data-onclick="TAO_FX8.openSmartImport()">＋ 添加账号</button><button class="fit" data-onclick="openHelp()">看看怎么用</button></div>').replace('还没有账号', '还没有账号 · 把 session、tdata 或手机号清单拖进来就能开始');
  }, 'fx8:empty');


  /* 浮动提示（data-tip） */
  const tipEl = document.createElement('div'); tipEl.className = 'fx-tip'; tipEl.id = 'fxTip'; tipEl.setAttribute('role', 'tooltip'); tipEl.hidden = true; document.body.appendChild(tipEl);
  let tipT = 0;
  const showTip = el => {
    clearTimeout(tipT);
    tipT = setTimeout(() => {
      tipEl.textContent = el.dataset.tip; tipEl.hidden = false; tipEl.classList.add('on'); el.setAttribute('aria-describedby', 'fxTip');
      const r = el.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
      tipEl.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + 'px';
      tipEl.style.top = (r.top - h - 10 < 8 ? r.bottom + 10 : r.top - h - 10) + 'px';
    }, 180);
  };
  const hideTip = () => { clearTimeout(tipT); tipEl.classList.remove('on'); tipEl.hidden = true; };
  document.addEventListener('mouseover', e => { const el = e.target.closest && e.target.closest('[data-tip]'); el ? showTip(el) : hideTip(); });
  document.addEventListener('focusin', e => { const el = e.target.closest && e.target.closest('[data-tip]'); el ? showTip(el) : hideTip(); });
  document.addEventListener('focusout', hideTip);
  addEventListener('scroll', hideTip, true);

  /* 钩子 */
  TAO_HOOK.after('render', function (value) { try { enhanceRows(); recommend(); guide(); } catch (e) { console.warn(e); } return value; }, 'fx8:render');
  TAO_HOOK.after('syncSelectionUi', function (value) { try { recommend(); } catch (_) {} return value; }, 'fx8:selection');
  TAO_HOOK.after('openModal', function (value) { try { annotate(); } catch (_) {} return value; }, 'fx8:modal');
  TAO_HOOK.after('switchView', function (value, args) { if (args[0] === 'settings') setTimeout(settingsSearch, 400); return value; }, 'fx8:view');
  annotate();
  try { enhanceRows(); recommend(); guide(); } catch (_) {}

  // 全局：拖文件到页面任意处 → 打开智能导入（没有弹窗打开时）
  let dragN = 0;
  addEventListener('dragenter', e => {
    if (!e.dataTransfer || ![...e.dataTransfer.types].includes('Files')) return;
    if (document.querySelector('.modal.on') || (typeof readonly !== 'undefined' && readonly)) return;
    if (++dragN === 1) document.documentElement.classList.add('fx-dragging');
  });
  addEventListener('dragleave', () => { if (--dragN <= 0) { dragN = 0; document.documentElement.classList.remove('fx-dragging'); } });
  addEventListener('dragover', e => { if (document.documentElement.classList.contains('fx-dragging')) e.preventDefault(); });
  addEventListener('drop', e => {
    if (!document.documentElement.classList.contains('fx-dragging')) return;
    e.preventDefault(); dragN = 0; document.documentElement.classList.remove('fx-dragging');
    const f = [...(e.dataTransfer.files || [])]; if (!f.length) return;
    openSmartImport(); smart._files = f; analyze(f, '');
  });

  return {tipFor, ST_HINT, openSmartImport, toggleCalm, guide, analyze: (f, t) => { ensureSmart(); return analyze(f, t); }};
})();

/* =================== 混合 ZIP 导入（POST /api/import/mixed，multipart，dry_run 先分析） =================== */
/*
 * 混合导入故意不复用 api()：api() 会为 JSON 请求补 Content-Type，
 * 而这里必须让浏览器为 FormData 自动生成 multipart boundary。
 */
function openMixed(files) {
  let m = document.getElementById('mMixed');
  const esc = v => String(v == null ? '' : v).replace(/[&<>\"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;'}[c]));
  const text = (v, fallback = '') => String(v == null || v === '' ? fallback : v);
  const describe = (code, detail, retry) => {
    try { return typeof TAO_ERR !== 'undefined' && TAO_ERR && TAO_ERR.describe ? TAO_ERR.describe(code, detail, retry) : text(detail || code, '导入失败'); }
    catch (_) { return text(detail || code, '导入失败'); }
  };
  const value = id => {
    const el = document.getElementById(id);
    return el && typeof el.value === 'string' ? el.value.trim() : '';
  };
  const options = () => ({tags: value('mxTags'), proxy: value('mxProxy'), label: value('mxLabel'), tdata_passcode: value('mxPass'), twofa: value('mxTwofa')});
  const fingerprint = () => JSON.stringify(options());
  if (!m) {
    m = document.createElement('div'); m.className = 'modal modal-wide fx-mixed'; m.id = 'mMixed';
    m.setAttribute('role', 'dialog'); m.setAttribute('aria-modal', 'true'); m.setAttribute('aria-labelledby', 'mMixedH');
    m.innerHTML = '<h3 id="mMixedH">导入混合压缩包</h3><div class="body">' +
      '<p class="muted">压缩包里的 tdata 和 .session 会自动拆开导入。先分析一遍，确认没问题再导入。</p>' +
      '<div class="fx-mx-files"></div>' +
      '<div class="row"><label class="f" style="flex:1 1 180px"><span>标签</span><input id="mxTags" placeholder="多个用逗号分隔" /></label>' +
      '<label class="f" style="flex:1 1 220px"><span>代理</span><input id="mxProxy" placeholder="socks5://host:1080" /></label></div>' +
      '<details class="fx-adv"><summary>更多选项</summary><div class="row">' +
      '<label class="f" style="flex:1 1 180px"><span>别名前缀</span><input id="mxLabel" /></label>' +
      '<label class="f" style="flex:1 1 180px"><span>tdata 本地密码（如有）</span><input id="mxPass" type="password" autocomplete="new-password" /></label>' +
      '<label class="f" style="flex:1 1 180px"><span>二验密码（导入后加密保存）</span><input id="mxTwofa" type="password" autocomplete="new-password" /></label></div></details>' +
      '<div class="fx-mx-res" aria-live="polite"></div></div>' +
      '<div class="foot"><button type="button" data-onclick="closeAll()">取消</button><button type="button" class="primary fx-mx-go">分析</button></div>';
    document.body.appendChild(m);
  }
  const res = m.querySelector('.fx-mx-res'), btn = m.querySelector('.fx-mx-go');
  m._files = (files || []).filter(f => f && /\.zip$/i.test(f.name || ''));
  m.querySelector('.fx-mx-files').innerHTML = m._files.map(f => '<span class="fx-mx-f">' + esc(f.name) + ' · ' + (Number(f.size || 0) / 1048576).toFixed(1) + ' MB</span>').join('');
  res.innerHTML = ''; btn.disabled = !m._files.length; btn.textContent = '分析'; m._stage = 'analyze'; m._fingerprint = fingerprint();
  const ST = {ready: ['待导入', 'ok'], imported: ['已导入', 'ok'], duplicate: ['已存在', 'warn'], failed: ['失败', 'err'], skipped: ['忽略', 'warn']};
  const formData = (f, dry) => {
    const fd = new FormData(); fd.append('file', f); fd.append('dry_run', dry ? 'true' : 'false');
    const o = options();
    Object.keys(o).forEach(k => { if (o[k]) fd.append(k, o[k]); });
    return fd;
  };
  async function send(f, dry) {
    if (typeof DEMO !== 'undefined' && DEMO) {
      await new Promise(r => setTimeout(r, 120));
      const it = [{path: '8613800000001/tdata', kind: 'tdata', phone: '+8613800000001', status: dry ? 'ready' : 'imported'},
        {path: 'a.session', kind: 'session', phone: '+447700900001', status: dry ? 'ready' : 'imported'},
        {path: 'b.session', kind: 'session', phone: '+447700900002', status: 'duplicate', code: 'import_duplicate', params: {existing_id: 42}},
        {path: 'locked/tdata', kind: 'tdata', status: 'failed', code: 'tdata_passcode'}];
      return {dry_run: dry, total: it.length, tdata: {found: 2}, session: {found: 2}, items: it, skipped: [{path: 'readme.txt', code: 'import_unknown_format'}]};
    }
    const tokenEl = typeof $ === 'function' ? $('#token') : document.getElementById('token');
    const token = tokenEl && tokenEl.value ? tokenEl.value : '';
    const headers = token ? {'Authorization': 'Bearer ' + token} : {};
    const r = await fetch('/api/import/mixed', {method: 'POST', body: formData(f, dry), headers});
    const raw = await r.text(); let j = null; try { j = JSON.parse(raw); } catch (_) {}
    if (!r.ok) {
      const e = new Error(r.status === 404 || r.status === 405
        ? '后端还不支持混合导入，请更新后端；或先用「工具 → ZIP 拆包」分成两个压缩包'
        : r.status === 413
          ? '文件太大，超过后端上限；可调大 TAM_MIXED_MAX_UPLOAD_MB 后重试'
          : describe(j && j.code, j ? j.detail : raw, j && j.retry_after));
      e.status = r.status; e.code = j && j.code; e.detail = j && j.detail; e.params = j && j.params; e.retry_after = j && j.retry_after;
      throw e;
    }
    return j || {};
  }
  function countStatus(items, status) { return items.filter(x => String(x.status || '').toLowerCase() === status).length; }
  function draw(all, dry, uploadErrors = []) {
    const items = all.flatMap(r => Array.isArray(r.items) ? r.items : []);
    const skipped = all.flatMap(r => Array.isArray(r.skipped) ? r.skipped : []);
    const ready = countStatus(items, 'ready'), imp = countStatus(items, 'imported'), dup = countStatus(items, 'duplicate'), bad = countStatus(items, 'failed');
    const serverCount = kind => all.reduce((n, r) => n + (Number(r && r[kind] && (dry ? r[kind].found : (r[kind].imported != null ? r[kind].imported : r[kind].found))) || 0), 0);
    const kinds = kind => serverCount(kind) || items.filter(x => x.kind === kind).length;
    const chip = (t, n, c) => '<span class="fx-mx-chip ' + c + '">' + esc(t) + ' ' + n + '</span>';
    const why = x => {
      let d = x.reason || x.detail || x.error || '';
      if (!d && x.code) d = describe(x.code, '', x.retry_after);
      if (x.code === 'import_duplicate') {
        const id = x.params && x.params.existing_id != null ? x.params.existing_id : x.existing_id;
        if (id != null && !String(d).includes('#' + id)) d += '（#' + id + '）';
      }
      return d;
    };
    const visible = items.concat(skipped.map(x => Object.assign({kind: 'file', status: 'skipped'}, x))).concat(uploadErrors.map(x => ({kind: 'file', status: 'failed', path: x.file, reason: x.error})));
    const rows = visible.slice(0, 300).map(x => {
      const s = ST[x.status] || [text(x.status, '—'), ''];
      return '<tr><td>' + esc(x.kind === 'tdata' ? 'tdata' : x.kind === 'session' ? 'session' : '文件') + '</td><td>' + esc(x.phone || x.path || '—') + '</td><td><span class="fx-mx-st ' + s[1] + '">' + esc(s[0]) + '</span></td><td title="' + esc(x.path || '') + '">' + esc(why(x) || x.path || '—') + '</td></tr>';
    }).join('');
    const shown = dry ? ready : imp;
    res.innerHTML = '<div class="fx-mx-sum">' + chip('tdata', kinds('tdata'), '') + chip('session', kinds('session'), '') +
      chip(dry ? '可导入' : '已导入', shown, 'ok') + chip('已存在', dup, 'warn') + chip('失败', bad, 'err') + chip('上传失败', uploadErrors.length, uploadErrors.length ? 'err' : '') + chip('忽略的文件', skipped.length, skipped.length ? 'warn' : '') + '</div>' +
      (visible.length ? '<div class="fx-mx-tw"><table class="fx-mx-t"><thead><tr><th>类型</th><th>手机号/文件</th><th>状态</th><th>说明</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '<p class="muted">没找到可导入的账号。</p>') +
      (visible.length > 300 ? '<p class="muted">只显示前 300 条，完整结果已保留在日志。</p>' : '');
    return {ready, imp, bad, dup, total: items.length, uploadErrors: uploadErrors.length};
  }
  const run = async () => {
    const dry = m._stage === 'analyze';
    if (m._stage === 'done') { closeAll(); return; }
    if (!m._files.length) { res.innerHTML = '<p class="fx-mx-err">请选择 .zip 文件。</p>'; return; }
    if (!dry && m._fingerprint !== fingerprint()) { m._stage = 'analyze'; btn.textContent = '重新分析'; res.innerHTML = '<p class="fx-mx-err">导入选项已变化，请重新分析。</p>'; return; }
    btn.disabled = true; btn.textContent = dry ? '分析中…' : '导入中…';
    try {
      const all = [], uploadErrors = [];
      for (const f of m._files) {
        try { all.push(await send(f, dry)); }
        catch (e) { uploadErrors.push({file: f.name, error: e && e.message ? e.message : String(e)}); }
      }
      if (!all.length && uploadErrors.length) throw new Error(uploadErrors[0].error);
      const c = draw(all, dry, uploadErrors);
      if (dry) {
        m._fingerprint = fingerprint(); m._stage = c.ready ? 'import' : 'analyze';
        btn.textContent = c.ready ? '导入 ' + c.ready + ' 个账号' : '重新分析';
      } else {
        m._stage = 'done'; btn.textContent = '完成';
        toast('混合导入 · 成功 ' + c.imp + (c.bad ? '，失败 ' + c.bad : '') + (c.dup ? '，已存在 ' + c.dup : '') + (c.uploadErrors ? '，上传失败 ' + c.uploadErrors : ''), c.bad || c.uploadErrors ? 'err' : 'ok');
        try { await Promise.resolve(refresh()); } catch (_) {}
      }
    } catch (e) {
      const msg = e && e.message ? e.message : describe(e && e.code, e, e && e.retry_after);
      res.innerHTML = '<p class="fx-mx-err">' + esc(msg) + '</p>'; btn.textContent = dry ? '重新分析' : '重试导入';
    } finally { btn.disabled = false; }
  };
  btn.onclick = run;
  m.querySelectorAll('input').forEach(i => i.oninput = () => { if (m._stage === 'import') { m._stage = 'analyze'; btn.textContent = '重新分析'; res.innerHTML = '<p class="muted">导入选项已变化，请重新分析。</p>'; } });
  closeAll(); openModal('mMixed');
}

/* =================== 严格审查后的修补（无障碍 / 键盘） =================== */
(() => {
  const run = fn => { try { fn(); } catch (_) {} };
  /* AI 面板隐藏时设 inert，避免 Tab 进入不可见区域 */
  run(() => {
    const ai = document.getElementById('aiPanel'); if (!ai) return;
    const sync = () => { ai.inert = ai.getAttribute('aria-hidden') === 'true'; };
    sync(); new MutationObserver(sync).observe(ai, {attributes: true, attributeFilter: ['aria-hidden']});
  });
  /* 地标 / 名称 */
  run(() => { const d = document.getElementById('demoBar'); if (d) d.setAttribute('role', 'status'); });
  run(() => { const b = document.getElementById('taoBrand'); if (b) b.setAttribute('aria-label', 'TAO Workspace · 回到账号清单'); });
  run(() => { const c = document.querySelector('.cmdk'); if (c) c.setAttribute('aria-label', c.textContent.replace(/\s+/g, ' ').trim() || '搜索账号或操作…'); });
  run(() => {
    const s = document.querySelector('.status-btn'); if (!s) return;
    const upd = () => { const m = document.getElementById('mode'); s.setAttribute('aria-label', (m ? m.textContent.trim() + ' · ' : '') + '连接状态与体检'); };
    upd(); const m = document.getElementById('mode'); if (m) new MutationObserver(upd).observe(m, {childList: true, characterData: true, subtree: true});
  });
  run(() => {
    if ([...document.querySelectorAll('h1')].some(h => h.offsetParent !== null && !h.closest('.drawer,.modal,[aria-hidden="true"]'))) return;
    const h = document.createElement('p'); h.className = 'sr-only'; h.textContent = 'TAO · Telegram 多账号管理';
    const main = document.querySelector('main') || document.body; main.prepend(h);
  });
  /* 勾选框：整格可点（命中区域 ≥ 24px） */
  run(() => {
    const tb = document.getElementById('tb'); if (!tb) return;
    tb.addEventListener('click', e => {
      const td = e.target.closest('td'); if (!td || e.target.matches('input')) return;
      const cb = td.querySelector(':scope > input.row-pick'); if (cb) { e.stopPropagation(); cb.click(); }
    }, true);
  });
  /* 自绘对话框（星图 / 任务明细 / 结果浮窗）的焦点约束 */
  document.addEventListener('keydown', e => {
    if (e.key !== 'Tab') return;
    const box = document.querySelector('.fx-stars.on') || document.querySelector('.fx-sheet.on');
    if (!box) return;
    const f = [...box.querySelectorAll('button:not(:disabled),input:not(:disabled),[href],[tabindex]:not([tabindex="-1"])')].filter(x => x.offsetParent !== null || getComputedStyle(x).position === 'fixed');
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
})();

/* ---------- 后端新接口兼容层（P0/P1） ---------- */
/*
 * 这一层只负责把新后端能力接到现有按钮：
 * - 工具箱批量只发送 account_ids，并按 10~50 个账号分块；
 * - 健康检查、自动登录、预热和工具箱执行前先调用批量预览；
 * - 账号列表在首次全量后使用 updated_since 增量合并，五分钟强制全量；
 * - 旧后端返回 404/405/422 时保留原来的逐账号执行路径。
 */
const TAO_BE = (() => {
  const MISSING = /\b(?:404|405|422)\b|not found|method not allowed|unprocessable|旧版本/i;
  const isDemo = () => typeof DEMO !== 'undefined' && DEMO;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const lab = id => typeof accLabel === 'function' ? accLabel(id) : '#' + id;
  const isLegacyError = e => MISSING.test(String(e && (e.message || e))) || [404, 405, 422].includes(Number(e && e.status));
  const ERR = typeof TAO_ERR !== 'undefined' ? TAO_ERR : {CN: {}, describe: (code, detail) => String(detail || '请求失败')};
  const describeError = (e, fallback) => {
    const code = e && e.code;
    const detail = e && (e.detail != null ? e.detail : e.message);
    return ERR.describe(code, detail || fallback, e && e.retry_after);
  };

  /* ---------- 批量预览 ---------- */
  let previewOff = false;
  async function preview(op, ids, params) {
    const list = (ids || []).map(Number).filter(Number.isFinite);
    if (previewOff || isDemo() || !list.length) return null;
    try {
      return await api('/api/batch/preview', {
        method: 'POST',
        body: JSON.stringify({op, ids: list, params: params || {}}),
      });
    } catch (e) {
      if (isLegacyError(e)) previewOff = true;
      return null;
    }
  }

  /* 返回真正执行的账号；null 表示用户取消或全部被跳过。 */
  async function gate(op, ids, params, title) {
    const original = (ids || []).map(Number).filter(Number.isFinite);
    const p = await preview(op, original, params);
    if (!p || !Array.isArray(p.skip) || !p.skip.length) return {ids: original, skip: []};
    const skip = p.skip.map(x => ({...x, id: Number(x.id)}));
    const run = Array.isArray(p.run) ? p.run.map(Number).filter(Number.isFinite) : original.filter(id => !skip.some(x => x.id === id));
    const groups = {};
    skip.forEach(x => {
      const key = x.reason || ERR.CN[x.reason_code] || x.reason_code || '不满足条件';
      (groups[key] ||= []).push(x.id);
    });
    const lines = Object.entries(groups).map(([reason, group]) => {
      const names = group.slice(0, 5).map(lab).join('、');
      return '· ' + reason + ' × ' + group.length + '（' + names + (group.length > 5 ? ' 等' : '') + '）';
    });
    if (!run.length) {
      toast('选中的 ' + original.length + ' 个账号都不满足条件：' + Object.keys(groups).join('、'), 'err');
      return null;
    }
    const ok = await uiConfirm({
      title: (title || '批量操作') + ' · 将跳过 ' + skip.length + ' 个',
      message: '以下账号会被跳过：\n' + lines.join('\n') + '\n\n继续处理其余 ' + run.length + ' 个？',
      okText: '继续 ' + run.length + ' 个',
    });
    return ok ? {ids: run, skip} : null;
  }

  /* ---------- 工具箱批量 ---------- */
  let toolboxContext = null;
  if (typeof tbCollect === 'function') {
    TAO_HOOK.after('tbCollect', function (params, args) {
      const spec = args[0];
      toolboxContext = {op: spec && spec.op, params, at: Date.now()};
      return params;
    }, 'be:toolbox-context');
  }
  const normalizeResult = row => {
    const status = String(row && row.status || '').toLowerCase();
    const ok = row && (row.ok === true || row.ok === 1 || ['ok', 'done', 'success'].includes(status));
    return {
      account_id: Number(row && (row.account_id != null ? row.account_id : row.id)),
      ok: !!ok,
      result: row && row.result != null ? row.result : (ok ? (row.detail || '') : undefined),
      code: row && row.code ? row.code : (!ok ? 'internal_error' : undefined),
      retry_after: row && row.retry_after != null ? row.retry_after : undefined,
      error: ok ? undefined : ERR.describe(row && row.code,
        (row && (row.error || row.detail || row.reason)) || (status === 'skipped' ? '已跳过' : '失败'),
        row && row.retry_after),
    };
  };

  async function serverBatch(op, ids, params, title, concurrency) {
    const task = uiTask(title);
    const conc = Math.max(1, Number(concurrency) || 1);
    const size = Math.max(10, Math.min(50, conc * 4));
    const rows = [];
    let ok = 0;
    try {
      for (let i = 0; i < ids.length; i += size) {
        const chunk = ids.slice(i, i + size);
        task.update({title, current: i, total: ids.length, text: i + ' / ' + ids.length + ' · 成功 ' + ok + ' · 服务端执行中…'});
        let response;
        try {
          response = await api('/api/toolbox/' + encodeURIComponent(op) + '/batch', {
            method: 'POST',
            body: JSON.stringify({account_ids: chunk, params: params || {}, concurrency: conc}),
          });
        } catch (e) {
          if (i === 0) throw e;
          chunk.forEach(id => rows.push({account_id: id, ok: false, code: e.code || 'internal_error',
            retry_after: e.retry_after || undefined, error: describeError(e, '服务端执行失败')}));
          continue;
        }
        const got = response && Array.isArray(response.results) ? response.results.map(normalizeResult) : [];
        const seen = new Set(got.map(x => x.account_id));
        chunk.forEach(id => { if (!seen.has(id)) got.push({account_id: id, ok: false,
          code: 'internal_error', error: ERR.describe('internal_error', '后端未返回结果')}); });
        got.forEach(row => { rows.push(row); if (row.ok) ok++; });
      }
      task.update({title, current: ids.length, total: ids.length, status: 'done', text: '完成 · 成功 ' + ok + ' · 失败 ' + (rows.length - ok)});
      return {total: rows.length, ok, failed: rows.length - ok, results: rows};
    } finally {
      setTimeout(() => task.close(), 800);
    }
  }

  if (typeof runPool === 'function') {
    const pool = runPool;
    TAO_HOOK.around('runPool', async function (next, args) {
      const ctx = toolboxContext;
      toolboxContext = null;
      const ids = args[0], title = args[2], concurrency = args[3];
      const isToolbox = typeof title === 'string' && title.indexOf('工具箱 · ') === 0;
      if (!isToolbox || !ctx || !ctx.op || Date.now() - ctx.at > 120000 || isDemo()) return next(...args);
      const original = (ids || []).map(Number).filter(Number.isFinite);
      const gateResult = await gate('toolbox:' + ctx.op, original, ctx.params, title.replace('工具箱 · ', ''));
      if (!gateResult) return {total: 0, ok: 0, failed: 0, results: []};
      const skipped = gateResult.skip.map(x => ({account_id: x.id, ok: false, skipped: true,
        code: x.reason_code || 'internal_error',
        error: '已跳过：' + (x.reason || ERR.CN[x.reason_code] || x.reason_code || '不满足条件')}));
      const merge = result => {
        const out = result || {total: 0, ok: 0, failed: 0, results: []};
        out.results = (out.results || []).map(row => ({...row, ...normalizeResult(row)})).concat(skipped);
        out.total = Number(out.total || 0) + skipped.length;
        out.failed = Number(out.failed || 0) + skipped.length;
        return out;
      };
      try {
        return merge(await serverBatch(ctx.op, gateResult.ids, ctx.params, title, concurrency));
      } catch (e) {
        if (!isLegacyError(e)) throw e;
        const fallbackArgs = [...args];
        fallbackArgs[0] = gateResult.ids;
        return merge(await pool.apply(this, fallbackArgs));
      }
    }, 'be:toolbox-batch');
  }

  /* ---------- 批量检查 / 自动登录 / 预热：执行前预览 ---------- */
  async function gated(next, args, operation, title) {
      if (typeof sel === 'undefined' || !sel.size) return next(...args);
      const all = [...sel];
      const op = operation.apply(null, args);
      if (!op) return next(...args);
      const gateResult = await gate(op, all, {}, title.apply(null, args));
      if (!gateResult) return;
      if (gateResult.ids.length === all.length) return next(...args);
      sel.clear(); gateResult.ids.forEach(id => sel.add(id));
      let result;
      try { result = await next(...args); }
      finally { sel.clear(); all.forEach(id => sel.add(id)); }
      // 原操作的进度/任务仍只处理 run；这里补一份跳过行，避免结果表漏掉预览阶段的账号。
      const skipped = gateResult.skip.map(item => ({
        account_id: item.id,
        ok: false,
        skipped: true,
        code: item.reason_code || 'internal_error',
        error: '已跳过：' + (item.reason || ERR.CN[item.reason_code] || item.reason_code || '不满足条件'),
      }));
      if (result && typeof result === 'object') {
        if (Array.isArray(result.results)) {
          const previousTotal = Number(result.total || result.results.length);
          result.results = result.results.concat(skipped);
          result.total = previousTotal + skipped.length;
          result.failed = Number(result.failed || 0) + skipped.length;
        } else {
          result.skipped = skipped;
        }
        try { if (typeof out === 'function') out(result, true); } catch (_) {}
      }
      return result;
  }
  try {
    TAO_HOOK.around('bulk', function (next, args) {
      return gated(next, args, kind => ({check: 'check', auto: 'auto_login'})[kind] || null, kind => ({check: '健康检查', auto: '批量自动登录'})[kind] || '批量操作');
    }, 'be:preview-bulk');
    TAO_HOOK.around('bulkWarmup', function (next, args) {
      return gated(next, args, () => 'warmup', () => '批量预热');
    }, 'be:preview-warmup');
  } catch (_) {}

  /* ---------- 账号增量刷新 ---------- */
  const inc = {map: null, order: [], since: 0, lastFull: 0, off: false, busy: null};
  const FULL_EVERY = 300000;
  const rawApi = api;
  async function fullAccounts() {
    const response = await rawApi('/api/accounts');
    if (!Array.isArray(response)) return response;
    inc.map = new Map(response.map(account => [Number(account.id), account]));
    inc.order = response.map(account => Number(account.id));
    inc.lastFull = Date.now();
    const cursor = response.reduce((max, account) => Math.max(max, Number(account.updated_at || account.last_check_at || 0)), 0);
    inc.since = cursor ? cursor - 1 : Math.floor(Date.now() / 1000) - 120;
    return response;
  }
  function seedFromCurrent() {
    if (inc.map || typeof accounts === 'undefined' || !Array.isArray(accounts) || !accounts.length) return;
    inc.map = new Map(accounts.map(account => [Number(account.id), account]));
    inc.order = accounts.map(account => Number(account.id));
    inc.lastFull = Date.now();
    const cursor = accounts.reduce((max, account) => Math.max(max, Number(account.updated_at || account.last_check_at || 0)), 0);
    inc.since = cursor ? cursor - 1 : Math.floor(Date.now() / 1000) - 120;
  }
  TAO_HOOK.around('api', function (next, args) {
    const path = args[0], options = args[1];
    const opts = options || {};
    if (path !== '/api/accounts' || (opts.method && String(opts.method).toUpperCase() !== 'GET') || isDemo() || inc.off) return next(...args);
    if (inc.busy) return inc.busy;
    // 首次启动时 refresh() 可能已经完成了全量请求；用当前内存列表作为增量基线，避免第二次刷新又拉全量。
    seedFromCurrent();
    if (!inc.map || Date.now() - inc.lastFull >= FULL_EVERY) {
      inc.busy = fullAccounts().finally(() => { inc.busy = null; });
      return inc.busy;
    }
    inc.busy = (async () => {
      let response;
      try { response = await rawApi('/api/accounts?updated_since=' + encodeURIComponent(inc.since)); }
      catch (_) { return fullAccounts(); }
      if (Array.isArray(response)) { inc.off = true; inc.map = null; return response; }
      if (!response || !Array.isArray(response.accounts)) return fullAccounts();
      (response.deleted_ids || []).forEach(id => inc.map.delete(Number(id)));
      response.accounts.forEach(account => {
        const id = Number(account.id);
        if (!inc.map.has(id)) inc.order.push(id);
        inc.map.set(id, Object.assign({}, inc.map.get(id) || {}, account));
      });
      if (response.server_now) inc.since = Number(response.server_now) - 1;
      inc.order = inc.order.filter(id => inc.map.has(id));
      return inc.order.map(id => inc.map.get(id));
    })().finally(() => { inc.busy = null; });
    return inc.busy;
  }, 'be:accounts-increment');

  const apiObject = {preview, gate, reset: () => { inc.map = null; inc.lastFull = 0; inc.off = false; }, _inc: inc};
  if (typeof globalThis !== 'undefined') globalThis.TAO_BE = apiObject;
  return apiObject;
})();

/* ---------- 账号检查结果显示增强 ---------- */
(() => {
  const resultCn = {ok: '通过', warn: '通过，但账号受限', fail: '失败'};
  const agoText = ts => {
    if (!ts) return '';
    const now = Date.now() / 1000;
    const sec = Math.max(0, now - Number(ts));
    if (sec < 60) return '刚刚';
    if (sec < 3600) return Math.floor(sec / 60) + ' 分钟前';
    if (sec < 86400) return Math.floor(sec / 3600) + ' 小时前';
    return Math.floor(sec / 86400) + ' 天前';
  };
  const checkText = account => {
    if (!account || !account.last_check_at) return '尚未检查';
    const state = resultCn[account.last_check_result] || '待核对';
    return '上次检查：' + agoText(account.last_check_at) + ' · ' + state + (account.last_check_error ? ' · ' + String(account.last_check_error).slice(0, 120) : '');
  };
  function decorateRows() {
    const body = document.querySelector('#tb');
    if (!body) return;
    body.querySelectorAll('tr').forEach(row => {
      const match = String(row.getAttribute('data-onclick') || '').match(/openDrawer\((\d+)\)/);
      if (!match) return;
      const account = (typeof accounts !== 'undefined' ? accounts : []).find(x => Number(x.id) === Number(match[1]));
      if (!account || !account.last_check_at) return;
      const cell = row.querySelector('td:nth-child(2)');
      if (!cell || cell.querySelector('.tao-check-meta')) return;
      const meta = document.createElement('div');
      meta.className = 'tao-check-meta' + (account.last_check_result === 'fail' ? ' is-fail' : account.last_check_result === 'warn' ? ' is-warn' : '');
      meta.textContent = checkText(account);
      cell.appendChild(meta);
    });
  }
  function decorateDrawer(id) {
    const account = (typeof accounts !== 'undefined' ? accounts : []).find(x => Number(x.id) === Number(id));
    const body = document.querySelector('#dBody');
    if (!account || !body || !account.last_check_at || body.querySelector('.tao-check-note')) return;
    const note = document.createElement('div');
    note.className = 'tao-check-note' + (account.last_check_result === 'fail' ? ' is-fail' : account.last_check_result === 'warn' ? ' is-warn' : '');
    note.setAttribute('role', 'note');
    note.innerHTML = '<b>' + esc(checkText(account)) + '</b>' + (account.last_check_error ? '<small>' + esc(String(account.last_check_error).slice(0, 240)) + '</small>' : '');
    body.prepend(note);
  }
  TAO_HOOK.after('render', function (value) { try { decorateRows(); } catch (_) {} return value; }, 'status:rows');
  TAO_HOOK.after('openDrawer', function (value, args) {
    setTimeout(() => { try { decorateDrawer(args[0]); } catch (_) {} }, 0);
    return value;
  }, 'status:drawer');
  if (typeof document !== 'undefined') {
    const body = document.querySelector('#tb');
    if (body && typeof MutationObserver !== 'undefined') new MutationObserver(decorateRows).observe(body, {childList: true});
  }
  try { decorateRows(); } catch (_) {}
  if (typeof globalThis !== 'undefined') globalThis.TAO_CHECK_UI = {checkText, decorateRows, decorateDrawer};
})();

/* ===== UX polish pass (2026-10-11) ===== */
(() => {
  const inp = document.querySelector('.account-search input');
  if (inp) { inp.setAttribute('aria-label', inp.placeholder); inp.placeholder = '搜索账号、手机号…'; }
  const chips = document.getElementById('chips');
  const ovr = () => { if (!chips) return; const o = chips.scrollWidth > chips.clientWidth + 2; chips.classList.toggle('fx-ovr', o); chips.classList.toggle('fx-end', o && chips.scrollLeft + chips.clientWidth >= chips.scrollWidth - 4); };
  if (chips) { chips.addEventListener('scroll', ovr, {passive: true}); new ResizeObserver(ovr).observe(chips); new MutationObserver(ovr).observe(chips, {childList: true}); ovr(); }
  // 移动端指南：标记下一步
  const markNow = () => { document.querySelectorAll('.fx-guide').forEach(g => { const st = [...g.querySelectorAll('.fx-guide-step')]; const n = st.find(s => !s.classList.contains('ok')); st.forEach(s => { const w = s === n; if (s.classList.contains('fx-now') !== w) s.classList.toggle('fx-now', w); }); }); };
  const rail = document.querySelector('.rail');
  if (rail) { new MutationObserver(markNow).observe(rail, {childList: true, subtree: true, attributes: true, attributeFilter: ['class']}); markNow();
    if (matchMedia('(max-width:720px)').matches && rail.querySelector('.fx-guide')) requestAnimationFrame(() => { rail.scrollLeft = 0; }); }
})();
/* 抽屉：状态说明卡 + 建议操作；国旗 emoji（Windows 显示为字母）去掉；「已授权」改成人话 */
(() => {
  if (typeof openDrawer !== 'function') return;
  const FLAG = /[\u{1F1E6}-\u{1F1FF}]{2}/gu;
  const ACT = {
    unauthorized: ['重新登录', id => startLogin(id)], new: ['登录', id => startLogin(id)],
    restricted: ['重新检查', id => check(id)], spam_block: ['重新检查', id => check(id)], spam_block_perm: ['重新检查', id => check(id)],
    flood_wait: ['稍后重新检查', id => check(id)], error: ['重新检查', id => check(id)], active: ['检查', id => check(id)]
  };
  const tone = st => st === 'active' ? 'ok' : /^(banned|frozen|deleted|unauthorized)$/.test(st) ? 'bad' : st === 'new' ? 'info' : 'warn';
  function decorate(id) {
    const body = document.getElementById('dBody'); if (!body) return;
    const a = (typeof accounts !== 'undefined' ? accounts : []).find(x => x.id === id); if (!a) return;
    body.querySelectorAll('.fx-dnote').forEach(n => n.remove());
    if (a.status !== 'active' || a.status_note || (a.last_check_result && a.last_check_result !== 'ok')) {
      const n = document.createElement('div'); n.className = 'fx-dnote t-' + (a.status === 'active' && a.last_check_result && a.last_check_result !== 'ok' ? 'warn' : tone(a.status)); n.setAttribute('role', 'note');
      const tip = (TAO_FX8.tipFor ? TAO_FX8.tipFor(a) : (a.status_note || '')).split('\n');
      tip[0] = tip[0].replace(/[，,]?\s*点开查看(原因|详情)。?/, '。').replace(/。。$/, '。');
      if (!a.status_note && /^(restricted|spam_block|spam_block_perm|flood_wait|error)$/.test(a.status)) tip.splice(1, 0, '具体原因还没记录，点「重新检查」获取最新原因');
      n.innerHTML = '<div class="tx"><b>' + esc(tip[0]) + '</b>' + tip.slice(1).map(t => '<small>' + esc(t) + '</small>').join('') + '</div>';
      const act = ACT[a.status];
      if (act && !readonly) { const b = document.createElement('button'); b.type = 'button'; b.className = 'sm fit'; b.textContent = act[0]; b.onclick = () => act[1](id); n.appendChild(b); }
      const hero = body.querySelector('.fx-dhero'); if (hero) hero.insertAdjacentElement('afterend', n); else body.prepend(n);
    }
    const w = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    const fix = []; while (w.nextNode()) fix.push(w.currentNode);
    fix.forEach(t => {
      let v = t.nodeValue, o = v;
      if (FLAG.test(v)) v = v.replace(FLAG, '').replace(/\s+$/, '');
      FLAG.lastIndex = 0;
      if (!t.parentElement.closest('.tag,input,textarea')) { if (v.trim() === '已授权') v = v.replace('已授权', '本地有会话'); else if (v.trim() === '未授权') v = v.replace('未授权', '本地无会话'); }
      if (v !== o) t.nodeValue = v;
    });
  }
  TAO_HOOK.after('openDrawer', function (value, args) {
    const go = () => { try { decorate(Number(args[0])); } catch (_) {} };
    if (value && typeof value.then === 'function') value.then(go, () => {}); else go();
    setTimeout(go, 400);
    return value;
  }, 'polish:drawer');
})();


/* =================== fx9 · CSP 安全的事件绑定（替代 242 个内联 onclick/onchange/oninput） ===================
   构建时 on<event>="代码" 已改成 data-on<event>="代码"。这里用一个只认白名单语法的小解释器执行，
   不用 eval / new Function，所以页面可以开 script-src 不带 'unsafe-inline' 的严格 CSP。
   支持：调用 a.b(…)、成员链、= / += 赋值、!、return、字面量、this / event、.catch(e=>…) 箭头。 */
/*@@LEX@@*/
/* TAO_LEX_START */
const __taoLexHas = new Set(["$", "DEMO", "TAO_MOTION", "TAO_KEYS", "MOCK", "tokenKey", "TAO_ERR", "TASK_KIND_CN", "TASK_TONE", "observedLoginTasks", "chatPicked", "ACTION_CN", "STATUS_CN", "cnStatus", "ERROR_CN", "KEY_CN", "isOk", "LOG_DETAIL_CN", "SUMMARY_LIMIT", "SUMMARY_SECRET", "DIAL", "nowMs", "KICK_SKIP_STATUS", "KICK_STATE_CN", "KICK_BASE_CN", "UI_TASKS", "saveKickRetry", "kickNow", "viewKey", "VIEW_NAMES", "themeKey", "ACTIONS", "PAL", "SET_ALIAS", "STAT_CN", "MORE_VIEWS", "twofaShown", "addAccount", "saveAccount", "check", "devices", "dialogs", "terminate", "logout", "delAccount", "verify", "doSend", "ONE_STOP_WATCHING", "doImport", "tdataBody", "TB_PRIV_KEYS", "TB_PRIV_VALS", "TB_GROUPS", "TB_DRAFTS", "AI_HIST_KEY", "AI_HIST_MAX", "TAO_HOOK", "TAO_DOM", "TAO_FX", "TAO_FX3", "TAO_FX4", "TAO_FX5", "TAO_FX8", "TAO_BE", "accounts", "stats", "sel", "filter", "loginId", "readonly", "accountsRevision", "visibleCache", "filterCountsCache", "kick", "busy", "taskTimer", "loginDetailTimer", "taskDetailVersion", "chatRows", "leadRows", "logPinned", "skewMs", "_uiResolver", "_uiMode", "_dialogEl", "_dialogReturnFocus", "_dialogKeyHandler", "_dialogTitleSeq", "UI_TASK_SEQ", "uiFilePending", "legacyProgressCard", "currentView", "MENU_OPEN", "quickFilter", "tableLimit", "accountRenderTimer", "qrLoginId", "qrWaitAbort", "officialCodeAccountId", "officialCodeRequest", "spinTimer", "proxyCapacityTimer", "timer", "stickyFrame", "TB_OPS", "TB_CUR", "TB_BUSY", "ZT_JOB", "ZT_MERGE_JOB", "ZT_MERGE_N", "AI_CFG", "AI_MSGS"]);
function __taoLexGet(n) { switch (n) {
  case '$': return $;
  case 'DEMO': return DEMO;
  case 'TAO_MOTION': return TAO_MOTION;
  case 'TAO_KEYS': return TAO_KEYS;
  case 'MOCK': return MOCK;
  case 'tokenKey': return tokenKey;
  case 'TAO_ERR': return TAO_ERR;
  case 'TASK_KIND_CN': return TASK_KIND_CN;
  case 'TASK_TONE': return TASK_TONE;
  case 'observedLoginTasks': return observedLoginTasks;
  case 'chatPicked': return chatPicked;
  case 'ACTION_CN': return ACTION_CN;
  case 'STATUS_CN': return STATUS_CN;
  case 'cnStatus': return cnStatus;
  case 'ERROR_CN': return ERROR_CN;
  case 'KEY_CN': return KEY_CN;
  case 'isOk': return isOk;
  case 'LOG_DETAIL_CN': return LOG_DETAIL_CN;
  case 'SUMMARY_LIMIT': return SUMMARY_LIMIT;
  case 'SUMMARY_SECRET': return SUMMARY_SECRET;
  case 'DIAL': return DIAL;
  case 'nowMs': return nowMs;
  case 'KICK_SKIP_STATUS': return KICK_SKIP_STATUS;
  case 'KICK_STATE_CN': return KICK_STATE_CN;
  case 'KICK_BASE_CN': return KICK_BASE_CN;
  case 'UI_TASKS': return UI_TASKS;
  case 'saveKickRetry': return saveKickRetry;
  case 'kickNow': return kickNow;
  case 'viewKey': return viewKey;
  case 'VIEW_NAMES': return VIEW_NAMES;
  case 'themeKey': return themeKey;
  case 'ACTIONS': return ACTIONS;
  case 'PAL': return PAL;
  case 'SET_ALIAS': return SET_ALIAS;
  case 'STAT_CN': return STAT_CN;
  case 'MORE_VIEWS': return MORE_VIEWS;
  case 'twofaShown': return twofaShown;
  case 'addAccount': return addAccount;
  case 'saveAccount': return saveAccount;
  case 'check': return check;
  case 'devices': return devices;
  case 'dialogs': return dialogs;
  case 'terminate': return terminate;
  case 'logout': return logout;
  case 'delAccount': return delAccount;
  case 'verify': return verify;
  case 'doSend': return doSend;
  case 'ONE_STOP_WATCHING': return ONE_STOP_WATCHING;
  case 'doImport': return doImport;
  case 'tdataBody': return tdataBody;
  case 'TB_PRIV_KEYS': return TB_PRIV_KEYS;
  case 'TB_PRIV_VALS': return TB_PRIV_VALS;
  case 'TB_GROUPS': return TB_GROUPS;
  case 'TB_DRAFTS': return TB_DRAFTS;
  case 'AI_HIST_KEY': return AI_HIST_KEY;
  case 'AI_HIST_MAX': return AI_HIST_MAX;
  case 'TAO_HOOK': return TAO_HOOK;
  case 'TAO_DOM': return TAO_DOM;
  case 'TAO_FX': return TAO_FX;
  case 'TAO_FX3': return TAO_FX3;
  case 'TAO_FX4': return TAO_FX4;
  case 'TAO_FX5': return TAO_FX5;
  case 'TAO_FX8': return TAO_FX8;
  case 'TAO_BE': return TAO_BE;
  case 'accounts': return accounts;
  case 'stats': return stats;
  case 'sel': return sel;
  case 'filter': return filter;
  case 'loginId': return loginId;
  case 'readonly': return readonly;
  case 'accountsRevision': return accountsRevision;
  case 'visibleCache': return visibleCache;
  case 'filterCountsCache': return filterCountsCache;
  case 'kick': return kick;
  case 'busy': return busy;
  case 'taskTimer': return taskTimer;
  case 'loginDetailTimer': return loginDetailTimer;
  case 'taskDetailVersion': return taskDetailVersion;
  case 'chatRows': return chatRows;
  case 'leadRows': return leadRows;
  case 'logPinned': return logPinned;
  case 'skewMs': return skewMs;
  case '_uiResolver': return _uiResolver;
  case '_uiMode': return _uiMode;
  case '_dialogEl': return _dialogEl;
  case '_dialogReturnFocus': return _dialogReturnFocus;
  case '_dialogKeyHandler': return _dialogKeyHandler;
  case '_dialogTitleSeq': return _dialogTitleSeq;
  case 'UI_TASK_SEQ': return UI_TASK_SEQ;
  case 'uiFilePending': return uiFilePending;
  case 'legacyProgressCard': return legacyProgressCard;
  case 'currentView': return currentView;
  case 'MENU_OPEN': return MENU_OPEN;
  case 'quickFilter': return quickFilter;
  case 'tableLimit': return tableLimit;
  case 'accountRenderTimer': return accountRenderTimer;
  case 'qrLoginId': return qrLoginId;
  case 'qrWaitAbort': return qrWaitAbort;
  case 'officialCodeAccountId': return officialCodeAccountId;
  case 'officialCodeRequest': return officialCodeRequest;
  case 'spinTimer': return spinTimer;
  case 'proxyCapacityTimer': return proxyCapacityTimer;
  case 'timer': return timer;
  case 'stickyFrame': return stickyFrame;
  case 'TB_OPS': return TB_OPS;
  case 'TB_CUR': return TB_CUR;
  case 'TB_BUSY': return TB_BUSY;
  case 'ZT_JOB': return ZT_JOB;
  case 'ZT_MERGE_JOB': return ZT_MERGE_JOB;
  case 'ZT_MERGE_N': return ZT_MERGE_N;
  case 'AI_CFG': return AI_CFG;
  case 'AI_MSGS': return AI_MSGS;
} }
function __taoLexSet(n, v) { switch (n) {
  case 'accounts': accounts = v; return true;
  case 'stats': stats = v; return true;
  case 'sel': sel = v; return true;
  case 'filter': filter = v; return true;
  case 'loginId': loginId = v; return true;
  case 'readonly': readonly = v; return true;
  case 'accountsRevision': accountsRevision = v; return true;
  case 'visibleCache': visibleCache = v; return true;
  case 'filterCountsCache': filterCountsCache = v; return true;
  case 'kick': kick = v; return true;
  case 'busy': busy = v; return true;
  case 'taskTimer': taskTimer = v; return true;
  case 'loginDetailTimer': loginDetailTimer = v; return true;
  case 'taskDetailVersion': taskDetailVersion = v; return true;
  case 'chatRows': chatRows = v; return true;
  case 'leadRows': leadRows = v; return true;
  case 'logPinned': logPinned = v; return true;
  case 'skewMs': skewMs = v; return true;
  case '_uiResolver': _uiResolver = v; return true;
  case '_uiMode': _uiMode = v; return true;
  case '_dialogEl': _dialogEl = v; return true;
  case '_dialogReturnFocus': _dialogReturnFocus = v; return true;
  case '_dialogKeyHandler': _dialogKeyHandler = v; return true;
  case '_dialogTitleSeq': _dialogTitleSeq = v; return true;
  case 'UI_TASK_SEQ': UI_TASK_SEQ = v; return true;
  case 'uiFilePending': uiFilePending = v; return true;
  case 'legacyProgressCard': legacyProgressCard = v; return true;
  case 'currentView': currentView = v; return true;
  case 'MENU_OPEN': MENU_OPEN = v; return true;
  case 'quickFilter': quickFilter = v; return true;
  case 'tableLimit': tableLimit = v; return true;
  case 'accountRenderTimer': accountRenderTimer = v; return true;
  case 'qrLoginId': qrLoginId = v; return true;
  case 'qrWaitAbort': qrWaitAbort = v; return true;
  case 'officialCodeAccountId': officialCodeAccountId = v; return true;
  case 'officialCodeRequest': officialCodeRequest = v; return true;
  case 'spinTimer': spinTimer = v; return true;
  case 'proxyCapacityTimer': proxyCapacityTimer = v; return true;
  case 'timer': timer = v; return true;
  case 'stickyFrame': stickyFrame = v; return true;
  case 'TB_OPS': TB_OPS = v; return true;
  case 'TB_CUR': TB_CUR = v; return true;
  case 'TB_BUSY': TB_BUSY = v; return true;
  case 'ZT_JOB': ZT_JOB = v; return true;
  case 'ZT_MERGE_JOB': ZT_MERGE_JOB = v; return true;
  case 'ZT_MERGE_N': ZT_MERGE_N = v; return true;
  case 'AI_CFG': AI_CFG = v; return true;
  case 'AI_MSGS': AI_MSGS = v; return true;
} return false; }
/* TAO_LEX_END */
const TAO_ACT = (() => {
  // 少数内联代码引用了脚本顶层的 let/const（window 上没有），这里显式开放
  // 顶层 let/const 不在 window 上；构建时生成 __taoLexHas/__taoLexGet/__taoLexSet 开放它们
  const lexHas = n => typeof __taoLexHas !== 'undefined' && __taoLexHas.has(n);
  const RX = /\s*(?:(\d+(?:\.\d+)?)|('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")|([A-Za-z_$][\w$]*)|(=>|\+=|===|!==|==|!=|&&|\|\||[()\[\],;.=!+\-]))/y;
  function lex(src) {
    const t = []; RX.lastIndex = 0; let m;
    while (RX.lastIndex < src.length) {
      const at = RX.lastIndex; m = RX.exec(src);
      if (!m) { if (/^\s*$/.test(src.slice(at))) break; throw new SyntaxError('TAO_ACT: 不支持的语法 @' + at + ' in ' + src); }
      if (m[1]) t.push({k: 'num', v: +m[1]});
      else if (m[2]) t.push({k: 'str', v: m[2].slice(1, -1).replace(/\\(.)/g, (_, c) => ({n: '\n', t: '\t'})[c] || c)});
      else if (m[3]) t.push({k: 'id', v: m[3]});
      else t.push({k: 'p', v: m[4]});
    }
    return t;
  }
  function parse(src) {
    const t = lex(src); let i = 0;
    const peek = v => t[i] && t[i].k === 'p' && t[i].v === v;
    const eat = v => { if (!peek(v)) throw new SyntaxError('TAO_ACT: 需要 ' + v + ' in ' + src); i++; };
    function prog() { const s = []; while (i < t.length) { if (peek(';')) { i++; continue; } s.push(stmt()); } return s; }
    function stmt() {
      if (t[i].k === 'id' && t[i].v === 'return') { i++; return {r: (i < t.length && !peek(';')) ? expr() : {lit: undefined}}; }
      return expr();
    }
    function expr() {
      // 箭头 x => body
      if (t[i] && t[i].k === 'id' && t[i + 1] && t[i + 1].k === 'p' && t[i + 1].v === '=>') { const p = t[i].v; i += 2; return {arrow: p, body: expr()}; }
      const left = binary();
      if (peek('=') || peek('+=')) { const op = t[i++].v; return {assign: left, op, val: expr()}; }
      return left;
    }
    function binary() {
      let l = unary();
      while (t[i] && t[i].k === 'p' && ['===', '!==', '==', '!=', '&&', '||', '+', '-'].includes(t[i].v)) { const op = t[i++].v; l = {bin: op, l, r: unary()}; }
      return l;
    }
    function unary() { if (peek('!')) { i++; return {not: unary()}; } if (peek('-')) { i++; return {neg: unary()}; } return postfix(primary()); }
    function primary() {
      const x = t[i++]; if (!x) throw new SyntaxError('TAO_ACT: 意外结束 in ' + src);
      if (x.k === 'num' || x.k === 'str') return {lit: x.v};
      if (x.k === 'id') { if (x.v === 'true') return {lit: true}; if (x.v === 'false') return {lit: false}; if (x.v === 'null') return {lit: null}; if (x.v === 'undefined') return {lit: undefined}; return {name: x.v}; }
      if (x.v === '(') { const e = expr(); eat(')'); return e; }
      throw new SyntaxError('TAO_ACT: 意外的 ' + x.v + ' in ' + src);
    }
    function postfix(e) {
      for (;;) {
        if (peek('.')) { i++; const p = t[i++]; if (!p || p.k !== 'id') throw new SyntaxError('TAO_ACT: 成员名 in ' + src); e = {mem: e, key: p.v}; }
        else if (peek('[')) { i++; const k = expr(); eat(']'); e = {mem: e, keyE: k}; }
        else if (peek('(')) { i++; const a = []; while (!peek(')')) { a.push(expr()); if (peek(',')) i++; else break; } eat(')'); e = {call: e, args: a}; }
        else return e;
      }
    }
    return prog();
  }
  const BAD_KEYS = new Set(['__proto__', 'constructor', 'prototype']);
  function root(name, ctx) {
    if (name === 'this') return ctx.el;
    if (name === 'event') return ctx.ev;
    if (ctx.scope && name in ctx.scope) return ctx.scope[name];
    if (lexHas(name)) return __taoLexGet(name);
    if (name in window) return window[name];
    throw new ReferenceError('TAO_ACT: 未知名称 ' + name);
  }
  function key(n, ctx) { const k = n.key != null ? n.key : ev(n.keyE, ctx); if (BAD_KEYS.has(String(k))) throw new Error('TAO_ACT: 禁止访问 ' + k); return k; }
  function ev(n, ctx) {
    if ('lit' in n) return n.lit;
    if (n.name) return root(n.name, ctx);
    if (n.not) return !ev(n.not, ctx);
    if (n.neg) return -ev(n.neg, ctx);
    if (n.bin) { const op = n.bin; if (op === '&&') return ev(n.l, ctx) && ev(n.r, ctx); if (op === '||') return ev(n.l, ctx) || ev(n.r, ctx);
      const a = ev(n.l, ctx), b = ev(n.r, ctx);
      return op === '===' ? a === b : op === '!==' ? a !== b : op === '==' ? a == b : op === '!=' ? a != b : op === '+' ? a + b : a - b; }
    if (n.mem) { const o = ev(n.mem, ctx); return o == null ? undefined : o[key(n, ctx)]; }
    if (n.arrow) return function (v) { return ev(n.body, Object.assign({}, ctx, {scope: Object.assign({}, ctx.scope, {[n.arrow]: v})})); };
    if (n.call) {
      let fn, self;
      if (n.call.mem) { self = ev(n.call.mem, ctx); fn = self == null ? undefined : self[key(n.call, ctx)]; }
      else { fn = ev(n.call, ctx); self = undefined; }
      if (typeof fn !== 'function') throw new TypeError('TAO_ACT: 不是函数 in ' + JSON.stringify(n.call.key || n.call.name));
      if (fn === Function || fn === window.eval || fn === setTimeout || fn === setInterval) throw new Error('TAO_ACT: 禁止调用');
      return fn.apply(self, n.args.map(a => ev(a, ctx)));
    }
    if (n.assign) {
      const target = n.assign, v = ev(n.val, ctx);
      if (target.name) {
        const nm = target.name; const cur = root(nm, ctx); const nv = n.op === '+=' ? cur + v : v;
        if (lexHas(nm)) { if (!__taoLexSet(nm, nv)) throw new TypeError('TAO_ACT: 常量不能赋值 ' + nm); } else window[nm] = nv; return nv;
      }
      if (target.mem) { const o = ev(target.mem, ctx), k = key(target, ctx); return (o[k] = n.op === '+=' ? o[k] + v : v); }
      throw new SyntaxError('TAO_ACT: 不能赋值');
    }
    throw new Error('TAO_ACT: 未知节点');
  }
  const cache = new Map();
  function run(code, el, e) {
    let ast = cache.get(code);
    if (!ast) { ast = parse(code); cache.set(code, ast); }
    const ctx = {el, ev: e, scope: null};
    for (const s of ast) {
      if (s.r) { const v = ev(s.r, ctx); if (v === false && e) e.preventDefault(); return v; }
      ev(s, ctx);
    }
  }
  const TYPES = ['click', 'change', 'input', 'load'];
  const SEL = TYPES.map(t => '[data-on' + t + ']').join(',');
  function bindEl(el) {
    if (!el.__taoOn) el.__taoOn = {};
    for (const t of TYPES) {
      if (el.__taoOn[t] || !el.hasAttribute('data-on' + t)) continue;
      el.__taoOn[t] = true;
      el.addEventListener(t, function (e) {
        const code = this.getAttribute('data-on' + t); if (!code) return;
        try { return run(code, this, e); }
        catch (err) { console.error(err); }
      });
    }
  }
  function scan(rootEl) {
    if (rootEl.nodeType !== 1) return;
    if (rootEl.matches(SEL)) bindEl(rootEl);
    rootEl.querySelectorAll(SEL).forEach(bindEl);
  }
  scan(document.documentElement);
  new MutationObserver(list => {
    for (const m of list) {
      if (m.type === 'attributes') { if (m.target.nodeType === 1) bindEl(m.target); continue; }
      for (const n of m.addedNodes) scan(n);
    }
  }).observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: TYPES.map(t => 'data-on' + t)});
  document.addEventListener('DOMContentLoaded', () => scan(document.documentElement));
  // 非阻塞字体：通过 data-fx-fonts 标记加载完成后切换 media
  const fl = document.querySelector('link[data-fx-fonts]');
  if (fl) { if (fl.sheet) fl.media = 'all'; else fl.addEventListener('load', () => { fl.media = 'all'; }); }
  return {run, parse};
})();

/* ---------- 视图标题系统（view-head） ----------
 * The header is assembled from existing controls rather than cloning them.
 * Keeping the original nodes preserves ids, data-on* handlers, focus state,
 * and the TAO_ACT delegation used by the rest of the page.
 */
(() => {
  const VIEWS = [
    {v: 'accounts', en: 'Accounts', title: '账号清单', sub: '管理、筛选与批量操作',
      acts: ['.accounts-panel .tools-right [data-onclick="refresh()"]', '.accounts-panel .tools-right .add-btn', '.accounts-panel .tools-right .tao-one-stop'], meta: '#viewMetaAccounts'},
    {v: 'tasks', en: 'Tasks', title: '任务中心', sub: '查看进行中与历史任务', card: 'tasks', hint: '#taskHint'},
    {v: 'leads', en: 'Leads', title: '线索库', sub: '发言人、群组与采集结果', card: 'leads', hint: '#leadHint'},
    {v: 'toolbox', en: 'Toolbox', title: '工具箱', sub: '账号维护与批量工具',
      acts: ['[data-widget="toolbox"]>h3>button[data-onclick="loadOps()"]']},
    {v: 'logs', en: 'Logs', title: '运行日志', sub: '错误、任务与系统事件',
      acts: ['#logPin', '[data-widget="logText"]>h3>button[data-onclick="unpinLog()"]']},
    {v: 'settings', en: 'Settings', title: '系统设置', sub: '连接、并发与界面偏好',
      acts: ['[data-widget="settings"]>h3>button']}
  ];
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  function build() {
    const shell = document.getElementById('appShell');
    if (!shell || document.getElementById('viewHead')) return;
    const head = el('header', 'view-head'); head.id = 'viewHead';
    VIEWS.forEach(cfg => {
      const copy = el('div', 'vh-copy'); copy.dataset.vh = cfg.v;
      const eyebrow = el('p', 'vh-eyebrow', cfg.en);
      const title = el('h1', 'vh-title', cfg.title);
      const sub = el('p', 'vh-sub'); sub.append(el('span', '', cfg.sub));
      const actions = el('div', 'vh-actions');
      actions.dataset.vh = cfg.v;
      actions.setAttribute('role', 'group');
      actions.setAttribute('aria-label', cfg.title + '操作');
      if (cfg.card) {
        const card = document.querySelector('[data-widget="' + cfg.card + '"]');
        const h3 = card && card.querySelector(':scope>h3');
        if (h3) {
          const hint = h3.querySelector(cfg.hint);
          if (hint) sub.append(hint);
          h3.querySelectorAll(':scope>button').forEach(button => actions.append(button));
          card.classList.add('vh-merged');
        }
      }
      (cfg.acts || []).forEach(selector => {
        document.querySelectorAll(selector).forEach(button => actions.append(button));
      });
      if (cfg.meta) {
        const meta = el('span', 'vh-meta'); meta.id = cfg.meta.slice(1); sub.append(meta);
      }
      copy.append(eyebrow, title, sub);
      head.append(copy, actions);
    });
    shell.before(head);
    sync();
    meta();
  }
  function sync() {
    const view = typeof currentView === 'string' ? currentView : 'accounts';
    document.querySelectorAll('#viewHead [data-vh]').forEach(node => {
      node.hidden = node.dataset.vh !== view;
    });
  }
  function meta() {
    const node = document.getElementById('viewMetaAccounts');
    if (!node || typeof accounts === 'undefined') return;
    const total = Array.isArray(accounts) ? accounts.length : 0;
    const active = typeof stats === 'object' && stats
      ? (stats.active != null ? stats.active : stats.by_status && stats.by_status.active)
      : null;
    const signedIn = active != null ? active : (Array.isArray(accounts) ? accounts.filter(a => a.status === 'active').length : 0);
    node.textContent = total ? total + ' 个账号 · ' + signedIn + ' 已登录' : '';
  }
  let last = null;
  let animation = null;
  function onView() {
    const view = typeof currentView === 'string' ? currentView : null;
    sync();
    if (view === last) return;
    const first = last === null; last = view;
    const head = document.getElementById('viewHead');
    if (first || !head || typeof head.animate !== 'function') return;
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const copy = head.querySelector('.vh-copy:not([hidden])');
    if (!copy) return;
    if (animation) animation.cancel();
    animation = copy.animate(
      [{opacity: 0, transform: 'translateY(6px)'}, {opacity: 1, transform: 'none'}],
      {duration: 240, easing: 'cubic-bezier(.33,1,.68,1)'}
    );
  }
  function init() {
    build();
    if (typeof TAO_HOOK !== 'undefined' && TAO_HOOK) {
      TAO_HOOK.after('applyView', onView, 'vh:view');
      TAO_HOOK.after('updateNavBadges', meta, 'vh:meta');
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
