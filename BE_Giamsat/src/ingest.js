import mqtt from "mqtt";

import { config } from "./config.js";
import { PumpEvent, Reading } from "./models.js";
import { state } from "./state.js";
import { buildColdChainSnapshot, getActiveShipment } from "./coldchain.js";
import { toFarmSensor, toTinyMLDecision, vibrationOf, withPendingCommand } from "./transform.js";

let client = null;
let io = null;

/** Trang thai bom lan cuoi thay o moi node, de phat hien thoi diem bom doi trang thai. */
const lastPumpState = new Map();

const dataTopic = () => `${config.mqtt.topicPrefix}/+/data`;
const commandTopic = (nodeId) => `${config.mqtt.topicPrefix}/${nodeId}/cmd`;

/** Chuoi da co "Z" hoac "+07:00" o cuoi thi da xac dinh duoc moc thoi gian. */
const HAS_TIMEZONE = /(?:Z|[+-]\d{2}:?\d{2})$/;

/**
 * Doi chuoi thoi gian cua node thanh Date.
 *
 * Node doc gio tu RTC DS3231 (Node 2) hoac NTP (Node 1) roi gui dang
 * "2026-08-30T12:51:52" - la gio Viet Nam nhung khong ghi mui gio. Neu de
 * JavaScript tu suy thi no lay mui gio cua may chay backend, dat may o dau
 * ra ket qua do. Vi vay gan thang mui gio da cau hinh vao.
 *
 * Ban tin khong co timestamp hoac timestamp hong thi dung gio nhan duoc.
 */
