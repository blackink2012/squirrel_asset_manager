"""
Maya材质属性映射工具 - PySide6 版本
支持两列属性映射、.mmap预设保存/加载
适配 Maya 2025+
"""

import os
import sys
import json
import threading
import requests
from functools import partial

# 获取脚本所在目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 预设目录：相对于脚本目录的 Assets/material_mapper_presets
PRESET_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "Assets", "material_mapper_presets"))

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 复用主 UI 的 AI 分析器配置（服务商预设 / 模型列表 / API 调用）
try:
    from core.ai_analyzer import AIAnalyzer
except ImportError:
    try:
        from squirrel_asset_manager.core.ai_analyzer import AIAnalyzer
    except ImportError:
        AIAnalyzer = None

# 尝试导入 PySide6
try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFileDialog, QMessageBox, QHeaderView
    import shiboken6
    PYSIDE_VERSION = 6
    print("使用 PySide6 版本")
except ImportError as e:
    # 如果 PySide6 导入失败，尝试 PySide2
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
        from PySide2.QtCore import Qt
        from PySide2.QtWidgets import QFileDialog, QMessageBox, QHeaderView
        import shiboken2
        PYSIDE_VERSION = 2
        print("使用 PySide2 版本")
    except ImportError:
        raise ImportError("需要 PySide6 或 PySide2")

# 统一shiboken导入
if PYSIDE_VERSION == 6:
    import shiboken6 as shiboken
else:
    import shiboken2 as shiboken

try:
    import maya.cmds as cmds
    import maya.OpenMayaUI as omui
    IN_MAYA = True
except ImportError:
    IN_MAYA = False


_T = None
_help_path = lambda p: p
try:
    # 优先包内相对导入，避免误命中 sys.path 中其他副本（如独立库 release 目录）的顶层 utils 包
    from ..utils.i18n import t as _T, help_path as _hpath
    _help_path = _hpath
except ImportError:
    try:
        from squirrel_asset_manager.utils.i18n import t as _T, help_path as _hpath
        _help_path = _hpath
    except ImportError:
        try:
            from utils.i18n import t as _T, help_path as _hpath
            _help_path = _hpath
        except ImportError:
            _T = None

def t(key, **kwargs):
    return _T(key, **kwargs) if _T is not None else (key.format(**kwargs) if kwargs else key)


# ---- 字体 DPI 适配：以 4K 27 英寸屏（约 163 DPI）为视觉基准 ----
# 不同 DPI 屏幕按比例缩放字号，保证视觉大小一致（4K 正常、2K 偏大的问题）。
# 如基准屏尺寸不是 27 英寸，可调整 REFERENCE_DPI 值。
REFERENCE_DPI = 163.0


def _font_scale():
    """返回当前屏幕相对基准 DPI 的字体缩放系数"""
    try:
        app = QtWidgets.QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        dpi = float(screen.physicalDotsPerInch()) if screen is not None else REFERENCE_DPI
        if dpi <= 0:
            dpi = REFERENCE_DPI
        return max(0.6, min(dpi / REFERENCE_DPI, 1.5))
    except Exception:
        return 1.0


FONT_SCALE = _font_scale()


def _fs(px):
    """按 DPI 缩放字号，最小 8px 保证可读"""
    return max(8, int(px * FONT_SCALE))


def _sc(px):
    """按 DPI 缩放尺寸（按钮 padding / min-height / min-width 等），最小 1px"""
    return max(1, int(px * FONT_SCALE))


def _font_style(text):
    """将样式文本中的 @FONT_nn@（字号）与 @SIZE_nn@（尺寸）占位符按 DPI 缩放"""
    import re

    def _repl(m):
        tag, val = m.group(1), int(m.group(2))
        return str(_fs(val)) if tag == "FONT" else str(_sc(val))

    return re.sub(r'@(FONT|SIZE)_(\d+)@', _repl, text)


# ==================== 转换函数列表 ====================
# 转换函数字典 - 包含中英文名称映射（仅用于UI显示，不包含实际算法）
MATERIAL_CONVERSION_FUNCTIONS = {
    # RGB通道处理
    "RGB转单通道": "rgb_to_channel",
    "rgb_to_channel": "rgb_to_channel",
    "RGB取红": "rgb_to_red",
    "rgb_to_red": "rgb_to_red",
    "RGB取绿": "rgb_to_green",
    "rgb_to_green": "rgb_to_green",
    "RGB取蓝": "rgb_to_blue",
    "rgb_to_blue": "rgb_to_blue",
    "RGB转灰度": "rgb_to_grayscale",
    "rgb_to_grayscale": "rgb_to_grayscale",
    
    # 透明度/透射
    "透明度转透射": "transparency_to_transmission",
    "透明度转透射权重": "transparency_to_transmission",
    "transparency_to_transmission": "transparency_to_transmission",
    
    # 粗糙度转换
    "光泽度转粗糙度": "shininess_to_roughness",
    "shininess_to_roughness": "shininess_to_roughness",
    "Blinn高光锐度转粗糙度": "blinn_cosPower_to_roughness",
    "blinn_cosPower_to_roughness": "blinn_cosPower_to_roughness",
    "Phong光泽度转粗糙度": "phong_shi_to_roughness",
    "phong_shi_to_roughness": "phong_shi_to_roughness",
    "漫反射粗糙度转PBR粗糙度": "diffuse_roughness_to_roughness",
    "diffuse_roughness_to_roughness": "diffuse_roughness_to_roughness",
    
    # PBR参数
    "从镜面反射估算金属度": "metalness_from_specular",
    "metalness_from_specular": "metalness_from_specular",
    "镜面反射强度转权重": "specular_to_specular_weight",
    "specular_to_specular_weight": "specular_to_specular_weight",
    "折射率转F0": "ior_to_f0",
    "ior_to_f0": "ior_to_f0",
    "F0转镜面反射颜色": "f0_to_specular_color",
    "f0_to_specular_color": "f0_to_specular_color",
    
    # 其他材质参数
    "自发光转发光亮度": "emission_to_emission_luminance",
    "emission_to_emission_luminance": "emission_to_emission_luminance",
    "半透明度转次表面散射": "translucence_to_subsurface",
    "translucence_to_subsurface": "translucence_to_subsurface",
    "薄膜厚度转涂层权重": "thin_film_thickness_to_weight",
    "thin_film_thickness_to_weight": "thin_film_thickness_to_weight",
    "反转值": "invert_value",
    "invert_value": "invert_value",
    "限制范围": "clamp",
    "clamp": "clamp",
    
    # 颜色运算
    "颜色乘标量": "color_mul_scalar",
    "color_mul_scalar": "color_mul_scalar",
    "颜色相加": "color_add",
    "color_add": "color_add",
    "颜色插值": "color_lerp",
    "color_lerp": "color_lerp",
    
    # 反向转换函数（反向映射时自动切换，见 TRANSFORM_REVERSE）
    "PBR粗糙度转漫反射粗糙度": "roughness_to_diffuse_roughness",
    "roughness_to_diffuse_roughness": "roughness_to_diffuse_roughness",
    "粗糙度转光泽度": "roughness_to_shininess",
    "roughness_to_shininess": "roughness_to_shininess",
    "粗糙度转Blinn高光锐度": "roughness_to_blinn_cosPower",
    "roughness_to_blinn_cosPower": "roughness_to_blinn_cosPower",
    "粗糙度转Phong光泽度": "roughness_to_phong_shi",
    "roughness_to_phong_shi": "roughness_to_phong_shi",
    "F0转折射率": "f0_to_ior",
    "f0_to_ior": "f0_to_ior",
    "镜面反射颜色转F0": "specular_color_to_f0",
    "specular_color_to_f0": "specular_color_to_f0",
    "颜色除标量": "color_div_scalar",
    "color_div_scalar": "color_div_scalar",
    "透射转透明度": "transmission_to_transparency",
    "transmission_to_transparency": "transmission_to_transparency",
    "涂层权重转薄膜厚度": "weight_to_thin_film_thickness",
    "weight_to_thin_film_thickness": "weight_to_thin_film_thickness"
}

# 转换函数的默认参数（选择/切换转换函数时自动填入参数列，用户可修改）
# 键为英文函数名，值为该函数可配置的参数字典（与转换工具的默认签名一致）
TRANSFORM_DEFAULT_PARAMS = {
    "rgb_to_channel": {"channel": "r"},
    "color_mul_scalar": {"scalar": 1.0},
    "color_div_scalar": {"scalar": 1.0},
    "color_add": {"color2": [1.0, 1.0, 1.0]},
    "clamp": {"min_val": 0.0, "max_val": 1.0},
    "color_lerp": {"color2": [1.0, 1.0, 1.0], "t": 0.5},
    "shininess_to_roughness": {"glossiness_mode": False},
    "f0_to_specular_color": {"diffuse_color": None},
}

# 各转换函数（英文名）接受的可选参数名列表（不含主输入值）。
# 反向映射时，当前行参数中「反向函数也接受的同名参数」会原样带过去，
# 保证往返转换（A→B→A）数值一致（如 颜色乘标量 {scalar:2} ⟷ 颜色除标量 {scalar:2}）。
TRANSFORM_PARAM_NAMES = {
    "rgb_to_channel": ["channel"],
    "color_mul_scalar": ["scalar"],
    "color_div_scalar": ["scalar"],
    "color_add": ["color2"],
    "color_lerp": ["color2", "t"],
    "clamp": ["min_val", "max_val"],
    "shininess_to_roughness": ["glossiness_mode"],
    "f0_to_specular_color": ["diffuse_color"],
    # 反向函数（多数无可选参数）
    "roughness_to_diffuse_roughness": [],
    "roughness_to_shininess": [],
    "roughness_to_blinn_cosPower": [],
    "roughness_to_phong_shi": [],
    "f0_to_ior": [],
    "specular_color_to_f0": [],
    "transmission_to_transparency": [],
    "weight_to_thin_film_thickness": [],
}

# 转换函数（英文名）↔ 反向转换函数（英文名），反向映射时自动切换；反复反向可来回切换
# 与材质转换工具的 TRANSFORM_REVERSE 保持一致
TRANSFORM_REVERSE = {
    # 正向 → 反向
    "diffuse_roughness_to_roughness": "roughness_to_diffuse_roughness",
    "shininess_to_roughness": "roughness_to_shininess",
    "blinn_cosPower_to_roughness": "roughness_to_blinn_cosPower",
    "phong_shi_to_roughness": "roughness_to_phong_shi",
    "ior_to_f0": "f0_to_ior",
    "f0_to_specular_color": "specular_color_to_f0",
    "color_mul_scalar": "color_div_scalar",
    "color_div_scalar": "color_mul_scalar",
    "transparency_to_transmission": "transmission_to_transparency",
    "thin_film_thickness_to_weight": "weight_to_thin_film_thickness",
    "invert_value": "invert_value",
    # 反向 → 正向
    "roughness_to_diffuse_roughness": "diffuse_roughness_to_roughness",
    "roughness_to_shininess": "shininess_to_roughness",
    "roughness_to_blinn_cosPower": "blinn_cosPower_to_roughness",
    "roughness_to_phong_shi": "phong_shi_to_roughness",
    "f0_to_ior": "ior_to_f0",
    "specular_color_to_f0": "f0_to_specular_color",
    "transmission_to_transparency": "transparency_to_transmission",
    "weight_to_thin_film_thickness": "thin_film_thickness_to_weight",
}

# 获取唯一的中文名称列表（去重，按类别排序）
def get_conversion_function_options():
    """获取转换函数选项列表（优先显示中文名称）"""
    seen = set()
    options = ["(无)"]
    
    # 按类别添加
    categories = [
        "RGB通道处理", ["RGB转单通道", "RGB取红", "RGB取绿", "RGB取蓝", "RGB转灰度"],
        "透明度/透射", ["透明度转透射", "透明度转透射权重"],
        "粗糙度转换", ["光泽度转粗糙度", "Blinn高光锐度转粗糙度", "Phong光泽度转粗糙度", "漫反射粗糙度转PBR粗糙度"],
        "PBR参数", ["从镜面反射估算金属度", "镜面反射强度转权重", "折射率转F0", "F0转镜面反射颜色"],
        "其他材质参数", ["自发光转发光亮度", "半透明度转次表面散射", "薄膜厚度转涂层权重", "反转值", "限制范围"],
        "颜色运算", ["颜色乘标量", "颜色相加", "颜色插值"],
        "反向转换", ["PBR粗糙度转漫反射粗糙度", "粗糙度转光泽度", "粗糙度转Blinn高光锐度", "粗糙度转Phong光泽度", "F0转折射率", "镜面反射颜色转F0", "颜色除标量", "透射转透明度", "涂层权重转薄膜厚度"]
    ]
    
    for i in range(0, len(categories), 2):
        cat_name = categories[i]
        funcs = categories[i + 1]
        for func_name in funcs:
            if func_name not in seen:
                options.append(func_name)
                seen.add(func_name)
    
    return options


