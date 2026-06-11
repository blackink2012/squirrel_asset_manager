import os
import shutil
from typing import Dict, List, Optional

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
            descendants = cmds.listRelatives(obj, allDescendents=True, fullPath=True) or []
            descendants.append(obj)
            seen = set()
            for d in descendants:
                shapes = cmds.listRelatives(d, shapes=True, fullPath=True) or []
                for s in shapes:
                    if s in seen or cmds.nodeType(s) != 'AlembicNode':
                        continue
                    seen.add(s)
                    for attr in ('abc_File', 'cacheName', 'cacheFileName', 'fileName'):
                        try:
                            path = cmds.getAttr(s + '.' + attr)
                            if path and os.path.isfile(path):
                                result[s] = path
                                break
                        except Exception:
                            continue
        except Exception as e:
            print(f"[AssetCollector] Alembic 收集失败 ({obj}): {e}")

    @staticmethod
    def _collect_gpu_cache(obj: str, result: Dict[str, str]):
        try:
            descendants = cmds.listRelatives(obj, allDescendents=True, fullPath=True) or []
            descendants.append(obj)
            seen = set()
            for d in descendants:
                shapes = cmds.listRelatives(d, shapes=True, fullPath=True) or []
                for s in shapes:
                    if s in seen or cmds.nodeType(s) != 'gpuCache':
                        continue
                    seen.add(s)
                    for attr in ('cacheFileName', 'cacheFile', 'cacheName'):
                        try:
                            path = cmds.getAttr(s + '.' + attr)
                            if path and os.path.isfile(path):
                                result[s] = path
                                break
                        except Exception:
                            continue
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

        for obj in associated_objects or []:
            if not cmds.objExists(obj):
                continue
            AssetCollector._collect_arnold_standin(obj, node_to_path)
            AssetCollector._collect_vray_proxy(obj, node_to_path)
            AssetCollector._collect_redshift_proxy(obj, node_to_path)

        return node_to_path

    @staticmethod
    def _collect_arnold_standin(obj: str, result: Dict[str, str]):
        AssetCollector._collect_by_shape_type(obj, 'aiStandIn',
            ('dso', 'filename', 'fileName', 'cacheFileName'), result)

    @staticmethod
    def _collect_vray_proxy(obj: str, result: Dict[str, str]):
        AssetCollector._collect_by_shape_type(obj, 'VRayProxy',
            ('fileName', 'filename', 'dso', 'cacheFileName'), result)

    @staticmethod
    def _collect_redshift_proxy(obj: str, result: Dict[str, str]):
        AssetCollector._collect_by_shape_type(obj, 'RedshiftProxyMesh',
            ('fileName', 'filename', 'cacheFileName'), result)

    @staticmethod
    def _collect_by_shape_type(obj: str, node_type: str, attrs: tuple, result: Dict[str, str]):
        try:
            descendants = cmds.listRelatives(obj, allDescendents=True, fullPath=True) or []
            descendants.append(obj)
            seen = set()
            for d in descendants:
                shapes = cmds.listRelatives(d, shapes=True, fullPath=True) or []
                for s in shapes:
                    if s in seen or cmds.nodeType(s) != node_type:
                        continue
                    seen.add(s)
                    for attr in attrs:
                        try:
                            path = cmds.getAttr(s + '.' + attr)
                            if path and os.path.isfile(path):
                                result[s] = path
                                break
                        except Exception:
                            continue
        except Exception as e:
            print(f"[AssetCollector] {node_type} 收集失败: {e}")

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
