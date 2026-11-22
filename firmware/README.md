# Firmware — lớp truyền MQTT cho hai nút cảm biến

Thư mục này chỉ lo **phần đưa dữ liệu lên broker**. Code đọc cảm biến và mô hình
TinyML của nhóm ghép vào các chỗ đánh dấu `=== TODO ===` trong từng sketch.

| Thư mục | Nạp vào | Vai trò |
| --- | --- | --- |
| `node1_stm32_link/` | **STM32** của Node 1 | Module UART: gửi số liệu lên ESP32, nhận lệnh bơm từ web |
| `node1_vuon_mqtt/` | **ESP32-S3** của Node 1 | Gateway: UART ↔ MQTT qua WiFi |
| `node2_vanchuyen_4g/` | **ESP32-S3** của Node 2 | Đọc cảm biến + đẩy MQTT qua 4G (A7680C) |

## Node 1 chia việc thế nào

STM32 giữ toàn bộ phần "thông minh": đọc BME680, BH1750, cảm biến độ ẩm đất, chạy
cây quyết định đã nhúng, đóng relay bơm, vẽ màn hình TFT ST7735. ESP32-S3 chỉ là
cửa ngõ ra Internet.

```
cảm biến ──► STM32 ──UART JSON──► ESP32-S3 ──MQTT/TLS──► HiveMQ ──► backend
              │                       ▲
           relay bơm                  └── lệnh bật/tắt bơm từ web đi ngược lại
           TFT ST7735
```

Nhịp gửi do **STM32 quyết định**: STM32 bắn một dòng thì ESP32 đẩy lên một bản tin.
Nếu STM32 treo, ESP32 im luôn và backend tự chuyển node sang Offline sau 90 giây —
đúng hành vi mong muốn, không có chuyện web hiển thị số liệu cũ như thật.

### Đấu dây STM32 ↔ ESP32-S3

| STM32F103 | | ESP32-S3 |
| --- | --- | --- |
| **PA9** (USART1_TX) | → | **GPIO17** (RX) |
| **PA10** (USART1_RX) | ← | **GPIO16** (TX) |
| **GND** | ↔ | **GND** |

TX nối vào RX, chéo nhau. GND phải nối chung, thiếu là dữ liệu ra rác. Cả hai chip
đều dùng mức 3.3 V nên không cần mạch chuyển mức.

Lưu ý: USART1 (PA9/PA10) trên F103 cũng là cổng dùng để nạp qua USB-TTL và để
`printf` debug. Đang cắm dây sang ESP32 thì không dùng cổng đó cho việc khác được
nữa — nạp bằng ST-Link thì không ảnh hưởng gì.

### Giao thức UART (115200 8N1, mỗi bản tin một dòng JSON kết thúc `\n`)

STM32 → ESP32:

```json
{"temp":29.40,"humid":78.20,"press":1008.20,"gas":42.10,"soil":31.5,"soil_raw":2140,"lux":18420,"pump":"ON","mode":"AUTO","dt":"ON"}
```

ESP32 → STM32:

```json
{"cmd":"pump","action":"ON"}
{"cmd":"mode","mode":"MANUAL"}
```

`pump` là trạng thái relay **thực tế** STM32 đang đặt, `dt` là kết quả cây quyết định
vừa tính. Backend so hai giá trị này để biết lệnh thủ công đã được thực hiện chưa.

### Ghép `agri_link` vào dự án CubeIDE

1. Chép `agri_link.h` vào `Core/Inc/`, `agri_link.c` vào `Core/Src/`.
2. CubeMX: bật một UART (ví dụ USART2) 115200 8N1, bật ngắt trong tab NVIC.
3. `main.c`: gọi `AgriLink_Init(&huart2)` trước vòng `while`, gọi `AgriLink_Poll()`
   và `AgriLink_Send(...)` trong vòng lặp, và gọi `AgriLink_RxByte()` trong
   `HAL_UART_RxCpltCallback`.
4. Viết hai hàm `AgriLink_OnPumpCommand` và `AgriLink_OnModeCommand` — hướng dẫn
   chi tiết kèm ví dụ nằm ngay trong `agri_link.h`.

