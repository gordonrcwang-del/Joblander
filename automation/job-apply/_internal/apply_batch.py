#!/usr/bin/env python3
"""
apply_batch.py — 一批投遞的執行器(ticket 08)。

WHY THIS EXISTS
以前投一個職缺要開 Claude Code 打一串話,而且一家公司投五個職缺就要登入五次。
這支把一批「已勾選」的職缺串起來:一家公司一個瀏覽器 session,登入一次連續處理
完該公司所有職缺,每投完一個停下來等人按「下一個」。

批次語意(spec 的決定,不是這裡發明的):
  - 來源是 scan_jobs.py queue 的輸出,它已經依 company_id 分組
  - 一家公司一個工作目錄。這推翻了「一個職缺一個工作目錄」的舊慣例 —— 代價是
    同一批的指令記錄混在一起,換來的是共用登入
  - 引擎程序(playwright_script.py)整批只起一次。瀏覽器 session 只在程序重啟時
    失效,同一個活著的程序內切換職缺網址不會掉登入
  - 每處理完一個職缺就停,等 dashboard 寫進 continue 檔才走下一個。因此不需要
    獨立的失敗處理策略:每一筆都有人在,失敗當場看得到
  - 換公司時開新的工作目錄與新的引擎程序,需要重新登入

每一步的狀態寫進 ~/.joblander/apply-state.json,dashboard 的狀態欄讀它。
「需要登入」和「等你同意」不在這裡寫 —— 那兩個時刻只有 agent 自己知道,由它寫
工作目錄裡的 agent-status.json。

USAGE
    python3 apply_batch.py --company asml
    python3 apply_batch.py --company asml --dry-run   # 不起引擎、不叫 agent,只跑流程
"""
import json
import os
import shutil
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..", ".."))
ENGINE_PY = os.path.join(BASE_DIR, "playwright_script.py")
SCAN_JOBS_PY = os.path.join(REPO_ROOT, "automation", "job-search", "_internal", "scan_jobs.py")

RUNTIME_DIR = os.path.join(os.path.expanduser("~"), ".joblander")
STATE_PATH = os.path.join(RUNTIME_DIR, "apply-state.json")
# 工作目錄刻意在專案外 —— 那是可丟棄的執行期狀態(Chrome profile、指令記錄),
# 不是專案內容。
SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".job-apply-sessions")

CONTINUE_FILE = "continue"
STOP_FILE = "stop"
AGENT_STATUS_FILE = "agent-status.json"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", os.path.join(os.path.expanduser("~"), ".local", "bin", "claude"))

# agent 每一筆的指令。刻意短:真正的 SOP 在 autofill-agent 技能裡,這裡只交代
# 「哪一個職缺、用哪個工作目錄、做完要回報什麼」。
AGENT_PROMPT = """Use the autofill-agent skill to apply for ONE job.

Job key: {key}
Job title: {title}
URL: {url}
Engine work dir: {dir}   (the engine is ALREADY RUNNING against this dir — do not start another one, and do not use a different --dir)

The browser session in that dir may already be signed in from an earlier job in
this batch. Check first; only sign in if you are actually signed out.

Write {dir}/agent-status.json at exactly two moments, and no others:
  - when you need the user to sign in manually:
    {{"status": "login_required", "note": "<one short line>"}}
  - when the filled form is ready and you are waiting for the user's yes:
    {{"status": "awaiting_approval", "note": "<one short line>"}}
Delete that file once the moment has passed.

Follow the existing submit rules: unless auto-submit-config.json says otherwise
for this company, show the user the filled form and wait for an explicit yes
before submitting."""

ALLOWED_TOOLS = ("ToolSearch Read Write Edit Skill Bash(python3 *) "
                 "Bash(mkdir *) Bash(cat *) Bash(ls *)")


def emit(msg):
    print("[apply_batch] %s" % msg, flush=True)


def write_state(**fields):
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    state = {"updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    state.update(fields)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)
    return state


def clear_state():
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)


def load_queue():
    out = subprocess.run([sys.executable, SCAN_JOBS_PY, "queue"],
                         capture_output=True, text=True, cwd=REPO_ROOT)
    if out.returncode != 0:
        sys.exit("scan_jobs.py queue 失敗:%s" % (out.stderr.strip() or out.stdout.strip()))
    return json.loads(out.stdout or "{}")


def make_work_dir(company_id):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = os.path.join(SESSIONS_DIR, "batch-%s-%s" % (company_id, time.strftime("%Y%m%d-%H%M%S")))
    os.makedirs(path, exist_ok=True)
    return path


SOP_DIR = os.path.join(BASE_DIR, "sop", "_local")


def profile_dir_for(company_id):
    """登入存在哪裡。

    這是整批共用、而且**跨批次留著**的 Chrome persistent profile,不是每批新建的
    工作目錄。命名照 SOP 既有的規矩(`sop/_local/<company>-<platform>.md`):
    Workday 是 `<company>-profile`,Eightfold 是 `<company>-eightfold-profile`。
    磁碟上 `~/.job-apply-sessions/amat-eightfold-profile` 已經存在,就是這個東西。

    登入保不保得住,取決於這個目錄,不取決於瀏覽器程序活多久 —— 之前一職缺一目錄
    的做法之所以不用每次重登,原因就在這裡:`--dir` 各自獨立,profile 是共用的。
    """
    for name in sorted(os.listdir(SOP_DIR)):
        if not name.endswith(".md") or not name.startswith(company_id + "-"):
            continue
        platform = name[len(company_id) + 1:-3]
        suffix = "-eightfold-profile" if platform == "eightfold" else "-profile"
        return os.path.join(SESSIONS_DIR, company_id + suffix)
    # 沒有對應 SOP 的公司照 Workday 的命名走 —— 這種公司本來就該先手動跑過一次。
    return os.path.join(SESSIONS_DIR, company_id + "-profile")


