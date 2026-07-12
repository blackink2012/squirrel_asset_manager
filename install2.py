"""
EZforMaya 安装脚本
使用方法：将此脚本拖入Maya窗口即可完成安装
"""

import os
import shutil
import sys

# 全局导入maya模块
try:
    import maya.cmds as cmds
    import maya.mel as mel
    MAYA_AVAILABLE = True
except ImportError:
    MAYA_AVAILABLE = False
    print("警告: 未检测到Maya环境，请在Maya中运行此脚本")

# 确保executeDroppedPythonFile能正确调用onMayaDroppedPythonFile
# 当使用maya.app.general.executeDroppedPythonFile执行脚本时，会寻找onMayaDroppedPythonFile函数
# 所以我们需要确保这个函数存在并且能正确处理参数

def install_ez_for_maya(source_dir=None):
    """安装EZforMaya工具集
    
    Args:
        source_dir: 源文件目录，如果为None则尝试自动获取
    """
    
    if not MAYA_AVAILABLE:
        print("错误: Maya环境不可用")
        return False

    # 获取Maya用户文档目录（不带版本号）
    # C:\Users\用户名\Documents\maya
    maya_user_dir = cmds.internalVar(userAppDir=True)

    # 获取当前Maya版本号
    maya_version = cmds.about(version=True)
    print(f"Maya版本: {maya_version}")

    # 创建目标scripts目录（不带版本号）
    target_scripts_dir = os.path.join(maya_user_dir, "scripts")
    if not os.path.exists(target_scripts_dir):
        os.makedirs(target_scripts_dir)
        print(f"创建目标目录: {target_scripts_dir}")

    print(f"Maya用户目录: {maya_user_dir}")
    print(f"目标脚本目录: {target_scripts_dir}")
    
    # 处理source_dir参数，确保它是有效的目录路径
    # 如果source_dir是空字符串或无效，尝试自动获取
    if not source_dir or not os.path.exists(source_dir):
        try:
            # 尝试多种方法获取脚本路径，确保可靠性
            script_path = None
            
            # 方法1: 直接使用__file__
            if "__file__" in globals():
                script_path = os.path.abspath(__file__)
                print(f"方法1 - __file__: {script_path}")
            
            # 方法2: 使用sys.modules获取
            if not script_path:
                import sys
                if __name__ in sys.modules:
                    module = sys.modules[__name__]
                    if hasattr(module, "__file__"):
                        script_path = os.path.abspath(module.__file__)
                        print(f"方法2 - sys.modules: {script_path}")
            
            # 方法3: 使用inspect模块获取
            if not script_path:
                import inspect
                frame = inspect.currentframe()
                if frame:
                    try:
                        script_path = os.path.abspath(frame.f_code.co_filename)
                        print(f"方法3 - inspect: {script_path}")
                    finally:
                        del frame
            
            if script_path:
                source_dir = os.path.dirname(script_path)
                print(f"使用脚本所在目录作为源目录: {source_dir}")
            else:
                raise Exception("无法通过任何方法获取脚本路径")
                
        except Exception as e:
            error_msg = f"无法获取脚本路径: {str(e)}"
            print(error_msg)
            if MAYA_AVAILABLE:
                cmds.confirmDialog(
                    title='错误',
                    message=error_msg,
                    button=['确定'],
                    defaultButton='确定'
                )
            return False
    else:
        print(f"使用传入的源目录: {source_dir}")
    
    print(f"源文件目录: {source_dir}")
    
    # 验证源文件是否存在
    required_files = ["EZforMany.py", "EZicon.png"]
    missing_files = []
    
    for file_name in required_files:
        file_path = os.path.join(source_dir, file_name)
        if not os.path.exists(file_path):
            missing_files.append(file_name)
    
    if missing_files:
        print(f"错误：缺少以下文件: {', '.join(missing_files)}")
        print(f"请确保所有文件都在目录: {source_dir}")
        
        cmds.confirmDialog(
            title='文件缺失',
            message='以下文件缺失:\n%s\n\n请确保所有文件都在同一目录中。' % '\n'.join(missing_files),
            button=['确定'],
            defaultButton='确定'
        )
        return False
    
    # 目标目录结构
    ezmany_dir = os.path.join(target_scripts_dir, "ezmany")
    other_dir = os.path.join(ezmany_dir, "other")
    icons_dir = os.path.join(other_dir, "icons")
    plugins_base_dir = os.path.join(other_dir, "plug-ins")
    scripts_dir = os.path.join(other_dir, "scripts")
    
    # 创建2020-2027版本的plug-ins文件夹
    plug_versions = ["2020", "2021", "2022", "2023", "2024", "2025", "2026", "2027", "2028", "2029", "2030"]
    quicktools_dir = os.path.join(ezmany_dir, "QuickTools")
    directories = [ezmany_dir, other_dir, icons_dir, scripts_dir, quicktools_dir]
    directories.extend([os.path.join(plugins_base_dir, v) for v in plug_versions])
    directories.extend([os.path.join(scripts_dir, v) for v in plug_versions])
    for directory in directories:
        if not os.path.exists(directory):
            os.makedirs(directory)
            print(f"创建目录: {directory}")
    
    # 定义源文件路径和目标文件路径
    copy_operations = [
        {
            "source": os.path.join(source_dir, "ZJG_选择工具集.py"),
            "target": os.path.join(quicktools_dir, "ZJG_选择工具集.py"),
            "description": "选择工具集脚本"
        },
        {
            "source": os.path.join(source_dir, "EZforMany.py"),
            "target": os.path.join(scripts_dir, "EZforMany.py"),
            "description": "EZforMany"
        },
        {
            "source": os.path.join(source_dir, "EZicon.png"),
            "target": os.path.join(icons_dir, "EZicon.png"),
            "description": "图标文件"
        }
    ]
    
    # 复制文件
    files_copied = []
    for operation in copy_operations:
        source_path = operation["source"]
        target_path = operation["target"]
        description = operation["description"]
        
        try:
            # 确保目标目录存在
            target_dir = os.path.dirname(target_path)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir)
            
            # 复制文件
            shutil.copy2(source_path, target_path)
            files_copied.append(description)
            print(f"复制{description}:")
            print(f"  从: {source_path}")
            print(f"  到: {target_path}")
            print(f"  状态: 成功")
        except Exception as e:
            print(f"错误: 无法复制{description}:")
            print(f"  源: {source_path}")
            print(f"  目标: {target_path}")
            print(f"  错误: {str(e)}")
    
    # 更新Maya.env，添加icons、scripts和plug-ins路径
    update_maya_env(maya_user_dir, maya_version, icons_dir, scripts_dir, plugins_base_dir)
    
    # 创建工具架按钮
    create_shelf_button(scripts_dir, maya_user_dir)
    
    # 安装完成提示
    print("\n" + "="*50)
    print("EZforMaya 安装完成!")
    print(f"已安装: {', '.join(files_copied)}")
    print(f"主目录: {ezmany_dir}")
    print("="*50)
    
    # 显示完成对话框
    cmds.confirmDialog(
        title='安装完成',
        message='EZforMaya 安装完成!\n\n文件已复制到:\n%s\n\n工具架按钮已创建。' % ezmany_dir,
        button=['确定'],
        defaultButton='确定'
    )
    
    return True

