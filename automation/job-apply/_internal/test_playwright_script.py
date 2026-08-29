#!/usr/bin/env python3
"""
test_playwright_script.py — 引擎裡不需要瀏覽器的那一半。

WHY THIS EXISTS
playwright_script.py 有 620 行而且一個測試都沒有,因為「它要開瀏覽器」。但它真正
要瀏覽器的只有最後那一下點擊;在那之前的東西 —— 檔案佇列的協定、指令怎麼變成
locator、batch 在第幾步停下來、不認識的 action 回什麼 —— 全都只是在對 dict 做決定。

execute(page, cmd, work_dir) 本來就把 page 當參數收進來,所以那裡早就是一道縫,
只是一直只有一種東西站在上面(真的 Page)。這支放上第二種:一個只記錄呼叫、不做
事的假 Page。有了第二個實作,那道縫才是真的縫,而不是理論上的。

沒放進 automation/_internal/test_imports.py 的 ENTRY_POINTS:那份清單是給 launchd
與 dashboard 會叫的東西,而這支要 playwright,新機器上不一定裝了。

    python3 test_playwright_script.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import playwright_script as engine
except ImportError as e:            # playwright 沒裝
    print("skipped: %s" % e)
    raise SystemExit(0)


class FakeLocator:
    """只記下自己是怎麼被找出來的。不假裝會點擊 —— 那部分本來就要真的瀏覽器。"""

    def __init__(self, how, args, kwargs):
        self.how, self.args, self.kwargs = how, args, kwargs


class FakePage:
    """Page 介面裡「不碰瀏覽器的那些路徑」真正用到的那一小片。

    刻意不補全:補全了就得跟著 Playwright 的 API 一起維護,而多出來的那些方法一個
    測試也不會走到。哪天有測試需要,那時再加。
    """

    def __init__(self, pages=None, evaluate_result=None):
        self.url = "https://example.test/apply"
        self.body_text = "Legal Name\nFirst Name"
        self.waits = []
        self._evaluate_result = evaluate_result if evaluate_result is not None else []
        self.context = type("Ctx", (), {"pages": pages if pages is not None else [self]})()

    def _rec(self, how):
        return lambda *a, **k: FakeLocator(how, a, k)

    def __getattr__(self, name):
        if name.startswith("get_by_") or name == "locator":
            return self._rec(name)
        raise AttributeError(name)

    def title(self):
        return "Apply — Example"

    def inner_text(self, _selector):
        return self.body_text

    def evaluate(self, _script):
        return self._evaluate_result

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


class CommandQueueTest(unittest.TestCase):
    """command.json / result.json 是 agent 跟引擎之間唯一的通道。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cmd_path = os.path.join(self.tmp.name, "command.json")
        self.result_path = os.path.join(self.tmp.name, "result.json")

    def test_no_command_file_is_not_an_error(self):
        self.assertEqual(engine.load_command(self.cmd_path), (None, None))

    def test_malformed_json_reports_instead_of_vanishing(self):
        """壞掉的寫入如果跟「還沒有指令」長一樣,呼叫端會永遠等下去。"""
        with open(self.cmd_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        cmd, err = engine.load_command(self.cmd_path)
        self.assertIsNone(cmd)
        self.assertTrue(err)

    def test_valid_command_reads_back(self):
        with open(self.cmd_path, "w", encoding="utf-8") as fh:
            json.dump({"action": "get_state"}, fh)
        self.assertEqual(engine.load_command(self.cmd_path), ({"action": "get_state"}, None))

    def test_result_is_written_atomically_and_leaves_no_tmp(self):
        engine.write_result(self.result_path, {"ok": True, "note": "中文"})
        with open(self.result_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["note"], "中文")
        self.assertFalse(os.path.exists(self.result_path + ".tmp"))

    def test_result_overwrites_the_previous_one_whole(self):
        engine.write_result(self.result_path, {"ok": False, "error": "x" * 200})
        engine.write_result(self.result_path, {"ok": True})
        with open(self.result_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"ok": True})


class ScopeTest(unittest.TestCase):
    def test_no_group_searches_the_whole_page(self):
        page = FakePage()
        self.assertIs(engine._scope(page, {"action": "click"}), page)

    def test_group_narrows_to_that_aria_group(self):
        loc = engine._scope(FakePage(), {"group": "Education 2"})
        self.assertEqual(loc.how, "get_by_role")
        self.assertEqual(loc.args[0], "group")
        self.assertEqual(loc.kwargs["name"], "Education 2")

    def test_group_matching_is_exact_by_default(self):
        """不然 'Education 1' 會同時命中 'Education 10'。"""
        self.assertTrue(engine._scope(FakePage(), {"group": "Education 1"}).kwargs["exact"])


