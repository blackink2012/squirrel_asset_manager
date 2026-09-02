# -*- coding: utf-8 -*-
"""AI 搜索意图分析 — 自然语言 → 资产库 tag 关键词

把用户自然语言搜索意图转成「概括性 tag 关键词」+ 子库类型：
- 文本意图分析：AIAnalyzer.chat_text() + extract_json()
- 图片意图分析（图搜图）：自包含视觉对话（复用 AIAnalyzer 既有接口，不改动其源码）
- AI 不可用 / 解析失败时降级为本地分词，保证搜索功能不中断
"""
import base64
import re
import time

try:
    from .ai_analyzer import AIAnalyzer
except ImportError:
    AIAnalyzer = None

_SUB_LIBRARIES = ("materials", "models", "textures", "lights", "scenes", "hdr")

# ── AI 搜索独立设置命名空间（与主 UI 的 AI 分析设置互不干扰） ──
_AI_SEARCH_PROVIDER_KEY = "ai_search_provider"
_AI_SEARCH_BASE_URL_KEY = "ai_search_base_url"
_AI_SEARCH_MODEL_KEY = "ai_search_model"
_AI_SEARCH_API_KEYS_KEY = "ai_search_api_keys"
_WISHLIST_KEY = "ai_search_wishlist"


def load_ai_search_settings() -> dict:
    """读取 AI 搜索自己的设置（读取失败返回空字典）"""
    try:
        from ..utils.settings import SettingsManager
        return SettingsManager().load() or {}
    except Exception:
        return {}


def get_ai_search_api_key(settings: dict, provider: str) -> str:
    """读取 AI 搜索独立存储的指定服务商 API Key"""
    keys = settings.get(_AI_SEARCH_API_KEYS_KEY)
    if isinstance(keys, dict):
        return keys.get(provider, "")
    return ""


def set_ai_search_api_key(settings: dict, provider: str, key: str) -> dict:
    """把 API Key 存入 AI 搜索独立命名空间，返回新设置副本"""
    out = dict(settings)
    keys = dict(out.get(_AI_SEARCH_API_KEYS_KEY) or {})
    keys[provider] = key
    out[_AI_SEARCH_API_KEYS_KEY] = keys
    return out


def save_ai_search_config(config: dict) -> None:
    """保存 AI 搜索自己的模型配置（provider/base_url/model/api_key）"""
    try:
        from ..utils.settings import SettingsManager
    except Exception:
        return
    try:
        settings = load_ai_search_settings()
        provider = config.get("provider", "")
        if provider:
            settings[_AI_SEARCH_PROVIDER_KEY] = provider
            settings = set_ai_search_api_key(
                settings, provider, config.get("api_key", ""))
        settings[_AI_SEARCH_BASE_URL_KEY] = config.get("base_url", "")
        settings[_AI_SEARCH_MODEL_KEY] = config.get("model", "")
        SettingsManager().save(settings)
    except Exception as e:
        print(f"[AISearch] 保存设置失败: {e}")


def load_wishlist() -> list:
    """读取心愿单（AI 回复中收藏的资产），跨会话持久化"""
    try:
        from ..utils.settings import SettingsManager
        items = SettingsManager().load().get(_WISHLIST_KEY) or []
        if isinstance(items, list):
            return [it for it in items if isinstance(it, dict)]
    except Exception:
        pass
    return []


def save_wishlist(items: list) -> None:
    """保存心愿单（整体覆盖，只存显示与路径所需字段）"""
    try:
        from ..utils.settings import SettingsManager
    except Exception:
        return
    try:
        settings = load_ai_search_settings()
        settings[_WISHLIST_KEY] = list(items)
        SettingsManager().save(settings)
    except Exception as e:
        print(f"[AISearch] 保存心愿单失败: {e}")


def _score_material(m, keywords) -> int:
    """资产相关度打分（含备注 notes）：命中的关键词片段数量"""
    if not keywords:
        return 0
    hay = " ".join([
        m.name_cn or "", m.name or "", " ".join(m.tags or []),
        m.category or "", m.node_type or "", m.sub_library or "",
        getattr(m, "notes", "") or "",
    ]).lower()
    return sum(1 for kw in keywords if kw.lower() in hay)


