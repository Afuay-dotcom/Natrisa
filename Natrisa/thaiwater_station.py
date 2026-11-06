"""
thaiwater_station.py
=====================
ดึงข้อมูลสถานีวัดระดับน้ำแบบ real-time จาก Thaiwater (สทนช./สสน.)
แล้วหาสถานีที่ใกล้พิกัดผู้ใช้มากที่สุด

Endpoint นี้ไม่ใช่ public API ที่มีเอกสารทางการ — เป็น endpoint ที่หน้าเว็บ
nationalthaiwater.onwr.go.th ใช้เองในการโหลดข้อมูลขึ้นแผนที่ (แกะมาจาก
DevTools Network tab) ดังนั้นควรใช้ด้วยความระมัดระวัง:
  - ไม่ต้องใช้ API key
  - อาจเปลี่ยนแปลง/ปิดได้โดยไม่แจ้งล่วงหน้า เพราะไม่ใช่ contract สาธารณะ
  - ควรใส่ User-Agent header ให้เหมือน browser จริง เผื่อ server เช็ค
"""

import math
from datetime import datetime, timedelta, timezone

import requests

THAIWATER_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"

# ใส่ header ให้ดูเหมือน request จาก browser จริง เผื่อ server มีการเช็ค
# (CORS header ที่เห็นใน DevTools จำกัดไว้ที่ nationalthaiwater.onwr.go.th
#  แต่นั่นบังคับใช้แค่ในเบราว์เซอร์ ไม่ใช่ฝั่ง server-to-server เหมือนโค้ดนี้)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://nationalthaiwater.onwr.go.th/waterlevel",
}


