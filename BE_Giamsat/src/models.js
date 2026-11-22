import mongoose from "mongoose";

/**
 * Mot ban ghi cam bien tho, giu nguyen cau truc JSON ma node gui len.
 * Node 1 (vuon) dung env/soil/light/pump/tinyml; Node 2 (van chuyen) dung env/imu/gps.
 * Cac nhanh khong lien quan se khong ton tai trong document.
 */
const readingSchema = new mongoose.Schema(
  {
    node_id: { type: String, required: true, index: true },
    timestamp: Date,

    env: {
      temp_c: Number,
      humid_pct: Number,
      pressure_hpa: Number,
      gas_kohm: Number,
      error: String,
    },

    // --- Node 1: vuon ---
    soil: {
      moisture_pct: Number,
      raw: Number,
    },
    light: {
      lux: Number,
    },
    pump: {
      state: { type: String, enum: ["ON", "OFF"] },
      mode: { type: String, enum: ["AUTO", "MANUAL"] },
    },
    tinyml: {
      decision: { type: String, enum: ["ON", "OFF"] },
      reason: String,
    },

    // --- Node 2: van chuyen ---
    imu: {
      accel_g: { x: Number, y: Number, z: Number },
      gyro_dps: { x: Number, y: Number, z: Number },
    },
    gps: {
      fix: Boolean,
      lat: Number,
      lon: Number,
      satellites: Number,
    },

    /** Gia tri backend tu tinh khi nhan ban tin, de khong phai tinh lai moi lan truy van. */
    derived: {
      /** Do lech gia toc so voi trong luc 1g, dung lam chi so rung lac tuc thoi (g). */
      vibration_g: Number,
    },

    received_at: { type: Date, default: Date.now },
  },
  { minimize: true },
);

// Truy van chinh luon la "ban ghi moi nhat cua mot node" -> index kep phuc vu ca latest lan history.
readingSchema.index({ node_id: 1, received_at: -1 });

export const Reading = mongoose.model("Reading", readingSchema);

const TTL_INDEX_NAME = "reading_ttl";

/**
 * Dat han luu cho collection readings: MongoDB tu xoa ban ghi cu hon `days` ngay.
 *
 * Khong khai bao index nay thang trong schema, vi khi doi so ngay thi mongoose
 * se bao loi "index already exists with different options" roi bo qua - han luu
 * cu van con nguyen ma khong ai biet. O day xu ly ro rang cả ba truong hop:
 * chua co thi tao, khac han cu thi sua bang collMod, dat 0 thi go han luu di.
 *
 * Nhat ky bat/tat bom khong bi xoa: no it ban ghi va la du lieu dang gia nhat
 * de chung minh vong lap khep kin da hoat dong.
 */
export async function ensureReadingTtlIndex(days) {
  const collection = Reading.collection;
  const seconds = Math.round(days * 24 * 3600);
  const existing = (await collection.indexes()).find((index) => index.name === TTL_INDEX_NAME);

  if (!existing) {
    if (days <= 0) return { action: "khong dat han luu" };
    await collection.createIndex({ received_at: 1 }, { name: TTL_INDEX_NAME, expireAfterSeconds: seconds });
    return { action: `da dat han luu ${days} ngay` };
  }

  if (days <= 0) {
    await collection.dropIndex(TTL_INDEX_NAME);
    return { action: "da go han luu, tu gio giu lai toan bo du lieu" };
  }

  if (existing.expireAfterSeconds !== seconds) {
    // Sua han luu tai cho, khong phai xoa index roi tao lai.
    await mongoose.connection.db.command({
      collMod: collection.collectionName,
      index: { name: TTL_INDEX_NAME, expireAfterSeconds: seconds },
    });
    return { action: `da doi han luu thanh ${days} ngay` };
  }

  return { action: `han luu ${days} ngay` };
}

/** Nhat ky bat/tat bom: do TinyML tren node quyet dinh, hoac do nguoi dung bam tren web. */
const pumpEventSchema = new mongoose.Schema({
  node_id: { type: String, required: true, index: true },
  time: { type: Date, default: Date.now },
  action: { type: String, enum: ["ON", "OFF"], required: true },
  trigger: { type: String, enum: ["TinyML", "Manual"], required: true },
  soilMoisture: Number,
  /** Lenh thu cong chi duoc coi la da thuc hien khi node bao lai trang thai bom trong ban tin ke tiep. */
  acknowledged: { type: Boolean, default: false },
});

pumpEventSchema.index({ node_id: 1, time: -1 });

export const PumpEvent = mongoose.model("PumpEvent", pumpEventSchema);

/**
 * Thong tin lo hang / chuyen di. Cam bien khong biet no dang cho lo nao,
 * nen phan metadata nay do nguoi dung khai bao va gan voi mot node.
 */
const shipmentSchema = new mongoose.Schema({
  code: { type: String, required: true, unique: true }, // ma chuyen, vd SHP-2024-0847
  batchCode: { type: String, required: true, index: true }, // ma lo nong san, dung cho QR /trace/:code
  productName: String,
  origin: String,
  destination: String,
  departureTime: Date,
  arrivalTime: Date,
  status: { type: String, default: "Đang vận chuyển" },

  /** Node dang gan tren xe cho lo hang nay. */
  node_id: { type: String, required: true, index: true },
  esp32Id: String,

  /** Nguong nhiet do bao quan cua lo hang (°C). Rau cu thuong 8; test tren ban lam viec nen de cao hon. */
  tempThreshold: { type: Number, default: 8 },

  /** Chuyen dang chay se duoc trang chuoi lanh lay lam mac dinh. */
  active: { type: Boolean, default: true, index: true },

  createdAt: { type: Date, default: Date.now },
});

export const Shipment = mongoose.model("Shipment", shipmentSchema);

/**
 * Ket qua moi lan chay du bao ARIMA, do dich vu AI (Python) gui sang luu.
 * Giu lai de doi chieu giua cac lan chay va de tro ly LLM tham chieu du bao
 * moi nhat khi tra loi.
 */
const arimaRunSchema = new mongoose.Schema({
  metric: String,
  historyRange: String,
  horizonHours: Number,
  result: mongoose.Schema.Types.Mixed,
  createdAt: { type: Date, default: Date.now },
});

arimaRunSchema.index({ createdAt: -1 });

export const ArimaRun = mongoose.model("ArimaRun", arimaRunSchema);

/** Lich su hoi dap voi tro ly LLM. */
const llmInteractionSchema = new mongoose.Schema({
  question: String,
  result: mongoose.Schema.Types.Mixed,
  sensor: mongoose.Schema.Types.Mixed,
  createdAt: { type: Date, default: Date.now },
});

llmInteractionSchema.index({ createdAt: -1 });

export const LlmInteraction = mongoose.model("LlmInteraction", llmInteractionSchema);
