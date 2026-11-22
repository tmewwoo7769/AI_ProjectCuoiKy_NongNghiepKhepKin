# -*- coding: utf-8 -*-
"""
recommender.py v2 – Hệ thống gợi ý cây trồng (Hybrid Recommender)

  1. Hiểu câu hỏi "bẩn": không dấu, viết tắt, sai chính tả nhẹ, tiếng Anh  → normalize_query()
  2. Phân tích câu hỏi (analyze_query): loại câu hỏi, mơ hồ hay cụ thể, đòi danh sách,
     vùng miền, nhu cầu ngầm (lợi nhuận nhanh, ít rủi ro, ít vốn, thu hoạch nhanh...).
  3. Hai cột CSV mới:
       regions       : North;Central;Highlands;South  (hoặc ALL; để trống = chưa rõ)
       market_value  : 1 = Thấp, 2 = Trung bình, 3 = Cao
     Thiếu cột thì code vẫn chạy (mặc định: mọi vùng, giá trị 2) và ghi cảnh báo vào log.
  4. Lọc theo vùng + xếp hạng có cộng/trừ điểm theo vùng và nhu cầu lợi nhuận.
  5. Dự phòng kinh tế (economic fallback): nếu cây người dùng muốn trồng KHÔNG hợp vùng /
     cảm biến → trả về 1-2 cây thay thế hợp điều kiện vườn VÀ có market_value tương đương.
  6. Chế độ danh sách: người dùng hỏi "có những cây nào trồng được" → trả nhiều cây (LIST_TOP_K).
  7. Ánh sáng đọc từ cảm biến dạng lux (> 100) được tự đổi sang % (LIGHT_MAX_LUX).

Hàm chính: recommend_crops(sensor_data, user_query, top_k, keyword_extractor)

Biến môi trường (tùy chọn):
  CROP_CSV_PATH   đường dẫn crop_data.csv
  FARM_REGION     north | central | highlands | south  (vùng của khu vườn; nên đặt!)
  LIGHT_MAX_LUX   lux tương ứng 100% ánh sáng (mặc định 80000)
  LIST_TOP_K      số cây trả về ở chế độ danh sách (mặc định 12)
  SUITABLE_SCORE  điểm tối thiểu (%) để coi là "hợp điều kiện vườn" (mặc định 70)
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import unicodedata
from functools import lru_cache
from typing import Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ============================== CẤU HÌNH ==============================
CSV_PATH = os.getenv(
    "CROP_CSV_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "crop_data.csv"),
)

FEATURES = ("soil", "temp", "light")
CSV_COLUMNS = {"soil": "ideal_soil", "temp": "ideal_temp", "light": "ideal_light"}
LABELS = {"soil": "Độ ẩm đất", "temp": "Nhiệt độ", "light": "Ánh sáng"}
UNITS = {"soil": "%", "temp": "°C", "light": os.getenv("LIGHT_UNIT", "%")}

DEFAULT_RANGES = {"soil": (0.0, 100.0), "temp": (0.0, 50.0), "light": (0.0, 100.0)}
WEIGHTS = {"soil": 1.0, "temp": 1.0, "light": 1.0}
COSINE_WEIGHT = 0.5
TOLERANCE_RATIO = {"soil": 0.10, "temp": 0.10, "light": 0.15}

LIGHT_MAX_LUX = float(os.getenv("LIGHT_MAX_LUX", "80000"))
LIST_TOP_K = int(os.getenv("LIST_TOP_K", "12"))
SUITABLE_SCORE = float(os.getenv("SUITABLE_SCORE", "70"))

# Điểm cộng/trừ CHỈ dùng để sắp xếp (điểm hiển thị vẫn là điểm cảm biến thuần)
REGION_BONUS = 0.03
REGION_PENALTY = 0.10
PROFIT_BONUS = 0.05
NEED_BONUS = 0.04   # cộng khi mô tả cây khớp nhu cầu (ít vốn / ít rủi ro / thu hoạch nhanh)
NEED_PENALTY = 0.06  # trừ khi cây có dấu hiệu tốn vốn / lâu thu hoạch / khó chăm

SENSOR_ALIASES = {
    "soil": ("soil_moisture", "soil", "do_am_dat"),
    "temp": ("temperature", "temp", "nhiet_do"),
    "light": ("light", "lux", "light_intensity", "anh_sang"),
}

# ============================== VÙNG MIỀN & GIÁ TRỊ KINH TẾ ==============================
REGION_LABELS = {
    "north": "Miền Bắc",
    "central": "Miền Trung",
    "highlands": "Tây Nguyên",
    "south": "Miền Nam",
}
ALL_REGIONS = frozenset(REGION_LABELS)
MARKET_LABELS = {1: "Thấp", 2: "Trung bình", 3: "Cao"}

# Giá trị được chấp nhận trong cột `regions` của CSV
_CSV_REGION_ALIASES = {
    "north": "north", "bac": "north", "mien bac": "north", "bac bo": "north",
    "south": "south", "nam": "south", "mien nam": "south", "nam bo": "south",
    "central": "central", "trung": "central", "mien trung": "central", "trung bo": "central",
    "highlands": "highlands", "highland": "highlands", "tay nguyen": "highlands",
    "cao nguyen": "highlands",
}
_CSV_ALL_WORDS = {"all", "tat ca", "toan quoc", "moi mien", "*"}

# Địa danh / cách gọi vùng trong CÂU HỎI (đã bỏ dấu, thường)
_QUERY_REGION_PLACES = {
    "highlands": ["tay nguyen", "cao nguyen", "da lat", "lam dong", "dak lak", "dac lak",
                  "gia lai", "kon tum", "dak nong", "dac nong", "buon ma thuot", "pleiku",
                  "bao loc", "di linh"],
    "north": ["mien bac", "phia bac", "bac bo", "ha noi", "hai phong", "quang ninh",
              "bac giang", "bac ninh", "thai nguyen", "nam dinh", "thai binh", "hai duong",
              "hung yen", "vinh phuc", "phu tho", "ha giang", "cao bang", "lang son",
              "son la", "moc chau", "sa pa", "lao cai", "yen bai", "hoa binh", "ninh binh"],
    "central": ["mien trung", "trung bo", "thanh hoa", "nghe an", "ha tinh", "quang binh",
                "quang tri", "hue", "da nang", "quang nam", "quang ngai", "binh dinh",
                "phu yen", "khanh hoa", "nha trang", "ninh thuan", "binh thuan", "phan thiet"],
    "south": ["mien nam", "phia nam", "nam bo", "vung nam", "sai gon", "ho chi minh",
              "can tho", "dong nai", "binh duong", "binh phuoc", "tay ninh", "long an",
              "tien giang", "ben tre", "vinh long", "tra vinh", "dong thap", "an giang",
              "kien giang", "hau giang", "soc trang", "bac lieu", "ca mau", "vung tau",
              "mien tay", "dong bang song cuu long", "dbscl", "mekong"],
}


def _parse_regions_cell(value) -> frozenset:
    """'North;Central' / 'Bắc, Trung' / 'ALL' → frozenset mã vùng. Rỗng = chưa rõ."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return frozenset()
    found = set()
    for part in re.split(r"[;,/|]+", str(value)):
        p = strip_accents(part).strip()
        if not p:
            continue
        if p in _CSV_ALL_WORDS:
            return ALL_REGIONS
        code = _CSV_REGION_ALIASES.get(p)
        if code:
            found.add(code)
        else:
            logger.warning("Giá trị vùng không nhận ra trong CSV: %r", part)
    return frozenset(found)


