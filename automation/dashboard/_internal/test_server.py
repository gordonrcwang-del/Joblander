#!/usr/bin/env python3
"""
test_server.py — ticket 02 的認證測試。

只驗外部行為:帶對的 token 拿得到資料,沒帶或帶錯的一律 401。用真的 socket 打
真的 handler,不 mock —— 認證出錯的方式(header 名字打錯、路由順序寫反)只有走
完整條路才看得到。

    python3 test_server.py
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

# server 先 import —— 它會把 automation/_internal/ 放進 sys.path,runlock 才找得到。
import server
import runlock  # noqa: E402


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token is not None:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.Handler.token = "test-token-abc"
        # port 0 = 讓 OS 挑一個沒人用的,測試才不會跟真的 server 或彼此撞埠。
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_api_without_token_is_rejected(self):
        code, _ = _get(self.base + "/api/jobs")
        self.assertEqual(code, 401)

    def test_api_with_wrong_token_is_rejected(self):
        code, _ = _get(self.base + "/api/jobs", "not-the-token")
        self.assertEqual(code, 401)

    def test_api_with_token_returns_data(self):
        code, body = _get(self.base + "/api/jobs", "test-token-abc")
        self.assertEqual(code, 200)
        self.assertIn("jobs", json.loads(body))

    def test_readonly_health_also_requires_token(self):
        """純讀取的 endpoint 也要 token,以免其他本機程式列舉狀態。"""
        self.assertEqual(_get(self.base + "/api/health")[0], 401)
        self.assertEqual(_get(self.base + "/api/health", "test-token-abc")[0], 200)

    def test_index_is_served_without_token(self):
        """HTML 不含 token,所以不需要 token —— 擋了就變雞生蛋。"""
        code, body = _get(self.base + "/")
        self.assertEqual(code, 200)
        self.assertNotIn("test-token-abc", body)

    def test_unknown_path_is_404_not_a_token_oracle(self):
        code, _ = _get(self.base + "/api/nope", "test-token-abc")
        self.assertEqual(code, 404)


class ParseTest(unittest.TestCase):
    """職稱裡的 '|' 在表格裡是跳脫的(AMAT 真的有這種標題),不能直接 split。"""

    MD = "\n".join([
        "# 今日職缺 — 2026-08-26（週三）",
        "",
        "## 📮 待投遞（已勾選，還沒申請） — 1 筆",
        "",
        "| ✓ | Key | 職稱 | 公司 | 地點 | 上架 |",
        "|---|---|---|---|---|---|",
        "| [x] | `amat-R1` | [【PSE】地點: 新竹 \\| 大量招募中](https://x.test/1) | Applied Materials | Hsinchu | 2026-08-12 |",
        "",
        "## ✨ 新職缺 — 1 筆",
        "",
        "| ✓ | Key | 職稱 | 公司 | 地點 | 上架 |",
        "|---|---|---|---|---|---|",
        "| [ ] | `asml-J-2` | [CS Engineer 🎓](https://x.test/2) | ASML | Hsinchu | 2026-08-25 |",
        "",
        "---",
        "## 掃描狀態 · 2026-08-26 13:01",
        "",
        "| 公司 | 狀態 | 掃到 | 通過 | 標題不符 | 地點不符 | 教育背景不符 | 年資/學歷 |",
        "|---|---|---|---|---|---|---|---|",
        "| KLA | ✅ | 73 | 0 | 0 | 0 | 1 | 0 |",
    ])

    def setUp(self):
        self.jobs = server.parse_today_jobs(self.MD)

    def test_only_the_three_job_sections_are_parsed(self):
        self.assertEqual([j["key"] for j in self.jobs], ["amat-R1", "asml-J-2"])

    def test_escaped_pipe_in_title_survives(self):
        self.assertEqual(self.jobs[0]["title"], "【PSE】地點: 新竹 | 大量招募中")

    def test_bucket_and_selected_flag(self):
        self.assertEqual(self.jobs[0]["bucket"], "selected")
        self.assertTrue(self.jobs[0]["selected"])
        self.assertEqual(self.jobs[1]["bucket"], "new")
        self.assertFalse(self.jobs[1]["selected"])

    def test_new_grad_marker_is_a_flag_not_part_of_the_title(self):
        self.assertTrue(self.jobs[1]["new_grad"])
        self.assertEqual(self.jobs[1]["title"], "CS Engineer")

    def test_url_is_extracted_from_the_markdown_link(self):
        self.assertEqual(self.jobs[1]["url"], "https://x.test/2")


class StatusTest(unittest.TestCase):
    """狀態推導是純函式 —— 給一組假的佇列檔內容就能斷言,不需要跑瀏覽器。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = os.path.join(self.tmp.name, "work")
        os.makedirs(self.work)
        p = mock.patch.object(server, "APPLY_STATE_PATH",
                              os.path.join(self.tmp.name, "apply-state.json"))
        p.start(); self.addCleanup(p.stop)

    def _state(self, **fields):
        fields.setdefault("dir", self.work)
        with open(server.APPLY_STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(fields, fh)

    def _write(self, name, payload):
        with open(os.path.join(self.work, name), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_no_state_file_means_idle(self):
        self.assertEqual(server.current_status()["key"], "idle")

    def test_running_with_no_command_is_prep(self):
        self._state(phase="running", company="ASML")
        self.assertEqual(server.current_status()["label"], "準備中")

    def test_fill_action_reads_as_filling(self):
        self._state(phase="running", company="ASML", job_title="CS AE")
        self._write("command.json", {"action": "fill", "label": "First Name"})
        st = server.current_status()
        self.assertEqual(st["label"], "填空中")
        self.assertEqual(st["tone"], "busy")

    def test_read_only_actions_read_as_reading_the_form(self):
        self._state(phase="running")
        for action in ("goto", "get_state", "list_fields", "screenshot"):
            self._write("command.json", {"action": action})
            self.assertEqual(server.current_status()["label"], "讀表單中", action)

    def test_agent_status_beats_the_queue(self):
        """引擎閒置時,agent 在思考跟 agent 在等人看起來一樣 —— 只有 agent 自己
        寫的狀態檔分得出來,所以它要贏過佇列推導。"""
        self._state(phase="running", company="ASML")
        self._write("command.json", {"action": "fill"})
        self._write("agent-status.json", {"status": "awaiting_approval", "note": "核對表單"})
        st = server.current_status()
        self.assertEqual(st["label"], "等你同意")
        self.assertEqual(st["tone"], "attn")

    def test_login_required_is_an_attention_state(self):
        self._state(phase="running")
        self._write("agent-status.json", {"status": "login_required"})
        self.assertEqual(server.current_status()["tone"], "attn")

    def test_batch_position_shows_in_the_subtitle(self):
        self._state(phase="running", company="ASML", job_title="CS AE",
                    batch={"index": 2, "total": 3})
        self.assertIn("第 2/3 筆", server.current_status()["sub"])

    def test_awaiting_next_has_its_own_state(self):
        self._state(phase="awaiting_next", company="ASML", batch={"index": 1, "total": 2})
        self.assertEqual(server.current_status()["key"], "awaiting_next")

    def test_done_and_failed_short_circuit(self):
        self._state(phase="done", note="3 筆全數送出")
        self.assertEqual(server.current_status()["tone"], "ok")
        self._state(phase="failed", note="找不到欄位")
        self.assertEqual(server.current_status()["tone"], "bad")

    def test_version_changes_only_when_the_status_changes(self):
        first = server.status_payload()["version"]
        self.assertEqual(server.status_payload()["version"], first)
        self._state(phase="running", company="ASML")
        self.assertNotEqual(server.status_payload()["version"], first)


class LockTest(unittest.TestCase):
    """鎖存在時掃描請求被拒絕,鎖不存在時被接受。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "ledger.lock")
        p = mock.patch.object(runlock, "LOCK_PATH", self.path)
        p.start(); self.addCleanup(p.stop)

    def test_free_lock_reports_nobody(self):
        self.assertIsNone(runlock.is_busy(self.path))

    def test_held_lock_names_its_holder(self):
        with runlock.ledger_lock("職缺掃描", path=self.path):
            holder = runlock.is_busy(self.path)
        self.assertIn("職缺掃描", holder)

    def test_second_holder_is_refused_not_queued(self):
        with runlock.ledger_lock("職缺掃描", path=self.path):
            with self.assertRaises(runlock.LockBusy):
                with runlock.ledger_lock("面試信掃描", path=self.path):
                    pass

    def test_lock_is_released_after_the_block(self):
        with runlock.ledger_lock("職缺掃描", path=self.path):
            pass
        with runlock.ledger_lock("面試信掃描", path=self.path):
            pass    # 不該丟例外

    def test_a_dead_holder_does_not_wedge_the_lock(self):
        """flock 由核心在程序結束時釋放 —— 這是「crash 不會留下卡死的鎖」唯一
        站得住的做法,所以這裡用真的子程序驗,不是模擬。"""
        import subprocess
        import sys
        code = ("import sys; sys.path.insert(0, %r);"
                "import runlock;"
                "runlock.ledger_lock('短命的', path=%r).__enter__();"
                "print('held')" % (os.path.dirname(os.path.abspath(runlock.__file__)), self.path))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertIn("held", out.stdout)
        self.assertIsNone(runlock.is_busy(self.path))

INTERVIEW_MD = """# 面試行程

> 引言,不是表格。

## 面試排程

| 日期 | 時間 | 公司／職位 | 形式 | 狀態 | 對象 |
|---|---|---|---|---|---|
| 2026-08-25（二） | 09:00 | KLA — Regional Development Applications Engineer | [一面／線上](https://kla.zoom.com/j/949) | 已完成 | n@kla.test |
| 2026-08-27（四） | 14:30 | KLA — Regional Development Applications Engineer | 二面／現場 | 已取消 | n@kla.test |
| 2026-09-02（三） | 10:00 | ASML — CS Applications Engineer - Taichung | 一面／現場 | 改期中 | k@asml.test |
| 2026-09-05（六） | 11:00 | AMAT — Customer Engineer (iTeam) | 二面／線上 | 待進行 | m@amat.test |
"""


class InterviewParseTest(unittest.TestCase):
    """面試行程.md 收斂成一張表之後,狀態是欄位不是區塊(使用者 2026-08-26 要求)。"""

    def setUp(self):
        self.rows = server.parse_interviews(INTERVIEW_MD)

    def test_reads_every_row(self):
        """六欄的列要收得進來 —— 舊的解析器寫死 5 欄,多一欄會讓分頁整個變空。"""
        self.assertEqual(len(self.rows), 4)

    def test_status_is_a_real_field(self):
        self.assertEqual([r["status"] for r in self.rows],
                         ["已完成", "已取消", "改期中", "待進行"])

    def test_cancelled_comes_from_the_status_column(self):
        """不再靠「形式欄裡有沒有『取消』兩個字」猜。"""
        self.assertEqual([r["cancelled"] for r in self.rows],
                         [False, True, False, False])

    def test_status_colour_classes(self):
        self.assertEqual([r["status_cls"] for r in self.rows],
                         ["closed", "closed", "pending", "interview"])

    def test_unknown_status_falls_back_to_grey(self):
        text = INTERVIEW_MD.replace("| 待進行 |", "| 面到一半 |")
        row = [r for r in server.parse_interviews(text) if r["status"] == "面到一半"][0]
        self.assertEqual(row["status_cls"], "closed")

    def test_company_and_position_still_split_on_the_em_dash(self):
        row = self.rows[2]
        self.assertEqual(row["company"], "ASML")
        self.assertEqual(row["position"], "CS Applications Engineer - Taichung")

    def test_meeting_link_still_comes_out_of_the_form_cell(self):
        self.assertEqual(self.rows[0]["form"], "一面／線上")
        self.assertEqual(self.rows[0]["url"], "https://kla.zoom.com/j/949")
        self.assertEqual(self.rows[1]["url"], "")

    def test_header_and_separator_rows_are_skipped(self):
        self.assertNotIn("日期", [r["date_label"] for r in self.rows])


if __name__ == "__main__":
    unittest.main(verbosity=2)
