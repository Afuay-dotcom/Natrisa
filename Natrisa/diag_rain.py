"""ทดสอบ TMD forecast (ฝนรายชั่วโมง) โดยไม่พิมพ์โทเคน
ตั้งใน .env:  TMD_TOKEN=<โทเคนใหม่>   แล้วรัน:  python diag_rain.py
"""
from dotenv import load_dotenv
load_dotenv()
import os, requests
from tmd_forecast import TMD_HOURLY_AT_URL, get_forecast_rain

token = (os.getenv("TMD_TOKEN") or "").strip().strip('"\'')
if not token:
    raise SystemExit("ไม่พบ TMD_TOKEN ใน .env")
r = requests.get(TMD_HOURLY_AT_URL, params={"lat": 13.75, "lon": 100.5, "fields": "rain", "duration": 3},
                 headers={"accept": "application/json", "authorization": f"Bearer {token}"}, timeout=20)
print("HTTP:", r.status_code)
print("ต้นข้อความที่ตอบกลับ:", r.text)
print("ผลจากฟังก์ชันจริง:", get_forecast_rain(13.75, 100.5))
