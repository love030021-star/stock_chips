"""每 5 分鐘抓一次期貨最新價（沒有可嵌入網頁的即時報價的商品），寫成 live.json。

輸出格式：{"at": 台北時間, "q": {市場id: {"px", "chg", "t"}}}
"""
import json
import sys
from datetime import datetime, timedelta, timezone

import markets

TPE = timezone(timedelta(hours=8))


CNBC = {"MNQ": "@NQ.1", "GOLD": "@GC.1", "BRENT": "@LCO.1"}


def cnbc(q, err):
    url = "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
    syms = "|".join(CNBC[m] for m in CNBC if m not in q)
    if not syms:
        return
    try:
        j = markets._get(url, {"symbols": syms, "requestMethod": "itv", "noform": "1", "partnerId": "2",
                               "fund": "1", "exthrs": "1", "output": "json"}, tries=2).json()
        rows = j["FormattedQuoteResult"]["FormattedQuote"]
        if isinstance(rows, dict):
            rows = [rows]
        for mid, sym in CNBC.items():
            for r in rows:
                if r.get("symbol") == sym and r.get("last"):
                    px = float(str(r["last"]).replace(",", ""))
                    chg = r.get("change_pct", "").replace("%", "").replace("+", "")
                    t = r.get("last_time") or ""
                    try:
                        t = datetime.fromisoformat(t.replace("Z", "+00:00")[:25]).astimezone(TPE).strftime("%m/%d %H:%M")
                    except ValueError:
                        t = datetime.now(TPE).strftime("%m/%d %H:%M")
                    q[mid] = {"px": px, "chg": float(chg) if chg not in ("", "UNCH") else 0.0, "t": t}
        miss = [m for m in CNBC if m not in q]
        if miss:
            err["miss"] = [{k: r.get(k) for k in ("symbol", "last", "code", "name")} for r in rows]
    except Exception as e:  # noqa: BLE001
        err["cnbc"] = str(e)[:300]


def main(out):
    q, err = {}, {}
    cnbc(q, err)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"at": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"), "q": q, "err": err}, f, ensure_ascii=False)
    print(q, err)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "live.json")
