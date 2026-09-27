import express from "express";
const app = express();
app.use(express.json());
app.use(express.static("public"));

const demo = {
  mode: "demo",
  currency: "EUR",
  summary: { registrations: 1284, ftds: 236, deposits: 418, ngr: 18420.55, commission: 5526.17 },
  campaigns: [
    { name: "Sports - Mobile", registrations: 522, ftds: 104, deposits: 183, ngr: 8120.30, commission: 2436.09 },
    { name: "Casino - Search", registrations: 431, ftds: 78, deposits: 139, ngr: 6035.20, commission: 1810.56 },
    { name: "Retargeting", registrations: 331, ftds: 54, deposits: 96, ngr: 4265.05, commission: 1279.52 }
  ]
};

function pick(obj, keys, fallback=0) {
  for (const k of keys) if (obj && obj[k] !== undefined && obj[k] !== null) return obj[k];
  return fallback;
}
function normalize(raw) {
  const root = raw?.data ?? raw?.result ?? raw ?? {};
  const rows = root.campaigns ?? root.rows ?? root.items ?? root.records ?? [];
  const summary = root.summary ?? root.totals ?? root;
  return {
    mode: "live",
    currency: pick(root, ["currency","currency_code"], "EUR"),
    summary: {
      registrations: Number(pick(summary, ["registrations","registration_count","signups","leads"], 0)),
      ftds: Number(pick(summary, ["ftds","ftd","first_time_depositors","first_depositors"], 0)),
      deposits: Number(pick(summary, ["deposits","deposit_count","depositors"], 0)),
      ngr: Number(pick(summary, ["ngr","net_gaming_revenue","revenue"], 0)),
      commission: Number(pick(summary, ["commission","affiliate_commission","earnings","payout"], 0))
    },
    campaigns: Array.isArray(rows) ? rows.map((r,i)=>({
      name: String(pick(r, ["campaign","campaign_name","name","brand","referral_code"], "Campaign "+(i+1))),
      registrations: Number(pick(r, ["registrations","registration_count","signups","leads"], 0)),
      ftds: Number(pick(r, ["ftds","ftd","first_time_depositors","first_depositors"], 0)),
      deposits: Number(pick(r, ["deposits","deposit_count","depositors"], 0)),
      ngr: Number(pick(r, ["ngr","net_gaming_revenue","revenue"], 0)),
      commission: Number(pick(r, ["commission","affiliate_commission","earnings","payout"], 0))
    })) : []
  };
}

app.get("/health", (_req,res)=>res.json({ok:true}));
app.get("/api/performance", async (req,res)=>{
  const url = process.env.AFFILIAR_API_URL;
  const key = process.env.AFFILIAR_API_KEY;
  if (!url || !key) return res.json(demo);

  try {
    const u = new URL(url);
    if (req.query.from) u.searchParams.set("from", String(req.query.from));
    if (req.query.to) u.searchParams.set("to", String(req.query.to));
    const header = process.env.AFFILIAR_AUTH_HEADER || "Authorization";
    const prefix = process.env.AFFILIAR_AUTH_PREFIX ?? "Bearer ";
    const headers = { Accept: "application/json", [header]: prefix + key };
    const r = await fetch(u, { headers });
    const text = await r.text();
    if (!r.ok) return res.status(502).json({error:"Affiliar API error",status:r.status,detail:text.slice(0,500)});
    let json;
    try { json = JSON.parse(text); } catch { return res.status(502).json({error:"Affiliar API returned non-JSON"}); }
    res.json(normalize(json));
  } catch (e) {
    res.status(502).json({error:"Unable to reach Affiliar API",detail:String(e?.message||e)});
  }
});

const port = process.env.PORT || 3000;
app.listen(port, "0.0.0.0", ()=>console.log("Affiliar Tracker listening on", port));
