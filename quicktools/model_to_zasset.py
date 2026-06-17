#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
模型资产转zasset工具
将ma/mb/obj/usd/abc/fbx等模型文件转换为.zasset资产格式
"""

import os
import json
import re
import sys
import uuid
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

MODEL_EXTENSIONS = ['.ma', '.mb', '.obj', '.usd', '.usda', '.usdc', '.abc', '.fbx', '.ass']
TEXTURE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.tga', '.tif', '.tiff', '.exr', '.hdr', '.bmp', '.tx', '.dds']
DEFAULT_FORMAT_PRIORITY = ['ma', 'mb', 'usd', 'usda', 'usdc', 'abc', 'fbx', 'obj', 'ass']


def _get_export_header():
    software_info = "Model Tool"
    renderer_info = "unknown"
    color_space_info = "ACEScg"
    if IN_MAYA:
        try:
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


def load_config():
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Assets', 'preset', 'model_mapping.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def find_existing_thumbnail(asset_folder, asset_name, config):
    """在资产文件夹中查找已有缩略图
    Args:
        asset_folder: 资产根目录
        asset_name: 资产名（用于 {assetName} 变量替换）
        config: model_mapping.json 配置
    Returns:
        str: 缩略图完整路径，未找到返回 None
    """
    paths = config.get('thumbnail_search_paths', [])
    for pattern in paths:
        resolved = pattern.replace('{assetName}', asset_name)
        resolved = resolved.replace('\\', '/')
        full_path = os.path.join(asset_folder, resolved)
        if os.path.isfile(full_path):
            return full_path
    return None


def read_source_metadata(asset_folder, asset_name, config):
    """读取源元数据文件并按映射合并到 meta dict
    Args:
        asset_folder: 资产根目录
        asset_name: 资产名（用于 {assetName} 变量替换）
        config: model_mapping.json 配置
    Returns:
        dict: 映射后的元数据字段，未找到或失败返回空 dict
    """
    sources = config.get('metadata_sources', [])
    result = {}
    
    for source in sources:
        pattern = source.get('file_pattern', '')
        fmt = source.get('file_format', 'json')
        mapping = source.get('field_mapping', [])
        
        resolved = pattern.replace('{assetName}', asset_name)
        resolved = resolved.replace('\\', '/')
        full_path = os.path.join(asset_folder, resolved)
        
        if not os.path.isfile(full_path):
            continue
        
        try:
            if fmt == 'json':
                with open(full_path, 'r', encoding='utf-8') as f:
                    raw_data = json.load(f)
            else:
                with open(full_path, 'r', encoding='utf-8') as f:
                    raw_data = f.read()
            
            for field in mapping:
                source_key = field.get('source', '')
                target_key = field.get('target', source_key)
                processor = field.get('processor', 'none')
                
                raw_value = raw_data.get(source_key) if isinstance(raw_data, dict) else None
                if raw_value is None:
                    continue
                
                if processor == 'split_comma':
                    processed = [t.strip() for t in raw_value.split(',') if t.strip()]
                elif processor == 'first_line':
                    processed = raw_value.split('\n')[0].strip()
                else:
                    processed = raw_value
                
                result[target_key] = processed
        except:
            pass
    
    return result


def extract_texture_references_from_ma(ma_path):
    references = []
    try:
        with open(ma_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        patterns = [
            r'setAttr\s+"\.ftn"\s+-type\s+"string"\s+"([^"]+)"',
            r'setAttr\s+"\.fileTextureName"\s+-type\s+"string"\s+"([^"]+)"',
            r'setAttr\s+"\.texture"\s+-type\s+"string"\s+"([^"]+)"',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                orig_path = match.group(1).replace('\\', '/')
                references.append(orig_path)
    except:
        pass
    return references


def collect_dependency_files(asset_folder, asset_name, model_files):
    dependencies = {}
    references = set()
    
    for fmt, filepath in model_files.items():
        if fmt in ['ma', 'mb'] and os.path.isfile(filepath):
            refs = extract_texture_references_from_ma(filepath)
            references.update(refs)
    
    for ref in references:
        ref_name = os.path.basename(ref)
        ext = os.path.splitext(ref_name)[1].lower()
        if ext in TEXTURE_EXTENSIONS:
            search_paths = [
                os.path.join(asset_folder, ref_name),
                os.path.join(asset_folder, 'textures', ref_name),
                os.path.join(asset_folder, 'sourceimages', ref_name),
                os.path.join(asset_folder, f"{asset_name}_fileDependencies", ref_name),
            ]
            for search_path in search_paths:
                if os.path.isfile(search_path):
                    dependencies[f"textures/{ref_name}"] = search_path
                    break
    
    extra_patterns = [
        f"{asset_name}.*\\.mtl$",
        f"{asset_name}.*\\.mcm$",
        f"{asset_name}.*\\.zmetal$",
        f"{asset_name}.*\\.tx$",
    ]
    
    for filename in os.listdir(asset_folder):
        filepath = os.path.join(asset_folder, filename)
        if not os.path.isfile(filepath):
            continue
        
        for pattern in extra_patterns:
            if re.match(pattern, filename, re.IGNORECASE):
                dependencies[filename] = filepath
                break
    
    return dependencies


DEFAULT_SUBFOLDER_PATTERNS = [
    "ass", "textures", "sourceimages"
]


def collect_subfolder_contents(asset_folder, subfolder_patterns=None):
    """扫描子文件夹中的文件，按相对路径收集
    Args:
        asset_folder: 资产根目录
        subfolder_patterns: 子文件夹名称列表（支持 * 通配符），为 None 时使用默认值
    Returns:
        dict: {rel_path: disk_full_path} 相对路径以 asset_folder 为基准
    """
    import fnmatch
    
    result = {}
    if not asset_folder or not os.path.isdir(asset_folder):
        return result
    
    if subfolder_patterns is None:
        subfolder_patterns = DEFAULT_SUBFOLDER_PATTERNS
    
    try:
        all_subdirs = [d for d in os.listdir(asset_folder)
                       if os.path.isdir(os.path.join(asset_folder, d))]
    except PermissionError:
        return result
    
    matched_dirs = set()
    for pattern in subfolder_patterns:
        pattern = pattern.strip().replace('/', '').replace('\\', '')
        if not pattern:
            continue
        for d in all_subdirs:
            if fnmatch.fnmatch(d, pattern):
                matched_dirs.add(d)
    
    for dirname in sorted(matched_dirs):
        dirpath = os.path.join(asset_folder, dirname)
        for root, dirs, files in os.walk(dirpath):
            for f in files:
                filepath = os.path.join(root, f)
                rel_path = os.path.relpath(filepath, asset_folder)
                result[rel_path] = filepath
    
    return result


def scan_models(folder_path, recursive=False):
    """扫描文件夹中的模型文件
    Args:
        folder_path: 要扫描的文件夹
        recursive: 是否递归搜索子文件夹
    Returns:
        dict: {asset_key: {fmt: filepath}}
            asset_key 是标识资产的唯一键，非递归时为 basename，
            递归时为 relpath/basename（相对文件夹路径）
    """
    models = {}
    if not os.path.exists(folder_path):
        return models
    
    if recursive:
        for root, dirs, files in os.walk(folder_path):
            rel_dir = os.path.relpath(root, folder_path)
            if rel_dir == '.':
                rel_dir = ''
            for filename in files:
                filepath = os.path.join(root, filename)
                basename, ext = os.path.splitext(filename)
                ext_lower = ext.lower()
                if ext_lower[1:] not in DEFAULT_FORMAT_PRIORITY:
                    continue
                # 构建唯一键：包含相对路径防止同名冲突
                if rel_dir:
                    asset_key = f"{rel_dir}/{basename}"
                else:
                    asset_key = basename
                if asset_key not in models:
                    models[asset_key] = {}
                models[asset_key][ext_lower[1:]] = filepath
    else:
        for filename in os.listdir(folder_path):
            filepath = os.path.join(folder_path, filename)
            if not os.path.isfile(filepath):
                continue
            basename, ext = os.path.splitext(filename)
            ext_lower = ext.lower()
            if ext_lower[1:] not in DEFAULT_FORMAT_PRIORITY:
                continue
            if basename not in models:
                models[basename] = {}
            models[basename][ext_lower[1:]] = filepath
    
    return models


def export_zasset(asset_name, model_files, config, output_folder,
                  asset_folder=None, target_formats=None, subfolder_patterns=None):
    import tempfile, shutil
    
    header = _get_export_header()
    
    meta = {
        'id': str(uuid.uuid4()),
        'version': header['version'],
        'software': header['software'],
        'renderer': header['renderer'],
        'color_space': header['color_space'],
        'create_date': header['create_date'],
        'name': asset_name,
        'name_cn': asset_name,
        'node_type': 'transform',
        'asset_type': 'models',
        'category': guess_category(asset_name, config),
        'tags': ['model'],
        'thumbnail_path': ''
    }
    
    if asset_folder:
        source_meta = read_source_metadata(asset_folder, asset_name, config)
        for key, value in source_meta.items():
            if key == '_asset_type':
                continue
            if key in ('tags',) and isinstance(value, list):
                existing = meta.get('tags', [])
                meta['tags'] = list(dict.fromkeys(existing + value))
            elif key == 'description':
                meta['description'] = value
            elif key == 'source_url':
                meta['source_url'] = value
            elif key == 'author':
                meta['author'] = value
            else:
                meta[key] = value
    
    if target_formats is None:
        target_formats = DEFAULT_FORMAT_PRIORITY
    
    formats = set()
    files_built = {}
    
    for fmt in target_formats:
        if fmt in model_files:
            filepath = model_files[fmt]
            filename = os.path.basename(filepath)
            files_built[filename] = filepath
            formats.add(fmt)
    
    if asset_folder:
        dependencies = collect_dependency_files(asset_folder, asset_name, model_files)
        for zip_path, disk_path in dependencies.items():
            files_built[zip_path] = disk_path
            ext = os.path.splitext(zip_path)[1].lower().lstrip('.')
            if ext:
                formats.add(ext)
    
    if asset_folder:
        if subfolder_patterns is None:
            subfolder_patterns = DEFAULT_SUBFOLDER_PATTERNS
        if subfolder_patterns:
            subfolder_files = collect_subfolder_contents(asset_folder, subfolder_patterns)
            for rel_path, disk_path in subfolder_files.items():
                if rel_path not in files_built:
                    files_built[rel_path] = disk_path
                    ext = os.path.splitext(rel_path)[1].lower().lstrip('.')
                    if ext:
                        formats.add(ext)
    
    texture_map = {}
    for zip_path, disk_path in files_built.items():
        if zip_path.startswith('textures/'):
            for ref_path in extract_texture_references_from_ma(model_files.get('ma', '')):
                ref_name = os.path.basename(ref_path)
                if zip_path.endswith(ref_name):
                    texture_map[ref_path] = zip_path
                    break
    
    if texture_map:
        meta['texture_map'] = texture_map
    
    meta['formats'] = sorted(formats)
    
    thumb_source = None
    if asset_folder:
        thumb_source = find_existing_thumbnail(asset_folder, asset_name, config)
    
    if thumb_source:
        try:
            from PIL import Image
            with Image.open(thumb_source) as img:
                img.thumbnail((512, 512), Image.Resampling.LANCZOS)
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGBA')
                import io
                buffer = io.BytesIO()
                img.save(buffer, format='PNG')
                thumbnail_data = buffer.getvalue()
                
                tmp_dir = tempfile.mkdtemp(prefix="model_build_")
                thumb_path = os.path.join(tmp_dir, "thumb.sicon")
                with open(thumb_path, 'wb') as f:
                    f.write(thumbnail_data)
                files_built["thumb.sicon"] = thumb_path
                meta['thumbnail_path'] = 'thumb.sicon'
                
                asset_path = os.path.join(output_folder, asset_name + '.zasset')
                success = ZassetBuilder.build(asset_path, files_built, meta)
                
                shutil.rmtree(tmp_dir, ignore_errors=True)
                
                if success:
                    return asset_path, None
                return None, "build failed"
        except Exception as e:
            pass
    
    tmp_dir = tempfile.mkdtemp(prefix="model_build_")
    try:
        asset_path = os.path.join(output_folder, asset_name + '.zasset')
        success = ZassetBuilder.build(asset_path, files_built, meta)
        if success:
            return asset_path, None
        return None, "build failed"
    except Exception as e:
        return None, str(e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def guess_category(asset_name, config):
    category_mapping = config.get('category_mapping', {})
    lower_name = asset_name.lower()
    
    for category, keywords in category_mapping.items():
        for keyword in keywords:
            if keyword.lower() in lower_name:
                return 'models||' + category
    
    return 'models||AAAcustom'


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
            # 将分类内部路径分隔符 || 替换为系统路径分隔符
            safe_cat = cur_cat.replace('||', os.sep)
            # 避免 root_lib 重复（如 root_lib="textures"，safe_cat="textures\foliage"）
            if safe_cat.startswith(root_lib + os.sep):
                safe_cat = safe_cat[len(root_lib) + 1:]
            return os.path.join(lib, root_lib, safe_cat)
        return ''
    except Exception:
        import traceback
        traceback.print_exc()
        return ''


def _copy_zassets_to_category(src_folder, category_path):
    """将src_folder及其子目录下所有.zasset文件夹复制到category_path（扁平化）"""
    import shutil
    if not category_path or not os.path.isdir(src_folder):
        return 0
    os.makedirs(category_path, exist_ok=True)
    count = 0
    for dirpath, dirnames, filenames in os.walk(src_folder):
        for dn in list(dirnames):
            if dn.lower().endswith('.zasset'):
                src = os.path.join(dirpath, dn)
                dst = os.path.join(category_path, dn)
                try:
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                    count += 1
                    print(f"[Model Tool] 导入: {dn} -> {category_path}")
                except Exception as e:
                    print(f"[Model Tool] 导入失败: {dn}: {e}")
    return count


class ModelToZassetDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(ModelToZassetDialog, self).__init__(parent)
        self.setWindowTitle("模型资产转zasset")
        self.setWindowFlags(self.windowFlags() |
                            QtCore.Qt.WindowMinimizeButtonHint |
                            QtCore.Qt.WindowMaximizeButtonHint)
        self.setMinimumSize(1200, 800)
        self.setStyleSheet("""
            QDialog { background-color: #2a2a2a;  }
            QLabel { color: #d0d0d0;  }
            QLineEdit { background: #333; border: 1px solid #4a4a4a; border-radius: 4px; 
                        padding: 5px 8px; color: #e0e0e0;  }
            QPushButton { background: #3a3a3a; color: #d0d0d0; border: none; 
                          padding: 7px 14px; border-radius: 4px;  }
            QPushButton:hover { background: #4a4a4a; }
            QPushButton:pressed { background: #2a2a2a; }
            QPushButton#okBtn { background: #5294e2; color: white; }
            QPushButton#okBtn:hover { background: #6ab0ff; }
            QTreeWidget { background: #2a2a2a; border: 1px solid #3a3a3a; color: #d0d0d0;  }
            QTreeWidget::item { padding: 4px; }
            QTreeWidget::item:hover { background: #333; }
            QTreeWidget::item:selected { background: #2d4a6f; }
            QTreeWidget::branch:open:has-children { image: none; }
            QTreeWidget::branch:closed:has-children { image: none; }
            QProgressBar { background: #333; border: 1px solid #4a4a4a; border-radius: 4px; }
            QProgressBar::chunk { background: #5294e2; }
            QGroupBox { border: 1px solid #4a4a4a; border-radius: 4px; margin-top: 32px; padding-top: 8px; padding-bottom: 8px; padding-left: 8px; padding-right: 8px; }
            QGroupBox::title { color: #909090; font-weight: bold; subcontrol-origin: margin; subcontrol-position: top left; padding-left: 6px; padding-right: 6px; background: #2a2a2a; margin-top: -10px;  }
            QCheckBox { color: #d0d0d0;  }
            QComboBox { background: #333; border: 1px solid #4a4a4a; border-radius: 4px; 
                        padding: 3px; color: #d0d0d0;  }
            QComboBox QAbstractItemView { background: #333; border: 1px solid #4a4a4a;  }
            QScrollArea { border: none; background: #2a2a2a; }
            QScrollArea::viewport { background: #2a2a2a; }
            QWidget { background: #2a2a2a; }
        """)
        
        self._input_folder = ""
        self._output_folder = ""
        self._models = {}
        self._is_converting = False
        self._is_cancelled = False
        
        self._config = load_config()
        self._setup_ui()
    
    def _setup_ui(self):
        """设置UI布局（与PBR工具保持一致）"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        
        # 主布局：左右两列
        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        main_splitter.setStyleSheet("QSplitter::handle { background: #3a3a3a; }")
        main_splitter.setSizes([550, 400])
        
        # 左侧：配置区域（放入滚动区域）
        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left_widget)
        
        # 输入文件夹选择
        input_group = QtWidgets.QGroupBox("模型文件夹")
        input_layout = QtWidgets.QHBoxLayout(input_group)
        input_layout.setContentsMargins(8, 8, 8, 8)
        
        self._input_path = QtWidgets.QLineEdit()
        self._input_path.setPlaceholderText("选择包含模型文件的文件夹...")
        input_layout.addWidget(self._input_path, 1)
        
        browse_btn = QtWidgets.QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_input_folder)
        input_layout.addWidget(browse_btn)
        
        left_layout.addWidget(input_group)
        
        # 输出文件夹选择
        output_group = QtWidgets.QGroupBox("输出位置")
        output_layout = QtWidgets.QHBoxLayout(output_group)
        output_layout.setContentsMargins(8, 8, 8, 8)
        
        self._output_path = QtWidgets.QLineEdit()
        self._output_path.setPlaceholderText("选择资产输出文件夹...")
        output_layout.addWidget(self._output_path, 1)
        
        output_browse_btn = QtWidgets.QPushButton("浏览...")
        output_browse_btn.clicked.connect(self._browse_output_folder)
        output_layout.addWidget(output_browse_btn)
        
        left_layout.addWidget(output_group)
        
        # 转换选项
        options_group = QtWidgets.QGroupBox("转换选项")
        options_layout = QtWidgets.QVBoxLayout(options_group)
        options_layout.setContentsMargins(8, 8, 8, 8)
        
        self._target_format_label = QtWidgets.QLabel("目标格式 (逗号分隔，留空则打包全部):")
        self._target_format_label.setStyleSheet("color: #c0c0c0; font-size: 13px;")
        options_layout.addWidget(self._target_format_label)
        
        format_row = QtWidgets.QHBoxLayout()
        format_row.setSpacing(8)
        self._target_format_input = QtWidgets.QLineEdit()
        self._target_format_input.setPlaceholderText("例如: ma,usd,abc")
        format_row.addWidget(self._target_format_input, 1)
        options_layout.addLayout(format_row)
        
        self._include_subfolders = QtWidgets.QCheckBox("包含子文件夹")
        self._include_subfolders.setChecked(True)
        options_layout.addWidget(self._include_subfolders)
        
        subfolder_row = QtWidgets.QHBoxLayout()
        subfolder_row.setSpacing(8)
        subfolder_label = QtWidgets.QLabel("子文件夹:")
        subfolder_label.setStyleSheet("color: #909090; font-size: 12px;")
        subfolder_label.setFixedWidth(70)
        subfolder_row.addWidget(subfolder_label)
        self._subfolder_patterns = QtWidgets.QLineEdit()
        self._subfolder_patterns.setPlaceholderText("ass, textures, sourceimages")
        self._subfolder_patterns.setText("ass, textures, sourceimages")
        subfolder_row.addWidget(self._subfolder_patterns, 1)
        options_layout.addLayout(subfolder_row)
        
        self._recursive_scan = QtWidgets.QCheckBox("递归搜索子文件夹（扫描深层目录中的模型文件）")
        self._recursive_scan.setChecked(False)
        options_layout.addWidget(self._recursive_scan)
        
        self._include_dependencies = QtWidgets.QCheckBox("包含依赖文件（贴图、材质等）")
        self._include_dependencies.setChecked(True)
        options_layout.addWidget(self._include_dependencies)
        
        self._use_existing_metadata = QtWidgets.QCheckBox("使用现有元数据和缩略图")
        self._use_existing_metadata.setChecked(True)
        options_layout.addWidget(self._use_existing_metadata)
        
        self._import_to_category = QtWidgets.QCheckBox("转换后导入当前分类")
        self._import_to_category.setChecked(False)
        self._import_to_category.setToolTip("转换完成后将zasset文件夹拷贝到当前资产库分类文件夹")
        options_layout.addWidget(self._import_to_category)
        
        left_layout.addWidget(options_group)
        
        # ── 文件路由配置 ──
        routing_group = QtWidgets.QGroupBox("文件路由配置")
        routing_layout = QtWidgets.QVBoxLayout(routing_group)
        routing_layout.setContentsMargins(8, 8, 8, 8)
        routing_layout.setSpacing(4)
        
        self._routing_widgets = []
        self._routing_layout = QtWidgets.QVBoxLayout()
        self._routing_layout.setSpacing(4)
        routing_layout.addLayout(self._routing_layout)
        
        routing_btn_layout = QtWidgets.QHBoxLayout()
        routing_btn_layout.setSpacing(6)
        add_route_btn = QtWidgets.QPushButton("+ 添加路由")
        add_route_btn.setStyleSheet("font-size: 13px; padding: 5px 12px;")
        add_route_btn.clicked.connect(self._add_routing_row)
        routing_btn_layout.addWidget(add_route_btn)
        save_routing_btn = QtWidgets.QPushButton("保存路由")
        save_routing_btn.setObjectName("okBtn")
        save_routing_btn.clicked.connect(self._save_routing_config)
        routing_btn_layout.addWidget(save_routing_btn)
        routing_btn_layout.addStretch()
        routing_layout.addLayout(routing_btn_layout)
        
        left_layout.addWidget(routing_group)
        
        # 元数据源配置
        meta_group = QtWidgets.QGroupBox("元数据源配置")
        meta_layout = QtWidgets.QVBoxLayout(meta_group)
        meta_layout.setContentsMargins(8, 8, 8, 8)
        meta_layout.setSpacing(6)
        
        # ── 缩略图搜索路径 ──
        thumb_label = QtWidgets.QLabel("缩略图搜索路径:")
        thumb_label.setStyleSheet("color: #5294e2; font-weight: bold;")
        meta_layout.addWidget(thumb_label)
        
        self._thumb_paths_layout = QtWidgets.QVBoxLayout()
        self._thumb_paths_layout.setSpacing(3)
        
        thumb_scroll = QtWidgets.QScrollArea()
        thumb_scroll.setWidgetResizable(True)
        thumb_scroll.setMaximumHeight(150)
        thumb_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        thumb_container = QtWidgets.QWidget()
        thumb_container.setStyleSheet("background: transparent;")
        thumb_container.setLayout(self._thumb_paths_layout)
        thumb_scroll.setWidget(thumb_container)
        meta_layout.addWidget(thumb_scroll)
        
        thumb_add_btn = QtWidgets.QPushButton("+ 添加路径")
        thumb_add_btn.setStyleSheet("font-size: 13px; padding: 5px 12px;")
        thumb_add_btn.clicked.connect(lambda: self._add_thumb_path_row())
        meta_layout.addWidget(thumb_add_btn)
        
        # ── 分隔线 ──
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("color: #3a3a3a;")
        meta_layout.addWidget(sep)
        
        # ── 元数据源 ──
        source_label = QtWidgets.QLabel("元数据源:")
        source_label.setStyleSheet("color: #5294e2; font-weight: bold;")
        meta_layout.addWidget(source_label)
        
        self._meta_scroll = QtWidgets.QScrollArea()
        self._meta_scroll.setWidgetResizable(True)
        self._meta_scroll.setMinimumHeight(150)
        self._meta_scroll.setMaximumHeight(270)
        self._meta_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        self._meta_container = QtWidgets.QWidget()
        self._meta_container.setStyleSheet("background: transparent;")
        self._meta_container_layout = QtWidgets.QVBoxLayout(self._meta_container)
        self._meta_container_layout.setContentsMargins(0, 0, 0, 0)
        self._meta_container_layout.setSpacing(4)
        self._meta_scroll.setWidget(self._meta_container)
        meta_layout.addWidget(self._meta_scroll)
        
        meta_btn_layout = QtWidgets.QHBoxLayout()
        meta_btn_layout.setSpacing(6)
        add_source_btn = QtWidgets.QPushButton("+ 添加源")
        add_source_btn.setStyleSheet("font-size: 14px; padding: 6px 14px;")
        add_source_btn.clicked.connect(self._add_meta_source)
        meta_btn_layout.addWidget(add_source_btn)
        meta_btn_layout.addStretch()
        save_meta_btn = QtWidgets.QPushButton("保存配置")
        save_meta_btn.setObjectName("okBtn")
        save_meta_btn.clicked.connect(self._save_meta_config)
        meta_btn_layout.addWidget(save_meta_btn)
        meta_layout.addLayout(meta_btn_layout)
        
        left_layout.addWidget(meta_group)
        main_splitter.addWidget(left_scroll)
        
        # 右侧：信息显示区域
        right_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)
        
        # 模型预览
        preview_group = QtWidgets.QGroupBox("识别到的模型资产")
        preview_layout = QtWidgets.QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        
        preview_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        
        self._model_tree = QtWidgets.QTreeWidget()
        self._model_tree.setHeaderLabel("模型文件和格式")
        self._model_tree.setMinimumHeight(150)
        preview_splitter.addWidget(self._model_tree)
        
        self._preview_tree = QtWidgets.QTreeWidget()
        self._preview_tree.setHeaderLabels([".zasset 结构预览", "大小"])
        self._preview_tree.setMinimumHeight(120)
        preview_splitter.addWidget(self._preview_tree)
        
        preview_layout.addWidget(preview_splitter, 1)
        
        self._scan_btn = QtWidgets.QPushButton("扫描模型")
        self._scan_btn.clicked.connect(self._scan_models)
        preview_layout.addWidget(self._scan_btn)
        
        right_layout.addWidget(preview_group, 1)
        
        # 进度条
        self._progress = QtWidgets.QProgressBar()
        self._progress.setVisible(False)
        right_layout.addWidget(self._progress)
        
        # 状态栏
        self._status_label = QtWidgets.QLabel("就绪")
        self._status_label.setStyleSheet("color: #808080; font-size: 12px;")
        right_layout.addWidget(self._status_label)
        main_splitter.addWidget(right_widget)
        
        layout.addWidget(main_splitter)
        
        # 按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch()
        
        help_btn = QtWidgets.QPushButton("?")
        help_btn.setFixedSize(34, 34)
        help_btn.setToolTip("使用帮助")
        help_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffa502; border: none;"
            "font-size: 18px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        help_btn.clicked.connect(self._on_help)
        btn_layout.addWidget(help_btn)
        
        self._cancel_btn = QtWidgets.QPushButton("取消")
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._cancel_btn)
        
        self._ok_btn = QtWidgets.QPushButton("转换")
        self._ok_btn.setObjectName("okBtn")
        self._ok_btn.setFixedWidth(180)
        self._ok_btn.clicked.connect(self._convert)
        btn_layout.addWidget(self._ok_btn)
        
        layout.addLayout(btn_layout)
        
        self._rebuild_routing_ui()
        self._rebuild_meta_ui()
        
        self._include_subfolders.toggled.connect(self._update_preview)
        self._subfolder_patterns.editingFinished.connect(self._update_preview)
        self._recursive_scan.toggled.connect(self._scan_models)
    
    def _browse_input_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择模型文件夹", "",
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._input_path.setText(folder)
            self._input_folder = folder
    
    def _browse_output_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择输出文件夹", "",
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._output_path.setText(folder)
            self._output_folder = folder
    
    def _scan_models(self):
        """扫描模型文件（按钮回调）"""
        if not self._input_folder:
            folder = self._input_path.text().strip()
            if not folder:
                QtWidgets.QMessageBox.warning(self, "提示", "请先选择模型文件夹")
                return
            self._input_folder = folder
        
        self._model_tree.clear()
        self._preview_tree.clear()
        self._models = scan_models(self._input_folder, self._recursive_scan.isChecked())
        
        if not self._models:
            self._status_label.setText("未找到支持的模型文件")
            return
        
        root = QtWidgets.QTreeWidgetItem([f"模型资产 ({len(self._models)} 个)"])
        self._model_tree.addTopLevelItem(root)
        
        for asset_key, formats in self._models.items():
            format_list = ", ".join(sorted(formats.keys()))
            # 从 key 中提取 basename 作为实际资产名
            basename = os.path.basename(asset_key)
            item = QtWidgets.QTreeWidgetItem([f"{asset_key} [{format_list}]"])
            item.setData(0, QtCore.Qt.UserRole, asset_key)       # dict lookup key
            item.setData(0, QtCore.Qt.UserRole + 1, basename)    # actual asset name
            root.addChild(item)
            
            for fmt, filepath in formats.items():
                size = os.path.getsize(filepath)
                size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"
                child = QtWidgets.QTreeWidgetItem([f"{os.path.basename(filepath)} ({size_str})"])
                child.setData(0, QtCore.Qt.UserRole, asset_key)
                child.setData(0, QtCore.Qt.UserRole + 1, basename)
                item.addChild(child)
        
        root.setExpanded(True)
        
        # 在 zasset 结构预览中显示子文件夹内容
        self._update_preview()
        
        self._status_label.setText(f"发现 {len(self._models)} 个模型资产")
    
    def _update_preview(self):
        """更新 zasset 结构预览树，显示子文件夹内容"""
        self._preview_tree.clear()
        
        if not self._input_folder or not self._models:
            return
        
        # 收集所有子文件夹文件
        subfolder_patterns = self._get_subfolder_patterns()
        subfolder_files = collect_subfolder_contents(self._input_folder, subfolder_patterns)
        
        if subfolder_files:
            preview_root = QtWidgets.QTreeWidgetItem([f"子文件夹内容 ({len(subfolder_files)} 个文件)"])
            self._preview_tree.addTopLevelItem(preview_root)
            
            # 按目录分组
            dirs = {}
            for rel_path in sorted(subfolder_files.keys()):
                dir_name = os.path.dirname(rel_path)
                if dir_name not in dirs:
                    dirs[dir_name] = []
                dirs[dir_name].append(rel_path)
            
            for dir_name in sorted(dirs.keys()):
                if not dir_name:
                    continue
                dir_item = QtWidgets.QTreeWidgetItem([f"{dir_name}/ ({len(dirs[dir_name])} 个文件)"])
                preview_root.addChild(dir_item)
                for rel_path in dirs[dir_name]:
                    disk_path = subfolder_files[rel_path]
                    size = os.path.getsize(disk_path) if os.path.isfile(disk_path) else 0
                    size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"
                    # 显示相对路径中的文件名和大小
                    file_item = QtWidgets.QTreeWidgetItem([os.path.basename(rel_path), size_str])
                    dir_item.addChild(file_item)
            
            preview_root.setExpanded(True)
    
    def _rebuild_routing_ui(self):
        """从配置重建路由UI"""
        for i in reversed(range(self._routing_layout.count())):
            w = self._routing_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        routing = self._config.get('file_routing', {})
        for folder, ext_list in routing.items():
            self._add_routing_row(folder, ' '.join(ext_list))
    
    def _rebuild_meta_ui(self):
        """从配置重建元数据源UI和缩略图路径"""
        for i in reversed(range(self._thumb_paths_layout.count())):
            w = self._thumb_paths_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        paths = self._config.get('thumbnail_search_paths', [])
        for p in paths:
            self._add_thumb_path_row(p)
        
        for i in reversed(range(self._meta_container_layout.count())):
            w = self._meta_container_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        sources = self._config.get('metadata_sources', [])
        for src in sources:
            self._add_meta_source_widget(src)
    
    def _add_routing_row(self, folder_name="", exts_text=""):
        """添加一行路由配置"""
        frame = QtWidgets.QFrame()
        frame.setStyleSheet("QFrame { border: 1px solid #3a3a3a; border-radius: 4px; padding: 4px; }")
        layout = QtWidgets.QHBoxLayout(frame)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        
        layout.addWidget(QtWidgets.QLabel("文件夹:"))
        folder_edit = QtWidgets.QLineEdit(folder_name)
        folder_edit.setObjectName("route_folder")
        folder_edit.setPlaceholderText("root / textures / ass")
        folder_edit.setFixedWidth(140)
        layout.addWidget(folder_edit)
        
        layout.addWidget(QtWidgets.QLabel("扩展名:"))
        exts_edit = QtWidgets.QLineEdit(exts_text)
        exts_edit.setObjectName("route_exts")
        exts_edit.setPlaceholderText(".ma .mb .usd")
        layout.addWidget(exts_edit, 1)
        
        del_btn = QtWidgets.QPushButton("×")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet("color: #e06060; font-weight: bold; padding: 0;")
        del_btn.clicked.connect(lambda: (frame.deleteLater(), None))
        layout.addWidget(del_btn)
        
        self._routing_layout.addWidget(frame)
    
    def _save_routing_config(self):
        """从UI收集并保存文件路由配置"""
        routing = {}
        for i in range(self._routing_layout.count()):
            frame = self._routing_layout.itemAt(i).widget()
            if not frame:
                continue
            folder_edit = frame.findChild(QtWidgets.QLineEdit, "route_folder")
            exts_edit = frame.findChild(QtWidgets.QLineEdit, "route_exts")
            if not folder_edit or not exts_edit:
                continue
            folder = folder_edit.text().strip()
            exts = exts_edit.text().strip().split()
            if folder and exts:
                routing[folder] = [e if e.startswith('.') else '.' + e for e in exts]
        self._config['file_routing'] = routing
        
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Assets', 'preset', 'model_mapping.json')
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            self._status_label.setText("路由配置已保存")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "保存失败", f"无法保存配置:\n{e}")
    
    def _add_thumb_path_row(self, text=""):
        """添加一行缩略图搜索路径"""
        row = QtWidgets.QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        
        path_edit = QtWidgets.QLineEdit(text)
        path_edit.setObjectName("thumb_path")
        path_edit.setPlaceholderText("{assetName}_fileDependencies/thumbnail.jpg")
        row_layout.addWidget(path_edit, 1)
        
        del_btn = QtWidgets.QPushButton("×")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet("color: #e06060; font-weight: bold; padding: 0;")
        del_btn.clicked.connect(lambda: (row.deleteLater(), None))
        row_layout.addWidget(del_btn)
        
        self._thumb_paths_layout.addWidget(row)
    
    def _add_meta_source_widget(self, data=None):
        """添加一个元数据源配置widget"""
        data = data or {}
        frame = QtWidgets.QFrame()
        frame.setStyleSheet("QFrame { border: 1px solid #3a3a3a; border-radius: 4px; padding: 4px; }")
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        
        header = QtWidgets.QHBoxLayout()
        idx = self._meta_container_layout.count() + 1
        title = QtWidgets.QLabel(f"源 {idx}")
        title.setStyleSheet("color: #5294e2; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        del_btn = QtWidgets.QPushButton("删除")
        del_btn.setStyleSheet("color: #e06060; font-size: 11px; padding: 2px 8px;")
        del_btn.clicked.connect(lambda: (frame.deleteLater(), None))
        header.addWidget(del_btn)
        layout.addLayout(header)
        
        pattern_layout = QtWidgets.QHBoxLayout()
        pattern_layout.addWidget(QtWidgets.QLabel("文件路径模板:"))
        pattern_edit = QtWidgets.QLineEdit(data.get('file_pattern', ''))
        pattern_edit.setObjectName("meta_pattern")
        pattern_edit.setPlaceholderText("{assetName}_fileDependencies/{assetName}.zooInfo")
        pattern_layout.addWidget(pattern_edit, 1)
        layout.addLayout(pattern_layout)
        
        fmt_layout = QtWidgets.QHBoxLayout()
        fmt_layout.addWidget(QtWidgets.QLabel("文件格式:"))
        fmt_combo = QtWidgets.QComboBox()
        fmt_combo.setObjectName("meta_format")
        fmt_combo.addItems(["json", "txt"])
        val = data.get('file_format', 'json')
        fmt_combo.setCurrentIndex(0 if val == 'json' else 1)
        fmt_layout.addWidget(fmt_combo)
        fmt_layout.addStretch()
        layout.addLayout(fmt_layout)
        
        mapping_label = QtWidgets.QLabel("字段映射:")
        mapping_label.setStyleSheet("color: #909090; font-size: 11px;")
        layout.addWidget(mapping_label)
        
        mapping_layout = QtWidgets.QVBoxLayout()
        mapping_layout.setObjectName("meta_mappings")
        mapping_layout.setSpacing(2)
        layout.addLayout(mapping_layout)
        
        add_field_btn = QtWidgets.QPushButton("+ 添加字段映射")
        add_field_btn.setStyleSheet("font-size: 14px; padding: 6px 14px;")
        add_field_btn.clicked.connect(
            lambda: self._add_meta_field_row(mapping_layout)
        )
        layout.addWidget(add_field_btn)
        
        for field in data.get('field_mapping', []):
            self._add_meta_field_row(
                mapping_layout,
                source=field.get('source', ''),
                target=field.get('target', ''),
                processor=field.get('processor', '')
            )
        
        self._meta_container_layout.addWidget(frame)
    
    def _add_meta_field_row(self, parent_layout, source="", target="", processor=""):
        """添加一行字段映射"""
        row = QtWidgets.QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        
        src_edit = QtWidgets.QLineEdit(source)
        src_edit.setObjectName("field_source")
        src_edit.setPlaceholderText("源字段")
        src_edit.setFixedWidth(130)
        row_layout.addWidget(src_edit)
        
        arrow = QtWidgets.QLabel("→")
        arrow.setStyleSheet("color: #5294e2;")
        arrow.setFixedWidth(20)
        row_layout.addWidget(arrow)
        
        tgt_edit = QtWidgets.QLineEdit(target)
        tgt_edit.setObjectName("field_target")
        tgt_edit.setPlaceholderText("目标字段")
        tgt_edit.setFixedWidth(130)
        row_layout.addWidget(tgt_edit)
        
        proc_combo = QtWidgets.QComboBox()
        proc_combo.setObjectName("field_processor")
        proc_combo.addItems(["none", "split_comma", "first_line"])
        proc_combo.setCurrentText(processor if processor else "none")
        proc_combo.setToolTip(
            "none — 不做处理，值是什么就用什么（推荐，适用大多数情况）\n"
            "split_comma — 按逗号分割字符串为列表（如 \"a,b,c\" → [\"a\",\"b\",\"c\"]）\n"
            "first_line — 只取文本第一行（用于多行文本只需标题）"
        )
        row_layout.addWidget(proc_combo)
        
        del_btn = QtWidgets.QPushButton("×")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet("color: #e06060; font-weight: bold; padding: 0;")
        del_btn.clicked.connect(lambda: (row.deleteLater(), None))
        row_layout.addWidget(del_btn)
        
        parent_layout.addWidget(row)
    
    def _add_meta_source(self):
        """UI按钮回调：添加空白元数据源"""
        self._add_meta_source_widget({})
    
    def _collect_meta_config(self):
        """从UI收集元数据源配置"""
        sources = []
        for i in range(self._meta_container_layout.count()):
            frame = self._meta_container_layout.itemAt(i).widget()
            if not frame or not isinstance(frame, QtWidgets.QFrame):
                continue
            
            pattern_edit = frame.findChild(QtWidgets.QLineEdit, "meta_pattern")
            fmt_combo = frame.findChild(QtWidgets.QComboBox, "meta_format")
            if not pattern_edit or not fmt_combo:
                continue
            
            fields = []
            mapping_layout = None
            for child in frame.findChildren(QtWidgets.QVBoxLayout):
                if child.objectName() == "meta_mappings":
                    mapping_layout = child
                    break
            
            if mapping_layout:
                for k in range(mapping_layout.count()):
                    row = mapping_layout.itemAt(k).widget()
                    if not row or not isinstance(row, QtWidgets.QWidget):
                        continue
                    src = row.findChild(QtWidgets.QLineEdit, "field_source")
                    tgt = row.findChild(QtWidgets.QLineEdit, "field_target")
                    proc = row.findChild(QtWidgets.QComboBox, "field_processor")
                    if src and tgt and proc:
                        fields.append({
                            'source': src.text(),
                            'target': tgt.text(),
                            'processor': proc.currentText()
                        })
            
            sources.append({
                'file_pattern': pattern_edit.text(),
                'file_format': fmt_combo.currentText(),
                'field_mapping': fields
            })
        
        return sources
    
    def _save_meta_config(self):
        """保存元数据源配置和缩略图路径到 model_mapping.json"""
        sources = self._collect_meta_config()
        self._config['metadata_sources'] = sources
        
        paths = []
        for i in range(self._thumb_paths_layout.count()):
            row = self._thumb_paths_layout.itemAt(i).widget()
            if not row:
                continue
            path_edit = row.findChild(QtWidgets.QLineEdit, "thumb_path")
            if path_edit and path_edit.text().strip():
                paths.append(path_edit.text().strip())
        self._config['thumbnail_search_paths'] = paths
        
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Assets', 'preset', 'model_mapping.json')
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            self._status_label.setText("元数据配置已保存")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "保存失败", f"无法保存配置:\n{e}")
    
    def _get_target_formats(self):
        """获取目标格式列表，为空则全部"""
        text = self._target_format_input.text().strip()
        if not text:
            return None
        custom = [f.strip().lower() for f in text.split(',') if f.strip()]
        return custom if custom else None
    
    def _get_subfolder_patterns(self):
        """获取子文件夹模式列表"""
        if not self._include_subfolders.isChecked():
            return []
        text = self._subfolder_patterns.text().strip()
        if not text:
            return DEFAULT_SUBFOLDER_PATTERNS
        patterns = [p.strip() for p in text.split(',') if p.strip()]
        return patterns if patterns else DEFAULT_SUBFOLDER_PATTERNS
    
    def _convert(self):
        if not self._input_folder:
            folder = self._input_path.text().strip()
            if not folder:
                QtWidgets.QMessageBox.warning(self, "警告", "请先选择模型文件夹")
                return
            self._input_folder = folder
        
        # 输出文件夹：优先使用设置的，未设置则弹出选择
        output_folder = self._output_path.text().strip()
        if not output_folder:
            output_folder = _get_import_category_path()
        if not output_folder:
            output_folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "选择输出文件夹", os.path.expanduser("~"))
        if not output_folder:
            return
        
        # 从模型树收集所有资产
        asset_entries = []
        root = self._model_tree.topLevelItem(0) if self._model_tree.topLevelItemCount() > 0 else None
        if root:
            for i in range(root.childCount()):
                item = root.child(i)
                asset_key = item.data(0, QtCore.Qt.UserRole)
                basename = item.data(0, QtCore.Qt.UserRole + 1)
                if asset_key and basename:
                    asset_entries.append((asset_key, basename))
        
        if not asset_entries:
            QtWidgets.QMessageBox.warning(self, "警告", "没有检测到模型资产，请先扫描")
            return
        
        self._is_converting = True
        self._is_cancelled = False
        self._ok_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setMaximum(len(asset_entries))
        
        target_formats = self._get_target_formats()
        
        success_count = 0
        fail_count = 0
        
        for i, (asset_key, asset_name) in enumerate(asset_entries):
            if self._is_cancelled:
                break
            
            if asset_key not in self._models:
                continue
            
            self._status_label.setText(f"正在转换 [{i+1}/{len(asset_entries)}]: {asset_name}")
            self._progress.setValue(i + 1)
            
            try:
                import maya.cmds as _cmds
                _cmds.refresh()
            except:
                pass
            
            result, error = export_zasset(
                asset_name,
                self._models[asset_key],
                self._config,
                output_folder,
                self._input_folder if self._use_existing_metadata.isChecked() else None,
                target_formats,
                self._get_subfolder_patterns()
            )
            
            if result:
                success_count += 1
            else:
                fail_count += 1
                self._status_label.setText(f"转换失败 [{i+1}/{len(asset_entries)}]: {asset_name} - {error}")
        
        self._is_converting = False
        self._ok_btn.setEnabled(True)
        self._progress.setVisible(False)
        
        if self._is_cancelled:
            self._status_label.setText(f"操作已中止。成功: {success_count}, 失败: {fail_count}")
        else:
            self._status_label.setText(f"转换完成！成功: {success_count}, 失败: {fail_count}")
        
        if getattr(self, '_import_to_category', None) and self._import_to_category.isChecked():
            if output_folder:
                print("[Model Tool] 开始导入当前分类...")
                category_path = _get_import_category_path()
                print(f"[Model Tool] 目标分类路径: {category_path}")
                if category_path and os.path.isdir(category_path):
                    imported = _copy_zassets_to_category(output_folder, category_path)
                    if imported:
                        self._status_label.setText(
                            f"完成: {success_count} 成功, {fail_count} 失败 | 已导入 {imported} 个到当前分类")
                        print(f"[Model Tool] 已导入 {imported} 个zasset到: {category_path}")
                    else:
                        print("[Model Tool] 未找到zasset文件或导入失败")
                else:
                    print("[Model Tool] 当前分类路径无效，跳过导入")
    
    def _on_cancel(self):
        if getattr(self, '_is_converting', False):
            self._is_cancelled = True
            self._cancel_btn.setEnabled(False)
            self._status_label.setText("正在中止...")
            try:
                import maya.cmds as _cmds
                _cmds.refresh()
            except Exception:
                pass
        else:
            self.close()
    
    def _on_help(self):
        import webbrowser
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = os.path.join(plugin_root, "Assets", "help", "model_to_zasset", "help.html")
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            print("[Model Tool] 帮助文件未找到:", help_path)


def main():
    if QtWidgets is None:
        print("无法加载Qt模块")
        return
    
    try:
        import maya.OpenMayaUI as omui
        from shiboken6 import wrapInstance
        main_window_ptr = omui.MQtUtil.mainWindow()
        main_window = wrapInstance(int(main_window_ptr), QtWidgets.QMainWindow)
    except:
        main_window = None
    
    dialog = ModelToZassetDialog(main_window)
    dialog.show()


if __name__ == "__main__":
    main()