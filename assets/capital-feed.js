/* Passive quote transport. Quotes render in the existing market/analysis UI. */
(() => {
  'use strict';
  const base=String(window.CAPITAL_FEED_BASE||'').replace(/\/$/,'');
  if(!base)return;
  let busy=false;
  async function poll(){
    if(busy||document.hidden)return;
    busy=true;
    try{
      const response=await fetch(`${base}/api/market-data`,{cache:'no-store',signal:AbortSignal.timeout(6000)});
      if(!response.ok)throw Error('HTTP '+response.status);
      const data=await response.json();
      if(typeof data?.connected!=='boolean'||!data.quotes||!data.contracts)throw Error('行情格式不符');
      window.dispatchEvent(new CustomEvent('capital-feed',{detail:data}));
    }catch(error){
      window.dispatchEvent(new CustomEvent('capital-feed',{detail:{connected:false,quotes:{},contracts:{},error:'即時行情未連線'}}));
    }finally{busy=false;}
  }
  poll();setInterval(poll,2000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
})();
