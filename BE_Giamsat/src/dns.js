import dns from "node:dns";

/**
 * Sua danh sach may chu DNS ma Node dung de tra ban ghi SRV/TXT.
 *
 * Vi sao can: chuoi ket noi mongodb+srv:// bat buoc phai tra ban ghi SRV va TXT.
 * Node khong dung bo phan giai cua he dieu hanh cho viec nay ma dung thu vien
 * c-ares voi danh sach DNS rieng. Tren may nay danh sach do la 127.0.0.1 trong
 * khi khong co dich vu DNS nao chay o day, nen moi truy van SRV bi tu choi ngay
 * (querySrv ECONNREFUSED) - du nslookup van chay binh thuong vi no hoi thang
 * DNS cua mang.
 *
 * Luu y: khong the ne bang cach dung chuoi khong co +srv. Cluster Atlas goi M0
 * la ha tang dung chung, no tu choi bat tay TLS khi noi thang vao tung shard.
 */

const PUBLIC_FALLBACK = ["8.8.8.8", "1.1.1.1"];

function isLoopback(server) {
  const host = String(server).replace(/^\[|\]$/g, "").replace(/:\d+$/, "");
  return host.startsWith("127.") || host === "::1";
}

export function ensureDnsResolvers() {
  const configured = (process.env.DNS_SERVERS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

  if (configured.length) {
    dns.setServers(configured);
    console.log("DNS: dung may chu chi dinh trong .env:", configured.join(", "));
    return;
  }

  const current = dns.getServers();
  if (current.length && current.every(isLoopback)) {
    dns.setServers(PUBLIC_FALLBACK);
    console.warn(`DNS: he thong khai bao ${current.join(", ")} nhung khong co dich vu DNS nao chay o do.`);
    console.warn(`DNS: da chuyen sang ${PUBLIC_FALLBACK.join(", ")} de tra duoc ban ghi SRV cua MongoDB Atlas.`);
    console.warn("DNS: muon sua han thi dat lai DNS cua card mang, hoac khai bao DNS_SERVERS trong .env");
  }
}
