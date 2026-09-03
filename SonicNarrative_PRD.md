# SonicNarrative · 需求文档（PRD）

> 版本：v1.0 草案 ｜ 日期：2026-09-02 ｜ 角色：简历作品（数据科学方向）
> 一句话定义：**把一首歌的 Spotify 音频特征，翻译成人类可感知的视觉场景 + 情绪文案 + AI 绘画 Prompt 的「特征感知翻译器」。**

---

## 0. 为什么做（Why）

传统音乐数据看板把歌曲拆成一串 0–1 的数字，冰冷、无感。SonicNarrative 想做相反的事：
**让数据被「感受」到**。输入特征 → 输出一张能看懂、能共情、能拿去生图的「感知卡片」。

- 对简历的价值：同时展示 **规则引擎（可解释）** + **LLM 编排（创造力）** + **数据可视化**，比单纯跑一个模型更有产品叙事。
- 对用户的真实价值：听歌时获得「这张专辑是什么颜色/什么质感」的跨模态体验；创作者能一键拿到生图 Prompt。

---

## 1. 通感映射体系（Synesthetic Engine）

### 1.1 设计原则
1. **规则优先、LLM 兜底**：5 个核心特征的「颜色/光影/质感/节拍」由确定性启发式规则算出（可解释、可复现、零成本）；LLM 只负责把规则结果「写成人话」和「写出生图 Prompt」。这样即便 API 挂了，卡片仍能渲染。
2. **连续值分桶**：每个特征用阈值切成 3 档（高/中/低），避免逐点抖动。
3. **可扩展**：映射表用 dict/JSON 存，后续你改配色不用动代码。

### 1.2 核心特征映射规则（4+1）

| 特征 | 含义 | 映射到 | 高（≥0.7） | 中（0.4–0.7） | 低（<0.4） |
|---|---|---|---|---|---|
| **Valence** | 情绪积极度 | 色彩冷暖 + 情绪基调 | 暖色（橙/暖黄/暖粉）、高饱和；明亮·愉悦·治愈 | 中性过渡色（青绿/淡紫）；平静·松弛 | 冷色（深蓝/靛/灰紫）；内省·忧郁·深沉 |
| **Energy** | 能量强度 | 光影动态 | 高对比、强光束、锐利边缘、快动效 | 中等对比、柔和光 | 漫射柔光、柔焦、静态 |
| **Danceability** | 舞蹈性 | 图形节奏 | 重复几何单元、强律动网格、流动线条 | 中度重复图案 | 自由有机形态、不规则分布 |
| **Acousticness** | 原声度 | 质感纹理 | 有机/手绘质感、纸张·水墨·自然元素 | 混合质感 | 数字/合成质感、霓虹、glitch 故障感 |
| **Tempo** (BPM) | 速度 | 视觉节拍 | >120：密集网格、短促元素、快节奏排布 | 90–120：中速、均衡 | <90：舒展、大留白、长线条 |

> 说明：Tempo 用绝对 BPM 阈值（约 50–200 区间），其余 4 个用 0–1 归一化值分桶。

### 1.3 规则函数示例（Python，可直接用）

```python
def map_valence(v: float) -> dict:
    if v >= 0.7:   return {"hue": "warm",   "palette": ["#FFB347","#FF7E67","#FFD56B"], "mood": "明亮·愉悦·治愈"}
    elif v >= 0.4: return {"hue": "neutral","palette": ["#7FB5B5","#B8A9C9"],            "mood": "平静·松弛·过渡"}
    else:          return {"hue": "cool",   "palette": ["#2C3E66","#4A4E8C","#6B6E9B"], "mood": "内省·忧郁·深沉"}

def map_energy(e: float) -> dict:
    if e >= 0.7:   return {"light": "高对比强光束，锐利边缘，动态闪烁"}
    elif e >= 0.4: return {"light": "中等对比，柔和定向光"}
    else:          return {"light": "漫射柔光，柔焦，几乎静止"}

def map_danceability(d: float) -> dict:
    if d >= 0.7:   return {"rhythm": "重复几何单元 + 强律动网格"}
    elif d >= 0.4: return {"rhythm": "中度重复图案"}
    else:          return {"rhythm": "自由有机形态，不规则分布"}

def map_acousticness(a: float) -> dict:
    if a >= 0.7:   return {"texture": "手绘/水墨/纸张质感，自然元素"}
    elif a >= 0.3: return {"texture": "混合质感（有机+数字）"}
    else:          return {"texture": "霓虹/合成质感，故障艺术(glitch)"}

def map_tempo(bpm: float) -> dict:
    if bpm > 120:  return {"pace": "密集网格，短促元素，快节奏排布"}
    elif bpm >= 90:return {"pace": "中速，均衡构图"}
    else:          return {"pace": "舒展留白，长线条"}
```

