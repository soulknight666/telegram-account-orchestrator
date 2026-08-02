# TAO 发行版、Windows GUI 与 Linux 无桌面部署设计

## 1. 背景

Telegram Account Orchestrator（TAO）当前主要通过 `start.bat` 和 `start.sh` 进入 `setup.py` 完成依赖安装、配置与启动。Windows 从互联网下载 ZIP 后，文件可能携带 Mark-of-the-Web，双击批处理文件时会显示“未知发布者”安全警告。项目还缺少面向普通用户的桌面配置入口、标准安装包、无桌面 Linux 部署流程和自动化 Release 构建。

本设计为 TAO 增加 Windows 10/11 x64 桌面发行版、Linux x64 部署发行版、Docker 镜像及 GitHub Release 自动构建。

## 2. 目标

- Windows 用户通过标准 `.exe` GUI 完成配置、检查、启动、停止和查看日志，不再把 `start.bat` 作为主要入口。
- 提供 Windows 便携 ZIP 与 Inno Setup 安装包。
- Linux 在没有桌面环境时，仍可通过终端向导完成全部必要配置。
- Linux 高级配置可通过 SSH 隧道连接一次性 Web 配置页完成，不直接把配置端口暴露到公网。
- 提供 Docker Compose 和原生 systemd 两种 Linux 部署方式。
- Windows、Linux、CLI 和 Web 配置入口共用同一套配置模型、校验规则和 `.env` 写入逻辑。
- 创建版本标签后自动测试、构建、扫描并发布产物及 SHA-256 校验文件。
- 保留现有 Web、Bot、CLI、MCP、支付/VIP、tdata/session 导入能力。

## 3. 范围边界

- 第一版桌面发行仅支持 Windows 10/11 x64。
- Linux 第一版目标为主流 x64 服务器发行版，重点覆盖 Ubuntu/Debian；Docker 方式作为跨发行版首选。
- Linux 不安装桌面组件。所谓 Linux GUI 指通过用户自己电脑的浏览器访问服务器本机配置页。
- 第一版不增加常驻系统托盘功能；关闭 Windows GUI 时明确询问是否停止后台服务。
- 第一版不制作 macOS 桌面包。
- 当前没有 Authenticode 代码签名证书。构建流程预留签名步骤和 GitHub Secrets 接口；未签名 GitHub 下载文件首次运行仍可能出现 SmartScreen 提示，但日常启动不再触发批处理文件的未知发布者对话框。

## 4. 总体架构

### 4.1 共享配置核心

新增独立配置模块，负责：

- 定义基础配置与高级配置字段。
- 读取 `.env` 并转换为类型化配置对象。
- 校验端口、部署模式、前端模式、凭据格式、目录和服务器安全要求。
- 自动生成 `TAM_MASTER_KEY` 与 `TAM_WEB_TOKEN`。
- 对 `.env` 执行备份、原子写入和权限收紧。
- 为 GUI、终端向导和 Web 配置页返回结构化错误，不直接依赖任何界面框架。

数据流为：

`GUI/CLI/Web 表单 -> 类型化配置模型 -> 校验 -> .env.bak -> 原子替换 .env -> preflight -> 启动运行时`。

### 4.2 共享进程管理核心

新增进程控制模块，负责：

- 根据配置构造 TAO 运行参数。
- 启动、停止和重启后台运行时。
- 检测端口占用和服务健康状态。
- 按行读取标准输出与标准错误并发送给 GUI 日志面板。
- 保存退出码、退出时间和最近错误。
- Windows 下隐藏后台控制台窗口。

Windows 冻结程序使用同一个可执行文件的双模式入口：默认进入 GUI；传入内部 `--runtime` 参数时进入 TAO 服务运行模式。这样无需依赖系统 Python，也避免冻结程序再次执行 `python -m tam.run`。

## 5. Windows 桌面发行版

### 5.1 技术方案

