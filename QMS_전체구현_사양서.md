# QMS 통합 대시보드 — 전체 구현 사양서 (Claude Code 핸드오프)

> 이 문서는 `kong9365/QMS_Integrated_Dashboard` 레포를 **운영(production) 수준**으로 리뉴얼하기 위한 실행 사양서입니다.
> Claude Code는 이 문서를 레포 루트에 두고 **단계(Phase)별로** 실행합니다. 한 번에 전부 바꾸지 마세요.
> 사용법 예: *"IMPLEMENTATION_PLAN.md 의 Phase 1, Task 1.1 만 구현하고 완료 기준을 검증한 뒤 멈춰라."*

---

## 0. 핵심 전제 (반드시 먼저 읽을 것)

- **스택 유지**: Streamlit + Plotly + Pandas 를 **그대로 유지**한다. React/별도 웹 프레임워크로 갈아엎지 않는다.
- **유지보수 주체**: 비전문가 1인 + AI. → 모든 변경은 **단순·명시적·문서화**되어야 한다. 영리한 추상화보다 읽기 쉬운 코드.
- **규모/용도**: 사내 LAN, 동시 사용 ~20명, 권한 분리 불필요, **모니터링 용도**(출하전확인은 *판정 보조*이지 출하 판정 시스템이 아님 → 화면에 그 취지를 명시).
- **배포**: 사내 항상 켜진 PC/서버 1대, 온프레미스. 클라우드 불필요.
- **백엔드 보존**: `qms_pro/services`, `qms_pro/domain`, `linkage`, 데이터 수집/가중/연계 로직은 **자산이다. 로직을 바꾸지 말 것.** 표현 계층과 운영 인프라만 손댄다.
- **PoC→운영의 핵심**: 화면 기술이 아니라 ① 데이터·화면 분리 ② 항상 켜짐(서비스화) ③ 기술 부채 정리. (Phase 1)

---

## 1. 현재 레포 구조 (사실 — 작업 전 직접 재확인 필수)

작업 시작 전 `find . -name "*.py" -not -path "./.git/*"` 로 실제 구조를 한 번 확인하라. 아래는 분석 시점 기준이다.

```
QMS_Integrated_Dashboard_v2.py     # 메인 Streamlit 앱 (~3,014줄). 진입점.
qms_styles.py                      # 실제 CSS/디자인 시스템 (~625줄). 디자인의 단일 진입점.
qms_project_meta.py                # 16개 프로젝트 메타 (라벨/색상/그룹/detail). 실제 원본.
qms_fetch_uncached.py              # 파생 컬럼 생성: D-day, 건수기여도, 자사/외주 등
qms_disk_cache.py                  # 디스크 캐시
qms_alert.py                       # 알림(원본)
qms_pdf_report.py                  # PQR/PDF 내보내기
QMS_API.py                         # (레거시) 필드 파서 카탈로그 — 메인이 import 안 함
qms_linkage.py                     # (레거시) — 메인이 import 안 함
qms_oos_dashboard_panels.py        # (레거시) — 메인이 import 안 함
run_dashboard_LAN.bat              # streamlit run ... --server.address 0.0.0.0 --server.port 8501
requirements.txt
secrets.example.env
.streamlit/config.toml

qms_pro/
  ui/theme.py            # ★ qms_styles.py 를 재노출하는 "호환 래퍼" (실체 아님)
  ui/components.py       # ★ 호환 래퍼
  ui/charts.py           # 차트 헬퍼 (CH)
  ui/filters.py          # 필터 헬퍼 (UIF)
  config/project_meta.py # ★ qms_project_meta.py 를 재노출하는 호환 래퍼 (PROJECT_META)
  services/qms_client.py # API_BASE_URL (os.environ 로 주입 — 하드코딩 없음)
  services/cache_service.py        # 캐시 접근 (DC)
  services/fetcher_service.py      # fetch_*_impl 15종 + build_and_apply_linkage
  services/alert_service.py        # send_slack, send_email, run_overdue_alert
  domain/metrics.py      # COMPLETED_KEYWORDS, safe_pct, weighted_metric_total/completed/overdue, _wcount, _wgroupby
  domain/linkage.py      # LinkageContext (부모-자식 그래프)
  pages/oos_panels.py
```

**16개 프로젝트 키**: `oos, deviation, deviationoutsourcing, deviationactionitem, investigation, capa, capaactionitem, actionitem, change, changeactionitem, changeimpactassessment, changeoutsourcing, complain, extension, businesstransfer, validityevaluation`

