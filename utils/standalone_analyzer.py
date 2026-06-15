#!/usr/bin/env python3
"""
Standalone Image / Video Analyzer
==================================
纯代码模块，不依赖项目的 UI、数据库、配置文件。
输入：图片/视频路径 + 可选 prompt + 可选 JSON 标签字典
输出：JSON 分析结果

依赖：
    pip install openai
    系统需安装 ffmpeg / ffprobe

用法速览:
    >>> from standalone_analyzer import StandaloneAnalyzer
    >>> a = StandaloneAnalyzer(base_url="http://localhost:11434/v1", model="qwen3-vl:8b")
    >>> img_r = a.analyze_image("photo.jpg")
    >>> vid_r = a.analyze_video("movie.mp4")
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Optional, Union

# ---------------------------------------------------------------------------
# 内嵌 Prompt（不再依赖外部 .txt 文件）
# ---------------------------------------------------------------------------

_IMAGE_ANALYSIS_PROMPT = """你是一个专业的图片分析助手。请分析这张图片，并以严格的JSON格式输出分析结果。

输出JSON格式要求：
{
  "category": "图片的分类，从风景/人物/产品/抽象/界面中选择最匹配的一个",
  "tags": ["标签1", "标签2", "标签3"],
  "description": "对图片内容的自然语言简要描述，50-100字",
  "quality_score": 数字1-10，评估图片的整体质量,
  "subjects": ["画面中出现的主体元素名词，如太阳、山脉、狗、道路、海、人物、电视、水杯、房子、桌子、树、森林、玩具熊、书等名词，列出3-10个"]
}

要求：
1. 只输出JSON，不要包含任何额外的文字说明
2. tags数组包含3-5个最相关的标签
3. quality_score根据构图、清晰度、色彩、主题表现力综合评分"""

_VIDEO_FRAME_PROMPT = """你是一个专业的视频帧分析助手。请分析这一帧画面，并以严格的JSON格式输出分析结果。

输出JSON格式：
{
  "content": "这一帧画面的内容描述",
  "composition": "画面构图分析，如三分构图/中心构图/对称构图等",
  "dominant_colors": ["主色调1", "主色调2"],
  "has_text": true或false,
  "has_face": true或false,
  "subjects": ["画面中出现的主体元素名词，如太阳、山脉、狗、道路、海、人物、电视、水杯、房子、桌子、树、森林、玩具熊、书等名词，列出3-10个"]
}

要求：
1. 只输出JSON，不要包含任何额外的文字说明
2. content描述要具体，包含场景元素、人物动作、光线条件等"""

_VIDEO_SUMMARY_PROMPT = """你是一个专业的视频分析专家。下面是一个视频的多帧分析结果，请基于这些帧分析信息，生成该视频的整体分析报告。

以严格的JSON格式输出：
{
  "category": "视频的分类，从风景/人物/产品/抽象/界面中选择最匹配的一个",
  "tags": ["标签1", "标签2", "标签3"],
  "description": "对视频内容的自然语言描述，100-200字",
  "quality_score": 1-10,
  "style": "视频风格，从纪实/电影感/Vlog/动画/Motion Graphics中选择",
  "camera_movements": ["镜头运动类型1", "镜头运动类型2"],
  "color_palette": "整体调色风格，如暖色调/冷色调/高饱和/低饱和/黑白",
  "pace": "节奏，从快/中/慢中选择",
  "scene_count": 场景数量估算值,
  "has_text": true或false,
  "has_face": true或false,
  "subjects": ["全视频中出现的主体元素名词，如太阳、山脉、狗、道路、海、人物、电视、水杯、房子、桌子、树、森林、玩具熊、书等名词.综合所有帧提炼，列出3-10个"]
}