_MARKET_WORDS = {"low": 1, "thap": 1, "medium": 2, "mid": 2, "trung binh": 2, "tb": 2,
                 "high": 3, "cao": 3}


def _parse_market_value(value) -> int:
    try:
        n = float(value)
        if not np.isnan(n):
            return int(min(3, max(1, round(n))))
    except (TypeError, ValueError):
        pass
    return _MARKET_WORDS.get(strip_accents(str(value)).strip(), 2)


def _region_match(crop_regions: frozenset, wanted) -> bool:
    """Cây hợp vùng nếu chưa khai báo vùng (không loại oan) hoặc có giao với vùng cần."""
    if not crop_regions:
        return True
    return bool(crop_regions & set(wanted))


RATING_LABELS = {4: "Rất hợp", 3: "Hợp", 2: "Hạn chế", 1: "Không nên"}


def ranking_from_csv(regions: frozenset) -> list:
    """Xếp hạng 4 vùng cho cây CÓ trong CSV (dựa trên cột regions). Rỗng nếu CSV chưa khai báo vùng."""
    if not regions:
        return []
    rows = []
    for code, label in REGION_LABELS.items():
        ok = code in regions
        rows.append({"code": code, "label": label, "rating": 3 if ok else 2,
                     "rating_label": RATING_LABELS[3 if ok else 2],
                     "reason": "Nằm trong vùng khuyến nghị của dữ liệu hệ thống" if ok
                               else "Không nằm trong vùng khuyến nghị của dữ liệu hệ thống",
                     "source": "csv"})
    return sorted(rows, key=lambda r: -r["rating"])


def ranking_from_profile(profile: dict) -> list:
    """Xếp hạng 4 vùng cho cây LẠ từ hồ sơ do LLM ước lượng (xem llm.get_crop_profile)."""
    rows = []
    for code, label in REGION_LABELS.items():
        item = (profile.get("region_ratings") or {}).get(code) or {"rating": 2, "reason": ""}
        rating = int(min(4, max(1, item.get("rating", 2))))
        rows.append({"code": code, "label": label, "rating": rating,
                     "rating_label": RATING_LABELS[rating], "reason": str(item.get("reason", "")),
                     "source": "ai"})
    return sorted(rows, key=lambda r: -r["rating"])


def region_labels(codes) -> list:
    return [REGION_LABELS[c] for c in REGION_LABELS if c in set(codes)]


# ============================== TIỆN ÍCH TIẾNG VIỆT / NLP ==============================
def strip_accents(text: str) -> str:
    """'Chịu hạn' -> 'chiu han'. Giúp so khớp cả khi người dùng gõ không dấu."""
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.lower()


