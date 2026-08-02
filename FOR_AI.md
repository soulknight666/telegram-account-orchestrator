# FOR_AI.md — Telegram Account Orchestrator (TAO) 开发者与 Agent 指南

> **增删工具必须双注册：** 网页看 `toolbox.py`，AI/Agent 看 `tools.py`。只改一侧会导致「网页有、AI 没有」或相反。详见 §8 / §9。

> 本文面向「读代码、改功能、接新接口」的 AI 与人类开发者。  
> 用户向使用说明见网页「帮助」与 `README.md`；**公开分发注意**见 `DISTRIBUTE.md`。

---

## 1. 项目一句话

自托管 Telegram **多账号管理器（TAM）**：Telethon 会话 Fernet 加密存 SQLite；提供 FastAPI 网页控制台、可选 Telegram 机器人前端、CLI、以及 Agent/MCP 工具层。

Python 包根目录：`tam/`（可 `python -m tam.cli`）。仓库顶层还有 `setup.py`、`start.bat`/`start.sh`、`tests/`。

---



## 网页转 API

- 实现：`tam/toapi_tool.py`（`convert_zip`）
- 路由：`POST /api/tools/toapi?mode=from_json|no_2fa|manual&password=&api_base=&tdata_passcode=`
- 前端：ZIP 工具面板「四、转 API」→ `ztToapi()`
- 下载复用：`/api/tools/unpack/{job}/toapi.zip`
- 与机器人 `gaf/zhuanapi.py` 对齐；勿删 `NOTICE.GAFBot`

## 第三方：GAFBot（MIT）