Điểm phải sửa trong vòng lặp cũ: chỉ cho cây quyết định điều khiển relay **khi
`manualMode == 0`**. Không có cờ này thì cây quyết định sẽ đè lên lệnh người dùng
ngay chu kỳ kế tiếp, nút bấm trên web coi như vô tác dụng.

Hàm `f2s()` trong `agri_link.c` tự đổi số thực thành chuỗi bằng số nguyên, nên
**không cần bật cờ `-u _printf_float`** của CubeIDE — cờ đó ăn thêm khoảng 10 KB
flash, khá phí trên F103 vốn đã phải chứa cây quyết định và thư viện TFT.

### Thử ESP32 gateway khi chưa nối STM32

Nạp sketch, mở Serial Monitor, dán nguyên dòng dưới rồi Enter — ESP32 coi như
STM32 vừa gửi và đẩy thẳng lên MQTT:

```
{"temp":29.4,"humid":78.2,"press":1008.2,"gas":42.1,"soil":31.5,"soil_raw":2140,"lux":18420,"pump":"ON","mode":"AUTO","dt":"ON"}
```

Cách này tách được lỗi mạng ra khỏi lỗi UART: dán tay mà lên được web thì phần
MQTT chắc chắn ổn, còn lại chỉ là chuyện đấu dây với STM32.

## Vì sao bắt buộc dùng HiveMQ Cloud

Node 2 đi ra Internet qua SIM của nhà mạng. Nhà mạng cấp cho nó một địa chỉ nằm
ngoài mạng nội bộ của trường, nên nó **không thể** kết nối tới broker chạy trên
laptop (`10.7.136.170` là địa chỉ chỉ có ý nghĩa bên trong mạng WiFi của trường).

Đặt broker trên cloud thì cả ba bên — Node 1, Node 2, backend — đều chỉ mở kết nối
*đi ra*, không bên nào cần địa chỉ công khai hay mở cổng router. Đây chính là lý do
kiến trúc mà nhóm nên viết vào báo cáo.

Broker cục bộ (`npm run broker` bên `BE_Giamsat`) từ giờ chỉ dùng để chạy thử với
node giả lập trên máy, không dùng với phần cứng thật nữa.

## Bước 1 — Tạo cluster HiveMQ Cloud

