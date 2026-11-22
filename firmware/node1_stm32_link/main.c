/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "st7735.h"
#include "bme68x_app.h"
#include "bh1750.h"
#include <stdio.h>
#include <stdlib.h>

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "agri_link.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define SOIL_ADC_DRY_AIR       4000.0f
#define SOIL_ADC_WET_WATER     1500.0f
#define DEBOUNCE_COUNT         3        /* phai lien tuc "can tuoi" 3 lan (~3s) moi bat bom, chong nhieu */
#define MIDDAY_LUX_THRESHOLD   20000    /* lux, coi la nang gat truc tiep */
#define MIDDAY_TEMP_THRESHOLD  33.0f    /* do C */
#define CRITICAL_DRY_PCT       15.0f    /* duoi muc nay thi tuoi bat chap dang nang gat */

/* Vong lap van chay 1 giay mot lan de debounce va man hinh phan hoi nhanh,
 * nhung khong gui len server moi giay. Ly do: MongoDB Atlas goi mien phi chi
 * co 512 MB, gui 1 giay/lan la 86.400 ban ghi moi ngay, day o trong khoang hai
 * tuan. Gui 5 giay/lan van du muot cho bieu do ma nhe hon 5 lan. */
#define SEND_EVERY_N_CYCLES    5
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
ADC_HandleTypeDef hadc1;

I2C_HandleTypeDef hi2c1;

SPI_HandleTypeDef hspi1;

UART_HandleTypeDef huart1;

/* USER CODE BEGIN PV */
/* Cac bien nay truoc day nam trong main(). Phai dua ra day vi hai ham
 * AgriLink_OnPumpCommand / AgriLink_OnModeCommand can doc va sua chung. */
static uint8_t pump_on = 0;
static uint8_t water_count = 0;

/* 0 = cay quyet dinh tu chay (AUTO), 1 = nguoi dung tren web giu quyen (MANUAL).
 * Day la co "tam ngung": khi bang 1 thi khoi dieu khien bom tu dong o vong lap
 * chinh bi bo qua, neu khong thi lenh nguoi dung se bi ghi de sau mot giay. */
static uint8_t manual_mode = 0;

/* Anh chup lan doc gan nhat, de khi vua nhan lenh thi bao trang thai moi len
 * server ngay ma khong phai doi het chu ky. */
