# -*- coding: utf-8 -*-
"""qms_pro.domain.linkage — qms_linkage 호환 래퍼.

이 모듈은 ``qms_linkage.py`` 의 현행 동작을 그대로 재노출하는 호환 래퍼입니다.
완료판정(C/T) 인버전 의심 사항은 별도 검증 트랙에서 처리하며,
본 래퍼에서는 결과 동등성을 위해 기존 로직을 변경하지 않습니다.

구체적으로, ``qms_linkage._is_closed()`` 는 ``완료여부 == 'T'`` 를 종결로 간주하는데
이는 공식 API 문서(T=진행중, C=완료)와 상충하는 것으로 의심됩니다. 그러나 이번
래퍼 단계에서는 **수정하지 않고 그대로 위임**하여 연계 집계 수치를 보존합니다.

참고: ``build_and_apply_linkage`` 는 qms_linkage 가 아니라 qms_fetch_uncached 에 정의되어
있으며, 이미 ``qms_pro.services.fetcher_service`` 에서 재노출됩니다(여기서는 제외).

대시보드는 아직 이 래퍼를 사용하지 않습니다.
"""
from __future__ import annotations

from qms_linkage import (
    LinkageContext,
    build_linkage,
    resolve_chain,
    summarize_children,
    summarize_parent,
    linkage_columns_for,
    apply_linkage_to_dataframe,
)

__all__ = [
    "LinkageContext",
    "build_linkage",
    "resolve_chain",
    "summarize_children",
    "summarize_parent",
    "linkage_columns_for",
    "apply_linkage_to_dataframe",
]
