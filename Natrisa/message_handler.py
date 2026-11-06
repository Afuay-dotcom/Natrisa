"""
message_handler.py
===================
ตัวกลางที่รวมฟังก์ชันจาก 3 โมดูลที่ทำไว้ก่อนหน้า:
  - flood_check.py        (เช็คความเสี่ยงน้ำท่วมจาก GISTDA)
  - thaiwater_station.py  (หาสถานีวัดน้ำใกล้สุดจาก Thaiwater)
  - rag_query.py           (ตอบคำถามทั่วไปจากคู่มือ ปภ.)

หน้าที่หลัก: รับ input จาก LINE (ไม่ว่าจะเป็นพิกัดหรือข้อความ)
แล้วตัดสินใจว่าควรเรียกฟังก์ชันไหน จากนั้นประกอบเป็นข้อความตอบกลับที่อ่านง่าย
"""

import re

from flood_check import check_flood_risk_live
from tmd_forecast import get_forecast_rain, format_forecast_rain_lines
from historical import answer_historical
from thaiwater_station import (
    fetch_all_stations, find_nearest_station, classify_station_risk,
    get_valid_waterlevel, get_valid_critical_level,
)
from rag_query import answer_question, classify_intent, chat_naturally, extract_date_from_text
from location_parser import try_parse_safe_location, try_geocode_address


# ---------------------------------------------------------------------------
# กรณีที่ 1: user แชร์พิกัด (LINE location message)
# ---------------------------------------------------------------------------

