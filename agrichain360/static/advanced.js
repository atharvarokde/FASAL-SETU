/* Fasal Setu Tech League Command Center */
(function(){
  if(typeof TABS!=='undefined' && !TABS.some(x=>x[0]==='intel')) TABS.splice(1,0,['intel','AI Command Center']);
  if(typeof V==='undefined') return;
  const esc=typeof E==='function'?E:(x)=>String(x??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const badge=typeof bd==='function'?bd:(x)=>`<span class="bd ${x}">${esc(x)}</span>`;
  const money=n=>Number(n||0).toLocaleString('en-IN',{maximumFractionDigits:1});
  async function intelligence(){
    const [o,q,a]=await Promise.all([api('/intelligence/overview'),api('/intelligence/dispatch-queue'),api('/intelligence/anomalies')]);
    const k=o.kpis, rd=o.risk_distribution, ca=o.critical_action;
    $('#view').innerHTML=`
      <div class="aihero"><div class="ai-pill">TECH LEAGUE • INTELLIGENCE MODE</div><h2 style="margin-top:8px">Fasal Setu Command Center</h2>
      <p class="lead">From sensor signal → anomaly → risk → recommendation → action → traceability.</p>
      <button class="btn" onclick="runTechDemo()">▶ Run 90-Second Demo</button>
      <button class="btn o" onclick="askAgri()">🤖 Ask AgriChain AI</button></div>
      <div class="ai-grid">
        <div class="c"><div class="kpi">${k.batches}</div><span class="m">Batches monitored</span></div>
        <div class="c"><div class="kpi">${money(k.quantity_kg)} kg</div><span class="m">Quantity monitored</span></div>
        <div class="c"><div class="kpi">${k.open_alerts}</div><span class="m">Open alerts</span></div>
        <div class="c"><div class="kpi">${k.high_risk}</div><span class="m">High / critical risk</span></div>
        <div class="c"><div class="kpi">${money(k.estimated_loss_prevented_kg)} kg</div><span class="m">Estimated loss prevented*</span></div>
      </div>
      <div class="g" style="margin-top:12px">
        <div class="c"><h3>AI Risk Overview</h3>${Object.entries(rd).map(([x,n])=>`<div style="margin:9px 0"><div style="display:flex;justify-content:space-between"><b>${x}</b><span>${n}</span></div><div class="progress"><i style="width:${k.batches?Math.round(n/k.batches*100):0}%"></i></div></div>`).join('')}<p class="m">*Modelled prototype metric, not measured field impact.</p></div>
        <div class="c critical"><h3>🚨 Critical Action</h3>${ca?`<h2>${esc(ca.batch.batch_code)}</h2><p><b>${esc(ca.batch.product_name)}</b> · ${ca.spoilage_probability}% spoilage probability</p><p>Estimated safe life: <b>${ca.estimated_safe_life_hours} h</b></p><p>${ca.contributors.map(x=>`<span class="ai-pill">${esc(x)}</span>`).join(' ')}</p><button class="btn" onclick="showRecommendation(${ca.batch.batch_id})">View AI Recommendation</button><button class="btn o" onclick="showPassport('${esc(ca.batch.batch_code)}')">Product Passport</button>`:'<p>No critical batch currently detected.</p>'}</div>
      </div>
      <div class="g">
        <div class="c"><h3>⚡ Live Anomalies</h3>${a.slice(0,6).map(x=>`<div class="nt"><b>${esc(x.type)}</b> ${badge(x.severity)}<br>${esc(x.message)}<div class="m">${esc(x.storage_code)} · ${dt(x.recorded_at)}</div></div>`).join('')||'<p class="m">No recent anomalies.</p>'}</div>
        <div class="c"><h3>🎯 Smart Dispatch Queue</h3>${q.slice(0,6).map(x=>`<div class="nt"><b>#${x.queue_position} ${esc(x.batch_code)}</b> ${badge(x.risk_level)}<br>${esc(x.product)} · ${x.remaining_safe_life_hours}h safe-life estimate<div class="m">${esc(x.reason)}</div></div>`).join('')||'<p class="m">No stored batches.</p>'}</div>
      </div>
      <div class="c"><h3>💬 Ask AgriChain AI</h3><div style="display:flex;gap:8px"><input id="aiQ" placeholder="Which batch should I dispatch first?"><button class="btn" onclick="askAgri()">Ask</button></div><div id="aiA" class="note" style="margin-top:10px">Rule-based AI fallback uses your live database and works without an API key.</div></div>`;
  }
  V.intel=intelligence;
  window.runTechDemo=async function(){try{const j=await api('/demo/run','POST',{});toast(j.message+': '+j.batch_code);tab='intel';await intelligence();setTimeout(()=>showRecommendation(j.risk.batch.batch_id),500)}catch(e){toast(e.message)}};
  window.showRecommendation=async function(id){try{const r=await api('/intelligence/recommendations/'+id);modal(`<h3>🤖 AI Recommendation</h3><p>${badge(r.priority)}</p><h3>${esc(r.action)}</h3><p class="m">Why: ${esc(r.reason)}</p><p><b>Action window:</b> ${r.deadline_minutes} minutes</p><p class="note">Expected benefit: ${esc(r.expected_benefit)}</p>`)}catch(e){toast(e.message)}};
  window.showPassport=async function(code){try{const p=await fetch('/api/passport/'+encodeURIComponent(code)).then(r=>r.json());modal(`<h3>🌱 Digital Product Passport</h3><div class="aihero"><h2>${esc(p.batch.batch_code)}</h2><p>${esc(p.batch.product_name)} · ${p.batch.quantity} ${esc(p.batch.unit)}</p><p>Harvest: ${esc(p.batch.harvest_date||'—')}</p><h2>${badge(p.verdict)} · Quality ${p.quality_score}/100</h2></div><div class="timeline">${p.events.map(e=>`<div><b>${esc(e.event_type)}</b> · ${esc(e.location||'')}</div>`).join('')}</div><button class="btn" onclick="window.open('/api/qr/${encodeURIComponent(code)}.svg','_blank')">Open QR</button>`)}catch(e){toast(e.message)}};
  window.askAgri=async function(){const q=$('#aiQ')?.value||'Which batch should I dispatch first?';try{const r=await api('/ai/assistant','POST',{question:q});$('#aiA').textContent=r.answer}catch(e){toast(e.message)}};
})();
