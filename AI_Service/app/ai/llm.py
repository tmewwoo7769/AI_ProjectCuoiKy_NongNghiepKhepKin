# -*- coding: utf-8 -*-
"""
llm.py v2 – Chatbot chuyên gia nông nghiệp, kết nối với recommender.py

Luồng xử lý của chat_with_llm():
  1. analyze_query(): phân loại câu hỏi (nông nghiệp / chuỗi lạnh / xã giao), mơ hồ hay rõ,
     có đòi danh sách không, vùng miền, nhu cầu ngầm. Không tốn token.
  2. Câu hỏi nông nghiệp đủ rõ → recommend_crops() (có vùng miền + giá trị kinh tế + cây thay thế).
  3. Ghép SYSTEM_PROMPT + "DỮ LIỆU THỜI GIAN THỰC" (cảm biến + phân tích + kết quả gợi ý).
  4. Gọi Gemini/OpenAI (có giới hạn thời gian, tự thử lại, tự đổi model khi quá tải).
  5. LLM lỗi → câu trả lời dự phòng DỰA TRÊN kết quả recommender (có bảng, có cây thay thế).

Biến môi trường:
  LLM_PROVIDER         gemini | openai                       (mặc định gemini)
  GEMINI_API_KEY / OPENAI_API_KEY
  LLM_MODEL            model chính          LLM_SMALL_MODEL  model nhỏ (lọc từ khóa)
  LLM_FALLBACK_MODELS  danh sách model dự phòng, ngăn cách bằng dấu phẩy
  LLM_REQUEST_TIMEOUT  giây tối đa cho MỘT lần gọi (mặc định 20)
  LLM_TOTAL_TIMEOUT    giây tối đa cho TOÀN BỘ các lần thử (mặc định 40; phải < timeout của frontend)
  USE_LLM_FILTER       1 = bật LLM nhỏ hiểu yêu cầu phức tạp (mặc định 0)
  FARM_REGION          north | central | highlands | south  (xem recommender.py)
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from functools import lru_cache
from typing import Optional

from app.ai.recommender import (
    LABELS,
    MARKET_LABELS,
    REGION_LABELS,
    UNITS,
    analyze_query,
    make_llm_keyword_extractor,
    parse_sensor_data,
    recommend_crops,
)

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "gemini": {"main": "gemini-3.5-flash", "small": "gemini-3.1-flash-lite"},
    # đổi nếu bạn dùng OpenAI (hoặc đặt LLM_MODEL trong .env)
    "openai": {"main": "gpt-4o-mini", "small": "gpt-4o-mini"},
}

# ============================== SYSTEM PROMPT ==============================
SYSTEM_PROMPT = """
# VAI TRÒ
Bạn là **ABc_Nông** 🌱 – cố vấn nông nghiệp cho hệ thống vườn thông minh (IoT). Bạn am hiểu thổ nhưỡng, khí hậu 4 vùng của Việt Nam (Bắc, Trung, Tây Nguyên, Nam), kỹ thuật canh tác và thị trường nông sản. Giọng thân thiện, kiên nhẫn, như người đi trước chỉ đường cho người mới. Luôn trả lời bằng tiếng Việt có dấu (người dùng viết tiếng Anh thì vẫn đáp tiếng Việt, có thể kèm tên tiếng Anh của cây trong ngoặc).

# NGUYÊN TẮC TRUNG THỰC (QUAN TRỌNG NHẤT)
1. Chỉ dùng số liệu có trong khối "DỮ LIỆU THỜI GIAN THỰC". TUYỆT ĐỐI không bịa số liệu, lịch sử tưới, hay dữ liệu chuyến hàng/chuỗi lạnh. Không có thì nói thẳng là chưa có.
2. Cảm biến chỉ là ảnh chụp tại một thời điểm của MỘT khu vườn. Đừng suy ra mùa vụ, loại đất hay vùng miền một cách chắc chắn từ đó. Nếu mục "Vùng" ghi là ước đoán hoặc chưa rõ, hãy nêu giả định hoặc hỏi lại.
3. "Độ phù hợp" trong danh sách là điểm tính toán từ cảm biến so với mức lý tưởng của từng cây: dùng đúng số đó, không tự đổi. Phần vùng miền, kỹ thuật, thị trường bạn được bổ sung bằng kiến thức của mình, nhưng phải nói rõ đó là nhận định chung.
4. Không hứa lợi nhuận cụ thể (giá nông sản biến động). Không chắc thì dùng "thường", "khoảng".

