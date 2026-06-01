@echo off
REM ============================================================
REM QMS 대시보드 앱 실행 (Task 1.4 Step B — 비관리자 자동시작용).
REM 작업 스케줄러 "QMS-Dashboard-User"(로그온 트리거)가 이 배치를 호출한다.
REM 앱은 cache_only 라 자격증명 불필요 → .env 주입 없이 streamlit 만 구동.
REM localhost(127.0.0.1) 바인딩 — LAN 공개는 관리자/IT 필요(보류).
REM ============================================================
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

REM ----- 경로 (실제 값으로 채워짐) -----
set "REPO=C:\Users\user\Desktop\Coding\cusor\QMS_Integrated_Dashboard"
set "PYTHON=C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe"
REM -------------------------------------

cd /d "%REPO%" || (echo [ERROR] REPO 경로 이동 실패: %REPO% & exit /b 1)
if not exist "logs" mkdir "logs"

REM 이미 8501 이 떠 있으면 중복 기동 방지(watchdog 재호출 대비).
netstat -ano | findstr /R /C:"127.0.0.1:8501 .*LISTENING" >nul 2>&1
if %ERRORLEVEL%==0 (
  echo [%date% %time%] 이미 8501 LISTENING — 기동 생략 >> "logs\app.log"
  endlocal & exit /b 0
)

echo [%date% %time%] QMS-Dashboard 기동 >> "logs\app.log"
"%PYTHON%" -m streamlit run QMS_Integrated_Dashboard_v2.py --server.address 127.0.0.1 --server.port 8501 --server.headless true >> "logs\app.log" 2>&1
endlocal & exit /b %ERRORLEVEL%