def update_maya_env(maya_user_dir, maya_version, icons_dir, scripts_dir, plugins_base_dir):
    """更新当前Maya版本目录下的Maya.env，添加环境变量路径"""
    try:
        # 清理版本号，只取主版本号如"2025"
        ver = ''.join(c for c in maya_version if c.isdigit() or c in '._-').split('.')[0] if maya_version else maya_version

        # Maya.env路径: ...\maya\2025\Maya.env
        # cmds.internalVar(userAppDir=True) 返回 ...\maya\，直接在其下拼接版本目录
        maya_base = maya_user_dir.rstrip('/\\')
        maya_env_path = os.path.join(maya_base, ver, "Maya.env")
        maya_env_dir = os.path.dirname(maya_env_path)

        # 确保目录存在
        if not os.path.exists(maya_env_dir):
            os.makedirs(maya_env_dir)

        # 备份
        if os.path.exists(maya_env_path):
            shutil.copy2(maya_env_path, maya_env_path + ".bak")

        # 读取现有内容
        env_content = {}
        if os.path.exists(maya_env_path):
            with open(maya_env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and '=' in line:
                        key, value = line.split('=', 1)
                        env_content[key.strip()] = value.strip()

        # 构建路径
        icons_path = icons_dir
        scripts_base_path = scripts_dir
        scripts_ver_path = os.path.join(scripts_dir, ver) if ver else None
        plugins_path = os.path.join(plugins_base_dir, ver) if ver else None

        # 更新环境变量 - 在末尾追加，不覆盖已有值
        def append_path_env(key, new_path):
            """在环境变量末尾追加单个路径"""
            if not new_path:
                return
            if key in env_content:
                current_paths = env_content[key].split(';')
                if new_path not in current_paths:
                    current_paths.append(new_path)
                    env_content[key] = ';'.join(current_paths)
            else:
                env_content[key] = new_path

        append_path_env("XBMLANGPATH", icons_path)
        append_path_env("MAYA_SCRIPT_PATH", scripts_base_path)
        append_path_env("MAYA_SCRIPT_PATH", scripts_ver_path)
        append_path_env("MAYA_PLUG_IN_PATH", plugins_path)

        # 写入
        with open(maya_env_path, 'w', encoding='utf-8') as f:
            for key, value in env_content.items():
                f.write(f"{key} = {value}\n")

        print(f"已更新Maya.env: {maya_env_path}")
        return True
    except Exception as e:
        print(f"更新Maya.env时出错: {str(e)}")
        return False

def _add_status_line_button(icon_path, command_code):
    """在 Maya 状态行添加 EZforMaya 快捷图标按钮（与松鼠资产管理器按钮大小一致）。"""
    try:
        import maya.OpenMayaUI as omui

        try:
            import shiboken6
            wrap_instance = shiboken6.wrapInstance
        except ImportError:
            try:
                import shiboken2
                wrap_instance = shiboken2.wrapInstance
            except ImportError:
                print("[EZforMaya] 无法导入 shiboken，跳过状态行按钮")
                return

        try:
            from PySide6 import QtWidgets, QtGui, QtCore
        except ImportError:
            try:
                from PySide2 import QtWidgets, QtGui, QtCore
            except ImportError:
                print("[EZforMaya] 无法导入 PySide，跳过状态行按钮")
                return

        status_line_name = mel.eval('string $tempStr = $gStatusLine')
        status_line_ptr = omui.MQtUtil.findControl(status_line_name)

        if not status_line_ptr:
            print("[EZforMaya] 无法找到状态行")
            return

        status_line_widget = wrap_instance(int(status_line_ptr), QtWidgets.QWidget)

        def _create_button():
            parent_height = status_line_widget.height()
            if parent_height <= 0:
                parent_height = status_line_widget.minimumHeight()
            if parent_height <= 0:
                parent_height = 40
            icon_sz = max(parent_height, 36)

            button = QtWidgets.QToolButton()
            button.setAutoRaise(True)
            button.setToolTip("EZforMaya - 脚本浏览器")
            button.setIconSize(QtCore.QSize(icon_sz, icon_sz))

            if icon_path and os.path.exists(icon_path):
                button.setIcon(QtGui.QIcon(icon_path))
            else:
                button.setText("EZ")

            def _on_click(_checked=False):
                exec(command_code, {"__name__": "__main__"})

            button.clicked.connect(_on_click)

            layout = status_line_widget.layout()
            if layout:
                layout.addWidget(button)
                print("[EZforMaya] 状态行按钮已添加")

        QtCore.QTimer.singleShot(500, _create_button)

    except Exception as e:
        print(f"[EZforMaya] 添加状态行按钮失败: {e}")


def create_shelf_button(scripts_dir, maya_user_dir):
    """在工具架上创建按钮
    
    Args:
        scripts_dir: 脚本目录路径
        maya_user_dir: Maya用户目录
    """
    
    if not MAYA_AVAILABLE:
        print("错误: Maya环境不可用")
        return
    
    # 工具架名称（可以修改为你喜欢的工具架名称）
    shelf_name = "Custom"
    
    # 获取当前工具架
    current_shelf = cmds.tabLayout("ShelfLayout", query=True, selectTab=True)
    
    # 如果指定的工具架不存在，则创建
    shelves = cmds.tabLayout("ShelfLayout", query=True, childArray=True)
    if shelf_name not in shelves:
        cmds.shelfLayout(shelf_name, parent="ShelfLayout")
        print(f"创建新工具架: {shelf_name}")
    
    # 切换到目标工具架
    cmds.tabLayout("ShelfLayout", edit=True, selectTab=shelf_name)
    
    # 图标路径
    icon_path = os.path.join(maya_user_dir, "scripts", "ezmany", "other", "icons", "EZicon.png")
    
    # 脚本路径
    script_path = os.path.join(scripts_dir, "EZforMany.py")
    
    # 创建Python命令来执行脚本
    python_command = '''
import os
import sys

maya_scripts_dir = os.path.join(os.path.expanduser("~"), "Documents", "maya", "scripts")

# 拼接EZforMany.py的完整路径
ezmany_path = os.path.join(maya_scripts_dir, "ezmany", "other", "scripts")

# 将路径添加到Python搜索路径
if ezmany_path not in sys.path:
    sys.path.append(ezmany_path)

# 最佳实践：优先检查特定模块，再考虑全局搜索
def best_practice_check(func_name):
    import maya.cmds as cmds
    import sys

    if hasattr(cmds, func_name):
        return True

    if func_name in globals() and callable(globals()[func_name]):
        return True
    
    return False
    
if best_practice_check("run_file_browser"):
    run_file_browser()
    
else:
    ezfor_many_file = os.path.join(ezmany_path, "EZforMany.py")
    exec(open(ezfor_many_file, encoding='utf-8').read())
    run_file_browser()

'''
    
    # 创建工具架按钮
    try:
        # 检查是否已存在同名按钮
        shelf_buttons = cmds.shelfLayout(shelf_name, query=True, childArray=True) or []
        button_exists = False
        
        for button in shelf_buttons:
            label = cmds.shelfButton(button, query=True, label=True)
            if label == "脚本浏览器":
                # 更新现有按钮
                cmds.shelfButton(button, edit=True, command=python_command, image=icon_path)
                button_exists = True
                print("更新工具架按钮: 脚本浏览器")
                break
        
        if not button_exists:
            # 创建新按钮
            cmds.shelfButton(
                parent=shelf_name,
                label="脚本浏览器",
                command=python_command,
                image=icon_path,
                annotation="EZforMany - 浏览和执行Maya脚本",
                sourceType="python"
            )
            print("创建工具架按钮: 脚本浏览器")
        
        print(f"图标路径: {icon_path}")
        print(f"脚本路径: {script_path}")

        # 添加状态行快捷按钮
        _add_status_line_button(icon_path, python_command)
        
    except Exception as e:
        print(f"错误: 无法创建工具架按钮: {str(e)}")
        # 尝试简化按钮创建
        try:
            cmds.shelfButton(
                parent=shelf_name,
                label="脚本浏览器",
                command=python_command,
                annotation="EZforMany"
            )
            print("创建简化版工具架按钮成功")
        except Exception as e2:
            print(f"错误: 无法创建简化版工具架按钮: {str(e2)}")
    
    # 切换回原来的工具架
    if current_shelf:
        cmds.tabLayout("ShelfLayout", edit=True, selectTab=current_shelf)

# ============================================================================
# Maya拖放脚本必需函数
# ============================================================================

def onMayaDroppedPythonFile(*args, **kwargs):
    """
    Maya拖放脚本必需函数
    当脚本被拖入Maya窗口时，Maya会自动调用此函数
    当使用maya.app.general.executeDroppedPythonFile执行脚本时，也会调用此函数
    """
    try:
        print("拖入的文件:")
        print(f"args: {args}")
        print(f"kwargs: {kwargs}")
        
        # 直接使用第一个参数作为脚本路径，这是executeDroppedPythonFile的标准传递方式
        script_path = args[0] if args else None
        print(f"直接使用args[0]作为脚本路径: {script_path}")
        
        if script_path:
            # 确保路径是绝对路径
            if not os.path.isabs(script_path):
                script_path = os.path.abspath(script_path)
                print(f"转换为绝对路径: {script_path}")
            
            source_dir = os.path.dirname(script_path)
            print(f"从args获取源文件目录: {source_dir}")
            success = install_ez_for_maya(source_dir)
            return success
        else:
            # 如果没有获取到参数，让install_ez_for_maya函数自己处理
            print("没有获取到脚本路径参数，让install_ez_for_maya函数自己处理")
            success = install_ez_for_maya()
            return success
        
    except Exception as e:
        import traceback
        error_msg = f"安装过程中出现错误:\n{str(e)}\n\n{traceback.format_exc()}"
        print(error_msg)
        
        # 使用全局的cmds变量，确保它已经被定义
        if MAYA_AVAILABLE:
            cmds.confirmDialog(
                title='安装错误',
                message=error_msg,
                button=['确定'],
                defaultButton='确定'
            )
        else:
            # 如果Maya不可用，只打印错误
            print("Maya环境不可用，无法显示错误对话框")
        
        return False



# ============================================================================
# 主程序入口
# ============================================================================

if __name__ == "__main__":
    """
    当直接运行此脚本时（不是通过拖放）
    """
    print("直接运行安装脚本...")
    print(f"__name__ = {__name__}")
    print(f"__file__ 是否在globals中: {'__file__' in globals()}")
    
    if not MAYA_AVAILABLE:
        print("错误: 此脚本需要在Maya中运行！")
        print("请将脚本拖入Maya窗口进行安装。")
        sys.exit(1)
    
    # 在Maya中，直接运行安装
    try:
        # 尝试获取当前脚本路径
        source_dir = None
        
        # 方法1: 检查是否有命令行参数
        if len(sys.argv) > 1:
            print(f"命令行参数: {sys.argv}")
            # 检查第一个参数是否是脚本路径
            if os.path.exists(sys.argv[1]):
                source_dir = os.path.dirname(sys.argv[1])
                print(f"从命令行参数获取源目录: {source_dir}")
        
        # 方法2: 尝试从__file__获取
        if not source_dir and "__file__" in globals():
            file_path = os.path.abspath(__file__)
            print(f"__file__ 值: {file_path}")
            source_dir = os.path.dirname(file_path)
            print(f"从__file__获取源目录: {source_dir}")
        
        # 如果成功获取到源目录，执行安装
        if source_dir:
            print(f"最终确定的源文件目录: {source_dir}")
            success = install_ez_for_maya(source_dir)
        else:
            # 尝试使用当前工作目录
            source_dir = os.getcwd()
            print(f"使用当前工作目录作为源目录: {source_dir}")
            success = install_ez_for_maya(source_dir)
    except Exception as e:
        import traceback
        print(f"安装过程中出现错误: {str(e)}")
        print(traceback.format_exc())
        cmds.confirmDialog(
            title='安装错误',
            message=str(e),
            button=['确定'],
            defaultButton='确定'
        )
        success = False