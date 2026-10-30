"""test_satellite.py — เทสต์การเก็บ/ตอบน้ำท่วมจากดาวเทียมย้อนหลัง โดยไม่ยิง API จริง (python test_satellite.py)"""
import os, sys, tempfile, types
from datetime import date

for name in ["dotenv", "google", "google.generativeai", "pinecone", "shapely", "shapely.geometry", "pypdf"]:
    if name not in sys.modules:
        try: __import__(name)
        except ImportError: sys.modules[name] = types.ModuleType(name)
sys.modules["dotenv"].load_dotenv = getattr(sys.modules["dotenv"], "load_dotenv", lambda *a, **k: None)
os.environ["HISTORY_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")   # ไม่แตะ history.db จริง
os.environ["GISTDA_API_KEY"] = "x"

import satellite_history as sh, historical as hi

def feat(fn, pv): return {"properties": {"file_name": fn, "pv_tn": pv}}

FEATS = [feat("S1D_20260805_1820", "สกลนคร")] * 4 + [feat("S1D_20260805_1", "นครพนม"),
         feat("S1D_20260801_9", "สกลนคร"), feat("junk", "สกลนคร")]

def seed():
    """เทสต์รันเรียงตามตัวอักษร จึงเตรียมข้อมูลเองในแต่ละเทสต์ ไม่พึ่งลำดับ"""
    sh.save_rows(sh.summarize_features(FEATS))

def test_summarize_and_save():
    rows = sh.summarize_features(FEATS)
    assert {(r["date"], r["province"]): r["feature_count"] for r in rows} == {
        ("2026-08-05", "สกลนคร"): 4, ("2026-08-05", "นครพนม"): 1, ("2026-08-01", "สกลนคร"): 1}
    db = os.path.join(tempfile.mkdtemp(), "own.db")             # ฐานข้อมูลแยกเฉพาะเทสต์นี้ ไม่ปนกับเทสต์อื่นที่รันก่อนหน้า
    sh.save_rows(rows, db); sh.save_rows(rows, db)               # รันซ้ำ ไม่ซ้ำแถว
    assert sh.get_coverage(db) == ("2026-08-01", "2026-08-05")
    assert [r["feature_count"] for r in sh.get_day("2026-08-05", db)] == [4, 1]

def test_multi_date_filename_not_counted():
    """file_name หลายภาพ = ภาพรวม (ทุกแปลงเหมือนกัน) บอกวันท่วมไม่ได้ -> ไม่นับ ไม่ใช่นับทุกวัน"""
    f = feat("rd2_20260926_0613, S1D_20261009_0559, S1C_20260928_0550", "บึงกาฬ")
    assert sh.summarize_features([f, f]) == []
    assert sh.summarize_features([feat("S1C_20261010_0550", "บึงกาฬ")])[0]["date"] == "2026-10-10"
    assert sh.summarize_features([feat("area_20481851", "x")]) == []

def test_province_prefix_and_whole_word_match():
    """GISTDA ส่ง 'จ.สกลนคร' -> เก็บเป็น 'สกลนคร' และจับคู่กับ 'จังหวัดสกลนคร' ได้; 'ตาก' ต้องไม่ไปจับ 'ตากใบ'"""
    rows = sh.summarize_features([feat("S1C_20261010_0550", "จ.สกลนคร"), feat("S1C_20261010_0550", "จังหวัดตาก")])
    assert {r["province"] for r in rows} == {"สกลนคร", "ตาก"}
    assert sh.match_province("จังหวัดสกลนคร", rows) == "สกลนคร"
    assert sh.match_province("จ.สกลนคร วันที่ 10", rows) == "สกลนคร"
    assert sh.match_province("อำเภอตากใบ นราธิวาส", rows) is None
    assert sh.match_province("ตาก", rows) == "ตาก"
    assert sh.match_province("พิกัด 17.1, 104.1", rows) is None

def test_migrate_old_prefixed_rows():
    import sqlite3
    db = os.path.join(tempfile.mkdtemp(), "mig.db")
    with sqlite3.connect(db) as c:
        c.executescript(sh._SCHEMA)
        c.executemany("INSERT INTO satellite_daily VALUES (?,?,?)",
                      [("2026-10-10", "จ.สกลนคร", 80), ("2026-10-10", "สกลนคร", 5), ("2026-10-11", "จ.นครพนม", 3)])
    got = {r["province"]: r["feature_count"] for r in sh.get_day("2026-10-10", db)}
    assert got == {"สกลนคร": 80}                                     # รวมชื่อซ้ำด้วย MAX
    assert sh.get_day("2026-10-11", db)[0]["province"] == "นครพนม"

