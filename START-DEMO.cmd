@echo off
setlocal
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM  Chay thu ca he thong voi node gia lap, khong can cam phan cung.
REM  Mo 4 cua so: broker MQTT -> backend -> node gia lap -> frontend
REM
REM  Khi da cam phan cung that thi dung START-ALL.cmd, va nho doi .env
REM  sang HiveMQ Cloud (xem BE_Giamsat/.env, phan GIAI DOAN 2).
REM ---------------------------------------------------------------------------

where node >nul 2>nul
if errorlevel 1 (
  echo Chua tim thay Node.js. Hay cai Node.js 20.19 tro len truoc khi chay.
  pause
  exit /b 1
)

if not exist "BE_Giamsat\.env" (
  echo Chua co tep BE_Giamsat\.env
  echo Hay copy BE_Giamsat\.env.example thanh BE_Giamsat\.env va dien MONGO_URL.
  pause
  exit /b 1
)

findstr /C:"<cluster>" "BE_Giamsat\.env" >nul 2>nul
if not errorlevel 1 (
  echo CANH BAO: .env con dong MQTT_URL de giu cho ^<cluster^>.
  echo Neu dang chay thu voi node gia lap thi bo qua canh bao nay.
  echo.
)

findstr /C:"127.0.0.1:27017" "BE_Giamsat\.env" >nul 2>nul
if not errorlevel 1 (
  echo LOI: MONGO_URL van dang tro toi MongoDB tren may, nhung may chua cai MongoDB.
  echo Hay dan connection string cua MongoDB Atlas vao BE_Giamsat\.env truoc.
  echo Dang: mongodb+srv://user:pass@cluster.mongodb.net/agri_ecosystem?retryWrites=true^&w=majority
  pause
  exit /b 1
)

echo Dang kiem tra thu vien...
call npm --prefix BE_Giamsat install --silent
call npm --prefix FE_Giam-sat install --silent

echo.

REM Chi can broker cuc bo khi .env dang tro toi localhost. Neu da chuyen sang
REM HiveMQ Cloud thi bo qua buoc nay, backend noi thang len cloud.
findstr /R /C:"^MQTT_URL=mqtt://localhost" "BE_Giamsat\.env" >nul 2>nul
if errorlevel 1 (
  echo [1/4] Dang dung MQTT broker tren cloud, bo qua broker cuc bo.
) else (
  echo [1/4] Mo broker MQTT cuc bo...
  start "1 - MQTT Broker" cmd /k "cd /d %~dp0BE_Giamsat && npm run broker"
  REM Backend can broker san sang truoc khi noi, nen cho mot nhip.
  timeout /t 3 /nobreak >nul
)

echo [2/4] Mo backend...
start "2 - Backend" cmd /k "cd /d %~dp0BE_Giamsat && npm start"

timeout /t 5 /nobreak >nul

echo [3/4] Mo node gia lap (Node 1 + Node 2)...
start "3 - Node gia lap" cmd /k "cd /d %~dp0BE_Giamsat && npm run fake"

echo [4/4] Mo frontend...
start "4 - Frontend" cmd /k "cd /d %~dp0FE_Giam-sat && npm run dev"

echo.
echo ===========================================================
echo   Frontend:  http://localhost:5173
echo   Backend:   http://localhost:4000/api/health
echo ===========================================================
echo.
echo Dong cua so nay khong tat 4 tien trinh tren.
echo Muon dung het thi dong ca 4 cua so vua mo.
pause