# NHẬN BIẾT TÌNH HUỐNG (xem mục "Phân loại câu hỏi" trong dữ liệu)
Người dùng hay gõ không dấu, viết tắt, sai chính tả hoặc lẫn tiếng Anh: hãy tự hiểu ý, đừng bắt họ viết lại.
- **XÃ GIAO / NGOÀI CHỦ ĐỀ**: đáp 1-2 câu thân thiện, rồi gợi ý 3 hướng bạn giúp được (xem tình trạng vườn / chọn cây hợp vườn / xử lý khô-úng-sâu bệnh). Không đổ số liệu cảm biến nếu họ không hỏi.
- **CHUỖI LẠNH / VẬN CHUYỂN**: dữ liệu bên dưới KHÔNG có thông tin chuyến hàng. Tuyệt đối không dùng nhiệt độ/độ ẩm đất của vườn để nói về xe lạnh hay thùng hàng. Nói rõ bạn chưa thấy số liệu chuyến hàng, đưa 3-4 nguyên tắc chung ngắn gọn, và mời người dùng xem trang Chuỗi lạnh hoặc cho biết loại hàng, nhiệt độ thùng, thời gian di chuyển.
- **MƠ HỒ**: ĐỪNG đoán và đừng liệt kê cây. Viết 1 câu nhận định ngắn về vườn, rồi hỏi 2-3 câu dẫn dắt, mỗi câu kèm sẵn lựa chọn để người mới chỉ cần chọn. Ví dụ: (a) Mục tiêu: ăn trong nhà / bán kiếm lời / làm cảnh? (b) Thời gian và vốn: ít / vừa / nhiều? (c) Vườn ở vùng nào, có nắng cả ngày không?
- **NGƯỜI MỚI, yêu cầu còn chung**: gợi ý nhanh 2-3 cây dễ nhất, sau đó hỏi thêm 2-3 câu như trên để thu hẹp.
- **RÕ RÀNG**: đi thẳng vào việc.
- **ĐỀ XUẤT TRỒNG MỚI**: Khi đề xuất một cây cụ thể để người dùng bắt đầu trồng, cuối câu trả lời bắt buộc hỏi: "Bạn có muốn trồng cây này không?" (frontend hiện nút Có/Không và vẽ hoạt cảnh trồng cây trên bản sao số 2D).

# PHÂN TÍCH NHU CẦU NGẦM
Mục "Nhu cầu ngầm" cho biết mục tiêu có thể ẩn sau câu hỏi. Mở đầu phần tư vấn bằng 1 câu xác nhận ("Mình hiểu bạn đang ưu tiên ..."), rồi chọn cây và giải thích theo đó:
- Lợi nhuận nhanh: cây ngắn ngày, thu hoạch sớm, giá trị Trung bình/Cao; nói rõ rủi ro giá và công chăm sóc.
- Ít rủi ro / vốn thấp: cây dễ sống, ít sâu bệnh, đầu tư nhỏ; tránh cây cần nhà màng, giàn đắt hay nhiều năm mới thu hoạch.
- Thu hoạch nhanh: ưu tiên rau ngắn ngày.
- Người mới / ít công: cây ít chăm; nêu 2-3 việc cần làm mỗi tuần.
Nếu mục tiêu mâu thuẫn (vd "lời lớn mà không rủi ro"), nói thẳng và đề xuất cách cân bằng.

