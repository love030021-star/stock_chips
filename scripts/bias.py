"""台指期價乖離（與你 TradingView 的「［小妹］最新正負價乖離＋接近提醒 5～9%」同一算法）。

均價 = 前 5 個已完成交易日的日 K 收盤平均（TradingView TAIFEX:TXF1! 日 K，不含正在進行的交易日）
正乖離 = 均價 ×（1 + N%），負乖離 = 均價 ×（1 − N%），只取 5% 與 6%。
TradingView 台指期日 K 從下午 3 點夜盤開盤起算，所以每天 15:00 新的交易日開始後，均價換成新的 5 天。
輸出 data/bias.json。
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "store" / "tv" / "TAIFEX_TXF1_1D.json"
OUT = ROOT / "data" / "bias.json"
TPE = timezone(timedelta(hours=8))
LENGTH = 5
LEVELS = (5, 6)


def main():
    rows = sorted(json.loads(SRC.read_text(encoding="utf-8")))
    now = datetime.now(TPE)

    def next_open(t):
        """K 棒 15:00 開盤，隔一個交易日（跳過週末）13:45 收盤；同一天 15:00 下一根開盤時，這根才算「前一個已完成交易日」。"""
        d = datetime.fromtimestamp(t, TPE).date() + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return datetime(d.year, d.month, d.day, 15, 0, tzinfo=TPE)

    done = [r for r in rows if now >= next_open(r[0])]
    use = done[-LENGTH:]
    if len(use) < LENGTH:
        return
    avg = sum(r[4] for r in use) / LENGTH
    day = lambda t: (next_open(t) - timedelta(hours=1, minutes=15)).strftime("%Y-%m-%d")   # 15:00 開盤的 K 棒屬於下一個交易日
    out = {
        "symbol": "TAIFEX:TXF1!", "length": LENGTH, "avg": round(avg, 2),
        "days": [[day(r[0]), r[4]] for r in use],
        "levels": {f"+{n}": round(avg * (1 + n / 100), 1) for n in LEVELS} | {f"-{n}": round(avg * (1 - n / 100), 1) for n in LEVELS},
        "bars": [[r[0], r[4]] for r in rows[-12:]],   # 網頁端依當下時間自己選 5 天，15:00 一到就換，不必等排程
        "generated_at": now.isoformat(timespec="minutes"),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("bias", out["avg"], out["levels"])


if __name__ == "__main__":
    main()
