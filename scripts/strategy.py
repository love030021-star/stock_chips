"""波段逆勢布局：日線判斷推動浪方向與目前浪位，在修正浪的回檔區間分批布局，5 分 K 抓進場點。

規則：
  1. 日線只數推動浪（三種轉折門檻取分數最高者），判斷是上升或下跌推動浪、目前在第幾浪。
     找不到推動浪時，用最近的高低點結構（高點、低點是否墊高）判斷方向。
  2. 修正浪才布局（逆勢）：
       第 2 浪：第 1 浪的 0.5、0.618、0.786 分三批，停損在第 1 浪起點外。
       第 4 浪：第 3 浪的 0.236、0.382、0.5 分三批，停損在第 1 浪終點外（不可重疊）。
       5 浪走完後的 ABC：整段 1–5 浪的 0.382、0.5、0.618 分三批，停損在 0.786 外。
     推動浪進行中（第 3、5 浪）不追價，先列出預估的下一個回檔區。
  3. 60 分、30 分若已出現 A、B 浪，列出 C 浪 = A 浪 × 1、1.618 的價位，與回檔區互相印證。
  4. 價格進入回檔區後，等 5 分 K 推算轉為布局方向（下跌 5 浪或 ABC 回檔完成）再進該批。

輸出 data/strategy.json。規則產生的價位只是參考，不是投資建議。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import txf

TPE = timezone(timedelta(hours=8))
PT = {"大台": 200, "小台": 50, "微台": 10}


def _load(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _dump(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def r10(x):
    return int(round(x / 10.0) * 10)


def daily_trend(daily: dict):
    keys = sorted(daily)
    bars = [[k] + daily[k][:4] for k in keys]
    a = txf.atr(bars)[-1]
    best = None
    for mult in txf.MULTS["D"]:
        piv, cur = txf.zigzag(bars, mult)
        for c in txf.candidates(piv, cur):
            if c["kind"] != "impulse":
                continue
            if best is None or c["score"] > best[0]["score"]:
                best = (c, piv, cur, mult)
    last = bars[-1][4]
    if best:
        c, piv, cur, mult = best
        pts = [(bars[piv[c["s"] + j][0]][0], piv[c["s"] + j][1]) for j in range(c["k"] + 1)]
        return {"d": c["d"], "k": c["k"], "pts": pts, "atr": a, "last": last, "last_t": keys[-1],
                "mult": mult, "score": c["score"], "cur": cur and (bars[cur[0]][0], cur[1])}
    # 備援：最近兩個高點、低點的結構
    piv, cur = txf.zigzag(bars, txf.MULTS["D"][1])
    hs = [p[1] for p in piv if p[2] == "H"][-2:]
    ls = [p[1] for p in piv if p[2] == "L"][-2:]
    d = 0
    if len(hs) == 2 and len(ls) == 2:
        d = 1 if hs[1] > hs[0] and ls[1] > ls[0] else -1 if hs[1] < hs[0] and ls[1] < ls[0] else 0
    return {"d": d, "k": None, "pts": [], "atr": a, "last": last, "last_t": keys[-1], "fallback": True}


WAVE_NOW = {1: "第 2 浪回檔", 2: "第 3 浪推進", 3: "第 4 浪回檔", 4: "第 5 浪推進", 5: "5 浪走完後的 ABC 修正"}


def plan_from(T):
    d, k, pts, atr = T["d"], T["k"], T["pts"], T["atr"]
    up = d == 1
    x = [p[1] for p in pts]
    n = lambda v: v * d  # noqa: E731  正規化成上升方向
    out = {"trend": "上升推動浪" if up else "下跌推動浪", "dir": d, "k": k,
           "now": WAVE_NOW.get(k, ""), "points": [[t, round(p), str(i)] for i, (t, p) in enumerate(pts)],
           "side": "做多" if up else "做空", "atr": round(atr)}
    if T.get("fallback"):
        out.update({"trend": {1: "上升趨勢", -1: "下跌趨勢", 0: "方向不明"}[d], "now": "找不到清楚的推動浪",
                    "phase": "wait", "text": "日線找不到符合規則的推動浪，先不布局。"})
        return out
    buf = 0.15 * atr

    def lv(base, span, ratios, label):
        return [{"ratio": r, "price": r10(base - d * span * r), "label": f"{label}的 {r:g}"} for r in ratios]

    if k == 1:   # 第 2 浪
        w1 = abs(x[1] - x[0])
        batches = lv(x[1], w1, (0.5, 0.618, 0.786), "第 1 浪")
        stop, stop_src = x[0] - d * buf, f"第 1 浪起點 {x[0]:,.0f} 外（第 2 浪不可跌破）" if up else f"第 1 浪起點 {x[0]:,.0f} 外（第 2 浪不可漲破）"
        assumed = batches[1]["price"]
        t1, t1s = x[1], f"第 1 浪{'高' if up else '低'}點"
        t2, t2s = assumed + d * w1 * 1.618, "第 3 浪 = 第 1 浪 × 1.618（自 0.618 回檔處起算）"
        phase = "correct"
    elif k == 3:  # 第 4 浪
        w3 = abs(x[3] - x[2])
        batches = lv(x[3], w3, (0.236, 0.382, 0.5), "第 3 浪")
        stop, stop_src = x[1] - d * buf, f"第 1 浪{'高' if up else '低'}點 {x[1]:,.0f} 外（第 4 浪不可重疊）"
        assumed = batches[1]["price"]
        t1, t1s = x[3], f"第 3 浪{'高' if up else '低'}點"
        t2, t2s = assumed + d * abs(x[1] - x[0]), "第 5 浪 = 第 1 浪（自 0.382 回檔處起算）"
        phase = "correct"
    elif k == 5:  # 5 浪後 ABC
        tot = abs(x[5] - x[0])
        batches = lv(x[5], tot, (0.382, 0.5, 0.618), "第 1–5 浪")
        stop = x[5] - d * tot * 0.786 - d * buf
        stop_src = f"第 1–5 浪的 0.786（{x[5] - d * tot * 0.786:,.0f}）外"
        t1, t1s = x[5] - d * tot * 0.236, "回到第 1–5 浪的 0.236"
        t2, t2s = x[5], f"第 5 浪{'高' if up else '低'}點"
        phase = "correct"
    else:        # 第 3、5 浪推進中
        if k == 2:
            w1 = abs(x[1] - x[0])
            proj = x[2] + d * w1 * 1.618
            batches = lv(proj, abs(proj - x[2]), (0.236, 0.382, 0.5), "預估第 3 浪")
            note = f"第 3 浪推進中，預估目標 {proj:,.0f}（第 1 浪 × 1.618）。到達後的第 4 浪回檔區預估如下，屆時依實際高點重算。"
            stop, stop_src = x[1] - d * buf, f"第 1 浪{'高' if up else '低'}點 {x[1]:,.0f} 外"
        else:
            proj = x[4] + d * abs(x[1] - x[0])
            batches = lv(proj, abs(proj - x[0]), (0.382, 0.5, 0.618), "預估第 1–5 浪")
            note = f"第 5 浪推進中，預估目標 {proj:,.0f}（第 5 浪 = 第 1 浪）。5 浪走完後的 ABC 修正區預估如下。"
            stop, stop_src = proj - d * abs(proj - x[0]) * 0.786 - d * buf, "預估第 1–5 浪的 0.786 外"
        t1, t1s, t2, t2s = proj, "預估推動浪終點", proj, "預估推動浪終點"
        phase = "trend"
        out["note"] = note
    out.update({"phase": phase, "batches": batches, "stop": r10(stop), "stop_src": stop_src,
                "t1": r10(t1), "t1_src": t1s, "t2": r10(t2), "t2_src": t2s})
    zone = sorted([batches[0]["price"], batches[-1]["price"]])
    out["zone"] = zone
    last = T["last"]
    if phase == "correct":
        above = (last > zone[1]) if up else (last < zone[0])
        inside = zone[0] <= last <= zone[1]
        beyond = (last < zone[0]) if up else (last > zone[1])
        if above:
            dist = (last - zone[1]) if up else (zone[0] - last)
            out["status"] = f"價格 {last:,.0f} 還在回檔區{'上方' if up else '下方'}，距第一批 {batches[0]['price']:,} 約 {dist:,.0f} 點，等待。"
        elif inside:
            out["status"] = f"價格 {last:,.0f} 已進入回檔區，依 5 分 K 訊號分批{'買進' if up else '放空'}。"
        elif beyond:
            out["status"] = f"價格 {last:,.0f} 已越過最後一批，靠近停損 {r10(stop):,}，不再加碼。"
    else:
        out["status"] = "推動浪進行中，逆勢布局要等回檔出現。"
    return out


def abc_detail(W, d):
    """60 分、30 分若有逆向的 ABC（A、B 已完成，C 進行中），列出 C 浪目標。"""
    want = "ABC 回檔" if d == 1 else "ABC 反彈"
    out = []
    for tf in ("H", "M30"):
        R = W.get(tf)
        if not R:
            continue
        for c in R["counts"]:
            if c["pattern"] == want and c["now"] == "C 浪":
                out.append({"tf": R["name"], "points": c["points"],
                            "targets": [t for t in c["targets"]], "invalid": c["invalid"]})
                break
    return out


def entry_signal(W, d, inside):
    R = W.get("M5")
    if not R or not R["counts"]:
        return {"ok": False, "text": "5 分 K 資料不足。"}
    c = R["counts"][0]
    nxt = next((t for t in c["targets"] if not t["reached"]), None)
    cd = (1 if nxt["price"] > R["last"]["c"] else -1) if nxt else (1 if c["invalid"]["price"] < R["last"]["c"] else -1)
    aligned = cd == d
    side = "向上" if d == 1 else "向下"
    s = f"5 分 K 主要推算：{c['now']}（{c['pattern']}），推算方向{'向上' if cd == 1 else '向下'}。"
    if aligned and inside:
        s += f"已轉為{side}，且價格在回檔區內，可進該批；5 分 K 失效價 {c['invalid']['price']:,} 可當短線停損。"
    elif aligned:
        s += f"雖已轉為{side}，但價格不在回檔區，先不進場。"
    else:
        s += f"尚未轉為{side}，等 5 分 K 出現{'下跌 5 浪或 ABC 回檔' if d == 1 else '上漲 5 浪或 ABC 反彈'}走完。"
    return {"ok": aligned and inside, "aligned": aligned, "text": s, "now": c["now"], "pattern": c["pattern"],
            "invalid": c["invalid"]["price"], "t": R["last"]["t"], "last": R["last"]["c"]}


def run(root: Path):
    store = root / "data" / "store" / "txf"
    daily = _load(store / "daily.json", {})
    W = _load(root / "data" / "wave.json", {})
    if len(daily) < 60:
        print("[strategy] 日線資料不足，略過")
        return
    T = daily_trend(daily)
    P = plan_from(T)
    last = (W.get("M5") or {}).get("last", {}).get("c") or T["last"]
    if P.get("zone"):
        inside = P["zone"][0] <= last <= P["zone"][1]
    else:
        inside = False
    out = {
        "generated_at": datetime.now(TPE).isoformat(timespec="minutes"),
        "daily_t": T["last_t"], "daily_close": T["last"],
        "last": last, "last_t": (W.get("M5") or {}).get("last", {}).get("t") or T["last_t"],
        "plan": P, "pt": PT,
        "abc": abc_detail(W, T["d"]) if T["d"] else [],
        "signal": entry_signal(W, T["d"], inside) if T["d"] else None,
    }
    # 用 5 分 K 最新價重算現況
    if P.get("phase") == "correct":
        T2 = dict(T, last=last)
        out["plan"]["status"] = plan_from(T2)["status"]
    _dump(root / "data" / "strategy.json", out)
    print(f"[strategy] 日線{P['trend']}，{P['now']}；{P.get('status', '')}")


if __name__ == "__main__":
    run(Path(__file__).resolve().parent.parent)
