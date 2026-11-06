"""
fix_thai_text.py
================
แก้ข้อความภาษาไทยที่เพี้ยนจากการแยกข้อความของ PDF คู่มือ ปภ.

อาการ: ฟอนต์ใน PDF map สระ "ำ" ไม่ครบ pypdf เลยคืนอักขระ U+FFFD (�) มาแทน
เช่น "จ�านวน" (ควรเป็น "จำนวน"), "น�้าท่วม" (ควรเป็น "น้ำท่วม")
ลำดับที่เจอ: พยัญชนะ + � + (วรรณยุกต์ถ้ามี) + "า"  ->  พยัญชนะ + (วรรณยุกต์) + "ำ"
"""

import re

_BROKEN = "\ufffd"
_TONE_MARKS = "\u0e48-\u0e4b"  # ไม้เอก โท ตรี จัตวา

# น�้า -> น้ำ (วรรณยุกต์คั่นระหว่าง � กับ า)
# (บางจุดมีช่องว่างคั่น หรือ "ำ" ปรากฏอยู่แล้วหลัง � ก็รองรับด้วย: น�้ า, น�้ำ)
_RE_BROKEN_AM_WITH_TONE = re.compile(f"{_BROKEN}([{_TONE_MARKS}]) ?[าำ]")
# จ�าน -> จำน
_RE_BROKEN_AM = re.compile(f"{_BROKEN} ?[าำ]")
# สัญลักษณ์ bullet ของฟอนต์ที่ไม่มีใน Unicode มาตรฐาน
_PRIVATE_USE = re.compile("[\ue000-\uf8ff]")


def fix_thai_text(text: str) -> str:
    text = _RE_BROKEN_AM_WITH_TONE.sub(r"\1ำ", text)
    text = _RE_BROKEN_AM.sub("ำ", text)
    text = _PRIVATE_USE.sub("•", text)
    return text


def count_broken(text: str) -> int:
    """จำนวนอักขระเพี้ยนที่ยังเหลือ (ใช้ตรวจหลังแก้)"""
    return text.count(_BROKEN)
