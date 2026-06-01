@echo off
REM ============================================================
REM QMS 데이터 수집 작업 (Task 1.4) — schtasks(QMS-Refresh)가 1시간 주기로 호출.
REM refresh_job 은 QMS 자격증명이 필요하므로, 코드가 .env 를 자동 로드하지 않는 한
REM 이 배치가 .env 를 프로세스 환경으로 주입한 뒤 실행한다(SYSTEM/서비스 계정 대비).
REM ============================================================
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

REM ----- 경로 (실제 값으로 채우세요) -----
set "REPO=C:\Users\user\Desktop\Coding\cusor\QMS_Integrated_Dashboard"
set "PYTHON=C:\PATH\TO\venv\Scripts\python.exe"
REM ---------------------------------------

cd /d "%REPO%" || (echo [ERROR] REPO 경로 이동 실패: %REPO% & exit /b 1)
if not exist "logs" mkdir "logs"

REM ----- .env 를 환경변수로 로드 (KEY=VALUE, # 주석/빈줄 무시) -----
REM DisableDelayedExpansion + "set VAR=값" 형태라 값에 % 가 있어도(예: 비밀번호) 안전.
if exist ".env" (
  for /f "usebackq eol=# tokens=1* delims==" %%A in (".env") do (
    if not "%%A"=="" set "%%A=%%B"
  )
) else (
  echo [WARN] .env 가 없습니다. 자격증명 미설정으로 수집이 실패할 수 있습니다.
)

echo [%date% %time%] QMS-Refresh 시작 >> "logs\refresh.log"
"%PYTHON%" -m qms_pro.jobs.refresh_job >> "logs\refresh.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] QMS-Refresh 종료 (exit=%RC%) >> "logs\refresh.log"

endlocal & exit /b %RC%
