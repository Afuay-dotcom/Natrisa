"""test_history.py — เทสต์ฝนสะสม / SQLite / คำถามย้อนหลัง โดยไม่ยิง API จริง (python test_history.py)"""
import os, sys, tempfile, types
from datetime import date, datetime, timezone, timedelta

# stub โมดูลหนักที่ไม่จำเป็นต่อเทสต์ (เครื่องจริงใช้ตัวจริง)
for name in ["dotenv", "google", "google.generativeai", "pinecone", "shapely", "shapely.geometry", "pypdf"]:
    if name not in sys.modules:
        try: __import__(name)
        except ImportError: sys.modules[name] = types.ModuleType(name)
sys.modules["dotenv"].load_dotenv = getattr(sys.modules["dotenv"], "load_dotenv", lambda *a, **k: None)
if not hasattr(sys.modules["shapely.geometry"], "Point"):
    sys.modules["shapely.geometry"].Point = lambda *a: None
    sys.modules["shapely.geometry"].shape = lambda d: None
os.environ["GISTDA_API_KEY"] = "x"

# stub rag_query / location_parser ตั้งแต่ต้นไฟล์ (message_handler import สองตัวนี้ ซึ่งลาก pinecone/google ตัวจริงมาด้วย)
# ทำให้เทสต์ไม่พึ่งลำดับการรัน และรันได้แม้ไม่ได้ติดตั้งแพ็กเกจเหล่านั้น
_rq = types.ModuleType("rag_query")
for _n in ["answer_question", "classify_intent", "chat_naturally", "extract_date_from_text"]:
    setattr(_rq, _n, lambda *a, **k: "")
_rq.genai = None; _rq.INTENT_MODEL = "x"
_lp = types.ModuleType("location_parser")
_lp.try_parse_safe_location = lambda t: None; _lp.try_geocode_address = lambda t: None
sys.modules["rag_query"], sys.modules["location_parser"] = _rq, _lp

import rainfall as rf, history_store as hs, historical as hi, thaiwater_station as ts

class Resp:
    def __init__(s, d): s.d = d
    def raise_for_status(s): pass
    def json(s): return s.d

