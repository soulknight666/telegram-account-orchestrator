# TAO Frontend Layout Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将 TAO 改造成“深蓝导航 + 明亮账号工作区”的分区式界面，保留现有账号、任务、一条龙、代理、验证码、2FA、导出和布局偏好。

**Architecture:** 保留 #board、data-widget、现有 API 和任务卡作为兼容层，在外层增加应用壳、左侧导航、视图切换和右侧账号详情抽屉。账号清单是默认视图；任务、工具、文件、日志和设置通过导航显示。旧 tam.layout 继续支持导入/导出，默认新布局只迁移视觉层。

**Tech Stack:** 原生 HTML/CSS/JavaScript、tam/web/build.py、Python pytest、Node VM/node:test。

---

## 文件职责

- Modify: tam/web/index.src.html — 应用壳、导航、批量分组、详情抽屉。
- Modify: tam/web/styles.css — 深蓝导航、明亮工作区、紧凑表格、抽屉、响应式。
- Modify: tam/web/app.js — 视图状态、导航折叠、分组状态、抽屉、布局迁移。
- Modify: tests/test_web_build.py — 构建和 DOM 契约。
- Create: tests/layout_ux.test.cjs — 无浏览器的导航、分组、抽屉、任务卡回归。
- Generated: tam/web/index.html — 只由 python tam/web/build.py 生成。
- Create: tmp/frontend-layout-redesign-20260921/ — 基线哈希、差异、验证、回滚。

### Task 1: 建立回归基线

**Files:** tests/test_web_build.py, tests/layout_ux.test.cjs, tmp/frontend-layout-redesign-20260921/baseline-sha256.json

- [ ] Step 1: 保存基线文件。执行：
    $out = 'tmp/frontend-layout-redesign-20260921'
    New-Item -ItemType Directory -Force $out | Out-Null
    Get-FileHash tam/web/app.js,tam/web/index.src.html,tam/web/styles.css,tam/web/index.html -Algorithm SHA256 | Select-Object Path,Hash | ConvertTo-Json | Set-Content "$out/baseline-sha256.json"

- [ ] Step 2: 在 tests/test_web_build.py 加入 test_layout_shell_contract_is_present，断言源文件包含 appShell、sideNav、workspace、accountDetailDrawer、data-nav=accounts、data-nav=tasks，且 CSS_PLACEHOLDER/JS_PLACEHOLDER 各只有一处。

- [ ] Step 3: 创建 tests/layout_ux.test.cjs。读取 tam/web/index.src.html，断言 appShell、sideNav、bulkGroupLogin、bulkGroupProxy、bulkGroupExport、bulkGroupTools、accountDetailDrawer 存在；先执行 pytest tests/test_web_build.py -q 与 node tests/layout_ux.test.cjs，确认新契约在实现前失败。

- [ ] Step 4: 提交测试基线。
    git add tests/test_web_build.py tests/layout_ux.test.cjs tmp/frontend-layout-redesign-20260921
    git commit -m "test: add frontend layout regression baseline"

### Task 2: 增加应用壳和左侧导航

**Files:** tam/web/index.src.html, tam/web/styles.css

- [ ] Step 1: 在原 header、demo bar 与 main 外层加入 appShell。壳内必须包含：
    <aside id="sideNav" class="side-nav" aria-label="主导航">；
    data-nav="accounts"、"tasks"、"toolbox"、"ziptools"、"logs"、"settings" 六个按钮；
    <button id="navCollapse" onclick="toggleSideNav()">折叠导航</button>；
    <section id="workspace" class="workspace">。
  保留 token、自动刷新、体检、热重载、关闭项目、AI、错误日志、编辑布局、帮助和刷新按钮。

- [ ] Step 2: 在现有 widget 上增加 data-view，不删除 data-widget。accounts、kickbar、leads 和统计卡归 accounts；tasks 归 tasks；toolbox 归 toolbox；ziptools 归 ziptools；logText/logJson 归 logs；settings 归 settings。保留 #board 作为旧布局兼容层。

- [ ] Step 3: styles.css 增加深蓝侧栏、明亮 workspace、导航 active/collapsed 样式；主工作区不再依赖 header 内按钮换行来表达模块层级。运行 python tam/web/build.py、pytest tests/test_web_build.py -q、node --check tam/web/app.js。

- [ ] Step 4: 提交。
    git add tam/web/index.src.html tam/web/styles.css tam/web/index.html
    git commit -m "feat: add workspace shell and primary navigation"

### Task 3: 导航视图状态与旧布局迁移

**Files:** tam/web/app.js, tam/web/styles.css, tests/layout_ux.test.cjs

- [ ] Step 1: 在 Node VM 测试中加载导航函数片段，断言 switchView('tasks') 改变 currentView、只显示 data-view=tasks，toggleSideNav() 切换 nav-collapsed，并使用 tam.view/tam.nav.collapsed 两个 localStorage 键。