分析要点：
1. 综合所有帧的内容，总结视频主题和叙事
2. 观察帧间变化，推断镜头运动类型（推镜头/拉镜头/摇镜/跟拍/手持/固定/航拍）
3. 根据场景切换频率判断视频节奏
4. 分析整体色彩风格和调色倾向"""

# ---------------------------------------------------------------------------
# Vocabulary（标签字典）
# ---------------------------------------------------------------------------


class Vocabulary:
    """可选的标签字典，约束 VLM 输出到预定义列表。"""

    def __init__(
        self,
        tags: Optional[list] = None,
        categories: Optional[list] = None,
        video_styles: Optional[list] = None,
        camera_movements: Optional[list] = None,
        music_genres: Optional[list] = None,
        music_moods: Optional[list] = None,
    ):
        self.tags = tags or []
        self.categories = categories or []
        self.video_styles = video_styles or []
        self.camera_movements = camera_movements or []
        self.music_genres = music_genres or []
        self.music_moods = music_moods or []

    @classmethod
    def from_file(cls, path: str) -> "Vocabulary":
        """从 JSON 文件加载标签字典。文件格式见 STANDALONE_GUIDE.md。"""
        if not path or not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(
                tags=data.get("tags", []),
                categories=data.get("categories", []),
                video_styles=data.get("video_styles", []),
                camera_movements=data.get("camera_movements", []),
                music_genres=data.get("music_genres", []),
                music_moods=data.get("music_moods", []),
            )
        except Exception:
            return cls()

    def is_loaded(self) -> bool:
        return bool(self.tags or self.categories or self.video_styles)

    def build_constraint_prompt(self) -> str:
        if not self.is_loaded():
            return ""
        lines = ["【重要约束】你必须从以下预定义列表中选取所有输出值，不得编造超出列表范围的词汇："]
        if self.categories:
            lines.append(f"- category 只能从以下选择：{', '.join(self.categories)}")
        if self.tags:
            lines.append(f"- tags 只能从以下选择：{', '.join(self.tags)}")
        if self.video_styles:
            lines.append(f"- style 只能从以下选择：{', '.join(self.video_styles)}")
        if self.camera_movements:
            lines.append(f"- camera_movements 只能从以下选择：{', '.join(self.camera_movements)}")
        if self.music_genres:
            lines.append(f"- genre 只能从以下选择：{', '.join(self.music_genres)}")
        if self.music_moods:
            lines.append(f"- mood 只能从以下选择：{', '.join(self.music_moods)}")
        lines.append("如果没有任何匹配的选项，对应字段留空或返回空数组。")
        return "\n".join(lines)

    def validate_metadata(self, metadata: dict) -> dict:
        if not self.is_loaded():
            return metadata
        if self.categories and metadata.get("category"):
            if metadata["category"] not in self.categories:
                metadata["category"] = ""
        if self.tags and metadata.get("tags"):
            metadata["tags"] = [t for t in metadata["tags"] if t in self.tags]
        if self.video_styles and metadata.get("style"):
            if metadata["style"] not in self.video_styles:
                metadata["style"] = ""
        if self.camera_movements and metadata.get("camera_movements"):
            metadata["camera_movements"] = [
                m for m in metadata["camera_movements"] if m in self.camera_movements
            ]
        return metadata


# ---------------------------------------------------------------------------
# 视频帧提取（纯 subprocess + ffmpeg）
# ---------------------------------------------------------------------------


def _find_ffprobe() -> str:
    """查找 ffprobe 可执行文件（优先插件内置 bin/）"""
    import os
    import shutil
    # ① 插件内置 bin/ffprobe.exe
    plugin_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bin', 'ffprobe.exe')
    if os.path.isfile(plugin_bin):
        return plugin_bin
    # ② 系统 PATH
    found = shutil.which('ffprobe')
    if found:
        return found
    # ③ 常见安装路径
    for p in ['C:/ffmpeg/bin/ffprobe.exe', 'C:/Program Files/ffmpeg/bin/ffprobe.exe']:
        if os.path.isfile(p):
            return p
    return ''


def _probe_video(video_path: str) -> dict:
    """用 ffprobe 获取视频基本信息。"""
    ffprobe = _find_ffprobe()
    if not ffprobe:
        raise RuntimeError("ffprobe not found")
    args = [
        ffprobe, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path,
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    probe = json.loads(result.stdout)
    video_stream = next((s for s in probe.get("streams", []) if s["codec_type"] == "video"), None)
    if not video_stream:
        raise ValueError("No video stream found")
    duration = float(probe.get("format", {}).get("duration", 0))
    width = video_stream.get("width", 0)
    height = video_stream.get("height", 0)
    fps_str = video_stream.get("r_frame_rate", "0/1")
    num, den = fps_str.split("/")
    fps = float(num) / float(den) if float(den) != 0 else 0
    return {"duration": duration, "width": width, "height": height, "fps": fps}


def _extract_frames(video_path: str, output_dir: str,
                    max_frames: int = 200,
                    scene_threshold: float = 0.3) -> list:
    """用 ffmpeg 场景检测滤镜提取关键帧。"""
    info = _probe_video(video_path)
    duration = info["duration"]
    if duration > 300:
        scene_threshold = min(scene_threshold * 1.5, 0.6)

    args = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"select=gt(scene,{scene_threshold})",
        "-vsync", "vfr",
        "-vframes", str(max_frames),
        os.path.join(output_dir, "frame_%04d.png"),
    ]
    subprocess.run(args, capture_output=True, timeout=120)

    frames = sorted([
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir) if f.endswith(".png")
    ])

    if not frames:
        middle_time = duration / 2
        fallback = os.path.join(output_dir, "fallback_frame.png")
        args_fb = [
            "ffmpeg", "-y",
            "-ss", str(middle_time), "-i", video_path,
            "-vf", "scale=1920:-1",
            "-vframes", "1", fallback,
        ]
        subprocess.run(args_fb, capture_output=True, timeout=30)
        frames = [fallback]

    if len(frames) > max_frames:
        step = len(frames) // max_frames
        frames = frames[::step][:max_frames]
    return frames


def _generate_motion_pairs(frames: list) -> list:
    return [(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]


# ---------------------------------------------------------------------------
# VLM Client
# ---------------------------------------------------------------------------


class VLMClient:
    """对 OpenAI 兼容 API（Ollama / vLLM）的轻量封装。"""

    def __init__(self, base_url: str = "http://127.0.0.1:11434/v1",
                 model: str = "qwen3-vl:8b",
                 max_retries: int = 3,
                 vocab: Optional[Vocabulary] = None):
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key="not-needed")
        self.model = model
        self.max_retries = max_retries
        self.vocab = vocab

    @staticmethod
    def image_to_base64(image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    @staticmethod
    def parse_json_response(text: str) -> dict:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {}

    def _build_system_prompt(self, base_prompt: str) -> str:
        if self.vocab and self.vocab.is_loaded():
            constraint = self.vocab.build_constraint_prompt()
            if constraint:
                return base_prompt + "\n" + constraint
        return base_prompt

    def _call(self, system_prompt: str, user_text: str,
              image_b64: str = "", image_b64_list: Optional[list] = None,
              temperature: float = 0.1) -> str:
        content = []
        b64_list = image_b64_list or ([image_b64] if image_b64 else [])
        for b64 in b64_list:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        if user_text:
            content.append({"type": "text", "text": user_text})

        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": content},
                    ],
                    temperature=temperature,
                    max_tokens=4096,
                )
                return resp.choices[0].message.content or ""
            except Exception:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
        return ""

    def analyze_image(self, image_path: str) -> dict:
        sp = self._build_system_prompt(_IMAGE_ANALYSIS_PROMPT)
        b64 = self.image_to_base64(image_path)
        resp = self._call(sp, "请分析这张图片", image_b64=b64)
        result = self.parse_json_response(resp)
        if self.vocab and self.vocab.is_loaded():
            result = self.vocab.validate_metadata(result)
        return result

    def analyze_frame(self, frame_path: str) -> dict:
        sp = _VIDEO_FRAME_PROMPT
        b64 = self.image_to_base64(frame_path)
        resp = self._call(sp, "请分析这一帧画面", image_b64=b64)
        return self.parse_json_response(resp)

    def analyze_motion(self, frame1: str, frame2: str) -> str:
        sp = (
            "你是一个视频镜头运动分析专家。下面是从同一视频中提取的两帧画面。\n"
            "请判断两帧之间发生的镜头运动类型，只需回复运动类型名称。\n\n"
            "可选类型：推镜头（主体变大）、拉镜头（主体变小）、摇镜（水平旋转）、\n"
            "跟拍（跟随主体移动）、手持晃动、固定镜头、航拍\n\n"
            '如果无法判断，回复"固定镜头"。'
        )
        b64_1 = self.image_to_base64(frame1)
        b64_2 = self.image_to_base64(frame2)
        resp = self._call(sp, "请分析这两帧之间的镜头运动类型",
                          image_b64_list=[b64_1, b64_2])
        return resp.strip()

    def analyze_video_summary(self, frame_results: list,
                              motion_results: list) -> dict:
        sp = self._build_system_prompt(_VIDEO_SUMMARY_PROMPT)
        user = (
            f"以下是该视频的逐帧分析结果：\n\n"
            f"帧分析结果：\n{json.dumps(frame_results, ensure_ascii=False, indent=2)}\n\n"
            f"镜头运动分析结果：\n{json.dumps(motion_results, ensure_ascii=False, indent=2)}\n\n"
            f"请基于以上信息，生成该视频的整体分析报告（只输出JSON）。"
        )
        resp = self._call(sp, user)
        result = self.parse_json_response(resp)
        if self.vocab and self.vocab.is_loaded():
            result = self.vocab.validate_metadata(result)
        return result


# ---------------------------------------------------------------------------
# StandaloneAnalyzer - 对外统一入口
# ---------------------------------------------------------------------------


class StandaloneAnalyzer:
    """独立的图片/视频分析器。

    示例:
        >>> a = StandaloneAnalyzer()
        >>> r = a.analyze_image("cat.jpg", prompt="这是一只什么猫？")
        >>> print(r["description"])

        # 带标签字典
        >>> r = a.analyze_image("cat.jpg", vocab="my_tags.json")

        # 视频分析
        >>> r = a.analyze_video("demo.mp4")
    """

    def __init__(self, base_url: str = "http://127.0.0.1:11434/v1",
                 model: str = "qwen3-vl:8b",
                 max_retries: int = 3):
        self.base_url = base_url
        self.model = model
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # 图片分析
    # ------------------------------------------------------------------

    def analyze_image(
        self,
        image_path: Union[str, Path],
        prompt: Optional[str] = None,
        vocab: Union[str, Vocabulary, None] = None,
    ) -> dict:
        """分析单张图片，返回 JSON dict。

        参数:
            image_path : 图片文件路径（支持 jpg/png/bmp/webp）
            prompt     : 可选，自定义分析提示词；为 None 时使用内置 prompt
            vocab      : 可选，标签字典 JSON 文件路径或 Vocabulary 实例

        返回:
            dict 包含 category / tags / description / quality_score / subjects 等字段
        """
        image_path = str(image_path)
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        v = self._resolve_vocab(vocab)
        client = VLMClient(self.base_url, self.model, self.max_retries, v)

        if prompt:
            custom_sp = (
                prompt + "\n\n以严格的JSON格式输出结果。"
            )
            sp_full = client._build_system_prompt(custom_sp)
            b64 = client.image_to_base64(image_path)
            resp = client._call(sp_full, "请分析这张图片", image_b64=b64)
            result = client.parse_json_response(resp)
            if v and v.is_loaded():
                result = v.validate_metadata(result)
            return result
        else:
            return client.analyze_image(image_path)

    # ------------------------------------------------------------------
    # 视频分析
    # ------------------------------------------------------------------

    def analyze_video(
        self,
        video_path: Union[str, Path],
        prompt: Optional[str] = None,
        vocab: Union[str, Vocabulary, None] = None,
        *,
        max_frames: int = 200,
        scene_threshold: float = 0.3,
        progress_callback=None,
    ) -> dict:
        """分析视频，返回 JSON dict。

        流程: 场景检测提取关键帧 → 逐帧分析 → 镜头运动分析 → 汇总

        参数:
            video_path       : 视频文件路径
            prompt           : 可选，自定义汇总 prompt；为 None 时使用内置 prompt
            vocab            : 可选，标签字典 JSON 文件路径或 Vocabulary 实例
            max_frames       : 最大提取帧数（默认 200）
            scene_threshold  : 场景检测阈值（默认 0.3，越小越敏感）
            progress_callback: 可选，进度回调 fn(stage: str, pct: float, detail: str)

        返回:
            dict 包含 category / tags / description / style / pace / subjects 等字段
            以及 _frame_count / _duration / _resolution 等视频元信息
        """
        video_path = str(video_path)
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")

        v = self._resolve_vocab(vocab)
        client = VLMClient(self.base_url, self.model, self.max_retries, v)

        def _p(stage, pct, detail=""):
            if progress_callback:
                progress_callback(stage, pct, detail)

        # 1. 提取关键帧
        _p("extracting", 0.02, "提取关键帧...")
        tmpdir = tempfile.mkdtemp(prefix="video_analysis_")
        try:
            info = _probe_video(video_path)
            if info["duration"] > 300:
                scene_threshold_adj = min(scene_threshold * 1.5, 0.6)
            else:
                scene_threshold_adj = scene_threshold

            frames = _extract_frames(video_path, tmpdir,
                                     max_frames, scene_threshold)
            _p("extracting", 0.10, f"提取到 {len(frames)} 帧")

            total_frames = len(frames)

            # 2. 逐帧分析
            frame_results = []
            for fi, fp in enumerate(frames):
                pct = 0.10 + 0.40 * (fi / max(total_frames, 1))
                _p("analyzing_frames", pct, f"帧 {fi + 1}/{total_frames}")
                try:
                    fr = client.analyze_frame(fp)
                    fr["_frame_index"] = fi
                    frame_results.append(fr)
                except Exception:
                    frame_results.append({"_frame_index": fi, "_error": "vlm_failed"})

            # 3. 运动分析（双帧对比）
            motion_pairs = _generate_motion_pairs(frames)
            motion_results = []
            for pi, (f1, f2) in enumerate(motion_pairs):
                pct = 0.50 + 0.20 * (pi / max(len(motion_pairs), 1))
                _p("analyzing_motion", pct, f"运动对 {pi + 1}/{len(motion_pairs)}")
                try:
                    motion_type = client.analyze_motion(f1, f2)
                    motion_results.append({
                        "pair_index": pi,
                        "type": motion_type,
                    })
                except Exception:
                    motion_results.append({"pair_index": pi, "type": "固定镜头"})

            # 4. 汇总
            _p("summarizing", 0.85, "生成汇总...")

            if prompt:
                sp = (
                    prompt + "\n\n以严格的JSON格式输出结果。"
                )
                sp_full = client._build_system_prompt(sp)
                user = (
                    f"以下是该视频的逐帧分析结果：\n\n"
                    f"帧分析结果：\n{json.dumps(frame_results, ensure_ascii=False, indent=2)}\n\n"
                    f"镜头运动分析结果：\n{json.dumps(motion_results, ensure_ascii=False, indent=2)}\n\n"
                    f"请基于以上信息，生成该视频的整体分析报告（只输出JSON）。"
                )
                resp = client._call(sp_full, user)
                result = client.parse_json_response(resp)
            else:
                try:
                    result = client.analyze_video_summary(
                        frame_results, motion_results
                    )
                except Exception:
                    result = self._fallback_from_frames(frame_results)

            if v and v.is_loaded():
                result = v.validate_metadata(result)

            # 附加视频元信息
            result.setdefault("_frame_count", len(frames))
            result.setdefault("_duration", info["duration"])
            result.setdefault("_resolution", f"{info['width']}x{info['height']}")
            result.setdefault("_frame_results", frame_results)

            _p("done", 1.0, "分析完成")
            return result
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_vocab(vocab: Union[str, Vocabulary, None]) -> Optional[Vocabulary]:
        if vocab is None:
            return None
        if isinstance(vocab, Vocabulary):
            return vocab
        return Vocabulary.from_file(vocab)

    @staticmethod
    def _fallback_from_frames(frame_results: list) -> dict:
        """当 VLM 汇总失败时，从帧分析中聚合基础信息。"""
        all_content = []
        all_colors = []
        all_subs = []
        has_text = False
        has_face = False
        for fr in frame_results:
            if fr.get("_error"):
                continue
            c = fr.get("content", "")
            if c:
                all_content.append(c)
            colors = fr.get("dominant_colors", [])
            if colors:
                all_colors.extend(colors)
            subs = fr.get("subjects", [])
            if subs:
                all_subs.extend(subs)
            if fr.get("has_text"):
                has_text = True
            if fr.get("has_face"):
                has_face = True

        top_colors = [c for c, _ in Counter(all_colors).most_common(3)]
        top_subs = list(dict.fromkeys(all_subs))[:8]
        palette = " / ".join(top_colors) if top_colors else ""
        return {
            "category": "",
            "tags": [],
            "description": (all_content[0] if all_content else ""),
            "quality_score": 5,
            "style": "",
            "camera_movements": [],
            "color_palette": palette,
            "pace": "中",
            "scene_count": 1,
            "has_text": has_text,
            "has_face": has_face,
            "subjects": top_subs,
        }


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Standalone Image/Video Analyzer")
    ap.add_argument("file", help="Image or video file path")
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--model", default="qwen3-vl:8b")
    ap.add_argument("--prompt", default=None, help="Custom prompt text")
    ap.add_argument("--vocab", default=None, help="Path to vocab JSON file")
    ap.add_argument("--mode", choices=["image", "video", "auto"], default="auto")
    ap.add_argument("--max-frames", type=int, default=200)
    ap.add_argument("--scene-threshold", type=float, default=0.3)
    args = ap.parse_args()

    a = StandaloneAnalyzer(base_url=args.base_url, model=args.model)

    ext = os.path.splitext(args.file)[1].lower()
    video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
    is_video = ext in video_exts

    if args.mode == "image" or (args.mode == "auto" and not is_video):
        result = a.analyze_image(args.file, prompt=args.prompt, vocab=args.vocab)
    else:
        result = a.analyze_video(args.file, prompt=args.prompt, vocab=args.vocab,
                                 max_frames=args.max_frames,
                                 scene_threshold=args.scene_threshold)

    print(json.dumps(result, ensure_ascii=False, indent=2))
