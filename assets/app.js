(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const MK = { twse: "上市", tpex: "上櫃", all: "全部" };
  const state = { waveTf: "D", pickMarket: "twse", histMarket: "twse", dtSide: "long", volMarket: "twse", turnMarket: "twse" };
  let D = null, H = null, W = null;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => n == null ? "—" : Number(n).toLocaleString("zh-TW", { minimumFractionDigits: d, maximumFractionDigits: d });
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "down" : "");
  const pct = (n) => n == null ? "—" : `${n > 0 ? "+" : ""}${fmt(n, 2)}%`;
  const price = (n) => fmt(n, n != null && n < 50 ? 2 : n < 1000 ? 1 : 0);
  const stk = (x, extra = "") =>
    `<td class="stk"><b>${esc(x.name)}</b><small class="num">${esc(x.code)}</small> <small>${esc(x.ind || "")}${x.market && state._showMkt ? "、" + MK[x.market] : ""}</small>${extra}</td>`;

  async function load() {
    fetch(`data/wave.json?v=${Date.now().toString(36)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
      .then((w) => { W = w; renderWave(); });
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


  // ---------- 台指期波浪 ----------
  function renderWave() {
    const box = $("#wave-chart"), cards = $("#wave-counts");
    const R = W && W[state.waveTf];
    if (!R) {
      $("#wave-meta").textContent = "台指期資料還沒產生，下一次每日更新後會出現。";
      box.innerHTML = ""; cards.innerHTML = "";
      return;
    }
    $("#wave-meta").textContent = `${W.contract}，${R.name}（${R.session}）。最新 ${R.last.t.replaceAll("-", "/")} 收 ${fmt(R.last.c)}。圖上虛線為 ${R.threshold} 的轉折。`;
    box.innerHTML = waveSvg(R);
    box.dataset.w = box.clientWidth;
    const [p, a] = R.counts;
    if (!p) { cards.innerHTML = `<div class="wave-card"><p>目前的轉折點不足以判斷波浪，等走勢更明確後會自動產生推算。</p></div>`; return; }
    cards.innerHTML = countCard(p, "主要推算", "") + (a ? countCard(a, "替代推算", "alt") : "");
  }

  let rsz;
  window.addEventListener("resize", () => {
    clearTimeout(rsz);
    rsz = setTimeout(() => { const b = $("#wave-chart"); if (W && Math.abs(b.clientWidth - (+b.dataset.w || 0)) > 20) renderWave(); }, 200);
  });

  function countCard(c, title, k) {
    const rows = c.targets.map((t) => `<tr class="${t.reached ? "done" : ""}"><td>${esc(t.name)}${t.reached ? "（已到）" : ""}</td><td class="r">${fmt(t.price)}</td></tr>`).join("");
    return `<div class="wave-card ${k}">
      <h3>${title}：${esc(c.now)}</h3>
      <div class="kind">${esc(c.pattern)}，轉折門檻 ${esc(c.threshold || "")} <span class="fit">符合度 <i><b style="width:${c.confidence}%"></b></i> ${c.confidence}</span></div>
      <p>${esc(c.text)}</p>
      <table><tbody>${rows}<tr class="inv"><td>失效價（${esc(c.invalid.note)}）</td><td class="r">${fmt(c.invalid.price)}</td></tr></tbody></table>
    </div>`;
  }

  function waveSvg(R) {
    const W_ = Math.max(300, Math.round($("#wave-chart").clientWidth - 8)), H_ = W_ < 640 ? 320 : 420;
    const L = 6, Rm = W_ < 640 ? 58 : 74, T = 14, B = 24;
    const bars = R.bars, n = bars.length;
    const idx = new Map(bars.map((b, i) => [b[0], i]));
    const prim = R.counts[0], alt = R.counts[1];
    let lo = Math.min(...bars.map((b) => b[3])), hi = Math.max(...bars.map((b) => b[2]));
    const span0 = hi - lo;
    const lines = [];
    if (prim) {
      prim.targets.filter((t) => !t.reached).slice(0, 3).forEach((t) => lines.push({ v: t.price, cls: "tgt", txt: fmt(t.price) }));
      lines.push({ v: prim.invalid.price, cls: "inv", txt: "失效 " + fmt(prim.invalid.price) });
    }
    lines.forEach((l) => { if (l.v > hi && l.v < hi + span0 * 0.6) hi = l.v; if (l.v < lo && l.v > lo - span0 * 0.6) lo = l.v; });
    const pad = (hi - lo) * 0.05; hi += pad; lo -= pad;
    const cw = (W_ - L - Rm) / n;
    const X = (i) => L + cw * (i + 0.5);
    const Y = (v) => T + (hi - v) / (hi - lo) * (H_ - T - B);
    let s = `<svg viewBox="0 0 ${W_} ${H_}" xmlns="http://www.w3.org/2000/svg">`;
    // 格線與座標
    const step = niceStep((hi - lo) / 6);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      s += `<line class="grid-l" x1="${L}" x2="${W_ - Rm}" y1="${Y(v)}" y2="${Y(v)}"/><text class="ax" x="${W_ - Rm + 6}" y="${Y(v) + 4}">${fmt(v)}</text>`;
    }
    let lastLab = "", lx = -999;
    bars.forEach((b, i) => {
      const lab = R.tf === "D" ? b[0].slice(0, 7).replace("-", "/") : b[0].slice(5, 10).replace("-", "/");
      if (lab !== lastLab && i > 2) {
        if (X(i) - lx > 70) { s += `<text class="ax" x="${X(i)}" y="${H_ - 8}" text-anchor="middle">${lab}</text>`; lx = X(i); }
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
    // 目標價與失效價
    lines.forEach((l) => {
      if (l.v < lo || l.v > hi) return;
      s += `<line class="${l.cls}" x1="${L}" x2="${W_ - Rm}" y1="${Y(l.v)}" y2="${Y(l.v)}"/><text class="${l.cls}-t" x="${W_ - Rm - 4}" y="${Y(l.v) - 4}" text-anchor="end">${l.txt}</text>`;
    });
    // 替代推算、主要推算
    const path = (c, cls) => {
      const pts = c.points.filter((p) => idx.has(p[0]));
      if (pts.length > 1) s += `<polyline class="cnt ${cls}" points="${pts.map((p) => `${X(idx.get(p[0]))},${Y(p[1])}`).join(" ")}"/>`;
    };
    if (alt) path(alt, "alt");
    if (prim) {
      path(prim, "");
      const pts = prim.points;
      pts.forEach((p, j) => {
        if (!idx.has(p[0]) || p[2] === "0") return;
        const isHigh = j > 0 ? p[1] > pts[j - 1][1] : p[1] > pts[1][1];
        const y = Y(p[1]) + (isHigh ? -14 : 14);
        s += `<g class="lbl"><circle cx="${X(idx.get(p[0]))}" cy="${y}" r="${W_ < 640 ? 8 : 9}"/><text x="${X(idx.get(p[0]))}" y="${y}">${esc(p[2])}</text></g>`;
      });
    }
    // 最新價
    const lc = R.last.c;
    s += `<rect class="now-b" x="${W_ - Rm + 2}" y="${Y(lc) - 9}" width="${Rm - 4}" height="18" rx="3"/><text class="now-t" x="${W_ - Rm + 6}" y="${Y(lc) + 4}">${fmt(lc)}</text>`;
    return s + "</svg>";
  }

  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const m = raw / p;
    return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * p;
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
  const renderers = { waveTf: () => renderWave(), pickMarket: renderPicks, histMarket: renderHistory, dtSide: renderDaytrade, volMarket: renderVolume, turnMarket: renderTurnover };
  document.querySelectorAll(".seg-ctl").forEach((g) => {
    g.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b || (g.dataset.group === "waveTf" ? !W : !D)) return;
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
