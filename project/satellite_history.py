"""
satellite_history.py
====================
เก็บ "จำนวนพื้นที่น้ำท่วมที่ดาวเทียม GISTDA ตรวจพบ" รายวัน รายจังหวัด ลงใน SQLite
(ไฟล์เดียวกับ history_store.py = history.db คนละตาราง) เพื่อให้ historical.py ตอบได้ว่า
"วันนั้นดาวเทียมเห็นน้ำท่วมแถวจังหวัดนั้นไหม"

ข้อควรรู้ (ต้องบอกผู้ใช้/เขียนในเล่ม):
  - วันที่ = วันที่ดาวเทียมถ่ายภาพ (สกัดจาก properties.file_name เช่น S1D_20260805_1820)
    ไม่ใช่วันที่น้ำเริ่มท่วมจริง
  - feature_count = จำนวน polygon ที่ตรวจพบ ไม่ใช่พื้นที่ (ไร่/ตร.กม.) และไม่ใช่ความลึก
  - sync ครั้งแรกได้ย้อนหลัง ~30 วัน (ตามที่ /flood/30days ให้) หลังจากนั้นสะสมเรื่อยๆ ทุกครั้งที่ sync
  - วันที่ไม่มีแถว = "ไม่มีบันทึก" ไม่ได้แปลว่าไม่ท่วม
"""

import json
import os
import re
import sqlite3
import time
from collections import defaultdict

from dotenv import load_dotenv

from history_store import DB_PATH

load_dotenv()  # อ่าน .env เอง ไม่พึ่ง module อื่นโหลดให้

BASE_URL = "https://api-gateway.gistda.or.th/api/2.0/resources"
THAILAND_BBOX = "97.3,5.6,105.7,20.5"
SYNC_PAGE_SIZE = 100    # probe_gistda.py ยืนยันแล้วว่า server ยอมให้ขอ limit=100 (หน้าละ ~1.4 วินาที เร็วกว่าขอทีละ 10 ราว 10 เท่า)
SYNC_MAX_PAGES = 100    # ต่อช่อง = สูงสุด 10,000 รายการ (ถ้าไม่ครบจะแบ่งช่องย่อย / รายงาน complete=False)
PAUSE_BETWEEN_PAGES_SEC = 0.3        # พักระหว่างหน้า ไม่ยิงถี่เกินไป
COOLDOWN_AFTER_ERROR_SEC = 30        # หลังเจอ error จาก server พักนานขึ้นก่อนไปต่อ (รอบก่อนหลัง 422 ช่องถัดๆ ไปได้ 0 ทั้งหมด)

