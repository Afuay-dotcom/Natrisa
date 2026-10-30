"""
rag_query.py
============
ขั้นตอน "ตอบคำถาม" (เรียกใช้ทุกครั้งที่ user ถามอะไรมาผ่าน LINE):

    คำถามจาก user
        -> แปลงคำถามเป็นเวกเตอร์ (Gemini embedding API)
        -> ค้นหา chunk ที่ใกล้เคียงที่สุดใน Pinecone (retrieval)
        -> เอา chunk ที่เจอ + คำถามเดิม ไปให้ Gemini สรุปเป็นคำตอบ (generation)
        -> ได้คำตอบที่อ้างอิงจากคู่มือจริง ไม่ใช่ความจำของโมเดลเปล่าๆ

ก่อนรัน ต้องติดตั้งและตั้งค่าเหมือน rag_ingest.py และต้องรัน rag_ingest.py
ให้เสร็จก่อนอย่างน้อย 1 ครั้ง (เพื่อให้มีข้อมูลอยู่ใน Pinecone ให้ค้นหา)
"""

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
import google.generativeai as genai
from pinecone import Pinecone

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

PINECONE_INDEX_NAME = "flood-kb-v3"  # ต้องตรงกับ rag_ingest.py (v3 = ข้อความที่แก้สระ "ำ" แล้ว)
EMBEDDING_MODEL = "models/gemini-embedding-001"  # ต้องเป็นโมเดลเดียวกับตอน ingest เสมอ
GENERATION_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")  # รุ่นล่าสุด (ก.ค. 2569)

TOP_K = 3  # จำนวน chunk ที่จะดึงมาอ้างอิงต่อคำถาม 1 ครั้ง

# เกณฑ์ cosine similarity ขั้นต่ำ: chunk ที่คะแนนต่ำกว่านี้ไม่ส่งให้ LLM
# ค่านี้ผู้จัดทำตั้งเอง ต้องจูนจากข้อมูลจริง: ดู log "[rag] score=" แล้วปรับใน .env
# (RAG_MIN_SIMILARITY) ให้คำถามที่ตรงคู่มือผ่าน และคำถามนอกเรื่องถูกตัด
MIN_SIMILARITY = float(os.getenv("RAG_MIN_SIMILARITY", "0.5"))

genai.configure(api_key=GEMINI_API_KEY)
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(PINECONE_INDEX_NAME)


# ---------------------------------------------------------------------------
# 1) แปลงคำถามเป็นเวกเตอร์
# ---------------------------------------------------------------------------

def embed_query(question: str) -> list:
    """
    แปลงคำถามของ user เป็นเวกเตอร์ ใช้ task_type ต่างจากตอน ingest
    (retrieval_query แทน retrieval_document) เพราะ Gemini ปรับ embedding
    ให้เหมาะกับบทบาทที่ต่างกันระหว่าง "เอกสาร" กับ "คำถาม"
    """
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=question,
        task_type="retrieval_query",
    )
    return result["embedding"]


# ---------------------------------------------------------------------------
# 2) ค้นหา chunk ที่เกี่ยวข้องที่สุดใน Pinecone
# ---------------------------------------------------------------------------

def retrieve_relevant_chunks(question: str, top_k: int = TOP_K) -> list:
    """
    ค้นหาใน Pinecone แล้วคืนข้อความของ chunk ที่คะแนน cosine >= MIN_SIMILARITY
    เรียงจากเกี่ยวข้องมากไปน้อย (log คะแนนทุก chunk ไว้ใช้จูนเกณฑ์/ตอบอาจารย์)
    """
    query_vector = embed_query(question)

    search_result = index.query(
        vector=query_vector,
        top_k=top_k,
        include_metadata=True,
    )

    matched_texts = []
    for match in search_result.get("matches", []):
        score = match.get("score", 0.0)
        chunk_text = (match.get("metadata") or {}).get("text", "")
        keep = bool(chunk_text) and score >= MIN_SIMILARITY
        print(f"[rag] score={score:.3f} {'ใช้' if keep else 'ตัดทิ้ง'}")
        if keep:
            matched_texts.append(chunk_text)

    return matched_texts


