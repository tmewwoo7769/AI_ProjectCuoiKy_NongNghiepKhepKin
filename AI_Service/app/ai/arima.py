from __future__ import annotations

from time import perf_counter

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA


METRICS = {
    "soilMoisture": ("soil_moisture", "Độ ẩm đất", "%", 35.0),
    "temperature": ("air_temp", "Nhiệt độ không khí", "°C", 38.0),
    "humidity": ("air_humidity", "Độ ẩm không khí", "%", 90.0),
}


def _fit_best(series: pd.Series):
    candidates = [(1, 1, 1), (2, 1, 1), (1, 1, 2), (2, 1, 2)]
    best = None
    for order in candidates:
        try:
            fitted = ARIMA(series, order=order).fit()
            if best is None or fitted.aic < best.aic:
                best = fitted
        except (ValueError, np.linalg.LinAlgError):
            continue
    if best is None:
        raise ValueError("Không thể huấn luyện ARIMA với dữ liệu hiện tại")
    return best


# Do phan giai gop du lieu, uu tien theo gio. Cang xuong duoi cang min hon
# nhung tam du bao cang ngan lai.
RESOLUTIONS = [("1h", 60), ("30min", 30), ("15min", 15), ("10min", 10), ("5min", 5)]
MIN_POINTS = 24


def _pick_resolution(prepared: pd.DataFrame, column: str):
    """Chon do phan giai tho nhat ma van du 24 diem de huan luyen.

    Cam bien gui vai giay mot ban tin, nhung gop theo gio thi mot ngay chi ra 24
    diem - vua du nguong toi thieu. Trong giai doan dau khi he thong moi chay
    duoc vai gio, gop theo gio se khong du va ARIMA khong chay duoc. Khi do ha
    xuong muc min hon de van co du bao, doi lai tam nhin ngan hon; do phan giai
    thuc te duoc tra ve trong ket qua de giao dien noi ro cho nguoi xem.
    """
    for label, minutes in RESOLUTIONS:
        series = (
            prepared.set_index("timestamp")[column]
            .resample(label)
            .mean()
            .interpolate(method="time")
            .dropna()
        )
        if len(series) >= MIN_POINTS:
            return series, label, minutes

    raise ValueError(
        "Chưa đủ dữ liệu để chạy ARIMA. Cần ít nhất 24 điểm sau khi gộp; "
        "hệ thống mới thu thập được quá ít, hãy để hai nút chạy thêm rồi thử lại."
    )


def run_arima(data: pd.DataFrame, metric: str, horizon_hours: int = 24) -> dict:
    column, label, unit, threshold = METRICS[metric]
    if data.empty:
        raise ValueError("Chưa có dữ liệu cảm biến nào để dự báo")

    prepared = data[["timestamp", column]].copy()
    prepared["timestamp"] = pd.to_datetime(prepared["timestamp"])
    series, resolution, step_minutes = _pick_resolution(prepared, column)

    # Khong du bao xa hon mot nua so diem dang co: ngoai khoang do khoang tin cay
    # phinh ra rat nhanh va con so mat y nghia.
    wanted_steps = max(1, round(horizon_hours * 60 / step_minutes))
    steps = min(wanted_steps, max(4, len(series) // 2))
    horizon_minutes = steps * step_minutes

    started = perf_counter()
    fitted = _fit_best(series)
    train_time = perf_counter() - started
    prediction = fitted.get_forecast(steps=steps)
    mean = prediction.predicted_mean
    interval = prediction.conf_int(alpha=0.05)

    residuals = np.asarray(fitted.resid, dtype=float)
    mae = float(np.mean(np.abs(residuals)))
    rmse = float(np.sqrt(np.mean(np.square(residuals))))
    order = fitted.model.order

    historical = [
        {"time": ts.isoformat(), "value": round(float(value), 2), "type": "historical"}
        for ts, value in series.tail(48).items()
    ]
    forecast = []
    for index, (ts, value) in enumerate(mean.items()):
        lower = float(interval.iloc[index, 0])
        upper = float(interval.iloc[index, 1])
        forecast.append(
            {
                "time": ts.isoformat(),
                "value": round(float(value), 2),
                "lower": round(lower, 2),
                "upper": round(upper, 2),
                "type": "forecast",
            }
        )

    tam_nhin = (f"{horizon_minutes / 60:.0f} giờ tới" if horizon_minutes >= 120
                else f"{horizon_minutes} phút tới")

    values = np.asarray(mean, dtype=float)
    if metric == "soilMoisture":
        conclusion = (
            "Độ ẩm đất được dự báo xuống dưới ngưỡng 35%; nên duy trì chế độ tưới tự động và theo dõi cảm biến."
            if float(values.min()) < threshold
            else f"Độ ẩm đất dự kiến vẫn trên ngưỡng 35% trong {tam_nhin}."
        )
    elif metric == "temperature":
        conclusion = (
            "Nhiệt độ có khả năng vượt ngưỡng 38°C; nên kiểm tra che nắng và hệ thống tưới làm mát."
            if float(values.max()) > threshold
            else f"Nhiệt độ dự kiến vẫn trong ngưỡng an toàn 38°C trong {tam_nhin}."
        )
    else:
        conclusion = (
            "Độ ẩm không khí có khả năng vượt 90%; cần chú ý thông gió và nguy cơ nấm bệnh."
            if float(values.max()) > threshold
            else f"Độ ẩm không khí dự kiến chưa vượt ngưỡng cảnh báo 90% trong {tam_nhin}."
        )

    return {
        "metric": label,
        "unit": unit,
        "threshold": threshold,
        "model": {
            "params": str(order),
            "dataPoints": int(len(series)),
            "trainTime": f"{train_time:.2f}s",
            "mae": round(mae, 2),
            "rmse": round(rmse, 2),
        },
        "resolution": resolution,
        "horizonMinutes": horizon_minutes,
        "horizonLabel": tam_nhin,
        "historical": historical,
        "forecast": forecast,
        "conclusion": conclusion,
        "generatedAt": pd.Timestamp.now().isoformat(),
    }
