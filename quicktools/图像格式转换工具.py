#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
图像格式转换工具
使用OpenCV进行HDR/EXR/TIFF/PNG/JPEG等格式的批量转换
"""

import os
os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '1'

import sys
import random
import string
import uuid
import io
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import maya.cmds as cmds
    IN_MAYA = True
except ImportError:
    IN_MAYA = False

_T = None
_help_path = lambda p: p
try:
    from utils.i18n import t as _T, help_path as _hpath
    _help_path = _hpath
except ImportError:
    try:
        from squirrel_asset_manager.utils.i18n import t as _T, help_path as _hpath
        _help_path = _hpath
    except ImportError:
        _T = None

def t(key, **kwargs):
    return _T(key, **kwargs) if _T is not None else (key.format(**kwargs) if kwargs else key)


def get_qt_modules():
    try:
        from PySide6 import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui
    except ImportError:
        try:
            from PySide2 import QtWidgets, QtCore, QtGui
            return QtWidgets, QtCore, QtGui
        except ImportError:
            return None, None, None

QtWidgets, QtCore, QtGui = get_qt_modules()


def check_dependencies():
    """检查依赖是否安装，返回 (ok, message)"""
    missing = []

    try:
        import cv2
    except ImportError:
        missing.append("opencv-python")

    try:
        import numpy as np
    except ImportError:
        missing.append("numpy")

    if not missing:
        try:
            import imageio.v3
            return True, t("msg.dependencies_ready")
        except ImportError:
            return True, t("qtool.imgconvert.ready_imageio_recommended")

    python_path = sys.executable
    python_version = sys.version

    install_cmds = []
    for pkg in missing:
        install_cmds.append(f"pip install {pkg}")
    install_cmd = " & ".join(install_cmds) if len(install_cmds) > 1 else install_cmds[0]

    import glob as _glob
    _found = _glob.glob(r"C:\Program Files\Autodesk\Maya*\bin\mayapy.exe")
    if _found:
        _maya_py = _found[-1]
    else:
        _maya_py = r"C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe"

    maya_pip_cmd = f'"{_maya_py}" -m pip install opencv-python numpy imageio'

    msg = (
        t("qtool.imgconvert.dep_missing_libraries")
        + "\n  "
        + "\n  ".join(missing)
        + t("qtool.imgconvert.dep_interpreter", python_path=python_path, python_version=python_version)
        + "\n\n━━ " + t("qtool.imgconvert.dep_install_methods") + " ━━"
        + "\n\n" + t("qtool.imgconvert.dep_method1_recommended")
        + "\n  " + t("qtool.imgconvert.dep_method1_cmd")
        + "\n\n  " + maya_pip_cmd
        + "\n\n" + t("qtool.imgconvert.dep_method2")
        + "\n  " + t("qtool.imgconvert.dep_method2_note")
        + "\n\n  " + install_cmd
        + "\n\n" + t("qtool.imgconvert.dep_method3")
        + "\n\n  import subprocess\n  import sys\n  result = subprocess.run(\n      [sys.executable, '-m', 'pip', 'install', 'opencv-python', 'numpy', 'imageio'],\n      capture_output=True,\n      text=True\n  )\n  print('" + t("qtool.imgconvert.dep_result") + "', result.returncode)\n  print('" + t("qtool.imgconvert.dep_output") + "', result.stdout)\n  print('" + t("qtool.imgconvert.dep_error") + "', result.stderr)\n"
    )
    return False, msg


def sanitize_filename(name):
    """清理文件名使其符合 Maya 规范

    Maya 要求文件名仅包含: 字母、数字、下划线、点号
    且必须以字母开头。
    非法字符替换为 '_'，如果开头不是字母则替换为一个随机字母。
    """
    allowed = set(string.ascii_letters + string.digits + '_.')
    result = ''.join(c if c in allowed else '_' for c in name)

    if result and not result[0].isalpha():
        prefix = random.choice(string.ascii_letters)
        result = prefix + result[1:] if len(result) > 1 else prefix

    if not result:
        result = '_untitled'

    if result != name:
        print(f"文件名不符合Maya规范，已自动清理: {name} -> {result}")
    return result


# ── 核心转换函数 ──

SUPPORTED_FORMATS = {
    '.hdr': {'mode': 'float', 'desc': 'Radiance HDR'},
    '.exr': {'mode': 'float', 'desc': 'OpenEXR'},
    '.png': {'mode': 'uint8', 'desc': 'PNG'},
    '.jpg': {'mode': 'uint8', 'desc': 'JPEG'},
    '.jpeg': {'mode': 'uint8', 'desc': 'JPEG'},
    '.tif': {'mode': 'both', 'desc': 'TIFF'},
    '.tiff': {'mode': 'both', 'desc': 'TIFF'},
    '.bmp': {'mode': 'uint8', 'desc': 'BMP'},
    '.webp': {'mode': 'uint8', 'desc': 'WebP'},
    '.tga': {'mode': 'uint8', 'desc': 'TGA'},
}


def convert_image(input_path, output_path, input_format, output_format,
                  format_options=None, target_width=None, target_height=None,
                  keep_aspect_ratio=True):
    """单文件图像格式转换

    Args:
        input_path: 输入文件路径
        output_path: 输出文件路径
        input_format: 输入格式后缀（如 .hdr, .exr）
        output_format: 输出格式后缀（如 .exr, .png）
        format_options: dict，可包含：
            - jpeg_quality: 1-100, 默认 95
            - png_compression: 0-9, 默认 5
            - exr_compression: 默认 'ZIP'
            - tiff_compression: 1=NONE, 5=LZW, 8=DEFLATE
        target_width: 目标宽度（像素），None表示不限制
        target_height: 目标高度（像素），None表示不限制
        keep_aspect_ratio: 是否保持宽高比

    Returns:
        (success: bool, message: str)
    """
    import cv2
    import numpy as np

    if format_options is None:
        format_options = {
            'jpeg_quality': 95,
            'png_compression': 5,
            'exr_compression': 'ZIP',
            'tiff_compression': 5,
        }

    original_cwd = os.getcwd()
    input_dir = os.path.dirname(input_path)
    output_dir = os.path.dirname(output_path)
    input_filename = os.path.basename(input_path)
    output_filename = os.path.basename(output_path)

    image = None

    try:
        os.chdir(input_dir)

        if input_format in ('.hdr', '.hdri', '.pic'):
            image = _imread_exr_hdr(input_filename, input_format, cv2, np)
            if image is None:
                return False, t("qtool.imgconvert.err_read_hdr", path=input_path)
            if image.dtype != np.float32:
                image = image.astype(np.float32)
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        elif input_format == '.exr':
            image = _imread_exr_hdr(input_filename, input_format, cv2, np)
            if image is None:
                return False, t("qtool.imgconvert.err_read_exr", path=input_path)
            if image.dtype != np.float32:
                image = image.astype(np.float32)
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        else:
            image = cv2.imread(input_filename, cv2.IMREAD_UNCHANGED)
            if image is None:
                return False, t("qtool.imgconvert.err_read_image", path=input_path)
            if len(image.shape) == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            elif image.shape[2] == 4:
                image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
            elif image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        if target_width is not None or target_height is not None:
            image = _resize_image(image, target_width, target_height, keep_aspect_ratio)

        os.chdir(output_dir)

        if output_format == '.exr':
            output_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            success = cv2.imwrite(output_filename, output_bgr)
            if not success:
                return False, t("qtool.imgconvert.err_save_exr", path=output_path)
            return True, t("qtool.imgconvert.convert_success")

        if output_format in ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp', '.tga'):
            if image.dtype == np.float32 or image.dtype == np.float64:
                tonemapped = image / (image + 1.0)
                tonemapped = np.power(tonemapped, 1.0 / 2.2)
                tonemapped = (tonemapped * 255.0).astype(np.uint8)
                output_image = cv2.cvtColor(tonemapped, cv2.COLOR_RGB2BGR)
            else:
                output_image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

            params = []
            if output_format in ('.jpg', '.jpeg'):
                params = [cv2.IMWRITE_JPEG_QUALITY, format_options.get('jpeg_quality', 95)]
            elif output_format == '.png':
                params = [cv2.IMWRITE_PNG_COMPRESSION, format_options.get('png_compression', 5)]
            elif output_format in ('.tif', '.tiff'):
                tiff_comp = format_options.get('tiff_compression', 5)
                if isinstance(tiff_comp, str):
                    tiff_comp_map = {"NONE": 1, "LZW": 5, "DEFLATE": 8, "JPEG": 7}
                    tiff_comp = tiff_comp_map.get(tiff_comp.upper(), 5)
                params = [cv2.IMWRITE_TIFF_COMPRESSION, tiff_comp]

            success = cv2.imwrite(output_filename, output_image, params)
            if not success:
                return False, t("qtool.imgconvert.err_save_image", path=output_path)
            return True, t("qtool.imgconvert.convert_success")

        return False, t("qtool.imgconvert.err_unsupported_format", fmt=output_format)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, t("qtool.imgconvert.err_convert_failed", err=str(e))
    finally:
        os.chdir(original_cwd)
        if image is not None:
            del image


def _resize_image(image, target_width=None, target_height=None, keep_aspect_ratio=True):
    """调整图像分辨率，保持宽高比

    Args:
        image: numpy数组格式的图像（RGB）
        target_width: 目标宽度（像素），None表示不限制
        target_height: 目标高度（像素），None表示不限制
        keep_aspect_ratio: 是否保持宽高比

    Returns:
        调整后的图像
    """
    import cv2
    h, w = image.shape[:2]

    if target_width is None and target_height is None:
        return image

    if keep_aspect_ratio:
        if target_width and target_height:
            scale = min(target_width / w, target_height / h)
            new_w, new_h = int(w * scale), int(h * scale)
        elif target_width:
            scale = target_width / w
            new_w, new_h = target_width, int(h * scale)
        else:
            scale = target_height / h
            new_w, new_h = int(w * scale), target_height
    else:
        new_w = target_width if target_width else w
        new_h = target_height if target_height else h

    if new_w == w and new_h == h:
        return image

    return cv2.resize(image, (new_w, new_h),
                      interpolation=cv2.INTER_AREA if (new_w < w) else cv2.INTER_LINEAR)


def _imread_exr_hdr(filename, fmt, cv2_mod, np_mod):
    """读取EXR/HDR文件，自动处理OpenCV编解码器禁用问题

    cv2先尝试直接读取，失败则回退到imageio。
    返回BGR格式的numpy数组（与cv2.imread一致），失败返回None。
    """
    os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '1'
    try:
        img = cv2_mod.imread(filename, cv2_mod.IMREAD_ANYDEPTH | cv2_mod.IMREAD_COLOR)
        if img is not None and img.size > 0:
            return img
    except Exception:
        pass

    try:
        import imageio.v3 as iio
        raw = iio.imread(filename)
        if raw is not None and raw.size > 0:
            if raw.ndim == 3 and raw.shape[2] >= 3:
                rgb = raw[:, :, :3].astype(np_mod.float32)
            elif raw.ndim == 2:
                rgb = np_mod.stack([raw.astype(np_mod.float32)] * 3, axis=-1)
            else:
                rgb = raw.astype(np_mod.float32)
            return cv2_mod.cvtColor(rgb, cv2_mod.COLOR_RGB2BGR)
    except Exception:
        pass

    return None


def find_image_files(root_folder, input_formats=None):
    """递归查找文件夹下的图像文件

    Args:
        root_folder: 根文件夹路径
        input_formats: 要过滤的输入格式列表，None或空列表表示所有支持格式

    Returns:
        list[(file_path, relative_path)]: 文件绝对路径和相对路径元组列表
    """
    supported_exts = list(SUPPORTED_FORMATS.keys())
    
    if input_formats:
        target_exts = [f'.{fmt.strip().lower()}' if not fmt.strip().startswith('.') else fmt.strip().lower() 
                       for fmt in input_formats if fmt.strip()]
        target_exts = [ext for ext in target_exts if ext in supported_exts]
    else:
        target_exts = supported_exts

    result = []
    for dirpath, dirnames, filenames in os.walk(root_folder):
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in target_exts:
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, root_folder)
                result.append((full_path, rel_path))
    return result


def batch_convert(input_files, output_folder, output_format, format_options=None,
                  progress_callback=None):
    """批量转换图像文件

    Args:
        input_files: 输入文件路径列表，每个元素为 (file_path, relative_path) 元组
        output_folder: 输出文件夹
        output_format: 输出格式后缀
        format_options: 格式选项 dict
        progress_callback: 可选回调 (current, total, filename)

    Returns:
        (converted: int, failed: int, failed_files: list[(name, error)])
    """
    import os
    os.makedirs(output_folder, exist_ok=True)

    total = len(input_files)
    converted = 0
    failed = 0
    failed_files = []

    for i, (input_file, rel_path) in enumerate(input_files):
        input_format = os.path.splitext(input_file)[1].lower()
        rel_dir = os.path.dirname(rel_path)
        stem = os.path.splitext(os.path.basename(input_file))[0]

        safe_stem = sanitize_filename(stem)
        if safe_stem != stem:
            src_path = input_file
            new_src = os.path.join(os.path.dirname(input_file), f"{safe_stem}{input_format}")
            os.rename(src_path, new_src)
            input_file = new_src
            print(f"源文件已重命名: {os.path.basename(src_path)} -> {os.path.basename(new_src)}")

        output_subdir = os.path.join(output_folder, rel_dir)
        os.makedirs(output_subdir, exist_ok=True)
        output_path = os.path.join(output_subdir, f"{safe_stem}{output_format}")

        if progress_callback:
            progress_callback(i + 1, total, rel_path)

        success, message = convert_image(
            input_file, output_path, input_format, output_format, format_options
        )

        if success:
            converted += 1
        else:
            failed += 1
            failed_files.append((rel_path, message))

    return converted, failed, failed_files


# ── UI 对话框 ──

class ImageFormatConverterDialog(QtWidgets.QDialog):
    """图像格式转换工具 UI"""

    MODE_FILES = 0
    MODE_FOLDER = 1

    def __init__(self, parent=None):
        super(ImageFormatConverterDialog, self).__init__(parent)
        self.setWindowTitle(t("qtool.imgconvert.title"))
        self.setMinimumSize(750, 600)

        ok, msg = check_dependencies()
        if not ok:
            self._show_dependency_error(msg)
            return

        self._setup_ui()

    def _show_dependency_error(self, msg):
        """显示依赖缺失提示并关闭对话框"""
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel(msg)
        label.setWordWrap(True)
        label.setStyleSheet("color: #e06060; font-size: 13px; padding: 20px;")
        layout.addWidget(label)

        btn = QtWidgets.QPushButton(t("common.ok"))
        btn.setObjectName("okBtn")
        btn.setStyleSheet(
            "QPushButton#okBtn { background: #5294e2; color: white; padding: 8px 20px; border-radius: 4px; }"
        )
        btn.clicked.connect(self.close)
        layout.addWidget(btn, 0, QtCore.Qt.AlignCenter)

    def _setup_ui(self):
        """设置UI布局"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        main_splitter.setStyleSheet("QSplitter::handle { background: #3a3a3a; }")

        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        mode_group = QtWidgets.QGroupBox(t("qtool.imgconvert.input_mode"))
        mode_layout = QtWidgets.QHBoxLayout(mode_group)
        mode_layout.setContentsMargins(8, 8, 8, 8)

        self._mode_files = QtWidgets.QRadioButton(t("qtool.imgconvert.select_files"))
        self._mode_files.setChecked(True)
        self._mode_files.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_files)

        self._mode_folder = QtWidgets.QRadioButton(t("qtool.imgconvert.select_folder"))
        self._mode_folder.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_folder)

        left_layout.addWidget(mode_group)

        input_group = QtWidgets.QGroupBox(t("qtool.imgconvert.input"))
        input_layout = QtWidgets.QHBoxLayout(input_group)
        input_layout.setContentsMargins(8, 8, 8, 8)

        self._input_path = QtWidgets.QLineEdit()
        self._input_path.setPlaceholderText(t("qtool.imgconvert.ph_select_images"))
        input_layout.addWidget(self._input_path, 1)

        browse_btn = QtWidgets.QPushButton(t("qtool.imgconvert.browse"))
        browse_btn.clicked.connect(self._browse_input)
        input_layout.addWidget(browse_btn)

        left_layout.addWidget(input_group)

        filter_group = QtWidgets.QGroupBox(t("qtool.imgconvert.input_format_filter"))
        filter_layout = QtWidgets.QVBoxLayout(filter_group)
        filter_layout.setContentsMargins(8, 8, 8, 8)

        filter_layout.addWidget(QtWidgets.QLabel(t("qtool.imgconvert.filter_hint")))
        self._input_formats_edit = QtWidgets.QLineEdit()
        self._input_formats_edit.setPlaceholderText(t("qtool.imgconvert.ph_format_example"))
        filter_layout.addWidget(self._input_formats_edit)

        self._supported_fmt_label = QtWidgets.QLabel(t("qtool.imgconvert.supported_formats", fmts=', '.join(sorted(SUPPORTED_FORMATS.keys()))))
        self._supported_fmt_label.setStyleSheet("color: #808080; font-size: 11px;")
        filter_layout.addWidget(self._supported_fmt_label)

        left_layout.addWidget(filter_group)

        output_group = QtWidgets.QGroupBox(t("qtool.imgconvert.output_location"))
        output_layout = QtWidgets.QHBoxLayout(output_group)
        output_layout.setContentsMargins(8, 8, 8, 8)

        self._output_path = QtWidgets.QLineEdit()
        self._output_path.setPlaceholderText(t("qtool.imgconvert.ph_select_output"))
        output_layout.addWidget(self._output_path, 1)

        output_browse_btn = QtWidgets.QPushButton(t("qtool.imgconvert.browse"))
        output_browse_btn.clicked.connect(self._browse_output_folder)
        output_layout.addWidget(output_browse_btn)

        left_layout.addWidget(output_group)

        format_group = QtWidgets.QGroupBox(t("qtool.imgconvert.output_format"))
        format_layout = QtWidgets.QVBoxLayout(format_group)
        format_layout.setContentsMargins(8, 8, 8, 8)

        fmt_row = QtWidgets.QHBoxLayout()
        fmt_row.addWidget(QtWidgets.QLabel(t("qtool.imgconvert.target_format")))
        self._format_combo = QtWidgets.QComboBox()
        fmt_list = ['.exr', '.hdr', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp', '.tga']
        for fmt in fmt_list:
            desc = SUPPORTED_FORMATS[fmt]['desc']
            self._format_combo.addItem(f"{fmt} ({desc})", fmt)
        self._format_combo.setCurrentIndex(0)
        fmt_row.addWidget(self._format_combo, 1)
        format_layout.addLayout(fmt_row)

        self._options_widget = QtWidgets.QWidget()
        self._options_layout = QtWidgets.QHBoxLayout(self._options_widget)
        self._options_layout.setContentsMargins(0, 0, 0, 0)
        format_layout.addWidget(self._options_widget)

        self._format_combo.currentIndexChanged.connect(self._update_format_options)
        self._update_format_options(0)

        left_layout.addWidget(format_group)

        res_group = QtWidgets.QGroupBox(t("qtool.imgconvert.output_resolution"))
        res_layout = QtWidgets.QVBoxLayout(res_group)
        res_layout.setContentsMargins(8, 8, 8, 8)

        res_row = QtWidgets.QHBoxLayout()
        res_row.addWidget(QtWidgets.QLabel(t("qtool.imgconvert.width") + ":"))
        self._res_width = QtWidgets.QLineEdit()
        self._res_width.setPlaceholderText(t("qtool.imgconvert.unlimited"))
        self._res_width.setMaximumWidth(70)
        self._res_width.setValidator(QtGui.QIntValidator(1, 65536, self))
        res_row.addWidget(self._res_width)

        res_row.addSpacing(8)
        res_row.addWidget(QtWidgets.QLabel(t("qtool.imgconvert.height") + ":"))
        self._res_height = QtWidgets.QLineEdit()
        self._res_height.setPlaceholderText(t("qtool.imgconvert.unlimited"))
        self._res_height.setMaximumWidth(70)
        self._res_height.setValidator(QtGui.QIntValidator(1, 65536, self))
        res_row.addWidget(self._res_height)

        res_row.addStretch()
        res_layout.addLayout(res_row)

        self._res_keep_aspect = QtWidgets.QCheckBox(t("qtool.imgconvert.keep_aspect_ratio"))
        self._res_keep_aspect.setChecked(True)
        res_layout.addWidget(self._res_keep_aspect)

        left_layout.addWidget(res_group)

        hierarchy_group = QtWidgets.QGroupBox(t("qtool.imgconvert.output_structure"))
        hierarchy_layout = QtWidgets.QVBoxLayout(hierarchy_group)
        hierarchy_layout.setContentsMargins(8, 8, 8, 8)

        self._keep_hierarchy = QtWidgets.QCheckBox(t("qtool.imgconvert.keep_hierarchy"))
        self._keep_hierarchy.setChecked(True)
        self._keep_hierarchy.setToolTip(t("qtool.imgconvert.keep_hierarchy_tip"))
        hierarchy_layout.addWidget(self._keep_hierarchy)

        left_layout.addWidget(hierarchy_group)

        left_layout.addStretch()
        main_splitter.addWidget(left_widget)

        right_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        log_group = QtWidgets.QGroupBox(t("qtool.imgconvert.convert_log"))
        log_layout = QtWidgets.QVBoxLayout(log_group)
        log_layout.setContentsMargins(8, 8, 8, 8)

        self._log_text = QtWidgets.QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet("background: #1a1a1a; color: #a0a0a0; font-family: Consolas;")
        log_layout.addWidget(self._log_text, 1)

        self._progress = QtWidgets.QProgressBar()
        self._progress.setVisible(False)
        log_layout.addWidget(self._progress)

        self._status_label = QtWidgets.QLabel(t("qtool.imgconvert.ready"))
        self._status_label.setStyleSheet("color: #808080; font-size: 12px;")
        log_layout.addWidget(self._status_label)

        right_layout.addWidget(log_group)
        main_splitter.addWidget(right_widget)

        layout.addWidget(main_splitter)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch()

        help_btn = QtWidgets.QPushButton("?")
        help_btn.setFixedSize(34, 34)
        help_btn.setToolTip(t("qtool.imgconvert.help_tooltip"))
        help_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffa502; border: none;"
            "font-size: 18px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        help_btn.clicked.connect(self._on_help)
        btn_layout.addWidget(help_btn)

        self._file_count_label = QtWidgets.QLabel(t("qtool.imgconvert.selected_count", n=0))
        self._file_count_label.setStyleSheet("color: #909090;")
        btn_layout.addWidget(self._file_count_label)

        self._cancel_btn = QtWidgets.QPushButton(t("common.cancel"))
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._cancel_btn)

        self._ok_btn = QtWidgets.QPushButton(t("qtool.imgconvert.start_convert"))
        self._ok_btn.setObjectName("okBtn")
        self._ok_btn.clicked.connect(self._convert)
        btn_layout.addWidget(self._ok_btn)

        layout.addLayout(btn_layout)

        self._input_files = []
        self._input_folder = ""
        self._is_converting = False
        self._is_cancelled = False

    def _on_cancel(self):
        if self._is_converting:
            self._is_cancelled = True
            self._cancel_btn.setEnabled(False)
            self._status_label.setText(t("qtool.imgconvert.aborting"))
        else:
            self.close()

    def _on_help(self):
        import webbrowser
        import os
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = _help_path(os.path.join(plugin_root, "Assets", "help", "图像格式转换工具", "help.html"))
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            print("[图像格式转换工具] 帮助文件未找到:", help_path)

    def _on_mode_changed(self):
        if self._mode_files.isChecked():
            self._input_path.setPlaceholderText(t("qtool.imgconvert.ph_select_images"))
        else:
            self._input_path.setPlaceholderText(t("qtool.imgconvert.ph_select_folder"))
        self._input_files = []
        self._input_folder = ""
        self._input_path.clear()
        self._file_count_label.setText(t("qtool.imgconvert.selected_count", n=0))

    def _browse_input(self):
        if self._mode_files.isChecked():
            self._browse_files()
        else:
            self._browse_folder()

    def _browse_files(self):
        from PySide6 import QtWidgets as _QW
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, t("qtool.imgconvert.select_images_title"),
            "",
            t("qtool.imgconvert.filter_all_images") + " (*.hdr *.HDR *.exr *.EXR *.png *.PNG *.jpg *.JPG *.jpeg *.JPEG *.tif *.TIF *.tiff *.TIFF *.bmp *.BMP *.webp *.WEBP *.tga *.TGA);;HDR (*.hdr *.HDR);;EXR (*.exr *.EXR);;PNG (*.png *.PNG);;JPEG (*.jpg *.jpeg);;TIFF (*.tif *.tiff)"
        )
        if files:
            self._input_files = [(f, os.path.basename(f)) for f in files]
            self._input_folder = ""
            if len(files) == 1:
                self._input_path.setText(files[0])
            else:
                self._input_path.setText(t("qtool.imgconvert.files_selected", n=len(files)))
            self._file_count_label.setText(t("qtool.imgconvert.selected_count", n=len(files)))

    def _browse_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, t("qtool.imgconvert.select_folder_title"), "",
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._input_folder = folder
            self._input_path.setText(folder)
            input_formats = self._get_input_formats()
            files = find_image_files(folder, input_formats)
            self._input_files = files
            self._file_count_label.setText(t("qtool.imgconvert.found_files", n=len(files)))

    def _get_input_formats(self):
        fmt_text = self._input_formats_edit.text().strip()
        if not fmt_text:
            return None
        result = []
        for f in fmt_text.replace('，', ',').split(','):
            f = f.strip().strip('.').lower()
            if f:
                result.append(f'.{f}')
        return result if result else None

    def _browse_output_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, t("qtool.imgconvert.select_output_title"), "",
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._output_path.setText(folder)

    def _update_format_options(self, index):
        for w in self._options_layout.findChildren(QtWidgets.QWidget):
            w.deleteLater()

        fmt = self._format_combo.itemData(index)
        if not fmt:
            return

        if fmt in ('.jpg', '.jpeg'):
            self._options_layout.addWidget(QtWidgets.QLabel(t("qtool.imgconvert.quality") + ":"))
            self._jpeg_quality = QtWidgets.QSpinBox()
            self._jpeg_quality.setRange(10, 100)
            self._jpeg_quality.setValue(95)
            self._jpeg_quality.setSuffix("%")
            self._options_layout.addWidget(self._jpeg_quality)

        elif fmt == '.png':
            self._options_layout.addWidget(QtWidgets.QLabel(t("qtool.imgconvert.compression") + ":"))
            self._png_compression = QtWidgets.QSpinBox()
            self._png_compression.setRange(0, 9)
            self._png_compression.setValue(5)
            self._options_layout.addWidget(self._png_compression)

        elif fmt in ('.tif', '.tiff'):
            self._options_layout.addWidget(QtWidgets.QLabel(t("qtool.imgconvert.compression") + ":"))
            self._tiff_compression = QtWidgets.QComboBox()
            self._tiff_compression.addItems(["LZW", "NONE", "DEFLATE"])
            self._tiff_compression.setCurrentText("LZW")
            self._options_layout.addWidget(self._tiff_compression)

        self._options_layout.addStretch()

    def _log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._log_text.append(f"[{timestamp}] {msg}")

    def _convert_progress(self, current, total, filename):
        self._progress.setValue(int(current / total * 100))
        self._status_label.setText(t("qtool.imgconvert.processing", current=current, total=total, filename=filename))

    def _convert_finished(self, converted, failed, failed_files):
        self._is_converting = False
        self._ok_btn.setEnabled(True)
        self._progress.setVisible(False)

        self._log(t("qtool.imgconvert.convert_done_count", converted=converted, failed=failed))
        for file, error in failed_files:
            self._log(f"  ✗ {file}: {error}")

        self._status_label.setText(t("qtool.imgconvert.status_done", converted=converted, failed=failed))
        QtWidgets.QMessageBox.information(self, t("qtool.imgconvert.convert_done_title"),
                                          t("qtool.imgconvert.message_done", converted=converted, total=converted + failed))

    def _convert(self):
        if self._is_converting:
            return

        if self._mode_folder.isChecked() and self._input_folder:
            input_formats = self._get_input_formats()
            self._input_files = find_image_files(self._input_folder, input_formats)

        if not self._input_files:
            QtWidgets.QMessageBox.warning(self, t("qtool.imgconvert.warning"), t("qtool.imgconvert.select_input_hint"))
            return

        output_folder = self._output_path.text().strip()
        if not output_folder:
            if self._input_folder:
                output_folder = self._input_folder
            elif self._input_files:
                output_folder = os.path.dirname(self._input_files[0][0])
            else:
                output_folder = ""

        if not output_folder:
            QtWidgets.QMessageBox.warning(self, t("qtool.imgconvert.warning"), t("qtool.imgconvert.no_output_hint"))
            return

        fmt = self._format_combo.itemData(self._format_combo.currentIndex())
        format_options = {}
        if hasattr(self, '_jpeg_quality'):
            format_options['jpeg_quality'] = self._jpeg_quality.value()
        if hasattr(self, '_png_compression'):
            format_options['png_compression'] = self._png_compression.value()
        if hasattr(self, '_tiff_compression'):
            format_options['tiff_compression'] = self._tiff_compression.currentText()

        target_width = None
        target_height = None
        try:
            tw = self._res_width.text().strip()
            if tw:
                target_width = int(tw)
            th = self._res_height.text().strip()
            if th:
                target_height = int(th)
        except ValueError:
            pass
        keep_aspect = self._res_keep_aspect.isChecked()
        keep_hierarchy = self._keep_hierarchy.isChecked()

        self._is_converting = True
        self._is_cancelled = False
        self._cancel_btn.setText(t("qtool.imgconvert.abort"))
        self._cancel_btn.setEnabled(True)
        self._ok_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._log_text.clear()
        
        if self._input_folder:
            self._log(t("qtool.imgconvert.log_start_folder", folder=self._input_folder))
        self._log(t("qtool.imgconvert.log_output_to", folder=output_folder))
        self._log(t("qtool.imgconvert.log_convert_count", n=len(self._input_files), fmt=fmt))

        if target_width or target_height:
            res_parts = []
            if target_width:
                res_parts.append(t("qtool.imgconvert.res_width", v=target_width))
            if target_height:
                res_parts.append(t("qtool.imgconvert.res_height", v=target_height))
            ratio = t("qtool.imgconvert.keep_ratio") if keep_aspect else t("qtool.imgconvert.stretch")
            self._log(t("qtool.imgconvert.log_output_resolution", res='×'.join(res_parts), ratio=ratio))

        input_files = list(self._input_files)
        total = len(input_files)
        converted = 0
        failed = 0
        failed_files = []

        try:
            for i, (input_file, rel_path) in enumerate(input_files):
                input_fmt = os.path.splitext(input_file)[1].lower()
                rel_dir = os.path.dirname(rel_path)
                stem = os.path.splitext(os.path.basename(input_file))[0]

                safe_stem = sanitize_filename(stem)
                if safe_stem != stem:
                    new_src = os.path.join(os.path.dirname(input_file), f"{safe_stem}{input_fmt}")
                    os.rename(input_file, new_src)
                    self._log(t("qtool.imgconvert.log_renamed", old=f"{stem}{input_fmt}", new=f"{safe_stem}{input_fmt}"))
                    input_file = new_src

                output_subdir = os.path.join(output_folder, rel_dir) if keep_hierarchy else output_folder
                os.makedirs(output_subdir, exist_ok=True)
                output_path = os.path.join(output_subdir, f"{safe_stem}{fmt}")

                self._progress.setValue(int((i + 1) / total * 100))
                self._status_label.setText(t("qtool.imgconvert.processing", current=i + 1, total=total, filename=rel_path))
                self._log(t("qtool.imgconvert.log_processing", path=rel_path))
                if IN_MAYA:
                    try:
                        import maya.cmds as cmds
                        cmds.refresh()
                    except Exception:
                        pass
                QtWidgets.QApplication.processEvents()

                if self._is_cancelled:
                    self._log(t("qtool.imgconvert.log_user_aborted"))
                    break

                success, message = convert_image(
                    input_file, output_path, input_fmt, fmt, format_options,
                    target_width=target_width, target_height=target_height,
                    keep_aspect_ratio=keep_aspect
                )

                if success:
                    converted += 1
                else:
                    failed += 1
                    failed_files.append((rel_path, message))
        finally:
            self._is_converting = False
            self._is_cancelled = False
            self._cancel_btn.setText(t("common.cancel"))
            self._cancel_btn.setEnabled(True)
            self._ok_btn.setEnabled(True)
            self._progress.setVisible(False)
            if self._is_cancelled:
                self._log(t("qtool.imgconvert.aborted_count", converted=converted, skipped=len(input_files) - converted - failed))
                self._status_label.setText(t("qtool.imgconvert.status_aborted", converted=converted, failed=failed))
                QtWidgets.QMessageBox.information(self, t("qtool.imgconvert.aborted_title"),
                    t("qtool.imgconvert.message_aborted", converted=converted, failed=failed))
            else:
                self._log(t("qtool.imgconvert.convert_done_count", converted=converted, failed=failed))
                for file, error in failed_files:
                    self._log(f"  ✗ {file}: {error}")
                self._status_label.setText(t("qtool.imgconvert.status_done", converted=converted, failed=failed))
                QtWidgets.QMessageBox.information(self, t("qtool.imgconvert.convert_done_title"),
                    t("qtool.imgconvert.message_done", converted=converted, total=converted + failed))