def _has_phrase(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", haystack) is not None


# Viết tắt / tiếng lóng hay gặp khi gõ nhanh (so khớp theo TỪ, đã bỏ dấu)
ABBREVIATIONS = {
    "ko": "khong", "k": "khong", "hok": "khong", "hong": "khong", "khum": "khong",
    "dc": "duoc", "dk": "duoc", "dg": "dang", "cx": "cung", "vs": "voi", "j": "gi",
    "trc": "truoc", "bt": "binh thuong", "vd": "vi du", "mn": "moi nguoi", "nd": "nhiet do",
    "tphcm": "ho chi minh", "tp.hcm": "ho chi minh", "hcm": "ho chi minh", "sg": "sai gon",
    "hn": "ha noi", "dl": "da lat", "bmt": "buon ma thuot", "dbscl": "dong bang song cuu long",
    "wa": "qua", "z": "vay", "zay": "vay", "ntn": "nhu the nao",
}

# Cụm tiếng Anh → tiếng Việt không dấu (thay theo cụm, dài trước)
EN_VI = [
    ("drought tolerant", "chiu han"), ("drought resistant", "chiu han"), ("low maintenance", "it cham soc"),
    ("high profit", "loi nhuan cao"), ("high value", "gia tri cao"), ("quick profit", "loi nhuan nhanh"),
    ("fast growing", "thu hoach nhanh"), ("low risk", "it rui ro"), ("low capital", "it von"),
    ("which crops", "cay nao"), ("what crops", "cay nao"), ("what can i grow", "trong duoc cay nao"),
    ("what to plant", "nen trong gi"), ("list of", "danh sach"), ("all crops", "tat ca cac cay"),
    ("beginner", "nguoi moi"), ("easy to grow", "de trong"), ("easy", "de trong"),
    ("profit", "loi nhuan"), ("harvest", "thu hoach"), ("fast", "nhanh"),
    ("water", "nuoc"), ("sunny", "ua nang"), ("shade", "ua mat"), ("vegetable", "rau"),
    ("fruit", "trai cay"), ("herb", "thao moc"), ("recommend", "goi y"), ("suggest", "goi y"),
    ("highlands", "tay nguyen"), ("northern", "mien bac"), ("southern", "mien nam"),
    ("central", "mien trung"), ("north", "mien bac"), ("south", "mien nam"),
    ("tomato", "ca chua"), ("chili", "ot"), ("chilli", "ot"), ("pepper", "ot"),
    ("strawberry", "dau tay"), ("lettuce", "xa lach"), ("cucumber", "dua leo"),
    ("corn", "ngo"), ("rice", "lua"), ("coffee", "ca phe"), ("durian", "sau rieng"),
    ("water spinach", "rau muong"), ("morning glory", "rau muong"), ("basil", "hung que"),
    ("mint", "bac ha"), ("cactus", "xuong rong"), ("aloe", "lo hoi"), ("peach", "dao"),
    ("mango", "xoai"), ("banana", "chuoi"), ("pineapple", "dua"),
]

# ----- Luật ý định (lọc theo mô tả cây) -----
INTENT_RULES = [
    {"intent": "chịu hạn",
     "triggers": ["chiu han", "kho han", "it nuoc", "it tuoi", "thieu nuoc", "tiet kiem nuoc", "mua kho"],
     "keywords": ["chịu hạn", "khô hạn", "ít nước", "ít tưới", "chịu khô", "tiết kiệm nước"]},
    {"intent": "dễ trồng / người mới",
     "triggers": ["de trong", "nguoi moi", "moi bat dau", "moi trong", "lan dau", "it cham soc",
                  "de cham", "khong ton cong", "bieng", "chua co kinh nghiem", "chua biet"],
     "keywords": ["dễ trồng", "dễ chăm", "người mới", "ít chăm sóc", "dễ sống"]},
    {"intent": "thu hoạch nhanh",
     "triggers": ["nhanh", "ngan ngay", "thu hoach som", "mau lon", "thu hoach nhanh"],
     "keywords": ["thu hoạch nhanh", "ngắn ngày", "mau lớn", "sinh trưởng nhanh"]},
    {"intent": "chịu nóng",
     "triggers": ["chiu nong", "troi nong", "nang nong", "mua he", "chiu nhiet"],
     "keywords": ["chịu nóng", "ưa nóng", "chịu nhiệt"]},
    {"intent": "ưa mát",
     "triggers": ["chiu lanh", "troi lanh", "ua mat", "mat me", "da lat", "mua dong"],
     "keywords": ["ưa mát", "chịu lạnh", "khí hậu mát", "ưa lạnh"]},
    {"intent": "ưa nước",
     "triggers": ["nhieu nuoc", "ua nuoc", "dat am", "thich nuoc", "ua am"],
     "keywords": ["ưa nước", "ưa ẩm", "nhiều nước", "cần nhiều nước"]},
    {"intent": "ưa nắng",
     "triggers": ["ua nang", "nhieu nang", "thich nang", "nang gat"],
     "keywords": ["ưa nắng", "thích nắng", "cần nhiều nắng"]},
    {"intent": "trồng chậu / không gian nhỏ",
     "triggers": ["ban cong", "trong chau", "can ho", "khong gian nho", "nho gon", "trong trong nha"],
     "keywords": ["trồng chậu", "ban công", "không gian nhỏ", "nhỏ gọn", "trong nhà"]},
    {"intent": "rau ăn lá",
     "triggers": ["rau an la", "an la", "rau xanh", "rau"],
     "keywords": ["rau ăn lá", "rau xanh", "rau"]},
    {"intent": "gia vị / thảo mộc",
     "triggers": ["gia vi", "thao moc", "herb"],
     "keywords": ["gia vị", "thảo mộc"]},
    {"intent": "cây ăn quả",
     "triggers": ["an qua", "trai cay", "an trai"],
     "keywords": ["ăn quả", "trái cây", "ăn trái"]},
    {"intent": "giá trị kinh tế",
     "triggers": ["loi nhuan", "kinh te", "kiem tien", "thu nhap", "ban duoc gia", "gia cao", "gia tri cao"],
     "keywords": ["giá trị kinh tế", "lợi nhuận", "giá cao"]},
]

# ----- Nhu cầu ngầm (hidden needs) -----
NEED_RULES = {
    "quick_profit": ["loi nhuan nhanh", "lam giau", "kiem tien nhanh", "thu loi nhanh", "co lai nhanh",
                     "nhanh co tien", "quay von nhanh"],
    "high_profit": ["loi nhuan", "gia cao", "gia tri cao", "kinh te cao", "ban duoc gia", "sinh loi",
                    "kiem tien", "thu nhap", "co lai", "lam giau", "kiem loi", "ban kiem loi", "ban lay tien",
                    "ban lay loi", "kinh doanh", "ban ra thi truong", "ban thuong mai"],
    "self_use": ["an trong nha", "gia dinh", "tu an", "an hang ngay", "an cho gia dinh", "dung trong nha",
                 "tu cung cap", "trong de an"],
    "ornamental": ["lam canh", "trang tri", "cho dep", "hoa canh", "cay canh"],
    "undecided": ["chon giup", "tuy ban", "ban chon giup", "khong biet chon", "gi cung duoc", "de ban quyet",
                  "chua ro"],
    "medium_capital": ["von vua", "von kha", "von trung binh", "cham duoc hang ngay"],
    "high_capital": ["dau tu lon", "von lon", "von nhieu", "dau tu dai han", "trong lau dai"],
    "low_risk": ["it rui ro", "an toan", "chac an", "khong lo", "on dinh", "so lo", "so rui ro",
                 "khong bi lo", "do thua lo"],
    "low_capital": ["it von", "von thap", "von nho", "chi phi thap", "tiet kiem chi phi", "gia re",
                    "von re", "khong co nhieu tien", "von it"],
    "fast_harvest": ["thu hoach nhanh", "thu hoach som", "ngan ngay", "mau thu hoach", "nhanh thu",
                     "nhanh co tien", "trong nhanh", "nhanh co thu"],
    "beginner": ["nguoi moi", "moi bat dau", "chua co kinh nghiem", "lan dau", "lam quen",
                 "chua biet", "moi trong", "moi lam"],
    "low_effort": ["it cham soc", "bieng", "ban ron", "khong co thoi gian", "khong ton cong",
                   "de cham", "it thoi gian", "it cong"],
}
NEED_LABELS = {
    "quick_profit": "muốn có lợi nhuận nhanh",
    "high_profit": "ưu tiên lợi nhuận / giá trị kinh tế cao",
    "low_risk": "muốn ít rủi ro, ổn định",
    "low_capital": "vốn đầu tư thấp",
    "fast_harvest": "muốn thu hoạch nhanh",
    "beginner": "là người mới, ít kinh nghiệm",
    "low_effort": "muốn ít công chăm sóc",
    "self_use": "trồng để ăn trong gia đình",
    "ornamental": "trồng làm cảnh",
    "undecided": "chưa rõ mình cần gì, nhờ hệ thống chọn giúp",
    "medium_capital": "vốn và công chăm ở mức vừa phải",
    "high_capital": "sẵn sàng đầu tư lớn, dài hạn",
}

LIST_TRIGGERS = [
    "danh sach", "liet ke", "nhung loai", "nhung cay", "cac loai", "cac cay", "tat ca",
    "co nhung", "bao nhieu loai", "may loai", "bang cay", "cay nao trong duoc",
    "cay gi trong duoc", "trong duoc cay nao", "trong duoc nhung", "trong duoc gi",
    "trong duoc nhung gi", "nhung gi trong duoc", "ke ra", "cho xem cac",
]

COLD_CHAIN_TRIGGERS = [
    "van chuyen", "xe lanh", "chuoi lanh", "kho lanh", "dap nat", "hang hoa", "lo hang",
    "chuyen hang", "trung chuyen", "hub", "giao hang", "container", "xe tai", "thung lanh",
    "rung lac", "ton that sau thu hoach",
]

ADVICE_TRIGGERS = ["tu van", "goi y", "de xuat", "nen lam gi", "nen lam sao", "nen chon", "huong dan"]

FOLLOWUP_TRIGGERS = [
    "con gi nua", "con gi khac", "gi khac", "nua khong", "nua di", "them nua", "vay con",
    "the con", "cai khac", "vay thi sao", "con nua khong", "tiep di", "tiep nua",
]

# Từ chỉ chủ đề nông nghiệp (để phân biệt câu hỏi nông nghiệp với trò chuyện xã giao)
FARM_WORDS = {
    "cay", "trong", "rau", "hoa", "qua", "trai", "tuoi", "dat", "phan", "bon", "sau", "benh",
    "nang", "nuoc", "gieo", "giong", "vuon", "nong", "hoach", "ruong", "nam", "canh", "tac",
    "may", "bom", "cam", "bien", "do", "am", "nhiet", "anh", "sang", "canh", "bao", "thoi", "tiet",
}
_FARM_STRONG = {"cay", "trong", "rau", "tuoi", "phan", "bon", "sau", "benh", "gieo", "giong",
                "vuon", "nong", "hoach", "ruong", "canh tac", "dat", "nang", "nuoc", "trai", "qua",
                "may bom", "cam bien", "nhiet do", "do am", "anh sang", "canh bao", "khu vuon"}

_STOPWORDS = {
    "toi", "muon", "trong", "cay", "nao", "cho", "la", "va", "can", "nen", "co", "khong",
    "gi", "the", "loai", "mot", "cac", "minh", "em", "anh", "chi", "hay", "giup", "goi",
    "phu", "hop", "voi", "nha", "vuon", "cua", "duoc", "thi", "nay", "do", "nhung", "xin",
    "chao", "ban", "oi", "dang", "hien", "tai", "dieu", "kien", "hon", "that", "rat", "kha",
}
_GENERIC_WORDS = {
    "tu", "van", "bay", "gio", "nhe", "a", "di", "ah", "biet", "hoi", "ha", "nhi", "vay", "sao",
    "lam", "thu", "xem", "tim", "hieu", "ve", "hoa", "qua", "trai", "cai", "it", "gi", "nao",
    "hom", "nay", "luc", "bi", "bo", "o", "tai", "khu", "vuc", "vung", "mien", "hien", "nhieu",
    "cac", "nhung", "duoc", "khong", "co", "cung", "da", "dang", "se", "roi", "thi", "ma", "nen",
    "hay", "hoac", "voi", "giup", "dum", "xin", "lam", "sao", "khac", "chon", "them",
}
# Từ khiến "đoán tên cây" dừng lại
_GUESS_STOP = _STOPWORDS | _GENERIC_WORDS | {
    "o", "tai", "duoc", "khong", "nhu", "the", "nao", "khu", "vuon", "nha", "vung", "mien",
    "gi", "nhe", "a", "ah", "va", "hay", "cho", "de", "thi", "voi", "vao", "luc", "khi", "bang",
}
_GENERIC_CROP_WORDS = {"cay", "rau", "hoa", "qua", "trai", "loai", "giong", "thu", "gi", "nao"}

KeywordGroups = list
KeywordExtractor = Callable[[str], KeywordGroups]


@lru_cache(maxsize=1)
def _vocab():
    """Từ vựng 'đúng' để sửa lỗi gõ sai (lấy từ luật + tên cây trong CSV)."""
    words = set(_STOPWORDS) | set(_GENERIC_WORDS) | set(FARM_WORDS)
    for r in INTENT_RULES:
        for t in r["triggers"]:
            words.update(t.split())
    for lst in (LIST_TRIGGERS, COLD_CHAIN_TRIGGERS, FOLLOWUP_TRIGGERS):
        for t in lst:
            words.update(t.split())
    for lst in NEED_RULES.values():
        for t in lst:
            words.update(t.split())
    for phrases in _QUERY_REGION_PLACES.values():
        for p in phrases:
            words.update(p.split())
    for _, vi in EN_VI:
        words.update(vi.split())
    try:
        for name in load_crops()["crop_name"]:
            words.update(re.findall(r"\w+", strip_accents(str(name))))
    except Exception:  # CSV chưa sẵn sàng vẫn chạy được
        pass
    words = {w for w in words if w}
    return words, sorted(w for w in words if len(w) >= 4)


def _fix_typos(text: str) -> str:
    known, pool = _vocab()
    out = []
    for tok in text.split():
        if len(tok) < 4 or tok in known or tok.isdigit():
            out.append(tok)
            continue
        match = difflib.get_close_matches(tok, pool, n=1, cutoff=0.84)
        out.append(match[0] if match else tok)
    return " ".join(out)


def normalize_query(text: str) -> str:
    """Chuẩn hóa câu hỏi: bỏ dấu, bỏ ký tự thừa, dịch Anh→Việt, mở rộng viết tắt, sửa lỗi gõ nhẹ."""
    t = strip_accents(text or "")
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for en, vi in EN_VI:
        t = re.sub(rf"(?<!\w){re.escape(en)}(?!\w)", vi, t)
    t = " ".join(ABBREVIATIONS.get(tok, tok) for tok in t.split())
    return _fix_typos(t)


def detect_regions(query_norm: str) -> list:
    """Các vùng được nhắc trong câu (theo thứ tự xuất hiện)."""
    hits = []
    for code, phrases in _QUERY_REGION_PLACES.items():
        positions = []
        for p in phrases:
            m = re.search(rf"(?<!\w){re.escape(p)}(?!\w)", query_norm)
            if m:
                positions.append(m.start())
        if positions:
            hits.append((min(positions), code))
    return [code for _, code in sorted(hits)]


def detect_needs(query_norm: str) -> list:
    needs = [n for n, trig in NEED_RULES.items() if any(_has_phrase(query_norm, t) for t in trig)]
    if "quick_profit" in needs:
        for n in ("high_profit", "fast_harvest"):
            if n not in needs:
                needs.append(n)
    return needs


def _farm_region_from_env_or_sensor(sensor: Optional[dict]):
    """Vùng của KHU VƯỜN: biến môi trường FARM_REGION, nếu không thì đoán rất thô từ nhiệt độ."""
    raw = strip_accents(os.getenv("FARM_REGION", "")).strip()
    if raw:
        code = _CSV_REGION_ALIASES.get(raw)
        if code:
            return code, "env"
        logger.warning("FARM_REGION=%r không hợp lệ (dùng north|central|highlands|south)", raw)
    if sensor and "temp" in sensor and sensor["temp"] <= 22:
        return "highlands", "sensor"  # mát quanh năm → nhiều khả năng vùng cao (chỉ là ước đoán)
    return None, None


# ============================== NẠP DỮ LIỆU ==============================
@lru_cache(maxsize=1)
def load_crops(path: str = CSV_PATH) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]

    required = ["crop_name", *CSV_COLUMNS.values(), "description"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"File CSV thiếu cột: {missing}")

    for col in CSV_COLUMNS.values():
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["crop_name", *CSV_COLUMNS.values()]).reset_index(drop=True)
    df["description"] = df["description"].fillna("").astype(str)
    df["care_tips"] = df["care_tips"].fillna("").astype(str) if "care_tips" in df.columns else ""

    # --- Cột mới: regions, market_value (thiếu thì dùng mặc định, không làm sập hệ thống) ---
    if "regions" in df.columns:
        df["_regions"] = df["regions"].map(_parse_regions_cell)
    else:
        logger.warning("crop_data.csv chưa có cột 'regions' → coi mọi cây hợp mọi vùng")
        df["_regions"] = [frozenset()] * len(df)
    if "market_value" in df.columns:
        df["market_value"] = df["market_value"].map(_parse_market_value)
    else:
        logger.warning("crop_data.csv chưa có cột 'market_value' → mặc định 2 (Trung bình)")
        df["market_value"] = 2

    df["_haystack"] = (df["crop_name"].astype(str) + " " + df["description"]).map(strip_accents)
    return df


