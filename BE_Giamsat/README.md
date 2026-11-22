# BE Giám sát — Backend hệ sinh thái nông nghiệp tuần hoàn

Nhận dữ liệu MQTT từ hai nút cảm biến, lưu vào MongoDB, phục vụ REST API và
WebSocket cho `FE_Giam-sat`.

```
Node 1 "Vườn"   (ESP32-S3, WiFi) ──┐
                                   ├──► MQTT Broker ◄── Backend ──► Frontend React
Node 2 "Vận chuyển" (ESP32-S3, 4G) ┘    (HiveMQ Cloud)      │        (REST + WebSocket)
                                                         MongoDB
```

Broker đặt trên cloud vì Node 2 gửi qua mạng di động từ ngoài đường, còn laptop
chạy backend nằm sau NAT không có IP công khai. Cả thiết bị lẫn backend đều mở
kết nối *đi ra* tới broker nên không cần mở cổng ở router.

## 1. Cài đặt

### Bước 1 — MongoDB

Chọn một trong hai cách:

**Cách A — MongoDB Atlas (không cần cài gì lên máy).**
Tạo tài khoản tại [mongodb.com/atlas](https://www.mongodb.com/atlas), tạo cluster
miễn phí M0, vào *Database Access* tạo user, vào *Network Access* thêm IP hiện tại
(hoặc `0.0.0.0/0` khi demo ở nhiều nơi), rồi copy connection string dạng
`mongodb+srv://...` vào `MONGO_URL`.

**Cách B — MongoDB Community trên máy.**
Tải bản Windows tại [mongodb.com/try/download/community](https://www.mongodb.com/try/download/community),
cài với tuỳ chọn *Install MongoDB as a Service*. Sau khi cài, `MONGO_URL` giữ nguyên
`mongodb://127.0.0.1:27017/agri_ecosystem`. Kiểm tra dịch vụ đã chạy:

```powershell
Get-Service MongoDB
```

### Bước 2 — MQTT broker (HiveMQ Cloud)

1. Vào [hivemq.com/mqtt-cloud-broker](https://www.hivemq.com/mqtt-cloud-broker/) → *Start free*.
2. Tạo cluster **Serverless (Free)**. Gói này cho 100 kết nối và 10 GB/tháng, thừa cho hai node.
3. Mở cluster → tab **Access Management** → *Add credentials*: đặt username/password,
   quyền **Publish and Subscribe**. Dùng chung bộ này cho backend và cho hai ESP32,
   hoặc tạo thêm bộ riêng cho thiết bị.
4. Ở trang *Overview* lấy **Cluster URL** dạng `xxxxx.s1.eu.hivemq.cloud`, cổng TLS **8883**.

Điền vào `.env`:

```
MQTT_URL=mqtts://xxxxx.s1.eu.hivemq.cloud:8883
MQTT_USER=<username>
MQTT_PASS=<password>
```

> ESP32 kết nối TLS tới HiveMQ Cloud cần nạp CA root certificate vào `WiFiClientSecure`.
> Có thể dùng `client.setInsecure()` khi thử nghiệm, nhưng nên nạp CA thật khi chấm thi.

### Bước 3 — Cài thư viện và tạo `.env`

```powershell
npm install
Copy-Item .env.example .env
```

Mở `.env`, điền thông tin MongoDB và HiveMQ ở trên.

### Bước 4 — Chạy

```powershell
npm start
```

Chạy đúng thì thấy:

```
Da ket noi MongoDB: mongodb://127.0.0.1:27017/agri_ecosystem
Da tao chuyen hang mac dinh: SHP-2024-0847 (lo LO-BDH-241115-03)
Backend chay tai http://localhost:4000
Da ket noi MQTT broker: mqtts://...
Dang lang nghe topic: agri/+/data
```

## 2. Kiểm thử khi chưa có phần cứng

Hai công cụ trong `tools/` cho phép chạy thử toàn bộ đường dữ liệu mà không cần
cắm ESP32, thậm chí không cần tài khoản HiveMQ.

**Broker chạy ngay trên máy** (mở terminal riêng):

```powershell
npm run broker
```

Rồi đổi `.env`: `MQTT_URL=mqtt://localhost:1883`, xoá `MQTT_USER`/`MQTT_PASS`.

**Giả lập hai node** (terminal riêng nữa):

```powershell
npm run fake
```

Node 1 giả lập có mô phỏng cả vòng lặp khép kín: bơm chạy thì độ ẩm đất tăng, bơm
tắt thì đất khô dần, và cây quyết định TinyML bật/tắt bơm theo ngưỡng 35%. Nó cũng
lắng nghe topic lệnh nên nút điều khiển bơm trên web hoạt động thật.

Gửi đúng một bản tin để kiểm tra nhanh:

```powershell
node tools/fake-node.js --node2 --once
```

Hoặc dùng `mosquitto_pub`:

```bash
mosquitto_pub -h localhost -p 1883 -t agri/node2_vanchuyen/data -m '{"node_id":"node2_vanchuyen","timestamp":"2026-08-30T12:51:52","env":{"temp_c":28.9,"humid_pct":70.76,"pressure_hpa":1006.04,"gas_kohm":30.33},"imu":{"accel_g":{"x":-0.14,"y":0.013,"z":0.993},"gyro_dps":{"x":-0.14,"y":0.35,"z":0.07}},"gps":{"fix":false}}'
```

Kiểm tra dữ liệu đã vào:

```powershell
curl http://localhost:4000/api/nodes/node2_vanchuyen/latest
curl http://localhost:4000/api/nodes/node2_vanchuyen/history
```

## 3. Quy ước MQTT

### Topic

| Topic | Chiều | Nội dung |
| --- | --- | --- |
| `agri/{node_id}/data` | Node → Backend | Bản tin cảm biến |
| `agri/{node_id}/cmd` | Backend → Node | Lệnh điều khiển |

### Bản tin Node 2 "Vận chuyển" — đã chạy

```json
{
  "node_id": "node2_vanchuyen",
  "timestamp": "2026-08-30T12:51:52",
  "env": { "temp_c": 28.9, "humid_pct": 70.76, "pressure_hpa": 1006.04, "gas_kohm": 30.33 },
  "imu": {
    "accel_g": { "x": -0.14, "y": 0.013, "z": 0.993 },
    "gyro_dps": { "x": -0.14, "y": 0.35, "z": 0.07 }
  },
  "gps": { "fix": true, "lat": 11.9404, "lon": 108.438, "satellites": 8 }
}
```

Khi chưa bắt được vệ tinh thì `gps` chỉ có `{ "fix": false }` — backend vẫn nhận và lưu
bình thường, chỉ là điểm đó không hiện lên bản đồ hành trình.

### Bản tin Node 1 "Vườn" — **đề xuất, firmware chưa viết**

Cấu trúc giữ đúng kiểu của Node 2 (`node_id`, `timestamp`, `env`) và thêm các nhánh
riêng của vườn:

```json
{
  "node_id": "node1_vuon",
  "timestamp": "2026-08-30T12:51:52",
  "env": { "temp_c": 29.4, "humid_pct": 78.2, "pressure_hpa": 1008.2, "gas_kohm": 42.1 },
  "soil": { "moisture_pct": 31.5, "raw": 2140 },
  "light": { "lux": 18420 },
  "pump": { "state": "ON", "mode": "AUTO" },
  "tinyml": { "decision": "ON", "reason": "Độ ẩm đất 31.5% thấp hơn ngưỡng 35%." }
}
```

`pump.state` là trạng thái relay **thực tế** vi điều khiển đang đặt, `pump.mode` cho
biết quyền điều khiển đang thuộc TinyML (`AUTO`) hay thuộc người dùng (`MANUAL`).
Backend dựa vào `pump.state` đổi giá trị để tự ghi nhật ký bật/tắt bơm — firmware
không phải gửi thêm sự kiện nào.

### Lệnh điều khiển

```json
{ "type": "pump", "action": "ON",     "issued_at": "...", "source": "backend" }
{ "type": "mode", "mode":   "MANUAL", "issued_at": "...", "source": "backend" }
```

Firmware Node 1 cần subscribe `agri/node1_vuon/cmd`, thực hiện lệnh, rồi **gửi ngay
một bản tin data** để báo lại trạng thái mới. Backend coi lệnh là hoàn tất khi thấy
`pump.state`/`pump.mode` khớp với lệnh đã gửi; quá 60 giây không thấy thì bỏ, giao
diện quay về hiển thị trạng thái thật của thiết bị.

## 4. REST API

Gốc: `http://localhost:4000/api`

### Dữ liệu thô

| Endpoint | Mô tả |
| --- | --- |
| `GET /nodes/:nodeId/latest` | Bản ghi mới nhất của một node |
| `GET /nodes/:nodeId/history?limit=100&range=6h` | Lịch sử, mới nhất trước |

### Vườn (Node 1)

| Endpoint | Mô tả |
| --- | --- |
| `GET /farm/sensor` | Số liệu hiện tại, đúng dạng `FarmSensor` của frontend |
| `GET /farm/tinyml` | Quyết định TinyML kèm các ngưỡng |
| `GET /farm/history?range=1h\|6h\|24h\|7d` | Bốn chuỗi thời gian để vẽ biểu đồ |
| `GET /farm/pump-events?limit=20` | Nhật ký bật/tắt bơm |
| `POST /farm/pump` `{ "action": "ON" }` | Bật/tắt bơm, chỉ khi đang ở chế độ `MANUAL` |
| `POST /farm/mode` `{ "mode": "MANUAL" }` | Chuyển quyền điều khiển |

### Chuỗi lạnh (Node 2)

| Endpoint | Mô tả |
| --- | --- |
| `GET /coldchain` | Cả trang trong một lần gọi: lô hàng, điểm hành trình, sự kiện, lịch sử |
| `GET /coldchain/shipments` | Danh sách chuyến |
| `POST /coldchain/shipments` | Khai báo chuyến mới (đặt chuyến cũ về không hoạt động) |
| `GET /trace/:code` | Trang truy xuất công khai; nhận cả mã chuyến lẫn mã lô |

### Hệ thống

| Endpoint | Mô tả |
| --- | --- |
| `GET /health` | Kiểm tra backend sống, có nối được Mongo/MQTT không |
| `GET /system/status` | Trạng thái hai node + MQTT + backend cho thanh trạng thái |

## 5. Sự kiện WebSocket

Frontend nối Socket.io tới cùng cổng `4000`.

| Sự kiện | Nội dung |
| --- | --- |
| `new_reading` | Bản ghi thô vừa lưu, cho mọi node |
| `farm_update` | `{ sensor, tinyml }` — mỗi khi Node 1 gửi bản tin |
| `coldchain_update` | Cả trang chuỗi lạnh đã tính lại — mỗi khi Node 2 gửi bản tin |
| `pump_event` | Khi bơm đổi trạng thái |
| `system_status` | Phát mỗi 10 giây để node im lặng tự chuyển sang Offline |

## 6. Cách tính các chỉ số dẫn xuất

**Chỉ số rung lắc.** MPU6500 báo gia tốc gồm cả trọng lực, xe đứng yên thì
`|a| ≈ 1 g`. Backend lấy `|‖a‖ − 1|` làm chỉ số rung tức thời của từng bản ghi, và
RMS của cả hành trình là `√(trung bình bình phương)` các giá trị đó. Vượt
`VIBRATION_IMPACT_G` (mặc định 0.5 g) thì tính là một lần va đập.

Đây là xấp xỉ từ dữ liệu lấy mẫu thưa (vài giây một bản tin), không phải phân tích
phổ rung đầy đủ — nếu cần chính xác hơn thì phải lấy mẫu IMU ở tần số cao ngay trên
vi điều khiển rồi gửi lên giá trị RMS đã tính sẵn.

**Thời gian vượt ngưỡng.** Mỗi bản ghi vượt ngưỡng đại diện cho một chu kỳ lấy mẫu;
chu kỳ lấy theo trung vị khoảng cách giữa các bản ghi thực tế.

**Chất lượng đường truyền.** So khoảng thời gian từ bản tin cuối tới hiện tại với
nhịp gửi trung vị của chính node đó. Gửi đúng nhịp là 100%, trễ gấp đôi còn 50%.

## 7. Lưu ý khi demo

**Ngưỡng nhiệt độ chuỗi lạnh.** Chuyến mặc định đặt ngưỡng 8 °C cho rau củ. BME680 để
trên bàn đọc khoảng 29 °C nên khi thử nghiệm giao diện sẽ báo vượt ngưỡng liên tục.
Nâng ngưỡng trong lúc thử rồi trả về 8 khi đặt cảm biến vào thùng lạnh thật:

```powershell
node tools/seed.js --threshold 35
```

**Frontend luôn hiển thị được.** Khi backend chưa chạy, frontend tự dùng dữ liệu mô
phỏng và hiện nhãn "Dữ liệu mô phỏng" trên thanh trạng thái. Không có tình huống
màn hình trắng giữa buổi chấm.

**Xoá dữ liệu để quay lại từ đầu:**

```powershell
node tools/seed.js --reset-readings
```

## 8. Phần chưa làm

- Firmware publish MQTT cho Node 1 (PubSubClient + WiFi) theo bản tin đề xuất ở mục 3.
- Node 2 chuyển từ WiFi sang 4G bằng bộ lệnh `AT+CMQTT...` của SIMCom A7680C.
- Dự báo ARIMA — frontend vẫn dùng `services/data/forecastData.ts`.
- Trợ lý LLM — frontend vẫn dùng `services/data/assistantData.ts`.

Hai phần sau đọc dữ liệu lịch sử từ collection `readings`, chưa có endpoint nào.