### 1.1 데이터 모델 핵심 축
- **공통 골격(전 레코드)**: `관리번호`, `상위번호(parentPrno)`, `프로젝트`, `진행상태`/`완료여부(taskCondition='C')`, `기한일(limitDate)`→`D-day`, `등록일`/`접수월`, `작성팀`/`작성자`.
- **시험·제품 축(OOS·일탈·조사에만, `시험정보목록` 내부)**: `품목코드/명`, `제조번호(lot)`, `시험종류`, `시험항목/기준/결과`, `허용기준`.
- **품질이상 신호**: `일탈 등급`, `발생 유형`, `이상발생 원인(Analyst error 등)`, `재발여부`, `시험실 이벤트 유형`.
- **조치·관리**: CAPA(`근본원인`·`조치내용`·`전체 결과`·`유효성평가 필요`·`완료일`), 변경(`변경 등급`·`구분`·`영향성평가 필요`), 불만(`불만 유형`·`원인분석`·`처리 결과`·`결론`).
- **연계 축(척추)**: `상위번호` 그래프 → `최종 종결 여부(체인)`(메인 ~L996 에서 계산), 종결순서 점검(선종결 의심/종결처리 누락), 연계 깊이.
- **가중 집계**: `건수기여도`(한 레코드가 여러 분석에 걸칠 때 1/N).

---

### 1.2 기존 중첩 탭 구조 (보존 대상 콘텐츠 — 실측)

현재 11개 상위 탭은 각자 하위탭(일부는 하위하위탭)을 가진다. **모든 탭 공통**으로 `연계 현황`(→ 드로어로 내장) + `원본 데이터`(→ 데이터·설정 ws)를 갖고, 나머지는 도메인 고유 뷰다. Phase 2 재편 시 **고유 뷰의 콘텐츠를 잃지 않는다.** 기존 렌더 함수를 재배치(rebind)할 것.

| 기존 탭 | 하위탭 (★=고유·반드시 보존) | 새 위치(워크스페이스 / sub-view) |
|--------|------------------------------|-------------------------------|
| OOS | 현황 / 경향분석 / ★경향분석보고서 / ★마감회의 & GMP / 연계 / 원본 | QC 시험품질 / sub-view(현황·경향·보고서·마감회의·GMP) |
| 일탈·인시던트 | 개요·KPI / 경향분석 / 원인·유형 / 재발 / 팀별·외주(자사/외주/통합) / 연계 / 원본 | QA 품질운영 / sub-view, 자사·외주는 필터 |
| 조사 | 개요·KPI / ★5M1E 상세 / 추이·팀별 / 연계 / 원본 | QC 또는 QA(시험실 조사) / sub-view |
| CAPA | 통합 KPI / CAPA 현황 / Action Item 이행 / 기한·지연 / 연계 / 원본 | 조치·변경 / sub-view |
| 변경 | 통합 KPI / 등급·구분 / ★영향성평가 / ★외주변경 / Action Item / 연계 / 원본 | 조치·변경 / sub-view |
| 불만 | 개요·KPI / 유형·처리결과 / 원인·결론 / 처리 성능 / 연계 / 원본 | QA 품질운영 / sub-view |

> **IA 원칙(중요)**: 금지하는 것은 *깊은 2~3단 중첩*이지 sub-view 자체가 아니다. 각 워크스페이스는 **얕은 1단 sub-view**(세그먼트/서브탭)를 가질 수 있고, 이것이 **향후 기능 확장의 표준 메커니즘**이다. `연계`는 드로어로, `원본`은 데이터 ws로 흡수해 공통 항목을 줄인 뒤, 도메인 고유 뷰만 sub-view로 남긴다.

## 2. 알려진 기술 부채 (Phase 1에서 정리 대상)

