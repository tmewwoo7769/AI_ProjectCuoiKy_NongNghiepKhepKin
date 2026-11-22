import dotenv from "dotenv";

dotenv.config();

function num(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function list(name, fallback) {
  const raw = process.env[name];
  if (!raw) return fallback;
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export const config = {
  port: num("PORT", 4000),
  corsOrigin: list("CORS_ORIGIN", ["http://localhost:5173", "http://localhost:4173"]),

  mongoUrl: process.env.MONGO_URL || "mongodb://127.0.0.1:27017/agri_ecosystem",

  mqtt: {
    url: process.env.MQTT_URL || "",
    user: process.env.MQTT_USER || undefined,
    pass: process.env.MQTT_PASS || undefined,
    topicPrefix: process.env.MQTT_TOPIC_PREFIX || "agri",
  },

  nodes: {
    farm: process.env.NODE_FARM_ID || "node1_vuon",
    cold: process.env.NODE_COLD_ID || "node2_vanchuyen",
  },

  offlineAfterMs: num("NODE_OFFLINE_AFTER_SEC", 90) * 1000,

  /**
   * Hai node gui thoi gian doc tu RTC/NTP theo gio dia phuong, khong kem mui gio.
   * Chuoi kieu do la nhap nhang, nen backend gan mui gio nay vao thay vi de
   * JavaScript suy ra theo gio cua may dang chay - nho vay dat backend o dau
   * ket qua cung nhu nhau.
   */
  nodeTimezone: process.env.NODE_TIMEZONE_OFFSET || "+07:00",

  /**
   * So ngay giu ban ghi cam bien. Qua han thi MongoDB tu xoa.
   * Dat 0 de tat han viec tu xoa va giu lai toan bo du lieu.
   *
   * Ly do can gioi han: Atlas goi mien phi chi co 512 MB. Hai node gui 5 giay
   * mot lan la khoang 35.000 ban ghi moi ngay; khong don thi day trong khoang
   * mot thang, va khi day thi backend ngung ghi duoc.
   */
  readingTtlDays: num("READING_TTL_DAYS", 60),

  thresholds: {
    soilMoisture: num("SOIL_MOISTURE_THRESHOLD", 35),
    airTemp: num("AIR_TEMP_THRESHOLD", 38),
    airHumidity: num("AIR_HUMID_THRESHOLD", 90),
    light: num("LIGHT_THRESHOLD", 5000),
    vibrationImpactG: num("VIBRATION_IMPACT_G", 0.5),
  },
};

/** Canh bao som neu thieu cau hinh bat buoc, thay vi de loi kho hieu luc chay. */
export function validateConfig() {
  const problems = [];
  if (!config.mqtt.url) problems.push("Thieu MQTT_URL trong .env");
  if (!config.mongoUrl) problems.push("Thieu MONGO_URL trong .env");
  if (config.mqtt.url.startsWith("mqtts://") && !config.mqtt.user) {
    problems.push("MQTT_URL dung TLS (mqtts) nhung chua co MQTT_USER/MQTT_PASS");
  }
  return problems;
}
