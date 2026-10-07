// Fasal Setu ESP32 + DHT22 sensor node.  Arduino IDE libraries: "DHT sensor library" (Adafruit), ArduinoJson not needed.
#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>
const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* SERVER    = "http://192.168.1.5:5000/api/device/reading";  // your computer's IP (use https URL when deployed online)
const char* DEVICE_KEY = "PASTE_DEVICE_KEY_FROM_DEVICES_TAB";
#define DHTPIN 4          // DHT22 data pin
#define DOOR_PIN 15       // reed switch to GND: LOW = door closed (optional)
#define BAT_PIN 34        // battery voltage via 2:1 divider (optional)
const unsigned long EVERY_MS = 60000;   // send once a minute
DHT dht(DHTPIN, DHT22);
void setup() {
  Serial.begin(115200); dht.begin(); pinMode(DOOR_PIN, INPUT_PULLUP);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println(WiFi.localIP());
}
void loop() {
  float t = dht.readTemperature(), h = dht.readHumidity();
  if (isnan(t) || isnan(h)) { Serial.println("DHT read failed"); delay(2000); return; }
  float vbat = analogRead(BAT_PIN) / 4095.0 * 3.3 * 2.0;            // volts
  int battery = constrain((int)((vbat - 3.3) / (4.2 - 3.3) * 100), 0, 100);  // Li-ion 3.3-4.2 V
  bool doorOpen = digitalRead(DOOR_PIN) == HIGH;
  String body = "{\"temperature\":" + String(t, 1) + ",\"humidity\":" + String(h, 1) + ",\"battery\":" + battery +
                ",\"signal\":" + WiFi.RSSI() + ",\"door_open\":" + (doorOpen ? "true" : "false") + ",\"power_ok\":true}";
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http; http.begin(SERVER); http.addHeader("Content-Type", "application/json"); http.addHeader("X-Device-Key", DEVICE_KEY);
    int code = http.POST(body); Serial.printf("POST %s -> %d\n", body.c_str(), code); http.end();
  }
  delay(EVERY_MS);
}
