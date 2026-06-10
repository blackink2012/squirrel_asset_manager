# squirrel\_asset\_manager — Maya 资产管理系统

Maya 插件级别的资产管理与批量操作平台。支持材质、模型、贴图、灯光、场景、HDR 六类资产的统一存储、检索、导入/导出。

***

## 安装与使用

### 下载

```bash
git clone https://gitee.com/zhangzhangle/MayaSquirrelAssetManager.git
```

克隆后文件夹名为 `MayaSquirrelAssetManager`，**使用前需重命名**为 `squirrel_asset_manager`：

```bash
ren MayaSquirrelAssetManager squirrel_asset_manager
```

### 安装

将`squirrel_asset_manager` 文件夹放入 Maya 脚本路径即可，例如：

**方式一：Maya 2025 脚本目录（推荐）**

```
C:\Users\<用户名>\Documents\maya\2025\scripts\squirrel_asset_manager\
```

**方式二：任意路径 + sys.path 添加**

```python
import sys
sys.path.insert(0, r"D:\你的路径\squirrel_asset_manager")
```

### 调用

在 Maya 脚本编辑器或 Shelf 按钮中执行：

```python
import squirrel_asset_manager
squirrel_asset_manager.show()
```

### 配置资产库

首次启动后，通过菜单 **设置 → 常规 → 资产库管理** 添加你的资产库路径。

资产库目录结构示例：

```
你的资产库/
├── materials/       # 材质
│   ├── 金属/
│   │   ├── Metal_Chrome.zasset
│   │   └── ...
│   └── 布料/
├── models/          # 模型
├── textures/        # 贴图
├── lights/          # 灯光
├── scenes/          # 场景
└── hdr/             # HDR
    ├── outdoor/
    └── indoor/
```

每个 `.zasset` 是一个文件夹资产包。

***

## 目录结构

```
squirrel_asset_manager/           # 插件根目录（自包含，拷贝即用）
├── __init__.py                   # 包入口，暴露 mgr / pmgr 代理
├── core/                         # 核心数据层
│   ├── manager.py                # MaterialManager 管理器（2097行）
│   ├── export_orchestrator.py    # 导出编排器（2535行）
│   ├── category.py               # 分类数据类
│   ├── material.py               # 材质数据类
│   ├── zasset_io.py              # .zasset 文件夹读写
│   ├── zasset_builder.py         # .zasset 构建
│   └── proxy_registry.py         # 代理格式注册表
├── ui/                           # UI 界面层
│   ├── main_window.py            # 主窗口（6056行）
│   ├── thumbnail_view.py         # 缩略图网格/列表视图
│   ├── category_tree.py          # 分类树组件
│   ├── preview_panel.py          # 右侧预览面板
│   ├── detail_panel.py           # 资产详情面板
│   ├── search_bar.py             # 搜索栏
│   ├── favorites_panel.py        # 收藏夹面板
│   ├── batch_action_bar.py       # 批量操作栏
│   ├── batch_rename_dialog.py    # 批量重命名对话框
│   ├── batch_tag_dialog.py       # 批量标签对话框
│   ├── asset_create_dialog.py    # 资产创建/导出对话框
│   ├── export_preset_dialog.py   # 导出预设对话框
│   ├── thumbnail_capture_overlay.py # 截图 Overlay
│   ├── batch_progress_overlay.py # 批量进度覆盖层
│   ├── settings_dialog.py        # 设置对话框
│   ├── name_conflict_dialog.py   # 命名冲突对话框
│   └── variant_import_dialog.py  # 变体导入对话框
├── integration/                  # Maya 集成层
│   ├── import_executor.py        # 统一导入执行器
│   ├── import_extractor.py       # 导入文件定位器
│   ├── import_external.py        # 外部文件夹导入
│   ├── file_importer.py          # 通用 Maya file 导入器
│   ├── texture_importer.py       # 贴图导入器
│   ├── format_router.py          # 格式路由
│   ├── export_connector.py       # 导出引擎封装层
│   └── zjg_exporter.py           # Maya 场景导出器（3718行）
├── utils/                        # 工具函数
│   ├── json_handler.py           # JSON 安全读写
│   ├── settings.py               # 设置持久化管理
│   ├── error_handler.py          # 全局异常处理
│   ├── maya_utils.py             # Maya 通用工具
│   ├── maya_plugin_checker.py    # 渲染器插件检测
│   ├── screen_capture.py         # 屏幕截取工具
│   ├── zjg_capture.py            # 独立截屏录屏工具栏
│   ├── standalone_analyzer.py    # 独立图片分析器
│   └── 预览ma节点连接.py         # .ma 文件节点连接预览
├── quicktools/                   # 快捷工具
│   ├── pbr_to_zasset.py          # PBR 贴图 -> .zasset
│   ├── model_to_zasset.py        # 模型 -> .zasset
│   ├── hdr_to_zasset.py          # HDR -> .zasset
│   ├── 图像格式转换工具.py       # 图像格式批量转换
│   └── 批量镜像复制.py           # 批量镜像复制工具
├── Assets/preset/                # 系统配置文件
│   ├── config.json               # 核心配置（子库/分类/颜色等）
│   ├── export_preset.json        # 导出预设
│   ├── pbr_mapping.json          # PBR 贴图命名规则与映射
│   └── model_mapping.json        # 模型分类规则
└── resources/styles/
    └── main.qss                  # 深色主题样式表
```

