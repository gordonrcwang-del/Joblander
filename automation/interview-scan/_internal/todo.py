#!/usr/bin/env python3
"""
todo.py — 待辦清單(interview-prep/general/TODO.md)的唯一寫入者(ticket 05)。

WHY THIS EXISTS
Dashboard 要能勾掉待辦,但它不直接寫任何 .md —— 比照職缺走 scan_jobs.py mark 的
做法,這裡是待辦的那一支。人照樣可以直接手改那份檔案:格式就是一行一筆的
markdown 核取清單,不是給機器看的序列化格式。

格式(一行一筆,敘述只描述動作與需要的東西,不寫解釋):

    ## 📤 待交付
    - [ ] 寄英文自介簡報(5–10 分鐘) → Natalie.Chou@kla.com · 2026-08-27 · 建立 2026-08-26

    ## ✅ 已完成
    - [x] 待交付 · 寄成績單 PDF → kevin.hsu-kblm@asml.com · 建立 2026-08-20 · 完成 2026-08-25

ID 是 (區塊, 動作, 對象) 的雜湊,不寫進檔案 —— 檔案裡多一個 id 欄位就多一個
人手改時會弄壞的東西。改了文字就是換一筆,那正是我們要的語意。

建立日期(ticket 01)是給「掃寄件備份自動勾掉待辦」用的:要判斷一封回信是不是在
這筆待辦成立**之後**才寄的,否則上個月寄給同一個人的舊信會把今天才出現的待辦
誤勾掉。它是中繼資料不是事項敘述 —— 人眼讀得懂,但 dashboard 的「事項」欄不顯示。
舊的行沒有這個欄位,照樣解析,而且**不補假日期** —— 不知道就是不知道,猜一個等於
把誤勾的風險藏起來。

準確地說,建立日期是「這筆待辦從哪天起算還沒完成」。所以 undo 會把它重新蓋成今天:
還原的意思就是「到現在為止這件事仍然沒做完」,舊的那封回信已經被判定不算數,不該
在下一次掃描又把它勾掉一次(ticket 02)。

USAGE
    python3 todo.py list [--json]
    python3 todo.py done <id> [--on YYYY-MM-DD]
    python3 todo.py undo <id>
    python3 todo.py add --kind deliver|reply --action <文字> [--contact <email>] [--due YYYY-MM-DD]
    python3 todo.py autoclose --sent '[{"to": "a@b.com", "date": "2026-08-27"}, ...]'
"""
import hashlib
import json
import os
import re
import sys
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..", ".."))
TODO_PATH = os.path.join(REPO_ROOT, "interview-prep", "general", "TODO.md")

TODAY = date.today().isoformat()

# 三個區塊。deliver/reply 是待辦,done 是已完成。
SECTIONS = [
    ("deliver", "📤 待交付", "待交付"),
    ("reply", "📨 待回信", "待回信"),
    ("done", "✅ 已完成", "已完成"),
]
KIND_BY_KEYWORD = {kw: key for key, _, kw in SECTIONS}

# 已完成區只留最近 20 筆 —— 這份檔案是人在讀的,無限長的完成史等於沒有。
DONE_LIMIT = 20

_ITEM_RE = re.compile(r"^\s*-\s*\[( |x|X)\]\s*(?P<body>.+?)\s*$")
_ARROW = "→"
_SEP = "·"
_CREATED = "建立"
_COMPLETED = "完成"


def _id(action, contact):
    """id 刻意不含區塊 —— 一筆待辦被勾掉之後會從「待回信」搬到「已完成」,
    id 若跟著變,dashboard 就沒辦法還原它。改文字才算換一筆,那正是要的語意。"""
    return hashlib.sha1(("%s|%s" % (action, contact)).encode("utf-8")).hexdigest()[:8]


def parse(text):
    """回傳 (header_lines, items)。header 是第一個 '## ' 之前的所有內容。"""
    header, items, section = [], [], None
    seen_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            seen_section = True
            section = None
            for keyword, mapped in KIND_BY_KEYWORD.items():
                if keyword in line:
                    section = mapped
                    break
            continue
        if not seen_section:
            header.append(line)
            continue
        m = _ITEM_RE.match(line)
        if not m or section is None:
            continue
        body = m.group("body")
        done = section == "done" or m.group(1).lower() == "x"

        # 已完成區的行帶著它原本的區塊當前綴(「待交付 · 寄…」),否則還原時
        # 不知道該放回哪一區。未完成的行不需要 —— 標題已經說了。
        kind = section
        if done:
            kind = "reply"
            head, sep, rest = body.partition(_SEP)
            tag = head.strip()
            if sep and tag in KIND_BY_KEYWORD:
                kind = KIND_BY_KEYWORD[tag]
                body = rest.strip()

        action, _, rest = body.partition(_ARROW)
        contact, due, completed, created = "", "", "", ""
        for part in [p.strip() for p in rest.split(_SEP)]:
            if not part:
                continue
            if part.startswith(_COMPLETED):
                completed = part[len(_COMPLETED):].strip()
            elif part.startswith(_CREATED):
                created = part[len(_CREATED):].strip()
            elif "@" in part and not contact:
                contact = part
            elif not due:
                due = part
        action = action.strip()
        items.append({
            "id": _id(action, contact),
            "kind": kind,
            "action": action,
            "contact": contact,
            "due": due,
            "created": created,
            "completed": completed,
            "done": done,
        })
    return header, items


