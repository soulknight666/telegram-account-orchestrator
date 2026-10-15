# TAO 后端接口对接说明

> 基于当前工作区源码整理，面向前端「智能导入」「每行一个下一步」「批量操作推荐」「上手指南」对接使用；已落地的新增接口会在对应章节标明，既有接口与字段保持兼容。

## 1. 框架与路由

- 框架：FastAPI + Uvicorn。
- 路由主文件：`tam/api.py`。
- 前端页面：`GET /`；OpenAPI：`GET /openapi.json`；Swagger：`GET /docs`。
- 大多数 `/api/*` 要求 `Authorization: Bearer <TAM_WEB_TOKEN>`。
- `/api/tools` 与 `/api/tools/call` 支持权限范围；只读令牌只能调用只读工具。
- 当前包版本：`0.3.1`；应用接口版本字段为 `1.8`。

## 2. 账号对象

### `GET /api/accounts` 单项示例

```json
{
  "id": 42,
  "label": "demo-042",
  "phone": "+***",
  "user_id": 123456789,
  "username": "demo_user",
  "proxy": "socks5://***:***@HOST:1080",
  "code_url": "https://HOST/****/GetHTML",
  "device_model": "TAO",
  "app_version": "1.0",
  "system_version": "Windows",
  "lang_code": "zh",
  "tags": ["batch1"],
  "status": "active",
  "status_note": null,
  "premium": 0,
  "locked": 0,
  "has_2fa": 1,
  "spam_until": null,
  "last_warm_at": null,
  "auto_kick": 1,
  "auto_kick_hours": null,
  "auto_kick_loop": 1,
  "login_at": 1730000000,
  "adopted_at": 1730000000,
  "last_check_at": 1730000100,
  "last_check_result": "ok",
  "last_check_error": null,
  "created_at": 1730000000,
  "updated_at": 1730000100,
  "authorized": true,
  "has_twofa_saved": true,
  "kick": {},
  "server_now": 1730000100,
  "twofa_result": null
}
```

列表不会返回 `session_enc`、`twofa_enc`；二验需单独调用 `/api/accounts/{id}/twofa`。

### 状态与授权

常见 `status`：`new`、`active`、`spam_block`、`spam_block_perm`、`frozen`、`unauthorized`、`restricted`、`banned`、`error`、`deleted`。`status_note` 保存补充原因。

`authorized` 代表本地是否存在会话（约等于 `bool(session_enc)`），不等于 Telegram 当前远程授权状态。因此 `active` 通常对应 `authorized=true`，但不是严格不变量。

最近检查信息主要看 `last_check_at`、`status`、`status_note`、`last_check_result`、`last_check_error`；审计日志会记录 `health_check`。

## 3. HTTP 接口总览

下表按功能列出当前接口；返回 `A` 表示账号对象，`T` 表示任务对象，`{ok:...}` 表示操作结果。

### 账号与登录

| 方法 | 路径 | 主要参数 | 返回 |
|---|---|---|---|
| GET | `/api/accounts` | `status`、`tag`、`updated_since?` | 默认账号数组；增量模式返回对象 |
| POST | `/api/accounts` | `AccountIn` | `A` |
| PATCH | `/api/accounts/{id}` | `label`、`phone`、`proxy`、`code_url`、`tags`、自动清设备字段 | `A` |
| DELETE | `/api/accounts/{id}` | `confirm` | `{ok}` |
| POST | `/api/accounts/{id}/lock` | `locked` | `{ok,locked}` |
| POST | `/api/accounts/batch-lock` | `ids`、`locked` | 批量结果 |
| GET/POST | `/api/accounts/{id}/twofa` | POST body `password` | 二验状态/保存结果 |
| POST | `/api/accounts/{id}/login/code` | — | 发码结果 |
| GET | `/api/accounts/{id}/login/official-code` | — | 官方验证码 |
| GET | `/api/accounts/{id}/official-code` | — | 上述别名 |
| POST | `/api/accounts/{id}/login/verify` | `code`、`password?` | 登录结果 |
| POST | `/api/accounts/{id}/login/qr` | — | 二维码登录 |
| POST | `/api/accounts/{id}/login/qr/wait` | `password?`、`timeout?` | 登录结果 |
| DELETE | `/api/accounts/{id}/login/qr` | — | `{ok}` |
| POST | `/api/accounts/{id}/login/auto` | 自动登录参数 | 任务/登录结果 |
| POST | `/api/accounts/{id}/check` | — | 健康检查 |
| POST | `/api/accounts/{id}/logout` | — | `{ok}` |
| POST | `/api/accounts/{id}/delete-telegram` | `reason`、`password?`、确认字段 | 注销结果 |
| GET | `/api/accounts/{id}/dialogs` | `limit` | 对话数组 |
| GET | `/api/accounts/{id}/devices` | — | 设备数组 |
| POST | `/api/accounts/{id}/devices/terminate` | — | 终止其它设备 |
| POST | `/api/accounts/{id}/message` | `peer`、`text` | 发送结果 |
| POST | `/api/accounts/{id}/spam-check` | — | Spam 状态 |

