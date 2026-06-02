# Task 2.0 — 콘텐츠 인벤토리 & 매핑 (코드 변경 0)

> 사양서 `QMS_전체구현_사양서.md` §1.2 / Phase 2 Task 2.0. 재편 시 **기존 하위탭 콘텐츠 누락 0** 보장용.
> 추출 기준: `QMS_Integrated_Dashboard_v2.py`(main, 분석 시점) + `qms_oos_dashboard_panels.py`(OOS) 실제 함수.
> 분류: **[sub-view 보존]** 해당 워크스페이스 1단 sub-view로 / **[드로어]** 연계 드릴다운 모달로 흡수 / **[데이터 ws]** 데이터·설정 워크스페이스로 / **[폐기]** (사유 명시, **사용자 승인 전 금지**).
> ★ = 사양서 명시 필수 보존(누락 0 대상).

## 0. 현 11탭 → 7 워크스페이스 대응(요약)

| 현 최상위 탭 | 핸들/주요함수 | 새 워크스페이스 |
|---|---|---|
| 경영진 KPI | `tab_exec`(인라인) + `render_analyst_error_reduction_kpi` L312 | **종합현황** |
| OOS | `tab_oos` → `oos_panels.render_oos_*` | **QC 시험품질** |
| 일탈 | `tab_dev` → `render_event_category_tab(kind="일탈")` L1304 | **QA 품질운영**(스코프=전사) |
| 인시던트 | `tab_incident` → `render_event_category_tab(kind="인시던트")` | **QA 품질운영**(일탈과 동일 함수, 필터로 구분) |
| 조사 | `tab_inv`(인라인) | **QC 시험품질**(시험실 조사) |
| CAPA | `tab_capa`(인라인) | **조치·변경** |
| 변경관리 | `tab_change`(인라인) | **조치·변경** |
| 고객불만 | `tab_complain`(인라인) | **QA 품질운영** |
| 워크플로우 | `tab_workflow`(인라인) | **종합현황**(연계 요약) + [드로어] |
| 기한관리 | `tab_deadline`(인라인) | **종합현황**(기한위험) |
| 설정 | `tab_settings` → `cfg_tab1~4` | **데이터·설정** |

> 7번째 워크스페이스 **제품·배치품질(신설)** 과 **알림·모니터링(신설)** 은 기존 탭 없음 → Phase 3(Task 3.3/3.4)에서 신설. 알림은 현재 설정탭 `cfg_tab3`(알림 설정)가 모태.

## 1. 공통 항목 (모든 도메인 탭에 반복) → 흡수

| 기존 sub-tab | 실제 함수 | 새 위치 |
|---|---|---|
| "연계 현황" (전 탭) | `render_linkage_section(project_key, ...)` L1011 | **[드로어]** st.dialog 연계 드릴다운(Task 2.4)으로 통합. 별도 sub-tab 폐지 |
| "원본 데이터" (전 탭) | `render_raw_data_section(...)` L722 | **[데이터 ws]** 데이터·설정 워크스페이스로 이전 |

## 2. 도메인별 고유 sub-tab 매핑 (1:1, 누락 0)

### OOS (`tab_oos`) → QC 시험품질
| sub-tab | 함수 | 새 위치 |
|---|---|---|
| 현황 | `oos_panels.render_oos_status` | [sub-view 보존] QC/OOS·현황 |
| 경향분석 | `oos_panels.render_oos_trend` | [sub-view 보존] QC/OOS·경향 |
| ★경향분석보고서 | `oos_panels.render_oos_report` | [sub-view 보존] QC/OOS·보고서 |
| ★마감회의 & GMP | `oos_panels.render_oos_gmp` | [sub-view 보존] QC/OOS·마감회의&GMP |
| 연계 현황 | `render_linkage_section("oos",...)` | [드로어] |
| 원본 데이터 | `render_raw_data_section(...)` | [데이터 ws] |

### 일탈 / 인시던트 (`render_event_category_tab` L1304, 공유) → QA 품질운영
하위탭(`tab_kpi/trend/cause/recur/team/link/raw` L1369), kind 로 일탈·인시던트 구분.
| sub-tab | 위치(함수 내 블록) | 새 위치 |
|---|---|---|
| 개요·KPI | L1374 `with tab_kpi` | [sub-view 보존] QA/일탈·개요 |
| 경향분석 | L1432 `with tab_trend` | [sub-view 보존] QA/일탈·경향 |
| 원인·유형 | L1535 `with tab_cause` | [sub-view 보존] QA/일탈·원인유형 |
| 재발 | L1559 `with tab_recur` | [sub-view 보존] QA/일탈·재발 |
| 팀별·외주(자사/외주/통합 3 sub-sub-tab L1605) | L1600 `with tab_team` | [sub-view 보존] QA/일탈·팀별외주 (자사/외주는 필터) |
| 연계 현황 | L1668 `with tab_link` | [드로어] |
| 원본 데이터 | L1679 `with tab_raw` | [데이터 ws] |

> 인시던트: 동일 함수(kind="인시던트"). 사양서 정정상 일탈/인시던트는 QA 스코프(전사). "이벤트 구분" 컬럼으로 분리.

### 조사 (`tab_inv`, 인라인 L2037) → QC 시험품질(시험실 조사)
| sub-tab | 위치 | 새 위치 |
|---|---|---|
| 개요·KPI | L2041 `i_overview` | [sub-view 보존] QC/조사·개요 |
| ★5M1E 상세 | `i_m1e` L2041+ (5M1E_* 컬럼 집계) | [sub-view 보존] QC/조사·5M1E |
| 추이·팀별 | `i_trend` | [sub-view 보존] QC/조사·추이팀별 |
| 연계 현황 | `render_linkage_section("investigation",...)` | [드로어] |
| 원본 데이터 | `render_raw_data_section` | [데이터 ws] |

