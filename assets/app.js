(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const state = { mkt: "TX", stTf: "D", waveTf: "D" };
  const FEED_BASE=String(window.CAPITAL_FEED_BASE||'').replace(/\/$/,'');
  const CAPITAL_ONLY=Boolean(FEED_BASE);
  const BROKER_MARKETS=['TX','SRF','TWII','TPEX','00631L','MNQ','YM','GOLD','BRENT','JP225'];
  let liveAnalysisRevision = '', latestCapitalFeed = null;
  let W = null, SP = null;
  const CACHE = {};
  const MARKETS = [{ id: "TX", name: "台指期", tfs: ["D", "H", "M30", "M5"] }, {id:"SRF",name:"小型0050期貨",tfs:["D"]}];
  let futuresCatalog = {markets:{}}, futuresMonths = {}, marketRequest = 0, currentMarketMeta = null;
  const baseAnalysisNote="";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => n == null ? "—" : Number(n).toLocaleString("zh-TW", { minimumFractionDigits: d, maximumFractionDigits: d });
  // 價格：千元以上不顯示小數，以下顯示兩位（原油、櫃買指數）
  const fp = (n, d) => n == null ? "—" : fmt(n, d != null ? d : Math.abs(n) < 1000 ? 2 : 0);
  // 價格刻度：算術（若要改半對數，把 LOG 設為 true）
  const LOG = false;
  const lg = (v) => (LOG && v > 0 ? Math.log(v) : v), ul = (v) => (LOG ? Math.exp(v) : v);
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "down" : "");
  const getJSON = (u) => fetch(`${u}?v=${Date.now().toString(36)}`,{cache:'no-store',signal:AbortSignal.timeout(6000)}).then((r) => (r.ok ? r.json() : null)).catch(() => null);

  const usesBrokerMarket=id=>['TX','SRF'].includes(id)||Boolean(latestCapitalFeed?.contracts?.[id]);
  const futureProduct = id => id === 'TX' || id === 'SRF';
  // 不分月份：台指期、小型0050 一律用近月連續（全日盤），不再選個別契約月份
  const MONTHS_ENABLED = false;
  function selectedMonth(id = state.mkt) {
    if (!MONTHS_ENABLED || !futureProduct(id)) return '';
    const months = futuresCatalog.markets[id]?.months || [];
    const saved = futuresMonths[id];
    return months.some(m => m.month === saved) ? saved : id === 'SRF' ? (months[0]?.month || '') : '';
  }
  const datedFuture = (id = state.mkt) => MONTHS_ENABLED && (id === 'SRF' || Boolean(selectedMonth(id)));
  async function initFutures() {
    futuresCatalog = await getJSON('data/futures-contracts.json') || {markets:{}};
    try { const saved=JSON.parse(localStorage.getItem('futures-months-v1')||'{}'); if(saved&&typeof saved==='object') futuresMonths=saved; } catch(e) {}
    const select=$('#fut-month');
    if(select) select.addEventListener('change',()=>{
      const id=state.mkt, value=select.value;
      if(!futureProduct(id))return;
      if(value && !(futuresCatalog.markets[id]?.months||[]).some(m=>m.month===value))return;
      futuresMonths[id]=value;
      try {localStorage.setItem('futures-months-v1',JSON.stringify(futuresMonths));}catch(e){}
      showMarket(id).catch(showAnalysisError);
    });
  }
  function syncMonthControl(id) {
    const control=$('#fut-contract'), select=$('#fut-month'); if(!control||!select)return;
    control.hidden=!MONTHS_ENABLED||!futureProduct(id); if(control.hidden)return;
    const months=futuresCatalog.markets[id]?.months||[];
    select.innerHTML=(id==='TX'?`<option value="">${CAPITAL_ONLY?'近月（自動切換）':'近月連續（原有資料）'}</option>`:'')+
      months.map(m=>`<option value="${esc(m.month)}">${esc(m.label)}${m.month===months[0].month?' · 近月':''}</option>`).join('');
    if(id==='SRF'&&!months.length)select.innerHTML='<option value="">月份資料暫未公布</option>';
    select.disabled=!months.length; select.value=selectedMonth(id);
    $('#fut-source').textContent=datedFuture(id)?'指定月份 · 期交所日行情（非即時）':'可選指定月份；近月連續與個別月份分開計算';
  }
  async function loadDatedFuture(id, month) {
    const entry=futuresCatalog.markets[id];
    const empty={W:null,S:null,meta:{contract_mode:true,market:id,month,ready:false,bar_count:0,as_of:entry?.as_of||'',message:'此月份行情尚未公布或無成交日K，暫不推算。'}};
    if(!month)return empty;
    if(FEED_BASE){
      const key=window.FuturesFeed?.key(latestCapitalFeed,id,month)||`${id}:${month}`;
      const response=await fetch(`${FEED_BASE}/api/analysis?market=${encodeURIComponent(key)}`,{cache:'no-store',signal:AbortSignal.timeout(6000)});
      const live=await response.json();
      if(!response.ok)throw Error(live.error||'此月份即時分析未連線');
      const expiry=String(live.contract?.expiry||live.contract?.last_trade_date||'').replace(/[^0-9]/g,'');
      if(live.ready&&expiry.slice(0,6)!==month)throw Error('分析回覆的月份不符；暫不顯示');
      return {W:live.wave,S:live.strategy,meta:{...live,contract_mode:true,source_mode:'live',market:id,month,bar_count:live.counts?.D||0}};
    }
    const data=await getJSON(`data/futures-${id}-${month}.json`);
    if(!data||data.meta?.market!==id||data.meta?.month!==month||data.wave?.contract_month!==month)return empty;
    return {W:data.wave,S:data.strategy,meta:data.meta};
  }
  function showContractStatus(meta) {
    const product=futuresCatalog.markets[meta.market]?.name||meta.market;
    const month=meta.month?`${meta.month.slice(0,4)}/${meta.month.slice(4)}`:'尚無月份';
    if(meta.source_mode==='live'){
      $('#fut-source').textContent=`${meta.market} ${month} · 群益指定月份`;
      const note=$('#capital-analysis-info');if(note)note.textContent=meta.message||'';
      if(!meta.ready)$('#st-meta').textContent=meta.message||'等待同月份已收K';
      if(!meta.connected||!meta.market_fresh)$('#st-signal').textContent='此月份即時行情尚未就緒；進場訊號停用。';
      return;
    }
    const text=`${product} ${month}｜期交所日行情，資料公布至 ${meta.as_of||'待公布'}（非即時）。${meta.message}`;
    $('#fut-source').textContent=`${meta.market} ${month} · ${meta.bar_count} 根日K · 公布至 ${meta.as_of||'待公布'}`;
    const note=$('#capital-analysis-info'); if(note)note.textContent=text;
    $('#data-date').textContent=`${product} ${month} · 日行情公布至 ${meta.as_of||'待公布'}`;
    $('#st-meta').textContent=meta.ready?`${product} ${month}，使用 ${meta.bar_count} 根獨立日K計算。布局以本月份最後日線收盤價為參考；沒有分K，123進場訊號未啟用。`:text;
    $('#st-signal').textContent=meta.ready?'本月份尚無分K資料，123進場訊號未啟用。':meta.message;
    const card=$('#wave-counts');
    if(meta.bar_count<60)card.innerHTML=`<div class="wave-card"><h3>等待本月份日線累積</h3><p>${esc(meta.message)}${meta.bar_count<9?' KD至少需要9根日K。':''} 不套用其他月份或現貨資料。</p></div>`;
    if(!meta.bar_count)$('#wave-meta').textContent=`${product} ${month}：尚無成交日K，不產生價格、波浪或KD。`;
  }

  async function loadMarket(id) {
    if(datedFuture(id))return loadDatedFuture(id,selectedMonth(id));
    if(CAPITAL_ONLY&&usesBrokerMarket(id)){
      if(!BROKER_MARKETS.includes(id)&&!id.startsWith('S:'))return {W:null,S:null,meta:{ready:false,external:true,wave:null,strategy:null,message:'此商品的波浪／KD／123尚未接入即時K線；不沿用舊檔訊號。'}};
      if(!FEED_BASE)return {W:null,S:null,meta:{connected:false,ready:false,message:'公開群益行情服務尚未啟用。'}};
      // 群益已收K分析就緒才用；還沒就緒（剛登入、海期、斷線）就改用本站每小時更新的波浪與布局，不讓分析變空白
      let data=null;
      try{
        const response=await fetch(`${FEED_BASE}/api/analysis?market=${encodeURIComponent(id)}`,{cache:'no-store',signal:AbortSignal.timeout(6000)});
        data=await response.json();
        if(!response.ok)data=null;
      }catch(e){data=null;}
      if(data&&data.ready===true&&data.wave&&data.strategy)return {W:data.wave,S:data.strategy,meta:data};
    }
    if (!CACHE[id]) {
      if (id.startsWith("S:")) {
        const m = await getJSON(`data/stocks/${id.slice(2)}.json`);
        CACHE[id] = { W: m, S: m && m.strategy };
      } else if (id === "TX") {
        const [w, s] = await Promise.all([getJSON("data/wave.json"), getJSON("data/strategy.json")]);
        CACHE[id] = { W: w, S: s };
      } else {
        const m = await getJSON(`data/markets/${id}.json`);
        CACHE[id] = { W: m, S: m && m.strategy };
      }
    }
    if(CAPITAL_ONLY)return {...CACHE[id],meta:{site:true,external:false,connected:true,ready:true,generated_at:CACHE[id]?.W?.generated_at||'',message:'波浪與布局使用本站每小時更新的日／60／30／5分K線；報價即時更新。'}};
    return CACHE[id];
  }

  const ST_TFS = ["D", "H", "M30", "M5"];
  function syncTfButtons() {
    const m = datedFuture() && !CAPITAL_ONLY ? {tfs:["D"]} : MARKETS.find((x) => x.id === state.mkt) || (state.mkt.startsWith("S:") ? { tfs: CAPITAL_ONLY&&usesBrokerMarket(state.mkt)?["D","H","M30","M5"]:["D"] } : MARKETS[0]);
    ["waveTf", "stTf"].forEach((g) => {
      const btns = [...document.querySelectorAll(`[data-group=${g}] button`)];
      btns.forEach((b) => { b.disabled = !m.tfs.includes(b.dataset.v); });
      if (g === "stTf") {   // 波段布局：日／60／30／5 分
        btns.forEach((b) => { b.hidden = !ST_TFS.includes(b.dataset.v); });
        if (!ST_TFS.includes(state.stTf) || !m.tfs.includes(state.stTf)) state.stTf = ST_TFS.find((t) => m.tfs.includes(t)) || "M30";
      } else if (!m.tfs.includes(state[g])) state[g] = CAPITAL_ONLY ? (m.tfs.includes('M5')?'M5':m.tfs[0]) : "D";
      btns.forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === state[g]));
    });
  }

  // 個股：輸入代號或名稱，載入每日預先算好的日線推算
  let STOCKS = [];
  async function pickStock(q) {
    q = String(q || "").trim();
    if (!q) return;
    const code = (q.match(/\d{4,6}[A-Z]?/) || [""])[0];
    if(CAPITAL_ONLY && code){
      $('#stk-msg').textContent=`${code}：報價優先群益，未接入時查詢證交所公布行情；圖表以K線時間為準。`;
      await showMarket(code==='00631L'?'00631L':'S:'+code);return;
    }
    const hit = STOCKS.find((x) => x.code === code) || STOCKS.find((x) => x.name === q || q.includes(x.name)) || STOCKS.find((x) => x.name.includes(q));
    if (!hit) {
      $("#stk-msg").textContent = STOCKS.length ? `查無「${q}」：收錄全部上市櫃股票，查不到可能是新上市未滿 120 個交易日、已下市或代號有誤。` : "個股資料還在建立中，請稍後再試。";
      return;
    }
    if(CAPITAL_ONLY){await pickStock(hit.code);return;}
    $("#stk-msg").textContent = `${hit.code} ${hit.name}（${hit.m || "上市"}）：只有日線分析，資料到 ${hit.t.replaceAll("-", "/")}。`;
    $("#stk-in").value = `${hit.code} ${hit.name}`;
    showMarket("S:" + hit.code);
    document.getElementById("wave").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------- 即時報價 ----------
  // 比特幣：Kraken WebSocket（秒級）；加權指數：證交所每 5 秒指數（盤中每 30 秒查一次）。其他商品沒有可在瀏覽器直接取得的免費即時報價。
  const LIVE = { ws: null, timer: null, mkt: null, px: null, t: "", chg: null, src: "", lastDraw: 0 };
  // Private broker feed: never apply a TX price to the SRF or another market.
  window.addEventListener("capital-feed", (event) => {
    latestCapitalFeed=event.detail;
    if (!BROKER_MARKETS.includes(state.mkt)&&!state.mkt.startsWith('S:')) return;
    const feed = event.detail, match=datedFuture()?window.FuturesFeed?.match(feed,state.mkt,selectedMonth()):null;
    const q = datedFuture()?match?.quote:(feed.quotes && feed.quotes[state.mkt]);
    const connected=q?.kind==='overseas'?feed.sources?.overseas?.connected:feed.connected;
    if (!connected || !q || q.simulated || !Number.isFinite(q.price) || !((q.last_tick_price||q.price)>0)) {
      if (LIVE.capitalActive) {
        LIVE.capitalActive = false; LIVE.qOk = false; LIVE.px = null;
        LIVE.src = ""; LIVE.msg = "即時行情未連線，等待此商品與月份報價";
        liveDraw(true);
      }
      return;
    }
    LIVE.capitalActive = true; LIVE.qOk = true; LIVE.src = "群益 SDK";LIVE.widget=false;$("#tvq").hidden=true;$("#tvq").replaceChildren();
    LIVE.px = q.last_tick_price || q.price; LIVE.stale = !q.fresh || (!q.timestamp_basis && Date.now()-(q.last_tick_at||q.source_updated_at||0)*1000>15000);LIVE.receiptOnly=q.timestamp_basis==='receipt';
    LIVE.chg = q.reference > 0 ? (LIVE.px / q.reference - 1) * 100 : null;
    const time = LIVE.receiptOnly?`報價接收 ${new Date(q.received_at*1000).toLocaleTimeString('zh-TW',{hour12:false})} · 來源 ${q.source_date||q.trading_day} ${q.source_time||''}（交易所時區待核對）`:q.last_tick_at ? new Date(q.last_tick_at * 1000).toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false }) : `交易日 ${q.trading_day}／成交時間 ${String(q.deal_time || 0).padStart(6,"0")}`;
    LIVE.t = `群益 ${q.product} · ${time}`;
    if(CAPITAL_ONLY) $("#data-date").textContent=`${q.product} · ${time}${LIVE.receiptOnly?'':q.kind==='index'?'（指數更新）':'（最後成交）'}`;
    liveDraw(true);
  });
  function liveStop() {
    if (LIVE.ws) { try { LIVE.ws.onclose = null; LIVE.ws.close(); } catch (e) { /* 略過 */ } LIVE.ws = null; }
    if (LIVE.timer) { clearInterval(LIVE.timer); LIVE.timer = null; }
    if (LIVE.timer2) { clearInterval(LIVE.timer2); LIVE.timer2 = null; }
    if (LIVE.timer3) { clearInterval(LIVE.timer3); LIVE.timer3 = null; }
    LIVE.px = null; LIVE.capitalActive = false; LIVE.updatedAt = 0;LIVE.receiptOnly=false;
  }
  function liveZone(px) {
    if (!SP || !SP.plans) return "";
    const P = SP.plans[state.stTf] || SP.plans.D;
    if (!P || !P.batches || !P.batches.length || !P.dir) return "";
    const up = P.dir === 1, d = P.dir, lab = `${P.tf_label}布局`;
    const rv = (t) => up ? t : t.replace("回檔", "反彈");
    if (P.phase === "trend") return `${lab}：推動浪進行中，預估${up ? "回檔" : "反彈"}區 ${fp(P.zone[0])}–${fp(P.zone[1])}`;
    if (P.stop != null && (px - P.stop) * d <= 0) return `${lab}：已${up ? "跌破" : "漲破"}失效停損 ${fp(P.stop)}`;
    const hit = P.batches.map((b, i) => [b, i]).filter(([b]) => (px - b.price) * d <= 0);
    if (!hit.length) return `${lab}：距第 1 批 ${fp(P.batches[0].price)} 還有 ${fp(Math.abs(px - P.batches[0].price))}`;
    const [b, i] = hit[hit.length - 1];
    return `${lab}：已到${rv(b.tier || "")}第 ${i + 1} 批 ${fp(b.price)}，等 ${P.entry_tf || "小週期"} K 的 123`;
  }
  function liveDraw(force) {
    const el = $("#live");
    if (!el) return;
    const now = Date.now();
    if (!force && now - LIVE.lastDraw < (CAPITAL_ONLY?200:800)) return;
    LIVE.lastDraw = now;
    if (LIVE.widget) {   // 有即時報價框時只顯示布局提示，不重複列價格
      const z = LIVE.px != null ? liveZone(LIVE.px) : "";
      el.innerHTML = z ? `<span class="lz">${esc(z)}</span>` : "";
      return;
    }
    if (LIVE.px == null) {
      el.innerHTML = LIVE.src ? `<span class="dot"></span>連線中…` : `<span class="dot off"></span>${esc(LIVE.msg || "")}`;
      el.className = "live";
      return;
    }
    const c = LIVE.chg;
    if(!LIVE.capitalActive)LIVE.stale=!(LIVE.updatedAt && now-LIVE.updatedAt<=60000);
    if(CAPITAL_ONLY&&!LIVE.capitalActive&&LIVE.updatedAt)$('#data-date').textContent=`${LIVE.src||'公開行情'} · ${new Date(LIVE.updatedAt).toLocaleString('zh-TW',{hour12:false})}`;
    el.className = "live on";
    el.innerHTML = `<span class="dot"></span><b>${LIVE.receiptOnly?'群益報價':LIVE.stale ? "資料價" : LIVE.capitalActive?"即時":"近期報價"} ${fp(LIVE.px)}</b>${c != null ? ` <span class="${cls(c)}">${c > 0 ? "+" : ""}${c.toFixed(2)}%</span>` : ""} <small>${esc(LIVE.t)}</small>${liveZone(LIVE.px) ? `<span class="lz">${esc(liveZone(LIVE.px))}</span>` : ""}`;
  }
  // TradingView 官方報價小工具（即時跳動，直接連你的 TV 商品）
  // 期交所授權限制：CME、COMEX、ICE 期貨不能嵌入網頁，改用 TradingView 上同標的的現貨報價看即時方向（與期貨有價差）
  const TVSYM = {
    JP225: ["CMCMARKETS:JAPAN225Z2026", ""],
  };
  // 期貨不能嵌入即時報價框，改讀每 5 分鐘更新一次的報價（live 分支）
  const LIVEQ_URL = "https://raw.githubusercontent.com/love030021-star/stock_chips/live/live.json";
  const LIVEQ = [];   // 免費來源的期貨報價有延遲且合約月份不同，已停用，改用你 TradingView 的每小時資料
  function tvQuote(id) {
    const box = $("#tvq");
    if (!box) return;
    box.innerHTML = "";
    box.hidden = !TVSYM[id];
    if (!TVSYM[id]) return;
    const dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
    box.innerHTML = '<div class="tradingview-widget-container"><div class="tradingview-widget-container__widget"></div></div>';
    const sc = document.createElement("script");
    sc.src = "https://s3.tradingview.com/external-embedding/embed-widget-single-quote.js";
    sc.async = true;
    sc.textContent = JSON.stringify({ symbol: TVSYM[id][0], width: "100%", isTransparent: true, colorTheme: dark ? "dark" : "light", locale: "zh_TW" });
    box.firstChild.appendChild(sc);
  }
  // 更新倒數：期貨報價每 5 分鐘（GitHub 排程，約晚 1 分鐘）；布局每小時 :34 抓資料、約 :38 重算完成（週一～週六）
  const CD_QUOTE = ["MNQ", "YM", "GOLD", "BRENT"];
  function nextTpe(fn) {
    const now = Date.now(), tpe = new Date(now + 8 * 3600e3);   // 用 UTC 欄位當台北時間
    return fn(tpe, now);
  }
  function nextQuote() {
    return nextTpe((t, now) => {
      const m = t.getUTCMinutes(), base = new Date(t); base.setUTCSeconds(0, 0);
      let add = 5 - (m % 5) + 1; if (add > 5) add -= 5;
      base.setUTCMinutes(m + add);
      return base.getTime() - 8 * 3600e3 - now;
    });
  }
  function nextPlan() {
    return nextTpe((t, now) => {
      const b = new Date(t); b.setUTCSeconds(0, 0);
      b.setUTCMinutes(38);
      if (b <= t) b.setUTCHours(b.getUTCHours() + 1);
      while (b.getUTCDay() === 0) { b.setUTCDate(b.getUTCDate() + 1); b.setUTCHours(0, 38); }
      return b.getTime() - 8 * 3600e3 - now;
    });
  }
  const mmss = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60; return s === 0 ? "更新中" : (h ? `${h} 小時 ` : "") + `${m} 分 ${String(x).padStart(2, "0")} 秒`; };
  // 沒辦法在網頁內即時跳動的商品，附上官方即時行情的連結
  const LIVE_LINK = {};
  function cdTick() {
    const el = $("#cd"), id = state.mkt;
    if (!el) return;
    if(datedFuture()){el.textContent=CAPITAL_ONLY?'指定月份報價與已收K分開更新；以各自資料時間為準。':'歷史K線以標示日期為準；即時報價等待行情服務。';return;}
    if(CAPITAL_ONLY&&usesBrokerMarket(id)){el.textContent=['TX','SRF'].includes(id)?'行情：群益逐筆串流；波浪／KD／123：已收K後重算。':'資料時間超過15秒標為快照；此商品分析尚待即時K線來源。';return;}
    if (!id || id.startsWith("S:")) { el.textContent = ""; return; }
    const parts = [];
    parts.push(`${CD_QUOTE.includes(id) && !(LIVE.qOk && LIVE.mkt === id) ? "價格與布局" : "布局"}下次更新 <b>${mmss(nextPlan())}</b>`);
    if (LIVE_LINK[id]) parts.push(`<a href="${LIVE_LINK[id][0]}" target="_blank" rel="noopener">${LIVE_LINK[id][1]} ↗</a>`);
    el.innerHTML = parts.join("　");
  }
  setInterval(cdTick, 1000);
  // 自己的 Cloudflare Worker（worker/quote.js）：替網頁抓期交所、證交所即時行情
  const QW = "https://tw-quote.love030021.workers.dev";
  const fmtT = (d, t) => { t = String(t || "").replace(/:/g, "").padStart(6, "0"); return `${String(d).slice(4, 6)}/${String(d).slice(6, 8)} ${t.slice(0, 2)}:${t.slice(2, 4)}:${t.slice(4, 6)}`; };
  function pollWorker(id, path, pick, ms = 5000) {
    let busy = false;
    const go = async () => {
      if (document.hidden || busy) return;     // 畫面沒在看就不抓，省額度
      busy = true;
      try {
        const j = await (await fetch(`${QW}${path}?_=${Date.now()}`)).json();
        const x = pick(j);
        if (!x || state.mkt!==id || datedFuture(id) || LIVE.mkt !== id || LIVE.capitalActive) return;
        {
          const date=String(x.d||'').replace(/[^0-9]/g,''),tm=String(x.t||'').replace(/[^0-9]/g,'').padStart(6,'0');
          const stamp=x.at||Date.parse(`${date.slice(0,4)}-${date.slice(4,6)}-${date.slice(6,8)}T${tm.slice(0,2)}:${tm.slice(2,4)}:${tm.slice(4,6)}+08:00`);
          if(!Number.isFinite(stamp)||stamp>Date.now()+5000){LIVE.stale=true;liveDraw(true);return;}
          LIVE.updatedAt=stamp;
        }
        LIVE.qOk = true; LIVE.stale = Date.now() - LIVE.updatedAt > 60000; LIVE.px = x.px; LIVE.chg = x.chg; LIVE.t = fmtT(x.d, x.t);
        liveDraw(true);
      } catch (e) { /* 略過 */ } finally { busy = false; }
    };
    go();
    return setInterval(go, ms);
  }
  // TradingView 每15秒推到 Worker；保留最後一筆，非近期時顯示資料價與來源時間。
  const TVQ_IDS = ["MNQ", "YM", "GOLD", "BRENT", "TX"];
  function pollTVQ(id) {
    return pollWorker(id, "/tvq", (j) => {
      const x = j && j[id];
      if (!x || !isFinite(x.px)) return null;
      const w = CACHE[id] && CACHE[id].W, D = w && w.D && w.D.bars ? w.D.bars : [];
      // 收盤後警示不再推送，最後一筆仍是最新成交價；只有每小時資料比它新時才改用每小時資料
      const R = w && (w.M5 || w.H), hT = R && R.last ? Date.parse(String(R.last.t).replace(" ", "T") + ":00+08:00") : 0;
      if (x.at < hT || Date.now() - x.at > 3 * 86400e3) return null;
      const last = D.length ? D[D.length - 1] : null;
      const tpe = new Date(x.at + 8 * 3600e3), day = tpe.toISOString().slice(0, 10);
      // 前一日收盤：日線最後一根若是今天（台指期 15:00 後的夜盤算下一個交易日）就取前一根
      const td = id === "TX" && tpe.getUTCHours() >= 15 ? new Date(tpe.getTime() + 86400e3).toISOString().slice(0, 10) : day;
      // TradingView 警示在 t 後面附上前一日收盤（期貨即結算價）「時間|前收」，有就照 TradingView 的算法
      const pc = parseFloat(String(x.t || "").split("|")[1]);
      const prev = isFinite(pc) && pc > 0 ? pc : last ? (String(last[0]).slice(0, 10) >= td && D.length > 1 ? D[D.length - 2][4] : last[4]) : null;
      return { px: x.px, chg: prev ? +((x.px / prev - 1) * 100).toFixed(2) : null,
        d: day.replace(/-/g, ""), t: tpe.toISOString().slice(11, 19), at:x.at };
    }, 5000);
  }
  function pollExchangeStock(id) {
    const symbol=id.slice(2),entry=STOCKS.find(x=>x.code===symbol);
    const market=entry?.m==='上櫃'?'otc':'tse';let busy=false;
    const poll=async()=>{
      if(busy||document.hidden||state.mkt!==id)return;busy=true;
      try{
        const r=await fetch(`${FEED_BASE}/api/stock-quote?symbol=${encodeURIComponent(symbol)}&market=${market}`,{cache:'no-store',signal:AbortSignal.timeout(7000)});
        const data=await r.json();if(state.mkt!==id||LIVE.capitalActive)return;
        if(!r.ok||!data.quote){LIVE.px=null;LIVE.src='';LIVE.msg=data.message||data.error||'交易所尚無可確認的成交資料';liveDraw(true);return;}
        const q=data.quote;
        LIVE.px=q.price;LIVE.chg=q.change_pct;LIVE.updatedAt=q.last_trade_at;
        LIVE.t=`${data.source} · ${q.date} ${q.time}`;LIVE.src=data.source;LIVE.stale=Date.now()-q.last_trade_at>60000;
        liveDraw(true);
      }catch(error){if(state.mkt===id){LIVE.px=null;LIVE.src='';LIVE.msg='交易所報價暫時未連線';liveDraw(true);}}
      finally{busy=false;}
    };poll();return setInterval(poll,10000);
  }
  function liveStart(id) {
    liveStop();
    const useBroker=CAPITAL_ONLY&&usesBrokerMarket(id);
    if(datedFuture(id)){
      $('#tvq').hidden=true;$('#tvq').replaceChildren();
      LIVE.mkt=id;LIVE.widget=false;LIVE.src='';LIVE.msg='等待此月份即時行情連線；下方顯示已公布的歷史K線';LIVE.stale=true;LIVE.qOk=false;
      liveDraw(true);if(latestCapitalFeed)window.dispatchEvent(new CustomEvent('capital-feed',{detail:latestCapitalFeed}));$('#cd').textContent='';return;
    }
    if(id.startsWith('S:')&&!useBroker&&FEED_BASE){$('#tvq').hidden=true;$('#tvq').replaceChildren();LIVE.mkt=id;LIVE.widget=false;LIVE.src='';LIVE.msg='正在查詢交易所成交資料…';LIVE.timer=pollExchangeStock(id);liveDraw(true);cdTick();return;}
    if(!useBroker)tvQuote(id);else{$('#tvq').hidden=true;$('#tvq').replaceChildren();}
    LIVE.mkt = id; LIVE.src = ""; LIVE.msg = ""; LIVE.stale = false; LIVE.widget = !useBroker && !!TVSYM[id]; LIVE.qOk = false;
    if(useBroker && (BROKER_MARKETS.includes(id)||id.startsWith('S:'))){LIVE.msg='等待群益資料連線；不以舊報價補值';if(TVQ_IDS.includes(id))LIVE.timer2=pollTVQ(id);/* 群益斷線時（LIVE.capitalActive=false）改用 TradingView 15 秒推送的近月價格 */liveDraw(true);cdTick();return;}
    if(useBroker && !['BTC','TWII','TPEX','00631L'].includes(id)){
      LIVE.msg='此商品尚待授權即時行情來源；不顯示舊報價冒充即時';
      if(TVQ_IDS.includes(id))LIVE.timer2=pollTVQ(id);
      liveDraw(true);cdTick();return;
    }
    if(useBroker && id==='00631L'){
      LIVE.src='證交所資料轉接';LIVE.timer=pollWorker(id,'/otc',j=>j['00631L'],1000);liveDraw(true);cdTick();return;
    }
    if (id === "BTC") {
      LIVE.src = "Kraken";
      const open = () => {
        const ws = new WebSocket("wss://ws.kraken.com/v2");
        LIVE.ws = ws;
        ws.onopen = () => ws.send(JSON.stringify({ method: "subscribe", params: { channel: "ticker", symbol: ["BTC/USD"] } }));
        ws.onmessage = (e) => {
          try {
            const j = JSON.parse(e.data);
            if (j.channel !== "ticker" || !j.data || !j.data[0]) return;
            const q = j.data[0];
            LIVE.updatedAt=Date.parse(q.timestamp)||0;
            if(!Number.isFinite(q.last)||q.last<=0)return;
            LIVE.px = q.last; LIVE.chg = Number.isFinite(q.change_pct)?q.change_pct:null;
            LIVE.t = LIVE.updatedAt?new Date(LIVE.updatedAt).toLocaleString("zh-TW", { hour12: false }):'來源未提供成交時間';
            liveDraw();
          } catch (err) { /* 略過 */ }
        };
        ws.onclose = () => { if (LIVE.mkt === "BTC") setTimeout(() => { if (LIVE.mkt === "BTC" && LIVE.ws === ws) open(); }, 3000); };
      };
      open();
    } else if (id === "TWII" || id === "TPEX") {
      LIVE.src = "live";
      LIVE.timer = pollWorker(id, "/otc", (j) => j[id === "TWII" ? "twii" : "otc"], useBroker?1000:5000);
    } else if (!id.startsWith("S:") && id !== "TX" && id !== "TWII" && id !== "TPEX") {
      // TradingView 商品：每小時由排程更新到網站；這裡每 2 分鐘檢查一次，有新資料就顯示最新價並重畫
      LIVE.src = "";
      LIVE.stale = true;
      const show = (m) => {
        const R = m && (m.M5 || m.H || m.D);
        if (!R || !R.last || LIVE.qOk) return;
        const D = m.D && m.D.bars ? m.D.bars : [];
        const prev = D.length > 1 ? D[D.length - 2][4] : null;
        LIVE.px = R.last.c; LIVE.chg = prev ? (R.last.c / prev - 1) * 100 : null;
        LIVE.t = String(R.last.t).slice(5).replace("-", "/");
        liveDraw(true);
      };
      show(CACHE[id] && CACHE[id].W);
      const poll = async () => {
        const m = await getJSON(`data/markets/${id}.json`);
        if (!m || LIVE.mkt !== id) return;
        const old = CACHE[id] && CACHE[id].W;
        if (!old || m.generated_at !== old.generated_at) {
          CACHE[id] = { W: m, S: m.strategy };
          if (state.mkt === id) { W = m; SP = null; renderWave(); renderStrategy(m.strategy); }
        }
        show(m);
      };
      const pollQ = async () => {
        if (!LIVEQ.includes(id)) return;
        try {
          const r = await fetch(`${LIVEQ_URL}?t=${Math.floor(Date.now() / 60000)}`);
          const j = await r.json();
          const x = j && j.q && j.q[id];
          if (!x || state.mkt!==id || datedFuture(id) || LIVE.mkt !== id || LIVE.capitalActive) return;
          LIVE.qOk = true; LIVE.px = x.px; LIVE.chg = x.chg; LIVE.t = x.t;
          liveDraw(true);
        } catch (e) { /* 略過 */ }
      };
      pollQ();
      LIVE.timer = setInterval(() => { poll(); pollQ(); }, 60000);
      if (id === "00631L") LIVE.timer2 = pollWorker(id, "/otc", (j) => j["00631L"], 5000);   // 證交所即時成交價
      if (TVQ_IDS.includes(id)) LIVE.timer2 = pollTVQ(id);
    } else if (id === "TX") {
      // 台指期：每小時更新（盤中用 TradingView TXF1!，收盤後以期交所資料為準）
      LIVE.src = ""; LIVE.stale = true;
      const showTX = (w) => {
        const R = w && (w.M5 || w.H || w.D);
        if (!R || !R.last || LIVE.qOk) return;
        const D = w.D && w.D.bars ? w.D.bars : [];
        const prev = D.length > 1 ? D[D.length - 2][4] : null;
        LIVE.px = R.last.c; LIVE.chg = prev ? (R.last.c / prev - 1) * 100 : null;
        LIVE.t = String(R.last.t).slice(5).replace("-", "/");
        liveDraw(true);
      };
      showTX(CACHE.TX && CACHE.TX.W);
      LIVE.timer2 = pollTVQ("TX");   // 台指期價格只用你 TradingView 推來的資料
      const pollTX = async () => {
        const [w, st] = await Promise.all([getJSON("data/wave.json"), getJSON("data/strategy.json")]);
        if (!w || state.mkt !== "TX" || datedFuture("TX") || LIVE.mkt !== "TX") return;
        const old = CACHE.TX && CACHE.TX.W;
        if (!old || w.generated_at !== old.generated_at) {
          CACHE.TX = { W: w, S: st };
          if (state.mkt === "TX") { W = w; SP = null; renderWave(); renderStrategy(st); }
        }
        showTX(w);
      };
      LIVE.timer = setInterval(pollTX, 60000);
    } else {
      LIVE.msg = "";
    }
    liveDraw(true);
    cdTick();
  }

  async function showMarket(id) {
    const request=++marketRequest;
    state.mkt = id;
    liveStop();
    syncMonthControl(id);
    W=null;SP=null;currentMarketMeta=null;$("#st-zone-title").closest(".st-card").hidden=true;
    $('#wave-chart').replaceChildren();$('#wave-counts').replaceChildren();
    ['#st-trend','#st-figure','#st-batches','#st-calc','#st-signal','#st-abc'].forEach(k=>$(k).replaceChildren());
    $('#wave-meta').textContent='載入此商品與月份的資料…';$('#st-meta').textContent='載入中…';
    $('#live').textContent='載入中…';
    if(CAPITAL_ONLY){liveStop();$('#data-date').textContent='等待此商品行情來源時間';}
    document.querySelectorAll("#mkt-tabs button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === id));
    syncTfButtons();
    const d = await loadMarket(id);
    if (state.mkt !== id || request!==marketRequest) return;
    W = d.W; SP = null;currentMarketMeta=d.meta||null;
    if(CAPITAL_ONLY){liveAnalysisRevision='';['#st-trend','#st-figure','#st-batches','#st-calc','#st-signal','#st-abc'].forEach(k=>$(k).innerHTML='');}
    renderWave();
    renderStrategy(d.S);
    liveStart(id);
    if(d.meta?.contract_mode)showContractStatus(d.meta);
    else if(CAPITAL_ONLY)showAnalysisStatus(d.meta);
    else {const note=$('#capital-analysis-info');if(note)note.textContent=baseAnalysisNote;if(W?.D?.last)$('#data-date').textContent=`${W.contract} · ${W.D.last.t}${W.D.partial?'（含未收日線）':''}`;}
    try { localStorage.setItem("mkt", id); } catch (e) { /* 略過 */ }
  }

  async function load() {
    await initFutures();
    if(!CAPITAL_ONLY){
      $('#wave').hidden=true;   // 只保留波段布局
      $('#chips h2').textContent='台指選擇權籌碼（每日公布，非即時）';
      const note=document.createElement('p');note.id='capital-analysis-info';note.className='sub';
      note.textContent='';
      $('#markets').append(note);
      refreshDailyChips(true);setInterval(()=>refreshDailyChips(false),300000);
      document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshDailyChips(false);});
    }

    if(CAPITAL_ONLY){
      $(".hero-date").firstChild.textContent="行情資料時間 ";
      $("#sources").innerHTML="\n    <h2>資料來源</h2>\n    <ul>\n      <li>群益行情：僅揭示站長已確認可公開的商品。顯示實際資料時間，未接入商品不沿用舊價。</li>\n      <li>國內商品：逐筆保存K線，向SDK回補日K與1分K；至少60根已收K才沿用本站波浪、KD及123規則計算。</li>\n      <li>海期：從商品檔選取精確規格的未到期合約；報價可接收，交易所時區與分析K線仍待實機核對。日經改用美元NKD，與原CMC商品不同。</li>\n      <li>加權、櫃買與台灣50正2：等待站長發布群益行情。公開版不將其他來源的價格標成群益。</li>\n      <li>Kraken WebSocket：比特幣BTC/USD即時行情；分析K線尚未串接。</li>\n      <li>期交所法人、選擇權、大額交易人籌碼：每5分鐘讀取本站最新公布檔，顯示資料日期；官方每日資料不能轉成逐筆即時。</li>\n    </ul>\n    <p class=\"sub\">公開報價畫面每2秒讀取，分析於已收K變動後重算；不同月份不直接拼接。K線依台北時鐘分桶，與原網站日盤08:45起算方式不同。歷史回補依SDK可提供範圍，不保證完整。波浪可隨新K線改變數浪。</p>\n  ";
      MARKETS.splice(0,MARKETS.length,{id:'TX',name:'台指期',tfs:['D','H','M30','M5']},{id:'SRF',name:'小型0050期貨',tfs:['D','H','M30','M5']});
      const otherMarkets=await getJSON('data/markets/index.json');
      (otherMarkets||[]).forEach(m=>MARKETS.push(m));
      const sources=$('#sources');sources.innerHTML='<h2>資料來源</h2><ul><li>台指期、小型0050：優先群益SDK，核對個別月份；報價每2秒讀取。沒有近期成交時顯示最後成交時間。</li><li>波浪／KD／123：使用同月份已收K線重算，未收K不產生進場訊號。根數不足時等待回補。</li><li>群益尚未提供的市場：沿用本站原有行情來源，依各市場資料時間顯示；圖表若未接入即時K線會明確標示。</li><li>比特幣：Kraken公開行情。法人、選擇權及大額交易人籌碼依官方每日公布日期更新。</li></ul>';
      state.stTf='M5';state.waveTf='M5';
      $('#wave').hidden=true;   // 只保留波段布局
      STOCKS=(await getJSON('data/stocks/index.json'))||[];
      $('#stk-list').innerHTML=STOCKS.map(x=>`<option value="${esc(x.code)} ${esc(x.name)}">`).join('');
      $('#stk-form').addEventListener('submit',e=>{e.preventDefault();pickStock($('#stk-in').value);});
      $('#data-date').textContent='等待群益公開行情；未使用示範資料';
      $('#mkt-tabs').innerHTML=MARKETS.map(m=>`<button data-v="${m.id}" aria-pressed="${m.id==='TX'}">${esc(m.name)}</button>`).join('');
      $('#mkt-tabs').addEventListener('click',e=>{const b=e.target.closest('button');if(b)showMarket(b.dataset.v).catch(showAnalysisError);});
      const note=document.createElement('p');note.id='capital-analysis-info';note.className='sub';$('#markets').append(note);
      $('#chips h2').textContent='台指選擇權籌碼（每日公布，非即時）';
      refreshDailyChips(true);setInterval(()=>refreshDailyChips(false),300000);
      document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshDailyChips(false);});
      let initial='TX';try{const saved=localStorage.getItem('mkt');if(MARKETS.some(m=>m.id===saved))initial=saved;}catch(e){}
      await showMarket(initial);
      setInterval(refreshLiveAnalysis,2000);
      return;
    }
    const idx = await getJSON("data/markets/index.json");
    (idx || []).forEach((m) => MARKETS.push(m));
    $("#mkt-tabs").innerHTML = MARKETS.map((m) => `<button data-v="${m.id}" aria-pressed="${m.id === "TX"}">${esc(m.name.replace(/（.*）/, ""))}</button>`).join("");
    $("#mkt-tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) showMarket(b.dataset.v); });
    let start = "TX";
    STOCKS = (await getJSON("data/stocks/index.json")) || [];
    $("#stk-list").innerHTML = STOCKS.map((x) => `<option value="${esc(x.code)} ${esc(x.name)}">`).join("");
    $("#stk-form").addEventListener("submit", (e) => { e.preventDefault(); pickStock($("#stk-in").value); });
    try { const s = localStorage.getItem("mkt"); if (s && (MARKETS.some((m) => m.id === s) || (s.startsWith("S:") && STOCKS.some((x) => x.code === s.slice(2))))) start = s; } catch (e) { /* 略過 */ }
    const tx = await loadMarket("TX");
    if (tx.W && tx.W.D) {
      const R = tx.W.D, t = R.partial && R.bars.length > 1 ? R.bars[R.bars.length - 2][0] : R.last.t;
      const d = new Date(t + "T00:00:00");
      $("#data-date").textContent = `${t.replaceAll("-", "/")}（${"日一二三四五六"[d.getDay()]}）${R.partial ? "，另含收盤後夜盤" : ""}`;
    }
    showMarket(start);
    watchUpdates();
  }

  function showAnalysisError(error){
    const note=$('#capital-analysis-info');if(note)note.textContent='即時分析服務未連線：'+error.message;
    ['#st-trend','#st-figure','#st-batches','#st-calc','#st-signal','#st-abc'].forEach(k=>$(k).innerHTML='');
    $('#st-meta').textContent='連線中斷，暫停顯示可進場訊號。';
  }
  let lastDailyCheck=0;
  async function refreshDailyChips(force=false){
    if(!force&&(document.hidden||Date.now()-lastDailyCheck<60000))return;
    lastDailyCheck=Date.now();
    const published=await getJSON('https://love030021-star.github.io/stock_chips/data/chips.json');
    const data=published||await getJSON('data/chips.json');
    renderChips(data);
    if(data)$('#chip-meta').textContent+=published?' · 已讀取網站最新公布版本；每5分鐘查詢。':' · 無法確認最新公布版本，目前為本機副本。';
  }
  function showAnalysisStatus(meta){
    if(!meta)return;
    const text=(meta.site?'':meta.external?'其他市場來源：價格以成交時間為準。':meta.connected?'群益連線中。':'群益公開行情未連線；分析僅供已收K參考。')+' '+meta.message+(meta.generated_at?' 計算時間 '+meta.generated_at:'')+(meta.ready?' 波浪隨新K線可能改變數浪。':'');
    const note=$('#capital-analysis-info');if(note)note.textContent=text;
    if(!meta.ready)$('#st-meta').textContent=text;
    if(!meta.connected){
      $('#st-signal').textContent=meta.external?'此商品尚未接入即時K線，暫不產生即時進場訊號。':'未連線：進場訊號已停用。';
    }else if(meta.ready&&meta.market_fresh===false){
      $('#st-signal').textContent='報價非近期成交／指數更新：進場訊號已停用，圖表僅供已收K參考。';
    }
  }
  async function refreshLiveAnalysis(){
    if(document.hidden)return;
    const market=state.mkt, month=selectedMonth();
    if(datedFuture()&&!CAPITAL_ONLY)return;
    try{
      const data=await loadMarket(market);if(state.mkt!==market||selectedMonth()!==month)return;
      const revision=market+'|'+(data.meta.generated_at||'')+'|'+data.meta.ready+'|'+data.meta.market_fresh;
      if(revision!==liveAnalysisRevision){
        liveAnalysisRevision=revision;W=data.W;SP=data.S;
        ['#st-trend','#st-figure','#st-batches','#st-calc','#st-signal','#st-abc'].forEach(k=>$(k).innerHTML='');
        renderWave();renderStrategy(data.S);
      }
      if(data.meta?.contract_mode){currentMarketMeta=data.meta;showContractStatus(data.meta);}else showAnalysisStatus(data.meta);
    }catch(error){showAnalysisError(error);}
  }
  setInterval(()=>{if(LIVE.px!=null)liveDraw(true);},500);

  // 加到手機桌面後，系統常把頁面留在背景、不重新載入。回到前景或每 10 分鐘：
  // 先檢查網站是否有新版（有就整頁重新載入），沒有就重新抓資料重畫。
  const BUILD = (document.querySelector('script[src*="app.js"]')?.getAttribute("src") || "").split("v=")[1] || "";
  let lastCheck = Date.now();
  async function checkUpdate(force) {
    if (!force && Date.now() - lastCheck < 60 * 1000) return;
    lastCheck = Date.now();
    try {
      const html = await fetch(`./?_=${Date.now()}`, { cache: "no-store" }).then((r) => r.text());
      const v = (html.match(/app\.js\?v=([\w]+)/) || [])[1];
      if (v && BUILD && v !== BUILD) { location.reload(); return; }
    } catch (e) { /* 離線時略過 */ }
    for (const k of Object.keys(CACHE)) delete CACHE[k];
    const idx = await getJSON("data/stocks/index.json");
    if (idx) STOCKS = idx;
    const contracts=await getJSON('data/futures-contracts.json');if(contracts?.markets)futuresCatalog=contracts;
    showMarket(state.mkt);
    getJSON("data/chips.json").then((c) => renderChips(c));
  }
  function watchUpdates() {
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") checkUpdate(); });
    window.addEventListener("pageshow", (e) => { if (e.persisted) checkUpdate(true); });
    window.addEventListener("focus", () => checkUpdate());
    setInterval(() => { if (document.visibilityState === "visible") checkUpdate(true); }, 10 * 60 * 1000);
  }

  // ---------- 波段逆勢布局 ----------
  function renderStrategy(S) {
    try { drawStrategy(S); } catch (e) {
      console.error(e);
      $("#st-meta").textContent = "波段布局顯示失敗，請重新整理頁面；若仍無法顯示，可能是資料格式更新中。";
    }
  }

  function drawStrategy(S) {
    const allocation=$("#st-zone-title").closest(".st-card");allocation.hidden=true;
    if (!S) {
      $("#st-meta").textContent = CAPITAL_ONLY ? "等待群益回補同月份歷史K線；資料足夠後自動計算。" : "這個商品的波段布局資料還沒產生，下一次每日更新後會出現。";
      ["#st-trend", "#st-figure", "#st-batches", "#st-calc", "#st-signal", "#st-abc"].forEach((k) => { $(k).innerHTML = ""; });
      return;
    }
    if (!S.plans && S.plan) S = Object.assign({}, S, { plans: { D: Object.assign({ tf: "D", tf_label: "日線", data_t: S.daily_t || "", data_close: S.daily_close, abc: S.abc || [], signal: S.signal }, S.plan) } });
    SP = S;
    const P = S.plans && (S.plans[state.stTf] || (CAPITAL_ONLY ? null : S.plans.D));
    if (!P) { $("#st-meta").textContent = "這個週期的資料還不夠。"; $("#st-trend").innerHTML = ""; $("#st-batches").innerHTML = ""; $("#st-calc").innerHTML = ""; $("#st-signal").innerHTML = ""; $("#st-abc").innerHTML = ""; return; }
    allocation.hidden=false;
    const up = P.dir === 1, M = S.plans.D;
    $("#st-meta").textContent = `${W.contract}，${P.tf_label}${state.mkt === "TX" ? "（全日盤）" : ""}資料到 ${P.data_t.replaceAll("-", "/")}${P.partial ? " 夜盤" : ""}（${fp(P.data_close)}），最新價 ${fp(S.last)}；進場看 ${P.entry_tf || "60 分"} K 的 123。`;
    const pts = P.points.map((p, i) => `${p[2] === "0" ? "起點" : "第 " + p[2] + " 浪"} ${p[0].slice(5).replace("-", "/")} ${fp(p[1])}${P.kd_ok ? (P.kd_ok[i] ? " KD✓" : " KD✗") : ""}`).join(" → ");
    const major = P.tf !== "D" && M ? `<p class="sub">日線大方向：<b class="${M.dir === 1 ? "up" : M.dir === -1 ? "down" : ""}">${esc(M.trend)}</b>（${esc(M.now)}）${P.vs_major ? `，這個波段是<b>${esc(P.vs_major)}</b>${P.vs_major.startsWith("逆") ? "，風險較高、目標宜保守" : ""}` : ""}。</p>` : "";
    $("#st-trend").innerHTML = `
      <div class="st-big ${up ? "up" : P.dir === -1 ? "down" : ""}">
        <span class="lab">${P.tf_label}${P.tf === "D" ? "大方向" : "波段方向"}</span>
        <b>${esc(P.trend)}</b>
        <span class="now">目前：${esc(P.now)}</span>
      </div>
      <div class="st-desc">
        <p>${esc(pts)}</p>
        <p class="st-act ${P.phase === "correct" ? "" : "warn"}">${esc(P.status || P.text || "")}</p>
        ${P.note ? `<p class="sub">${esc(P.note)}</p>` : ""}${major}
        ${P.phase && P.phase !== "wait" ? `<p class="sub">布局方向：<b class="${up ? "up" : "down"}">${P.side}</b>（逆著目前的${up ? "回檔" : "反彈"}分批進場，順著${P.tf_label}推動浪的方向）。</p>` : ""}
      </div>`;
    if (!P.batches || !P.batches.length) {
      $("#st-zone-title").textContent = "回檔區間分批";
      $("#st-batches").innerHTML = `<tr><td colspan="4" class="sub">這個週期目前沒有可布局的推動浪結構。</td></tr>`;
      $("#st-calc").innerHTML = ""; $("#st-signal").innerHTML = ""; $("#st-abc").innerHTML = ""; $("#st-figure").innerHTML = ""; return;
    }
    try {
      $("#st-figure").innerHTML = planFigure(P, S);
      const pw = document.querySelector("#st-figure .plan-wrap");   // 手機上先捲到最新的 K 線
      if (pw) pw.scrollLeft = pw.scrollWidth;
    } catch (e) { $("#st-figure").innerHTML = ""; }
    $("#st-zone-title").textContent = P.phase === "correct" ? `${up ? "回檔" : "反彈"}區間分批：${fp(P.zone[0])}–${fp(P.zone[1])}` : `預估下一個${up ? "回檔" : "反彈"}區`;
    let lastTier = null;
    $("#st-batches").innerHTML = P.batches.map((b, i) => `${b.tier && b.tier !== lastTier ? `<tr class="tier"><td colspan="4">${esc(up ? (lastTier = b.tier) : (lastTier = b.tier).replace("回檔", "反彈"))}</td></tr>` : ""}<tr><td>第 ${i + 1} 批</td><td class="r num"><b>${fp(b.price)}</b></td><td>${esc(b.label)}</td>
      <td class="r"><input class="lots" type="number" min="0" step="1" value="0" data-i="${i}" aria-label="第 ${i + 1} 批口數"></td></tr>`).join("");
    document.querySelectorAll("#st-batches .lots").forEach((el) => el.addEventListener("input", calcCost));
    // 商品選項依各市場的每點價值產生；記住使用者上次的設定（只存在這台瀏覽器）
    const prods = Object.keys(S.pt || {});
    $("#st-prod").innerHTML = prods.map((k) => `<option>${esc(k)}</option>`).join("");
    $("#st-prod").closest("label").hidden = prods.length < 2;
    try {
      const saved = JSON.parse(localStorage.getItem("st-pref") || "{}");
      if (saved.total) $("#st-total").value = saved.total;
      if (saved.mode) $("#st-mode").value = saved.mode;
      const sp = (saved.prod || {})[state.mkt];
      if (sp && prods.includes(sp)) $("#st-prod").value = sp;
      else if (prods.includes("小台")) $("#st-prod").value = "小台";
    } catch (e) { /* 無法讀取就用預設值 */ }
    $("#st-total").oninput = allocate;
    $("#st-mode").onchange = allocate;
    $("#st-prod").onchange = () => { savePref(); calcCost(); };
    allocate();
    const G = P.signal;
    const ST = { confirmed: "已確認", wait_break: "等突破第 2 點", wait3: "等第 3 點", wait2: "等第 2 點", trend: "推動浪中", none: "—" };
    const badge = G ? (G.ok ? "可進場" : (ST[G.state] || "等待")) : "—";
    const ptsRow = G && G.p1 ? `<table class="r123"><tbody>
        <tr><td>第 1 點（${up ? "回檔低點" : "反彈高點"}）</td><td class="r">${G.p1 ? fp(G.p1.price) : "—"}</td><td class="r sub">${G.p1 ? G.p1.t.slice(5) : ""}</td></tr>
        <tr><td>第 2 點（${up ? "反彈高點" : "回落低點"}）</td><td class="r">${G.p2 ? fp(G.p2.price) : "—"}</td><td class="r sub">${G.p2 ? G.p2.t.slice(5) : "尚未形成"}</td></tr>
        <tr><td>第 3 點（不破第 1 點）</td><td class="r">${G.p3 ? fp(G.p3.price) : "—"}</td><td class="r sub">${G.p3 ? G.p3.t.slice(5) : "尚未形成"}</td></tr>
        <tr><td>進場（收盤${up ? "突破" : "跌破"}第 2 點）</td><td class="r"><b>${G.p2 ? fp(G.p2.price) : "—"}</b></td><td class="r sub">${G.confirm_t ? G.confirm_t.slice(5) + " 確認" : ""}</td></tr>
      </tbody></table>` : "";
    $("#st-signal").innerHTML = `<div class="st-head"><h3>${esc(P.entry_tf || "60 分")} K 123 法則進場</h3><span class="st-dir ${G && G.ok ? (up ? "up" : "down") : ""}">${badge}</span></div>
      <p>${esc(G ? G.text : "沒有較小週期的 K 線資料。")}</p>${kdLine(P)}${G && G.bars ? r123Svg(G, P) : ""}${ptsRow}`;
    $("#st-abc").innerHTML = `<div class="st-head"><h3>修正浪細部（ABC）</h3></div>` + (P.abc.length ? P.abc.map((a) => {
      const [o, A, B] = a.points;
      return `<p>${esc(a.tf)}：A 浪 ${fp(o[1])} → ${fp(A[1])}，B 浪反${up ? "彈" : "落"}到 ${fp(B[1])}，C 浪進行中。</p>
        <table><tbody>${a.targets.map((t) => {
          const inZone = P.zone && t.price >= P.zone[0] && t.price <= P.zone[1];
          return `<tr><td>C 浪：${esc(t.name)}${inZone ? "<small class='hit'>在回檔區內</small>" : ""}</td><td class="r">${fp(t.price)}</td></tr>`;
        }).join("")}</tbody></table>`;
    }).join("") : `<p class="sub">較小週期目前沒有進行中的逆向 C 浪可對照。</p>`);
  }

  function kdLine(P) {
    const k = P.kd;
    if (!k) return "";
    const want = P.dir === 1 ? 1 : -1, ok = k.signal === want;
    return `<p class="kd-line ${ok ? "ok" : ""}">KD（${esc(P.kd_tf || "")}）：K ${k.k}／D ${k.d}，${esc(k.state)}${ok ? `，<b>與${P.dir === 1 ? "做多" : "做空"}方向一致</b>` : `，等${P.dir === 1 ? "低檔黃金交叉" : "高檔死亡交叉"}`}。123 確認＋KD 配合時進場最穩。</p>`;
  }

  // 布局圖解：K 線、波浪標示、三層回檔區、各批價位、失效價、目標與預期路徑，下面附文字解說
  const TIER_INFO = {
    "強勢回檔": "趨勢強時常只回到這裡就再出發，先放小量。",
    "回檔滿足 1": "一般修正最常見的滿足區，布局主力。",
    "回檔滿足 2": "最深的布局區，已接近波浪失效價，碰到時要先檢查數浪是否還成立。",
  };
  function planFigure(P, S) {
    if (!P.bars || !P.bars.length || !P.batches) return "";
    const up = P.dir === 1, B = P.bars, n = B.length, last = S.last;
    const rv = (t) => up ? t : t.replace("回檔", "反彈");
    const tiers = [];
    P.batches.forEach((b, i) => {
      let T = tiers.find((x) => x.name === b.tier);
      if (!T) tiers.push(T = { name: b.tier, items: [] });
      T.items.push(Object.assign({ n: i + 1 }, b));
    });
    const tgt = P.phase === "trend" ? (P.k === 2 ? P.t1 : P.t2) : P.t1;
    const vals = [...B.map((b) => b[2]), ...B.map((b) => b[3]), ...P.batches.map((b) => b.price), P.invalid, P.stop, tgt, last].filter((v) => v != null);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.06; lo -= pad; hi += pad;
    const Wd = 1000, Ht = 420, pl = 10, pr = 120, pt = 16, pb = 26, fut = 150;
    const cw = (Wd - pl - pr - fut) / n;
    const lh = lg(hi), ll = lg(lo);
    const X = (i) => pl + cw * (i + 0.5), Y = (v) => pt + (lh - lg(v)) / (lh - ll) * (Ht - pt - pb);
    const xr = Wd - pr, idx = (t) => B.findIndex((b) => b[0] === t);
    let g = "";
    const op = { "強勢回檔": 0.07, "回檔滿足 1": 0.14, "回檔滿足 2": 0.22 };
    tiers.forEach((T) => {
      const ps = T.items.map((x) => x.price), a = Math.max(...ps), b = Math.min(...ps);
      g += `<rect x="${pl}" y="${Y(a)}" width="${xr - pl}" height="${Math.max(2, Y(b) - Y(a))}" class="tz ${up ? "u" : "d"}" style="opacity:${op[T.name] || 0.1}"/>`;
    });
    const ord = P.batches.map((b, i) => [Y(b.price), i]).sort((a, b) => a[0] - b[0]);
    const ly0 = {}; let prevY = -99;
    ord.forEach(([y, i]) => { const yy = Math.max(y, prevY + 13); ly0[i] = yy; prevY = yy; });
    P.batches.forEach((b, i) => { g += `<line x1="${pl}" x2="${xr}" y1="${Y(b.price)}" y2="${Y(b.price)}" class="bl"/><text x="${xr + 6}" y="${ly0[i] + 4}" class="bt">${i + 1}｜${fp(b.price)}</text>`; });
    tiers.forEach((T) => {
      const ps = T.items.map((x) => x.price), m = (Math.max(...ps) + Math.min(...ps)) / 2;
      g += `<text x="${xr - 8}" y="${Y(m) + 4}" class="tn" text-anchor="end">${rv(T.name)}</text>`;
    });
    if (P.stop != null) g += `<line x1="${pl}" x2="${xr}" y1="${Y(P.stop)}" y2="${Y(P.stop)}" class="inv"/><text x="${xr + 6}" y="${Y(P.stop) + (up ? 16 : -6)}" class="it">停損 ${fp(P.stop)}</text>`;
    if (tgt != null) g += `<line x1="${pl}" x2="${xr}" y1="${Y(tgt)}" y2="${Y(tgt)}" class="tg"/><text x="${xr + 6}" y="${Y(tgt) + 4}" class="tt2">${P.phase === "trend" ? "預估" : "目標"} ${fp(tgt)}</text>`;
    B.forEach((b, i) => {
      const [, o, h, l, c] = b, cl = c >= o ? "u" : "d";
      g += `<line x1="${X(i)}" x2="${X(i)}" y1="${Y(h)}" y2="${Y(l)}" class="w ${cl}"/><rect x="${X(i) - cw * 0.35}" y="${Y(Math.max(o, c))}" width="${Math.max(1, cw * 0.7)}" height="${Math.max(1, Math.abs(Y(o) - Y(c)))}" class="b ${cl}"/>`;
    });
    const wp = (P.points || []).map((p) => [idx(p[0]), p[1], p[2]]).filter((p) => p[0] >= 0);
    if (wp.length > 1) g += `<polyline points="${wp.map((p) => `${X(p[0])},${Y(p[1])}`).join(" ")}" class="wl"/>`;
    wp.forEach((p, j) => {
      const isHigh = j > 0 ? p[1] > wp[j - 1][1] : p[1] < (wp[1] || p)[1] ? false : true;
      const y = Y(p[1]) + (isHigh ? -10 : 18);
      g += `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="4" class="wp"/><text x="${X(p[0])}" y="${y}" class="wt" text-anchor="middle">${p[2] === "0" ? "起點" : p[2]}</text>`;
    });
    // 預期路徑：推動浪中 → 先到預估終點再修正到布局區；修正中 → 進布局區後走向目標
    const lx = X(n - 1), ly = Y(last), zm = P.zone ? (P.zone[0] + P.zone[1]) / 2 : null;
    let path = "";
    if (P.phase === "trend" && tgt != null && zm != null) path = `M${lx},${ly} L${lx + fut * 0.45},${Y(tgt)} L${lx + fut * 0.9},${Y(zm)}`;
    else if (zm != null && tgt != null) {
      const inZ = P.zone[0] <= last && last <= P.zone[1];
      path = inZ ? `M${lx},${ly} L${lx + fut * 0.9},${Y(tgt)}` : `M${lx},${ly} L${lx + fut * 0.4},${Y(P.batches[0].price)} L${lx + fut * 0.9},${Y(tgt)}`;
    }
    if (path) g += `<path d="${path}" class="pp"/>`;
    g += `<circle cx="${lx}" cy="${ly}" r="4.5" class="lp"/><text x="${lx + 8}" y="${ly - 8}" class="lt2">現價 ${fp(last)}</text>`;
    const tl = [0, Math.floor(n / 3), Math.floor(2 * n / 3), n - 1].map((i) => `<text x="${X(i)}" y="${Ht - 8}" class="tt" text-anchor="middle">${B[i][0].slice(2, 16).replace(/-/g, "/")}</text>`).join("");
    const svg = `<div class="plan-wrap"><svg class="plan-svg" viewBox="0 0 ${Wd} ${Ht}" role="img" aria-label="${esc(P.tf_label)}波段布局圖">${g}${tl}</svg></div>`;
    // ---- 文字解說 ----
    const wname = up ? "回檔" : "反彈", act = up ? "買進" : "放空";
    const base = (P.batches[0].label || "").split("的")[0];
    const steps = [];
    const chain = (P.points || []).map((p) => `${p[2] === "0" ? "起點" : "第 " + p[2] + " 浪"} ${fp(p[1])}`).join(" → ");
    steps.push(`<b>方向</b>：${esc(P.tf_label)}數出<b class="${up ? "up" : "down"}">${esc(P.trend)}</b>，目前在「${esc(P.now)}」。${chain ? `轉折：${esc(chain)}。` : ""}${P.vs_major ? `這個波段${esc(P.vs_major)}。` : ""}`);
    if (P.phase === "trend") steps.push(`<b>現在不進場</b>：推動浪還在進行，不追價。預估走到 ${fp(tgt)} 附近後才會出現${wname}，圖上的色帶是預估的${wname}區，等推動浪真的結束會依實際${up ? "高" : "低"}點重算。`);
    else steps.push(`<b>${wname}怎麼量</b>：以「${esc(base.replace(/^依/, ""))}」這段走勢為基準，從${up ? "高" : "低"}點往回推算三層${wname}區。`);
    tiers.forEach((T) => {
      const ps = T.items.map((x) => x.price);
      steps.push(`<b>${rv(T.name)}</b>（第 ${T.items.map((x) => x.n).join("、")} 批，${fp(Math.min(...ps))}–${fp(Math.max(...ps))}）：${esc(rv(TIER_INFO[T.name] || ""))}`);
    });
    const G = P.signal;
    steps.push(`<b>進場</b>：價格碰到某一批後，不直接${act}，要等 ${esc(P.entry_tf || "較小週期")} K 走出 123（第 3 點不破第 1 點、收盤${up ? "突破" : "跌破"}第 2 點）才進已碰到的批次。${G && G.text ? "目前：" + esc(G.text) : ""}`);
    if (P.invalid != null) steps.push(`<b>失效停損 ${fp(P.stop)}</b>（失效價 ${fp(P.invalid)}）：${esc(P.invalid_src || "")}。到這裡代表數浪被推翻；停損放在失效價外一點的 ${fp(P.stop)}，${up ? "跌破" : "漲破"}就全部出場。${P.dropped && P.dropped.length ? `比失效停損更深的 ${esc(P.dropped.join("、"))} 不布局。` : ""}`);
    steps.push(`<b>目標</b>：目標 1 ${fp(P.t1)}（${esc(P.t1_src)}），目標 2 ${fp(P.t2)}（${esc(P.t2_src)}）。`);
    const legend = `<div class="pf-legend"><span><i class="lg-z"></i>${wname}三層（越深顏色越濃）</span><span><i class="lg-w"></i>波浪轉折</span><span><i class="lg-p"></i>預期路徑</span><span><i class="lg-i"></i>失效停損</span><span><i class="lg-t"></i>${P.phase === "trend" ? "預估終點" : "目標 1"}</span></div>`;
    return `<div class="st-head"><h3>${esc(P.tf_label)}布局圖解</h3></div>${svg}${legend}<ol class="pf-steps">${steps.map((x) => `<li>${x}</li>`).join("")}</ol>`;
  }

  // 60 分 K 小圖：K 棒、日線布局區（色帶）、各批價位與 1／2／3 點
  function r123Svg(G, P) {
    const B = G.bars, Wd = 640, Ht = 260, pl = 8, pr = 62, pt = 12, pb = 22;
    const lv = (P.batches || []).map((b) => b.price);
    let lo = Math.min(...B.map((b) => b[3]), ...lv), hi = Math.max(...B.map((b) => b[2]), ...lv);
    const pad = (hi - lo) * 0.05; lo -= pad; hi += pad;
    const n = B.length, cw = (Wd - pl - pr) / n;
    const lh = lg(hi), ll = lg(lo);
    const X = (i) => pl + cw * (i + 0.5), Y = (v) => pt + (lh - lg(v)) / (lh - ll) * (Ht - pt - pb);
    const up = P.dir === 1;
    let g = "";
    if (P.zone) g += `<rect x="${pl}" y="${Y(P.zone[1])}" width="${Wd - pl - pr}" height="${Math.max(1, Y(P.zone[0]) - Y(P.zone[1]))}" class="z"/>`;
    lv.forEach((v, i) => { g += `<line x1="${pl}" x2="${X(n - 1)}" y1="${Y(v)}" y2="${Y(v)}" class="lv"/><text x="${Wd - pr + 4}" y="${Y(v) + 4}" class="lt">${i + 1} ${fp(v)}</text>`; });
    B.forEach((b, i) => {
      const [, o, h, l, c] = b, cl = c >= o ? "u" : "d";
      g += `<line x1="${X(i)}" x2="${X(i)}" y1="${Y(h)}" y2="${Y(l)}" class="w ${cl}"/><rect x="${X(i) - cw * 0.35}" y="${Y(Math.max(o, c))}" width="${Math.max(1, cw * 0.7)}" height="${Math.max(1, Math.abs(Y(o) - Y(c)))}" class="b ${cl}"/>`;
    });
    const idx = (t) => B.findIndex((b) => b[0] === t);
    [["1", G.p1, up], ["2", G.p2, !up], ["3", G.p3, up]].forEach(([lab, p, below]) => {
      if (!p) return; const i = idx(p.t); if (i < 0) return;
      const y = Y(p.price) + (below ? 16 : -8);
      g += `<circle cx="${X(i)}" cy="${Y(p.price)}" r="3.5" class="pt"/><text x="${X(i)}" y="${y}" class="pl" text-anchor="middle">${lab}</text>`;
    });
    if (G.p2) g += `<line x1="${X(Math.max(0, idx(G.p2.t)))}" x2="${Wd - pr}" y1="${Y(G.p2.price)}" y2="${Y(G.p2.price)}" class="br"/>`;
    const tl = [0, Math.floor(n / 2), n - 1].map((i) => `<text x="${X(i)}" y="${Ht - 6}" class="tt" text-anchor="middle">${B[i][0].slice(5, 13).replace("-", "/")}</text>`).join("");
    return `<svg class="r123-svg" viewBox="0 0 ${Wd} ${Ht}" role="img" aria-label="小週期 K 線與 123 點位">${g}${tl}</svg>
      <p class="sub">色帶是${esc(P.tf_label)}${up ? "回檔" : "反彈"}區，橫線是各批價位，虛線是第 2 點（進場價）。</p>`;
  }

  function savePref() {
    try {
      const old = JSON.parse(localStorage.getItem("st-pref") || "{}");
      const prod = typeof old.prod === "object" && old.prod ? old.prod : {};
      prod[state.mkt] = $("#st-prod").value;
      localStorage.setItem("st-pref", JSON.stringify({ total: +$("#st-total").value || 0, mode: $("#st-mode").value, prod }));
    } catch (e) { /* 無法儲存就略過 */ }
  }

  // 依總口數分配到各批：平均分配時餘數給前面的批次（較容易成交），金字塔依 1:2:3 越深越多
  function allocate() {
    const inputs = [...document.querySelectorAll("#st-batches .lots")];
    const total = Math.max(0, Math.floor(+$("#st-total").value || 0));
    const w = $("#st-mode").value === "pyr" ? inputs.map((_, i) => i + 1) : inputs.map(() => 1);
    const sw = w.reduce((a, b) => a + b, 0);
    const raw = w.map((x) => (total * x) / sw);
    const lots = raw.map(Math.floor);
    let left = total - lots.reduce((a, b) => a + b, 0);
    const order = raw.map((r, i) => [r - Math.floor(r), i])
      .sort((a, b) => b[0] - a[0] || ($("#st-mode").value === "pyr" ? b[1] - a[1] : a[1] - b[1]));
    for (const [, i] of order) { if (left <= 0) break; lots[i]++; left--; }
    inputs.forEach((el, i) => { el.value = lots[i]; });
    savePref();
    calcCost();
  }

  function calcCost() {
    const P = SP.plans[state.stTf] || SP.plans.D, up = P.dir === 1, prod = $("#st-prod").value, mult = SP.pt[prod] || 1;
    const unit = (W && W.unit) || "元", money = futureProduct(state.mkt) ? "元（新台幣）" : unit;
    const lots = [...document.querySelectorAll("#st-batches .lots")].map((el) => Math.max(0, +el.value || 0));
    const n = lots.reduce((a, b) => a + b, 0);
    const total = Math.floor(+$("#st-total").value || 0);
    if (!n) { $("#st-calc").innerHTML = `<p class="sub">先輸入可下單總口數，系統會分配到各批並計算平均成本；各批口數也可以再自行調整。</p>`; return; }
    const over = total && n !== total ? `<p class="st-exit warn">各批合計 ${n} 口，與可下單總口數 ${total} 口不同。</p>` : "";
    const avg = P.batches.reduce((a, b, i) => a + b.price * lots[i], 0) / n;
    const d = P.dir;
    const g1 = (P.t1 - avg) * d * n * mult, g2 = (P.t2 - avg) * d * n * mult;
    const loss = P.stop != null ? (avg - P.stop) * d * n * mult : null;
    const rr = loss > 0 ? g1 / loss : 0;
    let cum = 0, rows = "";
    P.batches.forEach((b, i) => {
      if (!lots[i]) return;
      cum += lots[i];
      const a = P.batches.slice(0, i + 1).reduce((s, x, j) => s + x.price * lots[j], 0) / cum;
      rows += `<tr><td>成交到第 ${i + 1} 批</td><td class="r">${cum} 口，均價 ${fp(a)}</td></tr>`;
    });
    $("#st-calc").innerHTML = `<table><tbody>${rows}
      <tr><td><b>全部成交平均成本</b></td><td class="r"><b>${fp(avg)}</b></td></tr>
      <tr><td>目標 1（${esc(P.t1_src)}）</td><td class="r">${fp(P.t1)}，獲利 <span class="up">${fmt(g1)}</span> ${money}</td></tr>
      <tr><td>目標 2（${esc(P.t2_src)}）</td><td class="r">${fp(P.t2)}，獲利 <span class="up">${fmt(g2)}</span> ${money}</td></tr>
      ${loss != null ? `<tr><td>失效停損（${esc(P.stop_src || "")}）</td><td class="r">${fp(P.stop)}，虧損 <span class="down">${fmt(loss)}</span> ${money}</td></tr>
      <tr><td>風險報酬比（目標 1 ÷ 停損）</td><td class="r">${rr.toFixed(2)}</td></tr>` : ""}
      </tbody></table>${P.dropped && P.dropped.length ? `<p class="st-exit warn">${esc(P.dropped.join("、"))} 已超過失效停損，不布局。</p>` : ""}${over}${P.phase === "trend" ? `<p class="st-exit warn">推動浪還在進行，以上是預估價位，等推動浪結束後會依實際高低點重算。</p>` : ""}<p class="st-exit">${futureProduct(state.mkt) ? `${esc(prod)}每點 ${fmt(mult)} 元（新台幣）。` : unit === "點" ? "指數沒有每點價值，損益以點數 × 口數表示。" : `${esc(prod)}每點 ${mult} ${unit}。`}部分批次沒成交時，以上面「成交到第 N 批」的均價為準。</p>`;
  }

  // ---------- 選擇權籌碼 ----------
  const sg = (n) => n == null ? "—" : `${n > 0 ? "+" : ""}${fmt(n)}`;
  const dcell = (n) => `<span class="num ${cls(n)}">${sg(n)}</span>`;
  const md = (d) => d ? d.slice(5).replace("-", "/") : "";

  function renderChips(C) {
    if (!C) { $("#chip-meta").textContent = "選擇權籌碼資料還沒產生，下一次每日更新後會出現。"; return; }
    const A = C.analysis;
    const v = $("#chip-verdict");
    v.textContent = A.title;
    v.className = "verdict " + (A.verdict === "偏空" ? "bear" : A.verdict === "偏多" ? "bull" : "");
    $("#chip-meta").textContent = `資料日期 ${C.date}，日變化與 ${md(C.prev)} 比較。單位：口。`;
    const f = C.fut, ix = C.index;
    $("#chip-market").innerHTML = `
      <div><dt>${C.month ? C.month.slice(4) + " 月" : ""}台指期</dt><dd>${fmt(f.close)} <small class="num ${cls(f.chg)}">${sg(f.chg)}</small></dd></div>
      <div><dt>加權指數</dt><dd>${ix ? fmt(ix.close, 2) : "—"} <small class="num ${cls(ix && ix.chg)}">${ix ? sg(ix.chg) : ""}</small></dd></div>
      <div><dt>期現價差</dt><dd class="${cls(C.basis)}">${C.basis == null ? "—" : (C.basis > 0 ? "+" : "") + fmt(C.basis, 2)}</dd></div>
      <div><dt>Put／Call 未平倉比</dt><dd>${C.pcr == null ? "—" : fmt(C.pcr, 1) + "%"}</dd></div>`;
    $("#chip-one").textContent = C.oneliner;
    const li = (arr) => arr.length ? `<ul>${arr.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : `<p class="none">沒有明顯訊號</p>`;
    $("#chip-evid").innerHTML = `<div><h4 class="down">偏空證據</h4>${li(A.bear)}</div><div><h4 class="up">偏多證據</h4>${li(A.bull)}</div>`;

    $("#chip-net").innerHTML = `<thead><tr><th>類別</th><th class="r">多空淨額</th><th class="r">日變化</th><th>判讀</th></tr></thead>
      <tbody>${C.net.map((r) => `<tr><td>${esc(r.name)}</td><td class="r num ${cls(r.v)}"><b>${fmt(r.v)}</b></td><td class="r">${dcell(r.d)}</td><td>${esc(r.tag)}</td></tr>`).join("")}</tbody>`;

    $("#chip-cp-title").textContent = `大額交易人 Call／Put 明細（${C.month} 月選）`;
    const pair = (v, d) => `<td class="r num">${fmt(v)}</td><td class="r d">${dcell(d)}</td>`;
    $("#chip-cp").innerHTML = `<thead><tr><th>類別</th><th class="r">買 Call</th><th class="r">變化</th><th class="r">賣 Call</th><th class="r">變化</th><th class="r">買 Put</th><th class="r">變化</th><th class="r">賣 Put</th><th class="r">變化</th></tr></thead>
      <tbody>${C.cp.map((r) => `<tr><td>${esc(r.name)}</td>${pair(r.cb, r.dcb)}${pair(r.cs, r.dcs)}${pair(r.pb, r.dpb)}${pair(r.ps, r.dps)}</tr>`).join("")}
      ${C.total && C.total.C ? `<tr><td>全市場未平倉</td><td class="r num" colspan="4">Call ${fmt(C.total.C)}</td><td class="r num" colspan="4">Put ${fmt(C.total.P)}</td></tr>` : ""}</tbody>`;

    $("#chip-syn-title").textContent = `大額交易人合成多空（${C.month} 月選）`;
    $("#chip-syn").innerHTML = `<thead><tr><th>類別</th><th class="r">多方</th><th class="r">變化</th><th class="r">空方</th><th class="r">變化</th><th class="r">淨額</th><th class="r">變化</th></tr></thead>
      <tbody>${C.synth.map((r) => `<tr><td>${esc(r.name)}</td>${pair(r.long, r.dlong)}${pair(r.short, r.dshort)}<td class="r num ${cls(r.net)}"><b>${sg(r.net)}</b></td><td class="r d">${dcell(r.dnet)}</td></tr>`).join("")}</tbody>`;

    const W2 = C.walls || {};
    const wall = (w, t) => !w ? "" : `<div class="wall"><h4>${t}：${esc(w.contract)}（${md(w.exp)} 到期）</h4>
      <div class="lv"><div><b>壓力（Call 未平倉）</b>${w.res.slice().reverse().map((k) => `<span>${fmt(k[0])}　<small>${fmt(k[1])} 口</small></span>`).join("") || "—"}</div>
      <div><b>支撐（Put 未平倉）</b>${w.sup.map((k) => `<span>${fmt(k[0])}　<small>${fmt(k[1])} 口</small></span>`).join("") || "—"}</div></div>
      <div class="mx">最大 Call 未平倉 ${w.max_call ? fmt(w.max_call[0]) : "—"}，最大 Put 未平倉 ${w.max_put ? fmt(w.max_put[0]) : "—"}</div></div>`;
    $("#chip-walls").innerHTML = (wall(W2.weekly, "週選") + wall(W2.monthly, "月選")) || `<p class="sub">尚無履約價未平倉資料。</p>`;
    $("#chip-watch").innerHTML = C.watch.map((w) => `<li><b class="${w.side === "多方" ? "up" : w.side === "空方" ? "down" : ""}">${esc(w.side)}</b>${esc(w.text)}</li>`).join("");
    $("#chip-big").innerHTML = C.biggest.map((b) => `<li>${esc(b.name)}<span class="num ${cls(b.d)}">${sg(b.d)}</span></li>`).join("");

    const G = ["外資", "投信", "自營商", "前五大", "前十大", "前五特法", "前十特法"];
    $("#chip-hist").innerHTML = `<thead><tr><th>日期</th>${G.map((g) => `<th class="r">${g}</th>`).join("")}<th class="r">台指期</th></tr></thead>
      <tbody>${C.history.map((h) => `<tr><td class="num">${md(h.date)}</td>${G.map((g) => {
        const x = h.rows[g];
        return x ? `<td class="r"><span class="num ${cls(x[0])}">${fmt(x[0])}</span><br><small class="num ${cls(x[1])}">${x[1] == null ? "" : sg(x[1])}</small></td>` : `<td class="r">—</td>`;
      }).join("")}<td class="r num">${fmt(h.fut)}</td></tr>`).join("")}</tbody>`;
  }

  // ---------- 台指期波浪 ----------
  function renderWave() {
    const box = $("#wave-chart"), cards = $("#wave-counts");
    const R = W && W[state.waveTf];
    if (!R) {
      $("#wave-meta").textContent = W ? "這個商品沒有此週期的資料。" : CAPITAL_ONLY?'等待群益歷史K線與行情；不足60根已收K暫不推算。':"資料還沒產生，下一次每日更新後會出現。";
      box.innerHTML = ""; cards.innerHTML = "";
      return;
    }
    $("#wave-meta").textContent = `${W.contract}，${R.name}（${R.session}）。最新 ${R.last.t.replaceAll("-", "/")} 收 ${fp(R.last.c)}${R.partial ? "（最後一根是收盤後的夜盤，交易日尚未結束）" : ""}。圖上虛線為 ${R.threshold} 的轉折。`;
    if(datedFuture() && R.bars.length<60)$('#wave-meta').textContent=`${W.contract}，日線（全日盤）。最後成交日 ${R.last.t} 收 ${fp(R.last.c)}。本月份共 ${R.bars.length} 根日K；未滿60根暫不推算波浪。`;
    box.innerHTML = waveSvg(R) + (R.kd ? kdSvg(R) : "");
    box.dataset.w = box.clientWidth;
    const [p, a] = R.counts;
    if (!p) { cards.innerHTML = `<div class="wave-card"><p>目前的轉折點不足以判斷波浪，等走勢更明確後會自動產生推算。</p></div>`; return; }
    cards.innerHTML = countCard(p, "主要推算", "") + (a ? countCard(a, "替代推算", "alt") : "");
  }

  let rsz;
  window.addEventListener("resize", () => {
    clearTimeout(rsz);
    rsz = setTimeout(() => { const b = $("#wave-chart"); if (W && Math.abs(b.clientWidth - (+b.dataset.w || 0)) > 20) {renderWave();if(currentMarketMeta?.contract_mode)showContractStatus(currentMarketMeta);} }, 200);
  });

  function countCard(c, title, k) {
    const rows = c.targets.map((t) => `<tr class="${t.reached ? "done" : ""}"><td>${esc(t.name)}${t.reached ? "（已到）" : ""}</td><td class="r">${fp(t.price)}</td></tr>`).join("");
    return `<div class="wave-card ${k}">
      <h3>${title}：${esc(c.now)}</h3>
      <div class="kind">${esc(c.pattern)}，轉折門檻 ${esc(c.threshold || "")} <span class="fit">符合度 <i><b style="width:${c.confidence}%"></b></i> ${c.confidence}</span></div>
      <p>${esc(c.text)}</p>
      ${c.kd ? `<p class="kd-fit">KD 配合：${c.kd.map((x, i) => `<span class="${x.ok ? "ok" : "no"}">${esc(c.points[i][2] === "0" ? "起" : c.points[i][2])} K${Math.round(x.k)}${x.ok ? "✓" : "✗"}</span>`).join(" ")}（${esc(c.kd_fit)} 個轉折符合${c.kd_loose ? "；沒有每個轉折都符合 KD 的數法，此為放寬一個轉折的結果" : ""}）${c.kd_div ? `<br><b>${esc(c.kd_div)}</b>` : ""}</p>` : ""}
      <table><tbody>${rows}<tr class="inv"><td>失效價（${esc(c.invalid.note)}）</td><td class="r">${fp(c.invalid.price)}</td></tr></tbody></table>
    </div>`;
  }

  // KD 副圖：與上方 K 線同樣的根數，20／80 虛線、黃金／死亡交叉標點
  function kdSvg(R) {
    // 與上方 K 線圖相同的橫向座標（含右側推演空間），K 棒與 KD 上下對齊
    const n = R.kd.length, Wd = Math.max(300, Math.round($("#wave-chart").clientWidth - 8)), mob = Wd < 640, Ht = 110, pt = 8, pb = 16;
    const pl = 6, Rm = mob ? 56 : 72, nb = R.bars.length, fs = Math.max(6, Math.round(nb * (mob ? 0.18 : 0.14)));
    const cw = (Wd - Rm - pl) / (nb + fs), pr = Rm;
    const X = (i) => pl + cw * (i + (nb - n) + 0.5), Y = (v) => pt + (100 - v) / 100 * (Ht - pt - pb);
    const line = (j) => R.kd.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v[j]).toFixed(1)}`).join("");
    let g = [20, 50, 80].map((v) => `<line x1="${pl}" x2="${X(n - 1)}" y1="${Y(v)}" y2="${Y(v)}" class="${v === 50 ? "mid" : "lv"}"/><text x="${Wd - Rm + 4}" y="${Y(v) + 4}" class="t">${v}</text>`).join("");
    g += `<path d="${line(1)}" class="d"/><path d="${line(0)}" class="k"/>`;
    for (let i = 1; i < n; i++) {
      const [k0, d0] = R.kd[i - 1], [k1, d1] = R.kd[i];
      if (k0 <= d0 && k1 > d1 && k1 <= 30) g += `<circle cx="${X(i)}" cy="${Y(k1)}" r="3" class="gc"/>`;
      if (k0 >= d0 && k1 < d1 && k1 >= 70) g += `<circle cx="${X(i)}" cy="${Y(k1)}" r="3" class="dc"/>`;
    }
    const nw = R.kd_now || {};
    return `<svg viewBox="0 0 ${Wd} ${Ht}" class="kd-svg">${g}<text x="${pl + 4}" y="${pt + 10}" class="lab">KD(9,3,3)　K ${nw.k ?? "—"}　D ${nw.d ?? "—"}　${esc(nw.state || "")}</text></svg>`;
  }

  // 估計文字寬度並換行（中文字約等於字級寬，英數約 0.6 倍）
  function wrapText(t, fs, maxW) {
    const toks = t.match(/[\x21-\x7e]+|\s|[^\x00-\x7f]/g) || [];
    const wid = (x) => [...x].reduce((a, ch) => a + (/[\x00-\x7f]/.test(ch) ? fs * 0.62 : fs), 0);
    const out = []; let line = "", w = 0;
    for (const tk of toks) {
      const tw = wid(tk);
      const closing = /^[，。、；：）」』！？]$/.test(tk);
      if (w + tw > maxW && line.trim() && !closing) { out.push(line.trim()); line = ""; w = 0; if (tk === " ") continue; }
      line += tk; w += tw;
    }
    if (line.trim()) out.push(line.trim());
    return out;
  }

  // 各浪比例說明
  function legNotes(c) {
    const p = c.points, out = [];
    const len = (i) => Math.abs(lg(p[i][1]) - lg(p[i - 1][1]));
    p.forEach((pt, i) => {
      if (i === 0) return;
      const lab = pt[2]; let t = "";
      if (lab === "2" || lab === "4" || lab === "B") t = `回 ${(len(i) / len(i - 1)).toFixed(2)}`;
      else if (lab === "3" || lab === "5") t = `${(len(i) / len(1)).toFixed(2)}×①`;
      else if (lab === "C") t = `${(len(i) / len(1)).toFixed(2)}×A`;
      if (t) out.push({ a: p[i - 1], b: pt, t });
    });
    return out;
  }

  function noteRows(R, mob, tw) {
    const prim = R.counts[0], alt = R.counts[1];
    const f1 = mob ? 13 : 14, f2 = mob ? 12 : 13, lh = f2 + 6;
    const rows = [];
    const add = (c, t, f, h) => wrapText(t, f, tw).forEach((x) => rows.push({ c, t: x, f, h }));
    if (!prim) {
      add("note-t", "目前轉折點不足，暫無推算。", f2, lh);
    } else {
      const pts = prim.points, next = prim.targets.find((t) => !t.reached);
      const seq = pts.slice(1).map((p) => p[2]).join("-");
      add("note-m", `${W.contract} ${R.name}，${R.last.t.replaceAll("-", "/")} 收 ${fp(R.last.c)}`, f2, lh);
      add("note-h", `主要推算：${prim.now}（${prim.pattern}）`, f1, f1 + 8);
      add("note-t", `${fp(pts[0][1])} → ${fp(pts[pts.length - 1][1])} 已走完 ${seq}，各段比例標在線旁。`, f2, lh);
      if (next) add("note-g", `推演：往 ${fp(next.price)}（${next.name}）前進，見右側虛線箭頭。`, f2, lh);
      add("note-t", `${prim.invalid.note} ${fp(prim.invalid.price)} 此推算失效（灰色點線）。`, f2, lh);
      if (alt) add("note-m", `替代推算：${alt.now}（${alt.pattern}），失效 ${fp(alt.invalid.price)}，圖上藍色虛線。`, f2, lh);
    }
    return { rows, h: 16 + rows.reduce((a, r) => a + r.h, 0) };
  }

  function waveSvg(R) {
    const W_ = Math.max(300, Math.round($("#wave-chart").clientWidth - 8));
    const mob = W_ < 640;
    const L = 6, Rm = mob ? 56 : 72, B = 24;
    const bars = R.bars, n = bars.length;
    // 上方說明區：先排好文字決定高度
    const note = noteRows(R, mob, W_ - Rm - L - 20);
    const T = note.h + 22;
    const H_ = (mob ? 330 : 420) + T;
    const fs = Math.max(6, Math.round(n * (mob ? 0.18 : 0.14)));   // 右側推演空間（K 棒數）
    const idx = new Map(bars.map((b, i) => [b[0], i]));
    const prim = R.counts[0], alt = R.counts[1];
    const lc = R.last.c;
    let lo = Math.min(...bars.map((b) => b[3])), hi = Math.max(...bars.map((b) => b[2]));
    const span0 = hi - lo;
    const lines = [];
    const next = prim ? prim.targets.find((t) => !t.reached) : null;
    if (prim) {
      prim.targets.filter((t) => !t.reached).slice(0, 3).forEach((t) =>
        lines.push({ v: t.price, cls: "tgt", txt: `${t.name}　${fp(t.price)}` }));
      lines.push({ v: prim.invalid.price, cls: "inv", txt: `失效 ${fp(prim.invalid.price)}` });
    }
    lines.forEach((l) => { if (l.v > hi && l.v < hi + span0 * 0.6) hi = l.v; if (l.v < lo && l.v > lo - span0 * 0.6) lo = l.v; });
    const pad = Math.max((hi - lo) * 0.06, Math.abs(hi)*0.0001, 0.01); hi += pad; lo -= pad;
    const plotR = W_ - Rm;
    const cw = (plotR - L) / (n + fs);
    const X = (i) => L + cw * (i + 0.5);
    const lh = lg(hi), ll = lg(lo);
    const Y = (v) => T + (lh - lg(v)) / (lh - ll) * (H_ - T - B);
    const V = (y) => ul(lh - (y - T) / (H_ - T - B) * (lh - ll));
    const xl = X(n - 1);
    let s = `<svg viewBox="0 0 ${W_} ${H_}" xmlns="http://www.w3.org/2000/svg">
      <defs><marker id="arw" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="var(--gold)"/></marker></defs>`;
    // 推演區底色
    s += `<rect class="fut" x="${xl + cw / 2}" y="${T}" width="${plotR - xl - cw / 2}" height="${H_ - T - B}"/>`;
    s += `<text class="fut-t" x="${(xl + plotR) / 2}" y="${H_ - B - 6}" text-anchor="middle">推演</text>`;
    // 格線與座標
    const step = niceStep((hi - lo) / 6);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      s += `<line class="grid-l" x1="${L}" x2="${plotR}" y1="${Y(v)}" y2="${Y(v)}"/><text class="ax" x="${plotR + 6}" y="${Y(v) + 4}">${fp(v)}</text>`;
    }
    let lastLab = "", lx = -999;
    bars.forEach((b, i) => {
      const lab = R.tf === "D" ? b[0].slice(0, 7).replace("-", "/")
        : R.tf === "M5" ? b[0].slice(5, 10).replace("-", "/") + " " + b[0].slice(11, 13) + "時"
        : b[0].slice(5, 10).replace("-", "/");
      if (lab !== lastLab && i > 2 && X(i) - lx > (R.tf === "M5" ? 90 : 70) && X(i) < xl - 20) {
        s += `<text class="ax" x="${X(i)}" y="${H_ - 8}" text-anchor="middle">${lab}</text>`; lx = X(i);
      }
      lastLab = lab;
    });
    // K 棒（紅漲綠跌）
    const bw = Math.max(1, cw * 0.62);
    bars.forEach((b, i) => {
      const [, o, h, l, c] = b, up = c >= o, col = up ? "var(--up)" : "var(--down)";
      s += `<line x1="${X(i)}" x2="${X(i)}" y1="${Y(h)}" y2="${Y(l)}" stroke="${col}" stroke-width="1"/>`;
      s += `<rect x="${X(i) - bw / 2}" y="${Y(Math.max(o, c))}" width="${bw}" height="${Math.max(1, Math.abs(Y(o) - Y(c)))}" fill="${up ? "var(--panel)" : col}" stroke="${col}" stroke-width="1"/>`;
    });
    // 全部轉折
    const zz = R.pivots.filter((p) => idx.has(p[0])).map((p) => `${X(idx.get(p[0]))},${Y(p[1])}`);
    if (zz.length > 1) s += `<polyline class="zz" points="${zz.join(" ")}"/>`;
    // 目標價與失效價（名稱標在推演區）
    lines.forEach((l) => {
      if (l.v < lo || l.v > hi) return;
      s += `<line class="${l.cls}" x1="${L}" x2="${plotR}" y1="${Y(l.v)}" y2="${Y(l.v)}"/>`;
      s += `<text class="${l.cls}-t halo" x="${plotR - 4}" y="${Y(l.v) + (l.cls === "tgt" ? 14 : -4)}" text-anchor="end">${esc(l.txt)}</text>`;
    });
    // 推演路徑：最新價 → 下一個目標
    if (next && next.price >= lo && next.price <= hi) {
      const x2 = X(n - 1 + fs * 0.82);
      s += `<path class="proj" d="M${xl},${Y(lc)} Q${(xl + x2) / 2},${Y(lc)} ${x2},${Y(next.price)}" marker-end="url(#arw)"/>`;
    }
    // 替代推算、主要推算
    const path = (c, cls) => {
      const pts = c.points.filter((p) => idx.has(p[0]));
      if (pts.length > 1) s += `<polyline class="cnt ${cls}" points="${pts.map((p) => `${X(idx.get(p[0]))},${Y(p[1])}`).join(" ")}"/>`;
    };
    if (alt) path(alt, "alt");
    if (prim) {
      path(prim, "");
      if (prim.points.length > 1) {
        const last = prim.points[prim.points.length - 1];
        if (idx.has(last[0])) s += `<line class="cnt-cur" x1="${X(idx.get(last[0]))}" y1="${Y(last[1])}" x2="${xl}" y2="${Y(lc)}"/>`;
      }
      legNotes(prim).forEach((g) => {
        if (!idx.has(g.a[0]) || !idx.has(g.b[0])) return;
        const mx = (X(idx.get(g.a[0])) + X(idx.get(g.b[0]))) / 2, my = (Y(g.a[1]) + Y(g.b[1])) / 2;
        s += `<text class="ratio halo" x="${mx + 6}" y="${my}">${g.t}</text>`;
      });
      const pts = prim.points;
      pts.forEach((p, j) => {
        if (!idx.has(p[0]) || p[2] === "0") return;
        const isHigh = j > 0 ? p[1] > pts[j - 1][1] : p[1] > pts[1][1];
        const y = Y(p[1]) + (isHigh ? -14 : 14);
        s += `<g class="lbl"><circle cx="${X(idx.get(p[0]))}" cy="${y}" r="${mob ? 8 : 9}"/><text x="${X(idx.get(p[0]))}" y="${y}">${esc(p[2])}</text></g>`;
      });
    }
    // 說明區
    s += `<g class="note"><rect x="${L}" y="8" width="${plotR - L}" height="${note.h}" rx="8"/>`;
    let yy = 8 + 10;
    note.rows.forEach((r) => {
      s += `<text class="${r.c}" x="${L + 10}" y="${yy + r.f}" style="font-size:${r.f}px">${esc(r.t)}</text>`;
      yy += r.h;
    });
    s += `</g>`;
    // 最新價
    s += `<rect class="now-b" x="${plotR + 2}" y="${Y(lc) - 9}" width="${Rm - 4}" height="18" rx="3"/><text class="now-t" x="${plotR + 6}" y="${Y(lc) + 4}">${fp(lc)}</text>`;
    return s + "</svg>";
  }

  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const m = raw / p;
    return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * p;
  }

  // ---------- 切換按鈕 ----------
  const renderers = { stTf: () => { renderStrategy(SP); liveDraw(true); }, waveTf: () => renderWave() };
  document.querySelectorAll(".seg-ctl[data-group]").forEach((g) => {
    g.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b || b.disabled || !renderers[g.dataset.group]) return;
      g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      state[g.dataset.group] = b.dataset.v;
      renderers[g.dataset.group]();
      if(currentMarketMeta?.contract_mode)showContractStatus(currentMarketMeta);
    });
  });

  // ---------- 導覽標示目前區塊 ----------
  const links = [...document.querySelectorAll(".tabs a")];
  const io = new IntersectionObserver((ents) => {
    ents.forEach((en) => {
      if (!en.isIntersecting) return;
      links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + en.target.id));
    });
  }, { rootMargin: "-40% 0px -55% 0px" });
  document.querySelectorAll("main section[id]").forEach((s) => io.observe(s));

  load().catch(error=>{if(CAPITAL_ONLY)showAnalysisError(error);else console.error(error);});
})();




