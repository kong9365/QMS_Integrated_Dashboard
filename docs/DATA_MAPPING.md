# DATA_MAPPING — 프로토타입 요소 ↔ 실제 QMS 데이터

> 이 문서는 프로토타입(`assets/qms-data.js`의 더미값)이 실제로 **어떤 컬럼·계산식에 바인딩되는지**를 정의합니다.
> 출처: `QMS_Integrated_Dashboard_v2.py`, `QMS_API.py`, `qms_pro/` 모듈. 컬럼명은 기존 코드의 **한국어 정규화 컬럼**을 따릅니다.

---

## 0. 공통 데이터 모델

### 16개 프로젝트 (`PROJECT_META`, 병렬 수집)
`oos · deviation · investigation · capa · capaactionitem · actionitem · changemanagement · changeactionitem · changeimpactassessment · changeoutsourcing · complain · deviationoutsourcing · deviationactionitem · extension · businesstransfer · validityevaluation`
→ 각각 `fetch_*_data()` (`@st.cache_data ttl=1800` + 디스크 parquet 캐시). **이 수집/캐시 로직은 변경하지 마세요.**

### 모든 프로젝트 공통 컬럼 (`parse_list_only`)
| 정규화 컬럼 | 원본 API 키 | 비고 |
|---|---|---|
| `관리번호` | `prno` | 프로토타입의 "관리번호"(`.id` 셀) · 정수 |
| `제목` | `title` | |
| `진행상태` | `status` | raw 문자열 (예: finalCheck…) |
| `완료여부` | `taskCondition` | 완료 판정은 `COMPLETED_KEYWORDS` 기반 |
| `등록일` | `regDate`/`writeDate` (앞 10자) | |
| `기한일` | `limitDate` | D-day 계산 기준 |
| `등록자` | `regUserName` (이름만 추출) | |
| `상위번호` | `parentPrno` | **연계(부모-자식) 키** |
| `프로젝트` | (수집 시 부여) | `PROJECT_META[k]["label"]` |
| `연도` / `연도_등록` | 발견일시 / 등록일에서 파생 | 연도 필터용 |
| `D-day` | `limitDate - today` 파생 | 음수=초과(D+), 양수=잔여(D-) |
| `건수기여도` | 파생 (`apply_normalized_weights`) | **동시분석 1/N 가중**. 모든 집계는 이 합을 반올림 |

### 연계(체인) 파생 컬럼 (`build_and_apply_linkage` → 각 DF에 머지)
| 컬럼 | 의미 | 프로토타입 사용처 |
|---|---|---|
| `자식 수(전체)` | 연결된 연관프로젝트 총수(손자 포함) | 드로어 "연관프로젝트 수" |
| `자식 미종결 수` | 미종결 자식 수 | 종결순서 점검·드로어 |
| `자식 종결률 %` | 자식 종결 비율 | |
| `자식 최대 지연일` | 미완료 자식 최장 지연 | "최장지연" 셀 |
| `체인 최대 깊이` | 본→연관→… 최대 단계 | |
| `자식 구성` | "조사 2, CAPA 1" 형태 | 드로어 자식 목록 |
| `최종 종결 여부(체인)` | 본+모든 자식 종결 시 True | **lot 디스포지션 PASS/HOLD** · 드로어 배너 |
| `이상 케이스 플래그` | `부모종결_자식미종결` / `자식완료_부모미완료` | 종결순서 점검 테이블 |

**플래그 라벨(고정 문구):**
- `부모종결_자식미종결` → "본 프로젝트 종결 · 연관프로젝트 미완료" = **선(先)종결 의심**
- `자식완료_부모미완료` → "연관프로젝트 완료 · 본 프로젝트 미종결" = **종결처리 누락**

### 핵심 계산식
```python
KPI_TARGETS = {"CAPA 이행률": 90.0, "변경 완료율": 85.0, "불만 평균처리일": 30}
# 모든 건수 = 건수기여도 합을 round()
weighted_metric_total(df)      # 분모
weighted_metric_completed(df)  # 완료(COMPLETED_KEYWORDS)
weighted_metric_overdue(df)    # 기한초과
safe_pct(part, whole)          # 0분모 안전 백분율
```

---

