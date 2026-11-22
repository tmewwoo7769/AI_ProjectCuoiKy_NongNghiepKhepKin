/*
 * Node 2 "Van chuyen" - ESP32-S3
 * ===========================================================================
 * Doc BME680 + MPU6500 + GPS ATGM336H + RTC DS3231, dong goi JSON va day len
 * MQTT broker.
 *
 * Tang truyen tach rieng, doi bang MOT dong #define ben duoi:
 *
 *   USE_GSM 0  -> WiFi + PubSubClient (dung de kiem thu tren ban lam viec)
 *   USE_GSM 1  -> module 4G A7680C bang lenh AT (dung khi gan len xe that)
 *
 * Phan doc cam bien va JSON giong het nhau o ca hai che do, nen chuyen qua lai
 * khong anh huong gi toi du lieu.
 *
 * Thu vien: Adafruit BME680, TinyGPSPlus, RTClib, ArduinoJson v7, PubSubClient
 * Board: ESP32S3 Dev Module  (nho bat USB CDC On Boot: Enabled)
 */

#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME680.h>
#include <HardwareSerial.h>
#include <TinyGPSPlus.h>
#include <RTClib.h>
#include <ArduinoJson.h>

#include "secrets.h"

// ===========================================================================
//  Cau hinh
// ===========================================================================

#define USE_GSM 0   // 0 = WiFi (kiem thu), 1 = 4G A7680C (chay that)

static const char *NODE_ID = "node2_vanchuyen";
static const char *TOPIC_DATA = "agri/node2_vanchuyen/data";

/* Chu ky day du lieu len server.
 * Khong dat bang 2 giay nhu vong lap cu: Atlas goi mien phi chi co 512 MB,
 * 2 giay mot ban tin la hon 43.000 ban ghi moi ngay, day trong khoang 10 ngay.
 * 10 giay van du muot cho bieu do hanh trinh va nhe hon 5 lan. */
static const unsigned long PUBLISH_INTERVAL_MS = 10000;

// --- Chan noi day, giu nguyen theo so do cua ban ---
static const int PIN_I2C_SDA = 8;
static const int PIN_I2C_SCL = 9;
static const int PIN_GPS_RX = 17;   // ESP32 nhan <- TX cua GPS
static const int PIN_GPS_TX = 18;
static const int PIN_GSM_RX = 15;   // ESP32 nhan <- TX cua A7680C
static const int PIN_GSM_TX = 16;

/* Dia chi MPU6500 tuy chan AD0: noi xuong mass = 0x68, noi len nguon = 0x69.
 * Khong dat cung mot dia chi vi DS3231 cung nam o 0x68 - de xac dinh nham.
 * Chuong trinh tu do o ca hai va doi chieu thanh ghi WHO_AM_I. */
uint8_t mpuAddr = 0;          // 0 = chua tim thay
#define MPU_WHO_AM_I      0x75
#define MPU_PWR_MGMT_1    0x6B
#define MPU_ACCEL_XOUT_H  0x3B
#define ACCEL_SENS  16384.0   // LSB/g (thang +-2g)
#define GYRO_SENS   131.0     // LSB/(deg/s) (thang +-250dps)

// ===========================================================================
//  Thiet bi
// ===========================================================================

Adafruit_BME680 bme;
HardwareSerial GPS_Serial(1);
TinyGPSPlus gps;
RTC_DS3231 rtc;

float gyroOffsetX = 0, gyroOffsetY = 0, gyroOffsetZ = 0;
unsigned long lastPublish = 0;

// ===========================================================================
//  Tang truyen - WiFi
// ===========================================================================

#if !USE_GSM

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>

WiFiClientSecure net;
PubSubClient mqtt(net);

void transportBegin() {
  Serial.printf("Dang noi WiFi \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nDa noi WiFi. IP: %s\n", WiFi.localIP().toString().c_str());

  net.setInsecure();   // thu nghiem nhanh; khi bao cao nen nap CA root that
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setKeepAlive(60);

  // Ban tin Node 2 dai khoang 300 byte, vuot mac dinh 256 byte cua thu vien.
  // Thieu dong nay thi publish() luon tra ve false ma khong bao gi.
  mqtt.setBufferSize(1024);
}

