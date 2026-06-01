# -*- coding: utf-8 -*-
"""qms_pro.ui.theme — qms_styles 호환 래퍼.

기존 ``qms_styles.py`` 를 **이동/수정하지 않고** 공개 색상 상수와 UI 컴포넌트 함수를
얇게 재노출하는 **facade** 다. CSS/색상/컴포넌트 동작, 다크모드 로직, 사이드바 토글 JS는
모두 원본 그대로이며 이 모듈은 import 재노출만 담당한다.

대시보드 메인(``QMS_Integrated_Dashboard_v2.py``)은 이 facade 를 ``import ... as S`` 로
**실제 사용한다**. 위임 구조 설명은 ``docs/ARCHITECTURE.md`` 참조.
"""
from __future__ import annotations

from qms_styles import (
    # 색상 상수(별칭)
    PRIMARY,
    PRIMARY_L,
    ACCENT,
    LIGHT_BG,
    BORDER,
    GREEN,
    YELLOW,
    RED,
    ORANGE,
    CHART_COLORS,
    # 디자인 토큰(Task 1.6 — 단일 정의 재노출)
    NAVY_900, NAVY_800, NAVY_700, NAVY_600, NAVY_400, ACCENT_BLUE,
    SEM_DANGER, SEM_WARN, SEM_OK, SEM_INFO, SEM_LINK, SEM_NEUTRAL,
    CHART_SEQUENCE, CHART_SURFACE, CHART_GRID, FONT_BODY, FONT_MONO,
    # 레이아웃/헤더/푸터
    apply_global_css,
    section_header,
    render_header,
    render_footer,
    # 컴포넌트
    badge,
    empty_state,
    inject_sidebar_toggle,
    dark_mode_toggle,
    sparkline_html,
    metric_with_sparkline,
    kpi_gauge_improved,
    filter_reset_button,
    cache_age_bar,
    overdue_alert_card,
)

__all__ = [
    "PRIMARY",
    "PRIMARY_L",
    "ACCENT",
    "LIGHT_BG",
    "BORDER",
    "GREEN",
    "YELLOW",
    "RED",
    "ORANGE",
    "CHART_COLORS",
    "NAVY_900", "NAVY_800", "NAVY_700", "NAVY_600", "NAVY_400", "ACCENT_BLUE",
    "SEM_DANGER", "SEM_WARN", "SEM_OK", "SEM_INFO", "SEM_LINK", "SEM_NEUTRAL",
    "CHART_SEQUENCE", "CHART_SURFACE", "CHART_GRID", "FONT_BODY", "FONT_MONO",
    "apply_global_css",
    "section_header",
    "render_header",
    "render_footer",
    "badge",
    "empty_state",
    "inject_sidebar_toggle",
    "dark_mode_toggle",
    "sparkline_html",
    "metric_with_sparkline",
    "kpi_gauge_improved",
    "filter_reset_button",
    "cache_age_bar",
    "overdue_alert_card",
]