- [ ] Step 2: 在 app.js 增加以下状态和接口，不能改变现有 API：
    const viewKey = 'tam.view';
    const navKey = 'tam.nav.collapsed';
    let currentView = 'accounts';
    let navCollapsed = false;
    function loadViewPrefs() { 读取两个 localStorage 键，异常时保持默认值； }
    function switchView(view) { 只接受 accounts/tasks/toolbox/ziptools/logs/settings，保存 view 后调用 applyView； }
    function toggleSideNav() { 切换 navCollapsed，保存后调用 applyView； }
    function applyView() { 更新 nav active/aria-current、data-view 的 view-hidden/aria-hidden，并按需调用 loadTasks/loadOps/renderZt/loadSettings； }

- [ ] Step 3: 初始化顺序改为 loadViewPrefs(); loadLayout(); applyView();；render() 最后调用 applyView()，自动刷新只更新当前视图的数据，不把页面切回 accounts。

- [ ] Step 4: 保留现有 LAY/tam.layout 版本 3、拖动、隐藏、导入和导出。applyView 只能加 view-hidden，不能移除 widget；恢复默认布局时将 view 恢复为 accounts、导航恢复展开。

- [ ] Step 5: 运行 node tests/layout_ux.test.cjs、pytest tests/test_web_build.py -q、node --check tam/web/app.js 并提交：
    git add tam/web/app.js tam/web/styles.css tests/layout_ux.test.cjs tam/web/index.html
    git commit -m "feat: add persistent workspace navigation"

### Task 4: 分组批量操作与紧凑账号工作区

**Files:** tam/web/index.src.html, tam/web/app.js, tam/web/styles.css, tests/layout_ux.test.cjs

- [ ] Step 1: 将平铺 bulk 改成四个 details 分组，保留每个原 onclick：
    bulkGroupLogin：健康检查、自动登录、退出并重登、发消息；
    bulkGroupProxy：代理体检、更换代理、OKPay、节点；
    bulkGroupExport：批量导入、一条龙、session、号包、tdata、取码链接；
    bulkGroupTools：养号、踢出设备、标签、锁定/解锁、重生、删除/注销。
  选择摘要固定在 bulkSelectionSummary，显示已选数量和取消选择。

- [ ] Step 2: 在 app.js 的 render 中保留 sel 语义，更新 bulk.on 和 bulkSelectionSummary.dataset.count；clearSel 只清空选择，不清空筛选。选中账号时默认打开登录分组，其余按需展开。

- [ ] Step 3: CSS 让账号表格外层 .scroll 成为唯一滚动容器，thead th position:sticky；toolbar 和 bulk 使用现有动态 CSS 变量偏移；紧凑行高保持 checkbox、锁、状态、2FA、代理、取码、操作列可读。

- [ ] Step 4: 运行 node tests/layout_ux.test.cjs、pytest tests/test_web_build.py -q，确认所有旧 onclick/API 字符串仍存在，然后提交：
    git add tam/web/index.src.html tam/web/app.js tam/web/styles.css tam/web/index.html tests/layout_ux.test.cjs
    git commit -m "feat: group bulk actions for dense account workspace"

### Task 5: 账号详情抽屉

**Files:** tam/web/index.src.html, tam/web/app.js, tam/web/styles.css, tests/layout_ux.test.cjs

- [ ] Step 1: 在 body 末尾加入 accountDetailDrawer、accountDetailTitle、accountDetailBody 和 accountDetailBackdrop；抽屉初始 aria-hidden=true，遮罩初始 hidden。

- [ ] Step 2: 实现 accountById(id)、openAccountDetail(id)、closeAccountDetail()、renderAccountDetail(a)。详情只读展示手机号、别名、状态、授权、代理、2FA、取码链接、失败分类、标签和现有行级动作；所有文本经 esc()，不重复请求账号列表。

- [ ] Step 3: 账号行的详情按钮调用 openAccountDetail(a.id)；Escape 关闭抽屉，打开时聚焦关闭按钮，遮罩只关闭抽屉。测试现有 id、未知 id、打开/关闭 aria 状态和 XSS 转义。

- [ ] Step 4: 宽屏右侧固定宽度，小屏全屏面板；运行 node tests/layout_ux.test.cjs、node --check tam/web/app.js、pytest tests/test_web_build.py -q，然后提交：
    git add tam/web/index.src.html tam/web/app.js tam/web/styles.css tam/web/index.html tests/layout_ux.test.cjs
    git commit -m "feat: add non-blocking account detail drawer"

### Task 6: 任务中心和长任务视觉分离

**Files:** tam/web/index.src.html, tam/web/app.js, tam/web/styles.css, tests/layout_ux.test.cjs

