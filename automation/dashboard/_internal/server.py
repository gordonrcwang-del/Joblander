#!/usr/bin/env python3
"""
server.py — local dashboard for automation/, ticket 02 (server + token + jobs table).

WHY THIS EXISTS
求職狀態散在三個 markdown 檔裡,要看全貌得開三個編輯器分頁。這支 server 綁
127.0.0.1,把同一批資料端到一個網頁上。它是**第二個讀者,不是第二個寫入者** —
這張票只讀不寫,之後的寫入一律走 scan_jobs.py 既有的 CLI。

沒有第三方套件:http.server + json + re,沿用 job-search 掃描器「只用標準函式庫」
的慣例。

AUTH
啟動時產生一組隨機 token,寫進 repo 外的檔案(0600),並在終端印出帶 token 的
網址。頁面從網址的 ?t= 取得後存進 sessionStorage 並把網址洗乾淨。

HTML 本身不需要 token,因為它不含 token —— 本機其他程式抓到首頁也只是一份空殼。
所有 /api/* 都要 token,包含純讀取的,以免其他本機程式列舉狀態。

USAGE
    python3 server.py            # 前景啟動,終端印出帶 token 的網址
    python3 server.py --port N   # 覆寫埠號(預設 config.json 的 dashboard_port,再預設 8765)
"""
import errno
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# automation/_internal/runlock.py —— 所有 ledger 寫入者共用的那把鎖。
# 這裡是 automation/dashboard/_internal/,往上兩層就是 automation/。
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "_internal"))
from runlock import is_busy as lock_holder, LockBusy  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..", ".."))
INDEX_PATH = os.path.join(BASE_DIR, "index.html")
JOB_SEARCH_DIR = os.path.join(REPO_ROOT, "automation", "job-search")
TODAY_JOBS_PATH = os.path.join(JOB_SEARCH_DIR, "today-jobs.md")
APPLIED_JOBS_PATH = os.path.join(JOB_SEARCH_DIR, "applied-jobs.md")
SCAN_JOBS_PY = os.path.join(JOB_SEARCH_DIR, "_internal", "scan_jobs.py")
INTERVIEWS_PATH = os.path.join(REPO_ROOT, "interview-prep", "general", "面試行程.md")
INTERVIEW_SCAN_DIR = os.path.join(REPO_ROOT, "automation", "interview-scan", "_internal")
INTERVIEW_SCAN_PY = os.path.join(INTERVIEW_SCAN_DIR, "run_scan.py")
TODO_PY = os.path.join(INTERVIEW_SCAN_DIR, "todo.py")
APPLY_BATCH_PY = os.path.join(REPO_ROOT, "automation", "job-apply", "_internal", "apply_batch.py")

# 執行期狀態一律落在 repo 外 —— 這個 repo 是公開的,token 不能有任何進版控的機會。
RUNTIME_DIR = os.path.join(os.path.expanduser("~"), ".joblander")
TOKEN_PATH = os.path.join(RUNTIME_DIR, "dashboard-token")

# 申請流程的執行期狀態。批次執行器(ticket 08)寫這一份;申請 agent 在「需要登入」
# 和「等你同意」那兩個時刻寫工作目錄裡的 agent-status.json。兩者都在 repo 外。
APPLY_STATE_PATH = os.path.join(RUNTIME_DIR, "apply-state.json")
AGENT_STATUS_NAME = "agent-status.json"

DEFAULT_PORT = 8765


