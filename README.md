# Telegram 账号管理器（自托管）

基于 Telethon(MTProto) 的多账号管理工具：加密保存会话、按账号绑定代理与设备指纹、健康检查、受控批量操作、审计日志。CLI + Web 控制台 + **AI/Agent 控制层**三入口。

> **Windows 提示「无法验证发布者」**：从网上下载的 `.bat` 没有数字签名时的正常现象，点「运行」即可。也可先运行同目录 `unblock.bat`，或右键 `start.bat` → 属性 → 「解除锁定」。




## ZIP 工具（网页）

网页控制台「ZIP 工具」面板支持：

| 功能 | 说明 |
|------|------|
| 拆包 | 大号包按规则拆成小包 |
| 合并 | 多包合并，处理重名 |
| 按注册时间分类 | 默认离线分桶；可选联网查注册时间 |
| **转 API** | 上传 session/tdata ZIP → 重命名 session + `api.json` + 取码链接清单（对齐机器人能力） |

转 API 相关环境变量：`TAM_TOAPI_BASE`（优先）、`DM`、`SERVER_IP`/`API_PORT`。

机器人侧同名能力见菜单「转 API」；号包引擎来自 [GAFBot](https://github.com/kugua332334554/GAFBot)（MIT，见 `NOTICE.GAFBot`）。

## 致谢与第三方许可

机器人侧号包处理（`tam/gaf/`：筛活、转 API、拆包、防找回、整合等）移植自
**[GAFBot](https://github.com/kugua332334554/GAFBot)**（作者 **kugua311** / [@kugua332334554](https://github.com/kugua332334554)），
采用 **MIT License**。完整版权声明与许可证原文见本仓库 [`NOTICE.GAFBot`](NOTICE.GAFBot)。

按 MIT 条款：再分发时须保留上述版权与许可声明；本项目对 GAFBot 的修改与网页端适配由本仓库维护，与原作者无关。

## 快速开始

### 一键启动（推荐，新手选这个）

- Windows：双击 `start.bat`
- macOS / Linux：`./start.sh`
- 或：`python setup.py --auto`

**自动完成**：检查 Python → 建 `.venv` → 装依赖（失败自动换清华镜像）→ 生成 `.env` 与主密钥 → 体检修复 → 按你的选择启动。

**启动前只问两件事**（写入 `.env`，下次会记住默认值）：

1. **部署在哪里？** `local` 自己电脑（只监听 127.0.0.1）/ `server` 服务器（0.0.0.0，建议令牌+HTTPS）
2. **用哪个前端？** `web` 网页控制台 / `bot` Telegram 机器人 / `both` 两个一起

也可以不进菜单，直接指定：

```bash
./start.sh --deploy local --frontend web
./start.sh --deploy server --frontend both --token
```

- 本机 + 网页：默认免令牌，浏览器打开即用。
- 服务器：默认启用访问令牌。
- 选了机器人但还没有 Token：会提示填一次 `@BotFather` 的 `TAM_BOT_TOKEN`（可回车跳过，稍后写 `.env`）。
- api_id / 代理：一键模式不打断，需要时再写 `.env` 或网页参数面板（含批量并行 / 单号超时自动跳过 / 重生并行）。
- 其它：`--no-start`、`--port 8848`、`--skip-install`、`--no-venv`。

### 手动安装

```bash
pip install -r requirements.txt
cp .env.example .env
python -m tam.cli init-key        # 生成 TAM_MASTER_KEY，写入 .env
# 在 my.telegram.org 申请 api_id / api_hash，填入 .env

python -m tam.cli add 主号 --phone +8613800138000 --proxy socks5://127.0.0.1:1080
python -m tam.cli login 1         # 验证码 + 两步验证密码
python -m tam.cli check           # 全部账号健康检查
python -m tam.cli serve           # http://127.0.0.1:8848 Web 控制台
```

## 一键体检（出任何问题先跑它）

```bash
python -m tam.cli doctor        # 只体检，不改任何东西
python -m tam.cli doctor --fix  # 体检并自动修复
python -m tam.cli doctor --json # 机器可读输出（给脚本 / Agent）
```

也可以在 Web 控制台右上角点“一键体检”按钮（等价于 `doctor --fix`）。
安装向导的第 5 步、以及 `serve` 启动前都会自动跑一遍（`serve --no-doctor` 可关闭）。

检查 13 项，带 ✓ 的都能自动修：

| 检查项 | 自动修复 |
| --- | --- |
| Python 版本（含 3.13 兼容提醒） | — |
| 核心依赖是否齐全 | ✓ 自动 pip 安装（官方源失败自动转清华镜像） |
| `.env` 是否存在 | ✓ 从 `.env.example` 生成 |
| `TAM_MASTER_KEY` | ✓ 自动生成并写入 |
| `TAM_WEB_TOKEN` | ✓ 自动生成并写入 |
| 配置能否加载（.env 格式、行内注释、空值） | — |
| 会话加解密往返 | — |
| 数据库可读写 + 自动建表/迁移 | ✓ 建目录与表结构 |
| `api_id` / `api_hash` 是否合法 | —（未填只提醒） |
| Web 控制台文件完整性 | — |
| API 能否导入、接口数 | — |
| opentele（仅机器人侧部分旧链路可能用到） | ✓ 可装；**网页 tdata 导入已不依赖它** |
| 端口 8848 是否被占 | — |

## 卡商初始账号批量导入

支持 `手机号|取码链接` 行格式，例：

```
+18129773632|https://tgapi.puonl.com/@cof333/8dc96736-3efb-4353-a78b-274f20c5779f/GetHTML
+18129773633|https://tgapi.puonl.com/@cof333/xxxxxxxx/GetHTML|美国号B
```

```bash
python -m tam.cli import-accounts accounts.txt --tags batch1 --dry-run   # 先预览
python -m tam.cli import-accounts accounts.txt --tags batch1
cat accounts.txt | python -m tam.cli import-accounts -                   # 也可走 stdin

python -m tam.cli auto-login --id 1            # 发码 → 自动拉取码 → 登录
python -m tam.cli auto-login --tag batch1      # 批量（默认串行 + 随机间隔）
```

容错与行为：

- 分隔符支持 `|`、`\|`、`｜`、制表符、逗号、分号；两段顺序可以颠倒；手机号缺 `+` 自动补。
- 第三段作为备注/别名；`#` 开头与空行忽略；单行出错不中断整批，错误行集中返回。
- 幂等：手机号已存在只补写取码链接，不重复建号。
- 取码：`codefetch.py` 只用标准库拉取该链接（支持代理），先记录发码前的旧码作基线，再轮询直到出现不同的新码（默认 120s 超时、5s 一次），避免拿错上一次的验证码。
- 旧库自动迁移：启动时缺 `code_url` 列则自动补列。
- 开了两步验证的号需传 `--password`（取码链接只能拿到短信码）。

## 导入 .session / StringSession

卡商号包常见形态是 Telethon 的 `.session`（SQLite）或 StringSession 字符串。Web 控制台点 **「session 导入」**，或用 CLI：

```bash
# 目录里一批 .session（默认递归）
python -m tam.cli import-sessions /path/to/sessions --tags batch --proxy socks5://127.0.0.1:1080
python -m tam.cli import-sessions /path/to/one.session --label 主号

# 文本文件，一行一个 StringSession；可用 别名|session
python -m tam.cli import-session-strings sessions.txt --tags ss
cat sessions.txt | python -m tam.cli import-session-strings -
```

行为：

- 转 StringSession → 联网验证可用 → Fernet 加密入库；失效条目不留垃圾行。
- 已存在相同 `user_id` 则合并更新会话，不重复建号。
- 该能力只走人工入口（Web / CLI），**不向 Agent 开放**。
- Web 支持三种方式：本机上传 `.session`/zip、服务端路径、粘贴 StringSession。

API：`POST /api/accounts/import-sessions`、`POST /api/accounts/import-session-strings`、`POST /api/accounts/import-session-upload`。

## 从 Telegram Desktop tdata 导入

tdata 是桌面端的本地加密目录（`key_datas` + `D877F783D5D3EF8C*` 分片），内含 `auth_key` / `dc_id`。
解析由**内置纯 Python 解析器**完成，**无需 opentele**；兼容 Telegram Desktop 6.x。

```bash
python -m tam.cli import-tdata /path/to/tdata --label 主号 --proxy socks5://127.0.0.1:1080
python -m tam.cli import-tdata /path/to/tdata --password 本地密码        # 桌面端设了 passcode
python -m tam.cli import-tdata /path/to/号包目录 --scan --tags tdata   # 批量扫描子目录
```

行为：

- 转换链路：tdata → StringSession → 连接验证可用 → Fernet 加密入库；**全程不落明文 `.session` 文件**。
- 一份 tdata 含多个账号时逐个导入，别名自动加后缀；已有相同 `user_id` 则合并更新，不重复建号。
- 会话失效的条目不会在库里留垃圾行，错误单独回报。
- 默认使用桌面端官方 API 指纹 + `UseCurrentSession`（复用原授权，不额外产生新登录记录）；加 `--own-api` 才改用自己的 API ID，风控更高，不推荐。
- 导入后桌面端仍在线，两端共用同一授权；避免两边同时高频操作。
- 该能力归入 `HUMAN_ONLY`，**不向 Agent 开放**，只能人工走 CLI。

## 架构

```
tam/
├── config.py     环境配置（.env）
├── crypto.py     scrypt + Fernet：session 静态加密
├── db.py         SQLite：accounts / audit_log（含 code_url 取码链接）
├── importer.py   批量解析导入 手机号|取码链接 清单
├── codefetch.py  从取码链接抽取登录验证码（标准库，支持代理与轮询）
├── tdata.py / tdata_native.py   tdata 扫描与内置解析（无需 opentele）
├── ratelimit.py  令牌桶限速 + 人性化随机延迟
├── manager.py    Telethon 封装：登录、会话上下文、健康检查、批量调度
├── tools.py      AI 控制层：带 JSON Schema 的工具注册表 + 统一调用与安全策略
├── mcp_server.py MCP stdio 服务端（零依赖，供 Claude/Cursor 等接入）
├── api.py        FastAPI REST + Web 控制台
├── cli.py        命令行
└── web/index.html 单页控制台
```

### 关键设计

| 问题 | 方案 |
| --- | --- |
| 会话凭证泄露 | 只存 StringSession 密文；主密钥仅在环境变量，不入库 |
| 同账号并发冲突 | 每账号 asyncio.Lock，同一时刻仅一个活跃客户端 |
| 账号关联风险 | 每账号独立 socks5/http 代理 + 独立设备指纹（device_model / app_version / lang_code） |
| FloodWait / 限流 | 每账号令牌桶（默认 0.5 QPS）+ 批量动作 8–25 秒随机间隔 + 受控并发信号量 |
| 封号与失效感知 | health_check 区分 active / restricted / unauthorized / banned / flood_wait 并落库 |
| 可追溯 | 所有写操作进 audit_log，Web 控制台可查看 |

## REST 接口

所有 `/api/*` 需 `Authorization: Bearer <TAM_WEB_TOKEN>`。若向导里选了“本机免令牌”（`TAM_NO_AUTH=1` 且 `TAM_WEB_TOKEN` 为空），则不需要任何头部，直接访问即可。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /api/accounts | 账号列表（不含会话） |
| POST | /api/accounts | 新增账号 |
| PATCH/DELETE | /api/accounts/{id} | 修改 / 删除 |
| POST | /api/accounts/{id}/login/code | 发送验证码 |
| POST | /api/accounts/{id}/login/verify | 提交验证码（含两步验证） |
| POST | /api/accounts/{id}/session/import | 导入已有 StringSession |
| POST | /api/accounts/{id}/check | 健康检查 |
| GET | /api/accounts/{id}/dialogs | 会话列表 |
| GET | /api/accounts/{id}/devices | 已登录设备 |
| POST | /api/accounts/{id}/devices/terminate | 踢掉其他设备 |
| POST | /api/accounts/{id}/message | 发送消息 |
| POST | /api/accounts/{id}/login/auto | 自动取码登录（需 code_url） |
| POST | /api/accounts/import | 批量导入 `手机号\|取码链接`（支持 dry_run） |
| POST | /api/accounts/import-sessions | 批量导入 `.session` 文件（服务端本地路径） |
| POST | /api/accounts/import-session-strings | 批量导入 StringSession 文本 |
| POST | /api/accounts/import-session-upload | 浏览器上传 `.session` / zip（原始请求体） |
| POST | /api/accounts/import-tdata | 从 tdata 目录导入（可递归扫描） |
| POST | /api/batch/check、/api/batch/message、/api/batch/auto-login | 批量操作 |
| GET | /api/logs、/api/stats | 日志与统计 |
| GET | /api/autokick | 自动清设备的开关、周期、待处理数量 |
| POST | /api/autokick/run | 立即对到期账号扫一轮 |
| GET | /api/tools | Agent 工具清单 + JSON Schema（接受只读令牌） |
| POST | /api/tools/call | 统一工具调用入口（接受只读令牌） |
| GET | /api/settings | 参数面板：21 项可调参数的当前值与说明 |
| POST | /api/settings、/api/settings/reset | 写回 `.env`（保留原有注释与行顺序）/ 恢复默认 |
| GET | /api/toolbox/ops | 工具箱的 10 项操作及其参数表（前端据此动态渲染） |
| POST | /api/toolbox/{op}/batch | 对库里选中的号批量执行某项操作（严格保序返回） |
| POST | /api/tools/unpack/analyze | 只看包里有多少个号，不落盘 |
| POST | /api/tools/unpack?fmt=&workers= | 拆包，`-9-` 或 `5,5,5` |
| POST | /api/tools/merge/add?job= | 分次上传要合并的包（攒到同一个作业，上限 50） |
| POST | /api/tools/merge/{job}/run?workers= | 执行合并，同名自动改名不丢号，完事立即删源包 |
| POST | /api/tools/regtime?workers= | 按注册日期分类打包（默认完全离线） |
| GET | /api/tools/unpack/{job}/{filename} | 取结果包（带鉴权，所以网页端是 fetch 成 blob 再保存） |
| DELETE | /api/tools/unpack/{job} | 用完删除。号包是敏感物，不要赖在服务器上 |

## 给 AI / Agent 的控制手段

Agent 可调用能力注册在 `tam/tools.py`（`@tool`）。网页「工具箱」另有一份注册表 `tam/toolbox.py`。

**增删工具时两边要同步：** 只改 `toolbox.py` 则仅网页可用；只改 `tools.py` 则仅 AI/MCP 可用。需要对 AI 隐藏的能力不要写入 `tools.py`（或列入 `HUMAN_ONLY`）。详见 `FOR_AI.md` §8–§9。

| 入口 | 用法 |
| --- | --- |
| MCP | `python -m tam.cli mcp`（stdio，实现 initialize / tools/list / tools/call） |
| CLI | `python -m tam.cli tools`、`python -m tam.cli call send_message '{"account_id":1,"peer":"@me","text":"hi"}'` |
| HTTP | `GET /api/tools`、`POST /api/tools/call` |
| OpenAPI | `/openapi.json`、`/docs`（全部 REST 路由自带 operation_id） |
| Python | `from tam.tools import ToolContext, call_tool, list_tools` |

MCP 客户端配置示例：

```json
{"mcpServers": {"tam": {"command": "python", "args": ["-m", "tam.mcp_server"], "cwd": "/path/to/tam"}}}
```

工具清单（`danger` 标注风险等级）：

| 等级 | 工具 |
| --- | --- |
| read | list_accounts、get_account、stats、read_logs、health_check、list_dialogs、list_devices、preview_import、spintax_preview、healthy_accounts、proxy_audit、autokick_status |
| write | add_account、update_account、import_accounts、spam_check |
| destructive | auto_login、send_message、update_profile、terminate_other_devices、delete_account、warmup、autokick_run |

### 登录满 24 小时自动踢出其它设备

- `TAM_AUTO_KICK_HOURS=24` 全局开关（0 = 关闭）；账号级开关是 `auto_kick` 字段（Web 抽屉里的“自动清设备”勾选框）。
- 计时起点是 **本机接管时间** `adopted_at`（导入 tdata / StringSession / 验证码登录拿到会话那一刻），不是服务端的会话创建时间。
- 踢完会回拉一次会话列表校验：只有确认“只剩本机”才算成功并重新起算周期；还剩会话或报错则排一个重试。
- `TAM_KICK_RETRY=1h` 失败后的重试间隔，支持秒/分/时写法：`45s`、`10m`、`2h`、`1h30m`，纯数字按秒，最小 10 秒。
  也可以在 Web 顶栏“失败重试”输入框里改（存在数据库 `settings` 表，优先于 .env；留空保存即恢复默认），
  或调 `POST /api/autokick/retry {"value":"10m"}`；`POST /api/autokick/run {"retry":"30s"}` 只对本轮生效。
- 服务启动后后台每 10 分钟扫一轮；计时基准 = `kick_retry_at` > `last_kick_at` > `adopted_at` > `login_at` > `created_at`，满 N 小时就调一次“踢掉其他设备”，并写 `auto_kick` 审计日志。
- 为什么是 24 小时：Telegram 对新会话有 24 小时保护期，期内新会话无权注销其它会话，提前调只会报错。
- `banned / unauthorized / frozen / spam_block_perm` 的号自动跳过；失败不再把周期推满一天，而是按 `TAM_KICK_RETRY` 排重试（默认 1 小时）。
- 手动跑一轮：Web 顶部“立即执行”按钮，或 `POST /api/autokick/run`。

约定：

- 统一返回 `{"ok": bool, "tool": str, "result" | "error": {...}}`，工具调用**从不抛异常**，错误码固定为 `unknown_tool / bad_request / not_found / conflict / forbidden / readonly / error`。
- `destructive` 工具不传 `confirm: true` 时只返回预览（`executed: false`），不产生任何对外副作用。
- 参数用 JSON Schema 校验，未知参数与缺失必填参数直接拒绝，避免模型幻觉参数静默生效。
- `TAM_READONLY=1` 或使用 `TAM_READONLY_TOKEN` 时，写入类工具从清单中消失并被拒绝——推荐给 Agent 单独发只读令牌。
- `TAM_DRY_RUN=1` 全局干跑；`TAM_PEER_ALLOWLIST` 限定可发送对象。
- 登录、导入/导出会话**不对 Agent 开放**，只能人工走 CLI/Web，防止凭证被模型读走。
- CLI 全面非交互化：`login --send-code` / `--code` / `--password` / `--non-interactive`，`list --json`，失败退出码非零。

自检：`for f in tests/test_*.py; do python3 "$f" || break; done`；内核另有 `python3 tam/tests/t_zhenghe.py`、`t_shaireg.py`、`t_zip_concurrency.py`、`t_coerce_setting.py`、`t_toolbox.py`

## 免令牌模式（本机自用）

启动向导（`start.bat` / `start.sh` / `python setup.py`）会问一句：

```
? 启用访问令牌？（本机自用选 n）(Y/n)
```

- 选 `n`：写入 `TAM_WEB_TOKEN=` （空）+ `TAM_NO_AUTH=1`，浏览器打开 http://127.0.0.1:8848 直接可用，页面右上角令牌框留空。
- 选 `y`：没令牌就生成一个，已有则保持不变。
- 不想被问：`python setup.py --no-token` 或 `python setup.py --token`。
- 一键体检会识别 `TAM_NO_AUTH=1`，不再把空令牌当错误自动生成。

服务默认只监听 127.0.0.1，外部连不进来；**一旦改成 0.0.0.0 或放到服务器，必须重新启用令牌。**

## 布局编辑模式（仪表盘式网格）

右上角点【编辑布局】进入，再点【完成】退出。布局引擎是二维网格矩阵（坐标 x/y + 跨度 w/h），与 Grafana、react-grid-layout 同类机制。

| 操作 | 效果 |
| --- | --- |
| 拖控件中间 | 自由移位；拖动时显示虚线落点框，撞到的控件会被推开，松手后整体向上收紧 |
| 拖右边缘 | 改宽，吸附到网格列 |
| 拖下边缘 | 改高，吸附到 40px 行 |
| 拖右下角 | 同时改宽高；双击该角恢复默认尺寸 |
| 点右上角 × | 隐藏控件，底部恢复条可加回来（带撤销） |
| 一键整理 | 所有控件向上收紧，消除空隙 |
| 导出 / 导入布局 | 存为 `tam-layout.json`，可在其它电脑或浏览器导入 |
| 全部恢复默认 | 清空本地布局并刷新 |

说明：

- 控件粒度：每个统计数字、自动清设备提示条、账号列表、日志中文摘要、日志原始 JSON 各是独立控件。
- 响应式断点：宽屏 12 列 / 中屏 8 列 / 窄屏 4 列，**三套布局分开保存**，在手机上改动不会弄乱电脑上的布局。
- 手机触屏：长按约 0.2 秒才起拖，快滑仍然是页面滚动；拖到屏幕上下边缘会自动滚动。
- 性能：拖拽走 `requestAnimationFrame` + `translate3d`，实测单帧重算加渲染约 1.5ms。
- 布局存在浏览器 `localStorage['tam.layout']`（v3），换浏览器用导出/导入搬迁。

## 安全与合规

- 服务默认只监听 `127.0.0.1`；对外暴露必须加 HTTPS 反代与强令牌。
- `data/` 目录含加密会话，勿进版本库；建议整盘或目录级加密。
- 主密钥丢失 = 所有会话不可恢复；请离线备份。
- 仅用于管理**你自己拥有**的账号。群发、批量拉人、自动化骚扰违反 Telegram 服务条款，可能导致账号被限制或永久封禁；本项目默认参数偏保守，请勿调高。

## 常见问题

### tdata 导入报 `BaseException: err`

完整报错形如 `opentele/utils.py, line 121, in __new__ / raise BaseException("err")`，
前面还会打印一行 `[__firstlineno__] ...`。

原因：opentele 尚不支持 Python 3.13（上游 issue #133 / #145 至今未修）。
Python 3.13 给类新增了 `__firstlineno__` / `__static_attributes__`，opentele 的
`extend_class` 校验不认，直接抛 `BaseException`（注意：它不是 `Exception` 子类，
常规 `except` 拦不住）。

最省事：`python -m tam.cli doctor --fix`，或在 Web 控制台点“一键体检”，会自动完成下面第 1 种修复。

手动方案二选一：

1. 打兼容补丁（快，可回滚）：
   ```bash
   python -m tam.cli fix-opentele          # 打补丁并验证导入
   python -m tam.cli fix-opentele --status # 只看状态
   python -m tam.cli fix-opentele --revert # 还原
   ```
   补丁只把那句断言性的 `raise` 改成放行，修改前会备份为 `utils.py.tam-bak`。
   Web 控制台导入 tdata 时也会自动尝试这一步。

2. 改用 Python 3.12 重建虚拟环境（最稳妥）：
   ```powershell
   rmdir /s /q .venv
   py -3.12 -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   pip install -r requirements-optional.txt
   ```

### 导入报 `{"detail":"unauthorized"}` 或界面全是 401

Web 页面右上角的令牌要和 `.env` 里的 `TAM_WEB_TOKEN` 一致，输完按回车生效。

### opentele 没装上

网页端 **tdata 导入已改用内置解析器，不需要 opentele**。
`opentele` 仅在机器人前端部分旧功能可能用到，是可选依赖：`pip install -r requirements-optional.txt`。
未安装不影响 Web 控制台与 CLI 的 tdata / session 导入。

## 已知限制

- 官方客户端同时登录上限为 3 个账号（Premium 4 个），本工具用独立 API 会话不受该限制，但每次新增授权都会出现在“已登录设备”里。
- 新号（尤其虚拟号）在冷却期内易触发 `PeerFloodError`，建议先养号：完善资料、正常收发、逐步提升活跃度。
- 同一 IP 下多账号高频活动会显著提高关联与封禁概率，建议一号一代理。

## 任务中心 / 线索库（v1.8）

把“对一批目标逐个执行”变成可观测、可停止的任务，完整链路是：
**采集目标 → 建任务 → 富文本群发 → 逐目标看进度和失败原因**。

### 最近发言人采集

- WebUI 「任务中心 → 采集发言人」，填群组与天数。
- 只取最近真实发过言的人，自动跳过机器人、已注销账号，减少对长期不活跃成员的无效触达。
- 结果自动入线索库，按 (来源, user_id) 去重，重复采集只更新活跃度。
- API：`POST /api/tasks/collect`

### 线索库

- 按来源统计人数与有用户名比例，支持一键导出 CSV（带 BOM，Excel 直接打开不乱码）。
- 可直接对某个来源“向它群发”，不用手工拷名单。
- 采集时勾选「同时存对话记录」后，Web 上每条线索 / 每个来源可点 **「对话」** 查看发言上下文。
- API：`GET /api/leads`、`GET /api/leads/sources`、`GET /api/leads/messages`、`DELETE /api/leads`

### 富文本群发

- 支持 `{a|b}` 变体（spintax）、`{name}` 个性化变量、HTML 富文本与可点击超链接、附件。
- 多账号轮询发送，带随机间隔；发送前可用 `POST /api/message/preview` 看展开样本与链接。
- API：`POST /api/tasks/message`

### 任务管理

- 进度条 + 成功/失败/已跳过统计，运行中每 3 秒自动刷新。
- “明细”看逐目标结果，失败原因自动归类（如 FloodWaitError 多少个）。
- “停止”为协商式：不再发新内容，剩余目标标为已跳过，已发出的不会撤回。
- API：`GET /api/tasks`、`GET /api/tasks/{id}`、`POST /api/tasks/{id}/stop`、`DELETE /api/tasks/{id}`、`POST /api/tasks/cleanup`
- Agent 工具：`list_tasks`、`get_task`、`list_leads`、`lead_sources`（只读）、`stop_task`（需 confirm）


## 常用环境变量（并发 / ZIP / 清设备）

写在 `.env`，也可在 Web **参数面板**改完自动写回。启动时终端会打印当前 workers / 批量并发 / 清设备重试。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TAM_WORKERS` | `4` | ZIP 拆包 / 合并 / 注册时间分类的并发度，上限 32 |
| `TAM_TOAPI_BASE` | （空） | 网页/机器人转 API 的取码链接前缀，如 `https://api.example.com` |
| `TAM_BATCH_CONCURRENCY` | `3` | 工具箱、批量任务同时开几个号；调大易触频控 |
| `TAM_MAX_EXTRACT_MB` | `512` | 解压后总大小上限，防 zip 炸弹 |
| `TAM_KICK_RETRY` | `1h` | 清设备失败后的重试间隔：`45s` / `10m` / `2h` / `1h30m` |
| `TAM_AUTO_KICK_HOURS` | `24` | 本机接管满多少小时自动踢其它设备；`0` = 关 |
| `TAM_REGTIME_ENDPOINT` | 空 | 留空 = 注册时间分类完全离线，不外发任何号信息 |

完整列表见 `.env.example`。

## 双部署 × 双前端（v1.9）

现在一个命令就能决定“装在哪”和“怎么用”：

```bash
python -m tam.cli run                      # 读 .env 里的 TAM_DEPLOY / TAM_FRONTEND
python -m tam.cli run --frontend both      # 临时覆盖：网页 + 机器人一起跑
python -m tam.cli run --deploy server --frontend bot
python -m tam.cli bot                      # 只跑机器人
python -m tam.cli serve                    # 只跑网页（老命令，没变）
```

### 两个开关

| 变量 | 取值 | 含义 |
| --- | --- | --- |
| `TAM_DEPLOY` | `local` | 本机自用。只绑 `127.0.0.1`，不对外暴露，不强制代理 |
| | `server` | 服务器。绑 `0.0.0.0`，启动前会检查 `proxy.txt` 与 `TAM_MASTER_KEY` |
| `TAM_FRONTEND` | `web` | 只开网页控制台 |
| | `bot` | 只开 Telegram 机器人 |
| | `both` | 两个一起跑，共用同一份 `.env` 与数据目录 |

命令行参数 > 环境变量 > 默认值（`local` + `web`）。

### 两个前端分工不同，不是同一套东西换皮

- **网页控制台**：管你自己**托管在库里**的号——登录、导入 tdata、养号、群发、线索库，以及接管满 24 小时自动清设备（带踢后校验与失败重试）。
- **Telegram 机器人**：管用户**临时上传的号包**——发个 ZIP 进去，处理完回传，不入库。

### 网页控制台（近期能力）

- **AI 助手面板**：右下角 / 顶栏唤出侧栏；OpenAI 兼容 API；权限预设（只读/安全/标准/完整）与逐项工具开关；账号范围限制；对话经服务端执行已授权工具。


- **导入进度**：session / tdata / 字符串导入使用 NDJSON 流式进度，界面显示当前序号与结果。
- **二验**：状态查询（含是否绑定辅助邮箱）、发起/取消官方 2FA 重置；支持单号与多选。
- **导出 tdata**：真 Desktop 目录；缺 `opentele` 时可在界面一键安装。
- **ZIP 工具**：拆包、合并、注册时间、转 API、**格式互转**（session↔tdata）、**Passkey 创建**。
- **热重载**：顶栏按钮真实重启后端进程（`os.execv`），不是刷新页面。
- **错误日志**：服务端异常 / 5xx / 前端报错写入 SQLite + `data/errors.log`，可导出 JSON 上报。

`TAM_FRONTEND=both` 时网页与机器人共享配置与数据目录，但 **Telegram 账号库仅网页/API 使用**；机器人仍以临时号包为主。

### 机器人功能（菜单、命令两种用法都行）

筛活 `/shaihuo`、改 2FA `/twofa`、整合号包 `/merge`、双向测试 `/bidir`、隐私配置 `/privacy`、
格式互转 `/convert`、转 API `/toapi`、防找回 `/norecover`、筛 BAN `/ban`、筛料 `/material`、
清理账号 `/clean`、拆包 `/unpack`、销毁会话 `/destroy`、Passkey `/passkey`、注册时间 `/regtime`。
辅助：`/start` `/help` `/status` `/id`；管理员：`/vip ID`、`/unvip ID`、`/gb`（广播）。

**故意没做的三项**：踢设备、账号登录、在线取码——TAM 自己已经有更好的实现（带校验、带重试、
会话加密入库），机器人里的 `/kick`、`/login` 只负责把你引到那边，不重复造一份。

### 付费 / VIP

`OKPAY_COST` 留空就是**不收费**，任何人进来直接是 VIP（自用场景默认这样）。
填了 `OKPAY_ID` / `OKPAY_TOKEN` / `OKPAY_COST` 才会开付费门槛：下单后每 5 秒轮询，5 分钟不付自动关单，
付成自动升 VIP。管理员任何时候可以 `/vip ID` 手动开。

### 服务器部署提醒

`--deploy server` 会绑 `0.0.0.0`。请务必：别开 `TAM_NO_AUTH=1`、设好 `TAM_WEB_TOKEN`、
前面搭一层 HTTPS 反代、`proxy.txt` 配上代理池（否则所有号走服务器裸 IP，风控风险很高）。
启动时这几项没配好会直接在终端警告。

### 启动前体检（该提醒的都写在启动输出里）

`python -m tam.cli run` 会先把当前配置扫一遍，直接打在终端上，不用去翻文档：

```
----------------------------------------------------
  启动前体检（部署=server  前端=bot）
----------------------------------------------------
  ❌  机器人前端的依赖没装齐：python-telegram-bot[job-queue]、opentele
       → pip install -r requirements.txt
       → 只想用网页的话这些不用装，把 TAM_FRONTEND 改成 web 即可
  ❌  服务器模式未设 TAM_WEB_TOKEN，控制台会没有访问门槛。
       → python -m tam.cli init-key 生成一个填进 .env
  ⚠️  服务器模式会监听 0.0.0.0（全网可达），请在前面套一层 HTTPS 反代。
  ⚠️  没找到 proxy.txt，所有号会走服务器裸 IP，风控风险很高。
----------------------------------------------------
```

- **❌ 致命错误会直接阻断启动**（缺依赖、缺 Token、服务器模式下 `TAM_NO_AUTH=1` 或没 `TAM_WEB_TOKEN`），
  真知道自己在干什么可以加 `--force` 继续。
- **⚠️ 警告只提醒不拦**（没代理池、没管理员 ID、没配付费等于白嫖、密钥落盘……）。
- 依赖按前端分开查：只跑网页不会因为没装 `python-telegram-bot` 而报错，反之亦然。
- 缺 `TAM_MASTER_KEY` 这类问题也会输出一行人话提示，而不是抛一屏 traceback。
