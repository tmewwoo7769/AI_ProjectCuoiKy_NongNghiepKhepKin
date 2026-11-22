from __future__ import annotations

from datetime import datetime

import numpy as np
from sklearn.metrics import accuracy_score
from sklearn.tree import DecisionTreeClassifier

from ..schemas import TinyMLRequest


FEATURES = ["soil_moisture", "air_temp", "air_humidity", "light_lux", "hour_of_day"]


def _label(soil: np.ndarray, temp: np.ndarray, humidity: np.ndarray) -> np.ndarray:
    condition_1 = (soil < 35) & ((humidity < 80) | (temp > 28))
    condition_2 = soil < 25
    return (condition_1 | condition_2).astype(int)


def _train_model() -> tuple[DecisionTreeClassifier, float]:
    rng = np.random.default_rng(42)
    sample_count = 5_000
    soil = rng.uniform(10, 90, sample_count)
    temp = rng.uniform(16, 42, sample_count)
    humidity = rng.uniform(35, 98, sample_count)
    light = rng.uniform(0, 80_000, sample_count)
    hour = rng.uniform(0, 24, sample_count)
    x = np.column_stack([soil, temp, humidity, light, hour])
    y = _label(soil, temp, humidity)

    model = DecisionTreeClassifier(max_depth=5, min_samples_leaf=5, random_state=42)
    model.fit(x, y)
    return model, float(accuracy_score(y, model.predict(x)))


MODEL, TRAIN_ACCURACY = _train_model()


def predict(request: TinyMLRequest) -> dict:
    hour = request.hour_of_day
    if hour is None:
        now = datetime.now()
        hour = now.hour + now.minute / 60

    values = np.asarray(
        [[request.soil_moisture, request.air_temp, request.air_humidity, request.light_lux, hour]],
        dtype=float,
    )
    decision = int(MODEL.predict(values)[0])
    confidence = float(MODEL.predict_proba(values)[0][decision])

    if request.soil_moisture < 25:
        reason = f"Độ ẩm đất {request.soil_moisture:.1f}% dưới 25%, cần tưới ngay."
    elif decision == 1:
        reason = (
            f"Độ ẩm đất {request.soil_moisture:.1f}% dưới ngưỡng 35% và điều kiện môi trường phù hợp; "
            "TinyML quyết định bật bơm."
        )
    else:
        reason = "Các chỉ số chưa thỏa điều kiện tưới; TinyML giữ máy bơm ở trạng thái tắt."

    return {
        "soilMoisture": round(request.soil_moisture, 1),
        "temperature": round(request.air_temp, 1),
        "humidity": round(request.air_humidity, 1),
        "light": round(request.light_lux, 1),
        "decision": "ON" if decision else "OFF",
        "reason": reason,
        "confidence": round(confidence, 3),
        "modelAccuracy": round(TRAIN_ACCURACY, 3),
        "modelVersion": "decision-tree-v1",
        "threshold": {"soilMoisture": 35, "temperature": 38, "humidity": 90, "light": 5000},
    }