def reload_crops() -> None:
    """Gọi sau khi cập nhật file CSV."""
    load_crops.cache_clear()
    _vocab.cache_clear()


def _get_ranges(df: pd.DataFrame) -> dict:
    ranges = {}
    for f in FEATURES:
        lo, hi = DEFAULT_RANGES[f]
        col = df[CSV_COLUMNS[f]]
        lo, hi = min(lo, float(col.min())), max(hi, float(col.max()))
        if hi - lo < 1e-9:
            hi = lo + 1.0
        ranges[f] = (lo, hi)
    return ranges


def parse_sensor_data(sensor_data: Optional[dict]) -> Optional[dict]:
    """Chuẩn hóa cảm biến về {'soil','temp','light'}. Ánh sáng dạng lux (> 100 hoặc khóa 'lux')
    được đổi sang % theo LIGHT_MAX_LUX để khớp thang 0-100 của CSV."""
    if not sensor_data:
        return None
    out = {}
    for f, aliases in SENSOR_ALIASES.items():
        for key in aliases:
            value = sensor_data.get(key)
            if value is None:
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            if f == "light" and (key == "lux" or num > 100):
                num = min(100.0, max(0.0, num / LIGHT_MAX_LUX * 100.0))
            out[f] = num
            break
    return out or None


