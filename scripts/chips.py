"""台指選擇權籌碼：三大法人、大額交易人、合成多空、未平倉支撐壓力，並產生文字分析。

資料（期交所）：
  三大法人選擇權買賣權分計  /cht/3/callsAndPutsDateDown
  選擇權大額交易人未沖銷部位  /cht/3/largeTraderOptDown
  選擇權每日交易行情（各履約價未平倉） /cht/3/optDataDown
輸出：data/chips.json

計算方式（與玩股網相同，已用 10/21、10/22 資料驗算）：
  多空淨額 = (買Call − 賣Call) − (買Put − 賣Put)
  合成多方 = 買Call + 賣Put，合成空方 = 賣Call + 買Put
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

TPE = timezone(timedelta(hours=8))
BASE = "https://www.taifex.com.tw"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
SLEEP = 2.0
HIST = 10
GROUPS = ["外資", "投信", "自營商", "前五大", "前十大", "前五特法", "前十特法"]
LT_GROUPS = [("前五大", 0, 0), ("前十大", 0, 1), ("前五特法", 1, 0), ("前十特法", 1, 1)]
BIG = 5000     # 大幅變化門檻（口）
FLAT = 100     # 小於此視為持平

_s = requests.Session()
_s.headers.update(UA)


def log(*a):
    print("[chips]", *a, flush=True)


def _post(path, data, tries=4):
    last = None
    for i in range(tries):
        try:
            r = _s.post(BASE + path, data=data, timeout=60)
            r.raise_for_status()
            time.sleep(SLEEP)
            return r.content.decode("cp950", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(SLEEP * (i + 2))
    raise RuntimeError(f"POST {path} 失敗：{last}")


def _i(s):
    s = str(s).strip().replace(",", "")
    try:
        return int(float(s))
    except ValueError:
        return None


def _load(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _dump(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# ======================= 解析 =======================

def parse_large(text: str) -> dict:
    """-> {date: {"C"/"P": {month: {0/1: [b5, s5, b10, s10, total]}}}}，只取 TXO。"""
    out = {}
    for line in text.splitlines():
        c = [x.strip() for x in line.split(",")]
        if len(c) < 11 or c[1] != "TXO":
            continue
        cp = "C" if c[3] == "買權" else "P" if c[3] == "賣權" else None
        vals = [_i(x) for x in c[6:11]]
        if cp is None or None in vals:
            continue
        d = c[0].replace("/", "-")
        out.setdefault(d, {}).setdefault(cp, {}).setdefault(c[4], {})[int(c[5])] = vals
    return out


def parse_inst(text: str) -> dict:
    """-> {date: {who: {"C": [buyOI, sellOI], "P": [...]}}}"""
    out = {}
    names = {"外資及陸資": "外資", "投信": "投信", "自營商": "自營商"}
    for line in text.splitlines():
        c = [x.strip() for x in line.split(",")]
        if len(c) < 16 or c[1] != "臺指選擇權" or c[3] not in names:
            continue
        b, s = _i(c[10]), _i(c[12])
        if b is None or s is None:
            continue
        cp = "C" if c[2].upper() == "CALL" else "P"
        out.setdefault(c[0].replace("/", "-"), {}).setdefault(names[c[3]], {})[cp] = [b, s]
    return out


def parse_strikes(text: str) -> dict:
    """選擇權每日行情 -> {contract: {"exp": 'YYYYMMDD', "C": {strike: oi}, "P": {...}}}（一般時段）"""
    out = {}
    for line in text.splitlines():
        c = [x.strip() for x in line.split(",")]
        if len(c) < 21 or c[1] != "TXO" or c[17] != "一般":
            continue
        oi, k = _i(c[11]), _i(c[3])
        if oi is None or k is None:
            continue
        cp = "C" if c[4] == "買權" else "P"
        rec = out.setdefault(c[2], {"exp": c[20], "C": {}, "P": {}})
        rec[cp][k] = oi
    return out


# ======================= 計算 =======================

def net_of(cb, cs, pb, ps):
    return (cb - cs) - (pb - ps)


def nets_for(day_large, day_inst):
    """所有契約多空淨額。"""
    out = {}
    for who in ("外資", "投信", "自營商"):
        r = (day_inst or {}).get(who)
        if r and "C" in r and "P" in r:
            out[who] = net_of(r["C"][0], r["C"][1], r["P"][0], r["P"][1])
    if day_large:
        c, p = day_large.get("C", {}).get("999999"), day_large.get("P", {}).get("999999")
        if c and p:
            for name, t, ten in LT_GROUPS:
                if t in c and t in p:
                    i = 2 if ten else 0
                    out[name] = net_of(c[t][i], c[t][i + 1], p[t][i], p[t][i + 1])
    return out


def near_month(day_large):
    months = sorted(m for m in day_large.get("C", {}) if re.fullmatch(r"\d{6}", m) and m not in ("666666", "999999")
                    and day_large["C"][m].get(0, [0] * 5)[4] > 0)
    return months[0] if months else None


def month_detail(day_large, month):
    """月選大額 Call/Put 明細：{group: {"C": [buy, sell], "P": [buy, sell]}}，與全市場未平倉。"""
    out, total = {}, {}
    for cp in ("C", "P"):
        rec = day_large.get(cp, {}).get(month, {})
        if 0 in rec:
            total[cp] = rec[0][4]
        for name, t, ten in LT_GROUPS:
            if t in rec:
                i = 2 if ten else 0
                out.setdefault(name, {})[cp] = [rec[t][i], rec[t][i + 1]]
    return out, total


def tag(net, d):
    if d is None:
        return ""
    if abs(d) < FLAT:
        return "持平"
    big = "大幅" if abs(d) >= BIG else ""
    prev = net - d
    if prev < 0 <= net:
        return "轉為淨多"
    if prev > 0 >= net:
        return "轉為淨空"
    if net < 0:
        return f"{big}加空" if d < 0 else "淨空減少"
    return f"{big}增多" if d > 0 else "淨多減少"


def walls(strikes: dict, data_date: str, price: float):
    """最近週選與月選的 Call／Put 最大未平倉履約價。"""
    d0 = data_date.replace("-", "")
    res = {}
    weekly = [(k, v) for k, v in strikes.items() if not re.fullmatch(r"\d{6}", k) and v["exp"] > d0]
    monthly = [(k, v) for k, v in strikes.items() if re.fullmatch(r"\d{6}", k) and v["exp"] > d0]
    if weekly:
        # 10 天內到期的週選中，未平倉最多的一個（避開隔天就到期、部位已大量平倉的合約）
        limit = (datetime.strptime(d0, "%Y%m%d") + timedelta(days=10)).strftime("%Y%m%d")
        near = [w for w in weekly if w[1]["exp"] <= limit] or weekly
        res["weekly"] = max(near, key=lambda kv: sum(kv[1]["C"].values()) + sum(kv[1]["P"].values()))
    if monthly:
        res["monthly"] = min(monthly, key=lambda kv: kv[1]["exp"])
    out = {}
    for key, (name, v) in res.items():
        calls = sorted(v["C"].items(), key=lambda x: -x[1])
        puts = sorted(v["P"].items(), key=lambda x: -x[1])
        out[key] = {
            "contract": name, "exp": f"{v['exp'][:4]}-{v['exp'][4:6]}-{v['exp'][6:]}",
            "max_call": calls[0] if calls else None, "max_put": puts[0] if puts else None,
            "res": sorted(sorted([kv for kv in v["C"].items() if kv[0] > price], key=lambda x: -x[1])[:3]),
            "sup": sorted(sorted([kv for kv in v["P"].items() if kv[0] < price], key=lambda x: -x[1])[:3], reverse=True),
            "call_oi": sum(v["C"].values()), "put_oi": sum(v["P"].values()),
        }
    return out


# ======================= 文字分析 =======================

def fmt(n):
    return f"{n:,.0f}" if n is not None else "—"


def sgn(n):
    return f"+{n:,.0f}" if n > 0 else f"{n:,.0f}"


def analyze(cur):
    net, synth, fut = cur["net"], cur["synth"], cur["fut"]
    bear, bull, score = [], [], 0.0

    def get(rows, name):
        return next((r for r in rows if r["name"] == name), None)

    # 1. 前五、前十大所有契約
    t5, t10 = get(net, "前五大"), get(net, "前十大")
    if t5 and t10 and t5["d"] is not None and t10["d"] is not None:
        avg = (t5["d"] + t10["d"]) / 2
        w = 2 if abs(avg) >= BIG else 1 if abs(avg) >= 1000 else 0
        if w:
            txt = f"前五、前十大所有契約淨額分別 {sgn(t5['d'])}、{sgn(t10['d'])} 口"
            (bull if avg > 0 else bear).append(txt + ("，多方部位增加" if avg > 0 else "，空方部位增加"))
            score += w if avg > 0 else -w
    # 2. 特法所有契約
    s5, s10 = get(net, "前五特法"), get(net, "前十特法")
    if s5 and s10 and s5["d"] is not None and s10["d"] is not None:
        avg = (s5["d"] + s10["d"]) / 2
        w = 1.5 if abs(avg) >= 3000 else 1 if abs(avg) >= 800 else 0
        if w:
            txt = f"特定法人（前五、前十）所有契約淨額 {sgn(s5['d'])}、{sgn(s10['d'])} 口"
            (bull if avg > 0 else bear).append(txt)
            score += w if avg > 0 else -w
    # 3. 三大法人
    for who, wt in (("外資", 1.0), ("自營商", 0.5), ("投信", 0.5)):
        r = get(net, who)
        if r and r["d"] is not None and abs(r["d"]) >= 1000:
            txt = f"{who}淨額 {sgn(r['d'])} 口（{r['tag']}）"
            (bull if r["d"] > 0 else bear).append(txt)
            score += wt if r["d"] > 0 else -wt
    # 4. 月選合成淨額
    month_bias = 0
    for name in ("前十大", "前十特法"):
        r = get(synth, name)
        if r and r["dnet"] is not None and abs(r["dnet"]) >= 500:
            txt = f"{cur['month']} 月選{name}合成淨額 {sgn(r['dnet'])} 口（{fmt(r['net'] - r['dnet'])} → {fmt(r['net'])}）"
            (bull if r["dnet"] > 0 else bear).append(txt)
            score += 1 if r["dnet"] > 0 else -1
            month_bias += 1 if r["dnet"] > 0 else -1
    # 5. 期貨價格
    if fut.get("pct") is not None and abs(fut["pct"]) >= 0.5:
        txt = f"台指期 {fmt(fut['close'])}，{'上漲' if fut['chg'] > 0 else '下跌'} {abs(fut['chg']):,.0f} 點（{fut['pct']:+.2f}%）"
        (bull if fut["chg"] > 0 else bear).append(txt)
        score += 1 if fut["chg"] > 0 else -1

    if score >= 2.5:
        verdict = "偏多"
    elif score <= -2.5:
        verdict = "偏空"
    else:
        verdict = "中性"
    mixed = len(bull) >= 1 and len(bear) >= 1
    if verdict == "中性":
        title = "多空分歧，區間整理" if mixed else "籌碼變化不大"
    else:
        title = f"短線{verdict}"
        if month_bias and ((month_bias > 0) != (verdict == "偏多")):
            title += "，選擇權有反向防守"
        elif mixed:
            title += "，但訊號不一致"
    return {"verdict": verdict, "title": title, "score": round(score, 1), "bear": bear, "bull": bull}


def biggest(cur):
    items = [(f"{r['name']}所有契約淨額" if r["name"].startswith("前") else r["name"], r["d"])
             for r in cur["net"] if r["d"] is not None]
    items += [(f"{r['name']}月選合成淨額", r["dnet"]) for r in cur["synth"] if r["dnet"] is not None]
    items.sort(key=lambda x: -abs(x[1]))
    return [{"name": n, "d": d} for n, d in items[:8] if abs(d) >= FLAT]


def watch(cur):
    out = []
    w = cur.get("walls", {}).get("weekly") or cur.get("walls", {}).get("monthly")
    price = cur["fut"]["close"]
    if w and w["res"]:
        r = min(w["res"], key=lambda kv: kv[0])
        out.append({"side": "多方", "text": f"站上 {fmt(r[0])}（{w['contract']} Call 未平倉 {fmt(r[1])} 口），且前五、前十大淨額回升，反彈才較完整。"})
    if w and w["sup"]:
        s = max(w["sup"], key=lambda kv: kv[0])
        out.append({"side": "空方", "text": f"跌破 {fmt(s[0])}（{w['contract']} Put 未平倉 {fmt(s[1])} 口），須提防往下一個 Put 大量區測試。"})
    out.append({"side": "籌碼", "text": "觀察前五、前十大所有契約淨額是否延續目前方向，以及特法月選合成淨額的增減。"})
    return out


def oneliner(cur, a):
    fut = cur["fut"]
    t5 = next((r for r in cur["net"] if r["name"] == "前五大"), None)
    parts = [f"{cur['date'][5:].replace('-', '/')} 台指期收 {fmt(fut['close'])}"]
    if t5 and t5["d"] is not None:
        parts.append(f"前五大所有契約淨額 {fmt(t5['v'])}（{sgn(t5['d'])}）")
    s = next((r for r in cur["synth"] if r["name"] == "前十特法"), None)
    if s and s["dnet"] is not None:
        parts.append(f"前十特法月選合成淨額 {fmt(s['net'])}（{sgn(s['dnet'])}）")
    return "，".join(parts) + f"，整體判讀為「{a['title']}」。"


# ======================= 主流程 =======================

def build(large, inst, strikes, fut_daily, index_by_date):
    dates = sorted(d for d in large if d in inst)
    if not dates:
        return None
    d = dates[-1]
    prev = dates[-2] if len(dates) > 1 else None
    n_now = nets_for(large[d], inst[d])
    n_prev = nets_for(large[prev], inst[prev]) if prev else {}
    net = [{"name": g, "v": n_now[g], "d": (n_now[g] - n_prev[g]) if g in n_prev else None}
           for g in GROUPS if g in n_now]
    for r in net:
        r["tag"] = tag(r["v"], r["d"])

    month = near_month(large[d])
    det, total = month_detail(large[d], month) if month else ({}, {})
    det_p, _ = month_detail(large[prev], month) if (prev and month) else ({}, {})
    cp_rows, synth = [], []
    for name, _, _ in LT_GROUPS:
        if name not in det:
            continue
        cur, old = det[name], det_p.get(name)
        dd = lambda cp, i: (cur[cp][i] - old[cp][i]) if old and cp in old else None  # noqa: E731
        cp_rows.append({"name": name,
                        "cb": cur["C"][0], "dcb": dd("C", 0), "cs": cur["C"][1], "dcs": dd("C", 1),
                        "pb": cur["P"][0], "dpb": dd("P", 0), "ps": cur["P"][1], "dps": dd("P", 1)})
        lo, sh = cur["C"][0] + cur["P"][1], cur["C"][1] + cur["P"][0]
        if old:
            lo0, sh0 = old["C"][0] + old["P"][1], old["C"][1] + old["P"][0]
        synth.append({"name": name, "long": lo, "dlong": (lo - lo0) if old else None,
                      "short": sh, "dshort": (sh - sh0) if old else None,
                      "net": lo - sh, "dnet": ((lo - sh) - (lo0 - sh0)) if old else None})

    f = fut_daily.get(d)
    fut = {"close": None, "chg": None, "pct": None}
    if f:
        close = f[3]
        chg = f[5] if len(f) > 5 and f[5] is not None else None
        if chg is None:
            keys = sorted(k for k in fut_daily if k < d)
            chg = close - fut_daily[keys[-1]][3] if keys else None
        fut = {"close": close, "chg": chg, "pct": (chg / (close - chg) * 100) if chg is not None else None}
    idx = index_by_date.get(d)
    basis = (fut["close"] - idx["close"]) if (idx and fut["close"]) else None

    cur = {"date": d, "prev": prev, "month": month, "fut": fut, "index": idx, "basis": basis,
           "net": net, "cp": cp_rows, "synth": synth, "total": total}
    if strikes and fut["close"]:
        cur["walls"] = walls(strikes, d, fut["close"])
        tc = sum(sum(v["C"].values()) for v in strikes.values())
        tp = sum(sum(v["P"].values()) for v in strikes.values())
        cur["pcr"] = round(tp / tc * 100, 1) if tc else None
    a = analyze(cur)
    cur["analysis"] = a
    cur["biggest"] = biggest(cur)
    cur["watch"] = watch(cur)
    cur["oneliner"] = oneliner(cur, a)

    hist = []
    for k in dates[-HIST:][::-1]:
        i = dates.index(k)
        nn = nets_for(large[k], inst[k])
        pp = nets_for(large[dates[i - 1]], inst[dates[i - 1]]) if i > 0 else {}
        fk = fut_daily.get(k)
        hist.append({"date": k, "fut": fk[3] if fk else None,
                     "rows": {g: [nn[g], (nn[g] - pp[g]) if g in pp else None] for g in GROUPS if g in nn}})
    cur["history"] = hist
    return cur


def run(root: Path, fetch=True):
    store = root / "data" / "store" / "chips"
    today = datetime.now(TPE).date()
    if fetch:
        start = (today - timedelta(days=28)).strftime("%Y/%m/%d")
        end = today.strftime("%Y/%m/%d")
        try:
            large = parse_large(_post("/cht/3/largeTraderOptDown", {"queryStartDate": start, "queryEndDate": end}))
            # 三大法人：結束日不能晚於最新資料日，否則期交所回傳錯誤
            last_l = max(large).replace("-", "/") if large else end
            inst = parse_inst(_post("/cht/3/callsAndPutsDateDown",
                                    {"queryStartDate": start, "queryEndDate": last_l, "commodityId": "TXO"}))
            if not inst:
                raise RuntimeError("三大法人選擇權資料解析不到內容")
            old_l, old_i = _load(store / "large.json", {}), _load(store / "inst.json", {})
            old_l.update(large)
            old_i.update(inst)
            keep = sorted(old_l)[-40:]
            _dump(store / "large.json", {k: old_l[k] for k in keep})
            _dump(store / "inst.json", {k: old_i[k] for k in sorted(old_i)[-40:]})
            latest = max(d for d in old_l if d in old_i)
            ymd = latest.replace("-", "/")
            strikes = parse_strikes(_post("/cht/3/optDataDown", {
                "down_type": "1", "commodity_id": "TXO", "commodity_id2": "",
                "queryStartDate": ymd, "queryEndDate": ymd}))
            if strikes:
                _dump(store / "strikes.json", {"date": latest, "data": strikes})
            log(f"大額 {len(old_l)} 天、法人 {len(old_i)} 天，最新 {latest}，履約價合約 {len(strikes)} 個")
        except Exception as e:  # noqa: BLE001
            log("抓取失敗：", e)
    large, inst = _load(store / "large.json", {}), _load(store / "inst.json", {})
    st = _load(store / "strikes.json", {})
    fut_daily = _load(root / "data" / "store" / "txf" / "daily.json", {})
    index_by_date = {}
    for p in sorted((root / "data" / "store" / "daily").glob("*.json"))[-HIST - 5:]:
        dd = _load(p)
        ix = (dd.get("index") or {}).get("twse")
        if ix:
            index_by_date[dd["date"]] = ix
    dates = sorted(d for d in large if d in inst)
    strikes = st.get("data") if dates and st.get("date") == dates[-1] else None
    # json 的 key 是字串，履約價轉回數字
    if strikes:
        strikes = {k: {"exp": v["exp"], "C": {int(a): b for a, b in v["C"].items()},
                       "P": {int(a): b for a, b in v["P"].items()}} for k, v in strikes.items()}
    large = {d: {cp: {m: {int(t): v for t, v in mm.items()} for m, mm in x.items()} for cp, x in day.items()}
             for d, day in large.items()}
    out = build(large, inst, strikes, fut_daily, index_by_date)
    if out:
        out["generated_at"] = datetime.now(TPE).isoformat(timespec="minutes")
        _dump(root / "data" / "chips.json", out)
        log(out["oneliner"])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(Path(__file__).resolve().parent.parent, fetch=not a.no_fetch)
