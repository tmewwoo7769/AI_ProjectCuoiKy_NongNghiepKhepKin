/**
 * fake-node.js v2 – Gia lap Node 1 (Vuon) + Node 2 (Xe lanh) de kiem thu pipeline.
 *
 * NANG CAP v2: CO MAY THOI TIET NGAU NHIEN
 *   - On dinh 4-8 phut, sau do tuon mua / bao / nang nong cuc bo trong 45-120 giay.
 *   - Thoi tiet anh huong TRUC TIEP payload: mua lam dat am, lux giam, nhiet do giam;
 *     nang nong lam dat kho nhanh, lux tang. Dashboard 2D doc `weather.condition`
 *     de ve may mua / mat troi ruc.
 *
 * Cach chay:
 *   node tools/fake-node.js                 # ca hai node, chu ky 5 giay
 *   node tools/fake-node.js --node2 --once  # gui dung mot ban tin Node 2
 *   node tools/fake-node.js --interval 2000 --weather-debug
 */
import mqtt from "mqtt";

import { config } from "../src/config.js";

// Module mo phong hub trung chuyen (file tuy chon, khong co van chay duoc)
let nextTransitHub = () => null;
try {
  ({ nextTransitHub } = require("./transitHub.sim"));
} catch {
  /* khong co transitHub.sim → node 2 khong gui transit_hub */
}

const args = process.argv.slice(2);
const has = (flag) => args.includes(flag);
const valueOf = (flag, fallback) => {
  const index = args.indexOf(flag);
  return index >= 0 && args[index + 1] ? Number(args[index + 1]) : fallback;
};

const runFarm = has("--node1") || !has("--node2");
const runCold = has("--node2") || !has("--node1");
const once = has("--once");
const weatherDebug = has("--weather-debug");
const intervalMs = valueOf("--interval", 5000);

const prefix = config.mqtt.topicPrefix;
const FARM = config.nodes.farm;
const COLD = config.nodes.cold;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// ====================================================================
//  MAY THOI TIET (weather engine) – state machine don gian
//    stable → (sau 4-8 phut) → rain | storm | heat → (45-120s) → stable
// ====================================================================

const STABLE_MIN_MS = 4 * 60 * 1000; // it nhat 4 phut on dinh
const STABLE_JITTER_MS = 4 * 60 * 1000; // + 0-4 phut ngau nhien
const EVENT_MIN_MS = 45 * 1000; // su kien keo dai toi thieu 45s
const EVENT_JITTER_MS = 75 * 1000; // + 0-75s

const weather = {
  condition: "stable", // stable | rain | storm | heat
  rainMm: 0, // luong mua hien tai (mm/h, gia tri bieu thi)
  windKmh: 8,
  heatIndex: 0, // do chenh nhiet do khi nang nong
  nextEventAt: Date.now() + STABLE_MIN_MS + Math.random() * STABLE_JITTER_MS,
  eventEndsAt: 0,
};

function rollWeather() {
  const now = Date.now();

  if (weather.condition !== "stable") {
    if (now >= weather.eventEndsAt) {
      weather.condition = "stable";
      weather.rainMm = 0;
      weather.windKmh = 8;
      weather.heatIndex = 0;
      weather.nextEventAt = now + STABLE_MIN_MS + Math.random() * STABLE_JITTER_MS;
      if (weatherDebug) console.log("[weather] Het su kien → troi on dinh tro lai.");
    }
    return;
  }

  if (now >= weather.nextEventAt) {
    const pick = ["rain", "storm", "heat"][Math.floor(Math.random() * 3)];
    weather.condition = pick;
    weather.eventEndsAt = now + EVENT_MIN_MS + Math.random() * EVENT_JITTER_MS;
    if (pick === "rain") {
      weather.rainMm = 12 + Math.random() * 18; // mua vua
      weather.windKmh = 25 + Math.random() * 20;
    } else if (pick === "storm") {
      weather.rainMm = 25 + Math.random() * 30; // mua to / bao
      weather.windKmh = 50 + Math.random() * 30;
    } else {
      weather.heatIndex = 6 + Math.random() * 4; // nang nong 35-39°C
      weather.windKmh = 5;
    }
    if (weatherDebug) {
      console.log(
        `[weather] ⚡ Su kien moi: ${pick.toUpperCase()} (~${((weather.eventEndsAt - now) / 1000).toFixed(0)}s)`
      );
    }
  }
}