def start_engine(work_dir, start_url, profile_dir):
    """引擎整批只起一次 —— 省掉每一筆重開瀏覽器,不是為了保住登入(那是 profile
    目錄的事)。位置參數兩個都要給:引擎的介面是
    `playwright_script.py <start_url> <profile_dir> --dir <work_dir>`。"""
    log = open(os.path.join(work_dir, "engine.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, ENGINE_PY, start_url, profile_dir,
                             "--dir", work_dir],
                            stdout=log, stderr=subprocess.STDOUT, cwd=REPO_ROOT)
    return proc


def wait_for_continue(work_dir, poll=0.5):
    """停下來等人。回 True 表示繼續,False 表示使用者喊停。

    等的是檔案而不是 stdin —— 這支是被 dashboard 在背景叫起來的,沒有終端可以讀。
    """
    cont = os.path.join(work_dir, CONTINUE_FILE)
    stop = os.path.join(work_dir, STOP_FILE)
    while True:
        if os.path.exists(stop):
            os.remove(stop)
            return False
        if os.path.exists(cont):
            os.remove(cont)
            return True
        time.sleep(poll)


def run_agent(job, work_dir, dry_run):
    prompt = AGENT_PROMPT.format(key=job["key"], title=job["title"],
                                 url=job.get("url") or "", dir=work_dir)
    if dry_run:
        emit("DRY RUN — 不叫 agent。prompt 長度 %d" % len(prompt))
        return 0
    proc = subprocess.run([CLAUDE_BIN, "-p", prompt, "--allowedTools", ALLOWED_TOOLS],
                          cwd=REPO_ROOT)
    return proc.returncode


def mark_applied(key, dry_run):
    if dry_run:
        emit("DRY RUN — 不呼叫 mark %s applied" % key)
        return
    subprocess.run([sys.executable, SCAN_JOBS_PY, "mark", key, "applied"],
                   cwd=REPO_ROOT, capture_output=True, text=True)


def run_company(company_id, jobs, dry_run):
    work_dir = make_work_dir(company_id)
    company = jobs[0].get("company") or company_id
    total = len(jobs)
    emit("公司 %s:%d 筆,工作目錄 %s" % (company_id, total, work_dir))
    write_state(phase="prep", company=company, dir=work_dir,
                batch={"index": 0, "total": total}, job_title="", job_key="")

    profile_dir = profile_dir_for(company_id)
    emit("登入 profile:%s" % profile_dir)
    engine = None if dry_run else start_engine(work_dir, jobs[0]["url"], profile_dir)
    try:
        for i, job in enumerate(jobs, start=1):
            write_state(phase="running", company=company, dir=work_dir,
                        job_key=job["key"], job_title=job["title"],
                        batch={"index": i, "total": total})
            rc = run_agent(job, work_dir, dry_run)
            # agent 的狀態檔只在它停下來講話的那兩個時刻存在;一筆結束後清掉,
            # 免得下一筆一開始就顯示上一筆的「等你同意」。
            stale = os.path.join(work_dir, AGENT_STATUS_FILE)
            if os.path.exists(stale):
                os.remove(stale)
            if rc != 0:
                write_state(phase="failed", company=company, dir=work_dir,
                            job_key=job["key"], job_title=job["title"],
                            batch={"index": i, "total": total},
                            note="%s 沒有跑完(exit %d)" % (job["title"], rc))
                emit("失敗:%s exit %d —— 整批停在這裡" % (job["key"], rc))
                return False
            mark_applied(job["key"], dry_run)

            if i < total:
                write_state(phase="awaiting_next", company=company, dir=work_dir,
                            job_key=job["key"], job_title=job["title"],
                            batch={"index": i, "total": total},
                            note="這筆完成了,按「下一個」繼續")
                emit("等使用者按下一個(%d/%d)" % (i, total))
                if not wait_for_continue(work_dir):
                    emit("使用者喊停")
                    write_state(phase="done", company=company, dir=work_dir,
                                batch={"index": i, "total": total},
                                note="使用者在第 %d/%d 筆停下" % (i, total))
                    return False
        write_state(phase="done", company=company, dir=work_dir,
                    batch={"index": total, "total": total},
                    note="%s · %d 筆全數處理完" % (company, total))
        return True
    finally:
        if engine and engine.poll() is None:
            engine.terminate()
            try:
                engine.wait(timeout=10)
            except subprocess.TimeoutExpired:
                engine.kill()


def main():
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv
    company_id = None
    if "--company" in argv:
        i = argv.index("--company")
        if i + 1 < len(argv):
            company_id = argv[i + 1]

    queue = load_queue()
    if not queue:
        emit("沒有已勾選的職缺")
        clear_state()
        return 0
    if company_id and company_id not in queue:
        sys.exit("佇列裡沒有 %s —— 有的是:%s" % (company_id, ", ".join(sorted(queue))))

    # 一次只跑一家。跨公司要換 session、要重新登入,不該偷偷連著跑下去。
    target = company_id or sorted(queue)[0]
    ok = run_company(target, queue[target], dry_run)
    remaining = [c for c in sorted(queue) if c != target]
    if ok and remaining:
        emit("這一家做完了。其餘公司:%s —— 換公司要重新登入,回 dashboard 再按一次"
             % ", ".join(remaining))
    return 0


if __name__ == "__main__":
    sys.exit(main())
