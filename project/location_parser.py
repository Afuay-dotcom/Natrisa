"""
location_parser.py
====================
แปลงข้อความที่ user พิมพ์เข้ามาให้เป็นพิกัด (lat, lng) ให้ได้มากที่สุดเท่าที่
จะทำได้ เพราะในทางปฏิบัติ user ไม่ได้แชร์ location ผ่านปุ่มของ LINE เสมอไป
บางคนอาจจะ:
  1. พิมพ์พิกัดตรงๆ เช่น "lat=17.585385, lng=103.978062" หรือ "17.58, 103.97"
  2. แปะลิงก์ Google Maps มาในแชท (ทั้งลิงก์เต็มและลิงก์ย่อ)
  3. พิมพ์ชื่อสถานที่/ตำบล/อำเภอ/จังหวัด แทนพิกัด

โมดูลนี้พยายามแปลงทั้ง 3 แบบให้เป็น (lat, lng) โดยเรียงลำดับความน่าเชื่อถือ
จากมากไปน้อย: พิกัดตรงๆ > ลิงก์ Google Maps > ชื่อสถานที่ (ต้อง geocode)
"""

import re
import requests
from urllib.parse import unquote, urlparse, urljoin


# ---------------------------------------------------------------------------
# 1) พิกัดที่พิมพ์ตรงๆ ในข้อความ
# ---------------------------------------------------------------------------

# รองรับทั้งแบบมี label (lat=.., lng=..) และแบบตัวเลขคั่นด้วยจุลภาคเฉยๆ
_LATLNG_LABELED_PATTERN = re.compile(
    r"lat\s*[:=]\s*(-?\d{1,3}\.\d+)\s*,?\s*lng\s*[:=]\s*(-?\d{1,3}\.\d+)",
    re.IGNORECASE,
)

# แบบ "17.585385, 103.978062" (ไม่มี label) - ต้องระวังไม่ให้ไปจับเบอร์โทรหรือตัวเลขอื่น
# จึงบังคับให้ทั้งคู่ต้องมีจุดทศนิยม และอยู่ในช่วงพิกัดที่เป็นไปได้ของประเทศไทยคร่าวๆ
_LATLNG_BARE_PATTERN = re.compile(
    r"(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})"
)


def try_parse_latlng_text(text: str):
    """
    ลองจับพิกัดที่พิมพ์ตรงๆ ในข้อความ คืนค่า (lat, lng) หรือ None ถ้าไม่เจอ
    """
    match = _LATLNG_LABELED_PATTERN.search(text)
    if match:
        lat, lng = float(match.group(1)), float(match.group(2))
        if _is_plausible_thailand_coord(lat, lng):
            return (lat, lng)

    match = _LATLNG_BARE_PATTERN.search(text)
    if match:
        lat, lng = float(match.group(1)), float(match.group(2))
        if _is_plausible_thailand_coord(lat, lng):
            return (lat, lng)

    return None


def _is_plausible_thailand_coord(lat: float, lng: float) -> bool:
    """
    เช็คคร่าวๆ ว่าพิกัดอยู่ในกรอบที่เป็นไปได้ของประเทศไทย (กันจับตัวเลขมั่ว
    เช่น เบอร์โทร หรือราคาสินค้า มาตีความเป็นพิกัดผิดๆ)
    กรอบนี้กว้างกว่าเขตแดนจริงเล็กน้อยเพื่อไม่ให้ตัดพื้นที่ชายแดนออกไป
    """
    return 5.0 <= lat <= 21.0 and 97.0 <= lng <= 106.0


# ---------------------------------------------------------------------------
# 2) ลิงก์ Google Maps (ทั้งเต็มและย่อ)
# ---------------------------------------------------------------------------

# allowlist ตาม "hostname" จริง (ไม่ใช่เช็คแค่ substring ในข้อความ ซึ่งโดนหลอกได้ง่าย
# เช่น http://evil.com/?x=google.com/maps หรือ http://169.254.169.254/#maps.app.goo.gl
# แล้วให้เซิร์ฟเวอร์เราไปยิง request ไปที่ปลายทางภายใน = ช่องโหว่ SSRF)
_ALLOWED_MAPS_HOSTS = {
    "maps.app.goo.gl", "goo.gl", "maps.google.com",
    "google.com", "www.google.com", "google.co.th", "www.google.co.th",
}
_MAX_REDIRECT_HOPS = 5

# รูปแบบ URL ที่มีพิกัดฝังอยู่ตรงๆ มีหลายแบบที่ Google ใช้:
#   1. .../@17.585385,103.978062,15z          <- มุมมองแผนที่ทั่วไป
#   2. ?q=17.58,103.97                         <- แบบ query param
#   3. .../data=...!3d17.585385!4d103.978062  <- พิกัดของ "หมุด" เฉพาะจุด
#      (พบบ่อยในลิงก์แชร์สถานที่เจาะจงจากแอป Google Maps บนมือถือ)
_URL_COORD_PATTERN_AT_OR_Q = re.compile(r"[@=](-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)")
_URL_COORD_PATTERN_3D4D = re.compile(r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)")


