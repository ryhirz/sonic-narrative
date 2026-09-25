# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 前端交互层（Gradio）
=========================================================================
 这一层是「纯前端」：它不 import synesthesia_core，而是通过 HTTP 调用
 后端 FastAPI 网关的接口。这样做有三个好处：
   1. 前后端彻底解耦 —— 后端换成别的语言实现，前端一行不用改
   2. 演示架构时可以直接指出"请求走了 HTTP"，工程化说得清楚
   3. 后端挂掉时前端能给出友好提示，而不是整个崩掉

 启动顺序（必须先后端后前端）：
     1) .venv\\Scripts\\python.exe -m uvicorn main:app --reload --port 8000
     2) .venv\\Scripts\\python.exe gradio_app.py
     3) 浏览器打开 http://127.0.0.1:7860
=========================================================================
"""

from __future__ import annotations

import html as html_lib
import os

import gradio as gr
import httpx

# 加载项目根目录的 .env（如 SONIC_API_BASE 等前端配置可写在里面；
# .env 已在 .gitignore 中，密钥不入库）。
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

# ----------------------------------------------------------------------
# 一、后端地址配置
# ----------------------------------------------------------------------
# 后端换了端口或部署到别的机器，只改这个环境变量即可：
#     set SONIC_API_BASE=http://192.168.1.10:8000
API_BASE = os.getenv("SONIC_API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_V1 = f"{API_BASE}/api/v1"

TIMEOUT = 120.0  # 真实大模型翻译一次约 8~10 秒，超时放宽一点

# ----------------------------------------------------------------------
# 二、快捷预设（一键切换出截然不同的感知场景，演示效果最好）
# ----------------------------------------------------------------------
# 顺序：valence, energy, danceability, acousticness, tempo
PRESETS = {
    "🔥 赛博律动": (0.55, 0.92, 0.72, 0.03, 128.0),
    "🌲 晨雾森林": (0.42, 0.18, 0.28, 0.94, 68.0),
    "🏖️ 落日沙滩": (0.92, 0.68, 0.86, 0.22, 112.0),
    "🌧️ 深夜内省": (0.18, 0.32, 0.38, 0.72, 84.0),
}

# ----------------------------------------------------------------------
# 三、自定义 CSS（只做很轻的修饰，保持极简）
# ----------------------------------------------------------------------
CUSTOM_CSS = """
.card-frame iframe { border-radius: 18px; box-shadow: 0 12px 32px rgba(0,0,0,.18); }
.gallery-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:18px; }
.gallery-item iframe { width:100%; height:560px; border:0; border-radius:16px; }
"""


# ======================================================================
# 四、工具函数
# ======================================================================

def wrap_iframe(card_html: str, height: int = 660) -> str:
    """把整张卡片 HTML 塞进 iframe 里渲染。

    ⚠️ 为什么要多此一举？
       卡片是一份完整的 HTML 文档，里面有 `body{...}`、`*{margin:0}` 这类
       【全局选择器】。如果直接用 gr.HTML 插进页面，这些样式会覆盖 Gradio
       自身的样式，整个界面的布局会被冲垮（按钮错位、背景被换掉）。

       用 iframe 的 srcdoc 属性，等于给卡片开了一个独立的沙盒文档，
       样式互不干扰，卡片还能保留它自己的背景渐变和呼吸动画。

    注意：srcdoc 是 HTML 属性，内容里的双引号必须转义成 &quot;，
    否则属性会提前闭合，页面就乱了。html.escape(quote=True) 正是干这个的。
    """
    escaped = html_lib.escape(card_html, quote=True)
    return (
        f'<iframe srcdoc="{escaped}" '
        f'style="width:100%;height:{height}px;border:0;border-radius:18px;" '
        f'title="SonicNarrative 感知卡片"></iframe>'
    )


def _request(method: str, url: str, **kwargs) -> dict:
    """统一发 HTTP 请求，把网络错误翻译成友好提示。

    返回形如 {"ok": True, "data": {...}} 或 {"ok": False, "error": "..."}。
    这样上层函数不用到处写 try/except。
    """
    # ⚠️ 不能直接写 httpx.request(..., timeout=TIMEOUT, **kwargs)：
    #    一旦调用方也传了 timeout，就会出现
    #    "got multiple values for keyword argument 'timeout'"。
    #    用 setdefault：调用方没指定时才套用默认超时。
    kwargs.setdefault("timeout", TIMEOUT)

    try:
        resp = httpx.request(method, url, **kwargs)
    except httpx.ConnectError:
        return {
            "ok": False,
            "error": f"连不上后端服务（{API_BASE}）。请先启动："
                     f"`uvicorn main:app --reload --port 8000`",
        }
    except httpx.TimeoutException:
        return {"ok": False, "error": f"请求超时（>{TIMEOUT:.0f}s），大模型可能响应缓慢，请重试"}
    except Exception as exc:
        return {"ok": False, "error": f"请求失败：{type(exc).__name__}：{exc}"}

    try:
        body = resp.json()
    except Exception:
        # 常见于：端口被代理占用 / 地址填错，对方返回的不是我们的 JSON
        return {
            "ok": False,
            "error": f"后端返回了非 JSON 内容（HTTP {resp.status_code}）。"
                     f"请确认 `{API_BASE}` 指向的是 SonicNarrative 后端，且服务已启动。",
        }

    # 后端统一信封：code != 0 表示业务失败
    if resp.status_code != 200 or body.get("code") != 0:
        return {"ok": False, "error": body.get("message", f"HTTP {resp.status_code}")}

    return {"ok": True, "data": body.get("data")}


# ======================================================================
# 五、交互函数（每个都对应界面上的一个动作）
# ======================================================================

def check_backend() -> str:
    """探测后端是否在线，返回一段状态 Markdown。"""
    res = _request("GET", f"{API_V1}/health", timeout=5.0)
    if not res["ok"]:
        return (
            f"### 🔴 后端未连接\n\n{res['error']}\n\n"
            f"> 前端界面可以正常浏览，但所有功能需要后端支持。"
        )

    h = res["data"]
    engine = h.get("engines", {}).get("synesthesia", {})
    llm_on = engine.get("llm_enabled", False)

    if llm_on:
        engine_line = f"✅ 真实大模型已接入（{engine.get('model')}）"
    else:
        engine_line = "⚠️ 未配置 API Key，卡片走 Mock 兜底（功能可用，但非大模型创作）"

    rows = "\n".join(
        f"| {m['name']} | `{m['key']}` | {m['status']} | {m['version']} |"
        for m in h.get("modules", [])
    )

    return (
        f"### 🟢 后端已连接\n\n"
        f"**平台**：{h.get('platform')} v{h.get('version')} ｜ "
        f"**状态**：`{h.get('status')}` ｜ **已运行**：{h.get('uptime_seconds')}s\n\n"
        f"**翻译引擎**：{engine_line}\n\n"
        f"| 子模块 | 标识 | 状态 | 版本 |\n|---|---|---|---|\n{rows}\n"
    )


def do_translate(valence, energy, danceability, acousticness, tempo,
                 track, artist, mode, save):
    """调用后端翻译接口，返回 (卡片 HTML, 状态文案, 通感 JSON)。"""
    # 把界面的"自动 / 强制大模型 / 强制 Mock"翻译成接口要的 use_llm 参数
    use_llm = {"自动": None, "强制大模型": True, "强制 Mock": False}.get(mode)

    payload = {
        "features": {
            "valence": valence, "energy": energy, "danceability": danceability,
            "acousticness": acousticness, "tempo": tempo,
        },
        "meta": {
            "track_name": (track or "").strip() or "未知曲目",
            "artist": (artist or "").strip() or "未知艺人",
        },
        "use_llm": use_llm,
        "save": save,
    }

    res = _request("POST", f"{API_V1}/synesthesia/translate", json=payload)
    if not res["ok"]:
        return "", f"### ❌ 翻译失败\n\n{res['error']}", None

    d = res["data"]
    card = d["card"]

    if d["mode"] == "llm":
        tip = f"🤖 **大模型生成**（{d.get('model')}）"
    else:
        tip = "🎭 **Mock 兜底**（未配置 API Key 或上游不可用）"

    status_md = (
        f"### ✅ {tip}\n\n"
        f"**场景**：{card['scene_title']} {' '.join(card['emojis'])} ｜ "
        f"**配色**：{' '.join(f'`{c}`' for c in card['color_palette'])} ｜ "
        f"**HTML**：{d['html_length']} 字符"
        + (f"\n\n💾 已保存到画廊：`{d['saved_path']}`" if d.get("saved_path") else "")
    )

    # 返回卡片的 iframe、状态文案、以及可直接复制的 JSON
    return wrap_iframe(d["html"]), status_md, card


def load_gallery(limit: int) -> str:
    """拉取画廊列表，用 iframe 网格展示每张卡片。"""
    res = _request("GET", f"{API_V1}/synesthesia/gallery",
                   params={"limit": int(limit)}, timeout=15.0)
    if not res["ok"]:
        return f"<p style='color:#d32f2f;padding:16px'>❌ {res['error']}</p>"

    items = res["data"]["items"]
    if not items:
        return ("<p style='padding:24px;color:#666'>画廊还是空的。"
                "去「特征感知翻译器」生成一张，并勾选「保存到画廊」。</p>")

    # 卡片的完整 HTML 由后端以静态资源提供（/cards/xxx.html），
    # 直接用 iframe 的 src 指过去即可，前端不用再传一遍 HTML，省带宽。
    frames = "".join(
        f'<div class="gallery-item">'
        f'<iframe src="{API_BASE}{item["url"]}" title="{item["track"]}"></iframe>'
        f'<div style="text-align:center;font-size:12px;color:#666;margin-top:6px">'
        f'{item["track"]} · {item["scene_title"]}</div>'
        f'</div>'
        for item in items
    )

    total = res["data"]["total"]
    return (
        f"<p style='color:#666;font-size:13px;margin-bottom:12px'>"
        f"共 {total} 张样本，当前展示 {len(items)} 张</p>"
        f'<div class="gallery-grid">{frames}</div>'
    )


def load_health() -> str:
    """「平台状态」页签用：重新拉取一次健康检查。"""
    return check_backend()


def do_reverse(text: str, top_k: int, mode: str) -> tuple:
    """反向寻歌：一段描述 → 最贴切的那批歌。

    返回 (状态 Markdown, 匹配结果 Markdown 表格)。
    """
    text = (text or "").strip()
    if len(text) < 2:
        return "### ⚠️ 描述太短\n\n至少写 2 个字，例如「深夜独自开车，霓虹在车窗外流过」。", ""

    use_llm = {"自动": None, "强制大模型": True, "强制 Mock": False}.get(mode)

    res = _request("POST", f"{API_V1}/recommender/reverse",
                   json={"text": text, "top_k": int(top_k), "use_llm": use_llm})
    if not res["ok"]:
        return f"### ❌ 寻歌失败\n\n{res['error']}", ""

    d = res["data"]

    if d["mode"] == "llm":
        engine_tip = "🤖 **大模型反推特征**"
    else:
        engine_tip = "🏷️ **关键词规则反推**（未配置 API Key 或上游不可用）"

    status_md = (
        f"### ✅ {engine_tip}\n\n"
        f"**反推特征**：valence={d['features']['valence']} ｜ "
        f"energy={d['features']['energy']} ｜ "
        f"acousticness={d['features']['acousticness']} ｜ "
        f"tempo={d['features']['tempo']} BPM\n\n"
        f"> {d['reason']}"
    )

    # 曲库提示：曲库偏小时如实告诉用户，避免把"匹配度 81%"过度解读
    if d.get("library_note"):
        status_md += f"\n\n> 💡 {d['library_note']}"

    # 匹配结果表格
    rows = "\n".join(
        f"| {i} | {m['track_name']} · {m['artist']} | "
        f"{m['features']['valence']} / {m['features']['energy']} / "
        f"{m['features']['acousticness']} / {m['features']['tempo']} | "
        f"**{m['match_score']}%** |"
        for i, m in enumerate(d["matches"], 1)
    )
    table_md = (
        f"**在 {d['library_size']} 首曲库中匹配到 {len(d['matches'])} 首：**\n\n"
        f"| # | 歌曲 | 特征(情绪/能量/原声/BPM) | 匹配度 |\n"
        f"|---|---|---|---|\n{rows}\n"
    )

    return status_md, table_md


# ======================================================================
# 六、界面搭建
# ======================================================================

def build_ui() -> gr.Blocks:
    # ⚠️ Gradio 6.0 起，theme 和 css 要传给 launch()，不再是 Blocks() 的参数。
    #    放在 Blocks() 里只会收到一条 UserWarning，然后在 launch 时失效。
    with gr.Blocks(title="SonicNarrative · 特征感知翻译器") as demo:

        gr.Markdown(
            """