# KIẾN THỨC VÙNG MIỀN (cây lạ hoặc "trồng X được không?")
Khi người dùng hỏi một cây KHÔNG có trong dữ liệu hệ thống (mục "Cây người dùng nhắc" ghi chưa có) hoặc hỏi một cây cụ thể:
1. Dùng hiểu biết của bạn về đặc tính sinh thái (nhiệt độ, độ ẩm, nhu cầu lạnh, loại đất) để XẾP HẠNG 4 vùng Bắc / Trung / Tây Nguyên / Nam theo mức Rất hợp – Hợp – Hạn chế – Không nên, mỗi vùng 1 dòng lý do (bảng nhỏ là tốt). Đừng chỉ dựa vào số liệu cảm biến hay CSV.
2. Đối chiếu với mục "Vùng" của người dùng; chưa rõ thì nêu giả định hoặc hỏi lại.
3. Hợp: nêu 2-3 điều kiện/cách làm cần lưu ý. Không hợp: khuyên thẳng thắn là không nên.

# DỰ PHÒNG KINH TẾ (cây không hợp thì đề xuất cây thay thế)
Khi cây người dùng muốn trồng không hợp vùng hoặc điều kiện vườn:
- Chỉ chọn cây thay thế từ mục "Cây thay thế đề xuất" (đã lọc hợp vùng + hợp cảm biến), tối đa 2 cây. KHÔNG tự bịa tên cây ngoài danh sách đó.
- Cây thay thế phải có giá trị kinh tế tương đương hoặc gần (Thấp/Trung bình/Cao). Nếu cây gốc không có trong dữ liệu, hãy tự ước lượng giá trị của nó (ghi rõ là ước lượng) rồi chọn cây gần nhất trong danh sách. Nếu chỉ có cây kém giá trị hơn, nói thẳng điều đó.
- Mỗi cây thay thế: 1 câu vì sao hợp (vùng + cảm biến + giá trị) và 1 mẹo bắt đầu.

# CẢI THIỆN ĐẤT / VƯỜN
Chỉ khi liên quan: đất quá khô/ướt thì gợi ý kỹ thuật cụ thể (tưới nhỏ giọt, phủ gốc, trộn phân hữu cơ/xơ dừa, làm luống cao, che nắng...).

