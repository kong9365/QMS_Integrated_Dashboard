# QMS 통합 대시보드 — 배포·운영 가이드 (Windows, Task 1.4)

> 호스트: **Windows**(온프레미스, 항상 켜진 사내 PC 1대). 대시보드를 서비스로 상시 구동하고,
> 데이터 수집(refresh_job)을 1시간 주기 스케줄로 돌린다.
> **이 문서의 스크립트는 경로만 채우면 실행 가능하다. 실제 등록은 관리자 PowerShell에서 사용자가 수행한다.**

## 0. 아키텍처 요약 (왜 둘로 나뉘나)

```
[QMS PRO API] ──(자격증명 필요)──> [QMS-Refresh 스케줄작업: refresh_job, 1시간]
                                         │  결과를 .qms_cache(parquet) + _meta.json 으로 적재
                                         ▼
                                   [.qms_cache]
                                         ▼
[QMS-Dashboard 서비스: streamlit, 상시] ──(자격증명 불필요, cache_only)──> 화면
```

- **대시보드 서비스**는 캐시만 읽는다(Task 1.2 cache_only) → **자격증명 불필요**, API가 죽어도 마지막 캐시로 뜬다.
- **수집 작업**만 QMS에 접속한다 → **자격증명 필요**. 코드가 `.env` 를 자동 로드하지 않으므로
  `run_refresh.bat` 이 `.env` 를 환경에 주입한 뒤 refresh_job 을 실행한다.

## 1. 사전 준비

1. **Python venv** 생성 + 의존성 설치:
   ```powershell
   cd <REPO>
   python -m venv venv
   .\venv\Scripts\python -m pip install -r requirements.txt
   ```
   → 이후 스크립트의 `$PYTHON` = `<REPO>\venv\Scripts\python.exe`
2. **NSSM** 다운로드(https://nssm.cc) → `nssm.exe` 절대경로 확보 → 스크립트의 `$NSSM`.
3. **`.env` 작성**: `secrets.example.env` 를 `.env` 로 복사 후 실제 QMS 자격증명 입력.
   `.env` 는 레포 루트에 두고, **QMS-Refresh 실행 계정이 읽을 수 있어야 한다**(아래 §5 주의).
4. **최초 캐시 1회 생성**(서비스 시작 전 데이터가 있도록):
   ```powershell
   cd <REPO>
   .\deploy\run_refresh.bat
   ```
   → `.qms_cache\_meta.json` 의 `ok_count` 16/16 확인.

## 2. 채울 경로 (스크립트 상단)

| 변수 | 위치 | 예시 |
|------|------|------|
| `$REPO` | 모든 스크립트 / run_refresh.bat | `C:\...\QMS_Integrated_Dashboard` |
| `$PYTHON` | install_dashboard_service.ps1 / run_refresh.bat | `<REPO>\venv\Scripts\python.exe` |
| `$NSSM` | install_dashboard_service.ps1 / enable_lan.ps1 | `C:\tools\nssm\nssm.exe` |
| `$BIND` | install_dashboard_service.ps1 | `127.0.0.1`(기본) |

## 3. 설치 순서 (관리자 PowerShell)

```powershell
# (1) 대시보드 서비스 등록 + 시작
powershell -ExecutionPolicy Bypass -File deploy\install_dashboard_service.ps1

# (2) 1시간 주기 수집 작업 등록
powershell -ExecutionPolicy Bypass -File deploy\install_refresh_task.ps1

# (3) 즉시 수집 1회 테스트
schtasks /Run /TN QMS-Refresh
```

접속: `http://127.0.0.1:8501` (기본은 이 PC에서만).

## 4. 재부팅 검증법

1. PC 재부팅.
2. 로그인 없이(또는 자동 로그인) 잠시 후:
   - 서비스: `nssm status QMS-Dashboard` → `SERVICE_RUNNING`.
   - 브라우저 `http://127.0.0.1:8501` → 대시보드 렌더(마지막 캐시 기준).
3. 1시간 내 `QMS-Refresh` 자동 실행 확인: `.qms_cache\_meta.json` 의 `last_refresh` 갱신.
   (즉시 확인하려면 `schtasks /Run /TN QMS-Refresh` 후 사이드바 "마지막 갱신" 시각 확인.)

## 5. 로그 위치

| 항목 | 경로 |
|------|------|
| 대시보드 stdout/err | `<REPO>\logs\dashboard.out.log` / `dashboard.err.log` |
| 수집 작업 | `<REPO>\logs\refresh.log` |
| 갱신 메타(시각/건수/상태) | `<REPO>\.qms_cache\_meta.json` |

## 6. 코드 업데이트 시

```powershell
# 코드 변경(git pull 등) 후 서비스만 재시작
nssm restart QMS-Dashboard
# 의존성 변경 시: venv 에 pip install -r requirements.txt 후 재시작
```
수집 로직 변경 시 다음 스케줄에 자동 반영(또는 `schtasks /Run /TN QMS-Refresh`).

## 7. (선택) LAN 공개

기본은 `127.0.0.1`(이 PC만). 다른 PC에서 접속해야 하면 **의식적으로**:
```powershell
powershell -ExecutionPolicy Bypass -File deploy\enable_lan.ps1   # 'YES' 확인 필요
```
→ bind 0.0.0.0 + 방화벽 TCP 8501 (Domain,Private 한정). 운영 데이터 노출 범위를 확인할 것.

## ⚠️ 주의

- **자격증명은 `.env`(레포 루트) 경유**. OS 사용자 환경변수에 의존하지 말 것
  (SYSTEM/서비스 계정은 특정 사용자 env 를 못 본다). `run_refresh.bat` 이 `.env` 를 로드한다.
- `.env` 는 절대 커밋 금지(`.gitignore` 적용됨). 비밀번호에 `%` 등 특수문자가 있어도
  `run_refresh.bat` 의 로더(`DisableDelayedExpansion`)가 안전하게 처리한다.
- 대시보드는 출하 판정 시스템이 아니라 **모니터링 보조**다(화면 고지 유지).
- 스케줄 작업을 SYSTEM 계정으로 돌릴 경우 `.env` 가 SYSTEM 도 접근 가능한 위치/권한인지 확인.
