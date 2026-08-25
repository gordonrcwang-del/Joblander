# 求職條件

*這份檔案同時給人看、也給程式讀（automation/job-search/_internal/scan_jobs.py）。改條件只改這裡。*
*格式規則：`## 標題` 括號裡的英文代號不要改；每個條件一行，用 `- ` 開頭。*
*一行裡用 `|` 分隔的是同義詞，任一個對到就算符合。*

## 地點允許 (allow_locations)
- 台北 | Taipei
- 新北 | New Taipei | Xinbei | 林口 | Linkou | 汐止 | Xizhi
- 桃園 | Taoyuan | 龜山 | Guishan
- 新竹 | Hsinchu | Zhubei | 竹北 | 竹南
- 台中 | Taichung

## 年資要求 (years_threshold)
- reject_if_min_years_at_least: 3

## 學歷要求 (degree_threshold)
- highest_degree_held: master

## 教育背景接受 (accept_education_fields)
- chemical engineering | 化學工程 | 化工
- materials science | materials engineering | material science | material engineering | 材料科學 | 材料工程 | 材料系

## 職稱排除 (reject_titles)
- technician | 技術員 | 技術士
- intern | internship | 實習
- manager | director | supervisor | head of | 經理 | 課長 | 處長
- assistant engineer | assistant applications engineer | assistant application engineer | assistant customer service engineer | 助理工程師
- associate engineer
- UIR | Upgrade Install and Relocation
- field service engineer | field service | 現場服務工程師
- customer service engineer | customer support engineer | CSE | 客戶服務工程師 | 客服工程師
