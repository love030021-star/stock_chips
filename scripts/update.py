"""每日更新：台指期波浪、其他商品波浪、台指選擇權籌碼、波段布局。

  python scripts/update.py              抓資料並重算
  python scripts/update.py --build-only 只用既有資料重算台指期部分
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chips  # noqa: E402
import markets  # noqa: E402
import strategy  # noqa: E402
import txf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def log(*a):
    print("[update]", *a, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-only", action="store_true")
    args = ap.parse_args()
    steps = [
        ("台指期波浪推算", lambda: txf.run(ROOT, fetch=not args.build_only)),
        ("其他商品波浪推算", lambda: None if args.build_only else markets.run(ROOT)),
        ("台指選擇權籌碼", lambda: chips.run(ROOT, fetch=not args.build_only)),
        ("台指期波段布局", lambda: strategy.run(ROOT)),
    ]
    for name, fn in steps:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log(f"{name}失敗：", e)


if __name__ == "__main__":
    main()