# ==================== AI 映射上下文（精简版，约 1K tokens） ====================
# 硬性规则 + 已知反例，注入 _build_prompt 防止本地模型生成垃圾映射
# （颜色子通道、语义错乱、编造属性、类型/枚举错配等）
MAPPING_CONTEXT_RULES = """【任务】为每个源材质属性推断最合适的目标属性与转换函数，输出 JSON 数组（仅 JSON，无注释、无 Markdown）。

【材质映射硬性规则】
1. 目标属性为空 "" 表示跳过该源属性（无合适目标时必须填空）。
2. 颜色必须映射到父属性，绝不写子通道：目标以 R/G/B 结尾且父属性存在（如 diffuseColorR）会被引擎无条件跳过，导致颜色/贴图丢失。合法颜色目标示例：color / diffuseColor / reflectionColor / refractionColor / fogColor / translucencyColor / sheenColor / coatColor / illumColor / opacityMap / base_color / refl_color / refr_color / coat_color / ms_color。
3. 源属性必须真实存在，不得编造（如 aiMatteColorA 不存在）。
4. 类型必须匹配：布尔→布尔、浮点→浮点、颜色→颜色；枚举仅当两侧含义对应才映射，不确定就跳过。
5. transform 只能是下面"可用转换函数"列出的英文标识之一，无需转换填空字符串 ""。

【已知反例（禁止模仿）】
- baseColor→diffuseColorR：颜色子通道目标，必被引擎跳过
- aiMatteColor→illumColor / aiMatteColor→sheen_color：遮罩≠自发光/绒毛色
- indirectDiffuse→diffuseColorG：GI 权重≠漫反射绿通道
- subsurfaceScale→bumpDeltaScale：SSS 半径≠凹凸增量
- subsurfaceAnisotropy→anisotropyDerivation：SSS 各向异性≠高光方向枚举
- subsurfaceType→translucencyMode：两侧枚举含义不对应
- subsurfaceRadius(颜色)→ms_radius_scale(标量)：应映射 ms_radius
- coatAffectRoughness→reflectionAffectAlpha：涂层粗糙度影响≠反射 Alpha
- thinFilmWeight→refr_thin_walled：薄膜权重≠薄壁开关
- coatDarkening→coat_direct：暗化≠直射光权重
- transmissionDispersion(bool)→refrDispersionAbbe(float)：应映射 refrDispersionOn"""


class OllamaMapper:
    """AI 属性映射客户端 - 支持 Ollama / DeepSeek / 通义千问

    统一通过 OpenAI 兼容的 /chat/completions 接口（纯文本任务）让大模型
    根据源属性列表与目标属性列表，推断每行的目标属性、转换函数与默认值。
    """

    DEFAULT_HOST = "http://localhost:11434"
    DEFAULT_MODEL = "qwen3-vl:8b"

    # 服务商预设：直接复用主 UI 的 AIAnalyzer.PROVIDERS，保证与主界面完全一致（不再单独维护）
    PROVIDERS = AIAnalyzer.PROVIDERS if AIAnalyzer else {}

    def __init__(self, host=None, model=None, provider=None, api_key=None, base_url=None):
        settings = self._load_settings()
        self.provider = provider or settings.get("ai_provider") or "ollama"
        if self.provider not in self.PROVIDERS:
            self.provider = "ollama"
        cfg = self.PROVIDERS[self.provider]

        if api_key is not None:
            self.api_key = api_key
        else:
            # 各供应商独立存储 API Key，优先取当前供应商的 Key
            try:
                from squirrel_asset_manager.utils.settings import get_ai_api_key
                self.api_key = get_ai_api_key(settings, self.provider)
            except ImportError:
                try:
                    from utils.settings import get_ai_api_key
                    self.api_key = get_ai_api_key(settings, self.provider)
                except ImportError:
                    self.api_key = settings.get("ai_api_key", "")

        if base_url:
            self.base_url = base_url.rstrip("/")
        elif host and self.provider == "ollama":
            self.base_url = host.rstrip("/") + "/v1"
        else:
            saved_base = settings.get("ai_base_url", "")
            # 设置中保存的地址若等于某服务商默认地址，则视为未自定义，改用当前服务商默认
            default_urls = {
                c["base_url"].rstrip("/")
                for c in self.PROVIDERS.values() if c.get("base_url")
            }
            if saved_base and saved_base.rstrip("/") not in default_urls:
                self.base_url = saved_base.rstrip("/")
            else:
                self.base_url = cfg["base_url"]

        if model:
            self.model = model
        elif settings.get("ai_model"):
            self.model = settings.get("ai_model")
        else:
            self.model = cfg["default_model"]

    @staticmethod
    def _load_settings():
        """读取用户设置（失败时返回空字典）"""
        try:
            from squirrel_asset_manager.utils.settings import SettingsManager
            return SettingsManager().load()
        except Exception:
            try:
                from utils.settings import SettingsManager
                return SettingsManager().load()
            except Exception:
                return {}

    def is_available(self):
        """检查当前服务是否可用"""
        if self.provider == "ollama":
            try:
                ollama_base = self.base_url.rstrip("/").rsplit("/v1", 1)[0]
                response = requests.get(f"{ollama_base}/api/tags", timeout=5)
                return response.status_code == 200
            except Exception:
                return False
        return bool(self.api_key)

    def get_available_models(self):
        """获取可用模型列表（Ollama 实时 /api/tags；云端实时 /models，失败回退静态列表）"""
        if self.provider == "ollama":
            try:
                ollama_base = self.base_url.rstrip("/").rsplit("/v1", 1)[0]
                response = requests.get(f"{ollama_base}/api/tags", timeout=10)
                response.raise_for_status()
                models = [m.get("name", "") for m in response.json().get("models", [])]
                return models or [self.model]
            except Exception:
                return [self.model]

        static = list(self.PROVIDERS[self.provider]["models"]) or [self.model]
        fetched = []
        try:
            headers = {"Authorization": f"Bearer {self.api_key or 'ollama'}"}
            response = requests.get(f"{self.base_url}/models", headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            fetched = [m.get("id", "") for m in data.get("data", [])]
        except Exception:
            fetched = []
        if self.provider == "openai":
            # OpenAI /models 会返回嵌入/语音/图像生成等非对话模型，仅保留对话模型
            fetched = [
                m for m in fetched
                if m.startswith("gpt-") or m.startswith("chatgpt-")
                or (len(m) > 1 and m[0] == "o" and m[1].isdigit())
            ]
        # 实时列表与静态列表合并去重（保留静态列表中的视觉模型等，它们可能不在 /models 返回中）
        seen, merged = set(), []
        for m in list(fetched) + static:
            if m and m not in seen:
                seen.add(m)
                merged.append(m)
        return merged or static

    def suggest_mapping(self, source_type, target_type, source_attrs, target_attrs, transform_list):
        """请求 AI 推断属性映射

        Args:
            source_type: 源节点类型
            target_type: 目标节点类型
            source_attrs: 源属性列表 [{"name": str, "type": str}, ...]
            target_attrs: 目标属性列表 [{"name": str, "type": str}, ...]
            transform_list: 可用转换函数列表 [{"chinese": str, "english": str}, ...]

        Returns:
            list: 每项 {"source_attribute", "target_attribute", "transform", "default_value"}
        """
        prompt = self._build_prompt(source_type, target_type, source_attrs, target_attrs, transform_list)
        if self.provider == "ollama":
            return self._suggest_ollama(prompt)
        return self._suggest_openai_compat(prompt)

    def _suggest_ollama(self, prompt):
        """走 Ollama 原生 /api/chat 接口

        OpenAI 兼容接口（/v1/chat/completions）对 qwen3 等思考模型无法可靠关闭思考：
        实测 content 为空、思考全部占用输出（reasoning 字段），JSON 从未生成。
        原生接口的 think=false 可可靠关闭思考，保证结果写入 content。
        """
        ollama_base = self.base_url.rstrip("/").rsplit("/v1", 1)[0]
        url = f"{ollama_base}/api/chat"
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"num_predict": 8192, "temperature": 0.2},
        }
        response = requests.post(url, json=payload, headers=headers, timeout=180)
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        text = message.get("content") or ""
        if not text:
            # 兜底：个别版本思考内容可能放 reasoning / reasoning_content
            text = message.get("reasoning_content") or message.get("reasoning") or ""
        result = self._parse_response(text)
        if not result:
            # 诊断：解析失败时输出响应结构，便于从 Maya Script Editor 定位
            print("[OllamaMapper] 解析结果为空。响应 message keys:",
                  list(message.keys()), "| content len:", len(message.get("content") or ""),
                  "| reasoning len:", len(message.get("reasoning") or ""))
            print("[OllamaMapper] 提取文本片段:", repr(text[:800]))
        return result

    def _suggest_openai_compat(self, prompt):
        """走 OpenAI 兼容接口（DeepSeek / 通义千问）"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key or 'ollama'}",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 8192,
            "stream": False,
        }
        if self.provider == "deepseek":
            # DeepSeek 思考型模型默认开启思考，思考过程会占用输出导致最终结果为空，显式关闭
            payload["thinking"] = {"type": "disabled"}
        elif self.provider == "zhipu":
            model_l = self.model.lower()
            if model_l.startswith("glm-5.2") or model_l.startswith("glm-5.3"):
                # glm-5.2/5.3 始终思考，不支持 thinking.disabled（否则 400 错误码 1210）；
                # 用官方 reasoning_effort 参数降到最低档
                payload["reasoning_effort"] = "low"
            else:
                # glm-4.x / glm-5 / glm-5-turbo / glm-5.1 可显式关闭思考
                payload["thinking"] = {"type": "disabled"}
        elif self.provider == "openai":
            model_l = self.model.lower()
            # o 系列 / GPT-5 推理模型：不支持 temperature / max_tokens，
            # 改用 reasoning_effort（最低档）+ max_completion_tokens
            if model_l.startswith("gpt-5") or (len(model_l) > 1 and model_l[0] == "o" and model_l[1].isdigit()):
                payload.pop("temperature", None)
                payload.pop("max_tokens", None)
                payload["max_completion_tokens"] = 8192
                payload["reasoning_effort"] = "low"
        response = requests.post(url, json=payload, headers=headers, timeout=180)
        response.raise_for_status()
        data = response.json()
        try:
            message = data["choices"][0]["message"]
            text = message.get("content") or ""
            if not text:
                # 兜底：部分思考模型把内容放到 reasoning_content（DeepSeek 系）
                text = message.get("reasoning_content") or ""
        except (KeyError, IndexError, TypeError):
            message = None
            text = data.get("response", "")
        result = self._parse_response(text)
        if not result:
            # 诊断：解析失败时输出响应结构，便于从 Maya Script Editor 定位
            print("[OllamaMapper] 解析结果为空。响应 message keys:",
                  list(message.keys()) if isinstance(message, dict) else "N/A",
                  "| content len:", len(message.get("content") or "") if isinstance(message, dict) else "N/A",
                  "| reasoning len:", len(message.get("reasoning") or "") if isinstance(message, dict) else "N/A")
            print("[OllamaMapper] 提取文本片段:", repr(text[:800]))
        return result

    @staticmethod
    def _build_prompt(source_type, target_type, source_attrs, target_attrs, transform_list):
        """构造映射推断提示词"""
        source_lines = "\n".join(f"- {a['name']} ({a['type']})" for a in source_attrs)
        target_lines = "\n".join(f"- {a['name']} ({a['type']})" for a in target_attrs)
        transform_lines = "\n".join(f"- {c['chinese']} ({c['english']})" for c in transform_list)

        return f"""你是一位 Maya 材质属性映射专家。请为下列源属性逐一推断最合适的目标属性与转换函数。

源节点类型: {source_type}
目标节点类型: {target_type}

源属性列表（名称: 类型）:
{source_lines}

目标节点可用属性列表（名称: 类型）:
{target_lines}

可用转换函数（中文名 (英文标识)）:
{transform_lines}

{MAPPING_CONTEXT_RULES}

规则:
1. 目标属性必须从"目标节点可用属性列表"中选择；如果某个源属性没有合适的目标属性，target_attribute 填空字符串 ""。
2. transform 必须是上面列出的英文标识之一，若无需转换则填空字符串 ""。
3. 只输出 JSON 数组，不要输出任何其他文字、注释或 Markdown 代码块。

