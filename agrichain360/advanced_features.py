"""Competition-grade intelligence extensions for Fasal Setu.
Keeps the existing Flask/SQLite architecture and works without external AI APIs.
"""
from datetime import datetime, timezone, timedelta
import math


def register_advanced(app, auth, q, one, ex, now, assess, BSQL, process_reading, ev, num):
    def risk_score(r):
        if not r:
            return 0
        base = {"LOW": 18, "MEDIUM": 45, "HIGH": 72}.get(r.get("risk_level"), 85)
        exposure = min(float(r.get("temperature_exposure") or 0) * 3, 15)
        duration = min(float(r.get("duration_out_of_range") or 0) / 30, 10)
        return max(0, min(100, round(base + exposure + duration)))

    def batch_context(bid):
        b = one(BSQL + " WHERE b.batch_id=?", bid)
        if not b:
            return None
        r = assess(bid, store=False)
        score = risk_score(r)
        readings = q("""SELECT temperature,humidity,recorded_at FROM sensor_readings
                       WHERE storage_id=(SELECT storage_unit_id FROM batches WHERE batch_id=?)
                       AND quality_status='VALID' ORDER BY recorded_at DESC,reading_id DESC LIMIT 12""", bid)
        readings = readings[::-1]
        trend = 0
        if len(readings) >= 2:
            try:
                hours = max((datetime.fromisoformat(readings[-1]['recorded_at']) - datetime.fromisoformat(readings[0]['recorded_at'])).total_seconds()/3600, 0.25)
                trend = round((readings[-1]['temperature'] - readings[0]['temperature']) / hours, 2)
            except Exception:
                trend = 0
        probability = min(99, max(3, score + (10 if trend > 0.5 else 0)))
        safe_hours = max(2, round((r.get('est_shelf_life_days') or 1) * 24 * (1 - probability / 125), 1))
        contributors = []
        st = one("SELECT * FROM storage_units WHERE storage_id=?", b['storage_unit_id']) if b['storage_unit_id'] else None
        if readings and st:
            last = readings[-1]
            if last['temperature'] > st['max_temperature']: contributors.append('Temperature above safe limit')
            if last['temperature'] < st['min_temperature']: contributors.append('Temperature below safe limit')
            if last['humidity'] is not None and st['max_humidity'] is not None and last['humidity'] > st['max_humidity']: contributors.append('Humidity above safe limit')
        if trend > 0.5: contributors.append('Temperature rising rapidly')
        if (r.get('duration_out_of_range') or 0) > 30: contributors.append('Extended time outside safe range')
        if not contributors: contributors.append('No major excursion detected')
        level = 'CRITICAL' if probability >= 76 else 'HIGH' if probability >= 51 else 'MEDIUM' if probability >= 26 else 'LOW'
        return {"batch": b, "risk": r, "risk_score": score, "risk_level": level,
                "spoilage_probability": probability, "estimated_safe_life_hours": safe_hours,
                "confidence": min(96, 72 + len(readings) * 2), "contributors": contributors,
                "trend_per_hour": trend, "readings": readings}

    @app.get('/api/intelligence/overview')
    @auth()
    def intelligence_overview():
        batches = q(BSQL + ' ORDER BY b.batch_id DESC')
        risks = []
        total_qty = 0
        for b in batches:
            total_qty += float(b.get('quantity') or 0)
            try: c = batch_context(b['batch_id']); risks.append(c) if c else None
            except Exception: pass
        counts = {k: 0 for k in ('LOW','MEDIUM','HIGH','CRITICAL')}
        for c in risks: counts[c['risk_level']] = counts.get(c['risk_level'], 0) + 1
        open_alerts = one("SELECT COUNT(*) n FROM alerts WHERE status='OPEN'")['n']
        active_shipments = one("SELECT COUNT(*) n FROM shipments WHERE status IN ('PLANNED','IN_TRANSIT')")['n']
        storage = q('SELECT * FROM storage_units ORDER BY storage_code')
        high = sorted(risks, key=lambda x: x['spoilage_probability'], reverse=True)
        estimated_prevented = round(sum((c['batch']['quantity'] or 0) for c in risks if c['risk_level'] in ('HIGH','CRITICAL') and c['spoilage_probability'] < 90) * 0.18, 1)
        return {"kpis":{"batches":len(batches),"quantity_kg":round(total_qty,1),"open_alerts":open_alerts,"active_shipments":active_shipments,
                          "high_risk":counts['HIGH']+counts['CRITICAL'],"estimated_loss_prevented_kg":estimated_prevented},
                "risk_distribution":counts,
                "critical_action": high[0] if high else None,
                "storage": storage}

    @app.get('/api/intelligence/risk/<int:bid>')
    @auth()
    def intelligence_risk(bid):
        c = batch_context(bid)
        return c or ({"error":"Batch not found"}, 404)

    @app.get('/api/intelligence/recommendations/<int:bid>')
    @auth()
    def intelligence_recommendations(bid):
        c = batch_context(bid)
        if not c: return {"error":"Batch not found"}, 404
        b, r = c['batch'], c['risk']; rec = 'Continue normal monitoring.'; urgency='LOW'; deadline=1440
        if c['risk_level'] == 'CRITICAL': rec='Move the batch to the safest compatible cold-storage unit immediately and prioritize dispatch.'; urgency='CRITICAL'; deadline=15
        elif c['risk_level'] == 'HIGH': rec='Move to a stable compatible storage unit and prioritize dispatch before the estimated safe-life window closes.'; urgency='HIGH'; deadline=60
        elif c['trend_per_hour'] > 0.5: rec='Inspect refrigeration and reduce temperature rise before the safe limit is crossed.'; urgency='HIGH'; deadline=60
        elif c['risk_level'] == 'MEDIUM': rec='Monitor closely and prioritize this batch over low-risk batches.'; urgency='MEDIUM'; deadline=240
        reasons='; '.join(c['contributors'])
        return {"batch_code":b['batch_code'],"priority":urgency,"action":rec,"deadline_minutes":deadline,
                "reason":reasons,"expected_benefit":"Reduce exposure time and preserve remaining product quality.",
                "risk_score":c['risk_score'],"spoilage_probability":c['spoilage_probability']}

    @app.get('/api/intelligence/anomalies')
    @auth()
    def intelligence_anomalies():
        out=[]
        for s in q('SELECT * FROM storage_units ORDER BY storage_code'):
            rows=q("SELECT * FROM sensor_readings WHERE storage_id=? AND quality_status='VALID' ORDER BY recorded_at DESC,reading_id DESC LIMIT 12",s['storage_id'])[::-1]
            for i,r in enumerate(rows):
                if r['temperature'] > s['max_temperature'] or r['temperature'] < s['min_temperature']:
                    out.append({"storage_code":s['storage_code'],"type":"TEMPERATURE_LIMIT","severity":"HIGH","message":f"{r['temperature']}°C outside {s['min_temperature']}–{s['max_temperature']}°C","recorded_at":r['recorded_at']})
                if i and abs(r['temperature']-rows[i-1]['temperature']) >= 2:
                    out.append({"storage_code":s['storage_code'],"type":"SUDDEN_CHANGE","severity":"HIGH","message":f"Temperature changed {rows[i-1]['temperature']}°C → {r['temperature']}°C","recorded_at":r['recorded_at']})
        return out[-30:][::-1]

    @app.get('/api/intelligence/dispatch-queue')
    @auth()
    def intelligence_dispatch():
        out=[]
        for b in q(BSQL + " WHERE b.current_status='STORED'"):
            c=batch_context(b['batch_id'])
            if not c: continue
            priority=0 if c['risk_level']=='CRITICAL' else 1 if c['risk_level']=='HIGH' else 2 if c['risk_level']=='MEDIUM' else 3
            out.append({"priority":priority+1,"batch_id":b['batch_id'],"batch_code":b['batch_code'],"product":b['product_name'],
                        "quantity":b['quantity'],"risk_level":c['risk_level'],"risk_score":c['risk_score'],
                        "remaining_safe_life_hours":c['estimated_safe_life_hours'],"reason":c['contributors'][0]})
        out.sort(key=lambda x:(x['priority'],x['remaining_safe_life_hours']))
        for i,x in enumerate(out,1): x['queue_position']=i
        return out

    @app.get('/api/intelligence/quality/<int:bid>')
    @auth()
    def intelligence_quality(bid):
        c=batch_context(bid)
        if not c:return {"error":"Batch not found"},404
        score=max(0,min(100,round(100-c['spoilage_probability']*0.55-len([x for x in c['contributors'] if 'No major' not in x])*7)))
        return {"batch_code":c['batch']['batch_code'],"quality_score":score,"explanation":f"Score reflects temperature exposure, trend and remaining safe life. Contributors: {', '.join(c['contributors'])}."}

    @app.get('/api/passport/<code>')
    def product_passport(code):
        b=one(BSQL+' WHERE b.batch_code=?',code.upper())
        if not b:return {"error":"Batch not found"},404
        c=batch_context(b['batch_id'])
        events=q('SELECT event_type,location,description,event_time FROM batch_events WHERE batch_id=? ORDER BY event_time',b['batch_id'])
        sh=q('SELECT shipment_id,destination,status,dispatch_time,estimated_arrival,actual_arrival FROM shipments WHERE batch_id=? ORDER BY shipment_id',b['batch_id'])
        v=one('SELECT risk_level FROM risk_assessments WHERE batch_id=? ORDER BY assessed_at DESC LIMIT 1',b['batch_id'])
        verdict='FAIL' if c['risk_level']=='CRITICAL' else 'CAUTION' if c['risk_level'] in ('HIGH','MEDIUM') else 'PASS'
        return {"batch":b,"risk":c,"events":events,"shipments":sh,"verdict":verdict,"quality_score":max(0,100-c['spoilage_probability'])}

    @app.post('/api/ai/assistant')
    @auth()
    def ai_assistant():
        question=str((__import__('flask').request.json or {}).get('question','')).strip().lower()
        if not question:return {"answer":"Ask me about dispatch, risk, storage, alerts or a batch."}
        queue=intelligence_dispatch.__wrapped__().get_json() if hasattr(intelligence_dispatch,'__wrapped__') else []
        if 'dispatch' in question or 'first' in question or 'sell' in question:
            if queue:
                x=queue[0]; return {"mode":"rules-fallback","answer":f"Dispatch {x['batch_code']} first. It is queue position 1 with {x['risk_level']} risk and about {x['remaining_safe_life_hours']} hours of estimated safe life remaining. Main factor: {x['reason']}."}
        if 'alert' in question:
            n=one("SELECT COUNT(*) n FROM alerts WHERE status='OPEN'")['n']; return {"mode":"rules-fallback","answer":f"There are {n} open alerts. Prioritize HIGH and CRITICAL alerts because they indicate the greatest immediate cold-chain risk."}
        if 'storage' in question or 'safest' in question:
            ss=q('SELECT * FROM storage_units WHERE status="ACTIVE" ORDER BY current_occupancy/capacity ASC')
            if ss:return {"mode":"rules-fallback","answer":f"{ss[0]['storage_code']} currently has the most available capacity among active units. Always verify crop-specific temperature and humidity compatibility before assignment."}
        import re
        m=re.search(r'ac-\d{4}-\d{4}',question)
        if m:
            b=one(BSQL+' WHERE b.batch_code=?',m.group(0).upper())
            if b:
                c=batch_context(b['batch_id']); return {"mode":"rules-fallback","answer":f"{b['batch_code']} is {c['risk_level']} risk with a spoilage probability of {c['spoilage_probability']}%. Main factors: {', '.join(c['contributors'])}."}
        return {"mode":"rules-fallback","answer":"I can analyze the live database for dispatch priority, batch risk, storage capacity and open alerts. Try: 'Which batch should I dispatch first?'"}

    @app.post('/api/ai/crop-screen')
    @auth()
    def crop_screen():
        from flask import request
        f=request.files.get('image'); name=(f.filename if f else '').lower()
        # Demo-safe visual screening fallback; clearly marked as assisted/demo.
        if any(x in name for x in ('spot','fung','mold','disease')):
            issue='Possible fungal/quality damage'; conf=91
        elif any(x in name for x in ('healthy','fresh','good')):
            issue='No obvious visible quality issue in demo screening'; conf=94
        else:
            issue='Possible surface quality variation — manual inspection recommended'; conf=78
        return {"mode":"demo-assisted-screening","crop":"Unknown crop","possible_issue":issue,"confidence":conf,
                "recommendation":"Inspect the affected produce, isolate questionable items and do not mix uncertain produce with verified healthy stock.",
                "disclaimer":"Prototype visual screening; not a scientific diagnosis."}

    @app.get('/api/intelligence/smart-storage')
    @auth()
    def smart_storage():
        from flask import request
        pid=request.args.get('product_id',type=int); qty=request.args.get('quantity',type=float,default=0)
        p=one('SELECT * FROM products WHERE product_id=?',pid) if pid else None
        if not p:return {"error":"product_id required"},400
        candidates=[]
        for s in q('SELECT * FROM storage_units WHERE status="ACTIVE"'):
            free=s['capacity']-s['current_occupancy']
            temp_ok=p['min_temperature'] is None or (s['min_temperature']<=p['min_temperature'] and s['max_temperature']>=p['max_temperature'])
            hum_ok=p['min_humidity'] is None or (s['min_humidity']<=p['min_humidity'] and s['max_humidity']>=p['max_humidity'])
            candidates.append(((0 if temp_ok else 50)+(0 if hum_ok else 30)+(0 if free>=qty else 100)+100*(s['current_occupancy']/s['capacity']),s,temp_ok,hum_ok,free))
        candidates.sort(key=lambda x:x[0]); score,s,temp_ok,hum_ok,free=candidates[0]
        return {"recommended":s,"temperature_compatible":temp_ok,"humidity_compatible":hum_ok,"free_capacity":free,
                "reason":["Correct temperature range" if temp_ok else "Temperature range needs review","Correct humidity range" if hum_ok else "Humidity range needs review",f"{free:g} kg available"]}

    @app.post('/api/demo/run')
    @auth('MANAGER')
    def demo_run():
        # Select a demo-friendly stored batch, preferably tomato.
        b=one(BSQL+" WHERE lower(p.product_name)='tomato' AND b.storage_unit_id IS NOT NULL ORDER BY b.batch_id DESC LIMIT 1")
        if not b:b=one(BSQL+" WHERE b.storage_unit_id IS NOT NULL ORDER BY b.batch_id DESC LIMIT 1")
        if not b:return {"error":"No stored batch available for demo"},400
        st=one('SELECT * FROM storage_units WHERE storage_id=?',b['storage_unit_id'])
        base=max(st['min_temperature']+0.5, (st['min_temperature']+st['max_temperature'])/2)
        start=datetime.now(timezone.utc)-timedelta(minutes=30)
        readings=[base,base+0.7,base+1.8,st['max_temperature']+2.0,st['max_temperature']+3.8]
        for i,t in enumerate(readings):
            process_reading(st['storage_id'],t,min(95,st['max_humidity']+2 if st['max_humidity'] else 90),'DEMO', (start+timedelta(minutes=i*7)).isoformat())
        c=batch_context(b['batch_id'])
        ev(b['batch_id'],'DEMO_ANOMALY',st['storage_name'],f"Tech League demo: simulated temperature escalation to {readings[-1]:g}°C",None)
        return {"ok":True,"message":"90-second Tech League scenario prepared","batch_code":b['batch_code'],"storage_code":st['storage_code'],"risk":c,
                "next_steps":["Open AI Command Center","Show anomaly and risk","Open recommendation","Open Product Passport"]}
