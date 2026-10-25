"""個股日線波浪推算與波段布局（上市市值前 150 大，約等於台灣 50 ＋中型 100）。

資料（證交所，皆為公開資料）：
  每日：/rwd/zh/afterTrading/STOCK_DAY_ALL   一次取得所有上市股票當天的開高低收
  補歷史：/rwd/zh/afterTrading/STOCK_DAY     單一股票單月日線（新加入的股票補約 24 個月）
  名單：openapi t187ap03_L（已發行股數）× 收盤價 = 市值，取前 150 大普通股；每月更新一次，
        曾經納入的股票不會刪除資料。
輸出：
  data/stocks/index.json          [{code, name}]
  data/stocks/<代號>.json         日線波浪推算＋波段布局（格式同 data/markets/<id>.json）
  data/store/stocks/<代號>.json   日線原始資料（最近 600 根）

  python scripts/stocks.py               每日更新（含少量補歷史）
  python scripts/stocks.py --backfill N  補歷史最多 N 分鐘（另外的工作流程使用）
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import markets
import strategy
import txf

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "store" / "stocks"
OUT = ROOT / "data" / "stocks"
TWSE = "https://www.twse.com.tw/rwd/zh/afterTrading"
TOP_N = 150
HIST_MONTHS = 24
PT = {"股數（1 張 = 1000 股）": 1000}


def log(*a):
    print("[stocks]", *a, flush=True)


def _load(p: Path, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _num(x):
    s = str(x).replace(",", "").strip()
    if s in ("", "--", "---", "X"):
        raise ValueError(s)
    return float(s)


def _roc(d: str) -> str:
    y, m, dd = re.split(r"[/-]", d.strip())
    return f"{int(y) + 1911}-{int(m):02d}-{int(dd):02d}"


def _col(fields, *names):
    for n in names:
        for i, f in enumerate(fields):
            if n in f:
                return i
    raise KeyError(names)


def day_all():
    """當天所有上市股票：回傳 (日期, {代號: (名稱, [o,h,l,c,v])})。"""
    j = markets._get(f"{TWSE}/STOCK_DAY_ALL", {"response": "json"}).json()
    fields = j.get("fields") or []
    rows = j.get("data") or []
    d = j.get("date") or ""
    d = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else date.today().isoformat()
    ic, inm = _col(fields, "代號"), _col(fields, "名稱")
    io, ih, il, icl, iv = (_col(fields, "開盤"), _col(fields, "最高"), _col(fields, "最低"),
                           _col(fields, "收盤"), _col(fields, "成交股數"))
    out = {}
    for r in rows:
        try:
            out[r[ic].strip()] = (r[inm].strip(), [_num(r[io]), _num(r[ih]), _num(r[il]), _num(r[icl]), _num(r[iv])])
        except (ValueError, IndexError):
            continue
    return d, out


def month_bars(code: str, ym: date) -> dict:
    j = markets._get(f"{TWSE}/STOCK_DAY", {"date": ym.strftime("%Y%m01"), "stockNo": code, "response": "json"}).json()
    fields = j.get("fields") or []
    out = {}
    if not fields:
        return out
    idt, io, ih, il, icl, iv = (_col(fields, "日期"), _col(fields, "開盤"), _col(fields, "最高"),
                                _col(fields, "最低"), _col(fields, "收盤"), _col(fields, "成交股數"))
    for r in j.get("data") or []:
        try:
            out[_roc(r[idt])] = [_num(r[io]), _num(r[ih]), _num(r[il]), _num(r[icl]), _num(r[iv])]
        except (ValueError, IndexError):
            continue
    return out


def universe(closes: dict) -> list[tuple[str, str]]:
    """市值前 TOP_N 大的普通股（4 位數代號）。名單每月重算一次。"""
    meta_p = STORE / "_universe.json"
    meta = _load(meta_p, {})
    month = date.today().strftime("%Y-%m")
    if meta.get("month") == month and meta.get("list"):
        return [tuple(x) for x in meta["list"]]
    try:
        info = markets._get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L").json()
        shares = {}
        for r in info:
            code = str(r.get("公司代號", "")).strip()
            k = next((k for k in r if "已發行普通股數" in k), None)
            if code and k:
                try:
                    shares[code] = _num(r[k])
                except ValueError:
                    pass
        caps = []
        for code, (name, bar) in closes.items():
            if re.fullmatch(r"[1-9]\d{3}", code) and code in shares:
                caps.append((bar[3] * shares[code], code, name))
        caps.sort(reverse=True)
        lst = [(c, n) for _, c, n in caps[:TOP_N]]
    except Exception as e:  # noqa: BLE001
        log("市值名單取得失敗，沿用舊名單：", e)
        lst = [tuple(x) for x in meta.get("list", [])]
    old = {c: n for c, n in meta.get("all", [])}
    for c, n in lst:
        old[c] = n
    meta = {"month": month, "list": lst, "all": sorted(old.items())}
    markets._dump(meta_p, meta)
    return lst


def backfill(codes: list[str], minutes: float):
    """補歷史：每檔補到 HIST_MONTHS 個月，依時間預算逐檔進行，下次接著補。"""
    t_end = time.time() + minutes * 60
    today = date.today().replace(day=1)
    months = []
    d = today
    for _ in range(HIST_MONTHS):
        months.append(d)
        d = (d - timedelta(days=1)).replace(day=1)
    done = 0
    for code in codes:
        p = STORE / f"{code}.json"
        data = _load(p, {})
        meta = data.pop("_done", [])
        need = [m for m in months if m.strftime("%Y-%m") not in meta and m != today]
        if not need:
            continue
        for m in need:
            if time.time() > t_end:
                break
            try:
                data.update(month_bars(code, m))
                meta.append(m.strftime("%Y-%m"))
            except Exception as e:  # noqa: BLE001
                log(code, m.strftime("%Y-%m"), "失敗：", e)
            time.sleep(1.5)
        keep = sorted(data)[-600:]
        data = {k: data[k] for k in keep}
        data["_done"] = sorted(set(meta))
        markets._dump(p, data)
        done += 1
        if time.time() > t_end:
            break
    left = sum(1 for c in codes if len(_load(STORE / f"{c}.json", {}).get("_done", [])) < HIST_MONTHS - 1)
    log(f"補歷史處理 {done} 檔，尚有 {left} 檔未補完")


def analyze_all(lst):
    idx = []
    for code, name in lst:
        data = _load(STORE / f"{code}.json", {})
        data.pop("_done", None)
        if len(data) < 120:
            continue
        m = {"id": code, "name": f"{code} {name}", "session": "上市股票", "pt": PT, "unit": "元"}
        try:
            W = markets.analyze_market(m, {"D": data})
            W["contract"] = f"{code} {name}"
            markets._dump(OUT / f"{code}.json", W)
            idx.append({"code": code, "name": name, "t": max(data)})
        except Exception as e:  # noqa: BLE001
            log(code, "推算失敗：", e)
    markets._dump(OUT / "index.json", idx)
    log("完成", len(idx), "檔")


def run(backfill_minutes: float = 6):
    d, rows = day_all()
    log("證交所日行情", d, len(rows), "檔")
    lst = universe(rows)
    for code, name in lst:
        p = STORE / f"{code}.json"
        data = _load(p, {})
        if code in rows:
            data[d] = rows[code][1]
        markets._dump(p, data)
    backfill([c for c, _ in lst], backfill_minutes)
    analyze_all(lst)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=float, default=None, help="只補歷史，最多幾分鐘")
    a = ap.parse_args()
    if a.backfill:
        _d, rows = day_all()
        lst = universe(rows)
        backfill([c for c, _ in lst], a.backfill)
        analyze_all(lst)
    else:
        run()