def handle_location(lat: float, lng: float) -> str:
    """
    รับพิกัดจาก LINE -> เช็คทั้ง GISTDA (ข้อมูลพื้นที่น้ำท่วมจากดาวเทียม)
    และ Thaiwater (ระดับน้ำ real-time จากสถานีใกล้สุด) -> ประกอบเป็นข้อความเดียว

    หลักการสำคัญ: สี/สถานะทุกอันที่แสดงต้องอ้างอิงได้กับข้อมูลจริงจากหน่วยงานรัฐ
    ยกเว้นสถานะ "เฝ้าระวัง" (🟡) ของ Thaiwater ที่ใช้ระยะกันชนซึ่งผู้จัดทำกำหนดเอง
    (ระบุไว้ตรงๆ ในข้อความ ไม่ปิดบัง)
    """
    lines = []

    # ---- ส่วนที่ 1: ข้อมูลจาก GISTDA (พื้นที่น้ำท่วมจากดาวเทียม) ----
    # หมายเหตุ: GISTDA ไม่มี field สีหรือระดับความเสี่ยงให้ มีแค่ข้อมูล polygon
    # ว่าพิกัดตกอยู่ในเขตน้ำท่วมหรือไม่ (ข้อเท็จจริงตรงไปตรงมา ไม่ต้องตีความเพิ่ม)
    try:
        flood_result = check_flood_risk_live(lat, lng)
    except Exception as e:   # เช่น ไม่มี GISTDA_API_KEY: ส่วนสถานี/ฝนยังต้องตอบได้
        print(f"[warn] check_flood_risk ล้มเหลว: {e}")
        flood_result = {"risk": "error", "source": "ไม่พบข้อมูล", "incomplete": True}

    # แชร์พิกัด = ถามสถานการณ์ "ตอนนี้" จึงใช้เฉพาะภาพ 1 วันล่าสุด
    # ผลที่เจอแค่ในชุด 3/7/30 วันไม่แสดง (ไม่ใช้สัญลักษณ์เพิ่ม) ใครอยากดูย้อนหลังให้พิมพ์พิกัด/สถานที่ + วันที่
    if (flood_result.get("risk") in ("high", "low") and flood_result.get("source")
            and "1 วัน" not in flood_result["source"]):
        flood_result = {"risk": "unknown", "source": "ไม่พบข้อมูล",
                        "incomplete": flood_result.get("incomplete", False)}

    source = flood_result.get("source")
    window = f" ({source})" if source and source != "ไม่พบข้อมูล" else ""

    if flood_result["risk"] == "high":
        lines.append(f"🔴 พิกัดนี้อยู่ในเขตพื้นที่น้ำท่วมตามข้อมูลดาวเทียมของ GISTDA{window}")
    elif flood_result["risk"] == "low":
        lines.append(f"🟡 พิกัดนี้อยู่ใกล้เขตพื้นที่น้ำท่วมของ GISTDA แต่ยังไม่อยู่ในขอบเขตโดยตรง{window}")
    elif flood_result["risk"] == "error":
        # เรียก GISTDA ไม่สำเร็จ ≠ ไม่มีน้ำท่วม ต้องบอกผู้ใช้ตรงๆ
        lines.append("⚪ ตรวจสอบข้อมูลดาวเทียม GISTDA ไม่สำเร็จในขณะนี้ จึงยังบอกไม่ได้ว่าพื้นที่นี้ท่วมหรือไม่")
    else:
        # "unknown" ไม่ได้แปลว่าปลอดภัย แค่ไม่มีข้อมูล จึงใช้ ⚪ ไม่ใช้ 🟢
        # (ตามข้อค้นพบว่า GISTDA มี latency สูง ไม่ตรวจจับน้ำท่วมขนาดเล็ก)
        lines.append("⚪ ไม่พบข้อมูลพื้นที่น้ำท่วมจากดาวเทียม GISTDA ในบริเวณนี้จากภาพล่าสุด (1 วัน) "
                     "(ข้อมูลดาวเทียมอาจไม่ครอบคลุมน้ำท่วมขนาดเล็กหรือยังไม่อัปเดต)")
    if flood_result.get("incomplete") and flood_result["risk"] != "high":
        lines.append("(หมายเหตุ: ดึงข้อมูลพื้นที่น้ำท่วมได้ไม่ครบทุกรายการ ผลอาจคลาดเคลื่อน)")

    # ---- ส่วนที่ 2: ข้อมูลจากสถานีวัดน้ำที่ใกล้ที่สุด (real-time) ----
    try:
        all_stations = fetch_all_stations()
        nearest = find_nearest_station(lat, lng, all_stations, max_results=1)

        if nearest:
            station = nearest[0]
            risk = classify_station_risk(station)

            lines.append("")  # เว้นบรรทัด
            lines.append(f"📍 สถานีวัดน้ำใกล้สุด: {station['station_name']} "
                         f"(ห่าง {station['distance_km']} กม. ดูแลโดย{station.get('agency_name', 'ไม่ทราบหน่วยงาน')})")
            lines.append(f"🌊 แม่น้ำ: {station.get('river_name') or 'ไม่ทราบ'}")
            lines.append(f"⏱️ ข้อมูลล่าสุด: {station.get('waterlevel_datetime', 'ไม่ทราบ')}")

            # เปลี่ยนวิธีแสดงผลจากเดิมที่พึ่ง field 'diff_wl_bank' ของ Thaiwater
            # (พิสูจน์แล้วว่าไม่น่าเชื่อถือ - พบว่ามีค่าเท่ากับ waterlevel_msl เป๊ะ
            # ถึง 591/1428 สถานี และเว็บทางการของ สทนช. เองก็ไม่ใช้ field นี้แสดงผล
            # เช่นกัน ดูจาก nationalthaiwater.onwr.go.th ที่แสดงแค่ "ระดับน้ำ" กับ
            # "ระดับตลิ่ง" แยกเป็น 2 ค่า แล้วให้ผู้ใช้เทียบเอง)
            #
            # ระบบนี้จึงเปลี่ยนมาแสดงตามรูปแบบเดียวกับเว็บทางการ: โชว์ระดับน้ำ
            # และระดับตลิ่ง (ถ้ามี) แยกกันตรงๆ แล้วให้ classify_station_risk()
            # เป็นคนคำนวณผลต่างเอง (ไม่พึ่ง diff_wl_bank จาก backend อีกต่อไป)
            # แสดงเฉพาะค่าที่ผ่านการตรวจคุณภาพแล้ว (ไม่โชว์ sentinel 32765.9 หรือเกณฑ์ที่เป็น 0)
            current_msl = get_valid_waterlevel(station)
            critical_msl = get_valid_critical_level(station)

            if current_msl is not None:
                lines.append(f"📏 ระดับน้ำ: {current_msl} ม.รทก. (เทียบระดับน้ำทะเลปานกลาง)")

            if critical_msl is not None:
                # ฟิลด์นี้คือ critical_level_msl (เกณฑ์วิกฤต) ใช้ชื่อให้ตรงกับที่นำไปเทียบจริง
                lines.append(f"🚧 เกณฑ์ระดับน้ำวิกฤต (ที่{station.get('agency_name') or 'หน่วยงานเจ้าของสถานี'}กำหนด): "
                             f"{critical_msl} ม.รทก.")

            # สถานะจากการเทียบเกณฑ์วิกฤต (sourced จากหน่วยงานเจ้าของสถานี เมื่อมีข้อมูล)
            lines.append(f"{risk['emoji']} {risk['text']}")

        else:
            lines.append("\n(ไม่พบสถานีวัดน้ำใกล้เคียง)")

    except Exception as e:
        # ไม่ให้ทั้งข้อความล่มเพราะ Thaiwater endpoint มีปัญหาชั่วคราว
        lines.append(f"\n(ไม่สามารถดึงข้อมูลสถานีวัดน้ำได้ในขณะนี้)")
        print(f"[warn] Thaiwater fetch error: {e}")

    # ---- ส่วนที่ 3: ฝนพยากรณ์ล่วงหน้า (ไม่ใช้ฝนย้อนหลังแล้ว) ----
    try:
        lines.extend(format_forecast_rain_lines(get_forecast_rain(lat, lng)))
    except Exception as e:
        print(f"[warn] forecast rain error: {type(e).__name__}")

    # ---- ขอบเขตของข้อมูล: กันผู้ใช้เข้าใจว่า "ไม่เจอ = ปลอดภัย" ----
    lines.append("")
    lines.append("ℹ️ ข้อมูลข้างต้นครอบคลุมแม่น้ำและภาพดาวเทียมเท่านั้น ไม่รวมน้ำท่วมขังจากฝนตกหนักในตัวเมือง/"
                 "ระบบระบายน้ำล้น หากฝนตกหนักโปรดตรวจสอบประกาศจากหน่วยงานในพื้นที่ หรือโทร 1784 (ปภ.)")

    lines.append("\nหากต้องการคำแนะนำการปฏิบัติตัว พิมพ์คำถามมาได้เลยค่ะ")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# กรณีที่ 2: user พิมพ์คำถามเป็นข้อความ (LINE text message)
