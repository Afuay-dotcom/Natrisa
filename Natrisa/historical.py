"""
historical.py
=============
ตอบคำถามแนว "วันที่ X ที่จังหวัด Y ท่วมไหม / ท่วมสูงเท่าไร"

หลักการ: ตอบเฉพาะสิ่งที่ระบบมีข้อมูลจริง และบอกตรงๆ ว่าอะไรที่ระบบ "ไม่มี"
  มี:    ระดับน้ำที่สถานีใกล้สุด (เฉพาะวันที่ระบบเก็บ snapshot ไว้ใน SQLite)
  ไม่มี: ขอบเขตน้ำท่วมจากดาวเทียมรายวันย้อนหลัง และความลึกน้ำท่วม
         -> สรุปท่วม/ยังไม่พบสัญญาณท่วม จากหลักฐานที่มี (ดาวเทียมพบแปลง = ท่วม; ไม่พบ ≠ ยืนยันว่าไม่ท่วม) และไม่บอกความลึก
"""

from datetime import date, datetime, timedelta, timezone

from history_store import get_nearest_station_on_date, get_available_range
from satellite_history import (get_day as get_satellite_day, get_coverage as get_satellite_coverage,
                               get_dates as get_satellite_dates, match_province)
from thaiwater_station import get_valid_waterlevel, get_valid_critical_level, WARNING_BUFFER_METERS

_BKK = timezone(timedelta(hours=7))

NO_SATELLITE_NOTE = "ℹ️ สรุปจากข้อมูลที่ระบบเก็บไว้ ใช้ประกอบเท่านั้น ไม่ใช่การยืนยันเหตุการณ์จริง และบอกความลึกน้ำท่วมไม่ได้"


def parse_date_safe(date_str, today=None):
    """YYYY-MM-DD -> date  (แปลงปี พ.ศ. เป็น ค.ศ. ให้ถ้าปี > 2400) คืน None ถ้าอ่านไม่ได้"""
    if not date_str:
        return None
    try:
        d = datetime.strptime(str(date_str).strip().strip('"\''), "%Y-%m-%d").date()
    except ValueError:
        return None
    if d.year > 2400:
        try:
            d = d.replace(year=d.year - 543)
        except ValueError:
            return None
    return d


def extract_place_with_gemini(text):
    """สกัดชื่อสถานที่จากข้อความ (ใช้ Gemini ตัวเดียวกับที่ rag_query ตั้งค่าไว้) คืน None ถ้าไม่มี"""
    from rag_query import cached_generate  # import ตอนเรียกใช้ กัน circular import

    prompt = f"""จากข้อความต่อไปนี้ ให้สกัดเฉพาะ "ชื่อสถานที่" (จังหวัด อำเภอ หรือตำบล) ที่ผู้ใช้ถามถึง
ตอบเป็นชื่อสถานที่เท่านั้น เช่น "จังหวัดสกลนคร" ถ้าไม่มีชื่อสถานที่ในข้อความให้ตอบว่า NONE
ห้ามมีคำอธิบายอื่น

ข้อความ: "{text}"
คำตอบ:"""
    try:
        place = cached_generate("place", text, prompt).strip().strip('"\'')
        return None if (not place or place.upper() == "NONE") else place
    except Exception as e:
        print(f"[warn] extract_place ล้มเหลว: {e}")
        return None


def _format_station_line(st):
    level = get_valid_waterlevel({"waterlevel_msl": st.get("waterlevel_msl")})
    crit = get_valid_critical_level({"critical_level_msl": st.get("critical_level_msl")})
    if level is None:
        return (f"📍 สถานีวัดน้ำใกล้สุด {st['station_name']} (ห่าง {st['distance_km']} กม.): "
                f"ค่าที่บันทึกไว้ผิดปกติ ใช้ไม่ได้")
    text = (f"📍 สถานีวัดน้ำใกล้สุด {st['station_name']} (ห่าง {st['distance_km']} กม.): "
            f"ระดับน้ำ {level} ม.รทก. ณ {st.get('waterlevel_datetime') or 'ไม่ทราบเวลา'}")
    if crit is not None:
        text += f" (เกณฑ์วิกฤต {crit} ม.รทก.)"
    return text


