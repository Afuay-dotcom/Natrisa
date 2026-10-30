"""
test_fixes.py — เทสต์ข้อ 2, 3, 4 โดยไม่ต้องยิง API จริง
รัน: python test_fixes.py   (หรือ pytest test_fixes.py)
"""
import glob, importlib.util, os, sys, types
from datetime import datetime, timedelta, timezone

os.environ.setdefault("GISTDA_API_KEY", "test-key")

# ---- stub shapely เฉพาะตอนที่เครื่องไม่มี shapely (เครื่องจริงใช้ shapely ตัวจริง) ----
try:
    import shapely  # noqa
except ImportError:
    class _Pt:
        def __init__(s, x, y): s.x, s.y = x, y
    class _Poly:
        is_valid = True
        def __init__(s, rings): s.rings = rings
        def buffer(s, _): return s
        def covers(s, p):
            ring = s.rings[0]; inside = False
            for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
                if (y1 > p.y) != (y2 > p.y) and p.x < (x2 - x1) * (p.y - y1) / (y2 - y1) + x1:
                    inside = not inside
            return inside
    m = types.ModuleType("shapely"); g = types.ModuleType("shapely.geometry")
    g.Point = _Pt; g.shape = lambda d: _Poly(d["coordinates"])
    sys.modules["shapely"], sys.modules["shapely.geometry"] = m, g

import thaiwater_station as ts
import flood_check as fc
from fix_thai_text import fix_thai_text, count_broken

BKK = timezone(timedelta(hours=7))
def now_str(): return datetime.now(BKK).strftime("%Y-%m-%d %H:%M")

# ============================ ข้อ 2: ข้อความไทย ============================
def test_thai_fix():
    assert fix_thai_text("จ\ufffdานวน") == "จำนวน"
    assert fix_thai_text("น\ufffd้าท่วม") == "น้ำท่วม"
    assert fix_thai_text("น\ufffd้ำท่วม") == "น้ำท่วม"
    assert fix_thai_text("ส\ufffd าหรับ") == "สำหรับ"
    assert count_broken(fix_thai_text("ค\ufffdาแนะน\ufffdา")) == 0

# ============================ ข้อ 3: classify_station_risk ============================
def st(**kw):
    base = dict(waterlevel_msl=10.0, critical_level_msl=12.0, waterlevel_datetime=now_str(),
                agency_name="กฟผ.")
    base.update(kw); return base

def test_critical_zero_is_not_a_threshold():
    r = ts.classify_station_risk(st(waterlevel_msl=67.84, critical_level_msl=0))
    assert r["level"] == "unknown" and r["emoji"] == "⚪"

def test_sentinel_waterlevel():
    r = ts.classify_station_risk(st(waterlevel_msl=32765.9, critical_level_msl=5))
    assert r["level"] == "unknown"