class LocatorResolutionTest(unittest.TestCase):
    """一個指令可能同時帶好幾個選擇器欄位,誰贏是固定的。"""

    def resolve(self, cmd):
        return engine._locator_for(FakePage(), cmd)

    def test_role_wins_over_everything_else(self):
        loc = self.resolve({"role": "button", "name": "Apply", "text": "Apply", "label": "Apply"})
        self.assertEqual(loc.how, "get_by_role")

    def test_text_beats_label(self):
        self.assertEqual(self.resolve({"text": "Next", "label": "Next"}).how, "get_by_text")

    def test_label_beats_placeholder(self):
        self.assertEqual(self.resolve({"label": "City", "placeholder": "City"}).how, "get_by_label")

    def test_placeholder_beats_raw_selector(self):
        self.assertEqual(self.resolve({"placeholder": "Search", "selector": "#s"}).how,
                         "get_by_placeholder")

    def test_raw_selector_is_the_last_resort(self):
        self.assertEqual(self.resolve({"selector": "#submit"}).how, "locator")

    def test_nothing_to_match_on_returns_none(self):
        self.assertIsNone(self.resolve({"action": "click"}))

    def test_matching_is_inexact_by_default(self):
        """必填欄位的 accessible name 帶著紅色的 * —— exact 會全部找不到。"""
        self.assertFalse(self.resolve({"label": "First Name"}).kwargs["exact"])


class ExecuteContractTest(unittest.TestCase):
    """RESULT FORMAT 那一段的執行版本:每個結果都有 ok,失敗要說得出原因。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_cmd(self, cmd, page=None):
        return engine.execute(page or FakePage(), cmd, self.tmp.name)

    def test_unknown_action_fails_and_names_it(self):
        r = self.run_cmd({"action": "teleport"})
        self.assertFalse(r["ok"])
        self.assertIn("teleport", r["error"])

    def test_missing_action_key_fails_rather_than_raising(self):
        self.assertFalse(self.run_cmd({})["ok"])

    def test_get_state_reports_where_the_browser_is(self):
        r = self.run_cmd({"action": "get_state"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["url"], "https://example.test/apply")
        self.assertEqual(r["title"], "Apply — Example")

    def test_list_fields_is_capped_so_one_result_cannot_flood_the_agent(self):
        page = FakePage(evaluate_result=[{"label": "f%d" % i} for i in range(500)])
        self.assertEqual(len(self.run_cmd({"action": "list_fields"}, page)["fields"]), 200)

    def test_switch_page_refuses_when_no_second_tab_opened(self):
        r = self.run_cmd({"action": "switch_page"})
        self.assertFalse(r["ok"])
        self.assertIn("no additional page", r["error"])

    def test_wait_converts_seconds_to_milliseconds(self):
        page = FakePage()
        self.run_cmd({"action": "wait", "seconds": 3}, page)
        self.assertIn(3000, page.waits)


class BatchTest(unittest.TestCase):
    """batch 是省來回次數的主要手段,所以它停在哪裡必須說得準。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_batch(self, commands):
        return engine.execute(FakePage(), {"action": "batch", "commands": commands}, self.tmp.name)

    def test_all_good_reports_ok_with_every_result(self):
        r = self.run_batch([{"action": "get_state"}, {"action": "get_state"}])
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["results"]), 2)

    def test_stops_at_the_first_failure_and_says_which_index(self):
        r = self.run_batch([{"action": "get_state"}, {"action": "teleport"}, {"action": "get_state"}])
        self.assertFalse(r["ok"])
        self.assertEqual(r["stopped_at"], 1)

    def test_work_done_before_the_failure_is_still_reported(self):
        """不然 agent 不知道表單已經填到哪,只能整份重來。"""
        r = self.run_batch([{"action": "get_state"}, {"action": "teleport"}, {"action": "get_state"}])
        self.assertEqual(len(r["results"]), 2)
        self.assertTrue(r["results"][0]["ok"])

    def test_every_sub_result_is_tagged_with_its_action(self):
        r = self.run_batch([{"action": "get_state"}, {"action": "teleport"}])
        self.assertEqual([x["action"] for x in r["results"]], ["get_state", "teleport"])

    def test_an_exception_becomes_a_failed_step_not_a_dead_engine(self):
        """引擎是整場申請唯一那個程序 —— 它掛了,人得從頭登入一次。"""
        r = self.run_batch([{"action": "fill", "label": "First Name", "value": "x"}])
        self.assertFalse(r["ok"])
        self.assertEqual(r["stopped_at"], 0)

    def test_empty_batch_is_not_a_failure(self):
        r = self.run_batch([])
        self.assertTrue(r["ok"])
        self.assertEqual(r["results"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
