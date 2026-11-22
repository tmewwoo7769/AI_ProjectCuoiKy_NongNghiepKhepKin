from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from .ai import arima, llm, tinyml
from .node_repository import repository
from .schemas import (
    ArimaRequest,
    ControlModeRequest,
    LlmRequest,
    PumpControlRequest,
    SensorReading,
    TinyMLRequest,
)

# --- THÊM DÒNG NÀY ĐỂ IMPORT ROUTER AI MỚI ---
from .ai.ai_router import router as ai_router

@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.seed_demo_history()
    yield


app = FastAPI(title="Hệ thống AI nông nghiệp", version="1.1.0", lifespan=lifespan)

# --- ĐƯA ROUTER AI VÀO ỨNG DỤNG ---
app.include_router(ai_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    provider, model = llm.provider_status()
    database = repository.status()
    return {
        "status": "ready",
        "models": {"tinyml": "ready", "arima": "ready", "llm": provider},
        "llmModel": model,
        "dataSource": "device" if repository.has_device_data() else "demo",
        "database": database,
    }


@app.post("/api/sensors/farm")
def receive_sensor(reading: SensorReading) -> dict:
    reading_id = repository.append_sensor(reading)
    return {
        "success": True,
        "message": "Đã lưu dữ liệu cảm biến",
        "readingId": reading_id,
        "timestamp": reading.timestamp,
    }


@app.get("/api/farm/latest")
def farm_latest() -> dict:
    row = repository.latest_sensor()
    request = TinyMLRequest(
        soil_moisture=row["soil_moisture"],
        air_temp=row["air_temp"],
        air_humidity=row["air_humidity"],
        light_lux=row["light_lux"],
    )
    decision = tinyml.predict(request)
    control = repository.sync_auto_pump(
        decision["decision"],
        float(row["soil_moisture"]),
        str(row["device_id"]),
    )
    return {
        "temperature": round(float(row["air_temp"]), 1),
        "humidity": round(float(row["air_humidity"]), 1),
        "soilMoisture": round(float(row["soil_moisture"]), 1),
        "lightLux": round(float(row["light_lux"]), 1),
        "pumpStatus": control["pump_status"],
        "controlMode": control["control_mode"],
        "connectionQuality": 100 if row.get("source") == "device" else 92,
        "lastUpdated": str(row["timestamp"]),
        "esp32Status": "Online" if row.get("source") == "device" else "Offline",
        "dataSource": row.get("source", "demo"),
    }


@app.get("/api/farm/history")
def farm_history(hours: int = Query(default=24, ge=1, le=24 * 90)) -> dict:
    data = repository.sensor_history(hours)

    def points(column: str) -> list[dict]:
        return [
            {"time": row.timestamp.isoformat(), "value": round(float(getattr(row, column)), 2)}
            for row in data.itertuples()
        ]

    return {
        "temperature": points("air_temp"),
        "humidity": points("air_humidity"),
        "soilMoisture": points("soil_moisture"),
        "light": points("light_lux"),
    }


@app.get("/api/farm/pump-events")
def farm_pump_events(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    return repository.pump_events(limit)


@app.post("/api/farm/control-mode")
def farm_control_mode(request: ControlModeRequest) -> dict:
    row = repository.latest_sensor()
    decision = tinyml.predict(
        TinyMLRequest(
            soil_moisture=row["soil_moisture"],
            air_temp=row["air_temp"],
            air_humidity=row["air_humidity"],
            light_lux=row["light_lux"],
        )
    )
    state = repository.set_control_mode(
        request.mode,
        decision["decision"],
        float(row["soil_moisture"]),
        str(row["device_id"]),
    )
    sensor = farm_latest()
    return {
        "success": True,
        "message": (
            "Đã chuyển sang chế độ điều khiển thủ công."
            if request.mode == "MANUAL"
            else "Đã chuyển sang chế độ tự động theo quyết định TinyML."
        ),
        "sensor": sensor,
        "updatedAt": state["updated_at"],
    }


@app.post("/api/farm/pump")
def farm_pump_control(request: PumpControlRequest) -> dict:
    row = repository.latest_sensor()
    try:
        repository.manual_pump(
            request.action,
            float(row["soil_moisture"]),
            str(row["device_id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        "message": f"Máy bơm đã được {'bật' if request.action == 'ON' else 'tắt'}.",
    }


@app.post("/api/ai/tinyml/decision")
def tinyml_decision(request: TinyMLRequest) -> dict:
    return tinyml.predict(request)


@app.get("/api/ai/tinyml/latest")
def tinyml_latest() -> dict:
    row = repository.latest_sensor()
    return tinyml.predict(
        TinyMLRequest(
            soil_moisture=row["soil_moisture"],
            air_temp=row["air_temp"],
            air_humidity=row["air_humidity"],
            light_lux=row["light_lux"],
        )
    )


@app.post("/api/ai/arima")
@app.post("/api/ai/forecast", include_in_schema=False)
def arima_prediction(request: ArimaRequest) -> dict:
    hours = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}[request.history_range]
    data = repository.sensor_history(hours)
    try:
        result = arima.run_arima(data, request.metric, request.horizon_hours)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["dataSource"] = "device" if repository.has_device_data() else "demo"
    result["runId"] = repository.save_arima_run(
        result,
        request.metric,
        request.history_range,
        request.horizon_hours,
    )
    return result


@app.get("/api/ai/llm/history")
def llm_history(limit: int = Query(default=50, ge=1, le=100)) -> list[dict]:
    return repository.llm_history(limit)

# LƯU Ý: Đã xóa API /api/ai/llm cũ ở đây vì file ai_router.py đã đảm nhận việc này rồi!