static AgriSensors last_sensors;
static uint8_t last_decision = 0;
static uint8_t have_reading = 0;
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_ADC1_Init(void);
static void MX_I2C1_Init(void);
static void MX_SPI1_Init(void);
static void MX_USART1_UART_Init(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
float Soil_ADC_To_Percent(uint32_t adc_raw)
{
    float pct = (SOIL_ADC_DRY_AIR - (float)adc_raw) / (SOIL_ADC_DRY_AIR - SOIL_ADC_WET_WATER) * 100.0f;
    if (pct < 0.0f)   pct = 0.0f;
    if (pct > 100.0f) pct = 100.0f;
    return pct;
}

/* TinyML: Decision Tree ban cung train (t_AI.ipynb, accuracy 100% tren du lieu mo phong).
 * Chi can 2 dac trung: do am dat (%) va do am khong khi (%). */
uint8_t TinyML_PredictWatering(float soil_pct, float air_humidity_pct)
{
    if (soil_pct <= 35.01f) {
        if (air_humidity_pct <= 80.04f) {
            return 1; /* WATER */
        } else {
            if (soil_pct <= 25.92f) {
                return 1; /* WATER */
            } else {
                return 0; /* NO_WATER */
            }
        }
    } else {
        return 0; /* NO_WATER */
    }
}

/* Gom viec dong/ngat relay vao mot cho de trang thai va chan GPIO khong bao gio lech. */
static void Pump_Apply(uint8_t on)
{
    pump_on = on;
    HAL_GPIO_WritePin(RELAY_PUMP_GPIO_Port, RELAY_PUMP_Pin,
                      on ? GPIO_PIN_SET : GPIO_PIN_RESET);  /* SET = bat (active-high) */
}

/* ---- Hai ham duoi day do agri_link goi khi nhan duoc lenh tu web ---- */

void AgriLink_OnPumpCommand(uint8_t on)
{
    /* Dang o AUTO thi cay quyet dinh giu quyen. Backend cung da chan truoc buoc
     * nay, day la lop chan thu hai ngay tren thiet bi. */
    if (!manual_mode) return;

    Pump_Apply(on);
    water_count = 0;
}

void AgriLink_OnModeCommand(uint8_t manual)
{
    manual_mode = manual;

    /* Dat lai bo dem debounce o ca hai chieu: tra quyen ve AUTO thi cay quyet
     * dinh phai "can tuoi" du DEBOUNCE_COUNT lan lien tiep moi duoc bat bom,
     * khong duoc bat ngay bang bo dem cu tu truoc luc chuyen sang THU CONG. */
    water_count = 0;
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_ADC1_Init();
  MX_I2C1_Init();
  MX_SPI1_Init();
  MX_USART1_UART_Init();
  /* USER CODE BEGIN 2 */

  /* Bat duong truyen len ESP32 TRUOC KHI khoi tao man hinh va cam bien.
   *
   * Ly do dat o day chu khong dat cuoi: neu mot ham khoi tao cam bien bi treo
   * (vi du bus I2C bi keo xuong thap) thi chuong trinh khong bao gio chay toi
   * vong lap, va ESP32 se im lang y het nhu khi dut day. Gui dong chao ngay tu
   * dau thi phan biet duoc hai truong hop do:
   *   ESP32 thay "AGRI_LINK_READY" -> day va baud dung, loi nam o cho khac
   *   ESP32 khong thay gi          -> loi o duong truyen hoac STM32 chua chay
   */
  AgriLink_Init(&huart1);   /* ham nay tu gui "AGRI_LINK_READY" */

  ST7735_Init();
  ST7735_FillScreen(ST7735_BLACK);

  /* Thanh tieu de */
  ST7735_FillRectangle(0, 0, 128, 16, ST7735_COLOR565(0, 90, 60));
  ST7735_WriteString(10, 3, "NODE 1 - VUON", Font_7x10, ST7735_WHITE, ST7735_COLOR565(0, 90, 60));

  uint8_t bme_ok = BME68x_App_Init();
  if (!bme_ok) {
      ST7735_WriteString(5, 30, "BME680 FAIL!", Font_7x10, ST7735_RED, ST7735_BLACK);
  }

  BH1750_Init();

  /* Toi day la da qua duoc phan khoi tao cam bien. Neu ESP32 thay dong nay ma
   * khong thay "AGRI_LINK_READY" thi... khong the xay ra; nhung neu thay dong
   * READY ma khong thay dong nay thi mot trong ba ham Init o tren dang treo. */
  AgriLink_SendText("AGRI_SENSORS_OK");

  uint8_t send_tick = 0;
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
	  int16_t temp_x100;
	  uint32_t pressure_pa, humidity_x1000, gas_ohm;
	  uint16_t lux = 0;
	  uint8_t bh_ok;
	  char line[16];

	  /* ---- Doc do am dat (ADC) ---- */
	  HAL_ADC_Start(&hadc1);
	  HAL_ADC_PollForConversion(&hadc1, 100);
	  uint32_t soil_adc = HAL_ADC_GetValue(&hadc1);
	  HAL_ADC_Stop(&hadc1);

	  /* ---- Doc anh sang (BH1750) ---- */
	  bh_ok = BH1750_ReadLux(&lux);

	  /* ---- Doc BME680, tinh TinyML, dieu khien bom, hien thi, gui UART ---- */
	  uint8_t bme_read_ok = BME68x_App_Read(&temp_x100, &pressure_pa, &humidity_x1000, &gas_ohm);

	  {
	      float soil_pct = Soil_ADC_To_Percent(soil_adc);
	      float air_humidity_pct = bme_read_ok ? (humidity_x1000 / 1000.0f) : 0.0f;
	      float temp_c = bme_read_ok ? (temp_x100 / 100.0f) : 0.0f;

	      /* BME680 hong thi khong con do am khong khi de dua vao cay quyet dinh.
	       * Chi con mot dac trung la do am dat, nen quyet dinh theo dung nguong
	       * goc cua cay (35%) - an toan hon la doan bua bang gia tri 0. */
	      uint8_t tinyml_decision = bme_read_ok
	          ? TinyML_PredictWatering(soil_pct, air_humidity_pct)
	          : (soil_pct <= 35.01f ? 1 : 0);

	      /* Ne tuoi giua trua nang gat (lux cao + nhiet do cao), tru khi dat qua kho nguy hiem.
	       * Khong co nhiet do that thi khong the ket luan la nang gat, bo qua luat nay. */
	      uint8_t is_midday_sun = (bme_read_ok && bh_ok
	                               && lux > MIDDAY_LUX_THRESHOLD && temp_c > MIDDAY_TEMP_THRESHOLD);
	      if (is_midday_sun && soil_pct > CRITICAL_DRY_PCT) {
	          tinyml_decision = 0;
	      }

	      /* Debounce: phai "can tuoi" DEBOUNCE_COUNT lan lien tiep moi thuc su bat bom */
	      if (tinyml_decision == 1) {
	          if (water_count < 255) water_count++;
	      } else {
	          water_count = 0;
	      }

	      /* CHO SUA QUAN TRONG: toan bo khoi dieu khien tu dong chi chay khi dang
	       * o che do AUTO. Thieu dieu kien nay thi nguoi dung bam "Bat bom" tren
	       * web xong, mot giay sau vong lap nay ghi de lai ngay - nut bam tren web
	       * thanh vo tac dung ma khong bao loi gi. */
	      if (!manual_mode)
	      {
	          if (!pump_on && water_count >= DEBOUNCE_COUNT)
	          {
	              Pump_Apply(1);
	          }
	          else if (pump_on && soil_pct >= 40.0f)
	          {
	              Pump_Apply(0);
	              water_count = 0;
	          }
	      }

	      /* Bon dong duoi lay tu BME680. Doc loi thi hien chu "LOI" mau do chu
	       * khong in bien chua khoi tao ra man hinh - so rac con kho hieu hon la
	       * bao loi thang. */
	      uint16_t env_color = bme_read_ok ? ST7735_WHITE : ST7735_RED;

	      ST7735_WriteString(5, 24, "Nhiet do:", Font_7x10, ST7735_YELLOW, ST7735_BLACK);
	      ST7735_FillRectangle(70, 24, 55, 10, ST7735_BLACK);
	      if (bme_read_ok) snprintf(line, sizeof(line), "%d.%02dC", temp_x100 / 100, abs(temp_x100 % 100));
	      else             snprintf(line, sizeof(line), "LOI");
	      ST7735_WriteString(70, 24, line, Font_7x10, env_color, ST7735_BLACK);

	      ST7735_WriteString(5, 40, "Do am:", Font_7x10, ST7735_CYAN, ST7735_BLACK);
	      ST7735_FillRectangle(50, 40, 75, 10, ST7735_BLACK);
	      if (bme_read_ok) snprintf(line, sizeof(line), "%lu.%01lu%%", humidity_x1000 / 1000, (humidity_x1000 % 1000) / 100);
	      else             snprintf(line, sizeof(line), "LOI");
	      ST7735_WriteString(50, 40, line, Font_7x10, env_color, ST7735_BLACK);

	      ST7735_WriteString(5, 56, "Ap suat:", Font_7x10, ST7735_GREEN, ST7735_BLACK);
	      ST7735_FillRectangle(63, 56, 62, 10, ST7735_BLACK);
	      if (bme_read_ok) snprintf(line, sizeof(line), "%luPa", pressure_pa);
	      else             snprintf(line, sizeof(line), "LOI BME680");
	      ST7735_WriteString(63, 56, line, Font_7x10, env_color, ST7735_BLACK);

	      ST7735_WriteString(5, 72, "Gas:", Font_7x10, ST7735_MAGENTA, ST7735_BLACK);
	      ST7735_FillRectangle(36, 72, 89, 10, ST7735_BLACK);
	      if (bme_read_ok) snprintf(line, sizeof(line), "%luOhm", gas_ohm);
	      else             snprintf(line, sizeof(line), "LOI");
	      ST7735_WriteString(36, 72, line, Font_7x10, env_color, ST7735_BLACK);

	      ST7735_WriteString(5, 88, "Dat:", Font_7x10, ST7735_COLOR565(255,140,0), ST7735_BLACK);
	      ST7735_FillRectangle(36, 88, 89, 10, ST7735_BLACK);
	      snprintf(line, sizeof(line), "%d%%", (int)soil_pct);
	      ST7735_WriteString(36, 88, line, Font_7x10, ST7735_WHITE, ST7735_BLACK);

	      ST7735_WriteString(5, 104, "Sang:", Font_7x10, ST7735_COLOR565(255,255,0), ST7735_BLACK);
	      ST7735_FillRectangle(48, 104, 77, 10, ST7735_BLACK);
	      if (bh_ok) {
	          snprintf(line, sizeof(line), "%ulx", lux);
	      } else {
	          snprintf(line, sizeof(line), "LOI I2C");
	      }
	      ST7735_WriteString(48, 104, line, Font_7x10, ST7735_WHITE, ST7735_BLACK);

	      /* Dong cuoi hien them "[TAY]" khi web dang giu quyen dieu khien, de nhin
	       * man hinh la biet ngay bom dang do TinyML hay do nguoi dung quyet dinh. */
	      ST7735_FillRectangle(5, 118, 118, 10, ST7735_BLACK);
	      if (pump_on) {
	          ST7735_WriteString(5, 118, manual_mode ? "BOM: TUOI [TAY]" : "BOM: DANG TUOI",
	                             Font_7x10, ST7735_GREEN, ST7735_BLACK);
	      } else {
	          ST7735_WriteString(5, 118, manual_mode ? "BOM: TAT [TAY]" : "BOM: TAT",
	                             Font_7x10, ST7735_WHITE, ST7735_BLACK);
	      }

	      /* ---- Dong goi cho ESP32 ----
	       * Doi don vi cho khop voi ban tin MQTT ma backend mong doi:
	       *   BME680 tra ap suat bang Pa   -> chia 100 ra hPa
	       *   BME680 tra dien tro khi Ohm  -> chia 1000 ra kOhm */
	      last_sensors.temp_c       = temp_c;
	      last_sensors.humid_pct    = air_humidity_pct;
	      last_sensors.pressure_hpa = bme_read_ok ? (pressure_pa / 100.0f) : 0.0f;
	      last_sensors.gas_kohm     = bme_read_ok ? (gas_ohm / 1000.0f) : 0.0f;
	      last_sensors.soil_pct     = soil_pct;
	      last_sensors.soil_raw     = (uint16_t)soil_adc;
	      last_sensors.lux          = (float)lux;
	      last_sensors.env_ok       = bme_read_ok;
	      last_sensors.light_ok     = bh_ok;
	      last_decision             = tinyml_decision;
	      have_reading              = 1;

	      /* Gui ke ca khi BME680 loi. Node im lang thi web bao Offline va khong
	       * ai biet la mat dien, dut day hay chi hong mot con cam bien. */
	      if (++send_tick >= SEND_EVERY_N_CYCLES) {
	          send_tick = 0;
	          AgriLink_Send(&last_sensors, pump_on, manual_mode, last_decision);
	      }
	  }

	  /* ---- Cho het chu ky, nhung van ngo lenh tu web ----
	   * Truoc day cho la HAL_Delay(1000). Neu giu nguyen thi lenh nguoi dung bam
	   * phai nam cho toi mot giay moi duoc thuc hien. Cu 50 ms ngo mot lan thi do
	   * tre xuong duoi 0,1 giay, va vua thuc hien xong la bao trang thai moi len
	   * server ngay - web doi mau nut gan nhu tuc thi. */
	  uint32_t cycle_start = HAL_GetTick();
	  while (HAL_GetTick() - cycle_start < 1000)
	  {
	      if (AgriLink_Poll() && have_reading) {
	          AgriLink_Send(&last_sensors, pump_on, manual_mode, last_decision);
	          send_tick = 0;  /* vua gui roi thi tinh lai chu ky, khoi gui doi */
	      }
	      HAL_Delay(50);
	  }

    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};
  RCC_PeriphCLKInitTypeDef PeriphClkInit = {0};

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.HSEPredivValue = RCC_HSE_PREDIV_DIV1;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLMUL = RCC_PLL_MUL9;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
  PeriphClkInit.PeriphClockSelection = RCC_PERIPHCLK_ADC;
  PeriphClkInit.AdcClockSelection = RCC_ADCPCLK2_DIV6;
  if (HAL_RCCEx_PeriphCLKConfig(&PeriphClkInit) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief ADC1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_ADC1_Init(void)
{

  /* USER CODE BEGIN ADC1_Init 0 */

  /* USER CODE END ADC1_Init 0 */

  ADC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN ADC1_Init 1 */

  /* USER CODE END ADC1_Init 1 */

  /** Common config
  */
  hadc1.Instance = ADC1;
  hadc1.Init.ScanConvMode = ADC_SCAN_DISABLE;
  hadc1.Init.ContinuousConvMode = DISABLE;
  hadc1.Init.DiscontinuousConvMode = DISABLE;
  hadc1.Init.ExternalTrigConv = ADC_SOFTWARE_START;
  hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc1.Init.NbrOfConversion = 1;
  if (HAL_ADC_Init(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Regular Channel
  */
  sConfig.Channel = ADC_CHANNEL_0;
  sConfig.Rank = ADC_REGULAR_RANK_1;
  sConfig.SamplingTime = ADC_SAMPLETIME_1CYCLE_5;
  if (HAL_ADC_ConfigChannel(&hadc1, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC1_Init 2 */

  /* USER CODE END ADC1_Init 2 */

}

/**
  * @brief I2C1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_I2C1_Init(void)
{

  /* USER CODE BEGIN I2C1_Init 0 */

  /* USER CODE END I2C1_Init 0 */

  /* USER CODE BEGIN I2C1_Init 1 */

  /* USER CODE END I2C1_Init 1 */
  hi2c1.Instance = I2C1;
  hi2c1.Init.ClockSpeed = 100000;
  hi2c1.Init.DutyCycle = I2C_DUTYCYCLE_2;
  hi2c1.Init.OwnAddress1 = 0;
  hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
  hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
  hi2c1.Init.OwnAddress2 = 0;
  hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
  hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
  if (HAL_I2C_Init(&hi2c1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN I2C1_Init 2 */

  /* USER CODE END I2C1_Init 2 */

}

/**
  * @brief SPI1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_SPI1_Init(void)
{

  /* USER CODE BEGIN SPI1_Init 0 */

  /* USER CODE END SPI1_Init 0 */

  /* USER CODE BEGIN SPI1_Init 1 */

  /* USER CODE END SPI1_Init 1 */
  /* SPI1 parameter configuration*/
  hspi1.Instance = SPI1;
  hspi1.Init.Mode = SPI_MODE_MASTER;
  hspi1.Init.Direction = SPI_DIRECTION_2LINES;
  hspi1.Init.DataSize = SPI_DATASIZE_8BIT;
  hspi1.Init.CLKPolarity = SPI_POLARITY_LOW;
  hspi1.Init.CLKPhase = SPI_PHASE_1EDGE;
  hspi1.Init.NSS = SPI_NSS_SOFT;
  hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_64;
  hspi1.Init.FirstBit = SPI_FIRSTBIT_MSB;
  hspi1.Init.TIMode = SPI_TIMODE_DISABLE;
  hspi1.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE;
  hspi1.Init.CRCPolynomial = 10;
  if (HAL_SPI_Init(&hspi1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN SPI1_Init 2 */

  /* USER CODE END SPI1_Init 2 */

}

/**
  * @brief USART1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART1_UART_Init(void)
{

  /* USER CODE BEGIN USART1_Init 0 */

  /* USER CODE END USART1_Init 0 */

  /* USER CODE BEGIN USART1_Init 1 */

  /* USER CODE END USART1_Init 1 */
  huart1.Instance = USART1;
  huart1.Init.BaudRate = 115200;
  huart1.Init.WordLength = UART_WORDLENGTH_8B;
  huart1.Init.StopBits = UART_STOPBITS_1;
  huart1.Init.Parity = UART_PARITY_NONE;
  huart1.Init.Mode = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART1_Init 2 */

  /* USER CODE END USART1_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOA, TFT_DC_Pin|TFT_RST_Pin|TFT_CS_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(RELAY_PUMP_GPIO_Port, RELAY_PUMP_Pin, GPIO_PIN_RESET);  /* RESET = tat luc khoi dong (active-high) */

  /*Configure GPIO pins : TFT_DC_Pin TFT_RST_Pin TFT_CS_Pin */
  GPIO_InitStruct.Pin = TFT_DC_Pin|TFT_RST_Pin|TFT_CS_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /*Configure GPIO pin : RELAY_PUMP_Pin */
  GPIO_InitStruct.Pin = RELAY_PUMP_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(RELAY_PUMP_GPIO_Port, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
/* Ngat UART bao da nhan duoc mot byte tu ESP32. agri_link gom cac byte lai
 * thanh dong roi de vong lap chinh xu ly, khong lam viec nang trong ngat. */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1) {
        AgriLink_RxByte();
    }
}
/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
