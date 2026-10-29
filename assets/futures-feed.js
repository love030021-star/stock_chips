/* Resolve only exact, concrete futures contracts. No account or order data. */
((target)=>{
  const validMonth=m=>/^20\d{2}(0[1-9]|1[0-2])$/.test(String(m||''));
  function match(feed,root,month){
    if(!['TX','SRF'].includes(root)||!validMonth(month))return null;
    for(const key of [`${root}:${month}`,root]){
      const contract=feed?.contracts?.[key],quote=feed?.quotes?.[key];
      const expiry=String(contract?.expiry||contract?.last_trade_date||'').replace(/[^0-9]/g,'');
      const code=String(contract?.order_code||'');
      const family=String(contract?.exchange_code||contract?.exchange||'').toUpperCase();
      if(expiry.length!==8||expiry.slice(0,6)!==month||!code||code.endsWith('00')||code.includes('/')||quote?.product!==code)continue;
      if(!family.startsWith(root==='TX'?'TXF':'SRF'))continue;
      return {key,contract,quote};
    }
    return null;
  }
  target.FuturesFeed={validMonth,match,key:(feed,root,month)=>match(feed,root,month)?.key||`${root}:${month}`};
})(typeof module!=='undefined'?module.exports:window);
