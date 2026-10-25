(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const MK = { twse: "上市", tpex: "上櫃", all: "全部" };
  const state = { stTf: "D", waveTf: "D", pickMarket: "twse", histMarket: "twse", dtSide: "long", volMarket: "twse", turnMarket: "twse" };
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
    fetch(`data/chips.json?v=${Date.now().toString(36)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
      .then((c) => renderChips(c));
    fetch(`data/strategy.json?v=${Date.now().toString(36)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
      .then((x) => renderStrategy(x));
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




  // ---------- 波段逆勢布局 ----------
  let SP = null;
  function renderStrategy(S) {
    if (!S) { $("#st-meta").textContent = "策略資料還沒產生，下一次每日更新後會出現。"; return; }
    SP = S;
    const P = S.plans && S.plans[state.stTf];
    if (!P) { $("#st-meta").textContent = "這個週期的資料還不夠。"; $("#st-trend").innerHTML = ""; $("#st-batches").innerHTML = ""; $("#st-calc").innerHTML = ""; $("#st-signal").innerHTML = ""; $("#st-abc").innerHTML = ""; return; }
    const up = P.dir === 1, M = S.plans.D;
    $("#st-meta").textContent = `${P.tf_label}資料到 ${P.data_t.replaceAll("-", "/")}（收 ${fmt(P.data_close)}），5 分 K 最新 ${(S.last_t || "").replaceAll("-", "/")}（${fmt(S.last)}）。`;
    const pts = P.points.map((p) => `${p[2] === "0" ? "起點" : "第 " + p[2] + " 浪"} ${p[0].slice(5).replace("-", "/")} ${fmt(p[1])}`).join(" → ");
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
      $("#st-calc").innerHTML = ""; $("#st-signal").innerHTML = ""; $("#st-abc").innerHTML = ""; return;
    }
    $("#st-zone-title").textContent = P.phase === "correct" ? `${up ? "回檔" : "反彈"}區間分批：${fmt(P.zone[0])}–${fmt(P.zone[1])}` : `預估下一個${up ? "回檔" : "反彈"}區`;
    $("#st-batches").innerHTML = P.batches.map((b, i) => `<tr><td>第 ${i + 1} 批</td><td class="r num"><b>${fmt(b.price)}</b></td><td>${esc(b.label)}</td>
      <td class="r"><input class="lots" type="number" min="0" step="1" value="0" data-i="${i}" aria-label="第 ${i + 1} 批口數"></td></tr>`).join("");
    document.querySelectorAll("#st-batches .lots").forEach((el) => el.addEventListener("input", calcCost));
    // 記住使用者上次的設定（只存在這台瀏覽器）
    try {
      const saved = JSON.parse(localStorage.getItem("st-pref") || "{}");
      if (saved.total) $("#st-total").value = saved.total;
      if (saved.mode) $("#st-mode").value = saved.mode;
      if (saved.prod) $("#st-prod").value = saved.prod;
    } catch (e) { /* 無法讀取就用預設值 */ }
    $("#st-total").oninput = allocate;
    $("#st-mode").onchange = allocate;
    $("#st-prod").onchange = () => { savePref(); calcCost(); };
    allocate();
    const G = P.signal;
    $("#st-signal").innerHTML = `<div class="st-head"><h3>5 分 K 進場訊號</h3><span class="st-dir ${G && G.ok ? (up ? "up" : "down") : ""}">${G ? (G.ok ? "可進場" : "等待") : "—"}</span></div>
      <p>${esc(G ? G.text : "沒有 5 分 K 資料。")}</p>`;
    $("#st-abc").innerHTML = `<div class="st-head"><h3>修正浪細部（ABC）</h3></div>` + (P.abc.length ? P.abc.map((a) => {
      const [o, A, B] = a.points;
      return `<p>${esc(a.tf)}：A 浪 ${fmt(o[1])} → ${fmt(A[1])}，B 浪反${up ? "彈" : "落"}到 ${fmt(B[1])}，C 浪進行中。</p>
        <table><tbody>${a.targets.map((t) => {
          const inZone = P.zone && t.price >= P.zone[0] && t.price <= P.zone[1];
          return `<tr><td>C 浪：${esc(t.name)}${inZone ? "<small class='hit'>在回檔區內</small>" : ""}</td><td class="r">${fmt(t.price)}</td></tr>`;
        }).join("")}</tbody></table>`;
    }).join("") : `<p class="sub">較小週期目前沒有進行中的 C 浪可對照。</p>`);
  }

  function savePref() {
    try {
      localStorage.setItem("st-pref", JSON.stringify({ total: +$("#st-total").value || 0, mode: $("#st-mode").value, prod: $("#st-prod").value }));
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
    const P = SP.plans[state.stTf], up = P.dir === 1, prod = $("#st-prod").value, mult = SP.pt[prod];
    const lots = [...document.querySelectorAll("#st-batches .lots")].map((el) => Math.max(0, +el.value || 0));
    const n = lots.reduce((a, b) => a + b, 0);
    const total = Math.floor(+$("#st-total").value || 0);
    if (!n) { $("#st-calc").innerHTML = `<p class="sub">先輸入可下單總口數，系統會分配到各批並計算平均成本；各批口數也可以再自行調整。</p>`; return; }
    const over = total && n !== total ? `<p class="st-exit warn">各批合計 ${n} 口，與可下單總口數 ${total} 口不同。</p>` : "";
    const avg = P.batches.reduce((a, b, i) => a + b.price * lots[i], 0) / n;
    const d = P.dir;
    const loss = (avg - P.stop) * d * n * mult;
    const g1 = (P.t1 - avg) * d * n * mult, g2 = (P.t2 - avg) * d * n * mult;
    const rr = loss > 0 ? g1 / loss : 0;
    let cum = 0, rows = "";
    P.batches.forEach((b, i) => {
      if (!lots[i]) return;
      cum += lots[i];
      const a = P.batches.slice(0, i + 1).reduce((s, x, j) => s + x.price * lots[j], 0) / cum;
      rows += `<tr><td>成交到第 ${i + 1} 批</td><td class="r">${cum} 口，均價 ${fmt(a)}</td></tr>`;
    });
    $("#st-calc").innerHTML = `<table><tbody>${rows}
      <tr><td><b>全部成交平均成本</b></td><td class="r"><b>${fmt(avg)}</b></td></tr>
      <tr><td>停損（${esc(P.stop_src)}）</td><td class="r">${fmt(P.stop)}，虧損 <span class="down">${fmt(loss)}</span> 元</td></tr>
      <tr><td>目標 1（${esc(P.t1_src)}）</td><td class="r">${fmt(P.t1)}，獲利 <span class="up">${fmt(g1)}</span> 元</td></tr>
      <tr><td>目標 2（${esc(P.t2_src)}）</td><td class="r">${fmt(P.t2)}，獲利 <span class="up">${fmt(g2)}</span> 元</td></tr>
      <tr><td>風險報酬比（目標 1）</td><td class="r">${rr.toFixed(2)}</td></tr>
      </tbody></table>${over}${P.phase === "trend" ? `<p class="st-exit warn">推動浪還在進行，以上是預估價位，等推動浪結束後會依實際高低點重算。</p>` : ""}<p class="st-exit">${prod}每點 ${mult} 元。部分批次沒成交時，以上面「成交到第 N 批」的均價為準。</p>`;
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
    $("#chip-meta").textContent = `資料日期 ${md(C.date)}，日變化與 ${md(C.prev)} 比較。單位：口。`;
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
    const len = (i) => Math.abs(p[i][1] - p[i - 1][1]);
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
      add("note-m", `台指期近月 ${R.name}，${R.last.t.replaceAll("-", "/")} 收 ${fmt(R.last.c)}`, f2, lh);
      add("note-h", `主要推算：${prim.now}（${prim.pattern}）`, f1, f1 + 8);
      add("note-t", `${fmt(pts[0][1])} → ${fmt(pts[pts.length - 1][1])} 已走完 ${seq}，各段比例標在線旁。`, f2, lh);
      if (next) add("note-g", `推演：往 ${fmt(next.price)}（${next.name}）前進，見右側虛線箭頭。`, f2, lh);
      add("note-t", `${prim.invalid.note} ${fmt(prim.invalid.price)} 此推算失效（灰色點線）。`, f2, lh);
      if (alt) add("note-m", `替代推算：${alt.now}（${alt.pattern}），失效 ${fmt(alt.invalid.price)}，圖上藍色虛線。`, f2, lh);
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
        lines.push({ v: t.price, cls: "tgt", txt: `${t.name}　${fmt(t.price)}` }));
      lines.push({ v: prim.invalid.price, cls: "inv", txt: `失效 ${fmt(prim.invalid.price)}` });
    }
    lines.forEach((l) => { if (l.v > hi && l.v < hi + span0 * 0.6) hi = l.v; if (l.v < lo && l.v > lo - span0 * 0.6) lo = l.v; });
    const pad = (hi - lo) * 0.06; hi += pad; lo -= pad;
    const plotR = W_ - Rm;
    const cw = (plotR - L) / (n + fs);
    const X = (i) => L + cw * (i + 0.5);
    const Y = (v) => T + (hi - v) / (hi - lo) * (H_ - T - B);
    const V = (y) => hi - (y - T) / (H_ - T - B) * (hi - lo);
    const xl = X(n - 1);
    let s = `<svg viewBox="0 0 ${W_} ${H_}" xmlns="http://www.w3.org/2000/svg">
      <defs><marker id="arw" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="var(--gold)"/></marker></defs>`;
    // 推演區底色
    s += `<rect class="fut" x="${xl + cw / 2}" y="${T}" width="${plotR - xl - cw / 2}" height="${H_ - T - B}"/>`;
    s += `<text class="fut-t" x="${(xl + plotR) / 2}" y="${H_ - B - 6}" text-anchor="middle">推演</text>`;
    // 格線與座標
    const step = niceStep((hi - lo) / 6);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      s += `<line class="grid-l" x1="${L}" x2="${plotR}" y1="${Y(v)}" y2="${Y(v)}"/><text class="ax" x="${plotR + 6}" y="${Y(v) + 4}">${fmt(v)}</text>`;
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
    s += `<rect class="now-b" x="${plotR + 2}" y="${Y(lc) - 9}" width="${Rm - 4}" height="18" rx="3"/><text class="now-t" x="${plotR + 6}" y="${Y(lc) + 4}">${fmt(lc)}</text>`;
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
  const renderers = { stTf: () => renderStrategy(SP), waveTf: () => renderWave(), pickMarket: renderPicks, histMarket: renderHistory, dtSide: renderDaytrade, volMarket: renderVolume, turnMarket: renderTurnover };
  document.querySelectorAll(".seg-ctl").forEach((g) => {
    g.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      const need = { waveTf: W, stTf: SP }[g.dataset.group];
      if (!b || (g.dataset.group in { waveTf: 1, stTf: 1 } ? !need : !D)) return;
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