# ============================== PHÂN TÍCH CÂU HỎI ==============================

def _strip_phrases(query_norm: str, include_intents: bool = False) -> str:
    """Xóa các cụm đã hiểu (danh sách, vùng, nhu cầu, ý định...) để phần còn lại chỉ là 'từ lạ'
    (thường là tên cây). Dùng cho bước đoán tên cây và lọc từ khóa thô."""
    phrases = list(LIST_TRIGGERS) + list(FOLLOWUP_TRIGGERS) + list(COLD_CHAIN_TRIGGERS)
    for lst in NEED_RULES.values():
        phrases += lst
    for lst in _QUERY_REGION_PLACES.values():
        phrases += lst
    phrases += ["khu vuc", "vung dat", "mien"]
    if include_intents:
        for r in INTENT_RULES:
            phrases += r["triggers"]
    text = query_norm
    for ph in sorted(set(phrases), key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(ph)}(?!\w)", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def _find_target_crop(df: pd.DataFrame, query_norm: str):
    """Tìm cây trong CSV được nhắc đích danh trong câu hỏi (khớp cụm dài nhất)."""
    best_idx, best_len = None, 0
    for idx, name in df["crop_name"].items():
        raw = strip_accents(str(name))
        variants = {raw, *[v.strip() for v in re.split(r"[()/,]", raw) if v.strip()]}
        for v in variants:
            if len(v) >= 3 and len(v) > best_len and _has_phrase(query_norm, v):
                best_idx, best_len = idx, len(v)
    return best_idx


_GUESS_RE = re.compile(
    r"(?:muon trong|dinh trong|thich trong|trong|tim hieu ve|hoi ve|ve)\s+"
    r"(?:cay\s+|loai\s+|giong\s+|trai\s+)?([a-z0-9]+(?:\s[a-z0-9]+){0,2})"
)


def _guess_crop_name(query_norm: str) -> Optional[str]:
    """Đoán tên cây lạ người dùng nhắc (ví dụ 'trồng cây đào ở vườn' → 'dao')."""
    for m in _GUESS_RE.finditer(_strip_phrases(query_norm, include_intents=True)):
        words = []
        for w in m.group(1).split():
            if w in _GUESS_STOP:
                break
            words.append(w)
        if words and not all(w in _GENERIC_CROP_WORDS for w in words):
            return " ".join(words)
    return None


def analyze_query(user_query: Optional[str]) -> dict:
    """Phân tích nhanh câu hỏi (không tốn token). Dùng cho cả recommender lẫn llm.py.

    kind    : 'farm' | 'cold_chain' | 'smalltalk'
    vague   : True nếu là câu hỏi nông nghiệp nhưng quá chung chung → AI nên hỏi lại 2-3 câu
    """
    q = normalize_query(user_query or "")
    tokens = q.split()
    try:
        df = load_crops()
        crop_idx = _find_target_crop(df, q)
    except Exception:
        crop_idx = None

    regions = detect_regions(q)
    needs = detect_needs(q)
    intents = [r["intent"] for r in INTENT_RULES if any(_has_phrase(q, t) for t in r["triggers"])]
    list_mode = any(_has_phrase(q, t) for t in LIST_TRIGGERS)
    followup = any(_has_phrase(q, t) for t in FOLLOWUP_TRIGGERS)
    cold = any(_has_phrase(q, t) for t in COLD_CHAIN_TRIGGERS)
    advice = any(_has_phrase(q, t) for t in ADVICE_TRIGGERS)
    farm_signal = (
        advice or any(t in _FARM_STRONG for t in tokens) or any(_has_phrase(q, t) for t in _FARM_STRONG if " " in t)
        or bool(intents) or bool(regions) or crop_idx is not None or list_mode or bool(needs)
    )

    if cold and not (crop_idx is not None or list_mode):
        kind = "cold_chain"
    elif farm_signal:
        kind = "farm"
    else:
        kind = "smalltalk"

    soft_intents = {"dễ trồng / người mới"}
    soft_needs = {"beginner", "low_effort"}
    concrete = bool([i for i in intents if i not in soft_intents] or regions
                    or [n for n in needs if n not in soft_needs] or list_mode or crop_idx is not None)
    beginner_only = bool(({"beginner", "low_effort"} & set(needs) or set(intents) & soft_intents)
                         and not concrete)
    leftover = [t for t in tokens if t not in _STOPWORDS and t not in _GENERIC_WORDS
                and t not in _FARM_STRONG and t not in FARM_WORDS and not t.isdigit()]
    guess = None if (concrete and crop_idx is None and not leftover) else _guess_crop_name(q)
    vague = kind == "farm" and not concrete and not leftover and guess is None

    target_mv = 3 if ({"quick_profit", "high_profit"} & set(needs)) else None
    return {
        "normalized": q, "kind": kind, "vague": vague, "list_mode": list_mode,
        "followup": followup, "beginner_only": beginner_only, "regions": regions, "needs": needs, "intents": intents,
        "target_market_value": target_mv, "crop_in_db": crop_idx is not None,
        "unknown_crop_guess": None if crop_idx is not None else guess,
    }