## 1. 종합 현황 (Overview)

### ① KPI 4카드
| 프로토타입 카드 | 더미값 | 실제 바인딩 |
|---|---|---|
| CAPA 이행률 | 92.4% | `safe_pct(weighted_metric_completed(fcapa), weighted_metric_total(fcapa))` · 목표 `KPI_TARGETS["CAPA 이행률"]=90` |
| 변경 완료율 | 81.7% | 동일 패턴, `fchg`(changemanagement) · 목표 85 |
| 불만 평균처리일 | 26일 | `fcmp`의 `접수일`~`처리완료일` 평균 일수 · 목표 30 이내 |
| 기한초과(전사) | 18건 | 전 프로젝트 `weighted_metric_overdue` 합 · 임박=`D-day` 0~7 |
- 진척 바 채움% = 값/목표, 목표 마커 위치 = 목표값. delta = 전년 동기 대비(연도-1 필터).

### · 이상신호 3카드
| 카드 | 실제 바인딩 |
|---|---|
| 종결순서 점검 | 전 DF `이상 케이스 플래그` 카운트 (선종결 의심 + 종결처리 누락) |
| 재발 | `재발여부 == "예"` 건수(deviation/deviationoutsourcing, 최근 90일) |
| Analyst error | `이상발생 원인 == "Analyst error"` 건수기여도 합 (전년 대비 감소율은 `render_analyst_error_reduction_kpi` 로직) |

### ② 추세·분포
- **월별 품질이상 추세**: `_monthly_weighted_series(foos, 월)` = OOS, `fdev` = 일탈. 누적 막대.
- **이상발생 원인 도넛**: `foos`+`fdev`의 `이상발생 원인` groupby 건수기여도. 값 매핑 `cause_map`:
  `Method/Analyst error/Instrument error/Contamination/Environment/Man/Machine/Material/Measurement/Other`.

### ③ 상세·점검
- **기한 위험 테이블**: 전 프로젝트에서 `D-day` 오름차순(초과 우선) 상위 N. 컬럼: 관리번호·프로젝트·제목·작성팀·기한일·D-day·진행상태·🔗.
- **종결순서 점검 요약**: `이상 케이스 플래그` 2종 카운트.

### · 수집 상태
- `ALL_DFS` 각 프로젝트 `관리번호.nunique()`. (사이드바 "데이터 현황"과 동일. `deviationoutsourcing`은 일탈에 통합 표기.)

---

## 2. QC 시험품질 (OOS 중심)

데이터: `foos`(oos), `finv`(investigation), 시험실 일탈은 `fdev` 중 `일탈 유형 ∈ {시험오류, OOT, OOS}`.

| 프로토타입 요소 | 실제 바인딩 |
|---|---|
| OOS 총 건수 | `weighted_metric_total(foos)` |
| 자사/외주 | `foos['자사/외주']` 값별 (`자사`/`외주`) — OOS는 대부분 자사 |
| Analyst error | `foos[이상발생 원인=="Analyst error"]` |
| 미종결/종결률 | `weighted_metric_total - completed` / `safe_pct` |
| 시험종류별(자사·외주) | `시험정보목록[].시험종류`(`bizprocessNm`)별 + `자사/외주` 분리 (`_render_source_split` 로직) |
| 재발 추세 | 월별 `재발여부=="예"` |
| OOS 상세 테이블 | `foos` 행. 컬럼: 관리번호·제목·시험종류·품목/lot(`품목코드`/`제조번호`)·이상발생 원인·진행상태·🔗 |

---

## 3. QA 품질운영 (일탈·불만·기한연장)

데이터: `fdev`+`fdevout`(자사+외주 일탈), `fcmp`(complain), `fext`(extension).

