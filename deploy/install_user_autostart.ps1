<#
.SYNOPSIS
  비관리자(사용자 수준) 자동시작 등록 — NSSM/방화벽 없이 작업 스케줄러만 사용.
  Task 1.4 Step B. 관리자 권한이 없을 때(상승 불가) 사용한다.

.DESCRIPTION
  현재 사용자 계정으로 3개 작업을 등록한다(모두 /RL LIMITED — 관리자 불필요):
    1) QMS-Dashboard-User : 로그온 시 run_app.bat (대시보드 기동, localhost)
    2) QMS-Refresh-User   : 로그온 시 + 1시간마다 run_refresh.bat (데이터 수집)
    3) QMS-Watchdog-User  : 10분마다 run_app.bat (앱 미실행 시 재기동; run_app.bat 이 중복 기동 방지)

  제약(중요): PC 켜짐 + 이 사용자가 로그인한 동안에만 동작한다.
  무로그인 상시 구동 / LAN 공개 / 부팅 즉시(로그온 전) 실행은 관리자·IT 권한이 필요하다.

.NOTES
  실행: powershell -ExecutionPolicy Bypass -File deploy\install_user_autostart.ps1
  되돌리기(아래 3줄):
    schtasks /Delete /TN QMS-Dashboard-User /F
    schtasks /Delete /TN QMS-Refresh-User /F
    schtasks /Delete /TN QMS-Watchdog-User /F
#>

# ===== 파라미터 (현재 호스트 기준으로 채워짐) =====
$REPO    = "C:\Users\user\Desktop\Coding\cusor\QMS_Integrated_Dashboard"
$APP_BAT     = Join-Path $REPO "deploy\run_app.bat"
$REFRESH_BAT = Join-Path $REPO "deploy\run_refresh.bat"
# 앱 작업이 5분 주기로 self-heal(죽으면 재기동) 을 겸하므로 별도 watchdog 작업은 두지 않는다.
# =================================================

$ErrorActionPreference = "Stop"

foreach ($p in @($APP_BAT, $REFRESH_BAT)) {
    if (-not (Test-Path $p)) { Write-Error "배치 파일 없음: $p (먼저 deploy/ 스크립트 경로 확인)"; exit 1 }
}

# native schtasks 인자는 한 줄로(백틱 줄바꿈 뒤 '/' 인자 파서 혼동 방지).
$appTr     = '"' + $APP_BAT + '"'
$refreshTr = '"' + $REFRESH_BAT + '"'

# 기존 작업이 없으면 /Delete 가 stderr 를 내는데, PS 5.1 에서는 그게 NativeCommandError 로
# 승격되어 $ErrorActionPreference='Stop' 에 걸린다. cmd /c 로 감싸 종료코드를 삼킨다(없으면 무시).
function Remove-TaskQuiet([string]$name) {
    & cmd /c "schtasks /Delete /TN $name /F >nul 2>&1"
    $global:LASTEXITCODE = 0
}

# 주의: 이 계정은 /SC ONLOGON 등록이 거부된다(Access denied, 실측). /SC MINUTE·HOURLY 는 가능.
# → 앱은 5분 주기 MINUTE 작업으로 기동(run_app.bat 이 8501 LISTENING 이면 건너뜀 = 중복방지+self-heal).
#   이로써 "로그인 직후 기동 + 죽으면 재기동(watchdog)" 을 한 작업으로 달성한다.
Write-Host "[1/2] QMS-Dashboard-User (5분 주기 기동·self-heal, localhost)..." -ForegroundColor Cyan
Remove-TaskQuiet "QMS-Dashboard-User"
schtasks /Create /TN QMS-Dashboard-User /TR $appTr /SC MINUTE /MO 5 /RL LIMITED /F

Write-Host "[2/2] QMS-Refresh-User (1시간 주기 수집)..." -ForegroundColor Cyan
Remove-TaskQuiet "QMS-Refresh-User"
schtasks /Create /TN QMS-Refresh-User /TR $refreshTr /SC HOURLY /RL LIMITED /F

# (구) 별도 watchdog 작업은 앱 작업(5분 MINUTE)이 self-heal 을 겸하므로 불필요 — 잔재 있으면 제거.
Remove-TaskQuiet "QMS-Watchdog-User"

Write-Host ""
Write-Host "등록 완료. 즉시 기동 테스트:" -ForegroundColor Green
Write-Host "  schtasks /Run /TN QMS-Dashboard-User      # 앱 기동" -ForegroundColor Green
Write-Host "  schtasks /Run /TN QMS-Refresh-User        # 수집 1회" -ForegroundColor Green
Write-Host "  접속: http://127.0.0.1:8501" -ForegroundColor Green
Write-Host ""
Write-Host "등록 목록 확인: schtasks /Query /TN QMS-Dashboard-User /TN QMS-Refresh-User" -ForegroundColor Green
Write-Host "되돌리기: schtasks /Delete /TN QMS-Dashboard-User /F  - Refresh-User / Watchdog-User 동일" -ForegroundColor Yellow
