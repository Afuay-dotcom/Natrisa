"""
history_store.py
================
เก็บ snapshot ระดับน้ำจากสถานี Thaiwater รายวันลง SQLite (ไฟล์เดียว ไม่ต้องติดตั้งเพิ่ม)
เพื่อให้ตอบคำถามย้อนหลังตามวันที่ได้ "ตั้งแต่วันที่เริ่มเก็บเป็นต้นไป"

เก็บค่าดิบตามที่ API ให้มา (รวมค่าผิดปกติ) แล้วค่อยตรวจคุณภาพตอนอ่านด้วย
get_valid_waterlevel / get_valid_critical_level ใน thaiwater_station.py
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from thaiwater_station import haversine_distance

DB_PATH = os.getenv("HISTORY_DB_PATH") or str(Path(__file__).with_name("history.db"))
_BKK = timezone(timedelta(hours=7))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS station_reading (
    snapshot_date       TEXT NOT NULL,   -- YYYY-MM-DD (เวลาไทย) ที่ดึงข้อมูล
    station_id          INTEGER NOT NULL,
    station_name        TEXT,
    agency_name         TEXT,
    lat                 REAL,
    lng                 REAL,
    waterlevel_msl      REAL,
    critical_level_msl  REAL,
    waterlevel_datetime TEXT,
    PRIMARY KEY (snapshot_date, station_id)
);
CREATE INDEX IF NOT EXISTS idx_reading_date ON station_reading (snapshot_date);
"""


def _connect(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _name_th(obj):
    return ((obj.get("th") if isinstance(obj, dict) else None) or "").strip()


def save_station_snapshot(records, snapshot_date=None, db_path=None):
    """
    records = ผลจาก fetch_all_stations() (raw record ของ Thaiwater)
    รันซ้ำในวันเดียวกัน = เขียนทับของวันนั้น (ไม่เกิดแถวซ้ำ) คืนจำนวนแถวที่บันทึก
    """
    snapshot_date = snapshot_date or datetime.now(_BKK).strftime("%Y-%m-%d")
    rows = []
    for r in records:
        if not isinstance(r, dict):
            continue
        st = r.get("station", {}) or {}
        if st.get("id") is None:
            continue
        rows.append((
            snapshot_date, st.get("id"), _name_th(st.get("tele_station_name")),
            _name_th((r.get("agency", {}) or {}).get("agency_name")),
            _num(st.get("tele_station_lat")), _num(st.get("tele_station_long")),
            _num(r.get("waterlevel_msl")), _num(st.get("critical_level_msl")),
            r.get("waterlevel_datetime"),
        ))
    with _connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO station_reading VALUES (?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def get_available_range(db_path=None):
    """(วันแรก, วันสุดท้าย) ที่มี snapshot หรือ (None, None) ถ้ายังไม่มีเลย"""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT MIN(snapshot_date) a, MAX(snapshot_date) b FROM station_reading").fetchone()
    return (row["a"], row["b"]) if row and row["a"] else (None, None)


def get_nearest_station_on_date(lat, lng, snapshot_date, max_km=50.0, db_path=None, prefer_usable=False):
    """
    สถานีที่ใกล้สุด (ไม่เกิน max_km) ในวันที่ระบุ หรือ None ถ้าไม่มี snapshot วันนั้น/ไม่มีสถานีใกล้
    prefer_usable=True: ถ้ามีสถานีในรัศมีที่ "มีทั้งระดับน้ำและเกณฑ์วิกฤตที่ใช้ได้" ให้เลือกตัวที่ใกล้สุดในกลุ่มนั้น
    (สถานีที่ใกล้สุดจริงๆ หลายแห่งไม่มีเกณฑ์วิกฤต ทำให้สรุปท่วม/ไม่ท่วมไม่ได้) ถ้าไม่มีเลยค่อยใช้สถานีใกล้สุด
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM station_reading WHERE snapshot_date = ? AND lat IS NOT NULL AND lng IS NOT NULL",
            (snapshot_date,)).fetchall()
    scored = sorted(((haversine_distance(lat, lng, r["lat"], r["lng"]), r) for r in rows), key=lambda x: x[0])
    scored = [(d, r) for d, r in scored if d <= max_km]
    if not scored:
        return None
    best_d, best = scored[0]
    if prefer_usable:
        from thaiwater_station import get_valid_waterlevel, get_valid_critical_level
        for d, r in scored:
            if (get_valid_waterlevel({"waterlevel_msl": r["waterlevel_msl"]}) is not None
                    and get_valid_critical_level({"critical_level_msl": r["critical_level_msl"]}) is not None):
                best_d, best = d, r
                break
    out = dict(best)
    out["distance_km"] = round(best_d, 2)
    return out