def haversine_distance(lat1, lng1, lat2, lng2):
    """
    คำนวณระยะทางตรง (กิโลเมตร) ระหว่างจุด 2 จุดบนโลก ด้วยสูตร Haversine
    (แม่นยำกว่าการลบพิกัดตรงๆ เพราะโลกกลม)
    """
    R = 6371  # รัศมีโลก หน่วยกิโลเมตร

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def fetch_all_stations():
    """
    ดึงข้อมูลสถานีวัดระดับน้ำทั้งหมด (real-time) จาก Thaiwater
    คืนค่าเป็น list ของ dict แต่ละสถานี พร้อมข้อมูลระดับน้ำล่าสุด
    """
    resp = requests.get(THAIWATER_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    # โครงสร้างตามที่เห็นใน DevTools:
    # { waterlevel_data: { result: "OK", data: [ {...station1...}, {...station2...} ] } }
    section = payload.get("waterlevel_data") if isinstance(payload, dict) else None
    if not isinstance(section, dict):
        raise ValueError(f"รูปแบบข้อมูล Thaiwater ไม่ตรงที่คาด: {str(payload)[:200]}")
    if section.get("result") != "OK":
        d = section.get("data")
        msg = d.get("Message") or d.get("RespMessage") if isinstance(d, dict) else d
        raise ValueError(f"Thaiwater ฝั่งเซิร์ฟเวอร์ตอบ error (result={section.get('result')}): {str(msg)[:150]}")
    stations = section.get("data", [])
    if isinstance(stations, dict):  # กันกรณี API เปลี่ยนเป็น dict
        stations = list(stations.values())
    if not isinstance(stations, list):
        raise ValueError(f"Thaiwater data ไม่ใช่ list: {str(stations)[:200]}")
    # ตัดรายการที่ไม่ใช่ dict ทิ้ง (เช่น error string)
    return [x for x in stations if isinstance(x, dict)]


def find_nearest_station(lat: float, lng: float, stations: list, max_results: int = 1):
    """
    หาสถานีที่ใกล้พิกัด (lat, lng) มากที่สุด จาก list ของสถานีที่ได้จาก fetch_all_stations()

    คืนค่า list ของ dict สรุปข้อมูล เรียงจากใกล้สุดไปไกลสุด ตามจำนวน max_results
    """
    results = []

    for station_record in stations:
        station_info = station_record.get("station", {})

        station_lat = station_info.get("tele_station_lat")
        station_lng = station_info.get("tele_station_long")

        if station_lat is None or station_lng is None:
            continue  # ข้ามสถานีที่ไม่มีพิกัด (ข้อมูลไม่ครบ)

        distance_km = haversine_distance(lat, lng, station_lat, station_lng)

        station_name_th = (station_info.get("tele_station_name", {}).get("th") or "ไม่ทราบชื่อ").strip()

        # เกณฑ์ระดับน้ำวิกฤต ที่หน่วยงานเจ้าของสถานีกำหนดเอง (อาจเป็น None ถ้าไม่มีข้อมูล)
        # อ้างอิงจากสถิติที่เช็คแล้ว: มีข้อมูลนี้ราว 50.6% ของสถานีทั้งหมด
        # (สูงสุดในสถานีของ กฟผ. และกรมชลประทาน)
        critical_level_msl = station_info.get("critical_level_msl")

        agency_info = station_record.get("agency", {})
        agency_name_raw = agency_info.get("agency_name", {})
        agency_name_th = (
            (agency_name_raw.get("th") or "ไม่ทราบหน่วยงาน").strip()
            if isinstance(agency_name_raw, dict)
            else "ไม่ทราบหน่วยงาน"
        )

        results.append({
            "station_id": station_info.get("id"),
            "distance_km": round(distance_km, 2),
            "station_name": station_name_th,
            "station_lat": station_lat,
            "station_lng": station_lng,
            "waterlevel_datetime": station_record.get("waterlevel_datetime"),
            "waterlevel_msl": station_record.get("waterlevel_msl"),
            "diff_wl_bank": station_record.get("diff_wl_bank"),
            "diff_wl_bank_text": station_record.get("diff_wl_bank_text"),
            "river_name": station_record.get("river_name"),
            "critical_level_msl": critical_level_msl,
            "agency_name": agency_name_th,
        })

    # เรียงจากใกล้สุดไปไกลสุด
    results.sort(key=lambda r: r["distance_km"])

    return results[:max_results]


# ---------------------------------------------------------------------------
# จำแนกระดับความเสี่ยงของสถานี โดยอิงเกณฑ์วิกฤตที่หน่วยงานรัฐกำหนด
# ---------------------------------------------------------------------------

# ระยะกันชนสำหรับแจ้งเตือนล่วงหน้าก่อนถึงระดับวิกฤต (หน่วย: เมตร)
# *** ค่านี้ผู้จัดทำกำหนดขึ้นเอง ไม่ใช่เกณฑ์ทางการจากหน่วยงานใด ***
# ใช้เพื่อให้มีสถานะ "เฝ้าระวัง" ก่อนถึงจุดวิกฤตจริง จะได้แจ้งเตือน user ล่วงหน้า
WARNING_BUFFER_METERS = 0.5


# ---------------------------------------------------------------------------
# ตรวจคุณภาพข้อมูลสถานีก่อนนำไปจำแนกความเสี่ยง
# ---------------------------------------------------------------------------
# พบจาก snapshot จริง: (1) critical_level_msl = 0 หลายสถานี (หมายถึง "ไม่มีข้อมูล"
# แต่เก็บเป็น 0) ทำให้ระบบรายงานว่าเกินวิกฤตหลายสิบเมตร (2) บางสถานีมี
# waterlevel_msl = 32765.9 ซึ่งเป็นค่า sentinel ของเซนเซอร์ขัดข้อง

# ช่วงระดับน้ำที่เป็นไปได้ (ม.รทก.) — ค่าขอบเขตนี้ผู้จัดทำกำหนดเอง ไม่ใช่เกณฑ์ทางการ
# (จุดสูงสุดของไทย ดอยอินทนนท์ ~2,565 ม. / ใน snapshot ต่ำสุดที่เจอ ~ -6 ม.)
MIN_PLAUSIBLE_WATERLEVEL_MSL = -50.0
MAX_PLAUSIBLE_WATERLEVEL_MSL = 3000.0

# ข้อมูลเก่ากว่านี้ (ชั่วโมง) ไม่ควรจำแนกเป็น 🟢/🟡/🔴 — ค่านี้ผู้จัดทำกำหนดเอง
STALE_AFTER_HOURS = 24

_BANGKOK_TZ = timezone(timedelta(hours=7))  # ไทยไม่มี DST ใช้ offset คงที่ได้ ไม่ต้องพึ่ง tzdata


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_valid_waterlevel(station: dict):
    """คืนระดับน้ำ (float) ถ้าอยู่ในช่วงที่เป็นไปได้ ไม่งั้นคืน None (เช่น sentinel 32765.9)"""
    value = _to_float(station.get("waterlevel_msl"))
    if value is None:
        return None
    if not (MIN_PLAUSIBLE_WATERLEVEL_MSL <= value <= MAX_PLAUSIBLE_WATERLEVEL_MSL):
        return None
    return value


def get_valid_critical_level(station: dict):
    """คืนเกณฑ์วิกฤต (float) ถ้าใช้ได้จริง — ค่า 0 หรือติดลบถือว่า 'ไม่มีข้อมูล'"""
    value = _to_float(station.get("critical_level_msl"))
    if value is None or value <= 0:
        return None
    if value > MAX_PLAUSIBLE_WATERLEVEL_MSL:
        return None
    return value


def get_data_age_hours(station: dict, now=None):
    """อายุของข้อมูล (ชั่วโมง) จาก waterlevel_datetime — คืน None ถ้า parse ไม่ได้"""
    raw = station.get("waterlevel_datetime")
    if not raw:
        return None
    try:
        measured = datetime.fromisoformat(str(raw).strip().replace("Z", ""))
    except ValueError:
        return None
    if measured.tzinfo is None:
        measured = measured.replace(tzinfo=_BANGKOK_TZ)
    now = now or datetime.now(_BANGKOK_TZ)
    return (now - measured).total_seconds() / 3600


def classify_station_risk(station: dict) -> dict:
    """
    เทียบระดับน้ำปัจจุบัน (waterlevel_msl) กับเกณฑ์วิกฤตที่หน่วยงานเจ้าของสถานี
    กำหนดไว้เอง (critical_level_msl) แล้วจำแนกเป็น 3 สถานะ

    คืนค่า dict ที่บอกทั้งระดับ, emoji ที่ใช้แสดงผล, ข้อความอธิบาย,
    และ flag 'is_official_threshold' เพื่อบอกตรงๆ ว่าเกณฑ์ที่ใช้ตัดสิน
    มาจากหน่วยงานรัฐจริง (True) หรือเป็นระยะกันชนที่เราตั้งเอง (False)
    """
    agency = station.get("agency_name") or "หน่วยงานที่เกี่ยวข้อง"

    # 1) ค่าระดับน้ำต้องอ่านได้และสมเหตุสมผล (กัน sentinel 32765.9 ฯลฯ)
    current_val = get_valid_waterlevel(station)
    if current_val is None:
        return {
            "level": "unknown",
            "emoji": "⚪",
            "is_official_threshold": False,
            "text": "ค่าระดับน้ำจากสถานีนี้ผิดปกติหรือไม่มีข้อมูล (เซนเซอร์อาจขัดข้อง) "
                    "จึงไม่สามารถประเมินระดับน้ำได้",
        }

    # 2) ข้อมูลต้องไม่เก่าเกินไป ไม่งั้นสีเขียว/เหลืองจะทำให้เข้าใจผิดว่าเป็นสถานการณ์ปัจจุบัน
    age_hours = get_data_age_hours(station)
    if age_hours is not None and age_hours > STALE_AFTER_HOURS:
        return {
            "level": "unknown",
            "emoji": "⚪",
            "is_official_threshold": False,
            "text": f"ข้อมูลล่าสุดของสถานีนี้เก่ากว่า {STALE_AFTER_HOURS} ชั่วโมงแล้ว "
                    f"(อัปเดตล่าสุด {station.get('waterlevel_datetime')}) จึงไม่นำมาประเมินสถานะ",
        }

    # 3) เกณฑ์วิกฤตต้องเป็นค่าจริง (0 / ติดลบ = ไม่มีข้อมูล)
    critical_val = get_valid_critical_level(station)
    if critical_val is None:
        return {
            "level": "unknown",
            "emoji": "⚪",
            "is_official_threshold": False,
            "text": "สถานีนี้ไม่มีเกณฑ์ระดับน้ำวิกฤตที่หน่วยงานประกาศไว้ในระบบ "
                    "(รายงานได้เฉพาะระดับน้ำเท่านั้น)",
        }

    diff = critical_val - current_val  # ค่าบวก = ยังต่ำกว่าเกณฑ์วิกฤตอยู่กี่เมตร

    if diff < 0:
        # เกินเกณฑ์วิกฤตที่หน่วยงานกำหนดแล้วจริง — ข้อเท็จจริงจากตัวเลข sourced 100%
        return {
            "level": "วิกฤต",
            "emoji": "🔴",
            "is_official_threshold": True,
            "text": f"ระดับน้ำเกินเกณฑ์วิกฤตที่{agency}กำหนดไว้ {abs(diff):.2f} เมตร",
        }
    elif diff <= WARNING_BUFFER_METERS:
        # ยังไม่ถึงเกณฑ์ทางการ แต่ใกล้มาก — ใช้ระยะกันชนที่เรากำหนดเอง (ไม่ sourced)
        return {
            "level": "เฝ้าระวัง",
            "emoji": "🟡",
            "is_official_threshold": False,
            "text": f"ระดับน้ำใกล้ถึงเกณฑ์วิกฤตที่{agency}กำหนดไว้ (ห่างอีก {diff:.2f} เมตร) "
                    f"— สถานะนี้ประเมินจากระยะกันชนที่ผู้จัดทำระบบกำหนดเพื่อแจ้งเตือนล่วงหน้า",
        }
    else:
        # ยังห่างจากเกณฑ์วิกฤตทางการมาก — ข้อเท็จจริงจากตัวเลข sourced 100%
        return {
            "level": "ปกติ",
            "emoji": "🔵",  # ไม่ใช้ 🟢 เพื่อไม่ให้สื่อว่า "ปลอดภัย" (นี่คือสถานะแม่น้ำที่สถานีเดียว)
            "is_official_threshold": True,
            "text": f"ระดับน้ำยังต่ำกว่าเกณฑ์วิกฤตที่{agency}กำหนดไว้อยู่ {diff:.2f} เมตร "
                    f"(เป็นสถานะของแม่น้ำที่สถานีนี้เท่านั้น ไม่ได้ยืนยันว่าพื้นที่รอบข้างปลอดภัยจากน้ำท่วมขัง)",
        }


# ---------------------------------------------------------------------------
# ทดสอบรันตรงๆ
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_lat = 13.7563   # เปลี่ยนเป็นพิกัดที่ต้องการทดสอบได้
    test_lng = 100.5018

    print(f"กำลังดึงข้อมูลสถานีทั้งหมด...")
    all_stations = fetch_all_stations()
    print(f"ดึงมาได้ {len(all_stations)} สถานี")

    print(f"\nกำลังหาสถานีใกล้พิกัด lat={test_lat}, lng={test_lng} ...")
    nearest = find_nearest_station(test_lat, test_lng, all_stations, max_results=3)

    for i, station in enumerate(nearest, start=1):
        print(f"\n--- อันดับ {i} ---")
        for key, value in station.items():
            print(f"  {key}: {value}")