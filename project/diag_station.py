"""ดูโครงสร้างจริงที่ Thaiwater ส่งกลับ (ไม่มีความลับ)"""
import requests
from thaiwater_station import THAIWATER_URL, HEADERS

resp = requests.get(THAIWATER_URL, headers=HEADERS, timeout=30)
print("HTTP:", resp.status_code, "| ขนาด:", len(resp.content), "bytes")
try:
    p = resp.json()
except Exception:
    print("ไม่ใช่ JSON ต้นข้อความ:", resp.text[:300]); raise SystemExit

def show(o, name="payload", depth=0):
    pad = "  " * depth
    if isinstance(o, dict):
        print(f"{pad}{name}: dict คีย์={list(o.keys())[:12]}")
        if depth < 2:
            for k, v in list(o.items())[:6]:
                show(v, k, depth + 1)
    elif isinstance(o, list):
        print(f"{pad}{name}: list ยาว {len(o)}", "| ตัวแรก:", type(o[0]).__name__ if o else "-")
        if o and depth < 2:
            show(o[0], name + "[0]", depth + 1)
    else:
        print(f"{pad}{name}: {type(o).__name__} = {str(o)[:120]}")

show(p)