### 1.4 AI 绘画 Prompt 合成模板（交给 Qwen 润色）

```
根据以下映射结果，生成【一句情绪描述】+【一段英文 AI 绘画 Prompt】：
歌曲：{track_name} - {artist}
配色：{palette}  情绪基调：{mood}
光影：{light}  质感：{texture}  节奏：{rhythm}  节拍：{pace}
风格参考：cinematic，highly detailed，--ar 16:9
```

> LLM 只做「翻译/润色」，不决定配色——保证视觉结果始终由数据驱动、可归因。

---

## 2. MVP 功能范围（v1）

### 2.1 MVP 包含（只做两件事）
1. **正向翻译（单首 → HTML 感知卡片）**：输入一首歌的特征，输出一张含「配色条 / 情绪文案 / 光影·质感·节奏描述 / AI 绘画 Prompt」的 HTML 卡片（样例见 `sample_perception_card.html`）。
2. **批量生成报告**：上传 CSV（含 5 个特征列）→ 循环翻译 → 生成 N 张卡片 + 一个汇总 HTML 报告（可按 Valence/Energy 排序、可缩略图浏览）。

### 2.2 用户故事
- 作为**听众**，我希望输入喜欢的歌的特征就看到它的「颜色」，以便更直观地理解这首歌的气质。
- 作为**内容创作者**，我希望一键拿到 AI 绘画 Prompt，以便快速为歌单做封面。
- 作为**演示者（简历）**，我希望批量上传一份歌单 CSV 就产出一份报告，以便展示系统能力。

### 2.3 非目标（Non-goals，v1 明确不做）
- ❌ 反向寻歌（描述感受 → 推荐歌曲）
- ❌ 实时音频分析（上传音频文件 → 提取特征）
- ❌ 用户登录 / 社交分享 / 多语言
- ❌ 自定义映射规则的可视化编辑器

### 2.4 后续迭代（Now / Next / Later）
- **Now（v1）**：正向翻译 + 批量报告
- **Next（v2）**：① 反向寻歌（感受词 → 向量检索歌曲，需 embedding + 轻量向量库）② 音频特征提取（librosa 解析上传音频 → 自动填特征）
- **Later（v3）**：① 用户自定义映射参数 ② 3D 沉浸式场景 ③ 社区歌单分享 ④ 多模态（卡片可配音/可播放片段）

---

## 3. 数据集推荐

### 3.1 推荐数据集（均含全部所需字段）
| 数据集 | 规模 | 获取 | 说明 |
|---|---|---|---|
| **Spotify Tracks Dataset**（`maharshipandya/spotify-tracks-dataset`） | ~11.5 万首 | Kaggle | 列齐全，含 `track_name, artists, valence, energy, danceability, acousticness, tempo` 等，适合起步 |
| **Spotify 1.2M Tracks**（`rodolfolm/spotify-tracks-dataset`） | 120 万首 | Kaggle | 更大、更适合作「批量报告」演示，但文件较大 |

### 3.2 下载方式
```bash
# 1) 安装并配置 Kaggle（需 Kaggle 账号，免费）
pip install kaggle
#   把 kaggle.json 放到 ~/.kaggle/，chmod 600

# 2) 下载起步数据集并解压
kaggle datasets download -d maharshipandya/spotify-tracks-dataset -p data/ --unzip
```
> 若暂无 Kaggle 账号：直接用下方 Mock 数据先跑通管线，数据集只是「换数据源」，不影响开发。

