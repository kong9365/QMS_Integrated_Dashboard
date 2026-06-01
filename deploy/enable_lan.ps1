<#
.SYNOPSIS
  (선택·의식적) 대시보드를 사내 LAN에 공개한다. Task 1.4.
  ⚠️ 기본 비활성. 실행하면 다른 PC에서 이 대시보드에 접속 가능해진다.

.DESCRIPTION
  대시보드는 운영 데이터를 표시한다. LAN 공개는 "사내망 한정"이어도 노출 범위를
  넓히는 의식적 결정이다. 아래 두 가지를 수행한다:
    1) NSSM 서비스의 bind 주소를 0.0.0.0 으로 변경(모든 인터페이스 수신) + 재시작
    2) 방화벽 인바운드 규칙(TCP $PORT)을 Domain,Private 프로파일에만 허용
       (Public 프로파일에는 열지 않음 — 카페/공용망에서 노출 방지)

  되돌리기(비공개 복귀):
    - bind 를 127.0.0.1 로: nssm set QMS-Dashboard AppParameters ... (install 스크립트 재실행 권장)
    - 방화벽 제거: Remove-NetFirewallRule -DisplayName "QMS Dashboard 8501"

.NOTES
  실행: powershell -ExecutionPolicy Bypass -File deploy\enable_lan.ps1
  관리자 권한 필요.
#>

# ===== 파라미터 =====
$NSSM    = "C:\PATH\TO\nssm.exe"
$SERVICE = "QMS-Dashboard"
$PORT    = 8501
# ====================

$ErrorActionPreference = "Stop"

Write-Warning "이 스크립트는 대시보드를 사내 LAN에 공개합니다(다른 PC 접속 허용)."
Write-Warning "운영 데이터가 표시되므로 사내망 신뢰 범위를 확인한 뒤 진행하세요."
$ans = Read-Host "계속하려면 'YES' 입력"
if ($ans -ne "YES") { Write-Host "취소됨."; exit 0 }

if ($NSSM -like "*PATH\TO*" -or -not (Test-Path $NSSM)) {
    Write-Error "먼저 `$NSSM 경로를 실제 nssm.exe 로 채우세요."; exit 1
}

Write-Host "[1/2] 서비스 bind 0.0.0.0 으로 변경 + 재시작..." -ForegroundColor Cyan
# 주: NSSM AppParameters 전체를 다시 지정해야 한다. install 스크립트의 인자와 일치시킬 것.
$entry = "QMS_Integrated_Dashboard_v2.py"
& $NSSM set $SERVICE AppParameters "-m streamlit run $entry --server.port $PORT --server.address 0.0.0.0 --server.headless true"
& $NSSM restart $SERVICE

Write-Host "[2/2] 방화벽 인바운드 허용 (TCP $PORT, Domain+Private 한정)..." -ForegroundColor Cyan
# 기존 동일 규칙 있으면 제거 후 재생성
Get-NetFirewallRule -DisplayName "QMS Dashboard $PORT" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "QMS Dashboard $PORT" `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $PORT `
    -Profile Domain,Private | Out-Null

# 호스트 IPv4 안내
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.IPAddress -notlike "169.254.*" } |
       Select-Object -First 1 -ExpandProperty IPAddress)
Write-Host ""
Write-Host "완료. 다른 PC 접속: http://$ip`:$PORT" -ForegroundColor Green
Write-Host "비공개 복귀: install_dashboard_service.ps1 재실행(BIND=127.0.0.1) + " -ForegroundColor Yellow
Write-Host "            Remove-NetFirewallRule -DisplayName 'QMS Dashboard $PORT'" -ForegroundColor Yellow
