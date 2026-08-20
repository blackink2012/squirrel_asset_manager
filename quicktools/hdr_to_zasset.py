#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
HDR环境贴图转资产工具
将.hdr/.exr环境贴图转换为.zasset资产格式

缩略图生成复用"图像格式转换工具.py"的convert_image（已验证在Maya中可用）
"""

import os
os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '1'

import re
import sys
import io
import struct
import uuid
import tempfile
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import maya.cmds as cmds
    import maya.mel as mel
    IN_MAYA = True
except ImportError:
    IN_MAYA = False

from core.zasset_io import ZassetIO
from core.zasset_builder import ZassetBuilder

HDR_EXTENSIONS = {'.hdr', '.exr'}

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
    """动态获取Qt模块，兼容PySide6和PySide2"""
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


def _get_export_header():
    """获取导出文件头部信息（匹配插件标准格式）"""
    software_info = "HDR Tool"
    renderer_info = "unknown"
    color_space_info = "ACEScg"
    if IN_MAYA:
        try:
            import maya.cmds as cmds
            ver = cmds.about(version=True)
            software_info = f"Maya {ver}"
            renderer_info = cmds.getAttr("defaultRenderGlobals.currentRenderer")
            if cmds.objExists("defaultColorMgtGlobals"):
                for attr_name in ["workingSpaceName", "defaultInputSpaceName", "renderingSpace"]:
                    try:
                        if cmds.attributeQuery(attr_name, node="defaultColorMgtGlobals", exists=True):
                            result = cmds.getAttr(f"defaultColorMgtGlobals.{attr_name}")
                            if result and isinstance(result, str) and result.strip():
                                color_space_info = result.strip()
                                break
                    except:
                        pass
        except:
            pass

    return {
        "version": "2.0",
        "software": software_info,
        "renderer": renderer_info,
        "color_space": color_space_info,
        "create_date": datetime.now().strftime("%Y-%m-%d")
    }


def parse_hdr_resolution(filepath):
    """解析HDR文件分辨率"""
    try:
        with open(filepath, 'rb') as f:
            head = f.read(1024)
            if head[:2] != b'#?':
                return None
            match = re.search(rb'-Y (\d+) \+X (\d+)', head)
            if match:
                height = int(match.group(1))
                width = int(match.group(2))
                return width, height
    except:
        pass
    return None


def parse_exr_resolution(filepath):
    """解析EXR文件分辨率"""
    try:
        with open(filepath, 'rb') as f:
            data = f.read(4096)
        if data[0:4] != b'\x76\x2f\x31\x01':
            return None
        marker = b'dataWindow\x00box2i\x00\x10\x00\x00\x00'
        idx = data.find(marker)
        if idx < 0:
            return None
        offset = idx + len(marker)
        xmin, ymin, xmax, ymax = struct.unpack_from('<iiii', data, offset)
        if 0 <= xmax - xmin <= 65536 and 0 <= ymax - ymin <= 65536:
            return xmax - xmin + 1, ymax - ymin + 1
    except:
        pass
    return None


def get_hdr_info(filepath):
    """获取HDR/EXR文件信息"""
    ext = os.path.splitext(filepath)[1].lower()
    info = {
        'path': filepath,
        'filename': os.path.basename(filepath),
        'format': ext.lstrip('.'),
        'size_bytes': os.path.getsize(filepath)
    }

    if ext == '.hdr':
        res = parse_hdr_resolution(filepath)
    else:
        res = parse_exr_resolution(filepath)

    if res:
        info['width'], info['height'] = res
        info['resolution'] = f"{res[0]}x{res[1]}"
    else:
        info['width'] = 0
        info['height'] = 0
        info['resolution'] = "unknown"

    return info


def scan_hdr_files(folder_path):
    """扫描文件夹中的HDR/EXR文件（仅当前目录，不递归）"""
    results = []
    if not os.path.exists(folder_path):
        return results

    for filename in sorted(os.listdir(folder_path)):
        ext = os.path.splitext(filename)[1].lower()
        if ext in HDR_EXTENSIONS:
            filepath = os.path.join(folder_path, filename)
            info = get_hdr_info(filepath)
            results.append(info)

    return results


def find_hdr_files(root_folder, input_formats=None):
    """递归查找文件夹下的HDR/EXR文件，带格式过滤

    Args:
        root_folder: 根文件夹路径
        input_formats: 要过滤的格式列表，None表示.hdr和.exr，可指定如['.hdr']或['.exr']

    Returns:
        list[(file_path, relative_path)]: 文件绝对路径和相对路径元组列表
    """
    if input_formats:
        target_exts = set()
        for fmt in input_formats:
            fmt_clean = fmt.strip().lower()
            if not fmt_clean.startswith('.'):
                fmt_clean = '.' + fmt_clean
            if fmt_clean in HDR_EXTENSIONS:
                target_exts.add(fmt_clean)
    else:
        target_exts = HDR_EXTENSIONS

    result = []
    for dirpath, dirnames, filenames in os.walk(root_folder):
        for filename in sorted(filenames):
            ext = os.path.splitext(filename)[1].lower()
            if ext in target_exts:
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, root_folder)
                result.append((full_path, rel_path))
    return result


def _get_converter_module():
    """获取图像格式转换工具模块"""
    converter_path = os.path.join(os.path.dirname(__file__), '图像格式转换工具.py')
    import importlib.util
    spec = importlib.util.spec_from_file_location("_imgconv", converter_path)
    if spec is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return mod


def _check_thumb_deps():
    """检查缩略图生成依赖，返回 (ok, message)"""
    converter = _get_converter_module()
    if converter is None:
        return False, t("qtool.hdr.deps_converter_missing")

    try:
        import cv2
        import numpy as np
    except ImportError:
        return False, t("qtool.hdr.deps_cv2_missing")

    try:
        from PIL import Image
    except ImportError:
        return False, t("qtool.hdr.deps_pillow_missing")

    return True, t("common.ready")


def _find_existing_thumbnail(hdr_filepath, thumb_exts=None):
    """在HDR同目录下搜索同名缩略图（.png/.jpg）

    Returns:
        str路径 或 None
    """
    if thumb_exts is None:
        thumb_exts = ['.png', '.jpg', '.jpeg']
    basename = os.path.splitext(os.path.basename(hdr_filepath))[0]
    srcdir = os.path.dirname(hdr_filepath)
    for ext in thumb_exts:
        ext = ext.strip().lower()
        if not ext.startswith('.'):
            ext = '.' + ext
        candidate = os.path.join(srcdir, basename + ext)
        if os.path.isfile(candidate):
            print(f"[HDR Tool] 发现已有缩略图: {candidate}")
            return candidate
    return None


def _load_thumb_image(image_path, max_size=512):
    """加载已有图片并缩放到max_size，返回PNG bytes"""
    from PIL import Image
    img = Image.open(image_path)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    return buffer.getvalue()


def generate_thumbnail_from_hdr(filepath, max_size=512, thumb_exts=None):
    """从HDR/EXR文件生成缩略图PNG数据

    优先级：
      1. 搜索同目录同名.png/.jpg → 直接缩放使用
      2. 调用图像格式转换工具 convert_image 生成

    Args:
        filepath: HDR/EXR文件路径
        max_size: 最大尺寸
        thumb_exts: 搜索的缩略图扩展名列表，None使用默认['.png','.jpg']
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in HDR_EXTENSIONS:
        print(f"[HDR Tool] 不支持的文件格式: {ext}")
        return None

    existing = _find_existing_thumbnail(filepath, thumb_exts)
    if existing:
        try:
            png_data = _load_thumb_image(existing, max_size)
            print(f"[HDR Tool] 使用已有缩略图: {os.path.basename(existing)} ({len(png_data)} bytes)")
            return png_data
        except Exception as e:
            print(f"[HDR Tool] 加载已有缩略图失败: {e}，回退到生成模式")

    converter = _get_converter_module()
    if converter is None:
        print("[HDR Tool] 无法加载图像格式转换工具模块")
        return None

    fd, tmp_png = tempfile.mkstemp(suffix='.png', prefix='thumb_')
    os.close(fd)

    try:
        success, msg = converter.convert_image(filepath, tmp_png, ext, '.png',
                                               {'png_compression': 0})
        if not success:
            print(f"[HDR Tool] convert_image失败: {msg}")
            return None

        png_data = _load_thumb_image(tmp_png, max_size)
        print(f"[HDR Tool] 缩略图生成成功 ({len(png_data)} bytes)")
        return png_data
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[HDR Tool] 缩略图生成异常: {e}")
        return None
    finally:
        try:
            os.remove(tmp_png)
        except Exception:
            pass


