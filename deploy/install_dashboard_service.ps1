<#
.SYNOPSIS
  QMS 통합 대시보드를 NSSM 윈도우 서비스로 등록한다(상시 구동 + 자동 재시작).
  Task 1.4 (서비스화). 관리자 PowerShell에서 실행.

.DESCRIPTION
  대시보드는 Task 1.2 이후 cache_only 로 동작한다(로컬 .qms_cache 만 읽음).
  → 이 서비스는 QMS 자격증명이 필요 없다. 데이터 수집(자격증명 필요)은
    QMS-Refresh 스케줄 작업(install_refresh_task.ps1)이 담당한다.

  실제 등록 전, 아래 ===== 파라미터 ===== 의 경로 4곳을 채워야 한다.

.NOTES
  - 실행 정책: powershell -ExecutionPolicy Bypass -File deploy\install_dashboard_service.ps1
  - 제거:     nssm remove QMS-Dashboard confirm
  - 재시작:   nssm restart QMS-Dashboard   (코드 업데이트 후)
#>

# ===== 파라미터 (실제 경로로 채우세요) =====
$REPO   = "C:\Users\user\Desktop\Coding\cusor\QMS_Integrated_Dashboard"   # 레포 루트(절대경로)
$PYTHON = "C:\PATH\TO\venv\Scripts\python.exe"                            # venv python.exe 절대경로
$NSSM   = "C:\PATH\TO\nssm.exe"                                           # nssm.exe 절대경로
$PORT   = 8501
$BIND   = "127.0.0.1"   # 기본: 이 PC에서만 접속. LAN 공개는 deploy\enable_lan.ps1 (의식적으로)
$SERVICE = "QMS-Dashboard"
# ============================================

$ErrorActionPreference = "Stop"

# --- 사전 검증 ---
if ($PYTHON -like "*PATH\TO*" -or $NSSM -like "*PATH\TO*") {
    Write-Error "먼저 스크립트 상단의 `$PYTHON / `$NSSM 경로를 실제 값으로 채우세요."
    exit 1
}
foreach ($p in @($REPO, $PYTHON, $NSSM)) {
    if (-not (Test-Path $p)) { Write-Error "경로가 존재하지 않습니다: $p"; exit 1 }
}
$entry = Join-Path $REPO "QMS_Integrated_Dashboard_v2.py"
if (-not (Test-Path $entry)) { Write-Error "엔트리포인트 없음: $entry"; exit 1 }

# --- 로그 폴더 ---
$logDir = Join-Path $REPO "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

Write-Host "[1/4] 기존 서비스가 있으면 제거..." -ForegroundColor Cyan
& $NSSM stop $SERVICE 2>$null
& $NSSM remove $SERVICE confirm 2>$null

Write-Host "[2/4] 서비스 등록 (streamlit, headless)..." -ForegroundColor Cyan
# 대시보드는 cache_only → 자격증명 불필요. headless=true 로 프롬프트/브라우저 자동열기 방지.
& $NSSM install $SERVICE $PYTHON -m streamlit run $entry `
    --server.port $PORT --server.address $BIND --server.headless true

Write-Host "[3/4] 서비스 옵션 설정 (작업폴더/로그/자동시작/재시작)..." -ForegroundColor Cyan
& $NSSM set $SERVICE AppDirectory $REPO
& $NSSM set $SERVICE AppStdout (Join-Path $logDir "dashboard.out.log")
& $NSSM set $SERVICE AppStderr (Join-Path $logDir "dashboard.err.log")
& $NSSM set $SERVICE Start SERVICE_AUTO_START
# 비정상 종료 시 자동 재시작(기본 동작) + 재시작 지연 5초
& $NSSM set $SERVICE AppExit Default Restart
& $NSSM set $SERVICE AppRestartDelay 5000

Write-Host "[4/4] 서비스 시작..." -ForegroundColor Cyan
& $NSSM start $SERVICE

Write-Host ""
Write-Host "완료. 상태 확인: $NSSM status $SERVICE" -ForegroundColor Green
Write-Host "접속:        http://$BIND`:$PORT  (BIND=127.0.0.1 이면 이 PC에서만)" -ForegroundColor Green
Write-Host "로그:        $logDir\dashboard.*.log" -ForegroundColor Green