# ĐỊNH DẠNG TRẢ LỜI (Markdown)
- Câu đầu tiên là nhận định tình hình/ý định (1 câu). Sau đó là đoạn ngắn hoặc gạch đầu dòng, tránh tường chữ. Thường dưới ~250 từ (trừ khi có bảng).
- Chế độ DANH SÁCH: BẮT BUỘC có bảng Markdown. Chèn bảng trong mục "Bảng gợi ý" (được rút gọn cột ghi chú), giữ thứ tự theo độ phù hợp; sau bảng nhận xét 2-3 câu: cây nào hợp nhất, cây nào cần điều kiện gì. Nếu cần thêm lựa chọn, được thêm tối đa 3 cây phổ biến từ kiến thức của bạn trong mục riêng "Ngoài dữ liệu hệ thống (tham khảo)".
- Không đọc nguyên văn khối dữ liệu nội bộ; không nhắc các từ "ngữ cảnh", "CSV", "hệ thống nội bộ". Không nhắc lại số liệu cảm biến nếu không cần.
- Kết thúc bằng tối đa 1 câu hỏi gợi mở (trừ khi đang hỏi dẫn dắt).
""".strip()


# ============================== GỌI LLM ==============================
def _provider() -> str:
    return os.getenv("LLM_PROVIDER", "gemini").lower()


def _model_name(kind: str = "main") -> str:
    env = "LLM_MODEL" if kind == "main" else "LLM_SMALL_MODEL"
    name = (os.getenv(env) or "").strip().strip("'\"")
    if name.startswith("models/"):
        name = name[len("models/"):]
    return name or DEFAULT_MODELS[_provider()][kind]


def _fallback_models() -> list:
    raw = os.getenv("LLM_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite")
    return [m.strip() for m in raw.split(",") if m.strip()]


@lru_cache(maxsize=1)
def _gemini_client():
    from google import genai
    from google.genai import types
    key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip().strip("'\"")
    if not key:
        raise RuntimeError("Thiếu GEMINI_API_KEY trong file .env (đặt cạnh thư mục app/)")
    timeout_ms = int(float(os.getenv("LLM_REQUEST_TIMEOUT", "20")) * 1000)
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))


@lru_cache(maxsize=1)
def _openai_client():
    from openai import OpenAI  # pip install openai
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _gemini_call(system_prompt: str, history: list, user_message: str,
                 model: str, temperature: float) -> str:
    from google.genai import types
    contents = [
        types.Content(role="user" if m["role"] == "user" else "model",
                      parts=[types.Part(text=m["content"])])
        for m in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))
    resp = _gemini_client().models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=4096,  # model "thinking" tốn một phần token nên để dư
        ),
    )
    return (resp.text or "").strip()


def _gemini_generate(system_prompt: str, history: list, user_message: str,
                     model: str, temperature: float) -> str:
    """Thử model chính (2 lần), quá tải thì sang model dự phòng. Có tổng thời gian tối đa
    để không vượt timeout của frontend (nếu vượt, người dùng chỉ thấy 'Không thể gửi câu hỏi')."""
    from google.genai import errors

    deadline = time.monotonic() + float(os.getenv("LLM_TOTAL_TIMEOUT", "40"))
    candidates = [model] + [m for m in _fallback_models() if m != model]
    last_exc: Optional[Exception] = None

    for name in candidates:
        for attempt in range(2):
            if time.monotonic() > deadline:
                raise last_exc or TimeoutError("Hết thời gian chờ LLM")
            try:
                return _gemini_call(system_prompt, history, user_message, name, temperature)
            except errors.APIError as e:
                last_exc = e
                code = getattr(e, "code", None)
                if code in (429, 500, 503, 504):
                    logger.warning("Model %s lỗi %s (lần %d), thử lại...", name, code, attempt + 1)
                    time.sleep(1.0)
                    continue
                if code == 404:
                    logger.warning("Model %s không tồn tại, bỏ qua", name)
                    break
                raise  # 400/401/403: lỗi cấu hình, không thử lại
            except Exception as e:  # timeout mạng, lỗi kết nối...
                last_exc = e
                logger.warning("Model %s lỗi kết nối/timeout: %s", name, e)
                break
    raise last_exc  # type: ignore[misc]


def _generate(system_prompt: str, history: list, user_message: str,
              model: Optional[str] = None, temperature: float = 0.5) -> str:
    """Gọi LLM với system prompt + lịch sử + tin nhắn mới. Trả về văn bản."""
    model = model or _model_name("main")

    if _provider() == "openai":
        messages = [{"role": "system", "content": system_prompt},
                    *history,
                    {"role": "user", "content": user_message}]
        resp = _openai_client().chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_completion_tokens=1500)
        return (resp.choices[0].message.content or "").strip()

    return _gemini_generate(system_prompt, history, user_message, model, temperature)


@lru_cache(maxsize=1)
def get_llm_keyword_extractor():
    """LLM nhỏ để hiểu yêu cầu tự do (chỉ dùng khi rule-based không nhận ra)."""
    return make_llm_keyword_extractor(
        lambda prompt: _generate("Bạn chỉ trả về JSON hợp lệ, không giải thích.", [], prompt,
                                 model=_model_name("small"), temperature=0.0)
    )


def get_keyword_extractor_if_enabled():
    return get_llm_keyword_extractor() if os.getenv("USE_LLM_FILTER", "0") == "1" else None


# ============================== DỰNG NGỮ CẢNH CHO LLM ==============================
_REGION_SHORT = {"north": "Bắc", "central": "Trung", "highlands": "Tây Nguyên", "south": "Nam"}
_REGION_SOURCE = {
    "user": "do người dùng nêu trong câu hỏi",
    "env": "theo cấu hình của khu vườn",
    "sensor": "ƯỚC ĐOÁN THÔ từ nhiệt độ (không chắc chắn – nên xác nhận với người dùng)",
}
_KIND_TEXT = {
    "farm": "NÔNG NGHIỆP",
    "cold_chain": "CHUỖI LẠNH / VẬN CHUYỂN (KHÔNG có dữ liệu chuyến hàng trong phần này)",
    "smalltalk": "XÃ GIAO / NGOÀI CHỦ ĐỀ",
}
_NEED_TEXT = {
    "quick_profit": "muốn có lợi nhuận nhanh",
    "high_profit": "ưu tiên lợi nhuận / giá trị kinh tế cao",
    "low_risk": "muốn ít rủi ro, ổn định",
    "low_capital": "vốn đầu tư thấp",
    "fast_harvest": "muốn thu hoạch nhanh",
    "beginner": "là người mới, ít kinh nghiệm",
    "low_effort": "muốn ít công chăm sóc",
}


def _now_vn() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    except Exception:
        return datetime.now()


def _part_of_day(hour: int) -> str:
    if 5 <= hour < 11:
        return "buổi sáng"
    if 11 <= hour < 14:
        return "buổi trưa"
    if 14 <= hour < 18:
        return "buổi chiều"
    return "buổi tối"


def _soil_label(v: float) -> str:
    return "khá khô" if v < 30 else "vừa phải" if v <= 70 else "khá ẩm ướt"


def _temp_label(v: float) -> str:
    return "mát/lạnh" if v < 18 else "ôn hòa" if v <= 30 else "nóng" if v <= 35 else "rất nóng"


def _cut(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _regions_cell(item: dict) -> str:
    codes = item.get("region_codes") or []
    if not item.get("region_known"):
        return "chưa rõ"
    if len(codes) == 4:
        return "Mọi vùng"
    return ", ".join(_REGION_SHORT[c] for c in _REGION_SHORT if c in codes)


def render_table(items: list) -> str:
    """Bảng Markdown các cây (dùng cho chế độ danh sách và câu trả lời dự phòng)."""
    rows = ["| # | Cây | Độ phù hợp | Vùng phù hợp | Giá trị kinh tế | Lưu ý nhanh |",
            "|---|---|---|---|---|---|"]
    for r in items:
        score = f"{r['score']}%" if r["score"] is not None else "—"
        note = r["warnings"][0] if r["warnings"] else (r.get("care_tips") or r.get("description") or "")
        note = _cut(note, 70).replace("|", "/")
        rows.append(f"| {r['rank']} | **{r['crop_name']}** | {score} | {_regions_cell(r)} | "
                    f"{r['market_label']} | {note} |")
    return "\n".join(rows)


def build_context(sensor_data: Optional[dict], rec: Optional[dict], analysis: Optional[dict] = None) -> str:
    """Chuyển cảm biến + phân tích + kết quả recommender thành văn bản cho LLM."""
    now = _now_vn()
    lines = ["# DỮ LIỆU THỜI GIAN THỰC (nội bộ – không đọc nguyên văn cho người dùng)",
             f"Thời điểm: {now:%H:%M %d/%m/%Y}, {_part_of_day(now.hour)}."]

    # ---- Phân loại câu hỏi ----
    if analysis:
        lines += ["", "## Phân loại câu hỏi", f"- Loại: {_KIND_TEXT.get(analysis['kind'], analysis['kind'])}"]
        if analysis["kind"] == "farm":
            if analysis["vague"]:
                clarity = "MƠ HỒ → hỏi lại 2-3 câu dẫn dắt, CHƯA gợi ý cây cụ thể"
            elif analysis.get("beginner_only"):
                clarity = "NGƯỜI MỚI, yêu cầu còn chung → gợi ý nhanh 2-3 cây dễ + hỏi thêm 2-3 câu"
            else:
                clarity = "ĐỦ RÕ → trả lời trực tiếp"
            lines.append(f"- Độ rõ ràng: {clarity}")
            lines.append("- Chế độ: " + ("DANH SÁCH → bắt buộc có bảng" if (rec and rec.get("list_mode")) else "TƯ VẤN"))
            needs = [_NEED_TEXT[n] for n in analysis["needs"] if n in _NEED_TEXT]
            lines.append("- Nhu cầu ngầm suy ra: " + ("; ".join(needs) if needs else "chưa thấy rõ"))
            if analysis["regions"]:
                lines.append("- Vùng người dùng nhắc: " + ", ".join(REGION_LABELS[c] for c in analysis["regions"]))

    # ---- Cảm biến ----
    lines += ["", "## Cảm biến hiện tại"]
    sensor = parse_sensor_data(sensor_data)
    if not sensor:
        lines.append("- KHÔNG có dữ liệu cảm biến (thiết bị có thể đang offline).")
    else:
        if "soil" in sensor:
            lines.append(f"- {LABELS['soil']}: {sensor['soil']:g}{UNITS['soil']} ({_soil_label(sensor['soil'])})")
        if "temp" in sensor:
            lines.append(f"- {LABELS['temp']}: {sensor['temp']:g}{UNITS['temp']} ({_temp_label(sensor['temp'])})")
        if "light" in sensor:
            lines.append(f"- {LABELS['light']}: {sensor['light']:.0f}{UNITS['light']} (thang 0-100)")
        missing = [LABELS[f] for f in ("soil", "temp", "light") if f not in sensor]
        if missing:
            lines.append(f"- Thiếu dữ liệu: {', '.join(missing)}")

    # ---- Kết quả recommender ----
    if rec:
        flt, region = rec["filter"], rec.get("region") or {}
        lines += ["", "## Vùng của khu vườn"]
        if region.get("context"):
            lines.append(f"- {REGION_LABELS[region['context']]} ({_REGION_SOURCE.get(region.get('source'), '')})")
        else:
            lines.append("- CHƯA RÕ (hãy hỏi người dùng nếu việc chọn cây phụ thuộc vào vùng)")

        lines += ["", "## Kết quả từ hệ thống gợi ý (đã xếp hạng, #1 phù hợp nhất)",
                  f'Yêu cầu người dùng: "{rec["query"]}"']
        if flt["groups"]:
            lines.append(f"Ý định nhận ra: {', '.join(g['intent'] for g in flt['groups'])} | Ghi chú bộ lọc: {flt['note']}")
        else:
            lines.append(f"Ghi chú bộ lọc: {flt['note']}")
        lines.append(f"Số cây qua bộ lọc: {rec['total_candidates']}")

        for r in rec["recommendations"]:
            score = f"{r['score']}%" if r["score"] is not None else "chưa có (thiếu cảm biến)"
            tag = "" if r.get("matches_query", True) else " [THAY THẾ – không khớp yêu cầu, chọn theo điều kiện vườn]"
            lines.append(f"{r['rank']}. {r['crop_name']}{tag} – độ phù hợp: {score} | vùng: {_regions_cell(r)} | "
                         f"giá trị kinh tế: {r['market_label']}")
            lines.append(f"   Đặc tính: {r['description']}")
            if r.get("care_tips"):
                lines.append(f"   Mẹo chăm sóc: {r['care_tips']}")
            if r["warnings"]:
                lines.append("   Chỉ số đang lệch: " + "; ".join(r["warnings"]))
            elif r["conditions"]:
                lines.append("   Điều kiện hiện tại khớp tốt với mức lý tưởng.")

        if rec.get("list_mode"):
            lines += ["", "## Bảng gợi ý (dùng cho chế độ DANH SÁCH)", render_table(rec["recommendations"])]

        t = rec.get("target")
        if t:
            lines += ["", "## Cây người dùng nhắc"]
            if t["found_in_db"]:
                verdict = "HỢP" if t["suitable"] else "KHÔNG HỢP"
                lines.append(f"- {t['name']}: {verdict} với vườn/vùng hiện tại | giá trị kinh tế: {t['market_label']} | "
                             f"vùng thích hợp của cây: {', '.join(t['regions']) or 'chưa rõ'} | "
                             f"điểm cảm biến: {t['sensor_score'] if t['sensor_score'] is not None else 'chưa có'}")
                for reason in t["reasons"]:
                    lines.append(f"  - Lý do không hợp: {reason}")
            else:
                lines.append(f"- \"{t['name']}\": CHƯA CÓ trong dữ liệu hệ thống (tên do hệ thống đoán từ câu hỏi, có thể sai). "
                             "Hãy dùng kiến thức vùng miền của bạn.")
            alts = rec.get("alternatives") or []
            if alts:
                lines.append("")
                lines.append("## Cây thay thế đề xuất (đã lọc hợp vùng + hợp cảm biến, gần giá trị kinh tế)")
                for a in alts:
                    lines.append(f"- {a['crop_name']} – độ phù hợp {a['score']}% | vùng: {_regions_cell(a)} | "
                                 f"giá trị kinh tế: {a['market_label']} ({a.get('value_match', '')})")
                    lines.append(f"  Đặc tính: {a['description']}")
                    if a.get("care_tips"):
                        lines.append(f"  Mẹo: {a['care_tips']}")
                for note in t.get("alternative_notes", []):
                    lines.append(f"- Lưu ý: {note}")
            elif t["found_in_db"] and not t["suitable"]:
                lines.append("- Không tìm được cây thay thế phù hợp trong dữ liệu: hãy nói thẳng và gợi ý cải tạo/đổi vùng.")
    return "\n".join(lines)


# ============================== HỎI/TRẢ LỜI "CÓ MUỐN TRỒNG KHÔNG?" ==============================
_PLANT_YES = re.compile(
    r"^\s*(co|ok|oke|dong y|chap nhan|muon trong|vang|duoc|yes|yep|cu trong|"
    r"trong di|trong cay do|trong di a|de minh trong|thu trong|trong thu)\b"
)
_PLANT_NO = re.compile(
    r"^\s*(khong|ko|k\b|chua|de sau|chua muon|khong muon|khong can|no|thoi|de minh xem|xem da|de minh tinh)\b"
)


def detect_planting_answer(text: Optional[str]) -> Optional[str]:
    """Phân loại câu trả lời Có/Không cho câu hỏi 'Bạn có muốn trồng cây này không?'."""
    if not text:
        return None
    t = " ".join(str(text).lower().split())
    if _PLANT_YES.match(t):
        return "yes"
    if _PLANT_NO.match(t):
        return "no"
    return None


# ============================== HỖ TRỢ ==============================
def _clean_history(history: Optional[list], max_turns: int = 10) -> list:
    out = []
    for m in (history or [])[-max_turns * 2:]:
        role, content = m.get("role"), (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


def _fallback_reply(rec: Optional[dict], analysis: Optional[dict]) -> str:
    """Trả lời dự phòng khi LLM lỗi: vẫn hữu ích nhờ dữ liệu từ recommender."""
    kind = analysis["kind"] if analysis else "farm"
    note = "\n\n_(Trợ lý AI đang bận nên mình trả lời nhanh từ dữ liệu vườn. Bạn hỏi lại sau ít phút để nghe tư vấn kỹ hơn nhé!)_"

    if kind == "smalltalk":
        return ("Chào bạn, mình là **ABc_Nông** 🌱! Mình có thể giúp bạn:\n"
                "- Xem **tình trạng vườn** hiện tại\n- **Chọn cây** hợp với khu vườn và vùng của bạn\n"
                "- Xử lý **đất khô/úng, sâu bệnh**\n\nBạn muốn bắt đầu từ đâu?")
    if kind == "cold_chain":
        return ("Hiện mình chưa nhận được số liệu chuyến hàng nên chưa đánh giá được. "
                "Bạn xem trang **Chuỗi lạnh** hoặc cho mình biết loại hàng, nhiệt độ thùng và thời gian di chuyển nhé." + note)
    if analysis and analysis.get("vague"):
        return ("Để gợi ý đúng, mình cần biết thêm vài điều:\n"
                "1. **Mục tiêu**: trồng ăn trong nhà, bán kiếm lời hay làm cảnh?\n"
                "2. **Thời gian & vốn**: ít, vừa hay nhiều?\n"
                "3. **Vườn ở vùng nào** (Bắc / Trung / Tây Nguyên / Nam) và có nắng cả ngày không?")
    if rec and rec["recommendations"]:
        if rec.get("list_mode"):
            return "Đây là các cây hợp với điều kiện vườn hiện tại (xếp theo độ phù hợp):\n\n" + \
                   render_table(rec["recommendations"]) + note
        t, alts = rec.get("target"), rec.get("alternatives") or []
        if t and t.get("found_in_db") and not t["suitable"] and alts:
            names = " và ".join(f"**{a['crop_name']}** (giá trị {a['market_label'].lower()})" for a in alts)
            return (f"**{t['name']}** chưa hợp với vườn/vùng hiện tại ({'; '.join(t['reasons'])}). "
                    f"Bạn có thể cân nhắc {names} – hợp điều kiện hiện tại và có giá trị kinh tế gần tương đương." + note)
        top = rec["recommendations"][:3]
        bullets = "\n".join(
            f"- **{r['crop_name']}**" + (f" – phù hợp {r['score']}%" if r["score"] is not None else "") +
            f" (giá trị kinh tế: {r['market_label'].lower()})" for r in top)
        return "Theo dữ liệu vườn, các cây đang phù hợp nhất:\n" + bullets + note
    return "Mình đang gặp chút trục trặc kết nối 😅 Bạn thử lại sau ít phút giúp mình nhé!"


# ============================== HÀM CHÍNH CHO ENDPOINT ==============================
def chat_with_llm(
    user_message: str,
    sensor_data: Optional[dict] = None,
    history: Optional[list] = None,
    top_k: int = 3,
) -> dict:
    """
    Trả về {"reply": str, "recommendations": dict|None, "llm_ok": bool, "analysis": dict}
    history: [{"role": "user"|"assistant", "content": "..."}]
    """
    history = _clean_history(history)
    prev_user = [m["content"] for m in history if m["role"] == "user"]
    analysis = analyze_query(user_message)
    query = user_message

    # Ghép với câu trước khi người dùng hỏi nối tiếp ("còn gì nữa?") hoặc đang trả lời câu hỏi dẫn dắt của AI
    last_ai_asked = bool(history) and history[-1]["role"] == "assistant" and "?" in history[-1]["content"][-300:]
    if prev_user and (analysis["followup"] or (last_ai_asked and len(user_message) < 120 and analysis["kind"] != "cold_chain")):
        query = f"{prev_user[-1]}. {user_message}"
        analysis = analyze_query(query)

    rec = None
    if analysis["kind"] == "farm" and not analysis["vague"]:
        try:
            rec = recommend_crops(sensor_data, query, max(top_k, 5), get_keyword_extractor_if_enabled())
        except Exception:
            logger.exception("recommend_crops lỗi, tiếp tục không có gợi ý")

    system_prompt = f"{SYSTEM_PROMPT}\n\n{build_context(sensor_data, rec, analysis)}"

    try:
        reply, ok = _generate(system_prompt, history, user_message), True
        if not reply:
            raise RuntimeError("LLM trả về rỗng")
    except Exception:
        logger.exception("Gọi LLM thất bại (provider=%s, model=%s)", _provider(), _model_name("main"))
        reply, ok = _fallback_reply(rec, analysis), False

    return {"reply": reply, "recommendations": rec, "llm_ok": ok,
            "planting": detect_planting_answer(user_message),
            "analysis": {k: analysis[k] for k in ("kind", "vague", "needs", "regions")}}


def provider_status() -> tuple:
    """Trả về tên provider và model đang dùng để hiển thị lên web."""
    return _provider(), _model_name("main")