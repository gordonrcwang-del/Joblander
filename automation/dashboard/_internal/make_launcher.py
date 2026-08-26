#!/usr/bin/env python3
"""
make_launcher.py — 產生一個點一下就開看板的 macOS App。

WHY THIS EXISTS
伺服器是 launchd 顧著的,所以它一直在跑;但登入時不會自己開分頁,而且每次重啟
都換一組新通行碼(server.py 的 issue_token,刻意的)。這代表使用者不能存書籤 ——
隔天那組就失效了 —— 只能每次現讀通行碼再開。

要人記住並打一行指令太苛刻。這支把那行指令包成一個 App:點一下,它現讀
~/.joblander/dashboard-token,開好帶通行碼的網址。伺服器沒在跑就問要不要叫起來。

USAGE
    python3 make_launcher.py            # 產生並安裝到 ~/Applications
    python3 make_launcher.py --print    # 只印出 AppleScript,不產生
"""
import json
import os
import subprocess
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..", ".."))
APPS_DIR = os.path.join(os.path.expanduser("~"), "Applications")
APP_NAME = "joblander-dashboard.app"
APP_PATH = os.path.join(APPS_DIR, APP_NAME)
DEFAULT_PORT = 8765
DEFAULT_DISPLAY_NAME = "求職看板"

SCRIPT = '''on run
	set homePath to POSIX path of (path to home folder)
	set tokenPath to homePath & ".joblander/dashboard-token"

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

	try
		set tok to do shell script "cat " & quoted form of tokenPath
	on error
		display dialog "找不到通行碼,%(name)s 可能還沒裝好。" & return & return & ¬
			"先跑一次 install_launchd.py。" buttons {"好"} default button 1 with icon stop
		return
	end try

	do shell script "open " & quoted form of ("http://127.0.0.1:%(port)d/?t=" & tok)
end run
'''


def config():
    path = os.path.join(REPO_ROOT, "config.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def label():
    prefix = config().get("launchd_label_prefix", "com.example")
    return "%s.dashboard" % prefix


def source():
    cfg = config()
    return SCRIPT % {
        "port": int(cfg.get("dashboard_port", DEFAULT_PORT)),
        "name": cfg.get("dashboard_display_name", DEFAULT_DISPLAY_NAME),
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
    name = config().get("dashboard_display_name", DEFAULT_DISPLAY_NAME)
    plist = os.path.join(APP_PATH, "Contents", "Info.plist")
    for verb in ("Add :CFBundleDisplayName string", "Set :CFBundleDisplayName"):
        r = subprocess.run(["/usr/libexec/PlistBuddy", "-c", "%s %s" % (verb, name), plist],
                           capture_output=True)
        if r.returncode == 0:
            break
    os.utime(APP_PATH, None)   # 讓 Finder 重讀 Info.plist
    return APP_PATH


def main():
    if "--print" in sys.argv:
        print(source())
        return
    path = build()
    print("已建立:%s" % path)
    print("  在 Finder 的「應用程式」裡叫「%s」" %
          config().get("dashboard_display_name", DEFAULT_DISPLAY_NAME))
    print("  把它拖到 Dock,以後點一下就開看板")


if __name__ == "__main__":
    main()
