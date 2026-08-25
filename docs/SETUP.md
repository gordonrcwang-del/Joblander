# Setup

Roughly 30 minutes end to end. Steps 1–4 get the job scanner running; 5–6 are for the application autofill and the interview scan.

**macOS only.** The scheduling uses `launchd` and the Gmail password lives in the macOS Keychain. On Linux you'd swap those for cron and a secrets file — the Python itself is portable, nothing else is.

---

## 0. Prerequisites

| 需要 | 檢查指令 | 備註 |
|---|---|---|
| Python 3.9+ | `python3 --version` | 掃描器只用標準函式庫,沒有第三方套件 |
| [Claude Code](https://claude.ai/code) | `which claude` | interview-scan 和所有 skill 都靠它 |
| Playwright（選配） | `pip3 install playwright && playwright install chromium` | 只有要自動填申請表才需要 |

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

預設附了四家半導體設備商（KLA、Applied Materials、ASML、Lam Research）。要換產業就整批替換掉。

**ASML 那筆的 `auth_token` 是佔位符,要自己抓。** 它是 ASML 職涯網站前端 JS 內建的公開 search-only key,每個訪客都拿到同一個,不是登入憑證 —— 但公開 repo 裡不放實際字串,免得被 secret scanner 誤判成外洩。抓法:開 https://www.asml.com/en/careers/find-your-job,開瀏覽器 DevTools 的 Network 分頁,搜尋職缺,找往 `discover-euc1.sitecorecloud.io` 的請求,從 request header 或 query string 裡把 token 複製出來。

不想處理就把那筆設 `"enabled": false`,其他家照跑。

### 第一次跑

```bash
python3 automation/job-search/_internal/scan_jobs.py discover
```

產出 `automation/job-search/today-jobs.md` —— 一份給你手動勾選的清單。

---

## 5. 你自己的資料（申請表自動填 + 面試準備才需要）

```bash
cp "Background Informations/BACKGROUND.example.md" "Background Informations/BACKGROUND.md"
cp automation/job-apply/_internal/applicant_profile.example.json \
   automation/job-apply/_internal/applicant_profile.json
```

兩個都要從頭改成你自己的。分工是:

- **`applicant_profile.json`** —— 表單欄位。姓名、地址、電話、學歷、工作經歷、簽證答案。結構化,給機器填表用。
- **`BACKGROUND.md`** —— 敘事。STAR 故事、優缺點、離職原因、動機。給 `interview-prep` 生成面試題答案用。

⚠️ **`BACKGROUND.md` 值得你花一小時認真寫。** 這是整個系統唯一無法自動生成的東西 —— 公司資料、職缺內容、論壇風評系統都會自己查,查不到的只有你做過什麼、數字是多少。寫得含糊,產出的模擬面試答案就一樣含糊。

履歷 PDF 也放進 `Background Informations/`,路徑填回 `applicant_profile.json` 的 `resume.path`。整個資料夾除了 `.example` 檔以外都被 gitignore。

---

## 6. 排程（選配）

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
2. **`claude -p` 要用絕對路徑。** launchd 的 PATH 很精簡。`which claude` 查出來,設進 `CLAUDE_BIN` 環境變數或直接改 `scan_gmail_interviews.sh`。

log 在 `automation/*/\_internal/logs/launchd.log`。

---

## 驗證清單

```bash
python3 automation/job-search/_internal/scan_jobs.py discover   # 應該產出 today-jobs.md
security find-generic-password -s "job-scan-smtp-app-password" -w  # 應該印出密碼
git status                                                       # 不該看到任何個人檔案
```

最後那條最重要。**第一次 commit 前先看一遍 `git status`** —— `.gitignore` 只擋沒被追蹤的檔,已經被追蹤的檔改了照樣會進 commit。