def test_stale_data():
    old = (datetime.now(BKK) - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M")
    assert ts.classify_station_risk(st(waterlevel_datetime=old))["level"] == "unknown"

def test_normal_cases_unchanged():
    assert ts.classify_station_risk(st(waterlevel_msl=5, critical_level_msl=12))["level"] == "ปกติ"
    assert ts.classify_station_risk(st(waterlevel_msl=11.7, critical_level_msl=12))["level"] == "เฝ้าระวัง"
    r = ts.classify_station_risk(st(waterlevel_msl=12.5, critical_level_msl=12))
    assert r["level"] == "วิกฤต" and "0.50" in r["text"]

def test_unparseable_datetime_does_not_block():
    assert ts.classify_station_risk(st(waterlevel_datetime="???"))["level"] == "ปกติ"

def test_against_real_snapshot():
    import pandas as pd
    path = glob.glob("/mnt/user-data/uploads/*thaiwater_snapshot*.csv")
    if not path: return
    spec = importlib.util.spec_from_file_location("old_ts", glob.glob("/mnt/user-data/uploads/*thaiwater_station.py")[0])
    old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
    df = pd.read_csv(path[0]); counts_old, counts_new = {}, {}
    for _, row in df.iterrows():
        s = dict(waterlevel_msl=row.waterlevel_msl, critical_level_msl=row.critical_level_msl,
                 waterlevel_datetime=now_str(), agency_name=row.agency_name_th, station_name=row.station_name_th)
        a = old.classify_station_risk(s)["level"]; b = ts.classify_station_risk(s)["level"]
        counts_old[a] = counts_old.get(a, 0) + 1; counts_new[b] = counts_new.get(b, 0) + 1
    print("\n  [snapshot] ก่อนแก้:", counts_old, "\n  [snapshot] หลังแก้:", counts_new)
    assert counts_new.get("วิกฤต", 0) < counts_old.get("วิกฤต", 0)

# ============================ ข้อ 4: flood_check ============================
class FakeResp:
    def __init__(s, data, status=200): s._d, s.status_code = data, status
    def raise_for_status(s):
        if s.status_code != 200:
            import requests; raise requests.HTTPError(str(s.status_code))
    def json(s): return s._d

def square(cx, cy, r=0.001):
    return {"type": "Polygon", "coordinates": [[[cx-r, cy-r], [cx+r, cy-r], [cx+r, cy+r], [cx-r, cy+r], [cx-r, cy-r]]]}

def feat(i, geom): return {"type": "Feature", "id": f"f{i}", "geometry": geom, "properties": {"i": i}}

def paged_get(features_by_path, honor_offset=True, calls=None):
    """จำลอง API: คืนทีละ 10 รายการ (เหมือนที่เจอจริง) ตาม offset"""
    def get(url, headers=None, params=None, timeout=None):
        path = url.replace(fc.BASE_URL, "")
        if calls is not None: calls.append((path, params.get("offset")))
        feats = features_by_path.get(path, [])
        off = params["offset"] if honor_offset else 0
        page = feats[off: off + 10]
        return FakeResp({"numberMatched": len(feats), "numberReturned": len(page), "features": page})
    return get

LAT, LNG = 17.0, 104.0

def test_hit_beyond_first_page():
    far = [feat(i, square(LNG + 0.04, LAT + 0.04)) for i in range(24)]      # 24 อันแรกไม่ครอบจุด
    target = feat(99, square(LNG, LAT))                                       # อันที่ 25 ครอบจุด
    r = fc.check_flood_risk(LAT, LNG, get=paged_get({"/features/flood/1day": far + [target]}))
    assert r["risk"] == "high" and r["properties"]["i"] == 99

def test_old_logic_would_have_missed_it():
    """พิสูจน์ว่าบั๊กเดิมมีจริง: เช็คแค่หน้าแรก (10 รายการ) จะไม่เจอ"""
    far = [feat(i, square(LNG + 0.04, LAT + 0.04)) for i in range(24)]
    first_page = (far + [feat(99, square(LNG, LAT))])[:10]
    assert fc.find_containing_feature(LAT, LNG, first_page) is None

def test_falls_through_endpoints_when_nearby_polygon_misses():
    other = [feat(1, square(LNG + 0.03, LAT + 0.03))]          # 1day: มี polygon ใกล้ๆ แต่ไม่ครอบจุด
    mine = [feat(2, square(LNG, LAT))]                          # 7days: ครอบจุด
    r = fc.check_flood_risk(LAT, LNG, get=paged_get({"/features/flood/1day": other, "/features/flood/7days": mine}))
    assert r["risk"] == "high" and "7 วัน" in r["source"]

def test_low_and_unknown():
    near = [feat(1, square(LNG + 0.03, LAT + 0.03))]
    r = fc.check_flood_risk(LAT, LNG, get=paged_get({"/features/flood/3days": near}))
    assert r["risk"] == "low" and "3 วัน" in r["source"]
    assert fc.check_flood_risk(LAT, LNG, get=paged_get({}))["risk"] == "unknown"

def test_all_endpoints_fail_is_error_not_unknown():
    def boom(*a, **k): return FakeResp({}, status=500)
    assert fc.check_flood_risk(LAT, LNG, get=boom)["risk"] == "error"

def test_api_ignoring_offset_stops_and_flags_incomplete():
    calls = []
    feats = [feat(i, square(LNG + 0.04, LAT + 0.04)) for i in range(25)]
    r = fc.check_flood_risk(LAT, LNG, get=paged_get({"/features/flood/1day": feats}, honor_offset=False, calls=calls))
    assert r["risk"] == "low" and r["incomplete"] is True
    assert len([c for c in calls if c[0].endswith("1day")]) == 2   # หยุดหลังเจอหน้าซ้ำ ไม่วนไม่จบ

if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try: fn(); print("PASS", name)
            except Exception as e: fails += 1; print("FAIL", name, "->", repr(e))
    print("\nทั้งหมดผ่าน" if not fails else f"\nล้มเหลว {fails} เทสต์"); sys.exit(1 if fails else 0)
