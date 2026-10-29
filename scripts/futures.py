"""期交所指定月份日線：TX / SRF；合約之間不拼接、不代入連續近月。"""
from __future__ import annotations

import calendar
import csv
import io
import json
import math
import re
import time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path


import requests
import txf
import strategy

TPE = timezone(timedelta(hours=8))
SOURCE = "https://www.taifex.com.tw/cht/3/futDataDown"
PRODUCTS = {"TX": ("台指期貨", {"大台": 200, "小台": 50, "微台": 10}),
            "SRF": ("小型0050期貨", {"小型0050": 1000})}


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def parse_csv(text, product):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames or "到期月份(週別)" not in reader.fieldnames:
        raise ValueError("期交所未回傳日行情 CSV；保留原月份資料")
    rows = []
    for r in reader:
        month = (r.get("到期月份(週別)") or "").strip()
        if (r.get("契約") or "").strip() != product or not re.fullmatch(r"\d{6}", month):
            continue  # 價差契約（例如 202611/202612）不可視為單月份。
        session = (r.get("交易時段") or "").strip()
        if session not in ("一般", "盤後"):
            raise ValueError("交易時段格式異常；不推測日夜盤順序")
        day = date.fromisoformat(r["交易日期"].strip().replace("/", "-")).isoformat()
        values = []
        for key in ("開盤價", "最高價", "最低價", "收盤價", "成交量"):
            try:
                v = float(r[key].strip().replace(",", ""))
                values.append(v if math.isfinite(v) else None)
            except (ValueError, TypeError):
                values.append(None)
        rows.append({"day": day, "month": month, "session": session, "v": values})
    return rows


def valid_bar(r):
    o, h, l, c, v = r["v"]
    return all(x is not None and x > 0 for x in (o, h, l, c, v)) and h >= max(o, l, c) and l <= min(o, h, c)


def monthly_bars(rows, month, as_of):
    grouped = {}
    for r in rows:
        if r["month"] == month and r["day"] <= as_of and valid_bar(r):
            grouped.setdefault(r["day"], {})[r["session"]] = r["v"]
    bars = {}
    for day, sessions in sorted(grouped.items()):
        regular, night = sessions.get("一般"), sessions.get("盤後")
        first, last = night or regular, regular or night
        values = list(sessions.values())
        bars[day] = [first[0], max(v[1] for v in values), min(v[2] for v in values), last[3], sum(v[4] for v in values)]
    return bars


def make_model(product, month, rows, as_of, generated):
    name, pt = PRODUCTS[product]
    bars = monthly_bars(rows, month, as_of)
    daily = txf.analyze(bars, "D") if len(bars) >= 60 else None
    if bars and not daily:
        keys = sorted(bars)
        chart = [[k] + bars[k][:4] for k in keys]
        K, D = txf.kd(chart)
        daily = {"tf": "D", "name": "日線", "session": "全日盤（夜盤＋日盤）", "partial": False,
                 "last": {"t": keys[-1], "c": bars[keys[-1]][3]}, "threshold": "尚未推算",
                 "bars": chart, "pivots": [], "counts": [],
                 "kd": [[round(k, 1), round(d, 1)] for k, d in zip(K, D)] if len(bars) >= 9 else None,
                 "kd_now": txf.kd_now(K, D) if len(bars) >= 9 else None}
    wave = {"id": product, "contract": f"{name} {month[:4]}/{month[4:]}", "contract_month": month,
            "generated_at": generated, "source": "期交所指定月份日行情（每日公布，非即時）", "unit": "點" if product == "TX" else "元", "D": daily}
    plans = strategy.build_plans(wave, {"D": bars}, pt) if len(bars) >= 60 else None
    snapshot = None
    if bars:
        keys = sorted(bars)
        last = bars[keys[-1]]
        prev = bars[keys[-2]][3] if len(keys) > 1 else None
        snapshot = {"date": keys[-1], "open": last[0], "high": last[1], "low": last[2], "close": last[3], "volume": last[4],
                    "change_pct": (last[3] / prev - 1) * 100 if prev else None}
    meta = {"contract_mode": True, "market": product, "month": month, "as_of": as_of, "generated_at": generated,
            "bar_count": len(bars), "ready": len(bars) >= 60, "source_url": SOURCE, "snapshot": snapshot,
            "message": "指定月份日線；無分K資料，123進場訊號未啟用。" if len(bars) >= 60 else f"本月份有 {len(bars)} 根已收日K；滿60根才計算波浪與布局。"}
    return {"wave": wave, "strategy": plans, "meta": meta}