def _fmt(item):
    label = dict((k, kw) for k, _, kw in SECTIONS)[item["kind"]]
    line = "- [%s] " % ("x" if item["done"] else " ")
    if item["done"]:
        line += "%s %s " % (label, _SEP)
    line += item["action"]
    if item["contact"]:
        line += " %s %s" % (_ARROW, item["contact"])
    # 期限在完成後仍然留著 —— 少了它,還原一筆待辦就會把它的期限吃掉。
    if item["due"]:
        line += " %s %s" % (_SEP, item["due"])
    # 建立日期同樣要撐過「勾掉 → 還原」的來回,理由跟期限一樣:掉了就補不回來。
    if item.get("created"):
        line += " %s %s %s" % (_SEP, _CREATED, item["created"])
    if item["done"]:
        line += " %s %s %s" % (_SEP, _COMPLETED, item["completed"] or TODAY)
    return line


def render(header, items):
    open_items = [i for i in items if not i["done"]]
    done_items = [i for i in items if i["done"]]
    # 未完成依期限由近到遠,沒填期限的排最後;已完成依完成日新到舊。
    open_items.sort(key=lambda i: (i["due"] == "", i["due"]))
    done_items.sort(key=lambda i: i["completed"] or "", reverse=True)
    done_items = done_items[:DONE_LIMIT]

    lines = list(header)
    while lines and not lines[-1].strip():
        lines.pop()
    for key, heading, _ in SECTIONS:
        rows = done_items if key == "done" else [i for i in open_items if i["kind"] == key]
        lines += ["", "## %s" % heading, ""]
        lines += [_fmt(i) for i in rows] or ["（目前沒有）"]
    return "\n".join(lines).strip() + "\n"


def load():
    if not os.path.exists(TODO_PATH):
        return [], []
    with open(TODO_PATH, encoding="utf-8") as fh:
        return parse(fh.read())


def save(header, items):
    tmp = TODO_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(render(header, items))
    os.replace(tmp, TODO_PATH)


def _set_done(item_id, done, on=None):
    """on 是完成日期。事後才想起來勾的時候要能寫真正的那一天 —— 記成今天等於
    讓「已完成」那一段說謊,而那一段的用處就是回頭查什麼時候做的。"""
    header, items = load()
    for item in items:
        if item["id"] == item_id:
            item["done"] = done
            item["completed"] = (on or TODAY) if done else ""
            if not done:
                # 還原 = 「到今天為止這件事仍然沒做完」。不重新蓋章的話,當初把它
                # 勾掉的那封舊信下一次掃描還在,會再勾一次(ticket 02)。
                item["created"] = TODAY
            save(header, items)
            return item
    sys.exit("no such todo id: %s" % item_id)


OPEN_KINDS = ("deliver", "reply")


def add_item(kind, action, contact="", due="", created=None):
    """新增一筆待辦,回傳 (item, created_now)。

    重複新增同一筆不會產生第二行 —— 這支之後會被無人值守的掃描一天呼叫好幾次
    (ticket 03),不冪等的話清單很快就變成同一件事的十份副本。

    比對範圍**包含已完成的行**:一筆被自動勾掉的待辦,下一次掃描再送同樣的
    內容進來時必須認得它、不能當成新的又建一次(ticket 03 的「復活」坑)。
    """
    if kind not in OPEN_KINDS:
        sys.exit("kind 只能是 %s" % " 或 ".join(OPEN_KINDS))
    action = (action or "").strip()
    if not action:
        sys.exit("action 不能是空的")
    contact = (contact or "").strip()
    header, items = load()
    wanted = _id(action, contact)
    for item in items:
        if item["id"] == wanted:
            return item, False
    item = {
        "id": wanted,
        "kind": kind,
        "action": action,
        "contact": contact,
        "due": (due or "").strip(),
        "created": TODAY if created is None else created,
        "completed": "",
        "done": False,
    }
    items.append(item)
    save(header, items)
    return item, True


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _emails(text):
    return set(m.group(0).lower() for m in _EMAIL_RE.finditer(text or ""))


