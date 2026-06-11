import os
import shutil
from typing import Dict, List, Optional

try:
    import maya.cmds as cmds
    _IN_MAYA = True
except ImportError:
    _IN_MAYA = False

DEBUG = True


def _dbg(msg: str):
    if DEBUG:
        print(f"[AssetCollector] {msg}")


def _resolve_frame_pattern(path: str) -> str:
    if not os.path.isabs(path):
        return path
    dir_name = os.path.dirname(path)
    base = os.path.basename(path)
    if '####' in base or '%' in base:
        import glob as _glob
        pattern = path.replace('####', '*').replace('%04d', '*').replace('%4d', '*')
        candidates = sorted(_glob.glob(pattern))
        if candidates:
            return candidates[0]
    return path

def _get_file_path(attr_value) -> Optional[str]:
    if isinstance(attr_value, (list, tuple)):
        for v in attr_value:
            if v and isinstance(v, str):
                expanded = os.path.expandvars(os.path.expanduser(v))
                resolved = _resolve_frame_pattern(expanded)
                if os.path.isfile(resolved):
                    return expanded
                if os.path.isdir(os.path.dirname(expanded)):
                    return expanded
        return None
    if attr_value and isinstance(attr_value, str):
        expanded = os.path.expandvars(os.path.expanduser(attr_value))
        resolved = _resolve_frame_pattern(expanded)
        if os.path.isfile(resolved):
            return expanded
        if os.path.isdir(os.path.dirname(expanded)):
            return expanded
    return None


