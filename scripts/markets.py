"""其他商品的波浪推算與波段布局：加權指數、櫃買指數、小娜（MNQ）、1oz 黃金、布蘭特原油。

資料：
  Yahoo Finance chart API：^TWII、MNQ=F、1OZ=F、BZ=F 的日線與 60／30／5 分 K。
    1OZ=F 在 Yahoo 沒有日線歷史，改由 60 分 K 依 CME 交易日（美東 18:00 換日）組成日 K。
  櫃買中心官方月查詢：櫃買指數日線（盤中 K 線官方沒有提供）。
輸出：
  data/markets/<id>.json        波浪推算（格式同 data/wave.json）＋波段布局
  data/store/markets/<id>_D.json 日線（供其他程式使用，例如期現價差）
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

import strategy
import txf

TPE = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

MARKETS = [
    {"id": "TWII", "name": "加權指數", "yahoo": "^TWII", "session": "現貨盤（09:00–13:30）",
     "pt": {"點數": 1}, "unit": "點"},
    {"id": "TPEX", "name": "櫃買指數", "tpex": True, "session": "現貨盤",
     "pt": {"點數": 1}, "unit": "點"},
    {"id": "MNQ", "name": "小娜（微型那斯達克 MNQ）", "yahoo": "MNQ=F", "session": "CME 全時段",
     "pt": {"MNQ（美元）": 2, "NQ（美元）": 20}, "unit": "美元"},
    {"id": "GOLD", "name": "1oz 黃金（1OZ）", "yahoo": "1OZ=F", "daily_from_60m": True, "session": "CME 全時段",
     "pt": {"1OZ（美元）": 1, "MGC 10oz（美元）": 10}, "unit": "美元"},
    {"id": "BRENT", "name": "布蘭特原油（BZ）", "yahoo": "BZ=F", "session": "全時段",
     "pt": {"BZ 1000 桶（美元）": 1000}, "unit": "美元"},
]

_s = requests.Session()
_s.headers.update(UA)


def log(*a):
    print("[markets]", *a, flush=True)


def _dump(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _get(url, params=None, tries=4):
    last = None
    for i in range(tries):
        try:
            r = _s.get(url, params=params, timeout=40)
            if r.status_code == 429:
                raise RuntimeError("429")
            r.raise_for_status()
            time.sleep(1.0)
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"GET {url} 失敗：{last}")


# ---------------- Yahoo ----------------

_crumb = {}


def _yahoo_json(symbol, params):
    """先試 query1，再試 query2；被擋時取得 cookie／crumb 後重試。"""
    errs = []
    for host in ("query1", "query2"):
        try:
            return _get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}", params, tries=2).json()
        except Exception as e:  # noqa: BLE001
            errs.append(f"{host}: {e}")
    try:
        if not _crumb:
            _s.get("https://fc.yahoo.com", timeout=20)
            _crumb["v"] = _s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=20).text.strip()
        p = dict(params, crumb=_crumb["v"])
        return _get(f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}", p, tries=2).json()
    except Exception as e:  # noqa: BLE001
        errs.append(f"crumb: {e}")
    raise RuntimeError("；".join(errs))


def yahoo(symbol, interval, rng):
    """回傳 (bars [(utc_ts, o, h, l, c)], gmtoffset 秒)。略過沒有價格的 K 棒。"""
    j = _yahoo_json(symbol, {"interval": interval, "range": rng, "includePrePost": "false"})
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError(f"{symbol} {interval} 無資料：{(j.get('chart') or {}).get('error')}")
    r = res[0]
    q = r["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(r.get("timestamp") or []):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append((t, o, h, l, c))
    return out, r["meta"].get("gmtoffset", 0)


def to_map_intraday(bars):
    """盤中 K 棒：key 用台北時間 'YYYY-MM-DD HH:MM'。"""
    return {datetime.fromtimestamp(t, TPE).strftime("%Y-%m-%d %H:%M"): [o, h, l, c, 0] for t, o, h, l, c in bars}


def to_map_daily(bars, gmt):
    """日 K：key 用交易所當地日期。"""
    return {datetime.fromtimestamp(t + gmt, timezone.utc).strftime("%Y-%m-%d"): [o, h, l, c, 0]
            for t, o, h, l, c in bars}


def daily_from_hourly(bars, gmt):
    """CME 交易日：美東 18:00 開盤，屬於下一個日期。"""
    out = {}
    for t, o, h, l, c in sorted(bars):
        d = datetime.fromtimestamp(t + gmt + 6 * 3600, timezone.utc).strftime("%Y-%m-%d")
        b = out.get(d)
        if b is None:
            out[d] = [o, h, l, c, 0]
        else:
            b[1], b[2], b[3] = max(b[1], h), min(b[2], l), c
    return out


def resample(m5: dict, minutes: int) -> dict:
    out = {}
    for k in sorted(m5):
        hh, mm = int(k[11:13]), int(k[14:16])
        mins = hh * 60 + mm
        key = f"{k[:11]}{(mins // minutes * minutes) // 60:02d}:{(mins // minutes * minutes) % 60:02d}"
        o, h, l, c, v = m5[k]
        b = out.get(key)
        if b is None:
            out[key] = [o, h, l, c, v]
        else:
            b[1], b[2], b[3] = max(b[1], h), min(b[2], l), c
    return out


# ---------------- 櫃買指數 ----------------

def tpex_daily(months=26):
    out = {}
    d = date.today().replace(day=1)
    for _ in range(months):
        j = _get("https://www.tpex.org.tw/www/zh-tw/indexInfo/inx",
                 {"date": d.strftime("%Y/%m/01"), "response": "json"}).json()
        for t in j.get("tables") or []:
            for r in t.get("data") or []:
                try:
                    o, h, l, c = (float(str(x).replace(",", "")) for x in r[1:5])
                except ValueError:
                    continue
                out[r[0].replace("/", "-")] = [o, h, l, c, 0]
        d = (d - timedelta(days=1)).replace(day=1)
    return out


# ---------------- 主流程 ----------------

def fetch(m):
    tfs = {}
    if m.get("tpex"):
        tfs["D"] = tpex_daily()
        return tfs
    sym = m["yahoo"]
    h60, gmt = yahoo(sym, "60m", "730d")
    tfs["H"] = to_map_intraday(h60)
    if m.get("daily_from_60m"):
        tfs["D"] = daily_from_hourly(h60, gmt)
    else:
        d, gmt_d = yahoo(sym, "1d", "2y")
        tfs["D"] = to_map_daily(d, gmt_d)
    m5, _ = yahoo(sym, "5m", "60d")
    tfs["M5"] = to_map_intraday(m5)
    try:
        m30, _ = yahoo(sym, "30m", "60d")
        tfs["M30"] = to_map_intraday(m30)
    except RuntimeError:
        tfs["M30"] = resample(tfs["M5"], 30)
    # 5 分 K 只留最近約 6000 根
    keep = sorted(tfs["M5"])[-6000:]
    tfs["M5"] = {k: tfs["M5"][k] for k in keep}
    return tfs


def analyze_market(m, tfs):
    W = {"contract": m["name"], "id": m["id"], "unit": m["unit"],
         "generated_at": datetime.now(TPE).isoformat(timespec="minutes")}
    for tf in ("D", "H", "M30", "M5"):
        if tf in tfs:
            res = txf.analyze(tfs[tf], tf)
            if res:
                res["session"] = m["session"]
                res["partial"] = False
                W[tf] = res
    plans = strategy.build_plans(W, {tf: tfs[tf] for tf in ("D", "H", "M30") if tf in tfs}, m["pt"])
    W["strategy"] = plans
    return W


def run(root: Path):
    out_dir = root / "data" / "markets"
    index, errors = [], {}
    for m in MARKETS:
        try:
            tfs = fetch(m)
            W = analyze_market(m, tfs)
            _dump(out_dir / f"{m['id']}.json", W)
            _dump(root / "data" / "store" / "markets" / f"{m['id']}_D.json",
                  {k: tfs["D"][k] for k in sorted(tfs["D"])[-60:]})
            d = W.get("D", {})
            log(m["name"], "日線", len(tfs.get("D", {})), "根", "；", (d.get("counts") or [{}])[0].get("now", "—"))
            index.append({"id": m["id"], "name": m["name"], "tfs": [tf for tf in ("D", "H", "M30", "M5") if tf in W]})
        except Exception as e:  # noqa: BLE001
            log(m["name"], "失敗，保留舊資料：", e)
            errors[m["id"]] = str(e)[:500]
            old = out_dir / f"{m['id']}.json"
            if old.exists():
                o = json.loads(old.read_text(encoding="utf-8"))
                index.append({"id": m["id"], "name": m["name"],
                              "tfs": [tf for tf in ("D", "H", "M30", "M5") if tf in o], "stale": True})
    if index:
        _dump(out_dir / "index.json", index)
    _dump(out_dir / "_errors.json", {"at": datetime.now(TPE).isoformat(timespec="minutes"), "errors": errors})


if __name__ == "__main__":
    run(Path(__file__).resolve().parent.parent)
