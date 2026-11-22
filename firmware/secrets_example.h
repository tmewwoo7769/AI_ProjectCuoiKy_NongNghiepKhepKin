/*
 * Copy file nay thanh "secrets.h" trong CUNG thu muc voi file .ino,
 * roi dien thong tin that. Dung commit secrets.h len git.
 */
#pragma once

// ---------------------------------------------------------------- WiFi
#define WIFI_SSID "ten-wifi"
#define WIFI_PASS "mat-khau-wifi"

// ---------------------------------------------------------------- MQTT
//
// Cach A - broker cuc bo tren laptop (USE_TLS 0):
//   MQTT_HOST la dia chi IP cua laptop trong mang LAN, KHONG phai "localhost".
//   Chi dung duoc khi ESP32 va laptop cung mot mang va mang do khong chan
//   thiet bi noi chuyen voi nhau.
//
// #define MQTT_HOST "10.7.136.170"
// #define MQTT_PORT 1883
// #define MQTT_USER ""
// #define MQTT_PASS ""

// Cach B - HiveMQ Cloud (USE_TLS 1):
//   Chi dien phan host, khong kem "mqtts://" va khong kem cong.
#define MQTT_HOST "xxxxxxxx.s1.eu.hivemq.cloud"
#define MQTT_PORT 8883
#define MQTT_USER "username-tren-hivemq"
#define MQTT_PASS "mat-khau-tren-hivemq"

// ------------------------------------------------- Chi Node 2 dung 4G moi can
#define SIM_APN "v-internet"  // Viettel. Vinaphone: m3-world | Mobifone: m-wap
#define SIM_USER ""
#define SIM_PASS ""
