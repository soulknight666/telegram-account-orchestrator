# TAO 一条龙 · 单文件 Windows 发行版

只需外发 `TAO-OneStop.exe`，接收方无需安装 Python。放在有写入权限的文件夹后双击，配置 Telegram API 与代理，启动本地服务后打开 Web 控制台使用“一条龙”。

运行时会在 EXE 同级生成 `.env`、`data` 和 `export` 文件夹；这些是使用后的私有数据，后续转发仍只发送 EXE。

生成的号包自动保留于 EXE 同级 `export/tao_export_*/accounts-export.zip`，不同批次独立保存，浏览器下载副本不影响该文件。

单文件程序首次启动会解压内置运行库，请等待数秒。无数字签名时 Windows 可能显示未知发布者提示。

本地构建（使用项目环境，避免全局 pathlib 回移植包冲突）：

```powershell
.venv\Scripts\python.exe tools/build_branding.py
.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath deliverable --workpath build\single-delivery packaging\tao-one-stop.spec
```

成品：`deliverable/TAO-OneStop.exe`。
