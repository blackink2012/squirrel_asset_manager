import os
import json
import base64
import requests
from typing import Optional, Dict, Any, List


class AIAnalyzer:
    """AI 分析器 - 支持多种后端服务

    统一通过 OpenAI 兼容的 /chat/completions 接口调用：
    - ollama  （本地，默认）：http://localhost:11434/v1
    - deepseek（云端 API）：https://api.deepseek.com/v1（纯文本模型，不支持图片）
    - qwen    （阿里云 DashScope 兼容模式）：https://dashscope.aliyuncs.com/compatible-mode/v1
    - zhipu   （智谱 BigModel）：https://open.bigmodel.cn/api/paas/v4
    - openai  （OpenAI）：https://api.openai.com/v1
    """

    DEFAULT_MODEL = "qwen3-vl:8b"
    DEFAULT_HOST = "http://localhost:11434"

    # 智谱视觉模型（仅这些支持图片输入；glm-4.7/glm-4.5 等文本模型不支持）
    _ZHIPU_VISION_MODELS = {
        "glm-4.5v",
        "glm-4.1v", "glm-4.1v-thinking", "glm-4.1v-thinking-flash",
        "glm-4v", "glm-4v-plus", "glm-4v-flash", "glm-4v-plus-0111",
        "glm-5.3-flash",
    }

    # 服务商预设（label 仅用于 UI 展示；base_url 为空时按 provider 默认地址）
    PROVIDERS = {
        "ollama": {
            "label": "Ollama（本地）",
            "base_url": "http://localhost:11434/v1",
            "needs_key": False,
            "vision": True,
            "default_model": "qwen3-vl:8b",
            "models": [],
        },
        "deepseek": {
            "label": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "needs_key": True,
            "vision": False,  # 默认文本模型；deepseek-v4-flash-vision-exp 支持图片
            "default_model": "deepseek-v4-flash",
            "models": [
                "deepseek-v4-flash",
                "deepseek-v4-pro",
                "deepseek-v4-flash-vision-exp",
            ],
        },
        "qwen": {
            "label": "通义千问（阿里云 DashScope）",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "needs_key": True,
            "vision": True,
            "default_model": "qwen-vl-max",
            "models": [
                "qwen-vl-max",
                "qwen-vl-max-latest",
                "qwen-vl-plus",
                "qwen-vl-plus-latest",
                "qwen2.5-vl-72b-instruct",
                "qwen3-vl-plus",
                "qwen3-vl-72b-instruct",
            ],
        },
        "zhipu": {
            "label": "智谱（BigModel）",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "needs_key": True,
            "vision": False,  # 仅视觉模型（glm-4.5v 等）支持图片；文本模型不支持
            "default_model": "glm-4.5v",
            "models": [
                # 视觉 / 多模态
                "glm-4.5v",
                "glm-5.3-flash",
                # 文本：glm-5 系列
                "glm-5.3",
                "glm-5.2",
                "glm-5.1",
                "glm-5",
                "glm-5-turbo",
                # 文本：glm-4 系列
                "glm-4.7",
                "glm-4.7-flash",
                "glm-4.6",
                "glm-4.5-air",
                "glm-4.5-flash",
            ],
        },
        "openai": {
            "label": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "needs_key": True,
            "vision": False,  # 仅 gpt-4 / gpt-5 系列支持图片；o 系列推理模型不支持
            "default_model": "gpt-4o-mini",
            "models": [
                # GPT-4 系列（支持视觉）
                "gpt-4o-mini",
                "gpt-4o",
                "gpt-4.1",
                "gpt-4.1-mini",
                "gpt-4.1-nano",
                "gpt-4.5",
                # GPT-5 统一模型（支持视觉）
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.5",
                "gpt-5.4",
                "gpt-5.3",
                "gpt-5",
                # o 系列推理模型（纯文本）
                "o3",
                "o3-mini",
                "o1",
            ],
        },
    }

    def __init__(self, provider: str = None, model: str = None, host: str = None,
                 api_key: str = None, base_url: str = None):
        settings = self._load_settings()
        self._provider = provider or settings.get("ai_provider") or "ollama"
        if self._provider not in self.PROVIDERS:
            self._provider = "ollama"
        self._provider_cfg = self.PROVIDERS[self._provider]

        # API Key（云端必填；各供应商独立存储，优先取当前供应商的 Key）
        self._api_key = api_key
        if self._api_key is None:
            try:
                from ..utils.settings import get_ai_api_key
                self._api_key = get_ai_api_key(settings, self._provider)
            except Exception:
                self._api_key = settings.get("ai_api_key", "")
        if self._provider_cfg["needs_key"] and not self._api_key:
            self._api_key = ""

        # 地址：优先显式传入 → 用户自定义 base_url → provider 默认
        if base_url:
            self._base_url = base_url.rstrip("/")
        elif host and self._provider == "ollama":
            self._base_url = host.rstrip("/") + "/v1"
        else:
            saved_base = settings.get("ai_base_url", "")
            # 设置中保存的地址若等于某服务商默认地址，则视为未自定义，改用当前服务商默认
            default_urls = {
                c["base_url"].rstrip("/")
                for c in self.PROVIDERS.values() if c.get("base_url")
            }
            if saved_base and saved_base.rstrip("/") not in default_urls:
                self._base_url = saved_base.rstrip("/")
            else:
                self._base_url = self._provider_cfg["base_url"]

        # 模型：显式传入 → 设置保存 → provider 默认
        saved_model = settings.get("ai_model", "")
        if model:
            self._model = model
        elif saved_model:
            self._model = saved_model
        else:
            self._model = self._provider_cfg["default_model"]

        self._asset_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "Assets"
        )
        self._prompt_dir = os.path.join(self._asset_dir, "prompt")
        self._config_path = os.path.join(self._asset_dir, "preset", "config.json")
        self._config_cache = None

    @staticmethod
    def _load_settings() -> Dict[str, Any]:
        """读取用户设置（失败时返回空字典，走默认值）"""
        try:
            from ..utils.settings import SettingsManager
            return SettingsManager().load()
        except Exception:
            return {}

    @property
    def provider(self):
        return self._provider

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, value):
        self._model = value

    @property
    def api_key(self):
        return self._api_key

    @property
    def base_url(self):
        return self._base_url

    @property
    def _config(self) -> Dict[str, Any]:
        if self._config_cache is None:
            self._config_cache = self._load_json(self._config_path)
        return self._config_cache

    def _read_prompt_file(self, category_type: str) -> Optional[str]:
        prompt_file = os.path.join(self._prompt_dir, f"{category_type}.json")
        data = self._load_json(prompt_file)
        if data and isinstance(data, dict):
            return data.get("prompt", "")
        return None

    def _get_sub_categories(self, top_level_type: str) -> List[str]:
        sub_cats = self._config.get("default_sub_categories", {})
        entries = sub_cats.get(top_level_type, [])
        return [e[0] for e in entries if isinstance(e, list) and len(e) > 0]

    def _build_full_prompt(self, category_type: str) -> str:
        prompt_template = self._read_prompt_file(category_type)
        if not prompt_template:
            prompt_template = self._read_prompt_file("materials") or ""

        sub_cats = self._get_sub_categories(category_type)
        if not sub_cats:
            sub_cats = self._get_sub_categories("materials")

        sub_cats_str = ", ".join(sub_cats)
        return prompt_template.replace("{sub_categories}", sub_cats_str)

    def _supports_vision(self) -> bool:
        """当前服务/模型是否支持图片输入"""
        if self._provider_cfg["vision"]:
            return True
        # DeepSeek 实验性视觉模型
        if self._provider == "deepseek" and "vision" in self._model.lower():
            return True
        # 智谱视觉模型（glm-4.5v 等；glm-4.7 等文本模型不支持图片）
        if self._provider == "zhipu" and self._model.lower() in self._ZHIPU_VISION_MODELS:
            return True
        # OpenAI：gpt-4 / gpt-5 系列支持图片；o 系列推理模型不支持
        if self._provider == "openai" and self._model.lower().startswith("gpt-"):
            return True
        return False

    def analyze_image(self, image_bytes: bytes, category_type: str, language: str = "中文", existing_tags: List[str] = None) -> Optional[Dict[str, Any]]:
        # 当前模型不支持图片时拒绝
        if not self._supports_vision():
            print(f"[AI Analyzer] 当前模型 {self._model} 不支持图片分析")
            return None

        prompt = self._build_full_prompt(category_type)
        if not prompt:
            return None

        if language == "English":
            prompt = prompt + "\n\nImportant: Output all fields in English."
        else:
            prompt = prompt + "\n\n重要：所有输出字段请使用中文。"

        try:
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            if self._provider == "ollama":
                # Ollama 原生 /api/chat 支持 think=False 真正关闭思考模式；
                # OpenAI 兼容端点会忽略 think 参数，思考内容仍会占用输出导致 JSON 截断
                response = self._chat_ollama_native(
                    prompt + "\n\n请分析这张图片。",
                    temperature=0.3,
                    max_tokens=4096,
                    image_base64=base64_image,
                )
            else:
                response = self._chat_completions(
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}},
                            {"type": "text", "text": "请分析这张图片。"},
                        ]},
                    ],
                    temperature=0.3,
                    max_tokens=4096,
                )
            result = self._parse_response(response)
            if result is None:
                return None

            if existing_tags:
                existing_set = set(existing_tags)
                new_tags = result.get("tags", [])
                merged_tags = list(existing_set.union(new_tags))
                result["tags"] = merged_tags

            return result
        except Exception as e:
            print(f"[AI Analyzer] Error: {e}")
            return None

    def _chat_completions(self, messages: List[Dict[str, Any]],
                          temperature: float = 0.3,
                          max_tokens: int = 1024) -> str:
        """调用 OpenAI 兼容的 /chat/completions 接口"""
        url = f"{self._base_url}/chat/completions"

        headers = {"Content-Type": "application/json"}
        api_key = self._api_key or "ollama"  # Ollama 不校验 key，占位即可
        headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self._provider == "deepseek":
            # DeepSeek 思考型模型默认开启思考，思考过程会占用输出导致最终结果为空，显式关闭
            payload["thinking"] = {"type": "disabled"}
        elif self._provider == "zhipu":
            model_l = self._model.lower()
            if model_l.startswith("glm-5.2") or model_l.startswith("glm-5.3"):
                # glm-5.2/5.3 始终思考，不支持 thinking.disabled（否则 400 错误码 1210）；
                # 用官方 reasoning_effort 参数降到最低档
                payload["reasoning_effort"] = "low"
            else:
                # glm-4.x / glm-5 / glm-5-turbo / glm-5.1 可显式关闭思考
                payload["thinking"] = {"type": "disabled"}
        elif self._provider == "openai":
            model_l = self._model.lower()
            # o 系列 / GPT-5 推理模型：不支持 temperature / max_tokens，
            # 改用 reasoning_effort（最低档）+ max_completion_tokens
            if model_l.startswith("gpt-5") or (len(model_l) > 1 and model_l[0] == "o" and model_l[1].isdigit()):
                payload.pop("temperature", None)
                payload.pop("max_tokens", None)
                payload["max_completion_tokens"] = max_tokens
                payload["reasoning_effort"] = "low"

        response = requests.post(url, json=payload, headers=headers, timeout=120)
        response.raise_for_status()
        data = response.json()
        try:
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            if not content:
                # 兜底：部分思考模型可能把内容放到 reasoning_content
                content = message.get("reasoning_content") or ""
            return content
        except (KeyError, IndexError, TypeError):
            return data.get("response", "")

    def _parse_response(self, response: str) -> Optional[Dict[str, Any]]:
        try:
            start_idx = response.find("{")
            end_idx = response.rfind("}") + 1
            if start_idx == -1 or end_idx == 0:
                return None

            json_str = response[start_idx:end_idx]
            result = json.loads(json_str)

            return {
                "name_cn": result.get("name_cn", ""),
                "tags": result.get("tags", []),
                "notes": result.get("notes", ""),
                "sub_category": result.get("sub_category", "")
            }
        except Exception as e:
            print(f"[AI Analyzer] Parse error: {e}")
            return None

    def get_available_models(self) -> List[str]:
        if self._provider == "ollama":
            try:
                ollama_base = self._provider_cfg["base_url"].rstrip("/").rsplit("/v1", 1)[0]
                response = requests.get(f"{ollama_base}/api/tags", timeout=10)
                response.raise_for_status()
                data = response.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                return models or [self._model]
            except Exception:
                return [self._model]

        # 云端服务商：实时查询 /models 接口（OpenAI 兼容格式），失败回退静态列表
        static = list(self._provider_cfg["models"]) or [self._model]
        fetched = []
        try:
            headers = {"Authorization": f"Bearer {self._api_key or 'ollama'}"}
            response = requests.get(f"{self._base_url}/models", headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            fetched = [m.get("id", "") for m in data.get("data", [])]
        except Exception:
            fetched = []
        if self._provider == "openai":
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

    def chat_text(self, prompt: str, temperature: float = 0.2,
                  max_tokens: int = 8192) -> str:
        """纯文本对话：向当前模型发送提示词并返回回复文本。

        与 _chat_completions 的区别：
        - Ollama 走原生 /api/chat 接口（think=False 可靠关闭思考型模型的
          思考过程，避免 JSON 等结构化输出被思考内容吞掉）
        - 云端服务走 OpenAI 兼容 /chat/completions 接口

        Returns:
            str: 模型回复文本（可能包含思考/前后缀，调用方自行解析结构化内容）
        """
        if self._provider == "ollama":
            return self._chat_ollama_native(prompt, temperature, max_tokens)
        return self._chat_openai_compat(prompt, temperature, max_tokens)

    def _chat_ollama_native(self, prompt: str, temperature: float, max_tokens: int,
                            image_base64: str = None) -> str:
        """走 Ollama 原生 /api/chat 接口（think=False 可靠关闭思考型模型的思考过程）"""
        ollama_base = self._base_url.rstrip("/").rsplit("/v1", 1)[0]
        url = f"{ollama_base}/api/chat"
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        if image_base64:
            # 视觉分析：Ollama 原生接口通过顶层 images 字段传 base64 图片
            payload["images"] = [image_base64]
        response = requests.post(url, json=payload, headers=headers, timeout=180)
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        text = message.get("content") or ""
        if not text:
            # 兜底：个别版本思考内容可能放 reasoning / reasoning_content
            text = message.get("reasoning_content") or message.get("reasoning") or ""
        return text

    def _chat_openai_compat(self, prompt: str, temperature: float, max_tokens: int) -> str:
        """走 OpenAI 兼容接口（DeepSeek / 通义千问）"""
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key or 'ollama'}",
        }
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self._provider == "deepseek":
            # DeepSeek 思考型模型默认开启思考，思考过程会占用输出导致最终结果为空，显式关闭
            payload["thinking"] = {"type": "disabled"}
        elif self._provider == "zhipu":
            model_l = self._model.lower()
            if model_l.startswith("glm-5.2") or model_l.startswith("glm-5.3"):
                # glm-5.2/5.3 始终思考，不支持 thinking.disabled（否则 400 错误码 1210）；
                # 用官方 reasoning_effort 参数降到最低档
                payload["reasoning_effort"] = "low"
            else:
                # glm-4.x / glm-5 / glm-5-turbo / glm-5.1 可显式关闭思考
                payload["thinking"] = {"type": "disabled"}
        elif self._provider == "openai":
            model_l = self._model.lower()
            # o 系列 / GPT-5 推理模型：不支持 temperature / max_tokens，
            # 改用 reasoning_effort（最低档）+ max_completion_tokens
            if model_l.startswith("gpt-5") or (len(model_l) > 1 and model_l[0] == "o" and model_l[1].isdigit()):
                payload.pop("temperature", None)
                payload.pop("max_tokens", None)
                payload["max_completion_tokens"] = max_tokens
                payload["reasoning_effort"] = "low"
        response = requests.post(url, json=payload, headers=headers, timeout=180)
        response.raise_for_status()
        data = response.json()
        try:
            message = data["choices"][0]["message"]
            text = message.get("content") or ""
            if not text:
                # 兜底：部分思考模型把内容放到 reasoning_content（DeepSeek / 智谱系）
                text = message.get("reasoning_content") or ""
        except (KeyError, IndexError, TypeError):
            text = data.get("response", "")
        return text

    @staticmethod
    def extract_json(text: str) -> Optional[Dict[str, Any]]:
        """从回复文本中提取第一个 JSON 对象（剥离开思考内容/前后缀文本）"""
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None

    def translate_tags(self, tags: List[str], target_language: str) -> List[str]:
        if not tags:
            return []

        language_map = {
            "中文": "中文（简体中文）",
            "English": "英文（English）",
        }
        target_lang_display = language_map.get(target_language, target_language)

        tags_str = ", ".join(tags)
        if target_language == "English":
            prompt = f"Please translate the following tags to English. Output only a JSON array of translated strings, nothing else.\n\nTags: {tags_str}"
        else:
            prompt = f"请将以下标签翻译为{target_lang_display}。只输出翻译后的JSON字符串数组，不要输出其他内容。\n\n标签: {tags_str}"

        try:
            # 走 chat_text：Ollama 原生 /api/chat（think=False 关闭思考），云端走 OpenAI 兼容接口
            result_str = self.chat_text(prompt, temperature=0.3, max_tokens=4096)
            return self._parse_translation_response(result_str)
        except Exception as e:
            print(f"[AI Analyzer] Translate error: {e}")
            return tags

    def _parse_translation_response(self, response: str) -> List[str]:
        try:
            start_idx = response.find("[")
            end_idx = response.rfind("]") + 1
            if start_idx == -1 or end_idx == 0:
                return []

            json_str = response[start_idx:end_idx]
            result = json.loads(json_str)

            if isinstance(result, list):
                return [str(item) for item in result]
            return []
        except Exception:
            return []

    def is_available(self) -> bool:
        if self._provider == "ollama":
            try:
                response = requests.get(
                    self._provider_cfg["base_url"].rstrip("/").rsplit("/v1", 1)[0] + "/api/tags",
                    timeout=5,
                )
                return response.status_code == 200
            except Exception:
                return False
        # 云端服务：仅检查 API Key 是否已配置
        return bool(self._api_key)

    @staticmethod
    def _load_json(filepath: str) -> Dict[str, Any]:
        if not os.path.isfile(filepath):
            return {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
