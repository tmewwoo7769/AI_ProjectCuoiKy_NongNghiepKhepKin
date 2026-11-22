import express from "express";

import { config } from "./config.js";
import { ArimaRun, LlmInteraction, PumpEvent, Reading, Shipment } from "./models.js";
import { state } from "./state.js";
import { buildColdChainSnapshot, buildPublicTrace, invalidateShipmentCache } from "./coldchain.js";
import { sendCommand } from "./ingest.js";
import { toFarmHistory, toFarmSensor, toTinyMLDecision, withPendingCommand } from "./transform.js";

const RANGE_MS = {
  "1h": 3600_000,
  "6h": 6 * 3600_000,
  "24h": 24 * 3600_000,
  "7d": 7 * 24 * 3600_000,
};

/** Bat loi cua handler async de khong phai lap try/catch o tung route. */
function wrap(handler) {
  return (req, res, next) => Promise.resolve(handler(req, res, next)).catch(next);
}

/** Giu toi da `max` diem, lay deu tren toan bo day, luon giu diem cuoi cung. */
function downsample(items, max) {
  if (items.length <= max) return items;
  const step = Math.ceil(items.length / max);
  const kept = items.filter((_, i) => i % step === 0);
  const last = items[items.length - 1];
  if (kept[kept.length - 1] !== last) kept.push(last);
  return kept;
}

async function latestReading(nodeId) {
  return Reading.findOne({ node_id: nodeId }).sort({ received_at: -1 }).lean();
}