def month_start_offset(today, offset):
    n = today.year * 12 + today.month - 1 + offset
    y, m = divmod(n, 12)
    return date(y, m + 1, 1)


def fetch_rows(product, start, end):
    error = None
    for attempt in range(3):
        try:
            response = requests.post(SOURCE, data={"down_type": "1", "commodity_id": product, "commodity_id2": "",
                                     "queryStartDate": start.strftime("%Y/%m/%d"), "queryEndDate": end.strftime("%Y/%m/%d")},
                                     headers={"User-Agent": "stock_chips monthly futures daily data"}, timeout=40)
            response.raise_for_status()
            return parse_csv(response.content.decode("cp950", errors="replace"), product)
        except (requests.RequestException, ValueError) as exc:
            error = exc
            if attempt < 2:
                time.sleep(2 + attempt * 2)
    raise RuntimeError(f"{product} {start:%Y-%m} 行情下載失敗") from error


def update_product(root, product, now, fetch):
    today = now.date()
    cutoff = today if now.hour >= 14 else today - timedelta(days=1)
    all_rows = []
    for offset in range(-12, 1):
        start = month_start_offset(today, offset)
        end = min(today, date(start.year, start.month, calendar.monthrange(start.year, start.month)[1]))
        cache = root / "data" / "store" / "futures_months" / f"{product}-{start:%Y%m}.json"
        rows = read_json(cache, [])
        if fetch and (offset >= -1 or not rows):
            rows = fetch_rows(product, start, end)
            write_json(cache, rows)
            time.sleep(1)
        all_rows.extend(rows)
    regular = [r for r in all_rows if r["session"] == "一般" and r["day"] <= cutoff.isoformat()]
    if not regular:
        raise ValueError(f"{product} 無可用日行情；保留既有檔案")
    as_of = max(r["day"] for r in regular)
    months = sorted({r["month"] for r in regular if r["day"] == as_of})
    generated = now.isoformat(timespec="seconds")
    models = {m: make_model(product, m, all_rows, as_of, generated) for m in months}
    # 完整計算成功才更新公開檔；下載失敗不會清空現有合約選單。
    for month, model in models.items():
        write_json(root / "data" / f"futures-{product}-{month}.json", model)
    return {"name": PRODUCTS[product][0], "as_of": as_of, "generated_at": generated, "source_url": SOURCE,
            "months": [{"month": m, "label": f"{m[:4]}/{m[4:]}", "bar_count": models[m]["meta"]["bar_count"]} for m in months]}


def run(root, fetch=True):
    root = Path(root)
    path = root / "data" / "futures-contracts.json"
    catalog = read_json(path, {"schema": 1, "markets": {}})
    failures = []
    now = datetime.now(TPE)
    for product in PRODUCTS:
        try:
            catalog["markets"][product] = update_product(root, product, now, fetch)
            print(f"[futures] {product}: {catalog['markets'][product]['as_of']}，{len(catalog['markets'][product]['months'])} 個月份", flush=True)
        except Exception as exc:
            failures.append(product)
            print(f"[futures] {product}: {exc}；保留上次成功版本", flush=True)
    write_json(path, catalog)
    if failures:
        raise RuntimeError("指定月份更新失敗：" + ", ".join(failures))


if __name__ == "__main__":
    run(Path(__file__).resolve().parent.parent)