| 프로토타입 요소 | 실제 바인딩 |
|---|---|
| 일탈/인시던트 | `이벤트 구분`(`classify_deviation_vs_incident`: status에 `finalCheckInsident`→인시던트, `finalCheck`→일탈) |
| 자사/외주 | `자사/외주` (deviation=자사, deviationoutsourcing=외주) |
| 고객불만 | `weighted_metric_total(fcmp)` · 평균처리일=`접수일`~`처리완료일` |
| 기한연장 | `fext` 총건 · 승인대기=진행중 |
| 월별 자사·외주 추세 | `접수월` 또는 `월`별, `자사/외주` 분리 |
| 등급 도넛 | `일탈 등급 대분류`(`classify_deviation_grade`: Critical/Major/Minor/인시던트/미판정), 원본 `deviationRating1` |
| 일탈 상세 | 관리번호·제목·등급·자사외주·진행상태·🔗 |
| 고객불만 파이프라인 | `complain` 단계 — 접수→원인분석(`원인분석`)→처리(`처리 결과`)→결론(`결론`) |
> ⚠️ 고객불만은 `품목코드/제조번호` 필드 없음, 본문 비구조화 → 텍스트 추출은 **2차 과제**(README 전제).

---

## 4. 조치·변경 (CAPA·변경·영향성평가)

데이터: `fcapa`,`fcapaai`,`fai`(CAPA계열), `fchg`,`fchgai`,`fchgimp`,`fchgout`(변경계열), `fvalidity`.

| 프로토타입 요소 | 실제 바인딩 |
|---|---|
| CAPA 이행률 | §1과 동일 (`fcapa`) |
| 변경 완료율 | `fchg` |
| 유효성평가 필요 | `fvalidity` 총건 / `fcapa['유효성평가 필요']=="예"` |
| 지연 조치 | CAPA+변경 `weighted_metric_overdue` |
| CAPA 이행vs지연 추세 | 월별 completed vs overdue |
| 변경 등급 도넛 | `변경 등급`(`changeGrade` lv1=Level1/lv2/lv3) |
| 조치·변경 상세 | 구분(CAPA/변경/Action)·제목·근본원인(`근본원인`/`변경 이유`)·D-day·진행상태·🔗 |
| 영향성평가 | `fchgimp` `영향 GMP 영역`·`영향 영역 수`(12개 GMP 영역 a1~a12) |

---

## 5. 제품·배치 품질 🆕

> **정정(Task 3.2a recon → 3.2b 구현 반영)**: 파싱된 `품목코드`/`품목명` 보유는 **OOS(100%)·일탈(82.1%) 둘뿐**이고
> **조사·CAPA·Action·변경계열·불만은 부재**(자식 자체 품목 채움 0.0%). 따라서 자식은 품질 계보(OOS/일탈 뿌리)에서
> **체인 상속**한 파생 컬럼으로 귀속한다. 구현: `qms_pro/domain/attribution.py`
> (`attribute_dataframes` → 신규 파생 컬럼 `품목코드_귀속`·`품목명_귀속`·`제조번호_귀속`·`귀속출처`, **원본 컬럼 불변**).
> 실측 분포(고유 9,056): 자체보유 894 · **상속 406** · 미분류 7,756 · 복수(미분류) 0 / lot 자체 467 · **상속 44** · 미상 8,545.

### 서브탭 A — APQR (품목×연도)
- **품목 식별(정정)**: 자체 `품목코드`/`품목명` 은 **OOS·일탈만** 보유. 그 외(조사·CAPA·Action 등)는 **부재** →
  `attribution.py` 의 **`품목코드_귀속`/`품목명_귀속`**(가장 가까운 값 보유 조상 = 사실상 OOS/일탈 뿌리에서 상속, 코드·명은 한 쌍) 사용.
  귀속 불가(변경계보·고립) → `귀속출처=미분류`(라벨 `전사/미분류`), 조상 품목 충돌 → `복수(미분류)`.
- **매트릭스 행(정정)**: OOS·일탈은 `품목코드` 직접; **조사·CAPA·Action 은 `품목코드_귀속`(상속)으로 집계.**
  연도 축은 `연도`(전 프로젝트 100%); **조사는 `발견일시` 부재 → `등록일`/`연도` 사용.** 건수기여도 합 동일.
- **데이터 없음(QMS 외부 · 2차 과제, 매트릭스에 `na` 처리)**: 안정성시험 추세 · OOT · 회수/반품/재가공 · 규격/시험법 변경이력 · 배치 수/수율 · 공급업체 평가.
  → 이 항목들은 QMS API에 없음. UI에서 명시적으로 "데이터 없음" 표기(현 프로토타입대로).
