---
name: setup
description: "Walk a brand-new user through installing Joblander end to end — dependency check, config.json, Gmail App Password into the macOS Keychain, Gmail/Calendar connectors, résumé-driven generation of applicant-profile.json and job-criteria.md, the ASML search token, a first scan_jobs.py discover run, launchd scheduling, the always-on dashboard and its Dock launcher, and a final leak check on git status. Use whenever someone is setting this repo up for the first time or repairing a half-finished install, e.g. '幫我裝 Joblander', 'set this up for me', '我剛 clone 下來要怎麼開始', 'joblander 裝到一半卡住了', 'set up the job scanner'. Also use when a user reports that a piece of the system was never configured — no notification mail arriving, Gmail tools unavailable, today-jobs.md never appearing — since the fix is to re-run the matching phase's gate."
---

# Joblander Setup

Turns `docs/SETUP.md` into a gated, resumable install. Five phases; **each ends in a check that must pass before the next one starts.** A half-configured install is the failure mode this exists to prevent — every piece here fails silently rather than loudly, so the checks are the whole point.

The flowchart is `.claude/skills/setup/WORKFLOW.md`. Read `docs/SETUP.md` for the pitfalls behind each step; this file is the procedure.

**Project root:** the folder containing this `.claude/` directory. All paths below are relative to it.

## Resuming a partial install

Never assume a fresh machine. Before phase 1, detect what's already done and skip it:

| 已完成的訊號 | 跳過 |
|---|---|
| `config.json` 存在且 `gmail_address` 不是 `you@gmail.com` | 步驟 2 |
| `security find-generic-password -s "<service>" -w` 有輸出 | 步驟 3 |
| `automation/job-search/job-criteria.md` 存在 | 步驟 8 |
| `automation/job-apply/_internal/applicant-profile.json` 存在 | 步驟 6-7 |
| `~/Library/LaunchAgents/<prefix>.jobdiscover.plist` 存在 | 步驟 11 |

Report what you're skipping before you start. Never re-run a step's gate against a file you just skipped — a skip means it already passed.

---

## Phase 1 — 環境（全部裝好且驗證可用，才准往下）

