# Handoff: QMS 통합 대시보드 디자인 리뉴얼

## Overview
의약품 품질경영시스템(QMS) 통합 모니터링 대시보드의 **UI/UX 리뉴얼**을 위한 개발자 핸드오프 패키지입니다.
기존 시스템은 **Streamlit + Plotly** 기반(`QMS_Integrated_Dashboard_v2.py`, 약 146KB 단일 파일)으로,
16개 QMS 프로젝트를 9개 탭에 평면적으로 나열합니다.

이 핸드오프의 목표는 기존 **백엔드 로직·데이터 파이프라인을 그대로 보존**하면서,
표현 계층(Presentation)을 다음과 같이 재설계하는 것입니다:
- 9개 탭 → **7개 워크스페이스**로 재편 (연계·추적은 별도 탭을 폐지하고 전 화면 드릴다운 드로어로 내장)
- 모든 화면을 **3단 위계**(① 핵심 KPI → ② 추세·분포 → ③ 상세·점검)로 재배치
- "Signal over Noise / Control Tower" 컨셉 — 평소엔 차분하고, 이상이 생기면 즉시 눈에 띄는 엔터프라이즈 톤

## About the Design Files
이 번들의 HTML/CSS/JS 파일은 **디자인 레퍼런스(프로토타입)** 입니다 — 의도한 외형·레이아웃·인터랙션을
보여주는 참조물이며, 그대로 프로덕션에 복사해 쓰는 코드가 아닙니다.

**구현 작업의 본질**은 이 디자인을 **대상 코드베이스의 환경에서 재현**하는 것입니다. 두 가지 경로가 있습니다:

1. **Streamlit 유지 경로** (점진적, 권장 1차)
   기존 `QMS_Integrated_Dashboard_v2.py` 구조를 살리되, `qms_pro/ui/theme.py`의 CSS 주입을 본 프로토타입의
   디자인 토큰으로 교체하고, 탭 구성을 7 워크스페이스로 재편. Plotly 차트 스타일을 토큰에 맞춤.
   → 데이터 로직(`qms_pro/services`, `qms_pro/domain`) 변경 없음.

2. **커스텀 프론트엔드 경로** (전면 전환, 2차 옵션)
   React/Vue 등으로 프론트를 새로 만들고, 기존 Python을 REST API 백엔드로 분리.
   이 경우 본 프로토타입의 HTML 구조·컴포넌트가 거의 1:1로 옮겨집니다.

어느 경로든 **`DATA_MAPPING.md`가 핵심**입니다 — 프로토타입의 각 화면 요소가 실제 어떤 컬럼/계산식에
바인딩되는지 정의합니다. 프로토타입의 숫자·표는 전부 **예시 더미 데이터**(`assets/qms-data.js`)이며,
실제 값이 아닙니다.

## Fidelity
**High-fidelity (hifi).** 색상·타이포그래피·간격·컴포넌트·인터랙션이 최종 의도값입니다.
- 색상은 아래 Design Tokens의 정확한 hex를 사용하세요.
- 레이아웃(그리드 컬럼 비율, 카드 구조, 3단 위계)은 프로토타입을 픽셀 단위로 따르세요.
- 차트는 프로토타입에선 경량 CSS/div로 표현했지만, 실제로는 **Plotly(기존 스택) 또는 동급 차트 라이브러리**로
  동일한 형태(누적 막대, 도넛, 가로 랭크 바)를 재현하면 됩니다. 색은 토큰 시퀀스(navy→blue→teal)를 쓰세요.

## Screens / Views

좌측 76px 아이콘 레일에 7개 워크스페이스. 상단 탑바(제목·경로·수집상태) + 글로벌 필터바(연도·기준·진행상태·기한일·검색).
모든 화면 공통: `.canvas-in` max-width 1320px, 카드 그리드, `tier-lab`로 ①②③ 위계 구분.

> **역할 필터(QC/QA) 없음.** 초기 안에는 역할 토글이 있었으나 최종적으로 제거됨 — 모든 사용자가 7개 워크스페이스
> 전체에 자유롭게 접근합니다. 레일 dim/접근차단 로직을 넣지 마세요.

### 1. 종합 현황 (Overview) — `screens.overview`
- **목적**: 경영진/전사 통제탑. 진입 3초 내 "위험한 것"을 파악.
- **레이아웃**:
  - ① KPI 4열 (`.row.c4`): CAPA 이행률 / 변경 완료율 / 불만 평균처리일 / 기한초과(전사). 각 카드에 목표 마커가 있는 진척 바.
  - · 이상신호 3열: 종결순서 점검 / 재발 / Analyst error
  - ② 추세·분포 (`.row.c-21`): 월별 품질이상 추세(누적 막대) + 이상발생 원인 도넛
  - ③ 상세·점검 (`.row.c-21`): 기한 위험 테이블(즉시 조치) + 종결순서 점검 요약
  - · 수집 상태: 16개 프로젝트 건수 그리드
