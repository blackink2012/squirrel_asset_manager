import os
import json
import base64
import requests
from typing import Optional, Dict, Any, List


class AIAnalyzer:
    DEFAULT_MODEL = "qwen3-vl:8b"
    DEFAULT_HOST = "http://localhost:11434"

    def __init__(self, model: str = None, host: str = None):
        self._model = model or self.DEFAULT_MODEL
        self._host = host or self.DEFAULT_HOST
        self._asset_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "Assets"
        )
        self._prompt_dir = os.path.join(self._asset_dir, "prompt")
        self._config_path = os.path.join(self._asset_dir, "preset", "config.json")
        self._config_cache = None

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, value):
        self._model = value

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

    def analyze_image(self, image_bytes: bytes, category_type: str, language: str = "中文") -> Optional[Dict[str, Any]]:
        prompt = self._build_full_prompt(category_type)
        if not prompt:
            return None

        if language == "English":
            prompt = prompt + "\n\nImportant: Output all fields in English."
        else:
            prompt = prompt + "\n\n重要：所有输出字段请使用中文。"

        try:
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            response = self._call_ollama(base64_image, prompt)
            return self._parse_response(response)
        except Exception as e:
            print(f"[AI Analyzer] Error: {e}")
            return None

    def _call_ollama(self, base64_image: str, prompt: str) -> str:
        url = f"{self._host}/api/generate"

        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [base64_image],
            "stream": False,
            "options": {
                "temperature": 0.3,
                "max_tokens": 1024
            }
        }

        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        return response.json().get("response", "")

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
        try:
            response = requests.get(f"{self._host}/api/tags", timeout=10)
            response.raise_for_status()
            data = response.json()
            return [model.get("name", "") for model in data.get("models", [])]
        except Exception:
            return [self.DEFAULT_MODEL]

    def is_available(self) -> bool:
        try:
            response = requests.get(f"{self._host}/api/tags", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _load_json(filepath: str) -> Dict[str, Any]:
        if not os.path.isfile(filepath):
            return {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