def _extract_coords_from_url(url: str):
    """
    ลองจับพิกัดจาก URL ด้วยทุกรูปแบบที่รู้จัก คืนค่า (lat, lng) หรือ None
    """
    # ลองแบบ !3d!4d ก่อน เพราะแม่นยำกว่าสำหรับลิงก์แชร์สถานที่เฉพาะจุด
    # (แบบ @lat,lng คือ "center ของแผนที่" ซึ่งอาจไม่ตรงกับหมุดที่แชร์จริง
    # ถ้า URL มีทั้งสองแบบปนกัน)
    match = _URL_COORD_PATTERN_3D4D.search(url)
    if match:
        return float(match.group(1)), float(match.group(2))

    match = _URL_COORD_PATTERN_AT_OR_Q.search(url)
    if match:
        return float(match.group(1)), float(match.group(2))

    return None


# รูปแบบ URL ที่มีชื่อสถานที่ฝังอยู่ในเส้นทาง เช่น
#   .../maps/place/เทศบาลนครนครราชสีมา+ตำบลในเมือง+.../data=...
# ใช้ตอนที่ URL ไม่มีพิกัดตัวเลขฝังอยู่เลย (เช่น ใช้ Feature ID/CID แทน
# ซึ่งถอดรหัสเป็นพิกัดตรงๆ ไม่ได้โดยไม่มี Google Places API)
_URL_PLACE_NAME_PATTERN = re.compile(r"/maps/place/([^/]+)/")


def _extract_place_name_from_url(url: str):
    """
    ดึงชื่อสถานที่จาก URL path แล้วถอดรหัส URL-encoding + แทน '+' ด้วยช่องว่าง
    คืนค่าเป็นข้อความชื่อสถานที่ที่อ่านได้ หรือ None ถ้าไม่เจอ
    """
    match = _URL_PLACE_NAME_PATTERN.search(url)
    if not match:
        return None

    raw_name = match.group(1)
    decoded_name = unquote(raw_name).replace("+", " ")
    return decoded_name.strip()


# รูปแบบ URL ที่มี Feature ID/CID ฝังอยู่ เช่น
#   !1s0x31194cb7cce490ef:0xa0469d233ce3e50
# ตัวเลข hex หลัง ':0x' คือ CID ซึ่งเอาไปสร้าง URL แบบ
# https://www.google.com/maps?cid=<CID แบบ decimal> แล้วให้ Google redirect
# กลับมาเป็นหน้า place ที่มักมีพิกัดฝังอยู่ (แม่นยำกว่าการเดาจากชื่อสถานที่มาก
# เพราะไม่ต้องพึ่ง fuzzy matching เลย เป็นการถามตรงๆ ด้วยรหัสเป๊ะๆ)
_URL_CID_PATTERN = re.compile(r"!1s0x[0-9a-fA-F]+:0x([0-9a-fA-F]+)")


