# -*- coding: utf-8 -*-
"""qms_pro.services.data_access — 데이터 읽기 단일 진입 계층 (Task 1.1).

목적
----
화면(메인 앱)이 "어디서·어떻게 데이터를 읽는지"를 **이 한 곳으로 모은다.**
지금은 기존 동작과 **100% 동일**하게 디스크 캐시(cache_service) 우선 + 미스 시
fetcher_service 의 ``fetch_*_impl`` 을 호출해 반환한다. (Phase 1 Task 1.2 에서
이 계층의 내부만 SQLite 읽기로 바꾸면 화면 호출부는 수정 불필요.)

설계 원칙(사양서 §4.3)
- **로직 불변**: 가중·연계·파생 계산은 건드리지 않는다. 기존 ``fetch_*_impl`` 을
  그대로 호출한다. 이 모듈은 "경유 계층"일 뿐이다.
- **Streamlit 비의존**: 메모이즈(@st.cache_data)는 UI 계층(메인 앱)의 책임으로 남긴다.
  이 모듈은 streamlit 을 import 하지 않으므로 headless(수집 작업)에서도 재사용 가능.
- **반환 계약 동일**: 각 로더는 기존 메인 래퍼와 같은 ``(DataFrame, error)`` 튜플을 반환.

공개 API
--------
- ``load_project(project, *, disk_cache=None) -> (DataFrame, error)``
- ``load_all(*, disk_cache=None) -> dict[project_key -> (DataFrame, error)]``
- ``get_refresh_meta() -> dict``   (1차: 정적 메타. Task 1.2 에서 _meta 테이블 연동)
"""
from __future__ import annotations

from typing import Callable, Optional

from qms_pro.services import cache_service as _DC
from qms_pro.services.fetcher_service import (
    fetch_list_project_impl,
    fetch_oos_data_impl,
    fetch_deviation_data_impl,
    fetch_devout_data_stub_impl,
    fetch_capa_data_impl,
    fetch_change_data_impl,
    fetch_complain_data_impl,
    fetch_capaai_data_impl,
    fetch_changeai_data_impl,
    fetch_changeimpact_data_impl,
    fetch_changeout_data_impl,
    fetch_devoutai_data_impl,
    fetch_transfer_data_impl,
    fetch_validity_data_impl,
    fetch_investigation_data_impl,
)

# ---------------------------------------------------------------------------
# 프로젝트 키 → (디스크 캐시 키, impl 호출 람다) 매핑
# 기존 메인 앱(QMS_Integrated_Dashboard_v2.py)의 fetch_* 래퍼와 1:1 동일하다.
#   · 디스크 캐시 키는 메인의 _with_disk_cache(key, ...) 의 key 와 동일해야
#     기존 .qms_cache/*.parquet 를 그대로 재사용한다(회귀 0).
#   · "actionitem"/"extension" 은 list_project 경유(캐시 키 list_<project>).
#   · "deviationoutsourcing"(일탈외주) 은 stub — 디스크 캐시를 쓰지 않는다(기존과 동일).
# ---------------------------------------------------------------------------
# (cache_key, impl_callable). cache_key=None 이면 디스크 캐시 미적용(stub).
_PROJECT_LOADERS: dict[str, tuple[Optional[str], Callable[[], object]]] = {
    "oos":                    ("oos",                    fetch_oos_data_impl),
    "deviation":              ("deviation",              fetch_deviation_data_impl),
    "investigation":          ("investigation",          fetch_investigation_data_impl),
    "capa":                   ("capa",                   fetch_capa_data_impl),
    "capaactionitem":         ("capaactionitem",         fetch_capaai_data_impl),
    "actionitem":             ("list_actionitem",        lambda: fetch_list_project_impl("actionitem")),
    "changemanagement":       ("changemanagement",       fetch_change_data_impl),
    "changeactionitem":       ("changeactionitem",       fetch_changeai_data_impl),
    "changeimpactassessment": ("changeimpactassessment", fetch_changeimpact_data_impl),
    "changeoutsourcing":      ("changeoutsourcing",      fetch_changeout_data_impl),
    "complain":               ("complain",               fetch_complain_data_impl),
    "deviationoutsourcing":   (None,                     fetch_devout_data_stub_impl),
    "deviationactionitem":    ("deviationactionitem",    fetch_devoutai_data_impl),
    "extension":              ("list_extension",         lambda: fetch_list_project_impl("extension")),
    "businesstransfer":       ("businesstransfer",       fetch_transfer_data_impl),
    "validityevaluation":     ("validityevaluation",     fetch_validity_data_impl),
}

# 사이드바/ALL_DFS 와 동일한 16개 프로젝트 순서(표시·집계 순서 보존)
PROJECT_KEYS: tuple[str, ...] = tuple(_PROJECT_LOADERS.keys())


def _with_disk_cache(key: str, fn: Callable[[], object], disk_cache):
    """디스크 캐시 우선, 미스 시 fn() 실행 후 저장.

    메인 앱의 동명 헬퍼와 동작 동일: fn() 은 (df, err) 를 반환하며,
    err is None 일 때만 캐시에 저장한다.
    """
    cached = disk_cache.load(key)
    if cached is not None:
        return cached
    df, err = fn()
    if err is None:
        disk_cache.save(key, df)
    return df, err


def load_project(project: str, *, disk_cache=None):
    """단일 프로젝트 데이터를 읽어 ``(DataFrame, error)`` 로 반환.

    기존 메인 앱의 fetch_<project>_data() 래퍼와 동일한 결과를 낸다.
    디스크 캐시 키가 있는 프로젝트는 cache_service 경유, stub(일탈외주)은 직접 호출.
    """
    if project not in _PROJECT_LOADERS:
        raise KeyError(f"unknown project key: {project!r}")
    dc = disk_cache if disk_cache is not None else _DC
    cache_key, impl = _PROJECT_LOADERS[project]
    if cache_key is None:
        # stub(일탈외주): 디스크 캐시 미적용 — 기존 fetch_devout_data_stub() 과 동일
        return impl()
    return _with_disk_cache(cache_key, impl, dc)


def load_all(*, disk_cache=None) -> dict:
    """16개 프로젝트 전체를 읽어 ``{project_key: (DataFrame, error)}`` 로 반환.

    주: 메인 앱은 진행률/병렬(ThreadPoolExecutor) 표시를 위해 개별 ``load_project``
    를 직접 호출한다. 이 함수는 headless(수집 작업/검증)에서 일괄 로드용으로 제공한다.
    """
    return {pk: load_project(pk, disk_cache=disk_cache) for pk in PROJECT_KEYS}


def get_refresh_meta() -> dict:
    """갱신 메타데이터(초안). Task 1.2 에서 SQLite ``_meta`` 테이블과 연동 예정.

    현재(1차)는 소스 종류와 프로젝트 수만 보고한다. 메인 앱의 "마지막 갱신 시각/
    수집 소요"는 여전히 런타임 측정값을 사용한다(동작 불변).
    """
    return {
        "source": "disk_cache+fetcher",   # Task 1.2 에서 "sqlite" 로 전환
        "project_count": len(PROJECT_KEYS),
        "projects": list(PROJECT_KEYS),
    }


__all__ = [
    "PROJECT_KEYS",
    "load_project",
    "load_all",
    "get_refresh_meta",
]