bool transportConnect() {
  if (mqtt.connected()) return true;

  String clientId = String(NODE_ID) + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  Serial.printf("Dang noi MQTT %s:%d ...", MQTT_HOST, MQTT_PORT);

  if (mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS)) {
    Serial.println(" thanh cong");
    return true;
  }
  Serial.printf(" that bai, rc=%d\n", mqtt.state());
  return false;
}

void transportLoop() {
  if (WiFi.status() != WL_CONNECTED) transportBegin();
  if (!mqtt.connected()) transportConnect();
  mqtt.loop();
}

bool transportPublish(const char *payload) {
  return mqtt.publish(TOPIC_DATA, payload);
}

// ===========================================================================
//  Tang truyen - 4G A7680C
// ===========================================================================

#else

HardwareSerial GSM_Serial(2);

/** Doc phan hoi modem toi khi gap `expect` hoac het gio. In ra Serial de do loi. */
bool gsmWaitFor(const char *expect, uint32_t timeoutMs) {
  String buffer;
  unsigned long deadline = millis() + timeoutMs;

  while (millis() < deadline) {
    while (GSM_Serial.available()) {
      char c = GSM_Serial.read();
      buffer += c;
      Serial.write(c);
      if (buffer.indexOf(expect) >= 0) return true;
      if (buffer.indexOf("ERROR") >= 0) return false;
    }
    delay(5);
  }
  Serial.printf("\n[AT] het gio, khong thay \"%s\"\n", expect);
  return false;
}

bool gsmSend(const char *cmd, const char *expect, uint32_t timeoutMs) {
  while (GSM_Serial.available()) GSM_Serial.read();   // don rac cua lenh truoc
  Serial.printf("\n[AT] >> %s\n", cmd);
  GSM_Serial.println(cmd);
  return gsmWaitFor(expect, timeoutMs);
}

void transportBegin() {
  GSM_Serial.begin(115200, SERIAL_8N1, PIN_GSM_RX, PIN_GSM_TX);
  Serial.println("Doi module A7680C khoi dong (~8s)...");
  delay(8000);
}

bool transportConnect() {
  char cmd[256];

  if (!gsmSend("AT", "OK", 3000)) {
    Serial.println("!! Modem khong tra loi. Kiem tra day TX/RX co nguoc khong,");
    Serial.println("   va nguon co du dong khong (A7680C rut hon 2A luc phat).");
    return false;
  }

  gsmSend("ATE0", "OK", 2000);   // tat echo cho de doc log
  gsmSend("ATI", "OK", 2000);    // in phien ban firmware, dung de tra tai lieu AT

  if (!gsmSend("AT+CPIN?", "READY", 5000)) {
    Serial.println("!! SIM chua san sang (lap sai chieu, hoac dang khoa ma PIN).");
    return false;
  }

  gsmSend("AT+CSQ", "OK", 2000);      // 99 nghia la khong co song
  gsmSend("AT+CEREG?", "OK", 3000);   // 0,1 = da dang ky; 0,5 = roaming

  if (!gsmSend("AT+CGATT?", "+CGATT: 1", 5000)) {
    Serial.println("!! Chua vao duoc mang du lieu. Kiem tra APN va goi cuoc data.");
    return false;
  }

  snprintf(cmd, sizeof(cmd), "AT+CGDCONT=1,\"IP\",\"%s\"", SIM_APN);
  gsmSend(cmd, "OK", 5000);

  // Don sach phien truoc, tranh loi "already started" khi khoi dong lai.
  gsmSend("AT+CMQTTDISC=0,120", "OK", 5000);
  gsmSend("AT+CMQTTREL=0", "OK", 3000);
  gsmSend("AT+CMQTTSTOP", "OK", 5000);

  if (!gsmSend("AT+CMQTTSTART", "+CMQTTSTART: 0", 15000)) return false;

  // Tham so cuoi = 1: phien nay dung SSL/TLS (HiveMQ bat buoc).
  snprintf(cmd, sizeof(cmd), "AT+CMQTTACCQ=0,\"%s-%06X\",1",
           NODE_ID, (unsigned)(ESP.getEfuseMac() & 0xFFFFFF));
  if (!gsmSend(cmd, "OK", 5000)) return false;

  gsmSend("AT+CMQTTSSLCFG=0,0", "OK", 5000);          // gan ngu canh SSL 0 cho phien 0
  gsmSend("AT+CSSLCFG=\"sslversion\",0,4", "OK", 3000); // TLS 1.2
  gsmSend("AT+CSSLCFG=\"authmode\",0,0", "OK", 3000);   // khong kiem tra chung chi may chu

  // Mot so ban firmware dung "ssl://" thay cho "tcp://". Neu dong nay bao ERROR
  // thi doi lai roi nap. Chay ATI o tren de biet phien ban dang dung.
  snprintf(cmd, sizeof(cmd), "AT+CMQTTCONNECT=0,\"tcp://%s:%d\",60,1,\"%s\",\"%s\"",
           MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS);

  if (!gsmSend(cmd, "+CMQTTCONNECT: 0,0", 30000)) {
    Serial.println("!! Khong noi duoc broker. Kiem tra host/user/pass trong secrets.h");
    return false;
  }

  Serial.println("\n=== Da noi HiveMQ qua 4G ===");
  return true;
}

