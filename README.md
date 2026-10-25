# 波浪籌碼日報

每天收盤後自動更新的靜態網站（GitHub Pages）：

- **波浪推算**：台指期（期交所，全日盤）、加權指數、櫃買指數、小娜（MNQ）、1oz 黃金（1OZ）、布蘭特原油（BZ）的日線與 60／30／5 分 K 艾略特波浪推算。
- **波段逆勢布局**：日線、60 分、30 分各自數推動浪，回檔分三層（強勢回檔、回檔滿足 1、回檔滿足 2），用較小週期的 123 法則進場，停損設在波浪失效價外（失效停損），附圖解。
- **台指選擇權籌碼**：三大法人、大額交易人多空淨額、Call／Put 明細、合成多空、未平倉支撐壓力與規則判讀。

## 檔案

| 路徑 | 用途 |
|---|---|
| `index.html`、`assets/` | 網站頁面 |
| `scripts/update.py` | 每日流程 |
| `scripts/txf.py` | 台指期資料（期交所）與波浪推算核心 |
| `scripts/markets.py` | 其他商品資料（證交所、櫃買中心、TradingView 匯入檔）與推算 |
| `scripts/tv_import.py` | 把 Claude 對話紀錄中 TradingView 連接器回傳的 K 線存成 `data/store/tv/*.json` |
| `scripts/strategy.py` | 波段逆勢布局規則 |
| `scripts/chips.py` | 台指選擇權籌碼 |
| `data/` | 網站讀取的結果與原始資料 |

## 排程

台北時間週一到週五 21:47、23:17（期交所盤後資料），以及週二到週六 06:17（美股收盤後）。

## 注意

- 小娜、黃金、布蘭特原油與櫃買指數盤中 K 線來自 TradingView，GitHub Actions 無法直接取得，需在 Claude 對話中用 TradingView 連接器抓取後執行 `tv_import.py` 再推送。
- Yahoo Finance 會擋 GitHub Actions（429），只在沒有 TradingView 檔案時才嘗試。
- 本站不是證券或期貨投資顧問，內容只是規則計算結果，不構成投資建議。
