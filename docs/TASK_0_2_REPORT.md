# Task 0.2 — 의존성 버전 핀 + 레거시 확인 (보고)

> 사양서 `QMS_전체구현_사양서.md` Phase 0 / Task 0.2. **삭제 없음**(보고만).

## 1. 의존성 버전 핀 (`==`)

`requirements.txt`의 핵심 패키지를 캡처된 설치 버전으로 핀.

| 패키지 | 이전 | 핀 후 |
|--------|------|-------|
| streamlit | `>=1.32,<2.0` | `==1.44.1` |
| pandas | `>=2.1` | `==2.2.3` |
| numpy | `>=1.26` | `==2.4.2` |
| plotly | `>=5.20` | `==6.0.1` |
| requests | `>=2.31` | `==2.32.5` |
| urllib3 | `>=2.0` | `==2.0.4` |
| openpyxl | `>=3.1` | `==3.1.5` |
| pyarrow | `>=15.0` | `==19.0.1` |
| fpdf2 | `>=2.7` | `==2.8.7` |

**완료 기준 검증**: 핀된 requirements 재설치(`pip install -r requirements.txt`, exit 0) 후 `python -c "import QMS_Integrated_Dashboard_v2"` **통과**(Task 0.1 회귀 없음).

## 2. Phase 2 위젯 지원 확인 (streamlit 1.44.1)

| 위젯 | 지원 | 용도(Phase 2) |
|------|------|----------------|
| `st.dialog` | ✅ True | Task 2.4 연계 드릴다운 드로어(모달) |
| `st.segmented_control` | ✅ True | Task 2.2 역할 필터(QC/QA) 토글 |

> 핀 버전 **streamlit 1.44.1이 두 위젯을 모두 네이티브 지원**. Phase 2 진행에 버전 상향 불필요.
> (참고: `st.dialog`는 1.37+, `st.segmented_control`은 1.40+ 도입. 1.44.1은 둘 다 안정 지원.)

## 3. 레거시 3파일 — 메인 직접 import 여부 (삭제 금지)

`grep -nE "^\s*(import|from)\s+(QMS_API|qms_linkage|qms_oos_dashboard_panels)\b" QMS_Integrated_Dashboard_v2.py`

| 파일 | 메인 직접 import | 전이적 사용(런타임 경로) | 판정 |
|------|------------------|--------------------------|------|
| `QMS_API.py` | ❌ 없음 | `qms_pro/services/qms_client.py:17`, `qms_fetch_uncached.py:21` 경유 | **현역(간접)** — 삭제 불가 |
| `qms_linkage.py` | ❌ 없음 | `qms_pro/domain/linkage.py:19`, `qms_fetch_uncached.py:48` 경유 | **현역(간접)** — 삭제 불가 |
| `qms_oos_dashboard_panels.py` | ❌ 없음 | `qms_pro/pages/oos_panels.py:13` 경유 | **현역(간접)** — 삭제 불가 |

**결론**: 사양서 D6의 "메인에서 import 안 함"은 **직접 import 기준 사실**이나, 셋 다 `qms_pro` 래퍼/`qms_fetch_uncached`를 통해 **전이적으로 실행에 사용**됨. → "미사용 파일"이 아니라 "메인이 직접 부르지 않는 현역 백엔드 모듈". **삭제 금지**(사양서 §0 백엔드 보존 + D6 지침과 일치).

> 기타 참조처(런타임 외): `qms_api_audit.py`, `qms_doc_merge_labels.py`, `qms_proxy_server.py`, `qms_push_to_miso.py`, `qms_screen_mapper.py`, `_build_detail_urls.py`, `_diag_pagination.py`, `qms_linkage_ct_validation.py` 등 보조/진단 스크립트와 `backups/*`. 이들은 대시보드 런타임 클로저 밖.

## 4. statsmodels divide-by-zero 경고 → 사양서 §2 D7 등재

`QMS_Integrated_Dashboard_v2.py:2686` 의 `trendline="ols"`(plotly express → statsmodels 내부)에서 분산≈0 그룹 추세선 적합 시 `divide by zero encountered in scalar divide` RuntimeWarning 발생. **무해**(화면/수치 영향 없음, Task 0.1 기동 시 콘솔 에러 0건). 사양서 §2 부채표에 **D7**로 추가(Phase 1 검토 후보).