### CAPA (`tab_capa`, 인라인 L2152) → 조치·변경
| sub-tab(블록) | 새 위치 |
|---|---|
| 통합 KPI `capa_kpi` L2164 | [sub-view 보존] 조치변경/CAPA·KPI |
| CAPA 현황 `capa_status` L2174 | [sub-view 보존] 조치변경/CAPA·현황 |
| Action Item 이행 `capa_ai` L2213 | [sub-view 보존] 조치변경/CAPA·AI이행 |
| 기한·지연 `capa_deadline` L2234 | [sub-view 보존] 조치변경/CAPA·기한지연 |
| 연계 현황 `capa_link` L2254 | [드로어] |
| 원본 데이터 `capa_tab_raw` L2258 | [데이터 ws] |

### 변경관리 (`tab_change`, 인라인 L2275) → 조치·변경
| sub-tab(블록) | 새 위치 |
|---|---|
| 통합 KPI `chg_kpi` L2284 | [sub-view 보존] 조치변경/변경·KPI |
| 등급·구분 `chg_grade` L2300 | [sub-view 보존] 조치변경/변경·등급구분 |
| ★영향성평가 `chg_impact` L2330 | [sub-view 보존] 조치변경/변경·영향성평가 |
| ★외주변경 `chg_out` L2351 | [sub-view 보존] 조치변경/변경·외주변경 |
| Action Item `chg_ai` L2372 | [sub-view 보존] 조치변경/변경·AI |
| 연계 현황 `chg_link` L2391 | [드로어] |
| 원본 데이터 `chg_tab_raw` L2395 | [데이터 ws] |

### 고객불만 (`tab_complain`, 인라인 L2415) → QA 품질운영
| sub-tab(블록) | 새 위치 |
|---|---|
| 개요·KPI `cmp_kpi` L2433 | [sub-view 보존] QA/불만·개요 |
| 유형·처리결과 `cmp_type` L2454 | [sub-view 보존] QA/불만·유형처리 |
| 원인·결론 `cmp_cause` L2492 | [sub-view 보존] QA/불만·원인결론 |
| 처리 성능 `cmp_perf` L2518 | [sub-view 보존] QA/불만·처리성능 |
| 연계 현황 `cmp_link` L2624 | [드로어] |
| 원본 데이터 `cmp_tab_raw` L2628 | [데이터 ws] |

### 워크플로우 (`tab_workflow`, 인라인 L2645, sub-tab 없음) → 종합현황
| 콘텐츠 | 새 위치 |
|---|---|
| 후속 워크플로우 연계 현황 / 품질이슈→후속조치 Sankey(L2674) | [종합현황] 연계 요약 + [드로어]로 상세 |
| OOS 발생 vs CAPA 완료율 월별 상관(L2693~) | [sub-view 보존] 종합현황/연계분석 |

### 기한관리 (`tab_deadline`, 인라인 L2778, sub-tab 없음) → 종합현황
| 콘텐츠 | 새 위치 |
|---|---|
| 전 프로젝트 기한 현황 / 기한연장 현황 | [sub-view 보존] 종합현황/기한위험 |
| 기한 현황 간트 차트(px.timeline L2845) | [sub-view 보존] 종합현황/기한위험 |
| 기한 임박 상세(D-day≤7) | [sub-view 보존] 종합현황/기한위험 |

### 설정 (`tab_settings` L2892, sub-tab `cfg_tab1~4`) → 데이터·설정
| sub-tab | 새 위치 |
|---|---|
| 📊 데이터 현황 `cfg_tab1` | [데이터 ws] 수집상태/메타 |
| 🗄️ 캐시 관리 `cfg_tab2` | [데이터 ws] 캐시/갱신 |
| 🔔 알림 설정 `cfg_tab3` | **알림·모니터링 ws**(신설 Task 3.4)의 모태 → 우선 데이터·설정, Phase 3에서 이전 |
| ⚙️ 시스템 정보 `cfg_tab4` | [데이터 ws] |

### 경영진 KPI (`tab_exec` L1722) → 종합현황
| 콘텐츠 | 새 위치 |
|---|---|
| Analyst error 감소율 `render_analyst_error_reduction_kpi` L312 | [sub-view 보존] 종합현황/스탯스트립 |
| CAPA 이행률·변경 완료율·불만 평균처리일 진척바 카드 `S.kpi_stat_card` | [sub-view 보존] 종합현황/스탯스트립 |
| 프로젝트별 현황 요약 / 월별 추이 / 기한초과 / YoY | [sub-view 보존] 종합현황(3단 위계, Task 2.6) |

## 3. 폐기 후보 — **없음**

모든 기존 sub-tab을 보존/드로어/데이터ws로 매핑함(누락 0). **폐기 항목 없음** → 사용자 승인 불필요.
("연계 현황"·"원본 데이터"는 폐기가 아니라 드로어/데이터ws로 **흡수**(콘텐츠 유지).)

## 4. 검증
- 기존 최상위 11탭 × 고유 sub-tab 전부 위 표에 1:1 존재(누락 0).
- ★필수 보존 5종 모두 매핑됨: OOS 경향분석보고서·마감회의&GMP, 조사 5M1E, 변경 영향성평가·외주변경.
- 코드 변경 0(분석·문서화만).
