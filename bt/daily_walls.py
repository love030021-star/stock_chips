"""每天盤後：抓期交所台指選擇權各履約價未平倉、三大法人選擇權、台指期收盤，
算出「最近的牆」策略參數（SP、SC）與表格用的摘要，輸出 walls_out/latest.json 與 history.json。
只在 GitHub Actions 上跑（期交所擋其他來源）。不動網站資料。"""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chips  # noqa: E402
import txf  # noqa: E402

OUT = Path("walls_out")
OUT.mkdir(exist_ok=True)
MIN_OI = 2000
TPE = timezone(timedelta(hours=8))


def parse_strikes_by_day(text):
    """-> {date: {contract: {"exp":..., "C":{k:oi}, "P":{k:oi}}}}（一般時段）"""
    out = {}
    for line in text.splitlines():
        c = [x.strip() for x in line.split(",")]
        if len(c) < 21 or c[1] != "TXO" or c[17] != "一般":
            continue
        oi, k = chips._i(c[11]), chips._i(c[3])
        if oi is None or k is None:
            continue
        d = c[0].replace("/", "-")
        cp = "C" if c[4] == "買權" else "P"
        rec = out.setdefault(d, {}).setdefault(c[2], {"exp": c[20], "C": {}, "P": {}})
        rec[cp][k] = oi
    return out


def label(name, exp):
    if len(name) == 6:
        return f"{name}月選", f"月選{name}({int(exp[4:6])}/{int(exp[6:])}到期)"
    return name, f"{name[6:]}({int(exp[4:6])}/{int(exp[6:])}到期)"


def max_pain(v):
    ks = sorted(set(v["C"]) | set(v["P"]))
    best = None
    for s in ks:
        pain = sum(oi * max(0, s - k) for k, oi in v["C"].items()) + sum(oi * max(0, k - s) for k, oi in v["P"].items())
        if best is None or pain < best[0]:
            best = (pain, s)
    return best[1] if best else None


def bands(close):
    base = int(close // 1000) * 1000
    calls = [(base - 1000 + i * 1000, base - 100 + i * 1000) for i in range(4)][::-1]
    puts = [(base - i * 1000, base + 900 - i * 1000) for i in range(4)]
    return calls, puts


def main():
    today = datetime.now(TPE).date()
    start = today - timedelta(days=10)
    s, e = start.strftime("%Y/%m/%d"), today.strftime("%Y/%m/%d")
    S = parse_strikes_by_day(chips._post("/cht/3/optDataDown", {"down_type": "1", "commodity_id": "TXO", "commodity_id2": "",
                                                                 "queryStartDate": s, "queryEndDate": e}))
    days = sorted(S)
    if not days:
        raise SystemExit("沒有選擇權資料")
    d, prev = days[-1], (days[-2] if len(days) > 1 else None)
    fut = txf.fetch_daily_range(start, today)
    close = fut[d][3] if d in fut else None
    inst = {}
    try:
        inst = chips.parse_inst(chips._post("/cht/3/callsAndPutsDateDown", {"queryStartDate": s, "queryEndDate": d.replace("-", "/"), "commodityId": "TXO"}))
    except Exception as ex:  # noqa: BLE001
        print("法人資料失敗", ex)
    d0 = d.replace("-", "")
    live = sorted([(k, v) for k, v in S[d].items() if v["exp"] > d0], key=lambda kv: kv[1]["exp"])
    if close is None:
        raise SystemExit("沒有台指期收盤價")

    # 策略參數：最近到期（未結算）契約，未平倉 >= 2000 口
    near_name, near = live[0]
    sp = max([k for k, oi in near["P"].items() if oi >= MIN_OI and k <= close], default=0)
    sc = min([k for k, oi in near["C"].items() if oi >= MIN_OI and k >= close], default=0)

    summary, zones = [], []
    calls, puts = bands(close)
    for name, v in live:
        short, longname = label(name, v["exp"])
        tc, tp = sum(v["C"].values()), sum(v["P"].values())
        summary.append({"contract": short, "call": tc, "put": tp, "pcr": round(tp / tc, 2) if tc else None, "maxpain": max_pain(v)})
        pv = S.get(prev, {}).get(name) if prev else None
        for cp, bl in (("Call", calls), ("Put", puts)):
            key = cp[0]
            for lo, hi in bl:
                cur = sum(oi for k, oi in v[key].items() if lo <= k <= hi)
                old = sum(oi for k, oi in pv[key].items() if lo <= k <= hi) if pv else None
                zones.append({"type": cp, "lo": lo, "hi": hi, "oi": cur, "chg": None if old is None else cur - old,
                              "prev": old, "pct": None if not old else round((cur - old) / old * 100, 1), "contract": longname})
    inr = {}
    for who in ("外資", "投信", "自營商"):
        r = inst.get(d, {}).get(who)
        if r:
            bull, bear = r["C"][0] + r["P"][1], r["C"][1] + r["P"][0]
            inr[who] = [bull, bear, bull - bear]
    res = {"date": d, "close": close, "sp": sp, "sc": sc, "chipdir": 0, "near": near_name, "near_exp": near["exp"],
           "summary": summary, "zones": zones, "inst": inr,
           "walls_near": {"P": sorted([[k, oi] for k, oi in near["P"].items() if oi >= MIN_OI]),
                          "C": sorted([[k, oi] for k, oi in near["C"].items() if oi >= MIN_OI])},
           "made": datetime.now(TPE).isoformat(timespec="minutes")}
    (OUT / "latest.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("date", "close", "sp", "sc", "near")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
