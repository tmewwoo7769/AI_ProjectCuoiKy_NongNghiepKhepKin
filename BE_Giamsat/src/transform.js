import { config } from "./config.js";
import { state } from "./state.js";

/**
 * Chi so rung lac tuc thoi cua mot ban ghi: do lech cua do lon vector gia toc so voi 1g.
 * Xe dung yen -> |a| ~ 1g -> gia tri ~ 0. Phanh gap / xoc -> gia tri tang.
 * Day la xap xi tu du lieu lay mau thap, khong phai RMS pho rung day du.
 */
export function vibrationOf(reading) {
  const a = reading?.imu?.accel_g;
  if (!a || [a.x, a.y, a.z].some((v) => typeof v !== "number")) return null;
  const magnitude = Math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z);
  return +Math.abs(magnitude - 1).toFixed(4);
}

/** RMS cua chi so rung lac tren mot tap ban ghi. */
export function vibrationRms(readings) {
  const values = readings
    .map((r) => r.derived?.vibration_g ?? vibrationOf(r))
    .filter((v) => typeof v === "number");
  if (!values.length) return 0;
  const meanSquare = values.reduce((sum, v) => sum + v * v, 0) / values.length;
  return +Math.sqrt(meanSquare).toFixed(3);
}

function round(value, digits = 1) {
  return typeof value === "number" ? +value.toFixed(digits) : null;
}

function iso(value) {
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "string") return value;
  return new Date().toISOString();
}

// ---------------------------------------------------------------- Node 1: vuon

/** Ban ghi Node 1 -> DTO FarmSensor ma frontend dang dung. */
export function toFarmSensor(reading, nodeId) {
  return {
    temperature: round(reading?.env?.temp_c),
    humidity: round(reading?.env?.humid_pct),
    soilMoisture: round(reading?.soil?.moisture_pct),
    lightLux: Math.round(reading?.light?.lux ?? 0),
    pressure: round(reading?.env?.pressure_hpa, 2),
    gasKohm: round(reading?.env?.gas_kohm, 2),
    pumpStatus: reading?.pump?.state === "ON" ? "ON" : "OFF",
    controlMode: reading?.pump?.mode === "MANUAL" ? "MANUAL" : "AUTO",
    connectionQuality: state.connectionQuality(nodeId),
    lastUpdated: iso(reading?.received_at ?? reading?.timestamp),
    esp32Status: state.isOnline(nodeId) ? "Online" : "Offline",
  };
}

/**
 * Phu trang thai mong muon len DTO khi backend vua gui lenh ma node chua bao lai.
 *
 * Dung chung cho ca duong REST lan duong MQTT: neu chi phu o mot duong thi trong
 * luc cho xac nhan, ban tin dinh ky cua node se keo giao dien ve trang thai cu
 * roi lai nhay sang trang thai moi - nhin nhu bi loi.
 */
export function withPendingCommand(sensor, nodeId) {
  const pending = state.pendingCommands(nodeId);
  if (!Object.keys(pending).length) return { ...sensor, pending: false };

  const next = { ...sensor, pending: true, pendingCommands: pending };
  if (pending.pump) next.pumpStatus = pending.pump.action;
  if (pending.mode) next.controlMode = pending.mode.mode;
  return next;
}

/** Ban ghi Node 1 -> DTO TinyMLDecision. Quyet dinh do chinh vi dieu khien gui len. */
export function toTinyMLDecision(reading) {
  const thresholds = config.thresholds;
  const soilMoisture = round(reading?.soil?.moisture_pct) ?? 0;
  const decision = reading?.tinyml?.decision === "ON" ? "ON" : "OFF";

  const fallbackReason = decision === "ON"
    ? `Độ ẩm đất ${soilMoisture}% thấp hơn ngưỡng ${thresholds.soilMoisture}%. TinyML quyết định bật bơm để tưới.`
    : `Độ ẩm đất ${soilMoisture}% đạt ngưỡng ${thresholds.soilMoisture}%. TinyML quyết định tắt bơm.`;

  return {
    soilMoisture,
    temperature: round(reading?.env?.temp_c) ?? 0,
    humidity: round(reading?.env?.humid_pct) ?? 0,
    light: Math.round(reading?.light?.lux ?? 0),
    decision,
    reason: reading?.tinyml?.reason || fallbackReason,
    threshold: {
      soilMoisture: thresholds.soilMoisture,
      temperature: thresholds.airTemp,
      humidity: thresholds.airHumidity,
      light: thresholds.light,
    },
  };
}

/**
 * Nhom cac ban ghi tho thanh chuoi thoi gian cho bieu do trang Vuon.
 * Bon chuoi luon cung do dai va cung thu tu vi frontend ghep chung theo chi so;
 * gia tri thieu de null de bieu do ngat quang thay vi noi sai diem.
 */
