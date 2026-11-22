# -*- coding: utf-8 -*-
"""
ai_router.py – 2 endpoint FastAPI:
    POST /api/ai/recommend   → gợi ý cây (kết hợp user_query + cảm biến)
    POST /api/ai/llm         → chatbot tự nhiên, tự gọi recommender khi cần

Dùng trong app chính:
    from ai_router import router as ai_router
    app.include_router(ai_router)
"""
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ai.llm import chat_with_llm, get_keyword_extractor_if_enabled
from app.ai.recommender import recommend_crops

from app.node_repository import repository

router = APIRouter(prefix="/api/ai", tags=["AI"])


# ----------------------------- Schema -----------------------------
class SensorData(BaseModel):
    soil_moisture: Optional[float] = Field(None, description="Độ ẩm đất (%)")
    temperature: Optional[float] = Field(None, description="Nhiệt độ (°C)")
    light: Optional[float] = Field(None, description="Cường độ ánh sáng")


class RecommendRequest(BaseModel):
    sensor_data: Optional[SensorData] = None   # bỏ trống → lấy từ DB (get_latest_sensor_data)
    user_query: Optional[str] = None           # vd "cây chịu hạn cho người mới"
    top_k: int = Field(3, ge=1, le=10)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    # Chấp nhận cả chữ question (từ web cũ gửi lên) và message
    question: Optional[str] = Field(None, max_length=1000)
    message: Optional[str] = Field(None, max_length=1000)
    sensor_data: Optional[SensorData] = None
    history: List[ChatMessage] = []
    top_k: int = Field(3, ge=1, le=10)


# ----------------------------- Lấy dữ liệu cảm biến -----------------------------
def get_latest_sensor_data() -> Optional[dict]:
    try:
        row = repository.latest_sensor()
        return {
            "soil_moisture": float(row["soil_moisture"]),
            "temperature": float(row["air_temp"]),
            "light": float(row["light_lux"]),
        }
    except Exception:
        return None


def _resolve_sensor(sensor: Optional[SensorData]) -> Optional[dict]:
    if sensor is not None:
        data = sensor.model_dump(exclude_none=True)   # pydantic v1: dùng sensor.dict(exclude_none=True)
        if data:
            return data
    return get_latest_sensor_data()


# ----------------------------- Endpoint -----------------------------
# Dùng `def` (không phải `async def`) vì SDK của LLM/pandas là blocking:
# FastAPI sẽ tự chạy trong threadpool nên không chặn event loop.
@router.post("/recommend")
def api_recommend(req: RecommendRequest):
    try:
        return recommend_crops(
            sensor_data=_resolve_sensor(req.sensor_data),
            user_query=req.user_query,
            top_k=req.top_k,
            keyword_extractor=get_keyword_extractor_if_enabled(),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Không tìm thấy file crop_data.csv")
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/llm")
def api_llm(req: LLMRequest):
    user_text = req.message or req.question # Lấy cái nào có dữ liệu
    if not user_text:
        raise HTTPException(status_code=400, detail="Thiếu nội dung câu hỏi")
        
    return chat_with_llm(
        user_message=user_text,
        sensor_data=_resolve_sensor(req.sensor_data),
        history=[m.model_dump() for m in req.history],
        top_k=req.top_k,
    )
