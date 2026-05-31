# -*- coding: utf-8 -*-
"""qms_pro.services.qms_client — QMS_API 공개 심볼 호환 래퍼.

기존 ``QMS_API.py`` 를 **이동/수정하지 않고** 공개 심볼만 얇게 재노출한다(Phase 2-4).
로그인/세션/인증/파서 로직은 원본 그대로이며, 반환 형식도 동일하다.

보안
----
- 자격증명 상수(CLIENT_SECRET / LOGIN_PASSWORD 등)는 **의도적으로 재노출하지 않는다.**
- ``QMS_API`` import 시 원본이 출력하는 폴백 보안 경고는 그대로 발생할 수 있으나,
  이 래퍼는 secret 관련 로그를 추가하지 않는다.

대시보드는 아직 이 래퍼를 사용하지 않는다.
"""
from __future__ import annotations

from QMS_API import (
    # 엔드포인트/기본 URL (비밀값 아님)
    API_BASE_URL,
    API_LIST_ENDPOINT,
    API_DETAIL_ENDPOINT,
    API_LOGIN_ENDPOINT,
    # 세션/클라이언트
    QMSAPIClient,
    get_shared_client,
    reset_shared_client,
    # 응답 추출/리스트 파서/태스크 ID 해석
    extract_data_from_response,
    parse_list_only,
    resolve_list_task_id,
    task_id_detail_candidates,
    enrich_with_raw_extention,
    # OOS/조사 계열 파서
    parse_qms_detail_json,
    parse_lab_investigation_json,
    parse_full_investigation_json,
    parse_conclusion_json,
    parse_activity_log_json,
    parse_investigation_json,
    # 프로젝트별 파서
    parse_deviation_json,
    parse_capa_json,
    parse_change_json,
    parse_complain_json,
    parse_capaactionitem_json,
    parse_changeactionitem_json,
    parse_changeimpact_json,
    parse_changeoutsourcing_json,
    parse_deviationoutsourcing_json,
    parse_deviationactionitem_json,
    parse_businesstransfer_json,
    parse_validityevaluation_json,
    # 배치/유틸
    apply_normalized_weights,
    save_results,
    run_api_mode,
)

__all__ = [
    "API_BASE_URL",
    "API_LIST_ENDPOINT",
    "API_DETAIL_ENDPOINT",
    "API_LOGIN_ENDPOINT",
    "QMSAPIClient",
    "get_shared_client",
    "reset_shared_client",
    "extract_data_from_response",
    "parse_list_only",
    "resolve_list_task_id",
    "task_id_detail_candidates",
    "enrich_with_raw_extention",
    "parse_qms_detail_json",
    "parse_lab_investigation_json",
    "parse_full_investigation_json",
    "parse_conclusion_json",
    "parse_activity_log_json",
    "parse_investigation_json",
    "parse_deviation_json",
    "parse_capa_json",
    "parse_change_json",
    "parse_complain_json",
    "parse_capaactionitem_json",
    "parse_changeactionitem_json",
    "parse_changeimpact_json",
    "parse_changeoutsourcing_json",
    "parse_deviationoutsourcing_json",
    "parse_deviationactionitem_json",
    "parse_businesstransfer_json",
    "parse_validityevaluation_json",
    "apply_normalized_weights",
    "save_results",
    "run_api_mode",
]