def _looks_like_google_maps_link(text: str) -> bool:
    """True เฉพาะเมื่อ URL เป็น https และ hostname อยู่ใน allowlist จริงๆ"""
    try:
        parsed = urlparse(text.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _ALLOWED_MAPS_HOSTS:
        return False
    # google.com / goo.gl ต้องเป็นเส้นทางแผนที่เท่านั้น
    if host in ("google.com", "www.google.com", "google.co.th", "www.google.co.th"):
        return parsed.path.startswith("/maps")
    if host == "goo.gl":
        return parsed.path.startswith("/maps")
    return True


def _resolve_redirect_safely(url: str):
    """
    ตาม redirect ทีละ hop โดยเช็ค allowlist ทุก hop (ไม่ปล่อยให้ requests
    ตามไปเองอัตโนมัติ เพราะ short link อาจ redirect ไปโฮสต์อื่นได้)
    คืน URL ปลายทาง หรือ None ถ้าออกนอก allowlist / ตามไม่สำเร็จ
    """
    current = url
    for _ in range(_MAX_REDIRECT_HOPS):
        if not _looks_like_google_maps_link(current):
            print(f"[warn] redirect ออกนอก allowlist จึงหยุด: {current}")
            return None
        try:
            resp = requests.get(current, allow_redirects=False, timeout=8, stream=True)
            location = resp.headers.get("Location")
            status = resp.status_code
            resp.close()
        except requests.RequestException as e:
            print(f"[warn] resolve ลิงก์ล้มเหลว: {e}")
            return None
        if status in (301, 302, 303, 307, 308) and location:
            current = urljoin(current, location)
            continue
        return current
    print("[warn] redirect เกินจำนวน hop ที่กำหนด")
    return None


def try_parse_google_maps_link(text: str):
    """
    ลองแกะพิกัดจากลิงก์ Google Maps ที่ปรากฏในข้อความ
    รองรับทั้งลิงก์เต็ม (มีพิกัดอยู่ใน URL ตรงๆ) และลิงก์ย่อ (ต้อง resolve
    redirect ก่อนถึงจะเห็น URL เต็มที่มีพิกัด)
    """
    url_match = re.search(r"https?://\S+", text)
    if not url_match:
        return None

    url = url_match.group(0)

    if not _looks_like_google_maps_link(url):
        print(f"[info] ลิงก์ไม่ใช่ Google Maps: {url}")
        return None

    # กรณีลิงก์เต็มที่มีพิกัดฝังอยู่แล้ว ไม่ต้อง resolve redirect
    coords = _extract_coords_from_url(url)
    if coords and _is_plausible_thailand_coord(*coords):
        return coords

    # กรณีลิงก์ย่อ (goo.gl/maps, maps.app.goo.gl) ต้อง resolve redirect ก่อน
    # เพื่อดู URL ปลายทางจริงที่มักมีพิกัดฝังอยู่
    #
    # ใช้ GET แทน HEAD เพราะเซิร์ฟเวอร์บางตัว (รวมถึงของ Google บางเส้นทาง)
    # ตอบ HEAD request ไม่ถูกต้อง/ไม่ redirect ตามที่ควร ทำให้ resolve
    # ไม่สำเร็จ ใช้ stream=True เพื่อไม่ต้องโหลด body ทั้งหมดลงมา
    # (เราสนใจแค่ resp.url ที่ได้หลัง redirect เท่านั้น)
    final_url = _resolve_redirect_safely(url)
    if not final_url:
        return None
    print(f"[info] resolve ลิงก์ย่อ '{url}' -> '{final_url}'")

    coords = _extract_coords_from_url(final_url)
    if coords and _is_plausible_thailand_coord(*coords):
        return coords

    # ไม่มีพิกัดตัวเลขใน URL (ลิงก์ชื่อสถานที่) -> fallback เดาจากชื่อสถานที่ใน URL
    # (เคยลอง CID lookup แล้วไม่ได้ผล จึงตัดออก)
    place_name = _extract_place_name_from_url(final_url)
    if place_name:
        print(f"[info] URL ไม่มีพิกัดตัวเลข ลองใช้ชื่อสถานที่จาก URL แทน: '{place_name}'")
        result = try_geocode_address(place_name)
        if result:
            return result

    print(f"[warn] resolve ลิงก์สำเร็จแต่หาพิกัดใน URL ปลายทางไม่เจอ: {final_url}")
    return None


# ---------------------------------------------------------------------------
# 3) ชื่อสถานที่ / ตำบล / อำเภอ / จังหวัด (ต้อง geocode)
# ---------------------------------------------------------------------------

# ใช้ Nominatim (OpenStreetMap) เพราะเป็นบริการ geocoding ฟรี ไม่ต้องขอ API key
# ข้อจำกัด: ต้องใส่ User-Agent ตามนโยบายของ Nominatim และมี rate limit
# (แนะนำไม่เกิน 1 request/วินาที) ความแม่นยำอาจสู้ Google Geocoding API ไม่ได้
# แต่ใช้งานได้จริงสำหรับชื่อจังหวัด/อำเภอ/ตำบลในประเทศไทยส่วนใหญ่
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_HEADERS = {"User-Agent": "FloodRiskChatbot/1.0 (student project)"}


def _geocode_single_query(query_text: str):
    """
    ยิง request ไปหา Nominatim ครั้งเดียวด้วยข้อความที่กำหนด
    คืนค่า (lat, lng) ถ้าเจอผลลัพธ์ที่น่าเชื่อถือพอ หรือ None ถ้าไม่เจอ
    """
    params = {
        "q": f"{query_text}, Thailand",
        "format": "json",
        "limit": 1,
        "countrycodes": "th",
        "addressdetails": 1,
    }

    try:
        resp = requests.get(
            _NOMINATIM_URL, params=params, headers=_NOMINATIM_HEADERS, timeout=8
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[warn] Nominatim request ล้มเหลวสำหรับ '{query_text}': {e}")
        return None

    if not results:
        print(f"[warn] Nominatim ไม่เจอผลลัพธ์เลยสำหรับ '{query_text}'")
        return None

    top_result = results[0]

    importance = top_result.get("importance", 0)
    result_class = top_result.get("class", "")
    addresstype = top_result.get("addresstype", "")

    is_admin_class = result_class in ("boundary", "place")
    is_admin_addresstype = addresstype in (
        "village", "suburb", "town", "city", "county", "state", "province"
    )
    has_minimal_importance = importance >= 0.1

    if not (is_admin_class or is_admin_addresstype) and not has_minimal_importance:
        print(f"[warn] Nominatim เจอผลลัพธ์แต่ถูกกรองทิ้งสำหรับ '{query_text}': "
              f"class={result_class!r} addresstype={addresstype!r} importance={importance} "
              f"(display_name={top_result.get('display_name', 'ไม่ทราบ')!r})")
        return None

    lat = float(top_result["lat"])
    lng = float(top_result["lon"])

    if _is_plausible_thailand_coord(lat, lng):
        print(f"[info] Nominatim geocode สำเร็จสำหรับ '{query_text}': ({lat}, {lng})")
        return (lat, lng)

    print(f"[warn] Nominatim เจอผลลัพธ์แต่พิกัดอยู่นอกกรอบไทย: ({lat}, {lng})")
    return None


# ตัดเลขไปรษณีย์ 5 หลักท้ายข้อความออก เพราะเป็น "noise" ที่อาจทำให้
# free-text search ของ Nominatim สับสนได้
_TRAILING_POSTAL_CODE_PATTERN = re.compile(r"\s+\d{5}\s*$")


def try_geocode_address(text: str):
    """
    ลองแปลงชื่อสถานที่ (จังหวัด/อำเภอ/ตำบล) เป็นพิกัด ผ่าน Nominatim
    ควรเรียกเป็นทางเลือกสุดท้าย หลังจากลองจับพิกัด/ลิงก์แล้วไม่เจอ

    ใช้กลยุทธ์ "ลดทอนคำค้นทีละขั้น": ลองค้นด้วยข้อความเต็มก่อน (หลังตัด
    เลขไปรษณีย์ท้ายข้อความออกถ้ามี) ถ้าไม่เจอ ให้ตัดคำท้ายๆ ออกทีละคำ
    แล้วลองใหม่ เพิ่มโอกาสเจอผลลัพธ์แม้จะหยาบขึ้นบ้าง
    """
    cleaned_text = _TRAILING_POSTAL_CODE_PATTERN.sub("", text.strip())

    result = _geocode_single_query(cleaned_text)
    if result:
        return result

    words = cleaned_text.strip().split()
    for cut in range(1, min(4, len(words))):
        shortened = " ".join(words[:-cut])
        if not shortened:
            break
        result = _geocode_single_query(shortened)
        if result:
            print(f"[info] geocode สำเร็จหลังลดทอนคำค้นเป็น '{shortened}'")
            return result

    print(f"[warn] geocode ล้มเหลวทุกความพยายามสำหรับข้อความต้นฉบับ: '{text}'")
    return None


# ---------------------------------------------------------------------------
# ฟังก์ชันหลัก — ไล่ลองทีละวิธีตามลำดับความน่าเชื่อถือ
# ---------------------------------------------------------------------------

def try_parse_safe_location(text: str):
    """
    ลองจับพิกัดเฉพาะวิธีที่ "ปลอดภัย/แม่นยำสูง" เท่านั้น (พิกัดตรงๆ + ลิงก์
    Google Maps) ไม่รวม geocode ชื่อสถานที่ เพราะ geocode มีโอกาส false
    positive กับข้อความทั่วไปได้ (ดูปัญหาที่เจอจริงใน try_geocode_address)

    ควรเรียกใช้ฟังก์ชันนี้ก่อนเสมอ ก่อนจะพิจารณาว่าข้อความอาจเป็นชื่อสถานที่
    หรือเป็นคำถาม/บทสนทนาทั่วไป (ดูการใช้งานใน message_handler.py)
    """
    result = try_parse_latlng_text(text)
    if result:
        return result

    result = try_parse_google_maps_link(text)
    if result:
        return result

    return None


def parse_location_from_text(text: str):
    """
    ไล่ลองแปลงข้อความเป็นพิกัดตามลำดับ: พิกัดตรงๆ -> ลิงก์ Google Maps
    -> ชื่อสถานที่ (geocode) คืนค่า (lat, lng) ถ้าเจอ หรือ None ถ้าไม่เจอเลย

    หมายเหตุ: ฟังก์ชันนี้รวม geocode ไว้ด้วย ใช้เมื่อมั่นใจแล้วว่าข้อความ
    น่าจะเป็นชื่อสถานที่ (เช่น ผ่านการจำแนกเจตนาด้วย LLM มาก่อนแล้ว)
    ถ้าต้องการแค่เช็คแบบปลอดภัยไม่มี false positive ให้ใช้
    try_parse_safe_location() แทน
    """
    result = try_parse_safe_location(text)
    if result:
        return result

    result = try_geocode_address(text)
    if result:
        return result

    return None