| # | 부채 | 위치(분석 시점) | 처리 |
|---|------|----------------|------|
| D1 | 중복 정의 — `kpi_gauge`(L298), `render_header`, `render_footer`, `CHART_COLORS` 가 메인과 `qms_styles` 양쪽에 존재 | 메인 파일 | 메인의 중복본 제거, `qms_styles`/`qms_pro` 단일 소스로 통일 |
| D2 | 다크모드 깨짐 — `dark_mode` 세션(L153)+`S.dark_mode_toggle()`(L480) 있으나 CSS 1회만 주입, 차트 `plot_bgcolor="white"` **29곳** 하드코딩 | 메인 파일 | **라이트 우선** 확정. 깨진 토글 제거 또는 정상화. 차트 배경을 토큰으로 |
| D3 | facade 위임 구조 — `qms_pro/ui/theme.py`·`config/project_meta.py`·`ui/components.py` 가 루트 원본에 위임하는 facade | qms_pro | **범위 축소(정정)**: Phase 1에서 전면 마이그레이션 시도 금지(작동 백엔드 불변). D3 = "위임 구조를 `docs/ARCHITECTURE.md`에 문서화 + `theme.py` 등 stale 주석만 정정"으로 한정 |
| D4 | 취약 CSS/JS — 내부 DOM 속성(`data-testid`,`data-baseweb`) 의존 CSS + `window.parent.document` 조작 JS(사이드바 토글) | qms_styles | parent-document JS **제거**, DOM 의존 CSS 최소화·네이티브 우선 |
| D5 | 제목 체계 3중 혼재 — raw `####`(×23) / `S.section_header`(×16) / `st.subheader`(×19) | 메인 파일 | `S.section_header` 하나로 통일 |
| D6 | 현역 백엔드(메인 직접 import 안 함) — `QMS_API.py`,`qms_linkage.py`,`qms_oos_dashboard_panels.py` | 루트 | **정정**: '미사용'이 아니라 `qms_pro` 래퍼가 전이적으로 import하는 **현역 백엔드**. **삭제·이동 금지.** (`qms_client.py`→QMS_API, `domain/linkage.py`→qms_linkage, `pages/oos_panels.py`→qms_oos_dashboard_panels) |

---

## 3. 목표 운영 아키텍처

```
[QMS PRO API]
     │  (수집은 화면과 분리)
     ▼
[스케줄 수집 작업 refresh_job]  ← 기존 fetcher_service / domain / linkage / qms_fetch_uncached 재사용
  · 주기적 실행(예: 1시간) · 16개 프로젝트 fetch + 연계/가중/파생 계산
  · 결과를 로컬 .qms_cache(parquet)에 원자적 저장 + _meta.json(시각/건수/성공여부) 기록
     │
     ▼
[로컬 .qms_cache parquet]  ← 화면은 이것만 빠르게 읽음 (서버 불필요, 1인 운영 적합)
     │
     ▼
[Streamlit 앱]  ← 항상 켜진 사내 PC에서 서비스로 상시 구동 (cache_only, API 미접속)
```

- **데이터 저장소**: **`.qms_cache` parquet 파일**(정정 — SQLite 미사용, 별도 DB 서버 불필요). 프로젝트별 `{key}.parquet` + `_meta.json`(last_refresh, 프로젝트별 rows/status/error). (SQLite/이력질의는 Phase 4 옵션.)
- **수집 작업**: 독립 스크립트. UI와 같은 프로세스에서 돌리지 않는다(블로킹 방지).
- **스케줄**: Windows Task Scheduler(온프레미스 Windows 가정). 리눅스면 cron/systemd timer.
- **상시 구동**: Windows는 NSSM 또는 작업 스케줄러(로그온 시 시작+재시작). 리눅스면 systemd service.

> **가정(명시)**: 호스트는 Windows(레포에 `.bat` 존재). 리눅스/맥이면 해당 OS의 등가 수단으로 치환하고, 그 사실을 README에 기록하라.

---

## 4. Claude Code 작업 규칙 (전 Phase 공통)

1. **Phase·Task 단위로만** 작업한다. 지시받지 않은 Phase에 손대지 않는다.
2. **외과적 변경**: 요청과 무관한 코드/주석/포맷을 "개선"하지 않는다. 변경한 모든 줄은 해당 Task로 추적 가능해야 한다.
3. **백엔드 로직 불변**: `services`/`domain`/`linkage`의 계산 로직(가중·연계·완료판정)을 바꾸지 않는다. 읽는 방식(데이터 소스)만 바꾼다.
4. **검증 우선(목표 주도)**: 각 Task의 "완료 기준"을 충족하기 전엔 다음으로 넘어가지 않는다. 가능하면 재현 가능한 확인(스크립트/명령) 제시.
5. **비밀정보 금지**: `.env`/`secrets.toml`/실제 API URL·계정·웹훅을 커밋하지 않는다. 새 설정은 `secrets.example.env`에 키만 추가.
6. **버전 고정**: `requirements.txt`에 streamlit 등 핵심 패키지 버전을 핀(`==`)한다.
7. **브랜치**: 각 Phase는 별도 브랜치(`renewal/p1-foundation` 등). main 직접 커밋 금지.
8. **롤백 가능**: 파괴적 변경(파일 삭제, 대량 치환) 전 commit 분리. 한 커밋 = 한 논리적 변경.
9. **문서화**: 새 모듈/스크립트엔 상단 docstring(목적·입출력·실행법). README에 운영 절차 기록(1인+AI 유지보수 대비).
10. **불명확하면 멈추고 질문**: 가정해야 할 지점은 코드 주석과 보고에 가정을 명시.

