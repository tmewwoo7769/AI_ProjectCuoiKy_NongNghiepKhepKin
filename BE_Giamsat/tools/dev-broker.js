/**
 * MQTT broker chay ngay tren may, chi dung de thu pipeline khi chua co
 * tai khoan HiveMQ Cloud (hoac khi khong co mang).
 *
 *   npm run broker
 *   -> dat MQTT_URL=mqtt://localhost:1883 trong .env
 *
 * Kien truc that van la broker tren cloud: Node 2 gui qua 4G tu ngoai duong,
 * broker cuc bo chi phuc vu buoc kiem thu tren ban lam viec.
 */
import net from "node:net";

import Aedes from "aedes";

const PORT = Number(process.env.BROKER_PORT) || 1883;
const broker = new Aedes();

broker.on("client", (client) => console.log(`[broker] client ket noi: ${client.id}`));
broker.on("clientDisconnect", (client) => console.log(`[broker] client ngat: ${client.id}`));
broker.on("publish", (packet, client) => {
  if (!client) return; // bo qua ban tin noi bo cua broker
  console.log(`[broker] ${client.id} -> ${packet.topic} (${packet.payload.length} byte)`);
});

const server = net.createServer(broker.handle);

server.listen(PORT, () => {
  console.log(`MQTT broker cuc bo dang chay tai mqtt://localhost:${PORT}`);
  console.log("Broker nay khong yeu cau username/password.");
});

process.on("SIGINT", () => {
  console.log("\nDang tat broker...");
  server.close(() => broker.close(() => process.exit(0)));
});
