import sqlite3
from history_store import DB_PATH

with sqlite3.connect(DB_PATH) as c:
    rows = c.execute(
        "select date, sum(feature_count), count(*) "
        "from satellite_daily group by date order by date"
    ).fetchall()

print("DB:", DB_PATH)
for d, polygons, provinces in rows:
    print(d, "| แปลง:", polygons, "| จังหวัด:", provinces)