---

## 5. 단계별 구현

각 Task 형식: **목적 / 작업 / 건드릴 파일 / 완료 기준(검증) / 주의**.

### Phase 0 — 준비 & 베이스라인 (0.5일)

**Task 0.1 — 베이스라인 동작 확인**
- 목적: 리뉴얼 전, 현재 앱이 도는 상태를 기준점으로 박는다.
- 작업: `pip install -r requirements.txt` → `python -c "import QMS_Integrated_Dashboard_v2"` 통과 확인 → `streamlit run`으로 기동 확인(데이터 fetch까지).
- 완료 기준: ImportError 없이 기동, 최소 1개 탭이 데이터를 렌더. 화면 스크린샷/탭 목록을 보고.
- 주의: 비밀값은 로컬 `.env`로만. QMS 접근 불가 환경이면 그 사실을 보고하고 mock 경로 확인.

**Task 0.2 — 의존성 버전 핀 + 레거시 확인**
- 작업: `requirements.txt`의 streamlit/plotly/pandas 등에 `==` 버전 고정. `QMS_API.py`/`qms_linkage.py`/`qms_oos_dashboard_panels.py`가 메인에서 import되지 않음을 `grep`으로 재확인하고 보고(삭제 금지).
- 완료 기준: 핀된 requirements로 재설치 후에도 0.1 통과. 레거시 미사용 여부 표로 보고.

---

### Phase 1 — 운영 기반 (데이터 분리 · 서비스화 · 부채 정리) — 우선순위 최상

**Task 1.1 — 데이터 접근 계층 신설 (읽기 추상화)**
- 목적: 화면이 "라이브 fetch"가 아니라 "로컬 저장소"를 읽도록 한 곳으로 모은다.
- 작업: `qms_pro/services/data_access.py` 신설. `load_project(project) -> DataFrame`, `load_all() -> dict`, `get_refresh_meta() -> dict` 제공. 1차 구현은 **기존 cache_service/fetcher 결과를 그대로 반환**(동작 동일), 단 호출부가 이 계층만 보게 한다.
- 건드릴 파일: 신규 `data_access.py`; 메인 파일의 데이터 로딩 호출부를 이 계층 경유로 교체.
- 완료 기준: 화면 동작이 0.1과 **동일**(회귀 없음). 데이터 로딩 호출이 전부 `data_access`를 거침(grep으로 확인).
- 주의: 로직 변경 아님. 단순 경유 계층. 가중/연계 계산은 기존 함수 그대로 호출.

**Task 1.2 — 스케줄 수집 작업 + 캐시 적재** (저장소 정정: SQLite → `.qms_cache` parquet 재사용)
- 목적: QMS fetch·연계·파생 계산을 화면과 분리해 주기 실행하고 결과를 로컬에 적재.
- **저장소 정정**: SQLite `qms_data.db` 대신 **기존 `.qms_cache` parquet 재사용**. 사유: ① 동작 중인 캐시 존재 ② APQR는 DB 스냅샷 불필요(이벤트에 날짜 보유) ③ 1인+AI 운영엔 단순 우선. SQLite/이력질의는 **Phase 4 옵션으로 보류**.
- 작업: `qms_pro/jobs/refresh_job.py` 신설.
  - 기존 `run_all_snapshot_fetches()`(=`fetch_*_impl` 16종, 파생 D-day·건수기여도·자사/외주 포함) + `build_and_apply_linkage`(연계 컬럼 최종 종결 여부(체인) 등 in-place 머지) 호출 → 16개 프로젝트 enriched DataFrame.
  - 결과를 `.qms_cache` 에 **원자적(temp→swap)** 기록(`qms_disk_cache.save`가 이미 제공). `_meta.json`에 `last_refresh`(ISO)·프로젝트별 `rows`·`status`(ok/fail)·`error` 기록.
  - CLI 실행: `python -m qms_pro.jobs.refresh_job`. 표준 logging, 부분 실패해도 성공분은 저장.
- 건드릴 파일: 신규 `jobs/refresh_job.py`; `data_access.py`에 **cache_only 읽기**(라이브 fetch 금지) + `get_refresh_meta()`를 `_meta.json` 연동; 앱은 `cache_only=True`로 캐시만 읽고 런타임 linkage 컬럼 머지 제거(드릴다운 ctx만 경량 재구성).
- 완료 기준: `python -m qms_pro.jobs.refresh_job` 1회 실행 후 `.qms_cache`에 16개 프로젝트 + `_meta.json.last_refresh` 갱신. 이후 `streamlit run`이 **API를 안 건드리고** 캐시만으로 전 화면 렌더. **QMS 차단 상태로도 화면이 뜸을 확인**.
- 주의: 가중/연계 결과 컬럼이 기존과 1:1 동일해야 함(within-run round-trip 무손실 검증). BASELINE 절대 총계와의 1:1 비교는 금지(원천 라이브 변동).

