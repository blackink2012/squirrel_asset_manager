from ..utils.maya_utils import get_qt_modules

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


class FlowLayout(QtWidgets.QLayout):
    def __init__(self, parent=None, margin=0, spacing=-1):
        super(FlowLayout, self).__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QtCore.QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super(FlowLayout, self).setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QtCore.QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        effective_rect = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            space_x = self.spacing()
            space_y = self.spacing()
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y() + margins.bottom()


class DetailPanelWidget(QtWidgets.QWidget):
    materialApplied = QtCore.Signal(dict)
    materialEdited = QtCore.Signal(dict)
    materialDeleted = QtCore.Signal(str)
    thumbnailUpdateRequested = QtCore.Signal(str)
    tagFilterRequested = QtCore.Signal(str)
    exportPresetRequested = QtCore.Signal(dict)

    def __init__(self, parent=None):
        super(DetailPanelWidget, self).__init__(parent)
        self._material = None
        self._font_size = 13
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("background-color: #252525; border-top: 1px solid #3a3a3a;")

        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(16)

        preview_layout = QtWidgets.QVBoxLayout()
        preview_layout.setSpacing(8)

        self._swatch_label = QtWidgets.QLabel()
        self._swatch_label.setFixedSize(140, 140)
        self._swatch_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._swatch_label.setStyleSheet(
            "background-color: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px;"
        )
        preview_layout.addWidget(self._swatch_label)

        preview_type_row = QtWidgets.QHBoxLayout()
        preview_type_row.setSpacing(4)
        shapes = [
            ("\u25cf", "\u7403\u4f53\u9884\u89c8"),
            ("\u25a0", "\u7acb\u65b9\u4f53\u9884\u89c8"),
            ("\u25b2", "\u5e73\u9762\u9884\u89c8"),
        ]
        for char, tip in shapes:
            btn = QtWidgets.QPushButton(char)
            btn.setFixedSize(28, 28)
            btn.setToolTip(tip)
            btn.setStyleSheet(
                "QPushButton { background-color: #333333; color: #909090; border: none; "
                "border-radius: 4px; font-size: 12px; }"
                "QPushButton:hover { color: #d0d0d0; }"
                "QPushButton:checked { background-color: #2d4a6f; color: #5294e2; }"
            )
            btn.setCheckable(True)
            preview_type_row.addWidget(btn)
        preview_type_row.addStretch()
        preview_layout.addLayout(preview_type_row)

        main_layout.addLayout(preview_layout)

        info_panel = QtWidgets.QWidget()
        info_layout = QtWidgets.QVBoxLayout(info_panel)
        info_layout.setContentsMargins(0, 4, 0, 0)
        info_layout.setSpacing(6)

        title_row = QtWidgets.QHBoxLayout()
        self._name_label = QtWidgets.QLabel("\u672a\u9009\u62e9\u6750\u8d28")
        self._name_label.setStyleSheet("color: #ffffff; font-size: 16px; font-weight: bold;")
        title_row.addWidget(self._name_label)

        self._fav_btn = QtWidgets.QPushButton("\u2606")
        self._fav_btn.setFixedSize(28, 28)
        self._fav_btn.setToolTip("\u6dfb\u52a0\u5230\u6536\u85cf\u5939")
        self._fav_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #606060; border: none; font-size: 16px; }"
            "QPushButton:hover { color: #FFD700; }"
        )
        self._fav_btn.clicked.connect(self._on_toggle_favorite)
        self._fav_btn.setVisible(False)
        title_row.addWidget(self._fav_btn)
        title_row.addStretch()
        info_layout.addLayout(title_row)

        self._info_label = QtWidgets.QLabel("\u7c7b\u578b: -  |  \u5206\u7c7b: -")
        self._info_label.setStyleSheet("color: #a0a0a0; font-size: 13px;")
        info_layout.addWidget(self._info_label)

        desc_label = QtWidgets.QLabel("")
        desc_label.setStyleSheet("color: #808080; font-size: 12px;")
        desc_label.setWordWrap(True)
        self._desc_label = desc_label
        info_layout.addWidget(self._desc_label)

        divider1 = QtWidgets.QFrame()
        divider1.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        divider1.setStyleSheet("color: #3a3a3a;")
        info_layout.addWidget(divider1)

        tags_header = QtWidgets.QLabel("\u6807\u7b7e")
        tags_header.setStyleSheet("color: #909090; font-size: 12px;")
        info_layout.addWidget(tags_header)

        self._tags_widget = QtWidgets.QWidget()
        self._tags_layout = FlowLayout(self._tags_widget)
        self._tags_layout.setSpacing(4)
        info_layout.addWidget(self._tags_widget)

        # ── 注释 ──
        self._notes_label = QtWidgets.QLabel("")
        self._notes_label.setStyleSheet("color: #888888; font-size: 12px;")
        self._notes_label.setWordWrap(True)
        self._notes_label.setVisible(False)
        info_layout.addWidget(self._notes_label)

        info_layout.addStretch()
        main_layout.addWidget(info_panel, 1)

        self._show_empty()

    def _show_empty(self):
        pix = QtGui.QPixmap(140, 140)
        pix.fill(QtGui.QColor("#2a2a2a"))
        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        center = QtCore.QPoint(70, 55)
        painter.setBrush(QtGui.QColor("#3a3a3a"))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(center, 35, 35)

        highlight = QtCore.QPoint(55, 45)
        painter.setBrush(QtGui.QColor(80, 80, 80, 80))
        painter.drawEllipse(highlight, 10, 8)

        painter.setPen(QtGui.QColor(255, 255, 255, 30))
        font = painter.font()
        font.setPointSize(11)
        painter.setFont(font)
        painter.drawText(QtCore.QRect(0, 100, 140, 30), QtCore.Qt.AlignmentFlag.AlignCenter, "\u672a\u9009\u62e9")
        painter.end()
        self._swatch_label.setPixmap(pix)

        self._name_label.setText("\u672a\u9009\u62e9\u6750\u8d28")
        self._info_label.setText("\u7c7b\u578b: -  |  \u5206\u7c7b: -")
        self._desc_label.setText("")
        self._fav_btn.setVisible(False)
        self._clear_tags()
        self._notes_label.setVisible(False)

    def show_material(self, material):
        if material is None:
            self._show_empty()
            return
        self._material = material

        self._draw_sphere_preview(material)

        self._name_label.setText(material.get("name_cn", material.get("name", "")))
        self._info_label.setText(
            f"\u7c7b\u578b: {material.get('node_type', '-')}  |  "
            f"\u5206\u7c7b: {self._category_cn(material.get('category', ''))}"
        )
        self._desc_label.setText(material.get("description", ""))

        is_fav = material.get("_favorited", False)
        self._fav_btn.setVisible(True)
        self._fav_btn.setText("\u2605" if is_fav else "\u2606")
        self._fav_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {'#FFD700' if is_fav else '#606060'};"
            f"border: none; font-size: 16px; }}"
            "QPushButton:hover { color: #FFD700; }"
        )

        self._show_tags(material.get("tags", []))

        notes = material.get("notes", "")
        if notes:
            self._notes_label.setText("\u6ce8\u91ca: " + notes)
            self._notes_label.setVisible(True)
        else:
            self._notes_label.setVisible(False)

    def _draw_sphere_preview(self, material):
        pix = QtGui.QPixmap(140, 140)
        pix.fill(QtGui.QColor("#2a2a2a"))
        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        color = QtGui.QColor(material.get("color", "#606060"))
        center = QtCore.QPoint(70, 52)
        radius = 38

        gradient = QtGui.QRadialGradient(
            QtCore.QPointF(center.x() - radius * 0.3, center.y() - radius * 0.35),
            radius * 1.3
        )
        lighter = color.lighter(150)
        darker = color.darker(180)
        gradient.setColorAt(0.0, lighter)
        gradient.setColorAt(0.5, color)
        gradient.setColorAt(0.85, darker)
        gradient.setColorAt(1.0, darker.darker(150))

        painter.setBrush(QtGui.QBrush(gradient))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(center, radius, radius)

        highlight_grad = QtGui.QRadialGradient(
            QtCore.QPointF(center.x() - radius * 0.4, center.y() - radius * 0.5),
            radius * 0.55
        )
        highlight_grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 70))
        highlight_grad.setColorAt(0.4, QtGui.QColor(255, 255, 255, 25))
        highlight_grad.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
        painter.setBrush(QtGui.QBrush(highlight_grad))
        painter.drawEllipse(center, radius, radius)

        painter.setPen(QtGui.QColor(255, 255, 255, 40))
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(QtCore.QRect(0, 100, 140, 30), QtCore.Qt.AlignmentFlag.AlignCenter,
                         material.get("name_cn", "")[:8])
        painter.end()
        self._swatch_label.setPixmap(pix)

    def _category_cn(self, cat_id):
        names = {
            "metal": "\u91d1\u5c5e", "fabric": "\u5e03\u6599", "plastic": "\u5851\u6599",
            "glass": "\u73bb\u7483", "skin": "\u76ae\u80a4", "wood": "\u6728\u6750",
            "stone": "\u77f3\u6750", "liquid": "\u6db2\u4f53", "foliage": "\u690d\u88ab",
        }
        return names.get(cat_id, cat_id)

    def set_font_size(self, font_size):
        print(f"[DEBUG] DetailPanel.set_font_size({font_size})")
        self._font_size = font_size
        if self._material:
            self._show_tags(self._material.get("tags", []))

    def _show_tags(self, tags):
        print(f"[DEBUG] _show_tags called, self._font_size={self._font_size}, tags={tags}")
        self._clear_tags()
        for tag in tags:
            tag_btn = QtWidgets.QPushButton(tag)
            tag_btn.setStyleSheet(
                f"QPushButton {{ background-color: #2a3a4a; color: #5294e2; border: 1px solid #3a5a7a; "
                f"border-radius: 10px; padding: 3px 10px; font-size: {self._font_size}px; }}"
                "QPushButton:hover { background-color: #3a4a5a; border-color: #5294e2; }"
            )
            tag_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            tag_btn.clicked.connect(lambda checked, t=tag: self.tagFilterRequested.emit(t))
            self._tags_layout.addWidget(tag_btn)

    def _clear_tags(self):
        while self._tags_layout.count():
            item = self._tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_toggle_favorite(self):
        if self._material:
            fav = not self._material.get("_favorited", False)
            self._material["_favorited"] = fav
            self._fav_btn.setText("\u2605" if fav else "\u2606")
            self._fav_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {'#FFD700' if fav else '#606060'};"
                f"border: none; font-size: 16px; }}"
                "QPushButton:hover { color: #FFD700; }"
            )
