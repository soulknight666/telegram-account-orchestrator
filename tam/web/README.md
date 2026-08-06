# Web UI 源码说明

## 文件结构

- **index.src.html** - 开发源文件(HTML 结构)
- **styles.css** - 样式表
- **app.js** - 前端逻辑
- **build.py** - 构建脚本
- **index.html** - 构建产物(单文件部署,自动生成,不手动编辑)

## 开发流程

1. **编辑源文件**: 修改 `index.src.html` / `styles.css` / `app.js`
2. **构建**: `python build.py` (生成 `index.html`)
3. **测试**: 启动服务访问 `http://localhost:8848`

构建脚本将 3 个源文件合并为单个 HTML,保持"开箱即用"的部署体验。

## 注意

- `index.html` 是构建产物,不要手动编辑
- 提交时包含构建后的 `index.html`,确保用户无需构建即可使用
