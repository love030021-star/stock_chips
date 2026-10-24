"""讀取 data/store，計算推薦分數與各項排行，輸出網站使用的 JSON。

輸出：
  data/latest.json            最新一天的完整結果
  data/daily/YYYY-MM-DD.json  每天的結果（保留 60 天）
  data/history.json           歷史推薦清單（供「歷史清單」頁籤）
"""
from __future__ import annotations

import bisect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

TPE = timezone(timedelta(hours=8))
LOT = 1000  # 1 張 = 1,000 股

# ---- 推薦規則參數 ----
W_INST_TODAY = 25    # 法人當日買超 ÷ 5 日均量
W_INST_5D = 25       # 法人 5 日累計買超
W_STREAK = 10        # 外資、投信連續買超天數
W_BIG = 25           # 千張大戶持股比例週增減
W_MARGIN = 15        # 融資餘額減少
STREAK_CAP = 5
MIN_PRICE = 10.0
MIN_AVG_LOTS = 1000
BIG_EXCLUDE = 99.0
TOP_N = 20
HISTORY_DAYS = 60

MARKETS = {"twse": "上市", "tpex": "上櫃"}


def _load(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _dump(p: Path, obj, pretty=False):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False,
                            indent=1 if pretty else None,
                            separators=None if pretty else (",", ":")), encoding="utf-8")


def ranker(values):
    """回傳 f(x) = x 在 values 中的百分位 (0, 1]。"""
    vs = sorted(values)
    n = len(vs)
    if n == 0:
        return lambda x: 0.0
    return lambda x: bisect.bisect_right(vs, x) / n


def r1(x, d=1):
    return None if x is None else round(x, d)


def lots(shares):
    return None if shares is None else round(shares / LOT)


def streak(series, key):
    n = 0
    for rec in reversed(series):
        if rec is None or (rec.get(key) or 0) <= 0:
            break
        n += 1
    return n


def tdcc_pair(store: Path, today: str):
    """回傳 (最新快照日, 最新快照, 前一週快照日, 前一週快照)，日期不晚於 today。"""
    snaps = sorted(p for p in (store / "tdcc").glob("*.json") if p.stem <= today.replace("-", ""))
    cur = snaps[-1] if snaps else None
    prev = None
    if cur:
        for p in reversed(snaps[:-1]):
            if (datetime.strptime(cur.stem, "%Y%m%d") - datetime.strptime(p.stem, "%Y%m%d")).days >= 5:
                prev = p
                break
    fmt = lambda p: f"{p.stem[:4]}-{p.stem[4:6]}-{p.stem[6:]}" if p else None  # noqa: E731
    return fmt(cur), _load(cur, {}) if cur else {}, fmt(prev), _load(prev, {}) if prev else {}


def stock_rows(days, market, meta, tdcc_cur, tdcc_prev):
    """把每檔股票整理成計算用的列。"""
    today = days[-1][market]
    rows = []
    for code, t in today.items():
        series = [d[market].get(code) for d in days]
        last5 = [s for s in series[-5:] if s]
        prev4 = [s for s in series[-5:-1] if s]
        if t.get("c") is None or len(last5) < 3:
            continue
        c, ch = t["c"], t.get("ch") or 0
        prev_close = c - ch
        pct = ch / prev_close * 100 if prev_close else 0
        v5 = sum(s["v"] for s in last5) / len(last5)
        vp4 = sum(s["v"] for s in prev4) / len(prev4) if prev4 else None
        inst5 = sum(s.get("in") or 0 for s in last5)
        big = (tdcc_cur.get(code) or {}).get("big")
        big0 = (tdcc_prev.get(code) or {}).get("big")
        shares = t.get("sh") or (tdcc_cur.get(code) or {}).get("sh")
        mg, mgp = t.get("mg"), t.get("mgp")
        rows.append({
            "code": code, "market": market,
            "name": (meta.get(market, {}).get(code) or {}).get("name") or t.get("name", ""),
            "ind": (meta.get(market, {}).get(code) or {}).get("ind") or "其他",
            "c": c, "o": t.get("o"), "h": t.get("h"), "l": t.get("l"),
            "pct": pct, "v": t["v"], "a": t.get("a") or 0, "v5": v5, "vp4": vp4,
            "inst": t.get("in") or 0, "fn": t.get("fn") or 0, "tn": t.get("tn") or 0,
            "inst5": inst5, "fs": streak(series, "fn"), "ts": streak(series, "tn"),
            "big": big, "bigd": (big - big0) if (big is not None and big0 is not None) else None,
            "mg": mg, "mgd": (mg - mgp) if (mg is not None and mgp is not None) else None,
            "mgpct": ((mg - mgp) / mgp * 100) if (mg is not None and mgp) else None,
            "shares": shares,
            "turn": (t["v"] / shares * 100) if shares else None,
            "dt": t.get("dt"),
        })
    return rows


# ---------------- 推薦 ----------------

