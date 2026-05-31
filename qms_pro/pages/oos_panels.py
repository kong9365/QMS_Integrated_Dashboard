# -*- coding: utf-8 -*-
"""qms_pro.pages.oos_panels — qms_oos_dashboard_panels 호환 래퍼.

기존 ``qms_oos_dashboard_panels.py`` 를 **이동/수정하지 않고** 공개 렌더 함수와
상수만 얇게 재노출한다(Phase 2-9). OOS 현황/경향/보고서/GMP 렌더링, Plotly/Streamlit
UI, 완료판정/건수기여도/Analyst error 계산은 모두 원본 그대로이며 이 모듈은 import
재노출만 담당한다.

대시보드는 아직 이 래퍼를 사용하지 않는다(현재 ``import qms_oos_dashboard_panels as oos_panels`` 직접 사용).
"""
from __future__ import annotations

from qms_oos_dashboard_panels import (
    MONTH_LABELS,
    get_monthly_counts_weighted,
    render_oos_status,
    render_oos_trend,
    render_oos_report,
    render_oos_gmp,
)

__all__ = [
    "MONTH_LABELS",
    "get_monthly_counts_weighted",
    "render_oos_status",
    "render_oos_trend",
    "render_oos_report",
    "render_oos_gmp",
]
