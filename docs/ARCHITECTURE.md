# 아키텍처 — qms_pro facade 위임 구조 (D3 정정 문서)

> 사양서 `QMS_전체구현_사양서.md` §2 D3의 정정 결정 근거 문서.
> **결정**: `qms_pro` 패키지의 facade(위임) 구조를 **유지한다**. Phase 1에서 전면 마이그레이션(루트 원본을 `qms_pro` 안으로 이전 후 삭제)을 **시도하지 않는다**. 작동 백엔드 불변(사양서 §0·§4.3).

## 왜 이 구조인가 (배경)

리뉴얼 이전, 실제 구현은 **저장소 루트의 평면 모듈**(`qms_styles.py`, `qms_project_meta.py`, `QMS_API.py`, `qms_linkage.py`, `qms_oos_dashboard_panels.py`, `qms_disk_cache.py`, `qms_fetch_uncached.py`)에 있었다. 이후 레이어드 패키지 `qms_pro/`가 도입되면서, **루트 원본을 옮기지 않고** 얇게 재노출하는 facade를 두었다. 메인 앱은 이제 facade만 import 한다.

→ 즉 "절반짜리 마이그레이션"이 아니라 **의도적 facade 계층**이다. 루트 원본은 현역(자산)이고, facade는 메인이 보는 안정적 진입점이다.

## 위임 맵 (메인 → facade → 루트 원본)

메인 앱 `QMS_Integrated_Dashboard_v2.py` 의 import (L26~187):

| 메인이 import (facade) | 위임 대상(루트 원본) | 비고 |
|------------------------|----------------------|------|
| `qms_pro.ui.theme` (as S) | `qms_styles.py` | 색상 상수·CSS·컴포넌트 재노출 |
| `qms_pro.config.project_meta` (PROJECT_META) | `qms_project_meta.py` | 16개 프로젝트 메타. 폴백 빈 dict |
| `qms_pro.services.qms_client` (API_BASE_URL) | `QMS_API.py` | API 베이스 URL·클라이언트 |
| `qms_pro.services.cache_service` (as DC) | `qms_disk_cache.py` | 디스크 캐시 접근 |
| `qms_pro.services.fetcher_service` | `qms_fetch_uncached.py` | fetch_*_impl + 파생 계산 |
| `qms_pro.domain.linkage` | `qms_linkage.py` | 부모-자식 연계 그래프 |
| `qms_pro.pages.oos_panels` | `qms_oos_dashboard_panels.py` | OOS 패널 렌더 |
| `qms_pro.ui.filters` (as UIF) | (순수 구현) | 루트 위임 아님 |
| `qms_pro.domain.metrics` | (순수 구현) | 가중·완료판정 로직 본체 |

> **D6 연계**: 위 표에서 `QMS_API.py`·`qms_linkage.py`·`qms_oos_dashboard_panels.py` 는 메인이 **직접** import하지 않지만 facade(`qms_client`/`domain.linkage`/`pages.oos_panels`)를 통해 **전이적으로 실행에 쓰인다**. → '미사용'이 아닌 **현역 백엔드**. **삭제·이동 금지.**

## 레이아웃 제약 (중요)

루트 원본과 `qms_pro/`는 **같은 디렉터리(저장소 루트)**에 함께 있어야 import가 해석된다. `qms_pro/config/project_meta.py`가 repo root를 `sys.path`에 주입한다. **flat 루트 레이아웃을 유지**하고 파일을 이동하지 말 것.

## Phase 1에서의 D3 범위 (정정)

- ✅ 위임 구조를 이 문서로 **문서화**.
- ✅ `theme.py`·`config/project_meta.py`의 **stale 주석 정정**("대시보드는 아직 이 래퍼를 사용하지 않는다" → 실제 사용함; "Phase 2 후속 단계에서 이전" → 이전하지 않음).
- ❌ 루트 원본 이동/삭제, facade 제거, import 경로 대규모 변경 — **하지 않음**(백엔드 불변).

> 참고: `qms_pro/ui/components.py` 의 "현재 대시보드는 사용하지 않으며" 주석은 **사실**(메인 런타임 경로에 없음)이므로 정정 대상 아님.