export function createRoutes(io) {
  const router = express.Router();

  // ------------------------------------------------------------- He thong

  router.get("/health", (_req, res) => {
    res.json({
      ok: true,
      uptimeSec: Math.round(process.uptime()),
      mongo: state.mongoConnected,
      mqtt: state.mqttConnected,
    });
  });

  router.get("/system/status", (_req, res) => {
    const lastSeen = [state.lastSeen(config.nodes.farm), state.lastSeen(config.nodes.cold)]
      .filter(Boolean)
      .sort((a, b) => b - a)[0];

    res.json({
      esp32Farm: state.isOnline(config.nodes.farm) ? "Online" : "Offline",
      esp32Cold: state.isOnline(config.nodes.cold) ? "Online" : "Offline",
      mqtt: state.mqttConnected ? "Connected" : "Disconnected",
      backend: state.mongoConnected ? "Ready" : "Not responding",
      lastUpdate: (lastSeen ?? new Date()).toISOString(),
      detail: state.snapshot(),
    });
  });

  // ------------------------------------------- Du lieu tho theo tung node

  router.get("/nodes/:nodeId/latest", wrap(async (req, res) => {
    const latest = await latestReading(req.params.nodeId);
    if (!latest) return res.status(404).json({ error: "Chua co du lieu cho node nay" });
    res.json(latest);
  }));

  router.get("/nodes/:nodeId/history", wrap(async (req, res) => {
    const limit = Math.min(Number(req.query.limit) || 100, 2000);
    const filter = { node_id: req.params.nodeId };

    const rangeMs = RANGE_MS[req.query.range];
    if (rangeMs) filter.received_at = { $gte: new Date(Date.now() - rangeMs) };

    const history = await Reading.find(filter).sort({ received_at: -1 }).limit(limit).lean();
    res.json(history);
  }));

  // ------------------------------------------------------- Node 1: vuon

  router.get("/farm/sensor", wrap(async (_req, res) => {
    const latest = await latestReading(config.nodes.farm);
    if (!latest) return res.status(404).json({ error: "Node vuon chua gui du lieu nao" });
    res.json(withPendingCommand(toFarmSensor(latest, config.nodes.farm), config.nodes.farm));
  }));

  router.get("/farm/tinyml", wrap(async (_req, res) => {
    const latest = await latestReading(config.nodes.farm);
    if (!latest) return res.status(404).json({ error: "Node vuon chua gui du lieu nao" });
    res.json(toTinyMLDecision(latest));
  }));

  router.get("/farm/history", wrap(async (req, res) => {
    const range = RANGE_MS[req.query.range] ? req.query.range : "6h";
    const readings = await Reading.find({
      node_id: config.nodes.farm,
      received_at: { $gte: new Date(Date.now() - RANGE_MS[range]) },
    })
      .sort({ received_at: -1 })
      .limit(5000)
      .lean();

    const series = toFarmHistory(downsample(readings.reverse(), 400));
    res.json({ range, ...series });
  }));

  router.get("/farm/pump-events", wrap(async (req, res) => {
    const limit = Math.min(Number(req.query.limit) || 20, 200);
    const events = await PumpEvent.find({ node_id: config.nodes.farm })
      .sort({ time: -1 })
      .limit(limit)
      .lean();

    res.json(events.map((event) => ({
      time: new Date(event.time).toISOString(),
      action: event.action,
      trigger: event.trigger,
      soilMoisture: event.soilMoisture ?? 0,
    })));
  }));

  router.post("/farm/pump", wrap(async (req, res) => {
    const action = req.body?.action;
    if (action !== "ON" && action !== "OFF") {
      return res.status(400).json({ success: false, message: "action phải là ON hoặc OFF" });
    }

    const latest = await latestReading(config.nodes.farm);
    if (!latest) {
      return res.status(409).json({ success: false, message: "Node vườn chưa kết nối, không gửi được lệnh." });
    }

    const sensor = toFarmSensor(latest, config.nodes.farm);
    if (sensor.controlMode !== "MANUAL") {
      return res.status(409).json({
        success: false,
        message: "Hãy chuyển sang chế độ thủ công trước khi điều khiển máy bơm.",
      });
    }

    const result = sendCommand(config.nodes.farm, { type: "pump", action });
    if (!result.sent) return res.status(503).json({ success: false, message: result.reason });

    const next = withPendingCommand(sensor, config.nodes.farm);
    io?.emit("farm_update", { sensor: next, tinyml: toTinyMLDecision(latest) });

    res.json({
      success: true,
      message: `Đã gửi lệnh ${action === "ON" ? "bật" : "tắt"} bơm, đang chờ node xác nhận.`,
      sensor: next,
    });
  }));

  router.post("/farm/mode", wrap(async (req, res) => {
    const mode = req.body?.mode;
    if (mode !== "AUTO" && mode !== "MANUAL") {
      return res.status(400).json({ success: false, message: "mode phải là AUTO hoặc MANUAL" });
    }

    const latest = await latestReading(config.nodes.farm);
    if (!latest) {
      return res.status(409).json({ success: false, message: "Node vườn chưa kết nối, không gửi được lệnh." });
    }

    const result = sendCommand(config.nodes.farm, { type: "mode", mode });
    if (!result.sent) return res.status(503).json({ success: false, message: result.reason });

    const next = withPendingCommand(toFarmSensor(latest, config.nodes.farm), config.nodes.farm);
    io?.emit("farm_update", { sensor: next, tinyml: toTinyMLDecision(latest) });

    res.json({
      success: true,
      message: mode === "MANUAL"
        ? "Đã gửi lệnh chuyển sang chế độ thủ công."
        : "Đã gửi lệnh trả quyền điều khiển về TinyML.",
      sensor: next,
    });
  }));

  // -------------------------------------------------- Node 2: chuoi lanh

  router.get("/coldchain", wrap(async (_req, res) => {
    const snapshot = await buildColdChainSnapshot();
    if (!snapshot) return res.status(404).json({ error: "Chưa khai báo chuyến hàng nào" });
    res.json(snapshot);
  }));

  router.get("/coldchain/shipments", wrap(async (_req, res) => {
    res.json(await Shipment.find().sort({ createdAt: -1 }).lean());
  }));

  /** Khai bao mot chuyen moi, hoac cap nhat nguong nhiet do cua chuyen dang chay. */
  router.post("/coldchain/shipments", wrap(async (req, res) => {
    const body = req.body ?? {};
    if (!body.code || !body.batchCode) {
      return res.status(400).json({ error: "Thiếu code hoặc batchCode" });
    }

    if (body.active !== false) await Shipment.updateMany({ active: true }, { $set: { active: false } });

    const shipment = await Shipment.findOneAndUpdate(
      { code: body.code },
      {
        $set: {
          batchCode: body.batchCode,
          productName: body.productName,
          origin: body.origin,
          destination: body.destination,
          departureTime: body.departureTime ? new Date(body.departureTime) : new Date(),
          status: body.status || "Đang vận chuyển",
          node_id: body.node_id || config.nodes.cold,
          esp32Id: body.esp32Id,
          tempThreshold: body.tempThreshold ?? 8,
          active: body.active !== false,
        },
      },
      { upsert: true, new: true, setDefaultsOnInsert: true },
    ).lean();

    invalidateShipmentCache();
    const snapshot = await buildColdChainSnapshot();
    if (snapshot) io?.emit("coldchain_update", snapshot);

    res.status(201).json(shipment);
  }));

  // ------------------------------------------------------------- Ket qua AI
  //
  // Dich vu AI viet bang Python chay rieng o cong 8000: no doc du lieu cam bien
  // qua cac endpoint o tren, roi gui ket qua ve day de luu. Nho vay du lieu van
  // nam mot cho duy nhat la MongoDB, khong phai dung them co so du lieu thu hai.

  router.post("/ai/arima-runs", wrap(async (req, res) => {
    const { metric, historyRange, horizonHours, result } = req.body ?? {};
    if (!result) return res.status(400).json({ error: "Thiếu kết quả dự báo" });

    const run = await ArimaRun.create({ metric, historyRange, horizonHours, result });
    io?.emit("arima_run", { id: run._id, metric, createdAt: run.createdAt });
    res.status(201).json({ id: run._id });
  }));

  router.get("/ai/arima-runs/latest", wrap(async (_req, res) => {
    const run = await ArimaRun.findOne().sort({ createdAt: -1 }).lean();
    if (!run) return res.status(404).json({ error: "Chưa có lần chạy ARIMA nào" });

    // Tro ly LLM chi can phan tom tat, khong can ca tram diem du bao.
    res.json({
      metric: run.metric,
      generatedAt: run.createdAt,
      conclusion: run.result?.conclusion,
      unit: run.result?.unit,
      threshold: run.result?.threshold,
      resolution: run.result?.resolution,
      horizonLabel: run.result?.horizonLabel,
      model: run.result?.model,
    });
  }));

  router.post("/ai/llm-history", wrap(async (req, res) => {
    const { result, sensor } = req.body ?? {};
    if (!result) return res.status(400).json({ error: "Thiếu nội dung trả lời" });

    const saved = await LlmInteraction.create({ question: result.question, result, sensor });
    res.status(201).json({ id: saved._id });
  }));

  router.get("/ai/llm-history", wrap(async (req, res) => {
    const limit = Math.min(Number(req.query.limit) || 50, 100);
    const rows = await LlmInteraction.find().sort({ createdAt: -1 }).limit(limit).lean();
    res.json(rows.map((row) => ({ ...row.result, interactionId: row._id, savedAt: row.createdAt })));
  }));

  // ---------------------------------------------- Truy xuat cong khai QR

  router.get("/trace/:code", wrap(async (req, res) => {
    const trace = await buildPublicTrace(req.params.code);
    if (!trace) return res.status(404).json({ error: "Không tìm thấy lô hàng" });
    res.json(trace);
  }));

  return router;
}