- [ ] Step 1: 为 taskCard 输出增加 data-task-id、阶段、进度、成功/失败计数、检查点和停止/继续/明细动作；tests/layout_ux.test.cjs 断言这些字段存在。

- [ ] Step 2: 保留 loadTasks 的 /api/tasks?limit=10 轮询；任务中心只更新任务模块，不重建账号表格。uiTask 增加 aria-live="polite" 与“打开任务中心”动作，调用 switchView('tasks')。

- [ ] Step 3: 多任务按更新时间排序，每张任务卡使用唯一 task id；失败显示服务端阶段和错误分类原文；账号页的搜索、筛选、详情抽屉和其他模块不被任务卡遮挡。

- [ ] Step 4: 运行 node tests/layout_ux.test.cjs、node tests/one_stop.test.cjs、pytest tests/test_web_build.py -q 并提交：
    git add tam/web/index.src.html tam/web/app.js tam/web/styles.css tam/web/index.html tests/layout_ux.test.cjs
    git commit -m "feat: separate durable task center from account workspace"

### Task 7: 响应式、无障碍与偏好兼容

**Files:** tam/web/styles.css, tam/web/app.js, tests/test_web_build.py, tests/layout_ux.test.cjs

- [ ] Step 1: 增加断点契约测试，断言 CSS 包含 nav-collapsed、side-nav、account-detail-drawer、drawer-backdrop、bulk-group、overflow-x:auto；HTML 包含 aria-label、aria-hidden、aria-current。

- [ ] Step 2: 宽屏显示侧栏；中屏允许折叠；窄屏侧栏为抽屉、批量栏为底部浮层、表格自身水平滚动。syncStickyOffsets() 继续动态设置 header/toolbar 偏移，禁止硬编码端口或固定偏移。

- [ ] Step 3: loadLayout 遇到旧/损坏 tam.layout 时回退默认 A 方案；不清理账号数据。新增 tam.view 与 tam.nav.collapsed 只保存界面偏好；“恢复默认布局”同时恢复 accounts 和展开导航。

- [ ] Step 4: 运行 node --check tam/web/app.js、node tests/layout_ux.test.cjs、pytest tests/test_web_build.py -q 并提交：
    git add tam/web/app.js tam/web/styles.css tests/test_web_build.py tests/layout_ux.test.cjs tam/web/index.html
    git commit -m "test: cover responsive layout and preference migration"

### Task 8: 构建、全量验证、差异与回滚

**Files:** tam/web/index.html, tmp/frontend-layout-redesign-20260921/

- [ ] Step 1: 执行 python tam/web/build.py、node --check tam/web/app.js、python -m compileall -q tam。

- [ ] Step 2: 执行 pytest tests/test_web_build.py -q、node tests/layout_ux.test.cjs、node tests/toolbox_ux.test.cjs、node tests/one_stop.test.cjs、pytest -q；将命令、退出码、测试数量和四个前端文件哈希写入 tmp/frontend-layout-redesign-20260921/verification.txt。

- [ ] Step 3: 将基线四文件复制到 tmp/frontend-layout-redesign-20260921/baseline/，生成 ux-layout.diff，并写 rollback.ps1。回滚脚本按绝对根目录恢复 app.js、index.src.html、styles.css、index.html，执行 powershell -File rollback.ps1 -WhatIf，再真实执行一次并重新运行 test_web_build.py。

- [ ] Step 4: 复核现有账号表格、自动刷新草稿保护、一条龙、官方验证码、tdata/session/号包/取码链接导出、锁定、热重载和关闭项目 onclick/API 字符串仍保留；确认 index.html 等于 build.py 的源拼接结果。

- [ ] Step 5: 生成并提交：
    git diff -- tam/web/app.js tam/web/index.src.html tam/web/styles.css tam/web/index.html tests/test_web_build.py tests/layout_ux.test.cjs > tmp/frontend-layout-redesign-20260921/ux-layout.diff
    git add tam/web tests/test_web_build.py tests/layout_ux.test.cjs tmp/frontend-layout-redesign-20260921
    git commit -m "feat: deliver TAO workspace layout redesign"

## Plan 自审

- 规格覆盖：Task 2-8 覆盖应用壳、A 方案、账号首页、分组批量栏、紧凑表格、详情抽屉、任务中心、并行操作、响应式、旧偏好兼容和验证/回滚。
- 占位扫描：没有 TBD、TODO 或未定义文件名；新增函数和 DOM id 在对应任务中定义。
- 一致性：currentView/navCollapsed/switchView/toggleSideNav、data-view/data-nav、task id 和任务 API 在各任务中统一。
- 范围：只改前端布局和回归契约，不改账号数据、任务持久化、Telegram 协议或导出格式。