export function toFarmHistory(readings) {
  const ordered = [...readings].sort((a, b) => a.received_at - b.received_at);
  const pick = (fn) => ordered.map((r) => ({ time: iso(r.received_at), value: fn(r) }));

  return {
    temperature: pick((r) => round(r.env?.temp_c)),
    humidity: pick((r) => round(r.env?.humid_pct)),
    soilMoisture: pick((r) => round(r.soil?.moisture_pct)),
    light: pick((r) => (typeof r.light?.lux === "number" ? Math.round(r.light.lux) : null)),
  };
}

// -------------------------------------------------------- Node 2: chuoi lanh

function lastWithFix(readings) {
  return readings.find((r) => r.gps?.fix && typeof r.gps.lat === "number") ?? null;
}

/** Toa do -> chuoi hien thi. He thong khong goi dich vu geocoding ben ngoai. */
function describeLocation(reading) {
  const gps = reading?.gps;
  if (!gps?.fix || typeof gps.lat !== "number" || typeof gps.lon !== "number") {
    return "Chưa có định vị GPS";
  }
  return `${gps.lat.toFixed(5)}, ${gps.lon.toFixed(5)}`;
}

function bruiseRisk(impactCount, maxVibration) {
  if (impactCount >= 10 || maxVibration >= 1.5) return "Cao";
  if (impactCount >= 3 || maxVibration >= 0.8) return "Trung bình";
  return "Thấp";
}

/**
 * Uoc luong so phut vuot nguong: moi ban ghi vuot nguong dai dien cho mot chu ky lay mau,
 * chu ky lay tu khoang cach trung vi giua cac ban ghi thuc te.
 */
function estimateMinutes(subset, allReadings) {
  if (!subset.length || allReadings.length < 2) return 0;
  const stamps = allReadings.map((r) => new Date(r.received_at).getTime()).sort((a, b) => a - b);
  const gaps = [];
  for (let i = 1; i < stamps.length; i += 1) gaps.push(stamps[i] - stamps[i - 1]);
  gaps.sort((a, b) => a - b);
  const medianGap = gaps[Math.floor(gaps.length / 2)] || 0;
  return +((subset.length * medianGap) / 60000).toFixed(1);
}

/**
 * Gop metadata lo hang + du lieu cam bien thanh DTO Shipment cua frontend.
 * `readings` sap xep moi nhat truoc.
 */
export function toShipmentDto(shipment, readings) {
  const latest = readings[0] ?? null;
  const temps = readings.map((r) => r.env?.temp_c).filter((v) => typeof v === "number");
  const vibrations = readings
    .map((r) => r.derived?.vibration_g ?? vibrationOf(r))
    .filter((v) => typeof v === "number");

  const threshold = shipment.tempThreshold ?? 8;
  const overReadings = readings.filter((r) => typeof r.env?.temp_c === "number" && r.env.temp_c > threshold);
  const impactCount = vibrations.filter((v) => v > config.thresholds.vibrationImpactG).length;
  const maxVibration = vibrations.length ? Math.max(...vibrations) : 0;
  const positionSource = lastWithFix(readings);

  return {
    id: shipment.code,
    batchCode: shipment.batchCode,
    productName: shipment.productName,
    origin: shipment.origin,
    destination: shipment.destination,
    departureTime: iso(shipment.departureTime),
    status: shipment.status,
    esp32Id: shipment.esp32Id || shipment.node_id,
    temperature: {
      current: round(latest?.env?.temp_c, 2) ?? 0,
      min: temps.length ? +Math.min(...temps).toFixed(2) : 0,
      max: temps.length ? +Math.max(...temps).toFixed(2) : 0,
      threshold,
      overCount: overReadings.length,
      totalOverTime: estimateMinutes(overReadings, readings),
    },
    vibration: {
      current: round(latest?.derived?.vibration_g ?? vibrationOf(latest), 3) ?? 0,
      max: +maxVibration.toFixed(3),
      rms: vibrationRms(readings),
      impactCount,
      bruiseRisk: bruiseRisk(impactCount, maxVibration),
    },
    location: {
      lat: positionSource?.gps?.lat ?? null,
      lng: positionSource?.gps?.lon ?? null,
      address: describeLocation(positionSource),
      fix: Boolean(positionSource),
      satellites: positionSource?.gps?.satellites ?? null,
    },
  };
}

