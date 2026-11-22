from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SensorReading(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)
    soil_moisture: float = Field(ge=0, le=100)
    air_temp: float = Field(ge=-10, le=60)
    air_humidity: float = Field(ge=0, le=100)
    light_lux: float = Field(ge=0, le=120_000)
    pump_state: int = Field(default=0, ge=0, le=1)
    device_id: str = "ESP32-FARM-01"
    source: Literal["device", "test"] = "device"


class TinyMLRequest(BaseModel):
    soil_moisture: float = Field(ge=0, le=100)
    air_temp: float = Field(ge=-10, le=60)
    air_humidity: float = Field(ge=0, le=100)
    light_lux: float = Field(ge=0, le=120_000)
    hour_of_day: float | None = Field(default=None, ge=0, lt=24)


class ArimaRequest(BaseModel):
    metric: Literal["soilMoisture", "temperature", "humidity"] = "temperature"
    history_range: Literal["24h", "7d", "30d"] = "30d"
    horizon_hours: int = Field(default=24, ge=1, le=72)


class LlmRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


class ControlModeRequest(BaseModel):
    mode: Literal["AUTO", "MANUAL"]


class PumpControlRequest(BaseModel):
    action: Literal["ON", "OFF"]