1. Vào [hivemq.com/mqtt-cloud-broker](https://www.hivemq.com/mqtt-cloud-broker/) → *Start free* → đăng ký.
2. Tạo cluster **Serverless (Free)**. Gói này cho 100 kết nối, 10 GB/tháng — thừa cho 2 node.
3. Mở cluster → tab **Access Management** → *Add credentials*:
   username + password, quyền **Publish and Subscribe**.
4. Tab **Overview** lấy **Cluster URL**, dạng `abc123xyz.s1.eu.hivemq.cloud`.

Cùng một bộ username/password dùng được cho cả backend lẫn hai node.

## Bước 2 — Điền `secrets.h`

Copy `secrets_example.h` thành `secrets.h` đặt **trong cùng thư mục với file .ino**
(mỗi sketch một bản), rồi điền:

```c
#define WIFI_SSID  "hotspot-cua-ban"      // chỉ Node 1 cần
#define WIFI_PASS  "mat-khau"

#define MQTT_HOST  "abc123xyz.s1.eu.hivemq.cloud"   // KHÔNG kèm mqtts:// và không kèm cổng
#define MQTT_PORT  8883
#define MQTT_USER  "username-tren-hivemq"
#define MQTT_PASS  "mat-khau-tren-hivemq"

#define SIM_APN    "v-internet"           // chỉ Node 2 cần. Viettel: v-internet
```

APN theo nhà mạng: Viettel `v-internet`, Vinaphone `m3-world`, Mobifone `m-wap`.

## Bước 3 — Thư viện Arduino

Library Manager cài:

- **PubSubClient** (Nick O'Leary) — chỉ Node 1
- **ArduinoJson** phiên bản **7** (Benoit Blanchon) — cả hai node

Board Manager chọn **ESP32S3 Dev Module**.

## Bẫy hay gặp

**Node 1 — `publish()` luôn trả về false.** PubSubClient mặc định chỉ cho bản tin
256 byte, mà bản tin Node 1 dài khoảng 330 byte nên bị cắt và gửi hỏng, không báo
lỗi gì rõ ràng. Sketch đã gọi `mqtt.setBufferSize(1024)` để xử lý — đừng xoá dòng đó.

**Node 1 — kết nối TLS thất bại.** Sketch đang dùng `net.setInsecure()` cho nhanh.
Khi làm báo cáo nên nạp CA root thật bằng `net.setCACert(...)` để chứng minh có
xác thực máy chủ, sẽ ăn điểm hơn khi hỏi về bảo mật.

**Node 2 — modem không trả lời `AT`.** Hai nguyên nhân gần như luôn là: dây TX/RX
nối ngược (TX của ESP32 phải vào RX của module), hoặc nguồn yếu — A7680C lúc phát
sóng rút tới hơn 2 A, cấp từ chân 5V của ESP32 sẽ sập nguồn. Phải cấp nguồn riêng.

**Node 2 — `AT+CMQTTCONNECT` báo ERROR.** Cú pháp `AT+CMQTT*` khác nhau giữa các
bản firmware A7680C. Chạy `ATI` để biết phiên bản, rồi đối chiếu tài liệu
"A7680C AT Command Manual" đúng bản đó. Chỗ hay khác nhất: một số bản dùng
`ssl://` thay cho `tcp://` trong chuỗi địa chỉ.

## Cách dò lỗi Node 2 hiệu quả nhất

Trước khi nạp sketch, **gõ tay từng lệnh AT** để biết modem tắc ở bước nào. Nối
module vào máy bằng USB-TTL, mở Serial Monitor ở 115200, gõ lần lượt:

```
AT
ATI
AT+CPIN?
AT+CSQ
AT+CEREG?
AT+CGATT?
AT+CGDCONT=1,"IP","v-internet"
AT+CMQTTSTART
AT+CMQTTACCQ=0,"node2test",1
AT+CMQTTSSLCFG=0,0
AT+CSSLCFG="sslversion",0,4
AT+CSSLCFG="authmode",0,0
AT+CMQTTCONNECT=0,"tcp://abc123xyz.s1.eu.hivemq.cloud:8883",60,1,"user","pass"
```

Mong đợi ở từng bước:

| Lệnh | Trả về đúng | Nếu sai |
| --- | --- | --- |
| `AT` | `OK` | Sai dây TX/RX hoặc nguồn yếu |
| `AT+CPIN?` | `+CPIN: READY` | SIM lắp sai chiều, hoặc SIM khoá PIN |
| `AT+CSQ` | `+CSQ: 10,0` đến `+CSQ: 31,0` | Trả `99,99` là không có sóng |
| `AT+CGATT?` | `+CGATT: 1` | Chưa vào được mạng dữ liệu, sai APN |
| `AT+CMQTTCONNECT` | `+CMQTTCONNECT: 0,0` | Sai host/user/pass, hoặc đổi `tcp://` thành `ssl://` |

Qua được hết bảng này thì nạp sketch chắc chắn chạy. Gửi thử một bản tin bằng tay:

```
AT+CMQTTTOPIC=0,25
> agri/node2_vanchuyen/data
AT+CMQTTPAYLOAD=0,52
> {"node_id":"node2_vanchuyen","env":{"temp_c":28.9}}
AT+CMQTTPUB=0,1,60
```

Số sau dấu phẩy phải **đúng bằng số byte** của chuỗi gửi sau đó, thừa hay thiếu
một ký tự là modem treo chờ.

## Kiểm tra dữ liệu đã lên tới backend

Sau khi node chạy, bên `BE_Giamsat`:

```powershell
curl http://localhost:4000/api/nodes/node2_vanchuyen/latest
curl http://localhost:4000/api/nodes/node1_vuon/latest
```

Hoặc mở web ở `http://localhost:5173` — thanh trạng thái trên cùng phải chuyển
"ESP32 Hàng" sang **Online** và mất nhãn "Dữ liệu mô phỏng".
