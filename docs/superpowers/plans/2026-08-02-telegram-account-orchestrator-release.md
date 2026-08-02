# Telegram Account Orchestrator Open-Source Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将现有 Telegram 账号管理项目整理为可公开发布的 `Telegram Account Orchestrator (TAO)` GitHub 仓库，保留完整功能、MIT 许可和内部 `tam` 包名。

**Architecture:** 采用“发布层整理、运行层少改动”的策略。新增标准 Python/GitHub 元数据、许可证、贡献与安全文档；保留 `tam` 代码边界和 `tam/gaf/` 派生区；仅修复会阻止开源验证的测试配置、敏感样例和文档不一致。

**Tech Stack:** Python 3.13, Telethon, FastAPI, python-telegram-bot, pytest, Git, GitHub Actions, CodeGraph。

---

### Task 1: 建立发布基线与忽略规则

**Files:**
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/.gitignore`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/.gitattributes`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/README.md`
- Test: Git tracked-file audit

- [ ] **Step 1: 扩展 `.gitignore`**

加入以下规则，同时保留现有 `data/`、`.env`、`*.session` 规则：

```gitignore
# Python
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
.venv/
venv/

# Runtime secrets and account material
*.session
*.session-journal
tdata/
proxy.txt
*.sqlite
*.sqlite3
*.db
*.zip
*.pem
*.key

# Local application data
data/
logs/
jobs/
unpack/
.env
.env.*
!.env.example

# Local indexes and editor files
.codegraph/
.idea/
.vscode/
```

- [ ] **Step 2: 添加 `.gitattributes`**

```gitattributes
* text=auto eol=lf
*.bat text eol=crlf
*.sh text eol=lf
*.svg linguist-language=SVG
```

- [ ] **Step 3: 初始化默认分支为 `main`**

Run: `git branch -M main`

Expected: `git branch --show-current` prints `main`.

- [ ] **Step 4: 审计将要跟踪的文件**

Run: `git status --short --ignored` and confirm that `.env`, `data/`, session files, databases, ZIP files, `.codegraph/`, and caches are ignored.

- [ ] **Step 5: Commit**

```bash
git add .gitignore .gitattributes
git commit -m "chore: establish public repository hygiene"
```

### Task 2: 添加 Python 包元数据与 MIT 许可证

**Files:**
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/LICENSE`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/pyproject.toml`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/setup.py`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/requirements.txt`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/requirements-optional.txt`

- [ ] **Step 1: 写入 MIT License**

使用标准 MIT 文本，版权行为：

```text
Copyright (c) 2026 soulknight666
```

- [ ] **Step 2: 创建 `pyproject.toml`**

定义项目元数据：

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "telegram-account-orchestrator"
version = "0.1.0"
description = "Self-hosted Telegram multi-account management with Web UI, CLI, Bot, MCP, Telethon, and Telegram Desktop tdata import."
readme = "README.md"
requires-python = ">=3.11"
license = {file = "LICENSE"}
authors = [{name = "soulknight666"}]
dependencies = [
  "telethon>=1.36,<2",
  "cryptography>=42",
  "fastapi>=0.110",
  "uvicorn[standard]>=0.29",
  "pydantic>=2.6",
  "python-socks[asyncio]>=2.4",
]

[project.optional-dependencies]
bot = [
  "python-telegram-bot[job-queue]>=21.0",
  "opentele>=1.15",
  "requests>=2.31",
  "aiohttp>=3.9",
  "cbor2>=5.6",
  "qrcode>=7.4",
  "pillow>=10.0",
]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[project.scripts]
tao = "tam.cli:main"
tao-run = "tam.run:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 3: 对齐依赖声明**

将 `requirements.txt` 与 `pyproject.toml` 的核心依赖保持一致；将 `pytest-asyncio>=0.23` 加入开发依赖，不把它放入运行时依赖。

- [ ] **Step 4: 保留 `setup.py` 向导兼容性**

确认 `python setup.py --auto`、`python setup.py --no-start` 和现有 Windows 启动脚本仍调用向导逻辑；不删除用户现有入口。

- [ ] **Step 5: 验证元数据**

