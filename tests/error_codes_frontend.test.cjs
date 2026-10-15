const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../tam/web/app.js'), 'utf8');
const start = source.indexOf('const TAO_ERR = (() => {');
const end = source.indexOf('async function demoApi', start);
if (start < 0 || end < 0) throw new Error('TAO_ERR block not found');

function load() {
  const ctx = vm.createContext({
    DEMO: false,
    document: {},
    progress: () => {},
    $: id => id === '#prog' ? {style: {width: ''}} : {value: ''},
    fetch: async () => ({ok: true, json: async () => ({})}),
    Promise, JSON, String, Number, Math, Object, Array, Error, setTimeout, clearTimeout,
  });
  vm.runInContext(`${source.slice(start, end)}\nglobalThis.__TAO_ERR = TAO_ERR; globalThis.__api = api;`, ctx);
  return ctx;
}

const codes = [
  'auth_required', 'readonly_mode', 'not_found', 'validation_error', 'conflict', 'internal_error',
  'session_missing', 'session_invalid', 'account_banned', 'account_deleted', 'account_restricted',
  'account_locked', 'twofa_required', 'twofa_wrong', 'phone_invalid', 'code_timeout', 'code_invalid',
  'code_link_missing', 'flood_wait', 'proxy_error', 'network_timeout', 'dc_migrate',
  'import_bad_archive', 'import_unknown_format', 'import_duplicate', 'tdata_passcode', 'op_unknown',
  'op_param_missing', 'task_cancelled', 'peer_invalid', 'privacy_restricted', 'not_authorized',
];

test('TAO_ERR includes all backend codes and legacy compatibility', () => {
  const err = load().__TAO_ERR;
  for (const code of codes) assert.ok(err.CN[code], `missing ${code}`);
});

test('422 detail arrays are human-readable, never [object Object]', () => {
  const err = load().__TAO_ERR;
  const text = err.describe('validation_error', [
    {loc: ['body', 'account_ids'], msg: 'field required'},
  ]);
  assert.match(text, /参数有误/);
  assert.match(text, /account_ids：field required/);
  assert.doesNotMatch(text, /\[object Object\]/);
});

test('flood_wait includes seconds, minutes, and hours', () => {
  const err = load().__TAO_ERR;
  assert.match(err.describe('flood_wait', 'FloodWaitError', 37), /37 秒/);
  assert.match(err.describe('flood_wait', 'FloodWaitError', 120), /2 分钟/);
  assert.match(err.describe('flood_wait', 'FloodWaitError', 7200), /2 小时/);
});

test('English exception detail collapses to the Chinese base hint', () => {
  const err = load().__TAO_ERR;
  assert.equal(err.describe('session_invalid', 'AuthKeyUnregisteredError'), '会话已失效，需要重新登录');
  assert.equal(err.describe('twofa_wrong', 'PasswordHashInvalidError'), '两步验证密码错误');
});

test('api errors keep legacy 401 text and attach structured fields for other statuses', async () => {
  const ctx = load();
  ctx.fetch = async () => ({ok: false, status: 500, text: async () => JSON.stringify({
    detail: 'AuthKeyUnregisteredError', code: 'session_invalid', params: {account_id: 12}, retry_after: null,
  })});
  await assert.rejects(ctx.__api('/api/accounts/12/check'), err => {
    assert.equal(err.status, 500);
    assert.equal(err.code, 'session_invalid');
    assert.deepEqual(err.params, {account_id: 12});
    assert.equal(err.detail, 'AuthKeyUnregisteredError');
    assert.equal(err.message, '会话已失效，需要重新登录');
    return true;
  });

  const legacy = load();
  legacy.fetch = async () => ({ok: false, status: 401, text: async () => JSON.stringify({detail: 'unauthorized', code: 'auth_required'})});
  await assert.rejects(legacy.__api('/api/accounts'), err => {
    assert.equal(err.status, 401);
    assert.match(err.message, /令牌无效（401）/);
    return true;
  });
});