void transportLoop() { /* modem tu giu ket noi, khong can lam gi dinh ky */ }

bool transportPublish(const char *payload) {
  char cmd[64];

  snprintf(cmd, sizeof(cmd), "AT+CMQTTTOPIC=0,%d", (int)strlen(TOPIC_DATA));
  if (!gsmSend(cmd, ">", 3000)) return false;
  GSM_Serial.print(TOPIC_DATA);          // gui dung so byte da khai bao
  if (!gsmWaitFor("OK", 3000)) return false;

  snprintf(cmd, sizeof(cmd), "AT+CMQTTPAYLOAD=0,%d", (int)strlen(payload));
  if (!gsmSend(cmd, ">", 3000)) return false;
  GSM_Serial.print(payload);
  if (!gsmWaitFor("OK", 3000)) return false;

  return gsmSend("AT+CMQTTPUB=0,1,60", "+CMQTTPUB: 0,0", 15000);
}

#endif

// ===========================================================================
//  MPU6500
// ===========================================================================

void mpu6500_write(uint8_t reg, uint8_t val) {
  if (!mpuAddr) return;
  Wire.beginTransmission(mpuAddr);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

/** Liet ke moi thiet bi dang tra loi tren bus I2C. */
void i2cScan() {
  Serial.println("Quet bus I2C:");
  int found = 0;

  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() != 0) continue;

    Serial.printf("  0x%02X", addr);
    if (addr == 0x68) Serial.print("  <- DS3231, hoac MPU6500 khi AD0 noi mass");
    else if (addr == 0x69) Serial.print("  <- MPU6500 khi AD0 noi nguon");
    else if (addr == 0x76 || addr == 0x77) Serial.print("  <- BME680");
    Serial.println();
    found++;
  }

  if (!found) {
    Serial.println("  KHONG co thiet bi nao tra loi.");
    Serial.println("  Kiem tra day SDA (chan 8) va SCL (chan 9), va dien tro keo len 4.7k.");
  }
}

/** Doc thanh ghi dinh danh. Tra ve 0 neu dia chi khong tra loi. */
uint8_t mpuWhoAmI(uint8_t addr) {
  Wire.beginTransmission(addr);
  Wire.write(MPU_WHO_AM_I);
  if (Wire.endTransmission(false) != 0) return 0;
  if (Wire.requestFrom((int)addr, 1, (int)true) != 1) return 0;
  return Wire.read();
}

/**
 * Tim MPU o ca hai dia chi. Khong chi kiem tra "co tra loi khong" ma con doi
 * chieu ma dinh danh - vi DS3231 cung nam o 0x68 va cung se tra loi, nhan nham
 * no la MPU thi doc ra so vo nghia.
 */
bool mpuDetect() {
  const uint8_t candidates[] = { 0x69, 0x68 };

  for (uint8_t i = 0; i < 2; i++) {
    uint8_t id = mpuWhoAmI(candidates[i]);
    // 0x70 = MPU6500, 0x71 = MPU9250, 0x73 = MPU9255, 0x68 = MPU6050
    if (id == 0x70 || id == 0x71 || id == 0x73 || id == 0x68) {
      mpuAddr = candidates[i];
      Serial.printf("Tim thay MPU o 0x%02X, WHO_AM_I = 0x%02X\n", mpuAddr, id);
      return true;
    }
    Serial.printf("  0x%02X: WHO_AM_I = 0x%02X (khong phai MPU)\n", candidates[i], id);
  }

  Serial.println("!! KHONG tim thay MPU6500. Se khong gui du lieu rung lac.");
  return false;
}

