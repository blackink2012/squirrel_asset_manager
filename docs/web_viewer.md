# 网页版资产浏览器（web_viewer）

> Squirrel Asset Manager 的浏览器版本：**只读浏览**，无创建/编辑/删除资产功能。
> 参考 fab.com 的视觉与交互风格（深色主题、左侧分类树 + 卡片网格），
> 分类体系与 Maya 插件完全一致（读取同一 config.json 与磁盘结构）。

## 快速开始

双击 `squirrel_asset_manager/启动网页浏览.bat`，或命令行：

```bash
python web_viewer.py                 # 默认端口 8765，自动打开浏览器
python web_viewer.py --port 9000     # 自定义端口
python web_viewer.py --library D:/AssetLib --no-browser
```

启动后浏览器自动打开 `http://127.0.0.1:8765/`。

## 资产库路径设置

- **优先级**：`--library` 参数 > `~/.squirrel_asset_manager/app_settings.json` 的 `last_library_path`
- 与 Maya 插件**共用同一个配置文件**：在网页「设置」中修改路径会写回 `last_library_path`，
  下次打开 Maya 插件时也会读取该路径，反之亦然。
- 网页内提供「浏览…」服务端文件夹选择器，也可以直接粘贴路径。
- 路径要求：包含 `materials / models / lights / textures / scenes / hdr / ani`
  这些子文件夹的目录（即资产库根目录）。

## 功能清单（只读）

| 功能 | 说明 |
|------|------|
| 资产库加载 | 扫描 `.zasset` 文件夹，读 `meta.json` / `FolderMetadata.fdata`，478 资产约 0.5s |
| 分类浏览 | 左侧子库导航（材质/模型/灯光/贴图/场景/HDR/动态）+ 分类树 + 数量统计 |
| 标签筛选 | 按当前子库动态显示标签云，多选过滤（与 assets 实际标签一致） |
| 搜索 | 名称/中文名/标签/节点类型/渲染器/软件 多关键词 AND 匹配 |
| 排序 | 最新 / 最早 / 名称 A-Z / 名称 Z-A |
| 卡片网格 | 懒加载缩略图（IntersectionObserver）、hover 放大、动图标识、分辨率/节点类型角标 |
| 详情弹窗 | 大图预览（GIF 动图 / MP4 / PNG 自动选择）、多张预览图时下方缩略图条切换（含 textures 贴图）、元数据表、贴图文件清单（点击查看原图）、包含文件、复制路径 |
| 设置 | 资产库路径修改、扫描状态、文件夹浏览选择器 |

## 技术说明

- **零第三方依赖**：纯 Python 标准库（`http.server` + `json` + `os`），任意 Python 3.8+ 可运行，
  不依赖 PySide6 / Maya / core 包编译产物（.pyd），前端原生 JS 无框架。
- **只读安全**：所有接口只读；`/api/file` 做路径越界检查（拒绝 `..`），
  仅返回资产文件夹内部文件；不提供任何写接口。
- **与插件共享**：
  - 分类/子库配置：`Assets/preset/config.json`（新增子库、分类、颜色自动生效）
  - 库路径：`~/.squirrel_asset_manager/app_settings.json`
  - 磁盘格式：`.zasset` 文件夹 + `FolderMetadata.fdata`（与 manager.py 解析逻辑一致）

## API 一览

| 接口 | 说明 |
|------|------|
| `GET /api/state` | 库状态 + 分类树 + 标签云（不含资产明细） |
| `GET /api/assets?lib=&category=&tags=&q=&sort=&page=` | 资产列表（服务端过滤/搜索/排序/分页） |
| `GET /api/asset/<id>` | 资产详情（含贴图清单、包含文件） |
| `GET /api/thumb/<id>` | 缩略图（sicon PNG / aicon GIF） |
| `GET /api/media/<id>` | 动图/视频（thumb.mp4 / thumb.aicon） |
| `GET /api/file/<id>?rel=` | zasset 内部文件（贴图预览，路径安全检查） |
| `GET /api/fs?path=` | 服务端文件夹浏览（设置库路径用） |
| `POST /api/library` | 设置库路径（写回 app_settings.json） |
| `POST /api/refresh` | 重新扫描 |

## 目录结构

```
web_viewer/
├── scanner.py        # 资产库只读扫描器（分类树/标签云/计数）
├── server.py         # HTTP 服务 + JSON API + 静态资源
├── static/
│   ├── index.html    # 页面骨架
│   ├── app.css       # fab.com 风格深色主题
│   └── app.js        # 前端逻辑（原生 JS）
web_viewer.py         # 入口（sys.path 兼容处理）
启动网页浏览.bat       # 双击启动
```

## 已知限制

- 无拼音首字母搜索（Maya 插件有）；如需要可在 `/api/assets` 增加服务端拼音过滤。
- 缩略图 `.sicon`/`.aicon` 按魔数识别格式，兼容 PNG/GIF/JPEG/WebP。
- 详情页贴图预览支持 png/jpg/gif/webp/bmp；exr/hdr/tga/tiff/psd 仅显示文件信息。
- 服务器仅监听 `127.0.0.1`，不对外网开放。