def test_match_province():
    seed()
    rows = sh.get_day("2026-08-05")
    assert sh.match_province("จังหวัดสกลนคร", rows) == "สกลนคร"
    assert sh.match_province("อำเภอเมือง จันทบุรี", rows) is None

class _R:
    def __init__(s, d, status=200): s.d, s.status_code = d, status
    def raise_for_status(s): pass
    def json(s): return s.d

def _fake_api(feats, fail_bbox=None, flaky=0):
    """จำลอง GISTDA: ตอบทีละ 10 ตาม offset, แถบที่ระบุล้มตลอด, flaky = จำนวนครั้งแรกที่ตัดการเชื่อมต่อ"""
    import requests
    state = {"calls": 0}
    def get(url, headers=None, params=None, timeout=None):
        state["calls"] += 1
        if state["calls"] <= flaky:
            raise requests.ConnectionError("Remote end closed connection without response")
        if fail_bbox and params["bbox"] == fail_bbox:
            raise requests.ConnectionError("down")
        page = feats[params["offset"]: params["offset"] + 10]
        return _R({"numberMatched": len(feats), "numberReturned": len(page), "features": page})
    return get

class _R:
    def __init__(s, d, code=200): s.d, s.status_code = d, code
    def raise_for_status(s): pass
    def json(s): return s.d

def _paged(feats_by_tile, fail_tiles=()):
    """API ปลอม: ตอบทีละ 10 รายการตาม bbox ของช่อง และให้บางช่องล้ม"""
    def get(url, headers=None, params=None, timeout=None):
        bbox = params["bbox"]
        if bbox in fail_tiles:
            raise sh_requests.ConnectionError("Remote end closed connection")
        feats = feats_by_tile.get(bbox, [])
        page = feats[params["offset"]: params["offset"] + 10]
        return _R({"numberMatched": len(feats), "numberReturned": len(page), "features": page})
    return get

import requests as sh_requests
TILES = ["0,0,1,1", "1,0,2,1", "2,0,3,1"]

def test_tiles_cover_thailand():
    tiles = sh._tiles()
    lats = sorted((float(x.split(",")[1]), float(x.split(",")[3])) for x in tiles)
    assert lats[0][0] == 5.6 and abs(lats[-1][1] - 20.5) < 1e-3
    assert all(abs(lats[i][1] - lats[i + 1][0]) < 1e-3 for i in range(len(lats) - 1))   # ไม่มีช่องว่างระหว่างแถบ

def test_sync_dedupes_across_tiles_and_pages():
    shared = dict(feat("S1D_20260807_1", "กาฬสินธุ์"), id="shared")          # คร่อม 2 ช่อง
    own = [dict(feat("S1D_20260807_1", "กาฬสินธุ์"), id=f"f{i}") for i in range(22)]
    out = sh.sync_from_gistda(get=_paged({TILES[0]: own + [shared], TILES[1]: [shared]}),
                              tiles=TILES, sleep=lambda s: None)
    assert out["fetched"] == 23 and out["complete"] is True and out["tiles_failed"] == []
    assert sh.get_day("2026-08-07")[0]["feature_count"] == 23

def test_sync_survives_failed_tile_and_keeps_max():
    sh.save_rows([{"date": "2026-08-09", "province": "สกลนคร", "feature_count": 50}])   # เคยเก็บครบไว้แล้ว
    few = [dict(feat("S1D_20260809_1", "สกลนคร"), id=f"g{i}") for i in range(5)]
    out = sh.sync_from_gistda(get=_paged({TILES[0]: few}, fail_tiles=(TILES[1],)),
                              tiles=TILES, sleep=lambda s: None)
    assert out["complete"] is False and out["tiles_failed"] == [TILES[1]] and out["fetched"] == 5
    assert sh.get_day("2026-08-09")[0]["feature_count"] == 50       # ไม่ถูกค่าที่ขาดทับ

def _geo_get(points):
    """API ปลอมที่กรองตามตำแหน่งจริงใน bbox และตอบทีละ 10 รายการ (เหมือน GISTDA)"""
    def get(url, headers=None, params=None, timeout=None):
        x0, y0, x1, y1 = (float(v) for v in params["bbox"].split(","))
        inside = [f for f in points if x0 <= f["geometry"]["coordinates"][0] <= x1
                  and y0 <= f["geometry"]["coordinates"][1] <= y1]
        page = inside[params["offset"]: params["offset"] + 10]
        return _R({"numberMatched": len(inside), "numberReturned": len(page), "features": page})
    return get

def _pt(i, x, y):
    f = dict(feat("S1D_20260810_1", "ลพบุรี"), id=f"p{i}")
    f["geometry"] = {"type": "Point", "coordinates": [x, y]}
    return f

