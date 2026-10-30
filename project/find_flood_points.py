"""find_flood_points.py — หาจังหวัด + พิกัดที่ "มีน้ำท่วมจริง" จากชั้น 3 วันของ GISTDA ไว้ใช้สาธิตบอท
พิมพ์: ต่อวันที่ภาพ x จังหวัด = จำนวนแปลง + พิกัดตัวอย่างที่อยู่ในแปลงน้ำท่วม (ใช้ส่งทดสอบใน LINE ได้เลย)
และเทียบว่า history.db ของเรามีวันที่/จังหวัดนั้นหรือยัง   รัน:  python find_flood_points.py
"""
import os, re, sqlite3, collections
import requests
from dotenv import load_dotenv
from shapely.geometry import shape

load_dotenv()
KEY = os.getenv("GISTDA_API_KEY")
if not KEY:
    raise SystemExit("ไม่พบ GISTDA_API_KEY ใน .env")
URL = "https://api-gateway.gistda.or.th/api/2.0/resources/features/flood/3days"
BBOX = "97.3,5.6,105.7,20.5"
MAX_FEATURES = 6000

feats, offset = [], 0
while offset < MAX_FEATURES:
    r = requests.get(URL, headers={"API-Key": KEY}, params={"bbox": BBOX, "limit": 100, "offset": offset}, timeout=60)
    page = r.json().get("features", [])
    if not page:
        break
    feats += page
    offset += len(page)
print(f"ดึงได้ {len(feats)} แปลง")

best = {}                                  # (date, province) -> (area, lat, lng)
cnt = collections.Counter()
for f in feats:
    p = f.get("properties") or {}
    m = re.findall(r"(20\d{2})(\d{2})(\d{2})", p.get("file_name") or "")
    if len(set(m)) != 1:                   # ต้องระบุภาพวันเดียวเท่านั้น (เหมือนที่ระบบเก็บ)
        continue
    day = "-".join(m[0])
    prov = (p.get("pv_tn") or "ไม่ทราบ").replace("จ.", "").strip()
    cnt[(day, prov)] += 1
    try:
        pt = shape(f["geometry"]).representative_point()      # จุดที่อยู่ "ในแปลง" แน่นอน
    except Exception:
        continue
    area = float(p.get("_area") or p.get("f_area") or 0)
    if (day, prov) not in best or area > best[(day, prov)][0]:
        best[(day, prov)] = (area, pt.y, pt.x)

try:
    from history_store import DB_PATH
    with sqlite3.connect(DB_PATH) as c:
        have = {(d, p) for d, p in c.execute("select date, province from satellite_daily")}
except Exception:
    have = set()

print("\nวันที่ภาพ   จังหวัด            แปลง   พิกัดตัวอย่าง (อยู่ในแปลงน้ำท่วม)   อยู่ใน history.db?")
for (day, prov), n in sorted(cnt.items()):
    a, lat, lng = best.get((day, prov), (0, float("nan"), float("nan")))
    mark = "มี" if (day, prov) in have or (day, "จ." + prov) in have else "ยังไม่มี (รัน python daily_snapshot.py)"
    print(f"{day}  {prov:<16} {n:>5}   {lat:.5f}, {lng:.5f}    {mark}")
print("\nตัวอย่างข้อความทดสอบใน LINE: 'จังหวัด<ชื่อ> วันที่ <วัน> ตุลาคม 2569'  (ใช้ชื่อจังหวัดจากตารางด้านบน)")
