# -*- coding: utf-8 -*-
"""qms_pro.services.fetcher_service — QMS 수집/연계 호환 래퍼.

기존 ``qms_fetch_uncached.py`` 를 **이동/수정하지 않고** 필요한 함수만 얇게 재노출한다
(Phase 2-3). 네트워크/API 호출, 캐시, 연계, 완료판정/C·T 로직은 원본 그대로이며
이 모듈은 import 재노출만 담당한다(반환 형식 동일).

대시보드는 아직 이 래퍼를 사용하지 않는다. 현재 사용처는 baseline 스냅샷뿐이다.
"""
from __future__ import annotations

from qms_fetch_uncached import (
    # 프로젝트별 수집 구현 (대시보드 캐시 래퍼가 호출하는 본문)
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
    # 연계 인덱스 구축/적용
    build_and_apply_linkage,
    # 전체 스냅샷 일괄 수집(baseline live 모드에서 사용)
    run_all_snapshot_fetches,
)

__all__ = [
    "fetch_list_project_impl",
    "fetch_oos_data_impl",
    "fetch_deviation_data_impl",
    "fetch_devout_data_stub_impl",
    "fetch_capa_data_impl",
    "fetch_change_data_impl",
    "fetch_complain_data_impl",
    "fetch_capaai_data_impl",
    "fetch_changeai_data_impl",
    "fetch_changeimpact_data_impl",
    "fetch_changeout_data_impl",
    "fetch_devoutai_data_impl",
    "fetch_transfer_data_impl",
    "fetch_validity_data_impl",
    "fetch_investigation_data_impl",
    "build_and_apply_linkage",
    "run_all_snapshot_fetches",
]