Run: `python -m pip install -e . --no-deps`

Expected: editable install succeeds and `python -c "import tam; print(tam.__file__)"` exits 0.

- [ ] **Step 6: Commit**

```bash
git add LICENSE pyproject.toml setup.py requirements.txt requirements-optional.txt
git commit -m "build: add TAO package metadata and MIT license"
```

### Task 3: 完成 GAFBot 来源声明与敏感样例审计

**Files:**
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/NOTICE.GAFBot`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/README.md`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/.env.example`
- Modify: sample files under `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tests/`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/docs/THIRD_PARTY.md`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tools/audit_public_release.py`

- [ ] **Step 1: 保留完整 GAFBot MIT 文本**

核对 `NOTICE.GAFBot` 与上游 `LICENSE` 的版权行、MIT 条款和来源 URL；在文件开头明确限定派生范围为 `tam/gaf/`。

- [ ] **Step 2: 创建第三方来源说明**

`docs/THIRD_PARTY.md` 至少包含：GAFBot 项目名、上游 URL、MIT 许可证、派生目录、TAO 的适配范围，以及不把 GAFBot 归为 TAO 全部代码的说明。

- [ ] **Step 3: 替换可疑样例数据**

将测试和文档中的真实格式手机号、实时取码 URL、代理账号密码样例改成明确的占位值，例如 `+10000000000`、`https://example.invalid/code`、`user:password@example.invalid:1080`，保持解析器测试覆盖格式而不保留可访问端点。

- [ ] **Step 4: 执行文本扫描**

Run: `python tools/audit_public_release.py`

The script must fail on Telegram bot token patterns, private-key markers, non-example API endpoints, `.session`/`.env`/database files, and proxy credentials; it must print only file paths and line numbers, never secret values.

- [ ] **Step 5: Commit**

```bash
git add NOTICE.GAFBot README.md .env.example docs/THIRD_PARTY.md tests tools/audit_public_release.py
git commit -m "docs: document GAFBot provenance and sanitize examples"
```

### Task 4: 重写 README 与开源协作文档

**Files:**
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/README.md`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/FOR_AI.md`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/CONTRIBUTING.md`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/SECURITY.md`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/CHANGELOG.md`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/docs/ARCHITECTURE.md`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/docs/DEPLOYMENT.md`

- [ ] **Step 1: 统一 TAO 品牌和 SEO 首屏**

README 第一屏使用：

```markdown
# Telegram Account Orchestrator (TAO)

