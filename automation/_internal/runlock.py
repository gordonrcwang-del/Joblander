#!/usr/bin/env python3
"""
runlock.py — 跨程序互斥鎖,給所有會寫 ledger.json 的東西共用(ticket 06)。

WHY THIS EXISTS
職缺掃描、面試信掃描、dashboard 的手動觸發都會寫同一份紀錄檔。兩個同時跑會互相
覆蓋 —— 這也是既有兩個排程刻意錯開二十分鐘的原因。錯開是靠運氣,鎖才是保證。

用 fcntl.flock 而不是「檢查檔案存不存在」:flock 由核心在程序結束時自動釋放,
所以掃描被 kill 或 crash 都不會留下一把卡死的鎖。這是「鎖檔在程序異常結束後不會
永久卡住」那條驗收條件唯一站得住的做法。

鎖檔內容(pid / 誰 / 何時)只是給人看的診斷訊息,不是鎖本身。

USAGE
    from runlock import ledger_lock, LockBusy
    try:
        with ledger_lock("job scan"):
            ...
    except LockBusy as e:
        print(e.holder)     # "interview scan (pid 4321, 自 14:02)"
"""
import errno
import fcntl
import json
import os
import sys
import time

# 共用模組(runlock、config)住在 automation/_internal/。往上找到叫 automation
# 的那一層,不要數 ".." —— 這裡數錯過三次,其中一次讓排程掃描靜靜死了兩天,
# 因為它在寫 log 之前就死了。見 automation/_internal/test_imports.py。
_shared = os.path.abspath(__file__)
while os.path.basename(_shared) != "automation" and _shared != os.path.dirname(_shared):
    _shared = os.path.dirname(_shared)
sys.path.insert(0, os.path.join(_shared, "_internal"))
from config import RUNTIME_DIR  # noqa: E402

LOCK_PATH = os.path.join(RUNTIME_DIR, "ledger.lock")


class LockBusy(RuntimeError):
    def __init__(self, holder):
        super().__init__("已經有一個掃描在跑:%s" % holder)
        self.holder = holder


def read_holder(path=LOCK_PATH):
    """鎖被誰拿著 —— 純診斷,拿不到就回一句模糊但誠實的話。"""
    try:
        with open(path, encoding="utf-8") as fh:
            info = json.load(fh)
        return "%s(pid %s,自 %s)" % (info.get("what", "某個掃描"),
                                      info.get("pid", "?"), info.get("since", "?"))
    except (OSError, ValueError):
        return "某個掃描(讀不到鎖檔內容)"


class ledger_lock:
    """非阻塞的獨佔鎖。拿不到就丟 LockBusy —— 不排隊。

    排隊在這裡是錯的:使用者按了按鈕卻要等二十分鐘才動,跟壞掉沒兩樣,而且他
    看不到自己在隊伍裡。直接拒絕並說明是誰在跑,他才知道要等還是去做別的事。
    """

    def __init__(self, what, path=LOCK_PATH, timeout=0.0):
        """timeout > 0 時在放棄前重試 —— 給 mark/progress 這種瞬間就結束的寫入用:
        它們跟掃描搶鎖時等一下就好,不必讓使用者的勾選白白掉。掃描本身用
        timeout=0,因為它可能跑好幾分鐘,排隊等於當機。"""
        self.what = what
        self.path = path
        self.timeout = timeout
        self.fh = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # 'a+' 不會截斷 —— 搶輸的人不能把贏家寫的診斷資訊清掉。
        self.fh = open(self.path, "a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN):
                    self.fh.close(); self.fh = None
                    raise
                if time.monotonic() >= deadline:
                    holder = read_holder(self.path)
                    self.fh.close(); self.fh = None
                    raise LockBusy(holder)
                time.sleep(0.2)
        self.fh.seek(0)
        self.fh.truncate()
        json.dump({"what": self.what, "pid": os.getpid(),
                   "since": time.strftime("%Y-%m-%d %H:%M:%S")},
                  self.fh, ensure_ascii=False)
        self.fh.flush()
        return self

    def __exit__(self, *exc):
        if self.fh:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None
        return False


def is_busy(path=LOCK_PATH):
    """不搶鎖,只問「現在有人在跑嗎」。給 dashboard 畫面用。"""
    if not os.path.exists(path):
        return None
    try:
        fh = open(path, "a+", encoding="utf-8")
    except OSError:
        return None
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return read_holder(path)
    else:
        fcntl.flock(fh, fcntl.LOCK_UN)
        return None
    finally:
        fh.close()
