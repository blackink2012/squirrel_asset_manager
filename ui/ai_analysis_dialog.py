try:
    from ..utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

try:
    from ..utils.settings import (SettingsManager, get_ai_api_key, set_ai_api_key,
                                  apply_font_size_to_widget, get_ui_font_size)
except ImportError:
    SettingsManager = None
    get_ai_api_key = lambda settings, provider: settings.get("ai_api_key", "")
    set_ai_api_key = lambda settings, provider, key: settings
    apply_font_size_to_widget = lambda widget, font_size: None
    get_ui_font_size = lambda: 13

try:
    from ..core.ai_analyzer import AIAnalyzer
except ImportError:
    AIAnalyzer = None

from ..utils.maya_utils import get_qt_modules

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


def _ui_font_size():
    """主 UI 配置的字体大小（默认 13），4K 高 DPI 屏幕下与主界面字体/缩放保持一致"""
    try:
        return get_ui_font_size()
    except Exception:
        return 13


_STYLE = """
QDialog, QWidget { background-color: #252525; }
QLabel { color: #a0a0a0; font-size: 13px; }
QLineEdit {
    background-color: #2a2a2a;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    color: #ffffff;
    padding: 6px 10px;
    font-size: 13px;
}
QLineEdit:focus { border-color: #5294e2; }
QTextEdit {
    background-color: #2a2a2a;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    color: #ffffff;
    padding: 8px;
    font-size: 13px;
}
QTextEdit:focus { border-color: #5294e2; }
QPushButton {
    background-color: #3a3a3a;
    border: none;
    border-radius: 4px;
    color: #d0d0d0;
    padding: 8px 16px;
    font-size: 13px;
}
QPushButton:hover { background-color: #4a4a4a; }
QPushButton#applyBtn { background-color: #5294e2; }
QPushButton#applyBtn:hover { background-color: #62a4f2; }
QPushButton#selectAllBtn { background-color: #3a4a3a; }
QPushButton#selectAllBtn:hover { background-color: #4a5a4a; }
QPushButton#deselectAllBtn { background-color: #4a3a3a; }
QPushButton#deselectAllBtn:hover { background-color: #5a4a4a; }
QGroupBox {
    border: 1px solid #3a3a3a;
    border-radius: 6px;
    margin-top: 10px;
}
QGroupBox::title {
    color: #909090;
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}
QCheckBox { color: #d0d0d0; spacing: 6px; font-size: 13px; }
QCheckBox::indicator { width: 18px; height: 18px; }
QComboBox {
    background-color: #2a2a2a;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    color: #ffffff;
    padding: 5px 10px;
    font-size: 13px;
}
QComboBox:hover { border-color: #5294e2; }
QComboBox QAbstractItemView {
    background-color: #2a2a2a;
    color: #d0d0d0;
    selection-background-color: #2d4a6f;
}
QScrollBar:vertical {
    background: #1a1a1a;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #4a4a4a;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class AIBatchResultsDialog(QtWidgets.QDialog):
    batchApplied = QtCore.Signal(list)

    def __init__(self, parent=None, results=None):
        super(AIBatchResultsDialog, self).__init__(parent)
        self._results = results or []
        self._checkboxes = {}
        self._edit_widgets = {}
        self._setup_ui()

    def _setup_ui(self):
        total = len(self._results)
        self._font_size = _ui_font_size()
        self._scale = self._font_size / 13.0
        self._thumb_size = int(400 * self._scale)
        self.setWindowTitle(t('dialog.ai_batch_results.title', n=total))
        self.setMinimumSize(int(1140 * self._scale), int(500 * self._scale))
        self.resize(int(1240 * self._scale),
                    min(int(700 * self._scale), int(200 * self._scale) + total * int(200 * self._scale)))
        self.setStyleSheet(_STYLE)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        top_row = QtWidgets.QHBoxLayout()
        top_label = QtWidgets.QLabel(t('dialog.ai_batch_results.summary', n=total))
        top_label.setStyleSheet('color: #ffffff; font-size: 14px;')
        top_row.addWidget(top_label)
        top_row.addStretch()
        select_all = QtWidgets.QPushButton(t('common.select_all'))
        select_all.setObjectName('selectAllBtn')
        select_all.clicked.connect(self._select_all)
        top_row.addWidget(select_all)
        deselect_all = QtWidgets.QPushButton(t('btn.deselect_all'))
        deselect_all.setObjectName('deselectAllBtn')
        deselect_all.clicked.connect(self._deselect_all)
        top_row.addWidget(deselect_all)
        root.addLayout(top_row)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setStyleSheet('QSplitter::handle { background-color: #3a3a3a; width: 2px; }')

        # ====== 左侧: 可编辑列表 ======
        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setStyleSheet('QScrollArea { background: transparent; }')

        list_widget = QtWidgets.QWidget()
        self._list_layout = QtWidgets.QVBoxLayout(list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)

        for i, entry in enumerate(self._results):
            row = self._build_result_row(i, entry)
            self._list_layout.addWidget(row)

        self._list_layout.addStretch()
        scroll.setWidget(list_widget)
        left_layout.addWidget(scroll)
        splitter.addWidget(left_panel)

        # ====== 右侧: 大缩略图预览 ======
        right_panel = QtWidgets.QWidget()
        right_panel.setFixedWidth(int(420 * self._scale))
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(8)

        self._preview_thumb = QtWidgets.QLabel()
        self._preview_thumb.setFixedSize(self._thumb_size, self._thumb_size)
        self._preview_thumb.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._preview_thumb.setStyleSheet(
            'background-color: #1a1a1a; border: 1px solid #3a3a3a; border-radius: 8px;')
        right_layout.addWidget(self._preview_thumb)

        self._preview_name = QtWidgets.QLabel('')
        self._preview_name.setStyleSheet('color: #ffffff; font-size: 16px; font-weight: bold;')
        self._preview_name.setWordWrap(True)
        self._preview_name.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self._preview_name)

        self._preview_info = QtWidgets.QLabel(t('dialog.ai_batch_results.click_hint'))
        self._preview_info.setStyleSheet('color: #808080; font-size: 13px;')
        self._preview_info.setWordWrap(True)
        self._preview_info.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self._preview_info)

        right_layout.addStretch()
        splitter.addWidget(right_panel)

        splitter.setSizes([700, 420])
        root.addWidget(splitter, 1)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(10)
        self._move_cb = QtWidgets.QCheckBox(t('dialog.ai_batch_results.move_to_category'))
        self._move_cb.setChecked(True)
        self._move_cb.setToolTip(t('dialog.ai_batch_results.move_to_category.tooltip'))
        btn_row.addWidget(self._move_cb)
        btn_row.addStretch()
        cancel_btn = QtWidgets.QPushButton(t('common.cancel'))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._apply_btn = QtWidgets.QPushButton(t('dialog.ai_batch_results.apply_selected', n=total))
        self._apply_btn.setObjectName('applyBtn')
        self._apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self._apply_btn)
        root.addLayout(btn_row)

        apply_font_size_to_widget(self, self._font_size)

        if self._results:
            self._show_thumbnail(0)

    def _build_result_row(self, index, entry):
        material = entry.get('material', {})
        result = entry.get('result', {})

        frame = QtWidgets.QFrame()
        frame.setStyleSheet(
            'QFrame#resultCard { background-color: #2a2a2a; border: 1px solid #3a3a3a; '
            'border-radius: 6px; }')
        frame.setObjectName('resultCard')
        frame.setProperty('result_index', index)
        frame.installEventFilter(self)

        outer = QtWidgets.QHBoxLayout(frame)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        cb = QtWidgets.QCheckBox()
        cb.setChecked(True)
        cb.toggled.connect(self._update_apply_btn_text)
        outer.addWidget(cb)
        self._checkboxes[index] = cb

        fields = QtWidgets.QVBoxLayout()
        fields.setSpacing(4)

        orig_name = material.get('name', '?')
        name_row = QtWidgets.QHBoxLayout()
        name_row.setSpacing(4)
        name_row.addWidget(QtWidgets.QLabel(orig_name))
        name_row.addStretch()
        sub_lib = QtWidgets.QLabel(material.get('sub_library', ''))
        sub_lib.setStyleSheet('color: #707070; font-size: 12px;')
        name_row.addWidget(sub_lib)
        fields.addLayout(name_row)

        name_cn = QtWidgets.QLineEdit(result.get('name_cn', ''))
        name_cn.setPlaceholderText(t('label.readable_name'))
        name_cn.setMaximumHeight(32)
        name_cn.setStyleSheet('font-size: 13px;')
        name_cn.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        name_cb = QtWidgets.QCheckBox(t('label.ai_apply_name'))
        name_cb.setChecked(True)
        name_cb.setToolTip(t('dialog.ai_analysis.apply_field_tooltip'))
        name_cb.setFixedWidth(int(52 * self._scale))
        name_row2 = QtWidgets.QHBoxLayout()
        name_row2.setSpacing(4)
        name_row2.addWidget(name_cb)
        name_row2.addWidget(name_cn, 1)
        fields.addLayout(name_row2)

        mid_row = QtWidgets.QHBoxLayout()
        mid_row.setSpacing(6)

        cat_cb = QtWidgets.QCheckBox(t('label.ai_apply_category'))
        cat_cb.setChecked(True)
        cat_cb.setToolTip(t('dialog.ai_analysis.apply_field_tooltip'))
        cat_cb.setFixedWidth(int(52 * self._scale))
        cat_edit = QtWidgets.QLineEdit(result.get('sub_category', ''))
        cat_edit.setPlaceholderText(t('label.sub_category'))
        cat_edit.setMaximumHeight(30)
        cat_edit.setStyleSheet('font-size: 13px;')
        cat_edit.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        mid_row.addWidget(cat_cb)
        mid_row.addWidget(cat_edit, 1)

        tags_cb = QtWidgets.QCheckBox(t('label.ai_apply_tags'))
        tags_cb.setChecked(True)
        tags_cb.setToolTip(t('dialog.ai_analysis.apply_field_tooltip'))
        tags_cb.setFixedWidth(int(52 * self._scale))
        tags_edit = QtWidgets.QLineEdit(', '.join(result.get('tags', [])))
        tags_edit.setPlaceholderText(t('label.tags'))
        tags_edit.setMaximumHeight(30)
        tags_edit.setStyleSheet('font-size: 13px;')
        tags_edit.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        mid_row.addWidget(tags_cb)
        mid_row.addWidget(tags_edit, 1)
        fields.addLayout(mid_row)

        notes_edit = QtWidgets.QLineEdit(result.get('notes', ''))
        notes_edit.setPlaceholderText(t('label.notes'))
        notes_edit.setMaximumHeight(30)
        notes_edit.setStyleSheet('font-size: 13px;')
        notes_edit.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        notes_cb = QtWidgets.QCheckBox(t('label.ai_apply_notes'))
        notes_cb.setChecked(True)
        notes_cb.setToolTip(t('dialog.ai_analysis.apply_field_tooltip'))
        notes_cb.setFixedWidth(int(52 * self._scale))
        notes_row = QtWidgets.QHBoxLayout()
        notes_row.setSpacing(4)
        notes_row.addWidget(notes_cb)
        notes_row.addWidget(notes_edit, 1)
        fields.addLayout(notes_row)

        self._edit_widgets[index] = {
            'name_cb': name_cb,
            'name_cn': name_cn,
            'category_cb': cat_cb,
            'category': cat_edit,
            'tags_cb': tags_cb,
            'tags': tags_edit,
            'notes_cb': notes_cb,
            'notes': notes_edit,
        }

        outer.addLayout(fields, 1)
        self._install_filter_recursive(frame)
        return frame

    def _install_filter_recursive(self, widget):
        """递归给所有子控件安装 eventFilter"""
        widget.installEventFilter(self)
        for child in widget.findChildren(QtWidgets.QWidget):
            child.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.MouseButtonPress:
            idx = obj.property('result_index')
            if idx is not None:
                self._show_thumbnail(idx)
                return False
            parent = obj.parent()
            while parent is not None:
                idx = parent.property('result_index')
                if idx is not None:
                    self._show_thumbnail(idx)
                    break
                parent = parent.parent()
        return super().eventFilter(obj, event)

    def _on_row_activated(self, index):
        self._show_thumbnail(index)

    def _show_thumbnail(self, index):
        if index < 0 or index >= len(self._results):
            return
        entry = self._results[index]
        material = entry.get('material', {})
        result = entry.get('result', {})

        thumb_bytes = material.get('thumb_bytes')
        if thumb_bytes:
            pix = QtGui.QPixmap()
            pix.loadFromData(thumb_bytes)
            if not pix.isNull():
                pix = pix.scaled(self._thumb_size, self._thumb_size,
                                 QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                 QtCore.Qt.TransformationMode.SmoothTransformation)
                self._preview_thumb.setPixmap(pix)
            else:
                self._preview_thumb.setText(t('dialog.ai_batch_results.no_thumbnail'))
        else:
            self._preview_thumb.setText(t('dialog.ai_batch_results.no_thumbnail'))

        self._preview_name.setText(result.get('name_cn', '') or material.get('name', ''))
        lines = []
        cat = result.get('sub_category', '')
        if cat:
            lines.append(t('label.category_with_value', value=cat))
        tags = result.get('tags', [])
        if tags:
            lines.append(t('label.tags_with_value', value=", ".join(tags)))
        notes = result.get('notes', '')
        if notes:
            lines.append(t('label.notes_with_value', value=notes[:120]))
        self._preview_info.setText('\n'.join(lines))

    def _select_all(self):
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def _deselect_all(self):
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def _update_apply_btn_text(self):
        count = sum(1 for cb in self._checkboxes.values() if cb.isChecked())
        self._apply_btn.setText(t('dialog.ai_batch_results.apply_selected', n=count))

    def _on_apply(self):
        checked_indices = [i for i, cb in self._checkboxes.items() if cb.isChecked()]
        if not checked_indices:
            QtWidgets.QMessageBox.information(self, t('dialog.ai_batch_results.no_selection_title'),
                                              t('dialog.ai_batch_results.no_selection_msg'))
            return

        selected_results = []
        move_to_category = self._move_cb.isChecked()
        for i in checked_indices:
            entry = self._results[i]
            result = entry.get('result', {}).copy()
            w = self._edit_widgets.get(i, {})

            # 仅收集勾选的字段；未勾选的从结果中移除（不应用）
            if w.get('name_cb') and w['name_cb'].isChecked():
                name_cn = w.get('name_cn')
                if name_cn:
                    val = name_cn.text().strip()
                    if val:
                        result['name_cn'] = val
            else:
                result.pop('name_cn', None)

            if w.get('category_cb') and w['category_cb'].isChecked():
                cat_w = w.get('category')
                if cat_w:
                    val = cat_w.text().strip()
                    if val:
                        result['sub_category'] = val
            else:
                result.pop('sub_category', None)

            if w.get('tags_cb') and w['tags_cb'].isChecked():
                tags_w = w.get('tags')
                if tags_w:
                    val = tags_w.text().strip()
                    if val:
                        result['tags'] = [t.strip() for t in val.split(',') if t.strip()]
            else:
                result.pop('tags', None)

            if w.get('notes_cb') and w['notes_cb'].isChecked():
                notes_w = w.get('notes')
                if notes_w:
                    val = notes_w.text().strip()
                    if val:
                        result['notes'] = val
            else:
                result.pop('notes', None)

            selected_results.append({
                'material': entry.get('material', {}),
                'result': result,
                'move_to_category': move_to_category,
            })

        self.batchApplied.emit(selected_results)
        self.accept()


class AIAnalysisConfigDialog(QtWidgets.QDialog):
    configConfirmed = QtCore.Signal(dict)
    modelsFetched = QtCore.Signal(str, int, list)  # (provider, seq, models)

    def __init__(self, parent=None, available_models=None):
        super(AIAnalysisConfigDialog, self).__init__(parent)
        self._available_models = available_models or []
        self._models_fetch_seq = 0  # 模型列表异步请求序号，用于丢弃过期响应
        self._providers = AIAnalyzer.PROVIDERS if AIAnalyzer else {}
        self._load_saved_settings()
        self._setup_ui()

    def _load_saved_settings(self):
        """读取上次保存的 AI 服务配置"""
        self._saved = {}
        if SettingsManager:
            try:
                self._saved = SettingsManager().load()
            except Exception:
                self._saved = {}

    def _current_provider(self):
        """当前服务商 key"""
        idx = self._provider_combo.currentIndex()
        return self._provider_combo.itemData(idx) or "ollama"

    def _setup_ui(self):
        self._font_size = _ui_font_size()
        self._scale = self._font_size / 13.0
        self.setWindowTitle(t('dialog.ai_analysis_config.title'))
        self.setMinimumSize(int(420 * self._scale), int(560 * self._scale))
        self.resize(int(440 * self._scale), int(600 * self._scale))
        self.setStyleSheet(_STYLE)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QtWidgets.QLabel(t('dialog.ai_analysis_config.prompt'))
        title.setStyleSheet('color: #ffffff; font-size: 15px; font-weight: bold;')
        layout.addWidget(title)

        # 服务商
        provider_layout = QtWidgets.QHBoxLayout()
        provider_label = QtWidgets.QLabel(t('label.ai_provider'))
        provider_label.setFixedWidth(int(80 * self._scale))
        provider_layout.addWidget(provider_label)
        self._provider_combo = QtWidgets.QComboBox()
        saved_provider = self._saved.get('ai_provider', 'ollama')
        for key, cfg in (self._providers or {}).items():
            self._provider_combo.addItem(cfg.get('label', key), key)
        if self._provider_combo.count() == 0:
            self._provider_combo.addItem('Ollama（本地）', 'ollama')
        self._provider_combo.setCurrentIndex(
            max(0, self._provider_combo.findData(saved_provider)))
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_layout.addWidget(self._provider_combo, 1)
        layout.addLayout(provider_layout)

        # API Key（Ollama 不需要）
        key_layout = QtWidgets.QHBoxLayout()
        key_label = QtWidgets.QLabel(t('label.ai_api_key'))
        key_label.setFixedWidth(int(80 * self._scale))
        key_layout.addWidget(key_label)
        self._api_key_edit = QtWidgets.QLineEdit()
        self._api_key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText(t('dialog.ai_analysis_config.api_key_placeholder'))
        self._api_key_edit.setText(get_ai_api_key(self._saved, self._saved.get('ai_provider', 'ollama')))
        self._api_key_edit.editingFinished.connect(self._on_api_key_edited)
        key_layout.addWidget(self._api_key_edit, 1)
        layout.addLayout(key_layout)

        # API 地址
        url_layout = QtWidgets.QHBoxLayout()
        url_label = QtWidgets.QLabel(t('label.ai_base_url'))
        url_label.setFixedWidth(int(80 * self._scale))
        url_layout.addWidget(url_label)
        self._base_url_edit = QtWidgets.QLineEdit()
        self._base_url_edit.setPlaceholderText(t('dialog.ai_analysis_config.base_url_placeholder'))
        self._base_url_edit.setText(self._saved.get('ai_base_url', ''))
        url_layout.addWidget(self._base_url_edit, 1)
        layout.addLayout(url_layout)

        # 模型
        model_layout = QtWidgets.QHBoxLayout()
        model_label = QtWidgets.QLabel(t('label.ai_model'))
        model_label.setFixedWidth(int(80 * self._scale))
        model_layout.addWidget(model_label)
        self._model_combo = QtWidgets.QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setMinimumWidth(int(200 * self._scale))
        model_layout.addWidget(self._model_combo, 1)
        layout.addLayout(model_layout)

        # 语言
        lang_layout = QtWidgets.QHBoxLayout()
        lang_label = QtWidgets.QLabel(t('label.output_language'))
        lang_label.setFixedWidth(int(80 * self._scale))
        lang_layout.addWidget(lang_label)
        self._lang_combo = QtWidgets.QComboBox()
        self._lang_combo.addItems(['中文', 'English'])
        self._lang_combo.setCurrentIndex(0)
        lang_layout.addWidget(self._lang_combo, 1)
        layout.addLayout(lang_layout)

        # DeepSeek 无视觉模型提示
        self._vision_tip = QtWidgets.QLabel(t('dialog.ai_analysis_config.vision_tip'))
        self._vision_tip.setWordWrap(True)
        self._vision_tip.setStyleSheet('color: #e0a050; font-size: 12px;')
        layout.addWidget(self._vision_tip)

        self._review_cb = QtWidgets.QCheckBox(t('dialog.ai_analysis_config.review_after'))
        self._review_cb.setChecked(True)
        layout.addWidget(self._review_cb)

        self._translate_tags_cb = QtWidgets.QCheckBox(t('dialog.ai_analysis_config.translate_tags'))
        self._translate_tags_cb.setToolTip(t('dialog.ai_analysis_config.translate_tags.tooltip'))
        layout.addWidget(self._translate_tags_cb)

        layout.addStretch()

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        cancel_btn = QtWidgets.QPushButton(t('common.cancel'))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        start_btn = QtWidgets.QPushButton(t('dialog.ai_analysis_config.start'))
        start_btn.setObjectName('applyBtn')
        start_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(start_btn)

        layout.addLayout(btn_layout)

        self.modelsFetched.connect(self._on_models_fetched)
        self._on_provider_changed()
        apply_font_size_to_widget(self, self._font_size)

    def _on_provider_changed(self, *_):
        """服务商切换：更新 API Key / 地址 / 模型列表 / 提示"""
        provider = self._current_provider()
        cfg = (self._providers or {}).get(provider, {})

        needs_key = bool(cfg.get('needs_key'))
        self._api_key_edit.setEnabled(needs_key)
        if not needs_key:
            self._api_key_edit.setPlaceholderText('(本地服务无需填写)')
        else:
            self._api_key_edit.setPlaceholderText(t('dialog.ai_analysis_config.api_key_placeholder'))
        # 切换服务商时加载该服务商自己保存的 API Key，避免串用其他服务商的 Key
        self._api_key_edit.setText(get_ai_api_key(self._saved, provider))

        default_url = cfg.get('base_url', '')
        # 所有服务商的默认地址集合：当前值等于任一默认值时视为未自定义，切换时更新
        provider_defaults = {
            c.get('base_url', '').rstrip('/')
            for c in (self._providers or {}).values() if c.get('base_url')
        }
        current_url = self._base_url_edit.text().strip().rstrip('/')
        if not current_url or current_url in provider_defaults:
            self._base_url_edit.setText(default_url)

        # 模型列表
        saved_model = self._saved.get('ai_model', '')
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        if provider == 'ollama':
            models = list(self._available_models) or ['qwen3-vl:8b']
        else:
            models = list(cfg.get('models', [])) or [cfg.get('default_model', '')]
        self._model_combo.addItems(models)
        # 下拉框可编辑，setCurrentText 会直接写入编辑框文本；
        # 仅当保存的模型属于当前服务商时才恢复，否则选中新服务商默认模型
        if saved_model and saved_model in models:
            self._model_combo.setCurrentText(saved_model)
        elif provider == 'ollama' and 'qwen3-vl:8b' in models:
            self._model_combo.setCurrentText('qwen3-vl:8b')
        elif provider == 'ollama' and 'qwen3.5:9b' in models:
            self._model_combo.setCurrentText('qwen3.5:9b')
        elif models:
            self._model_combo.setCurrentIndex(0)
        self._model_combo.blockSignals(False)

        # 云端服务商：后台实时刷新模型列表（先显示静态列表，获取成功后替换）
        if provider != 'ollama':
            self._fetch_cloud_models(provider)

        # 无视觉模型提示
        if provider == 'deepseek':
            self._vision_tip.setText(t('dialog.ai_analysis_config.vision_tip'))
        elif provider == 'zhipu':
            self._vision_tip.setText(t('dialog.ai_analysis_config.vision_tip_zhipu'))
        elif provider == 'openai':
            self._vision_tip.setText(t('dialog.ai_analysis_config.vision_tip_openai'))
        self._vision_tip.setVisible(provider in ('deepseek', 'zhipu', 'openai'))
        if provider in ('deepseek', 'zhipu', 'openai'):
            self.adjustSize()

    def _fetch_cloud_models(self, provider):
        """后台实时获取云端模型列表（失败时保持静态列表，不发信号）"""
        if not AIAnalyzer:
            return
        api_key = self._api_key_edit.text().strip()
        base_url = self._base_url_edit.text().strip()
        # 每次请求递增序号，响应回来时只有最新序号才生效，避免切回旧服务商的过期响应覆盖
        self._models_fetch_seq += 1
        seq = self._models_fetch_seq

        def _worker():
            try:
                analyzer = AIAnalyzer(provider=provider, api_key=api_key or None,
                                      base_url=base_url or None)
                models = analyzer.get_available_models()
            except Exception:
                models = []
            self.modelsFetched.emit(provider, seq, models)

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def _on_api_key_edited(self):
        """API Key 输入完成后重新拉取当前云端服务商的模型列表"""
        provider = self._current_provider()
        if provider != 'ollama':
            self._fetch_cloud_models(provider)

    def _on_models_fetched(self, provider, seq, models):
        """模型列表实时获取完成：仅处理最新序号的响应，过期响应（已切换服务商）直接丢弃"""
        if provider != self._current_provider() or seq != self._models_fetch_seq:
            return
        if not models:
            return
        current = self._model_combo.currentText()
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(models)
        if current in models:
            self._model_combo.setCurrentText(current)
        elif models:
            self._model_combo.setCurrentIndex(0)
        self._model_combo.blockSignals(False)

    def _on_confirm(self):
        config = self.get_config()
        self._save_settings(config)
        self.configConfirmed.emit(config)
        self.accept()

    def _save_settings(self, config):
        """将 AI 服务配置保存到用户设置（API Key 按供应商独立保存）"""
        if not SettingsManager:
            return
        try:
            provider = config.get('provider', 'ollama')
            new_settings = set_ai_api_key(
                SettingsManager().load(), provider, config.get('api_key', ''))
            new_settings['ai_provider'] = provider
            new_settings['ai_base_url'] = config.get('base_url', '')
            new_settings['ai_model'] = config.get('model', '')
            SettingsManager().save(new_settings)
        except Exception as e:
            print(f"[AI Config] 保存设置失败: {e}")

    def get_config(self):
        return {
            'provider': self._current_provider(),
            'api_key': self._api_key_edit.text().strip(),
            'base_url': self._base_url_edit.text().strip(),
            'model': self._model_combo.currentText().strip(),
            'language': self._lang_combo.currentText(),
            'review_output': self._review_cb.isChecked(),
            'translate_existing_tags': self._translate_tags_cb.isChecked(),
        }


class AIAnalysisDialog(QtWidgets.QDialog):
    analysisApplied = QtCore.Signal(dict)
    
    def __init__(self, parent=None, material=None, analysis_result=None):
        super(AIAnalysisDialog, self).__init__(parent)
        self._material = material
        self._analysis = analysis_result
        self._setup_ui()
    
    def _setup_ui(self):
        self._font_size = _ui_font_size()
        self._scale = self._font_size / 13.0
        self._thumb_size = int(128 * self._scale)
        self.setWindowTitle(t('dialog.ai_analysis.title'))
        self.setFixedSize(int(500 * self._scale), int(520 * self._scale))
        self.setStyleSheet("""
            QDialog { background-color: #252525; }
            QLabel { color: #a0a0a0; }
            QLineEdit { 
                background-color: #2a2a2a; 
                border: 1px solid #3a3a3a; 
                border-radius: 4px; 
                color: #ffffff;
                padding: 6px 10px;
            }
            QLineEdit:focus { border-color: #5294e2; }
            QTextEdit { 
                background-color: #2a2a2a; 
                border: 1px solid #3a3a3a; 
                border-radius: 4px; 
                color: #ffffff;
                padding: 8px;
            }
            QTextEdit:focus { border-color: #5294e2; }
            QPushButton { 
                background-color: #3a3a3a; 
                border: none; 
                border-radius: 4px; 
                color: #d0d0d0;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #4a4a4a; }
            QPushButton#applyBtn { background-color: #5294e2; }
            QPushButton#applyBtn:hover { background-color: #62a4f2; }
            QGroupBox { 
                border: 1px solid #3a3a3a; 
                border-radius: 6px; 
                margin-top: 10px;
            }
            QGroupBox::title { 
                color: #909090; 
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        header_layout = QtWidgets.QHBoxLayout()
        header_layout.setSpacing(12)
        
        self._thumb_label = QtWidgets.QLabel()
        self._thumb_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._thumb_label.setStyleSheet("background-color: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px;")
        self._thumb_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self._thumb_label)
        
        info_group = QtWidgets.QGroupBox(t('dialog.ai_analysis.asset_info'))
        info_layout = QtWidgets.QVBoxLayout(info_group)
        info_layout.setContentsMargins(10, 10, 10, 10)
        
        name_layout = QtWidgets.QHBoxLayout()
        name_label = QtWidgets.QLabel(t('label.original_name'))
        name_label.setFixedWidth(int(70 * self._scale))
        self._original_name = QtWidgets.QLabel('')
        self._original_name.setStyleSheet('color: #ffffff;')
        name_layout.addWidget(name_label)
        name_layout.addWidget(self._original_name)
        info_layout.addLayout(name_layout)
        
        sub_lib_layout = QtWidgets.QHBoxLayout()
        sub_lib_label = QtWidgets.QLabel(t('label.top_category'))
        sub_lib_label.setFixedWidth(int(70 * self._scale))
        self._sub_library = QtWidgets.QLabel('')
        self._sub_library.setStyleSheet('color: #ffffff;')
        sub_lib_layout.addWidget(sub_lib_label)
        sub_lib_layout.addWidget(self._sub_library)
        info_layout.addLayout(sub_lib_layout)
        
        header_layout.addWidget(info_group)
        layout.addLayout(header_layout)
        
        result_group = QtWidgets.QGroupBox(t('dialog.ai_analysis.result'))
        result_layout = QtWidgets.QVBoxLayout(result_group)
        result_layout.setContentsMargins(10, 10, 10, 10)
        result_layout.setSpacing(10)
        
        name_cn_layout = QtWidgets.QHBoxLayout()
        self._name_cb = QtWidgets.QCheckBox(t('label.ai_apply_name'))
        self._name_cb.setChecked(True)
        self._name_cb.setToolTip(t('dialog.ai_analysis.apply_field_tooltip'))
        self._name_cb.setFixedWidth(int(70 * self._scale))
        self._name_cn_edit = QtWidgets.QLineEdit()
        self._name_cn_edit.setPlaceholderText(t('dialog.ai_analysis.name_cn_placeholder'))
        name_cn_layout.addWidget(self._name_cb)
        name_cn_layout.addWidget(self._name_cn_edit)
        result_layout.addLayout(name_cn_layout)
        
        category_layout = QtWidgets.QHBoxLayout()
        self._category_cb = QtWidgets.QCheckBox(t('label.ai_apply_category'))
        self._category_cb.setChecked(True)
        self._category_cb.setToolTip(t('dialog.ai_analysis.apply_field_tooltip'))
        self._category_cb.setFixedWidth(int(70 * self._scale))
        self._category_edit = QtWidgets.QLineEdit()
        self._category_edit.setPlaceholderText(t('dialog.ai_analysis.category_placeholder'))
        category_layout.addWidget(self._category_cb)
        category_layout.addWidget(self._category_edit)
        result_layout.addLayout(category_layout)
        
        tags_layout = QtWidgets.QHBoxLayout()
        self._tags_cb = QtWidgets.QCheckBox(t('label.ai_apply_tags'))
        self._tags_cb.setChecked(True)
        self._tags_cb.setToolTip(t('dialog.ai_analysis.apply_field_tooltip'))
        self._tags_cb.setFixedWidth(int(70 * self._scale))
        self._tags_edit = QtWidgets.QLineEdit()
        self._tags_edit.setPlaceholderText(t('dialog.ai_analysis.tags_placeholder'))
        tags_layout.addWidget(self._tags_cb)
        tags_layout.addWidget(self._tags_edit)
        result_layout.addLayout(tags_layout)
        
        notes_layout = QtWidgets.QVBoxLayout()
        self._notes_cb = QtWidgets.QCheckBox(t('label.ai_apply_notes'))
        self._notes_cb.setChecked(True)
        self._notes_cb.setToolTip(t('dialog.ai_analysis.apply_field_tooltip'))
        self._notes_edit = QtWidgets.QTextEdit()
        self._notes_edit.setFixedHeight(int(80 * self._scale))
        self._notes_edit.setPlaceholderText(t('dialog.ai_analysis.notes_placeholder'))
        notes_layout.addWidget(self._notes_cb)
        notes_layout.addWidget(self._notes_edit)
        result_layout.addLayout(notes_layout)
        
        layout.addWidget(result_group)
        
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)
        self._move_cb = QtWidgets.QCheckBox(t('dialog.ai_analysis.move_to_category'))
        self._move_cb.setChecked(True)
        self._move_cb.setToolTip(t('dialog.ai_analysis.move_to_category.tooltip'))
        button_layout.addWidget(self._move_cb)
        button_layout.addStretch()

        cancel_btn = QtWidgets.QPushButton(t('common.cancel'))
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        apply_btn = QtWidgets.QPushButton(t('dialog.ai_analysis.apply'))
        apply_btn.setObjectName('applyBtn')
        apply_btn.clicked.connect(self._on_apply)
        button_layout.addWidget(apply_btn)
        
        layout.addLayout(button_layout)

        apply_font_size_to_widget(self, self._font_size)
        self._load_data()
    
    def _load_data(self):
        if self._material:
            self._original_name.setText(self._material.get('name', ''))
            self._sub_library.setText(self._material.get('sub_library', ''))
            
            thumb_bytes = self._material.get('thumb_bytes', None)
            if thumb_bytes:
                pixmap = QtGui.QPixmap()
                pixmap.loadFromData(thumb_bytes)
                if not pixmap.isNull():
                    pixmap = pixmap.scaled(self._thumb_size, self._thumb_size,
                                           QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                           QtCore.Qt.TransformationMode.SmoothTransformation)
                    self._thumb_label.setPixmap(pixmap)
        
        if self._analysis:
            self._name_cn_edit.setText(self._analysis.get('name_cn', ''))
            self._category_edit.setText(self._analysis.get('sub_category', ''))
            tags = self._analysis.get('tags', [])
            if tags:
                self._tags_edit.setText(', '.join(tags))
            self._notes_edit.setText(self._analysis.get('notes', ''))
    
    def _on_apply(self):
        updates = self.get_updates()
        
        if updates:
            updates['material_id'] = self._material.get('id', '')
            updates['move_to_category'] = self._move_cb.isChecked()
            self.analysisApplied.emit(updates)
        
        self.accept()
    
    def get_updates(self):
        """按勾选状态收集要应用的字段（只包含勾选且非空的字段）"""
        updates = {}
        
        if self._name_cb.isChecked():
            name_cn = self._name_cn_edit.text().strip()
            if name_cn:
                updates['name_cn'] = name_cn
        
        if self._category_cb.isChecked():
            category = self._category_edit.text().strip()
            if category:
                updates['category'] = category
        
        if self._tags_cb.isChecked():
            tags_text = self._tags_edit.text().strip()
            if tags_text:
                tags = [t.strip() for t in tags_text.split(',') if t.strip()]
                updates['tags'] = tags
        
        if self._notes_cb.isChecked():
            notes = self._notes_edit.toPlainText().strip()
            if notes:
                updates['notes'] = notes
        
        return updates