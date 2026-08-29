#!/usr/bin/env python3
"""
config.py — config.json 與 ~/.joblander 的唯一讀取點。

WHY THIS EXISTS
在這支之前,config.json 被五個地方各自打開,而且對「檔案不在」有三種不同反應:
scan_jobs.py 與 send_email_notification.py 直接 sys.exit,server.py 與
make_launcher.py 回 {},install_launchd.py 就地塞一個預設值。呼叫端沒辦法從外面
看出自己拿到的是哪一種,所以「沒設定」在有些路徑上會炸、有些會靜靜跑出錯的結果。

這裡把那個選擇變成呼叫端明講的一件事:
    get(key, default)  —— 缺檔缺鍵都回 default,不會炸。給「沒設定也能跑」的東西。
    require(key)       —— 缺檔或缺鍵就帶著 SETUP.md 指引結束。給「沒設定就別跑」的。

RUNTIME_DIR 也放這裡。它本來在 runlock.py、server.py、apply_batch.py 各寫一次字面
值,還有一份寫死在 make_launcher.py 的 AppleScript 裡。

USAGE
    from config import get, require, RUNTIME_DIR, REPO_ROOT
    port = get("dashboard_port", 8765)
    addr = require("gmail_address")
"""
import json
import os
import sys

# 這支住在 automation/_internal/,repo 根目錄固定在往上兩層 —— 這是本檔唯一一處
# 需要知道自己有多深的地方,其他人問 REPO_ROOT 就好。
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.json")
RUNTIME_DIR = os.path.join(os.path.expanduser("~"), ".joblander")

_MISSING = ("config.json not found at %s — copy config.example.json to "
            "config.json and fill it in (see docs/SETUP.md)." % CONFIG_PATH)

_cache = None


def load(path=None):
    """整份 config,缺檔就回 {}。壞掉的 JSON 一律讓它炸 —— 那是打錯字,不是沒設定,
    悄悄當成空的只會把問題推到很後面才爆。"""
    global _cache
    if path is not None:                      # 測試用,不進 cache
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if _cache is None:
        _cache = load(CONFIG_PATH)
    return _cache


def get(key, default=None):
    """沒有就回 default。給沒設定也該跑得起來的東西 —— dashboard 的埠號、
    launchd 的標籤前綴那種。"""
    value = load().get(key)
    return default if value is None else value


def require(key):
    """沒有就結束,並且說清楚要去改哪個檔。給沒設定就不該繼續的東西 ——
    寄信用的 Gmail 帳號那種:少了它,程式跑完只會安靜地什麼都沒寄。"""
    if not os.path.exists(CONFIG_PATH):
        sys.exit(_MISSING)
    value = load().get(key)
    if value is None:
        sys.exit("config.json 少了必要的 `%s` —— 對照 config.example.json 補上"
                 "(見 docs/SETUP.md)。" % key)
    return value


def runtime_path(*parts):
    """~/.joblander/... 底下的路徑,順手把目錄開好。"""
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    return os.path.join(RUNTIME_DIR, *parts)
