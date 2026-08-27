import os
import sys
import shutil
import webbrowser
from pathlib import Path
import threading
import queue
from datetime import datetime

_T = None
_help_path = lambda p: p
try:
    # 优先包内相对导入，避免误命中 sys.path 中其他副本（如独立库 release 目录）的顶层 utils 包
    from ..utils.i18n import t as _T, help_path as _hpath
    _help_path = _hpath
except ImportError:
    try:
        from squirrel_asset_manager.utils.i18n import t as _T, help_path as _hpath
        _help_path = _hpath
    except ImportError:
        try:
            from utils.i18n import t as _T, help_path as _hpath
            _help_path = _hpath
        except ImportError:
            _T = None

def t(key, **kwargs):
    return _T(key, **kwargs) if _T is not None else (key.format(**kwargs) if kwargs else key)


def get_qt_modules():
    """获取 Qt 绑定（PySide6 优先，失败自动降级 PySide2）

    - Maya 2025+ 自带 PySide6
    - Maya 2022~2024 自带 PySide2
    """
    try:
        from PySide6 import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui
    except ImportError:
        pass
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui
    except ImportError:
        pass
    raise ImportError("需要 PySide6 或 PySide2")


QtWidgets, QtCore, QtGui = get_qt_modules()

QApplication = QtWidgets.QApplication
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QPushButton = QtWidgets.QPushButton
QLineEdit = QtWidgets.QLineEdit
QLabel = QtWidgets.QLabel
QCheckBox = QtWidgets.QCheckBox
QProgressBar = QtWidgets.QProgressBar
QTextEdit = QtWidgets.QTextEdit
QFileDialog = QtWidgets.QFileDialog
QMessageBox = QtWidgets.QMessageBox
QFrame = QtWidgets.QFrame
QTimer = QtCore.QTimer
Qt = QtCore.Qt
QFont = QtGui.QFont


# ---- 字体 DPI 适配：以 4K 27 英寸屏（约 163 DPI）为视觉基准 ----
# 不同 DPI 屏幕按比例缩放字号与尺寸，保证视觉大小一致（4K 正常、2K 偏大的问题）。
# 如基准屏尺寸不是 27 英寸，可调整 REFERENCE_DPI 值。
REFERENCE_DPI = 163.0


def _font_scale():
    """返回当前屏幕相对基准 DPI 的字体缩放系数"""
    try:
        app = QtWidgets.QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        dpi = float(screen.physicalDotsPerInch()) if screen is not None else REFERENCE_DPI
        if dpi <= 0:
            dpi = REFERENCE_DPI
        return max(0.6, min(dpi / REFERENCE_DPI, 1.5))
    except Exception:
        return 1.0


FONT_SCALE = _font_scale()


def _fs(px):
    """按 DPI 缩放字号（样式表 px），最小 8px 保证可读"""
    return max(8, int(px * FONT_SCALE))


def _fp(pt):
    """按 DPI 缩放字体点值（QFont pt），最小 6pt 保证可读"""
    return max(6, int(round(pt * FONT_SCALE)))


def _sc(px):
    """按 DPI 缩放尺寸（按钮 padding / min-height / min-width 等），最小 1px"""
    return max(1, int(px * FONT_SCALE))


def _font_style(text):
    """将样式文本中的 @FONT_nn@（字号）与 @SIZE_nn@（尺寸）占位符按 DPI 缩放"""
    import re

    def _repl(m):
        tag, val = m.group(1), int(m.group(2))
        return str(_fs(val)) if tag == "FONT" else str(_sc(val))

    return re.sub(r'@(FONT|SIZE)_(\d+)@', _repl, text)


def find_files_by_extensions(root_folder, extensions):
    target_exts = set()
    for ext in extensions:
        e = ext.strip().lower()
        if not e.startswith('.'):
            e = '.' + e
        target_exts.add(e)

    result = []
    for dirpath, _, filenames in os.walk(root_folder):
        for filename in filenames:
            if os.path.splitext(filename)[1].lower() in target_exts:
                result.append(Path(os.path.join(dirpath, filename)))
    return result


