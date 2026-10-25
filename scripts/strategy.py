"""依波浪推算、選擇權籌碼與未平倉，用固定規則產生下單策略（data/strategy.json）。

  當沖：5 分、30 分波浪為主，籌碼為輔，風險用 30 分 K ATR。
  波段：60 分、日線波浪為主，籌碼權重較高，風險用 60 分 K ATR。
  選擇權：用最近週選（跳過隔天到期者）的結算價計算權利金。

規則產生的價位只是參考，不是投資建議。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import txf

TPE = timezone(timedelta(hours=8))
PT = {"大台": 200, "小台": 50, "微台": 10}   # 每點新台幣
OPT_PT = 50                                 # 台指選擇權每點 50 元


def _load(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _dump(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def r50(x):
    return int(round(x / 50.0) * 50) if x is not None else None


def cur_atr(bars_map):
    keys = sorted(bars_map)[-200:]
    bars = [[k] + bars_map[k][:4] for k in keys]
    return txf.atr(bars)[-1] if len(bars) > 20 else None


def count_dir(c, last):
    nxt = next((t for t in c["targets"] if not t["reached"]), None)
    if nxt:
        return (1 if nxt["price"] > last else -1), nxt
    return (1 if c["invalid"]["price"] < last else -1), None


def bias(W, chips, spec):
    """spec: [(tf, 第幾個推算, 權重)]；回傳 (分數, 理由清單)。"""
    score, why = 0.0, []
    for tf, i, w in spec:
        R = W.get(tf)
        if not R or len(R["counts"]) <= i:
            continue
        c = R["counts"][i]
        d, _ = count_dir(c, R["last"]["c"])
        v = d * w * c["confidence"] / 100
        score += v
        why.append({"src": f"{R['name']}{'主要' if i == 0 else '替代'}推算", "text": f"{c['now']}（{c['pattern']}）",
                    "dir": d})
    return score, why


def chips_dir(chips):
    if not chips:
        return 0, None
    a = chips["analysis"]
    d = 1 if a["verdict"] == "偏多" else -1 if a["verdict"] == "偏空" else 0
    return d, {"src": "選擇權籌碼", "text": a["title"], "dir": d}


def levels(W, tfs, d, entry, atr):
    """從各週期推算收集方向一致的目標與失效價。"""
    tg, inv = [], []
    for tf in tfs:
        R = W.get(tf)
        if not R:
            continue
        for c in R["counts"]:
            cd, _ = count_dir(c, R["last"]["c"])
            if cd != d:
                continue
            for t in c["targets"]:
                if (t["price"] - entry) * d >= 0.8 * atr:
                    tg.append((t["price"], f"{R['name']}：{t['name']}"))
            iv = c["invalid"]["price"]
            if (entry - iv) * d > 0:
                inv.append((iv, f"{R['name']}推算失效價"))
    tg.sort(key=lambda x: (x[0] - entry) * d)
    inv.sort(key=lambda x: (entry - x[0]) * d)
    return tg, inv


def fut_plan(name, W, tfs, d, last, atr, stop_mult, why, min_rr=1.2):
    plan = {"name": name, "why": why, "atr": round(atr)}
    if d == 0:
        # 區間：等突破
        hi, lo = None, None
        for tf in tfs:
            R = W.get(tf)
            if not R:
                continue
            ph = [p[1] for p in R["pivots"] if p[2] == "H"][-1:]
            pl = [p[1] for p in R["pivots"] if p[2] == "L"][-1:]
            hi = ph[0] if ph and (hi is None or ph[0] < hi) and ph[0] > last else hi
            lo = pl[0] if pl and (lo is None or pl[0] > lo) and pl[0] < last else lo
        hi = hi or last + 1.5 * atr
        lo = lo or last - 1.5 * atr
        plan.update({
            "dir": "區間", "action": "方向不明，不追價，等突破區間再進場",
            "triggers": [
                {"side": "做多", "entry": r50(hi + 0.1 * atr), "stop": r50(hi - stop_mult * atr * 0.6),
                 "target": r50(hi + 2 * atr), "note": f"站上區間上緣 {hi:,.0f}"},
                {"side": "做空", "entry": r50(lo - 0.1 * atr), "stop": r50(lo + stop_mult * atr * 0.6),
                 "target": r50(lo - 2 * atr), "note": f"跌破區間下緣 {lo:,.0f}"},
            ],
        })
        for t in plan["triggers"]:
            t["risk_pts"] = abs(t["entry"] - t["stop"])
            t["risk"] = {k: t["risk_pts"] * v for k, v in PT.items()}
        return plan
    zone = sorted([r50(last), r50(last - d * 0.5 * atr)])
    entry = (zone[0] + zone[1]) / 2
    tg, inv = levels(W, tfs, d, entry, atr)
    stop, stop_src = None, ""
    for iv, src in inv:
        dist = (entry - iv) * d
        if 0.8 * atr <= dist <= 3 * atr:
            stop, stop_src = iv - d * 0.15 * atr, f"{src} {iv:,.0f} 再多留 0.15 ATR"
            break
    if stop is None:
        stop, stop_src = entry - d * stop_mult * atr, f"{stop_mult:g} 倍 ATR"
    t1 = tg[0] if tg else (entry + d * 1.5 * atr, "1.5 倍 ATR")
    t2 = next((t for t in tg[1:] if (t[0] - t1[0]) * d >= 0.5 * atr), None) or (t1[0] + d * 1.5 * atr, "再加 1.5 倍 ATR")
    risk = abs(entry - stop)
    rr = abs(t1[0] - entry) / risk if risk else 0
    # 風險報酬不足時，算出停損距離不變下、能達到 min_rr 的進場價
    better = None
    if risk and rr < min_rr:
        better = r50(t1[0] - d * min_rr * risk)
    plan.update({
        "dir": "做多" if d > 0 else "做空",
        "zone": zone, "entry": r50(entry),
        "stop": r50(stop), "stop_src": stop_src,
        "t1": r50(t1[0]), "t1_src": t1[1], "t2": r50(t2[0]), "t2_src": t2[1],
        "rr": round(rr, 2), "risk_pts": r50(risk) or round(risk),
        "risk": {k: round(risk) * v for k, v in PT.items()},
        "action": (f"{'拉回' if d > 0 else '反彈'}到 {zone[0]:,}–{zone[1]:,} 分批{'買進' if d > 0 else '放空'}"
                   if rr >= min_rr else f"風險報酬比只有 {rr:.2f}，等{'拉回到' if d > 0 else '反彈到'} {better:,} {'以下' if d > 0 else '以上'}再{'買' if d > 0 else '空'}，否則觀望"),
        "ok": rr >= min_rr, "better": better,
        "exit": f"到目標 1 先出一半，停損移到進場價；收盤{'跌破' if d > 0 else '站上'}停損價全部出場。",
    })
    return plan


# ---------------- 選擇權 ----------------

def pick_weekly(strikes, d0):
    skip = (datetime.strptime(d0, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
    wk = [(k, v) for k, v in strikes.items() if not k.isdigit() and v["exp"] > skip]
    mo = [(k, v) for k, v in strikes.items() if k.isdigit() and v["exp"] > skip]
    if wk:
        return min(wk, key=lambda kv: kv[1]["exp"])
    return min(mo, key=lambda kv: kv[1]["exp"]) if mo else (None, None)


def px_of(v, cp, k):
    return v.get(cp + "px", {}).get(str(k))


def near_strike(v, cp, target, side=0):
    """有權利金的履約價中，最接近 target 的；side=1 只找 >= target，-1 只找 <= target。"""
    ks = sorted(int(k) for k, p in v.get(cp + "px", {}).items() if p and p > 0)
    if side > 0:
        ks = [k for k in ks if k >= target]
    elif side < 0:
        ks = [k for k in ks if k <= target]
    return min(ks, key=lambda k: abs(k - target)) if ks else None


def opt_plans(strikes, d0, d, last, swing, chips):
    name, v = pick_weekly(strikes, d0)
    if not v:
        return None
    exp = f"{v['exp'][4:6]}/{v['exp'][6:]}"
    out = {"contract": name, "exp": exp, "plans": []}
    t1 = swing.get("t1")
    # 買方
    if d != 0:
        cp = "C" if d > 0 else "P"
        k = near_strike(v, cp, last + d * 100, side=d)
        p = px_of(v, cp, k) if k else None
        if k and p:
            be = k + p if d > 0 else k - p
            val_t1 = max(0, (t1 - k) * d) if t1 else None
            out["plans"].append({
                "kind": "買方", "title": f"買進 {exp} {k:,} {'Call' if d > 0 else 'Put'}",
                "legs": [["買", cp, k, p]],
                "cost": round(p * OPT_PT), "max_loss": round(p * OPT_PT), "breakeven": round(be),
                "note": (f"標的到目標 {t1:,} 時內含價值約 {val_t1:,.0f} 點。" if val_t1 is not None else "")
                        + "權利金虧損一半或標的觸及停損價時出場。",
            })
    # 賣方／價差
    walls = (chips or {}).get("walls", {}).get("weekly") if chips else None
    width = 300

    min_dist = 1.5 * swing["atr"]   # 短腳至少離現價 1.5 倍 60 分 ATR

    def one(cp, sk):
        lk = near_strike(v, cp, sk + (width if cp == "C" else -width))
        if not lk or lk == sk:
            return None
        ps, pl = px_of(v, cp, sk), px_of(v, cp, lk)
        if not ps or not pl or ps <= pl:
            return None
        return {"legs": [["賣", cp, sk, ps], ["買", cp, lk, pl]], "credit": ps - pl, "w": abs(lk - sk), "sk": sk}

    def spread(cp, wall_k):
        """短腳從未平倉大量區往現價移動，找第一個權利金達價差寬度 20% 的履約價。"""
        ks = sorted(int(k) for k, p in v.get(cp + "px", {}).items() if p and p > 0)
        if cp == "C":
            ks = [k for k in ks if last + min_dist <= k <= max(wall_k, last + min_dist)][::-1]
        else:
            ks = [k for k in ks if min(wall_k, last - min_dist) <= k <= last - min_dist]
        best = None
        for k in ks:
            r = one(cp, k)
            if not r:
                continue
            best = best or r
            if r["credit"] / r["w"] >= 0.2:
                r["moved"] = k != ks[0]
                return r
            best = r
        if best:
            best["weak"] = True
        return best

    def sp_note(s, cp):
        ratio = s["credit"] / s["w"]
        base = (f"短腳 {s['sk']:,}" + ("（未平倉大量區權利金太少，往現價移）" if s.get("moved") else "（未平倉大量區附近）")
                + f"，收入是寬度的 {ratio:.0%}。")
        if s.get("weak"):
            base += "權利金偏少、風險報酬差，不建議。"
        return base + f"收到權利金一半時獲利出場；標的{'站上' if cp == 'C' else '跌破'} {s['sk']:,} 停損或轉倉。"

    res_k = min((k for k, _ in walls["res"]), default=None) if walls and walls.get("res") else r50(last + 2 * swing["atr"])
    sup_k = max((k for k, _ in walls["sup"]), default=None) if walls and walls.get("sup") else r50(last - 2 * swing["atr"])
    if d > 0 and sup_k:
        s = spread("P", sup_k)
        if s:
            out["plans"].append({
                "kind": "價差", "title": f"賣出 {exp} {s['sk']:,} Put 價差（Bull Put）",
                "legs": s["legs"], "credit": round(s["credit"] * OPT_PT),
                "max_loss": round((s["w"] - s["credit"]) * OPT_PT), "breakeven": round(s["sk"] - s["credit"]),
                "note": sp_note(s, "P"), "weak": bool(s.get("weak"))})
    if d < 0 and res_k:
        s = spread("C", res_k)
        if s:
            out["plans"].append({
                "kind": "價差", "title": f"賣出 {exp} {s['sk']:,} Call 價差（Bear Call）",
                "legs": s["legs"], "credit": round(s["credit"] * OPT_PT),
                "max_loss": round((s["w"] - s["credit"]) * OPT_PT), "breakeven": round(s["sk"] + s["credit"]),
                "note": sp_note(s, "C"), "weak": bool(s.get("weak"))})
    if d == 0 and res_k and sup_k:
        a, b = spread("C", res_k), spread("P", sup_k)
        if a and b:
            cr = a["credit"] + b["credit"]
            w = max(a["w"], b["w"])
            out["plans"].append({
                "kind": "賣方", "title": f"{exp} 鐵兀鷹（{b['sk']:,}–{a['sk']:,} 區間）",
                "legs": b["legs"] + a["legs"], "credit": round(cr * OPT_PT),
                "max_loss": round((w - cr) * OPT_PT),
                "breakeven": f"{b['sk'] - cr:,.0f}／{a['sk'] + cr:,.0f}",
                "note": "看區間整理時使用。任一邊短腳被觸及就先處理那一邊；收到權利金一半時出場。"
                        + ("其中一邊權利金偏少，風險報酬差。" if a.get("weak") or b.get("weak") else ""),
                "weak": bool(a.get("weak") or b.get("weak"))})
    return out


def run(root: Path):
    W = _load(root / "data" / "wave.json", {})
    chips = _load(root / "data" / "chips.json")
    st = _load(root / "data" / "store" / "chips" / "strikes.json", {})
    store = root / "data" / "store" / "txf"
    if not W.get("M30") or not W.get("H"):
        print("[strategy] 波浪資料不足，略過")
        return
    last = W["M5"]["last"]["c"] if W.get("M5") else W["H"]["last"]["c"]
    a30 = cur_atr(_load(store / "m30.json", {})) or 80
    a60 = cur_atr(_load(store / "h60.json", {})) or 120

    cd, cwhy = chips_dir(chips)
    s_i, why_i = bias(W, chips, [("M5", 0, 1.0), ("M5", 1, 0.4), ("M30", 0, 0.8)])
    s_i += 0.5 * cd
    s_s, why_s = bias(W, chips, [("H", 0, 1.0), ("D", 0, 0.8), ("H", 1, 0.3), ("D", 1, 0.3)])
    s_s += 1.0 * cd
    if cwhy:
        why_i.append(cwhy)
        why_s.append(cwhy)
    di = 1 if s_i >= 0.4 else -1 if s_i <= -0.4 else 0
    ds = 1 if s_s >= 0.4 else -1 if s_s <= -0.4 else 0

    intraday = fut_plan("當沖", W, ["M5", "M30"], di, last, a30, 1.5, why_i)
    swing = fut_plan("波段", W, ["H", "D"], ds, last, a60, 2.5, why_s)
    intraday["score"], swing["score"] = round(s_i, 2), round(s_s, 2)
    out = {
        "generated_at": datetime.now(TPE).isoformat(timespec="minutes"),
        "last": last, "last_t": (W.get("M5") or W["H"])["last"]["t"],
        "intraday": intraday, "swing": swing,
    }
    if st.get("data") and chips and st.get("date") == chips.get("date"):
        out["options"] = opt_plans(st["data"], st["date"], ds, last, swing, chips)
    _dump(root / "data" / "strategy.json", out)
    print(f"[strategy] 當沖 {intraday['dir']}、波段 {swing['dir']}")


if __name__ == "__main__":
    run(Path(__file__).resolve().parent.parent)