Self-hosted Telegram multi-account management with Web UI, CLI, Bot, MCP, Telethon, and Telegram Desktop tdata import.
```

紧接着列出功能、安装方式、支持的 Python 版本、许可证和 GAFBot 第三方声明。

- [ ] **Step 2: 保留现有中文操作文档**

将当前详细中文章节迁移到清晰的目录结构中，不删除 CLI、Web、Bot、MCP、tdata、ZIP、自动清设备、配置项和已知限制说明。

- [ ] **Step 3: 添加英文搜索入口**

在 README 中增加一个简短英文 Overview、Features、Quick Start、Security 和 License 章节，确保 `Telegram account manager`、`multi-account`、`Telethon`、`self-hosted`、`MCP` 等关键词出现在标题或首屏文字中。

- [ ] **Step 4: 添加协作和安全文档**

`CONTRIBUTING.md` 写明 Windows/Linux 安装、测试命令、代码风格和 PR 要求；`SECURITY.md` 写明不得提交 API 凭据、Bot token、session、tdata、数据库和代理密码；`CHANGELOG.md` 从 `0.1.0` 开始；架构和部署文档解释 `tam` 包、`AccountManager`、`Database`、API、Bot、MCP 和 `tam/gaf/` 边界。

- [ ] **Step 5: 更新 HTML 元数据**

在 `tam/web/index.html` 更新 `<title>`、description、Open Graph title/description，使其使用 TAO 品牌和同一组关键词。

- [ ] **Step 6: Commit**

```bash
git add README.md FOR_AI.md CONTRIBUTING.md SECURITY.md CHANGELOG.md docs/ARCHITECTURE.md docs/DEPLOYMENT.md tam/web/index.html
git commit -m "docs: publish TAO project guide and contributor docs"
```

### Task 5: 修复测试入口并建立 GitHub Actions

**Files:**
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tests/test_doctor.py`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tests/test_opentele_patch.py`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tests/test_tdata_native.py`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tests/test_import.py`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tests/test_config.py`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tests/test_core.py`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tests/test_tdata.py`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tests/test_agent.py`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/tests/conftest.py`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/.github/workflows/ci.yml`

- [ ] **Step 1: 修正临时目录 fixture 名称**

将测试函数签名中的 `tmp: Path` 改为 pytest 内置的 `tmp_path: Path`，并同步函数体变量引用；不改测试意图。

- [ ] **Step 2: 配置异步测试**

在 `pyproject.toml` 使用 `asyncio_mode = "auto"`，并确认异步测试由 `pytest-asyncio` 收集，不再出现 “async def functions are not natively supported”。

- [ ] **Step 3: 让测试临时数据库显式创建父目录**

对直接传入 `Database(Path(tempfile.mkdtemp()) / "t.db")` 的测试，先创建目标目录或统一使用 `tmp_path / "t.db"`，保证 SQLite 在 Windows CI 中能打开。

- [ ] **Step 4: 添加最小测试配置**

`tests/conftest.py` 只放公共 fixture 和环境隔离逻辑，确保测试不读取用户根目录 `.env`，不触碰真实 `data/`。

- [ ] **Step 5: 添加 CI 工作流**

`.github/workflows/ci.yml` 使用 Windows 和 Ubuntu 矩阵，步骤固定为 checkout、Python 3.11/3.12/3.13、安装 `.[dev,bot]`、`python -m compileall -q tam tests`、`python -m pytest -q tests`。

- [ ] **Step 6: 先运行失败基线，再运行修复后测试**

Run before edits: `python -m pytest -q tests` and record the existing failures.

Run after edits: `python -m pytest -q tests`.

Expected after edits: all collected tests pass, with only documented deprecation warnings allowed.

- [ ] **Step 7: Commit**

```bash
git add tests pyproject.toml .github/workflows/ci.yml
git commit -m "test: make the public test suite deterministic in CI"
```

### Task 6: 发布候选验证与 GitHub 元数据准备

**Files:**
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/README.md`
- Modify: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/pyproject.toml`
- Create: `C:/Users/yyj/Desktop/tgac/telegram-account-manager/docs/RELEASE_CHECKLIST.md`

- [ ] **Step 1: 添加发布检查清单**

列出许可证、第三方声明、敏感文件扫描、测试、编译、README 首屏、GitHub topics、默认分支、远端 URL 和版本标签检查项。

- [ ] **Step 2: 运行完整本地验证**

```powershell
python -m compileall -q tam tests
python -m pytest -q tests
python -m pip check
git diff --check
git status --short --ignored
```

Expected: compile succeeds, tests pass, pip reports no broken requirements, diff check has no whitespace errors, and ignored runtime files do not appear as tracked files.

- [ ] **Step 3: 执行 tracked-file 安全审计**

Run: `git ls-files` and `python tools/audit_public_release.py --tracked-only`.

Expected: no `.env`, session, tdata, database, ZIP, proxy list, private key, token, or real credential is tracked.

- [ ] **Step 4: 检查最终包元数据**

Run: `python -c "import importlib.metadata as m; print(m.metadata('telegram-account-orchestrator')['Name']); print(m.version('telegram-account-orchestrator'))"`.

Expected: prints `telegram-account-orchestrator` and `0.1.0`.

- [ ] **Step 5: Commit release candidate**

```bash
git add docs/RELEASE_CHECKLIST.md README.md pyproject.toml
git commit -m "chore: prepare TAO 0.1.0 release candidate"
git tag -a v0.1.0 -m "TAO 0.1.0 initial public release"
```

- [ ] **Step 6: Prepare GitHub repository metadata**

Set repository name to `telegram-account-orchestrator`, description to the approved English one-line summary, homepage to the README/deployment entry point if one exists, and topics to the approved keyword list. Do not push until the user explicitly asks for remote publication.