function parseNodeTime(raw) {
  if (typeof raw === "string" && raw.length >= 19) {
    const normalized = HAS_TIMEZONE.test(raw) ? raw : raw + config.nodeTimezone;
    const parsed = new Date(normalized);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  return new Date();
}

/**
 * Chuan hoa ban tin JSON tu node thanh document Reading.
 * Chap nhan ca ban tin thieu truong (vd GPS chua fix) thay vi tu choi ca ban tin.
 */
function normalize(payload, topicNodeId) {
  const nodeId = payload.node_id || topicNodeId;
  if (!nodeId) throw new Error("Ban tin khong xac dinh duoc node_id");

  const timestamp = parseNodeTime(payload.timestamp);

  const doc = {
    node_id: nodeId,
    timestamp,
    env: payload.env,
    received_at: new Date(),
  };

  if (payload.soil) doc.soil = payload.soil;
  if (payload.light) doc.light = payload.light;
  if (payload.pump) doc.pump = payload.pump;
  if (payload.tinyml) doc.tinyml = payload.tinyml;
  if (payload.imu) doc.imu = payload.imu;
  if (payload.gps) doc.gps = payload.gps;

  const vibration = vibrationOf(payload);
  if (vibration !== null) doc.derived = { vibration_g: vibration };

  return doc;
}

/**
 * Bom doi trang thai. Neu backend vua gui lenh thu cong thi ghi nhan la Manual
 * va danh dau lenh do da duoc node xac nhan; nguoc lai la quyet dinh cua TinyML.
 */
async function recordPumpTransition(doc) {
  const nodeId = doc.node_id;
  const current = doc.pump?.state;
  if (current !== "ON" && current !== "OFF") return null;

  const previous = lastPumpState.get(nodeId);
  lastPumpState.set(nodeId, current);
  if (previous === undefined || previous === current) return null;

  const pending = state.pendingCommands(nodeId);
  const isAck = pending.pump?.action === current;
  if (isAck) state.clearPendingCommand(nodeId, "pump");

  const event = await PumpEvent.create({
    node_id: nodeId,
    time: doc.received_at,
    action: current,
    trigger: isAck ? "Manual" : "TinyML",
    soilMoisture: doc.soil?.moisture_pct,
    acknowledged: true,
  });

  return event.toObject();
}

async function handleMessage(topic, payloadBuffer) {
  const topicNodeId = topic.split("/")[1];
  const payload = JSON.parse(payloadBuffer.toString());
  const doc = normalize(payload, topicNodeId);

  const saved = await Reading.create(doc);
  state.markMessage(doc.node_id, doc.received_at);
  console.log(`[MQTT] ${topic} -> luu ban ghi ${saved._id}`);

  // Su kien tho, giu nguyen ten ma frontend dang lang nghe.
  io?.emit("new_reading", saved.toObject());

  if (doc.node_id === config.nodes.farm) {
    // Node bao lai dung che do da yeu cau -> lenh doi che do coi nhu hoan tat.
    const pendingMode = state.pendingCommands(doc.node_id).mode;
    if (pendingMode && doc.pump?.mode === pendingMode.mode) {
      state.clearPendingCommand(doc.node_id, "mode");
    }

    const pumpEvent = await recordPumpTransition(doc);
    if (pumpEvent) io?.emit("pump_event", pumpEvent);

    io?.emit("farm_update", {
      sensor: withPendingCommand(toFarmSensor(saved, doc.node_id), doc.node_id),
      tinyml: toTinyMLDecision(saved),
    });
  }

  if (doc.node_id === config.nodes.cold) {
    const shipment = await getActiveShipment();
    // Chi tinh lai khi ban ghi thuoc chuyen dang chay, tranh truy van thua.
    if (shipment?.node_id === doc.node_id) {
      const snapshot = await buildColdChainSnapshot(shipment);
      if (snapshot) io?.emit("coldchain_update", snapshot);
    }
  }
}

/** Ket noi broker va bat dau nhan du lieu. */
export function startIngest(socketServer) {
  io = socketServer;

  client = mqtt.connect(config.mqtt.url, {
    username: config.mqtt.user,
    password: config.mqtt.pass,
    clientId: `agri-backend-${Math.random().toString(16).slice(2, 10)}`,
    clean: true,
    reconnectPeriod: 5000,
    connectTimeout: 15_000,
  });

  client.on("connect", () => {
    state.mqttConnected = true;
    console.log(`Da ket noi MQTT broker: ${config.mqtt.url}`);
    client.subscribe(dataTopic(), { qos: 1 }, (err) => {
      if (err) console.error("Khong subscribe duoc:", err.message);
      else console.log(`Dang lang nghe topic: ${dataTopic()}`);
    });
    io?.emit("system_status_changed");
  });

  client.on("reconnect", () => console.log("Dang ket noi lai MQTT broker..."));

  client.on("close", () => {
    if (state.mqttConnected) console.warn("Mat ket noi MQTT broker");
    state.mqttConnected = false;
    io?.emit("system_status_changed");
  });

  client.on("error", (err) => {
    state.mqttConnected = false;
    console.error("Loi MQTT:", err.message);
  });

  client.on("message", (topic, payload) => {
    handleMessage(topic, payload).catch((err) => {
      console.error(`Loi xu ly ban tin tu ${topic}:`, err.message);
    });
  });

  return client;
}

/**
 * Gui lenh dieu khien xuong node. Lenh chi la yeu cau: node moi la ben quyet dinh
 * thuc hien, va se bao lai trang thai that trong ban tin du lieu ke tiep.
 */
export function sendCommand(nodeId, command) {
  if (!client?.connected) {
    return { sent: false, reason: "Backend chua ket noi duoc MQTT broker" };
  }

  const message = { ...command, issued_at: new Date().toISOString(), source: "backend" };
  client.publish(commandTopic(nodeId), JSON.stringify(message), { qos: 1 });
  state.setPendingCommand(nodeId, command);
  console.log(`[MQTT] gui lenh toi ${commandTopic(nodeId)}:`, JSON.stringify(message));

  return { sent: true, topic: commandTopic(nodeId), message };
}

export function stopIngest() {
  return new Promise((resolve) => {
    if (!client) return resolve();
    client.end(false, {}, resolve);
  });
}
