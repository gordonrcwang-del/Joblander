#!/usr/bin/env python3
"""
make_launcher.py — 產生一個點一下就開看板的 macOS App。

WHY THIS EXISTS
伺服器是 launchd 顧著的,所以它一直在跑,但登入時不會自己開分頁。網址現在是固定
的(通行碼在 2026-08-29 拿掉了),所以存書籤其實也行 —— 這支還留著,是因為它多做
一件書籤做不到的事:先確認伺服器活著,沒活著就問要不要叫起來,而不是丟一個連不上
的空白分頁給使用者自己猜。

USAGE
    python3 make_launcher.py            # 產生並安裝到 ~/Applications
    python3 make_launcher.py --print    # 只印出 AppleScript,不產生
"""
import os
import shutil
import subprocess
import sys
import tempfile

# 共用模組(runlock、config)住在 automation/_internal/。往上找到叫 automation
# 的那一層,不要數 ".." —— 這裡數錯過三次,其中一次讓排程掃描靜靜死了兩天,
# 因為它在寫 log 之前就死了。見 automation/_internal/test_imports.py。
_shared = os.path.abspath(__file__)
while os.path.basename(_shared) != "automation" and _shared != os.path.dirname(_shared):
    _shared = os.path.dirname(_shared)
sys.path.insert(0, os.path.join(_shared, "_internal"))
import config  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..", ".."))
APPS_DIR = os.path.join(os.path.expanduser("~"), "Applications")
APP_NAME = "joblander-dashboard.app"
APP_PATH = os.path.join(APPS_DIR, APP_NAME)
DEFAULT_PORT = 8765
DEFAULT_DISPLAY_NAME = "求職看板"

SCRIPT = '''on run
	-- 伺服器是 launchd 顧著的,平常一定在。不在的話講人話,不要丟一個空白分頁。
	set alive to false
	try
		do shell script "curl -s -o /dev/null --max-time 3 http://127.0.0.1:%(port)d/"
		set alive to true
	end try

	if not alive then
		display dialog "%(name)s 現在沒有在跑。" & return & return & ¬
			"要我試著把它叫起來嗎?" buttons {"取消", "叫起來"} ¬
			default button "叫起來" with icon caution
		if button returned of result is "叫起來" then
			try
				do shell script "launchctl load ~/Library/LaunchAgents/%(label)s.plist"
			end try
			delay 2
		else
			return
		end if
	end if

	do shell script "open http://127.0.0.1:%(port)d/"
end run
'''


# config() 曾經是這裡的一個區域函式 —— 現在是 automation/_internal/config.py。


def label():
    prefix = config.get("launchd_label_prefix", "com.example")
    return "%s.dashboard" % prefix


def source():
    return SCRIPT % {
        "port": int(config.get("dashboard_port", DEFAULT_PORT)),
        "name": config.get("dashboard_display_name", DEFAULT_DISPLAY_NAME),
        "label": label(),
    }


def build():
    if sys.platform != "darwin":
        sys.exit("這支只在 macOS 上有意義(需要 osacompile 與 .app)。")
    if not os.path.exists("/usr/bin/osacompile"):
        sys.exit("找不到 osacompile —— 需要 Xcode Command Line Tools。")

    os.makedirs(APPS_DIR, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".applescript",
                                     encoding="utf-8", delete=False) as fh:
        fh.write(source())
        tmp = fh.name
    try:
        subprocess.run(["/usr/bin/osacompile", "-o", APP_PATH, tmp], check=True)
    finally:
        os.unlink(tmp)

    # Finder 顯示的名字跟資料夾名稱分開 —— 檔名照 repo 的 kebab-case 規矩,
    # 使用者在 Dock 上看到的是看得懂的中文。
    name = config.get("dashboard_display_name", DEFAULT_DISPLAY_NAME)
    plist = os.path.join(APP_PATH, "Contents", "Info.plist")
    for verb in ("Add :CFBundleDisplayName string", "Set :CFBundleDisplayName"):
        r = subprocess.run(["/usr/libexec/PlistBuddy", "-c", "%s %s" % (verb, name), plist],
                           capture_output=True)
        if r.returncode == 0:
            break
    # osacompile 給的是 AppleScript 那顆通用圖示,Dock 上認不出是哪個 app。
    # applet.icns 就是 bundle 讀的那個檔名,覆蓋掉即可,不用改 Info.plist。
    icns = os.path.join(os.path.dirname(BASE_DIR), "assets", "icon.icns")
    if os.path.exists(icns):
        shutil.copyfile(icns, os.path.join(APP_PATH, "Contents", "Resources", "applet.icns"))
    else:
        print("警告:找不到 %s,先跑 build_icon.py,Dock 上會是預設圖示。" % icns)

    os.utime(APP_PATH, None)   # 讓 Finder 重讀 Info.plist 與圖示
    return APP_PATH


def main():
    if "--print" in sys.argv:
        print(source())
        return
    path = build()
    print("已建立:%s" % path)
    print("  在 Finder 的「應用程式」裡叫「%s」" %
          config.get("dashboard_display_name", DEFAULT_DISPLAY_NAME))
    print("  把它拖到 Dock,以後點一下就開看板")


if __name__ == "__main__":
    main()
