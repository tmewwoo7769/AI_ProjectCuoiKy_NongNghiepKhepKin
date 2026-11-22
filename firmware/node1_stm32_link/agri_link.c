#include "agri_link.h"

#include <stdio.h>
#include <string.h>

#define RX_LINE_MAX 128
#define TX_LINE_MAX 256

/* So lenh giu duoc trong khi cho vong lap chinh xu ly. Nguoi dung co the bam
 * "THU CONG" roi bam "Bat bom" ngay sau do, neu chi giu mot lenh thi lenh thu
 * hai bi vut mat ma khong bao gi. */
#define CMD_QUEUE_LEN 4

static UART_HandleTypeDef *s_uart = NULL;

/* Ngat nhan tung byte mot vao day. */
static uint8_t s_rxByte;

/* Dem dong dang nhan do. */
static char s_rxLine[RX_LINE_MAX];
static volatile uint16_t s_rxLen = 0;

/* Hang doi vong cac dong da nhan du, cho AgriLink_Poll() xu ly. Ngat chi ghi
 * vao s_head, vong lap chinh chi doc o s_tail, nen khong can khoa ngat. */
static char s_queue[CMD_QUEUE_LEN][RX_LINE_MAX];
static volatile uint8_t s_head = 0;
static volatile uint8_t s_tail = 0;

/**
 * Doi float thanh chuoi kieu "29.4" bang so nguyen.
 *
 * Ly do khong dung %f: newlib-nano tren STM32F103 mac dinh tat ho tro float
 * trong printf, in ra se thanh rac. Bat lai bang co linker -u _printf_float
 * thi ton them khoang 10 KB flash - qua phi cho mot con F103 vua chay them
 * cay quyet dinh va man hinh TFT.
 */
static void f2s(char *buf, int n, float v, int decimals) {
  // Chan khoang cho phep: vua tranh canh bao cua trinh bien dich ve do rong
  // dong trong "%0*ld", vua chan truong hop goi nham lam tran bo dem.
  if (decimals < 0) decimals = 0;
  if (decimals > 6) decimals = 6;

  int scale = 1;
  for (int i = 0; i < decimals; i++) scale *= 10;

  int negative = (v < 0.0f);
  if (negative) v = -v;

  long total = (long)(v * (float)scale + 0.5f);
  long ip = total / scale;
  long fp = total % scale;

  if (decimals <= 0) {
    // Khong co phan thap phan thi in so nguyen tron, tranh ra kieu "18420.0".
    snprintf(buf, n, "%s%ld", negative ? "-" : "", ip);
  } else {
    snprintf(buf, n, "%s%ld.%0*ld", negative ? "-" : "", ip, decimals, fp);
  }
}

void AgriLink_Init(UART_HandleTypeDef *huart) {
  s_uart = huart;
  s_rxLen = 0;
  s_head = 0;
  s_tail = 0;

  /* Gui dong chao TRUOC khi bat che do nhan.
   *
   * Ly do thu tu nay: tren mot so phien ban HAL cu cua dong F1, hai chieu gui
   * va nhan dung chung mot bien trang thai. Goi HAL_UART_Receive_IT truoc se
   * dat UART sang trang thai "dang ban", va moi HAL_UART_Transmit sau do tra ve
   * HAL_BUSY roi khong gui gi ca - im lang hoan toan, khong bao loi. Gui truoc
   * thi loai tru han kha nang do. */
  AgriLink_SendText("AGRI_LINK_READY");

  HAL_UART_Receive_IT(s_uart, &s_rxByte, 1);
}

