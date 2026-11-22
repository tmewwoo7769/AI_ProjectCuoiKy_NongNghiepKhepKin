"""Lop du lieu cho dich vu AI.

Thay cho mysql_repository truoc day: khong tu giu co so du lieu rieng ma doc
thang tu backend Node (cong 4000) - noi dang nhan du lieu MQTT that tu hai nut
cam bien va luu vao MongoDB Atlas.

Ly do doi: neu dich vu AI giu mot ban sao du lieu rieng thi ARIMA se du bao
tren so lieu demo chu khong phai so do that tu BME680 va cam bien do am dat.
Mot nguon du lieu duy nhat cung bot mot thu phai cai va phai bat luc trinh dien.

Giu nguyen ten phuong thuc va dang du lieu tra ve nhu ban MySQL, nen main.py
va hai module ARIMA/LLM khong phai sua theo.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

NODE_API = os.getenv("NODE_API_URL", "http://127.0.0.1:4000").rstrip("/")
FARM_NODE = os.getenv("NODE_FARM_ID", "node1_vuon")
TIMEOUT = 15

# Nguong canh bao, giu trung voi cau hinh cua backend Node.
# Backend Node luu moc thoi gian theo UTC. Doi sang gio dia phuong truoc khi
# dua vao mo hinh, vi truc thoi gian tren bieu do va cau tra loi cua tro ly deu
# hien thang cho nguoi dung - de nguyen UTC thi moi moc lech 7 tieng.
TZ_OFFSET_HOURS = float(os.getenv("TZ_OFFSET_HOURS", "7"))

SOIL_MIN = float(os.getenv("SOIL_MOISTURE_THRESHOLD", "35"))
TEMP_MAX = float(os.getenv("AIR_TEMP_THRESHOLD", "38"))
HUMID_MAX = float(os.getenv("AIR_HUMID_THRESHOLD", "90"))


class NodeBackendRepository:
    """Doc du lieu cam bien tu backend Node, luu ket qua AI nguoc lai qua REST."""

    # ------------------------------------------------------------- tien ich

    @staticmethod
    def _get(path: str, **params) -> Any:
        response = requests.get(f"{NODE_API}{path}", params=params or None, timeout=TIMEOUT)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _post(path: str, payload: dict) -> Any:
        response = requests.post(f"{NODE_API}{path}", json=payload, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _flatten(record: dict) -> dict:
        """Ban ghi tho cua backend Node -> dang phang ma ARIMA va LLM dang dung."""
        env = record.get("env") or {}
        soil = record.get("soil") or {}
        light = record.get("light") or {}
        pump = record.get("pump") or {}
        return {
            "timestamp": record.get("received_at"),
            "device_id": record.get("node_id"),
            "soil_moisture": soil.get("moisture_pct"),
            "air_temp": env.get("temp_c"),
            "air_humidity": env.get("humid_pct"),
            "light_lux": light.get("lux"),
            "pump_state": 1 if pump.get("state") == "ON" else 0,
            "source": "device",
        }

    # ------------------------------------------------------------ trang thai

    def status(self) -> dict:
        try:
            health = self._get("/api/health")
            return {
                "connected": bool(health and health.get("ok")),
                "engine": "MongoDB Atlas qua backend Node",
                "url": NODE_API,
                "mongo": bool(health and health.get("mongo")),
                "mqtt": bool(health and health.get("mqtt")),
            }
        except requests.RequestException as exc:
            return {"connected": False, "engine": "MongoDB Atlas qua backend Node",
                    "url": NODE_API, "error": str(exc)}

    def has_device_data(self) -> bool:
        try:
            return self._get(f"/api/nodes/{FARM_NODE}/latest") is not None
        except requests.RequestException:
            return False

    def seed_demo_history(self) -> int:
        """Khong tao du lieu gia. Du lieu that do hai nut cam bien gui qua MQTT."""
        return 0

    def append_sensor(self, reading) -> int:
        """Duong ghi du lieu di qua MQTT chu khong qua dich vu AI."""
        raise NotImplementedError(
            "Du lieu cam bien di vao he thong qua MQTT roi duoc backend Node luu. "
            "Dich vu AI chi doc, khong ghi du lieu cam bien."
        )

    # -------------------------------------------------------- du lieu cam bien

    def latest_sensor(self) -> dict:
        sensor = self._get("/api/farm/sensor")
        if not sensor:
            raise ValueError("Node vuon chua gui du lieu nao")

        return {
            "timestamp": sensor.get("lastUpdated"),
            "device_id": FARM_NODE,
            "soil_moisture": sensor.get("soilMoisture") or 0.0,
            "air_temp": sensor.get("temperature") or 0.0,
            "air_humidity": sensor.get("humidity") or 0.0,
            "light_lux": sensor.get("lightLux") or 0.0,
            "pump_state": 1 if sensor.get("pumpStatus") == "ON" else 0,
            "control_mode": sensor.get("controlMode", "AUTO"),
            "source": "device",
        }

    def sensor_history(self, hours: int | None = None) -> pd.DataFrame:
        """Lich su cam bien, sap xep tu cu den moi - dang ma run_arima() mong doi."""
        records = self._get(f"/api/nodes/{FARM_NODE}/history", limit=2000) or []
        rows = [self._flatten(r) for r in records]
        data = pd.DataFrame(rows)
        if data.empty:
            return data

        data["timestamp"] = (
            pd.to_datetime(data["timestamp"], format="ISO8601", utc=True)
            .dt.tz_localize(None)
            + pd.Timedelta(hours=TZ_OFFSET_HOURS)
        )
        for column in ("soil_moisture", "air_temp", "air_humidity", "light_lux"):
            data[column] = pd.to_numeric(data[column], errors="coerce").astype(float)

        if hours is not None:
            moc = data["timestamp"].max() - timedelta(hours=hours)
            data = data[data["timestamp"] >= moc]

        return data.sort_values("timestamp").reset_index(drop=True)

    # ----------------------------------------------------------- dieu khien bom

    def control_state(self) -> dict:
        sensor = self._get("/api/farm/sensor") or {}
        return {
            "control_mode": sensor.get("controlMode", "AUTO"),
            "pump_status": sensor.get("pumpStatus", "OFF"),
        }

    def set_control_mode(self, mode: str, auto_decision: str, soil_moisture: float, device_id: str) -> dict:
        result = self._post("/api/farm/mode", {"mode": mode})
        return {
            "success": bool(result.get("success")),
            "message": result.get("message", ""),
            "control_mode": mode,
            "pump_status": (result.get("sensor") or {}).get("pumpStatus", "OFF"),
        }

    def sync_auto_pump(self, decision: str, soil_moisture: float, device_id: str) -> dict:
        """Cay quyet dinh chay tren chinh vi dieu khien, may chu khong dong bo nguoc xuong."""
        return self.control_state()

    def manual_pump(self, action: str, soil_moisture: float, device_id: str) -> dict:
        result = self._post("/api/farm/pump", {"action": action})
        return {
            "success": bool(result.get("success")),
            "message": result.get("message", ""),
            "pump_status": (result.get("sensor") or {}).get("pumpStatus", action),
        }

    def pump_events(self, limit: int = 20) -> list[dict]:
        events = self._get("/api/farm/pump-events", limit=limit) or []
        return [
            {
                "time": e.get("time"),
                "action": e.get("action"),
                "trigger": e.get("trigger"),
                "soil_moisture": e.get("soilMoisture"),
            }
            for e in events
        ]

    # -------------------------------------------------------------- canh bao

    def today_alerts(self, limit: int = 50) -> list[dict]:
        """Canh bao suy tu du lieu 24 gio gan nhat thay vi doc tu bang rieng.

        Backend Node khong luu bang canh bao: nguong co the thay doi, ma suy lai
        tu so do goc thi luon nhat quan voi nguong dang dat, khong so hai nguon
        noi khac nhau.
        """
        data = self.sensor_history(hours=24)
        if data.empty:
            return []

        alerts: list[dict] = []
        for _, row in data.iterrows():
            khi = row["timestamp"].isoformat()
            if pd.notna(row["soil_moisture"]) and row["soil_moisture"] < SOIL_MIN:
                alerts.append({"time": khi, "level": "warning", "metric": "soil_moisture",
                               "value": round(float(row["soil_moisture"]), 1),
                               "message": f"Độ ẩm đất {row['soil_moisture']:.1f}% dưới ngưỡng {SOIL_MIN:.0f}%"})
            if pd.notna(row["air_temp"]) and row["air_temp"] > TEMP_MAX:
                alerts.append({"time": khi, "level": "danger", "metric": "air_temp",
                               "value": round(float(row["air_temp"]), 1),
                               "message": f"Nhiệt độ {row['air_temp']:.1f}°C vượt ngưỡng {TEMP_MAX:.0f}°C"})
            if pd.notna(row["air_humidity"]) and row["air_humidity"] > HUMID_MAX:
                alerts.append({"time": khi, "level": "warning", "metric": "air_humidity",
                               "value": round(float(row["air_humidity"]), 1),
                               "message": f"Độ ẩm không khí {row['air_humidity']:.1f}% vượt ngưỡng {HUMID_MAX:.0f}%"})

        return alerts[-limit:]

    # ------------------------------------------------------ luu ket qua AI

    def save_arima_run(self, result: dict, metric: str, history_range: str, horizon_hours: int) -> str | None:
        try:
            saved = self._post("/api/ai/arima-runs", {
                "metric": metric,
                "historyRange": history_range,
                "horizonHours": horizon_hours,
                "result": result,
            })
            return saved.get("id")
        except requests.RequestException:
            return None   # khong luu duoc thi van tra ket qua cho nguoi dung

    def latest_arima_summary(self) -> dict | None:
        try:
            return self._get("/api/ai/arima-runs/latest")
        except requests.RequestException:
            return None

    def save_llm_interaction(self, result: dict, sensor: dict) -> str | None:
        try:
            saved = self._post("/api/ai/llm-history", {"result": result, "sensor": sensor})
            return saved.get("id")
        except requests.RequestException:
            return None

    def llm_history(self, limit: int = 50) -> list[dict]:
        try:
            return self._get("/api/ai/llm-history", limit=limit) or []
        except requests.RequestException:
            return []


repository = NodeBackendRepository()
