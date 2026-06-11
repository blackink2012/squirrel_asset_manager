# Squirrel Asset Manager — 项目架构文档

> 基于 CodeGraph 语义索引生成 | 54 文件 · 1,779 节点 · 4,630 调用边 · 7.37 MB SQLite (WAL)

---

## 一、架构分层

```
squirrel_asset_manager/
│
├── core/          数据层 — 纯 Python，无 Qt 依赖
├── ui/            UI 层 — PySide6 信号驱动架构
├── integration/   集成层 — Maya 导出/导入执行器
├── utils/         工具模块 — 设置/JSON/截图/兼容层
├── quicktools/    快捷工具 — 一键转换脚本
└── Assets/        静态资源 — Prompt 模板 / 预设 / 模型 / 贴图
```

---

## 二、core/ — 数据层（9 文件，370 符号）

| 文件 | 核心类 | 符号 | 职责 |
|------|--------|------|------|
| `manager.py` | MaterialManager | 112 | 库管理器 — 加载/搜索/CRUD/收藏/分类/导入导出 |
| `zasset_io.py` | ZassetIO | 62 | .zasset 文件夹读写 — meta/node/thumbnail/textures |
| `export_orchestrator.py` | ExportOrchestrator | 86 | 6 阶段导出管线 — 元数据→贴图→材质→几何体→缩略图→代理 |
| `material.py` | Material | 50 | 材质 @dataclass — from_json/to_dict |
| `ai_analyzer.py` | AIAnalyzer | 20 | **新增** — Ollama 视觉模型分析 |
| `category.py` | Category | 18 | 分类树节点 |
| `zasset_builder.py` | ZassetBuilder | 12 | 构建 .zasset 文件夹 |
| `proxy_registry.py` | ProxyFormatRegistry | 9 | Arnold/V-Ray/Redshift 代理注册 |

### 数据流

```
磁盘 .zasset/
  → ZassetIO.read_meta() / read_thumbnail()
    → Material.from_json()
      → MaterialManager._materials[Dict]
        → Material.to_dict()
          → ThumbnailGridWidget.set_materials()
```

---

## 三、ui/ — UI 层（18 文件，827 符号）

### 3.1 主窗口

| 文件 | 核心类 | 符号 | 职责 |
|------|--------|------|------|
| `main_window.py` | MaterialLibraryWindow | 231 | 主窗口 — QFrame 工具栏 + QSplitter 分栏 + 信号路由 |

**布局结构**：
```
MaterialLibraryWindow (QMainWindow)
├── 工具栏 (QFrame)
│   ├── [☰] 面板切换
│   ├── SearchBarWidget (中央)
│   ├── [↻ 刷新] [🤖 AI 工具] [快捷工具] [设置] [?]
├── QSplitter 水平分割
│   ├── 左侧 — CategoryTreeWidget + FavoritesPanelWidget
│   └── 右侧 — ThumbnailGridWidget + DetailPanelWidget
```

### 3.2 缩略图网格

| 文件 | 核心类 | 符号 | 职责 |
|------|--------|------|------|
| `thumbnail_view.py` | ThumbnailGridWidget | 109 | QStackedWidget 双模式（图标网格+表格列表），虚拟化卡片池 |

**交互能力**：
- Ctrl+滚轮缩放 · Shift/Ctrl 多选 · 框选 · 右键菜单 · 拖拽赋予
- 信号体系：materialSelected / editMaterialRequested / aiAnalysisRequested / deleteRequested / ...

### 3.3 AI 分析对话框（新增）

| 文件 | 核心类 | 符号 | 职责 |
|------|--------|------|------|
| `ai_analysis_dialog.py` | AIAnalysisConfigDialog | 26 | 语言选择 + 模型选择 + 审查开关 |
| | AIBatchResultsDialog | | 可编辑审查窗口 + 400×400 大图预览 |
| | AIAnalysisDialog | | 单资产 AI 分析确认 |

### 3.4 资产导出

| 文件 | 核心类 | 符号 | 职责 |
|------|--------|------|------|
| `asset_create_dialog.py` | AssetCreateDialog | 57 | V3 双列非模态导出对话框 |
| `export_preset_dialog.py` | ExportPresetDialog | 43 | 导出格式预设配置 |
| `name_conflict_dialog.py` | NameConflictDialog | 9 | 同名冲突处理 |

### 3.5 分类与详情

| 文件 | 核心类 | 符号 | 职责 |
|------|--------|------|------|
| `category_tree.py` | CategoryTreeWidget | 54 | 分类树 — 复合 ID `"root_lib\|\|id"` |
| `detail_panel.py` | DetailPanelWidget | 27 | 详情面板 — 材质色球 + FlowLayout 标签 |
| `preview_panel.py` | PreviewPanelWidget | 73 | 3D 预览面板 |
| `favorites_panel.py` | FavoritesPanelWidget | 23 | 收藏夹 |

### 3.6 批量操作

| 文件 | 核心类 | 符号 | 职责 |
|------|--------|------|------|
| `batch_action_bar.py` | BatchActionBar | 15 | 批量操作栏 — 重命名/标签/移动/复制/删除 |
| `batch_rename_dialog.py` | BatchRenameDialog | 14 | 批量重命名 |
| `batch_progress_overlay.py` | BatchProgressOverlay | 10 | 批量进度覆盖层 |

### 3.7 其他 UI

