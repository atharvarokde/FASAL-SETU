"""Pretend to be an ESP32 / Raspberry Pi sensor (no hardware needed).
Usage: python hardware/device_simulator.py --key DEVICE_KEY [--url http://localhost:5000] [--count 20] [--every 2] [--spike]"""
import argparse, json, random, time, urllib.request
ap = argparse.ArgumentParser(); ap.add_argument("--key", required=True); ap.add_argument("--url", default="http://localhost:5000")
ap.add_argument("--count", type=int, default=20); ap.add_argument("--every", type=float, default=2); ap.add_argument("--base", type=float, default=14); ap.add_argument("--spike", action="store_true", help="drift upward to trigger alerts")
a = ap.parse_args(); t, bat = a.base, 95.0
for i in range(a.count):
    t += random.uniform(-.3, .3) + (0.5 if a.spike and i > a.count // 2 else 0); bat -= .2
    body = json.dumps({"temperature": round(t, 1), "humidity": round(random.uniform(88, 93), 1), "battery": round(bat, 1), "signal": random.randint(-80, -55), "door_open": False, "power_ok": True}).encode()
    r = urllib.request.Request(a.url + "/api/device/reading", data=body, headers={"Content-Type": "application/json", "X-Device-Key": a.key})
    print(i + 1, round(t, 1), urllib.request.urlopen(r, timeout=10).read().decode().strip()); time.sleep(a.every)