### 3.3 Mock 数据结构（CSV，立即可用）
```csv
track_name,artist,valence,energy,danceability,acousticness,tempo
Blinding Lights,The Weeknd,0.81,0.83,0.56,0.01,171
Weightless,Marconi Union,0.20,0.08,0.15,0.95,60
Levitating,Dua Lipa,0.88,0.77,0.80,0.08,103
Someone Like You,Adele,0.23,0.36,0.24,0.78,67
```
> 字段与 Spotify 官方音频特征对齐，后续接真实数据集零改造成本。

---

## 4. 技术选型

| 层 | 选型 | 理由（结合你的背景） |
|---|---|---|
| 数据处理 | **Python + pandas** | 你本科学的栈，CSV 读写/筛选低成本 |
| 映射引擎 | 纯 Python 函数（见 §1.3） | 确定性、可解释、不依赖 API，简历亮点 |
| 文本生成 | **Qwen（通义千问 / DashScope）** | 国产、有免费额度、中文语境好；只做润色，成本极低 |
| 渲染 | **HTML + CSS（单文件卡片）** | 你点名的「HTML 渲染」，零框架、易演示 |
| 交互（可选） | **Gradio**（你正在探索） | 拖入 CSV 即出报告，适合弱代码快速验证；非必须 |

**整体架构（数据流）**
```
CSV/单首特征 ──▶ 映射引擎(规则) ──▶ 结构化感官属性
                                      │
                                      ├─▶ HTML 卡片模板(确定性渲染)
                                      │
                                      └─▶ Qwen API(润色情绪文案+生图Prompt)
                                            │
                                            ▼
                                      批量报告(汇总 HTML)
```

**为什么这样选**：规则引擎保证「数据→体验」可解释、可归因（产品/技术都讲得清）；Qwen 只补足「人情味」文案，单首成本可忽略；HTML 单文件便于放进简历附件/录屏。

---

## 5. Agent 自动化开发步骤（弱代码也能推进）

按模块拆给 AI Agent（如 WorkBuddy）逐步生成，每个模块给一段可直接粘贴的指令：

1. **数据层**：「用 pandas 写 `load_songs(csv_path)`，返回 DataFrame，校验 5 个特征列存在，缺失值用中位数填充。」
2. **映射引擎**：「实现 §1.3 的 5 个 `map_*` 函数，输入一行特征，输出统一的 `sensory` dict。」
3. **LLM 层**：「封装 `generate_narrative(sensory, meta)`，调用 DashScope Qwen 接口，返回 `{mood_text, art_prompt}`，读取 `DASHSCOPE_API_KEY` 环境变量，加 try/except 兜底。」
4. **渲染层**：「用 f-string/Jinja2 写 `render_card(sensory, narrative, meta)`，输出一张自包含 HTML 卡片（含配色条、文案、Prompt 块）。」
5. **批量报告**：「写 `build_report(csv_path, out_dir)`：循环翻译 → 存卡片 → 生成 `index.html` 汇总页（缩略图 + 排序）。」
6. **交互（可选）**：「用 Gradio 做一个页面：上传 CSV → 显示报告链接。」
7. **打包演示**：「写 README（安装/运行/截图）+ 录 1 分钟 demo。」

> 每步让 Agent 先给可运行代码，你本地 `python xxx.py` 跑通再进下一步——契合「先跑起来」的节奏。

---

## 6. 上线后看什么（指标体系，简历加分项）

- **北极星**：单份报告平均生成卡片数（= 实际翻译的歌曲量）
- **驱动指标**：CSV 上传成功率、Qwen 调用成功率、单卡渲染耗时
- **健康指标**：规则引擎覆盖率（无需 API 也能出的卡片占比）、报错率

---

## 7. 假设与待确认
- 你会有/会申请 **DashScope API Key**（免费额度足够）；无 Key 时卡片仍可渲染（仅缺 LLM 文案）。
- MVP 形态为 **CLI + 静态 HTML**，非 Web 应用（降低复杂度）。
- 数据集优先用 Mock/小样本跑通，再换 Kaggle 全量。
- 若你希望 v1 就带 Gradio 界面或反向寻歌，请告知，我会调整范围。

---
*文档可随开发迭代更新；映射表与数据集字段已对齐，便于直接落地。*
