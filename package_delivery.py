# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 桌面交付打包
=========================================================================
 把「洞察报告 + 感知卡片文件夹 + README」打包复制到桌面，
 目录名为「SonicNarrative_分析成果交付」，并额外生成一份 zip 压缩包。

 运行：python package_delivery.py
=========================================================================
"""

import os
import shutil
import zipfile

PACKAGE_NAME = "SonicNarrative_分析成果交付"

# 要交付的内容：(源路径, 目标内的相对路径)
ITEMS = [
    ("SonicNarrative_Insight_Report.md", "SonicNarrative_Insight_Report.md"),
    ("README.md", "README.md"),
    ("output_cards", "output_cards"),
]


def get_desktop() -> str:
    """定位桌面目录（兼容 Windows / macOS / Linux）。"""
    # Windows 上 OneDrive 可能接管桌面，优先用系统 API 拿真实路径
    if os.name == "nt":
        try:
            import ctypes.wintypes

            buf = ctypes.create_unicode_buffer(1024)
            ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf)
            if buf.value and os.path.isdir(buf.value):
                return buf.value
        except Exception:
            pass
        # 回退：用户目录下的 Desktop
        candidate = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.isdir(candidate):
            return candidate
        one_drive = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
        if os.path.isdir(one_drive):
            return one_drive
        return candidate
    return os.path.join(os.path.expanduser("~"), "Desktop")


def main() -> None:
    desktop = get_desktop()
    target = os.path.join(desktop, PACKAGE_NAME)

    # 1. 准备目标目录（已存在则先清空，保证每次交付都是最新的）
    if os.path.exists(target):
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)

    print(f"桌面路径：{desktop}")
    print(f"交付目录：{target}\n")

    # 2. 逐项复制
    copied_files = 0
    for src, dst_rel in ITEMS:
        if not os.path.exists(src):
            print(f"  [跳过] 源不存在：{src}")
            continue
        dst = os.path.join(target, dst_rel)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
            n = len([f for f in os.listdir(dst) if f.endswith(".html")])
            print(f"  [复制] {src}/ -> {dst_rel}/（{n} 张卡片）")
            copied_files += n
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  [复制] {src} -> {dst_rel}")
            copied_files += 1

    # 3. 额外生成 zip，方便直接发送
    zip_path = os.path.join(desktop, f"{PACKAGE_NAME}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(target):
            for fn in files:
                full = os.path.join(root, fn)
                zf.write(full, os.path.relpath(full, target))

    print(f"\n[完成] 已交付 {copied_files} 个文件")
    print(f"       文件夹：{target}")
    print(f"       压缩包：{zip_path}（{os.path.getsize(zip_path) / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