def load_config():
    """config.json 是選用的 —— dashboard 只從裡面拿埠號,沒有也跑得起來。"""
    path = os.path.join(REPO_ROOT, "config.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def issue_token():
    """每次啟動換一組新 token —— 舊分頁失效是刻意的,不是缺陷。"""
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    token = secrets.token_urlsafe(32)
    # 先開 0600 再寫,避免內容有任何一瞬間是其他使用者讀得到的。
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    return token


# ---------------------------------------------------------------------------
# today-jobs.md 解析
#
# 這份 md 是 scan_jobs.py render() 的輸出。解析它(而不是直接讀 ledger.json)是
# 這張票刻意的選擇:dashboard 是那三個 md 的第二個讀者,使用者手改過的內容也照樣
# 看得到。代價是格式耦合 —— 下面三個 header 關鍵字和欄位順序跟 render() 綁在一起。
# ---------------------------------------------------------------------------

# GFM 表格裡的 '|' 會被跳脫成 '\|'(真實職稱含有它),所以不能直接 split('|')。
_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
_LINK_RE = re.compile(r"^\[(?P<text>.*)\]\((?P<url>[^)]*)\)$")
_KEY_RE = re.compile(r"^`(?P<key>[^`]+)`$")

# render() 的三個區塊標題 → dashboard 的分類。用關鍵字比對而不是全字串比對,
# 因為標題含 emoji 和筆數,那些會變。
_SECTIONS = [
    ("待投遞", "selected"),
    ("新職缺", "new"),
    ("之前看過", "seen"),
]


def _cells(line):
    parts = _CELL_SPLIT_RE.split(line.strip())
    # 首尾的 '|' 會各切出一個空字串,去掉;中間的空白 cell 要保留。
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [p.strip().replace("\\|", "|") for p in parts]


def _section_of(heading):
    for keyword, bucket in _SECTIONS:
        if keyword in heading:
            return bucket
    return None


def parse_today_jobs(text):
    """把 today-jobs.md 轉成 dashboard 表格要的欄位。

    只解析三個已知區塊底下的表格 —— 檔案尾端還有一張「掃描狀態」表,欄位完全
    不同,靠 bucket is None 擋掉。
    """
    jobs = []
    bucket = None
    for line in text.splitlines():
        if line.startswith("## "):
            bucket = _section_of(line)
            continue
        if bucket is None or not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) != 6:
            continue
        check, key_cell, title_cell, company, location, posted = cells
        m = _KEY_RE.match(key_cell)
        if not m:                       # 表頭與分隔線都在這裡被擋掉
            continue
        link = _LINK_RE.match(title_cell)
        jobs.append({
            "key": m.group("key"),
            "title": (link.group("text") if link else title_cell).replace(" 🎓", ""),
            "url": link.group("url") if link else "",
            "company": company,
            "location": location,
            "posted": posted,
            "bucket": bucket,
            "new_grad": "🎓" in title_cell,
            # 「待投遞」在 md 上是一個獨立區塊,在畫面上是「該列預設已勾選」。
            "selected": bucket == "selected",
            "checked_in_file": check.lower() in ("[x]", "[-]"),
        })
    return jobs


def read_jobs():
    if not os.path.exists(TODAY_JOBS_PATH):
        return {"jobs": [], "source_missing": True}
    with open(TODAY_JOBS_PATH, encoding="utf-8") as fh:
        text = fh.read()
    generated = ""
    first = text.splitlines()[0] if text else ""
    if first.startswith("# 今日職缺"):
        generated = first.replace("# 今日職缺 —", "").strip()
    return {"jobs": parse_today_jobs(text), "generated": generated, "source_missing": False}


# ---------------------------------------------------------------------------
# 寫入 seam
#
# Dashboard 不寫 ledger.json,也不寫任何 .md —— 所有狀態變更都轉呼叫 scan_jobs.py
# 既有的子指令。這維持了「一個寫入者」:排程和 dashboard 同時在動,走的是同一段
# 程式碼。mark 本身寫完就重畫 today-jobs.md,所以這裡不需要再要求 render。
# ---------------------------------------------------------------------------

class CliError(RuntimeError):
    pass


def run_cli_path(argv, timeout=60):
    """跑一個 python 腳本,回傳 stdout。非零離開碼一律當錯誤往上丟。"""
    proc = subprocess.run(
        [sys.executable] + list(argv),
        capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT)
    if proc.returncode != 0:
        raise CliError((proc.stderr or proc.stdout or "").strip()
                       or "%s 失敗(exit %d)" % (os.path.basename(argv[0]), proc.returncode))
    return proc.stdout


def run_cli(args, timeout=60):
    """呼叫 scan_jobs.py 的子指令。"""
    return run_cli_path([SCAN_JOBS_PY] + list(args), timeout=timeout)


# ---------------------------------------------------------------------------
# 面試行程.md / applied-jobs.md 解析(唯讀)
# ---------------------------------------------------------------------------