def pick_reasons(r):
    out = [f"法人買超 {lots(r['inst']):,} 張（均量 {r['inst'] / r['v5'] * 100:.1f}%）"]
    if r["inst5"] > 0:
        out.append(f"5 日累計 {lots(r['inst5']):,} 張")
    if r["fs"] >= 2:
        out.append(f"外資連買 {r['fs']} 天")
    if r["ts"] >= 2:
        out.append(f"投信連買 {r['ts']} 天")
    if r["bigd"] is not None and r["bigd"] > 0:
        out.append(f"千張大戶 +{r['bigd']:.2f}%")
    if r["mgpct"] is not None and r["mgpct"] < 0:
        out.append(f"融資 {r['mgpct']:.1f}%")
    return out


def score_picks(rows):
    cand = [r for r in rows
            if r["c"] >= MIN_PRICE and r["v5"] >= MIN_AVG_LOTS * LOT and r["inst"] > 0
            and not (r["big"] is not None and r["big"] >= BIG_EXCLUDE)]
    if not cand:
        return []
    p1 = ranker([r["inst"] / r["v5"] for r in cand])
    p2 = ranker([r["inst5"] / r["v5"] for r in cand if r["inst5"] > 0])
    p4 = ranker([r["bigd"] for r in cand if r["bigd"] is not None and r["bigd"] > 0])
    p5 = ranker([-r["mgpct"] for r in cand if r["mgpct"] is not None and r["mgpct"] < 0])
    out = []
    for r in cand:
        s1 = W_INST_TODAY * p1(r["inst"] / r["v5"])
        s2 = W_INST_5D * p2(r["inst5"] / r["v5"]) if r["inst5"] > 0 else 0
        s3 = W_STREAK * min(max(r["fs"], r["ts"]), STREAK_CAP) / STREAK_CAP
        s4 = W_BIG * p4(r["bigd"]) if (r["bigd"] or 0) > 0 else 0
        s5 = W_MARGIN * p5(-r["mgpct"]) if (r["mgpct"] or 0) < 0 else 0
        total = s1 + s2 + s3 + s4 + s5
        out.append({
            "code": r["code"], "name": r["name"], "ind": r["ind"], "market": r["market"],
            "close": r["c"], "pct": r1(r["pct"], 2), "vol": lots(r["v"]),
            "amt": r1(r["a"] / 1e8, 2), "score": r1(total),
            "parts": [r1(s1), r1(s2), r1(s3), r1(s4), r1(s5)],
            "inst": lots(r["inst"]), "inst5": lots(r["inst5"]),
            "fs": r["fs"], "ts": r["ts"], "big": r1(r["big"], 2), "bigd": r1(r["bigd"], 2),
            "mgpct": r1(r["mgpct"], 2), "reasons": pick_reasons(r),
        })
    out.sort(key=lambda x: (-x["score"], -x["inst"]))
    for i, x in enumerate(out[:TOP_N], 1):
        x["rank"] = i
    return out[:TOP_N]


# ---------------- 當沖熱門 ----------------

def close_pos(r):
    h, l, c = r["h"], r["l"], r["c"]
    if h is None or l is None:
        return 0.5
    if h == l:
        return 1.0 if r["pct"] > 0 else 0.0 if r["pct"] < 0 else 0.5
    return (c - l) / (h - l)


def dt_item(r, score, reasons, tag=None):
    return {
        "code": r["code"], "name": r["name"], "ind": r["ind"], "market": r["market"],
        "close": r["c"], "pct": r1(r["pct"], 2), "turn": r1(r["turn"], 2),
        "dtr": r1(r["dt"] / r["v"] * 100 if r["v"] else None, 1),
        "volx": r1(r["volx"], 2), "inst": lots(r["inst"]), "vol": lots(r["v"]),
        "score": r1(score), "reasons": reasons, "tag": tag,
    }


