# AI_Service — Dự báo ARIMA và trợ lý LLM

Dịch vụ Python (FastAPI) chạy ở cổng **8000**, lo hai mô hình AI của đề tài.

Phần mô hình do thành viên trong nhóm viết, giữ gần như nguyên vẹn. Thay đổi
duy nhất về kiến trúc là **nguồn dữ liệu**.

## Vì sao không dùng MySQL nữa

Bản đầu tiên giữ một cơ sở dữ liệu MySQL riêng và tự sinh 30 ngày dữ liệu mẫu
để có cái mà dự báo. Hệ quả là ARIMA chạy trên **dữ liệu mẫu**, trong khi số đo
thật từ BME680, cảm biến độ ẩm đất và MPU6500 lại nằm ở MongoDB Atlas do
`BE_Giamsat` thu thập qua MQTT.

Bây giờ dịch vụ này **không giữ cơ sở dữ liệu nào**. Nó đọc số đo thật từ
backend Node qua REST, chạy mô hình, rồi gửi kết quả ngược về đó để lưu.

```
hai nút cảm biến ──MQTT──► BE_Giamsat ──► MongoDB Atlas
                            (cổng 4000)         ▲
                                 │              │
                    đọc số đo    │              │ lưu kết quả
                                 ▼              │
                            AI_Service ─────────┘
                            (cổng 8000)
```

Đổi lại được ba thứ: dự báo chạy trên dữ liệu thật, dữ liệu chỉ nằm một chỗ,
và máy trình diễn không phải cài XAMPP.

## Cài đặt

```powershell
cd AI_Service
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env
```

## Chạy

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Backend Node phải chạy trước, vì dịch vụ này lấy dữ liệu từ đó.

Trang thử API: `http://127.0.0.1:8000/docs`

## Khoá mô hình ngôn ngữ

Chưa có khoá thì trợ lý dùng **bộ luật dự phòng** — vẫn trả lời dựa trên số đo
thật, chỉ là không gọi mô hình ngôn ngữ. Giao diện hiện nhãn "RAG dự phòng".

Muốn dùng Gemini hoặc OpenAI, điền khoá vào `.env` rồi khởi động lại. Kiểm tra
tại `/api/health`: trường `models.llm` phải đổi từ `fallback` sang `gemini`
hoặc `openai`.

## Độ phân giải của ARIMA

Mô hình cần tối thiểu 24 điểm sau khi gộp. Gộp theo giờ thì phải có ít nhất một
ngày dữ liệu.

Khi hệ thống mới chạy được vài giờ, `_pick_resolution()` tự hạ xuống mức mịn hơn
(30 phút, 15 phút, 10 phút, 5 phút) để vẫn dự báo được, đổi lại tầm nhìn ngắn
hơn. Độ phân giải và tầm nhìn thực tế nằm trong kết quả trả về và được hiện rõ
trên giao diện, không giấu đi.

Ví dụ khi mới có 3 giờ dữ liệu: gộp 10 phút, 24 điểm, dự báo 2 giờ tới.
Khi đã tích đủ vài ngày, nó tự quay lại gộp theo giờ và dự báo đủ 24 giờ.

## Cảnh báo được suy ra, không lưu sẵn

Bản MySQL có bảng `canh_bao` ghi sẵn cảnh báo lúc nhận dữ liệu. Bản này suy
trực tiếp từ số đo 24 giờ gần nhất mỗi khi cần.

Lý do: ngưỡng cảnh báo có thể thay đổi. Cảnh báo đã ghi sẵn sẽ giữ ngưỡng cũ và
mâu thuẫn với ngưỡng đang dùng; suy lại thì luôn nhất quán.

## Cấu trúc

| Tệp | Việc |
| --- | --- |
| `app/main.py` | Các endpoint HTTP |
| `app/node_repository.py` | Đọc dữ liệu từ backend Node, lưu kết quả ngược lại |
| `app/ai/arima.py` | Dự báo chuỗi thời gian |
| `app/ai/llm.py` | Trợ lý RAG, gọi Gemini/OpenAI hoặc dùng luật dự phòng |
| `app/ai/tinyml.py` | Bản cây quyết định chạy trên máy chủ, để đối chiếu với bản nhúng trên STM32 |
