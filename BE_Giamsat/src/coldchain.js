import { Reading, Shipment } from "./models.js";
import {
  assessShipment,
  toColdChainHistory,
  toShipmentDto,
  toShipmentEvents,
  toTrackPoints,
} from "./transform.js";
import { config } from "./config.js";

/**
 * Lop truy van dung chung cho ca REST API lan luong MQTT, de hai duong khong
 * tinh toan lech nhau. Chuyen dang chay duoc cache ngan vi no it thay doi.
 */
const CACHE_MS = 30_000;
let cache = { at: 0, shipment: null };

export function invalidateShipmentCache() {
  cache = { at: 0, shipment: null };
}

/** Chuyen dang chay. Neu chua khai bao chuyen nao thi tra ve null. */
export async function getActiveShipment() {
  if (cache.shipment && Date.now() - cache.at < CACHE_MS) return cache.shipment;
  const shipment = await Shipment.findOne({ active: true }).sort({ createdAt: -1 }).lean();
  cache = { at: Date.now(), shipment };
  return shipment;
}

export async function findShipmentByCode(code) {
  return Shipment.findOne({ $or: [{ code }, { batchCode: code }] }).lean();
}

/**
 * Cac ban ghi thuoc mot chuyen: tinh tu luc khoi hanh den luc den (neu da den).
 * Sap xep moi nhat truoc.
 */
export async function getShipmentReadings(shipment, limit = 500) {
  if (!shipment) return [];

  const filter = { node_id: shipment.node_id };
  const range = {};
  if (shipment.departureTime) range.$gte = shipment.departureTime;
  if (shipment.arrivalTime) range.$lte = shipment.arrivalTime;
  if (Object.keys(range).length) filter.received_at = range;

  return Reading.find(filter).sort({ received_at: -1 }).limit(limit).lean();
}

/** Toan bo du lieu trang Chuoi lanh trong mot lan truy van. */
export async function buildColdChainSnapshot(shipment, limit = 500) {
  const target = shipment ?? (await getActiveShipment());
  if (!target) return null;

  const readings = await getShipmentReadings(target, limit);
  const dto = toShipmentDto(target, readings);

  return {
    shipment: dto,
    trackPoints: toTrackPoints(readings, target),
    events: toShipmentEvents(readings, target),
    history: toColdChainHistory(readings),
    readingCount: readings.length,
  };
}

/** Du lieu rut gon cho trang truy xuat cong khai /trace/:code. */
export async function buildPublicTrace(code) {
  const shipment = await findShipmentByCode(code);
  if (!shipment) return null;

  const snapshot = await buildColdChainSnapshot(shipment);
  const dto = snapshot.shipment;
  const temps = snapshot.history.map((point) => point.temp).filter((v) => typeof v === "number");
  const avgTemp = temps.length ? +(temps.reduce((sum, v) => sum + v, 0) / temps.length).toFixed(2) : 0;

  return {
    batchCode: shipment.batchCode,
    productName: shipment.productName,
    origin: shipment.origin,
    destination: shipment.destination,
    departureTime: dto.departureTime,
    avgTemp,
    maxTemp: dto.temperature.max,
    overTempCount: dto.temperature.overCount,
    impactCount: dto.vibration.impactCount,
    ...assessShipment(dto),
    events: snapshot.events,
    trackPoints: snapshot.trackPoints,
  };
}

/** Chuyen mac dinh de he thong co du lieu hien thi ngay tu lan chay dau. */
export async function ensureDefaultShipment() {
  const existing = await Shipment.countDocuments();
  if (existing > 0) return null;

  const shipment = await Shipment.create({
    code: "SHP-2024-0847",
    batchCode: "LO-BDH-241115-03",
    productName: "Rau cải ngọt hữu cơ",
    origin: "Nông trại Bình Dương Hòa, Đà Lạt",
    destination: "Siêu thị Sài Gòn Co.op, Q.7 TP.HCM",
    departureTime: new Date(Date.now() - 6 * 3600 * 1000),
    status: "Đang vận chuyển",
    node_id: config.nodes.cold,
    esp32Id: "ESP32-COLD-02",
    tempThreshold: 8,
    active: true,
  });

  invalidateShipmentCache();
  return shipment;
}
