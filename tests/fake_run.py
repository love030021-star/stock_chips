"""離線測試：用與官方 API 相同格式的假資料跑完整流程（抓取 → 存檔 → 計分 → 輸出）。

  python tests/fake_run.py /tmp/somewhere
會把專案複製到指定資料夾後執行，不會動到正式 data/。
"""
import json
import random
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
dest = Path(sys.argv[1])
if dest.exists():
    shutil.rmtree(dest)
shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns(".git", "data"))
sys.path.insert(0, str(dest / "scripts"))

import sources as S  # noqa: E402
import update as U  # noqa: E402
import txf  # noqa: E402

txf.run = lambda root, fetch=True: None  # 台指期另外測試，這裡不連網

S.SLEEP = 0
random.seed(7)
INDS = ["半導體業", "電子零組件業", "電腦及週邊設備業", "光電業", "航運業", "金融保險業",
        "生技醫療業", "鋼鐵工業", "通信網路業", "其他電子業"]
TW = {f"{1100 + i * 7}": (f"上市{i:03d}", random.choice(INDS)) for i in range(420)}
TP = {f"{6000 + i * 9}": (f"上櫃{i:03d}", random.choice(INDS)) for i in range(360)}
TW["2330"] = ("台積電", "半導體業")
STATE = {}
for c in list(TW) + list(TP):
    STATE[c] = {"c": round(random.uniform(8, 600), 2), "base": random.uniform(300, 30000) * 1000,
                "mg": random.randint(200, 20000), "big": random.uniform(20, 95),
                "sh": random.randint(30, 3000) * 1_000_000}
STATE["2330"]["big"] = 99.4  # 測試千張大戶 99% 排除
HOLIDAY = {date(2026, 10, 9)}
DAYCACHE = {}


def fmt(x, d=2):
    return f"{x:,.{d}f}"


