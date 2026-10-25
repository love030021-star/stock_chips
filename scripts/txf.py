"""台指期（TX 近月）資料抓取與艾略特波浪推算。

資料：
  日線  期交所「期貨每日交易行情」，一般交易時段（日盤），每天取成交量最大的月合約。
  分 K  期交所「前 30 個交易日期貨每筆成交資料」自行彙整成 5、30、60 分，含日盤與夜盤。
輸出：
  data/store/txf/daily.json、m5.json、m30.json、h60.json   K 棒原始資料
  data/wave.json                          網站使用的推算結果

  python scripts/txf.py            抓資料並重算
  python scripts/txf.py --no-fetch 只用既有資料重算
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

TPE = timezone(timedelta(hours=8))
BASE = "https://www.taifex.com.tw"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
SLEEP = 2.0
DAILY_KEEP = 600      # 日線保留天數
H60_KEEP = 2400       # 60 分 K 保留根數（約 125 個交易日）
DAILY_BACKFILL_MONTHS = 26

_s = requests.Session()
_s.headers.update(UA)


def log(*a):
    print("[txf]", *a, flush=True)


def _load(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _dump(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _req(method, url, tries=4, **kw):
    last = None
    for i in range(tries):
        try:
            r = _s.request(method, url, timeout=60, **kw)
            r.raise_for_status()
            time.sleep(SLEEP)
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(SLEEP * (i + 2))
    raise RuntimeError(f"{method} {url} 失敗：{last}")


def _f(s):
    s = str(s).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


# ======================= 日線 =======================

def fetch_daily_range(start: date, end: date) -> dict:
    """回傳 {YYYY-MM-DD: [o,h,l,c,v]}，每天取一般時段成交量最大的月合約。"""
    r = _req("POST", f"{BASE}/cht/3/futDataDown", data={
        "down_type": "1", "commodity_id": "TX", "commodity_id2": "",
        "queryStartDate": start.strftime("%Y/%m/%d"), "queryEndDate": end.strftime("%Y/%m/%d")})
    text = r.content.decode("cp950", errors="replace")
    if not text.startswith("交易日期"):
        if "<html" in text.lower() or "alert" in text:
            return {}
        raise RuntimeError("期交所日線格式不符：" + text[:120])
    best = {}
    for line in text.splitlines()[1:]:
        c = [x.strip() for x in line.split(",")]
        if len(c) < 18 or c[1] != "TX" or c[17] != "一般" or not re.fullmatch(r"\d{6}", c[2]):
            continue
        o, h, l, cl, v = (_f(c[i]) for i in (3, 4, 5, 6, 9))
        if None in (o, h, l, cl) or not v:
            continue
        d = c[0].replace("/", "-")
        if d not in best or v > best[d][4]:
            best[d] = [o, h, l, cl, v]
    return best


def update_daily(store: Path, today: date):
    path = store / "daily.json"
    data = _load(path, {})
    if len(data) < 300:
        log("補日線歷史")
        end = today
        for _ in range(DAILY_BACKFILL_MONTHS):
            start = end - timedelta(days=30)
            try:
                data.update(fetch_daily_range(start, end))
            except RuntimeError as e:
                log("  ", e)
            end = start - timedelta(days=1)
    else:
        data.update(fetch_daily_range(today - timedelta(days=30), today))
    keys = sorted(data)[-DAILY_KEEP:]
    data = {k: data[k] for k in keys}
    _dump(path, data)
    log(f"日線 {len(data)} 天，最新 {keys[-1] if keys else '無'}")


# ======================= 分 K（5、30、60 分） =======================

# 週期代號 -> (分鐘數, 檔名, 保留根數)
INTRADAY = {"M5": (5, "m5.json", 7000), "M30": (30, "m30.json", 3000), "H": (60, "h60.json", 2400)}


def list_tick_files() -> list[str]:
    html = _req("GET", f"{BASE}/cht/3/futPrevious30DaysSalesData").text
    names = sorted(set(re.findall(r"DailydownloadCSV/(Daily_\d{4}_\d{2}_\d{2})\.zip", html)))
    return names


def bucket(d: str, t: str, iv: int = 60) -> str | None:
    """成交日期、時間 -> K 棒開始時間 'YYYY-MM-DD HH:MM'。日盤自 08:45 起算，夜盤自 15:00 起算。
    收盤那一筆（13:45、05:00）併入最後一根。"""
    t = t.zfill(6)
    m = int(t[:2]) * 60 + int(t[2:4])
    if 525 <= m <= 825:                       # 08:45–13:45
        start = 525 + iv * min((m - 525) // iv, (300 - 1) // iv)
    elif m >= 900:                            # 15:00–23:59
        start = 900 + iv * ((m - 900) // iv)
    elif m <= 300:                            # 00:00–05:00
        start = iv * min(m // iv, (300 - 1) // iv)
    else:
        return None
    return f"{d[:4]}-{d[4:6]}-{d[6:8]} {start // 60:02d}:{start % 60:02d}"


def parse_ticks(raw: bytes, ivs=(5, 30, 60)) -> dict:
    """每筆成交 CSV -> {分鐘數: {bar_key: [o,h,l,c,v]}}，只用該檔成交量最大的 TX 月合約。"""
    rows, vol = [], {}
    for line in raw.split(b"\n"):
        if b",TX " not in line[:20]:
            continue
        c = line.split(b",")
        if len(c) < 6 or c[1].strip() != b"TX":
            continue
        mon = c[2].strip()
        if not re.fullmatch(rb"\d{6}", mon):
            continue
        try:
            p, q = float(c[4]), float(c[5])
        except ValueError:
            continue
        rows.append((mon, c[0].strip().decode(), c[3].strip().decode(), p, q))
        vol[mon] = vol.get(mon, 0) + q
    out = {iv: {} for iv in ivs}
    if not rows:
        return out
    main = max(vol, key=vol.get)
    for mon, d, t, p, q in rows:
        if mon != main:
            continue
        for iv in ivs:
            k = bucket(d, t, iv)
            if not k:
                continue
            bars = out[iv]
            b = bars.get(k)
            if b is None:
                bars[k] = [p, p, p, p, q]
            else:
                b[1] = max(b[1], p)
                b[2] = min(b[2], p)
                b[3] = p
                b[4] += q
    return out


def update_intraday(store: Path, today: date):
    data = {tf: _load(store / fn, {}) for tf, (_, fn, _) in INTRADAY.items()}
    done = set(_load(store / "tick_files.json", []))
    names = list_tick_files()
    if not names:
        raise RuntimeError("找不到每筆成交資料清單")
    today_s = today.strftime("Daily_%Y_%m_%d")
    # 最近 3 個檔案每次重抓：檔案可能先只放夜盤，日盤收完才補齊
    todo = [n for n in names if n not in done] + [n for n in names[-3:] if n in done]
    for n in todo:
        try:
            r = _req("GET", f"{BASE}/file/taifex/Dailydownload/DailydownloadCSV/{n}.zip")
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw = z.read(z.namelist()[0])
        except Exception as e:  # noqa: BLE001
            log("  ", n, "下載失敗：", e)
            continue
        res = parse_ticks(raw)
        for tf, (iv, _, _) in INTRADAY.items():
            data[tf].update(res[iv])
        if n < today_s:
            done.add(n)
        log(f"  {n}：60 分 {len(res[60])} 根")
    for tf, (iv, fn, keep) in INTRADAY.items():
        keys = sorted(data[tf])[-keep:]
        _dump(store / fn, {k: data[tf][k] for k in keys})
        log(f"{iv} 分 K {len(keys)} 根，最新 {keys[-1] if keys else '無'}")
    _dump(store / "tick_files.json", sorted(done)[-60:])
    (store / "h60_files.json").unlink(missing_ok=True)


# ======================= 波浪推算 =======================

def atr(bars, n=14):
    out, prev_c, a = [], None, None
    for o, h, l, c in ((b[1], b[2], b[3], b[4]) for b in bars):
        tr = h - l if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c))
        a = tr if a is None else (a * (n - 1) + tr) / n
        out.append(a)
        prev_c = c
    return out


def zigzag(bars, mult):
    """以 ATR 倍數為反轉門檻找轉折。回傳 (確認的轉折 [(i, price, 'H'/'L')], 目前延伸中的極值)。"""
    a = atr(bars)
    piv = []
    hi_i = lo_i = 0
    trend = 0
    for i in range(1, len(bars)):
        h, l = bars[i][2], bars[i][3]
        thr = a[i] * mult
        if trend == 0:
            if h > bars[hi_i][2]:
                hi_i = i
            if l < bars[lo_i][3]:
                lo_i = i
            if bars[hi_i][2] - bars[lo_i][3] >= thr:
                if hi_i > lo_i:
                    piv.append((lo_i, bars[lo_i][3], "L"))
                    trend, ext = 1, hi_i
                else:
                    piv.append((hi_i, bars[hi_i][2], "H"))
                    trend, ext = -1, lo_i
            continue
        if trend == 1:
            if h >= bars[ext][2]:
                ext = i
            elif bars[ext][2] - l >= a[ext] * mult:
                piv.append((ext, bars[ext][2], "H"))
                trend, ext = -1, i
        else:
            if l <= bars[ext][3]:
                ext = i
            elif h - bars[ext][3] >= a[ext] * mult:
                piv.append((ext, bars[ext][3], "L"))
                trend, ext = 1, i
    if trend == 0:
        return piv, None
    return piv, (ext, bars[ext][2] if trend == 1 else bars[ext][3], "H" if trend == 1 else "L")


def closeness(r, ideals, tol):
    return max(math.exp(-((r - t) / tol) ** 2) for t in ideals)


IMP_NEXT = {1: "2", 2: "3", 3: "4", 4: "5", 5: "A"}


def check_impulse(x, cur):
    """x：已正規化為上升方向的轉折價（x[0] 起點）。cur：進行中波段的極值（正規化）。
    回傳 (是否符合規則, 指引分數 0~1, 說明)。"""
    k = len(x) - 1
    w = [x[i + 1] - x[i] for i in range(k)]   # w1, -w2, w3, -w4, w5
    if w[0] <= 0:
        return False, 0, ""
    g = []
    if k >= 2:
        r2 = -w[1] / w[0]
        if x[2] <= x[0] or r2 <= 0:
            return False, 0, "第 2 浪跌破第 1 浪起點"
        g.append(closeness(r2, [0.5, 0.618], 0.13))
    if k >= 3:
        if x[3] <= x[1]:
            return False, 0, ""
        g.append(closeness(w[2] / w[0], [1.618, 2.618, 1.0], 0.3))
    if k >= 4:
        if x[4] <= x[1]:
            return False, 0, "第 4 浪與第 1 浪重疊"
        g.append(closeness(-w[3] / w[2], [0.382, 0.236, 0.5], 0.1))
    if k >= 5:
        if w[4] <= 0 or w[2] < min(w[0], w[4]):
            return False, 0, "第 3 浪最短"
        g.append(closeness(w[4] / w[0], [1.0, 0.618, 1.618], 0.22))
        if x[5] <= x[3]:
            g[-1] *= 0.5  # 失敗的第 5 浪
    # 進行中的波段不能違反規則
    if cur is not None:
        if k == 1 and cur <= x[0]:
            return False, 0, ""
        if k == 3 and cur <= x[1]:
            return False, 0, ""
        if k == 4 and w[2] < w[0] and cur - x[4] > w[2]:
            return False, 0, ""
        if k == 5 and cur > x[5]:
            return False, 0, ""
    return True, (sum(g) / len(g) if g else 0.4), ""


def check_zigzag(x, cur):
    """修正浪 A-B-C（正規化為 A 往上）。"""
    k = len(x) - 1
    A = x[1] - x[0]
    if A <= 0:
        return False, 0
    g = []
    if k >= 2:
        rb = (x[1] - x[2]) / A
        if not 0 < rb < 1:
            return False, 0
        g.append(closeness(rb, [0.5, 0.618, 0.382], 0.12))
    if k >= 3:
        C = x[3] - x[2]
        if x[3] <= x[1]:
            return False, 0
        g.append(closeness(C / A, [1.0, 1.618, 0.618], 0.22))
    if cur is not None and k == 2 and cur <= x[0]:
        return False, 0
    return True, sum(g) / len(g)


def significance(piv, s, d):
    """起點是否為附近的主要高低點：與前面同類轉折相比是否更極端。"""
    p = piv[s][1] * d
    prev = [piv[j][1] * d for j in range(s - 2, max(-1, s - 7), -2)]
    if not prev:
        return 0.5
    return sum(1 for q in prev if p <= q) / len(prev)


def candidates(piv, cur):
    n = len(piv)
    out = []
    for k in range(1, 6):
        s = n - 1 - k
        if s < 0:
            break
        pts = [piv[s + j][1] for j in range(k + 1)]
        d = 1 if pts[1] > pts[0] else -1
        x = [p * d for p in pts]
        c = cur[1] * d if cur else None
        ok, g, _ = check_impulse(x, c)
        if ok:
            sig = significance(piv, s, d)
            score = 0.55 * g + 0.25 * sig + 0.04 * k
            out.append({"kind": "impulse", "s": s, "k": k, "d": d, "score": score})
    for k in (2, 3):
        s = n - 1 - k
        if s < 0:
            continue
        pts = [piv[s + j][1] for j in range(k + 1)]
        d = 1 if pts[1] > pts[0] else -1
        x = [p * d for p in pts]
        c = cur[1] * d if cur else None
        ok, g = check_zigzag(x, c)
        if ok:
            sig = significance(piv, s, d)
            score = 0.55 * g + 0.25 * sig + 0.04 * (k + 1) - 0.02
            out.append({"kind": "zigzag", "s": s, "k": k, "d": d, "score": score})
    out.sort(key=lambda c: -c["score"])
    return out


def project(c, piv, last_close):
    """進行中波段的目標價與失效價（實際價格）。"""
    s, k, d = c["s"], c["k"], c["d"]
    x = [piv[s + j][1] * d for j in range(k + 1)]
    T = []
    up = d == 1
    if c["kind"] == "impulse":
        w1 = x[1] - x[0]
        if k == 1:
            T = [(f"第 1 浪的 {r:g}", x[1] - w1 * r) for r in (0.382, 0.5, 0.618, 0.786)]
            inv, note = x[0], ("跌破第 1 浪起點" if up else "漲破第 1 浪起點")
        elif k == 2:
            T = [(f"第 1 浪 × {r:g}", x[2] + w1 * r) for r in (1.0, 1.618, 2.618)]
            inv, note = x[0], ("跌破第 1 浪起點" if up else "漲破第 1 浪起點")
        elif k == 3:
            w3 = x[3] - x[2]
            T = [(f"第 3 浪的 {r:g}", x[3] - w3 * r) for r in (0.236, 0.382, 0.5)]
            inv, note = x[1], ("跌破第 1 浪高點" if up else "漲破第 1 浪低點")
        elif k == 4:
            T = [(f"第 1 浪 × {r:g}", x[4] + w1 * r) for r in (0.618, 1.0, 1.618)]
            T.append(("第 1–3 浪 × 0.618", x[4] + (x[3] - x[0]) * 0.618))
            inv, note = x[4], ("跌破第 4 浪低點" if up else "漲破第 4 浪高點")
        else:
            tot = x[5] - x[0]
            T = [(f"第 1–5 浪的 {r:g}", x[5] - tot * r) for r in (0.236, 0.382, 0.5, 0.618, 0.786)]
            inv, note = x[5], ("突破第 5 浪高點" if up else "跌破第 5 浪低點")
    else:
        A = x[1] - x[0]
        if k == 2:
            T = [(f"A 浪 × {r:g}", x[2] + A * r) for r in (0.618, 1.0, 1.618)]
            inv, note = x[0], ("跌破 A 浪起點" if up else "漲破 A 浪起點")
        else:
            span = x[3] - x[0]
            T = [(f"A–C 段的 {r:g}", x[3] - span * r) for r in (0.382, 0.618, 1.0, 1.618)]
            inv, note = x[3], ("突破 C 浪高點" if up else "跌破 C 浪低點")
    # 方向：進行中波段與前一波相反
    lastdir = 1 if x[-1] > x[-2] else -1
    nd = -lastdir  # 正規化空間的進行方向
    cur_n = last_close * d
    targets = []
    for name, v in T:
        reached = (v - cur_n) * nd <= 0
        targets.append({"name": name, "price": round(v * d), "reached": reached})
    targets.sort(key=lambda t: t["price"] * d * nd)
    return targets, {"price": round(inv * d), "note": note}


def labels_for(c):
    if c["kind"] == "impulse":
        return ["0", "1", "2", "3", "4", "5"][: c["k"] + 1]
    return ["0", "A", "B", "C"][: c["k"] + 1]


def describe(c, piv, times, targets, inv, tf_name):
    d = "上升" if c["d"] == 1 else "下降"
    k = c["k"]
    lab = labels_for(c)
    end_t, end_p = times[piv[c["s"] + k][0]], piv[c["s"] + k][1]
    start_t, start_p = times[piv[c["s"]][0]], piv[c["s"]][1]
    if c["kind"] == "impulse":
        pattern = f"{d}推動浪"
        now = f"第 {IMP_NEXT[k]} 浪" if k < 5 else "A 浪修正"
        head = (f"自 {start_t} 的 {start_p:,.0f} 起算為{pattern}，"
                f"第 {lab[-1]} 浪在 {end_t} 的 {end_p:,.0f} 結束，目前推算在{' ' if now[0].isascii() else ''}{now}。")
    else:
        pattern = "ABC 反彈" if c["d"] == 1 else "ABC 回檔"
        now = "C 浪" if k == 2 else "新一波第 1 浪"
        head = (f"自 {start_t} 的 {start_p:,.0f} 起為 {pattern}，"
                f"{lab[-1]} 浪在 {end_t} 的 {end_p:,.0f} 結束，目前推算在{' ' if now[0].isascii() else ''}{now}。")
    nxt = [t for t in targets if not t["reached"]]
    tail = ""
    if nxt:
        tail = f"下一個參考價位 {nxt[0]['price']:,}（{nxt[0]['name']}）。"
    tail += f"若{inv['note']} {inv['price']:,}，此推算失效。"
    return pattern, now, head + tail


MULTS = {"D": (2.0, 2.5, 3.0), "H": (2.5, 3.5, 4.5), "M30": (2.5, 3.5, 4.5), "M5": (3.0, 4.0, 5.5)}
TF_NAME = {"D": "日線", "H": "60 分 K", "M30": "30 分 K", "M5": "5 分 K"}
TF_SHOW = {"D": 160, "H": 200, "M30": 220, "M5": 240}


def analyze(bars_map: dict, tf: str):
    keys = sorted(bars_map)
    bars = [[k] + bars_map[k][:4] for k in keys]
    if len(bars) < 60:
        return None
    last_close = bars[-1][4]
    fmt = (lambda k: k.replace("-", "/")) if tf == "D" else (lambda k: k[5:].replace("-", "/"))
    times = [fmt(b[0]) for b in bars]
    # 用三種轉折門檻（不同波浪級數）各自數浪，再挑分數最高的兩種不同結論
    pool = []
    for mult in MULTS[tf]:
        piv, cur = zigzag(bars, mult)
        if len(piv) < 4:
            continue
        for c in candidates(piv, cur):
            pool.append((c["score"], mult, piv, cur, c))
    pool.sort(key=lambda t: -t[0])
    counts, seen, prim = [], set(), None
    for score, mult, piv, cur, c in pool:
        targets, inv = project(c, piv, last_close)
        pattern, now, text = describe(c, piv, times, targets, inv, tf)
        key = (pattern, now)
        if key in seen:
            continue
        seen.add(key)
        lab = labels_for(c)
        counts.append({
            "pattern": pattern, "now": now, "text": text,
            "confidence": round(min(score, 1) * 100), "threshold": f"{mult:g} 倍 ATR(14)",
            "points": [[bars[piv[c["s"] + j][0]][0], round(piv[c["s"] + j][1]), lab[j]]
                       for j in range(c["k"] + 1)],
            "targets": targets, "invalid": inv,
        })
        if prim is None:
            prim = (mult, piv, cur)
        if len(counts) == 2:
            break
    if prim is None:
        mult = MULTS[tf][-1]
        piv, cur = zigzag(bars, mult)
    else:
        mult, piv, cur = prim
    show = TF_SHOW[tf]
    start_i = max(0, len(bars) - show)
    if counts:
        first_pt = min(p[0] for c in counts for p in c["points"])
        start_i = min(start_i, max(0, keys.index(first_pt) - 5))
    start_i = max(start_i, len(bars) - 360)
    first = bars[start_i][0]
    return {
        "tf": tf,
        "name": TF_NAME[tf],
        "session": "日盤（08:45–13:45）" if tf == "D" else "日盤與夜盤",
        "last": {"t": bars[-1][0], "c": last_close},
        "threshold": f"{mult:g} 倍 ATR(14)",
        "bars": [[b[0]] + [round(v) for v in b[1:5]] for b in bars[start_i:]],
        "pivots": [[bars[i][0], round(p), t] for i, p, t in piv if bars[i][0] >= first]
                  + ([[bars[cur[0]][0], round(cur[1]), "cur"]] if cur else []),
        "counts": counts,
    }


def run(root: Path, fetch=True):
    store = root / "data" / "store" / "txf"
    today = datetime.now(TPE).date()
    if fetch:
        for fn in (update_daily, update_intraday):
            try:
                fn(store, today)
            except Exception as e:  # noqa: BLE001
                log(fn.__name__, "失敗：", e)
    out = {"generated_at": datetime.now(TPE).isoformat(timespec="minutes"), "contract": "臺股期貨（TX）近月"}
    for tf, fn in (("D", "daily.json"), ("H", "h60.json"), ("M30", "m30.json"), ("M5", "m5.json")):
        res = analyze(_load(store / fn, {}), tf)
        if res:
            out[tf] = res
            if res["counts"]:
                log(tf, res["counts"][0]["text"])
    if "D" in out or "H" in out:
        _dump(root / "data" / "wave.json", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(Path(__file__).resolve().parent.parent, fetch=not a.no_fetch)
