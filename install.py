import os
import sys
import shutil
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

def copy_files_to_maya():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))

        maya_pref_dir = get_maya_pref_dir()
        maya_script_dir = get_maya_script_dir()

        source_squirrel_dir = script_dir
        target_squirrel_dir = os.path.join(maya_script_dir, "squirrel_asset_manager")

        if os.path.exists(target_squirrel_dir):
            shutil.rmtree(target_squirrel_dir)

        shutil.copytree(source_squirrel_dir, target_squirrel_dir)

        return True, maya_pref_dir, target_squirrel_dir
    except Exception as e:
        return False, str(e), ""

def uninstall_from_maya():
    try:
        maya_script_dir = get_maya_script_dir()
        target_squirrel_dir = os.path.join(maya_script_dir, "squirrel_asset_manager")
        if os.path.exists(target_squirrel_dir):
            shutil.rmtree(target_squirrel_dir)
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

        command_code = '''
from maya import cmds

try:
    from squirrel_asset_manager.ui.main_window import MaterialLibraryWindow
    MaterialLibraryWindow.show_window()
except Exception as e:
    cmds.warning(f"\u677e\u9f20\u8d44\u4ea7\u7ba1\u7406\u5668\u542f\u52a8\u5931\u8d25: {str(e)}")
'''

        success, pref_dir, squirrel_tool_dir = copy_files_to_maya()

        if success:
            message_text = f"\u5b89\u88c5\u6210\u529f\uff01\n\n\u811a\u672c\u76ee\u5f55: {squirrel_tool_dir}"
        else:
            message_text = f"\u5b89\u88c5\u5931\u8d25: {pref_dir}"

        icon_path = os.path.join(squirrel_tool_dir, "Assets", "icon", "squirrel_asset_iconC.png")

        cmds.shelfButton(
            button_name,
            parent=currentShelf,
            label="\u677e\u9f20\u8d44\u4ea7\u7ba1\u7406\u5668",
            command=command_code,
            annotation="\u542f\u52a8\u677e\u9f20\u8d44\u4ea7\u7ba1\u7406\u5668",
            image1=icon_path,
            sourceType="python"
        )

        cmds.confirmDialog(
            title="\u6210\u529f",
            message=message_text,
            button=["\u786e\u5b9a"],
            defaultButton="\u786e\u5b9a"
        )
    except Exception as e:
        cmds.warning(f"\u521b\u5efa\u5de5\u5177\u67b6\u6309\u94ae\u65f6\u51fa\u9519: {str(e)}")

if __name__ == "__main__":
    success, pref_dir, script_dir = copy_files_to_maya()
    if success:
        print(f"\u5b89\u88c5\u6210\u529f\uff01")
        print(f"\u811a\u672c\u76ee\u5f55: {script_dir}")
    else:
        print(f"\u5b89\u88c5\u5931\u8d25: {pref_dir}")
