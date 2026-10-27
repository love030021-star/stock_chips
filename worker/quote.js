// Cloudflare Worker「tw-quote」：替網站向期交所、證交所抓即時行情，加上 CORS 讓網頁可以讀。
// 部署在 https://tw-quote.love030021.workers.dev（Cloudflare 後台的線上編輯器直接貼上這份程式）。
//   GET /tx   台指期近月（依台北時間選日盤或夜盤）＋加權現貨（期交所行情資訊網）
//   GET /gtf  櫃買現貨（期交所行情資訊網）
//   GET /otc  櫃買指數、加權指數、00631L（證交所基本市況報導）
//   POST /hook  你 TradingView 警示的 webhook（只收 TradingView 官方 IP），把價格存進 D1（綁定名稱 DB，資料庫 tw-quotes）
//   GET /tvq  讀出各商品最新一筆 TradingView 價格 {id:{px,t,at}}
// 每個來源在同一個 Worker 執行個體內快取 4 秒，不論幾個人同時開網頁，對期交所的請求都不會太密。
const H = { "Access-Control-Allow-Origin": "*", "Cache-Control": "no-store" };
const UA = { "User-Agent": "Mozilla/5.0", "Accept": "application/json" };
const C = {};

async function cached(k, ms, fn) {
  const c = C[k];
  if (c && Date.now() - c.t < ms) return c.v;
  const v = await fn();
  C[k] = { t: Date.now(), v };
  return v;
}

async function taifex(mt, cid) {
  const r = await fetch("https://mis.taifex.com.tw/futures/api/getQuoteList", {
    method: "POST",
    headers: { ...UA, "Content-Type": "application/json" },
    body: JSON.stringify({ MarketType: mt, SymbolType: "F", KindID: "1", CID: cid, ExpireMonth: "",
      RowSize: "全部", PageNo: "", SortColumn: "", AscDesc: "A" }),
  });
  const j = await r.json();
  return (j.RtData && j.RtData.QuoteList) || [];
}

const q = (x) => x && x.CLastPrice
  ? { px: +x.CLastPrice, chg: x.CDiffRate === "" ? null : +x.CDiffRate, d: x.CDate, t: x.CTime }
  : null;

// 08:40–13:50 用日盤（MarketType 0），其他時間用夜盤（1）
function sess() {
  const d = new Date(Date.now() + 8 * 3600e3);
  const m = d.getUTCHours() * 60 + d.getUTCMinutes();
  return m >= 520 && m <= 830 ? "0" : "1";
}

async function tx() {
  return cached("tx", 4000, async () => {
    const L = await taifex(sess(), "TXF");
    const S = sess() === "0" ? L : await taifex("0", "TXF").catch(() => []);
    return {
      tx: q(L.find((x) => /-F$/.test(x.SymbolID) && x.CLastPrice)),
      spot: q(S.find((x) => x.SymbolID === "TXF-S")),
    };
  });
}

async function gtf() {
  return cached("gtf", 4000, async () => {
    const L = await taifex("0", "GTF");
    return { otc: q(L.find((x) => x.SymbolID === "GTF-S")) };
  });
}

async function otc() {
  return cached("otc", 4000, async () => {
    const r = await fetch("https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=otc_o00.tw%7Ctse_t00.tw%7Ctse_00631L.tw&json=1&delay=0&_=" + Date.now(), { headers: UA });
    const j = await r.json();
    const out = {};
    for (const m of j.msgArray || []) {
      const px = parseFloat(m.z !== "-" ? m.z : (m.b || "").split("_")[0]), y = parseFloat(m.y);   // 沒成交時用最佳買價
      if (!isFinite(px)) continue;
      out[m.c === "o00" ? "otc" : m.c === "t00" ? "twii" : m.c] = { px, chg: y ? +((px / y - 1) * 100).toFixed(2) : null, d: m.d, t: m.t };
    }
    return out;
  });
}

const TVIP = ["52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"];   // TradingView webhook 來源 IP

async function hook(req, env) {
  if (!TVIP.includes(req.headers.get("CF-Connecting-IP") || "")) return new Response("forbidden", { status: 403 });
  const b = JSON.parse(await req.text());
  const px = parseFloat(b.px);
  if (!b.id || !isFinite(px)) return new Response("bad", { status: 400 });
  await env.DB.prepare("CREATE TABLE IF NOT EXISTS q(id TEXT PRIMARY KEY,px REAL,t TEXT,at INTEGER)").run();
  await env.DB.prepare("INSERT OR REPLACE INTO q VALUES(?,?,?,?)")
    .bind(String(b.id).slice(0, 20), px, String(b.t || "").slice(0, 40), Date.now()).run();
  return new Response("ok", { headers: H });
}

async function tvq(env) {
  const r = await env.DB.prepare("SELECT * FROM q").all().catch(() => ({ results: [] }));
  const o = {};
  for (const x of r.results || []) o[x.id] = { px: x.px, t: x.t, at: x.at };
  return o;
}

export default {
  async fetch(req, env) {
    const p = new URL(req.url).pathname;
    try {
      if (p === "/hook" && req.method === "POST") return await hook(req, env);
      if (p === "/tvq") return new Response(JSON.stringify(await tvq(env)), { headers: H });
      if (p === "/tx") return new Response(JSON.stringify(await tx()), { headers: H });
      if (p === "/gtf") return new Response(JSON.stringify(await gtf()), { headers: H });
      if (p === "/otc") return new Response(JSON.stringify(await otc()), { headers: H });
      return new Response(JSON.stringify({ ok: true }), { headers: H });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e) }), { status: 502, headers: H });
    }
  },
};