_DATE_IN_FILENAME = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS satellite_daily (
    date          TEXT NOT NULL,   -- YYYY-MM-DD วันที่ดาวเทียมถ่ายภาพ
    province      TEXT NOT NULL,
    feature_count INTEGER NOT NULL,
    PRIMARY KEY (date, province)
);
"""


_PROV_PREFIX = re.compile(r"^\s*(?:จังหวัด|จ\.)\s*")


def clean_province(name: str) -> str:
    """ตัดคำนำหน้า 'จ.' / 'จังหวัด' ออก (GISTDA ส่ง pv_tn เป็น 'จ.สกลนคร') เพื่อให้จับคู่กับข้อความผู้ใช้ได้"""
    return _PROV_PREFIX.sub("", name or "").strip()


_MIGRATED = set()


def _normalize_existing(conn):
    """แถวเก่าที่เก็บเป็น 'จ.สกลนคร' -> 'สกลนคร' (รวมค่าด้วย MAX ถ้ามีชื่อซ้ำ) ทำครั้งเดียวต่อฐานข้อมูลต่อ process"""
    rows = conn.execute("SELECT date, province, feature_count FROM satellite_daily "
                        "WHERE province LIKE 'จ.%' OR province LIKE 'จังหวัด%'").fetchall()
    for d, p, n in rows:
        new = clean_province(p)
        conn.execute("DELETE FROM satellite_daily WHERE date=? AND province=?", (d, p))
        conn.execute("INSERT INTO satellite_daily VALUES (?,?,?) ON CONFLICT(date, province) DO UPDATE SET "
                     "feature_count = MAX(feature_count, excluded.feature_count)", (d, new, n))


def _connect(db_path=None):
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path, timeout=30)
    conn.executescript(_SCHEMA)
    if path not in _MIGRATED:
        with conn:
            _normalize_existing(conn)
        _MIGRATED.add(path)
    return conn


def _dates_province(feature: dict):
    """
    [(วันที่ภาพ YYYY-MM-DD, จังหวัด)] ของ feature เดียว — เฉพาะแปลงที่ file_name ระบุภาพ "วันเดียว"
    ชั้น 30 วัน (และบางแปลงของ 3 วัน) เป็นภาพรวม: ทุกแปลงมี file_name เป็นรายการภาพต้นทางทั้งชุดเหมือนกันหมด
    (ตรวจแล้ว: ทุกวันได้ 177,450 แปลง) จึงบอกไม่ได้ว่าแปลงนั้นท่วมวันไหน -> ไม่นับ คืน []
    """
    props = feature.get("properties") or {}
    dates = []
    for m in _DATE_IN_FILENAME.finditer(props.get("file_name") or ""):
        mo, d = int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if iso not in dates:
            dates.append(iso)
    if len(dates) != 1:
        return []
    province = clean_province(props.get("pv_tn") or props.get("pv_en") or "ไม่ทราบจังหวัด") or "ไม่ทราบจังหวัด"
    return [(dates[0], province)]


def _date_province(feature: dict):
    """(วันที่ภาพ, จังหวัด) หรือ None ถ้าระบุวันเดียวไม่ได้"""
    keys = _dates_province(feature)
    return keys[0] if keys else None


def _rows_from_counts(counts: dict) -> list:
    return [{"date": d, "province": p, "feature_count": n} for (d, p), n in sorted(counts.items())]


def summarize_features(features: list) -> list:
    """จัดกลุ่ม feature ตาม (วันที่ภาพ, จังหวัด) ข้าม feature ที่สกัดวันที่ไม่ได้"""
    counts = defaultdict(int)
    for f in features:
        for key in _dates_province(f):
            counts[key] += 1
    return _rows_from_counts(counts)


def save_rows(rows: list, db_path=None, keep_max: bool = False) -> int:
    """
    บันทึกแถวรายวัน/จังหวัด
      keep_max=False: เขียนทับค่าเดิม (ใช้เมื่อดึงครบทุกส่วน)
      keep_max=True : เก็บค่าที่มากกว่า (ใช้เมื่อดึงไม่ครบ กันเอาค่าที่ขาดไปทับค่าที่เคยเก็บครบแล้ว)
    """
    sql = ("INSERT INTO satellite_daily VALUES (?,?,?) ON CONFLICT(date, province) DO UPDATE SET "
           + ("feature_count = MAX(feature_count, excluded.feature_count)" if keep_max
              else "feature_count = excluded.feature_count"))
    with _connect(db_path) as conn:
        conn.executemany(sql, [(r["date"], r["province"], int(r["feature_count"])) for r in rows])
    return len(rows)


def get_coverage(db_path=None):
    """(วันแรก, วันสุดท้าย) ที่มีบันทึกดาวเทียม หรือ (None, None)"""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT MIN(date), MAX(date) FROM satellite_daily").fetchone()
    return (row[0], row[1]) if row and row[0] else (None, None)


def get_dates(db_path=None) -> list:
    """วันที่ (YYYY-MM-DD) ทั้งหมดที่มีบันทึกดาวเทียม เรียงจากเก่าไปใหม่"""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT date FROM satellite_daily ORDER BY date").fetchall()
    return [r[0] for r in rows]


def get_day(date_iso: str, db_path=None) -> list:
    """แถวของวันนั้นทุกจังหวัด เรียงจำนวนมาก -> น้อย"""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT province, feature_count FROM satellite_daily WHERE date = ? "
            "ORDER BY feature_count DESC", (date_iso,)).fetchall()
    return [{"province": p, "feature_count": n} for p, n in rows]


_PROVINCE_ALIASES = {"กรุงเทพมหานคร": ("กรุงเทพ", "กทม"), "พระนครศรีอยุธยา": ("อยุธยา",)}


def match_province(place: str, rows: list):
    """
    หาจังหวัดในผลของวันนั้นที่ปรากฏในข้อความสถานที่ หรือ None
    เทียบทั้งคำ: ต้องขึ้นต้นข้อความ/หลังเว้นวรรค หรือมี 'จังหวัด'/'จ.' นำหน้า และจบคำ
    -> 'อำเภอตากใบ นราธิวาส' ไม่ไปจับ 'ตาก' (ชื่อยาวสุดก่อน)
    """
    text = place or ""
    for r in sorted(rows, key=lambda r: -len(r["province"])):
        prov = clean_province(r["province"])
        if not prov:
            continue
        for name in (prov,) + _PROVINCE_ALIASES.get(prov, ()):
            if re.search(r"(?:^|[\s,(])(?:จังหวัด|จ\.)?\s*" + re.escape(name) + r"(?=$|[\s,)\d]|วันที่|เมื่อ)", text):
                return r["province"]
    return None


def _tiles(bbox=THAILAND_BBOX, bands=6):
    """แบ่งกรอบประเทศไทยเป็นแถบตามละติจูด เพื่อให้แต่ละคำขอเล็กลง (server ไม่ต้องตอบก้อนเดียวใหญ่ๆ)"""
    lng0, lat0, lng1, lat1 = (float(x) for x in bbox.split(","))
    step = (lat1 - lat0) / bands
    return [f"{lng0},{lat0 + i * step:.4f},{lng1},{lat0 + (i + 1) * step:.4f}" for i in range(bands)]


# รอก่อนลองใหม่ (วินาที) เมื่อ server ตัดการเชื่อมต่อ/ช้า/ตอบ 429,5xx — เทสต์ตั้งเป็น (0, 0, 0)
RETRY_DELAYS = (2, 5, 10)
REQUEST_TIMEOUT = 60


def _retrying(get):
    """ห่อ requests.get ให้ลองใหม่เองเมื่อเจอ error ชั่วคราว (RemoteDisconnected, timeout, 429, 5xx)"""
    import time
    import requests

    def wrapped(url, headers=None, params=None, timeout=None):
        last = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                resp = get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                return resp
            except requests.RequestException as e:
                last = e
                if attempt < len(RETRY_DELAYS):
                    print(f"     [retry {attempt + 1}] {type(e).__name__} รอ {RETRY_DELAYS[attempt]} วินาทีแล้วลองใหม่")
                    time.sleep(RETRY_DELAYS[attempt])
        raise last
    return wrapped


def _feature_key(f):
    import json
    fid = f.get("id")
    return str(fid) if fid is not None else json.dumps(f.get("geometry"), sort_keys=True)[:300]


PAUSE_BETWEEN_TILES_SEC = 0.5

# ช่วงเวลาที่ดึงตามปกติ (รายวัน) = 7 วันล่าสุด ข้อมูลน้อยกว่า 30 วันหลายเท่า (30days ทั้งประเทศมีหลายหมื่น polygon
# ดึงทีละ 10 รายการใช้เวลาเป็นชั่วโมง) ใช้ 30days เฉพาะตอนต้องการเติมย้อนหลังเต็มที่ (daily_snapshot.py --full)
SYNC_ENDPOINT = "/features/flood/7days"
FULL_ENDPOINT = "/features/flood/30days"
MAX_SPLIT_DEPTH = 5   # แบ่งช่องที่เต็มเพดานซ้ำได้สูงสุด 5 ชั้น (ช่องเล็กสุด ~0.08° x 0.26°)


def _split4(bbox: str) -> list:
    """แบ่ง bbox "lng_min,lat_min,lng_max,lat_max" เป็น 4 ส่วนเท่าๆ กัน"""
    x0, y0, x1, y1 = (float(v) for v in bbox.split(","))
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    return [f"{a:.4f},{b:.4f},{c:.4f},{d:.4f}" for a, b, c, d in
            [(x0, y0, xm, ym), (xm, y0, x1, ym), (x0, ym, xm, y1), (xm, ym, x1, y1)]]


def _fetch_region_paged(bbox, api_key, get, endpoint=SYNC_ENDPOINT, on_feature=None, sleep=time.sleep):
    """
    ดึง feature ของ 1 ช่องทีละหน้า ส่งแต่ละ feature (ที่ไม่ซ้ำในช่องนี้) ให้ on_feature
    (ไม่เก็บทั้งก้อนในหน่วยความจำ — ทั้งประเทศ 30 วันมีหลายแสน polygon)
    คืน (จำนวนที่ได้, status, error)
      status "done"      ดึงครบ (หน้าหมด/ครบตาม numberMatched)
             "cap"       ชนเพดาน SYNC_MAX_PAGES (ข้อมูลน่าจะมากกว่านี้)
             "error"     หน้าที่ N>1 ล้ม (เช่น 422) -> เก็บหน้าที่ได้แล้วไว้ ไม่ทิ้งทั้งช่อง
             "dup_pages" API ส่งหน้าซ้ำ (ไม่สนใจ offset) แบ่งช่องก็ไม่ช่วย
    ถ้าหน้าแรกก็ล้ม จะ raise ให้ผู้เรียกบันทึกว่าช่องนี้ล้ม
    """
    import requests

    url = BASE_URL + endpoint
    headers = {"API-Key": api_key}
    seen_local, got = set(), 0
    matched, offset = None, 0

    for page_no in range(SYNC_MAX_PAGES):
        if page_no:
            sleep(PAUSE_BETWEEN_PAGES_SEC)
        params = {"bbox": bbox, "limit": SYNC_PAGE_SIZE, "offset": offset}
        try:
            resp = get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            if not got:
                raise
            return got, "error", e

        if matched is None:
            matched = data.get("numberMatched")
        page = data.get("features", [])
        if not page:
            return got, "done", None

        fresh = 0
        for f in page:
            k = _feature_key(f)
            if k not in seen_local:
                seen_local.add(k); got += 1; fresh += 1
                if on_feature:
                    on_feature(f)
        if fresh == 0:
            return got, "dup_pages", None

        offset += len(page)
        if matched is not None:
            if got >= matched:
                return got, "done", None
        elif len(page) < SYNC_PAGE_SIZE:
            return got, "done", None

    return got, "cap", None


def sync_from_gistda(get=None, db_path=None, tiles=None, sleep=time.sleep, endpoint=None) -> dict:
    """
    ดึงน้ำท่วมทั้งประเทศจาก GISTDA (ปกติ 7 วันล่าสุด = SYNC_ENDPOINT; เติมย้อนหลังเต็มที่ใช้ endpoint=FULL_ENDPOINT = 30 วัน) ทีละ "แถบ" (_tiles) แล้วบันทึกลงตาราง satellite_daily

    - แบ่งดึงเป็นแถบ: ขอทั้งประเทศก้อนเดียวตอบช้ามากและ GISTDA ตัดการเชื่อมต่อกลางคัน (RemoteDisconnected)
    - ช่องที่ "เต็มเพดาน" (ชน SYNC_MAX_PAGES) หรือ "ล้มกลางทาง" (เช่น 422 ที่ offset สูง) จะเก็บหน้าที่
      ได้แล้วไว้ แล้วแบ่งเป็น 4 ส่วนดึงซ้ำ (ลึกสุด MAX_SPLIT_DEPTH ชั้น) เพื่อให้นับไม่ต่ำกว่าจริง
    - polygon ที่คร่อมหลายช่องนับครั้งเดียว (dedupe ด้วย id/geometry)
    - ช่องที่ล้มตั้งแต่หน้าแรก -> tiles_failed; ช่องที่ยังไม่ครบทั้งที่แบ่งสุดแล้ว -> still_truncated
      ทั้งสองกรณีให้ complete=False แต่ข้อมูลที่ได้ยังถูกบันทึก (ใช้ค่ามากกว่า ไม่ทับค่าที่เคยเก็บครบ)

    - กด Ctrl+C หรือถูกขัดจังหวะกลางทาง: ข้อมูลของช่องที่ดึงเสร็จแล้วถูกบันทึกก่อน แล้วค่อยหยุด (ไม่เสียงานที่ทำไปแล้ว)

    คืน {"rows_saved", "fetched", "complete", "requests_regions", "tiles_failed", "still_truncated", "endpoint"}
    get/tiles/sleep ไว้ให้ทดสอบโดยไม่ยิง API จริง
    """
    import requests

    api_key = os.getenv("GISTDA_API_KEY")
    if not api_key:
        raise RuntimeError("ไม่พบ GISTDA_API_KEY ใน .env")

    if get is None:
        get = _retrying(requests.Session().get)
    endpoint = endpoint or SYNC_ENDPOINT

    seen, failed, truncated = set(), [], []
    counts = defaultdict(int)                 # (วันที่, จังหวัด) -> จำนวน polygon (ไม่เก็บตัว feature)
    state = {"regions": 0, "dup_pages": False}

    def add_feature(f):
        k = _feature_key(f)
        if k in seen:                         # polygon ที่คร่อมหลายช่องนับครั้งเดียว
            return
        seen.add(k)
        for dp in _dates_province(f):
            counts[dp] += 1

    def fetch_region(bbox, depth, label):
        state["regions"] += 1
        before = len(seen)
        try:
            got, status, err = _fetch_region_paged(bbox, api_key, get, endpoint, add_feature, sleep)
        except Exception as e:
            print(f"[warn] {label} ล้มเหลวตั้งแต่หน้าแรก: {e} — พัก {COOLDOWN_AFTER_ERROR_SEC} วินาที")
            failed.append(bbox)
            sleep(COOLDOWN_AFTER_ERROR_SEC)
            return
        new = len(seen) - before
        incomplete = status in ("cap", "error")
        note = ""
        if status == "error":
            note = f" — หยุดกลางทาง ({type(err).__name__}: {err})"
        elif status == "cap":
            note = " — เต็มเพดาน"
        if incomplete and depth < MAX_SPLIT_DEPTH:
            note += " แบ่งช่องย่อย"
        print(f"[sync] {label}: ได้ {got} (ใหม่ {new}) รวม {len(seen)}{note}")
        sleep(COOLDOWN_AFTER_ERROR_SEC if status == "error" else PAUSE_BETWEEN_TILES_SEC)

        if incomplete:
            if depth < MAX_SPLIT_DEPTH:
                for i, sub in enumerate(_split4(bbox), start=1):
                    fetch_region(sub, depth + 1, f"{label}.{i}")
            else:
                truncated.append(bbox)
        elif status == "dup_pages":
            state["dup_pages"] = True

    tiles = tiles or _tiles()
    try:
        for n, tile in enumerate(tiles, start=1):
            fetch_region(tile, 0, f"แถบ {n}/{len(tiles)}")
    except BaseException:
        # Ctrl+C / ถูกขัดจังหวะ: เก็บของที่ได้แล้วลงฐานข้อมูลก่อน (keep_max กันทับค่าที่เคยครบ) แล้วค่อยหยุดต่อ
        saved_partial = save_rows(_rows_from_counts(counts), db_path, keep_max=True)
        print(f"[sync] ถูกขัดจังหวะ — บันทึกที่ดึงมาแล้ว {len(seen)} รายการ ({saved_partial} แถว) ก่อนหยุด")
        raise

    complete = not failed and not truncated and not state["dup_pages"]
    if endpoint == FULL_ENDPOINT:   # ภาพรวม 30 วัน แยกรายวันไม่ได้ -> ไม่เขียน satellite_daily
        saved = 0
    else:
        saved = save_rows(_rows_from_counts(counts), db_path, keep_max=True)   # เก็บค่าสูงสุดเสมอ: GISTDA อาจตัดแปลงเก่าออกจากชั้น 3 วันภายหลัง
    return {"rows_saved": saved, "fetched": len(seen), "complete": complete,
            "requests_regions": state["regions"], "tiles_failed": failed, "still_truncated": truncated,
            "endpoint": endpoint}