### 导入、导出与会话

| 方法 | 路径 | 主要参数 | 返回 |
|---|---|---|---|
| POST | `/api/accounts/import` | 批量导入对象 | 导入统计/任务 |
| POST | `/api/accounts/import-sessions` | `SessionFilesIn`、`stream?` | 同步统计或 NDJSON |
| POST | `/api/accounts/import-session-strings` | StringSession 列表 | 导入统计 |
| POST | `/api/accounts/import-session-upload` | multipart 文件 | 导入统计/任务 |
| POST | `/api/accounts/import-tdata` | tdata 路径/参数 | 导入统计 |
| POST | `/api/accounts/import-tdata-upload` | multipart ZIP/目录 | 导入统计 |
| POST | `/api/import/mixed` | multipart `file` + 导入参数 | 同步分析/导入统计 |
| POST | `/api/tdata/inspect` | 上传文件 | tdata 检测结果 |
| POST | `/api/accounts/{id}/session/import` | session 文件 | `{ok}` |
| POST | `/api/accounts/{id}/export` | `format=session|string|pack|tdata` | 文件/文本 |
| POST | `/api/accounts/export` | `ids`、`format` | ZIP |
| POST | `/api/accounts/export-code-links` | `ids` | 文本下载 |
| POST | `/api/accounts/{id}/regenerate-session` | — | 新会话 |
| POST | `/api/accounts/regenerate-session` | `ids` | 批量结果 |

### 批量操作与任务

| 方法 | 路径 | 主要参数 | 返回 |
|---|---|---|---|
| POST | `/api/batch/check` | `account_ids`、并发参数 | 健康检查结果数组 |
| POST | `/api/batch/preview` | `op`、`ids`、`params`、`tag?` | `{run,skip,total,server_now}` |
| POST | `/api/batch/warmup` | 账号、消息参数 | `T` |
| POST | `/api/batch/message` | 账号、目标、文本 | `T` |
| POST | `/api/batch/auto-login` | 账号、并发、等待/间隔 | `T` |
| POST | `/api/batch/logout-relogin` | 账号、登录参数 | `T` |
| POST | `/api/accounts/devices/terminate` | `ids` | `T`/批量结果 |
| POST | `/api/accounts/okpay-balance` | `ids`、`bot?`、`wait?`、`concurrency?` | 批量结果 |
| POST | `/api/tasks/login` | 登录任务参数 | `T` |
| GET | `/api/tasks` | `status?` | 任务数组 |
| GET | `/api/tasks/{task_id}` | — | `T` + targets |
| POST | `/api/tasks/{task_id}/stop` | — | `{ok}` |
| DELETE | `/api/tasks/{task_id}` | — | `{ok}` |
| POST | `/api/tasks/{task_id}/cleanup` | — | `{ok}` |
| POST/GET | `/api/tasks/one-stop` | 一条龙参数/查询 | `T` |
| GET | `/api/tasks/one-stop/{id}` | — | 任务详情 |
| POST | `/api/tasks/one-stop/{id}/resume` | — | 恢复结果 |
| POST | `/api/tasks/one-stop/{id}/export` | `format` | 导出文件 |

### 工具、系统、设置与 AI

