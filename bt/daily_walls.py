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
        rec = out.setdefault(d, {}).setdefault(c[2], {"exp": c[20], "C": {}, "P": {}, "Cpx": {}, "Ppx": {}})
        rec[cp][k] = oi
        px = chips._f(c[10]) if chips._f(c[10]) is not None else chips._f(c[8])
        if px is not None:
            rec[cp + "px"][k] = px
    return out


def parse_fut(text):
    out = {}
    names = {"外資及陸資": "外資", "投信": "投信", "自營商": "自營商"}
    for line in text.splitlines():
        c = [x.strip() for x in line.split(",")]
        if len(c) < 14 or c[1] != "臺股期貨" or c[2] not in names:
            continue
        v = chips._i(c[13])
        if v is not None:
            out.setdefault(c[0].replace("/", "-"), {})[names[c[2]]] = v
    return out


def chip_dir(days, large, inst, fut, rows_by_day, closes):
    """和回測相同的籌碼分數（外資期貨、外資選擇權、前十大特法日增減，PCR、SP~SC 位置、最大痛點）。"""
    prev, res = None, None
    for d in days:
        if d not in inst or d not in large or d not in fut or d not in closes:
            continue
        r = inst[d].get("外資", {})
        on = (r["C"][0] - r["C"][1]) - (r["P"][0] - r["P"][1]) if "C" in r and "P" in r else None
        L = large[d]
        c, p = L.get("C", {}).get("999999", {}), L.get("P", {}).get("999999", {})
        t10 = None
        if "1" in c and "1" in p:
            t10 = (c["1"][2] - c["1"][3]) - (p["1"][2] - p["1"][3])
        cur = {"fut": fut[d].get("外資"), "opt": on, "t10": t10}
        parts = {}
        if prev:
            for k, th in (("fut", 1000), ("opt", 1000), ("t10", 500)):
                if cur[k] is not None and prev[k] is not None:
                    dlt = cur[k] - prev[k]
                    parts[k] = 1 if dlt > th else -1 if dlt < -th else 0
        px, rows = closes[d], rows_by_day.get(d)
        if rows:
            near = [x for x in rows if abs(x[0] - px) <= 1500]
            cs = [x for x in near if x[0] >= px]
            ps = [x for x in near if x[0] <= px]
            if cs and ps:
                sc = max(cs, key=lambda x: x[1])[0]
                sp = max(ps, key=lambda x: x[2])[0]
                pos = (px - sp) / max(1, sc - sp)
                parts["pos"] = 1 if pos < 1 / 3 else -1 if pos > 2 / 3 else 0
            co, po = sum(x[1] for x in rows), sum(x[2] for x in rows)
            if co:
                pcr = po / co
                parts["pcr"] = 1 if pcr > 1.1 else -1 if pcr < 0.9 else 0

            def pnl(sx):
                return sum(x[1] * ((x[3] or 0) - max(0, sx - x[0])) + x[2] * ((x[4] or 0) - max(0, x[0] - sx)) for x in rows)
            cand = [x[0] for x in rows if abs(x[0] - px) <= 3000]
            if cand:
                pain = max(cand, key=pnl)
                parts["pain"] = 1 if px < pain - 200 else -1 if px > pain + 200 else 0
        score = sum(parts.values())
        res = {"date": d, "score": score, "parts": parts, "dir": 1 if score >= 2 else -1 if score <= -2 else 0}
        prev = cur
    return res


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
    try:
        large = chips.parse_large(chips._post("/cht/3/largeTraderOptDown", {"queryStartDate": s, "queryEndDate": d.replace("-", "/")}))
        futi = parse_fut(chips._post("/cht/3/futContractsDateDown", {"queryStartDate": s, "queryEndDate": d.replace("-", "/"), "commodityId": "TXF"}))
    except Exception as ex:  # noqa: BLE001
        large, futi = {}, {}
        print("大額／期貨法人失敗", ex)
    rows_by_day = {}
    for dd in days:
        dd0 = dd.replace("-", "")
        nl = sorted([(k, v) for k, v in S[dd].items() if v["exp"] > dd0], key=lambda kv: kv[1]["exp"])
        if nl:
            v = nl[0][1]
            ks = sorted(set(v["C"]) | set(v["P"]))
            rows_by_day[dd] = [[k, v["C"].get(k, 0), v["P"].get(k, 0), v["Cpx"].get(k), v["Ppx"].get(k)] for k in ks]
    closes = {k: v[3] for k, v in fut.items()}
    cd = chip_dir(days, large, inst, futi, rows_by_day, closes)
    d0 = d.replace("-", "")
    live = sorted([(k, v) for k, v in S[d].items() if v["exp"] > d0], key=lambda kv: kv[1]["exp"])
    if close is None:
        raise SystemExit("沒有台指期收盤價")

    # 策略參數：最近到期（未結算）契約，未平倉 >= 2000 口
    near_name, near = live[0]
    sp = max([k for k, oi in near["P"].items() if oi >= MIN_OI and k <= close], default=0)
    sc = min([k for k, oi in near["C"].items() if oi >= MIN_OI and k >= close], default=0)
    # 和回測相同：未平倉 >= 2000 口的 Put、Call 牆各取離收盤最近的 4 道（不足補 0），盤中由腳本挑現價 600 點內最近的
    def near4(side):
        ks = [k for k, oi in near[side].items() if oi >= MIN_OI]
        ks = sorted(sorted(ks, key=lambda k: abs(k - close))[:4])
        return ks + [0] * (4 - len(ks))
    sp4, sc4 = near4("P"), near4("C")

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
    res = {"date": d, "close": close, "sp": sp, "sc": sc, "sp4": sp4, "sc4": sc4,
           "chipdir": cd["dir"] if cd and cd["date"] == d else None, "chip": cd, "near": near_name, "near_exp": near["exp"],
           "summary": summary, "zones": zones, "inst": inr,
           "walls_near": {"P": sorted([[k, oi] for k, oi in near["P"].items() if oi >= MIN_OI]),
                          "C": sorted([[k, oi] for k, oi in near["C"].items() if oi >= MIN_OI])},
           "made": datetime.now(TPE).isoformat(timespec="minutes")}
    (OUT / "latest.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("date", "close", "sp4", "sc4", "chipdir", "chip", "near")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
