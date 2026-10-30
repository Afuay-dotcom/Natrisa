"""
rag_ingest.py
=============
ขั้นตอน "เตรียมฐานความรู้" (ทำครั้งเดียว หรือทำใหม่เวลาคู่มือมีการอัปเดต):

    PDF คู่มือปฏิบัติตัว/เบอร์ฉุกเฉิน (ปภ.)
        -> แยกข้อความ (pypdf)
        -> แบ่งเป็น chunk เล็กๆ (chunking)
        -> แปลงแต่ละ chunk เป็นเวกเตอร์ (Gemini embedding API)
        -> อัปโหลดเก็บใน Pinecone (vector database)

ก่อนรัน ต้องติดตั้ง:
    pip install pypdf pinecone google-generativeai python-dotenv

และตั้งค่าใน .env:
    GEMINI_API_KEY=...
    PINECONE_API_KEY=...
"""

import os

from dotenv import load_dotenv
from pypdf import PdfReader
import google.generativeai as genai
from pinecone import Pinecone, ServerlessSpec

from fix_thai_text import fix_thai_text, count_broken

load_dotenv()

# ---------------------------------------------------------------------------
# 0) ตั้งค่า
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

PDF_PATH = "คู่มือปภ.pdf"  # แก้ path ให้ตรงกับไฟล์จริงบนเครื่องคุณ

# v3 = index ใหม่ที่ใช้ข้อความที่แก้สระ "ำ" แล้ว (v2 เก็บเวกเตอร์จากข้อความเพี้ยน)
# ถ้าเปลี่ยนชื่อตรงนี้ ต้องเปลี่ยนใน rag_query.py และ export_rag_chunks.py ให้ตรงกันด้วย
PINECONE_INDEX_NAME = "flood-kb-v3"
VECTOR_ID_PREFIX = "pordor-manual"  # ID ของ Pinecone ต้องเป็น ASCII
EMBEDDING_MODEL = "models/gemini-embedding-001"  # โมเดล embedding ปัจจุบัน (text-embedding-004 เลิกใช้แล้ว)
EMBEDDING_DIMENSION = 3072  # ขนาดเวกเตอร์ default ของ gemini-embedding-001

CHUNK_SIZE = 800       # จำนวนตัวอักษรต่อ chunk (ปรับได้)
CHUNK_OVERLAP = 150    # จำนวนตัวอักษรที่ทับซ้อนกันระหว่าง chunk ถัดไป
                       # (กันไม่ให้ใจความสำคัญขาดตรงรอยตัด)

genai.configure(api_key=GEMINI_API_KEY)


# ---------------------------------------------------------------------------
# 1) แยกข้อความจาก PDF
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> str:
    """
    อ่านทุกหน้าของ PDF แล้วรวมเป็น string เดียว
    """
    reader = PdfReader(pdf_path)
    full_text = []

    for page_num, page in enumerate(reader.pages, start=1):
        page_text = fix_thai_text(page.extract_text() or "")  # แก้ "ำ" ที่เพี้ยนจากฟอนต์ PDF
        full_text.append(page_text)
        print(f"  อ่านหน้า {page_num}/{len(reader.pages)} แล้ว ({len(page_text)} ตัวอักษร)")

    joined = "\n\n".join(full_text)
    leftover = count_broken(joined)
    if leftover:
        print(f"  [warn] ยังเหลืออักขระเพี้ยน (�) {leftover} จุด ตรวจด้วยตาก่อนอัปโหลด")
    return joined


# ---------------------------------------------------------------------------
# 2) แบ่งข้อความเป็น chunk เล็กๆ
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list:
    """
    แบ่งข้อความยาวๆ เป็นชิ้นเล็ก (chunk) แบบ sliding window
    เพื่อให้แต่ละ chunk มีขนาดพอเหมาะสำหรับ embedding และไม่สูญเสียบริบทตรงรอยตัด
    (มี overlap ทับซ้อนกันระหว่าง chunk ถัดไป)
    """
    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = start + chunk_size
        chunk = text[start:end].strip()

        if chunk:  # ข้าม chunk ที่ว่างเปล่า (เช่น มีแต่ whitespace)
            chunks.append(chunk)

        start += chunk_size - overlap  # เลื่อนหน้าต่างไป โดยเหลื่อมกันตาม overlap

    return chunks


