"""
daily_snapshot.py
=================
บันทึกลง SQLite วันละครั้ง: (1) ระดับน้ำสถานี Thaiwater ทั้งหมดของวันนี้ (2) สรุปน้ำท่วมจากดาวเทียม GISTDA ชั้น 3 วันล่าสุด (นับตามวันถ่ายภาพ/จังหวัด)

รันมือ:   python daily_snapshot.py
อัตโนมัติ: ใน n8n ใช้ Schedule Trigger (วันละครั้ง) -> HTTP Request POST
           http://127.0.0.1:8000/snapshot   (endpoint อยู่ใน process_api.py)
"""

from dotenv import load_dotenv

load_dotenv()

from history_store import save_station_snapshot, get_available_range
from thaiwater_station import fetch_all_stations
from satellite_history import sync_from_gistda, FULL_ENDPOINT

DAILY_ENDPOINT = "/features/flood/3days"  # รอบประจำวันดึง 3 วันล่าสุด (เร็ว) ภาพที่ขึ้นช้าก็ยังเก็บทัน


def run_snapshot(full: bool = False) -> dict:
    """เก็บ 2 อย่าง: ระดับน้ำสถานีของวันนี้ + สรุปน้ำท่วมจากดาวเทียมชั้น 3 วันล่าสุด
    แยก try ต่างหาก: ถ้าอย่างใดอย่างหนึ่งล้ม อีกอย่างยังเก็บต่อได้ และผลลัพธ์บอกชัดว่าอะไรล้ม"""
    result = {}

    try:
        saved = save_station_snapshot(fetch_all_stations())
        first, last = get_available_range()
        result.update(saved_rows=saved, first_date=first, last_date=last)
    except Exception as e:
        print(f"[error] snapshot สถานีล้มเหลว: {e}")
        result["station_error"] = str(e)

    try:
        result["satellite"] = sync_from_gistda(endpoint=FULL_ENDPOINT if full else DAILY_ENDPOINT)
    except Exception as e:
        print(f"[error] sync ดาวเทียมล้มเหลว: {e}")
        result["satellite_error"] = str(e)

    return result


if __name__ == "__main__":
    import sys
    # ปกติดึงดาวเทียมย้อนหลัง 3 วัน (เร็ว) | python daily_snapshot.py --full = ชั้น 30 วัน ใช้ดูภาพรวมเท่านั้น (แยกรายวันไม่ได้ จึงไม่เขียนลง satellite_daily)
    print(run_snapshot(full="--full" in sys.argv))