# ---------------------------------------------------------------------------
_THAI_MONTHS = ("มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม|"
                "ม\\.ค\\.|ก\\.พ\\.|มี\\.ค\\.|เม\\.ย\\.|พ\\.ค\\.|มิ\\.ย\\.|ก\\.ค\\.|ส\\.ค\\.|ก\\.ย\\.|ต\\.ค\\.|พ\\.ย\\.|ธ\\.ค\\.")
# สัญญาณว่าข้อความที่มีพิกัดกำลังขอข้อมูลย้อนหลัง (เช็คแบบถูกๆ ก่อน จะได้ไม่เรียก LLM ทุกข้อความพิกัด)
_DATE_CUE = re.compile(r"\d{1,2}\s*[/-]\s*\d{1,2}\s*[/-]\s*\d{2,4}|เมื่อวาน|ย้อนหลัง|วันที่|" + _THAI_MONTHS)


def build_help_message() -> str:
    """
    ข้อความแนะนำวิธีใช้งาน สำหรับ user ที่ไม่แน่ใจว่าต้องแชร์ location ยังไง
    เสนอทางเลือกครบทุกวิธีที่ระบบรองรับ ไม่บังคับให้ใช้ปุ่มแชร์ location
    ของ LINE เพียงอย่างเดียว
    """
    return (
        "สวัสดีค่ะ หนูชื่อสายธาร ผู้ช่วยตรวจสอบความเสี่ยงน้ำท่วมนะคะ 😊\n\n"
        "อยากรู้สถานการณ์น้ำใกล้ตัวเอง บอกตำแหน่งมาได้เลยค่ะ เลือกวิธีไหนก็ได้ที่สะดวก:\n\n"
        "1️⃣ กดปุ่ม + ที่มุมซ้ายล่าง แล้วเลือก 'Location' เพื่อแชร์ตำแหน่งปัจจุบัน\n"
        "2️⃣ พิมพ์ชื่อจังหวัด อำเภอ หรือตำบลมาตรงๆ ก็ได้ค่ะ\n"
        "3️⃣ แปะลิงก์ Google Maps มาในแชทก็ได้\n"
        "4️⃣ หรือถ้ารู้พิกัดอยู่แล้ว พิมพ์แบบนี้ก็ได้ค่ะ เช่น lat=17.58, lng=103.97\n\n"
        "หรือถ้าอยากถามวิธีปฏิบัติตัวตอนน้ำท่วม พิมพ์คำถามมาได้เลยค่ะ"
    )