- 工具：`GET /api/tools`、`POST /api/tools/call`、`GET /api/toolbox/ops`、`POST /api/accounts/{id}/toolbox/{op}`、`POST /api/toolbox/{op}/batch`。
- 系统：`GET /api/logs`、`GET /api/doctor`、`POST /api/doctor/fix`、`GET /api/system/opentele`、`POST /api/system/install-opentele`、`GET/POST/DELETE /api/system/errors`、`GET /api/system/errors/export`、`POST /api/system/restart`、`GET /api/system/status`、`POST /api/system/shutdown`、`GET /api/stats`。
- 设置：`GET/POST /api/settings`、`POST /api/settings/reset`。
- AI：`GET/PUT /api/ai/config`、`POST /api/ai/chat`。
- 代理：`POST /api/proxies/check`、`GET /api/proxies/audit`、`GET /api/proxy/capacity`、`POST /api/accounts/batch-proxy`。
- 其它工具：`POST /api/spintax/preview`、`POST /api/tools/unpack/analyze`、`POST /api/tools/unpack`、`GET /api/tools/unpack/{job}/{filename}`、`DELETE /api/tools/unpack/{job}`、`POST /api/tools/merge/add`、`POST /api/tools/merge/{job}/run`、`POST /api/tools/regtime`、`POST /api/tools/convert`、`POST /api/tools/passkey`、`POST /api/tools/toapi`。

## 4. 导入与文件生命周期

- session 文件：`/api/accounts/import-sessions` 或上传接口；StringSession：`/api/accounts/import-session-strings`。
- tdata：`/api/accounts/import-tdata`、`/api/accounts/import-tdata-upload`；检测用 `/api/tdata/inspect`。
- 上传临时目录在同步请求结束或 NDJSON 流结束后清理；用户原始文件不会被自动删除。
- 解包任务位于 `data/unpack/{job}`，需调用 cleanup；导出临时目录通常自动清理，设置 `TAM_EXPORT_DIR` 后可保留。
- 文件识别目前按扩展名、SQLite/session 特征、ZIP 内 `.session/.json`、tdata 的 `key_datas` 判断。
- `/api/import/mixed` 是新增的混合 ZIP 入口：只展开一层内嵌 ZIP，逐项识别 tdata 与 `.session`，并在 `finally` 清理临时目录；旧导入接口保持原行为。

### 4.0 混合 ZIP 导入（P2-1）

`POST /api/import/mixed` 使用 `multipart/form-data`，字段如下：

| 字段 | 类型/默认值 | 说明 |
|---|---|---|
| `file` | ZIP，必填 | 混合包；tdata 目录以任意层级的 `key_datas` 判定 |
| `dry_run` | 布尔，默认 `true` | 只扫描、不写库；正式导入传 `false` |
| `label` | 字符串，可选 | 账号别名基础名；session 同名 JSON 的 `label/name` 优先 |
| `proxy` | 字符串，可选 | 透传给现有 session/tdata 导入逻辑 |
| `tags` | JSON 字符串数组或逗号文本 | 透传标签 |
| `password` / `tdata_password` / `tdata_passcode` | 字符串，可选 | tdata 本地密码，三者为兼容别名 |
| `own_api`、`debug` | 布尔，可选 | 与现有 tdata 导入接口一致 |
| `twofa` / `twofa_password` | 字符串，可选 | 成功导入后加密保存；session 同名 JSON 的 `twoFA/twofa` 作为逐项回退 |

当前现有 tdata/session 接口没有独立的 `group` 字段，分组请使用 `tags`；新接口不会虚构或改写旧字段。

接口为**同步返回**，不创建后台任务；大包仍受安全上限保护。默认解压总大小上限为 2GB、文件数上限为 20000，可用 `TAM_MIXED_MAX_EXTRACT_MB` 与 `TAM_MIXED_MAX_FILES` 调整；上传体积上限默认 1GB，可用 `TAM_MIXED_MAX_UPLOAD_MB` 调整。需要任务化时可在后续版本复用 `/api/tasks`，本次不改变已有任务契约。

成功响应（dry-run 与正式导入同构）：

```json
{
  "dry_run": true,
  "filename": "accounts.zip",
  "total": 2,
  "tdata": {"found": 1, "imported": 0, "failed": 0},
  "session": {"found": 1, "imported": 0, "failed": 0},
  "items": [{
    "path": "8613800000000/tdata", "kind": "tdata", "phone": "+8613800000000",
    "status": "ready", "account_id": null, "code": null, "reason": null
  }],
  "skipped": [{"path": "readme.txt", "code": "import_unknown_format", "reason": "无法识别"}],
  "server_now": 1730000100
}
```

