# Fasal Setu — Cold Chain Monitoring, Traceability & Risk (v2)
Flask backend + SQLite database + mobile-friendly web app (PWA).

## Run
    pip install -r requirements.txt
    python app.py            # open http://localhost:5000
First run creates and seeds `fasalsetu.db`. Delete it to reset. Phone on same Wi-Fi: http://<computer-IP>:5000
(The camera QR scanner and "install app" need https or localhost; deploy online for those on a phone.)

## Demo logins
admin@agrichain.local / Admin@123 · manager@ , farmer@ , transporter@agrichain.local / Pass@123

## Tests
    pip install -r requirements-dev.txt
    python -m pytest tests -q        # 14 backend tests

## What's included
- Auth, roles (Admin/Farmer/Manager/Transporter), change password, forgot password (6-digit code, printed in the server console or emailed if SMTP is set)
- Batches, products (per-crop limits + shelf life), storage units: add / edit / delete with confirmation; capacity checks
- Wrong-storage warning when a batch's crop range doesn't fit the unit
- Search + filters (batches by status/product/risk, alerts by severity/status)
- Monitoring: manual/simulated/device readings, validation, history chart, forecast (breach in next hour)
- Alerts: high/low temp & humidity, rapid-rise, door open, power cut, no-data (sensor offline), low battery, wrong storage. Auto-resolve when back in range
- Notifications panel + email / SMS / WhatsApp (see below)
- Risk (rules-v2) + shelf-life model from temperature history (Q10=2); FEFO dispatch list
- Shipments + checkpoints with temperature and GPS, cold-chain certificate (PDF), map view
- QR traceability: public trace page with PASS/CAUTION/FAIL buyer verdict, printable QR label sheet (PDF), camera scanner that logs events
- Exports: CSV (batches, alerts, readings/monitoring history) and PDF report
- Dark/light theme, English / Hindi / Marathi (menus and main labels), audit log, installable PWA
- Hardware: `hardware/esp32_dht22.ino` and `hardware/device_simulator.py`

## Real sensors (ESP32 / Raspberry Pi)
1. Devices tab -> New device -> copy the key (shown once).
2. Flash `esp32_dht22.ino` (edit WiFi, SERVER, key) or test with
   `python hardware/device_simulator.py --key <KEY> --spike`
3. Device posts to `POST /api/device/reading` with header `X-Device-Key`. Silence longer than "offline after" raises a NO_DATA alert.

## Email / SMS / WhatsApp alerts (optional; in-app notifications always work)
Set environment variables before `python app.py`:
    SMTP_HOST, SMTP_PORT(587), SMTP_USER, SMTP_PASS, SMTP_FROM          # email
    TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM                               # SMS
    TWILIO_WHATSAPP_FROM=whatsapp:+14155238886                          # WhatsApp (Twilio sandbox)
Alerts of MEDIUM/HIGH severity go to MANAGER/ADMIN users (set a phone number in E.164 format, e.g. +919876543210). Without these, messages are printed in the console.
Tuning: RATE_LIMIT_C_PER_H (default 3), AGRI_DB (database path), PORT.

## Deploy online (Render / Railway)
`Procfile` and `render.yaml` are included (gunicorn, 1 worker, SQLite on a persistent disk). Set `AGRI_DB` to the disk path.

## Honest limits
- Database is SQLite. Converting to PostgreSQL is not done (would need a DB layer such as SQLAlchemy).
- No ML model: your roadmap says to wait for reliable, labelled, validated data. Use the CSV exports as the future dataset.
- Crop limits, shelf-life baselines and the Q10 model are indicative; confirm with experts.
- Offline PWA caches the app shell only; data needs a connection. Map tiles need internet.
- Hindi/Marathi cover menus and main labels; data and long paragraphs stay English.

## Tech League Advanced Edition

This ZIP includes a competition-focused **AI Command Center** layered onto the existing Flask + SQLite application.

### Added modules
- AI Command Center with KPI/risk overview
- Hybrid risk intelligence and spoilage-probability estimate
- Estimated remaining safe-life window
- AI recommendation engine with urgency/deadline
- Live anomaly detection
- Smart FEFO dispatch queue
- Digital Product Passport endpoint and UI
- Quality score
- Data-driven AgriChain AI assistant with no-API fallback
- Crop image demo-assisted screening with clear prototype disclaimer
- Smart storage recommendation
- Repeatable **90-second Tech League Demo Mode**
- Competition-oriented UI styling

### Run
1. Extract the ZIP.
2. Open the project folder in VS Code.
3. Open a terminal in the folder.
4. Create/activate a virtual environment if desired.
5. Install dependencies:
   `pip install -r requirements.txt`
6. Start:
   `python app.py`
7. Open:
   `http://localhost:5000`

### Demo login
- Admin: `admin@agrichain.local` / `Admin@123`
- Manager: `manager@agrichain.local` / `Pass@123`
- Farmer: `farmer@agrichain.local` / `Pass@123`
- Transporter: `transporter@agrichain.local` / `Pass@123`

### Competition demo
Login as Manager → **AI Command Center** → **Run 90-Second Demo**.

The demo intentionally uses simulated sensor escalation. The UI labels modelled/demo metrics as such; it does not claim a validated field ML model.

### Important distinction
The risk engine is a transparent hybrid/rule-based prototype using the project's actual sensor/database data. It is **not presented as a scientifically validated ML model**. A future production version should train and validate crop-specific models using labelled historical datasets.
