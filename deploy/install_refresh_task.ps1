<#
.SYNOPSIS
  QMS 데이터 수집(refresh_job)을 1시간 주기 스케줄 작업(QMS-Refresh)으로 등록한다.
  Task 1.4 (서비스화). 관리자 PowerShell에서 실행.

.DESCRIPTION
  deploy\run_refresh.bat 을 1시간마다 실행한다. run_refresh.bat 이 .env 를 환경에
  로드하므로, 이 작업을 실행하는 계정은 레포 루트의 .env 를 읽을 수 있어야 한다.
  자격증명을 OS 사용자 환경변수에 의존하지 말 것(.env 경유 — SYSTEM/서비스 계정 대비).

.NOTES
  - 실행: powershell -ExecutionPolicy Bypass -File deploy\install_refresh_task.ps1
  - 즉시 1회 실행(테스트): schtasks /Run /TN QMS-Refresh
  - 제거:               schtasks /Delete /TN QMS-Refresh /F
  - 상태:               schtasks /Query /TN QMS-Refresh /V /FO LIST
#>

# ===== 파라미터 (실제 경로로 채우세요) =====
$REPO     = "C:\Users\user\Desktop\Coding\cusor\QMS_Integrated_Dashboard"   # 레포 루트
$TASKNAME = "QMS-Refresh"
$EVERY_MIN = 60   # 분 단위 주기(기본 60분)
# 실행 계정: 기본은 현재 사용자. .env 를 읽을 수 있는 계정이어야 한다.
# SYSTEM 으로 돌리려면 -RunAsSystem 사용(단, .env 가 SYSTEM 도 접근 가능한 위치여야 함).
$RunAsSystem = $false
# ============================================

$ErrorActionPreference = "Stop"

$bat = Join-Path $REPO "deploy\run_refresh.bat"
if (-not (Test-Path $bat)) { Write-Error "run_refresh.bat 없음: $bat (먼저 경로를 채우세요)"; exit 1 }

Write-Host "[1/2] 기존 작업이 있으면 제거..." -ForegroundColor Cyan
schtasks /Delete /TN $TASKNAME /F 2>$null | Out-Null

Write-Host "[2/2] 스케줄 작업 등록 (매 $EVERY_MIN 분, 최고 권한)..." -ForegroundColor Cyan
# /RL HIGHEST : 최고 권한 / /SC MINUTE /MO 60 : 60분 주기 / /TR : 실행 대상
# (native exe 인자는 한 줄로 — 백틱 줄바꿈 뒤 '/' 인자는 파서 혼동을 유발)
$trArg = '"' + $bat + '"'
if ($RunAsSystem) {
    schtasks /Create /TN $TASKNAME /TR $trArg /SC MINUTE /MO $EVERY_MIN /RU "SYSTEM" /RL HIGHEST /F
} else {
    # 현재 사용자로 등록(대화형 로그인 없이도 실행되게 하려면 /RU /RP 로 자격증명 지정 가능)
    schtasks /Create /TN $TASKNAME /TR $trArg /SC MINUTE /MO $EVERY_MIN /RL HIGHEST /F
}

Write-Host ""
Write-Host "완료. 즉시 1회 실행 테스트: schtasks /Run /TN $TASKNAME" -ForegroundColor Green
Write-Host "수집 로그: $REPO\logs\refresh.log" -ForegroundColor Green
Write-Host "갱신 메타: $REPO\.qms_cache\_meta.json (last_refresh/ok_count 확인)" -ForegroundColor Green