def score_daytrade(rows):
    cand = []
    for r in rows:
        if (r["c"] < MIN_PRICE or r["v"] < MIN_AVG_LOTS * LOT or not r["dt"]
                or not r["vp4"] or r["turn"] is None):
            continue
        r = dict(r)
        r["volx"] = r["v"] / r["vp4"]
        r["cp"] = close_pos(r)
        r["press"] = r["pct"] >= 5 and r["volx"] >= 2 and r["cp"] >= 0.8 and r["inst"] <= 0
        cand.append(r)

    longs = [r for r in cand if r["pct"] > 0 and not r["press"]]
    shorts = [r for r in cand if r["pct"] < 0 or r["press"]]

    def common(rs):
        return ranker([r["volx"] for r in rs]), ranker([r["turn"] for r in rs])

    out_long = []
    if longs:
        pv, pt = common(longs)
        pp = ranker([r["pct"] for r in longs])
        pi = ranker([r["inst"] / r["v"] for r in longs if r["inst"] > 0])
        for r in longs:
            s = (30 * pp(r["pct"]) + 20 * pv(r["volx"]) + 20 * pt(r["turn"])
                 + 15 * r["cp"] + (15 * pi(r["inst"] / r["v"]) if r["inst"] > 0 else 0))
            why = [f"漲 {r['pct']:.1f}%", f"量增 {r['volx']:.1f} 倍", f"週轉 {r['turn']:.1f}%"]
            if r["cp"] >= 0.8:
                why.append("收在高點附近")
            if r["inst"] > 0:
                why.append(f"法人買 {lots(r['inst']):,} 張")
            out_long.append(dt_item(r, s, why))

    out_short = []
    if shorts:
        pv, pt = common(shorts)
        down = [r for r in shorts if not r["press"]]
        pd = ranker([-r["pct"] for r in down])
        pi = ranker([-r["inst"] / r["v"] for r in shorts if r["inst"] < 0])
        for r in shorts:
            if r["press"]:
                s = 30 + 20 * pv(r["volx"]) + 20 * pt(r["turn"]) + 15 * r["cp"] + 15
                why = [f"漲 {r['pct']:.1f}% 收高", f"量增 {r['volx']:.1f} 倍", "法人未買超"]
                out_short.append(dt_item(r, s, why, "隔日沖賣壓"))
            else:
                s = (30 * pd(-r["pct"]) + 20 * pv(r["volx"]) + 20 * pt(r["turn"])
                     + 15 * (1 - r["cp"]) + (15 * pi(-r["inst"] / r["v"]) if r["inst"] < 0 else 0))
                why = [f"跌 {abs(r['pct']):.1f}%", f"量增 {r['volx']:.1f} 倍", f"週轉 {r['turn']:.1f}%"]
                if r["cp"] <= 0.2:
                    why.append("收在低點附近")
                if r["inst"] < 0:
                    why.append(f"法人賣 {abs(lots(r['inst'])):,} 張")
                out_short.append(dt_item(r, s, why))

    for lst in (out_long, out_short):
        lst.sort(key=lambda x: -x["score"])
        del lst[TOP_N:]
        for i, x in enumerate(lst, 1):
            x["rank"] = i
    return out_long, out_short


# ---------------- 成交量／週轉率 ----------------

def rank_simple(rows, key):
    rs = [r for r in rows if r.get(key) is not None]
    rs.sort(key=lambda r: -r[key])
    return [{
        "rank": i, "code": r["code"], "name": r["name"], "ind": r["ind"],
        "close": r["c"], "pct": r1(r["pct"], 2), "vol": lots(r["v"]),
        "amt": r1(r["a"] / 1e8, 2), "turn": r1(r["turn"], 2),
    } for i, r in enumerate(rs[:TOP_N], 1)]


# ---------------- 主流程 ----------------

def build_site(root: Path):
    store = root / "data" / "store"
    files = sorted((store / "daily").glob("*.json"))
    if not files:
        print("[build] store 沒有資料，略過")
        return
    days = [_load(p) for p in files[-10:]]
    today = days[-1]["date"]
    meta = _load(store / "meta.json", {})
    tcur_d, tcur, tprev_d, tprev = tdcc_pair(store, today)

    result = {
        "date": today,
        "generated_at": datetime.now(TPE).isoformat(timespec="minutes"),
        "trading_days": len(days),
        "tdcc": {"date": tcur_d, "prev": tprev_d},
        "index": days[-1].get("index", {}),
        "picks": {}, "volume": {}, "turnover": {}, "daytrade": {},
    }
    all_rows = []
    for m in MARKETS:
        rows = stock_rows(days, m, meta, tcur, tprev)
        all_rows += rows
        result["picks"][m] = score_picks(rows)
        result["volume"][m] = rank_simple(rows, "v")
        result["turnover"][m] = rank_simple(rows, "turn")
        up = sum(1 for r in rows if r["pct"] > 0)
        down = sum(1 for r in rows if r["pct"] < 0)
        result.setdefault("breadth", {})[m] = {"up": up, "down": down, "total": len(rows)}
    longs, shorts = score_daytrade(all_rows)
    result["daytrade"] = {"long": longs, "short": shorts}

    data = root / "data"
    _dump(data / "latest.json", result)
    _dump(data / "daily" / f"{today}.json", result)
    for p in sorted((data / "daily").glob("*.json"))[:-HISTORY_DAYS]:
        p.unlink()

    # 歷史清單：每天的推薦 + 最新收盤，前端據此計算推薦後報酬
    hist = []
    for p in sorted((data / "daily").glob("*.json")):
        d = _load(p)
        hist.append({"date": d["date"], "picks": {
            m: [[x["code"], x["name"], x["close"], x["score"]] for x in d["picks"].get(m, [])]
            for m in MARKETS}})
    codes = {(m, x[0]) for h in hist for m in MARKETS for x in h["picks"][m]}
    last_close = {m: {} for m in MARKETS}
    for m, c in codes:
        rec = days[-1][m].get(c)
        if rec and rec.get("c") is not None:
            last_close[m][c] = rec["c"]
    _dump(data / "history.json", {"date": today, "days": hist[::-1], "last_close": last_close})
    print(f"[build] {today} 完成：上市推薦 {len(result['picks']['twse'])}、"
          f"上櫃推薦 {len(result['picks']['tpex'])}、當沖 {len(longs)}/{len(shorts)}")