// ====================================================================
//  Trang thai gia lap
// ====================================================================

const farm = {
  soil: 34,
  pumpState: "OFF",
  mode: "AUTO",
  soilThreshold: config.thresholds.soilMoisture,
};

/** Chang duong Da Lat -> TP.HCM, noi suy tuyen tinh qua cac diem moc. */
const ROUTE = [
  [11.9404, 108.4380],
  [11.7203, 108.2150],
  [11.5362, 107.82],
  [11.4102, 107.588],
  [11.2837, 107.0645],
  [10.933, 106.85],
  [10.735, 106.722],
];

let progress = 0;

function positionAt(t) {
  const scaled = Math.min(t, 0.9999) * (ROUTE.length - 1);
  const index = Math.floor(scaled);
  const frac = scaled - index;
  const [lat1, lon1] = ROUTE[index];
  const [lat2, lon2] = ROUTE[index + 1] ?? ROUTE[index];
  return {
    lat: +(lat1 + (lat2 - lat1) * frac).toFixed(6),
    lon: +(lon1 + (lon2 - lon1) * frac).toFixed(6),
  };
}

const jitter = (base, spread) => +(base + (Math.random() - 0.5) * spread * 2).toFixed(2);

/**
 * Gio dia phuong khong kem mui gio - dung dinh dang ma RTC DS3231 va NTP
 * tren phan cung that se gui len (khong dung toISOString() vi tra ve gio UTC).
 */
function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function weatherBlock() {
  return {
    condition: weather.condition, // stable | rain | storm | heat
    rain_mm: +weather.rainMm.toFixed(1),
    wind_kmh: Math.round(weather.windKmh),
    heat_index: +weather.heatIndex.toFixed(1),
    next_change_in_s:
      weather.condition === "stable"
        ? Math.max(0, Math.round((weather.nextEventAt - Date.now()) / 1000))
        : Math.max(0, Math.round((weather.eventEndsAt - Date.now()) / 1000)),
  };
}

function farmPayload() {
  rollWeather();
  const raining = weather.condition === "rain" || weather.condition === "storm";
  const storming = weather.condition === "storm";
  const heat = weather.condition === "heat";

  // Bom chay thi dat am len; mua cap nuoc manh; nang nong lam dat kho nhanh.
  let delta = farm.pumpState === "ON" ? 1.6 : -0.55;
  if (raining) delta += storming ? 3.2 : 2.0;
  if (heat) delta -= 0.9;
  farm.soil = clamp(farm.soil + delta, 18, 80);

  // TinyML tren vi dieu khen chi so sanh nguong.
  if (farm.mode === "AUTO") {
    farm.pumpState = farm.soil < farm.soilThreshold ? "ON" : "OFF";
  }

  const soil = +farm.soil.toFixed(1);
  const decision = soil < farm.soilThreshold ? "ON" : "OFF";

  return {
    node_id: FARM,
    timestamp: stamp(),
    weather: weatherBlock(), // ← Dashboard 2D doc field nay
    env: {
      temp_c: jitter(29, 1.5) + (heat ? weather.heatIndex : 0) - (raining ? 3.5 : 0),
      humid_pct: clamp(jitter(78, 4) + (raining ? 18 : 0) - (heat ? 15 : 0), 20, 99),
      pressure_hpa: jitter(1007, 1.5) - (storming ? 4 : 0),
      gas_kohm: jitter(35, 6),
    },
    soil: { moisture_pct: soil, raw: Math.round(4095 - soil * 40) },
    light: {
      lux: Math.round(
        storming
          ? jitter(900, 400) // bao → troi toi
          : raining
            ? jitter(2500, 1200) // mua → u am
            : heat
              ? jitter(60000, 8000) // nang nong → nang gat
              : jitter(18000, 4000) // binh thuong
      ),
    },
    pump: { state: farm.pumpState, mode: farm.mode },
    tinyml: {
      decision,
      reason:
        decision === "ON"
          ? `Độ ẩm đất ${soil}% thấp hơn ngưỡng ${farm.soilThreshold}%.`
          : `Độ ẩm đất ${soil}% đạt ngưỡng ${farm.soilThreshold}%.`,
    },
  };
}

