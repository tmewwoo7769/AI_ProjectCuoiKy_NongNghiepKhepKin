import http from "node:http";

import cors from "cors";
import express from "express";
import mongoose from "mongoose";
import { Server } from "socket.io";

import { config, validateConfig } from "./src/config.js";
import { ensureDnsResolvers } from "./src/dns.js";
import { ensureDefaultShipment } from "./src/coldchain.js";
import { ensureReadingTtlIndex } from "./src/models.js";
import { startIngest, stopIngest } from "./src/ingest.js";
import { createRoutes } from "./src/routes.js";
import { state } from "./src/state.js";

const problems = validateConfig();
if (problems.length) {
  console.error("Cau hinh chua day du:");
  for (const problem of problems) console.error("  - " + problem);
  console.error('Hay copy ".env.example" thanh ".env" va dien thong tin.');
  process.exit(1);
}

// ---------------------------------------------------------------- MongoDB

// Phai goi truoc khi ket noi, vi mongodb+srv:// tra ban ghi SRV ngay luc connect.
ensureDnsResolvers();

mongoose.connection.on("connected", () => {
  state.mongoConnected = true;
});
mongoose.connection.on("disconnected", () => {
  state.mongoConnected = false;
  console.warn("Mat ket noi MongoDB");
});

try {
  await mongoose.connect(config.mongoUrl, { serverSelectionTimeoutMS: 10_000 });
  state.mongoConnected = true;
  console.log("Da ket noi MongoDB:", config.mongoUrl.replace(/\/\/.*@/, "//***@"));
} catch (err) {
  console.error("Khong ket noi duoc MongoDB:", err.message);
  console.error("Kiem tra MONGO_URL trong .env, va MongoDB da chay chua.");
  process.exit(1);
}

//const ttl = await ensureReadingTtlIndex(config.readingTtlDays);
//console.log(`Collection readings: ${ttl.action}`);

const seeded = await ensureDefaultShipment();
if (seeded) console.log(`Da tao chuyen hang mac dinh: ${seeded.code} (lo ${seeded.batchCode})`);

// ------------------------------------------------------- Express + Socket.io

const app = express();
app.use(cors({ origin: config.corsOrigin }));
app.use(express.json());

const server = http.createServer(app);
const io = new Server(server, { cors: { origin: config.corsOrigin } });

app.use("/api", createRoutes(io));

app.get("/", (_req, res) => {
  res.json({
    name: "Agri Backend",
    description: "Nhan du lieu MQTT tu Node 1 (vuon) va Node 2 (van chuyen)",
    api: "/api/health",
  });
});

app.use((_req, res) => res.status(404).json({ error: "Khong tim thay endpoint" }));

app.use((err, _req, res, _next) => {
  console.error("Loi xu ly request:", err.message);
  res.status(500).json({ error: "Loi may chu", detail: err.message });
});

io.on("connection", (socket) => {
  console.log(`[WS] Frontend ket noi: ${socket.id}`);
  socket.on("disconnect", () => console.log(`[WS] Frontend ngat: ${socket.id}`));
});

// ------------------------------------------------------------------- MQTT

startIngest(io);

/**
 * Node offline khong tao ra su kien nao, nen trang thai he thong duoc phat dinh ky
 * de giao dien tu chuyen sang "Offline" khi thiet bi ngung gui.
 */
const statusTimer = setInterval(() => {
  io.emit("system_status", {
    esp32Farm: state.isOnline(config.nodes.farm) ? "Online" : "Offline",
    esp32Cold: state.isOnline(config.nodes.cold) ? "Online" : "Offline",
    mqtt: state.mqttConnected ? "Connected" : "Disconnected",
    backend: state.mongoConnected ? "Ready" : "Not responding",
    lastUpdate: new Date().toISOString(),
  });
}, 10_000);

server.listen(config.port, () => {
  console.log(`Backend chay tai http://localhost:${config.port}`);
  console.log(`Node vuon: ${config.nodes.farm} | Node van chuyen: ${config.nodes.cold}`);
});

// --------------------------------------------------------------- Tat may chu

async function shutdown(signal) {
  console.log(`\nNhan ${signal}, dang dong ket noi...`);
  clearInterval(statusTimer);
  await stopIngest();
  await mongoose.connection.close();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 3000).unref();
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
