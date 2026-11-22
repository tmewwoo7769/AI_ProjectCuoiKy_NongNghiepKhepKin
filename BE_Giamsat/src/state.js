import { config } from "./config.js";

/**
 * Trang thai runtime cua he thong, giu trong bo nho (khong can luu DB).
 * Dung de tra loi nhanh cau hoi "node con song khong" va "lenh thu cong da duoc node xac nhan chua".
 */
const nodes = new Map();

/** Qua thoi gian nay ma node chua bao lai trang thai thi coi nhu lenh khong den noi. */
const PENDING_TTL_MS = 60_000;

function ensure(nodeId) {
  if (!nodes.has(nodeId)) {
    nodes.set(nodeId, {
      nodeId,
      lastSeen: null,
      messageCount: 0,
      /** Moc thoi gian cac ban tin gan nhat, dung de uoc luong chat luong duong truyen. */
      recentTimestamps: [],
      /** Lenh backend da gui nhung node chua bao lai trang thai, tach theo loai lenh. */
      pendingCommands: {},
    });
  }
  return nodes.get(nodeId);
}

export const state = {
  mqttConnected: false,
  mongoConnected: false,
  startedAt: new Date(),

  markMessage(nodeId, at = new Date()) {
    const entry = ensure(nodeId);
    entry.lastSeen = at;
    entry.messageCount += 1;
    entry.recentTimestamps.push(at.getTime());
    if (entry.recentTimestamps.length > 30) entry.recentTimestamps.shift();
  },

  get(nodeId) {
    return ensure(nodeId);
  },

  isOnline(nodeId) {
    const entry = nodes.get(nodeId);
    if (!entry?.lastSeen) return false;
    return Date.now() - entry.lastSeen.getTime() < config.offlineAfterMs;
  },

  lastSeen(nodeId) {
    return nodes.get(nodeId)?.lastSeen ?? null;
  },

  /**
   * Chat luong ket noi 0-100: so sanh khoang cach tu ban tin cuoi den hien tai
   * voi nhip gui trung binh cua chinh node do. Gui dung nhip -> 100.
   */
  connectionQuality(nodeId) {
    const entry = nodes.get(nodeId);
    if (!entry?.lastSeen) return 0;

    const stamps = entry.recentTimestamps;
    if (stamps.length < 3) return state.isOnline(nodeId) ? 100 : 0;

    const gaps = [];
    for (let i = 1; i < stamps.length; i += 1) gaps.push(stamps[i] - stamps[i - 1]);
    gaps.sort((a, b) => a - b);
    const medianGap = gaps[Math.floor(gaps.length / 2)] || 1;

    const sinceLast = Math.max(Date.now() - entry.lastSeen.getTime(), 1);
    const ratio = medianGap / Math.max(sinceLast, medianGap);
    return Math.round(Math.min(100, Math.max(0, ratio * 100)));
  },

  setPendingCommand(nodeId, command) {
    ensure(nodeId).pendingCommands[command.type] = { ...command, sentAt: new Date() };
  },

  clearPendingCommand(nodeId, type) {
    const entry = ensure(nodeId);
    if (type) delete entry.pendingCommands[type];
    else entry.pendingCommands = {};
  },

  /**
   * Cac lenh dang cho node xac nhan. Lenh qua han bi bo di de giao dien khong
   * hien mai mot trang thai mong muon ma thiet bi khong bao gio thuc hien.
   */
  pendingCommands(nodeId) {
    const entry = ensure(nodeId);
    for (const [type, command] of Object.entries(entry.pendingCommands)) {
      if (Date.now() - command.sentAt.getTime() > PENDING_TTL_MS) delete entry.pendingCommands[type];
    }
    return entry.pendingCommands;
  },

  snapshot() {
    return {
      mqttConnected: state.mqttConnected,
      mongoConnected: state.mongoConnected,
      startedAt: state.startedAt,
      nodes: [...nodes.values()].map((entry) => ({
        nodeId: entry.nodeId,
        online: state.isOnline(entry.nodeId),
        lastSeen: entry.lastSeen,
        messageCount: entry.messageCount,
        connectionQuality: state.connectionQuality(entry.nodeId),
        pendingCommands: state.pendingCommands(entry.nodeId),
      })),
    };
  },
};