正式导入仍逐项继续：重复账号使用 `status=duplicate`、`code=import_duplicate`，并在 `params.existing_id` 返回已有账号 ID；带本地密码的 tdata 使用 `status=failed`、`code=tdata_passcode`；其它单项错误只影响当前 item。整个 ZIP 损坏、路径穿越、符号链接或超过上限时返回 400/413 与 `code=import_bad_archive`。

## 4.1 前端批量工具箱契约

`POST /api/toolbox/{op}/batch` 使用 `account_ids`（不是 `ids`），并支持 `params` 与 `concurrency`：

```json
{
  "account_ids": [1, 2],
  "params": {"new": "***"},
  "concurrency": 3
}
```

接口同步返回汇总对象，不创建后台任务：

```json
{
  "op": "twofa",
  "total": 2,
  "ok": 1,
  "failed": 1,
  "results": [
    {"account_id": 1, "ok": true, "result": {"has_password": true}},
    {"account_id": 2, "ok": false, "error": "会话已失效", "code": "session_invalid"}
  ]
}
```

耗时的 `twofa` 支持该接口，但仍是当前请求内的同步批处理。`GET /api/tasks/{id}` 的 `targets` 实际字段为 `id`、`task_id`、`seq`、`target`、`account_id`、`status`、`detail`、`updated_at`、`status_cn`；状态为 `pending/running/ok/fail/skipped`，没有独立的 `result` 或 `error` 字段，失败原因放在 `detail`。

## 4.2 批量预览

新增 `POST /api/batch/preview`，只做前置判断，不执行 Telegram 操作：

```json
{
  "op": "toolbox:twofa",
  "ids": [1, 2, 3],
  "params": {"new": "***"}
}
```

示例返回（`reason_code` 使用统一错误码）：

```json
{
  "op": "toolbox:twofa",
  "run": [1, 3],
  "skip": [{"id": 2, "reason_code": "session_invalid", "reason": "会话已失效"}],
  "total": 3,
  "server_now": 1730000100
}
```

支持 `check`、`auto_login`、`twofa`、`warmup`、`message`、`one_stop` 和 `toolbox:<op>`。工具箱预览直接复用工具箱执行器的参数校验与二验会话筛选。

## 4.3 增量账号刷新与健康结果

- 不带 `updated_since` 的 `GET /api/accounts` 返回保持不变。
- 带 `updated_since=<unix>` 时返回 `{accounts, deleted_ids, server_now, updated_since}`。
- `accounts` 只包含游标之后变化的账号，`deleted_ids` 给出已删除账号 ID。
- 账号对象新增 `last_check_result`（`ok/warn/fail`）和 `last_check_error`；健康检查成功、受限、未授权、网络失败都会写入这两个字段。

## 4.4 P2 状态

混合 ZIP 自动拆分（`POST /api/import/mixed`）已落地；multipart tdata 体检、主要接口 `response_model` 仍是后续兼容增强。本轮没有删除或改名任何既有接口和字段。

## 4.5 统一错误 code（已落地，纯新增字段）

所有 FastAPI 4xx/5xx 响应在保留原 `detail` 的同时增加机器可读的 `code`；422 的 `detail` 仍是 FastAPI 原数组。批量结果失败项增加 `code`，任务 `targets` 增加 `code`（原 `detail` 保留），可选 `params` 与 `retry_after` 只在有值时返回。

已支持的 code：

| 类别 | code |
|---|---|
| 通用 | `auth_required`、`readonly_mode`、`not_found`、`validation_error`、`conflict`、`internal_error` |
| 账号/会话 | `session_missing`、`session_invalid`、`account_banned`、`account_deleted`、`account_restricted`、`account_locked` |
| 登录/取码 | `twofa_required`、`twofa_wrong`、`phone_invalid`、`code_timeout`、`code_invalid`、`code_link_missing` |
| 网络/限流 | `flood_wait`、`proxy_error`、`network_timeout`、`dc_migrate` |
| 导入 | `import_bad_archive`、`import_unknown_format`、`import_duplicate`、`tdata_passcode` |
| 工具/任务 | `op_unknown`、`op_param_missing`、`task_cancelled`、`peer_invalid`、`privacy_restricted` |

