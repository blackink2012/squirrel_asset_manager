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

def onMayaDroppedPythonFile(*args, **kwargs):
    try:
        shelfTopLevel = mel.eval("$temp = $gShelfTopLevel")
        currentShelf = cmds.tabLayout(shelfTopLevel, query=True, selectTab=True)
        
        button_name = "SquirrelAssetManager"
        if cmds.shelfButton(button_name, exists=True):
            cmds.deleteUI(button_name)
        
        command_code = '''
import os
import sys
import maya.cmds as cmds

script_dir = os.path.join(cmds.internalVar(userScriptDir=True), "squirrel_asset_manager")
if script_dir not in sys.path:
    sys.path.append(script_dir)

try:
    from squirrel_asset_manager.main import main
    main()
except Exception as e:
    cmds.warning(f"松鼠资产管理器启动失败: {str(e)}")
'''
        
        success, pref_dir, squirrel_tool_dir = copy_files_to_maya()
        
        if success:
            message_text = f"安装成功！\n\n脚本目录: {squirrel_tool_dir}"
        else:
            message_text = f"安装失败: {pref_dir}"
        
        icon_path = os.path.join(squirrel_tool_dir, "Assets", "icon", "squirrel_asset_iconC.png")
        
        cmds.shelfButton(
            button_name,
            parent=currentShelf,
            label="松鼠资产管理器",
            command=command_code,
            annotation="启动松鼠资产管理器",
            image1=icon_path,
            sourceType="python"
        )
        
        cmds.confirmDialog(
            title="成功",
            message=message_text,
            button=["确定"],
            defaultButton="确定"
        )
    except Exception as e:
        cmds.warning(f"创建工具架按钮时出错: {str(e)}")

if __name__ == "__main__":
    success, pref_dir, script_dir = copy_files_to_maya()
    if success:
        print(f"安装成功！")
        print(f"脚本目录: {script_dir}")
    else:
        print(f"安装失败: {pref_dir}")
