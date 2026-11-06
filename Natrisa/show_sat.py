"""แสดงจังหวัด/จำนวนแปลงน้ำท่วมรายวันที่เก็บไว้ใน history.db (ใช้เลือกตัวอย่างสำหรับ Demo ย้อนหลัง)
รัน:  python show_sat.py
"""
import sqlite3
from history_store import DB_PATH
con = sqlite3.connect(str(DB_PATH))
rows = con.execute("SELECT date, province, feature_count FROM satellite_daily ORDER BY date, feature_count DESC").fetchall()
if not rows:
    raise SystemExit("ยังไม่มีข้อมูลดาวเทียมใน history.db")
cur = None
for d, p, n in rows:
    if d != cur:
        print(f"\n{d}"); cur = d
    print(f"   {p:<20} {n:>6} แปลง")
y, m, dd = rows[0][0].split("-")
print("\nตัวอย่างข้อความใน LINE (ใช้ปี พ.ศ.):")
for d, p, n in rows[:3]:
    yy, mm, d2 = d.split("-")
    print(f"   จังหวัด{p} วันที่ {int(d2)} ตุลาคม {int(yy)+543}")
