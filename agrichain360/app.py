"""Fasal Setu backend: Flask + SQLite. Run: python app.py  ->  http://localhost:5000"""
import sqlite3, os, secrets, io
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g, Response
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer
import segno

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("AGRI_DB", os.path.join(BASE, "fasalsetu.db"))
SF = os.path.join(BASE, ".secret")
if not os.path.exists(SF):
    open(SF, "w").write(secrets.token_hex(32))
ser = URLSafeTimedSerializer(open(SF).read())
app = Flask(__name__, static_folder="static", static_url_path="")
SAMPLE_MIN = 15  # each reading represents a 15-minute sampling interval
SHELF = {"banana": 7, "tomato": 14, "apple": 90, "orange": 30, "mango": 10}  # indicative days

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL,
 email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN('ADMIN','FARMER','MANAGER','TRANSPORTER')), phone TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(product_id INTEGER PRIMARY KEY AUTOINCREMENT, product_name TEXT UNIQUE NOT NULL,
 category TEXT, unit TEXT DEFAULT 'KG', description TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS storage_units(storage_id INTEGER PRIMARY KEY AUTOINCREMENT, storage_code TEXT UNIQUE NOT NULL,
 storage_name TEXT NOT NULL, location TEXT, capacity REAL NOT NULL, current_occupancy REAL DEFAULT 0,
 min_temperature REAL NOT NULL, max_temperature REAL NOT NULL, min_humidity REAL, max_humidity REAL,
 status TEXT DEFAULT 'ACTIVE');
CREATE TABLE IF NOT EXISTS batches(batch_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_code TEXT UNIQUE,
 product_id INTEGER NOT NULL REFERENCES products, created_by INTEGER REFERENCES users, quantity REAL NOT NULL,
 harvest_date TEXT, received_date TEXT, current_status TEXT DEFAULT 'STORED',
 storage_unit_id INTEGER REFERENCES storage_units, qr_code_value TEXT);
CREATE TABLE IF NOT EXISTS sensor_readings(reading_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER REFERENCES batches,
 storage_id INTEGER NOT NULL REFERENCES storage_units, temperature REAL, humidity REAL, recorded_at TEXT NOT NULL,
 source TEXT DEFAULT 'SENSOR', quality_status TEXT DEFAULT 'VALID');
CREATE TABLE IF NOT EXISTS alerts(alert_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER REFERENCES batches,
 storage_id INTEGER REFERENCES storage_units, alert_type TEXT, severity TEXT, message TEXT, created_at TEXT,
 status TEXT DEFAULT 'OPEN', acknowledged_by INTEGER REFERENCES users);
CREATE TABLE IF NOT EXISTS shipments(shipment_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL REFERENCES batches,
 source_location TEXT, destination TEXT, transporter_name TEXT, dispatch_time TEXT, estimated_arrival TEXT,
 actual_arrival TEXT, status TEXT DEFAULT 'PLANNED');
CREATE TABLE IF NOT EXISTS risk_assessments(assessment_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL REFERENCES batches,
 temperature_exposure REAL, duration_out_of_range REAL, storage_duration REAL, risk_level TEXT, explanation TEXT,
 model_version TEXT, assessed_at TEXT);
CREATE TABLE IF NOT EXISTS batch_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL REFERENCES batches,
 event_type TEXT, location TEXT, description TEXT, recorded_by INTEGER REFERENCES users, event_time TEXT);
CREATE TABLE IF NOT EXISTS shipment_events(shipment_event_id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER NOT NULL REFERENCES shipments,
 event_type TEXT, location TEXT, temperature REAL, recorded_at TEXT);