def merge_notes_matches(manager, mats, keywords, sub_library=""):
    """把「仅备注(notes)命中关键词」的资产并入结果（不改动全局搜索逻辑）"""
    if not manager or not keywords:
        return mats
    try:
        if sub_library:
            candidates = manager.search({"sub_library": sub_library})
        else:
            candidates = manager.search("")
        base_ids = {m.id for m in mats}
        extra = []
        for m in candidates:
            if m.id in base_ids:
                continue
            notes = (getattr(m, "notes", "") or "").lower()
            if notes and any(kw.lower() in notes for kw in keywords):
                extra.append(m)
        if extra:
            return list(mats) + extra
    except Exception as e:
        print(f"[AISearch] 备注匹配异常: {e}")
    return mats


# 常用材质/搜索词中英双向对照表：搜索时把关键词展开成中英两种写法，
# 保证资产库无论中文命名还是英文命名都能命中（AI 返回任一语言均有效）。
_BILINGUAL_TERMS = {
    "金属": ["metal", "metallic"],
    "metal": ["金属", "metallic"],
    "metallic": ["金属", "metal"],
    "木": ["wood", "wooden", "木质"],
    "wood": ["木", "木质"],
    "木质": ["木", "wood"],
    "木纹": ["wood grain", "grain"],
    "wood grain": ["木纹", "grain"],
    "锈": ["rust", "rusty", "生锈", "锈蚀"],
    "rust": ["锈", "锈蚀", "生锈"],
    "rusty": ["锈", "锈蚀"],
    "锈蚀": ["锈", "rust"],
    "生锈": ["锈", "rust"],
    "塑料": ["plastic"],
    "plastic": ["塑料"],
    "玻璃": ["glass"],
    "glass": ["玻璃"],
    "皮": ["leather", "皮革"],
    "皮革": ["皮", "leather"],
    "leather": ["皮", "皮革"],
    "布": ["cloth", "fabric", "织物", "布料"],
    "cloth": ["布", "织物"],
    "fabric": ["布", "织物"],
    "织物": ["布", "cloth"],
    "石": ["stone", "rock", "石头"],
    "stone": ["石", "石头"],
    "石头": ["石", "stone"],
    "水泥": ["cement", "concrete"],
    "混凝土": ["concrete", "水泥"],
    "cement": ["水泥", "混凝土"],
    "concrete": ["混凝土", "水泥"],
    "橡胶": ["rubber"],
    "rubber": ["橡胶"],
    "陶瓷": ["ceramic"],
    "ceramic": ["陶瓷"],
    "金": ["gold"],
    "gold": ["金"],
    "银": ["silver"],
    "silver": ["银"],
    "铜": ["copper"],
    "copper": ["铜"],
    "铁": ["iron"],
    "iron": ["铁"],
    "钢": ["steel"],
    "steel": ["钢"],
    "铝": ["aluminum", "aluminium"],
    "aluminum": ["铝"],
    "aluminium": ["铝"],
    "铬": ["chrome", "chromium"],
    "chrome": ["铬"],
    "chromium": ["铬"],
    "漆": ["paint", "油漆"],
    "油漆": ["漆", "paint"],
    "paint": ["漆", "油漆"],
    "光泽": ["glossy", "gloss", "高光"],
    "glossy": ["光泽", "高光"],
    "高光": ["光泽", "glossy"],
    "哑光": ["matte", "亚光"],
    "matte": ["哑光", "亚光"],
    "亚光": ["哑光", "matte"],
    "磨砂": ["frosted", "sandblasted"],
    "frosted": ["磨砂"],
    "sandblasted": ["磨砂"],
    "拉丝": ["brushed"],
    "brushed": ["拉丝"],
    "镜面": ["mirror", "mirrored", "镜子"],
    "mirror": ["镜面", "镜子"],
    "镜子": ["镜面", "mirror"],
    "苔藓": ["moss"],
    "moss": ["苔藓"],
    "裂纹": ["crack"],
    "crack": ["裂纹"],
    "划痕": ["scratch"],
    "scratch": ["划痕"],
    "旧": ["aged", "worn", "做旧"],
    "aged": ["旧", "做旧"],
    "worn": ["旧", "做旧"],
    "做旧": ["旧", "aged"],
    "透明": ["transparent"],
    "transparent": ["透明"],
    "半透明": ["translucent"],
    "translucent": ["半透明"],
    "发光": ["glow", "emissive", "自发光"],
    "glow": ["发光", "自发光"],
    "emissive": ["发光", "自发光"],
    "自发光": ["发光", "glow", "emissive"],
    "霓虹": ["neon"],
    "neon": ["霓虹"],
    "水": ["water"],
    "water": ["水"],
    "冰": ["ice"],
    "ice": ["冰"],
    "雪": ["snow"],
    "snow": ["雪"],
    "沙": ["sand"],
    "sand": ["沙"],
    "泥": ["mud", "dirt", "泥巴"],
    "mud": ["泥", "泥巴"],
    "dirt": ["泥", "污渍"],
    "砖": ["brick"],
    "brick": ["砖"],
    "大理石": ["marble"],
    "marble": ["大理石"],
    "花岗岩": ["granite"],
    "granite": ["花岗岩"],
    "污渍": ["stain", "dirt"],
    "stain": ["污渍"],
    "碳纤维": ["carbon fiber"],
    "carbon fiber": ["碳纤维"],
    "亚麻": ["linen"],
    "linen": ["亚麻"],
    "天鹅绒": ["velvet"],
    "velvet": ["天鹅绒"],
    "丝绸": ["silk"],
    "silk": ["丝绸"],
    "牛仔": ["denim", "牛仔布"],
    "denim": ["牛仔"],
    "毛毡": ["felt"],
    "felt": ["毛毡"],
    "泡沫": ["foam"],
    "foam": ["泡沫"],
    "纸": ["paper"],
    "paper": ["纸"],
    "软木": ["cork"],
    "cork": ["软木"],
}