兼容规则：历史预览值 `not_authorized` 会在输出时归一为 `session_invalid`；旧的 `error`、`detail`、`status`、任务目标字段均继续保留。未知的未捕获 500 统一返回 `internal_error` 和“服务器内部错误”，堆栈只写服务端错误日志，不出现在响应中。
未列入业务词表的显式 HTTP 状态使用可预测的 `http_<status>`（例如 `http_400`、`http_405`）；这只是兼容兜底，不替代上表业务 code。

示例：

```json
{"detail":"会话已失效","code":"session_invalid","params":{"account_id":12}}
```

```json
{"detail":"Telegram FLOOD_WAIT","code":"flood_wait","retry_after":37}
```

```json
{"account_id":12,"ok":false,"error":"会话已失效","code":"session_invalid"}
```

## 5. 任务模型与推送方式

任务常用字段：`id`、`kind`、`title`、`params`、`status`、`total`、`created_at`、`started_at`、`finished_at`、`ok_count`、`fail_count`、`skip_count`、`done_count`、`percent`、`status_cn`、`live`。

状态：`pending → running → done/failed`；可经 `stopping → stopped`。目标项通常为 `pending/running/ok/fail/skipped`。

前端主要轮询：任务列表约 3 秒，登录详情约 1 秒；没有统一 SSE/WebSocket。导入和 AI 对话支持 NDJSON 流式返回。

新增任务类型通常要同步修改：API 创建入口、任务处理器、并发/停止/超时/失败策略、前端类型映射和详情展示、测试。

## 6. 批量操作与跳过逻辑

已有入口覆盖健康检查、热身、群发、自动登录、退出重登、终止设备、OkPay、重建 session、批量改二验、批量换代理、批量导出等。

部分操作会在执行前跳过：例如仅健康账号、自动登录前置条件、二验操作跳过未授权/错误/无会话账号、一条龙校验手机号/会话/取码链接/检查点。新增 `POST /api/batch/preview` 后，前端会先显示跳过原因，再只提交可执行账号；旧后端返回 404/405/422 时回到原有本地判断路径。

## 7. 设置来源

主要环境变量包括：`TAM_API_ID`、`TAM_API_HASH`、`TAM_MASTER_KEY`、`TAM_WEB_TOKEN`、`TAM_READONLY_TOKEN`、`TAM_READONLY`、`TAM_DRY_RUN`、`TAM_DATA_DIR`、`TAM_DEFAULT_PROXY`、`TAM_PROXY_FILE`、`TAM_RATE`、`TAM_MIN_DELAY`、`TAM_MAX_DELAY`、`TAM_WORKERS`、`TAM_BATCH_CONCURRENCY`、`TAM_REGEN_CONCURRENCY`、`TAM_UI_OP_TIMEOUT`、`TAM_MAX_EXTRACT_MB`、`TAM_MIXED_MAX_UPLOAD_MB`、`TAM_MIXED_MAX_EXTRACT_MB`、`TAM_MIXED_MAX_FILES`、`TAM_HOST`、`TAM_PORT`、`TAM_FRONTEND`、`TAM_DEPLOY`、`TAM_AUTO_KICK_HOURS`、`TAM_AUTO_KICK_LOOP`、`TAM_KICK_RETRY`、`TAM_LLM_TIMEOUT`、`TAM_TOAPI_BASE` 等；完整清单以 `.env.example` 为准。

`GET /api/settings` 返回 `key/type/value/default/desc/restart_required`，敏感值会掩码。部分设置（例如 kick retry）还会保存到 SQLite 并覆盖环境变量。当前没有统一的 `/api/version` 或 `/api/capabilities`，前端可通过 `/openapi.json`、`/api/tools`、`/api/toolbox/ops` 等发现能力。

## 8. AI 助手

支持 OpenAI-compatible、Anthropic、Gemini、Azure OpenAI；默认兼容 OpenAI 的 `https://api.openai.com/v1` 与 `gpt-4o-mini`。

接口：`GET/PUT /api/ai/config`、`POST /api/ai/chat`。支持工具目录、schema、工具调用、审批和 NDJSON。自然语言可解析为工具调用，但登录、导入 session/tdata、注销、导出等敏感操作标记为 `HUMAN_ONLY`，写操作需要用户批准。

## 9. 只读权限、测试与兼容性