**Task 1.3 — 갱신 신뢰성 & 화면 표기**
- 작업: 화면 상단(또는 종합 현황)에 `_meta` 기반 "마지막 갱신 시각 / 수집 상태 16/16" 표기. 수집 실패 프로젝트가 있으면 경고 배지. 수동 "지금 갱신" 버튼은 `refresh_job`을 트리거(동기 호출은 길면 비권장 → 우선 "다음 스케줄 안내"로 단순화 가능).
- 완료 기준: 일부 프로젝트 실패를 의도적으로 만들었을 때 화면이 "15/16 + 경고"로 표시.

**Task 1.4 — 상시 구동(서비스화)**
- 작업: 호스트 OS에 맞춰 상시 구동 설정 + 문서화.
  - Windows: `run_dashboard_LAN.bat` 점검 + NSSM(또는 작업 스케줄러 "로그온 시 시작/실패 시 재시작") 등록 절차를 `DEPLOY.md`에 기재. `refresh_job`은 작업 스케줄러로 1시간 주기 등록(`schtasks` 예시 포함).
  - 리눅스: `qms-dashboard.service` + `qms-refresh.timer`(systemd) 예시 제공.
- 완료 기준: PC 재부팅 후 앱이 자동 기동되고 수집이 주기 실행됨을 절차로 검증. `DEPLOY.md`에 단계 기록.
- 주의: 자격증명은 OS 사용자/환경변수 또는 `.env`로. 서비스 계정에 권한 설정.

**Task 1.5 — 기술 부채 정리 (D1·D2·D3·D4·D5)**
- 작업(각각 별도 커밋):
  - D1: 메인의 중복 `kpi_gauge`/`render_header`/`render_footer`/`CHART_COLORS` 제거 → `qms_styles`/`qms_pro` 단일 소스 사용. (게이지는 1.x의 진척바 컴포넌트로 대체되므로 Task 1.6과 연계)
  - D2: `dark_mode` 처리 — **라이트 우선** 확정. 깨진 토글을 제거(권장, 단순)하거나 정상화. 차트 `plot_bgcolor="white"` 29곳을 토큰(`--surface`/투명)으로 치환하는 공통 차트 레이아웃 헬퍼 적용(`qms_pro/ui/charts.py`).
  - D3: `qms_pro` 래퍼 — **단순한 쪽 택1**: (a) 실체를 `qms_pro` 안으로 이전 후 루트 원본 제거, 또는 (b) 래퍼 유지를 명시적으로 결정하고 docstring의 stale 문구 수정. 어느 쪽이든 "theme.py가 대시보드에서 안 쓰인다"는 잘못된 주석 정정.
  - D4: `window.parent.document` 조작 JS(사이드바 토글) 제거. 사이드바 제어는 Streamlit 네이티브로 대체하거나 제거.
  - D5: 제목을 `S.section_header` 하나로 통일(raw `####`·`st.subheader` 치환).
- 완료 기준: 각 항목별로 기능 회귀 없이 정리 완료. `grep`으로 `plot_bgcolor="white"`=0, 중복 정의 0, parent-document JS=0 확인.

**Task 1.6 — 디자인 토큰 확정 + 게이지→진척바**
- 작업: `qms_styles.py`에 토큰을 단일 정의(아래 §7 참조): 네이비 구조색 + 의미색 6종, Pretendard+mono, 8pt 간격, 반경/그림자. 12종 차트색 → 단일 시퀀스. 반원 게이지 → **목표 마커 진척 바 KPI 스탯 카드** 컴포넌트로 교체.
- 완료 기준: 경영진/종합 화면의 게이지가 진척바 카드로 바뀌고, 차트 색이 단일 시퀀스+의미색만 사용.

> **Phase 1 종료 기준(게이트)**: API가 죽어도 화면이 뜬다(데이터 분리 OK) · 재부팅 후 자동 기동(서비스화 OK) · 부채 6종 정리 · 토큰/진척바 적용. 여기까지가 "PoC→운영"의 핵심.

---

### Phase 2 — 정보구조(IA) 재편 (7 워크스페이스)

