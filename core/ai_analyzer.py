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
    """

    DEFAULT_MODEL = "qwen3-vl:8b"
    DEFAULT_HOST = "http://localhost:11434"

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
    }

    def __init__(self, provider: str = None, model: str = None, host: str = None,
                 api_key: str = None, base_url: str = None):
        settings = self._load_settings()
        self._provider = provider or settings.get("ai_provider") or "ollama"
        if self._provider not in self.PROVIDERS:
            self._provider = "ollama"
        self._provider_cfg = self.PROVIDERS[self._provider]

        # API Key（云端必填）
        self._api_key = api_key if api_key is not None else settings.get("ai_api_key", "")
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
            response = self._chat_completions(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}},
                        {"type": "text", "text": "请分析这张图片。"},
                    ]},
                ],
                temperature=0.3,
                max_tokens=1024,
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
        return list(self._provider_cfg["models"]) or [self._model]

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
            result_str = self._chat_completions(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024,
            )
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