- 完整令牌：可读写（仍受全局只读/试运行开关限制）。
- 只读令牌：仅允许只读工具和查询接口。
- 全局限制：`TAM_READONLY`、`TAM_DRY_RUN`。
- 后端测试：pytest、pytest-asyncio；前端测试：Node `node:test`，位于 `tam/tests` 与 `tests/*.test.cjs`。
- 当前工作区存在未提交的前端布局/视觉改动；本说明文件不包含这些代码改动，也没有覆盖它们。

## 10. 当前路由核对结论

已按当前工作区 `tam/api.py` 的 112 个 FastAPI 路由逐条核对。前端提出的路径差异中，实际后端路径如下：

| 前端/旧文档写法 | 当前后端实际路径 | 结论 |
|---|---|---|
| `/api/accounts/{id}/terminate` | `/api/accounts/{id}/devices/terminate` | 以实际路径为准 |
| `/api/batch-proxy` | `/api/accounts/batch-proxy` | 以实际路径为准 |
| `/api/errors`、`/errors/export` | `/api/system/errors`、`/api/system/errors/export` | 以实际路径为准 |
| `/api/opentele`、`/install-opentele` | `/api/system/opentele`、`/api/system/install-opentele` | 以实际路径为准 |
| `/api/restart`、`/shutdown` | `/api/system/restart`、`/api/system/shutdown` | 以实际路径为准 |
| `/api/doctor`（修复） | `GET /api/doctor`、`POST /api/doctor/fix` | 查询与修复分开 |
| `/api/autokick` | `GET /api/autokick`、`POST /api/autokick/run`、`POST /api/autokick/retry` | 已存在 |
| `/api/tasks/collect`、`/tasks/message`、`/chats`、`/leads` | 均已存在 | 已存在 |
| `/api/code-sources/probe` | 已存在 | 已存在 |

当前无法从本次工具会话直接连接到用户机器上的运行端口，因此没有伪造一份“运行时抓取”的 JSON。上表和本文件接口清单来自当前工作区源码；运行时以 `http://127.0.0.1:8848/openapi.json`（或实际监听端口）返回内容为最终准据。

## 11. 前端对接建议

1. 启动时先读取 `/openapi.json`，再按需读取 `/api/tools`、`/api/toolbox/ops`，避免硬编码不存在的接口。
2. 批量操作统一显示任务 ID，并轮询 `/api/tasks/{task_id}`；以 `status` 和 `target.status` 为准，不要只判断 `ok`。
3. 导入界面区分同步 JSON、NDJSON 流和后台任务三种返回模式。
4. 对账号操作前调用 `POST /api/batch/preview`，按原因分组展示“将跳过哪些账号”；确认后只提交 `run`，跳过项仍并入结果表，全部跳过时中止。
5. 对 `status=active` 同时检查 `authorized`、`status_note`、`last_check_at`，不要把 active 当作远程实时在线保证。

## 12. 前端新接口兼容策略

- 工具箱批量只发送 `account_ids`，按 10～50 个账号分块调用；每块完成更新独立进度卡。旧后端端点返回 404、405 或 422 时自动退回逐账号调用。
- 健康检查、自动登录、预热、工具箱执行前先调用批量预览；被跳过账号按原因分组确认，确认后只处理 `run`，并保留跳过结果。
- 账号列表首次全量，之后透明使用 `updated_since` 合并变化与 `deleted_ids`；每 5 分钟强制全量。旧后端返回数组或请求出错时回到全量模式。
- 列表和详情展示 `last_check_at`、`last_check_result`、`last_check_error`；当前状态正常但上次检查失败时，详情抽屉显示黄色说明卡。

## 13. 新增 multipart tdata 体检与响应契约

### `POST /api/import/tdata/inspect`