def _expand_bilingual(keywords):
    """中英文双向展开：命中对照表的词补充另一语言写法，保证中英文搜索都能命中"""
    out = []
    for kw in keywords:
        k = str(kw).strip().lower()
        if k in _BILINGUAL_TERMS:
            for e in _BILINGUAL_TERMS[k]:
                if e not in out:
                    out.append(e)
        if kw not in out:
            out.append(kw)
    return out


def search_materials_with_notes(manager, keywords, sub_library="", limit=30):
    """按关键词搜索资产（备注也纳入匹配）+ 相关度排序，返回 to_dict 列表。

    AI 搜索工具对话框与主搜索框的 AI 语义搜索共用此函数。
    关键词自动做中英双向展开，中英文命名都能命中。
    """
    if not manager or not keywords:
        return []
    keywords = _expand_bilingual(keywords)
    query = {"keyword": " ".join(keywords)}
    sub_libs = getattr(manager, "ASSET_SUB_LIBRARIES", {}) or {}
    if sub_library in sub_libs:
        query["sub_library"] = sub_library
    try:
        mats = manager.search(query)
    except Exception as e:
        print(f"[AISearch] 搜索失败: {e}")
        return []
    mats = merge_notes_matches(manager, mats, keywords, sub_library)
    scored = sorted(mats, key=lambda m: _score_material(m, keywords), reverse=True)
    try:
        return [m.to_dict(include_thumb=False) for m in scored[:limit]]
    except Exception:
        return []


# 实时模型列表短时缓存（部分机器 Ollama /api/tags 响应 2s+，缓存避免重复等待）
_model_cache = {}  # key -> (fetched_at, [models])