1. **檢查依賴。** `python3 --version`(需 3.9+)、`command -v claude`。

   **`claude` 查不到的話**,使用者多半是從桌面版或 VS Code 進來的,CLI 沒進 PATH。這個系統的排程整個靠 `claude -p`,沒有 CLI 就只有 `job-search` 能跑,`interview-scan` 一定裝不起來。裝法擇一,裝完重開終端機再回來:

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash    # 原生安裝，會裝到 ~/.local/bin/claude
   npm install -g @anthropic-ai/claude-code          # 已經有 node 的話
   ```

   桌面版和 VS Code 擴充是**額外的介面,不是替代品** —— 它們讓平常用起來順手,但 launchd 排程只認得得到絕對路徑的 CLI。使用者說「我用桌面版就好」的時候要講清楚這件事。

   `python3` 缺的話停下來交棒,那個你裝不了。Playwright 只有要自動填申請表才需要,問使用者要不要,要就跑 `pip3 install playwright && playwright install chromium`。

2. **寫 `config.json`。** `cp config.example.json config.json`,然後問使用者兩件事並填進去:
   - `gmail_address` —— 報告從這寄出也寄到這,行事曆也是這個帳號
   - `launchd_label_prefix` —— 反向 DNS,建議 `com.<使用者名字>`

   `gmail_app_password_keychain_service` 保持預設 `job-scan-smtp-app-password`,不要問。**把 `_readme` / `_gmail_address` 那類底線開頭的說明鍵留著**,它們是給人讀的,程式會忽略。

3. **App Password → Keychain（交棒 ①）。** 叫使用者去 https://myaccount.google.com/apppasswords 產一組 16 碼(需先開兩步驟驗證),貼回來之後你跑:

   ```bash
   security add-generic-password \
     -a "$(python3 -c 'import json;print(json.load(open("config.json"))["gmail_address"])')" \
     -s "job-scan-smtp-app-password" \
     -w "<16 碼>"
   ```

   **閘門:** `security find-generic-password -s "job-scan-smtp-app-password" -w` 要印得出東西。印不出來就退回交棒,不要往下。

4. **Connectors（交棒 ②）。** 叫使用者去 Claude 的 **Settings → Connectors** 接 **Gmail**(唯讀)和 **Google Calendar**。等他說「接好了」,**當場**驗:

   ```bash
   claude -p "用 ToolSearch 載入 mcp__claude_ai_Gmail__search_threads，然後搜尋 newer_than:1d，只回報找到幾封" \
     --allowedTools "ToolSearch mcp__claude_ai_Gmail__search_threads"
   ```

   `--allowedTools` 裡**一定要有 `ToolSearch`** —— `ENABLE_TOOL_SEARCH=true` 之下 `mcp__*` 全是 deferred,只給權限不會讓它可被呼叫,agent 會回報「工具不可用」然後靜靜地什麼都不做。

   **閘門:** 回得出數字才算通。沒通就退回去重接,不要往下 —— 連接器沒接好是整套裡最難查的失敗模式。

---

## Phase 2 — 履歷（生 `applicant-profile.json`）

5. **要履歷（交棒 ③）。** 請使用者把履歷 PDF 拖進來或給路徑。複製進 `background/`(整層已 gitignore)。沒有履歷就退回逐欄問答,能跑但慢很多。

6. **讀 PDF,生草稿。** 抽姓名、偏好稱呼、電話、email、地址、學歷、工作經歷,填成 `applicant-profile.example.json` 的結構。**先讀那個 example 檔**,照它的鍵名和巢狀結構寫,不要自己發明欄位。抽不到的欄位留空,不要猜。

7. **攤開給使用者逐欄確認。** 整份印出來,改到他說都對為止,**這時才寫檔**。接著問三件履歷上不會有的事:簽證/工作權答案、薪資期待、`application_defaults` 裡的問題。履歷 PDF 的路徑填回 `resume.path`。

   **閘門:** 使用者沒逐欄看過就不算過。這份檔案之後每一次自動填表都會照抄,錯一格會錯很多次。

---

## Phase 3 — 條件與公司

8. **從履歷推 `job-criteria.md` 草稿。** `cp automation/job-search/job-criteria.example.md automation/job-search/job-criteria.md`,然後照履歷改:職稱關鍵字、地點、學歷門檻、教育背景。**先讀 example 檔開頭的格式規則** —— `## 標題` 括號裡的英文代號是程式讀的,絕對不能改;同義詞用 `|` 分隔。

   給使用者看草稿、改到他說對,才算寫入。

9. **`sources.json` 原樣不動。** 預設四家半導體設備商(KLA、AMAT、ASML 已啟用,Lam Research 的 adapter 還沒實作、`enabled: false`)全部保留,**不要問使用者要不要留、也不要問要不要追加**。要換產業是之後的事,不是安裝流程的一部分。

10. **ASML search token（交棒 ④）。** 叫使用者:開 https://www.asml.com/en/careers/find-your-job → DevTools Network 分頁 → 搜尋任一職缺 → 找往 `discover-euc1.sitecorecloud.io` 的請求 → 從 header 或 query string 複製 token。

    **token 寫進 `config.json` 的 `source_secrets.asml.auth_token`,不是寫進 `sources.json`** —— `sources.json` 是公開的,只放佔位符,`scan_jobs.py` 載入時才把 `config.json` 的值疊上去。

    抓不到就重試,一直到抓到為止。**只有使用者明說放棄**,才把 `sources.json` 裡 ASML 那筆設 `"enabled": false`,其他家照跑。

---

## Phase 4 — 跑一次，確認三個產物

11. **跑 `python3 automation/job-search/_internal/scan_jobs.py discover`**,然後確認三樣東西都在:

    | 產物 | 在哪 |
    |---|---|
    | 勾選清單 | `automation/job-search/today-jobs.md` |
    | 執行紀錄 | `automation/job-search/_internal/logs/launchd.log` 最後一段 |
    | 通知信 | 使用者的信箱 |

    **這三個都是無條件產出的,今天沒有新職缺也一樣會有** —— 信裡就寫 `New candidates: none`。所以少任何一個都是真的壞了,不是「今天剛好沒東西」。

    信沒收到就退回步驟 3 重設 App Password:`send_email_notification()` 讀不到 Keychain 會靜靜跳過、只印一行 stdout,掃描照樣回傳成功。

    **閘門:** 三個到齊才往下。少了就把 stdout 原樣貼給使用者,不要自己猜原因。

