#!/usr/bin/env python3
"""
build_icon.py — 從 assets/*.svg 產出 Dock app 用的 icon.icns 與網頁用的 favicon。

WHY THIS EXISTS
icns 是二進位,不該用手做也不該只存在 repo 裡沒人知道怎麼重生。來源是兩份 SVG,
這支腳本是它們到 icns 之間唯一的路。改了 SVG 就重跑一次。

為什麼有兩份 SVG:同一張圖縮到 16px 會糊成一坨。大尺寸用 icon.svg(完整的下降軌
跡),16/32 用 icon-small.svg(只留看得出來的那兩個元素)。這是 icon 的常規做法,
不是兩份圖不同步。

只用 sips 與 iconutil —— 兩支都是 macOS 內建,不引入任何相依。

USAGE
    python3 build_icon.py
"""
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(os.path.dirname(BASE_DIR), "assets")
FULL_SVG = os.path.join(ASSETS_DIR, "icon.svg")
SMALL_SVG = os.path.join(ASSETS_DIR, "icon-small.svg")
ICNS_PATH = os.path.join(ASSETS_DIR, "icon.icns")
FAVICON_PATH = os.path.join(ASSETS_DIR, "favicon.png")

# (檔名, 邊長px, 用哪份來源)。@2x 的實際像素是標示尺寸的兩倍。
SLICES = [
    ("icon_16x16.png",        16,   SMALL_SVG),
    ("icon_16x16@2x.png",     32,   SMALL_SVG),
    ("icon_32x32.png",        32,   SMALL_SVG),
    ("icon_32x32@2x.png",     64,   SMALL_SVG),
    ("icon_128x128.png",      128,  FULL_SVG),
    ("icon_128x128@2x.png",   256,  FULL_SVG),
    ("icon_256x256.png",      256,  FULL_SVG),
    ("icon_256x256@2x.png",   512,  FULL_SVG),
    ("icon_512x512.png",      512,  FULL_SVG),
    ("icon_512x512@2x.png",   1024, FULL_SVG),
]


def render(svg, size, out):
    """sips 先把 SVG 光柵化成 1024,再降到目標尺寸 —— 直接指定小尺寸會鋸齒。"""
    tmp = out + ".1024.png"
    subprocess.run(["sips", "-s", "format", "png", svg, "--out", tmp],
                   check=True, capture_output=True)
    subprocess.run(["sips", "-z", str(size), str(size), tmp, "--out", out],
                   check=True, capture_output=True)
    os.remove(tmp)


def main():
    if sys.platform != "darwin":
        sys.exit("build_icon.py 只在 macOS 跑得動(要 sips 與 iconutil)。")
    for p in (FULL_SVG, SMALL_SVG):
        if not os.path.exists(p):
            sys.exit("找不到來源 SVG:%s" % p)

    iconset = os.path.join(ASSETS_DIR, "icon.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    try:
        for name, size, svg in SLICES:
            render(svg, size, os.path.join(iconset, name))
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", ICNS_PATH],
                       check=True, capture_output=True)
    finally:
        shutil.rmtree(iconset, ignore_errors=True)

    # favicon 走 32px:瀏覽器分頁實際顯示 16,但 HiDPI 螢幕會用到 2x。
    render(SMALL_SVG, 32, FAVICON_PATH)

    print("icon.icns   %6d bytes" % os.path.getsize(ICNS_PATH))
    print("favicon.png %6d bytes" % os.path.getsize(FAVICON_PATH))


if __name__ == "__main__":
    main()
