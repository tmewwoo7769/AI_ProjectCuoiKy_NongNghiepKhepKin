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

  static uint8_t pump_on = 0;
  static uint8_t water_count = 0;
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
	  char uart_buf[110];
	  int uart_len;

	  /* ---- Doc do am dat (ADC) ---- */
	  HAL_ADC_Start(&hadc1);
	  HAL_ADC_PollForConversion(&hadc1, 100);
	  uint32_t soil_adc = HAL_ADC_GetValue(&hadc1);
	  HAL_ADC_Stop(&hadc1);

	  /* ---- Doc anh sang (BH1750) ---- */
	  bh_ok = BH1750_ReadLux(&lux);

	  /* ---- Doc BME680, tinh TinyML, dieu khien bom, hien thi, gui UART ---- */
	  if (BME68x_App_Read(&temp_x100, &pressure_pa, &humidity_x1000, &gas_ohm))
	  {
	      float soil_pct = Soil_ADC_To_Percent(soil_adc);
	      float air_humidity_pct = humidity_x1000 / 1000.0f;
	      float temp_c = temp_x100 / 100.0f;
	      uint8_t tinyml_decision = TinyML_PredictWatering(soil_pct, air_humidity_pct);

	      /* Ne tuoi giua trua nang gat (lux cao + nhiet do cao), tru khi dat qua kho nguy hiem */
	      uint8_t is_midday_sun = (bh_ok && lux > MIDDAY_LUX_THRESHOLD && temp_c > MIDDAY_TEMP_THRESHOLD);
	      if (is_midday_sun && soil_pct > CRITICAL_DRY_PCT) {
	          tinyml_decision = 0;
	      }

	      /* Debounce: phai "can tuoi" DEBOUNCE_COUNT lan lien tiep moi thuc su bat bom */
	      if (tinyml_decision == 1) {
	          if (water_count < 255) water_count++;
	      } else {
	          water_count = 0;
	      }

	      if (!pump_on && water_count >= DEBOUNCE_COUNT)
	      {
	          pump_on = 1;
	          HAL_GPIO_WritePin(RELAY_PUMP_GPIO_Port, RELAY_PUMP_Pin, GPIO_PIN_SET);    /* SET = bat (active-high) */
	      }
	      else if (pump_on && soil_pct >= 40.0f)
	      {
	          pump_on = 0;
	          water_count = 0;
	          HAL_GPIO_WritePin(RELAY_PUMP_GPIO_Port, RELAY_PUMP_Pin, GPIO_PIN_RESET);  /* RESET = tat (active-high) */
	      }

	      ST7735_WriteString(5, 24, "Nhiet do:", Font_7x10, ST7735_YELLOW, ST7735_BLACK);
	      ST7735_FillRectangle(70, 24, 55, 10, ST7735_BLACK);
	      snprintf(line, sizeof(line), "%d.%02dC", temp_x100 / 100, abs(temp_x100 % 100));
	      ST7735_WriteString(70, 24, line, Font_7x10, ST7735_WHITE, ST7735_BLACK);

	      ST7735_WriteString(5, 40, "Do am:", Font_7x10, ST7735_CYAN, ST7735_BLACK);
	      ST7735_FillRectangle(50, 40, 75, 10, ST7735_BLACK);
	      snprintf(line, sizeof(line), "%lu.%01lu%%", humidity_x1000 / 1000, (humidity_x1000 % 1000) / 100);
	      ST7735_WriteString(50, 40, line, Font_7x10, ST7735_WHITE, ST7735_BLACK);

	      ST7735_WriteString(5, 56, "Ap suat:", Font_7x10, ST7735_GREEN, ST7735_BLACK);
	      ST7735_FillRectangle(63, 56, 62, 10, ST7735_BLACK);
	      snprintf(line, sizeof(line), "%luPa", pressure_pa);
	      ST7735_WriteString(63, 56, line, Font_7x10, ST7735_WHITE, ST7735_BLACK);

	      ST7735_WriteString(5, 72, "Gas:", Font_7x10, ST7735_MAGENTA, ST7735_BLACK);
	      ST7735_FillRectangle(36, 72, 89, 10, ST7735_BLACK);
	      snprintf(line, sizeof(line), "%luOhm", gas_ohm);
	      ST7735_WriteString(36, 72, line, Font_7x10, ST7735_WHITE, ST7735_BLACK);

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

	      ST7735_FillRectangle(5, 118, 118, 10, ST7735_BLACK);
	      if (pump_on) {
	          ST7735_WriteString(5, 118, "BOM: DANG TUOI", Font_7x10, ST7735_GREEN, ST7735_BLACK);
	      } else {
	          ST7735_WriteString(5, 118, "BOM: TAT", Font_7x10, ST7735_WHITE, ST7735_BLACK);
	      }

	      uart_len = snprintf(uart_buf, sizeof(uart_buf),
	          "T:%d.%02d,H:%lu.%01lu,P:%lu,G:%lu,SM:%lu,SP:%d,L:%u,PUMP:%d\r\n",
	          temp_x100 / 100, abs(temp_x100 % 100),
	          humidity_x1000 / 1000, (humidity_x1000 % 1000) / 100,
	          pressure_pa, gas_ohm, soil_adc, (int)soil_pct, lux, pump_on);
	      HAL_UART_Transmit(&huart1, (uint8_t*)uart_buf, uart_len, 100);
	  }
	  else
	  {
	      ST7735_WriteString(5, 24, "Doc loi BME680!", Font_7x10, ST7735_RED, ST7735_BLACK);
	  }

	  HAL_Delay(1000);

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