# 面試行程.md 現在只有一個區塊,取消與改期都收斂成表格裡的「狀態」欄。
_INTERVIEW_SECTION = "面試排程"
# 狀態欄 → 畫面顏色類別。改期中是黃的(要盯),待進行是綠的(還要面),
# 已完成與已取消都是灰的 —— 兩者都過去了,文字自己會說是哪一種。
INTERVIEW_STATUS_VIEW = {
    "待進行": "interview",
    "改期中": "pending",
    "已完成": "closed",
    "已取消": "closed",
}
_CANCELLED_STATUS = "已取消"
# 「公司 — 職位」用的是 em dash,不是 hyphen。職稱本身常含 hyphen(CS - EUV …),
# 所以只能切 em dash,而且只切第一個。
_COMPANY_SPLIT = "—"


def parse_interviews(text):
    rows, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = _INTERVIEW_SECTION in line
            continue
        if not inside or not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) != 6:
            continue
        day, time_, who, form, status, contact = cells
        if day in ("日期", "---") or set(day) <= {"-"}:
            continue
        company, _, position = who.partition(_COMPANY_SPLIT)
        link = _LINK_RE.match(form)
        rows.append({
            "date": _plain_date(day),
            "date_label": day,
            "time": time_,
            "company": company.strip(),
            "position": position.strip() or who.strip(),
            "form": (link.group("text") if link else form).strip(),
            "url": link.group("url") if link else "",
            "contact": contact,
            "status": status,
            "status_cls": INTERVIEW_STATUS_VIEW.get(status, "closed"),
            "cancelled": status == _CANCELLED_STATUS,
        })
    return rows


_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _plain_date(cell):
    """「2026-08-27（四）」→「2026-08-27」,排序要用純日期。"""
    m = _DATE_RE.search(cell)
    return m.group(1) if m else cell


# progress 狀態 → 畫面標籤與顏色類別。
# 「已撤回」在畫面上叫「已結束」並歸灰:那是使用者自己喊停,跟被對方拒絕的語意
# 不同,後者維持獨立且是紅的。
PROGRESS_VIEW = {
    "pending":   ("待回覆", "pending"),
    "interview": ("面試中", "interview"),
    "offer":     ("Offer", "offer"),
    "rejected":  ("被拒絕", "rejected"),
    "withdrawn": ("已結束", "closed"),
}
# applied-jobs.md 存的是中文標籤而不是狀態鍵,所以要反查回來。
_LABEL_TO_STATUS = {"待回覆": "pending", "面試中": "interview", "Offer": "offer",
                    "已拒絕": "rejected", "被拒絕": "rejected", "已撤回": "withdrawn",
                    "已結束": "withdrawn"}


def parse_applied(text):
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) != 5:
            continue
        applied_date, company, title_cell, label, key_cell = cells
        m = _KEY_RE.match(key_cell)
        if not m:
            continue
        link = _LINK_RE.match(title_cell)
        status = _LABEL_TO_STATUS.get(label, "pending")
        view_label, tone = PROGRESS_VIEW[status]
        rows.append({
            "key": m.group("key"),
            "date": applied_date,
            "company": company,
            "title": link.group("text") if link else title_cell,
            "url": link.group("url") if link else "",
            "status": status,
            "label": view_label,
            "tone": tone,
        })
    return rows


def _read(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_todos():
    """待辦的讀寫都走 todo.py —— dashboard 不直接碰 TODO.md,比照職缺走 mark。"""
    try:
        return json.loads(run_cli_path([TODO_PY, "list", "--json"]))
    except (CliError, ValueError, subprocess.TimeoutExpired) as e:
        return {"items": [], "error": str(e)}


def read_interviews():
    text = _read(INTERVIEWS_PATH)
    if text is None:
        return {"interviews": [], "source_missing": True}
    rows = parse_interviews(text)
    rows.sort(key=lambda r: (r["date"], r["time"]))
    return {"interviews": rows, "source_missing": False}


# 已投遞只回最近 50 筆 —— 畫面上就是這個上限,多送的資料只是讓頁面變慢。
APPLIED_LIMIT = 50


def read_applied():
    text = _read(APPLIED_JOBS_PATH)
    if text is None:
        return {"applied": [], "total": 0, "counts": {}, "source_missing": True}
    rows = parse_applied(text)
    rows.sort(key=lambda r: r["date"], reverse=True)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"applied": rows[:APPLIED_LIMIT], "total": len(rows),
            "limit": APPLIED_LIMIT, "counts": counts, "source_missing": False}


