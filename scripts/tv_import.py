"""從 Claude 對話紀錄（JSONL）擷取 TradingView 連接器回傳的 K 線，存成 data/store/tv/<代號>_<週期>.json。

用法：python scripts/tv_import.py <對話紀錄.jsonl>
只讀取 mcp-tv-get-ohlcv 的回傳結果，同一代號與週期取最後一次。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "store" / "tv"


def texts(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from texts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from texts(v)


def main(path):
    found = {}
    for line in open(path, encoding="utf-8"):
        if '"bars"' not in line or "get_ohlcv" not in line and "next_date_to" not in line and '"interval"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        for t in texts(rec):
            if not t.startswith('{"bars"'):
                continue
            try:
                j = json.loads(t)
            except ValueError:
                continue
            sym, iv, b = j.get("symbol"), j.get("interval"), j.get("bars")
            if not sym or not iv or not b:
                continue
            if isinstance(b, dict):   # columns
                rows = list(zip(b["t"], b["o"], b["h"], b["l"], b["c"]))
            else:
                rows = [(x["t"], x["o"], x["h"], x["l"], x["c"]) for x in b]
            key = (sym, iv)
            merged = {r[0]: r for r in found.get(key, [])}
            merged.update({r[0]: r for r in rows})
            found[key] = sorted(merged.values())
    OUT.mkdir(parents=True, exist_ok=True)
    for (sym, iv), rows in found.items():
        name = re.sub(r"[^A-Za-z0-9]+", "_", sym).strip("_") + "_" + iv
        p = OUT / f"{name}.json"
        old = {r[0]: r for r in json.loads(p.read_text())} if p.exists() else {}
        old.update({r[0]: list(r) for r in rows})
        p.write_text(json.dumps(sorted(old.values()), separators=(",", ":")))
        print(name, len(old))


if __name__ == "__main__":
    main(sys.argv[1])