# 🎧 SonicNarrative

**把冷冰冰的音频特征，翻译成你能看见、能走进去的地方。**

拖动下面的滑块调整音乐特征，引擎会为你生成一个完整的通感场景：配色、情绪文案，以及一段可直接使用的 AI 绘画提示词。
            """
        )

        # 后端连接状态（启动后自动探测一次）
        backend_status = gr.Markdown("⏳ 正在检测后端服务…")

        with gr.Tabs():

            # ---------------- 页签一：翻译器（核心体验）----------------
            with gr.Tab("🎨 特征感知翻译器"):
                with gr.Row():
                    # 左侧：输入区
                    with gr.Column(scale=4):
                        gr.Markdown("### 🎚️ 音频特征")
                        valence = gr.Slider(0, 1, value=0.81, step=0.01,
                                            label="Valence 情绪积极度（越高越明亮愉悦）")
                        energy = gr.Slider(0, 1, value=0.83, step=0.01,
                                           label="Energy 能量强度（越高越激烈）")
                        danceability = gr.Slider(0, 1, value=0.56, step=0.01,
                                                 label="Danceability 舞蹈性（越高越有律动）")
                        acousticness = gr.Slider(0, 1, value=0.01, step=0.01,
                                                 label="Acousticness 原声度（越高越自然不插电）")
                        tempo = gr.Slider(40, 220, value=171, step=1,
                                          label="Tempo 速度 BPM")

                        gr.Markdown("#### ⚡ 快捷预设")
                        with gr.Row():
                            preset_btns = [
                                gr.Button(name, size="sm") for name in PRESETS
                            ]

                        gr.Markdown("### 🎵 歌曲信息（可选）")
                        with gr.Row():
                            track = gr.Textbox(label="歌曲名", value="Blinding Lights")
                            artist = gr.Textbox(label="艺人", value="The Weeknd")

                        with gr.Row():
                            mode = gr.Radio(
                                ["自动", "强制大模型", "强制 Mock"],
                                value="自动",
                                label="引擎模式",
                                info="演示或省额度时选「强制 Mock」",
                            )
                            save = gr.Checkbox(label="保存到画廊", value=False)

                        btn = gr.Button("🎨 生成感知卡片", variant="primary", size="lg")

                    # 右侧：输出区
                    with gr.Column(scale=5):
                        status_md = gr.Markdown()
                        card_view = gr.HTML(
                            value="<div style='padding:60px;text-align:center;color:#999'>"
                                  "👈 调整左侧特征，然后点击「生成感知卡片」</div>"
                        )
                        with gr.Accordion("📋 查看通感 JSON / 复制 AI 绘画 Prompt", open=False):
                            json_out = gr.JSON(label="SenseCard")

                # 事件绑定
                btn.click(
                    fn=do_translate,
                    inputs=[valence, energy, danceability, acousticness, tempo,
                            track, artist, mode, save],
                    outputs=[card_view, status_md, json_out],
                )

                # 预设按钮：点击后把 5 个滑块一次性设成预设值
                for btn_obj, (name, values) in zip(preset_btns, PRESETS.items()):
                    btn_obj.click(
                        fn=lambda v=values: v,
                        outputs=[valence, energy, danceability, acousticness, tempo],
                    )

            # ---------------- 页签二：样本画廊 ----------------
            with gr.Tab("🖼️ 样本画廊"):
                gr.Markdown("展示 `output_cards/` 下所有已生成的感知卡片。")
                with gr.Row():
                    limit = gr.Slider(1, 50, value=6, step=1, label="展示数量")
                    refresh_gallery = gr.Button("🔄 刷新", variant="secondary")
                gallery_view = gr.HTML()

                refresh_gallery.click(fn=load_gallery, inputs=[limit], outputs=[gallery_view])

            # ---------------- 页签三：反向寻歌 ----------------
            with gr.Tab("🔁 反向寻歌"):
                gr.Markdown(
                    "**v2 子模块 · 反向寻歌（Reverse Recommender）**\n\n"
                    "用一句话描述你想要的感受或画面，引擎会反推出对应的音频特征，"
                    "再到曲库里找出最贴切的那首歌。这是「特征感知翻译器」的逆运算。"
                )

                with gr.Row():
                    with gr.Column(scale=4):
                        gr.Markdown("### ✍️ 描述你此刻想要的感觉")
                        reverse_text = gr.Textbox(
                            label="感受描述",
                            placeholder="例如：深夜独自开车，霓虹在车窗外流过",
                            lines=3,
                        )
                        with gr.Row():
                            reverse_topk = gr.Slider(
                                1, 10, value=5, step=1, label="返回几首（Top K）"
                            )
                            reverse_mode = gr.Radio(
                                ["自动", "强制大模型", "强制 Mock"],
                                value="自动",
                                label="反推引擎",
                                info="没配 API Key 时「自动」等价于关键词规则",
                            )
                        reverse_btn = gr.Button("🔁 为我寻歌", variant="primary", size="lg")

                    with gr.Column(scale=5):
                        reverse_status = gr.Markdown()
                        reverse_table = gr.Markdown()

                reverse_btn.click(
                    fn=do_reverse,
                    inputs=[reverse_text, reverse_topk, reverse_mode],
                    outputs=[reverse_status, reverse_table],
                )

            # ---------------- 页签四：平台状态 ----------------
            with gr.Tab("🩺 平台状态"):
                gr.Markdown("实时反映后端各子模块的运行情况（数据来自 `/api/v1/health`）。")
                refresh_health = gr.Button("🔄 刷新状态", variant="secondary")
                health_view = gr.Markdown()

                refresh_health.click(fn=load_health, outputs=[health_view])

        # 页面加载完成后自动探测后端 + 拉取画廊
        demo.load(fn=check_backend, outputs=[backend_status])
        demo.load(fn=load_gallery, inputs=[limit], outputs=[gallery_view])

    return demo


if __name__ == "__main__":
    demo = build_ui()
    # theme / css 在 Gradio 6 中要在这里传；show_api 参数已被移除，不要再写
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        theme=gr.themes.Soft(primary_hue="blue", secondary_hue="sky"),
        css=CUSTOM_CSS,
    )