function coldPayload() {
  progress = Math.min(1, progress + 0.012);
  const { lat, lon } = positionAt(progress);

  // Thinh thoang tao mot cu xoc de kiem tra logic phat hien va dap.
  const bump = Math.random() < 0.08;
  const accelZ = bump ? jitter(1.6, 0.4) : jitter(0.99, 0.03);

  return {
    node_id: COLD,
    timestamp: stamp(),
    weather: weatherBlock(), // xe lanh cung chung thoi tiet
    env: {
      temp_c: jitter(4.2, 1.2),
      humid_pct: jitter(70, 5),
      pressure_hpa: jitter(1006, 2),
      gas_kohm: jitter(30, 4),
    },
    imu: {
      accel_g: { x: jitter(-0.1, 0.08), y: jitter(0.01, 0.08), z: accelZ },
      gyro_dps: { x: jitter(-0.1, 0.5), y: jitter(0.3, 0.5), z: jitter(0.05, 0.4) },
    },
    gps: { fix: true, lat, lon, satellites: 7 + Math.floor(Math.random() * 4) },
    transit_hub: nextTransitHub(), // null neu khong co transitHub.sim
  };
}

// ====================================================================
//  Ket noi MQTT
// ====================================================================

const client = mqtt.connect(config.mqtt.url, {
  username: config.mqtt.user,
  password: config.mqtt.pass,
  clientId: `fake-node-${Math.random().toString(16).slice(2, 8)}`,
});

function publish(payload) {
  const topic = `${prefix}/${payload.node_id}/data`;
  client.publish(topic, JSON.stringify(payload), { qos: 1 });
  const summary = payload.soil
    ? `dat ${payload.soil.moisture_pct}% bom ${payload.pump.state} thoi tiet ${payload.weather.condition}`
    : `nhiet ${payload.env.temp_c}C gps ${payload.gps.lat},${payload.gps.lon}`;
  console.log(`-> ${topic}  ${summary}`);
}

client.on("connect", () => {
  console.log(`Da ket noi ${config.mqtt.url}`);
  if (runFarm) console.log(`Gia lap Node 1: ${FARM}`);
  if (runCold) console.log(`Gia lap Node 2: ${COLD}`);
  console.log("Weather engine: on dinh 4-8 phut → rain/storm/heat 45-120s (xem them --weather-debug)");

  // Firmware that cung phai subscribe topic lenh nhu the nay.
  if (runFarm) {
    client.subscribe(`${prefix}/${FARM}/cmd`, { qos: 1 });
  }

  const tick = () => {
    if (runFarm) publish(farmPayload());
    if (runCold) publish(coldPayload());
  };

  tick();
  if (once) {
    setTimeout(() => client.end(), 500);
    return;
  }

  setInterval(tick, intervalMs);
});

client.on("message", (topic, buffer) => {
  const command = JSON.parse(buffer.toString());
  console.log(`<- ${topic}`, command);

  if (command.type === "mode") farm.mode = command.mode;
  if (command.type === "pump" && farm.mode === "MANUAL") farm.pumpState = command.action;

  // Bao lai ngay de nguoi dung khong phai cho het mot chu ky.
  publish(farmPayload());
});

client.on("error", (err) => {
  console.error("Loi MQTT:", err.message);
  process.exit(1);
});

process.on("SIGINT", () => {
  console.log("\nDung gia lap.");
  client.end(true, () => process.exit(0));
});