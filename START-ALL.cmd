@echo off
setlocal
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM  Chay ca he thong: backend du lieu, dich vu AI va giao dien web.
REM
REM   BE_Giamsat   cong 4000 - nhan MQTT tu hai nut, luu MongoDB Atlas
REM   AI_Service   cong 8000 - du bao ARIMA va tro ly LLM, doc du lieu tu 4000
REM   FE_Giam-sat  cong 5173 - giao dien web
REM ---------------------------------------------------------------------------

where node >nul 2>nul
if errorlevel 1 (
  echo Chua tim thay Node.js. Hay cai Node.js 20.19 tro len truoc khi chay.
  pause
  exit /b 1
)

if not exist "BE_Giamsat\.env" (
  echo Chua co tep BE_Giamsat\.env
  echo Hay copy BE_Giamsat\.env.example thanh BE_Giamsat\.env va dien thong tin.
  pause
  exit /b 1
)

echo Dang kiem tra thu vien backend...
call npm --prefix BE_Giamsat install --silent

echo Dang kiem tra thu vien frontend...
call npm --prefix FE_Giam-sat install --silent

echo.
echo [1/3] Mo backend du lieu...
start "1 - Backend du lieu" cmd /k "cd /d %~dp0BE_Giamsat && npm start"

REM Dich vu AI doc du lieu tu backend nen doi backend len truoc.
timeout /t 6 /nobreak >nul

if exist "AI_Service\.venv\Scripts\python.exe" (
  echo [2/3] Mo dich vu AI...
  start "2 - Dich vu AI" cmd /k "cd /d %~dp0AI_Service && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
) else (
  echo [2/3] Bo qua dich vu AI: chua tao moi truong Python.
  echo       Tao bang: cd AI_Service ^&^& python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
)

timeout /t 3 /nobreak >nul

echo [3/3] Mo frontend...
start "3 - Frontend" cmd /k "cd /d %~dp0FE_Giam-sat && npm run dev"

echo.
echo ===========================================================
echo   Frontend    http://localhost:5173
echo   Backend     http://localhost:4000/api/health
echo   Dich vu AI  http://127.0.0.1:8000/docs
echo ===========================================================
echo.
echo Dong cua so nay khong tat cac tien trinh tren.
echo Muon dung het thi dong cac cua so vua mo.
pause
