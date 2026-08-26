# Setup

Roughly 30 minutes end to end. Steps 1–4 get the job scanner running; 5–7 are for the application autofill and the interview scan.

**macOS only.** The scheduling uses `launchd` and the Gmail password lives in the macOS Keychain. On Linux you'd swap those for cron and a secrets file — the Python itself is portable, nothing else is.

---

## 0. Prerequisites

| 需要 | 檢查指令 | 備註 |
|---|---|---|
| Python 3.9+ | `python3 --version` | 掃描器只用標準函式庫,沒有第三方套件 |
| Claude Code **CLI** | `claude --version` | interview-scan 和所有 skill 都靠它 |
| Playwright（選配） | `pip3 install playwright && playwright install chromium` | 只有要自動填申請表才需要 |

### 裝 Claude Code

**CLI 是必要的,不是選配。** launchd 排程直接呼叫 `claude -p`,沒有 CLI 的話 `interview-scan` 裝不起來,只剩 `job-search` 能跑。

```bash
curl -fsSL https://claude.ai/install.sh | bash    # 原生安裝，裝到 ~/.local/bin/claude
npm install -g @anthropic-ai/claude-code          # 已經有 node 的話
```

裝完**重開終端機**再 `claude --version`。

介面另外挑,兩個都是加在 CLI 上面的,不是替代:

