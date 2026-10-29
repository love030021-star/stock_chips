/* Private same-origin broker feed. No account, credential or order interfaces. */
(() => {
  'use strict';
  const FEED_BASE=String(window.CAPITAL_FEED_BASE||'').replace(/\/$/,'');
  const panel=document.createElement('section');
  panel.className='block capital-panel';
  panel.innerHTML='<h2>群益公開行情 <small>站長行情服務</small></h2><p class="sub">群益 SDK → 本機資料服務 → 本網站。價格更新不代表波浪、KD 或布局已重新計算。</p><div class="capital-status" role="status">正在確認資料服務…</div><div class="capital-cards"></div><p class="sub">可下載已發布的5／30／60分K；歷史範圍依來源可提供的資料。</p><div class="capital-tools"><label>匯出商品 <select class="capital-market"><option value="SRF">小型0050</option><option value="TX">台指期</option></select></label><label>週期 <select class="capital-tf"><option selected>5</option><option>30</option><option>60</option></select> 分鐘</label><button type="button" class="capital-export">下載已收集 K 線 JSON</button></div><p class="capital-result sub" role="status"></p>';
  const main=document.querySelector('main');
  if(!main) return;
  main.insertBefore(panel,main.firstChild);
  const status=panel.querySelector('.capital-status'),cards=panel.querySelector('.capital-cards');
  const escaped=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  if(!FEED_BASE){panel.querySelector('.capital-tools').hidden=true;panel.querySelectorAll('p.sub')[1].hidden=true;}
  let busy=false;
  const names={TX:'台指期',SRF:'小型0050期貨',TWII:'加權指數',TPEX:'櫃買指數','00631L':'台灣50正2',MNQ:'微型那斯達克',YM:'小道瓊',GOLD:'1盎司黃金',BRENT:'布蘭特原油',JP225:'日經225（美元NKD）'};
  async function poll(){
    if(busy||document.hidden)return;
    busy=true;
    try{
      if(!FEED_BASE){status.textContent='群益公開行情尚未啟用。下方分析與籌碼維持原有排程資料，以標示日期為準。';return;}
      const response=await fetch(`${FEED_BASE}/api/market-data`,{cache:'no-store',signal:AbortSignal.timeout(6000)});
      if(!response.ok)throw Error('HTTP '+response.status);
      const data=await response.json();
      if(!data||typeof data.connected!=='boolean'||!data.quotes||!data.contracts)throw Error('行情資料格式不符');
      status.textContent=data.connected?(data.history_loading?'群益行情已連線，正在回補歷史K線；完成後啟動波浪／KD／123重算。':'群益國內行情已連線；各市場是否可用，以商品報價及權限回覆為準。'):data.error||'尚未連線。等待站長啟用公開行情。';
      panel.classList.toggle('capital-connected',data.connected);
      cards.innerHTML=[...new Set(['TX','SRF',...Object.keys(data.contracts)])].map(id=>{
        const q=data.quotes[id],contract=data.contracts[id];
        if(!q)return `<article><span>${escaped(names[id]||contract?.name||id)}</span><strong>等待資料</strong><small>等待站長發布此商品</small></article>`;
        const price=Number(q.last_tick_price||q.price).toLocaleString('zh-TW',{maximumFractionDigits:2});
        const overseas=q.kind==='overseas',connected=overseas?data.sources?.overseas?.connected:data.connected;
        const tickTime=overseas?`來源 ${q.source_date||q.trading_day} ${q.source_time||''}（時區待核對）`:(q.last_tick_at||q.source_updated_at)?new Date((q.last_tick_at||q.source_updated_at)*1000).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',hour12:false}):'尚未收到逐筆成交';
        return `<article><span>${escaped(names[id]||contract?.name||id)} · ${escaped(q.product)}</span><strong>${price}</strong><small>${!connected?'已離線／最後快照':overseas?`群益報價，接收距今 ${q.receipt_age_seconds} 秒`:q.fresh?(q.kind==='index'?'近期指數更新':'近期實際成交'):'最新快照／非近期成交'}${q.simulated?' · 試撮':''}</small><small>買賣價快照 ${escaped(q.bid)} ／ ${escaped(q.ask)}</small><small>${overseas?'來源時間':q.kind==='index'?'指數更新':'最後成交'}：${escaped(tickTime)}</small><small>${contract?.kind==='equity'||contract?.kind==='index'?'證券／指數':`合約最後交易日：${escaped(contract?.expiry||contract?.last_trade_date||'待確認')}`}</small></article>`;
      }).join('');
      window.dispatchEvent(new CustomEvent('capital-feed',{detail:data}));
      panel.querySelector('.capital-source-status').textContent=Object.entries(data.sources||{}).filter(([,s])=>s.error).map(([id,s])=>`${names[id]||id}：${s.error}`).join('；');
    }catch(error){
      status.textContent='公開行情服務未連線：'+error.message;
      panel.classList.remove('capital-connected');
      cards.querySelectorAll('article small:first-of-type').forEach(label=>label.textContent='服務中斷／上次快照');
      window.dispatchEvent(new CustomEvent('capital-feed',{detail:{connected:false,quotes:{}}}));
    }finally{busy=false;}
  }
  panel.querySelector('.capital-export').onclick=async()=>{
    const out=panel.querySelector('.capital-result');
    try{
      const market=panel.querySelector('.capital-market').value,tf=panel.querySelector('.capital-tf').value;
      if(!FEED_BASE)throw Error('公開行情服務尚未啟用');
      const response=await fetch(`${FEED_BASE}/api/bars?market=${encodeURIComponent(market)}&tf=${tf}&limit=500`,{cache:'no-store'});
      const data=await response.json();
      if(!response.ok)throw Error(data.error);
      if(!data.bars.length){out.textContent='尚無已收集 K 線。連接行情後，需等候逐筆成交或回補資料。';return;}
      const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
      const a=document.createElement('a');a.href=url;a.download=`${data.product}-${tf}min.json`;a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
      out.textContent=`已匯出 ${data.bars.length} 根 K 線。僅含已收K，歷史範圍可能不完整。`;
    }catch(error){out.textContent=error.message;}
  };
  const paragraph=panel.querySelector('p.sub');
  paragraph.textContent=FEED_BASE?'群益行情公開快照，每2秒讀取。顯示來源時間與離線狀態；波浪、KD、123只使用已發布的已收K。':'群益串流入口已備妥，尚未接入公開報價。現有網站資料繼續依原本來源與排程更新。';
  const sources=document.createElement('details');sources.className='capital-sources';
  sources.innerHTML='<summary>行情來源與更新方式</summary><table><tbody><tr><td>群益報價</td><td>僅限站長已設定可公開的商品。上傳端每2秒快照，超過10秒沒有來源更新即標示離線。</td></tr><tr><td>個股／ETF</td><td>輸入代碼查詢已發布資料；尚未接入就顯示等待，不以歷史價格補值。</td></tr><tr><td>波浪／KD／123</td><td>同商品已收K推算；新K線可能改變數浪。未發布的週期不產生訊號。</td></tr><tr><td>海期</td><td>依商品規格、來源時間與可公開範圍個別接入，K線未驗證時不顯示分析。</td></tr><tr><td>比特幣</td><td>原有Kraken來源，與群益分開標示；尚未接分析K線。</td></tr><tr><td>法人／大額交易人／選擇權籌碼</td><td>官方每日資料，保留公布日期；每5分鐘查詢本站最新公布版本。</td></tr></tbody></table><p class="capital-source-status sub" role="status"></p>';
  panel.append(sources);
  poll();setInterval(poll,2000);
})();