def fetch_available_models(provider, api_key="", base_url="", _ttl=30):
    """实时获取模型列表：Ollama 走 /api/tags，云端走 OpenAI 兼容 /models。失败返回 []。

    带 30s 短时缓存（Ollama 固定 key；云端按 base_url），减少重复请求。
    """
    if not AIAnalyzer:
        return []
    from ..utils.http_compat import requests  # Maya 2027 无 requests 时回退标准库 urllib
    cache_key = provider if provider == "ollama" else (provider, (base_url or "").rstrip("/"))
    now = time.time()
    cached = _model_cache.get(cache_key)
    if cached and (now - cached[0]) < _ttl:
        return list(cached[1])
    try:
        if provider == "ollama":
            probe = AIAnalyzer(provider="ollama")
            base = probe._base_url.rstrip("/").rsplit("/v1", 1)[0]
            r = requests.get(base + "/api/tags", timeout=10)
            r.raise_for_status()
            fetched = [m.get("name", "") for m in r.json().get("models", [])
                       if m.get("name")]
        else:
            url = (base_url or "").rstrip("/")
            if not url:
                cfg = AIAnalyzer.PROVIDERS.get(provider, {}) or {}
                url = (cfg.get("base_url") or "").rstrip("/")
            url = url + "/models"
            headers = {"Authorization": "Bearer %s" % (api_key or "ollama")}
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            fetched = [m.get("id", "") for m in r.json().get("data", [])
                       if m.get("id")]
            if provider == "openai":
                # OpenAI /models 含嵌入/语音/图像生成等非对话模型，仅保留对话模型
                fetched = [
                    m for m in fetched
                    if m.startswith("gpt-") or m.startswith("chatgpt-")
                    or (len(m) > 1 and m[0] == "o" and m[1].isdigit())
                ]
        _model_cache[cache_key] = (time.time(), list(fetched))
        return fetched
    except Exception as e:
        print(f"[AISearch] 获取模型列表失败: {e}")
        return []


