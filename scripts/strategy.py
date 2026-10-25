"""波段逆勢布局（不設停損）：只用日線轉折判斷推動浪方向與浪位，在修正浪的區間分四批布局，60 分 K 抓進場點。

規則：
  1. 日線只數推動浪（三種轉折門檻取分數最高者），判斷是上升或下跌推動浪、目前在第幾浪。
     找不到推動浪時，用最近的高低點結構判斷方向。
  2. 修正浪才布局（逆勢），各分四批、不設停損：
       第 2 浪：第 1 浪的 0.382、0.5、0.618、0.786。
       第 4 浪：第 3 浪的 0.236、0.382、0.5、0.618。
       5 浪走完後的 ABC：整段 1–5 浪的 0.382、0.5、0.618、0.786。
     推動浪進行中（第 3、5 浪）不追價，先列出預估的下一個修正區。
  3. 波浪規則的失效價不當停損，只當「停止加碼、重新數浪」的位置，並算出到失效價與更深一段的浮動虧損（壓力測試）。
  4. 價格進入布局區後，等 60 分 K 推算轉為布局方向再進該批；60 分 K 若有逆向 ABC，列出 C 浪目標對照。

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


STEP = 10   # 價位的最小單位，plan_from() 依價格大小設定


def r10(x):
    """依價格大小取整：萬點以上到 10，千點以上到 1，百元以上到 0.1，其餘到 0.01。"""
    v = round(x / STEP) * STEP
    return int(v) if STEP >= 1 else round(v, 2)


def fx(x):
    x = r10(x)
    return f"{x:,.0f}" if STEP >= 1 else f"{x:,.2f}"


def daily_trend(daily: dict, tf: str = "D"):
    keys = sorted(daily)[-(600 if tf == "D" else 1500):]
    bars = [[k] + daily[k][:4] for k in keys]
    a = txf.atr(bars)[-1]
    best = None
    for mult in txf.MULTS[tf]:
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
    piv, cur = txf.zigzag(bars, txf.MULTS[tf][1])
    seq0 = list(piv) + ([cur] if cur else [])   # 目前延伸中的轉折也算進結構
    hs = [p[1] for p in seq0 if p[2] == "H"][-2:]
    ls = [p[1] for p in seq0 if p[2] == "L"][-2:]
    d = 0
    if len(hs) == 2 and len(ls) == 2:
        d = 1 if hs[1] > hs[0] and ls[1] > ls[0] else -1 if hs[1] < hs[0] and ls[1] < ls[0] else 0
    # 最近一段順著方向的走勢（上升：最後一個低點到其後高點；下跌反之）
    leg = None
    if d:
        seq = list(piv) + ([cur] if cur else [])
        for i in range(len(seq) - 1, 0, -1):
            a0, b0 = seq[i - 1], seq[i]
            if (d == 1 and a0[2] == "L" and b0[2] == "H") or (d == -1 and a0[2] == "H" and b0[2] == "L"):
                leg = [(bars[a0[0]][0], a0[1]), (bars[b0[0]][0], b0[1])]
                break
    return {"d": d, "k": None, "pts": leg or [], "atr": a, "last": last, "last_t": keys[-1], "fallback": True}


WAVE_NOW = {1: "第 2 浪回檔", 2: "第 3 浪推進", 3: "第 4 浪回檔", 4: "第 5 浪推進", 5: "5 浪走完後的 ABC 修正"}


def plan_from(T):
    """不設停損的分批布局：每個修正區四批，越深越接近波浪規則的失效價。
    失效價不是停損，而是「數浪被推翻」的位置：到了就停止加碼、等日線重新數浪。"""
    global STEP
    d, k, pts, atr = T["d"], T["k"], T["pts"], T["atr"]
    ref = abs(T["last"])
    STEP = 10 if ref >= 10000 else 1 if ref >= 1000 else 0.1 if ref >= 100 else 0.01
    up = d == 1
    x = [p[1] for p in pts]
    hl = "低" if up else "高"
    out = {"trend": "上升推動浪" if up else "下跌推動浪", "dir": d, "k": k,
           "now": WAVE_NOW.get(k, ""), "points": [[t, r10(p), str(i)] for i, (t, p) in enumerate(pts)],
           "side": "做多" if up else "做空", "atr": r10(atr) if STEP < 1 else round(atr), "nostop": True}

    def lv(base, span, ratios, label):
        return [{"ratio": r, "price": r10(base - d * span * r), "label": f"{label}的 {r:g}"} for r in ratios]

    phase, note, mult = "correct", None, None
    if T.get("fallback"):
        out["trend"] = {1: "上升趨勢", -1: "下跌趨勢", 0: "方向不明"}[d]
        if not d or len(x) < 2:
            out.update({"now": "找不到清楚的推動浪", "phase": "wait",
                        "text": "日線找不到符合規則的推動浪，高低點結構也看不出方向，先不布局。"})
            return out
        leg = abs(x[1] - x[0])
        batches = lv(x[1], leg, (0.382, 0.5, 0.618, 0.786), f"最近一段{'上漲' if up else '下跌'}")
        inv, inv_src = x[0], f"這段走勢起點 {fx(x[0])}（{'跌破' if up else '漲破'}代表高低點結構改變）"
        deep, deep_src = x[0] - d * leg * 0.272, "起點再延伸這段的 0.272"
        t1, t1s = x[1], f"這段走勢{'高' if up else '低'}點"
        t2, t2s = x[1] + d * leg * 0.618, "再延伸這段走勢的 0.618"
        out["now"] = f"沒有推動浪，依最近一段{'上漲' if up else '下跌'}布局"
        note = f"日線找不到符合規則的推動浪，改用高低點結構判斷為{out['trend']}，以最近一段 {fx(x[0])} → {fx(x[1])} 的回檔區布局。"
        out["points"] = [[t, r10(p), "0" if i == 0 else "1"] for i, (t, p) in enumerate(pts)]
    elif k == 1:   # 第 2 浪
        w1 = abs(x[1] - x[0])
        batches = lv(x[1], w1, (0.382, 0.5, 0.618, 0.786), "第 1 浪")
        inv, inv_src = x[0], f"第 1 浪起點 {fx(x[0])}（第 2 浪不可{'跌破' if up else '漲破'}）"
        deep, deep_src = x[0] - d * w1 * 0.272, "第 1 浪起點再延伸第 1 浪的 0.272"
        t1, t1s = x[1], f"第 1 浪{'高' if up else '低'}點"
        t2, t2s = batches[2]["price"] + d * w1 * 1.618, "第 3 浪 = 第 1 浪 × 1.618（自 0.618 回檔處起算）"
    elif k == 3:  # 第 4 浪
        w3 = abs(x[3] - x[2])
        batches = lv(x[3], w3, (0.236, 0.382, 0.5, 0.618), "第 3 浪")
        inv, inv_src = x[1], f"第 1 浪{'高' if up else '低'}點 {fx(x[1])}（第 4 浪不可與第 1 浪重疊）"
        deep, deep_src = x[2], f"第 3 浪起點 {fx(x[2])}"
        t1, t1s = x[3], f"第 3 浪{'高' if up else '低'}點"
        t2, t2s = batches[1]["price"] + d * abs(x[1] - x[0]), "第 5 浪 = 第 1 浪（自 0.382 回檔處起算）"
    elif k == 5:  # 5 浪後 ABC
        tot = abs(x[5] - x[0])
        batches = lv(x[5], tot, (0.382, 0.5, 0.618, 0.786), "第 1–5 浪")
        inv, inv_src = x[0], f"第 1 浪起點 {fx(x[0])}（整段 5 浪被完全吃回）"
        deep, deep_src = x[0] - d * tot * 0.272, "第 1 浪起點再延伸整段的 0.272"
        t1, t1s = x[5] - d * tot * 0.236, "回到第 1–5 浪的 0.236"
        t2, t2s = x[5], f"第 5 浪{'高' if up else '低'}點"
    else:        # 第 3、5 浪推進中：不追價，先列出預估的下一個修正區
        phase = "trend"
        last0 = T["last"]
        w1 = abs(x[1] - x[0])
        base = x[2] if k == 2 else x[4]
        mults = (1.618, 2.618, 4.236) if k == 2 else (1.0, 1.618, 2.618)
        proj = None
        for m_ in mults:            # 價格已越過較小的目標就改用下一個延伸比例
            p_ = base + d * w1 * m_
            if (p_ - last0) * d > 0:
                proj, mult = p_, m_
                break
        ext = mult is not None and mult != mults[0]
        if proj is None:
            proj, ext = last0, True
        wn = "3" if k == 2 else "5"
        why = (f"已超過第 1 浪 × {mults[-1]:g} 的延伸，暫以現價代替第 {wn} 浪終點" if mult is None else
               (f"第 5 浪 = 第 1 浪 × {mult:g}" if k == 4 else f"第 1 浪 × {mult:g}") + ("，較小的目標已被越過，視為延伸浪" if ext else ""))
        out["extended"] = ext
        if k == 2:
            batches = lv(proj, abs(proj - x[2]), (0.236, 0.382, 0.5, 0.618), "預估第 3 浪")
            note = f"第 3 浪推進中，預估目標 {fx(proj)}（{why}）。到達後的第 4 浪回檔區預估如下，屆時依實際{'高' if up else '低'}點重算。"
            inv, inv_src = x[1], f"第 1 浪{'高' if up else '低'}點 {fx(x[1])}（第 4 浪不可重疊）"
            deep, deep_src = x[2], f"第 3 浪起點 {fx(x[2])}"
            t1, t1s = proj, "預估第 3 浪終點"
            t2, t2s = batches[1]["price"] + d * w1, "第 5 浪 = 第 1 浪（自預估 0.382 處起算）"
        else:
            tot = abs(proj - x[0])
            batches = lv(proj, tot, (0.382, 0.5, 0.618, 0.786), "預估第 1–5 浪")
            note = f"第 5 浪推進中，預估目標 {fx(proj)}（{why}）。5 浪走完後的 ABC 修正區預估如下。"
            inv, inv_src = x[0], f"第 1 浪起點 {fx(x[0])}"
            deep, deep_src = x[0] - d * tot * 0.272, "第 1 浪起點再延伸整段的 0.272"
            t1, t1s = proj - d * tot * 0.236, "回到預估第 1–5 浪的 0.236"
            t2, t2s = proj, "預估第 5 浪終點"
    out.update({"phase": phase, "batches": batches, "invalid": r10(inv), "invalid_src": inv_src,
                "stress": [{"price": r10(inv), "label": "結構失效價：" + inv_src},
                           {"price": r10(deep), "label": "更深的壓力測試：" + deep_src}],
                "t1": r10(t1), "t1_src": t1s, "t2": r10(t2), "t2_src": t2s})
    if note:
        out["note"] = note
    zone = sorted([batches[0]["price"], batches[-1]["price"]])
    out["zone"] = zone
    last = T["last"]
    act = "買進" if up else "放空"
    if phase == "correct":
        before = (last > zone[1]) if up else (last < zone[0])
        inside = zone[0] <= last <= zone[1]
        broke = (last - inv) * d < 0
        if before:
            dist = (last - zone[1]) if up else (zone[0] - last)
            out["status"] = f"價格 {fx(last)} 還在{'回檔' if up else '反彈'}區{'上方' if up else '下方'}，距第一批 {fx(batches[0]['price'])} 約 {fx(dist)} 點，等待。"
        elif inside:
            out["status"] = f"價格 {fx(last)} 已進入{'回檔' if up else '反彈'}區，等 60 分 K 轉{'上' if up else '下'}再分批{act}。"
        elif broke:
            out["status"] = (f"價格 {fx(last)} 已{'跌破' if up else '漲破'}結構失效價 {fx(r10(inv))}，日線數浪被推翻，"
                             f"停止加碼，已有部位抱單等日線重新數浪。")
        else:
            out["status"] = f"價格 {fx(last)} 已越過最後一批，不再加碼；不停損，持有到目標，{'跌破' if up else '漲破'} {fx(r10(inv))} 才算數浪錯誤。"
    else:
        z = f"{fx(zone[0])}–{fx(zone[1])}"
        ex = "（已走成延伸浪）" if out.get("extended") else ""
        tgt = fx(t1 if k == 2 else t2) if mult is not None else "未定"
        if k == 2:
            out["status"] = (f"第 3 浪還在{'往上' if up else '往下'}{ex}，預估先到 {tgt}，之後第 4 浪{'回檔' if up else '反彈'}到 {z} 才分批"
                             f"{act}。現在 {fx(last)} 不進場。")
        else:
            out["status"] = (f"第 5 浪還在{'往上' if up else '往下'}{ex}，預估到 {tgt} 附近結束，之後的 ABC {'回檔' if up else '反彈'}到 {z} 才分批"
                             f"{act}。現在 {fx(last)} 不進場。")
    return out


LOWER = {"D": ("H",)}


def abc_detail(W, d, tf0="D"):
    """較小週期若有逆向的 ABC（A、B 已完成，C 進行中），列出 C 浪目標。"""
    want = "ABC 回檔" if d == 1 else "ABC 反彈"
    out = []
    for tf in LOWER[tf0]:
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
    """60 分 K 主要推算轉為布局方向（逆向的 5 浪或 ABC 走完）才進該批。"""
    R = W.get("H")
    if not R or not R["counts"]:
        return {"ok": False, "text": "60 分 K 資料不足。"}
    c = R["counts"][0]
    nxt = next((t for t in c["targets"] if not t["reached"]), None)
    cd = (1 if nxt["price"] > R["last"]["c"] else -1) if nxt else (1 if c["invalid"]["price"] < R["last"]["c"] else -1)
    aligned = cd == d
    side = "向上" if d == 1 else "向下"
    s = f"60 分 K 主要推算：{c['now']}（{c['pattern']}），推算方向{'向上' if cd == 1 else '向下'}。"
    if aligned and inside:
        s += f"已轉為{side}，且價格在日線{'回檔' if d == 1 else '反彈'}區內，可進該批。"
    elif aligned:
        s += f"雖已轉為{side}，但價格不在日線布局區，先不進場。"
    else:
        s += f"尚未轉為{side}，等 60 分 K 出現{'下跌 5 浪或 ABC 回檔' if d == 1 else '上漲 5 浪或 ABC 反彈'}走完。"
    return {"ok": aligned and inside, "aligned": aligned, "text": s, "now": c["now"], "pattern": c["pattern"],
            "invalid": c["invalid"]["price"], "t": R["last"]["t"], "last": R["last"]["c"]}


TF_LABEL = {"D": "日線", "H": "60 分", "M30": "30 分"}


def build_plans(W: dict, bars_by_tf: dict, pt: dict) -> dict:
    """W：波浪推算結果（含 M5）；bars_by_tf：{"D"/"H"/"M30": {key: [o,h,l,c,...]}}；pt：每點價值。"""
    lw = W.get("M5") or W.get("H") or {}
    last = lw.get("last", {}).get("c")
    last_t = lw.get("last", {}).get("t")
    out = {"generated_at": datetime.now(TPE).isoformat(timespec="minutes"), "pt": pt, "plans": {}}
    major = None
    for tf in ("D",):          # 只用日線轉折布局，60 分 K 抓進場
        bars = bars_by_tf.get(tf) or {}
        if len(bars) < 60:
            continue
        T = daily_trend(bars, tf)
        if last is None:
            last = T["last"]
        P = plan_from(dict(T, last=last))
        P["tf"], P["tf_label"] = tf, TF_LABEL[tf]
        P["data_t"], P["data_close"] = T["last_t"], T["last"]
        P["partial"] = bool(tf == "D" and len(bars[max(bars)]) > 6)
        if tf == "D":
            major = P
        elif major and P["dir"] and major["dir"]:
            P["vs_major"] = ("順著日線" if P["dir"] == major["dir"] else "逆著日線") + major["trend"]
        inside = bool(P.get("zone")) and P["zone"][0] <= last <= P["zone"][1]
        P["abc"] = abc_detail(W, T["d"], tf) if T["d"] else []
        P["signal"] = entry_signal(W, T["d"], inside) if T["d"] else None
        out["plans"][tf] = P
    out["last"], out["last_t"] = last, last_t or (major or {}).get("data_t")
    return out


def run(root: Path):
    store = root / "data" / "store" / "txf"
    W = _load(root / "data" / "wave.json", {})
    daily = txf.daily_with_night(store)
    if len(daily) < 60:
        print("[strategy] 日線資料不足，略過")
        return
    bars = {"D": daily, "H": _load(store / "h60.json", {}), "M30": _load(store / "m30.json", {})}
    out = build_plans(W, bars, PT)
    _dump(root / "data" / "strategy.json", out)
    for tf, P in out["plans"].items():
        print(f"[strategy] {P['tf_label']}：{P['trend']}，{P['now']}；{P.get('status', '')}")


if __name__ == "__main__":
    run(Path(__file__).resolve().parent.parent)
