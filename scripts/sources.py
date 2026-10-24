"""抓取證交所、櫃買中心、集保結算所的公開資料，整理成統一格式。

每個函式回傳 dict：證券代號 -> 欄位 dict。數量單位一律為「股」，
融資餘額為「張」（交易所原始單位）。查無資料（休市）回傳 None。
"""
from __future__ import annotations

import csv
import io
import re
import time
from datetime import date

import requests

UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}
TWSE = "https://www.twse.com.tw/rwd/zh"
TPEX = "https://www.tpex.org.tw/www/zh-tw"
SLEEP = 3.0  # 證交所對頻繁請求會封鎖 IP，每次請求間隔 3 秒

_session = requests.Session()
_session.headers.update(UA)


class SourceError(RuntimeError):
    pass


def _get(url: str, *, as_json: bool = True, encoding: str | None = None, tries: int = 4):
    last = None
    for i in range(tries):
        try:
            r = _session.get(url, timeout=40)
            r.raise_for_status()
            time.sleep(SLEEP)
            if encoding:
                r.encoding = encoding
            return r.json() if as_json else r.text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(SLEEP * (i + 2))
    raise SourceError(f"GET 失敗：{url}：{last}")


# ---------- 數字處理 ----------

def num(s) -> float | None:
    """'1,234.5' -> 1234.5；'--'、''、'X0.00' 之類無法解析的回傳 None。"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = re.sub(r"<[^>]+>", "", str(s)).replace(",", "").replace("%", "").strip()
    if s in ("", "--", "---", "-", "X", "除息", "除權", "除權息"):
        return None
    m = re.match(r"^[+-]?\d+(\.\d+)?$", s)
    return float(s) if m else None


def _sign_from_html(s: str) -> int:
    s = str(s)
    if "+" in s or "red" in s:
        return 1
    if "-" in re.sub(r"<[^>]+>", "", s) or "green" in s:
        return -1
    return 0


def _find_table(tables, *, title_kw: str | None = None, field_kw: str | None = None):
    for t in tables or []:
        if not t or not t.get("fields"):
            continue
        if title_kw and title_kw not in (t.get("title") or ""):
            continue
        if field_kw and not any(field_kw in f for f in t["fields"]):
            continue
        return t
    return None


def _idx(fields, kw, start=0):
    for i, f in enumerate(fields):
        if i >= start and kw in f.replace("\n", ""):
            return i
    raise SourceError(f"找不到欄位「{kw}」，實際欄位：{fields}")


def _ok(j) -> bool:
    return str(j.get("stat", "")).lower() == "ok"


def roc(d: date) -> str:
    return d.strftime("%Y%m%d")


def slash(d: date) -> str:
    return d.strftime("%Y/%m/%d")


# ---------- 證交所（上市） ----------

def twse_quotes(d: date):
    j = _get(f"{TWSE}/afterTrading/MI_INDEX?date={roc(d)}&type=ALLBUT0999&response=json")
    if not _ok(j):
        return None, None
    t = _find_table(j.get("tables"), title_kw="每日收盤行情")
    if not t:
        raise SourceError("MI_INDEX 沒有「每日收盤行情」表")
    f = t["fields"]
    ic, iv, ia = _idx(f, "證券代號"), _idx(f, "成交股數"), _idx(f, "成交金額")
    io_, ih, il, icl = _idx(f, "開盤價"), _idx(f, "最高價"), _idx(f, "最低價"), _idx(f, "收盤價")
    isg, idf = _idx(f, "漲跌(+/-)"), _idx(f, "漲跌價差")
    out = {}
    for r in t["data"]:
        code = r[ic].strip()
        diff = num(r[idf])
        chg = None if diff is None else diff * _sign_from_html(r[isg])
        out[code] = {
            "name": r[ic + 1].strip(),
            "o": num(r[io_]), "h": num(r[ih]), "l": num(r[il]), "c": num(r[icl]),
            "ch": chg, "v": num(r[iv]) or 0, "a": num(r[ia]) or 0,
        }
    idx = None
    t0 = _find_table(j.get("tables"), title_kw="價格指數(臺灣證券交易所)")
    if t0:
        for r in t0["data"]:
            if r[0].strip() == "發行量加權股價指數":
                sign = _sign_from_html(r[2])
                idx = {"name": "加權指數", "close": num(r[1]),
                       "chg": (num(r[3]) or 0) * sign, "pct": num(r[4])}
    return out, idx


def twse_insti(d: date):
    j = _get(f"{TWSE}/fund/T86?date={roc(d)}&selectType=ALLBUT0999&response=json")
    if not _ok(j):
        return None
    f = j["fields"]
    ic = _idx(f, "證券代號")
    i_f = _idx(f, "外陸資買賣超股數(不含外資自營商)")
    i_fd = _idx(f, "外資自營商買賣超股數")
    i_t = _idx(f, "投信買賣超股數")
    i_d = f.index("自營商買賣超股數") if "自營商買賣超股數" in f else _idx(f, "自營商買賣超股數")
    i_n = _idx(f, "三大法人買賣超股數")
    out = {}
    for r in j["data"]:
        out[r[ic].strip()] = {
            "fn": (num(r[i_f]) or 0) + (num(r[i_fd]) or 0),
            "tn": num(r[i_t]) or 0,
            "dn": num(r[i_d]) or 0,
            "in": num(r[i_n]) or 0,
        }
    return out


def twse_margin(d: date):
    j = _get(f"{TWSE}/marginTrading/MI_MARGN?date={roc(d)}&selectType=ALL&response=json")
    if not _ok(j):
        return None
    t = _find_table(j.get("tables"), title_kw="融資融券彙總")
    if not t:
        return {}
    f = t["fields"]
    ic, ip, it = _idx(f, "代號"), _idx(f, "前日餘額"), _idx(f, "今日餘額")
    return {r[ic].strip(): {"mgp": num(r[ip]), "mg": num(r[it])} for r in t["data"]}


def twse_daytrade(d: date):
    j = _get(f"{TWSE}/dayTrading/TWTB4U?date={roc(d)}&selectType=All&response=json")
    if not _ok(j):
        return None
    t = _find_table(j.get("tables"), field_kw="當日沖銷交易成交股數")
    if not t:
        return {}
    f = t["fields"]
    ic, iv = _idx(f, "證券代號"), _idx(f, "當日沖銷交易成交股數")
    return {r[ic].strip(): num(r[iv]) or 0 for r in t["data"]}


# ---------- 櫃買中心（上櫃） ----------

def tpex_quotes(d: date):
    j = _get(f"{TPEX}/afterTrading/dailyQuotes?date={slash(d)}&id=&response=json")
    if not _ok(j):
        return None
    t = _find_table(j.get("tables"), title_kw="上櫃股票行情")
    if not t or not t.get("data"):
        return None
    f = t["fields"]
    ic, icl, ich = _idx(f, "代號"), _idx(f, "收盤"), _idx(f, "漲跌")
    io_, ih, il = _idx(f, "開盤"), _idx(f, "最高"), _idx(f, "最低")
    iv, ia, ish = _idx(f, "成交股數"), _idx(f, "成交金額"), _idx(f, "發行股數")
    out = {}
    for r in t["data"]:
        out[r[ic].strip()] = {
            "name": r[ic + 1].strip(),
            "o": num(r[io_]), "h": num(r[ih]), "l": num(r[il]), "c": num(r[icl]),
            "ch": num(r[ich]), "v": num(r[iv]) or 0, "a": num(r[ia]) or 0,
            "sh": num(r[ish]),
        }
    return out


def tpex_insti(d: date):
    j = _get(f"{TPEX}/insti/dailyTrade?type=Daily&sect=EW&date={slash(d)}&id=&response=json")
    if not _ok(j):
        return None
    t = _find_table(j.get("tables"), field_kw="三大法人買賣超股數合計")
    if not t:
        return {}
    f = t["fields"]
    if len(f) < 24:
        raise SourceError(f"上櫃法人欄位數量不符：{f}")
    # 欄位順序：外資(不含自營) 2-4、外資自營 5-7、外資合計 8-10、投信 11-13、
    # 自營(自行) 14-16、自營(避險) 17-19、自營合計 20-22、三大法人合計 23
    out = {}
    for r in t["data"]:
        out[r[0].strip()] = {
            "fn": num(r[10]) or 0, "tn": num(r[13]) or 0,
            "dn": num(r[22]) or 0, "in": num(r[23]) or 0,
        }
    return out


def tpex_margin(d: date):
    j = _get(f"{TPEX}/margin/balance?date={slash(d)}&id=&response=json")
    if not _ok(j):
        return None
    t = _find_table(j.get("tables"), field_kw="資餘額")
    if not t:
        return {}
    f = t["fields"]
    ic, ip, it = _idx(f, "代號"), _idx(f, "前資餘額"), f.index("資餘額")
    return {r[ic].strip(): {"mgp": num(r[ip]), "mg": num(r[it])} for r in t["data"]}


def tpex_daytrade(d: date):
    j = _get(f"{TPEX}/intraday/stat?type=Daily&date={slash(d)}&id=&response=json")
    if not _ok(j):
        return None
    t = _find_table(j.get("tables"), field_kw="當日沖銷交易成交股數")
    if not t:
        return {}
    f = t["fields"]
    ic, iv = _idx(f, "證券代號"), _idx(f, "當日沖銷交易成交股數")
    return {r[ic].strip(): num(r[iv]) or 0 for r in t["data"]}


# ---------- 集保（每週股權分散表，只有最新一週） ----------

def tdcc_latest():
    """回傳 (資料日期 'YYYYMMDD', {代號: {'big': 千張大戶持股%, 'sh': 集保總股數}})。"""
    text = _get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", as_json=False, encoding="utf-8")
    text = text.lstrip("﻿")
    out, ddate = {}, None
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 6 or not row[0].strip().isdigit():
            continue
        ddate = row[0].strip()
        code, lvl = row[1].strip(), row[2].strip()
        if lvl == "15":
            out.setdefault(code, {})["big"] = num(row[5])
        elif lvl == "17":
            out.setdefault(code, {})["sh"] = num(row[4])
    if not out:
        raise SourceError("集保股權分散表解析不到資料")
    return ddate, out


# ---------- 證券基本資料（產業別、是否為普通股） ----------

def isin_list(mode: int):
    """mode=2 上市、4 上櫃。只回傳普通股（CFI 為 ES 開頭、4 位數代號）。"""
    html = _get(f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}",
                as_json=False, encoding="big5")
    out = {}
    for tr in re.findall(r"<tr>(.*?)</tr>", html, flags=re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)]
        if len(cells) < 6:
            continue
        m = re.match(r"^(\d{4})[\s　]+(.+)$", cells[0])
        if not m or not cells[5].startswith("ES"):
            continue
        out[m.group(1)] = {"name": m.group(2).strip(), "ind": cells[4] or "其他"}
    if len(out) < 300:
        raise SourceError(f"ISIN 清單筆數異常（mode={mode}，{len(out)} 筆）")
    return out