# ============================== GIAI ĐOẠN 1: LỌC THEO YÊU CẦU ==============================
def _rule_groups(query_norm: str) -> KeywordGroups:
    groups = []
    for rule in INTENT_RULES:
        if any(_has_phrase(query_norm, t) for t in rule["triggers"]):
            groups.append({"intent": rule["intent"], "keywords": list(rule["keywords"])})
    return groups


def _token_groups(query_norm: str) -> KeywordGroups:
    tokens = [t for t in re.findall(r"\w+", _strip_phrases(query_norm, include_intents=True))
              if len(t) >= 3 and t not in _STOPWORDS and t not in _GENERIC_WORDS]
    return [{"intent": "từ khóa", "keywords": tokens}] if tokens else []


def make_llm_keyword_extractor(call_llm: Callable[[str], str]) -> KeywordExtractor:
    """LLM nhỏ hiểu yêu cầu tự do → nhóm từ khóa (chỉ gọi khi rule-based không nhận ra)."""

    def extractor(user_query: str) -> KeywordGroups:
        prompt = (
            "Bạn là bộ trích xuất từ khóa cho hệ thống gợi ý cây trồng.\n"
            "Từ yêu cầu của người dùng, hãy tách thành các NHÓM Ý ĐỊNH về đặc tính cây trồng. "
            "Mỗi nhóm gồm vài từ khóa đồng nghĩa ngắn (1-3 từ, tiếng Việt có dấu) có thể xuất hiện "
            "trong mô tả cây. Bỏ qua phần không liên quan đến đặc tính cây.\n"
            'Chỉ trả về JSON đúng dạng: {"groups":[{"intent":"...","keywords":["...","..."]}]}\n\n'
            'Ví dụ: "mình ở chung cư, lười tưới" -> '
            '{"groups":[{"intent":"chịu hạn","keywords":["chịu hạn","ít tưới","ít nước"]},'
            '{"intent":"không gian nhỏ","keywords":["trồng chậu","ban công","nhỏ gọn"]}]}\n\n'
            f'Yêu cầu: "{user_query}"'
        )
        raw = call_llm(prompt)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0)) if match else {}
        groups = []
        for g in data.get("groups", []):
            kws = [str(k).strip() for k in g.get("keywords", []) if str(k).strip()]
            if kws:
                groups.append({"intent": str(g.get("intent", "")), "keywords": kws})
        return groups

    return extractor


def _filter_crops(df: pd.DataFrame, user_query: Optional[str],
                  extractor: Optional[KeywordExtractor], query_norm: str):
    """Lọc cây theo ý định trong câu hỏi. Trả về (df_đã_lọc, thông_tin_bộ_lọc)."""
    df = df.copy()
    df["_match_count"] = 0
    info = {"applied": False, "method": "none", "groups": [],
            "matched": len(df), "relaxed": False, "note": "Không có yêu cầu cụ thể."}

    if not user_query or not user_query.strip():
        return df, info

    groups, method = _rule_groups(query_norm), "rule"
    if not groups and extractor is not None:
        try:
            groups, method = extractor(user_query), "llm"
        except Exception:
            logger.exception("LLM keyword extractor lỗi, chuyển sang từ khóa thô")
            groups = []
    if not groups:
        groups, method = _token_groups(query_norm), "keyword"
    if not groups:
        info["note"] = "Không nhận ra yêu cầu cụ thể, xếp hạng theo cảm biến."
        return df, info

    match_count = np.zeros(len(df), dtype=int)
    for g in groups:
        kws = [strip_accents(k) for k in g["keywords"]]
        hit = df["_haystack"].map(lambda text: any(_has_phrase(text, k) for k in kws))
        match_count += hit.to_numpy(dtype=int)
    df["_match_count"] = match_count

    info.update(method=method, groups=groups)
    strict = df[df["_match_count"] == len(groups)]
    if len(strict) > 0:
        info.update(applied=True, matched=len(strict), note="Đã lọc theo đủ các yêu cầu.")
        return strict, info
    relaxed = df[df["_match_count"] >= 1]
    if len(relaxed) > 0:
        info.update(applied=True, relaxed=True, matched=len(relaxed),
                    note="Không có cây đáp ứng đủ mọi yêu cầu, đã nới lỏng: chỉ cần khớp một phần.")
        return relaxed, info
    info.update(applied=False, matched=len(df),
                note="Không có cây nào khớp yêu cầu trong dữ liệu, đã bỏ lọc và xếp hạng theo cảm biến.")
    return df, info


# ============================== GIAI ĐOẠN 2: XẾP HẠNG ==============================
def _score(sensor: dict, cand: pd.DataFrame, ranges: dict):
    feats = [f for f in FEATURES if f in sensor]
    w = np.array([WEIGHTS[f] for f in feats])

    def norm(values, f):
        lo, hi = ranges[f]
        return np.clip((np.asarray(values, dtype=float) - lo) / (hi - lo), 0.0, 1.0)

    eps = 1e-6
    s = np.array([norm(sensor[f], f) for f in feats]) * w + eps
    C = np.column_stack([norm(cand[CSV_COLUMNS[f]].to_numpy(), f) for f in feats]) * w + eps

    cosine = (C @ s) / (np.linalg.norm(C, axis=1) * np.linalg.norm(s))
    closeness = 1.0 - np.linalg.norm(C - s, axis=1) / np.linalg.norm(w)
    final = COSINE_WEIGHT * cosine + (1.0 - COSINE_WEIGHT) * closeness
    return np.clip(final, 0, 1), cosine


def _build_item(rank: int, row: pd.Series, sensor: Optional[dict], ranges: dict,
                region_ctx: Optional[str]) -> dict:
    regions = row.get("_regions", frozenset())
    mv = int(row.get("market_value", 2))
    item = {
        "rank": rank,
        "crop_name": str(row["crop_name"]),
        "description": str(row["description"]),
        "care_tips": str(row.get("care_tips", "")),
        "matches_query": bool(row.get("_matches", True)),
        "score": None if pd.isna(row["_score"]) else round(float(row["_score"]) * 100, 1),
        "cosine": None if pd.isna(row["_cos"]) else round(float(row["_cos"]), 3),
        "ideal": {f: float(row[CSV_COLUMNS[f]]) for f in FEATURES},
        "regions": region_labels(regions),
        "region_codes": sorted(regions),
        "region_known": bool(regions),
        "region_ok": (None if (not regions or not region_ctx) else region_ctx in regions),
        "market_value": mv,
        "market_label": MARKET_LABELS[mv],
        "conditions": {},
        "warnings": [],
    }
    if sensor:
        for f in FEATURES:
            if f not in sensor:
                continue
            ideal, current = item["ideal"][f], sensor[f]
            diff = current - ideal
            span = ranges[f][1] - ranges[f][0]
            if abs(diff) / span <= TOLERANCE_RATIO[f]:
                status = "ok"
            else:
                status = "high" if diff > 0 else "low"
                item["warnings"].append(
                    f"{LABELS[f]} hiện {current:g}{UNITS[f]}, "
                    f"{'cao' if diff > 0 else 'thấp'} hơn mức lý tưởng ({ideal:g}{UNITS[f]}) "
                    f"khoảng {abs(diff):.1f}{UNITS[f]}"
                )
            item["conditions"][f] = {"current": current, "ideal": ideal,
                                     "diff": round(diff, 2), "status": status}
    return item