- ⚠️ 변경·고객불만은 품목 귀속 약함(조상도 품목 0%) → `귀속출처=미분류` → "전사/미분류"로 표기.

### 서브탭 B — lot 디스포지션
- **입력**: 제조번호 `제조번호`(`lotNo`).
- **수집(정정)**: `제조번호` 실채움은 **OOS 만(100%)**. 일탈은 컬럼 존재하나 **0%(빈값)**, 조사는 컬럼 **부재** →
  **lot 직접 매칭은 OOS 전용**. 일탈·조사·CAPA/Action 은 lot 키로 못 잡고 **체인(상위번호) + `제조번호_귀속`(상속)** 으로 연결.
  채울 조상 lot 가 없거나 충돌 → `제조번호_귀속` **빈값(미상)** 유지(추측 금지).
- **판정**: 모든 관련 이벤트의 `최종 종결 여부(체인)`이 True → **PASS**, 하나라도 미종결 → **HOLD**.
  (OOS/일탈/조사 모두 `최종 종결 여부(체인)` 100% 보유. 보조: OOS `기준 일탈 최종 결과` 적합 1,300 / 부적합 35 / 빈 16.)
- ⚠️ 변경·고객불만은 lot 키 부재 → lot 디스포지션 **범위 외**(품목 단위 별도). README 전제 그대로.

---

## 6. 알림·모니터링 🆕

기존 `alert_service`(Slack/이메일) 활용. 룰은 기존 파생 컬럼으로 모두 평가 가능:
| 룰 | 조건식 |
|---|---|
| 기한 초과 | `D-day < 0` (D+1↑) |
| D-day 임박 | `0 ≤ D-day ≤ 3` (또는 7) |
| 재발 발생 | `재발여부 == "예"` |
| 미종결 누적 | `자식 미종결 수 ≥ 3` |
| 선종결 의심 | `이상 케이스 플래그` 포함 `부모종결_자식미종결` |
| 장기 정체 | 상태 변동 14일 이상 없음(상태 변경 타임스탬프 필요 — 없으면 2차) |
- **역할별 구독**: QC={OOS,시험실일탈,조사,재시험} / QA={일탈(전사),CAPA,변경,고객불만,기한연장}. 채널=Slack+이메일, 빈도=실시간/일일 다이제스트.

---

## 7. 데이터·설정

기존 `render_raw_data_section` 거의 그대로 대응:
| 프로토타입 요소 | 실제 |
|---|---|
| 프로젝트 선택 칩 | `PROJECT_META` 라벨 multiselect |
| 검색 | QMS번호(`관리번호`)·제목·등록자·제조번호·품목코드 |
| 원본 테이블 | 우선 컬럼(프로젝트·관리번호·제목·품목코드·제조번호·등록일·기한일·진행상태·D-day·등록자) + 파서 컬럼 + `_ext_*`(extention 원본 키) 토글 |
| Excel 다운로드 | 선택 프로젝트별 시트 (`_to_excel_safe_df` + openpyxl) |
| PQR 요약 | 문서번호=`관리번호` · 발생내용=`이벤트 정보` · 조치사항=`결론 - 최종 결론` |
| 전체 갱신 | `st.cache_data.clear() + DC.clear()` |
| _ext_* 토글 | `enrich_with_raw_extention` 컬럼(`_ext_<한글라벨>`) 표시 on/off |

---

## 구현 시 주의 (Streamlit 유지 경로)
1. `qms_pro/services`(수집·캐시·연계), `qms_pro/domain`(metrics·linkage) **변경 금지** — 표현 계층만 교체.
2. CSS는 `qms_pro/ui/theme.py`의 `apply_global_css()`를 본 토큰으로 교체.
3. 탭 구성(현 9탭)을 7 워크스페이스로 재편 — `st.tabs` 또는 사이드바 네비. 단, **역할 dim/차단 로직 없음**.
4. 차트는 Plotly 유지, `CHART_COLORS`를 의미색 6종 토큰으로 재매핑(현재 12색 → navy/blue/teal 시퀀스 + 의미색).
5. 모든 건수는 `건수기여도` 합 반올림 — 단순 `len()` 쓰지 말 것.
6. 연계 드릴다운은 기존 `summarize_children(ctx, prno)` 결과를 드로어로 렌더.