def get_maya_window():
    if IN_MAYA:
        try:
            from maya import OpenMayaUI
            import shiboken6
            ptr = OpenMayaUI.MQtUtil.mainWindow()
            if ptr is not None:
                return shiboken6.wrapInstance(int(ptr), QtWidgets.QWidget)
        except:
            try:
                from maya import OpenMayaUI
                import shiboken2
                ptr = OpenMayaUI.MQtUtil.mainWindow()
                if ptr is not None:
                    return shiboken2.wrapInstance(int(ptr), QtWidgets.QWidget)
            except:
                pass
        try:
            for obj in QtWidgets.QApplication.topLevelWidgets():
                if isinstance(obj, QtWidgets.QMainWindow) and hasattr(obj, 'windowTitle'):
                    title = obj.windowTitle()
                    if 'Autodesk' in title or ('Maya' in title and '资产' not in title and 'MaterialLibrary' not in title):
                        return obj
        except:
            pass
    return None


def main():
    """主函数，支持在Maya中和系统层独立运行"""
    print("[格式转换] 启动...")

    if QtWidgets is None:
        print("[格式转换] 无法加载PySide模块")
        return

    # 创建QApplication
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication(sys.argv)
        app.setApplicationName(t("qtool.imgconvert.app_name"))
        app.setOrganizationName("SquirrelAssetManager")
        need_exec = True
    else:
        need_exec = False

    # 检查依赖
    ok, msg = check_dependencies()
    if not ok:
        print(f"[格式转换] 依赖缺失:\n{msg}")
        if not need_exec:
            QtWidgets.QMessageBox.warning(None, t("qtool.imgconvert.dep_missing_title"), msg)
        else:
            # 在系统层运行时，创建一个简单的错误提示窗口
            error_dialog = QtWidgets.QDialog()
            error_dialog.setWindowTitle(t("qtool.imgconvert.dep_missing_title"))
            error_layout = QtWidgets.QVBoxLayout(error_dialog)
            error_label = QtWidgets.QLabel(msg)
            error_label.setWordWrap(True)
            error_layout.addWidget(error_label)
            ok_btn = QtWidgets.QPushButton(t("common.ok"))
            ok_btn.clicked.connect(error_dialog.accept)
            error_layout.addWidget(ok_btn)
            error_dialog.exec()
        return

    # 创建对话框
    parent_window = get_maya_window()
    dialog = ImageFormatConverterDialog(parent=parent_window)

    # 设置窗口标志
    if IN_MAYA and parent_window:
        dialog.setWindowFlags(QtCore.Qt.Window |
                             QtCore.Qt.WindowTitleHint |
                             QtCore.Qt.WindowSystemMenuHint |
                             QtCore.Qt.WindowMinimizeButtonHint |
                             QtCore.Qt.WindowMaximizeButtonHint |
                             QtCore.Qt.WindowCloseButtonHint)
        dialog.setParent(parent_window, QtCore.Qt.Window)
    else:
        # 系统层独立运行时，使用更简洁的窗口标志
        dialog.setWindowFlags(QtCore.Qt.Window)

    # 显示对话框
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()

    # 运行事件循环
    if need_exec:
        try:
            sys.exit(app.exec())
        except Exception as e:
            print(f"[格式转换] 退出: {e}")


if __name__ == '__main__':
    main()
