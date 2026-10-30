"""reset_satellite.py — สำรอง history.db แล้วล้างตาราง satellite_daily (ใช้ครั้งเดียวหลังแก้การสกัดวันที่)
ข้อมูลระดับน้ำสถานีไม่ถูกแตะ  วิธีใช้: python reset_satellite.py  แล้วตามด้วย python daily_snapshot.py --full"""
import shutil, sqlite3, time
from history_store import DB_PATH

bak = f"{DB_PATH}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(DB_PATH, bak)
print("สำรองไว้ที่", bak)
with sqlite3.connect(DB_PATH) as c:
    n = c.execute("DELETE FROM satellite_daily").rowcount
print(f"ลบ satellite_daily {n} แถวแล้ว — รัน python daily_snapshot.py --full ต่อได้เลย")