- GUI：Python 标准库 Tkinter/ttk。
- 冻结：PyInstaller `onedir` 模式。
- 安装器：Inno Setup。
- 运行时：打包后的 Python、TAO 包、Web 静态文件及全部必需依赖。

选择 `onedir` 而非 `onefile`，以减少启动解压等待、降低临时目录问题并改善杀毒软件兼容性。

### 5.2 界面布局

采用已确认的控制台式布局：

- 左侧导航：概览、基础配置、高级选项、运行日志。
- 顶部品牌区：TAO 名称与用户指定图片制作的应用图标。
- 概览：运行状态、模式、监听地址、配置状态和主要操作按钮。
- 基础配置：部署模式、前端模式、端口、API ID/API Hash、Bot Token、访问令牌、默认代理。
- 高级选项：并发数、工作目录、日志级别及受支持的扩展环境变量。
- 运行日志：实时日志、清空显示、复制、打开日志目录。

主要操作包括：保存配置、环境检测、启动、停止、重启、打开 Web 控制台。

### 5.3 Windows 配置与数据位置

- 安装版程序：`%ProgramFiles%\TAO`。
- 用户配置与数据：`%APPDATA%\TAO` 或用户明确选择的可写目录。
- 便携版默认使用程序目录旁的 `data` 目录，并允许切换到用户目录模式。
- 安装或升级程序不覆盖 `.env`、数据库、session、tdata、代理文件和日志。

### 5.4 图标

使用用户提供的 `HOYKZOGbkAAVtGl.png` 作为品牌源图，生成：

- Windows 多尺寸 `tao.ico`：16、24、32、48、64、128、256 像素。
- Tkinter 窗口 PNG 图标。
- Inno Setup 安装器和卸载器图标。
- GitHub Release 说明中的应用截图品牌元素。

源图采用居中裁切到正方形，保留人物主体，不叠加文字。

## 6. Linux 无桌面部署

### 6.1 Docker Compose

提供 `docker-compose.yml` 和生产镜像：

- 镜像发布到 `ghcr.io/soulknight666/telegram-account-orchestrator`。
- 配置目录挂载到 `/config`。
- 持久数据挂载到 `/data`。
- 默认 Web 端口为 `8848`。
- 提供健康检查与受控重启策略。
- 容器使用非 root 用户运行。

标准流程：

```bash
git clone https://github.com/soulknight666/telegram-account-orchestrator.git
cd telegram-account-orchestrator
./install.sh --docker
docker compose up -d
```

### 6.2 原生 systemd

`install.sh --systemd` 执行：

- 检查 Python 3.11+、venv 和 systemd。
- 创建低权限系统用户 `tao`。
- 安装程序到 `/opt/tao`。
- 创建虚拟环境并安装锁定依赖。
- 保存配置到 `/etc/tao/tao.env`。
- 保存数据到 `/var/lib/tao`，日志交给 journald。
- 安装并启用 `tao.service`。

升级使用 `install.sh --upgrade`，升级前备份配置并保留所有持久数据。

### 6.3 终端向导与远程 Web 配置

`tao setup --headless` 在纯终端中完成基础配置。高级配置页按需启动，并满足：

- 仅监听服务器 `127.0.0.1`。
- 生成一次性高强度令牌，默认 15 分钟失效。
- 打印对应 SSH 隧道命令，例如：

```bash
ssh -L 8849:127.0.0.1:8849 user@SERVER
```

- 用户在自己电脑访问 `http://127.0.0.1:8849`。
- 配置保存成功或令牌失效后自动关闭配置服务。
- 终端始终提供等价配置能力，因此浏览器步骤不是强制要求。

## 7. 错误处理与数据保护

- 字段错误定位到具体配置项，并给出可执行的修正说明。
- 端口占用时显示端口与可识别的占用进程，并允许修改端口后重试。
- `.env` 写入前创建 `.env.bak`，新内容写入临时文件后再原子替换。
- 主密钥、API Hash、Bot Token、访问令牌和代理密码默认脱敏，不进入普通日志。
- Windows 服务异常退出后保留退出码和完整日志，由用户决定是否重启。
- systemd 和 Docker 采用有限退避重启，避免高频崩溃循环。
- 安装器、升级器和卸载器默认保留用户配置与数据；删除数据需要独立明确操作。