def handle_text_question(question: str) -> str:
    """
    รับข้อความจาก user แล้วตัดสินใจว่าควรตอบแบบไหน ตามลำดับนี้:

    1) เช็คแบบ "ปลอดภัย/แม่นยำสูง" ก่อนเสมอ (regex พิกัดตรงๆ + ลิงก์ Google Maps)
       เพราะเร็วและไม่มี false positive จึงเช็คก่อนเรียก LLM เพื่อประหยัด
       เวลา/ต้นทุน

    2) ถ้าไม่เจอพิกัดแบบตรงไปตรงมา ให้ใช้ Gemini จำแนกเจตนา (classify_intent)
       ว่าข้อความนี้น่าจะเป็น: ชื่อสถานที่ / คำถามน้ำท่วมปัจจุบัน / คำถามข้อมูล
       ย้อนหลัง / ทักทาย / บทสนทนาทั่วไป วิธีนี้แม่นยำกว่าการเดาด้วย keyword
       ล้วนๆ (ที่เคยมีปัญหาเรื่อง "สวัสดี" ถูกตีความผิดเป็นพิกัดตอนใช้ geocode
       แบบไม่กรอง) — การจำแนกเจตนาเกิดขึ้น "ที่เดียว" ในทั้งระบบ คือจุดนี้
       เท่านั้น ฟังก์ชันปลายทางที่เรียกต่อ (answer_question ฯลฯ) จะไม่จำแนกซ้ำ

    3) ประมวลผลตาม intent ที่ได้:
       - location_name    -> ลอง geocode (ที่กรองผลลัพธ์เข้มงวดแล้ว) ถ้าสำเร็จ
                             ตอบเหมือนแชร์พิกัด ถ้าล้มเหลวให้ตอบแบบบทสนทนาทั่วไป
       - flood_question   -> ส่งเข้า RAG pipeline ตามปกติ (answer_question)
       - historical_flood -> answer_historical() (
                             ระดับน้ำสถานีจาก snapshot ใน SQLite)
       - greeting          -> ตอบข้อความแนะนำการใช้งาน
       - general_chat      -> ตอบแบบบทสนทนาธรรมชาติ ไม่ผ่าน RAG
    """
    # ขั้นที่ 1: เช็คพิกัด/ลิงก์แบบตรงไปตรงมาก่อน (เร็ว แม่นยำ ไม่ต้องเรียก LLM)
    safe_location = try_parse_safe_location(question)
    if safe_location:
        lat, lng = safe_location
        # พิกัด + วันที่ในข้อความเดียวกัน = ขอข้อมูลย้อนหลัง ณ วันนั้น
        if _DATE_CUE.search(question):
            return answer_historical(question, extract_date=extract_date_from_text, coords=(lat, lng))
        prefix = "📍 รับพิกัดจากข้อความที่พิมพ์มาแล้วค่ะ กำลังตรวจสอบให้นะคะ\n\n"
        return prefix + handle_location(lat, lng)

    # ขั้นที่ 2: ให้ Gemini จำแนกเจตนาของข้อความที่เหลือ
    intent = classify_intent(question)
    if intent == "llm_error":   # Gemini ล่ม/โควต้าหมด: อย่าตอบเหมือนแชททั่วไป (ผู้ใช้ถามเรื่องน้ำท่วมอยู่อาจเข้าใจผิด)
        return ("ตอนนี้ระบบ AI ของสายธารขัดข้องชั่วคราวค่ะ ลองส่งข้อความใหม่อีกครั้งในอีกสักครู่นะคะ "
                "แต่ถ้าเป็นเหตุฉุกเฉิน โทร 1784 (กรมป้องกันและบรรเทาสาธารณภัย) ได้ทันที "
                "หรือแชร์ตำแหน่งผ่านปุ่ม + มาได้เลย ส่วนนี้ยังตรวจสอบให้ได้ค่ะ")

    # ขั้นที่ 3: ประมวลผลตาม intent
    if intent == "location_name":
        geocoded = try_geocode_address(question)
        if geocoded:
            lat, lng = geocoded
            prefix = f"📍 คิดว่า '{question}' น่าจะหมายถึงพิกัดนี้ค่ะ กำลังตรวจสอบให้นะคะ\n\n"
            return prefix + handle_location(lat, lng)
        # geocode ไม่สำเร็จ (อาจสะกดผิด/ไม่ใช่ชื่อสถานที่จริง) -> ตอบแบบสุภาพ
        return ("ขอโทษนะคะ หนูหาตำแหน่งที่คุณพิมพ์มาไม่เจอเลย ลองพิมพ์ชื่อจังหวัด/"
                "อำเภอให้ชัดเจนขึ้น หรือแชร์ location ผ่านปุ่ม + ก็ได้ค่ะ")

    if intent == "greeting":
        return build_help_message()

    if intent == "flood_question":
        return answer_question(question)

    if intent == "historical_flood":
        return answer_historical(question, extract_date=extract_date_from_text)

    # general_chat (หรือ fallback เมื่อ classify_intent ไม่แน่ใจ)
    return chat_naturally(question)


