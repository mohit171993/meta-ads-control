const http=require('http');
const PORT=Number(process.env.PORT)||3000;
const BASE='https://app.affiliar.co/api/affiliate-api/v1';
const KEY=process.env.AFFILIAR_API_KEY;
const headers={Authorization:'Bearer '+KEY,Accept:'application/json'};

async function api(path){
  const r=await fetch(BASE+path,{headers});
  const text=await r.text();
  if(!r.ok) throw new Error(path+' '+r.status+' '+text.slice(0,160));
  return JSON.parse(text);
}
const norm=s=>String(s||'').toLowerCase().replace(/[^a-z0-9]/g,'');
function get(obj,names,def=0){
  if(!obj||typeof obj!=='object') return def;
  const wanted=new Set(names.map(norm));
  for(const [k,v] of Object.entries(obj)) if(wanted.has(norm(k))) return v;
  return def;
}
function findRows(obj){
  if(Array.isArray(obj)) return obj;
  if(obj&&typeof obj==='object'){
    for(const v of Object.values(obj)){
      const r=findRows(v);
      if(r.length) return r;
    }
  }
  return [];
}
const num=v=>Number(v)||0;
const html=`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0b1020"><title>Affiliar Performance</title><style>
body{margin:0;font-family:system-ui;background:#0b1020;color:#fff}main{max-width:900px;margin:auto;padding:18px}h1{margin:6px 0}.sub{color:#9ca3af}.filters{display:flex;gap:8px;overflow:auto;margin:16px 0}.filters button{padding:10px 14px;border:0;border-radius:10px;background:#1f2937;color:#fff}.filters .active{background:#fff;color:#111827}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.card,.panel{background:#161d2c;border:1px solid #313a4c;border-radius:16px;padding:14px}.label{font-size:11px;color:#9ca3af;text-transform:uppercase}.value{font-size:24px;font-weight:800;margin-top:6px}.panel{margin-top:12px}.row{padding:12px 0;border-top:1px solid #293244}.row:first-child{border-top:0}.head{display:flex;justify-content:space-between;font-weight:700}.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;margin-top:8px;font-size:11px;color:#9ca3af}.metrics b{display:block;color:#fff}.error{color:#fecaca}@media(min-width:700px){.grid{grid-template-columns:repeat(6,1fr)}}</style></head><body><main><h1>Affiliar Performance</h1><div class="sub">Live affiliate dashboard</div><div class="filters"><button data-days="0">Today</button><button class="active" data-days="7">7D</button><button data-days="30">30D</button><button data-days="90">90D</button><button id="refresh">Refresh</button></div><section class="grid" id="cards"></section><section class="panel"><h3>Campaign Performance</h3><div id="rows">Loading...</div></section></main><script>
let days=7;
const fmt=n=>new Intl.NumberFormat().format(+n||0);
const money=(n,c)=>new Intl.NumberFormat(undefined,{style:'currency',currency:c||'EUR'}).format(+n||0);
async function load(){
  const to=new Date(),from=new Date();
  if(days===0) from.setHours(0,0,0,0); else from.setDate(from.getDate()-days+1);
  const q='?from='+from.toISOString().slice(0,10)+'&to='+to.toISOString().slice(0,10);
  const res=await fetch('/api/dashboard'+q,{cache:'no-store'});
  const j=await res.json();
  if(!res.ok){rows.innerHTML='<div class="error">'+(j.error||'Error')+'</div>';return}
  const s=j.summary,c=j.currency;
  cards.innerHTML=[['Registrations',fmt(s.registrations)],['FTDs',fmt(s.ftds)],['Deposits',fmt(s.deposits)],['Players',fmt(s.players)],['NGR',money(s.ngr,c)],['Commission',money(s.commission,c)]].map(x=>'<div class="card"><div class="label">'+x[0]+'</div><div class="value">'+x[1]+'</div></div>').join('');
  rows.innerHTML=(j.campaigns||[]).map(x=>'<div class="row"><div class="head"><span>'+x.name+'</span><span>'+money(x.ngr,c)+'</span></div><div class="metrics"><div>REG<b>'+fmt(x.registrations)+'</b></div><div>FTD<b>'+fmt(x.ftds)+'</b></div><div>DEP<b>'+fmt(x.deposits)+'</b></div><div>PLAYERS<b>'+fmt(x.players)+'</b></div><div>NGR<b>'+money(x.ngr,c)+'</b></div></div></div>').join('')||'No campaign data';
}
document.querySelectorAll('[data-days]').forEach(b=>b.onclick=()=>{days=+b.dataset.days;document.querySelectorAll('[data-days]').forEach(x=>x.classList.remove('active'));b.classList.add('active');load()});
refresh.onclick=load;load();
</script></body></html>`;

http.createServer(async(req,res)=>{
  const u=new URL(req.url,'http://localhost');
  if(u.pathname==='/health'){
    res.writeHead(200,{'content-type':'application/json'});
    return res.end(JSON.stringify({ok:true,version:'github-v1'}));
  }
  if(u.pathname==='/api/dashboard'){
    try{
      const from=u.searchParams.get('from'),to=u.searchParams.get('to');
      const [campaignRaw,commissionRaw,overviewRaw]=await Promise.all([
        api('/campaign-reports?from='+encodeURIComponent(from||'')+'&to='+encodeURIComponent(to||'')),
        api('/commission'),
        api('/overview')
      ]);
      const campaigns=findRows(campaignRaw).filter(r=>r&&typeof r==='object'&&!Array.isArray(r)).map((r,i)=>({
        name:String(get(r,['campaign','campaign_name','campaignName','name','brand','referral_code','referralCode'],'Campaign '+(i+1))),
        registrations:num(get(r,['registrations','registration_count','registrationCount','regs','signups'])),
        ftds:num(get(r,['ftds','ftd','ftd_count','ftdCount','first_time_depositors','firstTimeDepositors'])),
        deposits:num(get(r,['deposits','deposit_count','depositCount','depositors'])),
        ngr:num(get(r,['ngr','net_gaming_revenue','netGamingRevenue','net_revenue','revenue'])),
        players:num(get(r,['players','player_count','playerCount','active_players','activePlayers']))
      }));
      const sum=k=>campaigns.reduce((a,b)=>a+num(b[k]),0);
      const summary={
        registrations:sum('registrations'),
        ftds:sum('ftds'),
        deposits:sum('deposits'),
        players:sum('players'),
        ngr:sum('ngr'),
        commission:num(get(commissionRaw,['commission','commission_amount','commissionAmount','total_commission','earnings']))
      };
      const currency=String(get(campaignRaw,['currency','currency_code','currencyCode'],get(overviewRaw,['currency','currency_code','currencyCode'],'EUR')));
      res.writeHead(200,{'content-type':'application/json','cache-control':'no-store'});
      return res.end(JSON.stringify({currency,summary,campaigns}));
    }catch(e){
      res.writeHead(502,{'content-type':'application/json','cache-control':'no-store'});
      return res.end(JSON.stringify({error:String(e.message||e)}));
    }
  }
  res.writeHead(200,{'content-type':'text/html; charset=utf-8','cache-control':'no-store, no-cache, must-revalidate'});
  res.end(html);
}).listen(PORT,'0.0.0.0',()=>console.log('AFFILIAR_TRACKER_READY',PORT));