| 文件 | 核心类 | 符号 | 职责 |
|------|--------|------|------|
| `search_bar.py` | SearchBarWidget | 21 | 搜索栏 + 拼音首字母过滤 |
| `settings_dialog.py` | SettingsDialog | 46 | 应用设置对话框 |
| `thumbnail_capture_overlay.py` | ThumbnailCaptureOverlay | 15 | 缩略图截图覆盖层 |
| `variant_import_dialog.py` | — | 12 | 变体（LOD/版本）导入 |

---

## 四、integration/ — Maya 集成层（9 文件，178 符号）

| 文件 | 符号 | 职责 |
|------|------|------|
| `zjg_exporter.py` | 89 | Maya 材质导出核心 — .zmetal / .mcm |
| `import_executor.py` | 27 | 统一导入入口 — 格式路由 |
| `export_connector.py` | 11 | 导出引擎封装（v1.x/v2.0 API 兼容） |
| `texture_importer.py` | 13 | 贴图/HDR 导入 |
| `import_external.py` | 11 | 外部资产导入 |
| `import_extractor.py` | 10 | zasset 资源提取 |
| `file_importer.py` | 8 | 几何体/代理文件导入 |
| `format_router.py` | 8 | 格式类型路由器 |

---

## 五、utils/ — 工具模块（10 文件，211 符号）

| 文件 | 符号 | 职责 |
|------|------|------|
| `zjg_capture.py` | 52 | CaptureTool — 完整截屏/录屏 |
| `standalone_analyzer.py` | 49 | 独立分析器 |
| `json_handler.py` | 37 | JSONHandler — JSON 读写 |
| `settings.py` | 25 | SettingsManager — 应用设置持久化 |
| `maya_plugin_checker.py` | 11 | Maya 插件状态检测 |
| `maya_utils.py` | 10 | PySide6 兼容层 — `get_qt_modules()` / `get_maya_window()` |
| `screen_capture.py` | 8 | ScreenCapture — 通用屏幕截取 |
| `error_handler.py` | 6 | 异常处理装饰器 |
| `mock_data.py` | 4 | MOCK_MATERIALS — 离线开发数据 |

---

## 六、quicktools/ — 快捷工具（3 文件，173 符号）

| 文件 | 符号 | 职责 |
|------|------|------|
| `pbr_to_zasset.py` | 74 | PBR 材质一键转 .zasset |
| `model_to_zasset.py` | 53 | 模型一键转 .zasset |
| `hdr_to_zasset.py` | 46 | HDR 一键转 .zasset |

---

## 七、Assets/ — 静态资源

```
Assets/
├── prompt/           ← AI Prompt 模板（7 分类独立 JSON，修改即时生效）
│   ├── materials.json   材质分析
│   ├── models.json      模型分析
│   ├── lights.json      灯光分析
│   ├── textures.json    贴图分析
│   ├── scenes.json      场景分析
│   ├── hdr.json         HDR 分析
│   └── ani.json         动态资产分析
├── preset/
│   ├── config.json      主配置 — 子库/子分类/标签/预设/文件扩展名
│   ├── export_preset.json  导出格式预设
│   ├── model_mapping.json   模型→材质映射
│   └── pbr_mapping.json     PBR 贴图类型规则
├── HDR_ligt/         预置环境光 .ma 文件
├── IBL/              HDR 天光贴图
├── Meshes/           测试几何体
└── help/             UI 截图
```

---

## 八、AI 分析子系统

### 8.1 数据流

```
用户点击 AI 工具 → AIAnalysisConfigDialog (语言/模型/审查)
  → AIAnalyzer.is_available() 检测 Ollama
  → AIAnalyzer.get_available_models() 拉取模型列表
  → 确认后开始串行分析
    → AIAnalyzer.analyze_image()
      → _build_full_prompt()    读 Assets/prompt/{分类}.json
      → 注入 {sub_categories}   从 config.json 读取实际子分类
      → _call_ollama()          HTTP POST /api/generate
      → _parse_response()       提取 JSON
  → review?
    ✅ Yes → AIBatchResultsDialog (可编辑审查 + 400×400 大图)
    ❌ No  → 直接批量应用
      → MaterialManager.update_material()
```

### 8.2 设计与约束

| 决策 | 原因 |
|------|------|
| 串行处理（非批量喂图） | 避免爆显存，每次仅 1 张缩略图 |
| 子分类动态注入 | 从 config.json 读取实际分类列表，不求精确接近就行 |
| 每个分类独立 Prompt | 材质/模型/灯光/HDR 等分析方向完全不同 |
| 审查后应用 | 用户可在确认窗口编辑 AI 结果后再写入元数据 |

### 8.3 调用链（CodeGraph）

```
ThumbnailGridWidget.aiAnalysisRequested
  → MaterialLibraryWindow._on_ai_analysis             (右键菜单)
  → MaterialLibraryWindow._on_ai_analysis_with_config (工具栏)
    → AIAnalysisConfigDialog
    → MaterialLibraryWindow._on_ai_analysis_batch
      → AIAnalyzer.analyze_image
      → AIBatchResultsDialog / _on_batch_ai_applied
        → MaterialManager.update_material
```

---

## 九、CodeGraph 索引状态

| 指标 | 数值 |
|------|------|
| 索引文件 | 54 |
| 节点总数 | 1,779 |
| 调用边 | 4,630 |
| 数据库 | 7.37 MB SQLite (WAL) |
| 后端 | node:sqlite - built-in |

**节点分布**：
- method 936 · function 279 · import 237
- variable 208 · class 65 · file 54

```
查询示例：
  codegraph query "AIAnalyzer"      # 搜索符号
  codegraph impact "AIAnalyzer"     # 影响面分析
  codegraph files --format tree     # 文件树
  codegraph callers "function_name" # 调用者追溯
```