class AISearchAssistant:
    """把自然语言搜索意图转成资产库 tag 关键词（概括性，强制中英成对）。"""

    _INTENT_PROMPT = (
        "你是资产库搜索助手。用户用自然语言描述想要的资产，"
        "请提取 3~6 个「概括性搜索关键词」(tag) 并判断资产子库类型。\n"
        "资产库中英文命名都可能存在，必须每个概念同时给出英文(en)和中文(zh)，"
        "只输出严格 JSON，不要输出其他内容：\n"
        '{"keywords": [{"en": "metal", "zh": "金属"}, {"en": "wood", "zh": "木"}], '
        '"sub_library": "materials", "note": "一句话说明"}\n'
        "sub_library 取值：materials | models | textures | lights | scenes | hdr\n"
    )

    _IMAGE_PROMPT = (
        "你是资产库搜索助手。请观察这张参考图片，提取 3~6 个「概括性搜索关键词」(tag) "
        "并判断资产子库类型。\n"
        "资产库中英文命名都可能存在，必须每个概念同时给出英文(en)和中文(zh)，"
        "只输出严格 JSON，不要输出其他内容：\n"
        '{"keywords": [{"en": "metal", "zh": "金属"}], '
        '"sub_library": "materials", "note": "一句话说明"}\n'
        "sub_library 取值：materials | models | textures | lights | scenes | hdr\n"
    )

    def __init__(self, analyzer=None, fetch_default=True):
        if analyzer is not None:
            self._analyzer = analyzer
        else:
            self._analyzer = self._build_analyzer(fetch_default=fetch_default)

    def _build_analyzer(self, fetch_default=True):
        """按 AI 搜索独立设置构建 AIAnalyzer；未配置时沿用共享设置（向后兼容）。

        fetch_default=True 时，无预设则从实时模型列表取默认（仅应在后台线程使用，
        避免 Ollama /api/tags 2s+ 响应阻塞 UI）；False 时直接沿用主 UI 模型，秒开。
        """
        if not AIAnalyzer:
            return None
        settings = load_ai_search_settings()
        provider = (settings.get(_AI_SEARCH_PROVIDER_KEY) or "").strip()
        if not provider:
            # 尚未在 AI 搜索里配置 → 沿用共享设置（首次使用体验）
            try:
                return AIAnalyzer()
            except Exception:
                return None
        cfg = AIAnalyzer.PROVIDERS.get(provider, {}) or {}
        api_key = get_ai_search_api_key(settings, provider) or ""
        base_url = (settings.get(_AI_SEARCH_BASE_URL_KEY) or "").strip()
        if not base_url:
            base_url = cfg.get("base_url", "")
        model = (settings.get(_AI_SEARCH_MODEL_KEY) or "").strip()
        if not model and fetch_default:
            # 未保存模型且允许联网 → 从实时模型列表取默认：
            # 优先沿用主 UI 已配置且真实存在的模型，其次取列表第一个
            try:
                models = fetch_available_models(provider, api_key, base_url)
                if models:
                    shared = (settings.get("ai_model") or "").strip()
                    model = shared if shared in models else models[0]
                else:
                    model = ""
            except Exception:
                model = ""
        if not model:
            # 无预设（未走实时获取 / 获取失败）→ 沿用主 UI 模型，避免同步联网阻塞
            model = (settings.get("ai_model") or "").strip()
        if not model:
            # 最后兜底服务商默认（可能指向不存在的模型，由错误提示引导）
            model = cfg.get("default_model", "")
        try:
            return AIAnalyzer(provider=provider, model=model,
                              api_key=api_key, base_url=base_url or None)
        except Exception as e:
            print(f"[AISearch] 构建分析器失败: {e}")
            try:
                return AIAnalyzer()
            except Exception:
                return None

    @property
    def analyzer(self):
        return self._analyzer

    @property
    def available(self) -> bool:
        """AI 服务是否可用（决定是否走意图分析，否则降级分词）"""
        if not self._analyzer:
            return False
        try:
            return self._analyzer.is_available()
        except Exception:
            return False

    @property
    def supports_vision(self) -> bool:
        """当前模型是否支持图片输入（供图搜图能力判断）"""
        if not self._analyzer:
            return False
        try:
            return self._analyzer._supports_vision()
        except Exception:
            return False

    def _vision_chat(self, prompt, image_bytes_list,
                     temperature=0.2, max_tokens=2048):
        """视觉对话：发送一张或多张图片 + 提示词，返回模型文本回复。

        自包含实现（不改动 AIAnalyzer 源码）：Ollama 走原生 /api/chat 的
        images 列表参数，云端走 OpenAI 兼容的多 image_url 消息。

        Args:
            image_bytes_list: bytes 或 [bytes, ...]（可多张）

        Returns:
            str: 模型回复文本；当前模型不支持图片时返回 None
        """
        analyzer = self._analyzer
        if analyzer is None or not self.supports_vision:
            return None
        if isinstance(image_bytes_list, (bytes, bytearray)):
            image_bytes_list = [bytes(image_bytes_list)]
        images_b64 = [
            base64.b64encode(b).decode('utf-8')
            for b in image_bytes_list if b
        ]
        if not images_b64:
            return None
        if analyzer._provider == "ollama":
            return self._ollama_native_vision(
                prompt, temperature, max_tokens, images_b64)
        content = [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}}
            for b64 in images_b64
        ]
        content.append(
            {"type": "text",
             "text": "请按提示词分析这%d张图片。" % len(images_b64)})
        return analyzer._chat_completions(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": content},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _ollama_native_vision(self, prompt, temperature, max_tokens, images_b64):
        """自包含的 Ollama 原生多图对话（/api/chat，think=False，不改动 AIAnalyzer）"""
        analyzer = self._analyzer
        ollama_base = analyzer._base_url.rstrip("/").rsplit("/v1", 1)[0]
        url = f"{ollama_base}/api/chat"
        payload = {
            "model": analyzer._model,
            "messages": [{"role": "user", "content": prompt, "images": images_b64}],
            "stream": False,
            "think": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
            "images": images_b64,  # 旧版 Ollama 兼容（新版会静默忽略顶层 images）
        }
        from ..utils.http_compat import requests  # Maya 2027 无 requests 时回退标准库 urllib
        response = requests.post(
            url, json=payload, headers={"Content-Type": "application/json"},
            timeout=180)
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        text = message.get("content") or ""
        if not text:
            text = message.get("reasoning_content") or message.get("reasoning") or ""
        return text

    # ── 文本意图分析 ────────────────────────────────────

    def analyze_intent(self, text, context=None):
        """把用户自然语言解析为搜索关键词。

        Args:
            text: 用户输入的自然语言
            context: 上一轮 {"text", "keywords", "sub_library"}，用于多轮细化

        Returns:
            dict: {"keywords": [...], "sub_library": str, "note": str}
                  失败时降级为分词关键词（keywords 可能为空列表）
        """
        if not text:
            return {"keywords": [], "sub_library": "", "note": ""}

        if self._analyzer:
            try:
                prompt = self._build_text_prompt(text, context)
                raw = self._analyzer.chat_text(
                    prompt, temperature=0.1, max_tokens=1024)
                parsed = AIAnalyzer.extract_json(raw)
                if isinstance(parsed, dict):
                    keywords = self._clean_keywords(parsed.get("keywords"))
                    keywords = self._ensure_bilingual(keywords)  # 强制中英成对
                    sub_lib = self._clean_sub_library(parsed.get("sub_library"))
                    if keywords:
                        return {"keywords": keywords, "sub_library": sub_lib,
                                "note": parsed.get("note", "")}
            except Exception as e:
                print(f"[AISearch] 意图分析失败: {e}")
                if self._is_model_not_found(e):
                    # 模型未安装/不存在：降级搜索 + 标记错误供 UI 提示
                    return {"keywords": self._fallback_keywords(text),
                            "sub_library": "", "note": "",
                            "error": "model_not_found"}

        return {"keywords": self._fallback_keywords(text),
                "sub_library": "", "note": ""}

    # ── 图片意图分析（图搜图） ───────────────────────────

    def analyze_image_intent(self, image_bytes_list, text="", context=None):
        """分析一张或多张参考图片，提取搜索关键词。

        Args:
            image_bytes_list: bytes 或 [bytes, ...]（可多张，与文本一起分析）
            text: 用户附加的自然语言说明
            context: 上一轮 {"text", "keywords", "sub_library"}，用于多轮细化

        Returns:
            dict: {"keywords": [...], "sub_library": str, "note": str}
            当前模型不支持视觉 / 分析失败时返回 None
        """
        if not self._analyzer:
            return None
        if not self.supports_vision:
            return None
        if isinstance(image_bytes_list, (bytes, bytearray)):
            image_bytes_list = [bytes(image_bytes_list)]
        if not image_bytes_list:
            return None
        try:
            prompt = self._build_image_prompt(text, context, len(image_bytes_list))
            raw = self._vision_chat(
                prompt, image_bytes_list, temperature=0.1, max_tokens=1024)
            if not raw:
                return None
            parsed = AIAnalyzer.extract_json(raw)
            if isinstance(parsed, dict):
                keywords = self._clean_keywords(parsed.get("keywords"))
                keywords = self._ensure_bilingual(keywords)  # 强制中英成对
                sub_lib = self._clean_sub_library(parsed.get("sub_library"))
                if keywords:
                    return {"keywords": keywords, "sub_library": sub_lib,
                            "note": parsed.get("note", "")}
        except Exception as e:
            print(f"[AISearch] 图片意图分析失败: {e}")
        return None

    # ── prompt 构建 ─────────────────────────────────────

    def _build_text_prompt(self, text, context=None):
        prompt = self._INTENT_PROMPT
        if context and context.get("keywords"):
            prompt += (
                "\n多轮细化：上一轮文本「{t}」，关键词 {k}。"
                "用户现在可能是在原基础上细化/替换，请保留仍相关的关键词并调整。\n"
            ).format(
                t=context.get("text", ""),
                k="、".join(context["keywords"]),
            )
        prompt += "\n用户输入：{text}\n".format(text=text)
        return prompt

    def _build_image_prompt(self, text, context=None, image_count=1):
        prompt = self._IMAGE_PROMPT
        if image_count > 1:
            # 注意：JSON 模板含大括号，不能用 str.format()，改用 % 格式化
            prompt = (
                "你是资产库搜索助手。请综合观察这%d张参考图片，"
                "提取 3~6 个「概括性搜索关键词」(tag) 并判断资产子库类型。\n"
                "资产库中英文命名都可能存在，必须每个概念同时给出英文(en)和中文(zh)，"
                "只输出严格 JSON，不要输出其他内容：\n"
                '{"keywords": [{"en": "metal", "zh": "金属"}], '
                '"sub_library": "materials", "note": "一句话说明"}\n'
                "sub_library 取值：materials | models | textures | lights | scenes | hdr\n"
            ) % image_count
        if text and text.strip():
            prompt += "\n用户附加说明：{text}\n".format(text=text.strip())
        return prompt

    # ── 清洗与降级 ──────────────────────────────────────

    @staticmethod
    def _clean_keywords(keywords):
        """清洗 AI 返回的关键词列表。

        兼容两种格式：纯字符串列表，或成对对象 [{"en": "...", "zh": "..."}]；
        成对对象展开为中英两个词。去空白/重复，限制 12 个（双语成对场景）。
        """
        if not isinstance(keywords, list):
            return []
        out = []
        for kw in keywords:
            if isinstance(kw, dict):
                for k in (kw.get("en"), kw.get("zh")):
                    k = str(k).strip().strip('"\'') if k else ""
                    if k and k not in out:
                        out.append(k)
            else:
                k = str(kw).strip().strip('"\'')
                if k and k not in out:
                    out.append(k)
        return out[:12]

    @staticmethod
    def _clean_sub_library(sub_lib):
        """校验并规范化子库类型，非法值返回空串"""
        if not isinstance(sub_lib, str):
            return ""
        sub_lib = sub_lib.strip().lower()
        return sub_lib if sub_lib in _SUB_LIBRARIES else ""

    def _ensure_bilingual(self, keywords):
        """强制关键词中英成对：本地对照表展开 + 单语时调用模型补齐另一种语言。

        模型即使只回英文（或只回中文），也会补上中文（或英文）写法，
        保证搜索命中中英文命名；翻译失败时原样返回。
        """
        keywords = _expand_bilingual(keywords)
        if not keywords:
            return keywords
        has_cjk = any(re.search(r"[\u4e00-\u9fff]", k) for k in keywords)
        has_ascii = any(re.search(r"[A-Za-z]", k) for k in keywords)
        if has_cjk and has_ascii:
            return keywords  # 已含中英两种写法，无需再调模型
        try:
            pairs = self._translate_pairs(keywords)
        except Exception:
            return keywords
        out = list(keywords)
        for en, zh in pairs:
            if en and en not in out:
                out.append(en)
            if zh and zh not in out:
                out.append(zh)
        return out[:12]

    def _translate_pairs(self, words):
        """调用模型把关键词同时译成中英成对，返回 [(en, zh), ...]，失败返回 []。"""
        if not self._analyzer:
            return []
        prompt = (
            "你是翻译助手。请把下面每个搜索关键词同时给出英文(en)和中文(zh)写法，"
            "保持顺序，只输出严格 JSON：\n"
            '{"keywords": [{"en": "metal", "zh": "金属"}, ...]}\n'
            "关键词：" + "、".join(words)
        )
        raw = self._analyzer.chat_text(prompt, temperature=0.1, max_tokens=512)
        parsed = AIAnalyzer.extract_json(raw) if raw else None
        if not isinstance(parsed, dict):
            return []
        pairs = []
        for it in parsed.get("keywords") or []:
            if isinstance(it, dict):
                pairs.append((str(it.get("en") or "").strip(),
                              str(it.get("zh") or "").strip()))
        return pairs

    @staticmethod
    def _is_model_not_found(e) -> bool:
        """判断异常是否为服务端返回「模型不存在」(404 not found)。

        Ollama 对未安装的模型请求 /api/chat 会返回 404 + "model ... not found"。
        """
        try:
            from ..utils.http_compat import requests  # noqa: F401
            if isinstance(e, requests.HTTPError):
                resp = getattr(e, "response", None)
                if resp is not None and resp.status_code == 404:
                    body = (resp.text or "").lower()
                    return ("not found" in body or "不存在" in body
                            or "not exist" in body or "no such" in body)
        except Exception:
            pass
        return False

    @staticmethod
    def _fallback_keywords(text):
        """AI 不可用时的分词降级：去常见引导词后按分隔符切分。"""
        if not text:
            return []
        t = re.sub(
            r"^(帮我|请|想要|想找|找一个|找|有没有|来一个|搜索|搜一下|查看|查一下)[的找]*",
            "", text.strip())
        t = re.sub(r"的材质|材质|风格|质感|图片|资产|模型", "", t)
        tokens = [
            x.strip() for x in re.split(r"[\s,，。、；;:：/]+", t) if x.strip()
        ]
        return tokens[:6]