- **VS Code 擴充**（推薦）—— 擴充商店搜 Claude Code。邊看檔案邊改,設定過程中要確認 `applicant-profile.json` 的時候特別有感
- **桌面版** —— [claude.ai/download](https://claude.ai/download)。不想碰編輯器就用這個

只裝桌面版、沒裝 CLI 的話,`command -v claude` 會查不到,步驟 7 的排程整段會失敗。

Clone 下來之後,**先把整個資料夾放到你想長期擺的位置再開始** —— launchd 排程會記住絕對路徑,之後搬家要重設。

---

## 1. `config.json`

```bash
cp config.example.json config.json
```

打開填三格:

- `gmail_address` —— 掃描報告從這個帳號寄出、也寄到這個帳號,interview-scan 也寫這個帳號的 Google Calendar
- `gmail_app_password_keychain_service` —— 保持預設 `job-scan-smtp-app-password` 就好,除非你想換名字
- `launchd_label_prefix` —— 反向 DNS 慣例,填 `com.<你的名字>` 之類的

`config.json` 已經在 `.gitignore` 裡,不會被 commit。

---

## 2. Gmail App Password → Keychain

**密碼不會寫進任何檔案。** 腳本每次要寄信時去 Keychain 讀。

1. 到 [Google 帳戶 → 安全性 → 應用程式密碼](https://myaccount.google.com/apppasswords) 產生一組（需要先開兩步驟驗證）
2. 存進 Keychain:

```bash
security add-generic-password \
  -a "$(python3 -c 'import json;print(json.load(open("config.json"))["gmail_address"])')" \
  -s "job-scan-smtp-app-password" \
  -w "<貼上那 16 碼>"
```

驗證:
```bash
security find-generic-password -s "job-scan-smtp-app-password" -w
```

沒設也不會擋掉掃描 —— 寄信失敗會被記進 log 然後跳過,掃描照跑。

---

## 3. 你要找什麼樣的工作

```bash
cp automation/job-search/job-criteria.example.md automation/job-search/job-criteria.md
```

這個檔**同時給人看也給程式讀**。格式規則寫在檔案開頭,`## 標題` 括號裡的英文代號不要改。地點、職稱關鍵字、學歷、排除條件都在裡面。

## 4. 你要盯哪些公司

編輯 `automation/job-search/_internal/sources.json`。**這是整個系統唯一的公司清單來源** —— 掃描器、interview-scan 的 Gmail 搜尋、SOP 覆蓋層命名全部從它衍生。加一家公司只要改這一個檔。

預設附了四家半導體設備商,實際會跑的是三家:

| 公司 | adapter | 狀態 |
|---|---|---|
| KLA | `workday` | 啟用 |
| Applied Materials | `eightfold` | 啟用 |
| ASML | `sitecore_discover` | 啟用,但要自己補 token（見下） |
| Lam Research | `unknown` | `enabled: false` —— adapter 還沒實作,`/api/apply/v2/jobs` 的正確參數要重抓封包 |

要換產業就整批替換掉。

### ASML 的 `auth_token` 要自己抓

`sources.json` 裡 ASML 那筆的 `auth_token` 是佔位符。它是 ASML 職涯網站前端 JS 內建的公開 search-only key,每個訪客都拿到同一個,不是登入憑證 —— 但公開 repo 裡不放實際字串,免得被 secret scanner 誤判成外洩。

抓法:開 https://www.asml.com/en/careers/find-your-job,開瀏覽器 DevTools 的 Network 分頁,搜尋職缺,找往 `discover-euc1.sitecorecloud.io` 的請求,從 request header 或 query string 裡把 token 複製出來。

**抓到的 token 填進 `config.json`,不是填回 `sources.json`:**

```json
{
  "source_secrets": {
    "asml": { "auth_token": "01-<你抓到的字串>" }
  }
}
```

`load_sources()` 會在載入時把 `source_secrets` 依 company id 疊到那家的 `config` 區塊上(`scan_jobs.py:44`)。`sources.json` 是公開檔、留著佔位符不用動;`config.json` 是 gitignore 的,token 不會進 repo。**直接改 `sources.json` 也會動,但那個檔會被 commit** —— 別這樣做。

同一個機制適用之後任何一家需要 key 的公司:`sources.json` 放佔位符,`config.json` 放真值。

不想處理就把 `sources.json` 裡 ASML 那筆設 `"enabled": false`,其他兩家照跑。

### 第一次跑

```bash
python3 automation/job-search/_internal/scan_jobs.py discover
```

產出 `automation/job-search/today-jobs.md` —— 一份給你手動勾選的清單。

---

## 5. 你自己的資料（申請表自動填 + 面試準備才需要）

```bash
cp "background/BACKGROUND.example.md" "background/BACKGROUND.md"
cp automation/job-apply/_internal/applicant-profile.example.json \
   automation/job-apply/_internal/applicant-profile.json
```

兩個都要從頭改成你自己的。分工是:

- **`applicant-profile.json`** —— 表單欄位。姓名、地址、電話、學歷、工作經歷、簽證答案。結構化,給機器填表用。
- **`BACKGROUND.md`** —— 敘事。STAR 故事、優缺點、離職原因、動機。給 `interview-prep` 生成面試題答案用。

⚠️ **`BACKGROUND.md` 值得你花一小時認真寫。** 這是整個系統唯一無法自動生成的東西 —— 公司資料、職缺內容、論壇風評系統都會自己查,查不到的只有你做過什麼、數字是多少。寫得含糊,產出的模擬面試答案就一樣含糊。

履歷 PDF 也放進 `background/`,路徑填回 `applicant-profile.json` 的 `resume.path`。整個資料夾除了 `.example` 檔以外都被 gitignore。

---

## 6. 連接 Gmail 和 Google Calendar（interview-scan 才需要）

⚠️ **這跟步驟 2 的 App Password 是兩件不同的事,不要搞混:**

| 用途 | 授權方式 |
|---|---|
| **寄**掃描報告給你 | 步驟 2 的 App Password（SMTP，存 Keychain） |
| **讀**你的信箱找面試邀約 | 這一步的 Gmail 連接器（OAuth） |
| 建立面試行事曆事件 | 這一步的 Google Calendar 連接器 |

只跑 `job-search` 找職缺的話,這步可以跳過 —— 掃描器不碰你的信箱。

### 連接方式

在 Claude（claude.ai 或桌面版）的 **Settings → Connectors** 裡,連接:

- **Gmail** —— interview-scan 只用 `search_threads` 和 `get_thread`,**唯讀**。它不寄信、不貼標籤、不刪信、不改任何東西。報告是走 SMTP 另外寄的。
- **Google Calendar** —— 用 `list_events`、`create_event`、`update_event`。**只新增和更新,永不刪除**,而且只動 `config.json` 裡那個帳號的行事曆。

連好之後驗證:

```bash
claude -p "用 ToolSearch 載入 mcp__claude_ai_Gmail__search_threads，然後搜尋 newer_than:1d，只回報找到幾封" \
  --allowedTools "ToolSearch mcp__claude_ai_Gmail__search_threads"
```

回得出數字就是通了。

### 一個排程專屬的坑

環境變數 `ENABLE_TOOL_SEARCH=true` 會讓所有 `mcp__*` 工具變成 deferred —— 這時候把它們列在 `--allowedTools` 裡**只是給了權限,並不會讓它們可被呼叫**。agent 會回報「Gmail 工具不可用」然後靜靜地什麼都不做,不會報錯。

解法是 `--allowedTools` 裡**必須同時列出 `ToolSearch`**。`scan-gmail-interviews.sh` 已經這樣寫了,別把它拿掉。

### 排程環境的限制

透過 claude.ai 互動式授權的連接器,在**完全無人的 headless 執行**下不一定拿得到。如果排程跑起來一直回報 Gmail 工具不可用,先手動跑一次 `scan-gmail-interviews.sh` 確認互動模式下正常,再去查排程環境的差異。

---

## 7. 排程（選配）

兩個 launchd job,一個找職缺、一個掃面試信:

```bash
# 找職缺 —— 08:00 / 13:00
cat > ~/Library/LaunchAgents/<你的 prefix>.jobdiscover.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string><你的 prefix>.jobdiscover</string>
  <key>ProgramArguments</key><array>
    <string>/opt/homebrew/bin/python3</string>
    <string><REPO 絕對路徑>/automation/job-search/_internal/scan_jobs.py</string>
    <string>discover</string><string>--scheduled</string>
  </array>
  <key>StartCalendarInterval</key><array>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
  </array>
</dict></plist>

launchctl load ~/Library/LaunchAgents/<你的 prefix>.jobdiscover.plist
EOF
```

面試掃描同理,指向 `automation/interview-scan/_internal/run_scan.py`,排在 **08:20 / 13:20**。

**那 20 分鐘的間隔是有意的,不要拿掉。** 兩個 job 都會寫 `ledger.json`（一個經 `discover`,一個經 `progress`）,同分鐘觸發過會互相蓋掉。

### 兩個踩過的坑

1. **不要讓 launchd 直接跑 `/bin/bash`。** macOS TCC 不准 bash 存取 `~/Desktop` 和 `~/Documents`,job 會在第一行之前就 exit 126。指向 `python3` 包一層,由它去叫 shell script。
2. **`claude -p` 要用絕對路徑。** launchd 的 PATH 很精簡。`which claude` 查出來,設進 `CLAUDE_BIN` 環境變數或直接改 `scan-gmail-interviews.sh`。

log 在 `automation/*/\_internal/logs/launchd.log`。

---

## 8. 看板（選配，但裝了才會天天用）

四個資料來源（今日職缺、面試行程、已投遞、待辦）在同一頁,只綁 `127.0.0.1`。先跑一次看看:

```bash
python3 automation/dashboard/_internal/server.py
```

會自己開瀏覽器。網址帶一組隨機通行碼,`Ctrl-C` 結束。

要它一直在,不用每次手動開:

```bash
python3 automation/dashboard/_internal/install_launchd.py --print   # 先看要裝什麼
python3 automation/dashboard/_internal/install_launchd.py           # 確認後再裝
```

這支跟步驟 7 的兩個 job 不一樣 —— 它不是排程,是 `KeepAlive` 常駐:登入就起來,掉了自己重啟。plist 用的是同一個 `launchd_label_prefix`,label 是 `<prefix>.dashboard`。

安裝時會順手在 `~/Applications` 做一個啟動器(Finder 裡叫「求職看板」)。**把它拖到 Dock** —— 之後開看板就是點一下。

### 三件會讓人以為壞掉的事

1. **登入不會自動跳分頁。** launchd 起的是伺服器,不是瀏覽器。要看就點啟動器。
2. **通行碼每次啟動都會換,所以不能存書籤。** 隔天那組就失效了。啟動器每次現讀 `~/.joblander/dashboard-token`,所以它不受影響。
3. **頁面突然說沒授權** = 伺服器重啟過(自動重啟也算)。點啟動器重開,不用做別的。

**看板自己不寫任何檔案。** 網頁上的每個動作都是去呼叫既有的 CLI,所以你手改過的 markdown 不會被它蓋掉。

---

## 驗證清單

```bash
python3 automation/job-search/_internal/scan_jobs.py discover   # 應該產出 today-jobs.md
security find-generic-password -s "job-scan-smtp-app-password" -w  # 應該印出密碼
claude -p "列出你能用的 Gmail 工具" --allowedTools "ToolSearch mcp__claude_ai_Gmail__search_threads"  # 應該列得出來
git status                                                       # 不該看到任何個人檔案
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/  # 裝了看板才要看,應該是 200
```

最後那條最重要。**第一次 commit 前先看一遍 `git status`** —— `.gitignore` 只擋沒被追蹤的檔,已經被追蹤的檔改了照樣會進 commit。
