"""ดูว่าแต่ละแปลงน้ำท่วมของ GISTDA มี 'ฟิลด์วันที่' อะไรบ้าง (เพื่อหาทางทำข้อมูลย้อนหลังรายวัน)
รัน:  python diag_props.py        (ยิง API 2 ครั้ง ไม่พิมพ์ API key)
"""
import os, re, collections
import requests
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("GISTDA_API_KEY")
if not KEY:
    raise SystemExit("ไม่พบ GISTDA_API_KEY ใน .env")
BASE = "https://api-gateway.gistda.or.th/api/2.0/resources/features/flood/"
BBOX = "97.3,5.6,105.7,20.5"   # ทั้งประเทศ

for ep in ("3days", "30days"):
    try:
        r = requests.get(BASE + ep, headers={"API-Key": KEY}, params={"bbox": BBOX, "limit": 100}, timeout=60)
        d = r.json()
    except Exception as e:
        print(ep, "ล้มเหลว:", type(e).__name__); continue
    feats = d.get("features", [])
    print(f"\n=== {ep}: HTTP {r.status_code} | matched={d.get('numberMatched')} | ได้ {len(feats)} แปลง")
    if not feats:
        continue
    print("ฟิลด์ใน properties:", sorted(feats[0].get("properties", {}).keys()))
    print("ตัวอย่างแปลงแรก:")
    for k, v in feats[0].get("properties", {}).items():
        print(f"   {k}: {str(v)[:80]}")
    # หาฟิลด์ที่เห็นเป็นวันที่ และนับค่าที่ต่างกันในหน้านี้
    for k in feats[0].get("properties", {}):
        vals = [str(f.get("properties", {}).get(k)) for f in feats]
        if any(re.search(r"20\d{2}[-_/]?\d{2}[-_/]?\d{2}", v) for v in vals):
            c = collections.Counter(re.findall(r"20\d{2}[-_/]?\d{2}[-_/]?\d{2}", " ".join(vals)))
            print(f"   >> ฟิลด์ '{k}' มีวันที่:", dict(list(c.items())[:8]))