> 확정 IA: 7 워크스페이스 · **단일 구조 + 역할 필터(QC/QA)** · 연계 드릴다운 전 화면 내장 · 종결점검 분산+종합요약. (IA 맵 HTML 참조)
> **sub-view 원칙**: 각 워크스페이스는 얕은 1단 sub-view(세그먼트/서브탭)를 갖는다(§1.2 매핑 표). 깊은 중첩만 금지. 기존 고유 뷰(예: OOS 경향분석보고서·마감회의&GMP, 조사 5M1E)는 손실 없이 sub-view로 보존하고, 향후 확장도 sub-view 추가로 처리.

**Task 2.0 — 기존 콘텐츠 인벤토리 & 매핑 (재편 전 필수 선행)**
- 목적: 재편 과정에서 기존 sub-tab/sub-sub-tab 콘텐츠가 **silently 누락되지 않게** 한다.
- 작업: 현재 11개 탭의 모든 하위탭 렌더 함수를 코드에서 추출해, §1.2 표를 **실제 함수명까지** 채운 매핑 문서(`docs/CONTENT_MAP.md`)를 작성. 각 항목에 [보존→새 sub-view] / [드로어로 흡수] / [데이터 ws로 이동] / [폐기(사유)] 중 하나를 명시. 폐기는 사용자 승인 없이는 금지.
- 완료 기준: 모든 기존 하위탭이 매핑 표에 1:1로 존재(누락 0). 보존 대상(★ 포함)이 어느 워크스페이스 sub-view로 가는지 확정.
- 주의: 이 단계는 분석·문서화만. 코드 변경 없음.

**Task 2.1 — 좌측 워크스페이스 레일 (11탭 → 7)**
- 작업: 기존 11개 상단 탭을 7 워크스페이스로 재편: `종합현황 · QC 시험품질 · QA 품질운영 · 조치·변경 · 제품·배치품질(신설) · 알림·모니터링(신설) · 데이터·설정`. 좌측 아이콘 레일은 `streamlit-option-menu`로 구현(requirements 추가, 버전 핀). 기존 탭의 콘텐츠 함수는 재사용·재배치(로직 보존).
- 완료 기준: 7개 레일 항목으로 전 콘텐츠 접근 가능, 기존 차트/표가 회귀 없이 해당 워크스페이스에 표시.
- 주의: 콘텐츠 렌더 함수 자체는 최대한 보존하고 "어디서 호출되는지"만 재배치.

**Task 2.2 — 역할 필터(QC/QA) 토글**
- 작업: 상단에 `st.segmented_control`로 전체/QC/QA 토글. 선택에 따라 워크스페이스/리스트를 필터·강조. QC=OOS+시험실 일탈/조사(작성팀 기준), QA=전사 일탈·CAPA·변경·불만 등. 물리 분할 아님(단일 구조).
- 완료 기준: 토글 전환 시 동일 화면에서 표시 범위가 역할에 맞게 바뀜. 일탈처럼 경계에 걸친 항목이 중복 없이 처리됨.

**Task 2.3 — 글로벌 필터바 상시화**
- 작업: 연도·기준(발견/등록)·진행상태·D-day·역할을 상단 가로 바로 이동(`st.columns`+고정 컨테이너). 사이드바에 세로로 쌓이던 16개 프로젝트 건수 캡션은 종합현황 "수집 상태 카드"로 이전.
- 완료 기준: 워크스페이스를 옮겨도 필터 선택이 유지됨(세션 상태). 사이드바 과밀 해소.

**Task 2.4 — 연계 드릴다운 드로어 내장 (st.dialog)**
- 작업: 표준 "연계 흐름" 컴포넌트를 `st.dialog` 모달로 구현. 각 워크스페이스의 상세 표 행에서 `🔗` 클릭 → 해당 `관리번호`의 부모-자식 체인 + `최종 종결 여부(체인)` + 지연일 표시. `domain/linkage`의 그래프를 그대로 사용.
- 완료 기준: OOS/일탈/CAPA 등 어느 워크스페이스에서든 행 클릭으로 체인이 같은 화면 모달에 뜸. 별도 연계 탭 없음.

**Task 2.5 — 종결순서 점검 분산 + 종합 요약**
- 작업: 점검 케이스(선종결 의심=본 종결·연관 미완료 / 종결처리 누락=연관 완료·본 미종결)를 각 워크스페이스에 **소유 레코드 기준**으로 노출. 전사 합산 요약은 종합현황에 한 줄 신호로 두고 클릭 시 해당 워크스페이스로 점프.
- 완료 기준: 종합현황 요약 수치 = 각 워크스페이스 점검 건수 합과 일치(검증).