def test_sync_splits_tile_that_hits_cap():
    old = sh.SYNC_MAX_PAGES; sh.SYNC_MAX_PAGES = 2            # เพดาน 20 รายการต่อคำขอ
    try:
        pts = [_pt(i, 0.1 + (i % 10) * 0.35, 0.1 + (i // 10) * 0.6) for i in range(60)]   # 60 จุดกระจายในช่อง 0..4
        out = sh.sync_from_gistda(get=_geo_get(pts), tiles=["0,0,4,4"], sleep=lambda s: None)
    finally:
        sh.SYNC_MAX_PAGES = old
    assert out["fetched"] == 60 and out["complete"] is True and out["still_truncated"] == []
    assert out["requests_regions"] > 1
    assert sh.get_day("2026-08-10")[0]["feature_count"] == 60

def test_sync_keeps_partial_pages_when_422_midway_and_splits():
    class Bad422:
        status_code = 422
        def raise_for_status(s): raise sh_requests.HTTPError("422 Unprocessable Entity")
    pts = [_pt(i, 0.2 + (i % 10) * 0.35, 0.2 + (i // 10) * 0.6) for i in range(60)]
    base = _geo_get(pts)
    def get(url, headers=None, params=None, timeout=None):
        if params["bbox"] == "0,0,4,4" and params["offset"] >= 20:      # ช่องใหญ่ล้มที่ offset สูง
            return Bad422()
        return base(url, headers=headers, params=params, timeout=timeout)
    out = sh.sync_from_gistda(get=get, tiles=["0,0,4,4"], sleep=lambda s: None)
    assert out["fetched"] == 60 and out["complete"] is True and out["tiles_failed"] == []

def test_sync_first_page_failure_marks_tile_failed():
    def get(url, headers=None, params=None, timeout=None): raise sh_requests.ConnectionError("closed")
    out = sh.sync_from_gistda(get=get, tiles=["0,0,1,1"], sleep=lambda s: None)
    assert out["tiles_failed"] == ["0,0,1,1"] and out["complete"] is False and out["fetched"] == 0

def test_sync_reports_still_truncated_at_max_depth():
    old = sh.SYNC_MAX_PAGES; sh.SYNC_MAX_PAGES = 1            # เพดาน 10
    try:
        pts = [_pt(i, 1.0, 1.0) for i in range(30)]           # 30 จุดซ้อนตำแหน่งเดียว แบ่งช่องเท่าไรก็ไม่หมดเพดาน
        out = sh.sync_from_gistda(get=_geo_get(pts), tiles=["0,0,2,2"], sleep=lambda s: None)
    finally:
        sh.SYNC_MAX_PAGES = old
    assert out["complete"] is False and out["still_truncated"]
    assert out["fetched"] == 10                               # ได้เท่าที่เพดานให้ ไม่วนไม่จบ

def test_progress_saved_when_interrupted():
    """กด Ctrl+C (หรือเน็ตหลุดแบบไม่ใช่ RequestException) กลางทาง: ช่องที่เสร็จแล้วต้องอยู่ในฐานข้อมูล"""
    feats = [dict(feat("S1D_20260811_1", "น่าน"), id=f"n{i}") for i in range(7)]
    def get(url, headers=None, params=None, timeout=None):
        if params["bbox"] == TILES[1]:
            raise KeyboardInterrupt()
        page = feats[params["offset"]: params["offset"] + 10] if params["bbox"] == TILES[0] else []
        return _R({"numberMatched": len(feats) if params["bbox"] == TILES[0] else 0, "features": page})
    try:
        sh.sync_from_gistda(get=get, tiles=TILES, sleep=lambda s: None); assert False
    except KeyboardInterrupt:
        pass
    assert sh.get_day("2026-08-11")[0]["feature_count"] == 7

def test_default_endpoint_is_7days_and_full_is_30days():
    urls = []
    def get(url, headers=None, params=None, timeout=None):
        urls.append(url); return _R({"numberMatched": 0, "features": []})
    sh.sync_from_gistda(get=get, tiles=["0,0,1,1"], sleep=lambda s: None)
    sh.sync_from_gistda(get=get, tiles=["0,0,1,1"], sleep=lambda s: None, endpoint=sh.FULL_ENDPOINT)
    assert urls[0].endswith("/features/flood/7days") and urls[1].endswith("/features/flood/30days")

def test_requests_ask_for_page_size_100_and_cooldown_after_error():
    seen_limits, sleeps = [], []
    class Bad:
        status_code = 422
        def raise_for_status(s): raise sh_requests.HTTPError("422")
    feats = [dict(feat("S1D_20260812_1", "ตาก"), id=f"t{i}") for i in range(30)]
    def get(url, headers=None, params=None, timeout=None):
        seen_limits.append(params["limit"])
        if params["offset"] >= 10 and params["bbox"] == "0,0,2,2":
            return Bad()
        page = [f for f in feats][params["offset"]: params["offset"] + 10]
        return _R({"numberMatched": 30, "features": page})
    sh.sync_from_gistda(get=get, tiles=["0,0,2,2"], sleep=sleeps.append)
    assert set(seen_limits) == {sh.SYNC_PAGE_SIZE} and sh.SYNC_PAGE_SIZE == 100
    assert sh.COOLDOWN_AFTER_ERROR_SEC in sleeps                      # หลัง error พักนานขึ้น

def test_retrying_retries_then_gives_up():
    sh.RETRY_DELAYS = (0, 0, 0)                                    # ไม่รอจริงตอนเทสต์
    calls = []
    def flaky(url, headers=None, params=None, timeout=None):
        calls.append(1)
        if len(calls) < 3: raise sh_requests.ConnectionError("closed")
        return _R({"features": []})
    assert sh._retrying(flaky)("u", params={}).status_code == 200 and len(calls) == 3
    calls.clear()
    def bad(url, headers=None, params=None, timeout=None): calls.append(1); raise sh_requests.ConnectionError("closed")
    try: sh._retrying(bad)("u", params={}); assert False
    except sh_requests.ConnectionError: assert len(calls) == 4      # ครั้งแรก + ลองใหม่ 3 ครั้ง
    calls.clear()
    def http503(url, headers=None, params=None, timeout=None): calls.append(1); return _R({}, 503)
    try: sh._retrying(http503)("u", params={}); assert False
    except sh_requests.HTTPError: assert len(calls) == 4

def run(place="จังหวัดสกลนคร", day="2026-08-05", **kw):
    base = dict(extract_date=lambda t: day, extract_place=lambda t: place, geocode=lambda p: (17.15, 104.14),
                station_fn=lambda *a: None, range_fn=lambda: (None, None), today=date(2026, 10, 8))
    base.update(kw); return hi.answer_historical("q", **base)

def test_answer_with_satellite():
    seed()
    out = run()
    assert "ใน" in out and "สกลนคร 4 แห่ง" in out
    assert "ไม่ใช่การยืนยันเหตุการณ์จริง" in out
    out = run(place="จังหวัดจันทบุรี")
    assert "ทั่วประเทศ" not in out and "ไม่พบบันทึกน้ำท่วมจากดาวเทียมของพื้นที่นี้" in out
    out = run(day="2026-07-01")
    assert "ไม่มีบันทึกน้ำท่วมจากดาวเทียมของวันที่ 2026-07-01" in out and "2026-08-01" in out

def test_answer_when_empty_or_broken():
    assert "ยังไม่มีบันทึกน้ำท่วมจากดาวเทียม" in run(sat_range_fn=lambda: (None, None))
    boom = lambda *a: (_ for _ in ()).throw(RuntimeError("db"))
    out = run(sat_range_fn=boom)
    assert "ไม่ใช่การยืนยันเหตุการณ์จริง" in out                # พังแล้วข้อความหลักยังอยู่

def test_run_snapshot_isolates_failures():
    import daily_snapshot as ds
    ds.fetch_all_stations = lambda: (_ for _ in ()).throw(RuntimeError("thaiwater down"))
    ds.sync_from_gistda = lambda **kw: {"rows_saved": 3, "fetched": 9, "complete": True}
    r = ds.run_snapshot()
    assert "station_error" in r and r["satellite"]["rows_saved"] == 3
    ds.fetch_all_stations = lambda: []
    ds.sync_from_gistda = lambda **kw: (_ for _ in ()).throw(RuntimeError("gistda down"))
    r = ds.run_snapshot()
    assert r["saved_rows"] == 0 and "satellite_error" in r

def test_daily_run_uses_3days_and_full_uses_30days():
    import daily_snapshot as ds
    seen = []
    ds.fetch_all_stations = lambda: []
    ds.sync_from_gistda = lambda **kw: seen.append(kw["endpoint"]) or {"rows_saved": 0}
    ds.run_snapshot(); ds.run_snapshot(full=True)
    assert seen == ["/features/flood/3days", "/features/flood/30days"]

if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try: fn(); print("PASS", name)
            except Exception as e:
                import traceback; fails += 1; print("FAIL", name, "->", repr(e)); traceback.print_exc()
    print("\nทั้งหมดผ่าน" if not fails else f"\nล้มเหลว {fails} เทสต์"); sys.exit(1 if fails else 0)
