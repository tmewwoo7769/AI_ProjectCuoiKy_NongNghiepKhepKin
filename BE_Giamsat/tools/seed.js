/**
 * Khai bao chuyen hang dang chay, hoac dat lai du lieu de quay demo tu dau.
 *
 *   node tools/seed.js                              # tao chuyen mac dinh neu chua co
 *   node tools/seed.js --threshold 32               # doi nguong nhiet do chuyen dang chay
 *   node tools/seed.js --reset-readings             # xoa sach ban ghi cam bien
 *   node tools/seed.js --code SHP-2026-01 --batch LO-2026-01 --product "Cà chua bi"
 *
 * Luu y --threshold: BME680 dat tren ban lam viec doc ~29 do C, cao hon nguong
 * bao quan 8 do C cua rau cu, nen khi test se bao vuot nguong lien tuc. Dat nguong
 * cao hon trong luc thu nghiem, va tra ve 8 khi do trong thung lanh that.
 */
import mongoose from "mongoose";

import { config } from "../src/config.js";
import { ensureDnsResolvers } from "../src/dns.js";
import { Reading, Shipment } from "../src/models.js";

ensureDnsResolvers();

const args = process.argv.slice(2);
const has = (flag) => args.includes(flag);
const valueOf = (flag, fallback) => {
  const index = args.indexOf(flag);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};

await mongoose.connect(config.mongoUrl, { serverSelectionTimeoutMS: 10_000 });
console.log("Da ket noi MongoDB");

if (has("--reset-readings")) {
  const { deletedCount } = await Reading.deleteMany({});
  console.log(`Da xoa ${deletedCount} ban ghi cam bien`);
}

const threshold = has("--threshold") ? Number(valueOf("--threshold", 8)) : null;

if (threshold !== null && !has("--code")) {
  const updated = await Shipment.findOneAndUpdate(
    { active: true },
    { $set: { tempThreshold: threshold } },
    { new: true },
  );
  console.log(updated
    ? `Da dat nguong nhiet do chuyen ${updated.code} = ${threshold} do C`
    : "Chua co chuyen nao dang chay de cap nhat");
} else {
  const code = valueOf("--code", "SHP-2024-0847");
  const batchCode = valueOf("--batch", "LO-BDH-241115-03");

  await Shipment.updateMany({ active: true }, { $set: { active: false } });

  const shipment = await Shipment.findOneAndUpdate(
    { code },
    {
      $set: {
        batchCode,
        productName: valueOf("--product", "Rau cải ngọt hữu cơ"),
        origin: valueOf("--origin", "Nông trại Bình Dương Hòa, Đà Lạt"),
        destination: valueOf("--destination", "Siêu thị Sài Gòn Co.op, Q.7 TP.HCM"),
        departureTime: new Date(Date.now() - 6 * 3600 * 1000),
        status: "Đang vận chuyển",
        node_id: valueOf("--node", config.nodes.cold),
        esp32Id: "ESP32-COLD-02",
        tempThreshold: threshold ?? 8,
        active: true,
      },
    },
    { upsert: true, new: true, setDefaultsOnInsert: true },
  );

  console.log(`Chuyen dang chay: ${shipment.code} | lo ${shipment.batchCode} | nguong ${shipment.tempThreshold} do C`);
  console.log(`Trang truy xuat: http://localhost:5173/trace/${shipment.batchCode}`);
}

await mongoose.connection.close();
