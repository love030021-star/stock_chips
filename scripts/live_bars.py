"""把 TradingView 警示每 15 秒推到 Worker 的價格（Worker 已彙整成 5 分 K）併進 data/store/tv。

- 5 分 K：只補在既有資料最後一根之後（每小時排程抓的 TradingView 原始 K 線較準，不覆蓋）。
- 30 分、60 分 K：從合併後的 5 分 K 重新彙整「最後一根（可能未收）之後」的區段。
由「5 分布局更新」排程每 5 分鐘執行，接著重算布局。
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TV = ROOT / "data" / "store" / "tv"
WORKER = "https://tw-quote.love030021.workers.dev/bars"
FILES = {"MNQ": "CME_MINI_MNQ1", "YM": "CBOT_MINI_YM1", "GOLD": "COMEX_1OZ1",
         "BRENT": "ICEEUR_BRN1", "TX": "TAIFEX_TXF1"}


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def save(p: Path, rows):
    p.write_text(json.dumps(sorted(rows), separators=(",", ":")), encoding="utf-8")


def regroup(m5: list, rows: list, sec: int) -> list:
    """用 5 分 K 重新彙整 sec 秒的 K 線，從既有最後一根（含）開始。"""
    have = {r[0]: r for r in rows}
    start = max(have) if have else 0
    off = start % sec   # 沿用既有 K 線的起點（例如台指期 30 分 K 從 08:45 起算）
    buckets = {}
    for t, o, h, l, c in sorted(m5):
        b = (t - off) // sec * sec + off
        if b < start:
            continue
        x = buckets.get(b)
        if x is None:
            buckets[b] = [b, o, h, l, c]
        else:
            x[2], x[3], x[4] = max(x[2], h), min(x[3], l), c
    have.update(buckets)
    return list(have.values())


def main():
    since = int(time.time()) - 6 * 3600
    req = urllib.request.Request(f"{WORKER}?since={since}", headers={"User-Agent": "Mozilla/5.0 stock_chips-live5m", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        live = json.load(r)
    by = {}
    for x in live:
        by.setdefault(x["id"], []).append([int(x["t"]), x["o"], x["h"], x["l"], x["c"]])
    changed = 0
    for mid, prefix in FILES.items():
        new = by.get(mid)
        if not new:
            continue
        p5 = TV / f"{prefix}_5m.json"
        m5 = load(p5)
        last = max((r[0] for r in m5), default=0)
        add = [r for r in new if r[0] > last]
        # 最後一根若是 Worker 也有的同一根（TradingView 抓的時候還沒收），用較完整的 Worker 版
        if m5 and any(r[0] == last for r in new):
            w = next(r for r in new if r[0] == last)
            for i, r in enumerate(m5):
                if r[0] == last:
                    m5[i] = [last, r[1], max(r[2], w[2]), min(r[3], w[3]), w[4]]
        if not add and not m5:
            continue
        m5 = m5 + add
        save(p5, m5)
        for iv, sec in (("30m", 1800), ("1h", 3600)):
            p = TV / f"{prefix}_{iv}.json"
            save(p, regroup(m5, load(p), sec))
        changed += 1
        print(mid, "新增 5 分 K", len(add), "根")
    print("updated", changed)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001  Worker 暫時連不上就沿用既有資料，照常重算
        print("live bars skipped:", e, file=sys.stderr)