/**
 * Ghep hai byte thanh so 16 bit co dau.
 *
 * Phai doc ra bien rieng theo dung thu tu. Viet gon thanh
 *   (Wire.read() << 8) | Wire.read()
 * la sai: chuan C++ khong quy dinh ve trai hay ve phai duoc tinh truoc, nen
 * trinh bien dich co the doc byte thap len truoc va gia tri bi dao byte. Loi
 * nay chay dung tren may nay nhung sai tren may khac, rat kho lan ra.
 */
static int16_t mpu_read16(void) {
  uint8_t hi = Wire.read();
  uint8_t lo = Wire.read();
  return (int16_t)(((uint16_t)hi << 8) | lo);
}

void mpu6500_readRaw(int16_t *ax, int16_t *ay, int16_t *az,
                     int16_t *gx, int16_t *gy, int16_t *gz) {
  *ax = *ay = *az = *gx = *gy = *gz = 0;
  if (!mpuAddr) return;

  Wire.beginTransmission(mpuAddr);
  Wire.write(MPU_ACCEL_XOUT_H);
  Wire.endTransmission(false);
  if (Wire.requestFrom((int)mpuAddr, 14, (int)true) != 14) return;

  *ax = mpu_read16();
  *ay = mpu_read16();
  *az = mpu_read16();
  mpu_read16();          // bo qua hai byte nhiet do noi tai
  *gx = mpu_read16();
  *gy = mpu_read16();
  *gz = mpu_read16();
}

void calibrateGyro(int samples = 300) {
  if (!mpuAddr) return;
  Serial.println("Dang calibrate Gyro, giu yen thiet bi...");
  long sumX = 0, sumY = 0, sumZ = 0;
  int16_t ax, ay, az, gx, gy, gz;

  for (int i = 0; i < samples; i++) {
    mpu6500_readRaw(&ax, &ay, &az, &gx, &gy, &gz);
    sumX += gx; sumY += gy; sumZ += gz;
    delay(5);
  }

  gyroOffsetX = sumX / (float)samples;
  gyroOffsetY = sumY / (float)samples;
  gyroOffsetZ = sumZ / (float)samples;
  Serial.printf("Gyro offset: X=%.2f Y=%.2f Z=%.2f\n", gyroOffsetX, gyroOffsetY, gyroOffsetZ);
}

// ===========================================================================
//  Dong goi JSON
// ===========================================================================

size_t buildPayload(char *out, size_t outSize) {
  DateTime now = rtc.now();
  bool bmeOk = bme.performReading();

  int16_t ax_raw, ay_raw, az_raw, gx_raw, gy_raw, gz_raw;
  mpu6500_readRaw(&ax_raw, &ay_raw, &az_raw, &gx_raw, &gy_raw, &gz_raw);

  JsonDocument doc;
  doc["node_id"] = NODE_ID;

  // Gio dia phuong, khong kem mui gio - backend tu gan +07:00 vao.
  char timeStr[25];
  snprintf(timeStr, sizeof(timeStr), "%04d-%02d-%02dT%02d:%02d:%02d",
           now.year(), now.month(), now.day(), now.hour(), now.minute(), now.second());
  doc["timestamp"] = timeStr;

  JsonObject env = doc["env"].to<JsonObject>();
  if (bmeOk) {
    env["temp_c"] = round(bme.temperature * 100) / 100.0;
    env["humid_pct"] = round(bme.humidity * 100) / 100.0;
    env["pressure_hpa"] = round((bme.pressure / 100.0) * 100) / 100.0;
    env["gas_kohm"] = round((bme.gas_resistance / 1000.0) * 100) / 100.0;
  } else {
    env["error"] = "bme680_read_fail";
  }

  /* Khong co MPU thi BO HAN nhanh imu, khong gui so 0.
   *
   * Ly do: server tinh chi so rung lac bang do lech cua do lon vector gia toc
   * so voi 1g. Gui 0,0,0 thi ra |0 - 1| = 1 g, vuot nguong va dap o MOI ban tin
   * - trang Chuoi lanh se bao va dap tang lien tuc va nguy co dap nat "Cao"
   * hoan toan gia. Thieu nhanh imu thi server bo qua, dung hon nhieu. */
  if (mpuAddr) {
    JsonObject imu = doc["imu"].to<JsonObject>();
    JsonObject accel = imu["accel_g"].to<JsonObject>();
    accel["x"] = round((ax_raw / ACCEL_SENS) * 1000) / 1000.0;
    accel["y"] = round((ay_raw / ACCEL_SENS) * 1000) / 1000.0;
    accel["z"] = round((az_raw / ACCEL_SENS) * 1000) / 1000.0;

    JsonObject gyro = imu["gyro_dps"].to<JsonObject>();
    gyro["x"] = round(((gx_raw - gyroOffsetX) / GYRO_SENS) * 100) / 100.0;
    gyro["y"] = round(((gy_raw - gyroOffsetY) / GYRO_SENS) * 100) / 100.0;
    gyro["z"] = round(((gz_raw - gyroOffsetZ) / GYRO_SENS) * 100) / 100.0;
  }

  JsonObject gpsObj = doc["gps"].to<JsonObject>();
  if (gps.location.isValid()) {
    // BAT BUOC co "fix": true. Server dua vao truong nay de quyet dinh co ve
    // diem len ban do hanh trinh hay khong; thieu no thi du co toa do that,
    // ban do van trong tron.
    gpsObj["fix"] = true;
    gpsObj["lat"] = gps.location.lat();
    gpsObj["lon"] = gps.location.lng();
    gpsObj["satellites"] = gps.satellites.value();
  } else {
    gpsObj["fix"] = false;
  }

  return serializeJson(doc, out, outSize);
}