这是上传版 tdata 只读体检接口，保留原有路径体检接口 `/api/tdata/inspect` 的行为不变。请求使用 `multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `file` | file | 是 | `.zip` 文件；可包含一个或多个 tdata 目录 |
| `tdata_passcode` | string | 否 | tdata 本地密码；兼容别名 `password`、`tdata_password` |
| `debug` | boolean | 否 | 是否在每个条目中附加底层诊断报告，默认 `false` |

接口只解压到临时目录并分析，不写入账号表、不创建导入任务。临时目录在请求结束时清理；上传与解压沿用混合导入的大小、文件数和路径穿越限制。

成功返回（字段保持稳定，未知扩展字段前端应忽略）：

```json
{
  "filename": "accounts.zip",
  "total": 1,
  "items": [
    {
      "path": "8613800000000/tdata",
      "phone": "+8613800000000",
      "dc": 2,
      "needs_password": false,
      "existing": false,
      "status": "ready",
      "account_id": null,
      "code": null,
      "reason": null,
      "params": null
    }
  ],
  "skipped": [],
  "server_now": 1730000100
}
```

`items[]` 的 `status` 为 `ready`、`duplicate` 或 `failed`。重复账号返回 `status=duplicate`、`code=import_duplicate`，并在 `params.existing_id` 给出现有账号 ID；本地密码错误返回 `code=tdata_passcode`；无法识别或损坏条目返回对应导入错误码。非 tdata 文件进入 `skipped[]`，`code=import_unknown_format`。`debug=true` 时还会有 `debug` 诊断对象。

### OpenAPI response_model

以下现有接口已声明 Pydantic 响应模型，且模型使用 `extra="allow"`，因此不会过滤旧版或未来增加的字段：

- `GET /api/accounts`：账号数组/增量对象
- `GET /api/tasks`、`GET /api/tasks/{task_id}`：任务及目标项
- `POST /api/toolbox/{op}/batch`：工具箱批量结果
- `POST /api/batch/preview`：可执行账号与跳过原因
- `POST /api/import/mixed`：混合导入统计、条目和跳过项
- `POST /api/import/tdata/inspect`：上传版体检结果
- `GET/POST /api/settings`、`POST /api/settings/reset`：设置返回结构

这些模型仅补充 `/openapi.json` 的结构描述，不改变现有字段名、业务状态或返回内容。`POST /api/import/tdata/inspect` 与混合导入均为同步返回；大包仍受上传/解压限制，前端可直接显示进度并在完成后刷新列表。

> 说明：当前后端没有单独的 `GET /api/accounts/{account_id}` 详情路由；详情相关的二验读取/保存仍是 `GET/POST /api/accounts/{account_id}/twofa`，该返回也已纳入 OpenAPI response_model。账号基础详情继续来自 `GET /api/accounts` 的单项对象。

### Web 严格 CSP 与声明式事件

前端源码使用 `data-onclick`、`data-onchange`、`data-oninput` 和 `data-onload`，不再输出 `onclick` 等内联事件属性；字体链接使用 `data-fx-fonts="1"`。`tam/web/build.py` 在合并完成后生成 `__taoLexHas`、`__taoLexGet`、`__taoLexSet`，随后对最终单文件中的每个内联脚本计算 SHA-256 并写入 CSP meta。构建完成后不要手动编辑 `tam/web/index.html`，否则脚本哈希会失效；请修改 `index.src.html`、`styles.css` 或 `app.js` 后重新运行 `python tam/web/build.py`。

根页面响应同时返回相同策略的 HTTP `Content-Security-Policy`，并额外加入 `frame-ancestors 'none'`。策略禁止 `unsafe-eval`，`TAO_ACT` 只执行白名单语法并由 `MutationObserver` 绑定 `data-on*` 元素；外部脚本部署时仅保留 `script-src 'self'` 与剩余内联脚本哈希。
## Web release build

`python -m tam.web.build --release` writes `tam/web/index.min.html` plus
same-origin, content-hashed assets named
`tam/web/release/app.min.<sha256-8>.js`,
`tam/web/release/styles.min.<sha256-8>.css`, and
`tam/web/release/tao-touch.<sha256-8>.png`. Old hashed/unhashed release assets
are removed on each build. The release build generates the lexical bridge
before Terser runs with `toplevel=false`, `keep_fnames=true`, and
`keep_classnames=true`; it does not inject the unused GSAP vendor. The readable
`tam/web/index.html` remains available with `TAM_WEB_DEBUG=1`. The root response
selects the release page by default, serves `Cache-Control: no-cache` and a
content hash `ETag`, and returns `304` when `If-None-Match` matches. GZip
compression is enabled for responses of at least 1024 bytes. CSP is recalculated
after release generation through `tam.web.csp` and is reused for the HTTP header.
`/release/*` responses use `Cache-Control: public, max-age=31536000, immutable`;
the root HTML remains `no-cache` with its ETag.
