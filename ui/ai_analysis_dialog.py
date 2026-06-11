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
        self._setup_ui()

    def _setup_ui(self):
        total = len(self._results)
        self.setWindowTitle(f'批量 AI 分析结果 — {total} 个资产')
        self.setMinimumSize(650, 300)
        self.resize(700, min(600, 120 + total * 100))
        self.setStyleSheet(_STYLE)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 顶部操作栏
        top_row = QtWidgets.QHBoxLayout()
        summary = f'共 {total} 个资产分析完成，请勾选要应用的资产：'
        top_label = QtWidgets.QLabel(summary)
        top_label.setStyleSheet('color: #ffffff; font-size: 14px;')
        top_row.addWidget(top_label)
        top_row.addStretch()

        select_all = QtWidgets.QPushButton('全选')
        select_all.setObjectName('selectAllBtn')
        select_all.clicked.connect(self._select_all)
        top_row.addWidget(select_all)

        deselect_all = QtWidgets.QPushButton('取消全选')
        deselect_all.setObjectName('deselectAllBtn')
        deselect_all.clicked.connect(self._deselect_all)
        top_row.addWidget(deselect_all)

        layout.addLayout(top_row)

        # 滚动列表
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setStyleSheet('QScrollArea { background: transparent; }')

        list_widget = QtWidgets.QWidget()
        list_layout = QtWidgets.QVBoxLayout(list_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(4)

        for i, entry in enumerate(self._results):
            row = self._build_result_row(i, entry)
            list_layout.addWidget(row)

        list_layout.addStretch()
        scroll.setWidget(list_widget)
        layout.addWidget(scroll, 1)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        cancel_btn = QtWidgets.QPushButton('取消')
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        apply_btn = QtWidgets.QPushButton(f'应用选中的资产 ({total} 个)')
        apply_btn.setObjectName('applyBtn')
        apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(apply_btn)
        self._apply_btn = apply_btn

        layout.addLayout(btn_row)

    def _build_result_row(self, index, entry):
        material = entry.get('material', {})
        result = entry.get('result', {})

        frame = QtWidgets.QFrame()
        frame.setStyleSheet(
            'QFrame#resultCard { background-color: #2a2a2a; border: 1px solid #3a3a3a; '
            'border-radius: 6px; padding: 4px; }')
        frame.setObjectName('resultCard')

        row_layout = QtWidgets.QHBoxLayout(frame)
        row_layout.setContentsMargins(10, 8, 10, 8)
        row_layout.setSpacing(10)

        # 复选框
        cb = QtWidgets.QCheckBox()
        cb.setChecked(True)
        cb.toggled.connect(self._update_apply_btn_text)
        row_layout.addWidget(cb)
        self._checkboxes[index] = cb

        # 缩略图
        thumb_label = QtWidgets.QLabel()
        thumb_label.setFixedSize(50, 50)
        thumb_label.setStyleSheet(
            'background-color: #1a1a1a; border: 1px solid #3a3a3a; border-radius: 4px;')
        thumb_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        thumb_bytes = material.get('thumb_bytes')
        if thumb_bytes:
            pix = QtGui.QPixmap()
            pix.loadFromData(thumb_bytes)
            if not pix.isNull():
                pix = pix.scaled(50, 50, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                 QtCore.Qt.TransformationMode.SmoothTransformation)
                thumb_label.setPixmap(pix)
            else:
                thumb_label.setText('N/A')
        else:
            thumb_label.setText('N/A')
        row_layout.addWidget(thumb_label)

        # 信息
        info_layout = QtWidgets.QVBoxLayout()
        info_layout.setSpacing(2)

        orig_name = material.get('name', '?')
        new_name = result.get('name_cn', '')
        name_line = QtWidgets.QHBoxLayout()
        name_line.setSpacing(6)
        orig_lbl = QtWidgets.QLabel(orig_name)
        orig_lbl.setStyleSheet('color: #808080; font-size: 12px;')
        name_line.addWidget(orig_lbl)
        if new_name:
            arrow = QtWidgets.QLabel('→')
            arrow.setStyleSheet('color: #5294e2; font-size: 12px;')
            name_line.addWidget(arrow)
            new_lbl = QtWidgets.QLabel(new_name)
            new_lbl.setStyleSheet('color: #5294e2; font-size: 12px; font-weight: bold;')
            name_line.addWidget(new_lbl)
        name_line.addStretch()
        info_layout.addLayout(name_line)

        sub_cat = result.get('sub_category', '')
        cat_line = QtWidgets.QLabel(f'分类: {sub_cat}') if sub_cat else QtWidgets.QLabel('')
        cat_line.setStyleSheet('color: #a0a0a0; font-size: 11px;')
        info_layout.addWidget(cat_line)

        tags = result.get('tags', [])
        if tags:
            tag_text = ', '.join(tags)
            tag_line = QtWidgets.QLabel(f'标签: {tag_text}')
            tag_line.setStyleSheet('color: #808080; font-size: 11px;')
            info_layout.addWidget(tag_line)

        notes = result.get('notes', '')
        if notes:
            note_line = QtWidgets.QLabel(notes[:80] + ('...' if len(notes) > 80 else ''))
            note_line.setStyleSheet('color: #707070; font-size: 11px;')
            info_layout.addWidget(note_line)

        row_layout.addLayout(info_layout, 1)

        return frame

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

        selected_results = [self._results[i] for i in checked_indices]
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