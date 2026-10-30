"""
process_api.py
================
Internal API แบบเบาที่สุด สำหรับให้ n8n เรียกใช้เท่านั้น
(ไม่ใช่ endpoint ที่เปิดรับ request จาก LINE โดยตรงแล้ว — n8n ทำหน้าที่นั้นแทน)

สถาปัตยกรรมใหม่:
    LINE -> n8n webhook (รับ request, verify signature, แยกประเภทข้อความ)
         -> เรียก endpoint นี้ (/process) เพื่อประมวลผล
         -> n8n ส่งข้อความตอบกลับไปที่ LINE Reply/Push API เอง

เหตุผลที่ยังใช้ Python endpoint นี้อยู่ (ไม่ย้าย logic ทั้งหมดไป n8n):
logic การประมวลผล (RAG, geocoding, geospatial calculation, intent
classification, sanity check ต่างๆ) มีความซับซ้อนสูงและผ่านการทดสอบมาแล้ว
การเขียนใหม่ทั้งหมดเป็น n8n node จะเสี่ยงเกิดบั๊กใหม่และใช้เวลานานเกินจำเป็น
n8n จึงทำหน้าที่เป็น "ด่านหน้า" (front door) เชื่อมต่อกับ LINE และจัดการ
เรื่อง routing/reply เท่านั้น ส่วนตรรกะหลักยังอยู่ใน Python ตามเดิม

รัน endpoint นี้:
    python -m uvicorn process_api:app --reload --port 8000

(รันที่ port เดิม 8000 ได้เลย เพราะตอนนี้ไม่มีใครจาก LINE ยิงตรงมาที่นี่แล้ว
มีแค่ n8n ที่คุยกับ endpoint นี้ผ่าน localhost เท่านั้น ไม่ต้องเปิด ngrok
ให้ endpoint นี้อีกต่อไป — ngrok จะเปลี่ยนไปเปิดให้ n8n แทน)
"""

import threading
from datetime import datetime

import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from message_handler import handle_message
from daily_snapshot import run_snapshot

def require_secret(x_api_secret: str | None = Header(default=None)):
    """
    ถ้าตั้ง API_SECRET ใน .env ไว้ ทุก endpoint ต้องมี header  X-API-Secret: <ค่าเดียวกัน>  (ตั้งใน n8n HTTP Request node)
    ถ้าไม่ตั้ง = เปิดโล่งเหมือนเดิม (ใช้ได้เฉพาะรันบนเครื่องตัวเองที่ bind 127.0.0.1)
    """
    expected = os.getenv("API_SECRET")
    if expected and x_api_secret != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


app = FastAPI(dependencies=[Depends(require_secret)])


class ProcessRequest(BaseModel):
    """
    รูปแบบข้อมูลที่ n8n จะส่งมาให้ endpoint นี้
    message_type: "text" หรือ "location"
    text: ใช้เมื่อ message_type == "text"
    lat, lng: ใช้เมื่อ message_type == "location"
    """
    message_type: str
    text: str | None = None
    lat: float | None = None
    lng: float | None = None


@app.post("/process")
def process(req: ProcessRequest):  # def (ไม่ใช่ async) เพราะข้างในเรียก requests แบบ blocking
    """
    จุดเดียวที่ n8n เรียกใช้ รับ input ที่ n8n แยกประเภทมาให้แล้ว
    ส่งต่อเข้า handle_message() (โมดูลเดิมที่ทดสอบผ่านมาแล้วทั้งหมด)
    คืนค่าเป็นข้อความตอบกลับ ให้ n8n เอาไปส่งต่อที่ LINE Reply/Push API เอง
    """
    try:
        if req.message_type == "text" and req.text:
            reply_text = handle_message("text", text=req.text)

        elif req.message_type == "location" and req.lat is not None and req.lng is not None:
            reply_text = handle_message("location", lat=req.lat, lng=req.lng)

        else:
            reply_text = "ขออภัยค่ะ ระบบรองรับเฉพาะข้อความและการแชร์พิกัดเท่านั้น"

    except Exception as e:
        print(f"[error] process_api ล้มเหลว: {e}")
        reply_text = "ขออภัยค่ะ ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง"

    return {"reply_text": reply_text}


_snapshot_lock = threading.Lock()
_snapshot_state = {"running": False, "started_at": None, "finished_at": None, "last_result": None}


def _run_snapshot_job():
    """รัน snapshot เบื้องหลัง (ดึงดาวเทียมหลายนาที ถ้ารอใน request เดียว n8n จะ timeout)"""
    try:
        result = {"ok": True, **run_snapshot()}
    except Exception as e:
        print(f"[error] snapshot ล้มเหลว: {e}")
        result = {"ok": False, "error": str(e)}
    _snapshot_state.update(running=False, finished_at=datetime.now().isoformat(timespec="seconds"),
                           last_result=result)
    _snapshot_lock.release()
    print(f"[snapshot] เสร็จแล้ว: {result}")


@app.post("/snapshot")
def snapshot():
    """
    ให้ n8n (Schedule Trigger วันละครั้ง) เรียก เพื่อบันทึกระดับน้ำสถานี + สรุปดาวเทียม
    ตอบกลับทันทีแล้วทำงานต่อเบื้องหลัง (ใช้เวลาหลายนาที) ดูผลได้ที่ GET /snapshot/status
    ถ้ากำลังรันอยู่แล้ว จะไม่เริ่มซ้อน
    """
    if not _snapshot_lock.acquire(blocking=False):
        return {"ok": True, "started": False, "message": "snapshot กำลังรันอยู่แล้ว", **_snapshot_state}
    _snapshot_state.update(running=True, started_at=datetime.now().isoformat(timespec="seconds"),
                           finished_at=None)
    threading.Thread(target=_run_snapshot_job, daemon=True).start()
    return {"ok": True, "started": True, "message": "เริ่มเก็บ snapshot เบื้องหลังแล้ว"}


@app.get("/snapshot/status")
def snapshot_status():
    """ดูว่า snapshot รอบล่าสุดรันอยู่ไหม / เสร็จเมื่อไหร่ / ผลเป็นอย่างไร"""
    return _snapshot_state


@app.get("/")
def health_check():
    return {"status": "process_api กำลังทำงานอยู่ค่ะ (รอ n8n เรียกใช้)"}