"""

def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row; g.db.execute("PRAGMA foreign_keys=ON")
    return g.db
@app.teardown_appcontext
def close(e):
    d = g.pop("db", None)
    if d: d.close()
def q(sql, *a): return [dict(r) for r in db().execute(sql, a).fetchall()]
def one(sql, *a):
    r = q(sql, *a); return r[0] if r else None
def ex(sql, *a):
    c = db().execute(sql, a); db().commit(); return c.lastrowid
def err(m, c=400): return jsonify(error=m), c
@app.errorhandler(ValueError)
def bad(e): return err(str(e))
def num(v, name, lo=None, hi=None, req=True):
    if v in (None, ""):
        if req: raise ValueError(f"{name} is required")
        return None
    try: x = float(v)
    except (TypeError, ValueError): raise ValueError(f"{name} must be a number")
    if (lo is not None and x < lo) or (hi is not None and x > hi): raise ValueError(f"{name} must be between {lo} and {hi}")
    return x

def auth(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            try: uid = ser.loads(request.headers.get("Authorization", "").replace("Bearer ", ""), max_age=7 * 86400)
            except Exception: return err("Please log in", 401)
            u = one("SELECT user_id,full_name,email,role FROM users WHERE user_id=?", uid)
            if not u: return err("Please log in", 401)
            if roles and u["role"] not in roles and u["role"] != "ADMIN": return err(f"Role {u['role']} is not allowed to do this", 403)
            g.user = u; return f(*a, **k)
        return w
    return deco

def ev(bid, typ, loc, desc, uid=None, when=None):
    ex("INSERT INTO batch_events(batch_id,event_type,location,description,recorded_by,event_time) VALUES(?,?,?,?,?,?)", bid, typ, loc, desc, uid, when or now())

# ---------- auth ----------
@app.post("/api/register")
def register():
    d = request.json or {}
    if d.get("role") not in ("FARMER", "MANAGER", "TRANSPORTER"): return err("Choose role FARMER, MANAGER or TRANSPORTER")
    if not d.get("full_name") or "@" not in str(d.get("email", "")): return err("Name and valid email required")
    if len(d.get("password", "")) < 6: return err("Password must be at least 6 characters")
    if one("SELECT 1 x FROM users WHERE email=?", d["email"].lower()): return err("Email already registered")
    uid = ex("INSERT INTO users(full_name,email,password_hash,role,phone,created_at) VALUES(?,?,?,?,?,?)", d["full_name"], d["email"].lower(), generate_password_hash(d["password"]), d["role"], d.get("phone"), now())
    return jsonify(ok=True, user_id=uid)
@app.post("/api/login")
def login():
    d = request.json or {}
    u = one("SELECT * FROM users WHERE email=?", str(d.get("email", "")).lower())
    if not u or not check_password_hash(u["password_hash"], d.get("password", "")): return err("Wrong email or password", 401)
    return jsonify(token=ser.dumps(u["user_id"]), user={k: u[k] for k in ("user_id", "full_name", "email", "role")})

# ---------- products / storage ----------
@app.get("/api/products")
@auth()
def products(): return jsonify(q("SELECT * FROM products ORDER BY product_name"))
@app.post("/api/products")
@auth("MANAGER")
def add_product():
    d = request.json or {}
    if not d.get("product_name"): return err("product_name required")
    try: ex("INSERT INTO products(product_name,category,unit,description,created_at) VALUES(?,?,?,?,?)", d["product_name"], d.get("category"), d.get("unit") or "KG", d.get("description"), now())
    except sqlite3.IntegrityError: return err("Product already exists")
    l = plim(d); ex("UPDATE products SET min_temperature=?,max_temperature=?,min_humidity=?,max_humidity=?,shelf_life_days=? WHERE product_name=?", *l, d["product_name"])
    return jsonify(ok=True)

def latest(sid): return one("SELECT temperature,humidity,recorded_at,quality_status FROM sensor_readings WHERE storage_id=? ORDER BY recorded_at DESC,reading_id DESC LIMIT 1", sid)
@app.get("/api/storage")
@auth()
def storage():
    rows = q("SELECT * FROM storage_units ORDER BY storage_code")
    for r in rows: r["latest"] = latest(r["storage_id"])
    return jsonify(rows)
@app.post("/api/storage")
@auth("MANAGER")
def add_storage():
    d = request.json or {}
    mn, mx = num(d.get("min_temperature"), "min_temperature", -40, 60), num(d.get("max_temperature"), "max_temperature", -40, 60)
    if mn >= mx: return err("min_temperature must be below max_temperature")
    hl, hh = num(d.get("min_humidity"), "min_humidity", 0, 100, False), num(d.get("max_humidity"), "max_humidity", 0, 100, False)
    cap = num(d.get("capacity"), "capacity", 1)
    if not d.get("storage_code") or not d.get("storage_name"): return err("Code and name required")
    try: ex("INSERT INTO storage_units(storage_code,storage_name,location,capacity,min_temperature,max_temperature,min_humidity,max_humidity) VALUES(?,?,?,?,?,?,?,?)", d["storage_code"], d["storage_name"], d.get("location"), cap, mn, mx, hl, hh)
    except sqlite3.IntegrityError: return err("Storage code already exists")
    ex("UPDATE storage_units SET lat=?,lng=? WHERE storage_code=?", num(d.get("lat"), "lat", -90, 90, False), num(d.get("lng"), "lng", -180, 180, False), d["storage_code"])
    return jsonify(ok=True)

# ---------- batches ----------
def new_batch(pid, qty, harvest, sid, uid, when=None):
    p = one("SELECT * FROM products WHERE product_id=?", pid)
    if not p: raise ValueError("Unknown product")
    st = None
    if sid:
        st = one("SELECT * FROM storage_units WHERE storage_id=?", sid)
        if not st: raise ValueError("Unknown storage unit")
        if st["status"] != "ACTIVE": raise ValueError("Storage unit is not ACTIVE")
        if st["current_occupancy"] + qty > st["capacity"]: raise ValueError(f"Not enough capacity (free: {st['capacity'] - st['current_occupancy']:g})")
    bid = ex("INSERT INTO batches(product_id,created_by,quantity,harvest_date,received_date,current_status,storage_unit_id) VALUES(?,?,?,?,?,?,?)", pid, uid, qty, harvest, (when or now())[:10], "STORED" if st else "REGISTERED", sid)
    code = f"AC-{datetime.now().year}-{bid:04d}"
    ex("UPDATE batches SET batch_code=?,qr_code_value=? WHERE batch_id=?", code, code, bid)
    ev(bid, "CREATED", None, f"{qty:g} {p['unit']} of {p['product_name']} registered", uid, when)
    if st:
        ex("UPDATE storage_units SET current_occupancy=current_occupancy+? WHERE storage_id=?", qty, sid)
        ev(bid, "STORED", st["storage_name"], f"Stored in {st['storage_code']} (limits {st['min_temperature']:g}-{st['max_temperature']:g}°C)", uid, when)
    return bid
BSQL = "SELECT b.*,p.product_name,p.unit,s.storage_code,(SELECT risk_level FROM risk_assessments r WHERE r.batch_id=b.batch_id ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1) risk FROM batches b JOIN products p USING(product_id) LEFT JOIN storage_units s ON s.storage_id=b.storage_unit_id"
@app.get("/api/batches")
@auth()
def batches(): return jsonify(q(BSQL + " ORDER BY b.batch_id DESC"))
@app.post("/api/batches")
@auth("FARMER", "MANAGER")
def add_batch():
    d = request.json or {}
    qty = num(d.get("quantity"), "quantity", 0.01)
    h = d.get("harvest_date")
    if h:
        try: datetime.strptime(h, "%Y-%m-%d")
        except ValueError: return err("harvest_date must be YYYY-MM-DD")
        if h > now()[:10]: return err("harvest_date cannot be in the future")
    bid = new_batch(int(num(d.get("product_id"), "product_id")), qty, h, int(d["storage_id"]) if d.get("storage_id") else None, g.user["user_id"])
    return jsonify(batch_id=bid, batch_code=one("SELECT batch_code FROM batches WHERE batch_id=?", bid)["batch_code"], warning=check_placement(bid))
@app.post("/api/batches/<int:bid>/assign")
@auth("MANAGER", "FARMER")
def assign(bid):
    b = one("SELECT * FROM batches WHERE batch_id=?", bid); sid = int((request.json or {}).get("storage_id", 0))
    st = one("SELECT * FROM storage_units WHERE storage_id=?", sid)
    if not b or not st: return err("Batch or storage not found", 404)
    if b["current_status"] in ("IN_TRANSIT", "DELIVERED"): return err("Batch already shipped")
    if st["status"] != "ACTIVE" or st["current_occupancy"] + b["quantity"] > st["capacity"]: return err("Storage inactive or lacks capacity")
    if b["storage_unit_id"]: ex("UPDATE storage_units SET current_occupancy=current_occupancy-? WHERE storage_id=?", b["quantity"], b["storage_unit_id"])
    ex("UPDATE storage_units SET current_occupancy=current_occupancy+? WHERE storage_id=?", b["quantity"], sid)
    ex("UPDATE batches SET storage_unit_id=?,current_status='STORED' WHERE batch_id=?", sid, bid)
    ev(bid, "STORED", st["storage_name"], f"Moved to {st['storage_code']}", g.user["user_id"])
    return jsonify(ok=True, warning=check_placement(bid))

# ---------- monitoring / alerts ----------
def dev(st, t, h):
    td = max(st["min_temperature"] - t, t - st["max_temperature"], 0)
    hd = 0
    if h is not None and st["min_humidity"] is not None: hd = max(st["min_humidity"] - h, 0)
    if h is not None and st["max_humidity"] is not None: hd = max(hd, h - st["max_humidity"])
    return td, hd
def process_reading(sid, t, h, source="SENSOR", when=None, uid=None):
    st = one("SELECT * FROM storage_units WHERE storage_id=?", sid)
    if not st: raise ValueError("Unknown storage unit")
    valid = t is not None and -40 <= t <= 60 and (h is None or 0 <= h <= 100)
    rid = ex("INSERT INTO sensor_readings(storage_id,temperature,humidity,recorded_at,source,quality_status) VALUES(?,?,?,?,?,?)", sid, t, h, when or now(), source, "VALID" if valid else "INVALID")
    if not valid: return rid, []
    found = []
    if t > st["max_temperature"]: found.append(("HIGH_TEMP", t - st["max_temperature"], 1, f"{t:g}°C exceeds max {st['max_temperature']:g}°C"))
    if t < st["min_temperature"]: found.append(("LOW_TEMP", st["min_temperature"] - t, 1, f"{t:g}°C below min {st['min_temperature']:g}°C"))
    if h is not None and st["max_humidity"] is not None and h > st["max_humidity"]: found.append(("HIGH_HUMIDITY", h - st["max_humidity"], 4, f"Humidity {h:g}% above {st['max_humidity']:g}%"))
    if h is not None and st["min_humidity"] is not None and h < st["min_humidity"]: found.append(("LOW_HUMIDITY", st["min_humidity"] - h, 4, f"Humidity {h:g}% below {st['min_humidity']:g}%"))
    out = []
    for typ, d, scale, msg in found:
        if one("SELECT 1 x FROM alerts WHERE storage_id=? AND alert_type=? AND status!='RESOLVED'", sid, typ): continue
        sev = "HIGH" if d / scale >= 4 else "MEDIUM" if d / scale >= 1.5 else "LOW"
        out.append(ex("INSERT INTO alerts(storage_id,alert_type,severity,message,created_at,status) VALUES(?,?,?,?,?,'OPEN')", sid, typ, sev, f"{st['storage_code']}: {msg}", when or now()))
    if found:
        for b in q("SELECT batch_id FROM batches WHERE storage_unit_id=?", sid): assess(b["batch_id"], when)
    return rid, out
@app.post("/api/readings")
@auth("MANAGER")
def add_reading():
    d = request.json or {}
    rid, al = process_reading(int(num(d.get("storage_id"), "storage_id")), num(d.get("temperature"), "temperature"), num(d.get("humidity"), "humidity", req=False), d.get("source", "SENSOR") if d.get("source") in ("SENSOR", "SIMULATED") else "SENSOR")
    return jsonify(reading_id=rid, alerts_created=len(al))
@app.post("/api/simulate/<int:sid>")
@auth("MANAGER")
def simulate(sid):
    import random
    st = one("SELECT * FROM storage_units WHERE storage_id=?", sid)
    if not st: return err("Unknown storage unit", 404)
    last = latest(sid); mid = (st["min_temperature"] + st["max_temperature"]) / 2
    t = (last["temperature"] if last else mid) + random.uniform(-1.2, 1.4) + (mid - (last["temperature"] if last else mid)) * 0.1
    hm = ((st["min_humidity"] or 85) + (st["max_humidity"] or 95)) / 2
    h = max(0, min(100, hm + random.uniform(-6, 6)))
    rid, al = process_reading(sid, round(t, 1), round(h, 1), "SIMULATED")
    return jsonify(reading_id=rid, temperature=round(t, 1), humidity=round(h, 1), alerts_created=len(al))
@app.get("/api/readings/<int:sid>")
@auth()
def readings(sid): return jsonify(q("SELECT * FROM (SELECT * FROM sensor_readings WHERE storage_id=? ORDER BY recorded_at DESC,reading_id DESC LIMIT 60) ORDER BY recorded_at,reading_id", sid))
@app.get("/api/alerts")
@auth()
def alerts(): return jsonify(q("SELECT a.*,u.full_name ack_by FROM alerts a LEFT JOIN users u ON u.user_id=a.acknowledged_by ORDER BY (a.status='RESOLVED'),a.created_at DESC LIMIT 100"))
@app.post("/api/alerts/<int:aid>/<act>")
@auth("MANAGER")
def alert_act(aid, act):
    a = one("SELECT * FROM alerts WHERE alert_id=?", aid)
    if not a or act not in ("acknowledge", "resolve"): return err("Alert not found", 404)
    if a["status"] == "RESOLVED": return err("Already resolved")
    if act == "acknowledge" and a["status"] != "OPEN": return err("Alert is not OPEN")
    ex("UPDATE alerts SET status=?,acknowledged_by=COALESCE(acknowledged_by,?) WHERE alert_id=?", "ACKNOWLEDGED" if act == "acknowledge" else "RESOLVED", g.user["user_id"], aid)
    return jsonify(ok=True)

# ---------- risk (rule-based, documented) ----------
def assess(bid, when=None):
    b = one("SELECT b.*,p.product_name FROM batches b JOIN products p USING(product_id) WHERE batch_id=?", bid)
    st = one("SELECT * FROM storage_units WHERE storage_id=?", b["storage_unit_id"]) if b and b["storage_unit_id"] else None
    if not b: return None
    start = (b["received_date"] or now()[:10]) + "T00:00:00"
    maxdev, outn = 0.0, 0
    if st:
        for r in q("SELECT temperature,humidity FROM sensor_readings WHERE storage_id=? AND quality_status='VALID' AND recorded_at>=?", st["storage_id"], start):
            td, hd = dev(st, r["temperature"], r["humidity"])
            if td > 0 or hd > 0: outn += 1; maxdev = max(maxdev, td, hd / 4)
    out_min = outn * SAMPLE_MIN
    days = max((datetime.now(timezone.utc) - datetime.fromisoformat(start + "+00:00")).total_seconds() / 86400, 0)
    score = maxdev * (out_min / 60) + 0.5 * maxdev
    lvl = "HIGH" if maxdev >= 5 or score >= 6 else "MEDIUM" if (score >= 2 or maxdev >= 2) else "LOW"
    base = SHELF.get(b["product_name"].lower(), 7)
    est = max(0, round(base * {"LOW": 1, "MEDIUM": .85, "HIGH": .65}[lvl] - days, 1))
    exp = (f"Max excursion {maxdev:.1f}°C-equivalent for ~{out_min} min ({outn} readings x {SAMPLE_MIN} min); in storage {days:.1f} days. "
           f"Score = excursion x hours_out + 0.5 x excursion = {score:.1f}. Estimated remaining shelf life (prototype) ~{est} days of indicative {base}-day baseline.") if st else "Batch not in storage; no readings to assess."
    ex("INSERT INTO risk_assessments(batch_id,temperature_exposure,duration_out_of_range,storage_duration,risk_level,explanation,model_version,assessed_at) VALUES(?,?,?,?,?,?,?,?)", bid, round(maxdev, 2), out_min, round(days, 2), lvl, exp, "rules-v1", when or now())
    return dict(batch_id=bid, risk_level=lvl, explanation=exp, est_shelf_life_days=est, duration_out_of_range=out_min, temperature_exposure=round(maxdev, 2))
@app.get("/api/risk/<int:bid>")
@auth()
def risk(bid): return jsonify(assess(bid) or ("", 404))

# ---------- shipments ----------
@app.get("/api/shipments")
@auth()
def shipments():
    rows = q("SELECT s.*,b.batch_code FROM shipments s JOIN batches b USING(batch_id) ORDER BY s.shipment_id DESC")
    for r in rows: r["events"] = q("SELECT * FROM shipment_events WHERE shipment_id=? ORDER BY recorded_at", r["shipment_id"])
    return jsonify(rows)
@app.post("/api/shipments")
@auth("TRANSPORTER", "MANAGER")
def add_ship():
    d = request.json or {}; b = one("SELECT * FROM batches WHERE batch_id=?", int(num(d.get("batch_id"), "batch_id")))
    if not b: return err("Batch not found", 404)
    if b["current_status"] in ("IN_TRANSIT", "DELIVERED"): return err("Batch already shipped")
    if not d.get("destination"): return err("destination required")
    sid = ex("INSERT INTO shipments(batch_id,source_location,destination,transporter_name,estimated_arrival,status) VALUES(?,?,?,?,?,'PLANNED')", b["batch_id"], d.get("source_location"), d["destination"], d.get("transporter_name") or g.user["full_name"], d.get("estimated_arrival") or None)
    ev(b["batch_id"], "SHIPMENT_PLANNED", d.get("source_location"), f"Shipment to {d['destination']} planned", g.user["user_id"])
    return jsonify(shipment_id=sid)
@app.post("/api/shipments/<int:sid>/event")
@auth("TRANSPORTER", "MANAGER")
def ship_event(sid):
    d = request.json or {}; s = one("SELECT * FROM shipments WHERE shipment_id=?", sid); typ = d.get("event_type")
    if not s: return err("Shipment not found", 404)
    if typ not in ("PICKED_UP", "CHECKPOINT", "DELIVERED"): return err("Invalid event_type")
    if s["status"] == "DELIVERED": return err("Shipment already delivered")
    if typ != "PICKED_UP" and s["status"] == "PLANNED": return err("Shipment must be PICKED_UP first")
    t = num(d.get("temperature"), "temperature", -40, 60, False); loc = d.get("location"); b = one("SELECT * FROM batches WHERE batch_id=?", s["batch_id"])
    ex("INSERT INTO shipment_events(shipment_id,event_type,location,temperature,recorded_at,lat,lng) VALUES(?,?,?,?,?,?,?)", sid, typ, loc, t, now(), num(d.get("lat"), "lat", -90, 90, False), num(d.get("lng"), "lng", -180, 180, False))
    if typ == "PICKED_UP":
        ex("UPDATE shipments SET status='IN_TRANSIT',dispatch_time=? WHERE shipment_id=?", now(), sid)
        if b["storage_unit_id"]: ex("UPDATE storage_units SET current_occupancy=MAX(0,current_occupancy-?) WHERE storage_id=?", b["quantity"], b["storage_unit_id"])
        ex("UPDATE batches SET current_status='IN_TRANSIT',storage_unit_id=NULL WHERE batch_id=?", b["batch_id"]); ev(b["batch_id"], "SHIPPED", loc, "Dispatched", g.user["user_id"])
    elif typ == "DELIVERED":
        ex("UPDATE shipments SET status='DELIVERED',actual_arrival=? WHERE shipment_id=?", now(), sid)
        ex("UPDATE batches SET current_status='DELIVERED' WHERE batch_id=?", b["batch_id"]); ev(b["batch_id"], "DELIVERED", loc or s["destination"], "Delivered", g.user["user_id"])
    else: ev(b["batch_id"], "CHECKPOINT", loc, f"Checkpoint{' %.1f°C' % t if t is not None else ''}", g.user["user_id"])
    return jsonify(ok=True)

# ---------- traceability (public) ----------
@app.get("/api/trace/<code>")
def trace(code):
    b = one(BSQL + " WHERE b.batch_code=?", code.upper())
    if not b: return err("Batch not found", 404)
    ships = q("SELECT * FROM shipments WHERE batch_id=?", b["batch_id"])
    for s in ships: s["events"] = q("SELECT * FROM shipment_events WHERE shipment_id=?", s["shipment_id"])
    r = one("SELECT * FROM risk_assessments WHERE batch_id=? ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1", b["batch_id"])
    return jsonify(batch=dict(batch_code=b["batch_code"], product=b["product_name"], quantity=b["quantity"], unit=b["unit"], harvest_date=b["harvest_date"], status=b["current_status"]),
                   events=q("SELECT event_type,location,description,event_time FROM batch_events WHERE batch_id=? ORDER BY event_time,event_id", b["batch_id"]), shipments=ships, risk=r, verdict=verdict(b["batch_id"]))
@app.get("/api/qr/<code>.svg")
def qr(code):
    if not one("SELECT 1 x FROM batches WHERE batch_code=?", code.upper()): return err("Not found", 404)
    buf = io.BytesIO(); segno.make(request.host_url + "?trace=" + code.upper()).save(buf, kind="svg", scale=5, border=2)
    return Response(buf.getvalue(), mimetype="image/svg+xml")

# ---------- reports / analytics ----------
@app.get("/api/report")
@auth()
def report():
    rec = []
    st = q("SELECT * FROM storage_units"); hi = q("SELECT b.batch_code,p.product_name FROM batches b JOIN products p USING(product_id) WHERE b.current_status='STORED' AND (SELECT risk_level FROM risk_assessments r WHERE r.batch_id=b.batch_id ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1)='HIGH'")
    for s in st:
        if s["capacity"] and s["current_occupancy"] / s["capacity"] > .9: rec.append(f"{s['storage_code']} is over 90% full: plan dispatch or add capacity.")
    for b in hi: rec.append(f"Batch {b['batch_code']} ({b['product_name']}) is HIGH risk: inspect and dispatch first (earliest-expiry-first).")
    n = q("SELECT COUNT(*) c FROM alerts WHERE status='OPEN' AND severity='HIGH'")[0]["c"]
    if n: rec.append(f"{n} HIGH severity alert(s) are still OPEN: acknowledge and check cooling.")
    if not rec: rec.append("All monitored storage is within limits. No action needed.")
    return jsonify(batches_by_status=q("SELECT current_status k,COUNT(*) c FROM batches GROUP BY 1"), alerts_by_status=q("SELECT status k,COUNT(*) c FROM alerts GROUP BY 1"),
        alerts_by_type=q("SELECT alert_type k,COUNT(*) c FROM alerts GROUP BY 1"), storage=st,
        risk=q("SELECT risk_level k,COUNT(*) c FROM (SELECT batch_id,risk_level,MAX(assessment_id) FROM risk_assessments GROUP BY batch_id) GROUP BY 1"),
        totals=dict(batches=q("SELECT COUNT(*) c FROM batches")[0]["c"], storage=len(st), open_alerts=q("SELECT COUNT(*) c FROM alerts WHERE status!='RESOLVED'")[0]["c"], shipments=q("SELECT COUNT(*) c FROM shipments")[0]["c"]),
        recommendations=rec)

@app.get("/")
def home(): return app.send_static_file("index.html")

def init_db():
    c = sqlite3.connect(DB); c.executescript(SCHEMA); migrate(c)
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        for n, e, r, p in [("Admin", "admin@agrichain.local", "ADMIN", "Admin@123"), ("Meera Manager", "manager@agrichain.local", "MANAGER", "Pass@123"), ("Farhan Farmer", "farmer@agrichain.local", "FARMER", "Pass@123"), ("Tara Transporter", "transporter@agrichain.local", "TRANSPORTER", "Pass@123")]:
            c.execute("INSERT INTO users(full_name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)", (n, e, generate_password_hash(p), r, now()))
        for n, cat in [("Banana", "Fruit"), ("Tomato", "Vegetable"), ("Apple", "Fruit"), ("Orange", "Fruit"), ("Mango", "Fruit")]:
            c.execute("INSERT INTO products(product_name,category,unit,created_at) VALUES(?,?,'KG',?)", (n, cat, now()))
        for s in [("CU-01", "Banana Ripening Room", "Pimpri, Pune", 2000, 13, 15, 85, 95), ("CU-02", "Tomato Cold Room", "Pune Market Yard", 1500, 10, 13, 85, 95), ("CU-03", "Apple Cold Store", "Nashik", 5000, 0, 4, 90, 95)]:
            c.execute("INSERT INTO storage_units(storage_code,storage_name,location,capacity,min_temperature,max_temperature,min_humidity,max_humidity) VALUES(?,?,?,?,?,?,?,?)", s)
        defaults(c); c.commit(); c.close()
        with app.app_context():
            t0 = datetime.now(timezone.utc) - timedelta(hours=4)
            b1 = new_batch(1, 500, (t0 - timedelta(days=1)).date().isoformat(), 1, 3, t0.isoformat(timespec="seconds"))
            new_batch(2, 300, t0.date().isoformat(), 2, 3, t0.isoformat(timespec="seconds")); new_batch(3, 1200, t0.date().isoformat(), 3, 3, t0.isoformat(timespec="seconds"))
            for i in range(16):
                w = (t0 + timedelta(minutes=15 * i)).isoformat(timespec="seconds")
                process_reading(1, 14 + (3.5 if 8 <= i <= 11 else 0.3 * (i % 3)), 90, "SIMULATED", w)
                process_reading(2, 11.5 + 0.2 * (i % 4), 90, "SIMULATED", w); process_reading(3, 2 + 0.3 * (i % 5), 92, "SIMULATED", w)
    else: c.close()
# ======================= v2 additions =======================
import threading, smtplib, csv, json, urllib.request, urllib.parse, base64, mimetypes
from email.message import EmailMessage
from xml.sax.saxutils import escape as xesc
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
mimetypes.add_type("application/manifest+json", ".webmanifest")
RATE_LIMIT = float(os.environ.get("RATE_LIMIT_C_PER_H", 3))  # rate-of-rise alert threshold

SCHEMA += """
CREATE TABLE IF NOT EXISTS audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, user_name TEXT, method TEXT, path TEXT, detail TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS notifications(notification_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, message TEXT, created_at TEXT, is_read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS devices(device_id INTEGER PRIMARY KEY AUTOINCREMENT, storage_id INTEGER NOT NULL REFERENCES storage_units, device_name TEXT, api_key TEXT UNIQUE NOT NULL, battery_pct REAL, signal_dbm REAL, last_seen TEXT, offline_after_min INTEGER DEFAULT 30, created_at TEXT);
CREATE TABLE IF NOT EXISTS password_resets(email TEXT, code_hash TEXT, expires_at TEXT);
"""
PL = {"Banana": (13, 15, 85, 95, 7), "Tomato": (10, 13, 85, 95, 14), "Apple": (0, 4, 90, 95, 90), "Orange": (3, 8, 85, 90, 30), "Mango": (10, 13, 85, 90, 10)}
SL = {"CU-01": (18.6298, 73.7997), "CU-02": (18.4894, 73.8553), "CU-03": (19.9975, 73.7898)}
def defaults(c):
    for n, v in PL.items(): c.execute("UPDATE products SET min_temperature=?,max_temperature=?,min_humidity=?,max_humidity=?,shelf_life_days=? WHERE product_name=? AND min_temperature IS NULL", (*v, n))
    for k, v in SL.items(): c.execute("UPDATE storage_units SET lat=?,lng=? WHERE storage_code=? AND lat IS NULL", (*v, k))
def migrate(c):
    for t, col in [("products", "min_temperature"), ("products", "max_temperature"), ("products", "min_humidity"), ("products", "max_humidity"), ("products", "shelf_life_days"), ("storage_units", "lat"), ("storage_units", "lng"), ("shipment_events", "lat"), ("shipment_events", "lng")]:
        try: c.execute(f"ALTER TABLE {t} ADD COLUMN {col} REAL")
        except sqlite3.OperationalError: pass
    defaults(c); c.commit()

def P(s): return datetime.fromisoformat(s)
# ---------- notifications (in-app, email, SMS, WhatsApp) ----------
def twilio(to, frm, body):
    sid, tk = os.environ["TWILIO_SID"], os.environ["TWILIO_TOKEN"]
    r = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=urllib.parse.urlencode({"To": to, "From": frm, "Body": body}).encode(), method="POST")
    r.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{tk}".encode()).decode()); urllib.request.urlopen(r, timeout=15)
def send_external(emails, phones, text):
    sent = []
    try:
        if os.environ.get("SMTP_HOST") and emails:
            m = EmailMessage(); m["Subject"] = "Fasal Setu alert"; m["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "alerts@fasalsetu")); m["To"] = ", ".join(emails); m.set_content(text)
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587)), timeout=15) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"): s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
                s.send_message(m)
            sent.append("email")
        if os.environ.get("TWILIO_SID") and os.environ.get("TWILIO_TOKEN"):
            for ph in phones:
                if os.environ.get("TWILIO_FROM"): twilio(ph, os.environ["TWILIO_FROM"], text); sent.append("sms")
                if os.environ.get("TWILIO_WHATSAPP_FROM"): twilio("whatsapp:" + ph, os.environ["TWILIO_WHATSAPP_FROM"], text); sent.append("whatsapp")
    except Exception as e: print("NOTIFY error:", e)
    if not sent: print("NOTIFY (email/SMS/WhatsApp not configured, in-app only):", text)
def notify(sev, typ, msg):
    text = f"[Fasal Setu] {sev} {typ}: {msg}"; us = q("SELECT user_id,email,phone FROM users WHERE role IN('MANAGER','ADMIN')")
    for u in us: ex("INSERT INTO notifications(user_id,message,created_at,is_read) VALUES(?,?,?,0)", u["user_id"], text, now())
    if sev in ("MEDIUM", "HIGH"): threading.Thread(target=send_external, args=([u["email"] for u in us], [u["phone"] for u in us if u["phone"]], text), daemon=True).start()
@app.get("/api/notifications")
@auth()
def notifications():
    rows = q("SELECT * FROM notifications WHERE user_id=? ORDER BY notification_id DESC LIMIT 50", g.user["user_id"])
    return jsonify(items=rows, unread=sum(1 for r in rows if not r["is_read"]))
@app.post("/api/notifications/read")
@auth()
def notif_read(): ex("UPDATE notifications SET is_read=1 WHERE user_id=?", g.user["user_id"]); return jsonify(ok=True)

# ---------- alert engine (auto-resolve, rate-of-change, wrong storage) ----------
def raise_alert(sid, typ, sev, msg, when=None, bid=None):
    if one("SELECT 1 x FROM alerts WHERE storage_id=? AND alert_type=? AND COALESCE(batch_id,0)=COALESCE(?,0) AND status!='RESOLVED'", sid, typ, bid): return None
    aid = ex("INSERT INTO alerts(batch_id,storage_id,alert_type,severity,message,created_at,status) VALUES(?,?,?,?,?,?,'OPEN')", bid, sid, typ, sev, msg, when or now())
    notify(sev, typ, msg); return aid
def auto_resolve(sid, types):
    if not types: return
    for a in q(f"SELECT alert_id FROM alerts WHERE storage_id=? AND status!='RESOLVED' AND alert_type IN ({','.join('?' * len(types))})", sid, *types):
        ex("UPDATE alerts SET status='RESOLVED',message=message||' [auto-resolved]' WHERE alert_id=?", a["alert_id"])
def process_reading(sid, t, h, source="SENSOR", when=None, uid=None, bid=None):
    st = one("SELECT * FROM storage_units WHERE storage_id=?", sid)
    if not st: raise ValueError("Unknown storage unit")
    valid = t is not None and -40 <= t <= 60 and (h is None or 0 <= h <= 100)
    prev = one("SELECT temperature,recorded_at FROM sensor_readings WHERE storage_id=? AND quality_status='VALID' ORDER BY recorded_at DESC,reading_id DESC LIMIT 1", sid)
    rid = ex("INSERT INTO sensor_readings(storage_id,temperature,humidity,recorded_at,source,quality_status) VALUES(?,?,?,?,?,?)", sid, t, h, when or now(), source, "VALID" if valid else "INVALID")
    if not valid: return rid, []
    auto_resolve(sid, ["NO_DATA"])
    found = []
    if t > st["max_temperature"]: found.append(("HIGH_TEMP", t - st["max_temperature"], 1, f"{t:g}°C exceeds max {st['max_temperature']:g}°C"))
    if t < st["min_temperature"]: found.append(("LOW_TEMP", st["min_temperature"] - t, 1, f"{t:g}°C below min {st['min_temperature']:g}°C"))
    if h is not None and st["max_humidity"] is not None and h > st["max_humidity"]: found.append(("HIGH_HUMIDITY", h - st["max_humidity"], 4, f"Humidity {h:g}% above {st['max_humidity']:g}%"))
    if h is not None and st["min_humidity"] is not None and h < st["min_humidity"]: found.append(("LOW_HUMIDITY", st["min_humidity"] - h, 4, f"Humidity {h:g}% below {st['min_humidity']:g}%"))
    out = []
    for typ, d, scale, msg in found:
        sev = "HIGH" if d / scale >= 4 else "MEDIUM" if d / scale >= 1.5 else "LOW"
        a = raise_alert(sid, typ, sev, f"{st['storage_code']}: {msg}", when)
        if a: out.append(a)
    active = {f[0] for f in found}
    auto_resolve(sid, [x for x in ("HIGH_TEMP", "LOW_TEMP", "HIGH_HUMIDITY", "LOW_HUMIDITY") if x not in active])
    rising = False
    if prev:
        hrs = (P(when or now()) - P(prev["recorded_at"])).total_seconds() / 3600
        if 1 / 60 <= hrs <= 1 and "HIGH_TEMP" not in active:
            rate = (t - prev["temperature"]) / hrs
            if rate >= RATE_LIMIT:
                rising = True
                a = raise_alert(sid, "RAPID_RISE", "MEDIUM", f"{st['storage_code']}: temperature rising {rate:.1f}°C/h (now {t:g}°C, limit {st['max_temperature']:g}°C)", when)
                if a: out.append(a)
    if not rising: auto_resolve(sid, ["RAPID_RISE"])
    if found:
        for b in q("SELECT batch_id FROM batches WHERE storage_unit_id=?", sid): assess(b["batch_id"], when)
    return rid, out
def device_state(sid, typ, active, sev="MEDIUM", msg=""):
    code = (one("SELECT storage_code FROM storage_units WHERE storage_id=?", sid) or {}).get("storage_code", "")
    if active: raise_alert(sid, typ, sev, f"{code}: {msg}")
    else: auto_resolve(sid, [typ])
def check_placement(bid):
    b = one("SELECT b.*,p.product_name,p.min_temperature pmin,p.max_temperature pmax,s.storage_code,s.min_temperature smin,s.max_temperature smax FROM batches b JOIN products p USING(product_id) LEFT JOIN storage_units s ON s.storage_id=b.storage_unit_id WHERE b.batch_id=?", bid)
    if not b or b["pmin"] is None or b["smin"] is None: return None
    if b["smin"] < b["pmin"] - 1 or b["smax"] > b["pmax"] + 1:
        msg = f"{b['batch_code']} ({b['product_name']}) needs {b['pmin']:g}-{b['pmax']:g}°C but {b['storage_code']} is set to {b['smin']:g}-{b['smax']:g}°C"
        raise_alert(b["storage_unit_id"], "WRONG_STORAGE", "MEDIUM", msg, bid=bid); return msg

# ---------- risk + shelf-life model (temperature-history based, Q10=2) ----------
def assess(bid, when=None, store=True):
    b = one("SELECT b.*,p.product_name,p.shelf_life_days,p.min_temperature pmin,p.max_temperature pmax FROM batches b JOIN products p USING(product_id) WHERE b.batch_id=?", bid)
    if not b: return None
    st = one("SELECT * FROM storage_units WHERE storage_id=?", b["storage_unit_id"]) if b["storage_unit_id"] else None
    start = (b["received_date"] or now()[:10]) + "T00:00:00"
    days = max((datetime.now(timezone.utc) - P(start + "+00:00")).total_seconds() / 86400, 0)
    base = b["shelf_life_days"] or SHELF.get(b["product_name"].lower(), 7)
    maxdev, outn, rates = 0.0, 0, []
    if st:
        ideal = (b["pmin"] + b["pmax"]) / 2 if b["pmin"] is not None else (st["min_temperature"] + st["max_temperature"]) / 2
        for r in q("SELECT temperature,humidity FROM sensor_readings WHERE storage_id=? AND quality_status='VALID' AND recorded_at>=?", st["storage_id"], start):
            td, hd = dev(st, r["temperature"], r["humidity"]); rates.append(2 ** ((r["temperature"] - ideal) / 10))
            if td > 0 or hd > 0: outn += 1; maxdev = max(maxdev, td, hd / 4)
    out_min = outn * SAMPLE_MIN; avg = sum(rates) / len(rates) if rates else 1.0
    score = maxdev * (out_min / 60) + 0.5 * maxdev
    lvl = "HIGH" if maxdev >= 5 or score >= 6 else "MEDIUM" if (score >= 2 or maxdev >= 2) else "LOW"
    est = max(0, round(base - days * avg, 1))
    exp = (f"Max excursion {maxdev:.1f}°C-equivalent for ~{out_min} min ({outn} readings x {SAMPLE_MIN} min); in storage {days:.1f} days. Risk score {score:.1f} (excursion x hours_out + 0.5 x excursion). "
           f"Shelf-life model: {base:g}-day baseline, ageing rate {avg:.2f}x from temperature history (Q10=2) -> ~{est} days left (indicative).") if st else "Batch not in storage; no readings to assess."
    if store: ex("INSERT INTO risk_assessments(batch_id,temperature_exposure,duration_out_of_range,storage_duration,risk_level,explanation,model_version,assessed_at) VALUES(?,?,?,?,?,?,?,?)", bid, round(maxdev, 2), out_min, round(days, 2), lvl, exp, "rules-v2", when or now())
    return dict(batch_id=bid, risk_level=lvl, explanation=exp, est_shelf_life_days=est, duration_out_of_range=out_min, temperature_exposure=round(maxdev, 2), ageing_rate=round(avg, 2))
@app.get("/api/fefo")
@auth()
def fefo():
    out = []
    for b in q(BSQL + " WHERE b.current_status='STORED'"):
        a = assess(b["batch_id"], store=False)
        out.append(dict(batch_id=b["batch_id"], batch_code=b["batch_code"], product_name=b["product_name"], quantity=b["quantity"], storage_code=b["storage_code"], risk_level=a["risk_level"], remaining_days=a["est_shelf_life_days"]))
    out.sort(key=lambda x: (x["remaining_days"], {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[x["risk_level"]]))
    for i, x in enumerate(out): x["priority"] = i + 1
    return jsonify(out)
@app.get("/api/forecast/<int:sid>")
@auth()
def forecast(sid):
    st = one("SELECT * FROM storage_units WHERE storage_id=?", sid)
    if not st: return err("Unknown storage unit", 404)
    rows = q("SELECT temperature,recorded_at FROM sensor_readings WHERE storage_id=? AND quality_status='VALID' ORDER BY recorded_at DESC,reading_id DESC LIMIT 8", sid)[::-1]
    if len(rows) < 3: return jsonify(enough_data=False)
    t0 = P(rows[-1]["recorded_at"]); xs = [(P(r["recorded_at"]) - t0).total_seconds() / 3600 for r in rows]; ys = [r["temperature"] for r in rows]
    if xs[-1] - xs[0] < 5 / 60: return jsonify(enough_data=False)
    n = len(xs); mx, my = sum(xs) / n, sum(ys) / n; den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den; ic = my - slope * mx
    res = dict(enough_data=True, slope_per_hour=round(slope, 2), predicted_1h=round(ic + slope, 1), current=ys[-1], will_breach=False, limits=[st["min_temperature"], st["max_temperature"]])
    for lim, typ, up in ((st["max_temperature"], "HIGH_TEMP", True), (st["min_temperature"], "LOW_TEMP", False)):
        if abs(slope) > 1e-6 and ((up and slope > 0) or (not up and slope < 0)) and (ys[-1] <= lim if up else ys[-1] >= lim):
            x = (lim - ic) / slope
            if 0 < x <= 1: res.update(will_breach=True, breach_type=typ, minutes_to_breach=round(x * 60))
    return jsonify(res)

# ---------- edit / delete ----------
def own_or_mgr(b): return g.user["role"] in ("MANAGER", "ADMIN") or b["created_by"] == g.user["user_id"]
def chk_date(h):
    if h:
        try: datetime.strptime(h, "%Y-%m-%d")
        except ValueError: raise ValueError("harvest_date must be YYYY-MM-DD")
        if h > now()[:10]: raise ValueError("harvest_date cannot be in the future")
@app.put("/api/batches/<int:bid>")
@auth("FARMER", "MANAGER")
def edit_batch(bid):
    b = one("SELECT * FROM batches WHERE batch_id=?", bid)
    if not b: return err("Batch not found", 404)
    if not own_or_mgr(b): return err("You can only edit your own batches", 403)
    d = request.json or {}; qty = num(d.get("quantity", b["quantity"]), "quantity", 0.01); h = d.get("harvest_date", b["harvest_date"]); chk_date(h)
    pid = int(d.get("product_id", b["product_id"]))
    if not one("SELECT 1 x FROM products WHERE product_id=?", pid): return err("Unknown product")
    if b["storage_unit_id"] and qty != b["quantity"]:
        st = one("SELECT * FROM storage_units WHERE storage_id=?", b["storage_unit_id"])
        if st["current_occupancy"] - b["quantity"] + qty > st["capacity"]: return err("Not enough capacity in storage unit")
        ex("UPDATE storage_units SET current_occupancy=current_occupancy-?+? WHERE storage_id=?", b["quantity"], qty, b["storage_unit_id"])
    ex("UPDATE batches SET quantity=?,harvest_date=?,product_id=? WHERE batch_id=?", qty, h, pid, bid); ev(bid, "EDITED", None, f"Edited: qty {qty:g}, harvest {h}", g.user["user_id"])
    return jsonify(ok=True, warning=check_placement(bid))
@app.delete("/api/batches/<int:bid>")
@auth("FARMER", "MANAGER")
def del_batch(bid):
    b = one("SELECT * FROM batches WHERE batch_id=?", bid)
    if not b: return err("Batch not found", 404)
    if not own_or_mgr(b): return err("You can only delete your own batches", 403)
    if b["storage_unit_id"]: ex("UPDATE storage_units SET current_occupancy=MAX(0,current_occupancy-?) WHERE storage_id=?", b["quantity"], b["storage_unit_id"])
    for s in q("SELECT shipment_id FROM shipments WHERE batch_id=?", bid): ex("DELETE FROM shipment_events WHERE shipment_id=?", s["shipment_id"])
    for t in ("shipments", "risk_assessments", "batch_events", "alerts", "sensor_readings"): ex(f"DELETE FROM {t} WHERE batch_id=?", bid)
    ex("DELETE FROM batches WHERE batch_id=?", bid); return jsonify(ok=True)
def plim(d):
    mn, mx = num(d.get("min_temperature"), "min_temperature", -40, 60, False), num(d.get("max_temperature"), "max_temperature", -40, 60, False)
    if (mn is None) != (mx is None) or (mn is not None and mn >= mx): raise ValueError("Give both min and max temperature (min below max)")
    return mn, mx, num(d.get("min_humidity"), "min_humidity", 0, 100, False), num(d.get("max_humidity"), "max_humidity", 0, 100, False), num(d.get("shelf_life_days"), "shelf_life_days", 0.1, 1000, False)
@app.put("/api/products/<int:pid>")
@auth("MANAGER")
def edit_product(pid):
    p = one("SELECT * FROM products WHERE product_id=?", pid)
    if not p: return err("Product not found", 404)
    d = {**p, **(request.json or {})}; l = plim(d)
    try: ex("UPDATE products SET product_name=?,category=?,unit=?,description=?,min_temperature=?,max_temperature=?,min_humidity=?,max_humidity=?,shelf_life_days=? WHERE product_id=?", d["product_name"], d.get("category"), d.get("unit") or "KG", d.get("description"), *l, pid)
    except sqlite3.IntegrityError: return err("Product name already exists")
    return jsonify(ok=True)
@app.delete("/api/products/<int:pid>")
@auth("MANAGER")
def del_product(pid):
    if one("SELECT 1 x FROM batches WHERE product_id=?", pid): return err("Cannot delete: batches use this product")
    ex("DELETE FROM products WHERE product_id=?", pid); return jsonify(ok=True)
@app.put("/api/storage/<int:sid>")
@auth("MANAGER")
def edit_storage(sid):
    s = one("SELECT * FROM storage_units WHERE storage_id=?", sid)
    if not s: return err("Storage not found", 404)
    d = {**s, **(request.json or {})}; mn, mx = num(d["min_temperature"], "min_temperature", -40, 60), num(d["max_temperature"], "max_temperature", -40, 60)
    if mn >= mx: return err("min_temperature must be below max_temperature")
    cap = num(d["capacity"], "capacity", 1)
    if cap < s["current_occupancy"]: return err("Capacity is below current occupancy")
    if d.get("status") not in ("ACTIVE", "MAINTENANCE", "INACTIVE"): return err("Invalid status")
    ex("UPDATE storage_units SET storage_name=?,location=?,capacity=?,min_temperature=?,max_temperature=?,min_humidity=?,max_humidity=?,status=?,lat=?,lng=? WHERE storage_id=?", d["storage_name"], d.get("location"), cap, mn, mx, num(d.get("min_humidity"), "min_humidity", 0, 100, False), num(d.get("max_humidity"), "max_humidity", 0, 100, False), d["status"], num(d.get("lat"), "lat", -90, 90, False), num(d.get("lng"), "lng", -180, 180, False), sid)
    return jsonify(ok=True)
@app.delete("/api/storage/<int:sid>")
@auth("MANAGER")
def del_storage(sid):
    if one("SELECT 1 x FROM batches WHERE storage_unit_id=?", sid): return err("Cannot delete: batches are stored here. Move them first.")
    for t in ("sensor_readings", "alerts", "devices"): ex(f"DELETE FROM {t} WHERE storage_id=?", sid)
    ex("DELETE FROM storage_units WHERE storage_id=?", sid); return jsonify(ok=True)

# ---------- password ----------
@app.post("/api/password/change")
@auth()
def pw_change():
    d = request.json or {}; u = one("SELECT * FROM users WHERE user_id=?", g.user["user_id"])
    if not check_password_hash(u["password_hash"], d.get("old_password", "")): return err("Current password is wrong")
    if len(d.get("new_password", "")) < 6: return err("New password must be at least 6 characters")
    ex("UPDATE users SET password_hash=? WHERE user_id=?", generate_password_hash(d["new_password"]), u["user_id"]); return jsonify(ok=True)
@app.post("/api/password/forgot")
def pw_forgot():
    em = str((request.json or {}).get("email", "")).lower(); u = one("SELECT * FROM users WHERE email=?", em); res = {"message": "If that email exists, a reset code was sent (valid 15 minutes)."}
    if u:
        code = f"{secrets.randbelow(10 ** 6):06d}"; ex("DELETE FROM password_resets WHERE email=?", em)
        ex("INSERT INTO password_resets(email,code_hash,expires_at) VALUES(?,?,?)", em, generate_password_hash(code), (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat())
        print(f"PASSWORD RESET CODE for {em}: {code}")  # also emailed when SMTP is configured
        threading.Thread(target=send_external, args=([em], [], f"Your Fasal Setu reset code: {code}"), daemon=True).start()
        if os.environ.get("AGRI_DEV_SHOW_CODE") == "1": res["dev_code"] = code
    return jsonify(res)
@app.post("/api/password/reset")
def pw_reset():
    d = request.json or {}; em = str(d.get("email", "")).lower(); r = one("SELECT * FROM password_resets WHERE email=?", em)
    if not r or P(r["expires_at"]) < datetime.now(timezone.utc) or not check_password_hash(r["code_hash"], str(d.get("code", ""))): return err("Invalid or expired code")
    if len(d.get("new_password", "")) < 6: return err("New password must be at least 6 characters")
    ex("UPDATE users SET password_hash=? WHERE email=?", generate_password_hash(d["new_password"]), em); ex("DELETE FROM password_resets WHERE email=?", em); return jsonify(ok=True)

# ---------- devices (ESP32 / Raspberry Pi), door/power, offline detection ----------
@app.get("/api/devices")
@auth()
def devices():
    rows = q("SELECT d.device_id,d.storage_id,d.device_name,d.battery_pct,d.signal_dbm,d.last_seen,d.offline_after_min,d.created_at,s.storage_code FROM devices d JOIN storage_units s USING(storage_id)")
    for r in rows: r["online"] = (datetime.now(timezone.utc) - P(r["last_seen"] or r["created_at"])).total_seconds() / 60 <= r["offline_after_min"] and bool(r["last_seen"])
    return jsonify(rows)
@app.post("/api/devices")
@auth("MANAGER")
def add_device():
    d = request.json or {}; sid = int(num(d.get("storage_id"), "storage_id"))
    if not one("SELECT 1 x FROM storage_units WHERE storage_id=?", sid): return err("Unknown storage unit")
    key = secrets.token_hex(16); ex("INSERT INTO devices(storage_id,device_name,api_key,offline_after_min,created_at) VALUES(?,?,?,?,?)", sid, d.get("device_name") or "Sensor", key, int(num(d.get("offline_after_min") or 30, "offline_after_min", 1, 1440)), now())
    return jsonify(api_key=key)
@app.delete("/api/devices/<int:did>")
@auth("MANAGER")
def del_device(did): ex("DELETE FROM devices WHERE device_id=?", did); return jsonify(ok=True)
@app.post("/api/device/reading")
def device_reading():
    dv = one("SELECT * FROM devices WHERE api_key=?", request.headers.get("X-Device-Key", ""))
    if not dv: return err("Invalid device key", 401)
    d = request.json or {}; sid = dv["storage_id"]; t = num(d.get("temperature"), "temperature"); h = num(d.get("humidity"), "humidity", req=False)
    bat, sig = num(d.get("battery"), "battery", 0, 100, False), num(d.get("signal"), "signal", -150, 0, False)
    ex("UPDATE devices SET last_seen=?,battery_pct=COALESCE(?,battery_pct),signal_dbm=COALESCE(?,signal_dbm) WHERE device_id=?", now(), bat, sig, dv["device_id"])
    auto_resolve(sid, ["NO_DATA"])
    if "door_open" in d: device_state(sid, "DOOR_OPEN", bool(d["door_open"]), "MEDIUM", "Door is open")
    if "power_ok" in d: device_state(sid, "POWER_CUT", not d["power_ok"], "HIGH", "Power cut detected")
    if bat is not None: device_state(sid, "LOW_BATTERY", bat < 15, "LOW", f"Sensor '{dv['device_name']}' battery {bat:g}%")
    rid, al = process_reading(sid, t, h, "SENSOR")
    return jsonify(reading_id=rid, alerts_created=len(al))
@app.post("/api/events/device")
@auth("MANAGER")
def manual_event():
    d = request.json or {}; sid = int(num(d.get("storage_id"), "storage_id")); typ = d.get("type")
    m = {"DOOR_OPEN": ("DOOR_OPEN", True, "MEDIUM", "Door opened"), "DOOR_CLOSED": ("DOOR_OPEN", False, "", ""), "POWER_CUT": ("POWER_CUT", True, "HIGH", "Power cut"), "POWER_RESTORED": ("POWER_CUT", False, "", "")}.get(typ)
    if not m or not one("SELECT 1 x FROM storage_units WHERE storage_id=?", sid): return err("Invalid storage or type")
    device_state(sid, m[0], m[1], m[2], m[3]); return jsonify(ok=True)
def check_offline():
    with app.app_context():
        for d in q("SELECT d.*,s.storage_code FROM devices d JOIN storage_units s USING(storage_id)"):
            if (datetime.now(timezone.utc) - P(d["last_seen"] or d["created_at"])).total_seconds() / 60 > d["offline_after_min"]:
                raise_alert(d["storage_id"], "NO_DATA", "HIGH", f"{d['storage_code']}: sensor '{d['device_name']}' sent no data for over {d['offline_after_min']} min")
        close(None)
def _watch():
    import time
    while True:
        time.sleep(60)
        try: check_offline()
        except Exception as e: print("watcher error:", e)

# ---------- verdict, scan, audit ----------
def verdict(bid):
    b = one("SELECT p.min_temperature lo,p.max_temperature hi FROM batches b JOIN products p USING(product_id) WHERE b.batch_id=?", bid)
    r = one("SELECT risk_level FROM risk_assessments WHERE batch_id=? ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1", bid)
    tt = q("SELECT e.temperature t FROM shipment_events e JOIN shipments s USING(shipment_id) WHERE s.batch_id=? AND e.temperature IS NOT NULL", bid); lo, hi = b["lo"], b["hi"]
    bad = [x for x in tt if lo is not None and (x["t"] < lo - 2 or x["t"] > hi + 2)]; lvl = r["risk_level"] if r else "LOW"
    return dict(status="FAIL" if lvl == "HIGH" or bad else "CAUTION" if lvl == "MEDIUM" else "PASS", risk_level=lvl, transit_readings=len(tt), transit_excursions=len(bad), product_range=[lo, hi], transit_tolerance_c=2)
@app.get("/api/scan/<code>")
@auth()
def scan_lookup(code):
    b = one(BSQL + " WHERE b.batch_code=?", code.upper())
    return jsonify(b) if b else err("Batch not found", 404)
@app.post("/api/batches/<code>/scan")
@auth("FARMER", "MANAGER", "TRANSPORTER")
def scan_event(code):
    b = one("SELECT batch_id FROM batches WHERE batch_code=?", code.upper()); d = request.json or {}
    if not b: return err("Batch not found", 404)
    if d.get("event_type") not in ("RECEIVED", "INSPECTED", "LOADED", "UNLOADED"): return err("Invalid event_type")
    ev(b["batch_id"], d["event_type"], d.get("location"), d.get("note") or "Scanned via QR", g.user["user_id"]); return jsonify(ok=True)
@app.get("/api/audit")
@auth("MANAGER")
def audit_list(): return jsonify(q("SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT 150"))
@app.after_request
def audit_hook(resp):
    if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api/") and resp.status_code < 400 and g.get("user") and "notifications" not in request.path:
        try:
            b = request.get_json(silent=True); b = {k: ("***" if "password" in k else v) for k, v in b.items()} if isinstance(b, dict) else {}
            ex("INSERT INTO audit_log(user_id,user_name,method,path,detail,created_at) VALUES(?,?,?,?,?,?)", g.user["user_id"], g.user["full_name"], request.method, request.path, json.dumps(b)[:300], now())
        except Exception: pass
    return resp

# ---------- exports: CSV, PDF report, QR labels, certificate ----------
def csv_resp(name, rows, cols):
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(cols)
    for r in rows: w.writerow([r.get(c) for c in cols])
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={name}.csv"})
@app.get("/api/export/<kind>.csv")
@auth()
def export_csv(kind):
    if kind == "batches": return csv_resp(kind, q(BSQL + " ORDER BY b.batch_id"), ["batch_code", "product_name", "quantity", "unit", "harvest_date", "received_date", "current_status", "storage_code", "risk"])
    if kind == "alerts": return csv_resp(kind, q("SELECT * FROM alerts ORDER BY created_at DESC"), ["alert_id", "storage_id", "alert_type", "severity", "message", "created_at", "status"])
    if kind == "readings":
        sid = request.args.get("storage_id")
        rows = q("SELECT r.*,s.storage_code FROM sensor_readings r JOIN storage_units s USING(storage_id)" + (" WHERE r.storage_id=?" if sid else "") + " ORDER BY recorded_at DESC LIMIT 5000", *([sid] if sid else []))
        return csv_resp(kind, rows, ["storage_code", "recorded_at", "temperature", "humidity", "source", "quality_status"])
    return err("Unknown export", 404)
def pdf_doc(fname, title, parts):
    buf = io.BytesIO(); doc = SimpleDocTemplate(buf, pagesize=A4, title=title, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm); st = getSampleStyleSheet(); flow = [Paragraph(xesc(title), st["Title"])]
    for p in parts:
        if isinstance(p, str): flow.append(Paragraph(xesc(p), st["BodyText"]))
        elif p[0] == "h": flow.append(Paragraph(xesc(p[1]), st["Heading2"]))
        else:
            t = Table([[xesc(str(c))[:70] for c in row] for row in p[1]], repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b7a75")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), .3, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8)])); flow += [t, Spacer(1, 6)]
    doc.build(flow); return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename={fname}"})
@app.get("/api/export/report.pdf")
@auth()
def report_pdf():
    r = report.__wrapped__().get_json() if hasattr(report, "__wrapped__") else {}; T = r["totals"]
    return pdf_doc("Fasal Setu-report.pdf", "Fasal Setu Cold Chain Report", [f"Generated {now()[:16].replace('T', ' ')} UTC", ("h", "Summary"), ("t", [["Batches", "Storage units", "Active alerts", "Shipments"], [T["batches"], T["storage"], T["open_alerts"], T["shipments"]]]),
        ("h", "Recommendations")] + r["recommendations"] + [("h", "Storage utilisation"), ("t", [["Code", "Name", "Occupancy/Capacity", "Limits C"]] + [[s["storage_code"], s["storage_name"], f"{s['current_occupancy']:g}/{s['capacity']:g}", f"{s['min_temperature']:g}-{s['max_temperature']:g}"] for s in r["storage"]]),
        ("h", "Active alerts"), ("t", [["Time", "Type", "Severity", "Message"]] + [[a["created_at"][:16], a["alert_type"], a["severity"], a["message"]] for a in q("SELECT * FROM alerts WHERE status!='RESOLVED' ORDER BY created_at DESC LIMIT 40")]),
        ("h", "Batches"), ("t", [["Code", "Product", "Qty", "Status", "Risk"]] + [[b["batch_code"], b["product_name"], b["quantity"], b["current_status"], b["risk"] or "-"] for b in q(BSQL + " ORDER BY b.batch_id")])])
@app.get("/api/labels.pdf")
@auth()
def labels_pdf():
    ids = [int(i) for i in request.args.get("ids", "").split(",") if i.strip().isdigit()]
    rows = q(BSQL + (f" WHERE b.batch_id IN ({','.join('?' * len(ids))})" if ids else " WHERE b.current_status!='DELIVERED'"), *ids)
    buf = io.BytesIO(); c = rl_canvas.Canvas(buf, pagesize=A4); W, H = A4; cw, ch = W / 3, H / 8
    for i, b in enumerate(rows):
        if i and i % 24 == 0: c.showPage()
        col, row = (i % 24) % 3, (i % 24) // 3; x, y = col * cw, H - (row + 1) * ch
        c.setStrokeColor(colors.lightgrey); c.rect(x + 3, y + 3, cw - 6, ch - 6)
        w = QrCodeWidget(request.host_url + "?trace=" + b["batch_code"]); bb = w.getBounds(); sz = 22 * mm; d = Drawing(sz, sz, transform=[sz / (bb[2] - bb[0]), 0, 0, sz / (bb[3] - bb[1]), 0, 0]); d.add(w); renderPDF.draw(d, c, x + 8, y + 8)
        c.setFont("Helvetica-Bold", 10); c.drawString(x + 8 + sz + 6, y + ch - 24, b["batch_code"]); c.setFont("Helvetica", 8)
        c.drawString(x + 8 + sz + 6, y + ch - 36, f"{b['product_name']} {b['quantity']:g} {b['unit']}"); c.drawString(x + 8 + sz + 6, y + ch - 47, f"Harvest: {b['harvest_date'] or '-'}"); c.drawString(x + 8 + sz + 6, y + ch - 58, "Scan to trace")
    c.save(); return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": "attachment; filename=qr-labels.pdf"})
@app.get("/api/shipments/<int:sid>/certificate.pdf")
@auth()
def certificate(sid):
    s = one("SELECT s.*,b.batch_code,b.batch_id,b.quantity,p.product_name,p.min_temperature lo,p.max_temperature hi FROM shipments s JOIN batches b USING(batch_id) JOIN products p USING(product_id) WHERE s.shipment_id=?", sid)
    if not s: return err("Shipment not found", 404)
    ev_ = q("SELECT * FROM shipment_events WHERE shipment_id=? ORDER BY recorded_at", sid); v = verdict(s["batch_id"])
    ok = v["status"] != "FAIL" and s["status"] == "DELIVERED"
    return pdf_doc(f"cold-chain-certificate-{s['batch_code']}.pdf", "Cold Chain Certificate", [("h", "COLD CHAIN INTACT" if ok else ("COLD CHAIN BREACH" if v["status"] == "FAIL" else "SHIPMENT NOT YET DELIVERED")),
        f"Batch {s['batch_code']} - {s['product_name']} - {s['quantity']:g} kg. Route: {s['source_location'] or '-'} to {s['destination']}. Transporter: {s['transporter_name'] or '-'}.",
        f"Dispatch: {s['dispatch_time'] or '-'}  Arrival: {s['actual_arrival'] or '-'}. Product range: {s['lo']}-{s['hi']} C (transit tolerance +/-2 C). Storage risk: {v['risk_level']}. Transit readings: {v['transit_readings']}, excursions: {v['transit_excursions']}.",
        ("h", "Temperature log"), ("t", [["Time", "Event", "Location", "Temp C"]] + [[e["recorded_at"][:16], e["event_type"], e["location"] or "-", e["temperature"] if e["temperature"] is not None else "-"] for e in ev_]), "Generated by Fasal Setu (prototype). Limits are indicative."])

from advanced_features import register_advanced
register_advanced(app, auth, q, one, ex, now, assess, BSQL, process_reading, ev, num)

init_db()
if os.environ.get("AGRI_WATCHER", "1") == "1": threading.Thread(target=_watch, daemon=True).start()
if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