***

## 7 个子库

| ID          | 名称  | 默认分类                             |
| ----------- | --- | -------------------------------- |
| `materials` | 材质  | 金属、布料、塑料、玻璃、皮肤、木材、石材、液体、植被、自定义   |
| `models`    | 模型  | 角色、车辆、武器、建筑、家具、自然、道具、机械、电子设备、自定义 |
| `textures`  | 贴图  | 金属贴图、布料贴图、塑料贴图、玻璃贴图、皮肤贴图、自定义等    |
| `lights`    | 灯光  | 方向光、点光源、聚光灯、区域光、影棚灯光、环境光、自定义     |
| `scenes`    | 场景  | 室内、室外、角色场景、影棚、环境、产品展示、自定义        |
| `hdr`       | HDR | 户外、室内、影棚、日落、夜晚、天空、工业、自定义         |
| `ani`       | 动态  | abc、agent 缓存                     |

***

## 核心功能

### 资产存储

- **统一 .zasset 文件夹格式**：每个资产一个文件夹，内含 `meta.json` + `node.zmetal` + `thumb.sicon` + `textures/`
- **已不再使用 ZIP 包格式**，改为纯文件夹即资产

### 双库架构

- **Category 库**：按分类组织的固定资产库
- **Project 库**：按项目组织的独立资产库
- 标签页切换，操作互不干扰

### 搜索与过滤

- 关键词模糊搜索（名称/中文名/标签）
- 分类树浏览（支持单/多选）
- 标签 AND 组合筛选
- 首字母快速定位

### 批量管理

- 批量移动分类（跨子库）
- 批量添加/删除标签
- 批量重命名（前缀/后缀/查找替换）
- 批量删除（两级确认）
- 多选后批量操作栏自动显示

### 导入能力

- 从 Maya 选中物体创建资产（材质/模型/灯光/HDR）
- 外部文件/文件夹导入为 .zasset
- PBR 贴图套件批量导入（自动识别后缀/分辨率）
- HDR/EXR 环境贴图导入（自动生成缩略图）

### 导出能力

- 三种模式：单资产 / 批量全自动 / 批量半自动
- 格式支持：.ma / .mb / .fbx / .obj / .abc / .usd / .glb / .gltf / .ass / .vrmesh / .rs / .zmetal / .mcm
- 每资产自动截图（viewFit + isolate）

### 快捷工具

- **PBR → .zasset**：从贴图文件夹批量构建资产
- **模型 → .zasset**：从模型文件夹批量构建资产
- **HDR → .zasset**：从 HDR/EXR 文件批量构建资产
- **图像格式转换**：多格式互转（PNG/JPG/EXR/HDR/TIFF 等）

***

## 配置体系

所有硬编码已迁移到 JSON 配置文件，通过设置 UI 可编辑：

| 文件                   | 用途                                     |
| -------------------- | -------------------------------------- |
| `config.json`        | 子库列表、默认分类、资产格式、节点类型、分类颜色、Dome Light 预设 |
| `pbr_mapping.json`   | 14 种 PBR 贴图类型的命名规则、6 种着色器属性映射、组合贴图连接方案 |
| `model_mapping.json` | 9 种模型分类与关键词匹配规则                        |
| `export_preset.json` | 7 种资产类型的默认导出格式配置                       |

***

## 渲染器支持

| 渲染器      | 材质节点                                      | 代理格式    |
| -------- | ----------------------------------------- | ------- |
| Arnold   | aiStandardSurface                         | .ass    |
| V-Ray    | VRayMtl                                   | .vrmesh |
| Redshift | RedshiftMaterial                          | .rs     |
| Maya 原生  | standardSurface / lambert / blinn / phong | —       |

***

## .zasset 文件夹结构

```
MyAsset.zasset/
├── meta.json               # 资产元数据（ID/名称/分类/标签/格式等）
├── node.zmetal             # 材质节点属性（可选）
├── thumb.sicon             # 静态缩略图（可选）
├── thumb.aicon             # 动态 GIF 缩略图（可选）
├── thumb.mp4               # MP4 视频缩略图（可选）
├── node.mcm                # 材质-物体映射（可选）
├── node.ma / .fbx / ...   # 几何体文件（可选）
├── node.ass / .vrmesh      # 代理文件（可选）
└── textures/               # 贴图目录（可选）
    ├── basecolor.png
    ├── normal.png
    └── ...
```

***

## 专有文件扩展名

| 扩展名       | 用途                     |
| --------- | ---------------------- |
| `.zasset` | 统一资产文件夹格式              |
| `.zmetal` | 材质节点序列化数据              |
| `.sicon`  | 静态 PNG 缩略图             |
| `.aicon`  | 动态 GIF 缩略图             |
| `.mcm`    | 材质-物体映射关系              |
| `.fdata`  | 文件夹元数据（FolderMetadata） |

***

## Maya 集成要点

- 通过 `import squirrel_asset_manager` 引用
- 包入口暴露 `mgr`（Category 库）和 `pmgr`（Project 库）两个全局代理
- 截图依赖 Maya 视口（playblast / capture）
- 材质网络重建依赖 Maya cmds API
- 导出使用 Maya file 命令 + 自家属性遍历引擎

***

*本说明基于代码实际状态整理，文件行数为 Approx 值。*