- **데이터**: `DATA_MAPPING.md` §종합현황 참조.

### 2. QC 시험품질 (Lab Quality) — `screens.qc`
- **목적**: QC 시험실 — OOS·시험실 일탈/조사.
- **레이아웃**: ① 스탯 4열(OOS 총건수 / 자사·외주 / Analyst error / 미종결·종결률) → ② 시험종류별 자사·외주 분리 가로바 + 재발 추세 → ③ OOS 상세 테이블(연계 드릴다운).

### 3. QA 품질운영 (Quality Ops) — `screens.qa`
- **목적**: 일탈/인시던트(전사)·고객불만·기한연장.
- **레이아웃**: ① 스탯 4열(일탈/인시던트 / 자사·외주 / 고객불만 / 기한연장) → ② 일탈 월별 자사·외주 추세 + 등급 도넛(Minor/Major/Critical) → ③ 일탈 상세 테이블 + 고객불만 파이프라인.

### 4. 조치·변경 (Actions & Change) — `screens.actions`
- **목적**: CAPA·Action Item·변경관리·영향성평가 통합.
- **레이아웃**: ① 스탯 4열(CAPA 이행률 / 변경 완료율 / 유효성평가 필요 / 지연 조치) → ② CAPA 이행vs지연 추세 + 변경 등급 도넛 → ③ 조치·변경 상세 테이블(부모 추적).

### 5. 제품·배치 품질 (Product & Batch) — `screens.product` 🆕 신설
- **목적**: APQR(연간제품품질평가)·출하 전 확인. 서브탭 2개.
- **서브탭 A — APQR**: 품목 선택 → 품목×연도 매트릭스(OOS/일탈/조사/CAPA이행률/재발). **데이터 가용성 패널**로
  "집계 가능(QMS 보유)" vs "데이터 없음(QMS 외부·2차 과제)"를 명시. ⚠️ 안정성시험·OOT·회수·수율 등은 현 QMS에 없음 — 2차.
- **서브탭 B — lot 디스포지션**: 제조번호(lot) 입력 → 해당 lot 관련 이벤트의 체인 종결여부로 **PASS/HOLD 판정**.
  ⚠️ 변경·고객불만은 lot 키 부재로 lot 디스포지션 범위에서 제외(품목 단위 별도 점검) — 전제 명시 필요.

### 6. 알림·모니터링 (Alerts & Monitoring) — `screens.alerts` 🆕 신설/강화
- **목적**: 알림 룰·역할별 구독·채널. 기존 `alert_service` 활용.
- **레이아웃**: ① 스탯 4열(활성 룰 / 오늘 발생 / 미확인 / 채널) → ② 알림 룰 토글 목록(기한초과·D-day임박·재발·미종결누적·선종결의심·장기정체) → ③ 역할별 구독(QC/QA) + 최근 알림 피드.

### 7. 데이터·설정 (Data & Settings) — `screens.data`
- **목적**: 원본 조회·Excel/PQR 내보내기·설정. 기존 `render_raw_data_section` 대응.
- **레이아웃**: ① 수집 현황 → ② 원본 조회(프로젝트 선택·검색·테이블) → ③ 내보내기(Excel/PQR) + 전역 필터·표시 설정.

## Interactions & Behavior
- **워크스페이스 전환**: 레일 아이콘 클릭 → `QMS.go(id)` → `screens[id]()` 재렌더, 캔버스 스크롤 top.
- **연계 드릴다운 드로어** (핵심): 모든 테이블의 "🔗 체인" 클릭 → `QMS.openChain(관리번호)` → 우측 480px 드로어
  슬라이드 인. 드로어 내용: 체인 종결여부 배너(HOLD/PASS) · 체인 흐름(발생→조사→CAPA→…) · 기본정보 · 연관프로젝트(자식) 목록.
  닫기: ✕ 버튼 / 스크림 클릭 / ESC.
  - 트랜지션: `transform translateX(100%)→0`, `.26s cubic-bezier(.4,0,.2,1)`. 스크림 `opacity .2s`.
- **제품 서브탭**: `QMS.setProductTab('apqr'|'lot')`.
- **필터바·토글**: 프로토타입에선 표시용(정적). 실제 구현 시 기존 사이드바 필터 로직(`qms_pro/ui/filters.py`)에 연결.
- **반응형**: `max-width:1100px`에서 4열→2열, 2:1→1열. `max-width:760px`에서 레일 64px·경로 숨김.

