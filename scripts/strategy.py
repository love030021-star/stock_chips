"""波段逆勢布局：日線、60 分、30 分各自數推動浪，在修正浪的區間分三層布局，
用下一個較小週期的 123 法則抓進場點（日線→60 分、60 分→30 分、30 分→5 分）。

回檔（反彈）三層，以被修正的那一段走勢計算：
  強勢回檔：0.382、0.44、0.5
  回檔滿足 1：0.618、0.72、0.809
  回檔滿足 2：0.91、0.95
被修正的走勢：第 2 浪看第 1 浪，第 4 浪看第 3 浪，5 浪走完後看整段 1–5 浪；找不到推動浪時看最近一段走勢。
推動浪進行中（第 3、5 浪）不追價，先列出預估的下一個修正區。
停損設在波浪規則的失效價外 0.15 倍 ATR（失效停損）；超過停損的批次不布局。

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
    K, D = txf.kd(bars)
    best = None
    for strict in (True, False):          # 先找每個轉折都有 KD 配合的推動浪，找不到才放寬到差一個
        for mult in txf.MULTS[tf]:
            piv, cur = txf.zigzag(bars, mult)
            for c in txf.kd_filter(txf.candidates(piv, cur), piv, K, D):
                if c["kind"] != "impulse" or (strict and c["kd_miss"]):
                    continue
                if best is None or c["score"] > best[0]["score"]:
                    best = (c, piv, cur, mult)
        if best:
            break
    last = bars[-1][4]
    if best:
        c, piv, cur, mult = best
        pts = [(bars[piv[c["s"] + j][0]][0], piv[c["s"] + j][1]) for j in range(c["k"] + 1)]
        return {"d": c["d"], "k": c["k"], "pts": pts, "atr": a, "last": last, "last_t": keys[-1],
                "kd_ok": c["kd_ok"],
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


TIERS = [("強勢回檔", (0.382, 0.44, 0.5)), ("回檔滿足 1", (0.618, 0.72, 0.809)), ("回檔滿足 2", (0.91, 0.95))]

WAVE_NOW = {1: "第 2 浪回檔", 2: "第 3 浪推進", 3: "第 4 浪回檔", 4: "第 5 浪推進", 5: "5 浪走完後的 ABC 修正"}


import math


def _l(p):
    return math.log(p) if p > 0 else None


def rt(base, other, r):
    """算術回檔：自 base 往 other 回 r（點數）。"""
    return base - (base - other) * r


def lgx(base, a, b, m):
    """算術延伸：自 base 延伸 a→b 這段的 m 倍。大幅下跌時會算到接近 0 或負值，才改用等比例延伸。"""
    lin = base + (b - a) * m
    la, lbb, lb = _l(a), _l(b), _l(base)
    if lin > base * 0.15 or None in (la, lbb, lb):
        return lin
    return math.exp(lb + (lbb - la) * m)


def plan_from(T):
    """分批布局：每個修正區分三層（最多七批），停損設在波浪規則失效價之外（失效停損）。"""
    global STEP
    d, k, pts, atr = T["d"], T["k"], T["pts"], T["atr"]
    ref = abs(T["last"])
    STEP = 10 if ref >= 10000 else 1 if ref >= 1000 else 0.1 if ref >= 100 else 0.01
    up = d == 1
    tfn = T.get("tfn", "日線")
    x = [p[1] for p in pts]
    hl = "低" if up else "高"
    out = {"trend": "上升推動浪" if up else "下跌推動浪", "dir": d, "k": k, "kd_ok": T.get("kd_ok"),
           "now": WAVE_NOW.get(k, ""), "points": [[t, r10(p), str(i)] for i, (t, p) in enumerate(pts)],
           "side": "做多" if up else "做空", "atr": r10(atr) if STEP < 1 else round(atr)}

    def lv(base, other, label):
        """回檔（反彈）三層：強勢回檔、回檔滿足 1、回檔滿足 2。"""
        # 網站上不公開切割比例：只輸出層級、價位與依據的走勢
        return [{"tier": tn, "price": r10(rt(base, other, r)), "label": f"依{label}"}
                for tn, rs in TIERS for r in rs]

    phase, note, mult = "correct", None, None
    if T.get("fallback"):
        out["trend"] = {1: "上升趨勢", -1: "下跌趨勢", 0: "方向不明"}[d]
        if not d or len(x) < 2:
            out.update({"now": "找不到清楚的推動浪", "phase": "wait",
                        "text": f"{tfn}找不到符合規則的推動浪，高低點結構也看不出方向，先不布局。"})
            return out
        leg = abs(x[1] - x[0])
        batches = lv(x[1], x[0], f"最近一段{'上漲' if up else '下跌'}")
        inv, inv_src = x[0], f"這段走勢起點 {fx(x[0])}（{'跌破' if up else '漲破'}代表高低點結構改變）"
        t1, t1s = x[1], f"這段走勢{'高' if up else '低'}點"
        t2, t2s = lgx(x[1], x[0], x[1], 0.618), "這段走勢的延伸目標"
        out["now"] = f"沒有推動浪，依最近一段{'上漲' if up else '下跌'}布局"
        note = f"{tfn}找不到符合規則的推動浪，改用高低點結構判斷為{out['trend']}，以最近一段 {fx(x[0])} → {fx(x[1])} 的回檔區布局。"
        out["points"] = [[t, r10(p), "0" if i == 0 else "1"] for i, (t, p) in enumerate(pts)]
    elif k == 1:   # 第 2 浪
        w1 = abs(x[1] - x[0])
        batches = lv(x[1], x[0], "第 1 浪")
        inv, inv_src = x[0], f"第 1 浪起點 {fx(x[0])}（第 2 浪不可{'跌破' if up else '漲破'}）"
        t1, t1s = x[1], f"第 1 浪{'高' if up else '低'}點"
        t2, t2s = lgx(rt(x[1], x[0], 0.618), x[0], x[1], 1.618), "第 3 浪預估目標"
    elif k == 3:  # 第 4 浪
        w3 = abs(x[3] - x[2])
        batches = lv(x[3], x[2], "第 3 浪")
        inv, inv_src = x[1], f"第 1 浪{'高' if up else '低'}點 {fx(x[1])}（第 4 浪不可與第 1 浪重疊）"
        t1, t1s = x[3], f"第 3 浪{'高' if up else '低'}點"
        t2, t2s = lgx(rt(x[3], x[2], 0.382), x[0], x[1], 1.0), "第 5 浪預估目標"
    elif k == 5:  # 5 浪後 ABC
        tot = abs(x[5] - x[0])
        batches = lv(x[5], x[0], "第 1–5 浪")
        inv, inv_src = x[0], f"第 1 浪起點 {fx(x[0])}（整段 5 浪被完全吃回）"
        t1, t1s = rt(x[5], x[0], 0.236), "第一個反彈目標" if up else "第一個回落目標"
        t2, t2s = x[5], f"第 5 浪{'高' if up else '低'}點"
    else:        # 第 3、5 浪推進中：不追價，先列出預估的下一個修正區
        phase = "trend"
        last0 = T["last"]
        w1 = abs(x[1] - x[0])
        base = x[2] if k == 2 else x[4]
        mults = (1.618, 2.618, 4.236) if k == 2 else (1.0, 1.618, 2.618)
        proj = None
        for m_ in mults:            # 價格已越過較小的目標就改用下一個延伸比例
            p_ = lgx(base, x[0], x[1], m_)
            if (p_ - last0) * d > 0:
                proj, mult = p_, m_
                break
        ext = mult is not None and mult != mults[0]
        if proj is None:
            proj, ext = last0, True
        wn = "3" if k == 2 else "5"
        why = (f"已超過所有延伸目標，暫以現價代替第 {wn} 浪終點" if mult is None else
               ("較小的目標已被越過，視為延伸浪" if ext else "依第 1 浪推算"))
        out["extended"] = ext
        if k == 2:
            batches = lv(proj, x[2], "預估第 3 浪")
            note = f"第 3 浪推進中，預估目標 {fx(proj)}（{why}）。到達後的第 4 浪回檔區預估如下，屆時依實際{'高' if up else '低'}點重算。"
            inv, inv_src = x[1], f"第 1 浪{'高' if up else '低'}點 {fx(x[1])}（第 4 浪不可重疊）"
            t1, t1s = proj, "預估第 3 浪終點"
            t2, t2s = lgx(rt(proj, x[2], 0.382), x[0], x[1], 1.0), "第 5 浪預估目標"
        else:
            tot = abs(proj - x[0])
            batches = lv(proj, x[0], "預估第 1–5 浪")
            note = f"第 5 浪推進中，預估目標 {fx(proj)}（{why}）。5 浪走完後的 ABC 修正區預估如下。"
            inv, inv_src = x[0], f"第 1 浪起點 {fx(x[0])}"
            t1, t1s = rt(proj, x[0], 0.236), "第一個反彈目標" if up else "第一個回落目標"
            t2, t2s = proj, "預估第 5 浪終點"
    # 失效停損：結構失效價再往外 0.15 倍 ATR（避開剛好碰到失效價的假跌破）
    stop = inv - d * 0.15 * atr
    if d == 1:
        stop = max(stop, inv * 0.5)
    stop = r10(stop)
    dropped = [b for b in batches if (b["price"] - stop) * d <= 0 or (b["price"] - inv) * d < 0]
    batches = [b for b in batches if b not in dropped] or batches[:1]
    out.update({"phase": phase, "batches": batches, "invalid": r10(inv), "invalid_src": inv_src,
                "stop": stop, "stop_src": f"失效價 {fx(inv)} 外",
                "t1": r10(t1), "t1_src": t1s, "t2": r10(t2), "t2_src": t2s})
    if dropped:
        out["dropped"] = [f"{b['tier'] if up else b['tier'].replace('回檔', '反彈')}（{fx(b['price'])}）" for b in dropped]
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
            hit = [b for b in batches if (last <= b["price"] if up else last >= b["price"])]
            tier = hit[-1]["tier"] if hit else batches[0]["tier"]
            out["status"] = f"價格 {fx(last)} 已進入{tier}，等 {T.get('lower_label', '小週期')} K 出現 123 確認再分批{act}。"
        elif (last - stop) * d <= 0:
            out["status"] = (f"價格 {fx(last)} 已{'跌破' if up else '漲破'}失效停損 {fx(stop)}，{tfn}數浪被推翻，"
                             f"全部出場，等{tfn}重新數浪。")
        elif broke:
            out["status"] = (f"價格 {fx(last)} 已{'跌破' if up else '漲破'}結構失效價 {fx(r10(inv))}，接近失效停損 {fx(stop)}，"
                             f"停止加碼，{'跌破' if up else '漲破'}停損就出場。")
        else:
            out["status"] = f"價格 {fx(last)} 已越過最後一批，不再加碼；持有到目標，{'跌破' if up else '漲破'}失效停損 {fx(stop)} 出場。"
    elif k in (2, 4) and not T.get("fallback") and (last - x[k]) * d < 0:
        # 推動浪進行中：價格回到第 2（4）浪終點之外，這段推動浪的數法就不成立
        out["status"] = (f"價格 {fx(last)} 已{'跌破' if up else '漲破'}第 {k} 浪終點 {fx(x[k])}，"
                         f"{tfn}推動浪的數法可能已經失效，先不布局，等{tfn}重新數浪。")
        out["broken"] = True
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


LOWER = {"D": ("H",), "H": ("M30",), "M30": ("M5",), "M5": ()}
ENTRY_TF = {"D": "H", "H": "M30", "M30": "M5", "M5": "M5"}   # 5 分沒有更小週期，用 5 分 K 自己找 123   # 各週期布局用來找 123 的較小週期
TFN = {"D": "日線", "H": "60 分", "M30": "30 分", "M5": "5 分"}


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


def rule123(hbars: dict, d: int, start: str, P: dict, ln: str = "60 分", pn: str = "日線") -> dict:
    """60 分 K 的 123 法則，確認日線回檔（反彈）是否結束。
    做多：第 1 點 = 回檔以來的最低點；第 2 點 = 之後反彈的高點（至少 3 倍 60 分 ATR、半個日線 ATR）；
          第 3 點 = 再拉回（至少第 1–2 點的 0.236）但沒有跌破第 1 點；60 分 K 收盤突破第 2 點即確認。
    做空反過來。跌破（漲破）第 1 點就重新找。"""
    keys = [k for k in sorted(hbars) if k >= start][-400:]
    if len(keys) < 5:
        return {"ok": False, "state": "none", "text": f"{pn}回檔開始後的 {ln} K 還太少，等資料累積。"}
    raw = [[k] + hbars[k][:4] for k in keys]
    # 做空時把價格翻成負數，統一用做多的邏輯
    bars = [[k, o * d, (h if d == 1 else l) * d, (l if d == 1 else h) * d, c * d] for k, o, h, l, c in raw]
    A0 = txf.atr(bars)
    # 第 1→2 點至少要有 3 倍 60 分 ATR、且不小於半個日線 ATR，避免把盤中小波動當成 123
    A = [max(3 * a, 0.5 * float(P.get("atr") or 0)) / 1.5 for a in A0]
    p1, p2, p3, conf = (0, bars[0][3]), None, None, None
    for i in range(1, len(bars)):
        _, o, h, l, c = bars[i]
        if l < p1[1]:
            p1, p2, p3, conf = (i, l), None, None, None
            continue
        if conf is not None:
            continue
        if p3 is None:
            if p2 is None or h > p2[1]:
                p2 = (i, h)
            elif p2[1] - p1[1] >= 1.5 * A[i] and p2[1] - l >= 0.236 * (p2[1] - p1[1]):
                p3 = (i, l)
        else:
            if l < p3[1]:
                p3 = (i, l)
            if c > p2[1]:
                conf = i
    pt = lambda q: {"t": bars[q[0]][0], "price": r10(q[1] * d)} if q else None  # noqa: E731
    big = p2 is not None and p2[1] - p1[1] >= 1.5 * A[-1]
    lo, hi = ("低", "高") if d == 1 else ("高", "低")
    brk, dn = ("突破", "跌破") if d == 1 else ("跌破", "漲破")
    P1, P2, P3 = pt(p1), pt(p2) if big or p3 else None, pt(p3)
    zone = P.get("zone") or [None, None]
    first = P["batches"][0]["price"] if P.get("batches") else None
    reached = first is not None and (p1[1] * d <= first if d == 1 else p1[1] * d >= first)
    filled = [i for i, b in enumerate(P.get("batches") or []) if (p1[1] * d <= b["price"] if d == 1 else p1[1] * d >= b["price"])]
    if conf is not None:
        state = "confirmed"
        txt = (f"123 已確認：第 1 點 {fx(P1['price'])}（{P1['t'][5:]}）、第 2 點 {fx(P2['price'])}、第 3 點 {fx(P3['price'])}，"
               f"{bars[conf][0][5:]} 收盤{brk}第 2 點，{ln} K 判斷{'回檔' if d == 1 else '反彈'}結束。")
    elif p3 is not None:
        state = "wait_break"
        txt = (f"第 3 點 {fx(P3['price'])} 已出現（沒有{dn}第 1 點 {fx(P1['price'])}），等 {ln} K 收盤{brk}第 2 點 {fx(P2['price'])}。")
    elif big:
        state = "wait3"
        txt = (f"已從第 1 點 {fx(P1['price'])} {'反彈' if d == 1 else '回落'}到第 2 點 {fx(P2['price'])}，"
               f"等拉回形成第 3 點，不可{dn} {fx(P1['price'])}。")
    else:
        state = "wait2"
        txt = f"{ln} K 還在創新{lo}（目前第 1 點 {fx(P1['price'])}），等出現夠大的{'反彈' if d == 1 else '回落'}形成第 2 點。"
    ok = state == "confirmed" and reached
    if state == "confirmed" and not reached:
        txt += f"但{'回檔' if d == 1 else '反彈'}沒有到第 1 批 {fx(first)} 就結束，不追價。"
    elif ok:
        txt += f"已碰到第 {'、'.join(str(i + 1) for i in filled)} 批價位，這幾批可依{brk}第 2 點的價位進場；更深的批次留待下一次 123。"
    elif reached:
        txt += f"價格已碰到第 {'、'.join(str(i + 1) for i in filled)} 批，等 123 確認再進場。"
    else:
        txt += f"價格還沒到第 1 批 {fx(first)}。" if first is not None else ""
    if state in ("wait_break", "confirmed"):
        txt += f"若之後{dn}第 1 點 {fx(P1['price'])}，代表這次 123 失敗，改等更深的批次重新找 123。"
    show = raw[-120:]
    return {"ok": ok, "state": state, "text": txt, "p1": P1, "p2": P2, "p3": P3,
            "confirm_t": bars[conf][0] if conf is not None else None, "entry": P2["price"] if P2 else None,
            "filled": [i + 1 for i in filled],
            "bars": [[k, r10(o), r10(h), r10(l), r10(c)] for k, o, h, l, c in show],
            "t": raw[-1][0], "last": raw[-1][4]}


TF_LABEL = {"D": "日線", "H": "60 分", "M30": "30 分", "M5": "5 分"}


def build_plans(W: dict, bars_by_tf: dict, pt: dict) -> dict:
    """W：波浪推算結果（含 M5）；bars_by_tf：{"D"/"H"/"M30": {key: [o,h,l,c,...]}}；pt：每點價值。"""
    lw = W.get("M5") or W.get("H") or {}
    last = lw.get("last", {}).get("c")
    last_t = lw.get("last", {}).get("t")
    out = {"generated_at": datetime.now(TPE).isoformat(timespec="minutes"), "pt": pt, "plans": {}}
    major = None
    for tf in ("D", "H", "M30", "M5"):   # 各週期分開布局，用下一個較小週期的 123 抓進場
        bars = bars_by_tf.get(tf) or {}
        if len(bars) < 60:
            continue
        T = daily_trend(bars, tf)
        # 與上方「波浪推算」同一套數浪：主要數法是推動浪就用它的浪點；是修正浪就等修正結束，不另外找推動浪
        prim = ((W.get(tf) or {}).get("counts") or [None])[0]
        wait_note = None
        if prim and prim.get("kind") == "impulse" and prim.get("d") and len(prim.get("points") or []) >= 2:
            T = dict(T, d=prim["d"], k=len(prim["points"]) - 1, fallback=False,
                     pts=[(pt_[0], pt_[1]) for pt_ in prim["points"]],
                     kd_ok=[x.get("ok") for x in prim.get("kd") or []])
        elif prim and prim.get("kind"):
            wait_note = f"{TFN[tf]}主要數法為{prim.get('pattern', '修正浪')}（{prim.get('now', '')}），屬於修正走勢；等修正結束、出現新的推動浪再布局。"
            T = dict(T, d=0, k=None, pts=[], fallback=True)
        if last is None:
            last = T["last"]
        et = ENTRY_TF[tf]
        P = plan_from(dict(T, last=last, lower_label=TFN[et], tfn=TFN[tf]))
        if wait_note:
            P.update({"now": "依主要數法：修正浪進行中", "trend": "修正走勢", "phase": "wait", "text": wait_note})
        P["tf"], P["tf_label"], P["entry_tf"] = tf, TF_LABEL[tf], TFN[et]
        P["data_t"], P["data_close"] = T["last_t"], T["last"]
        ks = sorted(bars)
        i0 = len(ks) - 150
        if P.get("points"):
            try:
                i0 = min(i0, ks.index(P["points"][0][0]) - 8)
            except ValueError:
                pass
        i0 = max(0, i0, len(ks) - 320)
        P["bars"] = [[k] + [r10(v) for v in bars[k][:4]] for k in ks[i0:]]
        P["partial"] = bool(tf == "D" and len(bars[max(bars)]) > 6)
        if tf == "D":
            major = P
        elif major and P["dir"] and major["dir"]:
            P["vs_major"] = ("順著日線" if P["dir"] == major["dir"] else "逆著日線") + major["trend"]
        P["abc"] = abc_detail(W, T["d"], tf) if T["d"] else []
        P["kd"] = (W.get(et) or {}).get("kd_now")
        P["kd_tf"] = TFN[et]
        hb = bars_by_tf.get(et) or {}
        if not T["d"] or P.get("phase") != "correct":
            P["signal"] = {"ok": False, "state": "trend",
                           "text": f"{TFN[tf]}推動浪還在進行，不找 123；等推動浪結束、進入修正後再看 {TFN[et]} K。"} if T["d"] else None
        elif not hb:
            P["signal"] = {"ok": False, "state": "none", "text": f"這個商品沒有 {TFN[et]} K，請自行確認 123。"}
        else:
            P["signal"] = rule123(hb, T["d"], P["points"][-1][0], P, TFN[et], TFN[tf])
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
    bars = {"D": daily, "H": _load(store / "h60.json", {}), "M30": _load(store / "m30.json", {}),
            "M5": _load(store / "m5.json", {})}
    out = build_plans(W, bars, PT)
    _dump(root / "data" / "strategy.json", out)
    for tf, P in out["plans"].items():
        print(f"[strategy] {P['tf_label']}：{P['trend']}，{P['now']}；{P.get('status', '')}")


if __name__ == "__main__":
    run(Path(__file__).resolve().parent.parent)
