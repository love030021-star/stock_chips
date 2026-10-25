# 籌碼選股日報

每個交易日盤後，自動抓取證交所、櫃買中心、集保結算所的公開資料，依三大法人、千張大戶與融資變化計分，上市、上櫃各列出 20 檔，另有當沖熱門、成交量、週轉率排行與歷史清單。網站是純靜態頁面，放在 GitHub Pages，資料由 GitHub Actions 每天自動更新。

## 部署

1. 把整個資料夾推到一個 GitHub repo 的 `main` 分支。
2. repo 的 **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**。
3. **Actions** 分頁打開 workflow，手動執行一次「每日更新與部署」。第一次會往前補約 10 個交易日的資料（約 5 分鐘）。
4. 之後週一到週五台北時間 21:47 自動更新（23:17 再補跑一次，處理融資資料晚公布的情況）。

## 檔案

| 路徑 | 用途 |
|---|---|
| `index.html`、`assets/` | 網站頁面 |
| `scripts/sources.py` | 各資料源的抓取與欄位解析 |
| `scripts/update.py` | 每日流程：抓資料、存檔、補歷史、更新集保 |
| `scripts/build.py` | 計分與排行，權重和門檻在檔案開頭 |
| `data/store/` | 原始資料（保留 45 個交易日、8 週集保） |
| `data/latest.json`、`data/history.json`、`data/daily/` | 網站讀取的結果 |
| `scripts/txf.py` | 台指期日線與 5、30、60 分 K 抓取與波浪推算，輸出 `data/wave.json` |
| `tests/fake_run.py` | 用假資料跑完整流程的離線測試 |

## 調整規則

`scripts/build.py` 開頭的常數：`W_INST_TODAY`、`W_INST_5D`、`W_STREAK`、`W_BIG`、`W_MARGIN`（權重），`MIN_PRICE`、`MIN_AVG_LOTS`、`BIG_EXCLUDE`、`TOP_N`（篩選條件）。改完推上去，下一次排程就會用新規則；若要立刻重算，可在本機執行 `python scripts/update.py --build-only`。

## 台指期波浪推算

日線用期交所每日行情的日盤近月合約（第一次會補約兩年），5、30、60 分 K 用期交所「前 30 個交易日每筆成交」資料彙整日盤與夜盤，之後每天累積。轉折以 ATR 倍數判斷，再依艾略特波浪規則與費氏比例挑出主要與替代推算，`scripts/txf.py` 的 `analyze()` 可調整門檻倍數。

## 注意

- 集保股權分散表只提供最新一週，上線後要累積兩週才有「大戶週增減」分數，在那之前該項以 0 分計。
- 上市股票的週轉率以集保總股數作為發行股數的近似值；上櫃使用櫃買中心公布的發行股數。
- 本站不是證券投資顧問，名單僅為規則篩選結果，不構成投資建議。