def hourly_get(url, params=None, timeout=None):
    # 4 วัน x 24 ชม. เริ่ม 2026-10-05T00:00 ฝน 1 มม./ชม. ทุกชั่วโมง; ชั่วโมงเดียวเป็น None
    from datetime import timedelta
    t0 = datetime(2026, 10, 5); times, vals = [], []
    for i in range(96):
        times.append((t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")); vals.append(1.0)
    vals[50] = None
    return Resp({"hourly": {"time": times, "precipitation": vals}})

# ---------- rainfall ----------
def test_classify():
    c = rf.classify_rain_24h
    assert c(0) == "ไม่มีฝน" and c(0.1) == "ฝนเล็กน้อย" and c(10.0) == "ฝนเล็กน้อย" and c(10.1) == "ฝนปานกลาง"
    assert c(35.0) == "ฝนปานกลาง" and c(35.1) == "ฝนหนัก" and c(90.0) == "ฝนหนัก" and c(90.1) == "ฝนหนักมาก"
    assert c(None) is None


# ---------- SQLite ----------
def rec(i, lat, lng, wl, crit=None, name="สถานี"):
    return {"station": {"id": i, "tele_station_name": {"th": f"{name}{i}"}, "tele_station_lat": lat,
                        "tele_station_long": lng, "critical_level_msl": crit},
            "agency": {"agency_name": {"th": "กรมชล"}}, "waterlevel_msl": wl, "waterlevel_datetime": "2026-09-21 07:00"}

def test_store():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    assert hs.get_available_range(db) == (None, None)
    recs = [rec(1, 17.1, 104.1, 150.2, 152.0), rec(2, 13.7, 100.5, 1.1)]
    assert hs.save_station_snapshot(recs, "2026-09-21", db) == 2
    hs.save_station_snapshot(recs, "2026-09-21", db)                    # รันซ้ำวันเดิม ไม่ซ้ำแถว
    assert hs.get_available_range(db) == ("2026-09-21", "2026-09-21")
    n = hs.get_nearest_station_on_date(17.0, 104.0, "2026-09-21", db_path=db)
    assert n["station_id"] == 1 and n["distance_km"] < 20
    assert hs.get_nearest_station_on_date(17.0, 104.0, "2026-09-20", db_path=db) is None      # ไม่มี snapshot วันนั้น
    assert hs.get_nearest_station_on_date(5.0, 100.0, "2026-09-21", max_km=50, db_path=db) is None  # ไกลเกินไป

# ---------- historical ----------
def test_nearest_prefers_usable_station():
    db = os.path.join(tempfile.mkdtemp(), "u.db")
    # สถานี 1 ใกล้สุดแต่ไม่มีเกณฑ์วิกฤต / สถานี 2 ไกลกว่าแต่มีครบ
    hs.save_station_snapshot([rec(1, 17.01, 104.0, 141.0, None, "ใกล้"), rec(2, 17.1, 104.0, 141.5, 145.0, "ครบ")], "2026-10-11", db)
    assert hs.get_nearest_station_on_date(17.0, 104.0, "2026-10-11", db_path=db)["station_id"] == 1
    assert hs.get_nearest_station_on_date(17.0, 104.0, "2026-10-11", db_path=db, prefer_usable=True)["station_id"] == 2
    # ไม่มีสถานีครบเลย -> ยังคืนสถานีใกล้สุดเหมือนเดิม
    db2 = os.path.join(tempfile.mkdtemp(), "u2.db")
    hs.save_station_snapshot([rec(1, 17.01, 104.0, 141.0, None, "ใกล้")], "2026-10-11", db2)
    assert hs.get_nearest_station_on_date(17.0, 104.0, "2026-10-11", db_path=db2, prefer_usable=True)["station_id"] == 1

def test_parse_date():
    assert hi.parse_date_safe("2026-09-21") == date(2026, 9, 21)
    assert hi.parse_date_safe("2569-09-21") == date(2026, 9, 21)     # ปี พ.ศ.
    assert hi.parse_date_safe("เมื่อวาน") is None and hi.parse_date_safe(None) is None
    assert hi.parse_date_safe("2026-13-40") is None

def run(q="วันที่ 21 กันยายน สกลนครท่วมไหม ท่วมสูงกี่เมตร", **kw):
    base = dict(extract_date=lambda t: "2026-09-21", extract_place=lambda t: "จังหวัดสกลนคร",
                geocode=lambda p: (17.15, 104.14),
                station_fn=lambda la, ln, d: None, range_fn=lambda: (None, None), today=date(2026, 10, 8))
    base.update(kw); return hi.answer_historical(q, **base)

def test_historical_flow():
    out = run()
    assert "ฝน" not in out.split("ℹ️")[0] 
    assert "ไม่ใช่การยืนยันเหตุการณ์จริง" in out and "เพิ่งเริ่มเก็บ" in out
    st = {"station_name": "สกลนคร", "distance_km": 3.2, "waterlevel_msl": 150.2,
          "critical_level_msl": 152.0, "waterlevel_datetime": "2026-09-21 07:00"}
    out = run(station_fn=lambda la, ln, d: st)
    assert "150.2 ม.รทก." in out and "เกณฑ์วิกฤต 152.0" in out
    out = run(station_fn=lambda la, ln, d: None, range_fn=lambda: ("2026-10-01", "2026-10-07"))
    assert "2026-10-01" in out


def test_verdict_flooded_or_not():
    st = lambda lv, cr=152.0: {"station_name": "สกลนคร", "distance_km": 3.2, "waterlevel_msl": lv,
                               "critical_level_msl": cr, "waterlevel_datetime": "2026-09-21 07:00"}
    sat_flood = lambda d: [{"date": d, "province": "สกลนคร", "feature_count": 7}]
    sat_other = lambda d: [{"date": d, "province": "นครพนม", "feature_count": 3}]
    # ดาวเทียมพบน้ำท่วมในจังหวัด = ท่วม (แม้ระดับน้ำสถานีปกติ)
    out = run(station_fn=lambda *a: st(140.0), sat_day_fn=sat_flood)
    assert "🔴 สรุป: พบน้ำท่วมในสกลนคร" in out
    # ไม่พบแปลงในจังหวัด + ระดับน้ำต่ำกว่าเกณฑ์เกิน 0.5 ม. = ยังไม่พบสัญญาณ (ไม่ฟันธงว่าไม่ท่วม)
    out = run(station_fn=lambda *a: st(150.2), sat_day_fn=sat_other)
    assert "🔵 สรุป: ระดับน้ำสถานีใกล้สุดยังต่ำกว่าเกณฑ์วิกฤต 1.8 ม." in out and "ยังสรุปไม่ได้ว่าพื้นที่นั้นไม่ท่วม" in out
    # ใกล้เกณฑ์ / ถึงเกณฑ์
    assert "🟡 สรุป" in run(station_fn=lambda *a: st(151.7), sat_day_fn=sat_other)
    assert "🔴 สรุป: ระดับน้ำสถานีใกล้สุดสูงถึง" in run(station_fn=lambda *a: st(152.3), sat_day_fn=sat_other)
    # ไม่มีสถานี / ค่าเสีย = สรุปไม่ได้
    assert "⚪ สรุป" in run(station_fn=lambda *a: None, sat_day_fn=sat_other)
    assert "⚪ สรุป" in run(station_fn=lambda *a: st(32765.9, 0), sat_day_fn=sat_other)
    # ค่าสถานีเก่ากว่าวันที่ถามเกิน 1 วัน (สถานีออฟไลน์) = ใช้สรุปไม่ได้
    old = {**st(150.2), "waterlevel_datetime": "2026-09-01 07:00"}
    assert "⚪ สรุป: ค่าของสถานีใกล้สุดเก่าเกินไป" in run(station_fn=lambda *a: old, sat_day_fn=sat_other)
    # วันที่ไม่มีข้อมูลดาวเทียมเลย: บอกตรงๆ ว่าไม่มีข้อมูลดาวเทียม ไม่ใช่ว่า "ไม่พบน้ำท่วม"
    out = run(station_fn=lambda *a: st(150.2), sat_day_fn=lambda d: [])
    assert "วันนั้นไม่มีข้อมูลดาวเทียม" in out

def test_flood_check_crash_does_not_kill_reply():
    import message_handler as mh
    def boom(la, ln): raise RuntimeError("ไม่พบ GISTDA_API_KEY")
    mh.check_flood_risk_live = boom
    mh.get_forecast_rain = lambda la, ln: None
    mh.fetch_all_stations = lambda: []; mh.find_nearest_station = lambda *a, **k: []
    out = mh.handle_location(17.1, 104.1)
    assert "ตรวจสอบข้อมูลดาวเทียม GISTDA ไม่สำเร็จ" in out

def test_llm_error_gives_outage_message():
    import message_handler as mh
    mh.try_parse_safe_location = lambda q: None
    mh.classify_intent = lambda q: "llm_error"
    out = mh.handle_text_question("น้ำท่วมต้องทำยังไง")
    assert "1784" in out and "ขัดข้อง" in out

def test_historical_edge_cases():
    assert "ระบุวันที่" in run(extract_date=lambda t: None)
    assert "ยังมาไม่ถึง" in run(extract_date=lambda t: "2026-12-01")
    assert "ต้องการดูพื้นที่ไหน" in run(extract_place=lambda t: None)
    assert "หาตำแหน่ง" in run(geocode=lambda p: None)

def test_station_bad_values_not_shown():
    bad = {"station_name": "วัดศรัทธาธรรม", "distance_km": 1.0, "waterlevel_msl": 32765.9,
           "critical_level_msl": 0, "waterlevel_datetime": "x"}
    out = run(station_fn=lambda *a: bad)
    assert "32765" not in out and "ผิดปกติ" in out

# ---------- handle_location ทั้งก้อน ----------
def test_handle_location_message():
    rq = types.ModuleType("rag_query")
    for n in ["answer_question", "classify_intent", "chat_naturally", "extract_date_from_text"]: setattr(rq, n, lambda *a, **k: "")
    rq.genai = None; rq.INTENT_MODEL = "x"
    lp = types.ModuleType("location_parser"); lp.try_parse_safe_location = lambda t: None; lp.try_geocode_address = lambda t: None
    sys.modules["rag_query"], sys.modules["location_parser"] = rq, lp
    import importlib, message_handler as mh; importlib.reload(mh)
    mh.check_flood_risk_live = lambda la, ln: {"risk": "unknown", "source": "ไม่พบข้อมูล"}
    now = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M")
    st = {"station_id": 1, "distance_km": 2.0, "station_name": "ทดสอบ", "agency_name": "กรมชล", "river_name": "น้ำ",
          "waterlevel_datetime": now, "waterlevel_msl": 10.0, "critical_level_msl": 20.0}
    mh.fetch_all_stations = lambda: []; mh.find_nearest_station = lambda *a, **k: [st]
    mh.get_forecast_rain = lambda la, ln: {"rain_mm": 120.0, "max_hour_mm": 30.0, "hours": 24, "level": "ฝนหนักมาก", "from": "a", "to": "b"}
    out = mh.handle_location(13.7, 100.5)
    print("\n----- ตัวอย่างข้อความที่ผู้ใช้จะได้รับ -----\n" + out + "\n---------------------------------------")
    assert "🟢" not in out and "พยากรณ์ฝนล่วงหน้า 24 ชม." in out and "ฝนสะสม" not in out and "1784" in out and "ไม่ได้ยืนยันว่าท่วมจริง" in out
    mh.get_forecast_rain = lambda la, ln: (_ for _ in ()).throw(RuntimeError("x"))
    assert "พยากรณ์ฝน" not in mh.handle_location(13.7, 100.5)       # ฝนล่ม ไม่ทำให้ทั้งข้อความล่ม

def test_live_location_only_1day_is_shown():
    import message_handler as mh
    mh.get_forecast_rain = lambda la, ln: None
    mh.fetch_all_stations = lambda: []; mh.find_nearest_station = lambda *a, **k: []
    props = {"file_name": "FLOOD_20261005_xx.tif", "pv_tn": "แพร่"}
    mh.check_flood_risk_live = lambda la, ln: {"risk": "high", "source": "พื้นที่น้ำท่วมในรอบ 30 วันล่าสุด", "properties": props}
    out = mh.handle_location(18.0, 100.0)
    assert "🔴" not in out and "🟡" not in out and "30 วัน" not in out and "2026-10-05" not in out and "⚪" in out
    mh.check_flood_risk_live = lambda la, ln: {"risk": "low", "source": "พื้นที่น้ำท่วมในรอบ 7 วันล่าสุด"}
    out = mh.handle_location(18.0, 100.0)
    assert "🟡" not in out and "7 วัน" not in out
    mh.check_flood_risk_live = lambda la, ln: {"risk": "high", "source": "พื้นที่น้ำท่วม 1 วันล่าสุด", "properties": props}
    assert "🔴" in mh.handle_location(18.0, 100.0)
    mh.check_flood_risk_live = lambda la, ln: {"risk": "low", "source": "พื้นที่น้ำท่วม 1 วันล่าสุด"}
    assert "🟡" in mh.handle_location(18.0, 100.0)


def test_coords_with_date_goes_historical_without_date_stays_live():
    import message_handler as mh
    mh.get_forecast_rain = lambda la, ln: None
    mh.fetch_all_stations = lambda: []; mh.find_nearest_station = lambda *a, **k: []
    mh.check_flood_risk_live = lambda la, ln: {"risk": "unknown", "source": "ไม่พบข้อมูล"}
    called = {}
    mh.answer_historical = lambda q, **k: called.update(k) or "HIST"
    mh.try_parse_safe_location = lambda t: (17.5, 103.9)
    assert mh.handle_text_question("lat=17.5, lng=103.9 วันที่ 21 กันยายน") == "HIST" and called["coords"] == (17.5, 103.9)
    called.clear()
    assert "HIST" not in mh.handle_text_question("lat=17.5, lng=103.9") and not called
    assert mh._DATE_CUE.search("17.5,103.9 เมื่อวาน") and not mh._DATE_CUE.search("lat=17.585385, lng=103.978062")


def test_historical_with_coords_skips_geocode():
    import historical as hi, datetime as dt
    out = hi.answer_historical("พิกัด 17.5,103.9 วันที่ 5 ตุลาคม", extract_date=lambda q: "2026-10-05",
        extract_place=lambda q: None, geocode=lambda p: 1/0,
        station_fn=lambda la, ln, d: None, range_fn=lambda: (None, None),
        sat_day_fn=lambda d: [{"province": "แพร่", "feature_count": 3}], sat_range_fn=lambda: ("2026-10-01", "2026-10-11"),
        today=dt.date(2026, 10, 11), coords=(17.5, 103.9))
    assert "พิกัด 17.5000, 103.9000" in out and "ทั่วประเทศ" not in out and "พิมพ์ชื่อจังหวัด" in out


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try: fn(); print("PASS", name)
            except Exception as e:
                import traceback; fails += 1; print("FAIL", name, "->", repr(e)); traceback.print_exc()
    print("\nทั้งหมดผ่าน" if not fails else f"\nล้มเหลว {fails} เทสต์"); sys.exit(1 if fails else 0)