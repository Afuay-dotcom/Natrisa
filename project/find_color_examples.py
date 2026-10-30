"""find_color_examples.py — หาตัวอย่างพิกัด/วันที่ "ครบทุกสี" ไว้ทดสอบและ Demo บอท
  ส่วน A: สีจากสถานีวัดน้ำ 🔴🟡🔵⚪ (หาจาก history.db ที่เก็บไว้ ได้ครบแน่นอนถ้ามีสถานีในสภาพนั้นจริง)
  ส่วน B: สีจากดาวเทียม 🔴🟡 ตอนส่งพิกัดสด (ใช้ชั้น 1 วันของ GISTDA ถ้าวันนี้มีข้อมูล)
รัน:  python find_color_examples.py
"""
import os, sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from history_store import DB_PATH
from thaiwater_station import get_valid_waterlevel, get_valid_critical_level, WARNING_BUFFER_METERS

load_dotenv()
THAI_MONTH = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม",
              "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]


def thai_date(iso):
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d} {THAI_MONTH[m]} {y + 543}"


# ---------------- ส่วน A: สถานี ----------------
con = sqlite3.connect(str(DB_PATH))
con.row_factory = sqlite3.Row
dates = [r[0] for r in con.execute("SELECT DISTINCT snapshot_date FROM station_reading ORDER BY 1 DESC")]
if not dates:
    raise SystemExit("history.db ยังไม่มีข้อมูลสถานี ให้รัน python daily_snapshot.py ก่อน")
print("วันที่มีข้อมูลสถานีใน DB:", ", ".join(dates[:7]))

groups = {"red": [], "yellow": [], "blue": [], "white": []}
for day in dates[:3]:                      # ดูย้อนหลังไม่เกิน 3 snapshot ล่าสุด
    d0 = datetime.strptime(day, "%Y-%m-%d").date()
    for r in con.execute("SELECT * FROM station_reading WHERE snapshot_date=?", (day,)):
        st = dict(r)
        lv = get_valid_waterlevel({"waterlevel_msl": st["waterlevel_msl"]})
        cr = get_valid_critical_level({"critical_level_msl": st["critical_level_msl"]})
        if st["lat"] is None or st["lng"] is None:
            continue
        if lv is None or cr is None:
            groups["white"].append((day, st, None, lv, cr)); continue
        try:
            rd = datetime.strptime((st["waterlevel_datetime"] or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            rd = None
        if rd is not None and rd < d0 - timedelta(days=1):        # ค่าเก่าเกิน ระบบนับเป็นขาว
            groups["white"].append((day, st, None, lv, cr)); continue
        gap = round(cr - lv, 2)
        key = "red" if gap <= 0 else "yellow" if gap <= WARNING_BUFFER_METERS else "blue"
        groups[key].append((day, st, gap, lv, cr))

icons = {"red": "🔴 แดง   (น้ำถึง/เกินระดับวิกฤต)", "yellow": "🟡 เหลือง (ต่ำกว่าวิกฤตไม่เกิน 0.5 ม.)",
         "blue": "🔵 น้ำเงิน (ปกติ)", "white": "⚪ ขาว   (ไม่มีค่า/ไม่มีเกณฑ์/ข้อมูลเก่า)"}
for key in ("red", "yellow", "blue", "white"):
    items = groups[key]
    # เรียงให้ตัวอย่างที่ "เห็นชัด" ขึ้นก่อน: แดงเอาที่เกินวิกฤตมากสุด เหลืองเอาที่ใกล้สุด
    if key == "red":
        items.sort(key=lambda x: x[2])
    elif key == "yellow":
        items.sort(key=lambda x: x[2])
    print(f"\n=== {icons[key]}: พบ {len(items)} สถานี ===")
    if not items:
        print("   (ไม่มีสถานีในสภาพนี้ใน 3 วันล่าสุด — ถ้าต้องใช้สีนี้ใน Demo ให้ใช้วิดีโอ/ภาพสำรอง หรือรอวันที่มีน้ำสูง)")
    for day, st, gap, lv, cr in items[:3]:
        extra = f"ต่ำกว่าวิกฤต {gap} ม." if gap is not None and gap > 0 else ("เกินวิกฤต" if gap is not None else "")
        print(f"   {st['station_name']} ({st['agency_name']}) ระดับ {lv} / วิกฤต {cr} {extra}  [snapshot {day}]")
        print(f"      ส่งพิกัด:        {st['lat']:.5f},{st['lng']:.5f}")
        print(f"      ถามย้อนหลัง:    {st['lat']:.5f},{st['lng']:.5f} วันที่ {thai_date(day)}")

# ---------------- ส่วน B: ดาวเทียมสด (ชั้น 1 วัน) ----------------
print("\n=== ส่วน B: ดาวเทียมชั้น 1 วัน (สำหรับส่งพิกัดสด 🔴/🟡) ===")
KEY = os.getenv("GISTDA_API_KEY")
if not KEY:
    print("ไม่พบ GISTDA_API_KEY ข้ามส่วนนี้")
else:
    import requests
    from shapely.geometry import shape, Point
    URL = "https://api-gateway.gistda.or.th/api/2.0/resources/features/flood/1day"
    feats, off = [], 0
    try:
        while off < 3000:
            r = requests.get(URL, headers={"API-Key": KEY},
                             params={"bbox": "97.3,5.6,105.7,20.5", "limit": 100, "offset": off}, timeout=60)
            page = r.json().get("features", [])
            if not page:
                break
            feats += page; off += len(page)
    except Exception as e:
        print("ดึงไม่สำเร็จ:", type(e).__name__)
    geoms = []
    for f in feats:
        try:
            geoms.append(shape(f["geometry"]))
        except Exception:
            pass
    print(f"ชั้น 1 วันตอนนี้มี {len(geoms)} แปลง (อ่านมาสูงสุด 3,000)")
    if not geoms:
        print("วันนี้ไม่มีน้ำท่วมในชั้น 1 วัน → ส่งพิกัดสดจะไม่เห็น 🔴/🟡 จากดาวเทียม (ปกติ ไม่ใช่บั๊ก)")
        print("แนะนำ: โชว์ 🔴 ดาวเทียมด้วยการถามย้อนหลัง (python show_sat.py) และ 🔴🟡🔵 ด้วยพิกัดสถานีจากส่วน A")
    else:
        g = max(geoms, key=lambda x: x.area)
        red = g.representative_point()
        print(f"🔴 อยู่ในพื้นที่น้ำท่วม:  {red.y:.5f},{red.x:.5f}")
        minx, miny, maxx, maxy = g.bounds
        found = None
        for dx, dy in ((0.03, 0), (-0.03, 0), (0, 0.03), (0, -0.03)):      # ~3 กม. ต้องไม่เกิน 5 กม. (bbox ของระบบ)
            cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
            base_x = maxx if dx > 0 else minx if dx < 0 else cx
            base_y = maxy if dy > 0 else miny if dy < 0 else cy
            p = Point(base_x + dx, base_y + dy)
            if not any(x.contains(p) for x in geoms):
                found = p; break
        if found:
            print(f"🟡 ใกล้พื้นที่น้ำท่วม:   {found.y:.5f},{found.x:.5f}   (ห่างขอบแปลง ~3 กม.)")
        else:
            print("🟡 หาจุดใกล้ที่ไม่อยู่ในแปลงอื่นไม่ได้ในรอบนี้")
