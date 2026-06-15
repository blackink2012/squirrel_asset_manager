import os
import sys
import maya.cmds as cmds
import maya.mel as mel

def get_maya_version():
    return cmds.about(version=True)

def get_maya_language():
    try:
        lang = mel.eval("getApplicationLanguage")
        return lang
    except:
        return 0

def get_maya_pref_dir():
    maya_pref = cmds.internalVar(userPrefDir=True)
    return maya_pref

def get_maya_script_dir():
    maya_script = cmds.internalVar(userScriptDir=True)
    return maya_script

def _find_project_root():
    """定位项目根目录（squirrel_asset_manager 的父目录）"""
    install_script = os.path.abspath(__file__)
    candidate = os.path.dirname(os.path.dirname(install_script))
    pkg_marker = os.path.join(candidate, "squirrel_asset_manager", "__init__.py")
    if os.path.isfile(pkg_marker):
        return candidate
    return os.path.dirname(install_script)

def install_to_maya():
    try:
        project_root = _find_project_root()
        maya_script_dir = get_maya_script_dir()
        maya_pref_dir = get_maya_pref_dir()

        loader_path = os.path.join(maya_script_dir, "squirrel_asset_manager_loader.py")
        with open(loader_path, "w", encoding="utf-8") as f:
            f.write('# squirrel_asset_manager_loader.py\n')
            f.write('# 由 install.py 自动生成，指向原始项目路径\n')
            f.write('# 不要删除此文件，否则松鼠资产管理器将无法启动\n')
            f.write('\n')
            f.write('import sys, os\n')
            f.write(f'project_root = {repr(project_root)}\n')
            f.write('if project_root not in sys.path:\n')
            f.write('    sys.path.insert(0, project_root)\n')

        return True, maya_pref_dir, loader_path, project_root
    except Exception as e:
        return False, str(e), "", ""

def uninstall_from_maya():
    try:
        maya_script_dir = get_maya_script_dir()
        loader_path = os.path.join(maya_script_dir, "squirrel_asset_manager_loader.py")
        if os.path.exists(loader_path):
            os.remove(loader_path)
        return True
    except Exception as e:
        return False, str(e)

def onMayaDroppedPythonFile(*args, **kwargs):
    try:
        shelfTopLevel = mel.eval("$temp = $gShelfTopLevel")
        currentShelf = cmds.tabLayout(shelfTopLevel, query=True, selectTab=True)

        button_name = "SquirrelAssetManager"
        if cmds.shelfButton(button_name, exists=True):
            cmds.deleteUI(button_name)

        project_root = _find_project_root()

        command_code = (
            "import sys, os\n"
            "from maya import cmds\n"
            f"project_root = {repr(project_root)}\n"
            "if project_root not in sys.path:\n"
            "    sys.path.insert(0, project_root)\n"
            "\n"
            "try:\n"
            "    from squirrel_asset_manager.ui.main_window import MaterialLibraryWindow\n"
            "    MaterialLibraryWindow.show_window()\n"
            "except Exception as e:\n"
            "    cmds.warning(f'\u677e\u9f20\u8d44\u4ea7\u7ba1\u7406\u5668\u542f\u52a8\u5931\u8d25: {str(e)}')\n"
        )

        success, pref_dir, loader_path, tool_dir = install_to_maya()

        icon_path = os.path.join(project_root, "squirrel_asset_manager", "Assets", "icon", "squirrel_asset_iconC.png")
        icon_path = icon_path.replace("\\", "/")

        cmds.shelfButton(
            button_name,
            parent=currentShelf,
            label="\u677e\u9f20\u8d44\u4ea7\u7ba1\u7406\u5668",
            command=command_code,
            annotation="\u542f\u52a8\u677e\u9f20\u8d44\u4ea7\u7ba1\u7406\u5668",
            image1=icon_path,
            sourceType="python"
        )

        if success:
            message_text = (
                f"\u5b89\u88c5\u6210\u529f\uff01\n\n"
                f"\u5df2\u521b\u5efa\u52a0\u8f7d\u5668: {loader_path}\n\n"
                f"\u8bf7\u786e\u8ba4\u5de5\u5177\u680f\u5df2\u6dfb\u52a0\u300c\u677e\u9f20\u8d44\u4ea7\u7ba1\u7406\u5668\u300d\u6309\u94ae\uff0c\n"
                f"\u70b9\u51fb\u5373\u53ef\u542f\u52a8\u3002"
            )
        else:
            message_text = f"\u5b89\u88c5\u5931\u8d25: {pref_dir}"

        cmds.confirmDialog(
            title="\u6210\u529f",
            message=message_text,
            button=["\u786e\u5b9a"],
            defaultButton="\u786e\u5b9a"
        )
    except Exception as e:
        cmds.warning(f"\u521b\u5efa\u5de5\u5177\u67b6\u6309\u94ae\u65f6\u51fa\u9519: {str(e)}")

if __name__ == "__main__":
    success, pref_dir, loader_path, tool_dir = install_to_maya()
    if success:
        print("\u5b89\u88c5\u6210\u529f\uff01")
        print(f"\u52a0\u8f7d\u5668: {loader_path}")
        print(f"\u9879\u76ee\u6839\u76ee\u5f55: {tool_dir}")
    else:
        print(f"\u5b89\u88c5\u5931\u8d25: {pref_dir}")
