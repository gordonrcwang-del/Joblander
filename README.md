# Joblander

求職的三件苦差事 —— 每天翻職缺、重複填申請表、追蹤面試信 —— 交給 [Claude Code](https://claude.ai/code) 自動處理,你只負責決定投哪個、以及面試當天講什麼。

給誰用:**同時在投很多家、而且投的是同一批公司**的人。投三家的話手動比較快;投三十家、每家開五個缺、每個缺一份 Workday 表格填四十分鐘,才是這套東西存在的理由。

> 這原本是一個人為了自己求職寫的工具,清乾淨之後放出來。預設附的是台灣半導體設備商,但公司清單、求職條件、你的個人資料**全部是設定,不是程式碼** —— 換產業、換國家只要改設定檔。

---

## 三個子系統

### 1. `job-search` —— 每天自動找職缺

直接打各家 ATS 自己的公開 JSON API（Workday、Eightfold、Sitecore Discover),不開瀏覽器、不裝第三方套件。抓回來的職缺用你寫的條件篩過,產出一份勾選清單並寄到你信箱。

只寄**變化的部分**。今天沒有新職缺就完全不寄信 —— 一份九成都是你已經知道的事的報告,會訓練你不再打開它。

### 2. `job-apply` —— 自動填申請表

一個常駐的 Playwright 瀏覽器,由檔案佇列驅動,把 `applicant_profile.json` 的內容填進申請表。

**送出前一定會停下來等你點頭**（除非你自己在 `auto_submit_config.json` 裡對特定公司改成自動）。**OAuth 登入永遠是你手動做** —— 那是真的安全機制,不繞過。

平台 quirk 累積在 `sop/workday.md` 和 `sop/eightfold.md` 裡:哪個下拉選單會靜默選錯、哪個 checkbox 的 `value` 屬性在說謊、哪個 `wait_for_text` 錨點會提早回傳。這些是踩出來的,不是讀文件讀來的。

### 3. `interview-scan` —— 掃面試信

讀 Gmail,把信分成面試邀約／婉拒／offer／測驗四類,更新職缺狀態、寫進行事曆、必要時建面試準備資料夾。**只讀不動 Gmail**,不寄信、不貼標籤、不刪信。

同一件事只通知你一次 —— 三天的搜尋窗口內同一封信會被讀到六次,去重機制靠的是「公司 + 硬時間戳」這種它自己無法改寫的簽章。

### 加上四個 Claude Code skill

| Skill | 做什麼 |
|---|---|
| `interview-prep` | 建 `<公司>/<職位>/` 資料夾,派兩波 subagent 平行產出公司簡介、職位分析、模擬面試 QA、領域基本知識 |
| `position-intro` | 貼一個職缺連結,直接在對話裡回你 JD 摘要 + 論壇風評,不寫檔 |
| `autofill-agent` | 開瀏覽器把申請表填完 |
| `workflow-chart` | 幫其他 skill 產流程圖 |

---

## 開始

```bash
git clone https://github.com/gordonrcwang-del/Joblander.git
cd Joblander
cp config.example.json config.json          # 填你的 Gmail
python3 automation/job-search/_internal/scan_jobs.py discover
```

完整步驟看 **[docs/SETUP.md](docs/SETUP.md)** —— 大約 30 分鐘,含 Keychain 設定和 launchd 排程。

---

## 你的資料不會離開你的電腦

這個 repo 裡沒有任何人的個人資料。你自己的資料放在四個被 `.gitignore` 的位置:

| 檔案 | 內容 | 附範例 |
|---|---|---|
| `config.json` | Gmail 位址、排程名稱 | `config.example.json` |
| `Background Informations/BACKGROUND.md` | 履歷敘事、STAR 故事 | `BACKGROUND.example.md` |
| `automation/job-apply/_internal/applicant_profile.json` | 表單欄位資料 | `applicant_profile.example.json` |
| `automation/job-apply/_internal/sop/_local/` | 各公司申請表的實際操作步驟 | `_local/README.md` |

再加上執行時產生的 `ledger.json`（投遞紀錄）、`today-jobs.md`、`interview_prep/<公司>/`,全部 ignore。

**Gmail 密碼不存在任何檔案裡** —— 放 macOS Keychain,腳本要寄信時才去讀。

> ⚠️ `.gitignore` 只擋**還沒被追蹤**的檔案。第一次 commit 前跑一次 `git status`,確認裡面沒有你的東西。

---

## 擴充到別的公司或產業

`automation/job-search/_internal/sources.json` 是唯一的公司清單來源。掃描器、Gmail 搜尋字串、SOP 覆蓋層命名全部從它衍生 —— 加一家公司只改這一個檔,不用碰任何 prompt 或程式碼。

ATS 平台目前支援 `workday`、`eightfold`、`sitecore_discover`。新平台要寫一個 adapter 函式加進 `scan_jobs.py`,以及一份 `sop/<platform>.md`。

公司特有的東西（那家的申請表問哪些問題、你的標準答案是什麼）放 `sop/_local/`,那層不公開。**平台通則往上放、公司特例往下放** —— 這條線是這個 repo 能公開的原因。

---

## 已知限制

- **macOS only** —— Keychain + launchd。Python 本身是可攜的,換 cron 和 secrets 檔就能跑 Linux。
- **ATS API 會變** —— 那些是各家網站自己前端在用的端點,沒有版本保證。壞了就是要重新抓封包。
- **Eightfold 全新帳號沒測過** —— 目前每次跑都是從既有的儲存 profile 自動填入。
- **繁體中文為主** —— 產出的文件、篩選條件、論壇搜尋都預設台灣求職情境。

---

## License

MIT — 見 [LICENSE](LICENSE)。