# ---------------------------------------------------------------------------
# 3) ให้ Gemini สรุปคำตอบจากบริบทที่ค้นเจอ
# ---------------------------------------------------------------------------

def generate_answer(question: str, context_chunks: list) -> str:
    """
    ประกอบ prompt จากคำถาม + context ที่ค้นเจอ แล้วให้ Gemini เรียบเรียงคำตอบ
    บังคับให้ตอบจากบริบทที่ให้เท่านั้น เพื่อลด hallucination
    """
    if not context_chunks:
        return ("ขออภัยค่ะ สายธารไม่พบข้อมูลที่ตรงกับคำถามนี้ในคู่มือ ปภ. "
                "ถ้าเป็นเหตุเร่งด่วน โทรสายด่วน ปภ. 1784 ได้เลยนะคะ")

    context_text = "\n\n---\n\n".join(context_chunks)

    prompt = f"""คุณชื่อ "สายธาร" เป็นผู้ช่วยแชทบอทที่คอยดูแลและให้คำแนะนำประชาชน
เรื่องอุทกภัยผ่านแอป LINE พูดคุยด้วยน้ำเสียงเป็นกันเอง อบอุ่น ห่วงใย เหมือน
คนที่คุยกันจริงๆ ไม่ใช่การอ่านประกาศราชการ

กติกาการตอบ:
- ใช้ภาษาพูดที่เป็นธรรมชาติ ลงท้ายด้วย "ค่ะ/นะคะ" อย่างเหมาะสม ไม่ใช่ทุกประโยค
- ตอบเหมือนคุยกับคนคนหนึ่ง ไม่ใช่ท่องคู่มือให้ฟัง แม้เนื้อหาต้องอ้างอิงจากคู่มือ
  ด้านล่างเท่านั้นก็ตาม ให้เรียบเรียงเป็นคำพูดของตัวเองใหม่ ไม่ใช่ copy ประโยคจากคู่มือ
- **สำคัญมาก - ตอบเฉพาะสิ่งที่ถูกถามเท่านั้น อย่ายัดทุกสถานการณ์มาในคำตอบเดียว**
  ถ้าคู่มือมีคำแนะนำหลายระดับความรุนแรง (เช่น น้ำท่วมเล็กน้อย vs รุนแรงจนต้องอพยพ)
  ให้เลือกตอบเฉพาะระดับที่ตรงกับคำถามหรือสถานการณ์ที่บอกมาก่อน แล้วปิดท้ายด้วย
  การชวนถามต่อ เช่น "ถ้าน้ำเริ่มท่วมสูงขึ้นจนต้องอพยพ ถามมาได้อีกนะคะ จะบอกขั้นตอนให้ค่ะ"
  แทนการอธิบายทุกระดับความรุนแรงรวดเดียวในคำตอบแรก
- ความยาวคำตอบ: พยายามให้อยู่ในราว 3-5 ประโยคสำหรับคำถามทั่วไป (ยกเว้นคำถามที่
  จำเป็นต้องมีขั้นตอนละเอียดจริงๆ ถึงจะยาวกว่านี้ได้) เพราะเป็นแชทมือถือ อ่านยาว
  เกินไปจะรู้สึกอึดอัด ไม่ใช่การอ่านเอกสาร
- ถ้าเนื้อหามีหลายขั้นตอน ให้เล่าเป็นลำดับด้วยประโยคสั้นๆ ต่อเนื่องกัน แทนการทำ
  bullet list ยาวๆ แบบเอกสารทางการ (เว้นแต่จำเป็นจริงๆ เพราะขั้นตอนซับซ้อนมาก)
- ห้ามเดาหรือแต่งข้อมูลเพิ่มเติมเองโดยเด็ดขาด ถ้าข้อมูลในคู่มือไม่พอตอบคำถาม
  ให้บอกตรงๆ ว่าไม่พบข้อมูล และแนะนำให้ติดต่อสายด่วน ปภ. 1784

ข้อมูลอ้างอิงจากคู่มือ (ใช้เป็นแหล่งข้อมูลเท่านั้น ไม่ต้องลอกคำต่อคำ):
{context_text}

คำถามจากประชาชน: {question}

คำตอบของสายธาร:"""

    return generate_text(prompt, 0.3, GENERATION_MODEL)


