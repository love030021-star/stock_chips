"""每 5 分鐘抓一次期貨最新價（沒有可嵌入網頁的即時報價的商品），寫成 live.json。

輸出格式：{"at": 台北時間, "q": {市場id: {"px", "chg", "t"}}}
"""
import json
import sys
from datetime import datetime, timedelta, timezone

import markets

TPE = timezone(timedelta(hours=8))
SYMS = {"MNQ": "MNQ=F", "GOLD": "GC=F", "BRENT": "BZ=F"}


def main(out):
    q = {}
    for mid, sym in SYMS.items():
        try:
            j = markets._yahoo_json(sym, {"interval": "1m", "range": "1d", "includePrePost": "true"})
            r = j["chart"]["result"][0]
            meta = r["meta"]
            closes = r["indicators"]["quote"][0]["close"]
            ts = r.get("timestamp") or []
            px, t = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
            for i in range(len(closes) - 1, -1, -1):
                if closes[i] is not None:
                    if ts[i] >= (t or 0):
                        px, t = closes[i], ts[i]
                    break
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            q[mid] = {"px": round(px, 2), "chg": round((px / prev - 1) * 100, 2) if prev else None,
                      "t": datetime.fromtimestamp(t, TPE).strftime("%m/%d %H:%M")}
        except Exception as e:  # noqa: BLE001
            print(mid, "失敗：", e)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"at": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"), "q": q}, f, ensure_ascii=False)
    print(q)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "live.json")
