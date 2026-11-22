/*
 * agri_link - lop truyen UART giua STM32 (Node 1) va ESP32-S3 gateway
 * ---------------------------------------------------------------------------
 * STM32 giu vai tro chinh: doc cam bien, chay cay quyet dinh, dong relay bom,
 * ve man hinh TFT. ESP32 chi la cua ngo ra Internet.
 *
 * Giao thuc: moi ban tin la MOT DONG JSON ket thuc bang '\n', 115200 8N1.
 *
 *   STM32 -> ESP32  (dinh ky, STM32 quyet dinh nhip):
 *     {"temp":29.4,"humid":78.2,"press":1008.2,"gas":42.1,
 *      "soil":31.5,"soil_raw":2140,"lux":18420,
 *      "pump":"ON","mode":"AUTO","dt":"ON"}
 *
 *   ESP32 -> STM32  (khi nguoi dung bam nut tren web):
 *     {"cmd":"pump","action":"ON"}
 *     {"cmd":"mode","mode":"MANUAL"}
 *
 * Vi sao JSON ma khong phai khung nhi phan: doc thang duoc bang Serial Monitor
 * nen do loi nhanh hon nhieu, va phia ESP32 dung chung thu vien ArduinoJson da
 * co san. Ban tin chi khoang 130 byte, 5 giay mot lan nen khong nang.
 *
 * ---------------------------------------------------------------------------
 * CACH GHEP VAO DU AN CUBEIDE
 *
 *  1. Chep agri_link.h vao Core/Inc/ va agri_link.c vao Core/Src/
 *  2. Trong CubeMX bat mot UART (vi du USART1) o 115200 8N1, bat ngat
 *     (NVIC -> USART1 global interrupt).
 *  3. Trong main.c:
 *
 *       #include "agri_link.h"
 *
 *       int main(void) {
 *         ...
 *         AgriLink_Init(&huart1);
 *         while (1) {
 *           AgriLink_Poll();                  // xu ly lenh vua nhan
 *           ...doc cam bien, chay cay quyet dinh nhu cu...
 *           AgriLink_Send(&sensors, pumpOn, manualMode, dtDecision);
 *           HAL_Delay(5000);
 *         }
 *       }
 *
 *       void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
 *         if (huart->Instance == USART1) AgriLink_RxByte();
 *       }
 *
 *  4. Cai dat hai ham AgriLink_OnPumpCommand / AgriLink_OnModeCommand o main.c
 *     (xem mo ta ben duoi).
 */

#ifndef AGRI_LINK_H
#define AGRI_LINK_H

#include <stdint.h>

#include "main.h"  // keo theo HAL cua du an

#ifdef __cplusplus
extern "C" {
#endif

/** Cac gia tri cam bien cua Node 1, dung don vi giong het ban tin MQTT. */
typedef struct {
  float temp_c;
  float humid_pct;
  float pressure_hpa;
  float gas_kohm;
  float soil_pct;
  uint16_t soil_raw;
  float lux;

  /**
   * 0 = lan doc BME680 vua roi that bai. Khi do bon gia tri env o tren khong
   * dang tin, ban tin se kem truong "err" thay vi so lieu gia.
   *
   * Van gui ban tin chu khong im lang: node im lang thi web bao Offline va
   * khong ai biet la mat dien, dut day hay chi hong mot con cam bien.
   */
  uint8_t env_ok;

  /** 0 = BH1750 doc loi, gia tri lux khong dang tin. */
  uint8_t light_ok;
} AgriSensors;

/** Goi mot lan trong main() truoc vong while. */
void AgriLink_Init(UART_HandleTypeDef *huart);

/**
 * Gui mot ban tin len ESP32.
 * @param pumpOn      trang thai relay THUC TE dang dat (1 = dang bom)
 * @param manualMode  1 = nguoi dung dang giu quyen, 0 = cay quyet dinh tu chay
 * @param dtDecision  ket qua cay quyet dinh vua tinh (1 = nen bat bom)
 */
void AgriLink_Send(const AgriSensors *s, uint8_t pumpOn, uint8_t manualMode, uint8_t dtDecision);

/**
 * Gui mot dong chu thuan (tu them ky tu xuong dong).
 *
 * Dung de do loi duong truyen: goi ngay sau AgriLink_Init, truoc khi khoi tao
 * cam bien. Ben ESP32 thay dong nay la biet chac day va toc do baud deu dung,
 * nho vay tach duoc loi duong truyen ra khoi loi cam bien.
 */
void AgriLink_SendText(const char *text);

/** Goi trong HAL_UART_RxCpltCallback. Nhan tung byte mot, khong chan luong. */
void AgriLink_RxByte(void);

/**
 * Xu ly cac lenh dang cho: goi AgriLink_OnPumpCommand / AgriLink_OnModeCommand
 * cho tung lenh. Tach ra khoi ngat de khong lam viec nang trong ngat.
 *
 * @return 1 neu vua thuc hien it nhat mot lenh. Khi do nen goi AgriLink_Send()
 *         ngay de bao trang thai moi len server, thay vi doi het chu ky - web
 *         se cap nhat trong tich tac chu khong phai vai giay.
 *
 * Goi ham nay CANG THUONG XUYEN CANG TOT, khong phai moi chu ky gui mot lan.
 * Xem vi du vong cho trong main_example.c.
 */
uint8_t AgriLink_Poll(void);

// ---------------------------------------------------------------------------
//  Hai ham duoi day do UNG DUNG tu viet trong main.c
// ---------------------------------------------------------------------------

/**
 * Web yeu cau bat/tat bom. Chi nen thuc hien khi dang o che do MANUAL.
 * Vi du:
 *     void AgriLink_OnPumpCommand(uint8_t on) {
 *       if (!manualMode) return;              // AUTO thi cay quyet dinh giu quyen
 *       pumpOn = on;
 *       HAL_GPIO_WritePin(RELAY_GPIO_Port, RELAY_Pin, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
 *     }
 */
void AgriLink_OnPumpCommand(uint8_t on);

/**
 * Web yeu cau doi che do dieu khien.
 * Vi du:
 *     void AgriLink_OnModeCommand(uint8_t manual) {
 *       manualMode = manual;
 *       // Tra quyen ve cho cay quyet dinh thi ap dung ngay ket qua moi nhat,
 *       // khong doi den chu ky sau.
 *       if (!manual) { pumpOn = lastDecision; HAL_GPIO_WritePin(...); }
 *     }
 *
 * Day chinh la co "tam ngung" de cay quyet dinh khong de len lenh nguoi dung:
 * trong vong lap chinh, chi goi phan dieu khien relay cua cay quyet dinh khi
 * manualMode == 0.
 */
void AgriLink_OnModeCommand(uint8_t manual);

#ifdef __cplusplus
}
#endif

#endif  // AGRI_LINK_H