## 8. Release 自动化

新增标签触发的 GitHub Actions 工作流。标签格式为 `v*`，流程为：

1. 在 Ubuntu 与 Windows 上运行现有完整测试。
2. 运行 Python 编译检查和公开发布敏感信息扫描。
3. 构建 Windows PyInstaller 目录。
4. 运行冻结程序冒烟测试。
5. 构建便携 ZIP。
6. 使用 Inno Setup 构建安装包。
7. 构建 Linux tar.gz。
8. 构建并推送 GHCR Docker 镜像。
9. 生成 `SHA256SUMS.txt`。
10. 创建 GitHub Release 并上传全部产物。

预留可选签名步骤：当 GitHub Secrets 中配置证书、密码和时间戳服务后，对 GUI EXE、运行时 EXE 和安装包进行 Authenticode 签名；未配置时继续生成未签名产物并在工作流摘要中标注。

## 9. 测试策略

- 配置模型：解析、默认值、脱敏、必填字段、服务器安全规则。
- 配置写入：备份、原子替换、未知字段保留、写入失败回滚。
- 进程控制：命令构造、启动、停止、崩溃状态、日志流、端口冲突。
- GUI：页面切换、保存校验、按钮状态和后台事件处理的无显示单元测试。
- Windows 打包：在干净 GitHub Actions Runner 中启动 `TAO Launcher.exe --smoke-test`。
- Linux：ShellCheck、安装路径测试、systemd 单元静态验证。
- Docker：构建镜像、启动容器、调用健康端点、停止并验证持久卷。
- Release：验证产物命名、图标资源、版本号和 SHA-256 文件。
- 回归：保留并运行现有全部 pytest 测试。

## 10. Telegram 稳定分享预览

GitHub 仓库页面的 Social Preview 由 GitHub 生成，当前 `og:image` 使用短时效签名地址。README 首图只影响仓库正文展示，不覆盖仓库页面的 Open Graph 元数据。

项目增加一个 GitHub Pages 分享入口：

- 页面地址：`https://soulknight666.github.io/telegram-account-orchestrator/`。
- 页面设置稳定的 `og:title`、`og:description`、`og:image` 和 Twitter Card 元数据。
- `og:image` 指向仓库 Raw 固定资源 `docs/assets/preview.png`，不包含临时签名参数。
- 页面提供仓库简介、主要能力、GitHub 仓库按钮和 Release 下载按钮。
- README 顶部继续展示同一品牌图片，但 Telegram 分享统一推荐 Pages 地址。
- GitHub Actions 部署 Pages，并验证生成页面中的 OG 图片地址为 HTTPS 固定地址。

`github.io` 是 GitHub Pages 的默认托管域名，仓库 Pages 使用 `{账号}.github.io/{仓库名}/` 路径。后续如配置自定义域名，只需替换 Pages 基础地址和 canonical URL。

## 11. 验收标准

- Windows 用户无需运行 `.bat` 即可安装或启动 TAO。
- Windows GUI 能保存配置、运行检查、启动/停止服务、打开控制台并显示日志。
- 安装版和便携版均可在未安装 Python 的 Windows 10/11 x64 环境启动。
- Linux 无桌面环境能仅通过 SSH 终端完成安装和基础配置。
- Linux 高级 Web 配置仅通过本机监听加 SSH 隧道访问。
- Docker Compose 和 systemd 方式均能启动并通过健康检查。
- 配置和数据在升级后保持不变。
- 标签工作流自动生成约定的全部 Release 产物、Docker 镜像和校验文件。
- Telegram 分享 GitHub Pages 地址时使用固定 Raw 预览图，不依赖 GitHub 仓库页的短时效 Social Preview 地址。
- 所有新增测试和现有测试通过，公开发布扫描通过。
