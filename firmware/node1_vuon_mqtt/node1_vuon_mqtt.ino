// ===== Test LED 7 đoạn: hiển thị "8." =====
// Đổi thành false nếu dùng LED ANODE chung
#define COMMON_CATHODE true

//              a   b   c   d   e   f   g   dp
const int seg[8] = {12, 13, 14, 25, 26, 27, 32, 33};
const char* ten[8] = {"a","b","c","d","e","f","g","dp"};

#define ON  (COMMON_CATHODE ? HIGH : LOW)
#define OFF (COMMON_CATHODE ? LOW  : HIGH)

void tatHet() {
  for (int i = 0; i < 8; i++) digitalWrite(seg[i], OFF);
}

void batHet() {            // hiển thị "8."
  for (int i = 0; i < 8; i++) digitalWrite(seg[i], ON);
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < 8; i++) {
    pinMode(seg[i], OUTPUT);
  }
  tatHet();

  // Bước 1: sáng từng đoạn để kiểm tra dây nối đúng chưa
  Serial.println("Test tung doan...");
  for (int i = 0; i < 8; i++) {
    tatHet();
    digitalWrite(seg[i], ON);
    Serial.printf("Doan %s (GPIO%d) sang\n", ten[i], seg[i]);
    delay(700);
  }
  tatHet();
  delay(500);
}

void loop() {
  // Bước 2: hiện "8." nhấp nháy
  batHet();
  Serial.println("Hien thi: 8.");
  delay(1000);

  tatHet();
  delay(500);
}