def export_zasset(hdr_info, output_folder, asset_name=None, generate_thumb=True,
                  thumb_exts=None):
    """将HDR文件导出为.zasset格式

    Args:
        hdr_info: HDR文件信息dict（来自get_hdr_info）
        output_folder: 输出文件夹路径
        asset_name: 资产名称，默认使用文件名
        generate_thumb: 是否生成缩略图
        thumb_exts: 搜索已有缩略图的扩展名列表，如['.png','.jpg']
    """
    import tempfile, shutil

    if asset_name is None:
        asset_name = os.path.splitext(hdr_info['filename'])[0]

    header = _get_export_header()

    ext = hdr_info['format']
    meta = {
        'id': str(uuid.uuid4()),
        'version': header['version'],
        'software': header['software'],
        'renderer': header['renderer'],
        'color_space': header['color_space'],
        'create_date': header['create_date'],
        'name': asset_name,
        'name_cn': asset_name,
        'asset_type': 'hdr',
        'node_type': 'environmentMap',
        'category': 'environment',
        'tags': ['hdr', 'environment', ext],
        'thumbnail_path': '',
        'resolution': hdr_info.get('resolution', 'unknown'),
        'width': hdr_info.get('width', 0),
        'height': hdr_info.get('height', 0),
        'formats': [ext],
        'properties': {
            'environment': {
                'type': 'texture',
                'path': hdr_info['filename'],
                'format': ext,
                'resolution': hdr_info.get('resolution', 'unknown')
            }
        }
    }

    tex_filename = hdr_info['filename']
    files_built = {f"textures/{tex_filename}": hdr_info['path']}

    thumb_data = generate_thumbnail_from_hdr(hdr_info['path'], thumb_exts=thumb_exts) if generate_thumb else None
    tmp_dir = None
    if thumb_data:
        tmp_dir = tempfile.mkdtemp(prefix="hdr_thumb_")
        thumb_path = os.path.join(tmp_dir, "thumb.sicon")
        with open(thumb_path, 'wb') as f:
            f.write(thumb_data)
        files_built["thumb.sicon"] = thumb_path
        meta['thumbnail_path'] = 'thumb.sicon'
    elif generate_thumb:
        print(f"[HDR Tool] 缩略图未生成: {hdr_info['filename']}")
        import sys as _sys
        if 'cv2' in _sys.modules and 'imageio' not in _sys.modules:
            print("[HDR Tool] 提示: Maya中可能未安装imageio")
            print("[HDR Tool] 解决方案1 - 在系统终端执行（推荐）：")
            print(f"[HDR Tool]   \"{_sys.executable}\" -m pip install imageio")
            print("[HDR Tool] 解决方案2 - 在Maya脚本编辑器中执行（复制以下代码）：")
            print("[HDR Tool]   import subprocess")
            print("[HDR Tool]   import sys")
            print("[HDR Tool]   subprocess.run([sys.executable, '-m', 'pip', 'install', 'imageio'])")

    asset_path = os.path.join(output_folder, asset_name + '.zasset')

    try:
        success = ZassetBuilder.build(asset_path, files_built, meta)
        if success:
            return asset_path, None
        return None, "build failed"
    except Exception as e:
        return None, str(e)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _get_import_category_path():
    """获取当前资产库分类文件夹路径（实时查询主窗口选中分类）"""
    try:
        import squirrel_asset_manager as _sam
        mw = getattr(_sam, 'main_window', None)
        if mw is None:
            return ''
        cat_tree = getattr(mw, '_get_active_category_tree', lambda: None)()
        cur_cat = cat_tree.get_active_category() if cat_tree else "custom"
        root_lib = cat_tree.get_active_root_lib() if cat_tree else "materials"
        if cur_cat == "all" or not cur_cat:
            cur_cat = "custom"
        lib = mw._active_mgr.get_library_path() if getattr(mw, '_active_mgr', None) else ""
        if lib and cur_cat:
            folder = mw._find_category_folder(cur_cat, root_lib)
            if folder and os.path.isdir(folder):
                return folder
            return os.path.join(lib, root_lib, cur_cat)
        return ''
    except Exception:
        import traceback
        traceback.print_exc()
        return ''