# ---------------------------------------------------------------------------
# 3) แปลง chunk เป็นเวกเตอร์ด้วย Gemini embedding API
# ---------------------------------------------------------------------------

def embed_text(text: str) -> list:
    """
    เรียก Gemini embedding API แปลงข้อความ 1 ชิ้นเป็นเวกเตอร์ (list ของตัวเลข)
    """
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=text,
        task_type="retrieval_document",  # บอกโมเดลว่านี่คือเอกสารที่จะถูกค้นหาทีหลัง
    )
    return result["embedding"]


# ---------------------------------------------------------------------------
# 4) อัปโหลดเวกเตอร์ทั้งหมดเข้า Pinecone
# ---------------------------------------------------------------------------

def get_or_create_pinecone_index():
    """
    เชื่อมต่อ Pinecone แล้วสร้าง index ใหม่ถ้ายังไม่มี
    """
    pc = Pinecone(api_key=PINECONE_API_KEY)

    existing_indexes = [idx["name"] for idx in pc.list_indexes()]

    if PINECONE_INDEX_NAME not in existing_indexes:
        print(f"ยังไม่มี index ชื่อ '{PINECONE_INDEX_NAME}' กำลังสร้างใหม่...")
        pc.create_index(
            name=PINECONE_INDEX_NAME,
            dimension=EMBEDDING_DIMENSION,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
    else:
        print(f"เจอ index '{PINECONE_INDEX_NAME}' อยู่แล้ว ใช้ตัวเดิม")

    return pc.Index(PINECONE_INDEX_NAME)


def upload_chunks_to_pinecone(chunks: list, source_name: str = "คู่มือปภ."):
    """
    ไล่ embed แต่ละ chunk แล้วอัปโหลดเข้า Pinecone ทีละก้อน
    เก็บ metadata (ข้อความต้นฉบับ + ที่มา) ไว้ด้วย เพื่อให้ตอนค้นเจอ
    สามารถดึงข้อความจริงกลับมาใช้ตอบคำถามได้
    """
    index = get_or_create_pinecone_index()
    vectors_to_upsert = []

    for i, chunk in enumerate(chunks):
        print(f"  กำลัง embed chunk {i + 1}/{len(chunks)} ...")
        embedding = embed_text(chunk)

        vectors_to_upsert.append({
            # ID คงที่ (อิงลำดับ chunk) รันซ้ำแล้วเขียนทับของเดิม ไม่เกิด vector ซ้ำ
            "id": f"{VECTOR_ID_PREFIX}-{i:04d}",
            "values": embedding,
            "metadata": {
                "text": chunk,
                "source": source_name,
                "chunk_index": i,
            },
        })

    # อัปโหลดเป็นชุด (batch) ทีละ 100 เวกเตอร์ ตามข้อจำกัดของ Pinecone
    batch_size = 100
    for i in range(0, len(vectors_to_upsert), batch_size):
        batch = vectors_to_upsert[i:i + batch_size]
        index.upsert(vectors=batch)
        print(f"  อัปโหลดชุดที่ {i // batch_size + 1} เรียบร้อย ({len(batch)} เวกเตอร์)")

    print(f"\nอัปโหลดครบทั้งหมด {len(vectors_to_upsert)} chunks เข้า Pinecone แล้ว")


# ---------------------------------------------------------------------------
# ทำงานจริง
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"1) กำลังแยกข้อความจาก: {PDF_PATH}")
    raw_text = extract_text_from_pdf(PDF_PATH)
    print(f"   ได้ข้อความทั้งหมด {len(raw_text)} ตัวอักษร\n")

    print("2) กำลังแบ่ง chunk...")
    text_chunks = chunk_text(raw_text)
    print(f"   แบ่งได้ {len(text_chunks)} chunks\n")

    print("3) กำลัง embed และอัปโหลดเข้า Pinecone...")
    upload_chunks_to_pinecone(text_chunks)