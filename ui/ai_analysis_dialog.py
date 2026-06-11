from ..utils.maya_utils import get_qt_modules

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()

_STYLE = """
QDialog, QWidget { background-color: #252525; }
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
QCheckBox { color: #d0d0d0; spacing: 6px; }
QCheckBox::indicator { width: 16px; height: 16px; }
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
        self.setWindowTitle(f'批量 AI 分析结果 — {total} 个资产')
        self.setMinimumSize(900, 400)
        self.resize(1000, min(700, 150 + total * 160))
        self.setStyleSheet(_STYLE)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(QtWidgets.QLabel(f'共 {total} 个资产分析完成，可编辑后勾选应用：'))
        top_row.addStretch()
        select_all = QtWidgets.QPushButton('全选')
        select_all.setObjectName('selectAllBtn')
        select_all.clicked.connect(self._select_all)
        top_row.addWidget(select_all)
        deselect_all = QtWidgets.QPushButton('取消全选')
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
        right_panel.setFixedWidth(220)
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(8)

        self._preview_thumb = QtWidgets.QLabel()
        self._preview_thumb.setFixedSize(200, 200)
        self._preview_thumb.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._preview_thumb.setStyleSheet(
            'background-color: #1a1a1a; border: 1px solid #3a3a3a; border-radius: 8px;')
        right_layout.addWidget(self._preview_thumb)

        self._preview_name = QtWidgets.QLabel('')
        self._preview_name.setStyleSheet('color: #ffffff; font-size: 14px; font-weight: bold;')
        self._preview_name.setWordWrap(True)
        self._preview_name.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self._preview_name)

        self._preview_info = QtWidgets.QLabel('点击左侧资产查看缩略图')
        self._preview_info.setStyleSheet('color: #808080; font-size: 12px;')
        self._preview_info.setWordWrap(True)
        self._preview_info.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self._preview_info)

        right_layout.addStretch()
        splitter.addWidget(right_panel)

        splitter.setSizes([700, 240])
        root.addWidget(splitter, 1)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()
        cancel_btn = QtWidgets.QPushButton('取消')
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._apply_btn = QtWidgets.QPushButton(f'应用选中的资产 ({total} 个)')
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
        sub_lib.setStyleSheet('color: #707070; font-size: 11px;')
        name_row.addWidget(sub_lib)
        fields.addLayout(name_row)

        name_cn = QtWidgets.QLineEdit(result.get('name_cn', ''))
        name_cn.setPlaceholderText('易读名')
        name_cn.setMaximumHeight(28)
        name_cn.setStyleSheet('font-size: 12px;')
        name_cn.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        fields.addWidget(name_cn)

        mid_row = QtWidgets.QHBoxLayout()
        mid_row.setSpacing(6)

        cat_edit = QtWidgets.QLineEdit(result.get('sub_category', ''))
        cat_edit.setPlaceholderText('子分类')
        cat_edit.setMaximumHeight(26)
        cat_edit.setStyleSheet('font-size: 11px;')
        cat_edit.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        mid_row.addWidget(cat_edit)

        tags_edit = QtWidgets.QLineEdit(', '.join(result.get('tags', [])))
        tags_edit.setPlaceholderText('标签')
        tags_edit.setMaximumHeight(26)
        tags_edit.setStyleSheet('font-size: 11px;')
        tags_edit.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        mid_row.addWidget(tags_edit, 1)
        fields.addLayout(mid_row)

        notes_edit = QtWidgets.QLineEdit(result.get('notes', ''))
        notes_edit.setPlaceholderText('注释')
        notes_edit.setMaximumHeight(26)
        notes_edit.setStyleSheet('font-size: 11px;')
        notes_edit.textChanged.connect(lambda *a, i=index: self._on_row_activated(i))
        fields.addWidget(notes_edit)

        self._edit_widgets[index] = {
            'name_cn': name_cn,
            'category': cat_edit,
            'tags': tags_edit,
            'notes': notes_edit,
        }

        outer.addLayout(fields, 1)
        return frame

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
                pix = pix.scaled(200, 200, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                 QtCore.Qt.TransformationMode.SmoothTransformation)
                self._preview_thumb.setPixmap(pix)
            else:
                self._preview_thumb.setText('无缩略图')
        else:
            self._preview_thumb.setText('无缩略图')

        self._preview_name.setText(result.get('name_cn', '') or material.get('name', ''))
        lines = []
        cat = result.get('sub_category', '')
        if cat:
            lines.append(f'分类: {cat}')
        tags = result.get('tags', [])
        if tags:
            lines.append(f'标签: {", ".join(tags)}')
        notes = result.get('notes', '')
        if notes:
            lines.append(f'注释: {notes[:120]}')
        self._preview_info.setText('\n'.join(lines))

    def _select_all(self):
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def _deselect_all(self):
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def _update_apply_btn_text(self):
        count = sum(1 for cb in self._checkboxes.values() if cb.isChecked())
        self._apply_btn.setText(f'应用选中的资产 ({count} 个)')

    def _on_apply(self):
        checked_indices = [i for i, cb in self._checkboxes.items() if cb.isChecked()]
        if not checked_indices:
            QtWidgets.QMessageBox.information(self, '提示', '没有选中任何资产')
            return

        selected_results = []
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
            })

        self.batchApplied.emit(selected_results)
        self.accept()


class AIAnalysisDialog(QtWidgets.QDialog):
    analysisApplied = QtCore.Signal(dict)
    
    def __init__(self, parent=None, material=None, analysis_result=None):
        super(AIAnalysisDialog, self).__init__(parent)
        self._material = material
        self._analysis = analysis_result
        self._setup_ui()
    
    def _setup_ui(self):
        self.setWindowTitle('AI 分析结果')
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
        
        info_group = QtWidgets.QGroupBox('资产信息')
        info_layout = QtWidgets.QVBoxLayout(info_group)
        info_layout.setContentsMargins(10, 10, 10, 10)
        
        name_layout = QtWidgets.QHBoxLayout()
        name_label = QtWidgets.QLabel('原始名称:')
        name_label.setFixedWidth(70)
        self._original_name = QtWidgets.QLabel('')
        self._original_name.setStyleSheet('color: #ffffff;')
        name_layout.addWidget(name_label)
        name_layout.addWidget(self._original_name)
        info_layout.addLayout(name_layout)
        
        sub_lib_layout = QtWidgets.QHBoxLayout()
        sub_lib_label = QtWidgets.QLabel('顶级分类:')
        sub_lib_label.setFixedWidth(70)
        self._sub_library = QtWidgets.QLabel('')
        self._sub_library.setStyleSheet('color: #ffffff;')
        sub_lib_layout.addWidget(sub_lib_label)
        sub_lib_layout.addWidget(self._sub_library)
        info_layout.addLayout(sub_lib_layout)
        
        header_layout.addWidget(info_group)
        layout.addLayout(header_layout)
        
        result_group = QtWidgets.QGroupBox('AI 分析结果')
        result_layout = QtWidgets.QVBoxLayout(result_group)
        result_layout.setContentsMargins(10, 10, 10, 10)
        result_layout.setSpacing(10)
        
        name_cn_layout = QtWidgets.QHBoxLayout()
        name_cn_label = QtWidgets.QLabel('易读名:')
        name_cn_label.setFixedWidth(70)
        self._name_cn_edit = QtWidgets.QLineEdit()
        self._name_cn_edit.setPlaceholderText('AI 分析的易读名称')
        name_cn_layout.addWidget(name_cn_label)
        name_cn_layout.addWidget(self._name_cn_edit)
        result_layout.addLayout(name_cn_layout)
        
        category_layout = QtWidgets.QHBoxLayout()
        category_label = QtWidgets.QLabel('建议分类:')
        category_label.setFixedWidth(70)
        self._category_edit = QtWidgets.QLineEdit()
        self._category_edit.setPlaceholderText('建议的子分类')
        category_layout.addWidget(category_label)
        category_layout.addWidget(self._category_edit)
        result_layout.addLayout(category_layout)
        
        tags_layout = QtWidgets.QHBoxLayout()
        tags_label = QtWidgets.QLabel('标签:')
        tags_label.setFixedWidth(70)
        self._tags_edit = QtWidgets.QLineEdit()
        self._tags_edit.setPlaceholderText('用逗号分隔的标签')
        tags_layout.addWidget(tags_label)
        tags_layout.addWidget(self._tags_edit)
        result_layout.addLayout(tags_layout)
        
        notes_layout = QtWidgets.QVBoxLayout()
        notes_label = QtWidgets.QLabel('注释:')
        self._notes_edit = QtWidgets.QTextEdit()
        self._notes_edit.setFixedHeight(80)
        self._notes_edit.setPlaceholderText('AI 分析的注释描述')
        notes_layout.addWidget(notes_label)
        notes_layout.addWidget(self._notes_edit)
        result_layout.addLayout(notes_layout)
        
        layout.addWidget(result_group)
        
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.addStretch()
        
        cancel_btn = QtWidgets.QPushButton('取消')
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        apply_btn = QtWidgets.QPushButton('应用到元数据')
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