**Task 2.6 — 종합현황 3단 위계**
- 작업: 종합현황을 ① 스탯 스트립(CAPA 이행률·변경 완료율·불만 처리일·기한초과) → ② 월별 이상추세+원인분포 → ③ 기한위험 리스트+종결점검 요약 순으로 재배치.
- 완료 기준: 진입 시 위→아래로 경보→추세→상세가 읽힘. 게이지 잔존 0.

---

### Phase 3 — 컴포넌트 표준화 + 신설 체계

**Task 3.1 — 표준 컴포넌트 라이브러리 확정** (§8 참조)
- 작업: KPI 스탯 카드 · 데이터 테이블(`st.dataframe`+`column_config`로 상태 Pill/진행률) · 상태 Pill · 연계 드로어 · 신호/빈 상태 카드를 `qms_pro/ui/components.py`(실체화)로 모아 전 화면 재사용.
- 완료 기준: 모든 워크스페이스 상단 스탯·상세 표가 동일 컴포넌트를 사용(중복 구현 0).

**Task 3.2 — 체인 루트 품목/lot 전파 (신설 체계 토대)**
- 작업: `domain/linkage`에 "체인 루트(OOS/일탈/조사)의 `품목`/`제조번호`를 자식(CAPA/Action 등)에 전파"하는 함수 추가(읽기 전용 파생, 원본 불변). 추적 불가 케이스는 §7의 전제대로 `미분류`로 표기.
- 완료 기준: CAPA/Action 레코드에 부모 체인의 품목/lot가 채워지고, 변경·불만 등 키 부재 항목은 `미분류`로 라벨됨. 단위 테스트(샘플 체인)로 검증.

**Task 3.3 — 제품·배치 품질 워크스페이스 신설** (§7 참조)
- 작업: 서브탭 2개.
  - **APQR(품목×연도)**: 품목 선택 → 연도별 OOS/일탈(등급)/조사/CAPA(이행)/재발/원인 집계. 변경·불만은 "전사/미분류"로 별도 표기. QMS 미보유 항목(안정성·OOT·회수/반품·생산수율)은 화면에 **"데이터 없음"** 명시.
  - **출하 전 확인(lot)**: 제조번호 입력 → 직접보유(OOS/일탈/조사)+체인 자식 수집 → 전부 종결이면 ✅ PASS, 미종결 있으면 ⛔ HOLD + 목록/지연일. 화면에 "*모니터링 보조이며 정식 출하 판정 시스템이 아님*" 고지.
- 완료 기준: 샘플 품목/ lot로 집계·판정이 데이터와 일치. "데이터 없음"·고지 문구 표기.

**Task 3.4 — 알림·모니터링 센터 강화**
- 작업: 기존 `alert_service`(send_slack/send_email/run_overdue_alert) 위에 룰 관리 화면: 기한초과·D-3 임박·재발·미종결 누적 룰 + 역할별(QC/QA) 구독 + Slack/이메일 채널. 발송은 `refresh_job` 또는 별도 스케줄에 연결.
- 완료 기준: 룰 토글/구독 설정이 저장되고, 조건 충족 시 기존 채널로 발송됨(테스트 발송 확인).

---

### Phase 4 — 고도화 (옵션, 평가 후)
- 개인화 레이아웃, 이상신호 푸시, APQR 2차(안정성·회수/반품 등 외부 데이터 연계).
- (B안 커스텀 프론트 전환은 현재 규모/유지보수 여건상 **권장하지 않음** — 필요가 입증될 때만 재평가.)

---

## 6. 신설 체계 상세 사양

### 6.1 체인 전파 가능성 (품목·lot 귀속) — 전제 명시
- **직접 보유(루트)**: OOS, 일탈(시험·제조관련), 일탈외주, 조사 — `시험정보목록`에 품목/lot 존재.
- **체인 상속 가능**: CAPA, CAPA Action, 모니터링AI, 변경AI — *부모가 위 루트일 때만*.
- **추적 불가 → `미분류` 처리(전제)**: 변경·외주변경·영향성평가(품목코드 필드 부재), 고객불만(품목/lot 필드 없음·본문 비구조화), 공정 일탈 중 lot 미기재, 상위번호 없는 고아 CAPA, 업무이전·유효성평가.

### 6.2 APQR(연간품질평가) 1차 범위
- **집계 가능(QMS 보유)**: 품목별 OOS 건수, 일탈 건수(등급별), 조사 건수/유형, CAPA 이행률·지연(체인 상속분), 재발 건수, Analyst error 등 원인 분포. 변경·불만은 "전사/미분류"로 별도.
- **데이터 없음(화면에 명시)**: 안정성시험 결과·추세, OOT, 회수/반품/재가공, 기준규격·시험법 변경 이력, 제조/포장 배치 수·수율, 공급업체 평가. → Phase 4에서 외부 연계 검토.