// ===========================================================================

void setup() {
  Serial.begin(115200);

  // Cho may tinh nhan ra cong USB ao, nhung khong qua 3 giay de board van chay
  // duoc khi chi cam sac.
  unsigned long waitStart = millis();
  while (!Serial && millis() - waitStart < 3000) delay(10);
  delay(200);

  Serial.println("\n=== Node 2 - Van chuyen ===");
  Serial.printf("Tang truyen: %s\n", USE_GSM ? "4G (A7680C)" : "WiFi");

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);

  // Bo dem mac dinh 256 byte khong du: GPS phat NMEA lien tuc khoang 1 KB moi
  // giay, giua hai lan doc ma tran bo dem thi cau NMEA bi cat va TinyGPS bo qua.
  GPS_Serial.setRxBufferSize(2048);
  GPS_Serial.begin(9600, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);

  if (!bme.begin(0x76)) {
    Serial.println("!! Khong tim thay BME680 o dia chi 0x76");
  } else {
    bme.setTemperatureOversampling(BME680_OS_8X);
    bme.setHumidityOversampling(BME680_OS_2X);
    bme.setPressureOversampling(BME680_OS_4X);
    bme.setIIRFilterSize(BME680_FILTER_SIZE_3);
    bme.setGasHeater(320, 150);
  }

  i2cScan();
  mpuDetect();
  mpu6500_write(MPU_PWR_MGMT_1, 0x00);   // danh thuc, bo che do ngu
  delay(100);
  calibrateGyro();

  if (!rtc.begin()) Serial.println("!! Khong tim thay DS3231");
  if (rtc.lostPower()) {
    // Mat pin nuoi thi dat lai theo gio bien dich. Nho: gio nay la gio luc bam
    // Upload, khong phai gio thuc - neu lech thi thay pin CR2032 roi nap lai.
    rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));
    Serial.println("!! DS3231 mat nguon nuoi, da dat lai theo gio bien dich");
  }

  transportBegin();
  transportConnect();

  Serial.println("=== San sang ===");
}

void loop() {
  // Nap lien tuc cho TinyGPS. Khong duoc de trong delay dai roi moi doc mot lan.
  while (GPS_Serial.available()) gps.encode(GPS_Serial.read());

  transportLoop();

  if (millis() - lastPublish >= PUBLISH_INTERVAL_MS) {
    lastPublish = millis();

    char payload[512];
    size_t len = buildPayload(payload, sizeof(payload));

    if (len == 0 || len >= sizeof(payload) - 1) {
      Serial.println("!! Ban tin qua dai, bo qua");
    } else if (transportPublish(payload)) {
      Serial.printf("-> %s  %s\n", TOPIC_DATA, payload);
    } else {
      Serial.println("!! Gui that bai, thu noi lai");
      transportConnect();
    }
  }

  delay(5);
}