def _verdict_line(st, day, place, sat_day_fn):
    """
    สรุปสั้นๆ ว่า "ท่วม / ยังไม่พบสัญญาณท่วม" จากหลักฐานที่ระบบมี (ไม่ยืนยันว่าไม่ท่วมแน่นอน)
      1) ดาวเทียมพบแปลงน้ำท่วมในจังหวัดนั้น = หลักฐานบวก  -> 🔴
      2) ระดับน้ำสถานีใกล้สุดเทียบเกณฑ์วิกฤต             -> 🔴 / 🟡 (ภายใน 0.5 ม.) / 🔵
    ถ้าไม่มีทั้งสองอย่าง หรืออ่านค่าไม่ได้ -> ไม่สรุป (⚪)
    """
    try:
        rows = sat_day_fn(day.isoformat()) or []
    except Exception:
        rows = []
    province = match_province(place, rows) if rows else None
    if province:
        n = next(r["feature_count"] for r in rows if r["province"] == province)
        if n > 0:
            return f"🔴 สรุป: พบน้ำท่วมใน{province} จากภาพดาวเทียมของวันที่ {day.isoformat()} ({n} แห่ง)"
    sat_note = "ไม่มีบันทึกน้ำท่วมของจังหวัดนี้จากดาวเทียมในระบบ" if rows else "วันนั้นไม่มีข้อมูลดาวเทียม"
    if not st:
        return "⚪ สรุป: ไม่มีข้อมูลระดับน้ำสถานีของวันนั้น จึงสรุปไม่ได้ว่าท่วมหรือไม่"
    level = get_valid_waterlevel({"waterlevel_msl": st.get("waterlevel_msl")})
    crit = get_valid_critical_level({"critical_level_msl": st.get("critical_level_msl")})
    if level is None or crit is None:
        return "⚪ สรุป: สถานีใกล้สุดไม่มีค่าระดับน้ำหรือเกณฑ์วิกฤตที่ใช้ได้ จึงสรุปไม่ได้ว่าท่วมหรือไม่"
    read_day = parse_date_safe((st.get("waterlevel_datetime") or "")[:10])
    if read_day is not None and read_day < day - timedelta(days=1):
        return (f"⚪ สรุป: ค่าของสถานีใกล้สุดเก่าเกินไป (วัดเมื่อ {read_day.isoformat()}) "
                f"จึงใช้สรุปวันที่ {day.isoformat()} ไม่ได้")
    gap = round(crit - level, 2)
    if gap <= 0:
        return f"🔴 สรุป: ระดับน้ำสถานีใกล้สุดสูงถึงหรือเกินเกณฑ์วิกฤต ({sat_note})"
    if gap <= WARNING_BUFFER_METERS:
        return f"🟡 สรุป: ระดับน้ำสถานีใกล้สุดใกล้เกณฑ์วิกฤต (ต่ำกว่า {gap} ม.) ({sat_note})"
    return (f"🔵 สรุป: ระดับน้ำสถานีใกล้สุดยังต่ำกว่าเกณฑ์วิกฤต {gap} ม. และ{sat_note} "
            f"(ยังสรุปไม่ได้ว่าพื้นที่นั้นไม่ท่วม)")


def _satellite_lines(place, day, sat_day_fn, sat_range_fn, sat_dates_fn=get_satellite_dates):
    """บรรทัดสรุปน้ำท่วมจากดาวเทียมของวันนั้น (วันที่ = วันถ่ายภาพ) — ไม่ให้ error ทำให้ทั้งข้อความล่ม"""
    try:
        first, last = sat_range_fn()
        if not first:
            return ["🛰️ ระบบยังไม่มีบันทึกน้ำท่วมจากดาวเทียมย้อนหลัง (ยังไม่เคย sync)"]
        rows = sat_day_fn(day.isoformat())
        if not rows:
            try:
                dates = sat_dates_fn() or []
            except Exception:
                dates = []
            have = ", ".join(dates[-6:]) if dates else f"{first} ถึง {last}"
            return [f"🛰️ ไม่มีบันทึกน้ำท่วมจากดาวเทียมของวันที่ {day.isoformat()} "
                    f"(ระบบมีบันทึกของวันที่: {have}; 'ไม่มีบันทึก' ไม่ได้แปลว่าไม่ท่วม)"]
        province = match_province(place, rows)
        if province:
            n = next(r["feature_count"] for r in rows if r["province"] == province)
            line = f"🛰️ ดาวเทียมตรวจพบพื้นที่น้ำท่วมใน{province} {n} แห่ง (ภาพวันที่ {day.isoformat()})"
        else:
            if (place or "").startswith("พิกัด"):
                line = "🛰️ อยากดูสถิติน้ำท่วมจากดาวเทียมของจังหวัด พิมพ์ชื่อจังหวัดมาด้วยได้เลยค่ะ เช่น \"จังหวัดสกลนคร วันที่ 9 ตุลาคม\""
            else:
                line = f"🛰️ ไม่พบบันทึกน้ำท่วมจากดาวเทียมของพื้นที่นี้ในวันที่ {day.isoformat()}"
        return [line]
    except Exception as e:
        print(f"[warn] อ่านข้อมูลดาวเทียมย้อนหลังล้มเหลว: {e}")
        return []