def _pick_alternatives(scored: pd.DataFrame, sensor, region_ctx, target_idx,
                       target_mv: Optional[int], n: int = 2):
    """Chọn cây thay thế: hợp vùng + hợp cảm biến + giá trị kinh tế gần với cây gốc."""
    pool = scored.drop(index=target_idx) if target_idx is not None else scored
    notes = []
    if region_ctx:
        regional = pool[pool["_regions"].map(lambda s: _region_match(s, [region_ctx]))]
        if len(regional):
            pool = regional
        else:
            notes.append("không có cây nào khai báo hợp vùng này")
    if sensor:
        fit = pool[pool["_score"] * 100 >= SUITABLE_SCORE]
        if len(fit):
            pool = fit
        else:
            notes.append(f"không cây nào đạt ngưỡng {SUITABLE_SCORE:g}% điểm cảm biến")
    if pool.empty:
        return pool, notes
    pool = pool.copy()
    if target_mv is not None:
        pool["_gap"] = (pool["market_value"] - target_mv).abs()
        near = pool[pool["_gap"] <= 1]
        pool = near if len(near) else pool
        sort_cols = ["_gap", "_score"] if sensor else ["_gap"]
        pool = pool.sort_values(sort_cols, ascending=[True, False][: len(sort_cols)])
    elif sensor:
        pool = pool.sort_values("_score", ascending=False)
    else:
        pool = pool.sort_values("market_value", ascending=False)
    return pool.head(n), notes


