# 右键"添加引用"功能设计

**日期**: 2025-07-01
**状态**: 已批准

## 概述

在资产卡片右键菜单新增"添加引用"按钮，将 `.zasset` 内的几何体文件以 Maya Reference 方式加载到当前场景。

## 与"导入"的区别

| | 导入 | 添加引用 |
|---|---|---|
| Maya 命令 | `cmds.file(import=True)` | `cmds.file(reference=True)` |
| 场景关系 | 节点合并到场景 | 创建引用节点，源文件独立 |
| 命名空间 | 无 | 自动创建 namespace |

## 支持的格式

`.ma`, `.mb`, `.fbx`, `.abc`, `.usd`, `.obj`

## 流程

1. 右键资产卡片 → "添加引用"
2. 解压 `.zasset`（若未缓存则解压，缓存有效则复用）
3. 扫描解压目录中的几何体文件
4. 若多种格式 → 弹出格式选择对话框
5. `cmds.file(path, reference=True, namespace=资产名)`
6. 解析引用节点，重定向嵌套依赖引用路径（复用 `_redirect_dependency_paths`）

## 改动清单

| 文件 | 改动 |
|------|------|
| `ui/settings_dialog.py` | `_CONTEXT_MENU_ITEMS` 新增 `("add_reference", "添加引用")` |
| `Assets/preset/context_menu_preset.json` | 各子库新增 `"add_reference": true` |
| `ui/thumbnail_view.py` | 新增 `addReferenceRequested` 信号 + 菜单项 |
| `ui/main_window.py` | 新增 `_on_add_reference()` handler |

## 依赖

- 复用 `import_executor.py` 的 `.zasset` 解压逻辑
- 复用 `_redirect_dependency_paths()` 重定向嵌套引用