# ---------------------------------------------------------------------------
# 4) จำแนกเจตนาของข้อความ (Intent Classification)
# ---------------------------------------------------------------------------

# โควต้าฟรีนับ "แยกตามโมเดล": ตั้ง GEMINI_INTENT_MODEL เป็นโมเดลอื่น (เช่นรุ่น lite ที่โควต้าสูงกว่า)
# จะแบ่งภาระออกจากโมเดลตอบคำถาม; GEMINI_FALLBACK_MODELS (คั่นด้วย ,) = โมเดลสำรองเมื่อตัวหลักโควต้าหมด
INTENT_MODEL = os.getenv("GEMINI_INTENT_MODEL", GENERATION_MODEL)
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]

_CACHE_MAX = 500
_llm_cache = {}   # (ชนิดงาน, ข้อความ) -> ผลลัพธ์ เฉพาะงาน temperature 0 ที่สำเร็จ: ถามซ้ำไม่เสียโควต้า


def generate_text(prompt, temperature, primary=None):
    """เรียก Gemini; ถ้าโมเดลหลักล้มเหลว (โควต้าหมด/ล่ม) ลองโมเดลสำรองตามลำดับ; ล้มหมดจึง raise"""
    models = [primary or GENERATION_MODEL] + [m for m in FALLBACK_MODELS if m != (primary or GENERATION_MODEL)]
    last = None
    for name in models:
        try:
            resp = genai.GenerativeModel(name).generate_content(
                prompt, generation_config={"temperature": temperature})
            return resp.text
        except Exception as e:
            last = e
            print(f"[warn] Gemini {name} ล้มเหลว ({type(e).__name__})")
    raise last


def cached_generate(kind, key, prompt, temperature=0.0, primary=None):
    k = (kind, key.strip())
    if k in _llm_cache:
        return _llm_cache[k]
    out = generate_text(prompt, temperature, primary or INTENT_MODEL)
    if len(_llm_cache) >= _CACHE_MAX:
        _llm_cache.clear()
    _llm_cache[k] = out
    return out

import re
_GREETING_ONLY = re.compile(r"^(สวัสดี|หวัดดี|ดีค่ะ|ดีครับ|hello|hi|hey|เมนู|ช่วยเหลือ|วิธีใช้|help)[\s!ค่ะครับคะจ้าน้า]*$", re.I)
_VALID_INTENTS = {"location_name", "flood_question", "historical_flood", "greeting", "general_chat"}