## State Management
프로토타입 상태(`QMS.state`): `{ active: 워크스페이스ID, productTab: 'apqr'|'lot' }`.
실제 구현에서 추가로 필요한 전역 상태(기존 Streamlit session_state 대응):
- 필터: `selected_years[]`, `year_basis(발견일시|등록일)`, `status_filter(전체|진행중|완료)`, `dday_filter(전체|D-day 임박 7일|기한 초과)`
- 연계 컨텍스트: `qms_linkage_ctx` (부모-자식 인덱스 — `build_and_apply_linkage`로 생성, 변경 불필요)
- 데이터 페치: 16개 프로젝트 병렬 수집 + 디스크 캐시(parquet, TTL 1800s). **기존 로직 그대로 사용.**

## Design Tokens
`assets/qms-app.css` `:root`에 정의된 정확한 값입니다.

### Colors — 구조색(Navy)
| 토큰 | hex | 용도 |
|---|---|---|
| `--navy-900` | `#0B1530` | 레일 그라데이션 하단 |
| `--navy-800` | `#0E1B3D` | **주 브랜드** · 레일·헤더·강조 텍스트 |
| `--navy-700` | `#16244F` | 레일 그라데이션 |
| `--navy-600` | `#213164` | 보조 |
| `--navy-300` | `#6B79A6` | 차트 보조 시퀀스 |
| `--accent` | `#2F54D6` | 액션·링크·선택 강조 |

### Colors — 의미색(6종, 의미 고정)
| 토큰 | hex | 의미 |
|---|---|---|
| `--critical` | `#D7263D` | 기한초과·미종결·위험 |
| `--warning` | `#E8830C` | D-day 임박·주의 |
| `--success` | `#1F9D63` | 완료·목표달성·정상 |
| `--info` | `#2F6FED` | 진행중·정보·추세 |
| `--teal` | `#0E9AA7` | 연계·개선·QC 구독 |
| `--muted` | `#7A88A8` | 비활성·라벨 |
> 각 색의 `-bg` 변형은 동일 색 12~14% 알파. (예: `--critical-bg: rgba(215,38,61,.12)`)

### Neutrals
`--bg #EEF1F8` · `--surface #FFFFFF` · `--surface-2 #F7F9FD` · `--line #E2E7F0` · `--line-2 #EDF0F7`
· `--ink #0E1B3D` · `--ink-soft #4A5470` · `--ink-faint #8A93AC`

### Typography
- 본문: **Pretendard** (Variable, CDN: `cdn.jsdelivr.net/gh/orioncactus/pretendard`)
- 수치·관리번호·D-day·lot: **JetBrains Mono** + `font-variant-numeric: tabular-nums` (`.mono` 클래스)
- 스케일: KPI 값 29px/800 · 패널 제목 13.5px/750 · 본문 12.5~13px · 캡션 11~11.5px · tier 라벨 10.5px/800 대문자

### Shape & Elevation
- 반경: `--r-sm 8px` · `--r 12px` · `--r-lg 16px`
- 그림자: `--sh-1`(카드 기본) · `--sh-2`(패널 강조) · `--sh-3`(드로어/모달)
- 간격: 8pt 기반 — 카드 패딩 15~17px, 그리드 gap 14~16px, 캔버스 패딩 22px

## Assets
- **폰트**: Pretendard, JetBrains Mono (CDN 또는 self-host). 별도 이미지/아이콘 에셋 없음.
- **아이콘**: 현재 이모지(◎ 🔬 🛡️ ⟳ 📦 🔔 ▤)로 표현. 실제 구현 시 아이콘 폰트/SVG 세트로 교체 권장(의미 동일하게).
- **로고**: `▦` 글리프 placeholder — 실제 브랜드 마크로 교체.

## Files
| 파일 | 역할 |
|---|---|
| `prototype/QMS 화면 프로토타입.html` | 셸 마크업 (레일·탑바·필터바·캔버스·드로어 컨테이너) |
| `prototype/assets/qms-app.css` | 디자인 토큰 + 앱 셸 + 전 컴포넌트 스타일 |
| `prototype/assets/qms-data.js` | **더미** 샘플 데이터 + 렌더 헬퍼 함수(`h.stat`, `h.panel`, `h.table`, `h.donut`, `h.barsDual` …) |
| `prototype/assets/qms-screens.js` | 7개 워크스페이스 화면 렌더러 (`QMS.screens.*`) + 워크스페이스 메타 |
| `prototype/assets/qms-app.js` | 라우팅·드로어·부팅 와이어링 |
| `prototype/QMS 화면 프로토타입 (standalone).html` | 오프라인 단일 파일(폰트 인라인) — 빠른 미리보기용. **구현 참조는 분리된 파일을 보세요.** |
| `DATA_MAPPING.md` | ⭐ 프로토타입 요소 ↔ 실제 QMS 컬럼/계산식 매핑 |

## 실행 방법 (미리보기)
`prototype/QMS 화면 프로토타입.html`을 브라우저에서 열면 됩니다(인터넷 연결 시 폰트 로드).
오프라인은 `(standalone)` 파일 사용.
