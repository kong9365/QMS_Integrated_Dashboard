@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "HOST_IP="
for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "$ips=[System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) ^| Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and $_.IPAddressToString -ne '127.0.0.1' -and $_.IPAddressToString -notlike '169.254.*' } ^| ForEach-Object { $_.IPAddressToString }; if($ips){$ips[0]}"`) do set "HOST_IP=%%I"

echo QMS 통합 대시보드 시작 (LAN 공개: .streamlit\config.toml 기준)
echo 로컬 PC 브라우저: http://localhost:8501
if defined HOST_IP (
    echo 다른 PC 브라우저: http://%HOST_IP%:8501
) else (
    echo 다른 PC 브라우저: IPv4 자동 탐지 실패 ^(ipconfig로 IPv4 확인 후 접속^)
)
echo.
streamlit run QMS_Integrated_Dashboard_v2.py
pause