/** Cac ban ghi co dinh vi -> danh sach diem tren ban do hanh trinh. */
export function toTrackPoints(readings, shipment, maxPoints = 60) {
  const withFix = readings
    .filter((r) => r.gps?.fix && typeof r.gps.lat === "number" && typeof r.gps.lon === "number")
    .sort((a, b) => a.received_at - b.received_at);

  const step = Math.max(1, Math.ceil(withFix.length / maxPoints));
  const sampled = withFix.filter((_, i) => i % step === 0 || i === withFix.length - 1);
  const threshold = shipment?.tempThreshold ?? 8;

  return sampled.map((r, index) => {
    const rms = r.derived?.vibration_g ?? vibrationOf(r) ?? 0;
    const overTemp = typeof r.env?.temp_c === "number" && r.env.temp_c > threshold;
    const shaken = rms > config.thresholds.vibrationImpactG;

    const clock = new Date(r.received_at).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
    let label = `Vị trí ${clock}`;
    if (index === 0) label = "Điểm xuất phát";
    else if (index === sampled.length - 1) label = "Vị trí hiện tại";
    if (shaken) label = "Rung lắc mạnh";
    else if (overTemp) label = "Vượt ngưỡng nhiệt độ";

    return {
      time: iso(r.received_at),
      lat: r.gps.lat,
      lng: r.gps.lon,
      temp: round(r.env?.temp_c, 2) ?? 0,
      rms: +rms.toFixed(3),
      status: shaken || overTemp ? "warning" : "normal",
      label,
    };
  });
}

/** Cac moc dang chu y trong hanh trinh: xuat phat, vuot nguong, rung manh, vi tri hien tai. */
export function toShipmentEvents(readings, shipment, maxEvents = 20) {
  const ordered = [...readings].sort((a, b) => a.received_at - b.received_at);
  if (!ordered.length) return [];

  const threshold = shipment?.tempThreshold ?? 8;
  const events = [];
  const first = ordered[0];

  events.push({
    time: iso(shipment?.departureTime ?? first.received_at),
    location: describeLocation(first),
    temp: round(first.env?.temp_c, 2) ?? 0,
    rms: +(first.derived?.vibration_g ?? vibrationOf(first) ?? 0).toFixed(3),
    description: `Bắt đầu ghi nhận hành trình lô ${shipment?.batchCode ?? ""}`.trim(),
    type: "normal",
  });

  let wasOverTemp = false;
  for (const r of ordered) {
    const rms = r.derived?.vibration_g ?? vibrationOf(r) ?? 0;
    const temp = r.env?.temp_c;
    const overTemp = typeof temp === "number" && temp > threshold;

    if (rms > config.thresholds.vibrationImpactG) {
      events.push({
        time: iso(r.received_at),
        location: describeLocation(r),
        temp: round(temp, 2) ?? 0,
        rms: +rms.toFixed(3),
        description: `Phát hiện rung lắc mạnh (${rms.toFixed(2)} g) – nguy cơ dập nát nông sản`,
        type: "warning",
      });
    }

    // Chi ghi mot su kien khi bat dau vuot nguong, tranh spam moi ban tin.
    if (overTemp && !wasOverTemp) {
      events.push({
        time: iso(r.received_at),
        location: describeLocation(r),
        temp: round(temp, 2) ?? 0,
        rms: +rms.toFixed(3),
        description: `Nhiệt độ ${temp.toFixed(1)}°C vượt ngưỡng bảo quản ${threshold}°C`,
        type: "warning",
      });
    }
    wasOverTemp = overTemp;
  }

  const last = ordered[ordered.length - 1];
  events.push({
    time: iso(last.received_at),
    location: describeLocation(last),
    temp: round(last.env?.temp_c, 2) ?? 0,
    rms: +(last.derived?.vibration_g ?? vibrationOf(last) ?? 0).toFixed(3),
    description: "Bản tin mới nhất từ node vận chuyển",
    type: "normal",
  });

  return events.slice(-maxEvents);
}

/** Chuoi nhiet do + rung lac cho hai bieu do trang Chuoi lanh. */
export function toColdChainHistory(readings) {
  return [...readings]
    .sort((a, b) => a.received_at - b.received_at)
    .map((r) => ({
      time: iso(r.received_at),
      temp: round(r.env?.temp_c, 2) ?? 0,
      rms: +(r.derived?.vibration_g ?? vibrationOf(r) ?? 0).toFixed(3),
    }));
}

/** Danh gia tong ket cho trang truy xuat cong khai. */
export function assessShipment(dto) {
  const overCount = dto.temperature.overCount;
  const impacts = dto.vibration.impactCount;

  if (overCount > 10 || impacts > 15) {
    return { assessment: "fail", assessmentLabel: "Không đạt điều kiện bảo quản" };
  }
  if (overCount > 0 || impacts > 5) {
    return { assessment: "warn", assessmentLabel: "Có cảnh báo trong hành trình" };
  }
  return { assessment: "pass", assessmentLabel: "Đạt điều kiện bảo quản" };
}
