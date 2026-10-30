"""
probe_gistda.py — ตรวจพฤติกรรมจริงของ GISTDA /flood/30days ก่อนจะดึงข้อมูลย้อนหลังเต็มรูปแบบ
(รันครั้งเดียว ยิงไม่กี่ครั้ง แล้วส่งผลที่พิมพ์ออกมาให้พี่ดู; ไม่พิมพ์ API key)

ตอบ 3 คำถาม:
  1) server ยอมให้ขอหน้าละเกิน 10 รายการไหม (limit=10/50/100/500/1000)  -> ถ้ายอม ดึงเร็วขึ้นหลายสิบเท่า
  2) ขอ offset เกินจำนวนจริงแล้วตอบอะไร (422 ที่เจอมาจากตรงนี้ไหม)
  3) แถบละติจูด 15.5-20.5 (เหนือ/อีสาน) ตอนนี้มีข้อมูลไหม (รอบล่าสุดได้ 0 ทั้งที่รอบก่อนได้ 3,345)
"""
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()
BASE = "https://api-gateway.gistda.or.th/api/2.0/resources/features/flood/30days"
KEY = os.getenv("GISTDA_API_KEY")
if not KEY:
    raise SystemExit("ไม่พบ GISTDA_API_KEY ใน .env")
HEADERS = {"API-Key": KEY}


def call(bbox, limit=None, offset=None):
    params = {"bbox": bbox}
    if limit is not None: params["limit"] = limit
    if offset is not None: params["offset"] = offset
    t0 = time.time()
    try:
        r = requests.get(BASE, headers=HEADERS, params=params, timeout=60)
    except requests.RequestException as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = {"status": r.status_code, "sec": round(time.time() - t0, 1)}
    try:
        d = r.json()
        out.update(matched=d.get("numberMatched"), returned=d.get("numberReturned"),
                   n_features=len(d.get("features", [])), keys=sorted(d.keys()))
        if r.status_code >= 400:
            out["body"] = str(d)[:200]
    except ValueError:
        out["body"] = r.text[:200]
    return out


SMALL = "103.5,16.8,104.3,17.6"      # สกลนคร (พื้นที่เล็ก)
print("== 1) ลองขอหน้าละกี่รายการ (bbox สกลนคร) ==")
for lim in (None, 10, 50, 100, 500, 1000):
    print(f"limit={lim}:", call(SMALL, limit=lim, offset=0))

print("\n== 2) offset เกินจำนวนจริง ==")
base = call(SMALL, limit=10, offset=0)
m = base.get("matched")
print("numberMatched =", m)
if isinstance(m, int):
    for off in (m - 1, m, m + 10, m + 1000):
        print(f"offset={off}:", call(SMALL, limit=10, offset=off))

print("\n== 3) แถบละติจูดเหนือ/อีสาน ตอนนี้มีข้อมูลไหม ==")
for name, bb in (("แถบ 5 (15.5-18.0)", "97.3,15.5303,105.7,18.0152"),
                 ("แถบ 6 (18.0-20.5)", "97.3,18.0152,105.7,20.5")):
    print(name, call(bb, limit=10, offset=0))
