(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const MK = { twse: "上市", tpex: "上櫃", all: "全部" };
  const state = { pickMarket: "twse", histMarket: "twse", dtSide: "long", volMarket: "twse", turnMarket: "twse" };
  let D = null, H = null;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => n == null ? "—" : Number(n).toLocaleString("zh-TW", { minimumFractionDigits: d, maximumFractionDigits: d });
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "down" : "");
  const pct = (n) => n == null ? "—" : `${n > 0 ? "+" : ""}${fmt(n, 2)}%`;
  const price = (n) => fmt(n, n != null && n < 50 ? 2 : n < 1000 ? 1 : 0);
  const stk = (x, extra = "") =>
    `<td class="stk"><b>${esc(x.name)}</b><small class="num">${esc(x.code)}</small> <small>${esc(x.ind || "")}${x.market && state._showMkt ? "、" + MK[x.market] : ""}</small>${extra}</td>`;

  async function load() {
    try {
      const v = Date.now().toString(36);
      [D, H] = await Promise.all([
        fetch(`data/latest.json?v=${v}`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }),
        fetch(`data/history.json?v=${v}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
    } catch (e) {
      $("#data-date").textContent = "尚無資料";
      $("#pick-list").innerHTML = `<li class="empty">還沒有產生資料。第一次每日排程跑完後，這裡會出現當天的推薦名單。</li>`;
      return;
    }
    renderHeader(); renderPicks(); renderHistory(); renderDaytrade(); renderVolume(); renderTurnover();
  }

  function renderHeader() {
    const d = new Date(D.date + "T00:00:00");
    const wk = "日一二三四五六"[d.getDay()];
    $("#data-date").textContent = `${D.date.replaceAll("-", "/")}（${wk}）`;
    const ix = D.index && D.index.twse;
    if (ix && ix.close) {
      $("#index-box").hidden = false;
      $("#idx-close").textContent = fmt(ix.close, 2);
      const el = $("#idx-chg");
      el.textContent = `${ix.chg > 0 ? "▲" : ix.chg < 0 ? "▼" : ""} ${fmt(Math.abs(ix.chg), 2)}（${pct(ix.pct)}）`;
      el.className = "idx-chg num " + cls(ix.chg);
    }
    const t = D.tdcc || {};
    $("#tdcc-note").textContent = t.date
      ? `目前使用的集保資料：${t.date}${t.prev ? `，與 ${t.prev} 比較` : "（尚無前一週資料可比較，大戶項目暫時以 0 分計）"}。`
      : "尚未取得集保資料，大戶項目暫時以 0 分計。";
    $("#gen-note").textContent = `本次資料產生時間：${(D.generated_at || "").replace("T", " ")}，使用 ${D.trading_days} 個交易日的歷史資料。`;
  }

  // ---------- 推薦 ----------
  function pickList() {
    const m = state.pickMarket;
    const list = m === "all" ? [...D.picks.twse, ...D.picks.tpex] : D.picks[m].slice();
    const key = $("#pick-sort").value;
    list.sort((a, b) => (b[key] ?? -1e9) - (a[key] ?? -1e9));
    return list;
  }

  function renderPicks() {
    const m = state.pickMarket;
    const list = pickList();
    $("#picks-title").textContent = m === "all" ? `上市、上櫃推薦 ${list.length} 檔` : `${MK[m]}推薦 ${list.length} 檔`;
    const up = list.filter((x) => x.pct > 0).length, dn = list.filter((x) => x.pct < 0).length;
    const avg = list.length ? list.reduce((s, x) => s + x.score, 0) / list.length : 0;
    const inds = {};
    list.forEach((x) => (inds[x.ind] = (inds[x.ind] || 0) + 1));
    const top = Object.entries(inds).sort((a, b) => b[1] - a[1])[0];
    $("#pick-stats").innerHTML = `
      <div><dt>推薦檔數</dt><dd>${list.length}</dd></div>
      <div><dt>平均推薦分數</dt><dd>${fmt(avg, 1)}</dd></div>
      <div><dt>上漲／下跌</dt><dd><span class="up">${up}</span>／<span class="down">${dn}</span></dd></div>
      <div><dt>最多檔的產業</dt><dd class="txt">${top ? `${esc(top[0])} ${top[1]} 檔` : "—"}</dd></div>`;
    if (!list.length) {
      $("#pick-list").innerHTML = `<li class="empty">今天沒有符合條件的股票。</li>`;
      return;
    }
    const names = ["法人當日", "法人 5 日", "連買", "大戶", "融資"];
    const max = [25, 25, 10, 25, 15];
    $("#pick-list").innerHTML = list.map((x, i) => `
      <li class="pick">
        <button aria-expanded="false">
          <span class="rank">${i + 1}</span>
          <span class="who"><span class="nm">${esc(x.name)}</span>
            <span class="meta"><span class="num">${esc(x.code)}</span> ${esc(x.ind)}${m === "all" ? "、" + MK[x.market] : ""}</span></span>
          <span class="px"><span class="c">${price(x.close)}</span><br><span class="p ${cls(x.pct)}">${pct(x.pct)}</span></span>
          <span class="sc"><span class="v">${fmt(x.score, 1)}</span>
            <span class="bar" aria-hidden="true">${x.parts.map((p) => `<i style="width:${p}%"></i>`).join("")}</span></span>
        </button>
        <div class="detail">
          <ul class="chips">${x.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
          <div class="parts">${x.parts.map((p, k) => `<div><i style="background:var(--s${k + 1})"></i>${names[k]}<b>${fmt(p, 1)}<small> / ${max[k]}</small></b></div>`).join("")}</div>
          <p class="sub">成交量 ${fmt(x.vol)} 張，成交金額 ${fmt(x.amt, 2)} 億${x.big != null ? `，千張大戶持股 ${fmt(x.big, 2)}%` : ""}</p>
        </div>
      </li>`).join("");
  }

  $("#pick-list").addEventListener("click", (e) => {
    const b = e.target.closest(".pick > button");
    if (!b) return;
    const li = b.parentElement;
    const open = li.classList.toggle("open");
    b.setAttribute("aria-expanded", open);
  });
  $("#pick-sort").addEventListener("change", renderPicks);

  // ---------- 歷史 ----------
  function renderHistory() {
    const sel = $("#hist-date");
    if (!H || !H.days.length) {
      $("#hist-table").innerHTML = `<tbody><tr><td class="empty">累積兩個交易日以上的推薦後，這裡會列出過去的名單與之後的漲跌。</td></tr></tbody>`;
      return;
    }
    if (!sel.options.length) {
      sel.innerHTML = H.days.map((d) => `<option>${d.date}</option>`).join("");
      if (H.days.length > 1) sel.selectedIndex = 1;
      sel.addEventListener("change", renderHistory);
    }
    const day = H.days.find((d) => d.date === sel.value) || H.days[0];
    const m = state.histMarket;
    const last = H.last_close[m] || {};
    const rows = (day.picks[m] || []).map(([code, name, close, score]) => {
      const now = last[code];
      const ret = now != null && close ? (now / close - 1) * 100 : null;
      return { code, name, close, score, now, ret };
    });
    const rets = rows.filter((r) => r.ret != null);
    const avg = rets.length ? rets.reduce((s, r) => s + r.ret, 0) / rets.length : null;
    const win = rets.filter((r) => r.ret > 0).length;
    $("#hist-summary").innerHTML = day.date === H.date
      ? `這是今天（${day.date}）的名單，還沒有後續漲跌。選更早的日期可以看推薦後的表現。`
      : `${day.date} 的${MK[m]}名單，以推薦當天收盤價計算到 ${H.date} 收盤：平均 <span class="num ${cls(avg)}">${pct(avg)}</span>，上漲 ${win} 檔／共 ${rets.length} 檔。`;
    $("#hist-table").innerHTML = `
      <thead><tr><th>名次</th><th>股票</th><th class="r">分數</th><th class="r">推薦日收盤</th><th class="r">最新收盤</th><th class="r">之後漲跌</th></tr></thead>
      <tbody>${rows.map((r, i) => `<tr>
        <td class="num">${i + 1}</td>
        <td class="stk"><b>${esc(r.name)}</b><small class="num">${esc(r.code)}</small></td>
        <td class="r num">${fmt(r.score, 1)}</td>
        <td class="r num">${price(r.close)}</td>
        <td class="r num">${price(r.now)}</td>
        <td class="r num ${cls(r.ret)}">${day.date === H.date ? "—" : pct(r.ret)}</td></tr>`).join("")}</tbody>`;
  }

  // ---------- 當沖 ----------
  function renderDaytrade() {
    const list = D.daytrade[state.dtSide] || [];
    state._showMkt = true;
    $("#dt-table").innerHTML = list.length ? `
      <thead><tr><th>名次</th><th>股票與理由</th><th class="r">收盤</th><th class="r">漲跌幅</th><th class="r">週轉率</th><th class="r">當沖比率</th><th class="r">量增</th><th class="r">法人(張)</th><th class="r">分數</th></tr></thead>
      <tbody>${list.map((x) => `<tr>
        <td class="num">${x.rank}</td>
        ${stk(x, `<span class="why">${x.reasons.map(esc).join("，")}</span>${x.tag ? `<span class="tag">${esc(x.tag)}</span>` : ""}`)}
        <td class="r num">${price(x.close)}</td>
        <td class="r num ${cls(x.pct)}">${pct(x.pct)}</td>
        <td class="r num">${fmt(x.turn, 2)}%</td>
        <td class="r num">${fmt(x.dtr, 1)}%</td>
        <td class="r num">${fmt(x.volx, 1)} 倍</td>
        <td class="r num ${cls(x.inst)}">${fmt(x.inst)}</td>
        <td class="r num">${fmt(x.score, 1)}</td></tr>`).join("")}</tbody>`
      : `<tbody><tr><td class="empty">今天沒有符合條件的股票。</td></tr></tbody>`;
    state._showMkt = false;
  }

  // ---------- 成交量、週轉率 ----------
  function renderVolume() {
    const m = state.volMarket;
    $("#vol-title").textContent = `${MK[m]}成交量前 20 名`;
    const list = D.volume[m] || [];
    $("#vol-table").innerHTML = `
      <thead><tr><th>名次</th><th>股票</th><th class="r">收盤</th><th class="r">漲跌幅</th><th class="r">成交金額(億)</th><th class="r">週轉率</th><th class="r">成交量(張)</th></tr></thead>
      <tbody>${list.map((x) => `<tr><td class="num">${x.rank}</td>${stk(x)}
        <td class="r num">${price(x.close)}</td><td class="r num ${cls(x.pct)}">${pct(x.pct)}</td>
        <td class="r num">${fmt(x.amt, 2)}</td><td class="r num">${x.turn == null ? "—" : fmt(x.turn, 2) + "%"}</td>
        <td class="r num"><b>${fmt(x.vol)}</b></td></tr>`).join("")}</tbody>`;
  }
  function renderTurnover() {
    const m = state.turnMarket;
    $("#turn-title").textContent = `${MK[m]}週轉率前 20 名`;
    const list = D.turnover[m] || [];
    $("#turn-table").innerHTML = `
      <thead><tr><th>名次</th><th>股票</th><th class="r">收盤</th><th class="r">漲跌幅</th><th class="r">成交量(張)</th><th class="r">成交金額(億)</th><th class="r">週轉率</th></tr></thead>
      <tbody>${list.map((x) => `<tr><td class="num">${x.rank}</td>${stk(x)}
        <td class="r num">${price(x.close)}</td><td class="r num ${cls(x.pct)}">${pct(x.pct)}</td>
        <td class="r num">${fmt(x.vol)}</td><td class="r num">${fmt(x.amt, 2)}</td>
        <td class="r num"><b>${fmt(x.turn, 2)}%</b></td></tr>`).join("")}</tbody>`;
  }

  // ---------- 切換按鈕 ----------
  const renderers = { pickMarket: renderPicks, histMarket: renderHistory, dtSide: renderDaytrade, volMarket: renderVolume, turnMarket: renderTurnover };
  document.querySelectorAll(".seg-ctl").forEach((g) => {
    g.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b || !D) return;
      g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      state[g.dataset.group] = b.dataset.v;
      renderers[g.dataset.group]();
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

  load();
})();