class FileMirrorCopier:
    def __init__(self):
        self.progress_queue = queue.Queue()

    def mirror_copy(self, source_folder, dest_folder, extensions, dry_run=False, keep_structure=True):
        try:
            source_path = Path(source_folder)
            dest_path = Path(dest_folder)

            files = find_files_by_extensions(source_folder, extensions)
            total = len(files)

            if total == 0:
                self.progress_queue.put(("error", t("qtool.mirror.error_no_files")))
                return

            copied = 0
            skipped = 0
            failed = 0
            failed_files = []

            # 扁平模式：检查重名冲突（取第一个）
            if not keep_structure:
                seen_names = {}

            for i, src_file in enumerate(files):
                rel_path = src_file.relative_to(source_path)
                if keep_structure:
                    dest_file = dest_path / rel_path
                    display_path = str(rel_path)
                else:
                    base = src_file.name
                    if base in seen_names:
                        # 同名文件：用父目录前缀区分
                        parts = src_file.relative_to(source_path).parts
                        if len(parts) > 1:
                            base = f"{parts[-2]}_{parts[-1]}"
                        else:
                            base = f"{i}_{base}"
                    seen_names[src_file.name] = base
                    dest_file = dest_path / base
                    display_path = base

                self.progress_queue.put(("progress", (i + 1, total, display_path)))

                if dest_file.exists():
                    skipped += 1
                    self.progress_queue.put(("log", t("qtool.mirror.log_skip_exists", path=display_path)))
                    continue

                if dry_run:
                    self.progress_queue.put(("log", t("qtool.mirror.log_preview_will_copy", path=display_path)))
                    continue

                try:
                    if keep_structure:
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                    else:
                        dest_path.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src_file), str(dest_file))
                    copied += 1
                    self.progress_queue.put(("log", t("qtool.mirror.log_copied", path=display_path)))
                except Exception as e:
                    failed += 1
                    failed_files.append((display_path, str(e)))
                    self.progress_queue.put(("log", t("qtool.mirror.log_failed", path=display_path, err=e)))

            if dry_run:
                self.progress_queue.put(("complete", (total, 0, 0, 0, [])))
            else:
                self.progress_queue.put(("complete", (total, copied, skipped, failed, failed_files)))

        except Exception as e:
            self.progress_queue.put(("error", t("qtool.mirror.error_operation_failed", err=str(e))))


class MirrorCopyWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.copier = FileMirrorCopier()
        self.dry_run = False
        self.keep_structure = True
        self._init_ui()

        self._timer = QTimer()
        self._timer.timeout.connect(self._process_queue)
        self._timer.start(100)

    def _init_ui(self):
        self.setWindowTitle(t("qtool.mirror.title"))
        self.resize(750, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel(t("qtool.mirror.title"))
        title_font = QFont("Arial", _fp(16))
        title_font.setBold(True)
        title.setFont(title_font)
        title_row.addWidget(title)

        title_row.addStretch()

        help_btn = QPushButton("?")
        help_btn.setFixedSize(_sc(34), _sc(34))
        help_btn.setToolTip(t("qtool.mirror.help_tooltip"))
        help_btn.setStyleSheet(
            _font_style(
                "QPushButton { background-color: #3a3a3a; color: #ffa502; border: none;"
                "font-size: @FONT_18@px; font-weight: bold; border-radius: @SIZE_4@px; }"
                "QPushButton:hover { background-color: #4a4a4a; }"
            )
        )
        help_btn.clicked.connect(self._on_help)
        title_row.addWidget(help_btn)

        layout.addLayout(title_row)
        layout.addSpacing(5)

        # 源文件夹
        src_layout = QVBoxLayout()
        src_layout.setSpacing(3)
        src_label = QLabel(t("qtool.mirror.source_folder") + ":")
        src_label.setFont(QFont("Arial", _fp(10)))
        src_layout.addWidget(src_label)

        src_row = QHBoxLayout()
        src_row.setSpacing(5)
        self.src_edit = QLineEdit()
        self.src_edit.setFont(QFont("Arial", _fp(10)))
        src_row.addWidget(self.src_edit)

        self.src_btn = QPushButton(t("qtool.mirror.browse"))
        self.src_btn.clicked.connect(self._browse_source)
        src_row.addWidget(self.src_btn)
        src_layout.addLayout(src_row)
        layout.addLayout(src_layout)

        # 目标文件夹
        dest_layout = QVBoxLayout()
        dest_layout.setSpacing(3)
        dest_label = QLabel(t("qtool.mirror.dest_folder") + ":")
        dest_label.setFont(QFont("Arial", _fp(10)))
        dest_layout.addWidget(dest_label)

        dest_row = QHBoxLayout()
        dest_row.setSpacing(5)
        self.dest_edit = QLineEdit()
        self.dest_edit.setFont(QFont("Arial", _fp(10)))
        dest_row.addWidget(self.dest_edit)

        self.dest_btn = QPushButton(t("qtool.mirror.browse"))
        self.dest_btn.clicked.connect(self._browse_dest)
        dest_row.addWidget(self.dest_btn)
        dest_layout.addLayout(dest_row)
        layout.addLayout(dest_layout)

        # 文件格式
        fmt_layout = QVBoxLayout()
        fmt_layout.setSpacing(3)
        fmt_label = QLabel(t("qtool.mirror.fmt_label") + ":")
        fmt_label.setFont(QFont("Arial", _fp(10)))
        fmt_layout.addWidget(fmt_label)

        self.fmt_edit = QLineEdit(".exr, .hdr, .png, .jpg")
        self.fmt_edit.setFont(QFont("Arial", _fp(10)))
        fmt_layout.addWidget(self.fmt_edit)
        layout.addLayout(fmt_layout)

        # 选项 + 按钮
        action_layout = QHBoxLayout()
        action_layout.setSpacing(15)

        self.preview_cb = QCheckBox(t("qtool.mirror.preview_only"))
        self.preview_cb.setFont(QFont("Arial", _fp(10)))
        action_layout.addWidget(self.preview_cb)

        self.structure_cb = QCheckBox(t("qtool.mirror.copy_structure"))
        self.structure_cb.setFont(QFont("Arial", _fp(10)))
        self.structure_cb.setChecked(True)
        self.structure_cb.stateChanged.connect(self._on_structure_changed)
        action_layout.addWidget(self.structure_cb)

        action_layout.addStretch()

        self.start_btn = QPushButton(t("qtool.mirror.start_copy"))
        self.start_btn.clicked.connect(self._start_copy)
        action_layout.addWidget(self.start_btn)

        self.clear_btn = QPushButton(t("qtool.mirror.clear_log"))
        self.clear_btn.clicked.connect(self._clear_log)
        action_layout.addWidget(self.clear_btn)

        self.quit_btn = QPushButton(t("common.close"))
        self.quit_btn.clicked.connect(self.close)
        action_layout.addWidget(self.quit_btn)

        layout.addLayout(action_layout)

        # 进度
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(3)
        self.progress_label = QLabel(t("qtool.mirror.waiting"))
        self.progress_label.setFont(QFont("Arial", _fp(10)))
        progress_layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)
        layout.addLayout(progress_layout)

        # 日志
        log_label = QLabel(t("qtool.mirror.operation_log") + ":")
        log_label.setFont(QFont("Arial", _fp(10)))
        log_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(log_label)

        self.log_text = QTextEdit()
        self.log_text.setFont(QFont("Courier New", _fp(9)))
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, stretch=1)

    def _browse_source(self):
        folder = QFileDialog.getExistingDirectory(self, t("qtool.mirror.select_source_title"))
        if folder:
            self.src_edit.setText(folder)

    def _browse_dest(self):
        folder = QFileDialog.getExistingDirectory(self, t("qtool.mirror.select_dest_title"))
        if folder:
            self.dest_edit.setText(folder)

    def _parse_extensions(self):
        raw = self.fmt_edit.text().strip()
        if not raw:
            return []
        exts = []
        for item in raw.split(","):
            e = item.strip()
            if e:
                if not e.startswith("."):
                    e = "." + e
                exts.append(e)
        return exts

    def _start_copy(self):
        source = self.src_edit.text().strip()
        dest = self.dest_edit.text().strip()
        exts = self._parse_extensions()

        if not source:
            QMessageBox.critical(self, t("qtool.mirror.error"), t("qtool.mirror.err_no_source"))
            return
        if not dest:
            QMessageBox.critical(self, t("qtool.mirror.error"), t("qtool.mirror.err_no_dest"))
            return
        if not exts:
            QMessageBox.critical(self, t("qtool.mirror.error"), t("qtool.mirror.err_no_formats"))
            return
        if source == dest:
            QMessageBox.critical(self, t("qtool.mirror.error"), t("qtool.mirror.err_same_folder"))
            return
        if not os.path.isdir(source):
            QMessageBox.critical(self, t("qtool.mirror.error"), t("qtool.mirror.err_source_not_exist"))
            return

        self.start_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText(t("qtool.mirror.scanning"))

        self.dry_run = self.preview_cb.isChecked()
        if self.dry_run:
            self._log_message(t("qtool.mirror.preview_mode_header"))
        self._log_message(t("qtool.mirror.log_source", folder=source))
        self._log_message(t("qtool.mirror.log_dest", folder=dest))
        self._log_message(t("qtool.mirror.log_formats", fmts=', '.join(exts)))

        thread = threading.Thread(
            target=self.copier.mirror_copy,
            args=(source, dest, exts, self.dry_run, self.keep_structure),
            daemon=True
        )
        thread.start()

    def _log_message(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    def _clear_log(self):
        self.log_text.clear()

    def _on_help(self):
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = _help_path(os.path.join(plugin_root, "Assets", "help", "批量镜像复制", "help.html"))
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            print("[批量镜像复制] 帮助文件未找到:", help_path)

    def _on_structure_changed(self, state):
        self.keep_structure = (state == Qt.Checked)

    def _process_queue(self):
        try:
            while True:
                msg_type, data = self.copier.progress_queue.get_nowait()

                if msg_type == "progress":
                    current, total, filename = data
                    percent = int((current / total) * 100)
                    self.progress_bar.setValue(percent)
                    self.progress_label.setText(f"{t('qtool.mirror.processing')}: {current}/{total}")

                elif msg_type == "log":
                    self._log_message(data)

                elif msg_type == "complete":
                    total, copied, skipped, failed, failed_files = data
                    self.progress_bar.setValue(100)

                    if self.dry_run:
                        self.progress_label.setText(t("qtool.mirror.preview_done"))
                        self._log_message(t("qtool.mirror.preview_done_count", total=total))
                    else:
                        self.progress_label.setText(t("qtool.mirror.copy_done"))
                        summary = (t("qtool.mirror.operation_done") + "\n"
                                   + t("qtool.mirror.summary_total", total=total) + "\n"
                                   + t("qtool.mirror.summary_copied", copied=copied) + "\n"
                                   + t("qtool.mirror.summary_skipped", skipped=skipped) + "\n"
                                   + t("qtool.mirror.summary_failed", failed=failed))
                        if failed_files:
                            summary += "\n\n" + t("qtool.mirror.failed_files") + ":"
                            for f, err in failed_files:
                                summary += f"\n  - {f}: {err}"
                        QMessageBox.information(self, t("qtool.mirror.done"), summary)
                        self._log_message(t("qtool.mirror.operation_done_summary",
                                            total=total, copied=copied, skipped=skipped, failed=failed))

                    self.start_btn.setEnabled(True)

                elif msg_type == "error":
                    QMessageBox.critical(self, t("qtool.mirror.error"), data)
                    self._log_message(t("qtool.mirror.log_error", err=data))
                    self.progress_label.setText(t("qtool.mirror.error_status"))
                    self.start_btn.setEnabled(True)

        except queue.Empty:
            pass


# 保存窗口引用，防止被垃圾回收
_mirror_window = None


def main():
    global _mirror_window

    app = QApplication.instance()
    need_exec = False
    if not app:
        app = QApplication(sys.argv)
        need_exec = True

    _mirror_window = MirrorCopyWindow()
    _mirror_window.show()
    _mirror_window.raise_()
    _mirror_window.activateWindow()

    if need_exec:
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
