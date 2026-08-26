#!/usr/bin/env python3
"""
install_launchd.py — 讓 dashboard 在登入時自己起來(ticket 09)。

WHY THIS EXISTS
「登入電腦就能用」是這個 dashboard 的前提之一。plist 用產生的而不是簽進 repo 的:
裡面每一條路徑都是這台機器專屬的(家目錄、python 位置、repo 放在哪),簽進去等於
把作者的目錄結構強加給每個 clone 的人。

用 launchd 不是 cron —— 專案既有的兩個排程都是 launchd,而 cron 在 macOS 上拿不到
TCC 授權。KeepAlive 讓它掉了會自己回來,dashboard 死掉時使用者只會看到瀏覽器連不上,
不會有任何提示。

USAGE
    python3 install_launchd.py            # 產生 plist、載入、印出網址
    python3 install_launchd.py --uninstall
    python3 install_launchd.py --print    # 只印 plist,不寫檔
"""
import json
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..", ".."))
SERVER_PY = os.path.join(BASE_DIR, "server.py")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "launchd.log")
PLIST_DIR = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents")

PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>EnvironmentVariables</key>
\t<dict>
\t\t<key>HOME</key>
\t\t<string>{home}</string>
\t\t<key>PATH</key>
\t\t<string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
\t</dict>
\t<key>Label</key>
\t<string>{label}</string>
\t<key>ProgramArguments</key>
\t<array>
\t\t<string>{python}</string>
\t\t<string>{server}</string>
\t\t<string>--no-browser</string>
\t</array>
\t<key>KeepAlive</key>
\t<true/>
\t<key>RunAtLoad</key>
\t<true/>
\t<key>StandardErrorPath</key>
\t<string>{log}</string>
\t<key>StandardOutPath</key>
\t<string>{log}</string>
</dict>
</plist>
"""


def label():
    """沿用 config.json 的 launchd_label_prefix,跟既有兩個排程同一個命名空間。"""
    path = os.path.join(REPO_ROOT, "config.json")
    prefix = "com.example"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            prefix = json.load(fh).get("launchd_label_prefix", prefix)
    return "%s.dashboard" % prefix


def python_bin():
    """launchd 的 PATH 很小,一定要絕對路徑。用跑這支安裝程式的同一個直譯器 ——
    使用者能跑安裝,就代表那個直譯器裝得起這個專案要的東西。"""
    return sys.executable


def render():
    return PLIST_TEMPLATE.format(
        home=os.path.expanduser("~"), label=label(),
        python=python_bin(), server=SERVER_PY, log=LOG_PATH)


def plist_path():
    return os.path.join(PLIST_DIR, label() + ".plist")


def uninstall():
    path = plist_path()
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), label())],
                   capture_output=True)
    if os.path.exists(path):
        os.remove(path)
        print("已移除 %s" % path)
    else:
        print("本來就沒裝:%s" % path)


def install():
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(PLIST_DIR, exist_ok=True)
    path = plist_path()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render())
    # 先 bootout 再 bootstrap —— 直接 bootstrap 一個已載入的 label 會失敗,
    # 而且是那種訊息看不出原因的失敗。
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), label())],
                   capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", "gui/%d" % os.getuid(), path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("launchctl bootstrap 失敗:%s" % (r.stderr.strip() or r.stdout.strip()))
    print("已安裝並啟動:%s" % label())
    print("  plist: %s" % path)
    print("  log:   %s" % LOG_PATH)
    print("  網址(含 token)在 log 的最後幾行,或讀 ~/.joblander/dashboard-token")

    # 順手把啟動器做出來 —— 沒有它,使用者每次都得記一行指令(而且 token 每次
    # 重啟都會換,書籤存不住)。做不出來不算裝失敗,伺服器才是主體。
    try:
        import make_launcher
        print("  啟動器:%s" % make_launcher.build())
        print("  把它從「應用程式」拖到 Dock,以後點一下就開")
    except SystemExit as exc:
        print("  (啟動器沒做成:%s)" % exc)
    except Exception as exc:
        print("  (啟動器沒做成:%s)" % exc)


def main():
    if "--print" in sys.argv:
        print(render())
    elif "--uninstall" in sys.argv:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
