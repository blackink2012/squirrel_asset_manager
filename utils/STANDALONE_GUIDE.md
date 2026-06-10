# StandaloneAnalyzer — 独立图片/视频分析模块 集成指南

## 概述

`standalone_analyzer.py` 是一个**零 UI 依赖**的纯代码模块，可集成到任何 Python 项目中。
它利用本地的 vision 语言模型（Qwen3-VL / GPT-4V 等 Ollama 兼容模型）对图片和视频进行自动分析。

```
输入                          输出
─────────────────────────────────────
图片路径 →                    ┌──────────────┐
提示词 (可选) →  VLM 分析 →   │  JSON dict   │
标签字典 (可选) →             └──────────────┘
```

## 环境要求

| 依赖 | 说明 |
|------|------|
| Python 3.10+ | |
| `pip install openai` | 调用 Ollama 兼容 API |
| ffmpeg / ffprobe | 仅视频分析需要（提取关键帧） |
| Ollama 运行中 | `ollama pull qwen3-vl:8b` 或其他 VL 模型 |

**不需要**: PySide6、FastAPI、数据库、YAML 配置文件。

## 快速开始

### 1. 基础用法

```python
from standalone_analyzer import StandaloneAnalyzer

# 初始化（指向你本地运行的 Ollama）
a = StandaloneAnalyzer(
    base_url="http://localhost:11434/v1",
    model="qwen3-vl:8b",
)

# 分析图片
result = a.analyze_image("photo.jpg")
print(result["description"])
print(result["tags"])          # ["汽车", "赛博朋克", "城市夜景"]
print(result["quality_score"]) # 8

# 分析视频
result = a.analyze_video("demo.mp4")
print(result["category"])      # "产品"
print(result["style"])         # "Motion Graphics"
print(result["pace"])          # "快"
print(result["scene_count"])   # 12
```

### 2. 自定义提示词

```python
# 传入你自己的 prompt，覆盖内置默认
result = a.analyze_image(
    "photo.jpg",
    prompt="请用中文描述这张图片的色调和氛围，输出 JSON: {\"colors\": [...], \"mood\": \"\" }"
)
# 注意：自定义 prompt 末尾会自动追加 "以严格的JSON格式输出结果。"
```

### 3. 带标签字典

```python
# vocab.json 格式见下方
result = a.analyze_image("photo.jpg", vocab="vocab.json")
# VLM 的输出会被约束到字典中的预定义值
```

### 4. 视频分析带进度回调

```python
def on_progress(stage, pct, detail):
    print(f"[{stage:20s}] {pct:5.1%}  {detail}")

result = a.analyze_video("long_movie.mp4", progress_callback=on_progress)
# 输出:
# [extracting          ]  2.0%  提取关键帧...
# [extracting          ] 10.0%  提取到 45 帧
# [analyzing_frames    ] 25.0%  帧 23/45
# [analyzing_motion    ] 55.0%  运动对 8/44
# [summarizing         ] 85.0%  生成汇总...
# [done                ] 100.0% 分析完成
```

### 5. 命令行

```bash
python standalone_analyzer.py photo.jpg
python standalone_analyzer.py movie.mp4 --vocab my_tags.json
python standalone_analyzer.py photo.jpg --prompt "判断这张图片是否有汽车" --model minicpm-v:latest
```

---

## API 参考

### `StandaloneAnalyzer`

```python
class StandaloneAnalyzer:
    def __init__(self, base_url="http://127.0.0.1:11434/v1", model="qwen3-vl:8b", max_retries=3)
    
    def analyze_image(self, image_path, prompt=None, vocab=None) -> dict
    def analyze_video(self, video_path, prompt=None, vocab=None, *, 
                       max_frames=200, scene_threshold=0.3, progress_callback=None) -> dict
```

#### 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `image_path` / `video_path` | `str` | 文件路径 |
| `prompt` | `str` or `None` | None = 使用内置默认提示词；有值 = 覆盖默认 |
| `vocab` | `str` or `Vocabulary` or `None` | 标签字典 JSON 文件路径或实例 |
| `max_frames` | `int` | 视频最多提取多少关键帧（默认 200） |
| `scene_threshold` | `float` | 场景检测敏感度，越小越敏感（默认 0.3） |
| `progress_callback` | `fn(stage, pct, detail)` | 进度回调，三个参数：阶段名、百分比（0~1）、描述文本 |

#### 返回值

`analyze_image` 返回:
```python
{
    "category": "风景",           # 风景/人物/产品/抽象/界面 之一
    "tags": ["山", "日落", "云"],  # 3-5 个标签
    "description": "图片展示了...", # 50-100 字描述
    "quality_score": 8,           # 1-10
    "subjects": ["山", "太阳"],    # 3-10 个主体元素
}
```