void AgriLink_Send(const AgriSensors *s, uint8_t pumpOn, uint8_t manualMode, uint8_t dtDecision) {
  if (!s_uart || !s) return;

  char soil[12], lux[12];
  f2s(soil, sizeof(soil), s->soil_pct, 1);
  f2s(lux, sizeof(lux), s->lux, 0);

  /* Phan moi truong: doc duoc thi gui so lieu, khong doc duoc thi gui truong
   * "err" de server biet node van song ma cam bien hong. */
  char env[96];
  if (s->env_ok) {
    char temp[12], humid[12], press[12], gas[12];
    f2s(temp, sizeof(temp), s->temp_c, 2);
    f2s(humid, sizeof(humid), s->humid_pct, 2);
    f2s(press, sizeof(press), s->pressure_hpa, 2);
    f2s(gas, sizeof(gas), s->gas_kohm, 2);
    snprintf(env, sizeof(env), "\"temp\":%s,\"humid\":%s,\"press\":%s,\"gas\":%s",
             temp, humid, press, gas);
  } else {
    snprintf(env, sizeof(env), "\"err\":\"BME680\"");
  }

  char line[TX_LINE_MAX];
  int n = snprintf(line, sizeof(line),
                   "{%s,\"soil\":%s,\"soil_raw\":%u,\"lux\":%s,\"light_ok\":%u,"
                   "\"pump\":\"%s\",\"mode\":\"%s\",\"dt\":\"%s\"}\n",
                   env,
                   soil, (unsigned)s->soil_raw, lux, (unsigned)(s->light_ok ? 1 : 0),
                   pumpOn ? "ON" : "OFF",
                   manualMode ? "MANUAL" : "AUTO",
                   dtDecision ? "ON" : "OFF");

  if (n > 0) {
    HAL_UART_Transmit(s_uart, (uint8_t *)line, (uint16_t)n, 200);
  }
}

void AgriLink_SendText(const char *text) {
  if (!s_uart || !text) return;
  HAL_UART_Transmit(s_uart, (uint8_t *)text, (uint16_t)strlen(text), 200);
  HAL_UART_Transmit(s_uart, (uint8_t *)"\n", 1, 50);
}

void AgriLink_RxByte(void) {
  char c = (char)s_rxByte;

  if (c == '\n' || c == '\r') {
    if (s_rxLen > 0) {
      s_rxLine[s_rxLen] = '\0';

      uint8_t next = (uint8_t)((s_head + 1) % CMD_QUEUE_LEN);
      // Hang doi day nghia la vong lap chinh dang treo o dau do; bo lenh moi
      // con hon ghi de len lenh chua kip thuc hien.
      if (next != s_tail) {
        memcpy(s_queue[s_head], s_rxLine, s_rxLen + 1);
        s_head = next;
      }
      s_rxLen = 0;
    }
  } else if (s_rxLen < RX_LINE_MAX - 1) {
    s_rxLine[s_rxLen++] = c;
  } else {
    // Dong qua dai, gan nhu chac chan la nhieu duong truyen: bo va lam lai.
    s_rxLen = 0;
  }

  HAL_UART_Receive_IT(s_uart, &s_rxByte, 1);
}

/**
 * Tim "key":"value" va so sanh value voi `expect`.
 *
 * Mau tim gom ca dau hai cham de khong khop nham voi mot gia tri trung ten
 * khoa: trong {"cmd":"mode","mode":"MANUAL"} thi tim "mode": chi ra dung khoa
 * thu hai, vi gia tri "mode" o ve truoc di sau no la dau phay.
 *
 * Chi dung duoc voi JSON khong co khoang trang - dung dinh dang ma ArduinoJson
 * ben ESP32 sinh ra. Du dung cho lenh ngan, khong can keo ca thu vien JSON vao F103.
 */
static uint8_t fieldEquals(const char *json, const char *key, const char *expect) {
  char pattern[32];
  snprintf(pattern, sizeof(pattern), "\"%s\":", key);

  const char *at = strstr(json, pattern);
  if (!at) return 0;

  const char *quote = strchr(at + strlen(pattern), '"');
  if (!quote) return 0;

  return strncmp(quote + 1, expect, strlen(expect)) == 0;
}

uint8_t AgriLink_Poll(void) {
  uint8_t handled = 0;

  // Xu ly het nhung lenh dang cho, khong chi mot lenh moi lan goi.
  while (s_tail != s_head) {
    const char *line = s_queue[s_tail];

    if (fieldEquals(line, "cmd", "pump")) {
      AgriLink_OnPumpCommand(fieldEquals(line, "action", "ON") ? 1 : 0);
      handled = 1;
    } else if (fieldEquals(line, "cmd", "mode")) {
      AgriLink_OnModeCommand(fieldEquals(line, "mode", "MANUAL") ? 1 : 0);
      handled = 1;
    }

    s_tail = (uint8_t)((s_tail + 1) % CMD_QUEUE_LEN);
  }

  return handled;
}
