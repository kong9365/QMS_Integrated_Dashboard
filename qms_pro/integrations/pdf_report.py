# -*- coding: utf-8 -*-
"""qms_pro.integrations.pdf_report — qms_pdf_report 호환 래퍼.

기존 ``qms_pdf_report.py`` 를 **이동/수정하지 않고** 공개 PDF 생성 함수만 얇게
재노출한다(Phase 2-8). fpdf2 기반 보고서 생성 로직(KPI 요약/수집현황/기한초과 목록)은
원본 그대로이며 이 모듈은 import 재노출만 담당한다.

대시보드는 아직 이 래퍼를 사용하지 않는다(현재 대시보드는 ``import qms_pdf_report`` 직접 사용).
"""
from __future__ import annotations

from qms_pdf_report import (
    generate_report,
    generate_report_streamlit,
)

__all__ = [
    "generate_report",
    "generate_report_streamlit",
]
