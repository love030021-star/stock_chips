"""盤中即時籌碼：每 20 秒抓期交所行情網的台指週選各履約價與台指期，寫成 live 分支的 txo.json。

Cloudflare Worker 連期交所會被擋（520），所以改在 GitHub Actions 上抓。
txo.json：o＝最新一筆（各履約價成交價、量、最佳買賣），series＝今日每分鐘價平與成交量，
snaps＝最近 30 分鐘每分鐘的各履約價成交量（網頁算 15 分鐘增量用）。
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

TPE = timezone(timedelta(hours=8))
URL = "https://mis.taifex.com.tw/futures/api/getQuoteList"
OUT = Path("live_out")


def post(mt, sym, cid):
    body = json.dumps({"MarketType": mt, "SymbolType": sym, "KindID": "1", "CID": cid, "ExpireMonth": "",
                       "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return (json.load(r).get("RtData") or {}).get("QuoteList") or []


def sess(now):
    m = now.hour * 60 + now.minute
    return "0" if 520 <= m <= 830 else "1"


def open_now(now):
    m, wd = now.hour * 60 + now.minute, now.weekday()   # 週一=0
    day, night = 525 <= m <= 825, m >= 900 or m <= 300
    if wd == 6 or (wd == 5 and m > 300) or (wd == 0 and m < 525):
        return False
    return day or night


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def build(now):
    mt = sess(now)
    L = post(mt, "O", "TXO")
    G, tm = {}, ""
    for x in L:
        m = re.match(r"^[A-Z0-9]{3}(\d{5,6})([A-X])\d", x.get("SymbolID") or "")
        e = re.search(r"(\d{4})/(\d{2})/(\d{2})", x.get("DispEName") or "")
        if not m or not e:
            continue
        exp, k, cp = f"{e[1]}-{e[2]}-{e[3]}", int(m[1]), "C" if m[2] <= "L" else "P"
        g = G.setdefault(exp, {"exp": exp, "name": x.get("DispCName"), "vol": 0, "s": {}})
        v = int(f(x.get("CTotalVolume")))
        g["vol"] += v
        g["s"].setdefault(k, {})[cp] = [f(x.get("CLastPrice")), v, f(x.get("CRefPrice")), f(x.get("CBestBidPrice")),
                                        f(x.get("CBestAskPrice")), int(f(x.get("CBestBidSize"))), int(f(x.get("CBestAskSize")))]
        t = (x.get("CDate") or "") + (x.get("CTime") or "")
        if x.get("CTime") and t > tm:
            tm = t
    today = now.strftime("%Y-%m-%d")
    lst = sorted([g for g in G.values() if g["exp"] >= today], key=lambda g: g["exp"])
    fut = None
    try:
        F = post(mt, "F", "TXF")
        q = next((x for x in F if re.search(r"-F$", x.get("SymbolID") or "") and f(x.get("CLastPrice"))), None) or \
            next((x for x in F if (x.get("SymbolID") or "").startswith("TXF") and not (x.get("SymbolID") or "").endswith("-S")
                  and f(x.get("CLastPrice"))), None)
        if q:
            fut = {"px": f(q["CLastPrice"]), "chg": f(q.get("CDiffRate")), "d": q.get("CDate"), "t": q.get("CTime")}
    except Exception as e:  # noqa: BLE001
        print("期貨讀取失敗", e, file=sys.stderr)
    books = {}
    for g in lst[:4]:
        best, bd = None, 1e18
        for k, r in g["s"].items():
            c, p = r.get("C"), r.get("P")
            if c and p and c[0] and p[0] and abs(c[0] - p[0]) < bd:
                bd, best = abs(c[0] - p[0]), k
        px = (fut or {}).get("px") or best or 0
        rows = [[k, g["s"][k].get("C"), g["s"][k].get("P")] for k in sorted(g["s"]) if abs(k - px) <= 3000]
        books[g["exp"]] = {"ok": True, "mt": mt, "t": tm, "exp": g["exp"], "name": g["name"], "atm": best, "fut": fut, "rows": rows}
    main = next((g for g in lst if g["vol"] > 0), lst[0] if lst else None)
    return {"main": main["exp"] if main else None, "list": [[g["exp"], g["name"], g["vol"]] for g in lst], "books": books}


def push():
    subprocess.run("cd live_out && git add -A && git commit -q --amend -m live --allow-empty && git push -q -f origin HEAD:live",
                   shell=True, check=False)


def main():
    end = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 340) * 60
    state = {"series": [], "snaps": {}}
    p = OUT / "txo.json"
    if p.exists():
        try:
            old = json.loads(p.read_text())
            state["series"], state["snaps"] = old.get("series", []), old.get("snaps", {})
        except Exception:  # noqa: BLE001
            pass
    last_min = None
    while time.time() < end:
        now = datetime.now(TPE)
        once = len(sys.argv) > 2 and sys.argv[2] == "once"
        if not open_now(now) and not once:
            time.sleep(60)
            continue
        try:
            data = build(now)
        except Exception as e:  # noqa: BLE001
            print("讀取失敗", e, file=sys.stderr)
            time.sleep(20)
            continue
        b = data["books"].get(data["main"])
        t = int(time.time() // 60 * 60)
        if b and t != last_min:
            last_min = t
            cv = sum(r[1][1] for r in b["rows"] if r[1])
            pv = sum(r[2][1] for r in b["rows"] if r[2])
            state["series"].append([t, b["exp"], (b["fut"] or {}).get("px"), b["atm"], cv, pv])
            state["series"] = [s for s in state["series"] if s[0] > t - 86400]
            state["snaps"][str(t)] = {"exp": b["exp"], "d": {r[0]: [r[1][1] if r[1] else 0, r[2][1] if r[2] else 0] for r in b["rows"]}}
            state["snaps"] = {k: v for k, v in state["snaps"].items() if int(k) > t - 1860}
        out = dict(data, series=state["series"], snaps=state["snaps"], updated=int(time.time()))
        p.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
        push()
        if once:
            break
        time.sleep(max(5, 20 - (time.time() % 20)))


if __name__ == "__main__":
    main()
