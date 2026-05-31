# -*- coding: utf-8 -*-
"""qms_pro.ui.components — 공통 UI 컴포넌트 (추가 전용, Phase 3-1-a).

설계 원칙
---------
- 기존 ``qms_styles.py`` 의 단순 컴포넌트는 ``qms_pro.ui.theme`` 래퍼를 통해 **동일 객체로
  재노출**한다(시각/동작 변경 0).
- 신규 함수는 보수적으로 — 새 디자인 토큰/대규모 CSS/별도 다크모드 스타일을 만들지 않고
  Streamlit 기본 위젯 또는 기존 ``badge`` 를 활용한 최소 구현/초안 수준으로만 둔다.
- 이 모듈은 **추가 전용**이다. 현재 대시보드는 사용하지 않으며, 이후 단계에서 파일럿
  치환하며 검증한다.
"""
from __future__ import annotations

import streamlit as st

from qms_pro.ui import theme as T

# ── 기존 컴포넌트 재노출(동일 객체, 시각 동일) ──────────────────────────────
badge = T.badge
empty_state = T.empty_state
metric_with_sparkline = T.metric_with_sparkline
sparkline_html = T.sparkline_html
overdue_alert_card = T.overdue_alert_card


# ── 신규 컴포넌트(보수적 초안) ──────────────────────────────────────────────

def render_info_box(message: str, icon: str = "ℹ️") -> None:
    """정보 안내 박스 (st.info 수준)."""
    st.info(f"{icon} {message}")


def render_warning_box(message: str, icon: str = "⚠️") -> None:
    """경고 박스 (st.warning 수준)."""
    st.warning(f"{icon} {message}")


def render_status_badge(text: str, level: str = "blue") -> str:
    """상태 배지 — 기존 badge() 를 그대로 사용(HTML 문자열 반환)."""
    return badge(text, level)


def render_kpi_card(label: str, value, *, help: str | None = None) -> None:
    """KPI 카드(최소 구현). 현재는 st.metric 기반 초안 — 추후 공통 카드로 확장 예정.

    주의: 새 CSS/디자인 토큰을 도입하지 않는다.
    """
    st.metric(label=label, value=value, help=help)


def render_risk_card(label: str, count, *, level: str = "overdue") -> None:
    """리스크 카드(초안). 기존 overdue_alert_card 가 적합하면 위임, 아니면 metric 초안.

    추후 Risk Center 에서 공통화 시 확장. 현재는 최소 구현.
    """
    # 정수형 카운트면 기존 overdue_alert_card 스타일을 활용(시각 동일)
    try:
        n = int(count)
    except (TypeError, ValueError):
        st.metric(label=label, value=count)
        return
    overdue_alert_card(label, n, level=level)


__all__ = [
    "badge",
    "empty_state",
    "metric_with_sparkline",
    "sparkline_html",
    "overdue_alert_card",
    "render_kpi_card",
    "render_risk_card",
    "render_status_badge",
    "render_warning_box",
    "render_info_box",
]
