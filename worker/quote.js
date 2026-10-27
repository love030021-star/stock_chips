// Cloudflare Worker：替網站向期交所、證交所抓即時行情（加上 CORS 讓網頁可以讀）
// GET /tx    → 台指期近月（日盤／夜盤取較新的那筆）＋加權現貨
// GET /otc   → 櫃買指數、加權指數（證交所基本市況報導）
const H = {
  "Access-Control-Allow-Origin": "*",
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
};
const UA = { "User-Agent": "Mozilla/5.0", "Accept": "application/json" };

async function taifex(mt) {
  const r = await fetch("https://mis.taifex.com.tw/futures/api/getQuoteList", {
    method: "POST",
    headers: { ...UA, "Content-Type": "application/json" },
    body: JSON.stringify({ MarketType: mt, SymbolType: "F", KindID: "1", CID: "TXF", ExpireMonth: "",
      RowSize: "全部", PageNo: "", SortColumn: "", AscDesc: "A" }),
  });
  const j = await r.json();
  return (j.RtData && j.RtData.QuoteList) || [];
}

const q = (x) => x && x.CLastPrice ? {
  px: +x.CLastPrice, chg: x.CDiffRate === "" ? null : +x.CDiffRate, diff: x.CDiff === "" ? null : +x.CDiff,
  d: x.CDate, t: x.CTime, name: x.DispCName,
} : null;

async function tx() {
  const [day, night] = await Promise.all([taifex("0"), taifex("1").catch(() => [])]);
  const near = (L) => L.find((x) => /-F$/.test(x.SymbolID) && x.CLastPrice);
  let best = null;
  for (const x of [near(day), near(night)]) {
    if (x && (!best || x.CDate + x.CTime.padStart(6, "0") > best.CDate + best.CTime.padStart(6, "0"))) best = x;
  }
  return { tx: q(best), spot: q(day.find((x) => x.SymbolID === "TXF-S")) };
}

async function otc() {
  const r = await fetch("https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=otc_o00.tw%7Ctse_t00.tw&json=1&delay=0&_=" + Date.now(), { headers: UA });
  const j = await r.json();
  const out = {};
  for (const m of j.msgArray || []) {
    const px = parseFloat(m.z), y = parseFloat(m.y);
    if (!isFinite(px)) continue;
    out[m.c === "o00" ? "otc" : "twii"] = { px, chg: y ? +((px / y - 1) * 100).toFixed(2) : null, d: m.d, t: m.t };
  }
  return out;
}

export default {
  async fetch(req) {
    if (req.method === "OPTIONS") return new Response(null, { headers: H });
    const p = new URL(req.url).pathname;
    try {
      if (p === "/tx") return new Response(JSON.stringify(await tx()), { headers: H });
      if (p === "/otc") return new Response(JSON.stringify(await otc()), { headers: H });
      return new Response(JSON.stringify({ ok: true, routes: ["/tx", "/otc"] }), { headers: H });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e) }), { status: 502, headers: H });
    }
  },
};
