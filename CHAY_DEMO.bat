@echo off
title Khoi dong He thong Eureka
echo Dang don dep cac tien trinh cu...
taskkill /F /IM node.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
echo Da don dep xong!

echo.
echo [1/4] Dang mo Backend (Cong 4000)...
start "1 - Backend" cmd /k "cd /d D:\Eureka\BE_Giamsat && npm start"
timeout /t 3 /nobreak >nul

echo [2/4] Dang mo Fake Data (Tao du lieu ao)...
start "2 - Fake Data" cmd /k "cd /d D:\Eureka\BE_Giamsat && npm run fake"

echo [3/4] Dang mo AI Service (Cong 8000)...
start "3 - AI Service" cmd /k "cd /d D:\Eureka\AI_Service && .venv\Scripts\activate && uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo [4/4] Dang mo Frontend (Cong 5173)...
start "4 - Frontend" cmd /k "cd /d D:\Eureka\FE_Giamsat && npm run dev"

echo.
echo =======================================================
echo DA KHOI DONG XONG 4 TIEN TRINH!
echo Hay doi 5 giay roi mo trinh duyet truy cap:
echo http://localhost:5173
echo =======================================================
pause