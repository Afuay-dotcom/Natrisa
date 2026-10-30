"""
flood_check.py
==============
ตรวจสอบว่าพิกัด (lat, lng) ที่ผู้ใช้ส่งมาจาก LINE อยู่ในพื้นที่น้ำท่วม
ตามข้อมูลจาก GISTDA Open API หรือไม่

ตั้งค่า API key ใน .env (ห้ามเขียน key ลงในโค้ด):
    GISTDA_API_KEY=your_key_here

ใช้ shapely สำหรับ point-in-polygon:  pip install shapely

สิ่งที่แก้จากเวอร์ชันก่อน
-------------------------
1) ดึงข้อมูลแบบแบ่งหน้า (limit/offset) — API ตอบทีละ 10 รายการ (เคยเจอ
   numberMatched=94 แต่ numberReturned=10) เวอร์ชันเก่าเช็คแค่หน้าแรก
   ถ้าจุดผู้ใช้อยู่ใน polygon ลำดับที่ 11+ จะพลาด
   *ชื่อพารามิเตอร์ limit/offset ยังไม่ได้ยืนยันกับเอกสาร GISTDA* ถ้า API ไม่รองรับ
   โค้ดจะตรวจเจอ (ได้หน้าซ้ำ) แล้วหยุด พร้อมตั้ง flag incomplete=True
2) เช็คว่าจุดอยู่ใน polygon "ทุก endpoint" ไล่จากใหม่สุดไปเก่าสุด และหยุดที่อันแรกที่เจอ
   เวอร์ชันเก่าหยุดที่ endpoint แรกที่มี polygon อยู่ใกล้ๆ (แม้ไม่ครอบจุด) เลยไม่เคย
   ได้เช็ค /7days หรือ /30days ที่อาจครอบจุดนั้นจริง
3) เรียก API ล้มเหลวทุก endpoint → risk = "error" (เดิมถูกรายงานเหมือน "ไม่มีน้ำท่วม")
"""

import json
import os

import requests
from dotenv import load_dotenv
from shapely.geometry import Point, shape

load_dotenv()  # โหลด .env เองในไฟล์นี้ ไม่พึ่งว่าโมดูลอื่นจะ import ก่อน
BASE_URL = "https://api-gateway.gistda.or.th/api/2.0/resources"

# เรียงจากข้อมูลใหม่สุด -> เก่าสุด
FLOOD_ENDPOINTS = [
    ("/features/flood/1day", "พื้นที่น้ำท่วม 1 วันล่าสุด"),
    ("/features/flood/3days", "พื้นที่น้ำท่วมในรอบ 3 วันล่าสุด"),
    ("/features/flood/7days", "พื้นที่น้ำท่วมในรอบ 7 วันล่าสุด"),
    ("/features/flood/30days", "พื้นที่น้ำท่วมในรอบ 30 วันล่าสุด"),
]

BUFFER_DEG = 0.05   # ~5-6 กม. รอบจุดที่ผู้ใช้ส่งมา
PAGE_SIZE = 100
MAX_PAGES = 5       # ต่อ endpoint (สูงสุด 500 รายการ ใน bbox ~11 กม. เหลือเฟือ)
REQUEST_TIMEOUT = 10


def make_bbox(lat: float, lng: float, buffer_deg: float = BUFFER_DEG) -> str:
    """GISTDA ต้องการ bbox: lng_min,lat_min,lng_max,lat_max (EPSG:4326)"""
    return f"{lng - buffer_deg},{lat - buffer_deg},{lng + buffer_deg},{lat + buffer_deg}"


def _feature_key(feature: dict) -> str:
    fid = feature.get("id")
    if fid is not None:
        return str(fid)
    return json.dumps(feature.get("geometry"), sort_keys=True)


def fetch_features_paged(url: str, bbox: str, headers: dict,
                         page_size: int = PAGE_SIZE, max_pages: int = MAX_PAGES,
                         get=requests.get):
    """
    ดึงทีละหน้าด้วย limit/offset จนครบตาม numberMatched
    คืน (features, complete)
      complete = True  เมื่อดึงครบตามที่ API บอก (หรือหน้าสุดท้ายไม่เต็มหน้า)
      complete = False เมื่อต้องหยุดก่อนครบ (API ไม่สนใจ offset / เกิน max_pages)
    พารามิเตอร์ get ไว้ให้ทดสอบโดยไม่ต้องยิง API จริง
    """
    all_features, seen = [], set()
    number_matched = None
    offset = 0

    for _ in range(max_pages):
        params = {"bbox": bbox, "limit": page_size, "offset": offset}
        resp = get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if number_matched is None:
            number_matched = data.get("numberMatched")

        features = data.get("features", [])
        if not features:
            return all_features, True  # หมดแล้ว

        new_count = 0
        for f in features:
            key = _feature_key(f)
            if key not in seen:
                seen.add(key)
                all_features.append(f)
                new_count += 1

        if new_count == 0:
            print("[warn] GISTDA ส่งหน้าซ้ำ น่าจะไม่รองรับ limit/offset จึงหยุดดึง")
            return all_features, False

        offset += len(features)

        if number_matched is not None:
            if len(all_features) >= number_matched:
                return all_features, True
        elif len(features) < page_size:
            return all_features, True  # ไม่มี numberMatched: ถือว่าหน้าไม่เต็ม = หน้าสุดท้าย

    return all_features, False  # ชน max_pages ก่อนครบ


