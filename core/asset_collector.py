import os
import shutil
from typing import Dict, List, Optional, Set

try:
    import maya.cmds as cmds
    _IN_MAYA = True
except ImportError:
    _IN_MAYA = False


class AssetCollector:

    @staticmethod
    def collect_all(associated_objects: List[str]) -> Dict[str, Dict[str, str]]:
        result = {
            "caches": {},
            "proxies": {},
            "references": {},
        }
        if not _IN_MAYA:
            return result

        result["caches"] = AssetCollector.collect_cache_files(associated_objects)
        result["proxies"] = AssetCollector.collect_proxy_files(associated_objects)
        result["references"] = AssetCollector.collect_reference_files(associated_objects)
        return result

    @staticmethod
    def collect_cache_files(associated_objects: List[str]) -> Dict[str, str]:
        node_to_path: Dict[str, str] = {}
        if not _IN_MAYA:
            return node_to_path

        for obj in associated_objects or []:
            if not cmds.objExists(obj):
                continue
            AssetCollector._collect_alembic(obj, node_to_path)
            AssetCollector._collect_gpu_cache(obj, node_to_path)
            AssetCollector._collect_ncache(obj, node_to_path)

        return node_to_path

    @staticmethod
    def _collect_alembic(obj: str, result: Dict[str, str]):
        try:
            alembic_nodes = cmds.listConnections(obj, type='AlembicNode')
            if not alembic_nodes:
                return
            for a_node in set(alembic_nodes):
                if not cmds.objExists(a_node):
                    continue
                path = cmds.getAttr(a_node + '.abc_File')
                if path and os.path.isfile(path):
                    result[a_node] = path
        except Exception as e:
            print(f"[AssetCollector] Alembic 收集失败 ({obj}): {e}")

    @staticmethod
    def _collect_gpu_cache(obj: str, result: Dict[str, str]):
        try:
            gpu_nodes = cmds.ls(type='gpuCache')
            if not gpu_nodes:
                return
            for g_node in gpu_nodes:
                if not cmds.objExists(g_node):
                    continue
                shapes = cmds.listRelatives(g_node, shapes=True, type='gpuCache') or []
                for s in shapes:
                    path = cmds.getAttr(s + '.cacheFileName')
                    if path and os.path.isfile(path):
                        result[s] = path
        except Exception as e:
            print(f"[AssetCollector] GPU Cache 收集失败: {e}")

    @staticmethod
    def _collect_ncache(obj: str, result: Dict[str, str]):
        try:
            cf_nodes = cmds.listConnections(obj, type='cacheFile')
            if not cf_nodes:
                return
            for cf in set(cf_nodes):
                if not cmds.objExists(cf):
                    continue
                attrs = ['.cachePath', '.path']
                for attr in attrs:
                    try:
                        path = cmds.getAttr(cf + attr)
                        if path and os.path.isfile(path):
                            result[cf] = path
                            break
                    except Exception:
                        continue
        except Exception as e:
            print(f"[AssetCollector] nCache 收集失败 ({obj}): {e}")

    @staticmethod
    def collect_proxy_files(associated_objects: List[str]) -> Dict[str, str]:
        node_to_path: Dict[str, str] = {}
        if not _IN_MAYA:
            return node_to_path

        AssetCollector._collect_arnold_standin(node_to_path)
        AssetCollector._collect_vray_proxy(node_to_path)
        AssetCollector._collect_redshift_proxy(node_to_path)

        return node_to_path

    @staticmethod
    def _collect_arnold_standin(result: Dict[str, str]):
        try:
            standins = cmds.ls(type='aiStandIn')
            for s in standins:
                if not cmds.objExists(s):
                    continue
                path = cmds.getAttr(s + '.dso')
                if path and os.path.isfile(path):
                    result[s] = path
        except Exception as e:
            print(f"[AssetCollector] Arnold StandIn 收集失败: {e}")

    @staticmethod
    def _collect_vray_proxy(result: Dict[str, str]):
        try:
            proxies = cmds.ls(type='VRayProxy')
            for vp in proxies:
                if not cmds.objExists(vp):
                    continue
                path = cmds.getAttr(vp + '.fileName')
                if path and os.path.isfile(path):
                    result[vp] = path
        except Exception as e:
            print(f"[AssetCollector] VRayProxy 收集失败: {e}")

    @staticmethod
    def _collect_redshift_proxy(result: Dict[str, str]):
        try:
            proxies = cmds.ls(type='RedshiftProxyMesh')
            for rp in proxies:
                if not cmds.objExists(rp):
                    continue
                path = cmds.getAttr(rp + '.fileName')
                if path and os.path.isfile(path):
                    result[rp] = path
        except Exception as e:
            print(f"[AssetCollector] RedshiftProxy 收集失败: {e}")

    @staticmethod
    def collect_reference_files(associated_objects: List[str]) -> Dict[str, str]:
        ref_to_path: Dict[str, str] = {}
        if not _IN_MAYA:
            return ref_to_path

        try:
            ref_nodes = cmds.file(query=True, reference=True) or []
            for rn in ref_nodes:
                if not cmds.objExists(rn):
                    continue
                try:
                    filename = cmds.referenceQuery(rn, filename=True)
                    if filename and os.path.isfile(filename):
                        ref_to_path[rn] = filename
                except Exception:
                    continue
        except Exception as e:
            print(f"[AssetCollector] 引用收集失败: {e}")

        return ref_to_path

    @staticmethod
    def copy_collected_files(collected: Dict[str, Dict], dest_dir: str) -> Dict[str, Dict]:
        target_map: Dict[str, Dict] = {
            "caches": {},
            "proxies": {},
            "references": {},
        }

        for category in ("caches", "proxies", "references"):
            cat_dir = os.path.join(dest_dir, category)
            os.makedirs(cat_dir, exist_ok=True)
            for node_name, src_path in collected.get(category, {}).items():
                if not os.path.isfile(src_path):
                    print(f"[AssetCollector] 跳过不存在的文件: {src_path}")
                    continue
                try:
                    base = os.path.basename(src_path)
                    dst = os.path.join(cat_dir, base)
                    if os.path.normcase(os.path.abspath(src_path)) != os.path.normcase(os.path.abspath(dst)):
                        shutil.copy2(src_path, dst)
                    target_map[category][node_name] = os.path.join(category, base).replace("\\", "/")
                    print(f"[AssetCollector] 已复制 {category}: {src_path} -> {dst}")
                except Exception as e:
                    print(f"[AssetCollector] 复制失败 {src_path}: {e}")
        return target_map
