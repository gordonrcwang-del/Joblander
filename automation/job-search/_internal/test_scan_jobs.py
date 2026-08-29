#!/usr/bin/env python3
"""
test_scan_jobs.py — 紀錄檔寫入路徑的回歸測試(ticket 01)。

WHY THIS EXISTS
dashboard 把每一次勾選、略過、狀態更新都壓在 mark / queue / progress 這三條路徑
上,而它們原本零測試。這裡只驗**外部行為** —— 紀錄檔和兩份 markdown 的內容真的
變了 —— 不驗哪個函式被呼叫過。

每個測試在自己的臨時目錄跑,把模組層的四個路徑常數指過去,絕不碰使用者真實的
紀錄檔。只用標準函式庫,沿用專案「掃描器零依賴」的慣例。

    python3 test_scan_jobs.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_jobs
import runlock


def make_job(company="ASML", cid="asml", state="candidate", title="CS Engineer",
             posted="2026-08-20", first_seen="2026-08-20", locations=None):
    """一筆最小但完整的職缺紀錄 —— 欄位照 make_job_record 的形狀。"""
    return {
        "adapter": "workday", "applied_date": None, "attempts": [],
        "company": company, "company_id": cid, "external_path": "/x",
        "first_seen": first_seen, "last_seen": "2026-08-26",
        "locations": locations or ["Hsinchu,TWN"], "new_grad_flag": False,
        "note": "", "posted_date": posted, "progress": None,
        "progress_changed": None, "req_id": "R1",
        "screen": {"verdict": "pass", "location_rule": "Hsinchu,TWN ~ hsinchu"},
        "selected_date": None, "skipped_date": None, "state": state,
        "state_changed": first_seen, "title": title,
        "url": "https://example.test/%s" % title.replace(" ", "-"),
    }


class LedgerCase(unittest.TestCase):
    """把 scan_jobs 的四個路徑常數指到臨時目錄。"""

    JOBS = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.tmp.name
        self.ledger_path = os.path.join(d, "ledger.json")
        self.today_path = os.path.join(d, "today-jobs.md")
        self.applied_path = os.path.join(d, "applied-jobs.md")
        # 鎖也指到臨時檔 —— 測試不該碰 ~/.joblander,也不該被真的掃描擋住。
        lock_patch = mock.patch("runlock.LOCK_PATH", os.path.join(d, "ledger.lock"))
        lock_patch.start()
        self.addCleanup(lock_patch.stop)
        patches = {
            "LEDGER_PATH": self.ledger_path,
            "LEDGER_BAK_PATH": os.path.join(d, "ledger.bak.json"),
            "TODAY_JOBS_PATH": self.today_path,
            "APPLIED_JOBS_PATH": self.applied_path,
        }
        for name, value in patches.items():
            p = mock.patch.object(scan_jobs, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.write_ledger({"schema": 1, "companies": {}, "jobs": dict(self.JOBS)})

    # -- helpers ------------------------------------------------------------
    def write_ledger(self, ledger):
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, ensure_ascii=False)

    def ledger(self):
        with open(self.ledger_path, encoding="utf-8") as fh:
            return json.load(fh)

    def today_md(self):
        with open(self.today_path, encoding="utf-8") as fh:
            return fh.read()

    def applied_md(self):
        with open(self.applied_path, encoding="utf-8") as fh:
            return fh.read()

    def run_cmd(self, fn, *argv):
        with mock.patch.object(sys, "argv", ["scan_jobs.py"] + list(argv)):
            fn()

    def capture(self, fn, *argv):
        """跑一個子指令並收下它整份 stdout —— CLI 契約驗的就是這個字串。"""
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["scan_jobs.py"] + list(argv)):
            with contextlib.redirect_stdout(buf):
                fn()
        return buf.getvalue()

    def section(self, text, heading_keyword):
        """抓出某個 '## …' 區塊的內容,到下一個 '## ' 為止。"""
        out, inside = [], False
        for line in text.splitlines():
            if line.startswith("## "):
                inside = heading_keyword in line
                continue
            if inside:
                out.append(line)
        return "\n".join(out)


class MarkTest(LedgerCase):
    JOBS = {
        "asml-1": make_job(title="CS Engineer"),
        "asml-2": make_job(title="EUV Planner"),
    }

    def test_selected_moves_the_job_into_the_pending_section(self):
        self.run_cmd(scan_jobs.cmd_mark, "mark", "asml-1", "selected")
        self.assertEqual(self.ledger()["jobs"]["asml-1"]["state"], "selected")
        md = self.today_md()
        self.assertIn("asml-1", self.section(md, "待投遞"))
        self.assertNotIn("asml-1", self.section(md, "之前看過"))

    def test_selected_stamps_selected_date_so_queue_can_sort(self):
        """queue 依 selected_date 排序。mark 不寫它的話,同一家公司裡混著
        dashboard 勾的(None)和 today-jobs.md 勾的(日期)會讓排序爆掉。"""
        self.run_cmd(scan_jobs.cmd_mark, "mark", "asml-1", "selected")
        self.assertEqual(self.ledger()["jobs"]["asml-1"]["selected_date"], scan_jobs.TODAY)

    def test_skipped_disappears_from_every_visible_section(self):
        self.run_cmd(scan_jobs.cmd_mark, "mark", "asml-1", "skipped")
        self.assertEqual(self.ledger()["jobs"]["asml-1"]["state"], "skipped")
        self.assertNotIn("asml-1", self.today_md())
        self.assertIn("asml-2", self.today_md())      # 其他職缺不受影響

    def test_skipped_records_are_thinned_on_write(self):
        """skipped 的紀錄刻意只留 THIN_KEEP 那幾個欄位(2026-08-25 的瘦身決定),
        所以 skipped_date 和 attempts 寫進檔案後就不見了。這裡把那個行為釘住 ——
        dashboard 之後想顯示「什麼時候略過的」會踩到,不是它壞掉。"""
        self.run_cmd(scan_jobs.cmd_mark, "mark", "asml-1", "skipped")
        job = self.ledger()["jobs"]["asml-1"]
        self.assertEqual(job["state"], "skipped")
        self.assertNotIn("skipped_date", job)
        self.assertNotIn("attempts", job)
        self.assertEqual(set(job) - {"screen"}, set(scan_jobs.THIN_KEEP) & set(job))

    def test_applied_also_rewrites_the_applied_list(self):
        self.run_cmd(scan_jobs.cmd_mark, "mark", "asml-1", "applied")
        job = self.ledger()["jobs"]["asml-1"]
        self.assertEqual(job["applied_date"], scan_jobs.TODAY)
        self.assertEqual(job["progress"], "pending")
        self.assertIn("asml-1", self.applied_md())

    def test_unknown_key_exits_nonzero_without_touching_the_ledger(self):
        before = self.ledger()
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd(scan_jobs.cmd_mark, "mark", "nope-9", "selected")
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(self.ledger(), before)

    def test_invalid_state_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_cmd(scan_jobs.cmd_mark, "mark", "asml-1", "banana")
        self.assertEqual(self.ledger()["jobs"]["asml-1"]["state"], "candidate")


class QueueTest(LedgerCase):
    JOBS = {
        "asml-1": make_job(title="A"),
        "asml-2": make_job(title="B"),
        "amat-1": make_job(company="Applied Materials", cid="amat", title="C"),
        "asml-done": make_job(title="D", state="applied"),
    }

    def _queue(self):
        import io
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            scan_jobs.cmd_queue()
        return json.loads(buf.getvalue())

    def test_queue_is_empty_until_something_is_selected(self):
        self.assertEqual(self._queue(), {})

    def test_queue_groups_by_company_id(self):
        for key in ("asml-1", "asml-2", "amat-1"):
            self.run_cmd(scan_jobs.cmd_mark, "mark", key, "selected")
        grouped = self._queue()
        self.assertEqual(sorted(grouped), ["amat", "asml"])
        self.assertEqual(len(grouped["asml"]), 2)
        self.assertEqual(len(grouped["amat"]), 1)

    def test_queue_excludes_already_applied_jobs(self):
        self.run_cmd(scan_jobs.cmd_mark, "mark", "asml-1", "selected")
        grouped = self._queue()
        self.assertEqual([j["key"] for j in grouped["asml"]], ["asml-1"])

    def test_queue_survives_a_mix_of_mark_selected_and_ingest_selected(self):
        """回歸:ingest 走 today-jobs.md 會蓋 selected_date,mark 原本不會。
        兩者混在同一家公司時,排序拿 None 跟字串比會 TypeError。"""
        ledger = self.ledger()
        ledger["jobs"]["asml-2"]["state"] = "selected"
        ledger["jobs"]["asml-2"]["selected_date"] = "2026-08-01"
        self.write_ledger(ledger)
        self.run_cmd(scan_jobs.cmd_mark, "mark", "asml-1", "selected")
        grouped = self._queue()          # 不該丟例外
        self.assertEqual(len(grouped["asml"]), 2)


class ProgressTest(LedgerCase):
    JOBS = {"asml-1": make_job(title="A", state="applied")}

    def setUp(self):
        super().setUp()
        ledger = self.ledger()
        ledger["jobs"]["asml-1"]["applied_date"] = "2026-08-13"
        ledger["jobs"]["asml-1"]["progress"] = "pending"
        self.write_ledger(ledger)

    def test_progress_updates_the_status_column_in_the_applied_list(self):
        self.run_cmd(scan_jobs.cmd_progress, "progress", "asml-1", "interview")
        self.assertEqual(self.ledger()["jobs"]["asml-1"]["progress"], "interview")
        self.assertIn(scan_jobs.PROGRESS_LABEL["interview"], self.applied_md())

    def test_progress_stamps_the_change_date(self):
        self.run_cmd(scan_jobs.cmd_progress, "progress", "asml-1", "offer")
        self.assertEqual(self.ledger()["jobs"]["asml-1"]["progress_changed"], scan_jobs.TODAY)

    def test_note_is_stored(self):
        self.run_cmd(scan_jobs.cmd_progress, "progress", "asml-1", "interview",
                     "--note", "8/28 一面")
        self.assertEqual(self.ledger()["jobs"]["asml-1"]["note"], "8/28 一面")

    def test_invalid_status_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_cmd(scan_jobs.cmd_progress, "progress", "asml-1", "banana")
        self.assertEqual(self.ledger()["jobs"]["asml-1"]["progress"], "pending")


class BackupTest(LedgerCase):
    JOBS = {"asml-1": make_job()}

    def test_every_write_leaves_a_backup_of_the_previous_ledger(self):
        self.run_cmd(scan_jobs.cmd_mark, "mark", "asml-1", "selected")
        self.assertTrue(os.path.exists(scan_jobs.LEDGER_BAK_PATH))
        with open(scan_jobs.LEDGER_BAK_PATH, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["jobs"]["asml-1"]["state"], "candidate")


class CliContractTest(LedgerCase):
    """docstring 的 CLI CONTRACT 那一段的執行版本。

    server.py 與 apply_batch.py 只能看到 stdout 跟離開碼 —— 它們 import 不到這個
    模組。所以那兩樣東西是介面本身,不是實作細節。這裡把它釘住,免得哪天有人在
    queue 的路徑上加一行 print,批次投遞就整批解析失敗。
    """

    JOBS = {
        "asml-1": make_job(title="CS Engineer", state="selected"),
        "asml-2": make_job(title="EUV Planner", state="candidate"),
        "amat-1": make_job(company="AMAT", cid="amat", title="Process Eng",
                           state="selected"),
    }

    def setUp(self):
        super().setUp()
        ledger = self.ledger()
        ledger["jobs"]["asml-1"]["selected_date"] = "2026-08-20"
        ledger["jobs"]["amat-1"]["selected_date"] = "2026-08-21"
        self.write_ledger(ledger)

    def test_queue_stdout_is_json_and_nothing_else(self):
        out = self.capture(scan_jobs.cmd_queue, "queue")
        json.loads(out)          # 前後多一個字都會在這裡炸

    def test_queue_groups_by_company_id_and_omits_unselected(self):
        queue = json.loads(self.capture(scan_jobs.cmd_queue, "queue"))
        self.assertEqual(sorted(queue), ["amat", "asml"])
        self.assertEqual([j["key"] for j in queue["asml"]], ["asml-1"])

    def test_queue_entries_carry_exactly_the_documented_fields(self):
        queue = json.loads(self.capture(scan_jobs.cmd_queue, "queue"))
        self.assertEqual(sorted(queue["asml"][0]),
                         ["key", "locations", "selected_date", "title", "url"])

    def test_queue_sorts_by_selected_date_within_a_company(self):
        ledger = self.ledger()
        ledger["jobs"]["asml-2"]["state"] = "selected"
        ledger["jobs"]["asml-2"]["selected_date"] = "2026-08-01"
        self.write_ledger(ledger)
        queue = json.loads(self.capture(scan_jobs.cmd_queue, "queue"))
        self.assertEqual([j["key"] for j in queue["asml"]], ["asml-2", "asml-1"])

    def test_an_empty_queue_is_an_empty_object_not_an_error(self):
        """apply_batch 會把非零離開碼當成失敗中止 —— 沒東西可投不是失敗。"""
        self.write_ledger({"schema": 1, "companies": {}, "jobs": {}})
        self.assertEqual(json.loads(self.capture(scan_jobs.cmd_queue, "queue")), {})

    def test_unknown_subcommand_exits_one(self):
        with mock.patch.object(sys, "argv", ["scan_jobs.py", "nonsense"]):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    scan_jobs.main()
        self.assertEqual(cm.exception.code, 1)

    def test_mark_without_enough_arguments_exits_one(self):
        with mock.patch.object(sys, "argv", ["scan_jobs.py", "mark"]):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    scan_jobs.cmd_mark()
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