def check_flood_risk_live(lat: float, lng: float, get=requests.get) -> dict:
    """สถานะ "ตอนนี้" ของพิกัดที่แชร์มา: ตรวจเฉพาะภาพ 1 วันล่าสุด (1 คำขอ แทนที่จะเรียกครบ 4 ชั้นแล้วทิ้งผล 3 ชั้น)"""
    return check_flood_risk(lat, lng, get=get, endpoints=FLOOD_ENDPOINTS[:1])


def find_containing_feature(lat: float, lng: float, features: list) -> dict | None:
    """
    คืน feature แรกที่ polygon ครอบจุดนี้ (รวมจุดที่อยู่บนขอบ) หรือ None
    shapely ใช้ลำดับ (lng, lat) เหมือน GeoJSON
    """
    point = Point(lng, lat)

    for feature in features:
        geom_dict = feature.get("geometry")
        if not geom_dict:
            continue
        try:
            geom = shape(geom_dict)
            if not geom.is_valid:
                geom = geom.buffer(0)  # วิธีมาตรฐานซ่อม polygon ที่ self-intersect
            if geom.covers(point):
                return feature
        except Exception as e:  # geometry เสียแค่อันเดียว ไม่ควรทำให้ทั้งการเช็คล่ม
            print(f"[warn] ข้าม geometry ที่อ่านไม่ได้: {e}")

    return None


def check_flood_risk(lat: float, lng: float, get=requests.get, endpoints=None) -> dict:
    """
    จุดที่ message_handler เรียกใช้ คืน dict:
      risk: "high"    จุดอยู่ใน polygon น้ำท่วม (source = ช่วงเวลาของข้อมูลที่เจอ)
            "low"     มี polygon น้ำท่วมใกล้ๆ (ใน bbox) แต่ไม่ครอบจุด
            "unknown" ไม่มี polygon ในบริเวณนี้เลย (ไม่ได้แปลว่าปลอดภัย)
            "error"   เรียก GISTDA ไม่สำเร็จทุก endpoint
      incomplete: True ถ้าดึงข้อมูลได้ไม่ครบ ผล "low"/"unknown" อาจคลาดเคลื่อน
    """
    api_key = os.getenv("GISTDA_API_KEY")  # อ่านตอนเรียกใช้ ไม่ใช่ตอน import (ไม่มีค่า default ตั้งใจ)
    if not api_key:
        raise RuntimeError("ไม่พบ GISTDA_API_KEY ใน .env")

    bbox = make_bbox(lat, lng)
    headers = {"API-Key": api_key}

    nearby_label = None
    incomplete = False
    successful_calls = 0

    for path, label in (endpoints or FLOOD_ENDPOINTS):
        try:
            features, complete = fetch_features_paged(BASE_URL + path, bbox, headers, get=get)
        except (requests.RequestException, ValueError) as e:
            print(f"[warn] เรียก {label} ไม่สำเร็จ: {e}")
            incomplete = True
            continue

        successful_calls += 1
        if not complete:
            incomplete = True
        if not features:
            continue

        if nearby_label is None:
            nearby_label = label

        matched = find_containing_feature(lat, lng, features)
        if matched:
            return {
                "risk": "high",
                "message": f"พิกัดนี้อยู่ในเขตพื้นที่น้ำท่วมตามข้อมูล {label}",
                "source": label,
                "properties": matched.get("properties", {}),
                "incomplete": incomplete,
            }

    if successful_calls == 0:
        return {
            "risk": "error",
            "message": "เรียกข้อมูลจาก GISTDA ไม่สำเร็จ",
            "source": "ไม่พบข้อมูล",
            "incomplete": True,
        }

    if nearby_label:
        return {
            "risk": "low",
            "message": f"พิกัดนี้อยู่ใกล้พื้นที่น้ำท่วม แต่ยังไม่อยู่ในเขตน้ำท่วมโดยตรง (อ้างอิง {nearby_label})",
            "source": nearby_label,
            "incomplete": incomplete,
        }

    return {
        "risk": "unknown",
        "message": "ไม่พบข้อมูลน้ำท่วมในพื้นที่นี้จากข้อมูลล่าสุด (อาจไม่มีสถานการณ์น้ำท่วม)",
        "source": "ไม่พบข้อมูล",
        "incomplete": incomplete,
    }


if __name__ == "__main__":
    # ทดสอบ: ใส่พิกัดที่รู้ว่าท่วมจริง (หาได้จาก show_sakon_flood_points.py)
    test_lat, test_lng = 17.1, 104.1
    print(f"กำลังตรวจสอบพิกัด: lat={test_lat}, lng={test_lng}")
    print(check_flood_risk(test_lat, test_lng))