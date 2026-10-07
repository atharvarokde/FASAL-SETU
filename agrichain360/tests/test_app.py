import os, sys, tempfile, sqlite3
from datetime import datetime, timezone, timedelta
import pytest
d = tempfile.mkdtemp()
os.environ.update(AGRI_DB=os.path.join(d, "t.db"), AGRI_WATCHER="0", AGRI_DEV_SHOW_CODE="1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A
C = A.app.test_client()
def login(u, p="Pass@123"): return {"Authorization": "Bearer " + C.post("/api/login", json={"email": f"{u}@agrichain.local", "password": p}).get_json()["token"]}
M, F, T, AD = login("manager"), login("farmer"), login("transporter"), login("admin", "Admin@123")
def iso(mins=0): return (datetime.now(timezone.utc) - timedelta(minutes=mins)).replace(microsecond=0).isoformat()
def alerts(**kw):
    return [a for a in C.get("/api/alerts", headers=M).get_json() if all(a[k] == v for k, v in kw.items())]
def new_storage(code, lo, hi):
    assert C.post("/api/storage", headers=M, json={"storage_code": code, "storage_name": code, "capacity": 1000, "min_temperature": lo, "max_temperature": hi, "min_humidity": 80, "max_humidity": 95}).status_code == 200
    return [s for s in C.get("/api/storage", headers=M).get_json() if s["storage_code"] == code][0]["storage_id"]

def test_roles_and_auth():
    assert C.post("/api/readings", headers=F, json={"storage_id": 1, "temperature": 5}).status_code == 403
    assert C.get("/api/batches").status_code == 401
    assert C.post("/api/login", json={"email": "x@y.z", "password": "bad"}).status_code == 401

def test_batch_crud_and_capacity():
    r = C.post("/api/batches", headers=F, json={"product_id": 2, "quantity": 100, "storage_id": 2}).get_json(); bid = r["batch_id"]
    occ = lambda: [s for s in C.get("/api/storage", headers=M).get_json() if s["storage_id"] == 2][0]["current_occupancy"]
    before = occ()
    assert C.post("/api/batches", headers=F, json={"product_id": 2, "quantity": 99999, "storage_id": 2}).status_code == 400
    assert C.put(f"/api/batches/{bid}", headers=F, json={"quantity": 150}).status_code == 200 and occ() == before + 50
    assert C.put(f"/api/batches/{bid}", headers=login("transporter"), json={"quantity": 1}).status_code == 403
    assert C.delete(f"/api/batches/{bid}", headers=F).status_code == 200 and occ() == before - 100

def test_product_storage_crud_and_blocks():
    assert C.delete("/api/products/1", headers=M).status_code == 400
    assert C.post("/api/products", headers=M, json={"product_name": "Grapes", "min_temperature": 0, "max_temperature": 2, "shelf_life_days": 20}).status_code == 200
    pid = [p for p in C.get("/api/products", headers=M).get_json() if p["product_name"] == "Grapes"][0]["product_id"]
    assert C.put(f"/api/products/{pid}", headers=M, json={"description": "green"}).status_code == 200
    assert C.delete(f"/api/products/{pid}", headers=M).status_code == 200
    sid = new_storage("DEL-1", 0, 5)
    assert C.put(f"/api/storage/{sid}", headers=M, json={"storage_name": "Renamed", "status": "ACTIVE"}).status_code == 200
    assert C.delete(f"/api/storage/{sid}", headers=M).status_code == 200
    assert C.delete("/api/storage/1", headers=M).status_code == 400

def test_wrong_storage_warning():
    r = C.post("/api/batches", headers=F, json={"product_id": 3, "quantity": 10, "storage_id": 1}).get_json()
    assert r["warning"] and alerts(alert_type="WRONG_STORAGE")

def test_alert_and_auto_resolve():
    sid = new_storage("AR-1", 2, 8)
    assert C.post("/api/readings", headers=M, json={"storage_id": sid, "temperature": 15, "humidity": 90}).get_json()["alerts_created"] == 1
    assert alerts(storage_id=sid, alert_type="HIGH_TEMP", status="OPEN")
    C.post("/api/readings", headers=M, json={"storage_id": sid, "temperature": 5, "humidity": 90})
    assert alerts(storage_id=sid, alert_type="HIGH_TEMP", status="RESOLVED") and not alerts(storage_id=sid, alert_type="HIGH_TEMP", status="OPEN")

def test_invalid_reading_flagged_no_alert():
    sid = new_storage("INV-1", 2, 8)
    assert C.post("/api/readings", headers=M, json={"storage_id": sid, "temperature": 999}).get_json()["alerts_created"] == 0
    assert C.get(f"/api/readings/{sid}", headers=M).get_json()[0]["quality_status"] == "INVALID"
    assert C.post("/api/readings", headers=M, json={"storage_id": sid, "temperature": "abc"}).status_code == 400

def test_rate_of_change_alert():
    sid = new_storage("ROC-1", 0, 10)
    with A.app.app_context():
        A.process_reading(sid, 2.0, 90, "SIMULATED", iso(30)); A.process_reading(sid, 6.0, 90, "SIMULATED", iso(0))
    assert alerts(storage_id=sid, alert_type="RAPID_RISE", status="OPEN")

def test_forecast_breach():
    sid = new_storage("FC-1", 0, 10)
    assert C.get(f"/api/forecast/{sid}", headers=M).get_json()["enough_data"] is False
    with A.app.app_context():
        for m, t in ((40, 2), (20, 4), (0, 6)): A.process_reading(sid, t, 90, "SIMULATED", iso(m))
    f = C.get(f"/api/forecast/{sid}", headers=M).get_json()
    assert f["will_breach"] and f["breach_type"] == "HIGH_TEMP" and 0 < f["minutes_to_breach"] <= 60

def test_device_flow_door_power_battery():
    sid = new_storage("DEV-1", 2, 8)
    key = C.post("/api/devices", headers=M, json={"storage_id": sid, "device_name": "ESP32-A"}).get_json()["api_key"]
    assert C.post("/api/device/reading", json={"temperature": 5}, headers={"X-Device-Key": "bad"}).status_code == 401
    H = {"X-Device-Key": key}
    assert C.post("/api/device/reading", headers=H, json={"temperature": 5, "humidity": 90, "battery": 10, "signal": -70, "door_open": True, "power_ok": False}).status_code == 200
    for t in ("DOOR_OPEN", "POWER_CUT", "LOW_BATTERY"): assert alerts(storage_id=sid, alert_type=t, status="OPEN"), t
    C.post("/api/device/reading", headers=H, json={"temperature": 5, "humidity": 90, "battery": 80, "door_open": False, "power_ok": True})
    for t in ("DOOR_OPEN", "POWER_CUT", "LOW_BATTERY"): assert alerts(storage_id=sid, alert_type=t, status="RESOLVED"), t
    dv = [x for x in C.get("/api/devices", headers=M).get_json() if x["storage_id"] == sid][0]
    assert dv["battery_pct"] == 80 and dv["online"]
    assert C.post("/api/events/device", headers=M, json={"storage_id": sid, "type": "DOOR_OPEN"}).status_code == 200

def test_offline_detection_then_recovery():
    sid = new_storage("OFF-1", 2, 8)
    key = C.post("/api/devices", headers=M, json={"storage_id": sid, "offline_after_min": 10}).get_json()["api_key"]
    con = sqlite3.connect(os.environ["AGRI_DB"]); con.execute("UPDATE devices SET last_seen=? WHERE storage_id=?", (iso(60), sid)); con.commit(); con.close()
    A.check_offline(); assert alerts(storage_id=sid, alert_type="NO_DATA", status="OPEN")
    C.post("/api/device/reading", headers={"X-Device-Key": key}, json={"temperature": 5}); assert alerts(storage_id=sid, alert_type="NO_DATA", status="RESOLVED")

def test_fefo_sorted_and_risk_model():
    f = C.get("/api/fefo", headers=M).get_json(); days = [x["remaining_days"] for x in f]
    assert f and days == sorted(days) and f[0]["priority"] == 1
    r = C.get("/api/risk/1", headers=M).get_json(); assert r["risk_level"] in ("LOW", "MEDIUM", "HIGH") and "Shelf-life model" in r["explanation"]

def test_exports():
    assert C.get("/api/export/batches.csv", headers=M).data.startswith(b"batch_code")
    assert b"alert_type" in C.get("/api/export/alerts.csv", headers=M).data
    assert b"temperature" in C.get("/api/export/readings.csv?storage_id=1", headers=M).data
    for u in ("/api/export/report.pdf", "/api/labels.pdf"):
        r = C.get(u, headers=M); assert r.status_code == 200 and r.data[:4] == b"%PDF", u

def test_password_change_and_reset():
    C.post("/api/register", json={"full_name": "Pw User", "email": "pw@x.com", "password": "oldpass1", "role": "FARMER"})
    H = {"Authorization": "Bearer " + C.post("/api/login", json={"email": "pw@x.com", "password": "oldpass1"}).get_json()["token"]}
    assert C.post("/api/password/change", headers=H, json={"old_password": "nope", "new_password": "newpass1"}).status_code == 400
    assert C.post("/api/password/change", headers=H, json={"old_password": "oldpass1", "new_password": "newpass1"}).status_code == 200
    code = C.post("/api/password/forgot", json={"email": "pw@x.com"}).get_json()["dev_code"]
    assert C.post("/api/password/reset", json={"email": "pw@x.com", "code": "000000", "new_password": "zzzzzz"}).status_code == 400
    assert C.post("/api/password/reset", json={"email": "pw@x.com", "code": code, "new_password": "resetpw1"}).status_code == 200
    assert C.post("/api/login", json={"email": "pw@x.com", "password": "resetpw1"}).status_code == 200
    assert "dev_code" not in C.post("/api/password/forgot", json={"email": "nobody@x.com"}).get_json()

def test_shipment_certificate_trace_verdict_scan_audit_notifications():
    b = C.post("/api/batches", headers=F, json={"product_id": 2, "quantity": 5, "storage_id": 2}).get_json()
    sh = C.post("/api/shipments", headers=T, json={"batch_id": b["batch_id"], "destination": "Mumbai"}).get_json()["shipment_id"]
    ev = lambda **k: C.post(f"/api/shipments/{sh}/event", headers=T, json=k).status_code
    assert ev(event_type="PICKED_UP", location="Pune", temperature=11, lat=18.5, lng=73.8) == 200 and ev(event_type="CHECKPOINT", temperature=12) == 200 and ev(event_type="DELIVERED", location="Mumbai") == 200
    r = C.get(f"/api/shipments/{sh}/certificate.pdf", headers=T); assert r.status_code == 200 and r.data[:4] == b"%PDF"
    t = C.get(f"/api/trace/{b['batch_code']}").get_json(); assert t["verdict"]["status"] in ("PASS", "CAUTION", "FAIL") and t["verdict"]["transit_readings"] == 2
    assert C.post(f"/api/batches/{b['batch_code']}/scan", headers=T, json={"event_type": "INSPECTED"}).status_code == 200
    assert C.get(f"/api/scan/{b['batch_code']}", headers=T).get_json()["batch_code"] == b["batch_code"]
    assert any("/shipments" in a["path"] for a in C.get("/api/audit", headers=M).get_json())
    assert C.get("/api/notifications", headers=M).get_json()["unread"] > 0
    C.post("/api/notifications/read", headers=M); assert C.get("/api/notifications", headers=M).get_json()["unread"] == 0
    assert C.get("/api/audit", headers=F).status_code == 403
