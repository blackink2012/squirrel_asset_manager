try:
    from ..utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

from ..utils.maya_utils import get_qt_modules

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()

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
        self.setWindowTitle(t('dialog.ai_batch_results.title', n=total))
        self.setMinimumSize(1140, 500)
        self.resize(1240, min(700, 200 + total * 200))
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
        right_panel.setFixedWidth(420)
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(8)

        self._preview_thumb = QtWidgets.QLabel()
        self._preview_thumb.setFixedSize(400, 400)
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
        fields.addWidget(name_cn)

        mid_row = QtWidgets.QHBoxLayout()
        mid_row.setSpacing(6)

        cat_edit = QtWidgets.QLineEdit(result.get('sub_category', ''))
        cat_edit.setPlaceholderText(t('label.sub_category'))
        cat_edit.setMaximumHeight(30)
        cat_edit.setStyleSheet('font-size: 13px;')
        cat_edit.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        mid_row.addWidget(cat_edit)

        tags_edit = QtWidgets.QLineEdit(', '.join(result.get('tags', [])))
        tags_edit.setPlaceholderText(t('label.tags'))
        tags_edit.setMaximumHeight(30)
        tags_edit.setStyleSheet('font-size: 13px;')
        tags_edit.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        mid_row.addWidget(tags_edit, 1)
        fields.addLayout(mid_row)

        notes_edit = QtWidgets.QLineEdit(result.get('notes', ''))
        notes_edit.setPlaceholderText(t('label.notes'))
        notes_edit.setMaximumHeight(30)
        notes_edit.setStyleSheet('font-size: 13px;')
        notes_edit.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        fields.addWidget(notes_edit)

        self._edit_widgets[index] = {
            'name_cn': name_cn,
            'category': cat_edit,
            'tags': tags_edit,
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
                pix = pix.scaled(400, 400, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
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

            name_cn = w.get('name_cn')
            if name_cn:
                val = name_cn.text().strip()
                if val:
                    result['name_cn'] = val

            cat_w = w.get('category')
            if cat_w:
                val = cat_w.text().strip()
                if val:
                    result['sub_category'] = val

            tags_w = w.get('tags')
            if tags_w:
                val = tags_w.text().strip()
                if val:
                    result['tags'] = [t.strip() for t in val.split(',') if t.strip()]

            notes_w = w.get('notes')
            if notes_w:
                val = notes_w.text().strip()
                if val:
                    result['notes'] = val

            selected_results.append({
                'material': entry.get('material', {}),
                'result': result,
                'move_to_category': move_to_category,
            })

        self.batchApplied.emit(selected_results)
        self.accept()


class AIAnalysisConfigDialog(QtWidgets.QDialog):
    configConfirmed = QtCore.Signal(dict)

    def __init__(self, parent=None, available_models=None):
        super(AIAnalysisConfigDialog, self).__init__(parent)
        self._available_models = available_models or []
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(t('dialog.ai_analysis_config.title'))
        self.setFixedSize(380, 280)
        self.setStyleSheet(_STYLE)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QtWidgets.QLabel(t('dialog.ai_analysis_config.prompt'))
        title.setStyleSheet('color: #ffffff; font-size: 15px; font-weight: bold;')
        layout.addWidget(title)

        layout.addSpacing(4)

        lang_layout = QtWidgets.QHBoxLayout()
        lang_label = QtWidgets.QLabel(t('label.output_language'))
        lang_label.setFixedWidth(80)
        lang_layout.addWidget(lang_label)
        self._lang_combo = QtWidgets.QComboBox()
        self._lang_combo.addItems(['中文', 'English'])
        self._lang_combo.setCurrentIndex(0)
        lang_layout.addWidget(self._lang_combo, 1)
        layout.addLayout(lang_layout)

        model_layout = QtWidgets.QHBoxLayout()
        model_label = QtWidgets.QLabel(t('label.ai_model'))
        model_label.setFixedWidth(80)
        model_layout.addWidget(model_label)
        self._model_combo = QtWidgets.QComboBox()
        if self._available_models:
            self._model_combo.addItems(self._available_models)
            default_model = 'qwen3-vl:8b'
            if default_model in self._available_models:
                self._model_combo.setCurrentText(default_model)
            elif 'qwen3.5:9b' in self._available_models:
                self._model_combo.setCurrentText('qwen3.5:9b')
        else:
            self._model_combo.addItem('qwen3-vl:8b')
        model_layout.addWidget(self._model_combo, 1)
        layout.addLayout(model_layout)

        self._review_cb = QtWidgets.QCheckBox(t('dialog.ai_analysis_config.review_after'))
        self._review_cb.setChecked(True)
        layout.addWidget(self._review_cb)

        self._translate_tags_cb = QtWidgets.QCheckBox(t('dialog.ai_analysis_config.translate_tags'))
        self._translate_tags_cb.setToolTip(t('dialog.ai_analysis_config.translate_tags.tooltip'))
        layout.addWidget(self._translate_tags_cb)

        layout.addSpacing(6)

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

    def _on_confirm(self):
        config = {
            'language': self._lang_combo.currentText(),
            'review_output': self._review_cb.isChecked(),
            'model': self._model_combo.currentText(),
            'translate_existing_tags': self._translate_tags_cb.isChecked(),
        }
        self.configConfirmed.emit(config)
        self.accept()

    def get_config(self):
        return {
            'language': self._lang_combo.currentText(),
            'review_output': self._review_cb.isChecked(),
            'model': self._model_combo.currentText(),
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
        self.setWindowTitle(t('dialog.ai_analysis.title'))
        self.setFixedSize(500, 520)
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
        self._thumb_label.setFixedSize(128, 128)
        self._thumb_label.setStyleSheet("background-color: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px;")
        self._thumb_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self._thumb_label)
        
        info_group = QtWidgets.QGroupBox(t('dialog.ai_analysis.asset_info'))
        info_layout = QtWidgets.QVBoxLayout(info_group)
        info_layout.setContentsMargins(10, 10, 10, 10)
        
        name_layout = QtWidgets.QHBoxLayout()
        name_label = QtWidgets.QLabel(t('label.original_name'))
        name_label.setFixedWidth(70)
        self._original_name = QtWidgets.QLabel('')
        self._original_name.setStyleSheet('color: #ffffff;')
        name_layout.addWidget(name_label)
        name_layout.addWidget(self._original_name)
        info_layout.addLayout(name_layout)
        
        sub_lib_layout = QtWidgets.QHBoxLayout()
        sub_lib_label = QtWidgets.QLabel(t('label.top_category'))
        sub_lib_label.setFixedWidth(70)
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
        name_cn_label = QtWidgets.QLabel(t('label.readable_name') + ':')
        name_cn_label.setFixedWidth(70)
        self._name_cn_edit = QtWidgets.QLineEdit()
        self._name_cn_edit.setPlaceholderText(t('dialog.ai_analysis.name_cn_placeholder'))
        name_cn_layout.addWidget(name_cn_label)
        name_cn_layout.addWidget(self._name_cn_edit)
        result_layout.addLayout(name_cn_layout)
        
        category_layout = QtWidgets.QHBoxLayout()
        category_label = QtWidgets.QLabel(t('label.suggested_category'))
        category_label.setFixedWidth(70)
        self._category_edit = QtWidgets.QLineEdit()
        self._category_edit.setPlaceholderText(t('dialog.ai_analysis.category_placeholder'))
        category_layout.addWidget(category_label)
        category_layout.addWidget(self._category_edit)
        result_layout.addLayout(category_layout)
        
        tags_layout = QtWidgets.QHBoxLayout()
        tags_label = QtWidgets.QLabel(t('label.tags'))
        tags_label.setFixedWidth(70)
        self._tags_edit = QtWidgets.QLineEdit()
        self._tags_edit.setPlaceholderText(t('dialog.ai_analysis.tags_placeholder'))
        tags_layout.addWidget(tags_label)
        tags_layout.addWidget(self._tags_edit)
        result_layout.addLayout(tags_layout)
        
        notes_layout = QtWidgets.QVBoxLayout()
        notes_label = QtWidgets.QLabel(t('label.notes'))
        self._notes_edit = QtWidgets.QTextEdit()
        self._notes_edit.setFixedHeight(80)
        self._notes_edit.setPlaceholderText(t('dialog.ai_analysis.notes_placeholder'))
        notes_layout.addWidget(notes_label)
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
                    pixmap = pixmap.scaled(128, 128, QtCore.Qt.AspectRatioMode.KeepAspectRatio, 
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
        updates = {}
        
        name_cn = self._name_cn_edit.text().strip()
        if name_cn:
            updates['name_cn'] = name_cn
        
        category = self._category_edit.text().strip()
        if category:
            updates['category'] = category
        
        tags_text = self._tags_edit.text().strip()
        if tags_text:
            tags = [t.strip() for t in tags_text.split(',') if t.strip()]
            updates['tags'] = tags
        
        notes = self._notes_edit.toPlainText().strip()
        if notes:
            updates['notes'] = notes
        
        if updates:
            updates['material_id'] = self._material.get('id', '')
            updates['move_to_category'] = self._move_cb.isChecked()
            self.analysisApplied.emit(updates)
        
        self.accept()
    
    def get_updates(self):
        updates = {}
        
        name_cn = self._name_cn_edit.text().strip()
        if name_cn:
            updates['name_cn'] = name_cn
        
        category = self._category_edit.text().strip()
        if category:
            updates['category'] = category
        
        tags_text = self._tags_edit.text().strip()
        if tags_text:
            tags = [t.strip() for t in tags_text.split(',') if t.strip()]
            updates['tags'] = tags
        
        notes = self._notes_edit.toPlainText().strip()
        if notes:
            updates['notes'] = notes
        
        return updates