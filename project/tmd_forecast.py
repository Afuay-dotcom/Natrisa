"""
tmd_forecast.py
===============
ฝน "พยากรณ์ล่วงหน้า" 24 ชม. จาก TMD Weather Forecast API (nwpapi)
เอกสาร: https://data.tmd.go.th/nwpapi/doc/apidoc/forecast_location.html

*** เป็นค่าพยากรณ์จากแบบจำลอง (ความละเอียด 2 กม.) ไม่ใช่ฝนที่วัดได้จริง ***
ตั้งค่าใน .env:  TMD_TOKEN=<โทเคนของกรมอุตุฯ>   (ห้ามใส่ในโค้ดหรือแชต)
"""
import os
from datetime import datetime, timedelta, timezone

import requests

TMD_HOURLY_AT_URL = "https://data.tmd.go.th/nwpapi/v1/forecast/location/hourly/at"
REQUEST_TIMEOUT = 10
FORECAST_HOURS = 24
_BKK = timezone(timedelta(hours=7))

FORECAST_CAVEAT = "(พยากรณ์โดยกรมอุตุนิยมวิทยา ไม่ใช่ฝนที่ตกจริง)"


def _extract_forecasts(payload):
    """รองรับชื่อคีย์ที่เอกสารใช้ไม่ตรงกัน (WeatherForecasts / WeatherForcasts / WeatherForecast / weather_forecast)"""
    if not isinstance(payload, dict):
        return []
    items = (payload.get("WeatherForecasts") or payload.get("WeatherForcasts")
             or payload.get("WeatherForecast"))
    if items is None and isinstance(payload.get("weather_forecast"), dict):
        items = payload["weather_forecast"].get("locations")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return []
    fc = items[0].get("forecasts")
    return [f for f in fc if isinstance(f, dict)] if isinstance(fc, list) else []


def get_forecast_rain(lat, lng, hours=FORECAST_HOURS, get=requests.get, now=None, token=None):
    """คืน dict {'rain_mm','max_hour_mm','hours','level','from','to'} หรือ None ถ้าไม่มีโทเคน/ดึงไม่ได้"""
    from rainfall import classify_rain_24h
    token = token if token is not None else (os.getenv("TMD_TOKEN") or "").strip().strip('"\'')
    if not token:
        return None
    now = now or datetime.now(_BKK)
    now = now.astimezone(_BKK)
    params = {"lat": lat, "lon": lng, "fields": "rain", "duration": hours,
              "date": now.strftime("%Y-%m-%d"), "hour": now.hour}
    headers = {"accept": "application/json", "authorization": f"Bearer {token}"}
    try:
        resp = get(TMD_HOURLY_AT_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            print(f"[warn] TMD forecast HTTP {resp.status_code}")
            return None
        forecasts = _extract_forecasts(resp.json())
    except Exception as e:
        print(f"[warn] TMD forecast ล้มเหลว ({type(e).__name__})")  # ไม่พิมพ์ข้อความ error เพราะอาจมี URL/โทเคน
        return None
    vals, times = [], []
    for f in forecasts:
        v = (f.get("data") or {}).get("rain")
        if isinstance(v, (int, float)) and v >= 0:
            vals.append(float(v))
            times.append(f.get("time"))
    if not vals:
        return None
    total = sum(vals)
    return {"rain_mm": round(total, 1), "max_hour_mm": round(max(vals), 1), "hours": len(vals),
            "level": classify_rain_24h(total), "from": times[0], "to": times[-1]}


def format_forecast_rain_lines(fc):
    if not fc:
        return []
    lines = [f"🌦️ พยากรณ์ฝนล่วงหน้า {fc['hours']} ชม.: {fc['rain_mm']} มม. ({fc['level']}) "
             f"สูงสุด {fc['max_hour_mm']} มม./ชม. {FORECAST_CAVEAT}"]
    from rainfall import is_high_rain, RAIN_RISK_NOTE
    if is_high_rain(fc["level"]):
        lines.append(RAIN_RISK_NOTE)
    return lines