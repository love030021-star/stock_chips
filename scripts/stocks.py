"""全部上市、上櫃股票的日線波浪推算與波段布局。

資料（皆為公開、可依日期查詢的全市場日行情，一天一次請求即可取得所有股票）：
  上市：證交所 /rwd/zh/afterTrading/MI_INDEX?type=ALLBUT0999  「每日收盤行情」表
  上櫃：櫃買中心 /www/zh-tw/afterTrading/dailyQuotes           「上櫃股票行情」表
只收 4 位數代號的股票。補歷史時由新到舊逐日抓（約 24 個月），依時間預算分次補完。
輸出：
  data/stocks/index.json          [{code, name, m, t}]
  data/stocks/<代號>.json         日線波浪推算＋波段布局（格式同 data/markets/<id>.json）
  data/store/stocks/<代號>.json   {"name", "m", "bars": {日期: [o,h,l,c,v]}}（最近 600 根）
  data/store/stocks/_days.json    已抓過的日期（上市、上櫃分開）

  python scripts/stocks.py               每日更新：補最近缺的日期（時間預算 8 分鐘）後重算
  python scripts/stocks.py --backfill N  補歷史最多 N 分鐘後重算
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import markets

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "store" / "stocks"
OUT = ROOT / "data" / "stocks"
HIST_DAYS = 740          # 往回補約 24 個月（日曆天）
KEEP = 600
PT = {"股數（1 張 = 1000 股）": 1000}
CODE = re.compile(r"[1-9]\d{3}")
MKT = {"twse": "上市", "tpex": "上櫃"}


def log(*a):
    print("[stocks]", *a, flush=True)


def _load(p: Path, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _num(x):
    s = re.sub(r"<[^>]+>", "", str(x)).replace(",", "").strip()
    if s in ("", "--", "---", "X"):
        raise ValueError(s)
    return float(s)


def _tables(j):
    if j.get("fields"):
        yield j["fields"], j.get("data") or []
    for t in j.get("tables") or []:
        if t.get("fields"):
            yield t["fields"], t.get("data") or []


def _col(fields, *names):
    for n in names:
        for i, f in enumerate(fields):
            if n in f:
                return i
    raise KeyError(names)


def _parse(fields, rows, code_f, name_f):
    ic, inm = _col(fields, code_f), _col(fields, name_f)
    io, ih, il, icl, iv = (_col(fields, "開盤"), _col(fields, "最高"), _col(fields, "最低"),
                           _col(fields, "收盤"), _col(fields, "成交股數"))
    out = {}
    for r in rows:
        code = str(r[ic]).strip()
        if not CODE.fullmatch(code):
            continue
        try:
            out[code] = (str(r[inm]).strip(), [_num(r[io]), _num(r[ih]), _num(r[il]), _num(r[icl]), _num(r[iv])])
        except (ValueError, IndexError):
            continue
    return out


def twse_day(d: date) -> dict:
    j = markets._get("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                     {"date": d.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"}).json()
    for fields, rows in _tables(j):
        if any("證券代號" in f for f in fields) and rows:
            return _parse(fields, rows, "證券代號", "證券名稱")
    return {}


def tpex_day(d: date) -> dict:
    j = markets._get("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",
                     {"date": d.strftime("%Y/%m/%d"), "id": "", "response": "json"}).json()
    out = {}
    for fields, rows in _tables(j):
        if any("代號" in f for f in fields) and rows:
            out.update(_parse(fields, rows, "代號", "名稱"))
    return out


FETCH = {"twse": twse_day, "tpex": tpex_day}


class Store:
    """所有股票的日線放在記憶體，最後一次寫回。"""

    def __init__(self):
        self.s = {}
        for p in STORE.glob("*.json"):
            if p.name.startswith("_"):
                continue
            j = _load(p, {})
            if "bars" not in j:                       # 舊格式：{日期: bar, "_done": [...]}
                j.pop("_done", None)
                j = {"name": "", "m": "上市", "bars": j}
            self.s[p.stem] = j

    def add(self, mkt: str, d: str, rows: dict):
        for code, (name, bar) in rows.items():
            e = self.s.setdefault(code, {"name": name, "m": MKT[mkt], "bars": {}})
            e["bars"][d] = bar
            if d >= e.get("name_d", ""):
                e["name"], e["m"], e["name_d"] = name, MKT[mkt], d

    def save(self):
        STORE.mkdir(parents=True, exist_ok=True)
        for code, e in self.s.items():
            keys = sorted(e["bars"])[-KEEP:]
            # 一天一行，讓每日更新的差異只有新增的那一行
            body = ",\n".join(f'"{k}":{json.dumps(e["bars"][k], separators=(",", ":"))}' for k in keys)
            head = json.dumps({"name": e.get("name", ""), "m": e.get("m", ""), "name_d": e.get("name_d", "")},
                              ensure_ascii=False)[:-1]
            (STORE / f"{code}.json").write_text(f'{head},"bars":{{\n{body}\n}}}}\n', encoding="utf-8")


def fill(st: Store, minutes: float):
    """由新到舊補缺的交易日。今天若還沒有資料（收盤前）不記為已完成。"""
    days_p = STORE / "_days.json"
    done = _load(days_p, {})
    sets = {k: set(done.get(k, [])) for k in FETCH}
    t_end = time.time() + minutes * 60
    today = date.today()
    n = 0
    for back in range(HIST_DAYS):
        if time.time() > t_end:
            break
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        ds = d.isoformat()
        for mkt in FETCH:
            if ds in sets[mkt] or time.time() > t_end:
                continue
            try:
                rows = FETCH[mkt](d)
            except Exception as e:  # noqa: BLE001
                log(mkt, ds, "失敗：", e)
                time.sleep(3)
                continue
            if rows:
                st.add(mkt, ds, rows)
            if rows or back >= 1:            # 假日沒有資料也記錄，避免重抓
                sets[mkt].add(ds)
            n += 1
            time.sleep(1.2)
    markets._dump(days_p, {k: sorted(v) for k, v in sets.items()})
    left = 0
    for back in range(HIST_DAYS):
        d = today - timedelta(days=back)
        if d.weekday() < 5:
            left += sum(1 for mkt in FETCH if d.isoformat() not in sets[mkt])
    log(f"本次抓了 {n} 個日期，尚缺 {left} 個（上市＋上櫃分開計）")
    return left


def analyze_all(st: Store):
    idx, keep = [], set()
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    for code in sorted(st.s):
        e = st.s[code]
        bars = e["bars"]
        if len(bars) < 120 or max(bars) < cutoff:     # 資料不足，或已下市、長期停牌
            continue
        name = e.get("name") or code
        m = {"id": code, "name": f"{code} {name}", "session": e.get("m", ""), "pt": PT, "unit": "元"}
        try:
            W = markets.analyze_market(m, {"D": bars})
            W["contract"] = f"{code} {name}"
            W["market"] = e.get("m", "")
            markets._dump(OUT / f"{code}.json", W)
            idx.append({"code": code, "name": name, "m": e.get("m", ""), "t": max(bars)})
            keep.add(f"{code}.json")
        except Exception as ex:  # noqa: BLE001
            log(code, "推算失敗：", ex)
    for p in OUT.glob("*.json"):                     # 移除已不在名單的舊檔
        if not p.name.startswith("_") and p.name != "index.json" and p.name not in keep:
            p.unlink()
    markets._dump(OUT / "index.json", idx)
    log("完成", len(idx), "檔")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=float, default=None, help="補歷史最多幾分鐘")
    a = ap.parse_args(argv)
    try:
        st = Store()
        left = fill(st, a.backfill if a.backfill else 8)
        st.save()
        analyze_all(st)
        markets._dump(OUT / "_errors.json", {"at": datetime.now().isoformat(timespec="minutes"),
                                             "error": None, "missing_days": left})
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        print(tb, flush=True)
        markets._dump(OUT / "_errors.json", {"at": datetime.now().isoformat(timespec="minutes"), "error": tb[-3000:]})


def run():
    main([])


if __name__ == "__main__":
    main(sys.argv[1:])