class AssetCollector:

    @staticmethod
    def collect_all(associated_objects: List[str]) -> Dict[str, Dict[str, str]]:
        result = {
            "caches": {},
            "proxies": {},
            "references": {},
        }
        if not _IN_MAYA:
            _dbg("不在 Maya 环境，跳过收集")
            return result

        _dbg(f"开始收集，关联对象 {len(associated_objects)} 个: {associated_objects}")
        result["caches"] = AssetCollector.collect_cache_files(associated_objects)
        result["proxies"] = AssetCollector.collect_proxy_files(associated_objects)
        result["references"] = AssetCollector.collect_reference_files(associated_objects)
        _dbg(f"收集完成: 缓存={len(result['caches'])}, 代理={len(result['proxies'])}, 引用={len(result['references'])}")
        return result

    @staticmethod
    def collect_cache_files(associated_objects: List[str]) -> Dict[str, str]:
        node_to_path: Dict[str, str] = {}
        if not _IN_MAYA:
            return node_to_path

        for obj in associated_objects or []:
            if not cmds.objExists(obj):
                _dbg(f"跳过不存在的对象: {obj}")
                continue
            _dbg(f"--- 检查对象: {obj} ---")
            AssetCollector._collect_alembic(obj, node_to_path)
            AssetCollector._collect_gpu_cache(obj, node_to_path)
            AssetCollector._collect_ncache(obj, node_to_path)

        return node_to_path

    @staticmethod
    def _get_all_shapes(obj: str) -> List[str]:
        shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
        descendants = cmds.listRelatives(obj, allDescendents=True, fullPath=True) or []
        for d in descendants:
            sub_shapes = cmds.listRelatives(d, shapes=True, fullPath=True) or []
            for s in sub_shapes:
                if s not in shapes:
                    shapes.append(s)
        return shapes

    @staticmethod
    def _collect_alembic(obj: str, result: Dict[str, str]):
        try:
            descendants = cmds.listRelatives(obj, allDescendents=True, fullPath=True) or []
            descendants.append(obj)
            _dbg(f"  Alembic: DAG 扫描 {len(descendants)} 个节点 (含自身)")

            seen = set()
            found_shapes = []

            for d in descendants:
                shapes = cmds.listRelatives(d, shapes=True, fullPath=True) or []
                for s in shapes:
                    nt = cmds.nodeType(s)
                    if nt == 'AlembicNode':
                        found_shapes.append((d, s))

            _all_obj_shapes = AssetCollector._get_all_shapes(obj)
            _dbg(f"  Alembic: 对象下共 {len(_all_obj_shapes)} 个 shape")

            if not found_shapes:
                _dbg(f"  Alembic: DAG 未发现, 尝试 DG 连接...")
                for s in _all_obj_shapes:
                    conns = cmds.listConnections(s, type='AlembicNode') or []
                    for a_node in set(conns):
                        if cmds.objExists(a_node) and cmds.nodeType(a_node) == 'AlembicNode':
                            _dbg(f"    DG 连接: {s} -> {a_node}")
                            found_shapes.append(('(DG)', a_node))

            if not found_shapes:
                _dbg(f"  Alembic: DG 未发现, 全场景扫描 AlembicNode...")
                all_alembic = cmds.ls(type='AlembicNode')
                _dbg(f"    场景共 {len(all_alembic)} 个 AlembicNode")
                obj_shapes_set = set(_all_obj_shapes)
                for a_node in all_alembic:
                    if not cmds.objExists(a_node):
                        continue
                    dests = cmds.listConnections(a_node, d=True, s=False) or []
                    matched = any(d in obj_shapes_set for d in dests)
                    _dbg(f"    {a_node}: 下游={dests[:5]}{'...' if len(dests)>5 else ''}, 匹配={matched}")
                    if matched:
                        found_shapes.append(('(scene)', a_node))

            _dbg(f"  Alembic: 共发现 {len(found_shapes)} 个 AlembicNode")

            for parent, s in found_shapes:
                if s in seen:
                    continue
                seen.add(s)

                all_attrs = cmds.listAttr(s)
                _dbg(f"    shape={s}, parent={parent}, 属性: {all_attrs}")

                for attr in ('abc_File', 'cacheName', 'cacheFileName', 'fileName'):
                    full_attr = s + '.' + attr
                    try:
                        val = cmds.getAttr(full_attr)
                        _dbg(f"      getAttr({full_attr}) = {val!r}")
                        path = _get_file_path(val)
                        if path:
                            _dbg(f"      => 找到文件: {path}")
                            result[s] = path
                            break
                        else:
                            _dbg(f"      => 值 {val!r} 不是有效文件路径")
                    except Exception as ex:
                        _dbg(f"      getAttr({full_attr}) 异常: {ex}")

                if s not in result:
                    _dbg(f"    => 未能从 {s} 提取到文件路径")

        except Exception as e:
            _dbg(f"  Alembic 收集异常 ({obj}): {e}")
            import traceback
            traceback.print_exc()

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
                            val = cmds.getAttr(s + '.' + attr)
                            path = _get_file_path(val)
                            if path:
                                _dbg(f"  GPU Cache: {s} -> {path}")
                                result[s] = path
                                break
                        except Exception:
                            continue
        except Exception as e:
            _dbg(f"  GPU Cache 收集异常: {e}")

    @staticmethod
    def _collect_ncache(obj: str, result: Dict[str, str]):
        try:
            cf_nodes = cmds.listConnections(obj, type='cacheFile')
            if not cf_nodes:
                return
            for cf in set(cf_nodes):
                if not cmds.objExists(cf):
                    continue
                for attr in ('.cachePath', '.path'):
                    try:
                        val = cmds.getAttr(cf + attr)
                        path = _get_file_path(val)
                        if path:
                            _dbg(f"  nCache: {cf} -> {path}")
                            result[cf] = path
                            break
                    except Exception:
                        continue
        except Exception as e:
            _dbg(f"  nCache 收集异常 ({obj}): {e}")

    @staticmethod
    def collect_proxy_files(associated_objects: List[str]) -> Dict[str, str]:
        node_to_path: Dict[str, str] = {}
        if not _IN_MAYA:
            return node_to_path

        for obj in associated_objects or []:
            if not cmds.objExists(obj):
                continue
            _dbg(f"  代理收集: 检查 {obj}")
            AssetCollector._collect_arnold_standin(obj, node_to_path)
            AssetCollector._collect_vray_proxy(obj, node_to_path)
            AssetCollector._collect_redshift_proxy(obj, node_to_path)

        return node_to_path

    @staticmethod
    def _collect_arnold_standin(obj: str, result: Dict[str, str]):
        AssetCollector._collect_or_scene_scan(obj, 'aiStandIn',
            ('dso', 'filename', 'fileName', 'cacheFileName'), result)

    @staticmethod
    def _collect_vray_proxy(obj: str, result: Dict[str, str]):
        AssetCollector._collect_or_scene_scan(obj, 'VRayProxy',
            ('fileName', 'filename', 'dso', 'cacheFileName'), result)

    @staticmethod
    def _collect_redshift_proxy(obj: str, result: Dict[str, str]):
        AssetCollector._collect_or_scene_scan(obj, 'RedshiftProxyMesh',
            ('fileName', 'filename', 'cacheFileName', 'cacheName',
             'exoFile', 'rsProxyFile', 'proxyFile'), result)

    @staticmethod
    def _collect_or_scene_scan(obj: str, node_type: str, attrs: tuple, result: Dict[str, str]):
        old_len = len(result)
        AssetCollector._collect_by_shape_type(obj, node_type, attrs, result)
        found = len(result) - old_len
        _dbg(f"    [{node_type}] DAG 扫描找到 {found} 个")
        if found == 0:
            _dbg(f"    [{node_type}] DAG 未发现, 全场景扫描...")
            all_nodes = cmds.ls(type=node_type)
            _dbg(f"      场景共 {len(all_nodes)} 个 {node_type}")
            obj_shapes = set(AssetCollector._get_all_shapes(obj))
            for p_node in all_nodes:
                if not cmds.objExists(p_node):
                    continue
                dests = cmds.listConnections(p_node, d=True, s=False) or []
                connected = any(d in obj_shapes for d in dests)
                _dbg(f"      {p_node}: 下游={dests[:3]}{'...' if len(dests)>3 else ''}, 关联={connected}")
                if not connected:
                    continue
                for attr in attrs:
                    val = None
                    try:
                        val = cmds.getAttr(p_node + '.' + attr, asString=True)
                    except Exception:
                        try:
                            val = cmds.getAttr(p_node + '.' + attr)
                        except Exception:
                            continue
                    if val:
                        _dbg(f"        getAttr({p_node}.{attr}) = {val!r}")
                        path = _get_file_path(val)
                        if path:
                            _dbg(f"        => 找到文件: {path}")
                            result[p_node] = path
                            break

    @staticmethod
    def _collect_by_shape_type(obj: str, node_type: str, attrs: tuple, result: Dict[str, str]):
        try:
            descendants = cmds.listRelatives(obj, allDescendents=True, fullPath=True) or []
            descendants.append(obj)
            _dbg(f"    [{node_type}] DAG 扫描 {len(descendants)} 个节点")

            seen = set()
            all_shapes = AssetCollector._get_all_shapes(obj)
            _dbg(f"    [{node_type}] 对象下共 {len(all_shapes)} 个 shape")
            shape_types = sorted(set(cmds.nodeType(s) for s in all_shapes))
            _dbg(f"    [{node_type}] shape 类型: {shape_types}")

            for d in descendants:
                shapes = cmds.listRelatives(d, shapes=True, fullPath=True) or []
                for s in shapes:
                    nt = cmds.nodeType(s)
                    if s in seen or nt != node_type:
                        continue
                    seen.add(s)
                    _dbg(f"    [{node_type}] 发现 shape: {s}")
                    for attr in attrs:
                        try:
                            val = cmds.getAttr(s + '.' + attr)
                            _dbg(f"      getAttr({s}.{attr}) = {val!r}")
                            path = _get_file_path(val)
                            if path:
                                _dbg(f"      => 找到文件: {path}")
                                result[s] = path
                                break
                        except Exception as ex:
                            _dbg(f"      getAttr({s}.{attr}) 异常: {ex}")

                    if s not in result:
                        _dbg(f"      => 未能从 {s} 提取到文件路径")
        except Exception as e:
            _dbg(f"  {node_type} 收集异常: {e}")

    @staticmethod
    def collect_reference_files(associated_objects: List[str]) -> Dict[str, str]:
        ref_to_path: Dict[str, str] = {}
        if not _IN_MAYA:
            return ref_to_path

        try:
            ref_nodes = cmds.file(query=True, reference=True) or []
            _dbg(f"  引用: 场景共 {len(ref_nodes)} 个引用节点")
            for rn in ref_nodes:
                if not cmds.objExists(rn):
                    continue
                try:
                    filename = cmds.referenceQuery(rn, filename=True)
                    if filename and os.path.isfile(filename):
                        _dbg(f"  引用: {rn} -> {filename}")
                        ref_to_path[rn] = filename
                except Exception:
                    continue
        except Exception as e:
            _dbg(f"  引用收集异常: {e}")

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
                if '####' in src_path or '%0' in src_path:
                    target_map[category][node_name] = AssetCollector._copy_frame_sequence(
                        src_path, cat_dir)
                else:
                    target_map[category][node_name] = AssetCollector._copy_single_file(
                        src_path, cat_dir)
                    if target_map[category][node_name]:
                        _dbg(f"  已复制 {category}: {src_path}")
        return target_map

    @staticmethod
    def _copy_single_file(src: str, dest_dir: str) -> str:
        if not os.path.isfile(src):
            _dbg(f"  跳过不存在的文件: {src}")
            return ""
        try:
            base = os.path.basename(src)
            dst = os.path.join(dest_dir, base)
            if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
                shutil.copy2(src, dst)
            return base.replace("\\", "/")
        except Exception as e:
            _dbg(f"  复制失败 {src}: {e}")
            return ""

    @staticmethod
    def _copy_frame_sequence(src_pattern: str, dest_dir: str) -> str:
        import glob as _glob
        pattern = src_pattern.replace('####', '*').replace('%04d', '*').replace('%4d', '*')
        files = sorted(_glob.glob(pattern))
        if not files:
            _dbg(f"  跳过不存在的序列: {src_pattern} (pattern={pattern})")
            return ""
        base = os.path.basename(src_pattern)
        if '####' in base:
            base = base.replace('####', '####')
        elif '%04d' in base:
            base = base.replace('%04d', '%04d')
        elif '%4d' in base:
            base = base.replace('%4d', '%4d')
        _dbg(f"  复制帧序列: {src_pattern} -> {len(files)} 帧")
        for f in files:
            frame_name = os.path.basename(f)
            dst = os.path.join(dest_dir, frame_name)
            if os.path.normcase(os.path.abspath(f)) != os.path.normcase(os.path.abspath(dst)):
                shutil.copy2(f, dst)
        return base.replace("\\", "/")