---

## Phase 5 — 排程與收尾

12. **問排程時段。** 問使用者想幾點收職缺報告(可以多個時段,答「不用」就跳過整步)。**面試掃描一律排在每個時段 +20 分,這個間隔不開放調整** —— 兩個 job 都寫 `ledger.json`,同分鐘觸發會互相蓋掉。

    plist 照 `docs/SETUP.md` 步驟 7 的模板產,兩個都要:
    - `<prefix>.jobdiscover` → `automation/job-search/_internal/scan_jobs.py discover --scheduled`
    - `<prefix>.interviewscan` → `automation/interview-scan/_internal/run_scan.py`

    兩個坑照抄不要改:**`ProgramArguments` 第一個一定是 `python3` 的絕對路徑,不能是 `/bin/bash`**(macOS TCC 不准 bash 碰 `~/Desktop`、`~/Documents`,job 會在第一行之前 exit 126);`claude` 也要絕對路徑,`which claude` 查出來設進 `CLAUDE_BIN`。

13. **生 `BACKGROUND.md` 骨架。** `cp background/BACKGROUND.example.md background/BACKGROUND.md`,把履歷裡的公司、職稱、年份、專案標題填成空的段落標題。**STAR 內容交棒給使用者,但不擋安裝完成** —— 那份只有 `interview-prep` 會讀,真的排到面試前用不到。列進最後回報的待辦。

14. **問要不要裝看板。** 說明一句就好:「四個資料來源在同一頁,只有你自己連得到,點 Dock 圖示就開」。答「不用」就跳過,不影響其他東西。

    ```bash
    python3 automation/dashboard/_internal/install_launchd.py
    ```

    **這支跟步驟 12 的兩個排程不同 —— 它是 `KeepAlive` 常駐,不是定時觸發。** 登入就起來、掉了自己重啟,label 是 `<prefix>.dashboard`,跟另外兩個同一個命名空間。它會順手在 `~/Applications` 做啟動器,**拖進 Dock 這一步只有使用者自己能做,要明講**。

    裝完當場驗:`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/` 要是 200。

    兩件事一定要交代,不然使用者會以為壞了:登入**不會**自動跳分頁(起的是伺服器不是瀏覽器)、點啟動器跳出「現在沒有在跑」就是伺服器掉了、**對話框按「叫起來」就好**。

    順帶講清楚:看板**沒有任何驗證**,綁 `127.0.0.1` 就是全部的防護,網址固定所以**可以存書籤**。不要自作主張加回通行碼或 Origin 檢查 —— 2026-08-29 取捨過了。

15. **收尾檢查。**

    ```bash
    python3 automation/job-search/_internal/scan_jobs.py discover
    security find-generic-password -s "job-scan-smtp-app-password" -w
    claude -p "列出你能用的 Gmail 工具" --allowedTools "ToolSearch mcp__claude_ai_Gmail__search_threads"
    git status
    ```

    **`git status` 是硬閘門。** 看到任何個人檔案就停下來修 `.gitignore`,不要說「應該沒問題」。`.gitignore` 只擋還沒被追蹤的檔,已追蹤的改了照樣進 commit。

16. **回報。** 哪幾步做了、哪幾步跳過(以及為什麼)、`BACKGROUND.md` 還欠什麼、下一步做什麼。看板裝了就把「把啟動器拖進 Dock」列進待辦 —— 那一步只有使用者能做。

---

## What this skill does NOT do

不代跑五個交棒點:Google App Password、Connectors 的 OAuth 授權、履歷檔本身、ASML 的 sitecore token、`BACKGROUND.md` 的 STAR 內容。前四個是真的安全邊界或只有使用者手上有的東西,第五個無法自動生成 —— 履歷寫得含糊,模擬面試答案就一樣含糊。

不 commit、不 push、不動 `docs/SETUP.md`。不換公司清單或產業(那是安裝完之後改 `sources.json` 的事)。不在使用者逐欄確認之前寫任何一份個人資料檔。