### 6.3 출하 전 확인(lot 디스포지션)
- 입력: 제조번호(lot). 수집: 해당 lot 직접보유 레코드 + 체인 자식. 판정: 전부 `최종 종결 여부(체인)`=종결 → PASS / 미종결 존재 → HOLD(+목록·지연).
- 범위 밖(전제): 변경·고객불만은 lot 키 부재로 lot 판정에서 제외(품목 단위 별도 점검).
- **고지**: 정식 출하 판정 시스템이 아닌 모니터링 보조임을 화면에 표기.

---

## 7. 디자인 토큰 (단일 정의)

> 상세 시각은 동봉 `QMS_디자인_리뉴얼_계획서_v2.html` 참조. 코드 토큰 값:

- **구조색(네이비)**: `#0B1530 / #0E1B3D / #16244F / #213164 / #6B79A6`, Accent `#2F54D6`.
- **의미색(고정)**: 위험 `#D7263D` · 주의 `#E8830C` · 정상 `#1F9D63` · 정보 `#2F6FED` · 연계 `#0E9AA7` · 중립 `#7A88A8`.
- **상태 매핑 고정**: 빨강=초과/미종결, 주황=임박(D-3), 초록=완료/달성, 파랑=진행/정보, 틸=연계/개선, 회색=비활성.
- **차트**: 시퀀스 1세트(네이비→블루→틸). 12종 혼용 폐지.
- **타이포**: 본문 Pretendard, 수치/관리번호/D-day는 mono + `tabular-nums`. 스케일 Display40 / H24 / Title16 / Body14 / Caption12.
- **간격**: 8pt(4·8·12·16·24·32). **반경**: 8/12/16. **그림자** 3단.

---

## 8. 표준 컴포넌트 (재사용)
1. **KPI 스탯 카드** — 라벨·큰 수치(tnum)·전년 델타·목표 마커 진척 바·좌측 의미색. (반원 게이지 대체)
2. **데이터 테이블** — `st.dataframe`+`column_config`(상태 Pill/진행률), 모노 관리번호, `🔗` 연계 호출.
3. **상태 Pill** — 초과/임박/완료/진행/미해당, 의미색 고정.
4. **연계 드릴다운 드로어** — `st.dialog`, 체인 흐름+종결 여부, 전 화면 호출.
5. **신호/빈 상태 카드** — 이상신호·기한위험·데이터 없음.
6. **좌측 레일 / 글로벌 필터바 / 역할 토글** — `streamlit-option-menu` / `st.columns` / `st.segmented_control`.
7. **워크스페이스 sub-view** — 얕은 1단 세그먼트/서브탭(`st.segmented_control` 또는 1단 `st.tabs`). 도메인 고유 뷰 보존 + 향후 기능 확장의 표준 자리. 그 안에서 3단 위계 적용. 2단 이상 중첩 금지.

---

## 9. 검증 · 회귀 · 롤백
- **회귀 기준선**: Phase 0의 화면/수치를 기준으로, 각 Phase 후 핵심 수치(프로젝트 건수, CAPA 이행률 등 가중지표)가 **변하지 않았는지** 확인. 변하면 데이터 분리/전파 구현 오류이므로 멈추고 점검.
- **데이터 정합**: `refresh_job` 산출 DataFrame의 컬럼 셋·행수를 라이브 fetch 결과와 1회 대조(Phase 1).
- **단위 테스트(최소)**: 체인 전파, 출하전확인 판정, 종결점검 분류에 샘플 기반 테스트 추가.
- **롤백**: Phase별 브랜치 + 작은 커밋. 문제 시 직전 커밋으로 복귀.

## 10. 비기능 (1인+AI 운영 대비)
- **로깅**: `refresh_job`·발송·오류를 파일 로그로. 실패 시 화면 경고.
- **문서**: `DEPLOY.md`(설치·서비스·스케줄·복구), README(아키텍처 1장 그림+실행법). 새 코드 docstring 필수.
- **단순성**: 추상화·설정 옵션을 늘리지 말 것. 읽고 고치기 쉬운 코드가 최우선.
- **성능**: 화면은 DB만 읽으므로 즉시 렌더. 무거운 계산은 전부 `refresh_job`에서.

---

### 실행 순서 요약
`Phase 0`(준비) → `Phase 1`(운영기반: 데이터분리·서비스화·부채정리·토큰) → `Phase 2`(IA 7워크스페이스) → `Phase 3`(컴포넌트·신설체계) → `Phase 4`(옵션).
**Phase 1 게이트를 통과하기 전에는 Phase 2로 넘어가지 않는다.**
