/*
 * VI DU GHEP agri_link VAO main.c CUA STM32 (Node 1)
 * ===========================================================================
 * File nay KHONG nap truc tiep. No chi ra chinh xac nhung doan can them vao
 * main.c san co cua nhom. Cac ten ham doc cam bien o day la ten gia dinh -
 * thay bang ten that trong code cua nhom.
 *
 * Tong cong co 5 cho phai sua, danh so [1] den [5] ben duoi.
 */

/* ==========================================================================
 * [1] THEM #include
 * Dat trong khoi  USER CODE BEGIN Includes
 * ========================================================================== */

#include "agri_link.h"

/* ==========================================================================
 * [2] THEM BIEN TRANG THAI
 * Dat trong khoi  USER CODE BEGIN PV  (private variables)
 * ========================================================================== */

/* Trang thai relay THUC TE dang dat. Day chinh la gia tri backend dua vao de
 * ghi nhat ky bat/tat bom, nen phai luon dung voi chan GPIO. */
static uint8_t pumpOn = 0;

/* 0 = cay quyet dinh tu chay (AUTO), 1 = nguoi dung giu quyen (MANUAL).
 * Day la co "tam ngung" - thieu no thi cay quyet dinh se de len lenh nguoi
 * dung ngay chu ky sau, nut bam tren web thanh vo tac dung. */
static uint8_t manualMode = 0;

/* Ket qua cay quyet dinh lan gan nhat, de khi tra quyen ve AUTO thi ap dung
 * duoc ngay ma khong phai doi het mot chu ky. */
static uint8_t lastDecision = 0;

/* ==========================================================================
 * [3] THEM HAI HAM XU LY LENH
 * Dat trong khoi  USER CODE BEGIN 0  (truoc ham main)
 *
 * Hai ham nay do agri_link goi khi nhan duoc lenh tu web.
 * ========================================================================== */

/** Dong/ngat relay va cap nhat trang thai. Gom mot cho de khong bi lech. */
static void setPump(uint8_t on) {
  pumpOn = on;
  /* === TODO === Doi RELAY_GPIO_Port / RELAY_Pin cho khop CubeMX cua nhom.
   * Neu relay kich muc thap thi dao lai SET va RESET. */
  HAL_GPIO_WritePin(RELAY_GPIO_Port, RELAY_Pin, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

void AgriLink_OnPumpCommand(uint8_t on) {
  /* Dang o AUTO thi cay quyet dinh giu quyen, bo qua lenh.
   * Backend cung da chan truoc buoc nay, day la lop chan thu hai. */
  if (!manualMode) return;
  setPump(on);
}

void AgriLink_OnModeCommand(uint8_t manual) {
  manualMode = manual;

  /* Tra quyen ve AUTO thi ap dung ngay ket qua cay quyet dinh moi nhat. */
  if (!manual) setPump(lastDecision);
}

/* ==========================================================================
 * [4] SUA HAM main()
 * ========================================================================== */

int main(void) {
  HAL_Init();
  SystemClock_Config();
  MX_GPIO_Init();
  MX_I2C1_Init();
  MX_ADC1_Init();
  MX_USART1_UART_Init();  /* USART1 = PA9/PA10, noi sang ESP32 */
  MX_SPI1_Init();         /* man hinh TFT */

  /* --- USER CODE BEGIN 2 --- */

  /* ...code khoi tao BME680, BH1750, TFT san co cua nhom... */

  setPump(0);                 /* dam bao bom tat luc khoi dong */
  AgriLink_Init(&huart1);     /* THEM DONG NAY - doi huart1 cho khop */

  /* --- USER CODE END 2 --- */

  while (1) {
    /* --- USER CODE BEGIN WHILE --- */

    /* ...doc cam bien nhu code san co cua nhom... */
    AgriSensors s;
    s.temp_c       = BME680_ReadTemperature();
    s.humid_pct    = BME680_ReadHumidity();
    s.pressure_hpa = BME680_ReadPressure();
    s.gas_kohm     = BME680_ReadGasResistance();
    s.soil_raw     = Soil_ReadRaw();
    s.soil_pct     = Soil_RawToPercent(s.soil_raw);
    s.lux          = BH1750_ReadLux();

    /* Cay quyet dinh da nhung - ham nay la code C sinh ra tu mo hinh
     * scikit-learn, tra ve 1 neu nen bat bom. */
    lastDecision = TinyML_Predict(s.soil_pct, s.temp_c, s.humid_pct, s.lux);

    /* SUA CHO NAY: truoc day chac chan la goi thang setPump(lastDecision).
     * Gio phai boc trong dieu kien manualMode, neu khong thi lenh tu web
     * se bi ghi de ngay o chu ky ke tiep. */
    if (!manualMode) {
      setPump(lastDecision);
    }

    /* ...ve man hinh TFT nhu cu... */
    TFT_ShowStatus(s.temp_c, s.humid_pct, s.soil_pct, pumpOn, manualMode);

    /* THEM DONG NAY: gui len ESP32 de day tiep len MQTT. */
    AgriLink_Send(&s, pumpOn, manualMode, lastDecision);

    /* THAY HAL_Delay(5000) BANG VONG CHO NAY.
     *
     * Cho du 5 giay nhung cu 50 ms lai ngo xem co lenh tu web khong. Neu de
     * HAL_Delay(5000) thi lenh nguoi dung bam phai nam cho toi 5 giay moi duoc
     * thuc hien - bam nut xong ngoi doi, tuong nhu bi treo.
     *
     * Va khi vua thuc hien lenh thi bao trang thai moi len ngay, khong doi het
     * chu ky. Backend dua vao ban tin nay de xac nhan lenh da chay, nen web se
     * doi trang thai gan nhu tuc thi. */
    uint32_t cycleStart = HAL_GetTick();
    while (HAL_GetTick() - cycleStart < 5000) {
      if (AgriLink_Poll()) {
        AgriLink_Send(&s, pumpOn, manualMode, lastDecision);
      }
      HAL_Delay(50);
    }

    /* --- USER CODE END WHILE --- */
  }
}

/* ==========================================================================
 * [5] THEM CALLBACK NGAT UART
 * Dat trong khoi  USER CODE BEGIN 4  (cuoi main.c)
 *
 * Neu du an da co san ham nay cho UART khac thi chi them nhanh if ben trong,
 * dung khai bao them mot ham thu hai - se bi loi trung ten luc lien ket.
 * ========================================================================== */

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
  if (huart->Instance == USART1) {
    AgriLink_RxByte();
  }
}
