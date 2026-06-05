# -*- coding: utf-8 -*-
"""qms_pro.domain.linkage — qms_linkage 호환 래퍼.

이 모듈은 ``qms_linkage.py`` 의 현행 동작을 그대로 재노출하는 호환 래퍼입니다.
로직은 모두 ``qms_linkage`` 에 있으며 본 래퍼는 변경하지 않습니다.

완료 판정: ``qms_linkage._is_closed()`` 는 ``완료여부 == 'C'`` 를 종결(완료)로 간주합니다
(공식 API 문서 C=완료, T=진행중과 일치). 과거 'T=종결' 인버전 버그는 교정 완료되어,
qms_linkage_ct_validation 의 검증·실데이터 스팟체크로 'C'가 옳음을 확인했습니다.

종결순서 플래그(선종결/누락) 판정은 '기한일 연장' 전용 프로젝트(list_extension, 프로젝트
라벨='기한연장') 레코드를 '실질 자식(종결 모수)'에서 제외합니다 — 마감연장 종결이 본조치
종결로 오인되어 '종결처리 누락'이 부풀려지던 오탐을 제거(qms_linkage._is_extension_record).

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
