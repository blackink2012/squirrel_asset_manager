# 截图 UI 持久化行为约定

**文件**: `material_library/ui/thumbnail_capture_overlay.py` + `material_library/ui/main_window.py`  
**日期**: 2026-05-18  
**重要性**: P0 — 违反此约定将严重阻塞批量资产创建流程

---

## 核心原则

> **截图 Overlay（取景框）一旦被用户定位，任何后续操作不得移动、重建、隐藏或关闭它。**
> 
> 用户手动关闭（点击 ✕ 或关闭窗口）是 Overlay 被销毁的唯一合法途径。

---

## 详细行为

### 创建规则
- Overlay 仅在首次创建时调用 `show()`，此时窗口出现在默认位置
- 用户调整位置/大小后，Overlay **一直保持该位置**，直到用户主动关闭
- 任何时候都不应在代码中调用 `show()` / `raise_()` 来「重置」已存在的 Overlay

### 多资产批处理
- 第 1 个资产 → 如 Overlay 不存在则创建并显示；如已存在（前一批遗留）则直接复用
- 第 2+ 个资产 → `_process_single_asset_auto` 直接通过 `self._asset_overlay._on_screenshot()` 自动截图
  - **不调用** Overlay 的 `show()` / `raise_()` / `activateWindow()`
  - 仅更新 `save_path_override` 和 `save_path`
- 全部完成后 → `_cleanup_after_export` 不清除 Overlay，不调用 `hide()`

### 跨批次（多次点击「导出资产」）
- 第 1 批完成后，Overlay 保持原位
- 用户重新选材质 → 再次点击「导出资产」
- `_on_create_asset` **不得**调用 `deleteLater()` / `close()` / 重新创建 Overlay
- 复用现有 Overlay，只换 `save_path_override` 和信号连接

### 跨工具重启（Maya 重启）
- Maya 重启后 Overlay 释放是正常的（Maya 进程结束）
- 建议未来版本将位置/大小持久化到 config.json

---

## 代码约束

| 文件 | 函数 | 禁止操作 |
|------|------|---------|
| `main_window.py` | `_on_create_asset` | ❌ 删除/重建 `_asset_overlay` |
| `main_window.py` | `_process_single_asset_interactive` | ❌ 每次重建 overlay（仅在 overlay 为 None 时创建） |
| `main_window.py` | `_process_single_asset_auto` | ❌ 调用 overlay 的 `show()`/`raise_()` |
| `main_window.py` | `_cleanup_after_export` | ❌ `hide()` 或 `deleteLater()` overlay |
| `thumbnail_capture_overlay.py` | `_on_screenshot` | ✅ 截图后 `self.show()` 是父类行为（恢复窗口），不可修改（ZJG 原生） |

---

## 验证方法

1. 打开工具 → 点击「导出资产」→ 截图 overlay 出现在默认位置
2. 将 overlay 拖到屏幕右上角 → 截图 → 第二批素材自动截图仍使用右上角位置
3. 导出完成后重新点击「导出资产」→ Overlay 仍在右上角，没有回到中心
4. 手动关闭 overlay → 点击「导出资产」→ 重新出现在默认位置（首次创建）