# ============================== HÀM CHÍNH ==============================
def recommend_crops(
    sensor_data: Optional[dict] = None,
    user_query: Optional[str] = None,
    top_k: int = 3,
    keyword_extractor: Optional[KeywordExtractor] = None,
    target_profile: Optional[dict] = None,
) -> dict:
    """
    Gợi ý cây trồng (chữ ký v1 giữ nguyên; thêm tham số tùy chọn target_profile).

    target_profile: hồ sơ cây LẠ (không có trong CSV) do LLM ước lượng:
        {"name", "market_value": 1-3, "region_ratings": {"north": {"rating": 1-4, "reason": "..."}, ...}}
        → dùng để xếp hạng vùng miền và chọn cây thay thế có giá trị kinh tế tương đương.

    Trả về dict: query, sensor, filter, total_candidates, recommendations[...]
    + (mới) analysis, region, list_mode, target, alternatives.
    """
    df = load_crops()
    ranges = _get_ranges(df)
    sensor = parse_sensor_data(sensor_data)
    analysis = analyze_query(user_query)
    qn = analysis["normalized"]

    # ----- Vùng: ưu tiên vùng người dùng nêu → FARM_REGION → đoán từ nhiệt độ -----
    user_regions = analysis["regions"]
    farm_region, farm_src = _farm_region_from_env_or_sensor(sensor)
    if user_regions:
        region_ctx, region_src = user_regions[0], "user"
    else:
        region_ctx, region_src = farm_region, farm_src
    region_info = {
        "detected": user_regions, "context": region_ctx, "source": region_src,
        "label": REGION_LABELS.get(region_ctx) if region_ctx else None,
    }

    needs = analysis["needs"]
    profit_need = bool({"quick_profit", "high_profit"} & set(needs))
    list_mode = analysis["list_mode"]

    # ----- Giai đoạn 1: lọc theo ý định + vùng người dùng nêu rõ -----
    target_idx = None if (list_mode or analysis["vague"]) else _find_target_crop(df, qn)
    if target_idx is not None:
        # Người dùng nêu đích danh 1 cây → chỉ giữ cây đó làm "ứng viên khớp yêu cầu";
        # các cây thay thế sẽ được bổ sung bên dưới theo điều kiện vườn.
        cand = df.loc[[target_idx]].copy()
        cand["_match_count"] = 1
        tname = str(df.loc[target_idx, "crop_name"])
        filter_info = {"applied": True, "method": "crop_name", "matched": 1, "relaxed": False,
                       "groups": [{"intent": "cây được nhắc đích danh", "keywords": [tname]}],
                       "note": f"Người dùng hỏi đích danh cây: {tname}."}
    else:
        cand, filter_info = _filter_crops(df, user_query, keyword_extractor, qn)
    if user_regions and target_idx is None:
        mask = cand["_regions"].map(lambda s: _region_match(s, user_regions))
        names = ", ".join(region_labels(user_regions))
        if mask.any():
            cand = cand[mask]
            filter_info["applied"] = True
            filter_info["note"] += f" Đã lọc theo vùng: {names}."
        else:
            filter_info["note"] += f" Không cây nào khai báo hợp vùng {names}, đã bỏ lọc vùng."

    # ----- Giai đoạn 2: điểm cảm biến cho TOÀN BỘ cây (để còn tìm cây thay thế) -----
    scored = df.copy()
    scored["_match_count"] = 0
    if sensor:
        scored["_score"], scored["_cos"] = _score(sensor, scored, ranges)
    else:
        scored["_score"], scored["_cos"] = np.nan, np.nan

    def rank_key(part: pd.DataFrame) -> pd.Series:
        base = part["_score"].fillna(0.0)
        if region_ctx:
            ok = part["_regions"].map(lambda s: _region_match(s, [region_ctx]) if s else None)
            base = base + ok.map({True: REGION_BONUS, False: -REGION_PENALTY}).fillna(0.0)
        if profit_need:
            base = base + PROFIT_BONUS * (part["market_value"] - 1) / 2.0
        hay = part["_haystack"]
        if {"low_capital", "low_risk", "low_effort", "beginner"} & set(needs):
            easy = hay.map(lambda t: any(k in t for k in ("de trong", "it cham soc", "it sau benh", "de song")))
            hard = hay.map(lambda t: any(k in t for k in ("nhieu nam", "kho cham", "nha mang", "kho trong")))
            base = base + easy.astype(float) * NEED_BONUS - hard.astype(float) * NEED_PENALTY
        if {"fast_harvest", "quick_profit"} & set(needs):
            fast = hay.map(lambda t: any(k in t for k in ("thu hoach nhanh", "ngan ngay", "mau lon")))
            base = base + fast.astype(float) * NEED_BONUS
        return base

    def rank(part: pd.DataFrame, match_counts=None) -> pd.DataFrame:
        part = part.copy()
        part["_score"] = scored.loc[part.index, "_score"]
        part["_cos"] = scored.loc[part.index, "_cos"]
        if match_counts is not None:
            part["_match_count"] = match_counts.loc[part.index]
        part["_rank_key"] = rank_key(part)
        if sensor:
            return part.sort_values(["_rank_key", "_match_count"], ascending=False)
        sort_cols = ["_match_count", "market_value", "crop_name"] if profit_need else ["_match_count", "crop_name"]
        asc = [False, False, True] if profit_need else [False, True]
        return part.sort_values(sort_cols, ascending=asc)

    match_counts = cand["_match_count"]
    cand = cand.copy()
    cand["_matches"] = True
    cand = rank(cand, match_counts)

    k = max(1, int(top_k))
    if list_mode:
        k = max(k, LIST_TOP_K)
    top = cand.head(k)

    if filter_info["applied"] and len(top) < k:
        rest = df.drop(index=cand.index).copy()
        rest["_match_count"], rest["_matches"] = 0, False
        top = pd.concat([top, rank(rest, rest["_match_count"]).head(k - len(top))])
        filter_info["note"] += " Đã bổ sung cây thay thế (không khớp yêu cầu) phù hợp với điều kiện vườn."

    items = [_build_item(i + 1, row, sensor, ranges, region_ctx) for i, (_, row) in enumerate(top.iterrows())]

    # ----- Dự phòng kinh tế: cây người dùng muốn trồng có hợp vườn không? -----
    target, alternatives, region_ranking = None, [], []
    if not list_mode and not analysis["vague"]:
        if target_idx is not None:
            trow = scored.loc[target_idx]
            t_score = None if pd.isna(trow["_score"]) else round(float(trow["_score"]) * 100, 1)
            t_regions = trow["_regions"]
            t_region_ok = None if (not t_regions or not region_ctx) else region_ctx in t_regions
            reasons = []
            if t_region_ok is False:
                reasons.append(f"không thuộc vùng phù hợp của cây (cây hợp: {', '.join(region_labels(t_regions))};"
                               f" vườn/yêu cầu: {REGION_LABELS[region_ctx]})")
            if t_score is not None and t_score < SUITABLE_SCORE:
                reasons.append(f"điểm phù hợp cảm biến chỉ {t_score}% (ngưỡng {SUITABLE_SCORE:g}%)")
            suitable = not reasons
            region_ranking = ranking_from_csv(t_regions)
            target = {
                "name": str(trow["crop_name"]), "found_in_db": True, "suitable": suitable,
                "sensor_score": t_score, "region_ok": t_region_ok,
                "regions": region_labels(t_regions), "market_value": int(trow["market_value"]),
                "market_label": MARKET_LABELS[int(trow["market_value"])], "reasons": reasons,
                "best_regions": [r["label"] for r in region_ranking if r["rating"] == max(
                    (x["rating"] for x in region_ranking), default=0)],
            }
            if not suitable:
                alt_df, alt_notes = _pick_alternatives(scored, sensor, region_ctx, target_idx,
                                                       int(trow["market_value"]), n=2)
                alt_df = alt_df.copy()
                alternatives = [_build_item(i + 1, r, sensor, ranges, region_ctx)
                                for i, (_, r) in enumerate(alt_df.iterrows())]
                for a in alternatives:
                    gap = abs(a["market_value"] - int(trow["market_value"]))
                    a["value_match"] = "tương đương" if gap == 0 else "gần tương đương"
                target["alternative_notes"] = alt_notes
        elif analysis["unknown_crop_guess"]:
            name = (target_profile or {}).get("name") or analysis["unknown_crop_guess"]
            target = {"name": name, "found_in_db": False}
            mv = analysis["target_market_value"]
            n_alt = 3
            if target_profile:
                mv = int(target_profile.get("market_value") or 2)
                ratings = {c: v.get("rating", 2) for c, v in (target_profile.get("region_ratings") or {}).items()}
                r_ctx = ratings.get(region_ctx) if region_ctx else None
                region_ranking = ranking_from_profile(target_profile)
                top_rating = region_ranking[0]["rating"] if region_ranking else 0
                target.update(
                    suitable=None if r_ctx is None else r_ctx >= 3,
                    region_rating=r_ctx, market_value=mv, market_label=MARKET_LABELS[mv],
                    best_regions=[r["label"] for r in region_ranking if r["rating"] == top_rating],
                    market_note=target_profile.get("market_note", ""),
                    growth_days=target_profile.get("growth_days"),
                    capital=target_profile.get("capital"), difficulty=target_profile.get("difficulty"),
                )
                n_alt = 0 if target["suitable"] else 2
            if n_alt:
                alt_df, alt_notes = _pick_alternatives(scored, sensor, region_ctx, None, mv, n=n_alt)
                alternatives = [_build_item(i + 1, r, sensor, ranges, region_ctx)
                                for i, (_, r) in enumerate(alt_df.iterrows())]
                for a in alternatives:
                    if mv is not None:
                        gap = abs(a["market_value"] - mv)
                        a["value_match"] = "tương đương" if gap == 0 else "gần tương đương"
                    else:
                        a["value_match"] = "chưa xác định (cây gốc không có trong dữ liệu)"
                target["alternative_notes"] = alt_notes

    filter_info["groups"] = [{"intent": g["intent"], "keywords": g["keywords"]} for g in filter_info["groups"]]

    return {
        "query": user_query,
        "sensor": sensor,
        "filter": filter_info,
        "total_candidates": int(len(cand)),
        "recommendations": items,
        "list_mode": list_mode,
        "analysis": {k_: analysis[k_] for k_ in
                     ("kind", "vague", "beginner_only", "needs", "intents", "regions", "followup",
                      "target_market_value")},
        "region": region_info,
        "target": target,
        "alternatives": alternatives,
        "region_ranking": region_ranking,
    }


if __name__ == "__main__":
    demo_sensor = {"soil_moisture": 34.7, "temperature": 28.8, "light": 52000}
    for q in [None, "Tôi muốn trồng cây chịu hạn", "toi muon trong cay o khu vuc mien nam, co nhung loai cay nao trong duoc",
              "cay nao dễ trồng cho người mới", "toi muon trong cay dao o vuon duoc khong"]:
        res = recommend_crops(demo_sensor, q, top_k=3)
        print(f"\n=== {q!r} | {res['analysis']['kind']} vague={res['analysis']['vague']} "
              f"list={res['list_mode']} | {res['filter']['note']}")
        for r in res["recommendations"]:
            print(f"  #{r['rank']} {r['crop_name']:<18} {r['score']}%  {r['regions']} gt={r['market_label']}")
        if res["target"]:
            print("  TARGET:", res["target"])
        for a in res["alternatives"]:
            print("  ALT:", a["crop_name"], a["market_label"], a["score"])