def classify_intent(text: str) -> str:
    """
    ใช้ Gemini จำแนกว่าข้อความที่ user พิมพ์เข้ามาน่าจะเป็นเจตนาแบบไหน
    """
    prompt = f"""จำแนกข้อความต่อไปนี้ว่าเข้าข่ายประเภทไหนมากที่สุด จาก 5 ประเภทนี้เท่านั้น:

location_name - ข้อความเป็นชื่อสถานที่ เช่น ชื่อจังหวัด อำเภอ ตำบล หมู่บ้าน
  หรือสถานที่สำคัญ (ไม่ใช่พิกัดตัวเลข เพราะกรณีนั้นถูกจับได้แยกอยู่แล้ว)

historical_flood - คำถามที่ขอดูข้อมูลระดับน้ำ, สถานการณ์น้ำท่วม, หรือจุดน้ำท่วม "ย้อนหลัง" 
  หรือมีการระบุวันที่ในอดีตชัดเจน เช่น "ข้อมูลน้ำท่วมวันที่ 21 กันยายน", "ระดับน้ำเมื่อวาน"

flood_question - คำถามเกี่ยวกับน้ำท่วม อุทกภัย การเตรียมตัว การรับมือ
  เบอร์ฉุกเฉิน หรือภัยพิบัติที่เกี่ยวข้องในสถานการณ์ปัจจุบัน

greeting - ข้อความทักทาย (สวัสดี, หวัดดี, hi) หรือถามวิธีใช้งานระบบ

general_chat - ข้อความอื่นๆ ที่ไม่เข้าข่ายข้างต้น เช่น พูดคุยทั่วไป
  ขอบคุณ ชมเชย หรือคำถามที่ไม่เกี่ยวกับน้ำท่วม/สถานที่เลย

ข้อความ: "{text}"

ตอบกลับมาเป็นคำเดียวเท่านั้น จาก 5 คำนี้: location_name, historical_flood, flood_question, greeting, general_chat
ห้ามมีคำอธิบายอื่นเพิ่มเติม:"""

    if _GREETING_ONLY.match(text.strip()):   # ทักทายล้วนๆ ไม่ต้องเรียก LLM (ประหยัดโควต้า)
        return "greeting"

    try:
        intent = cached_generate("intent", text, prompt).strip().lower()

        if intent in _VALID_INTENTS:
            return intent
        return "general_chat"

    except Exception as e:
        print(f"[warn] classify_intent ล้มเหลว: {e}")
        return "llm_error"   # Gemini ล่ม/โควต้าหมด: ให้ message_handler แจ้งว่าระบบขัดข้อง ไม่ตอบเหมือนแชททั่วไป


# ---------------------------------------------------------------------------
# 5) สกัดวันที่จากข้อความ (Date Extraction)
# ---------------------------------------------------------------------------

def extract_date_from_text(text: str) -> str:
    """
    ให้ Gemini สกัดวันที่ออกจากข้อความภาษาไทย แล้วแปลงให้อยู่ในฟอร์แมต YYYY-MM-DD
    """
    today_str = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d")   # เวลาไทย ไม่พึ่ง timezone เครื่อง
    
    prompt = f"""วันนี้คือวันที่ {today_str} 
จากข้อความต่อไปนี้ ให้คุณหา 'วันที่' ที่ผู้ใช้ระบุถึง แล้วแปลงให้อยู่ในรูปแบบ YYYY-MM-DD (ปี ค.ศ. - เดือน - วัน) เท่านั้น 
หากไม่มีการระบุปี ให้ถือว่าเป็นปี ค.ศ. ปัจจุบัน
หากข้อความกล่าวถึง "เมื่อวาน" ให้ลบ 1 วันจากวันนี้, "เมื่อวานซืน" ให้ลบ 2 วัน

ข้อความ: "{text}"

ตอบกลับมาเฉพาะฟอร์แมต YYYY-MM-DD เท่านั้น ห้ามมีคำอธิบายอื่นใดเพิ่มเติม:"""
    
    try:
        # คีย์แคชรวมวันที่วันนี้ เพราะ "เมื่อวาน" ความหมายเปลี่ยนตามวัน
        return cached_generate("date", f"{today_str}|{text}", prompt).strip()
    except Exception as e:
        print(f"[warn] extract_date ล้มเหลว: {e}")
        return None


# ---------------------------------------------------------------------------
# 6) ตอบบทสนทนาทั่วไป (ไม่ผ่าน RAG เพราะไม่ใช่คำถามเกี่ยวกับน้ำท่วม)
# ---------------------------------------------------------------------------