def day_data(d):
    if d in DAYCACHE:
        return DAYCACHE[d]
    out = {}
    for c, s in STATE.items():
        prev = s["c"]
        pct = max(-0.1, min(0.1, random.gauss(0.002, 0.03)))
        c_ = round(max(1, prev * (1 + pct)), 2)
        hi = round(max(prev, c_) * (1 + abs(random.gauss(0, 0.01))), 2)
        lo = round(min(prev, c_) * (1 - abs(random.gauss(0, 0.01))), 2)
        v = int(s["base"] * random.uniform(0.4, 3.5)) // 1000 * 1000
        inst = int(v * random.gauss(0.02, 0.08)) // 1000 * 1000
        f_ = int(inst * random.uniform(0.3, 1.2)) // 1000 * 1000
        t_ = int(inst * random.uniform(-0.2, 0.5)) // 1000 * 1000
        mgp = s["mg"]
        s["mg"] = max(0, mgp + random.randint(-400, 300))
        s["c"] = c_
        out[c] = dict(o=prev, h=hi, l=lo, c=c_, ch=round(c_ - prev, 2), v=v, a=int(v * c_),
                      inst=inst, f=f_, t=t_, d=inst - f_ - t_, mgp=mgp, mg=s["mg"],
                      dt=int(v * random.uniform(0.1, 0.6)) // 1000 * 1000)
    DAYCACHE[d] = out
    return out


def sign_html(ch):
    return "<p style= color:red>+</p>" if ch > 0 else "<p style= color:green>-</p>" if ch < 0 else " "


def fake_get(url, *, as_json=True, encoding=None, tries=4):
    m = re.search(r"date=(\d{8}|\d{4}/\d{2}/\d{2})", url)
    d = None
    if m:
        s = m.group(1).replace("/", "")
        d = date(int(s[:4]), int(s[4:6]), int(s[6:]))
    if d and (d in HOLIDAY or d.weekday() >= 5):
        return {"stat": "很抱歉，沒有符合條件的資料!"}
    if "isin.twse" in url:
        src = TW if "strMode=2" in url else TP
        rows = "".join(f"<tr><td>{c}　{n}</td><td>TW000{c}</td><td>2000/01/01</td>"
                       f"<td>x</td><td>{ind}</td><td>ESVUFR</td><td></td></tr>"
                       for c, (n, ind) in src.items())
        rows += "<tr><td>0050　元大台灣50</td><td>x</td><td>x</td><td>x</td><td></td><td>CEOGEU</td><td></td></tr>"
        return f"<table>{rows}</table>"
    if "tdcc" in url:
        lines = ["資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%"]
        for c, s in STATE.items():
            lines.append(f"20261022,{c:<6},15,10,{int(s['sh'] * s['big'] / 100)},{s['big']:.2f}")
            lines.append(f"20261022,{c:<6},17,999,{s['sh']},100.00")
        return "﻿" + "\r\n".join(lines)
    dd = day_data(d)
    if "MI_INDEX" in url:
        f = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價",
             "收盤價", "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價",
             "最後揭示賣量", "本益比"]
        data = [[c, TW[c][0], fmt(x["v"], 0), "1", fmt(x["a"], 0), fmt(x["o"]), fmt(x["h"]),
                 fmt(x["l"]), fmt(x["c"]), sign_html(x["ch"]), fmt(abs(x["ch"])), "", "", "", "", "0"]
                for c, x in dd.items() if c in TW]
        data.append(["0050", "元大台灣50", "1,000", "1", "1", "1", "1", "1", "1", " ", "0", "", "", "", "", ""])
        return {"stat": "OK", "tables": [
            {"title": "115年 價格指數(臺灣證券交易所)", "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"],
             "data": [["發行量加權股價指數", "46,041.43", "<p style ='color:green'>-</p>", "846.34", "-1.81", ""]]},
            {"title": "115年 每日收盤行情(全部)", "fields": f, "data": data}, {}]}
    if "T86" in url:
        f = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
             "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
             "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)",
             "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)",
             "自營商賣出股數(避險)", "自營商買賣超股數(避險)", "三大法人買賣超股數"]
        data = [[c, TW[c][0] + "   ", "0", "0", fmt(x["f"], 0), "0", "0", "0", "0", "0", fmt(x["t"], 0),
                 fmt(x["d"], 0), "0", "0", "0", "0", "0", "0", fmt(x["inst"], 0)]
                for c, x in dd.items() if c in TW]
        return {"stat": "OK", "fields": f, "data": data}
    if "MI_MARGN" in url:
        f = ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額", "買進", "賣出",
             "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"]
        data = [[c, TW[c][0], "1", "1", "0", fmt(x["mgp"], 0), fmt(x["mg"], 0), "1", "0", "0", "0", "0", "0", "1", "0", " "]
                for c, x in dd.items() if c in TW]
        return {"stat": "OK", "tables": [{"title": "信用交易統計", "fields": ["項目"], "data": []},
                                         {"title": "115年 融資融券彙總 (全部)", "fields": f, "data": data}]}
    if "TWTB4U" in url:
        f = ["證券代號", "證券名稱", "暫停現股賣出後現款買進當沖註記", "當日沖銷交易成交股數",
             "當日沖銷交易買進成交金額", "當日沖銷交易賣出成交金額"]
        data = [[c, TW[c][0], "", fmt(x["dt"], 0), "1", "1"] for c, x in dd.items() if c in TW]
        return {"stat": "OK", "tables": [{"title": "統計", "fields": ["當日沖銷交易總成交股數"], "data": [["1"]]},
                                         {"title": "標的", "fields": f, "data": data}]}
    if "dailyQuotes" in url:
        f = ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數",
             "最後買價", "最後買量(張數)", "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"]
        data = [[c, TP[c][0], fmt(x["c"]), f"{x['ch']:+.2f} ", fmt(x["o"]), fmt(x["h"]), fmt(x["l"]), "0",
                 fmt(x["v"], 0), fmt(x["a"], 0), "1", "", "", "", "", fmt(STATE[c]["sh"], 0), "", "", ""]
                for c, x in dd.items() if c in TP]
        return {"stat": "ok", "tables": [{"title": "上櫃股票行情", "fields": f, "data": data},
                                         {"title": "管理股票", "fields": f, "data": []}]}
    if "dailyTrade" in url:
        f = ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數合計"]
        data = []
        for c, x in dd.items():
            if c not in TP:
                continue
            row = [c, TP[c][0]] + ["0"] * 21 + [fmt(x["inst"], 0)]
            row[4] = row[10] = fmt(x["f"], 0)
            row[13] = fmt(x["t"], 0)
            row[22] = fmt(x["d"], 0)
            data.append(row)
        return {"stat": "ok", "tables": [{"title": "三大法人買賣明細資訊", "fields": f, "data": data}, {}]}
    if "margin/balance" in url:
        f = ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)", "資限額",
             "前券餘額(張)", "券賣", "券買", "券償", "券餘額", "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"]
        data = [[c, TP[c][0], fmt(x["mgp"], 0), "1", "1", "0", fmt(x["mg"], 0)] + ["0"] * 13
                for c, x in dd.items() if c in TP]
        return {"stat": "ok", "tables": [{"title": "上櫃股票融資融券餘額", "fields": f, "data": data}]}
    if "intraday/stat" in url:
        f = ["證券代號", "證券名稱", "暫停現股賣出後現款買進當沖註記", "當日沖銷交易成交股數",
             "當日沖銷交易買進成交金額", "當日沖銷交易賣出成交金額"]
        data = [[c, TP[c][0], " ", fmt(x["dt"], 0), "1", "1"] for c, x in dd.items() if c in TP]
        return {"stat": "ok", "tables": [{"title": "統計", "fields": ["當日沖銷交易總成交股數"], "data": [["1"]]},
                                         {"title": "", "fields": f, "data": data}]}
    raise AssertionError(url)


S._get = fake_get

# 先放一份「上週」集保快照，測試大戶週增減
prev_snap = {c: {"big": round(s["big"] - random.gauss(0, 0.6), 2), "sh": s["sh"]} for c, s in STATE.items()}
U.dump(U.TDCC / "20261016.json", prev_snap)

# 模擬連續兩個交易日排程
for d in ["2026-10-21", "2026-10-22"]:
    sys.argv = ["update.py", "--date", d]
    U.main()

latest = json.loads((dest / "data" / "latest.json").read_text())
assert latest["date"] == "2026-10-22", latest["date"]
assert latest["trading_days"] == 10, latest["trading_days"]
for m in ("twse", "tpex"):
    assert len(latest["picks"][m]) == 20
    assert all(p["close"] >= 10 for p in latest["picks"][m])
    assert "2330" not in [p["code"] for p in latest["picks"][m]]
    assert len(latest["volume"][m]) == 20 and len(latest["turnover"][m]) == 20
assert "0050" not in json.dumps(latest["volume"]), "ETF 不應出現"
assert len(latest["daytrade"]["long"]) == 20 and len(latest["daytrade"]["short"]) == 20
hist = json.loads((dest / "data" / "history.json").read_text())
assert len(hist["days"]) == 2
print("OK", latest["picks"]["twse"][0])
print("store days:", sorted(p.stem for p in (dest / "data/store/daily").glob("*.json")))
