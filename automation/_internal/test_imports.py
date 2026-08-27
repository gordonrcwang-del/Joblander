#!/usr/bin/env python3
"""
test_imports.py — 每一支進入點都 import 得起來。

WHY THIS EXISTS
2026-08-26 加共用鎖的時候,同一個 sys.path 錯誤犯了三次(server.py、test_server.py、
run_scan.py)。前兩次當場就炸,第三次沒有 —— run_scan.py 是 launchd 在背景跑的,
它在寫 log 之前就死了,所以排程掃描連續失敗而畫面上什麼都沒有,是使用者說「我按了
掃描但沒收到信」才發現的。

這支就是把那個沉默補起來:每一支會被排程或 dashboard 叫起來的腳本,都真的 import
一次。不驗行為,只驗「載入得起來」—— 那正是上面那個 bug 唯一的症狀。

    python3 test_imports.py
"""
import os
import subprocess
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))

# 被 launchd、dashboard 或另一支腳本叫起來的進入點。純 library 不必列 ——
# 它們本來就會被這些人帶進來。
ENTRY_POINTS = [
    "automation/_internal/runlock.py",
    "automation/job-search/_internal/scan_jobs.py",
    "automation/interview-scan/_internal/run_scan.py",
    "automation/interview-scan/_internal/todo.py",
    "automation/interview-scan/_internal/send_email_notification.py",
    "automation/dashboard/_internal/server.py",
    "automation/dashboard/_internal/install_launchd.py",
    "automation/dashboard/_internal/make_launcher.py",
    "automation/job-apply/_internal/apply_batch.py",
]


class ImportTest(unittest.TestCase):
    def test_every_entry_point_imports(self):
        broken = []
        for rel in ENTRY_POINTS:
            path = os.path.join(REPO_ROOT, rel)
            if not os.path.exists(path):
                broken.append("%s —— 檔案不存在" % rel)
                continue
            # 用子程序:每一支都要有自己乾淨的 sys.path,不能靠前一支順手插進來的
            # 路徑,否則就驗不出這個 bug(它正是「自己沒把路徑接對」)。
            code = (
                "import importlib.util, sys;"
                "spec = importlib.util.spec_from_file_location('m', %r);"
                "mod = importlib.util.module_from_spec(spec);"
                "sys.modules['m'] = mod;"
                "spec.loader.exec_module(mod)" % path
            )
            proc = subprocess.run([sys.executable, "-c", code],
                                  cwd=REPO_ROOT, capture_output=True, text=True)
            if proc.returncode != 0:
                last = (proc.stderr.strip().splitlines() or ["(沒有錯誤輸出)"])[-1]
                broken.append("%s —— %s" % (rel, last))
        self.assertEqual(broken, [], "這幾支 import 不起來:\n  " + "\n  ".join(broken))


if __name__ == "__main__":
    unittest.main(verbosity=2)
