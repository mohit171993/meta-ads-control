let range="7";
const cards=document.querySelector("#cards"), campaigns=document.querySelector("#campaigns"), mode=document.querySelector("#mode"), updated=document.querySelector("#updated");
const fmt=(n,currency)=>new Intl.NumberFormat(undefined,{style:"currency",currency,maximumFractionDigits:2}).format(Number(n||0));
const num=n=>new Intl.NumberFormat().format(Number(n||0));

function dates(){
  const to=new Date(), from=new Date();
  if(range==="today") from.setHours(0,0,0,0); else from.setDate(from.getDate()-Number(range)+1);
  const d=x=>x.toISOString().slice(0,10);
  return {from:d(from),to:d(to)};
}
function render(data){
  const c=data.currency||"EUR", s=data.summary||{};
  mode.textContent=(data.mode||"live").toUpperCase();
  mode.className="badge "+(data.mode==="demo"?"demo":"live");
  cards.innerHTML=[
    ["Registrations",num(s.registrations)],
    ["FTDs",num(s.ftds)],
    ["Deposits",num(s.deposits)],
    ["NGR",fmt(s.ngr,c)],
    ["Commission",fmt(s.commission,c)]
  ].map(([l,v])=>`<article class="card"><div class="label">${l}</div><div class="value">${v}</div></article>`).join("");

  const rows=data.campaigns||[];
  campaigns.innerHTML=rows.length?rows.map(r=>`
    <div class="campaign">
      <div class="campaign-head"><span>${r.name}</span><span>${fmt(r.commission,c)}</span></div>
      <div class="metric-row">
        <div class="metric">REG<b>${num(r.registrations)}</b></div>
        <div class="metric">FTD<b>${num(r.ftds)}</b></div>
        <div class="metric">DEP<b>${num(r.deposits)}</b></div>
        <div class="metric">NGR<b>${fmt(r.ngr,c)}</b></div>
      </div>
    </div>`).join(""):'<p>No campaign rows returned.</p>';
  updated.textContent="Updated "+new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});
  document.querySelector("#apiHelp").textContent=data.mode==="demo"
    ?"Demo mode: add AFFILIAR_API_URL and AFFILIAR_API_KEY in Railway to enable live reporting."
    :"Live data is being proxied securely through Railway.";
}
async function load(){
  const {from,to}=dates(); updated.textContent="Loading…";
  try{
    const r=await fetch(`/api/performance?from=${from}&to=${to}`,{cache:"no-store"});
    const data=await r.json();
    if(!r.ok) throw new Error(data.detail||data.error||"Request failed");
    render(data);
  }catch(e){ campaigns.innerHTML=`<div class="error">${e.message}</div>`; updated.textContent="Failed"; }
}
document.querySelectorAll("[data-range]").forEach(b=>b.addEventListener("click",()=>{range=b.dataset.range;document.querySelectorAll("[data-range]").forEach(x=>x.classList.remove("active"));b.classList.add("active");load()}));
document.querySelector("#refresh").addEventListener("click",load);
if("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
load();
