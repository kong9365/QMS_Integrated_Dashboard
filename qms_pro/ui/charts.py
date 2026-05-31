# -*- coding: utf-8 -*-
"""qms_pro.ui.charts — Plotly 레이아웃/렌더 공통 헬퍼 (차트 팩토리).

목적
----
대시보드/oos_panels 에 반복되는 Plotly 보일러플레이트
``fig.update_layout(height=.., margin=dict(l=..,r=..,t=..,b=..), plot_bgcolor="white", ...)``
+ ``st.plotly_chart(fig, use_container_width=True, key=..)`` 를 한 곳으로 모은다.

설계 원칙 (시각 동일 보존)
--------------------------
- height/margin/plot_bgcolor/legend 등 **시각에 영향 주는 값은 모두 호출자가 지정**한다.
  헬퍼는 "지정된 것만" 적용하며(None 이면 건드리지 않음), 기본값으로 시각을 바꾸지 않는다.
  → 기존 호출부를 이 헬퍼로 1:1 치환해도 결과가 동일하도록 설계.
- 이 모듈은 **추가 전용**이다. 현재 대시보드는 아직 사용하지 않으며, 이후 단계에서
  호출부를 점진적으로 치환하며 baseline/육안 검증한다.

대표 마진 프리셋(자주 쓰이는 값) — 편의용이며 강제하지 않는다.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import streamlit as st

# 자주 반복되는 마진 (l, r, t, b). 강제값 아님 — 호출부 가독성용.
MARGIN_TIGHT = (10, 10, 10, 10)   # 대시보드에서 가장 흔한 값(28+회)
MARGIN_PIE = (0, 0, 10, 10)       # 파이/도넛에서 흔함
MARGIN_AXIS = (40, 20, 30, 40)    # 축 라벨 있는 라인/바에서 흔함


def _margin_dict(margin) -> dict | None:
    """margin 을 dict(l,r,t,b) 로 정규화. None→None, dict→그대로, (l,r,t,b)→dict."""
    if margin is None:
        return None
    if isinstance(margin, Mapping):
        return dict(margin)
    if isinstance(margin, Sequence) and len(margin) == 4:
        l, r, t, b = margin
        return dict(l=l, r=r, t=t, b=b)
    raise ValueError(f"margin must be None, dict, or (l,r,t,b) tuple — got {margin!r}")


def apply_layout(
    fig,
    *,
    height: int | None = None,
    margin=None,
    plot_bgcolor: str | None = None,
    legend: Mapping | None = None,
    barmode: str | None = None,
    showlegend: bool | None = None,
    **extra: Any,
):
    """지정된 레이아웃 항목만 fig 에 적용하고 fig 를 반환.

    None 인 인자는 적용하지 않으므로(레이아웃 미변경), 기존 호출부를 그대로 옮길 수 있다.
    margin 은 (l,r,t,b) 튜플 또는 dict 모두 허용.
    """
    layout: dict[str, Any] = {}
    if height is not None:
        layout["height"] = height
    md = _margin_dict(margin)
    if md is not None:
        layout["margin"] = md
    if plot_bgcolor is not None:
        layout["plot_bgcolor"] = plot_bgcolor
    if legend is not None:
        layout["legend"] = dict(legend)
    if barmode is not None:
        layout["barmode"] = barmode
    if showlegend is not None:
        layout["showlegend"] = showlegend
    layout.update(extra)
    if layout:
        fig.update_layout(**layout)
    return fig


def render_chart(fig, *, key: str | None = None, use_container_width: bool = True, config: Mapping | None = None) -> None:
    """st.plotly_chart 공통 래퍼. 기존 호출부와 동일 인자(use_container_width 기본 True)."""
    kwargs: dict[str, Any] = {"use_container_width": use_container_width}
    if key is not None:
        kwargs["key"] = key
    if config is not None:
        kwargs["config"] = dict(config)
    st.plotly_chart(fig, **kwargs)


def styled_chart(
    fig,
    *,
    key: str | None = None,
    use_container_width: bool = True,
    config: Mapping | None = None,
    **layout: Any,
) -> None:
    """apply_layout + render_chart 를 한 번에. layout 키워드는 apply_layout 으로 전달."""
    apply_layout(fig, **layout)
    render_chart(fig, key=key, use_container_width=use_container_width, config=config)
