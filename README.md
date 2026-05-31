# QMS Integrated Dashboard

품질경영시스템(QMS) 데이터를 수집·연계하여 한 화면에서 모니터링하는 **Streamlit** 대시보드입니다.
부적합/OOS 현황, 마감회의 KPI, 기한 관리(간트), 부모-자식 연계(linkage), Slack/이메일 알림, PDF 보고서를 제공합니다.

## 아키텍처

`qms_pro/` 레이어드 패키지가 **얇은 래퍼**로 공개 API를 제공하고, 실제 구현은 저장소 루트의 모듈에 있습니다. 두 레벨은 같은 디렉터리(저장소 루트)에 함께 있어야 import가 해석됩니다.

```
QMS_Integrated_Dashboard_v2.py     # 메인 Streamlit 앱 (엔트리포인트)
qms_pro/                           # 레이어드 패키지 (래퍼/조합)
├── config/    project_meta, settings
├── domain/    metrics, linkage          # 순수 계산 로직
├── services/  qms_client, cache_service, fetcher_service, alert_service
├── pages/     oos_panels                # 탭/화면
├── ui/        theme, charts, filters, components
└── integrations/ pdf_report
# ── 루트 구현 모듈 (qms_pro 래퍼가 재노출) ──
QMS_API.py                 # QMS REST API 클라이언트 (Keycloak 인증)
qms_fetch_uncached.py      # 데이터 수집 구현
qms_linkage.py             # 부모-자식 연계 그래프
qms_oos_dashboard_panels.py# OOS 탭 렌더링
qms_styles.py              # 디자인 시스템 (CSS/컴포넌트)
qms_project_meta.py        # 16개 프로젝트 메타(라벨/색상/그룹)
qms_disk_cache.py          # 디스크 캐시
qms_alert.py               # Slack/SMTP 알림
qms_pdf_report.py          # PDF 보고서
```

## 설치

```bash
pip install -r requirements.txt   # Python 3.11 ~ 3.13
```

## 환경 변수 설정

자격증명은 **소스코드에 없으며 환경변수에서만** 읽습니다. `secrets.example.env` 를 `.env` 로 복사해 값을 채우세요.

```bash
cp secrets.example.env .env        # Windows: copy secrets.example.env .env
```

| 변수 | 설명 |
|------|------|
| `QMS_API_BASE_URL` | QMS 서버 주소 |
| `QMS_CLIENT_NAME` / `QMS_CLIENT_SECRET` | Keycloak 클라이언트 |
| `QMS_REALM_NAME` | Keycloak realm |
| `QMS_LOGIN_USERNAME` / `QMS_LOGIN_PASSWORD` | 접속 계정 |
| `QMS_SLACK_WEBHOOK` | (선택) Slack 알림 Webhook |
| `QMS_SMTP_HOST` / `QMS_SMTP_PORT` / `QMS_SMTP_USER` / `QMS_SMTP_PASS` / `QMS_ALERT_TO` | (선택) 이메일 알림 |
| `QMS_PROXY_KEY` 등 | (선택) 프록시 서버 설정 |

> `.env`, `.streamlit/secrets.toml` 등 비밀 정보 파일은 `.gitignore` 로 제외됩니다. **실제 자격증명을 커밋하지 마세요.**

## 실행

```bash
streamlit run QMS_Integrated_Dashboard_v2.py
```

Windows 에서는 `run_dashboard_LAN.bat` 을 실행하면 LAN 공개(`0.0.0.0:8501`)로 구동됩니다.
