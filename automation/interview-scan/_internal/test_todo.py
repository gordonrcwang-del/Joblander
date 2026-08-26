#!/usr/bin/env python3
"""
test_todo.py — 待辦清單讀寫的回歸測試(ticket 05)。

只驗外部行為:TODO.md 的內容真的變了、來回一趟不掉資料。每個測試在自己的臨時
檔上跑,不碰使用者真的清單。

    python3 test_todo.py
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import todo

HEADER = "# 待辦清單\n\n> 範圍只有兩件事。\n"
SAMPLE = HEADER + """
## 📤 待交付

- [ ] 寄成績單 PDF → kevin@asml.test · 2026-08-30

## 📨 待回信

- [ ] 回覆選 PQE 或 NPI → may@garmin.test · 2026-08-26
- [ ] 追二面是否改線上 → miya@amat.test · 2026-08-24

## ✅ 已完成

- [x] 待交付 · 寄英文履歷 PDF → natalie@kla.test · 2026-08-19 · 完成 2026-08-18
"""


class TodoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "TODO.md")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE)
        p = mock.patch.object(todo, "TODO_PATH", self.path)
        p.start(); self.addCleanup(p.stop)

    def text(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def items(self):
        return todo.load()[1]

    def by_action(self, needle):
        for item in self.items():
            if needle in item["action"]:
                return item
        self.fail("找不到待辦:%s" % needle)


class ParseTest(TodoCase):
    def test_reads_every_section(self):
        self.assertEqual(len(self.items()), 4)

    def test_kind_comes_from_the_section(self):
        self.assertEqual(self.by_action("成績單")["kind"], "deliver")
        self.assertEqual(self.by_action("PQE")["kind"], "reply")

    def test_completed_rows_keep_their_original_kind(self):
        """已完成區的行帶前綴,不然還原時不知道該放回哪一區。"""
        item = self.by_action("英文履歷")
        self.assertTrue(item["done"])
        self.assertEqual(item["kind"], "deliver")

    def test_contact_and_due_are_separate_fields(self):
        item = self.by_action("成績單")
        self.assertEqual(item["contact"], "kevin@asml.test")
        self.assertEqual(item["due"], "2026-08-30")

    def test_header_is_preserved_verbatim(self):
        todo.save(*todo.load())
        self.assertIn("> 範圍只有兩件事。", self.text())


class DoneTest(TodoCase):
    def test_done_moves_the_row_into_the_completed_section(self):
        item = self.by_action("PQE")
        todo._set_done(item["id"], True)
        after = self.by_action("PQE")
        self.assertTrue(after["done"])
        self.assertEqual(after["completed"], todo.TODAY)
        self.assertIn("- [x] 待回信 · 回覆選 PQE 或 NPI", self.text())

    def test_id_survives_the_move_so_undo_can_find_it(self):
        before = self.by_action("PQE")["id"]
        todo._set_done(before, True)
        self.assertEqual(self.by_action("PQE")["id"], before)

    def test_round_trip_keeps_the_due_date(self):
        """回歸:完成時只寫「完成 <日期>」的話,還原就會把期限吃掉。"""
        item = self.by_action("PQE")
        todo._set_done(item["id"], True)
        todo._set_done(item["id"], False)
        after = self.by_action("PQE")
        self.assertFalse(after["done"])
        self.assertEqual(after["due"], "2026-08-26")
        self.assertEqual(after["kind"], "reply")

    def test_unknown_id_exits_without_touching_the_file(self):
        before = self.text()
        with self.assertRaises(SystemExit):
            todo._set_done("deadbeef", True)
        self.assertEqual(self.text(), before)

    def test_completed_section_is_capped(self):
        header, items = todo.load()
        for i in range(30):
            items.append({"id": "x%02d" % i, "kind": "reply", "action": "舊事項 %02d" % i,
                          "contact": "a@b.test", "due": "", "completed": "2026-07-%02d" % (i + 1),
                          "done": True})
        todo.save(header, items)
        done = [i for i in todo.load()[1] if i["done"]]
        self.assertEqual(len(done), todo.DONE_LIMIT)
        # 留下的是最新的那些
        self.assertIn("舊事項 29", [i["action"] for i in done])

    def test_open_items_sort_by_deadline_within_their_section(self):
        """排序是每個區塊各自排的 —— 待交付整段永遠在待回信前面,那是版面,
        不是優先順序。"""
        todo.save(*todo.load())
        section, lines = None, []
        for line in self.text().splitlines():
            if line.startswith("## "):
                section = line
            elif line.startswith("- [ ]") and section and "待回信" in section:
                lines.append(line)
        self.assertIn("追二面", lines[0])       # 2026-08-24,比 08-26 早




class CreatedDateTest(TodoCase):
    """ticket 01 —— 建立日期是 02「掃寄件備份自動勾掉」的前提。"""

    def test_legacy_rows_get_no_fake_created_date(self):
        """舊的行沒有建立日期就是沒有。猜一個等於把誤勾的風險藏起來。"""
        self.assertEqual(self.by_action("PQE")["created"], "")

    def test_legacy_rows_still_parse_everything_else(self):
        item = self.by_action("PQE")
        self.assertEqual(item["contact"], "may@garmin.test")
        self.assertEqual(item["due"], "2026-08-26")

    def test_add_stamps_today(self):
        item, fresh = todo.add_item("reply", "回覆面試時間", "hr@test.test", "2026-09-01")
        self.assertTrue(fresh)
        self.assertEqual(item["created"], todo.TODAY)
        self.assertIn("建立 " + todo.TODAY, self.text())

    def test_add_without_a_due_date(self):
        todo.add_item("reply", "回覆薪資期望", "hr@test.test")
        item = self.by_action("薪資期望")
        self.assertEqual(item["due"], "")
        self.assertEqual(item["created"], todo.TODAY)

    def test_created_and_due_survive_a_round_trip(self):
        """跟期限同一個坑:來回一趟掉了建立日期,02 就再也判斷不出新舊。
        還原會把建立日期重新蓋成今天,理由見 UndoRestampTest。"""
        item, _ = todo.add_item("reply", "回覆選 PQE 或 NPI 續問", "may@garmin.test", "2026-08-30")
        todo._set_done(item["id"], True)
        self.assertEqual(self.by_action("續問")["created"], todo.TODAY)
        todo._set_done(item["id"], False)
        back = self.by_action("續問")
        self.assertEqual(back["created"], todo.TODAY)
        self.assertEqual(back["due"], "2026-08-30")
        self.assertFalse(back["done"])

    def test_add_is_idempotent(self):
        todo.add_item("reply", "回覆面試時間", "hr@test.test", "2026-09-01")
        before = len(self.items())
        item, fresh = todo.add_item("reply", "回覆面試時間", "hr@test.test", "2026-09-01")
        self.assertFalse(fresh)
        self.assertEqual(len(self.items()), before)
        self.assertEqual(item["created"], todo.TODAY)

    def test_add_does_not_resurrect_a_completed_item(self):
        """ticket 03 的復活坑:勾掉之後再送同樣內容進來,不能又長出一筆未完成的。"""
        item, _ = todo.add_item("reply", "回覆面試時間", "hr@test.test", "2026-09-01")
        todo._set_done(item["id"], True)
        again, fresh = todo.add_item("reply", "回覆面試時間", "hr@test.test", "2026-09-01")
        self.assertFalse(fresh)
        self.assertTrue(again["done"])
        self.assertEqual(len([i for i in self.items() if "面試時間" in i["action"]]), 1)

    def test_add_refuses_the_completed_section(self):
        with self.assertRaises(SystemExit):
            todo.add_item("done", "不該進得去", "x@test.test")

    def test_add_refuses_an_empty_action(self):
        with self.assertRaises(SystemExit):
            todo.add_item("reply", "   ", "x@test.test")

    def test_add_leaves_hand_written_todos_alone(self):
        """ticket 03:掃描只會 append。使用者手抄的那幾筆不能被動到。"""
        snap = lambda: {(i["id"], i["action"], i["due"], i["done"]) for i in self.items()}
        before = snap()
        todo.add_item("deliver", "完成線上測驗", "hr@test.test", "2026-09-05")
        after = snap()
        # 用集合比:render() 會依期限重排,順序本來就會變,那不是「被動到」。
        self.assertTrue(before <= after)
        self.assertEqual(len(after), len(before) + 1)

    def test_json_exposes_created(self):
        todo.add_item("reply", "回覆面試時間", "hr@test.test", "2026-09-01")
        _, items = todo.load()
        self.assertIn("created", items[0])

class UndoRestampTest(TodoCase):
    def test_undo_restamps_created_to_today(self):
        """還原 = 「到今天為止仍然沒做完」。不重新蓋章的話,當初勾掉它的那封舊信
        下一次掃描還在,會再勾一次(ticket 02 的還原坑)。"""
        item, _ = todo.add_item("reply", "回覆時程", "a@test.test", created="2026-08-01")
        todo._set_done(item["id"], True)
        self.assertEqual(self.by_action("回覆時程")["created"], "2026-08-01")
        todo._set_done(item["id"], False)
        self.assertEqual(self.by_action("回覆時程")["created"], todo.TODAY)

    def test_done_can_record_an_earlier_completion_date(self):
        """事後補勾要能寫真正的那一天,不然「已完成」那段會說謊。"""
        item, _ = todo.add_item("reply", "回覆時程", "a@test.test", created="2026-08-01")
        todo._set_done(item["id"], True, on="2026-08-05")
        self.assertEqual(self.by_action("回覆時程")["completed"], "2026-08-05")

    def test_done_alone_does_not_touch_created(self):
        item, _ = todo.add_item("reply", "回覆時程", "a@test.test", created="2026-08-01")
        todo._set_done(item["id"], True)
        self.assertEqual(self.by_action("回覆時程")["created"], "2026-08-01")


class AutocloseTest(TodoCase):
    """ticket 02 —— 回過的信自動勾掉。寧可漏勾不可誤勾。"""

    def add(self, kind="reply", action="回覆時程", contact="may@garmin.test",
            created="2026-08-10"):
        item, _ = todo.add_item(kind, action, contact, created=created)
        return item

    def test_closes_when_a_reply_went_out_after_the_todo_appeared(self):
        self.add()
        closed = todo.autoclose([{"to": "may@garmin.test", "date": "2026-08-12"}])
        self.assertEqual([c["action"] for c in closed], ["回覆時程"])
        self.assertTrue(self.by_action("回覆時程")["done"])

    def test_completed_date_is_the_reply_date_not_today(self):
        """勾掉的日期要是真的回信那天,不然「已完成」那一段會說謊。"""
        self.add()
        todo.autoclose([{"to": "may@garmin.test", "date": "2026-08-12"}])
        self.assertEqual(self.by_action("回覆時程")["completed"], "2026-08-12")

    def test_same_day_reply_does_not_count_yet(self):
        """日期只到「日」,同一天分不出先後 —— 分不出來就算不成立。
        沒有這條,「勾錯 → 還原」當天就會被同一封信再勾一次(見下面兩條)。"""
        self.add(created="2026-08-10")
        self.assertEqual(todo.autoclose(
            [{"to": "may@garmin.test", "date": "2026-08-10"}]), [])

    def test_mail_sent_before_the_todo_existed_does_not_close_it(self):
        """核心的誤勾情境:上個月寄給同一個人的信,不能把今天才出現的待辦勾掉。"""
        self.add(created="2026-08-10")
        self.assertEqual(todo.autoclose(
            [{"to": "may@garmin.test", "date": "2026-07-30"}]), [])
        self.assertFalse(self.by_action("回覆時程")["done"])

    def test_deliverables_are_never_touched(self):
        """寄出不等於交付完成,所以待交付不歸自動勾掉管。"""
        self.add(kind="deliver", action="寄成績單")
        self.assertEqual(todo.autoclose(
            [{"to": "may@garmin.test", "date": "2026-08-12"}]), [])
        self.assertFalse(self.by_action("寄成績單")["done"])

    def test_non_email_contacts_are_skipped(self):
        self.add(contact="Garmin HR")
        self.assertEqual(todo.autoclose(
            [{"to": "may@garmin.test", "date": "2026-08-12"}]), [])

    def test_rows_without_a_created_date_are_skipped(self):
        """使用者手抄的舊行沒有建立日期 —— 不知道就是不知道,不猜。"""
        item = self.by_action("PQE")
        self.assertEqual(item["created"], "")
        self.assertEqual(todo.autoclose(
            [{"to": "may@garmin.test", "date": "2026-08-12"}]), [])
        self.assertFalse(self.by_action("PQE")["done"])

    def test_address_match_is_case_insensitive(self):
        self.add(contact="May@Garmin.Test")
        self.assertEqual(len(todo.autoclose(
            [{"to": "may@garmin.test", "date": "2026-08-12"}])), 1)

    def test_several_recipients_in_one_field(self):
        self.add()
        self.assertEqual(len(todo.autoclose(
            [{"to": "hr@x.test, may@garmin.test", "date": "2026-08-12"}])), 1)

    def test_nothing_to_close_leaves_the_file_alone(self):
        before = self.text()
        self.assertEqual(todo.autoclose([]), [])
        self.assertEqual(self.text(), before)

    def test_undone_item_is_not_reclosed_by_the_same_old_mail(self):
        """勾錯 → 還原 → 下一次掃描看到同一封信,不能又勾掉。"""
        self.add()
        sent = [{"to": "may@garmin.test", "date": "2026-08-12"}]
        item = todo.autoclose(sent)[0]
        todo._set_done(item["id"], False)
        self.assertEqual(todo.autoclose(sent), [])
        self.assertFalse(self.by_action("回覆時程")["done"])

    def test_undo_survives_mail_sent_the_very_same_day(self):
        """實測抓到的洞:還原把建立日期蓋成今天,而那封信也是今天寄的。
        用「當天不算」擋掉 —— 不然使用者按了還原,下一次掃描立刻又勾回去。"""
        self.add()
        sent = [{"to": "may@garmin.test", "date": todo.TODAY}]
        item = todo.autoclose(sent)[0]
        todo._set_done(item["id"], False)
        self.assertEqual(todo.autoclose(sent), [])
        self.assertFalse(self.by_action("回覆時程")["done"])

    def test_a_fresh_reply_after_the_undo_still_closes_it(self):
        """還原之後真的又回了一次信,還是要勾得掉 —— 不能因為還原就永久免疫。"""
        self.add()
        item = todo.autoclose([{"to": "may@garmin.test", "date": "2026-08-12"}])[0]
        todo._set_done(item["id"], False)
        self.assertEqual(len(todo.autoclose(
            [{"to": "may@garmin.test", "date": "2026-12-01"}])), 1)

    def test_entries_without_a_date_are_ignored(self):
        self.add()
        self.assertEqual(todo.autoclose([{"to": "may@garmin.test"}]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