输出格式（数组每一项对应一个源属性）:
[{{"source_attribute": "源属性名", "target_attribute": "目标属性名", "transform": "转换函数英文标识"}}]
"""

    @staticmethod
    def _parse_response(text):
        """从模型响应中解析 JSON 数组

        依次尝试文本中每个 "[" 起始的位置，逐步向右扩展 "]"，
        取第一个能完整解析为 JSON 数组且包含有效源属性名的片段，
        以跳过思考内容中的伪 JSON（如推理列举的 [1, 2]）。
        """
        try:
            # 兜底：剥离 qwen3 / deepseek-r1 等思考型模型的思考内容（以 <think> 标签包裹）
            if "<think>" in text:
                idx = text.find("</think>")
                if idx != -1:
                    text = text[idx + len("</think>"):]

            pos = 0
            while True:
                start = text.find("[", pos)
                if start == -1:
                    # 输出响应片段便于诊断（Maya Script Editor 可查看）
                    print(f"[OllamaMapper] 响应中未找到 JSON 数组，响应内容: {repr(text[:600])}")
                    return []
                end = text.find("]", start + 1)
                while end != -1:
                    try:
                        data = json.loads(text[start:end + 1])
                    except Exception:
                        end = text.find("]", end + 1)
                        continue
                    if isinstance(data, list):
                        result = []
                        for item in data:
                            if isinstance(item, dict):
                                row_data = {
                                    "source_attribute": str(item.get("source_attribute", "")),
                                    "target_attribute": str(item.get("target_attribute", "")),
                                    "transform": str(item.get("transform", "")),
                                    "default_value": str(item.get("default_value", "")),
                                }
                                # 透传转换函数参数（AI 响应中携带时）
                                if isinstance(item.get("parameters"), dict):
                                    row_data["parameters"] = item["parameters"]
                                result.append(row_data)
                        # 至少一个项含有效源属性名才算命中，避免解析到思考中的伪 JSON
                        if result and any(r["source_attribute"] for r in result):
                            return result
                    end = text.find("]", end + 1)
                pos = start + 1
        except Exception as e:
            print(f"[OllamaMapper] 解析失败: {e}")
            return []


def get_maya_main_window():
    """获取Maya主窗口指针"""
    try:
        main_window_ptr = omui.MQtUtil.mainWindow()
        if main_window_ptr is not None:
            if PYSIDE_VERSION == 6:
                import shiboken6 as shiboken
                return shiboken.wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
            else:
                import shiboken2 as shiboken
                return shiboken.wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    except Exception as e:
        print(f"获取Maya主窗口失败: {e}")
    return None


class MaterialPropertyMapper(QtWidgets.QDialog):
    """材质属性映射工具主窗口"""

    # AI 模型列表加载完成信号（跨线程回调，参数：模型列表, 是否静默）
    models_ready = QtCore.Signal(str, list, bool)   # (provider, 可用模型列表, silent)

    def __init__(self, parent=None):
        # 尝试获取Maya主窗口作为父窗口
        maya_window = get_maya_main_window()
        if maya_window is not None:
            parent = maya_window

        super(MaterialPropertyMapper, self).__init__(parent)

        self.setWindowTitle(t("qtool.matprop.window_title"))
        self.setMinimumSize(_sc(860), _sc(700))
        self.resize(_sc(1960), _sc(1230))

        # 增大下拉按钮宽度
        self.setStyleSheet(_font_style("""
            QWidget {
                font-size: @FONT_18@px;
            }
            QComboBox {
                min-height: @SIZE_30@px;
                padding: @SIZE_5@px @SIZE_30@px @SIZE_6@px @SIZE_10@px;
                font-size: @FONT_18@px;
            }
            QComboBox::drop-down {
                width: @SIZE_25@px;
            }
            QPushButton {
                min-height: @SIZE_30@px;
                padding: @SIZE_6@px @SIZE_15@px;
                font-size: @FONT_18@px;
            }
            QLineEdit {
                min-height: @SIZE_30@px;
                padding: @SIZE_5@px @SIZE_10@px;
                font-size: @FONT_18@px;
            }
            QTableWidget {
                font-size: @FONT_18@px;
            }
        """))

        self.preset_dir = PRESET_DIR
        if not os.path.exists(self.preset_dir):
            os.makedirs(self.preset_dir)

        # 初始化节点信息
        self.source_node_name = ""
        self.target_node_name = ""

        self.setup_ui()
        self.load_last_preset()

    def _table_resize_event(self, event):
        """表格resize事件，保持列比例"""
        table = self.table
        if table.columnCount() == 0:
            return

        total_width = table.viewport().width()
        if total_width <= 0:
            return

        # 定义列宽比例：选择50px固定, 材质属性/目标属性均分, 转换函数1/6, 默认值1/6, 参数1/6
        fixed_width = 50
        stretch_width = total_width - fixed_width
        col3_width = int(stretch_width * (1/6))
        col4_width = int(stretch_width * (1/6))
        col5_width = int(stretch_width * (1/6))
        remaining = stretch_width - col3_width - col4_width - col5_width
        col1_width = remaining // 2
        col2_width = remaining - col1_width

        # 保存用户手动调整的宽度比例（如果曾经调整过）
        if not hasattr(self, '_user_adjusted_ratio'):
            self._user_adjusted_ratio = False

        if not self._user_adjusted_ratio:
            table.setColumnWidth(0, fixed_width)
            table.setColumnWidth(1, col1_width)
            table.setColumnWidth(2, col2_width)
            table.setColumnWidth(3, col3_width)
            table.setColumnWidth(4, col4_width)
            table.setColumnWidth(5, col5_width)

        # 调用原始resize事件
        if hasattr(table, '_original_resize_event'):
            table._original_resize_event(event)

    def setup_ui(self):
        """创建UI界面（横向主从：顶部工具条 + 左控制面板 / 右工作区 + 底部状态栏）"""
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(_sc(8), _sc(8), _sc(8), _sc(4))
        main_layout.setSpacing(_sc(6))

        # ── 顶部工具条（帮助靠左，属性浏览器 + 预设操作靠右）────────
        toolbar_layout = QtWidgets.QHBoxLayout()
        self.help_btn = QtWidgets.QPushButton(t("btn.help"))
        self.help_btn.clicked.connect(self.show_help_dialog)
        toolbar_layout.addWidget(self.help_btn)
        toolbar_layout.addStretch()
        self.browser_btn = QtWidgets.QPushButton(t("btn.attribute_browser"))
        self.browser_btn.clicked.connect(self.show_attribute_browser)
        self.save_preset_btn = QtWidgets.QPushButton(t("btn.save_preset"))
        self.save_preset_btn.clicked.connect(self.save_preset_dialog)
        self.load_preset_btn = QtWidgets.QPushButton(t("btn.load_preset"))
        self.load_preset_btn.clicked.connect(self.load_preset_dialog)
        self.open_folder_btn = QtWidgets.QPushButton(t("common.open_folder"))
        self.open_folder_btn.clicked.connect(self.open_preset_folder)
        toolbar_layout.addWidget(self.browser_btn)
        toolbar_layout.addWidget(self.save_preset_btn)
        toolbar_layout.addWidget(self.load_preset_btn)
        toolbar_layout.addWidget(self.open_folder_btn)
        main_layout.addLayout(toolbar_layout)

        # ── 主体：左控制面板 | 右工作区 ──────────────────────
        splitter = QtWidgets.QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ===== 左：控制面板 =====
        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(_sc(8))

        # AI 智能映射（可折叠）
        ai_group = QtWidgets.QGroupBox("AI 智能映射")
        ai_group_layout = QtWidgets.QVBoxLayout(ai_group)
        ai_group_layout.setContentsMargins(_sc(6), _sc(4), _sc(6), _sc(6))
        ai_group_layout.setSpacing(_sc(6))

        # 折叠标题行 + 一键映射按钮
        header_row = QtWidgets.QHBoxLayout()
        self.ai_toggle_btn = QtWidgets.QToolButton()
        self.ai_toggle_btn.setText("AI 智能映射")
        self.ai_toggle_btn.setCheckable(True)
        self.ai_toggle_btn.setChecked(True)  # 默认展开
        self.ai_toggle_btn.setArrowType(Qt.DownArrow)
        self.ai_toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.ai_toggle_btn.clicked.connect(self._toggle_ai_panel)
        self.ai_refresh_btn = QtWidgets.QPushButton("刷新")
        self.ai_refresh_btn.clicked.connect(self.refresh_ai_models)
        self.ai_mapping_btn = QtWidgets.QPushButton("⚡ 一键映射")
        self.ai_mapping_btn.clicked.connect(self.ai_mapping_all)
        header_row.addWidget(self.ai_toggle_btn)
        header_row.addStretch()
        header_row.addWidget(self.ai_refresh_btn)
        header_row.addWidget(self.ai_mapping_btn)
        ai_group_layout.addLayout(header_row)

        # 可折叠内容体
        self._ai_body = QtWidgets.QWidget()
        ai_body_layout = QtWidgets.QVBoxLayout(self._ai_body)
        ai_body_layout.setContentsMargins(0, 0, 0, 0)
        ai_body_layout.setSpacing(_sc(6))

        ai_layout = QtWidgets.QHBoxLayout()
        ai_label = QtWidgets.QLabel("AI 服务:")
        self.ai_provider_combo = QtWidgets.QComboBox()
        for key, cfg in OllamaMapper.PROVIDERS.items():
            self.ai_provider_combo.addItem(cfg.get("label", key), key)
        self.ai_provider_combo.currentIndexChanged.connect(self._on_ai_provider_changed)
        ai_layout.addWidget(ai_label)
        ai_layout.addWidget(self.ai_provider_combo, 1)
        ai_body_layout.addLayout(ai_layout)

        ai_model_layout = QtWidgets.QHBoxLayout()
        model_label = QtWidgets.QLabel("AI 模型:")
        self.ai_model_combo = QtWidgets.QComboBox()
        self.ai_model_combo.setEditable(True)
        self.ai_model_combo.addItem(OllamaMapper.DEFAULT_MODEL)
        # 模型名可能很长（如带 tag 的本地模型），独占整行保证完整显示
        self.ai_model_combo.setMinimumWidth(_sc(200))
        ai_model_layout.addWidget(model_label)
        ai_model_layout.addWidget(self.ai_model_combo, 1)
        ai_body_layout.addLayout(ai_model_layout)

        key_layout = QtWidgets.QHBoxLayout()
        key_label = QtWidgets.QLabel("API Key:")
        self.ai_api_key_edit = QtWidgets.QLineEdit()
        self.ai_api_key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.ai_api_key_edit.setPlaceholderText("本地服务无需填写")
        key_layout.addWidget(key_label)
        key_layout.addWidget(self.ai_api_key_edit, 1)
        ai_body_layout.addLayout(key_layout)

        ai_group_layout.addWidget(self._ai_body)
        left_layout.addWidget(ai_group)
        left_layout.addStretch()

        left_panel.setMinimumWidth(_sc(330))
        left_panel.setMaximumWidth(_sc(440))
        splitter.addWidget(left_panel)

        # ===== 右：主工作区 =====
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(_sc(6))

        # 节点类型横向操作行（源 / 目标并排）
        node_bar = QtWidgets.QHBoxLayout()

        source_node_label = QtWidgets.QLabel(t("qtool.matprop.label.source_node_type"))
        self.source_node_type = QtWidgets.QLineEdit()
        self.source_node_type.setReadOnly(True)
        source_node_browse_btn = QtWidgets.QPushButton("加载源材质")
        source_node_browse_btn.clicked.connect(lambda: self.browse_node(True))
        clear_source_btn = QtWidgets.QPushButton("清除")
        clear_source_btn.clicked.connect(self.clear_source_attributes)
        node_bar.addWidget(source_node_label)
        node_bar.addWidget(self.source_node_type, 1)
        node_bar.addWidget(source_node_browse_btn)
        node_bar.addWidget(clear_source_btn)

        node_bar.addSpacing(_sc(12))

        target_node_label = QtWidgets.QLabel(t("qtool.matprop.label.target_node_type"))
        self.target_node_type = QtWidgets.QLineEdit()
        self.target_node_type.setReadOnly(True)
        target_node_browse_btn = QtWidgets.QPushButton("加载目标材质")
        target_node_browse_btn.clicked.connect(lambda: self.browse_node(False))
        clear_target_btn = QtWidgets.QPushButton("清除")
        clear_target_btn.clicked.connect(self.clear_target_attributes)
        node_bar.addWidget(target_node_label)
        node_bar.addWidget(self.target_node_type, 1)
        node_bar.addWidget(target_node_browse_btn)
        node_bar.addWidget(clear_target_btn)

        node_bar.addSpacing(_sc(12))

        load_defaults_btn = QtWidgets.QPushButton("载入默认值")
        load_defaults_btn.setToolTip("载入所选目标节点上各目标属性的当前值到默认值列")
        load_defaults_btn.clicked.connect(self.load_default_values)
        node_bar.addWidget(load_defaults_btn)

        right_layout.addLayout(node_bar)

        # 表格工具条：行操作 + 搜索 + 批处理
        table_toolbar = QtWidgets.QHBoxLayout()

        self.add_btn = QtWidgets.QPushButton(t("btn.add_row"))
        self.add_btn.clicked.connect(lambda: self.add_row_with_options())
        self.remove_btn = QtWidgets.QPushButton("删除选中行")
        self.remove_btn.clicked.connect(self.remove_selected_rows)
        table_toolbar.addWidget(self.add_btn)
        table_toolbar.addWidget(self.remove_btn)
        table_toolbar.addSpacing(_sc(8))

        search_label = QtWidgets.QLabel(t("qtool.matprop.label.search"))
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText(t("qtool.matprop.placeholder.search"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_table_rows)
        table_toolbar.addWidget(search_label)
        table_toolbar.addWidget(self.search_edit, 1)
        table_toolbar.addSpacing(_sc(8))

        self.reverse_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.reverse_mapping"))
        self.reverse_btn.clicked.connect(self.reverse_mapping)
        self.clear_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.clear_table"))
        self.clear_btn.clicked.connect(self.clear_table)
        table_toolbar.addWidget(self.reverse_btn)
        table_toolbar.addWidget(self.clear_btn)
        right_layout.addLayout(table_toolbar)

        # 表格
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            t("qtool.matprop.header.select"),
            t("qtool.matprop.header.material_attr"),
            t("qtool.matprop.header.target_attr"),
            t("qtool.matprop.header.transform"),
            t("qtool.matprop.header.parameters"),
            t("qtool.matprop.header.default")
        ])
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)

        header = self.table.horizontalHeader()
        if hasattr(header, 'setSectionResizeMode'):
            header.setSectionResizeMode(0, QHeaderView.Fixed)
            header.setSectionResizeMode(1, QHeaderView.Interactive)
            header.setSectionResizeMode(2, QHeaderView.Interactive)
            header.setSectionResizeMode(3, QHeaderView.Interactive)
            header.setSectionResizeMode(4, QHeaderView.Interactive)
            header.setSectionResizeMode(5, QHeaderView.Interactive)
        else:
            header.setResizeMode(0, QHeaderView.Fixed)
            header.setResizeMode(1, QHeaderView.Interactive)
            header.setResizeMode(2, QHeaderView.Interactive)
            header.setResizeMode(3, QHeaderView.Interactive)
            header.setResizeMode(4, QHeaderView.Interactive)
            header.setResizeMode(5, QHeaderView.Interactive)

        self.table.setColumnWidth(0, _sc(50))
        self.table.setColumnWidth(1, _sc(130))
        self.table.setColumnWidth(2, _sc(130))
        self.table.setColumnWidth(3, _sc(100))
        self.table.setColumnWidth(4, _sc(120))
        self.table.setColumnWidth(5, _sc(80))

        self.table.horizontalHeader().setStretchLastSection(False)

        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)

        # 保存原始resize事件
        self.table._original_resize_event = self.table.resizeEvent
        # 重写resize事件以保持列比例
        self.table.resizeEvent = self._table_resize_event

        right_layout.addWidget(self.table, 1)
        splitter.addWidget(right_panel)

        # 初始面板宽度：左 300 / 右剩余
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([_sc(300), _sc(680)])
        main_layout.addWidget(splitter, 1)

        # ── 底部状态栏 ──────────────────────────────────────
        status_bar = QtWidgets.QFrame()
        status_bar.setFrameShape(QtWidgets.QFrame.StyledPanel)
        status_layout = QtWidgets.QHBoxLayout(status_bar)
        status_layout.setContentsMargins(_sc(8), _sc(3), _sc(8), _sc(3))
        status_layout.setSpacing(_sc(8))
        self.status_path_label = QtWidgets.QLabel(
            f'{t("qtool.matprop.label.preset_path")}: {self.preset_dir}')
        self.status_rows_label = QtWidgets.QLabel()
        status_layout.addWidget(self.status_path_label)
        status_layout.addStretch()
        status_layout.addWidget(self.status_rows_label)
        main_layout.addWidget(status_bar)

        # 行数统计：监听表格模型增删
        self.table.model().rowsInserted.connect(lambda *_: self._update_status_rows())
        self.table.model().rowsRemoved.connect(lambda *_: self._update_status_rows())

        # AI 模型列表后台加载回调（必须在刷新前连接）
        self.models_ready.connect(self._on_models_ready)

        # 初始化
        self._load_ai_settings_into_ui()
        self._update_status_rows()

        for i in range(3):
            self.add_row()

    def _toggle_ai_panel(self):
        """展开/收起 AI 配置面板"""
        if self._ai_body.isVisible():
            self._ai_body.hide()
            self.ai_toggle_btn.setArrowType(Qt.RightArrow)
        else:
            self._ai_body.show()
            self.ai_toggle_btn.setArrowType(Qt.DownArrow)

    def _update_status_rows(self):
        """更新状态栏行数统计"""
        if hasattr(self, "status_rows_label"):
            self.status_rows_label.setText(f"共 {self.table.rowCount()} 行")

    def add_row_with_options(self):
        """添加新行，自动获取当前材质类型的属性列表"""
        source_type = self.source_node_type.text() if self.source_node_type else ""
        target_type = self.target_node_type.text() if self.target_node_type else ""

        source_attributes = []
        target_attributes = []

        if source_type:
            try:
                temp_node = cmds.createNode(source_type, skipSelect=True)
                attributes = cmds.listAttr(temp_node, read=True, write=True, visible=True)
                if attributes:
                    source_attributes = self._filter_vector_components(attributes)
                cmds.delete(temp_node)
            except Exception as e:
                print(f"获取源节点类型属性失败: {e}")

        if target_type:
            try:
                temp_node = cmds.createNode(target_type, skipSelect=True)
                attributes = cmds.listAttr(temp_node, read=True, write=True, visible=True)
                if attributes:
                    target_attributes = self._filter_vector_components(attributes)
                cmds.delete(temp_node)
            except Exception as e:
                print(f"获取目标节点类型属性失败: {e}")

        self.add_row(left_text="", right_text="", left_options=source_attributes, right_options=target_attributes)
    
    def add_row(self, left_text="", right_text="", left_options=None, right_options=None, transform_text="", default_value="", parameters=None):
        """添加新行

        Args:
            left_text: 左侧当前选中的文本
            right_text: 右侧当前选中的文本
            left_options: 左侧下拉列表的所有选项
            right_options: 右侧下拉列表的所有选项
            transform_text: 转换函数文本
            default_value: 默认值
            parameters: 转换函数参数字典（保存在行内，保存映射文件时写回 "parameters" 字段）
        """
        row_position = self.table.rowCount()
        self.table.insertRow(row_position)

        # 复选框
        checkbox = QtWidgets.QCheckBox()
        checkbox_widget = QtWidgets.QWidget()
        checkbox_layout = QtWidgets.QHBoxLayout(checkbox_widget)
        checkbox_layout.addWidget(checkbox)
        checkbox_layout.setAlignment(Qt.AlignCenter)
        checkbox_layout.setContentsMargins(0, 0, 0, 0)
        self.table.setCellWidget(row_position, 0, checkbox_widget)

        # 左侧材质属性输入 - 可编辑下拉列表
        left_combo = QtWidgets.QComboBox()
        left_combo.setEditable(True)
        left_combo.wheelEvent = lambda e: e.ignore()
        left_combo.installEventFilter(self)
        # 添加none选项作为清空标识
        left_combo.addItem("(无)")
        if left_options:
            left_combo.addItems(left_options)
            if left_text:
                left_combo.setCurrentText(left_text)
            else:
                left_combo.setCurrentText("(无)")
        elif left_text:
            left_combo.addItem(left_text)
            left_combo.setCurrentText(left_text)
        else:
            left_combo.setCurrentText("(无)")
        left_combo.currentTextChanged.connect(lambda text: self._on_left_attribute_changed(row_position, text))
        self.table.setCellWidget(row_position, 1, left_combo)

        # 右侧目标属性输入 - 可编辑下拉列表
        right_combo = QtWidgets.QComboBox()
        right_combo.setEditable(True)
        right_combo.wheelEvent = lambda e: e.ignore()
        right_combo.installEventFilter(self)
        # 添加none选项作为清空标识
        right_combo.addItem("(无)")
        if right_options:
            right_combo.addItems(right_options)
            if right_text:
                right_combo.setCurrentText(right_text)
            else:
                right_combo.setCurrentText("(无)")
        elif right_text:
            right_combo.addItem(right_text)
            right_combo.setCurrentText(right_text)
        else:
            right_combo.setCurrentText("(无)")
        right_combo.currentTextChanged.connect(lambda text: self._on_right_attribute_changed(row_position, text))
        self.table.setCellWidget(row_position, 2, right_combo)

        # 转换函数输入 - 可编辑下拉列表（显示中文名称）
        transform_combo = QtWidgets.QComboBox()
        transform_combo.setEditable(True)
        transform_combo.wheelEvent = lambda e: e.ignore()
        # 添加转换函数选项
        func_options = get_conversion_function_options()
        transform_combo.addItems(func_options)
        # 转换函数变更时自动填入默认参数
        transform_combo.currentTextChanged.connect(lambda text, rp=row_position: self._on_transform_changed(rp, text))
        # 设置当前值
        if transform_text:
            # 尝试映射为中文显示
            display_text = transform_text
            # 检查是否是英文名称，尝试找到对应的中文
            # 先收集所有中文名称（不包含英文形式的）
            chinese_names = []
            for name in func_options:
                if name != "(无)":
                    chinese_names.append(name)
            # 查找对应的中文
            for chi_name in chinese_names:
                if chi_name in MATERIAL_CONVERSION_FUNCTIONS and MATERIAL_CONVERSION_FUNCTIONS[chi_name] == transform_text:
                    display_text = chi_name
                    break
            transform_combo.setCurrentText(display_text)
        else:
            transform_combo.setCurrentText("(无)")
        self.table.setCellWidget(row_position, 3, transform_combo)

        # 转换函数参数输入 - 文本框（JSON 格式，选择转换函数后自动填入该函数的真实默认参数）
        param_edit = QtWidgets.QLineEdit()
        param_edit.setToolTip(t("qtool.matprop.tooltip.parameters"))
        # 映射文件显式配置的参数优先；未配置时由 _on_transform_changed 自动填入默认参数
        if parameters:
            param_edit.setText(json.dumps(parameters, ensure_ascii=False))
        self.table.setCellWidget(row_position, 4, param_edit)

        # 默认值输入 - 文本框
        default_edit = QtWidgets.QLineEdit()
        default_edit.setPlaceholderText(t("qtool.matprop.placeholder.default_value"))
        if default_value:
            default_edit.setText(default_value)
        self.table.setCellWidget(row_position, 5, default_edit)

    def _on_transform_changed(self, row, text):
        """转换函数变更时，自动填入该函数的默认参数（用户可修改）"""
        param_edit = self.table.cellWidget(row, 4)
        if not param_edit:
            return
        func_name = text.strip()
        if not func_name or func_name == "(无)":
            param_edit.setText("")
            return
        # 中文名 → 英文函数名
        if func_name in MATERIAL_CONVERSION_FUNCTIONS:
            func_name = MATERIAL_CONVERSION_FUNCTIONS[func_name]
        defaults = TRANSFORM_DEFAULT_PARAMS.get(func_name)
        param_edit.setText(json.dumps(defaults, ensure_ascii=False) if defaults else "")

    def eventFilter(self, obj, event):
        """事件过滤器 - 处理下拉列表的右键点击"""
        if event.type() == QtCore.QEvent.ContextMenu:
            # 判断是左侧还是右侧的下拉列表
            column = -1
            for row in range(self.table.rowCount()):
                if self.table.cellWidget(row, 1) == obj:
                    column = 1
                    break
                elif self.table.cellWidget(row, 2) == obj:
                    column = 2
                    break

            if column in [1, 2]:
                menu = QtWidgets.QMenu()
                if column == 1:
                    clear_action = menu.addAction(t("qtool.matprop.menu.clear_source"))
                else:
                    clear_action = menu.addAction(t("qtool.matprop.menu.clear_target"))

                action = menu.exec_(event.globalPos())
                if action == clear_action:
                    # 找到该下拉列表对应的行并设置为"(无)"
                    for row in range(self.table.rowCount()):
                        if self.table.cellWidget(row, column) == obj:
                            obj.setCurrentText("(无)")
                            break
                return True

        # 对于其他事件，不拦截，让Qt继续处理
        return False

    def _show_table_context_menu(self, pos):
        """显示表格右键菜单

        Args:
            pos: 相对于viewport的鼠标位置 (QPoint)
        """
        # 获取点击位置对应的索引
        index = self.table.indexAt(pos)

        if not index.isValid():
            return

        row = index.row()
        column = index.column()

        # 只对属性列显示菜单（源属性列和目标属性列）
        if column not in [1, 2]:
            return

        # 检查该单元格是否有下拉列表
        combo = self.table.cellWidget(row, column)
        if not combo:
            return

        menu = QtWidgets.QMenu()

        ai_action = menu.addAction("AI 判断此属性")

        if column == 1:
            clear_action = menu.addAction(t("qtool.matprop.menu.clear_source"))
        else:
            clear_action = menu.addAction(t("qtool.matprop.menu.clear_target"))

        # 显示菜单并获取选中的动作
        action = menu.exec_(self.table.viewport().mapToGlobal(pos))

        if action == ai_action:
            self.ai_mapping_row(row)
        elif action == clear_action:
            self._clear_single_attribute(row, column)

    def _clear_single_attribute(self, row, column):
        """清空单个单元格的下拉列表值"""
        if row < 0 or row >= self.table.rowCount():
            return

        combo = self.table.cellWidget(row, column)
        if combo:
            combo.setCurrentText("(无)")

    def _on_left_attribute_changed(self, changed_row, new_text):
        """左侧属性变更检测"""
        if not new_text or new_text == "(无)":
            return

        # 检查同一侧是否有重复
        duplicates = []
        for row in range(self.table.rowCount()):
            if row != changed_row:
                combo = self.table.cellWidget(row, 1)
                if combo and combo.currentText() == new_text:
                    duplicates.append(row)

        if duplicates:
            # 清空重复的属性
            for row in duplicates:
                self._clear_single_attribute(row, 1)
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matprop.msg.duplicate_attr", new_text=new_text))

    def _on_right_attribute_changed(self, changed_row, new_text):
        """右侧属性变更检测"""
        if not new_text or new_text == "(无)":
            return

        # 检查同一侧是否有重复
        duplicates = []
        for row in range(self.table.rowCount()):
            if row != changed_row:
                combo = self.table.cellWidget(row, 2)
                if combo and combo.currentText() == new_text:
                    duplicates.append(row)

        if duplicates:
            # 清空重复的属性
            for row in duplicates:
                self._clear_single_attribute(row, 2)
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matprop.msg.duplicate_attr", new_text=new_text))

    def remove_selected_rows(self):
        """移除选中行"""
        selected_rows = set()
        for item in self.table.selectedItems():
            selected_rows.add(item.row())
        
        # 如果没有选中行，检查哪些行的复选框被选中
        if not selected_rows:
            for row in range(self.table.rowCount()):
                widget = self.table.cellWidget(row, 0)
                if widget:
                    checkbox = widget.findChild(QtWidgets.QCheckBox)
                    if checkbox and checkbox.isChecked():
                        selected_rows.add(row)
        
        # 按降序删除，避免索引变化
        for row in sorted(selected_rows, reverse=True):
            self.table.removeRow(row)
        
        if not selected_rows:
            QMessageBox.information(self, t("common.tip"), t("qtool.matprop.msg.select_row_to_delete"))
    
    def _get_table_cell_text(self, row, col):
        """读取表格单元格文本（兼容下拉框和普通项）"""
        widget = self.table.cellWidget(row, col)
        if widget is not None:
            return widget.currentText() if hasattr(widget, 'currentText') else widget.text()
        item = self.table.item(row, col)
        return item.text() if item else ""

    def _filter_table_rows(self, keyword):
        """根据关键词筛选表格行（匹配源属性列和目标属性列）"""
        keyword = (keyword or "").strip().lower()
        for row in range(self.table.rowCount()):
            if not keyword:
                self.table.setRowHidden(row, False)
                continue
            src_text = self._get_table_cell_text(row, 1).lower()
            dst_text = self._get_table_cell_text(row, 2).lower()
            self.table.setRowHidden(row, not (keyword in src_text or keyword in dst_text))

    def clear_table(self):
        """清空表格"""
        while self.table.rowCount() > 0:
            self.table.removeRow(0)
    
    def get_mapping_data(self):
        """获取所有映射数据"""
        data = []
        for row in range(self.table.rowCount()):
            # 从下拉列表获取属性值
            left_combo = self.table.cellWidget(row, 1)
            right_combo = self.table.cellWidget(row, 2)
            transform_combo = self.table.cellWidget(row, 3)
            default_edit = self.table.cellWidget(row, 5)

            if left_combo and right_combo:
                left_text = left_combo.currentText().strip()
                right_text = right_combo.currentText().strip()
                transform_text = transform_combo.currentText().strip() if transform_combo else ""
                default_value = default_edit.text().strip() if default_edit else ""

                # 忽略"(无)"选项
                if left_text == "(无)":
                    left_text = ""
                if right_text == "(无)":
                    right_text = ""
                if transform_text == "(无)":
                    transform_text = ""

                # 将中文转换为英文函数名（用于保存）
                if transform_text and transform_text in MATERIAL_CONVERSION_FUNCTIONS:
                    transform_text = MATERIAL_CONVERSION_FUNCTIONS[transform_text]

                # 保存所有行，即使源或目标属性为空
                if left_text or right_text:  # 至少有一个非空
                    row_data = {
                        "source_attribute": left_text,
                        "target_attribute": right_text,
                        "transform": transform_text,
                        "default_value": default_value
                    }
                    # 读取转换函数参数（参数列 JSON 文本，解析失败则忽略该行参数）
                    param_edit = self.table.cellWidget(row, 4)
                    if param_edit:
                        param_text = param_edit.text().strip()
                        if param_text:
                            try:
                                params = json.loads(param_text)
                                if isinstance(params, dict) and params:
                                    row_data["parameters"] = params
                            except Exception:
                                print(f"[参数] 行 {row} 的参数不是合法 JSON，已忽略: {param_text}")
                    data.append(row_data)
        return data

    def set_mapping_data(self, data):
        """设置映射数据到表格"""
        # 清空表格
        self.clear_table()

        # 获取源节点和目标节点类型
        source_type = self.source_node_type.text() if self.source_node_type else ""
        target_type = self.target_node_type.text() if self.target_node_type else ""

        # 获取源节点和目标节点的属性列表
        source_attributes = []
        target_attributes = []

        if source_type:
            # 尝试获取源节点类型的属性
            try:
                # 创建一个临时节点来获取属性
                temp_node = cmds.createNode(source_type, skipSelect=True)
                attributes = cmds.listAttr(temp_node, read=True, write=True, visible=True)
                if attributes:
                    source_attributes = self._filter_vector_components(attributes)
                # 删除临时节点
                cmds.delete(temp_node)
            except Exception as e:
                print(f"获取源节点类型属性失败: {e}")

        if target_type:
            # 尝试获取目标节点类型的属性
            try:
                # 创建一个临时节点来获取属性
                temp_node = cmds.createNode(target_type, skipSelect=True)
                attributes = cmds.listAttr(temp_node, read=True, write=True, visible=True)
                if attributes:
                    target_attributes = self._filter_vector_components(attributes)
                # 删除临时节点
                cmds.delete(temp_node)
            except Exception as e:
                print(f"获取目标节点类型属性失败: {e}")

        # 添加新数据
        for item in data:
            source_attr = item.get("source_attribute", "")
            target_attr = item.get("target_attribute", "")
            
            # 确保即使源或目标为空也能正确添加行
            self.add_row(
                source_attr,
                target_attr,
                left_options=source_attributes,
                right_options=target_attributes,
                transform_text=item.get("transform", ""),
                default_value=item.get("default_value", ""),
                parameters=item.get("parameters") or {}
            )

    def save_preset(self, filepath):
        """保存预设到文件"""
        mappings = self.get_mapping_data()
        if not mappings:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matprop.msg.no_mapping_data"))
            return False

        from datetime import datetime

        source_node_type = self.source_node_type.text() if self.source_node_type else ""
        target_node_type = self.target_node_type.text() if self.target_node_type else ""

        # 构建新的JSON结构
        preset_data = {
            "version": "3.0",
            "name": f"{source_node_type} → {target_node_type}",
            "software": "maya",
            "source_type": source_node_type,
            "target_type": target_node_type,
            "description": f"{source_node_type} 到 {target_node_type} 的属性映射",
            "created_date": datetime.now().strftime("%Y-%m-%d"),
            "mappings": mappings
        }

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(preset_data, f, indent=4, ensure_ascii=False)

            QMessageBox.information(self, t("msg.success"), t("qtool.matprop.msg.preset_saved", filepath=filepath))
            return True
        except Exception as e:
            QMessageBox.critical(self, t("msg.error"), t("qtool.matprop.msg.save_failed", e=str(e)))
            return False

    def load_preset(self, filepath):
        """从文件加载预设"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                preset_data = json.load(f)

            # 加载源节点和目标节点类型信息
            source_type = preset_data.get("source_type", "")
            target_type = preset_data.get("target_type", "")

            # 设置源节点类型信息
            if source_type:
                self.source_node_name = ""
                if self.source_node_type:
                    self.source_node_type.setText(source_type)

            # 设置目标节点类型信息
            if target_type:
                self.target_node_name = ""
                if self.target_node_type:
                    self.target_node_type.setText(target_type)



            # 加载映射数据
            mappings = preset_data.get("mappings", [])
            self.set_mapping_data(mappings)


            return True
        except Exception as e:
            QMessageBox.critical(self, t("msg.error"), t("qtool.matprop.msg.load_failed", e=str(e)))
            return False
    
    def save_preset_dialog(self):
        """打开保存预设对话框"""
        # 生成默认文件名：源节点类型_目标节点类型
        source_type = self.source_node_type.text() if self.source_node_type else ""
        target_type = self.target_node_type.text() if self.target_node_type else ""
        default_filename = f"{source_type}_{target_type}" if (source_type and target_type) else "material_mapping"
        default_filepath = os.path.join(self.preset_dir, default_filename)
        
        filepath, _ = QFileDialog.getSaveFileName(
            self, t("qtool.matprop.dialog.save_preset"), default_filepath, t("qtool.matprop.dialog.mapping_filter")
        )

        if filepath:
            if not filepath.endswith('.mmap'):
                filepath += '.mmap'
            self.save_preset(filepath)

    def load_preset_dialog(self):
        """打开加载预设对话框"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, t("qtool.matprop.dialog.load_preset"), self.preset_dir, t("qtool.matprop.dialog.mapping_filter")
        )
        
        if filepath:
            self.load_preset(filepath)
    
    def save_last_preset(self):
        """自动保存当前设置为最后使用的预设"""
        last_preset_path = os.path.join(self.preset_dir, "_last_preset.mmap")
        mappings = self.get_mapping_data()

        if mappings:
            from datetime import datetime
            source_node_type = self.source_node_type.text() if self.source_node_type else ""
            target_node_type = self.target_node_type.text() if self.target_node_type else ""

            preset_data = {
                "version": "3.0",
                "name": f"{source_node_type} → {target_node_type}",
                "software": "maya",
                "source_type": source_node_type,
                "target_type": target_node_type,
                "description": f"{source_node_type} 到 {target_node_type} 的属性映射",
                "created_date": datetime.now().strftime("%Y-%m-%d"),
                "mappings": mappings
            }

            try:
                with open(last_preset_path, 'w', encoding='utf-8') as f:
                    json.dump(preset_data, f, indent=4, ensure_ascii=False)
            except IOError as e:
                print(f"保存最后预设失败: {e}")
    
    def load_last_preset(self):
        """加载最后使用的预设"""
        last_preset_path = os.path.join(self.preset_dir, "_last_preset.mmap")
        if os.path.exists(last_preset_path):
            self.load_preset(last_preset_path)
    
    def open_preset_folder(self):
        """打开预设文件夹"""
        import subprocess
        try:
            folder_path = os.path.abspath(self.preset_dir)
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
            subprocess.Popen(['explorer', folder_path])
        except Exception as e:
            QMessageBox.warning(self, t("msg.error"), t("qtool.matprop.msg.open_folder_failed", e=str(e)))



    def reverse_mapping(self):
        """交换源属性和目标属性"""
        # 交换源节点和目标节点的名称和类型
        source_name = self.source_node_name
        target_name = self.target_node_name

        self.source_node_name = target_name
        self.target_node_name = source_name

        # 交换节点类型显示
        source_type = self.source_node_type.text() if self.source_node_type else ""
        target_type = self.target_node_type.text() if self.target_node_type else ""
        self.source_node_type.setText(target_type)
        self.target_node_type.setText(source_type)

        # 交换表格中的属性（全程屏蔽信号，避免触发重复检测警告）
        pairs = []
        for row in range(self.table.rowCount()):
            left_combo = self.table.cellWidget(row, 1)
            right_combo = self.table.cellWidget(row, 2)
            if left_combo and right_combo:
                pairs.append((left_combo, right_combo))

        for c in [c for p in pairs for c in p]:
            c.blockSignals(True)
        try:
            for left_combo, right_combo in pairs:
                left_text = left_combo.currentText()
                right_text = right_combo.currentText()
                left_options = [left_combo.itemText(i) for i in range(left_combo.count())]
                right_options = [right_combo.itemText(i) for i in range(right_combo.count())]

                left_combo.clear()
                left_combo.addItems(right_options)
                right_combo.clear()
                right_combo.addItems(left_options)

                # 恢复当前文本（在选项交换后）
                left_combo.setCurrentText(right_text)
                right_combo.setCurrentText(left_text)
        finally:
            for c in [c for p in pairs for c in p]:
                c.blockSignals(False)

        # 将转换函数切换为反向函数（如 漫反射粗糙度转PBR粗糙度 → PBR粗糙度转漫反射粗糙度）
        for row in range(self.table.rowCount()):
            transform_combo = self.table.cellWidget(row, 3)
            if not transform_combo:
                continue
            t_text = transform_combo.currentText().strip()
            if not t_text or t_text == "(无)":
                continue
            en = MATERIAL_CONVERSION_FUNCTIONS.get(t_text, t_text)
            rev = TRANSFORM_REVERSE.get(en)
            if rev:
                # 读取当前参数，反向时把反向函数也接受的同名参数带过去，
                # 保证往返转换（A→B→A）数值一致（如 乘标量{scalar:2} ⟷ 除标量{scalar:2}）
                param_edit = self.table.cellWidget(row, 4)
                carry = {}
                if param_edit:
                    pt = param_edit.text().strip()
                    if pt:
                        try:
                            carry = json.loads(pt)
                            if not isinstance(carry, dict):
                                carry = {}
                        except Exception:
                            carry = {}
                transform_combo.setCurrentText(self._display_transform_name(rev))
                # setCurrentText 触发 _on_transform_changed 已填入反向函数默认参数，
                # 这里再用「反向函数接受的同名参数」覆盖，保留用户设置的值
                if carry and param_edit:
                    rev_names = TRANSFORM_PARAM_NAMES.get(rev, [])
                    keep = {k: v for k, v in carry.items() if k in rev_names}
                    if keep:
                        try:
                            merged = {}
                            cur_text = param_edit.text().strip()
                            if cur_text:
                                parsed = json.loads(cur_text)
                                if isinstance(parsed, dict):
                                    merged = parsed
                            merged.update(keep)
                            param_edit.setText(json.dumps(merged, ensure_ascii=False))
                        except Exception:
                            pass

    def load_node_attributes(self, node, is_source):
        """加载节点的默认属性到表格"""
        try:
            # 获取节点的可读写属性（保持Maya原生的属性顺序）
            attributes = cmds.listAttr(node, read=True, write=True, visible=True)
            if not attributes:
                return

            # 过滤掉向量属性的分量
            filtered_attributes = self._filter_vector_components(attributes)

            if is_source:
                # 加载源节点属性到左侧
                # 保留右侧的目标属性
                current_targets = []
                for row in range(self.table.rowCount()):
                    right_combo = self.table.cellWidget(row, 2)
                    if right_combo:
                        current_targets.append(right_combo.currentText())

                # 清空表格
                self.clear_table()

                # 添加源节点属性到左侧，保留右侧目标属性
                for i, attr in enumerate(filtered_attributes):
                    target_value = current_targets[i] if i < len(current_targets) else ""
                    self.add_row(left_text=attr, left_options=filtered_attributes,
                                right_text=target_value, right_options=current_targets)
            else:
                # 加载目标节点属性到右侧
                # 保留左侧的源属性
                current_sources = []
                for row in range(self.table.rowCount()):
                    left_combo = self.table.cellWidget(row, 1)
                    if left_combo:
                        current_sources.append(left_combo.currentText())

                # 清空表格
                self.clear_table()

                # 自动匹配属性
                matched_targets = self._auto_match_attributes(current_sources, filtered_attributes.copy())

                # 首先添加有匹配的源属性
                for i, src_attr in enumerate(current_sources):
                    target_value = matched_targets.get(src_attr, "")
                    self.add_row(left_text=src_attr, left_options=current_sources,
                                right_text=target_value, right_options=filtered_attributes)

                # 然后添加剩余的目标属性
                remaining_targets = [attr for attr in filtered_attributes if attr not in matched_targets.values()]
                for target_attr in remaining_targets:
                    self.add_row(left_text="", left_options=current_sources,
                                right_text=target_attr, right_options=filtered_attributes)

        except Exception as e:
            print(f"加载节点属性失败: {e}")

    def _auto_match_attributes(self, source_attrs, target_attrs):
        """自动匹配属性

        Args:
            source_attrs: 源属性列表
            target_attrs: 目标属性列表（会被修改，使用时请传入副本）

        Returns:
            dict: 源属性到目标属性的映射
        """
        matches = {}

        # 首先匹配同名属性
        for src_attr in source_attrs:
            if src_attr in target_attrs:
                matches[src_attr] = src_attr
                target_attrs.remove(src_attr)  # 避免重复匹配

        # 然后匹配最相似的属性
        for src_attr in source_attrs:
            if src_attr not in matches:
                best_match = self._find_best_match(src_attr, target_attrs)
                if best_match:
                    matches[src_attr] = best_match
                    target_attrs.remove(best_match)  # 避免重复匹配

        return matches

    def _find_best_match(self, source_attr, target_attrs):
        """找到最相似的属性

        Args:
            source_attr: 源属性名
            target_attrs: 目标属性列表

        Returns:
            str: 最相似的目标属性名，或None
        """
        if not target_attrs:
            return None

        best_match = None
        highest_score = 0

        for target_attr in target_attrs:
            score = self._score_match(source_attr, target_attr)
            if score > highest_score:
                highest_score = score
                best_match = target_attr

        # 只有相似度足够高才返回
        if highest_score > 0.5:
            return best_match
        return None

    def _score_match(self, source_attr, target_attr):
        """多因素评分系统计算匹配度

        Args:
            source_attr: 源属性名
            target_attr: 目标属性名

        Returns:
            float: 匹配度分数 (0-1)
        """
        # 1. 完全匹配
        if source_attr == target_attr:
            return 1.0

        # 2. 语义映射匹配
        semantic_map = self._get_semantic_mapping()
        source_key = self._to_snake_case(source_attr)
        target_key = self._to_snake_case(target_attr)
        if source_key in semantic_map and semantic_map[source_key] == target_key:
            return 0.95

        # 3. 字符串相似度
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, source_key, target_key).ratio()

        # 4. 关键词重叠
        source_words = set(source_key.split('_'))
        target_words = set(target_key.split('_'))
        if source_words & target_words:
            keyword_score = len(source_words & target_words) / max(len(source_words), len(target_words))
        else:
            keyword_score = 0

        # 5. 长度相似性
        length_ratio = min(len(source_key), len(target_key)) / max(len(source_key), len(target_key))

        # 综合评分
        total_score = (similarity * 0.5) + (keyword_score * 0.3) + (length_ratio * 0.2)
        return min(total_score, 1.0)

    def _to_snake_case(self, text):
        """将驼峰命名转换为蛇形命名

        Args:
            text: 原始字符串

        Returns:
            str: 转换后的蛇形命名字符串
        """
        import re
        # 将驼峰命名转换为下划线分隔
        text = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', text)
        text = re.sub('([a-z0-9])([A-Z])', r'\1_\2', text)
        return text.lower()

    def _get_semantic_mapping(self):
        """获取语义映射字典

        Returns:
            dict: 语义映射字典
        """
        return {
            # 基础属性
            'base_color': 'color',
            'basecolor': 'color',
            'diffuse_color': 'color',
            'diffusecolor': 'color',
            
            # 粗糙度
            'roughness': 'roughness',
            'specular_roughness': 'roughness',
            'refl_roughness': 'roughness',
            
            # 金属度
            'metalness': 'metallic',
            'metallic': 'metallic',
            
            # 法线
            'normal': 'normal_camera',
            'normal_camera': 'normal_camera',
            'normalcamera': 'normal_camera',
            
            # 发光
            'emission': 'incandescence',
            'emission_color': 'incandescence',
            'emissioncolor': 'incandescence',
            'incandescence': 'incandescence',
            
            # 透明度
            'opacity': 'transparency',
            'opacity_color': 'transparency',
            'transparency': 'transparency',
            
            # 高光
            'specular': 'specular_color',
            'specular_color': 'specular_color',
            'specularcolor': 'specular_color',
            
            # 折射率
            'ior': 'refractions',
            'refractive_index': 'refractions',
            'refractions': 'refractions',
            
            # 反射
            'reflectivity': 'reflectance',
            'reflectance': 'reflectance',
            'refl_weight': 'reflectance',
            
            # 凹凸
            'bump': 'bump_map',
            'bump_map': 'bump_map',
            'bumpmap': 'bump_map',
            
            # 位移
            'displacement': 'displace',
            'displace': 'displace'
        }

    def _filter_vector_components(self, attributes):
        """过滤掉向量属性的分量，只保留主属性"""
        # 常见的向量分量后缀
        vector_suffixes = ['R', 'G', 'B', 'X', 'Y', 'Z', 'W', 'U', 'V']
        attributes_set = set(attributes)

        # 存储主属性
        main_attributes = []
        # 存储已处理的主属性名
        processed_main_attrs = set()

        for attr in attributes:
            # 检查是否为分量属性
            is_component = False
            for suffix in vector_suffixes:
                if attr.endswith(suffix) and len(attr) > 1:
                    # 获取主属性名
                    main_attr = attr[:-1]
                    # 仅当主属性真实存在时才视为分量，
                    # 避免误删 specularIOR、ior、specular 等以字母结尾的真实属性
                    if main_attr in attributes_set:
                        if main_attr not in processed_main_attrs:
                            main_attributes.append(main_attr)
                            processed_main_attrs.add(main_attr)
                        is_component = True
                        break

            # 如果不是分量属性，直接添加
            if not is_component and attr not in processed_main_attrs:
                main_attributes.append(attr)
                processed_main_attrs.add(attr)

        return main_attributes

    # ==================== AI 智能映射 ====================

    def _load_ai_settings_into_ui(self):
        """将已保存的 AI 配置预填到界面"""
        settings = OllamaMapper._load_settings()
        provider = settings.get("ai_provider", "ollama")
        # 屏蔽信号：setCurrentIndex 触发的服务商切换回调里也会刷新模型列表，
        # 避免启动时重复发起网络请求（实测 Ollama /api/tags 每次约 2s，重复 3 次即 6s）
        self.ai_provider_combo.blockSignals(True)
        idx = self.ai_provider_combo.findData(provider)
        if idx >= 0:
            self.ai_provider_combo.setCurrentIndex(idx)
        self.ai_provider_combo.blockSignals(False)

        try:
            from squirrel_asset_manager.utils.settings import get_ai_api_key
            api_key = get_ai_api_key(settings, provider)
        except ImportError:
            api_key = settings.get("ai_api_key", "")
        self.ai_api_key_edit.setText(api_key)
        # 仅同步 API Key 输入框的可用状态，不触发模型刷新
        needs_key = bool(OllamaMapper.PROVIDERS.get(provider, {}).get("needs_key"))
        self.ai_api_key_edit.setEnabled(needs_key)
        self.ai_api_key_edit.setPlaceholderText(
            "API Key（本地服务无需填写）" if not needs_key else "API Key")

        saved_model = settings.get("ai_model", "")
        if saved_model:
            self.ai_model_combo.setCurrentText(saved_model)

        # 后台线程刷新模型列表，不阻塞界面（信号回调更新下拉框）
        self.refresh_ai_models(silent=True)

    def _on_ai_provider_changed(self, *_):
        """服务商切换：控制 API Key 输入、刷新模型列表"""
        provider = self.ai_provider_combo.itemData(self.ai_provider_combo.currentIndex()) or "ollama"
        needs_key = bool(OllamaMapper.PROVIDERS.get(provider, {}).get("needs_key"))
        self.ai_api_key_edit.setEnabled(needs_key)
        if not needs_key:
            self.ai_api_key_edit.setPlaceholderText("API Key（本地服务无需填写）")
        else:
            self.ai_api_key_edit.setPlaceholderText("API Key")
        # 切换服务商时加载该服务商自己保存的 API Key
        try:
            from squirrel_asset_manager.utils.settings import SettingsManager, get_ai_api_key
        except ImportError:
            try:
                from utils.settings import SettingsManager, get_ai_api_key
            except ImportError:
                return
        self.ai_api_key_edit.setText(get_ai_api_key(SettingsManager().load(), provider))

        # 立即用新服务商静态模型列表填充，避免残留上一个服务商的模型；异步刷新后替换
        static = list(OllamaMapper.PROVIDERS.get(provider, {}).get("models", [])) or [OllamaMapper.DEFAULT_MODEL]
        self.ai_model_combo.blockSignals(True)
        self.ai_model_combo.clear()
        self.ai_model_combo.addItems(static)
        self.ai_model_combo.setCurrentIndex(0)
        self.ai_model_combo.blockSignals(False)

        self.refresh_ai_models(silent=True)

    def _save_ai_settings_to_settings(self):
        """将当前界面 AI 配置保存到用户设置（与主窗口共享）"""
        try:
            from squirrel_asset_manager.utils.settings import SettingsManager, set_ai_api_key
        except ImportError:
            try:
                from utils.settings import SettingsManager, set_ai_api_key
            except ImportError:
                return
        provider = self.ai_provider_combo.itemData(self.ai_provider_combo.currentIndex()) or "ollama"
        key = self.ai_api_key_edit.text().strip()
        new_settings = set_ai_api_key(SettingsManager().load(), provider, key)
        new_settings["ai_provider"] = provider
        new_settings["ai_model"] = self.ai_model_combo.currentText().strip()
        SettingsManager().save(new_settings)

    def refresh_ai_models(self, silent=False):
        """刷新当前服务商的可用模型列表（后台线程执行，不阻塞界面）

        网络请求在后台线程进行，完成后通过 models_ready 信号回到主线程更新下拉框，
        避免 Ollama /api/tags 等同步请求卡住 UI（实测单次约 2s，阻塞期间窗口白板）。

        Args:
            silent: 为 True 时不弹错误提示（用于初始化时静默尝试）
        """
        provider = self.ai_provider_combo.itemData(self.ai_provider_combo.currentIndex()) or "ollama"

        def _worker():
            try:
                mapper = OllamaMapper(
                    provider=provider,
                    api_key=self.ai_api_key_edit.text().strip() or None)
                models = mapper.get_available_models()
            except Exception:
                models = []
            self.models_ready.emit(provider, models, silent)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_models_ready(self, provider, models, silent):
        """模型列表加载完成（主线程回调，更新下拉框）"""
        # 已切换到其他服务商时丢弃过期响应，避免旧服务商列表覆盖
        current_provider = self.ai_provider_combo.itemData(self.ai_provider_combo.currentIndex()) or "ollama"
        if provider != current_provider:
            return
        if models:
            current = self.ai_model_combo.currentText()
            self.ai_model_combo.blockSignals(True)
            self.ai_model_combo.clear()
            self.ai_model_combo.addItems(models)
            if current in models:
                self.ai_model_combo.setCurrentText(current)
            elif OllamaMapper.DEFAULT_MODEL in models:
                self.ai_model_combo.setCurrentText(OllamaMapper.DEFAULT_MODEL)
            elif models:
                # 下拉框可编辑：未命中时选中首个模型，避免残留上一个服务商的模型文本
                self.ai_model_combo.setCurrentIndex(0)
            self.ai_model_combo.blockSignals(False)
        elif not silent:
            QMessageBox.warning(self, t("msg.warning"),
                "无法连接 AI 服务，请检查服务是否可用或 API Key 是否正确")

    def _get_ai_mapper(self):
        """构造并检查 AI 客户端，不可用时返回 None"""
        provider = self.ai_provider_combo.itemData(self.ai_provider_combo.currentIndex()) or "ollama"
        model = self.ai_model_combo.currentText().strip() or OllamaMapper.DEFAULT_MODEL
        api_key = self.ai_api_key_edit.text().strip()
        mapper = OllamaMapper(provider=provider, model=model, api_key=api_key)
        if not mapper.is_available():
            QMessageBox.warning(self, t("msg.warning"),
                "无法连接 AI 服务，请检查服务是否可用或 API Key 是否正确")
            return None
        self._save_ai_settings_to_settings()
        return mapper

    def ai_mapping_all(self):
        """AI 整表映射：判断表格中所有源属性对应的目标属性/转换函数/默认值"""
        rows = self._collect_source_rows()
        if not rows:
            QMessageBox.information(self, t("common.tip"),
                "请先在表格中添加源属性，再进行 AI 映射")
            return
        self._ai_map_rows(rows)

    def ai_mapping_row(self, row):
        """AI 判断单行映射"""
        combo = self.table.cellWidget(row, 1)
        if not combo:
            return
        text = combo.currentText().strip()
        if not text or text == "(无)":
            QMessageBox.information(self, t("common.tip"),
                "请先在表格中添加源属性，再进行 AI 映射")
            return
        self._ai_map_rows([row])

    def _collect_source_rows(self):
        """收集表格中源属性非空的行号（保持表格顺序）"""
        rows = []
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 1)
            if combo:
                text = combo.currentText().strip()
                if text and text != "(无)":
                    rows.append(r)
        return rows

    def _collect_attr_types(self, node_type, attr_names=None):
        """通过临时节点收集属性名→类型的映射

        Args:
            node_type: 节点类型
            attr_names: 需要收集的属性名列表；为 None 时收集全部可见可读写属性

        Returns:
            dict: {属性名: 类型字符串}
        """
        if not node_type:
            if attr_names:
                return {a: "unknown" for a in attr_names}
            return {}
        try:
            temp_node = cmds.createNode(node_type, skipSelect=True)
            result = {}
            names = attr_names
            if names is None:
                names = cmds.listAttr(temp_node, read=True, write=True, visible=True) or []
            for a in names:
                try:
                    result[a] = cmds.getAttr(f"{temp_node}.{a}", type=True)
                except Exception:
                    result[a] = "unknown"
            cmds.delete(temp_node)
            return result
        except Exception as e:
            print(f"收集属性类型失败: {e}")
            if attr_names:
                return {a: "unknown" for a in attr_names}
            return {}

    def _collect_target_attrs(self, target_type):
        """收集目标属性列表（名称: 类型）

        优先通过目标节点类型创建临时节点获取全量属性；
        若失败则回退为表格目标下拉中已出现的属性名。
        """
        if target_type:
            type_map = self._collect_attr_types(target_type, None)
            if type_map:
                return [{"name": n, "type": t} for n, t in type_map.items()]
        seen = {}
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 2)
            if combo:
                for i in range(combo.count()):
                    text = combo.itemText(i).strip()
                    if text and text != "(无)" and text not in seen:
                        seen[text] = "unknown"
        return [{"name": n, "type": t} for n, t in seen.items()]

    def _ai_map_rows(self, rows):
        """对指定行执行 AI 映射判断并填入表格"""
        if not rows:
            return

        mapper = self._get_ai_mapper()
        if mapper is None:
            return

        source_type = self.source_node_type.text() if self.source_node_type else ""
        target_type = self.target_node_type.text() if self.target_node_type else ""

        # 收集源属性（去重，保持顺序）
        source_attrs = []
        seen_sources = set()
        for r in rows:
            combo = self.table.cellWidget(r, 1)
            if not combo:
                continue
            name = combo.currentText().strip()
            if name and name != "(无)" and name not in seen_sources:
                seen_sources.add(name)
                source_attrs.append({"name": name, "type": "unknown"})

        if not source_attrs:
            QMessageBox.information(self, t("common.tip"),
                "请先在表格中添加源属性，再进行 AI 映射")
            return

        # 补全源属性类型
        type_map = self._collect_attr_types(source_type, [a["name"] for a in source_attrs])
        for a in source_attrs:
            a["type"] = type_map.get(a["name"], "unknown")

        # 收集目标属性列表
        target_attrs = self._collect_target_attrs(target_type)
        if not target_attrs:
            QMessageBox.warning(self, t("msg.warning"),
                "无法获取目标属性列表，请先选择目标节点")
            return

        # 转换函数列表
        transform_list = []
        for name in get_conversion_function_options():
            if name == "(无)":
                continue
            transform_list.append({"chinese": name, "english": MATERIAL_CONVERSION_FUNCTIONS[name]})

        # 调用 AI（阻塞期间刷新 UI 光标）
        self.setCursor(Qt.WaitCursor)
        self.ai_mapping_btn.setEnabled(False)
        self.ai_refresh_btn.setEnabled(False)
        try:
            QtWidgets.QApplication.processEvents()
            suggestions = mapper.suggest_mapping(
                source_type, target_type, source_attrs, target_attrs, transform_list
            )
        except Exception as e:
            suggestions = []
            QMessageBox.critical(self, t("msg.error"), f"AI 映射失败:\n{e}")
        finally:
            self.ai_mapping_btn.setEnabled(True)
            self.ai_refresh_btn.setEnabled(True)
            self.unsetCursor()

        if not suggestions:
            QMessageBox.information(self, t("common.tip"),
                "AI 未返回有效的映射结果，请重试或更换模型")
            return

        self._apply_ai_suggestions(suggestions, rows)
        self._cleanup_empty_duplicate_rows()
        QMessageBox.information(self, t("msg.success"),
            "AI 映射完成，请检查并手动调整")

    def _cleanup_empty_duplicate_rows(self):
        """AI 映射后清理：移除"对应关系为空"且存在同名的行

        对应关系为空 = 源属性为空 或 目标属性为空；
        同名 = 该行非空一侧的属性名在表格其他行也出现。
        """
        # 统计源列 / 目标列各属性名出现次数
        source_counts = {}
        target_counts = {}
        for r in range(self.table.rowCount()):
            left_combo = self.table.cellWidget(r, 1)
            right_combo = self.table.cellWidget(r, 2)
            src = left_combo.currentText().strip() if left_combo else ""
            tgt = right_combo.currentText().strip() if right_combo else ""
            if src and src != "(无)":
                source_counts[src] = source_counts.get(src, 0) + 1
            if tgt and tgt != "(无)":
                target_counts[tgt] = target_counts.get(tgt, 0) + 1

        rows_to_remove = []
        for r in range(self.table.rowCount()):
            left_combo = self.table.cellWidget(r, 1)
            right_combo = self.table.cellWidget(r, 2)
            src = left_combo.currentText().strip() if left_combo else ""
            tgt = right_combo.currentText().strip() if right_combo else ""
            if src == "(无)":
                src = ""
            if tgt == "(无)":
                tgt = ""

            # 对应关系为空
            if not src or not tgt:
                # 同名判定：源属性名或目标属性名在表格其他行出现
                is_dup = bool(
                    (src and source_counts.get(src, 0) > 1)
                    or (tgt and target_counts.get(tgt, 0) > 1)
                )
                if is_dup:
                    rows_to_remove.append(r)

        for r in sorted(rows_to_remove, reverse=True):
            self.table.removeRow(r)

    def _apply_ai_suggestions(self, suggestions, rows=None):
        """将 AI 建议填入表格指定行（rows 为 None 时应用于所有有源属性的行）

        优先采用 AI 的判断：目标属性允许重复填入（不做同列去重跳过），
        由用户查看后手动调整；手动修改时的同列唯一性检测仍生效。
        """
        by_source = {}
        for s in suggestions:
            src = (s.get("source_attribute") or "").strip()
            if src and src not in by_source:
                by_source[src] = s

        for row in range(self.table.rowCount()):
            if rows is not None and row not in rows:
                continue
            left_combo = self.table.cellWidget(row, 1)
            if not left_combo:
                continue
            src = left_combo.currentText().strip()
            if not src or src == "(无)":
                continue
            sug = by_source.get(src)
            if not sug:
                continue

            # 目标属性（优先 AI 判断，允许重复）
            right_combo = self.table.cellWidget(row, 2)
            target = (sug.get("target_attribute") or "").strip()
            if right_combo and target:
                right_combo.blockSignals(True)
                self._set_combo_text(right_combo, target)
                right_combo.blockSignals(False)

            # 转换函数
            transform_combo = self.table.cellWidget(row, 3)
            t_name = (sug.get("transform") or "").strip()
            if transform_combo and t_name:
                display = self._display_transform_name(t_name)
                transform_combo.blockSignals(True)
                self._set_combo_text(transform_combo, display)
                transform_combo.blockSignals(False)
            # 参数：AI 建议携带时填入，否则按当前转换函数自动填默认参数
            param_edit = self.table.cellWidget(row, 4)
            if param_edit:
                sug_params = sug.get("parameters")
                if isinstance(sug_params, dict) and sug_params:
                    param_edit.setText(json.dumps(sug_params, ensure_ascii=False))
                elif transform_combo:
                    self._on_transform_changed(row, transform_combo.currentText())

    def _set_combo_text(self, combo, text):
        """设置可编辑下拉框文本，若选项不存在则先添加"""
        texts = [combo.itemText(i) for i in range(combo.count())]
        if text not in texts:
            combo.addItem(text)
        combo.setCurrentText(text)

    def _display_transform_name(self, english_name):
        """将转换函数英文标识转为中文显示名"""
        if not english_name:
            return ""
        for cn_name, en_name in MATERIAL_CONVERSION_FUNCTIONS.items():
            if en_name == english_name and not cn_name.isascii():
                return cn_name
        return english_name

    def clear_source_attributes(self):
        """清空所有源属性"""
        for row in range(self.table.rowCount()):
            left_combo = self.table.cellWidget(row, 1)
            if left_combo:
                left_combo.setCurrentText("(无)")

    def clear_target_attributes(self):
        """清空所有目标属性"""
        for row in range(self.table.rowCount()):
            right_combo = self.table.cellWidget(row, 2)
            if right_combo:
                right_combo.setCurrentText("(无)")

    def load_default_values(self):
        """载入目标节点属性当前值到默认值列

        流程：校验所选 Maya 节点类型与目标节点类型一致后，
        读取表格中各目标属性在该节点上的当前值，填入默认值列。
        """
        target_type = self.target_node_type.text() if self.target_node_type else ""
        if not target_type:
            QMessageBox.warning(self, t("msg.warning"),
                "请先设置目标节点类型")
            return

        selected = cmds.ls(selection=True)
        if not selected:
            QMessageBox.warning(self, t("msg.warning"),
                "请先在 Maya 中选择目标节点")
            return
        node = selected[0]
        node_type = cmds.nodeType(node)
        if node_type != target_type:
            QMessageBox.warning(self, t("msg.warning"),
                f"所选节点类型「{node_type}」与目标节点类型「{target_type}」不一致，无法载入默认值")
            return

        filled = 0
        for row in range(self.table.rowCount()):
            right_combo = self.table.cellWidget(row, 2)
            if not right_combo:
                continue
            tgt = right_combo.currentText().strip()
            if not tgt or tgt == "(无)":
                continue
            try:
                value = cmds.getAttr(f"{node}.{tgt}")
            except Exception:
                continue  # 属性不存在或不可读，跳过

            if isinstance(value, (list, tuple)):
                value_str = ", ".join(str(v) for v in value)
            elif isinstance(value, bool):
                value_str = "True" if value else "False"
            else:
                value_str = str(value)

            default_edit = self.table.cellWidget(row, 5)
            if default_edit:
                default_edit.setText(value_str)
                filled += 1

        QMessageBox.information(self, t("msg.success"),
            f"已从节点「{node}」载入 {filled} 个目标属性的默认值")

    def browse_node(self, is_source):
        """浏览并选择Maya节点"""
        # 获取当前选择的节点
        selected_nodes = cmds.ls(selection=True)
        if selected_nodes:
            node = selected_nodes[0]
            # 获取节点类型
            try:
                node_type = cmds.nodeType(node)
                if is_source:
                    self.source_node_name = node
                    self.source_node_type.setText(node_type)
                    # 加载源节点属性到表格
                    self.load_node_attributes(node, is_source)
                else:
                    self.target_node_name = node
                    self.target_node_type.setText(node_type)
                    # 加载目标节点属性到表格
                    self.load_node_attributes(node, False)
            except Exception as e:
                print(f"获取节点类型失败: {e}")
                QMessageBox.warning(self, t("msg.error"), t("qtool.matprop.msg.get_node_type_failed", e=e))
        else:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matprop.msg.select_node_in_maya"))

    def show_attribute_browser(self):
        """显示属性浏览器对话框"""
        selected_objects = cmds.ls(selection=True)
        if not selected_objects:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matprop.msg.select_object_in_maya"))
            return

        dialog = AttributeBrowserDialog(selected_objects[0], self)
        dialog.attribute_selected.connect(self._on_attribute_selected)
        dialog.show()

    def _on_attribute_selected(self, attribute_name, is_source):
        """属性浏览器选中的属性"""
        row = self.table.currentRow()
        if row < 0:
            row = self.table.rowCount()
            self.add_row()

        if is_source:
            combo = self.table.cellWidget(row, 1)
            if combo:
                # 检查属性是否已在下拉列表中
                if attribute_name not in [combo.itemText(i) for i in range(combo.count())]:
                    combo.addItem(attribute_name)
                combo.setCurrentText(attribute_name)
        else:
            combo = self.table.cellWidget(row, 2)
            if combo:
                # 检查属性是否已在下拉列表中
                if attribute_name not in [combo.itemText(i) for i in range(combo.count())]:
                    combo.addItem(attribute_name)
                combo.setCurrentText(attribute_name)


    
    def closeEvent(self, event):
        """窗口关闭时自动保存"""
        self.save_last_preset()
        super(MaterialPropertyMapper, self).closeEvent(event)

    def show_help_dialog(self):
        """显示帮助对话框"""
        import os
        import webbrowser
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = _help_path(os.path.join(plugin_root, "Assets", "help", "材质节点属性映射工具", "help.html"))
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            QtWidgets.QMessageBox.information(self, t("btn.help"),
                t("qtool.matprop.msg.help_not_found", path=help_path))


class AttributeBrowserDialog(QtWidgets.QDialog):
    """属性浏览器对话框"""

    attribute_selected = QtCore.Signal(str, bool)

    ATTRIBUTE_TYPE_MAP = {
        'double': 'qtool.matprop.attrtype.numeric',
        'float': 'qtool.matprop.attrtype.numeric',
        'int': 'qtool.matprop.attrtype.integer',
        'long': 'qtool.matprop.attrtype.integer',
        'short': 'qtool.matprop.attrtype.integer',
        'byte': 'qtool.matprop.attrtype.integer',
        'char': 'qtool.matprop.attrtype.integer',
        'bool': 'qtool.matprop.attrtype.boolean',
        'double2': 'qtool.matprop.attrtype.vector2',
        'double3': 'qtool.matprop.attrtype.vector3',
        'float2': 'qtool.matprop.attrtype.vector2',
        'float3': 'qtool.matprop.attrtype.vector3',
        'vector': 'qtool.matprop.attrtype.vector',
        'string': 'qtool.matprop.attrtype.string',
        'message': 'qtool.matprop.attrtype.message',
        'time': 'qtool.matprop.attrtype.time',
        'doubleArray': 'qtool.matprop.attrtype.numeric_array',
        'floatArray': 'qtool.matprop.attrtype.numeric_array',
        'intArray': 'qtool.matprop.attrtype.integer_array',
        'stringArray': 'qtool.matprop.attrtype.string_array',
        'vectorArray': 'qtool.matprop.attrtype.vector_array',
    }

    def __init__(self, node_name, parent=None):
        super(AttributeBrowserDialog, self).__init__(parent)
        self.node_name = node_name
        self.setWindowTitle(t("qtool.matprop.dialog.attribute_browser_title", node_name=node_name))
        self.setMinimumSize(500, 400)
        self.setup_ui()
        self.load_attributes()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        search_layout = QtWidgets.QHBoxLayout()
        search_layout.addWidget(QtWidgets.QLabel(t("qtool.matprop.label.search")))
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.textChanged.connect(self.filter_attributes)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            t("qtool.matprop.header.attr_name"),
            t("qtool.matprop.header.type"),
            t("qtool.matprop.header.current_value"),
            t("qtool.matprop.header.action")
        ])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.itemDoubleClicked.connect(self.on_attribute_double_clicked)

        header = self.table.horizontalHeader()
        if hasattr(header, 'setSectionResizeMode'):
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.Fixed)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            header.setSectionResizeMode(3, QHeaderView.Fixed)
        else:
            header.setResizeMode(0, QHeaderView.Stretch)
            header.setResizeMode(1, QHeaderView.Fixed)
            header.setResizeMode(2, QHeaderView.Stretch)
            header.setResizeMode(3, QHeaderView.Fixed)

        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(3, 100)

        layout.addWidget(self.table)

        button_layout = QtWidgets.QHBoxLayout()

        self.use_as_source_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.use_as_source"))
        self.use_as_source_btn.clicked.connect(lambda: self.use_attribute(True))
        button_layout.addWidget(self.use_as_source_btn)

        self.use_as_target_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.use_as_target"))
        self.use_as_target_btn.clicked.connect(lambda: self.use_attribute(False))
        button_layout.addWidget(self.use_as_target_btn)

        button_layout.addStretch()

        close_btn = QtWidgets.QPushButton(t("common.close"))
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def load_attributes(self):
        """加载节点的所有属性"""
        self.table.setRowCount(0)

        if not cmds.objExists(self.node_name):
            return

        attributes = cmds.listAttr(self.node_name, read=True, write=True)
        if not attributes:
            return

        for attr in sorted(attributes):
            try:
                attr_full = f"{self.node_name}.{attr}"
                attr_type = cmds.getAttr(attr_full, type=True)
                try:
                    value = cmds.getAttr(attr_full)
                    if isinstance(value, (list, tuple)):
                        value_str = str(value)
                    elif value is None:
                        value_str = "None"
                    else:
                        value_str = str(value)
                except RuntimeError:
                    value_str = "N/A"

                type_text = t(self.ATTRIBUTE_TYPE_MAP.get(attr_type, attr_type))

                row = self.table.rowCount()
                self.table.insertRow(row)

                self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(attr))
                self.table.item(row, 0).setData(Qt.UserRole, attr)

                self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(type_text))
                self.table.item(row, 1).setData(Qt.UserRole, attr_type)

                self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(value_str))

                use_btn = QtWidgets.QPushButton(t("btn.use"))
                use_btn.clicked.connect(partial(self.on_use_clicked, row))
                self.table.setCellWidget(row, 3, use_btn)

            except Exception as e:
                continue

        self.table.resizeRowsToContents()

    def filter_attributes(self, text):
        """根据搜索文本过滤属性"""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                attr_name = item.text().lower()
                text_lower = text.lower()
                self.table.setRowHidden(row, text_lower not in attr_name)

    def on_use_clicked(self, row):
        """使用按钮点击"""
        item = self.table.item(row, 0)
        if item:
            attr_name = item.text()
            is_source = True
            self.attribute_selected.emit(attr_name, is_source)

    def on_attribute_double_clicked(self, item, column):
        """双击属性"""
        row = item.row()
        attr_item = self.table.item(row, 0)
        if attr_item:
            attr_name = attr_item.text()
            self.attribute_selected.emit(attr_name, True)

    def use_attribute(self, is_source):
        """使用选中的属性"""
        selected_rows = self.table.selectedItems()
        if not selected_rows:
            QMessageBox.information(self, t("common.tip"), t("qtool.matprop.msg.select_attribute_first"))
            return

        row = selected_rows[0].row()
        attr_item = self.table.item(row, 0)
        if attr_item:
            attr_name = attr_item.text()
            self.attribute_selected.emit(attr_name, is_source)


def main():
    """QuickTool 入口函数"""
    import sys
    
    if QtWidgets is None:
        print("[MaterialMapper] 无法加载 PySide 模块")
        return

    print("[MaterialMapper] PySide 模块加载成功")

    app = QtWidgets.QApplication.instance()
    if not app:
        print("[MaterialMapper] 创建新的 QApplication")
        app = QtWidgets.QApplication(sys.argv)
        need_exec = True
    else:
        print("[MaterialMapper] 使用现有的 QApplication")
        need_exec = False

    parent_window = get_maya_main_window()
    print(f"[MaterialMapper] 父窗口: {parent_window}")

    print("[MaterialMapper] 创建对话框...")
    dialog = MaterialPropertyMapper(parent=parent_window)

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

    print("[MaterialMapper] 显示对话框...")
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    print("[MaterialMapper] 对话框已显示")

    if need_exec:
        print("[MaterialMapper] 进入事件循环...")
        app.exec()


# 主启动函数
def show_material_property_mapper():
    """显示材质属性映射工具窗口"""
    # 清理可能存在的旧窗口
    global mapper_window
    if 'mapper_window' in globals() and mapper_window is not None:
        try:
            mapper_window.close()
            mapper_window.deleteLater()
        except:
            pass
        mapper_window = None

    # 清理Qt应用中的旧窗口
    for widget in QtWidgets.QApplication.topLevelWidgets():
        try:
            if widget.__class__.__name__ == "MaterialPropertyMapper":
                widget.close()
                widget.deleteLater()
        except:
            pass

    try:
        mapper_window = MaterialPropertyMapper()
        mapper_window.show()

        # 确保窗口在最前面
        mapper_window.raise_()
        mapper_window.activateWindow()

        return mapper_window
    except Exception as e:
        print(f"创建窗口时出错: {str(e)}")
        import traceback
        traceback.print_exc()
        QMessageBox.critical(None, t("msg.error"), t("qtool.matprop.msg.create_window_failed", e=str(e)))
        return None


# 直接运行脚本时显示窗口
if __name__ == "__main__":
    try:
        # 清理可能存在的旧窗口
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if widget.__class__.__name__ == "MaterialPropertyMapper":
                widget.close()
                widget.deleteLater()
        
        # 显示新窗口
        main()
    except Exception as e:
        print(f"运行脚本时出错: {e}")
        # 尝试使用简单错误对话框
        try:
            error_msg = t("qtool.matprop.msg.script_run_failed", e=str(e))
            QtWidgets.QMessageBox.critical(None, t("msg.error"), error_msg)
        except Exception:
            print(error_msg)