`tam/gaf/` 源自 [GAFBot](https://github.com/kugua332334554/GAFBot)（Copyright (c) 2026 kugua311，MIT）。
再分发须保留 `NOTICE.GAFBot` 中的版权与许可全文。勿删除该声明。

## 2. 目录地图

```
telegram-account-manager/
├── start.bat / start.sh / unblock.bat   # 一键启动（Windows 注意 CRLF + 未签名提示）
├── setup.py                             # 向导：venv、依赖、.env、部署/前端选择
├── .env.example                         # 配置模板（公开包不含真实 .env）
├── requirements.txt / requirements-optional.txt
├── DISTRIBUTE.md                        # 公开包说明
├── FOR_AI.md                            # 本文件
├── README.md
├── tests/                               # 顶层单测
└── tam/                                 # Python 包
    ├── api.py                           # FastAPI 路由 + 托管 index.html
    ├── manager.py                       # 账号业务核心（登录/导入/导出/重生/检测）
    ├── db.py                            # SQLite Account / audit_log / settings
    ├── config.py                        # Settings（环境变量 + 可运行时覆盖）
    ├── crypto.py                        # Fernet 加解密 session
    ├── cli.py / run.py                  # CLI 与进程入口
    ├── bot.py                           # 机器人前端（功能菜单、付费 VIP）
    ├── toolbox.py                       # 网页「工具箱」可扩展操作注册表
    ├── tools.py                         # Agent 工具 JSON Schema + 安全策略
    ├── mcp_server.py                    # MCP stdio
    ├── tasks.py / leads.py              # 任务中心 / 线索库
    ├── autokick.py                      # 接管满 N 小时清其它设备
    ├── tdata.py / tdata_native.py       # tdata 扫描与纯 Python 解析
    ├── codefetch.py                     # 取码链接轮询
    ├── web/index.html                   # 单文件 SPA（无构建步骤）
    └── gaf/                             # 历史/机器人侧内核与周边
        ├── core/chaibao.py|zhenghe.py|shaireg.py   # ZIP 拆包/合并/注册时间
        ├── fangzhaohui.py               # 机器人「防找回」整包流程
        ├── pay.py / okpay_sign.py       # OKPay 商户支付（Bot VIP）
        └── ...                          # 筛活、改 2FA、互转等
```

**约定：** 网页与 REST 的「正确业务」优先写在 `manager.py` + `api.py`；`gaf/*` 多为机器人路径或历史脚本，改功能时先查 manager，避免只改 bot 不同步。

---

## 3. 启动与配置链路

1. `start.bat` → `python setup.py --auto`（可带 `--deploy` / `--frontend`）
2. `setup.py` 生成 `.env`、`TAM_MASTER_KEY`、`TAM_WEB_TOKEN` 等，再 `python -m tam.cli run`
3. `config.Settings.load()` 读环境变量；部分键可被 DB `settings` 表运行时覆盖（参数面板）
4. `api.py` 挂载 `/` 返回 `web/index.html`；默认 local 绑 `127.0.0.1:8848`

关键环境变量（详见 `.env.example`）：

| 变量 | 作用 |
|------|------|
| `TAM_MASTER_KEY` | 加密 session 的主密钥；丢失则旧会话无法解密 |
| `TAM_WEB_TOKEN` | Bearer；`TAM_NO_AUTH=1` 且本机可免令牌 |
| `TAM_DEPLOY` | `local` / `server` |
| `TAM_FRONTEND` | `web` / `bot` / `both` |
| `TAM_API_ID` / `TAM_API_HASH` | 手机号登录建议自备；tdata/session 导入可空（会用桌面端公开指纹） |
| `TAM_AUTO_KICK_HOURS` / `TAM_AUTO_KICK_LOOP` / `TAM_KICK_RETRY` | 自动清设备：默认周期、默认循环、失败重试 |
| `TAM_WORKERS` | ZIP 拆包/合并并发（1–32） |
| `TAM_BATCH_CONCURRENCY` | 网页批量/工具箱默认并行（1–32，参数面板可改） |
| `TAM_REGEN_CONCURRENCY` | 批量重生默认并行（1–8） |
| `TAM_UI_OP_TIMEOUT` | 单号操作超时秒数，超时自动跳过（15–600） |
| `OKPAY_*` | 仅 Bot 付费门槛；与「网页查用户钱包余额」不是同一件事 |

---

## 4. 数据模型（`db.py`）

### accounts（核心字段）

- `id`, `label`, `phone`, `user_id`, `username`
- `session_enc` — Fernet 密文，**API 默认不返回**
- `proxy`, `code_url`（取码链接，自动登录用）
- `device_model`, `app_version`, `system_version`, `lang_code`
- `status`, `status_note`, `spam_until`
- `login_at`, `adopted_at`（清设备计时起点）, `last_kick_at`, `kick_retry_at`, `auto_kick`
- `auto_kick_hours`（该号自定义周期小时，空=全局）, `auto_kick_loop`（1=循环，0=只踢一次）
- `tags`（JSON 数组）

公开序列化用 `Account.public()`，禁止把 `session_enc` 塞进列表接口。

### 其它表

- `audit_log` — `db.log(account_id, action, ok, detail)`
- `settings` — 运行时 KV
- 任务/线索见 `tasks.py` / `leads.py` 自建表

---

## 5. 业务核心（`manager.py` · `AccountManager`）

| 方法 | 用途 |
|------|------|
| `_build_client` / `_load_session` / `_save_session` | Telethon 客户端与加解密 |
| `send_code` / `sign_in` / `auto_login` | 登录；auto 依赖 `code_url` + `codefetch` |
| `import_session` / `import_session_files` / `import_session_strings` | StringSession / .session 文件 |
| `import_tdata` | tdata 目录 → StringSession → 入库 |
| `export_session_string` / `export_session_file` / `export_account_pack` / `export_tdata` | 导出（敏感） |
| `regenerate_session` | 网页防找回：新指纹登录 + 旧 session logout |
| `health_check` / `check_spam_status` / `check_okpay_balance` | 健康 / SpamBot / 私聊 @Okpay |
| `terminate_other_sessions` / `logout` | 踢设备 / 退出 |
| `session(...)` | async 上下文，已授权客户端 |

**魔改提示：**

- 新增「对单个号做一件 Telegram 事」：在 `AccountManager` 加 async 方法 → `api.py` 加路由 → `web/index.html` 按钮调用。
- 批量：可串行 `for id in ids`，或 `asyncio.Semaphore` 限制并发（参考 okpay 批量）。
- 会话写入后若改设备指纹，记得 `db.update(..., device_model=..., adopted_at=...)`。

---

## 6. HTTP API（`api.py`）

鉴权：`Authorization: Bearer <TAM_WEB_TOKEN>`（`auth` / `auth_scoped`）。

### 账号 CRUD 与登录

- `GET/POST /api/accounts`，`PATCH/DELETE /api/accounts/{id}`
- `POST .../login/code`，`.../login/verify`，`.../login/auto`
- `POST .../session/import`，`.../logout`，`.../check`
- `GET .../dialogs`，`.../devices`，`POST .../devices/terminate`
- `POST .../spam-check`，`POST .../okpay-balance`
- `POST .../regenerate-session`
- `POST .../export`（body `format`: `string|session|pack|tdata`）

### 批量 / 导入

- `POST /api/accounts/import`（手机号\|取码链接）
- `POST /api/accounts/import-sessions` / `import-session-strings` / `import-session-upload`
- `POST /api/accounts/import-tdata` / `import-tdata-upload`
- `POST /api/accounts/export`，`POST /api/accounts/regenerate-session`
- `POST /api/accounts/devices/terminate`（body `{ids:[...]}`）
- `POST /api/accounts/okpay-balance`（批量）
- `POST /api/batch/check|message|warmup|auto-login`

### 任务 / 线索 / 工具 / 系统

- `/api/tasks*`，`/api/leads*`，`/api/chats`，`/api/tasks/collect`
- `/api/tools`，`/api/tools/call`（Agent）
- `/api/doctor`，`/api/stats`，`/api/logs`，`/api/autokick*`
- ZIP 工具箱路由在 `toolbox` / 相关 zip API（见 `api.py` 后半与 `gaf/core`）

**上传约定：** session/tdata 浏览器上传用 **原始 body** + 头 `X-Filename`，不是 multipart（与前端 `fetch` 一致）。

**安全约定：** 默认响应不含 session 明文；仅显式 export / regenerate 相关路径处理凭证。`tools.py` 里 `HUMAN_ONLY` 限制 Agent 调登录/导入/导出类工具。

---

## 7. 前端（`tam/web/index.html`）

- 单文件：CSS + HTML + JS，无打包器。
- `api(path, opts)`：统一带 token；`guard(fn)`：写操作 + 日志区。
- 布局：`#board` 上 `data-widget` 绝对定位，localStorage 键 `tam.layout`。
- 弹窗：`openModal('mXxx')` + `#bd` 遮罩；帮助：`#mHelp`。
- 演示：`DEMO` 为 true 时用 MOCK 数据（直接打开 html 文件时）。

**加按钮检查清单：**

1. 详情抽屉或 `#bulk` 加 `onclick`
2. JS 调对应 `/api/...`
3. 危险操作 `confirm(...)`
4. 更新 `#mHelp` 文档，避免与实现脱节

---

## 8. 工具箱扩展（`toolbox.py`）

注册表驱动 UI，**加功能不必先改前端表单**：

```python
# 1. 实现
async def op_my_feature(client, p: dict) -> dict:
    ...
    return {"ok": True, ...}

# 2. OP_SPECS 增加一项（name/type/default/required）
# 3. OPS["my_feature"] = op_my_feature
# 4. 保持 assert set(OP_SPECS ops) == set(OPS)
```

参数类型：`str|int|float|bool|password|textarea|privacy` 等（见现有 spec）。  
网页对勾选账号批量调 toolbox 执行入口（见 `api.py` 中 toolbox 相关路由）。

### ⚠️ 与 AI 工具层必须同步

| 层 | 文件 | 谁在用 |
|----|------|--------|
| **网页工具箱** | `toolbox.py`（`OP_SPECS` + `OPS`） | 控制台「工具箱」、批量对选中号执行 |
| **AI / Agent** | `tools.py`（`@tool` 注册表） | AI 面板、`/api/tools`、`/api/tools/call`、MCP |

**规则（强制）：**

1. **新增**面向账号的可操作能力时：
   - 至少写入 `toolbox.py`（网页可用）；
   - **若希望 AI 助手也能调用**，必须在 `tools.py` 用 `@tool(...)` 再注册一份，并实现调用逻辑（可复用 manager / 与 toolbox 相同的 Telethon 请求）。
2. **删除或改名**工具时：
   - 同时改/删 `toolbox.py` 与 `tools.py`，避免一边有、一边无；
   - AI 侧旧名会留在用户已保存的 `ai_panel_config.tools` 开关里，可忽略未知键。
3. **仅人工、不对 AI 开放**的能力：只放工具箱或专用 REST；若误写进 `tools.py`，应列入 `HUMAN_ONLY` 或根本不注册。
4. 自检：`OP_SPECS`/`OPS` 集合相等；`list_tools()` 能看到新 Agent 工具名。

反例：只改了 `toolbox.py` 的「屏蔽用户」，AI 对话里永远调不到，直到 `tools.py` 补上 `block_user`。

ZIP 工具（拆包/合并/注册时间/格式互转等）已在 `tools.py` 注册为 `zip_*`；AI **不能直接接收浏览器上传**，只能处理 **data 目录内已有文件** 或已有 unpack 作业。用户需先把包放到 data 下或经网页上传生成 job。

---

## 9. Agent / MCP（`tools.py`，`mcp_server.py`）

- 工具用 JSON Schema 描述；`call_tool` 统一返回 `{ok, tool, result|error}`（尽量 HTTP 200）。
- 只读令牌 / `TAM_READONLY` / `TAM_DRY_RUN` / `TAM_PEER_ALLOWLIST` 限制写与发消息对象。
- **网页 AI 面板**（`ai_panel.py`）只暴露 `tools.py` 里已注册、且用户在权限里勾选的工具；**不会**自动读取 `toolbox.py`。
- 改 Agent 能力：扩 `tools.py` 注册；危险操作用 `danger="destructive"` + `confirm`；凭证类列入 `HUMAN_ONLY`。
- 增删工具后：更新本节与用户「帮助」里相关一句；有权限预设（只读/安全/标准）时确认新工具的默认开关是否合理（`default_config()` 按 `danger==read` 默认开启）。

---

## 10. ZIP 内核（`gaf/core`）

| 模块 | 职责 |
|------|------|
| `chaibao.py` | 安全解压（zip-slip）、拆包、`run_parallel` |
| `zhenghe.py` | 合并，重名 `_unique_name` |
| `shaireg.py` | 注册时间分桶，可离线 |

并发：`resolve_workers` / 环境变量 `TAM_WORKERS`。改算法时优先改 `gaf/core/*`，并补 `tam/tests/t_zhenghe.py` 等。

---

## 11. 机器人前端（`bot.py` + `gaf/*`）

- 菜单驱动；VIP/OKPay 商户支付在 `gaf/pay.py`（`OKPAY_ID/TOKEN/COST`）。
- `fangzhaohui.py`：上传号包 → 转 session → 新指纹登录 → 旧 logout → 回传新 session+json（**不**自动出 tdata）。
- 网页 `regenerate_session` 与此对齐思路，但落库到 SQLite，而不是只回传 zip。

改 Bot 功能时确认是否也要网页/API 对等，避免「只能机器人做」。

---

## 12. 测试

```text
tests/                 # 集成向
tam/tests/t_*.py       # 内核/工具箱/参数转换等
```

常用：`python -m unittest` 或直接跑 `tam/tests/t_zhenghe.py`（文件内有 `*_CORE_OK` 标记）。  
改加密、导入、ZIP、清设备逻辑后至少跑相关测试。

---

## 13. 常见魔改配方

### A. 新增强制「对账号做 X」

1. `AccountManager.do_x(self, account_id, ...)`（可选，复杂逻辑放这里）
2. **网页批量**：`toolbox.py` 增加 `op_x` + `OP_SPECS` + `OPS`（前端工具箱自动出现）
3. **AI 同步（必做，除非明确不对 AI 开放）**：`tools.py` 用 `@tool` 注册同名能力，内部调 manager 或等价 Telethon 调用
4. 需要独立 REST 时：`api.py` 增加路由；详情/批量按钮按需改 `index.html`
5. 更新 `FOR_AI.md` / 网页「帮助」一句；跑相关测试

### B. 新导入格式

1. 解析与校验放 manager 或独立模块
2. 转成 StringSession 后走现有 `_save_session` / user_id 合并逻辑
3. 上传接口仿 `import-session-upload`（临时目录、zip-slip、上限）

### C. 新导出格式

1. 从 `_load_session` 取明文
2. 文件类用 `Response` + `Content-Disposition`
3. 前端用 `fetch` + blob 下载（参考 `_downloadBlob`），不要用裸 `api()` 若响应是文件流

### D. 改自动清设备

- 计时：`adopted_at` + `TAM_AUTO_KICK_HOURS`
- 循环：`autokick.py`；重试：`kick_retry_at` + `TAM_KICK_RETRY`
- 真正踢设备：`terminate_other_sessions`

### E. 改 UI 布局/样式

- 只动 `web/index.html` 的 CSS/HTML
- `.card h2,h3` 与 `.card > .scroll` 需保留左右 padding，避免贴边裁切
- 布局状态在 localStorage，大改可提示用户「恢复默认布局」

---



## 二验字段与列表安全

- `accounts.has_2fa`：`1` 有 / `0` 无 / `NULL` 未检查。健康检查时 `GetPasswordRequest` 回写。
- `accounts.twofa_enc`：工具箱「改二步验证」成功或「补录」时用主密钥 Fernet 加密保存；**禁止**在 `Account.public()` / `GET /api/accounts` 中下发明文。
- 揭密：`GET /api/accounts/{id}/twofa`（点眼睛时）
- 补录/清除：`POST /api/accounts/{id}/twofa` body `{"password":"..."}`（空密码=清除本地密文，不改 Telegram）
- 导出号包：`export_account_pack` 若有 `twofa_enc` 则写入 JSON 的 `twofa`/`password` 与同级 `2fa.txt`

## 重生/导入接管落库（adopted_at 与设备指纹）

- 日常 `_save_session`：**仅在空缺时**补 `adopted_at`/`login_at`，避免每次回写把 24h 清设备永远推后。
- **重生会话 / 合并覆盖导入**走 `_commit_takeover`，强制：
  - 覆盖 `session_enc`
  - `adopted_at = login_at = now`（清设备从本机新接管重新计时）
  - `last_kick_at` / `kick_retry_at` 清空（避免旧重试在 Telegram 24h 保护期内空跑）
  - 重生时写入新 `device_model` / `app_version` / `system_version`（面板可见）
- 写完读回校验；返回体带 `adopted_at`、`device_model` 等，便于前端与任务明细展示。

## 批量重生会话：异步作业 / 流式 / 同步

三种模式（优先级从高到低）：

1. **异步作业（推荐，网页默认）** `POST /api/accounts/regenerate-session?async=1`
   - 立刻返回 `{"ok":true,"async":true,"job":<task_id>,"concurrency":N,"task":{...}}`
   - 任务写入 `tasks.py` 任务中心，`kind=regenerate_session`
   - body 可带 `concurrency`（1–8，默认 1）；网页默认读 `TAM_REGEN_CONCURRENCY`
   - 单号硬超时约 150s，避免一号卡死拖死整批；连接/发码/读码均有短超时
   - 前端轮询 `GET /api/tasks/{id}`（约 2s）；可 `POST /api/tasks/{id}/stop` 协商停止
   - 关掉浏览器、断网、重连都不影响执行（与 ZIP job / 群发任务同一套模型）
2. **NDJSON 流** `?stream=1` 或 `Accept: application/x-ndjson`：
   - `{"event":"start","total":N}` / `item` / `done`（流式仍串行）
   - 客户端断开时 `request.is_disconnected()` 后停止后续号
3. **同步汇总**（默认，兼容 CLI/旧前端）：等全部跑完一次返回 JSON；支持 `concurrency`。

## 未授权 / 失效会话的 API 行为

- 无本地 `session_enc`：`dialogs` / `devices` / `terminate` / `spam-check` / `message` 等 **立刻 400**，不连 Telegram。
- `logout`：无会话也 **200**，只清本地字段（`remote_logged_out=false`）；有会话则尽量远端注销，失败仍清本地。
- `_SessionCtx`：连接限时；连上后 `is_user_authorized()` 为假则标 `unauthorized` 并快速失败。
- `AuthKeyNotFound`：Telegram 不认该 auth_key（已注销/重生/封号/死号包）。需重新登录或导入有效 session，不能靠重试同一把钥匙。

## OKPay 余额

- `manager.probe_okpay_balance`：`/start` → 可选点「余额」→ **逐个点选 USDT/TRX/CNY 等键盘按钮** → 解析金额。
- 返回 `balances` / `clicked` / `reply`；工具箱与网页共用。
- 日志：`okpay_balance balances={...} clicked=[...]`（不再只有 keys 列表）。

## 网页批量进度与卡死跳过

- 前端 `runPool` / `runSerial`：进度条显示完成数、成功/失败、当前账号。
- 健康检查、自动登录、OKPay、踢设备、工具箱、打标签、删除等走逐号调用 + 并行池。
- `TAM_BATCH_CONCURRENCY`：默认并行；`TAM_UI_OP_TIMEOUT`：单号超时秒数，超时记失败并 **自动跳过** 继续下一个。
- 养号 / 代理体检等服务端整批接口用 `withWaitProgress` 等待条（无法精确到每个号）。

## 变更摘要（近期）

| 项 | 说明 |
|----|------|
| 重生 async + 任务中心 | `?async=1`，可并行、可停止、可关浏览器 |
| `_commit_takeover` | 重生/合并导入强制回写设备指纹与 `adopted_at` |
| 未授权快速失败 | 避免 500 / 长时间卡住 |
| 重生连接超时 | 批量不因单号连不上而假死 |
| 任务假死 | TaskRunner 单目标超时跳过；重生抢锁 45s；轮询无进展结束；异常写 FAILED |
| OKPay 点选币种 | 自动点菜单再解析余额 |
| 批量进度条 | 多数批量操作逐号进度 |
| 并行 + 超时参数 | 参数面板可改，写回 `.env` |

## 14. 明确不要做的事

- 不要在 `GET /api/accounts` 返回 `session_enc`、明文 session 或二验明文（`twofa` / `twofa_enc`）。
- 不要在日志里打完整 session 字符串。
- 不要去掉 export/regenerate 的确认与鉴权。
- 不要假设 `gaf/fangzhaohui` 与网页已自动同步——改一侧检查另一侧。
- 公开打包前删除 `.env`、`data/`、`*.session`、真实 token（见 `DISTRIBUTE.md`）。

---

## 15. 依赖与可选组件

- 核心：`telethon`、`fastapi`、`uvicorn`、`cryptography` 等（`requirements.txt`）
- 可选：`opentele`（导出 tdata、部分指纹生成）；无则 tdata **导入**仍可用 `tdata_native`，**导出 tdata** 会报清晰错误
- 机器人：`python-telegram-bot`（见 requirements）

---

## 16. 给 AI 的优先阅读顺序

1. `FOR_AI.md`（本文件）
2. `tam/manager.py`（业务）
3. `tam/api.py`（路由清单）
4. `tam/db.py` + `tam/config.py`
5. `tam/toolbox.py`（扩展操作）
6. `tam/web/index.html`（搜函数名即可，文件较大）
7. 按需：`autokick.py`、`tasks.py`、`gaf/core/*`、`tools.py`

改代码时保持：**manager 业务单一真相 → api 薄封装 → 前端只展示与触发**。

---


## AI 面板（`ai_panel.py`）

- `GET/PUT /api/ai/config`：配置与权限（api_key 仅脱敏回传）
- `POST /api/ai/chat`：多轮工具调用；策略过滤 `tools` 开关 + 账号白名单；HUMAN_ONLY 永不开放
- 配置存 `settings.ai_panel_config` JSON

## 系统运维 API（网页顶栏）


| 路径 | 说明 |
|------|------|
| `POST /api/system/restart` | 真实重启：`confirm=true` 后约 1s `os.execv` 同一 `sys.argv` |
| `GET /api/system/errors` | 错误事件列表 + 统计 |
| `POST /api/system/errors` | 前端/用户上报 |
| `DELETE /api/system/errors` | 清空（可 `older_than_hours`） |
| `GET /api/system/errors/export` | 下载 JSON 报告 |
| `GET/POST /api/system/opentele` / `install-opentele` | 检测 / 一键安装 opentele |

错误表：`error_events`（`db.add_error` / `list_errors`）。文件副本：`{data_dir}/errors.log`。

ZIP 扩展：`POST /api/tools/convert`、`POST /api/tools/passkey`。二验工具箱：`twofa_status` / `twofa_reset` / `twofa_reset_cancel`。

帮助模态：`#mHelp`，仅 `.help-main` 滚动，打开时 `body.modal-open` 锁页面滚动。

*文档版本与公开包同步维护；新增用户可见功能时请同时更新网页「帮助」与本节配方表。*


## 能力补齐（AI + 网页工具箱双注册）

以下能力已同步到 `toolbox.py` 与 `tools.py`（Agent 可调，高危需 `confirm`）：

| 能力 | 工具名 | 说明 |
|------|--------|------|
| 二验状态 / 改密 / 重置 | `twofa_status` / `twofa_set` / `twofa_reset` / `twofa_reset_cancel` | 改密对应工具箱 `twofa` |
| 隐私设置 | `privacy_set` | phone/last_seen/invite/avatar |
| 退出登录 / 注销 Telegram | `logout_session` / `delete_tg_account` | 后者真正删号，不可逆 |
| 筛活 / OKPay / 筛料 | `alive_check` / `okpay_balance` / `check_phones` | |
| 通讯录 | `list_contacts` / `add_contact` / `delete_contact` / `contacts_clear` | |
| 会话清理 / 防找回 | `dialogs_clear` / `profile_clear` | |
| 媒体 | `send_media` / `download_media` | 路径限制在 `data/` 内；单文件 ≤50MB |
| 频道 | `create_channel` | 可设公开 username |
| 用户名 | `set_username` | 空=清除 |
| Bot 交互 | `interact_bot` | 发消息并等待回复（不限 SpamBot） |

**仍然做不到 / 刻意不做：**

- **注册新号**：必须短信/语音验证码 + 人工，不能无人值守开号。
- **改手机号**：官方流程要再收验证码，需人工或配取码链接，未做成一键工具。
- **语音/视频通话**：依赖 WebRTC / 第三方（如 pytgcalls），不在本仓库范围。
- **创建/配置 BotFather Bot**：需与 @BotFather 多轮交互且涉及 bot token 保管，保持人工。
- **登录 / 导入 / 导出会话**：仍在 `HUMAN_ONLY`，不对 Agent 开放凭证。

群组搜索/加入/成员/消息读写、屏蔽用户等此前已在 AI 层提供。