def _copy_zassets_to_category(src_folder, category_path):
    """将src_folder及其子目录下所有.zasset文件复制到category_path（扁平化）"""
    import shutil
    if not category_path or not os.path.isdir(src_folder):
        return 0
    os.makedirs(category_path, exist_ok=True)
    count = 0
    for dirpath, dirnames, filenames in os.walk(src_folder):
        for fn in filenames:
            if fn.lower().endswith('.zasset'):
                src = os.path.join(dirpath, fn)
                dst = os.path.join(category_path, fn)
                try:
                    shutil.copy2(src, dst)
                    count += 1
                    print(f"[HDR Tool] 导入: {fn} -> {category_path}")
                except Exception as e:
                    print(f"[HDR Tool] 导入失败: {fn}: {e}")
    return count


class HDRToZAssetDialog(QtWidgets.QDialog):
    """HDR环境贴图转资产工具UI"""

    def __init__(self, parent=None):
        super(HDRToZAssetDialog, self).__init__(parent)
        self.setWindowTitle(t("qtool.hdr.window_title"))
        self.setMinimumSize(850, 620)
        self.setStyleSheet("""
            QDialog { background-color: #2a2a2a; }
            QLabel { color: #d0d0d0; }
            QLineEdit { background: #333; border: 1px solid #4a4a4a; border-radius: 4px;
                        padding: 5px 8px; color: #e0e0e0; }
            QPushButton { background: #3a3a3a; color: #d0d0d0; border: none;
                          padding: 7px 14px; border-radius: 4px; }
            QPushButton:hover { background: #4a4a4a; }
            QPushButton:pressed { background: #2a2a2a; }
            QPushButton#okBtn { background: #5294e2; color: white; }
            QPushButton#okBtn:hover { background: #6ab0ff; }
            QTreeWidget { background: #2a2a2a; border: 1px solid #3a3a3a; color: #d0d0d0; }
            QTreeWidget::item { padding: 4px; }
            QTreeWidget::item:hover { background: #333; }
            QTreeWidget::item:selected { background: #2d4a6f; }
            QProgressBar { background: #333; border: 1px solid #4a4a4a; border-radius: 4px; }
            QProgressBar::chunk { background: #5294e2; }
            QGroupBox { border: 1px solid #4a4a4a; border-radius: 4px; margin-top: 24px; padding-top: 14px; padding-bottom: 8px; padding-left: 8px; padding-right: 8px; }
            QGroupBox::title { color: #909090; font-weight: bold; subcontrol-origin: margin; subcontrol-position: top left; padding-left: 6px; padding-right: 6px; padding-top: 0px; padding-bottom: 0px; background: #2a2a2a; margin-top: -2px; }
            QCheckBox { color: #d0d0d0; }
            QRadioButton { color: #d0d0d0; }
        """)

        self._hdr_files = []        # 普通模式：list[info_dict]
        self._batch_results = []     # 批量模式：list[(asset_name, info)]
        self._recursive_files = []   # 递归模式：list[(info, rel_path)]
        self._scan_mode = 0          # 0=普通, 1=批量(子文件夹), 2=递归
        self._input_files = []       # 文件模式：list[(full_path, basename)]
        self._input_folder = ""      # 文件夹模式：文件夹路径
        self.output_folder = ""

        self._setup_ui()

    def _on_cancel(self):
        if getattr(self, '_is_converting', False):
            self._is_cancelled = True
            self._cancel_btn.setEnabled(False)
            self._status_label.setText(t("qtool.common.aborting"))
            try:
                import maya.cmds as _cmds
                _cmds.refresh()
            except Exception:
                pass
        else:
            self.close()

    def _on_help(self):
        import webbrowser
        import os
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = _help_path(os.path.join(plugin_root, "Assets", "help", "hdr_to_zasset", "help.html"))
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            print("[HDR Tool] 帮助文件未找到:", help_path)

    def _setup_ui(self):
        """设置UI布局"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        main_splitter.setStyleSheet("QSplitter::handle { background: #3a3a3a; }")
        main_splitter.setSizes([450, 400])

        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        mode_group = QtWidgets.QGroupBox(t("qtool.hdr.input_mode_group"))
        mode_layout = QtWidgets.QHBoxLayout(mode_group)
        mode_layout.setContentsMargins(8, 8, 8, 8)

        self._mode_files = QtWidgets.QRadioButton(t("qtool.hdr.mode_files"))
        self._mode_files.setChecked(True)
        self._mode_files.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_files)

        self._mode_folder = QtWidgets.QRadioButton(t("qtool.hdr.mode_folder"))
        self._mode_folder.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_folder)

        left_layout.addWidget(mode_group)

        input_group = QtWidgets.QGroupBox(t("qtool.hdr.input_group"))
        input_layout = QtWidgets.QHBoxLayout(input_group)
        input_layout.setContentsMargins(8, 8, 8, 8)

        self._input_path = QtWidgets.QLineEdit()
        self._input_path.setPlaceholderText(t("qtool.hdr.input_placeholder"))
        input_layout.addWidget(self._input_path, 1)

        browse_btn = QtWidgets.QPushButton(t("common.browse"))
        browse_btn.clicked.connect(self._browse_input)
        input_layout.addWidget(browse_btn)

        left_layout.addWidget(input_group)

        output_group = QtWidgets.QGroupBox(t("qtool.common.output_group"))
        output_layout = QtWidgets.QHBoxLayout(output_group)
        output_layout.setContentsMargins(8, 8, 8, 8)

        self._output_path = QtWidgets.QLineEdit()
        self._output_path.setPlaceholderText(t("qtool.common.output_placeholder"))
        output_layout.addWidget(self._output_path, 1)

        output_browse_btn = QtWidgets.QPushButton(t("common.browse"))
        output_browse_btn.clicked.connect(self._browse_output_folder)
        output_layout.addWidget(output_browse_btn)

        left_layout.addWidget(output_group)

        options_group = QtWidgets.QGroupBox(t("qtool.hdr.options_group"))
        options_layout = QtWidgets.QVBoxLayout(options_group)
        options_layout.setContentsMargins(8, 8, 8, 8)

        self._generate_thumb = QtWidgets.QCheckBox(t("qtool.hdr.generate_thumb"))
        self._generate_thumb.setChecked(True)
        self._generate_thumb.setToolTip(t("qtool.hdr.generate_thumb_tooltip"))
        options_layout.addWidget(self._generate_thumb)

        thumb_search_layout = QtWidgets.QHBoxLayout()
        self._search_existing_thumb = QtWidgets.QCheckBox(t("qtool.hdr.search_existing_thumb"))
        self._search_existing_thumb.setChecked(True)
        self._search_existing_thumb.setToolTip(t("qtool.hdr.search_existing_thumb_tooltip"))
        thumb_search_layout.addWidget(self._search_existing_thumb)

        self._thumb_exts_input = QtWidgets.QLineEdit()
        self._thumb_exts_input.setMaximumWidth(120)
        self._thumb_exts_input.setPlaceholderText(t("qtool.hdr.thumb_exts_placeholder"))
        self._thumb_exts_input.setToolTip(t("qtool.hdr.thumb_exts_tooltip"))
        thumb_search_layout.addWidget(self._thumb_exts_input)
        thumb_search_layout.addStretch()
        options_layout.addLayout(thumb_search_layout)

        self._recursive_mode = QtWidgets.QCheckBox(t("qtool.hdr.recursive_mode"))
        self._recursive_mode.setChecked(True)
        self._recursive_mode.setToolTip(t("qtool.hdr.recursive_mode_tooltip"))
        options_layout.addWidget(self._recursive_mode)

        self._keep_hierarchy = QtWidgets.QCheckBox(t("qtool.hdr.keep_hierarchy"))
        self._keep_hierarchy.setChecked(True)
        self._keep_hierarchy.setToolTip(t("qtool.hdr.keep_hierarchy_tooltip"))
        options_layout.addWidget(self._keep_hierarchy)

        self._import_to_category = QtWidgets.QCheckBox(t("qtool.hdr.import_to_category"))
        self._import_to_category.setChecked(False)
        self._import_to_category.setToolTip(t("qtool.hdr.import_to_category_tooltip"))
        options_layout.addWidget(self._import_to_category)

        filter_layout = QtWidgets.QHBoxLayout()
        filter_layout.addWidget(QtWidgets.QLabel(t("qtool.hdr.format_filter")))
        self._filter_input = QtWidgets.QLineEdit()
        self._filter_input.setPlaceholderText(t("qtool.hdr.format_filter_placeholder"))
        self._filter_input.setStyleSheet("font-size: 13px;")
        filter_layout.addWidget(self._filter_input, 1)
        options_layout.addLayout(filter_layout)

        

        left_layout.addWidget(options_group)

        left_layout.addStretch()
        main_splitter.addWidget(left_widget)

        right_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        preview_group = QtWidgets.QGroupBox(t("qtool.hdr.preview_group"))
        preview_layout = QtWidgets.QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(8, 8, 8, 8)

        self._file_tree = QtWidgets.QTreeWidget()
        self._file_tree.setHeaderLabel(t("qtool.hdr.file_tree_header"))
        self._file_tree.setMinimumHeight(200)
        preview_layout.addWidget(self._file_tree, 1)

        self._scan_btn = QtWidgets.QPushButton(t("qtool.hdr.scan"))
        self._scan_btn.clicked.connect(self._scan_files)
        preview_layout.addWidget(self._scan_btn)

        right_layout.addWidget(preview_group, 1)

        self._progress = QtWidgets.QProgressBar()
        self._progress.setVisible(False)
        right_layout.addWidget(self._progress)

        self._status_label = QtWidgets.QLabel(t("common.ready"))
        self._status_label.setStyleSheet("color: #808080; font-size: 13px;")
        right_layout.addWidget(self._status_label)
        main_splitter.addWidget(right_widget)

        layout.addWidget(main_splitter)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch()

        help_btn = QtWidgets.QPushButton("?")
        help_btn.setFixedSize(34, 34)
        help_btn.setToolTip(t("btn.help.tooltip"))
        help_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffa502; border: none;"
            "font-size: 18px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        help_btn.clicked.connect(self._on_help)
        btn_layout.addWidget(help_btn)

        self._cancel_btn = QtWidgets.QPushButton(t("common.cancel"))
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._cancel_btn)

        self._ok_btn = QtWidgets.QPushButton(t("qtool.common.convert"))
        self._ok_btn.setObjectName("okBtn")
        self._ok_btn.setFixedWidth(180)
        self._ok_btn.clicked.connect(self._convert)
        btn_layout.addWidget(self._ok_btn)

        layout.addLayout(btn_layout)

    def _on_mode_changed(self):
        if not hasattr(self, '_input_path'):
            return
        is_files = self._mode_files.isChecked()
        if is_files:
            self._input_path.setPlaceholderText(t("qtool.hdr.input_placeholder"))
        else:
            self._input_path.setPlaceholderText(t("qtool.hdr.folder_placeholder"))
        self._input_files = []
        self._input_folder = ""
        self._hdr_files = []
        self._batch_results = []
        self._recursive_files = []
        self._input_path.clear()
        self._file_tree.clear()
        self._status_label.setText(t("common.ready"))
        if hasattr(self, '_recursive_mode'):
            self._recursive_mode.setEnabled(not is_files)
            self._recursive_mode.setChecked(False if is_files else True)
        if hasattr(self, '_keep_hierarchy'):
            self._keep_hierarchy.setEnabled(not is_files)
        if hasattr(self, '_filter_input'):
            self._filter_input.setEnabled(not is_files)
        if hasattr(self, '_scan_btn'):
            self._scan_btn.setEnabled(not is_files)

    def _browse_input(self):
        if self._mode_files.isChecked():
            self._browse_input_files()
        else:
            self._browse_input_folder()

    def _browse_input_files(self):
        from PySide6 import QtWidgets as _QW
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, t("qtool.hdr.dlg_input_files"),
            "",
            "HDR/EXR (*.hdr *.HDR *.exr *.EXR);;HDR (*.hdr *.HDR);;EXR (*.exr *.EXR);;" + t("qtool.hdr.all_files") + " (*.*)"
        )
        if files:
            self._input_files = [(f, os.path.basename(f)) for f in files]
            self._input_folder = ""
            self._hdr_files = [get_hdr_info(f) for f in files]
            self._batch_results = []
            self._recursive_files = []
            if len(files) == 1:
                self._input_path.setText(files[0])
            else:
                self._input_path.setText(t("qtool.hdr.files_selected", count=len(files)))
            self._status_label.setText(t("qtool.hdr.selected_files", count=len(files)))
            self._update_file_tree()

    def _browse_input_folder(self):
        """浏览输入文件夹"""
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, t("qtool.hdr.dlg_input_folder"), "",
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._input_folder = folder
            self._input_files = []
            self._input_path.setText(folder)

    def _browse_output_folder(self):
        """浏览输出文件夹"""
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, t("qtool.common.dlg_output"), "",
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._output_path.setText(folder)

    def _scan_files(self):
        """扫描HDR文件"""
        if self._mode_files.isChecked():
            if not self._hdr_files:
                QtWidgets.QMessageBox.warning(self, t("common.warning"), t("qtool.hdr.please_select_files"))
                return
            self._status_label.setText(t("qtool.hdr.selected_files", count=len(self._hdr_files)))
            return

        folder_path = self._input_path.text()
        if not folder_path or not os.path.exists(folder_path):
            QtWidgets.QMessageBox.warning(self, t("common.warning"), t("qtool.hdr.please_select_folder"))
            return

        self._status_label.setText(t("qtool.hdr.scanning"))
        QtWidgets.QApplication.processEvents()

        self._batch_results = []
        self._hdr_files = []
        self._recursive_files = []

        input_formats = None
        filter_text = self._filter_input.text().strip()
        if filter_text:
            input_formats = []
            for f in filter_text.replace('，', ',').split(','):
                f = f.strip().strip('.').lower()
                if f:
                    input_formats.append(f'.{f}')
            if not input_formats:
                input_formats = None

        if self._recursive_mode.isChecked():
            found = find_hdr_files(folder_path, input_formats)
            if found:
                self._recursive_files = []
                for file_path, rel_path in found:
                    info = get_hdr_info(file_path)
                    self._recursive_files.append((info, rel_path))
                self._status_label.setText(t("qtool.hdr.recursive_found", count=len(self._recursive_files)))
            else:
                self._status_label.setText(t("qtool.hdr.no_hdr_found"))
        else:
            self._hdr_files = scan_hdr_files(folder_path)
            if input_formats:
                self._hdr_files = [info for info in self._hdr_files
                                   if os.path.splitext(info['path'])[1].lower() in input_formats]
            if self._hdr_files:
                self._status_label.setText(t("qtool.hdr.found_count", count=len(self._hdr_files)))
            else:
                self._status_label.setText(t("qtool.hdr.no_hdr_found"))

        self._update_file_tree()

    def _update_file_tree(self):
        """更新文件树"""
        self._file_tree.clear()

        if self._recursive_files:
            folders = {}
            for info, rel_path in self._recursive_files:
                dir_name = os.path.dirname(rel_path)
                if dir_name not in folders:
                    folders[dir_name] = []
                folders[dir_name].append((info, rel_path))

            for folder_name in sorted(folders.keys()):
                if folder_name:
                    folder_item = QtWidgets.QTreeWidgetItem([folder_name])
                    folder_item.setForeground(0, QtGui.QColor("#5294e2"))
                    self._file_tree.addTopLevelItem(folder_item)
                    for info, rel_path in folders[folder_name]:
                        child = QtWidgets.QTreeWidgetItem([info['filename']])
                        child.setForeground(0, QtGui.QColor("#d0d0d0"))
                        res_item = QtWidgets.QTreeWidgetItem([t("qtool.hdr.resolution", res=info.get('resolution', 'unknown'))])
                        res_item.setForeground(0, QtGui.QColor("#909090"))
                        child.addChild(res_item)
                        folder_item.addChild(child)
                    folder_item.setExpanded(True)
                else:
                    for info, rel_path in folders[folder_name]:
                        item = QtWidgets.QTreeWidgetItem([info['filename']])
                        item.setForeground(0, QtGui.QColor("#5294e2"))
                        res_item = QtWidgets.QTreeWidgetItem([t("qtool.hdr.resolution", res=info.get('resolution', 'unknown'))])
                        res_item.setForeground(0, QtGui.QColor("#909090"))
                        item.addChild(res_item)
                        item.setExpanded(True)
                        self._file_tree.addTopLevelItem(item)
        elif self._batch_results:
            for asset_name, info in self._batch_results:
                item = QtWidgets.QTreeWidgetItem([asset_name])
                item.setForeground(0, QtGui.QColor("#5294e2"))
                child = QtWidgets.QTreeWidgetItem([info['filename']])
                child.setForeground(0, QtGui.QColor("#d0d0d0"))
                res_text = t("qtool.hdr.resolution", res=info.get('resolution', 'unknown'))
                res_item = QtWidgets.QTreeWidgetItem([res_text])
                res_item.setForeground(0, QtGui.QColor("#909090"))
                item.addChild(child)
                item.addChild(res_item)
                item.setExpanded(True)
                self._file_tree.addTopLevelItem(item)
        else:
            for info in self._hdr_files:
                item = QtWidgets.QTreeWidgetItem([info['filename']])
                item.setForeground(0, QtGui.QColor("#5294e2"))
                res_text = t("qtool.hdr.resolution", res=info.get('resolution', 'unknown'))
                res_item = QtWidgets.QTreeWidgetItem([res_text])
                res_item.setForeground(0, QtGui.QColor("#909090"))
                item.addChild(res_item)
                item.setExpanded(True)
                self._file_tree.addTopLevelItem(item)

    def _convert(self):
        """执行转换"""
        if getattr(self, '_is_converting', False):
            return

        has_data = bool(self._hdr_files) or bool(self._batch_results) or bool(self._recursive_files)
        if not has_data:
            QtWidgets.QMessageBox.warning(self, t("common.warning"), t("qtool.hdr.please_scan_first"))
            return

        if not self._output_path.text():
            if self._recursive_files or self._batch_results:
                pass
            elif self._hdr_files and self._hdr_files[0].get('path'):
                self._output_path.setText(os.path.dirname(self._hdr_files[0]['path']))

        if self._generate_thumb.isChecked():
            ok, dep_msg = _check_thumb_deps()
            if not ok:
                reply = QtWidgets.QMessageBox.question(
                    self, t("qtool.hdr.deps_missing"),
                    dep_msg + "\n\n" + t("qtool.hdr.continue_without_thumb"),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No
                )
                if reply == QtWidgets.QMessageBox.No:
                    return
                self._generate_thumb.setChecked(False)
                self._status_label.setText(t("qtool.hdr.thumb_skipped"))

        output_folder = self._output_path.text().strip()
        if not output_folder:
            if self._hdr_files and self._hdr_files[0].get('path'):
                output_folder = os.path.dirname(self._hdr_files[0]['path'])
            else:
                QtWidgets.QMessageBox.warning(self, t("common.warning"), t("qtool.hdr.no_output_folder"))
                return
        generate_thumb = self._generate_thumb.isChecked()
        keep_hierarchy = self._keep_hierarchy.isChecked()

        if self._recursive_files:
            items = [(info, rel_path) for info, rel_path in self._recursive_files]
            mode = 'recursive'
        elif self._batch_results:
            items = [(name, info) for name, info in self._batch_results]
            mode = 'batch'
        else:
            items = [(None, info) for info in self._hdr_files]
            mode = 'single'

        self._is_converting = True
        self._is_cancelled = False
        self._cancel_btn.setText(t("qtool.hdr.abort"))
        self._cancel_btn.setEnabled(True)
        self._ok_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setMaximum(len(items))
        self._progress.setValue(0)

        success_count = 0
        fail_count = 0
        errors = []

        try:
            for i, item in enumerate(items):
                if mode == 'recursive':
                    info, rel_path = item
                    display_name = rel_path
                    rel_dir = os.path.dirname(rel_path)
                    out_subdir = os.path.join(output_folder, rel_dir) if (rel_dir and keep_hierarchy) else output_folder
                    asset_name = os.path.splitext(os.path.basename(rel_path))[0]
                elif mode == 'batch':
                    asset_name, info = item
                    out_subdir = output_folder
                    display_name = asset_name
                else:
                    _, info = item
                    asset_name = os.path.splitext(info['filename'])[0]
                    out_subdir = output_folder
                    display_name = asset_name

                self._progress.setValue(i + 1)
                self._status_label.setText(t("qtool.hdr.processing", name=display_name))
                if IN_MAYA:
                    try:
                        import maya.cmds as _cmds
                        _cmds.refresh()
                    except Exception:
                        pass
                QtWidgets.QApplication.processEvents()

                if self._is_cancelled:
                    break

                try:
                    os.makedirs(out_subdir, exist_ok=True)
                    asset_path, error = export_zasset(
                        info, out_subdir,
                        asset_name=asset_name,
                        generate_thumb=generate_thumb
                    )
                    if asset_path:
                        success_count += 1
                    else:
                        errors.append(f"{display_name}: {error}")
                        fail_count += 1
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    errors.append(f"{display_name}: {e}")
                    fail_count += 1
        finally:
            self._is_converting = False
            self._cancel_btn.setText(t("common.cancel"))
            self._cancel_btn.setEnabled(True)
            self._progress.setVisible(False)
            self._ok_btn.setEnabled(True)
            self._scan_btn.setEnabled(True)

            if self._is_cancelled:
                self._status_label.setText(t("qtool.hdr.aborted", success=success_count, failed=fail_count))
                QtWidgets.QMessageBox.information(self, t("qtool.hdr.aborted_title"),
                    t("qtool.hdr.aborted_body", success=success_count, failed=fail_count))
            elif errors:
                error_msg = "\n".join(errors[:10])
                if len(errors) > 10:
                    error_msg += "\n" + t("qtool.hdr.and_more_errors", n=len(errors) - 10)
                QtWidgets.QMessageBox.warning(self, t("qtool.hdr.done_title"),
                    t("qtool.hdr.done_with_errors", success=success_count, failed=fail_count, detail=error_msg))
            else:
                QtWidgets.QMessageBox.information(self, t("qtool.hdr.done_title"),
                    t("qtool.hdr.done_body", count=success_count))

            self._status_label.setText(t("qtool.hdr.done_status", success=success_count, failed=fail_count))

            if getattr(self, '_import_to_category', None) and self._import_to_category.isChecked():
                print("[HDR Tool] 开始导入当前分类...")
                category_path = _get_import_category_path()
                print(f"[HDR Tool] 目标分类路径: {category_path}")
                if category_path and os.path.isdir(category_path):
                    imported = _copy_zassets_to_category(output_folder, category_path)
                    if imported:
                        self._status_label.setText(
                            t("qtool.hdr.done_imported", success=success_count, failed=fail_count, imported=imported))
                        print(f"[HDR Tool] 已导入 {imported} 个zasset到: {category_path}")
                    else:
                        print("[HDR Tool] 未找到zasset文件或导入失败")
                else:
                    print("[HDR Tool] 当前分类路径无效，跳过导入")
                    print("[HDR Tool] 提示: 从材质库主窗口运行此工具才能自动获取分类路径")


def get_maya_window():
    """获取Maya主窗口作为父窗口"""
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
    """主函数"""
    print("[HDR Tool] 启动...")

    if QtWidgets is None:
        print("[HDR Tool] 无法加载PySide模块")
        return

    print("[HDR Tool] PySide模块加载成功")

    app = QtWidgets.QApplication.instance()
    if not app:
        print("[HDR Tool] 创建新的QApplication")
        app = QtWidgets.QApplication(sys.argv)
        need_exec = True
    else:
        print("[HDR Tool] 使用现有的QApplication")
        need_exec = False

    parent_window = get_maya_window()
    print(f"[HDR Tool] 父窗口: {parent_window}")

    print("[HDR Tool] 创建对话框...")
    dialog = HDRToZAssetDialog(parent=parent_window)

    if IN_MAYA and parent_window:
        dialog.setWindowFlags(QtCore.Qt.Window |
                             QtCore.Qt.WindowTitleHint |
                             QtCore.Qt.WindowSystemMenuHint |
                             QtCore.Qt.WindowMinimizeButtonHint |
                             QtCore.Qt.WindowMaximizeButtonHint |
                             QtCore.Qt.WindowCloseButtonHint)
        dialog.setParent(parent_window, QtCore.Qt.Window)
    else:
        dialog.setWindowFlags(QtCore.Qt.Window |
                              QtCore.Qt.WindowTitleHint |
                              QtCore.Qt.WindowSystemMenuHint |
                              QtCore.Qt.WindowMinimizeButtonHint |
                              QtCore.Qt.WindowMaximizeButtonHint |
                              QtCore.Qt.WindowCloseButtonHint)

    print("[HDR Tool] 显示对话框...")
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    print("[HDR Tool] 对话框已显示")

    if need_exec:
        print("[HDR Tool] 进入事件循环...")
        app.exec()


if __name__ == '__main__':
    main()