# ---------------------------------------------------------------------------
# 狀態欄(ticket 07)
#
# 兩個來源:
#   1. 從既有的檔案佇列推導 —— 申請引擎用 command.json / result.json 一來一往驅動,
#      指令裡的 action 直接對應到「讀表單中」「填空中」。免改引擎任何東西。
#   2. 由申請 agent 主動寫的狀態檔 —— 「需要登入」和「等你同意」在佇列上沒有痕跡:
#      引擎閒置時,agent 在思考跟 agent 在等人看起來一模一樣。這兩個時刻各寫一次檔。
#
# 刻意不對每一步插樁。那些步驟是踩出來的、還在演進,多一個插樁就多一個會跟現實
# 脫節的地方。
# ---------------------------------------------------------------------------

# playwright_script.py 的 action → 使用者看得懂的粒度。
_ACTION_STATUS = {
    "goto": "讀表單中", "get_state": "讀表單中", "list_fields": "讀表單中",
    "screenshot": "讀表單中", "scroll": "讀表單中",
    "fill": "填空中", "type_into": "填空中", "select": "填空中",
    "choose_option": "填空中", "click": "填空中", "press_key": "填空中",
    "upload_file": "填空中",
}
# agent 主動回報的兩個時刻。
_AGENT_STATUS = {
    "login_required": ("需要登入", "attn"),
    "awaiting_approval": ("等你同意", "attn"),
}

IDLE = {"key": "idle", "label": "閒置", "tone": "idle", "sub": "沒有進行中的工作"}


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def work_dir_of(st):
    return (st or {}).get("dir") or ""


def _apply_status():
    st = _load_json(APPLY_STATE_PATH)
    if not st:
        return None
    where = " · ".join(x for x in (st.get("company"), st.get("job_title")) if x)
    batch = st.get("batch") or {}
    if batch.get("total"):
        where += " · 第 %s/%s 筆" % (batch.get("index", "?"), batch["total"])

    phase = st.get("phase")
    if phase == "awaiting_next":
        # spec 列的七種粒度沒有這一個。批次是「每投完一筆停下等人」的,那個停頓
        # 一定要有自己的顯示 —— 否則畫面停在「完成」,使用者不知道還有下一筆。
        return {"key": "awaiting_next", "label": "等你按下一個", "tone": "attn",
                "sub": st.get("note") or where, "dir": work_dir_of(st)}
    if phase == "done":
        return {"key": "done", "label": "完成", "tone": "ok",
                "sub": st.get("note") or where}
    if phase == "failed":
        return {"key": "fail", "label": "失敗", "tone": "bad",
                "sub": st.get("note") or where}

    work_dir = st.get("dir") or ""
    # agent 主動寫的狀態優先 —— 它知道自己在等人,佇列不知道。
    agent = _load_json(os.path.join(work_dir, AGENT_STATUS_NAME)) or {}
    hit = _AGENT_STATUS.get(agent.get("status"))
    if hit:
        label, tone = hit
        return {"key": agent["status"], "label": label, "tone": tone,
                "sub": agent.get("note") or where}

    command = _load_json(os.path.join(work_dir, "command.json")) or {}
    label = _ACTION_STATUS.get(command.get("action"))
    if label:
        return {"key": "running", "label": label, "tone": "busy", "sub": where}
    return {"key": "prep", "label": "準備中", "tone": "busy",
            "sub": where or "開瀏覽器"}


# 掃描是 dashboard 自己起的子程序,狀態就在記憶體裡,不必猜。
_scan_lock = threading.Lock()
_scan = {"running": None, "started": None, "note": ""}


def current_status():
    with _scan_lock:
        running, note = _scan["running"], _scan["note"]
    if running:
        return {"key": "scanning", "label": running, "tone": "busy",
                "sub": note or "跑到一半,結束會自己更新"}
    return _apply_status() or IDLE


def status_payload():
    st = current_status()
    holder = lock_holder()
    # version 是內容的指紋 —— 用它而不是計數器,server 重啟後客戶端不會因為
    # 計數歸零就以為狀態變了。
    blob = json.dumps([st, holder], ensure_ascii=False, sort_keys=True)
    return {"status": st, "lock": holder,
            "version": hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]}


# 長輪詢的上限。比瀏覽器/代理常見的 30 秒閒置逾時短一點,免得連線被中途剪斷。
POLL_TIMEOUT = 25.0
POLL_INTERVAL = 0.4


def wait_for_status(since, deadline):
    """狀態變了就回,否則撐到 deadline —— 「等你同意」的價值全在它出現的當下,
    所以這是全頁唯一由 server 主動推的東西,其餘資料維持手動刷新。"""
    while True:
        payload = status_payload()
        if payload["version"] != since or time.monotonic() >= deadline:
            return payload
        time.sleep(POLL_INTERVAL)


