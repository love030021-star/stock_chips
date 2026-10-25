"""每日更新：抓當天（與缺漏的前幾天）資料存到 data/store，再產生網站用的 JSON。

用法：
  python scripts/update.py                 # 抓今天，歷史不足時自動往前補
  python scripts/update.py --date 2026-10-22
  python scripts/update.py --build-only    # 只用既有 store 重算
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sources as S  # noqa: E402
from build import build_site  # noqa: E402
import txf  # noqa: E402
import chips  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "store"
DAILY = STORE / "daily"
TDCC = STORE / "tdcc"
META = STORE / "meta.json"
TPE = timezone(timedelta(hours=8))
KEEP_DAYS = 45          # store 保留的交易日數
MIN_HISTORY = 10        # 計分需要的交易日數（5 日均量、連買天數、量增比較）
MAX_BACKFILL_CAL = 21   # 往前補資料最多回溯的日曆天數


def log(*a):
    print("[update]", *a, flush=True)


def dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def stored_days() -> list[str]:
    return sorted(p.stem for p in DAILY.glob("*.json"))


def refresh_meta(force=False):
    meta = load(META, {})
    age_ok = meta.get("_updated") and (
        datetime.now(TPE).date() - date.fromisoformat(meta["_updated"])).days < 7
    broken = any("\ufffd" in v.get("name", "") for m in ("twse", "tpex") for v in meta.get(m, {}).values())
    if age_ok and not force and not broken:
        return meta
    log("更新證券基本資料（產業別）")
    try:
        meta = {"_updated": datetime.now(TPE).date().isoformat(),
                "twse": S.isin_list(2), "tpex": S.isin_list(4)}
        dump(META, meta)
    except S.SourceError as e:
        log("基本資料更新失敗，沿用舊資料：", e)
    return meta


def fetch_day(d: date, meta) -> bool:
    """抓某一天的資料。休市回傳 False。"""
    key = S.roc(d)
    log("抓取", d.isoformat())
    tw_q, tw_idx = S.twse_quotes(d)
    if tw_q is None:
        log("  上市無行情，視為休市")
        return False
    tw_i = S.twse_insti(d) or {}
    tw_m = S.twse_margin(d)
    tw_dt = S.twse_daytrade(d) or {}
    tp_q = S.tpex_quotes(d) or {}
    tp_i = S.tpex_insti(d) or {}
    tp_m = S.tpex_margin(d)
    tp_dt = S.tpex_daytrade(d) or {}
    if tw_m is None or tp_m is None:
        # 融資資料較晚公布（約 21:30），還沒出來就先不存，等下一次排程
        raise S.SourceError("融資融券資料尚未公布")

    def merge(q, ins, mg, dt, universe):
        out = {}
        for code, rec in q.items():
            if universe and code not in universe:
                continue
            r = dict(rec)
            r.update(ins.get(code, {"fn": 0, "tn": 0, "dn": 0, "in": 0}))
            r.update(mg.get(code, {}))
            r["dt"] = dt.get(code)
            out[code] = r
        return out

    day = {
        "date": d.isoformat(),
        "index": {"twse": tw_idx},
        "twse": merge(tw_q, tw_i, tw_m, tw_dt, meta.get("twse")),
        "tpex": merge(tp_q, tp_i, tp_m, tp_dt, meta.get("tpex")),
    }
    log(f"  上市 {len(day['twse'])} 檔、上櫃 {len(day['tpex'])} 檔")
    if len(day["twse"]) < 300:
        raise S.SourceError("上市普通股筆數異常，停止更新")
    dump(DAILY / f"{key}.json", day)
    return True


def update_tdcc():
    try:
        ddate, data = S.tdcc_latest()
    except S.SourceError as e:
        log("集保資料抓取失敗：", e)
        return
    path = TDCC / f"{ddate}.json"
    if not path.exists():
        log("新的集保股權分散表", ddate)
        dump(path, data)
    snaps = sorted(TDCC.glob("*.json"))
    for p in snaps[:-8]:  # 保留 8 週
        p.unlink()


def prune():
    days = stored_days()
    for k in days[:-KEEP_DAYS]:
        (DAILY / f"{k}.json").unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD，預設為今天（台北時間）")
    ap.add_argument("--build-only", action="store_true")
    args = ap.parse_args()

    if not args.build_only:
        target = date.fromisoformat(args.date) if args.date else datetime.now(TPE).date()
        meta = refresh_meta()
        have = set(stored_days())
        if S.roc(target) not in have and target.weekday() < 5:
            try:
                fetch_day(target, meta)
            except S.SourceError as e:
                log("當天資料還不完整，等下一次排程：", e)
        # 歷史不足時往前補
        d = target - timedelta(days=1)
        while len(stored_days()) < MIN_HISTORY and (target - d).days <= MAX_BACKFILL_CAL:
            if d.weekday() < 5 and S.roc(d) not in set(stored_days()):
                try:
                    fetch_day(d, meta)
                except S.SourceError as e:
                    log("  補資料失敗：", e)
            d -= timedelta(days=1)
        update_tdcc()
        prune()

    build_site(ROOT)
    try:
        txf.run(ROOT, fetch=not args.build_only)
    except Exception as e:  # noqa: BLE001
        log("台指期波浪推算失敗：", e)
    try:
        chips.run(ROOT, fetch=not args.build_only)
    except Exception as e:  # noqa: BLE001
        log("選擇權籌碼分析失敗：", e)


if __name__ == "__main__":
    main()