def chat_naturally(text: str) -> str:
    """
    ตอบข้อความพูดคุยทั่วไปที่ไม่เกี่ยวกับน้ำท่วม ด้วยบุคลิก "สายธาร"
    """
    prompt = f"""คุณชื่อ "สายธาร" เป็นผู้ช่วยแชทบอทดูแลเรื่องอุทกภัยผ่าน LINE
พูดคุยเป็นกันเอง อบอุ่น เหมือนคนคุยกันจริงๆ

ตอนนี้ user พิมพ์ข้อความที่ไม่ใช่คำถามเกี่ยวกับน้ำท่วมโดยตรง ให้ตอบสั้นๆ
เป็นธรรมชาติ เหมือนคนคุยกัน ถ้าเป็นการทักทาย/ขอบคุณ/ชมเชย ให้ตอบรับอย่าง
อบอุ่น ถ้าเป็นคำถามความรู้ทั่วไปที่ไม่เกี่ยวกับน้ำท่วม ให้ตอบสั้นๆ อย่างสุภาพ
แล้วชวนกลับมาที่หน้าที่หลักของคุณ

ข้อความจาก user: {text}

คำตอบของสายธาร (สั้น เป็นธรรมชาติ):"""

    try:
        return generate_text(prompt, 0.8, GENERATION_MODEL)
    except Exception as e:
        print(f"[warn] chat_naturally ล้มเหลว: {e}")
        return "ขอโทษนะคะ ตอนนี้หนูขัดข้องนิดหน่อยค่ะ ลองพิมพ์มาใหม่อีกครั้งได้ไหมคะ"


# ---------------------------------------------------------------------------
# ฟังก์ชันหลัก — เรียกเฉพาะตอนที่รู้แล้วว่าเป็นคำถามเกี่ยวกับน้ำท่วม (RAG ล้วนๆ)
# ---------------------------------------------------------------------------
#
# หมายเหตุสถาปัตยกรรม: ฟังก์ชันนี้ "ไม่จำแนกเจตนาเอง" แล้ว เพราะ message_handler.py
# เป็นผู้เรียก classify_intent() เพียงจุดเดียวของทั้งระบบ แล้วค่อยตัดสินใจเรียก
# ฟังก์ชันที่เหมาะสม (answer_question สำหรับ flood_question, chat_naturally
# สำหรับ greeting/general_chat, extract_date_from_text สำหรับ historical_flood)
# การให้ฟังก์ชันนี้ classify ซ้ำอีกรอบจะเสีย API call โดยไม่จำเป็นและเสี่ยง
# ตัดสินใจไม่ตรงกับรอบแรก

def answer_question(question: str) -> str:
    """
    ตอบคำถามเกี่ยวกับน้ำท่วมด้วย RAG: ค้นหา chunk ที่เกี่ยวข้อง -> ให้ Gemini สรุป
    ผู้เรียกต้องมั่นใจแล้วว่าคำถามนี้เข้าข่าย flood_question (เช่นผ่าน
    classify_intent() มาก่อนแล้วจาก message_handler.py)
    """
    relevant_chunks = retrieve_relevant_chunks(question)
    answer = generate_answer(question, relevant_chunks)
    return answer


# ---------------------------------------------------------------------------
# ทดสอบรันตรงๆ
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ทดสอบ classify_intent แยกเป็นเคสต่างๆ (เหมือนที่ message_handler.py จะเรียกใช้จริง)
    test_cases = [
        "ขอดูข้อมูลน้ำท่วมวันที่ 21 กันยายนหน่อยค่ะ",   # ควรได้ historical_flood
        "ถ้าน้ำท่วมบ้านต้องทำยังไงบ้าง",                # ควรได้ flood_question
        "สวัสดีค่ะ",                                      # ควรได้ greeting
    ]
    for q in test_cases:
        intent = classify_intent(q)
        print(f"คำถาม: {q}  ->  เจตนา: {intent}")

    print("\n=== ทดสอบ answer_question (RAG ล้วนๆ) ===")
    test_question = "ถ้าน้ำท่วมบ้านต้องทำยังไงบ้าง"
    print(f"คำถาม: {test_question}\n")
    answer = answer_question(test_question)
    print("คำตอบจากระบบ:")
    print(answer)

    print("\n=== ทดสอบ extract_date_from_text ===")
    date_question = "ขอดูข้อมูลน้ำท่วมวันที่ 21 กันยายนหน่อยค่ะ"
    extracted = extract_date_from_text(date_question)
    print(f"คำถาม: {date_question}  ->  วันที่สกัดได้: {extracted}")