`analyze_video` 返回:
```python
{
    "category": "产品",
    "tags": ["汽车", "广告", "科技"],
    "description": "...",
    "quality_score": 9,
    "style": "Motion Graphics",       # 纪实/电影感/Vlog/动画/Motion Graphics
    "camera_movements": ["推镜头", "跟拍"],
    "color_palette": "高饱和暖色调",
    "pace": "快",                    # 快/中/慢
    "scene_count": 34,
    "has_text": true,
    "has_face": false,
    "subjects": ["汽车", "像素", ...],
    # 附加元信息:
    "_frame_count": 45,              # 实际提取的帧数
    "_duration": 88.5,               # 视频时长（秒）
    "_resolution": "1920x1080",
    "_frame_results": [...]          # 每帧的详细分析结果
}
```

---

## 标签字典 JSON 格式

```json
{
    "tags": ["汽车", "山脉", "海洋", "城市", "人物", "食物", ...],
    "categories": ["风景", "人物", "产品", "抽象", "界面"],
    "video_styles": ["纪实", "电影感", "Vlog", "动画", "Motion Graphics"],
    "camera_movements": ["推镜头", "拉镜头", "摇镜", "跟拍", "手持", "固定", "航拍"],
    "music_genres": ["流行", "爵士", "古典", "电子", "摇滚", "原声"],
    "music_moods": ["欢快", "悲伤", "安静", "紧张", "史诗", "放松"]
}
```

六个字段都是**可选**的，空列表表示不做约束。如果文件不存在或格式错误，等同于无字典（不做约束）。

---

## 集成到自己的项目

只需两步：

```python
# 1. 把 standalone_analyzer.py 放到你的项目目录
# 2. 导入使用

from standalone_analyzer import StandaloneAnalyzer

analyzer = StandaloneAnalyzer()

def my_image_pipeline(image_path, tag_dict_path=None):
    """你的图片处理流水线"""
    result = analyzer.analyze_image(image_path, vocab=tag_dict_path)
    # 将结果写入你的数据库 / 文件系统
    return {
        "category": result.get("category", ""),
        "tags": result.get("tags", []),
        "score": result.get("quality_score", 0),
        "description": result.get("description", ""),
    }
```

如果你需要修改内嵌 prompt，可以直接编辑 `standalone_analyzer.py` 顶部的 `_IMAGE_ANALYSIS_PROMPT`、`_VIDEO_FRAME_PROMPT`、`_VIDEO_SUMMARY_PROMPT` 字符串常量。

---

## 进阶：高级定制

### 用自己的 Vocabulary 实例

```python
from standalone_analyzer import Vocabulary

v = Vocabulary(
    tags=["白天", "黑夜", "室内", "室外"],
    categories=["照片", "渲染图"],
)
result = analyzer.analyze_image("img.png", vocab=v)
```

### 自定义视频帧提取参数

```python
# 对长视频降低帧数，加快分析
result = analyzer.analyze_video(
    "movie.mp4",
    max_frames=50,           # 最多 50 帧（默认 200）
    scene_threshold=0.5,     # 更低的敏感度（默认 0.3）
)
```

### 完全自定义的 VLM 调用

如果内置方法不满足需求，可以直接用底层的 `VLMClient`：

```python
from standalone_analyzer import VLMClient, Vocabulary

client = VLMClient(
    base_url="http://localhost:11434/v1",
    model="qwen3-vl:8b",
    vocab=Vocabulary.from_file("my_tags.json"),
)

# 单图调用
b64 = client.image_to_base64("img.jpg")
system_prompt = "你是一个... 以JSON格式输出"
response = client._call(system_prompt, "请分析", image_b64=b64)
result = client.parse_json_response(response)
```

---

## 常见问题

**Q: 分析很慢怎么办？**
A: 视频分析需要逐帧调用 VLM，每帧约 1-3 秒。减小 `max_frames` 可加速。

**Q: 返回的 JSON 为空？**
A: 检查 Ollama 是否运行：`ollama list`。确保模型已拉取：`ollama pull qwen3-vl:8b`。

**Q: ffmpeg 报错？**
A: Windows 需要 ffmpeg 在 PATH 中，或通过 `choco install ffmpeg` / `winget install ffmpeg` 安装。

**Q: 能用其他模型吗？**
A: 只要支持 OpenAI 兼容 API 的 vision 模型都可以，例如 `minicpm-v:latest`、`llava:latest`。
