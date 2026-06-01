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

import json as _json
import os as _os
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
# 연계 ctx 재구성용(드릴다운). build_linkage 는 슬림 행에서 인덱스만 만드는 경량 연산
# (apply_linkage_to_dataframe 의 무거운 셀-머지와 달리 네트워크/대량 쓰기 없음).
# build_linkage=qms_linkage, _df_to_linkage_rows=qms_fetch_uncached(원본 위치 그대로 재사용).
from qms_linkage import build_linkage as _build_linkage
from qms_fetch_uncached import _df_to_linkage_rows as _slim_rows

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


def cache_key_for(project: str):
    """프로젝트 키 → 디스크 캐시 키. stub(캐시 미적용)은 None. refresh_job 과 공유."""
    if project not in _PROJECT_LOADERS:
        raise KeyError(f"unknown project key: {project!r}")
    return _PROJECT_LOADERS[project][0]


# 캐시 전용 읽기에서 만료를 무시할 때 쓰는 매우 큰 TTL(사실상 무한).
# 신선도는 refresh_job 이 책임지므로, 앱은 "마지막 정상 캐시" 를 항상 읽어야 한다
# (네트워크 차단 시에도 마지막 스냅샷으로 렌더 = 운영 디커플링).
_CACHE_ONLY_TTL = 10 ** 12


def load_project(project: str, *, disk_cache=None, cache_only: bool = False):
    """단일 프로젝트 데이터를 읽어 ``(DataFrame, error)`` 로 반환.

    - ``cache_only=False``(기본): 디스크 캐시 우선, 미스 시 fetch_*_impl 호출(기존 동작).
    - ``cache_only=True``: **캐시만** 읽는다(라이브 fetch 호출 안 함). 만료 무시하고
      마지막 정상 캐시를 반환. 캐시가 아예 없으면 ``(빈 DataFrame, "no_cache")``.
      → 앱(화면)은 이 모드를 쓴다. 수집은 refresh_job 만 담당(운영 디커플링).
    """
    if project not in _PROJECT_LOADERS:
        raise KeyError(f"unknown project key: {project!r}")
    dc = disk_cache if disk_cache is not None else _DC
    cache_key, impl = _PROJECT_LOADERS[project]

    if cache_only:
        if cache_key is None:
            # stub(일탈외주): 캐시 미적용 — 기존과 동일하게 직접 생성(네트워크 미사용 stub)
            return impl()
        cached = dc.load(cache_key, ttl=_CACHE_ONLY_TTL)  # 만료 무시
        if cached is not None:
            return cached
        import pandas as _pd
        return _pd.DataFrame(), "no_cache"

    # 기존 동작(캐시 우선 + 미스 시 라이브)
    if cache_key is None:
        return impl()
    return _with_disk_cache(cache_key, impl, dc)


def load_all(*, disk_cache=None, cache_only: bool = False) -> dict:
    """16개 프로젝트 전체를 읽어 ``{project_key: (DataFrame, error)}`` 로 반환.

    주: 메인 앱은 진행률/병렬(ThreadPoolExecutor) 표시를 위해 개별 ``load_project``
    를 직접 호출한다. 이 함수는 headless(수집 작업/검증)에서 일괄 로드용으로 제공한다.
    """
    return {pk: load_project(pk, disk_cache=disk_cache, cache_only=cache_only)
            for pk in PROJECT_KEYS}


def get_refresh_meta() -> dict:
    """갱신 메타데이터. refresh_job 이 기록한 ``.qms_cache/_meta.json`` 을 읽어 반환.

    _meta.json 이 없으면(아직 refresh_job 미실행) source="none" 으로 보고한다.
    화면 상단의 "마지막 갱신/수집 상태 N/16" 표기에 사용(Task 1.3 연계).
    """
    meta_path = _os.path.join(_DC.cache_dir(), "_meta.json")
    if _os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = _json.load(f)
            meta["source"] = "qms_cache(parquet)"
            return meta
        except Exception as e:  # noqa: BLE001
            return {"source": "error", "error": str(e), "projects": {}}
    return {
        "source": "none",   # refresh_job 미실행
        "last_refresh": None,
        "ok_count": 0,
        "total_count": len(PROJECT_KEYS),
        "projects": {},
    }


def build_ctx(all_dfs: dict):
    """드릴다운용 연계 ctx 만 경량 재구성(컬럼 머지는 하지 않음).

    캐시에는 이미 refresh_job 이 적용한 연계 컬럼(최종 종결 여부(체인) 등)이 들어 있으므로,
    앱은 무거운 ``apply_linkage_to_dataframe`` 를 다시 돌릴 필요가 없다. 단, 행 클릭
    드릴다운(부모/자식 상세)은 그래프 인덱스(ctx)가 필요하므로 ``build_linkage`` 로
    슬림 행에서 인덱스만 빠르게 만든다.
    """
    rows: list = []
    for df in all_dfs.values():
        rows.extend(_slim_rows(df))
    return _build_linkage(rows)


__all__ = [
    "PROJECT_KEYS",
    "cache_key_for",
    "load_project",
    "load_all",
    "get_refresh_meta",
    "build_ctx",
]