# ---------------------------------------------------------------------------
# จุดเข้าหลัก — webhook จะเรียกใช้ฟังก์ชันนี้
# ---------------------------------------------------------------------------

def handle_message(message_type: str, **kwargs) -> str:
    """
    จุดเข้าหลักเดียว ที่ webhook (line_webhook.py) จะเรียกใช้
    แยกตามประเภทข้อความที่ได้รับจาก LINE

    message_type: "location" หรือ "text"
    kwargs:
        - ถ้า "location": ต้องมี lat, lng
        - ถ้า "text": ต้องมี text
    """
    if message_type == "location":
        return handle_location(kwargs["lat"], kwargs["lng"])

    elif message_type == "text":
        return handle_text_question(kwargs["text"])

    else:
        return "ขออภัยค่ะ ระบบรองรับเฉพาะข้อความและการแชร์พิกัดเท่านั้น"


# ---------------------------------------------------------------------------
# ทดสอบรันตรงๆ
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== ทดสอบกรณีแชร์พิกัด ===")
    print(handle_message("location", lat=13.7563, lng=100.5018))

    print("\n\n=== ทดสอบกรณีพิมพ์คำถามเกี่ยวกับน้ำท่วม ===")
    print(handle_message("text", text="ถ้าน้ำท่วมบ้านต้องทำยังไงบ้าง"))

    print("\n\n=== ทดสอบกรณีทักทาย (ควรได้ help message ไม่ใช่ตีความเป็นพิกัด) ===")
    print(handle_message("text", text="สวัสดี"))

    print("\n\n=== ทดสอบกรณีพิมพ์พิกัดตรงๆ ===")
    print(handle_message("text", text="lat=17.585385, lng=103.978062"))

    print("\n\n=== ทดสอบกรณีพูดคุยทั่วไป (ไม่เกี่ยวกับน้ำท่วม) ===")
    print(handle_message("text", text="วันนี้อากาศดีจังเลย"))

    print("\n\n=== ทดสอบกรณีถามข้อมูลย้อนหลัง (ควรได้ข้อมูลฝน/สถานี ไม่ใช่ chat_naturally) ===")
    print(handle_message("text", text="ขอดูข้อมูลน้ำท่วมวันที่ 21 กันยายนหน่อยค่ะ"))