def autoclose(sent):
    """把「已經回過信」的待回信待辦標為完成,回傳被勾掉的項目清單。

    sent 是 [{"to": ..., "date": "YYYY-MM-DD"}, ...] —— 掃描從寄件備份撈出來的
    收件人與寄出日期。比對只做一件事:這個對象的信箱,在這筆待辦成立**之後**
    收到過我寄的信。

    「之後」是嚴格大於,不含當天。日期只到「日」這個精度,所以同一天的信分不出
    先後 —— 而分不出來的時候必須算它不成立,否則「勾錯 → 還原」這條路會漏:還原
    會把建立日期蓋成今天,今天寄的那封信要是算數,下一次掃描就又把它勾掉一次,
    使用者根本按不掉。代價是當天建立、當天回覆的待辦要等隔天的掃描才勾得掉,
    而寄件備份的搜尋窗口有七天,漏不掉。寧可晚一天,不可勾錯。

    保守的地方全在這裡,而且都是刻意的:
    - 只碰「待回信」。「待交付」是寄東西給對方,寄出不代表交付完成。
    - 對象不是 email 位址就跳過。用公司名或人名去猜比對,誤勾的代價是一筆真的
      待辦無聲消失,而使用者不會知道自己漏回了誰。
    - 沒有建立日期的舊待辦一律跳過。猜「很久以前」會被三個月前的舊信勾掉,猜
      「剛剛」則永遠勾不掉 —— 兩種猜法都比不做更糟。
    """
    header, items = load()
    by_email = {}
    for entry in sent or []:
        day = (entry.get("date") or "").strip()[:10]
        if not day:
            continue
        for addr in _emails(entry.get("to")):
            if day > by_email.get(addr, ""):
                by_email[addr] = day
    closed = []
    for item in items:
        if item["done"] or item["kind"] != "reply":
            continue
        if not item.get("created"):
            continue
        addr = item["contact"].strip().lower()
        if not addr or not _EMAIL_RE.fullmatch(addr):
            continue
        replied = by_email.get(addr)
        if not replied or replied <= item["created"]:
            continue
        item["done"] = True
        item["completed"] = replied
        closed.append(dict(item, replied=replied))
    if closed:
        save(header, items)
    return closed


def _flags(argv):
    """--k v 拆成 dict。刻意不用 argparse —— 這個檔案其他地方都是手寫 argv。"""
    out, i = {}, 0
    while i < len(argv):
        token = argv[i]
        if not token.startswith("--"):
            sys.exit("看不懂的參數:%s" % token)
        if i + 1 >= len(argv):
            sys.exit("%s 後面少了值" % token)
        out[token[2:]] = argv[i + 1]
        i += 2
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list":
        _, items = load()
        if "--json" in sys.argv:
            print(json.dumps({"items": items, "today": TODAY,
                              "done_limit": DONE_LIMIT}, ensure_ascii=False))
        else:
            for i in items:
                print("%s  [%s] %s%s" % (i["id"], "x" if i["done"] else " ",
                                          i["action"],
                                          (" · " + (i["due"] or i["completed"])) if (i["due"] or i["completed"]) else ""))
    elif cmd == "add":
        f = _flags(sys.argv[2:])
        unknown = set(f) - {"kind", "action", "contact", "due"}
        if unknown:
            sys.exit("看不懂的參數:%s" % ", ".join(sorted(unknown)))
        if "kind" not in f or "action" not in f:
            sys.exit("usage: todo.py add --kind deliver|reply --action <文字> "
                     "[--contact <email>] [--due YYYY-MM-DD]")
        item, fresh = add_item(f["kind"], f["action"], f.get("contact", ""), f.get("due", ""))
        print("%s:%s  %s" % ("已新增" if fresh else "已存在,沒有重複新增",
                             item["action"], item["id"]))
    elif cmd == "autoclose":
        f = _flags(sys.argv[2:])
        unknown = set(f) - {"sent"}
        if unknown:
            sys.exit("看不懂的參數:%s" % ", ".join(sorted(unknown)))
        if "sent" not in f:
            sys.exit("usage: todo.py autoclose --sent '[{\"to\": ..., \"date\": ...}]'")
        try:
            sent = json.loads(f["sent"])
        except ValueError as exc:
            sys.exit("--sent 不是合法的 JSON:%s" % exc)
        if not isinstance(sent, list):
            sys.exit("--sent 要是一個陣列")
        closed = autoclose(sent)
        for item in closed:
            print("已勾掉 %s %s → %s(回信 %s)" % (item["id"], item["action"],
                                                  item["contact"], item["replied"]))
        print("autoclose: %d closed" % len(closed))
    elif cmd in ("done", "undo"):
        if len(sys.argv) < 3:
            sys.exit("usage: todo.py %s <id>" % cmd)
        on = None
        if "--on" in sys.argv:
            i = sys.argv.index("--on")
            if i + 1 >= len(sys.argv):
                sys.exit("--on 後面少了日期")
            on = sys.argv[i + 1]
        item = _set_done(sys.argv[2], cmd == "done", on)
        print("已%s:%s" % ("標為完成" if cmd == "done" else "還原", item["action"]))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