def start_scan(label, args, note):
    """在背景跑一個掃描子程序。鎖由子程序自己拿 —— dashboard 先問一次只是為了
    能立刻回一個清楚的拒絕訊息,不是為了自己持有鎖。"""
    holder = lock_holder()
    if holder:
        raise LockBusy(holder)
    with _scan_lock:
        if _scan["running"]:
            raise LockBusy(_scan["running"])
        _scan["running"], _scan["started"], _scan["note"] = label, time.time(), note

    def worker():
        try:
            out = run_cli_path(args, timeout=45 * 60)
            done = {"ok": True, "output": out[-2000:]}
        except Exception as e:                      # noqa: BLE001 — 什麼都要收,否則執行緒靜默死掉
            done = {"ok": False, "output": str(e)[-2000:]}
        with _scan_lock:
            _scan["running"] = None
            _scan["note"] = ("完成" if done["ok"] else "失敗:" + done["output"][-200:])
    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class DashboardServer(ThreadingHTTPServer):
    # 重啟時上一個 socket 常還在 TIME_WAIT,沒有這行就得等一分鐘才綁得回同一個埠。
    allow_reuse_address = True
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "joblander-dashboard/0.1"
    token = ""          # 由 serve() 在啟動時填入

    def log_message(self, fmt, *args):
        sys.stderr.write("[dashboard] %s\n" % (fmt % args))

    # -- helpers ------------------------------------------------------------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        # 這是本機單頁應用,沒有任何跨站需求 —— 明確關掉比留白安全。
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(raw)

    def _authed(self):
        """Bearer token。用 compare_digest 避免逐字元比對的時間差。"""
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return secrets.compare_digest(header[7:], self.token)

    # -- routes -------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/":
            # HTML 不含 token,所以不擋 —— 擋了就變雞生蛋:頁面得先拿到東西才問得到 token。
            if not os.path.exists(INDEX_PATH):
                return self._send(500, "index.html missing", "text/plain; charset=utf-8")
            with open(INDEX_PATH, encoding="utf-8") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")

        if not path.startswith("/api/"):
            return self._send(404, {"error": "not found"})

        # 純讀取的 endpoint 也要 token,以免其他本機程式列舉狀態。
        if not self._authed():
            return self._send(401, {"error": "missing or invalid token"})

        if path == "/api/health":
            return self._send(200, {"ok": True})
        if path == "/api/jobs":
            return self._send(200, read_jobs())
        if path == "/api/interviews":
            return self._send(200, read_interviews())
        if path == "/api/applied":
            return self._send(200, read_applied())
        if path == "/api/todos":
            return self._send(200, read_todos())
        if path == "/api/status":
            return self._send(200, status_payload())
        if path == "/api/status/wait":
            since = ""
            if "?" in self.path:
                from urllib.parse import parse_qs
                since = (parse_qs(self.path.split("?", 1)[1]).get("since") or [""])[0]
            return self._send(200, wait_for_status(since, time.monotonic() + POLL_TIMEOUT))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._authed():
            return self._send(401, {"error": "missing or invalid token"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or "{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "body must be JSON"})

        if path == "/api/jobs/mark":
            return self._mark(payload)
        if path == "/api/todos/done":
            return self._todo_done(payload)
        if path == "/api/apply/start":
            return self._apply_start(payload)
        if path in ("/api/apply/continue", "/api/apply/stop"):
            return self._apply_signal(path.rsplit("/", 1)[1])
        if path == "/api/scan/jobs":
            return self._scan("職缺掃描中", [SCAN_JOBS_PY, "discover"], "掃描各家 ATS")
        if path == "/api/scan/interviews":
            return self._scan("面試信掃描中", [INTERVIEW_SCAN_PY], "讀 Gmail、更新行事曆")
        return self._send(404, {"error": "not found"})

    def _todo_done(self, payload):
        ids = payload.get("ids") or []
        done = bool(payload.get("done", True))
        if not ids or not all(isinstance(i, str) for i in ids):
            return self._send(400, {"error": "ids 必須是非空字串陣列"})
        ok, failed = [], []
        for item_id in ids:
            try:
                run_cli_path([TODO_PY, "done" if done else "undo", item_id])
                ok.append(item_id)
            except (CliError, subprocess.TimeoutExpired) as e:
                failed.append({"id": item_id, "error": str(e)})
        return self._send(200, {"changed": ok, "failed": failed, "todos": read_todos()})

    def _apply_start(self, payload):
        company = payload.get("company")
        if not isinstance(company, str) or not company:
            return self._send(400, {"error": "company 必須是 company_id 字串"})
        st = _load_json(APPLY_STATE_PATH) or {}
        if st.get("phase") in ("prep", "running", "awaiting_next"):
            return self._send(409, {"error": "已經有一批在跑(%s)—— 先讓它結束"
                                             % (st.get("company") or "?")})
        argv = [APPLY_BATCH_PY, "--company", company]

        def worker():
            try:
                run_cli_path(argv, timeout=8 * 60 * 60)
            except Exception:                      # noqa: BLE001
                # 失敗的細節由 apply_batch.py 自己寫進 apply-state.json;
                # 這裡再寫一次只會蓋掉更精確的訊息。
                pass
        threading.Thread(target=worker, daemon=True).start()
        # 子程序要一兩秒才寫出第一份狀態,先讓前端知道我們動了。
        return self._send(202, {"started": company})

    def _apply_signal(self, name):
        """在批次的工作目錄放一個檔案 —— 執行器等的是檔案,不是 stdin:它是被
        dashboard 在背景叫起來的,沒有終端可以讀。"""
        st = _load_json(APPLY_STATE_PATH) or {}
        work_dir = work_dir_of(st)
        if not work_dir or not os.path.isdir(work_dir):
            return self._send(409, {"error": "現在沒有進行中的批次"})
        with open(os.path.join(work_dir, name), "w", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        return self._send(202, status_payload())

    def _scan(self, label, argv, note):
        try:
            start_scan(label, argv, note)
        except LockBusy as e:
            # 409 而不是 200 + 錯誤欄位:這不是「請求有問題」,是「現在不行」。
            # 明確拒絕、不排隊 —— 排隊的話使用者看不到自己在隊伍裡,跟當機沒兩樣。
            return self._send(409, {"error": str(e)})
        return self._send(202, status_payload())

    def _mark(self, payload):
        keys = payload.get("keys") or []
        state = payload.get("state")
        # selected/skipped 是 dashboard 唯一該碰的兩個狀態。applied 由申請流程自己
        # 寫(ticket 08),rejected/closed 是掃描的事 —— 白名單擋住,不是轉發全部。
        if state not in ("selected", "skipped"):
            return self._send(400, {"error": "state 必須是 selected 或 skipped"})
        if not keys or not all(isinstance(k, str) for k in keys):
            return self._send(400, {"error": "keys 必須是非空字串陣列"})

        done, failed = [], []
        for key in keys:
            try:
                run_cli(["mark", key, state])
                done.append(key)
            except (CliError, subprocess.TimeoutExpired) as e:
                failed.append({"key": key, "error": str(e)})
        # 一批裡有的成功有的失敗是正常的(例如某個 key 已經被排程改掉了),
        # 所以回 200 加明細,而不是整批失敗。
        return self._send(200, {"marked": done, "failed": failed, "jobs": read_jobs()})


def serve(port, open_browser=True):
    token = issue_token()
    Handler.token = token
    try:
        httpd = DashboardServer(("127.0.0.1", port), Handler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            sys.exit("埠 %d 已經有人在用 —— 多半是另一個 dashboard 還開著。\n"
                     "  看是誰:  lsof -ti :%d\n"
                     "  或換一個:python3 server.py --port %d" % (port, port, port + 1))
        raise
    url = "http://127.0.0.1:%d/?t=%s" % (port, token)
    # flush=True:launchd 底下 stdout 不是 tty,不 flush 的話這幾行會卡在緩衝區裡,
    # 使用者去看 log 只會看到空檔案。
    print("Joblander dashboard", flush=True)
    print("  " + url, flush=True)
    print("  token 也寫在 %s(0600)" % TOKEN_PATH, flush=True)
    print("  Ctrl-C 結束", flush=True)
    if open_browser:
        # 只在有 GUI 的情況下試,失敗就算了 —— 網址已經印出來了。
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()


def main():
    port = load_config().get("dashboard_port", DEFAULT_PORT)
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    serve(port, open_browser="--no-browser" not in sys.argv)


if __name__ == "__main__":
    main()