def _nearest_usable_station(lat, lng, day):
    return get_nearest_station_on_date(lat, lng, day, prefer_usable=True)


def answer_historical(question, *, extract_date=None, extract_place=None, geocode=None,
                      station_fn=_nearest_usable_station,
                      range_fn=get_available_range, sat_day_fn=get_satellite_day,
                      sat_range_fn=get_satellite_coverage, today=None, coords=None,
                      sat_dates_fn=get_satellite_dates):
    if extract_date is None:
        from rag_query import extract_date_from_text as extract_date
    if extract_place is None:
        extract_place = extract_place_with_gemini
    if geocode is None:
        from location_parser import try_geocode_address as geocode

    today = today or datetime.now(_BKK).date()

    day = parse_date_safe(extract_date(question))
    if day is None:
        return "สายธารอยากช่วยหาข้อมูลย้อนหลังให้นะคะ ช่วยระบุวันที่ให้ชัดเจนอีกนิดได้ไหมคะ เช่น 21 กันยายน 2569"
    if day > today:
        return f"วันที่ {day.isoformat()} ยังมาไม่ถึงเลยค่ะ ถ้าอยากรู้วันที่ผ่านมา ลองระบุวันที่ในอดีตนะคะ"

    if coords:
        # ผู้ใช้ส่งพิกัดมาแล้ว ไม่ต้อง geocode; ลองสกัดชื่อจังหวัดจากข้อความไว้จับคู่ข้อมูลดาวเทียมรายจังหวัด
        lat, lng = coords
        try:
            province_hint = extract_place(question)
        except Exception:
            province_hint = None
        place = province_hint or f"พิกัด {lat:.4f}, {lng:.4f}"
    else:
        place = extract_place(question)
        if not place:
            return "ช่วยบอกด้วยนะคะว่าต้องการดูพื้นที่ไหน เช่น ชื่อจังหวัดหรืออำเภอ พร้อมวันที่ที่อยากทราบ"

        coords = geocode(place)
        if not coords:
            return f"หาตำแหน่งของ '{place}' ไม่เจอค่ะ ลองระบุชื่อจังหวัด/อำเภอให้ชัดเจนขึ้นนะคะ"
        lat, lng = coords

    lines = [f"📅 ข้อมูลย้อนหลังวันที่ {day.isoformat()} บริเวณ {place}"]

    # --- ระดับน้ำสถานี (เฉพาะวันที่ระบบเก็บไว้) ---
    st = station_fn(lat, lng, day.isoformat())
    lines.append(_verdict_line(st, day, place, sat_day_fn))
    if st:
        lines.append(_format_station_line(st))
    else:
        first, last = range_fn()
        if first:
            lines.append(f"📍 วันนั้นยังไม่มีข้อมูลระดับน้ำสถานี (ระบบเริ่มเก็บตั้งแต่ {first})")
        else:
            lines.append("📍 ระบบเพิ่งเริ่มเก็บข้อมูลระดับน้ำรายวัน จึงยังไม่มีข้อมูลย้อนหลังของสถานี")

    # --- น้ำท่วมจากดาวเทียม (เฉพาะวันที่ระบบเก็บสรุปไว้) ---
    lines.extend(_satellite_lines(place, day, sat_day_fn, sat_range_fn, sat_dates_fn))

    lines.append("")
    lines.append(NO_SATELLITE_NOTE)
    return "\n".join(lines)