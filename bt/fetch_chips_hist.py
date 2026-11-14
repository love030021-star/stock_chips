"""回測用：從期交所補抓歷史選擇權籌碼（法人、大額交易人、台指期外資未平倉、各履約價未平倉）。只在 GitHub Actions 上跑。"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chips  # noqa: E402

OUT = Path("bt_out")
OUT.mkdir(exist_ok=True)
START, END = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])


def months():
    d = START
    while d <= END:
        e = min(END, (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))
        yield d, e
        d = e + timedelta(days=1)


def parse_fut(text):
    """三大法人－區分各期貨契約：台指期各身份別的未平倉多空淨額（口）。"""
    out = {}
    names = {"外資及陸資": "外資", "投信": "投信", "自營商": "自營商"}
    for line in text.splitlines():
        c = [x.strip() for x in line.split(",")]
        if len(c) < 14 or c[1] != "臺股期貨" or c[2] not in names:
            continue
        v = chips._i(c[13])
        if v is not None:
            out.setdefault(c[0].replace("/", "-"), {})[names[c[2]]] = v
    return out


def load(name):
    p = OUT / name
    return json.loads(p.read_text()) if p.exists() else {}


large, inst, fut = load("large.json"), load("inst.json"), load("fut.json")
for a, b in months():
    s, e = a.strftime("%Y/%m/%d"), b.strftime("%Y/%m/%d")
    try:
        large.update(chips.parse_large(chips._post("/cht/3/largeTraderOptDown", {"queryStartDate": s, "queryEndDate": e})))
        inst.update(chips.parse_inst(chips._post("/cht/3/callsAndPutsDateDown", {"queryStartDate": s, "queryEndDate": e, "commodityId": "TXO"})))
        fut.update(parse_fut(chips._post("/cht/3/futContractsDateDown", {"queryStartDate": s, "queryEndDate": e, "commodityId": "TXF"})))
        print(a, "ok", len(large), len(inst), len(fut), flush=True)
    except Exception as ex:  # noqa: BLE001
        print(a, "失敗", ex, flush=True)
for n, v in (("large.json", large), ("inst.json", inst), ("fut.json", fut)):
    (OUT / n).write_text(json.dumps(v, ensure_ascii=False, separators=(",", ":")))

# 各履約價未平倉：每天只留最近兩個到期契約、現價 ±4000 點
st = load("strikes.json")
days = sorted(d for d in inst if START.isoformat() <= d <= END.isoformat())
for d in days:
    if d in st:
        continue
    ymd = d.replace("-", "/")
    try:
        S = chips.parse_strikes(chips._post("/cht/3/optDataDown", {"down_type": "1", "commodity_id": "TXO", "commodity_id2": "",
                                                                   "queryStartDate": ymd, "queryEndDate": ymd}))
    except Exception as ex:  # noqa: BLE001
        print(d, "履約價失敗", ex, flush=True)
        continue
    d0 = d.replace("-", "")
    near = sorted([(k, v) for k, v in S.items() if v["exp"] > d0], key=lambda kv: kv[1]["exp"])[:2]
    rec = []
    for name, v in near:
        ks = sorted(set(v["C"]) | set(v["P"]))
        rec.append({"c": name, "exp": v["exp"], "r": [[k, v["C"].get(k, 0), v["P"].get(k, 0), v["Cpx"].get(k), v["Ppx"].get(k)] for k in ks]})
    st[d] = rec
    if len(st) % 20 == 0:
        (OUT / "strikes.json").write_text(json.dumps(st, separators=(",", ":")))
        print(d, "履約價", len(st), flush=True)
(OUT / "strikes.json").write_text(json.dumps(st, separators=(",", ":")))
print("完成", len(st))
