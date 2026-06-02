# -*- coding: utf-8 -*-
"""qms_pro.ui.components — 표준 UI 컴포넌트 라이브러리 (Task 3.1 실체화).

목적
----
전 워크스페이스가 **동일한 표준 컴포넌트**를 재사용하도록 모은 단일 진입점.
중복 구현(인라인 ``st.dataframe`` column_config, 선택+🔗 버튼 패턴, 즉석 스탯 카드)을
이 모듈로 흡수해 **중복 0** 을 만든다.

설계 원칙
---------
- **시각=프로토타입 · 토큰=HANDOFF · 데이터/계산 로직 불변.** 이 모듈은 *표현 계층*이며
  집계·완료판정·연계 로직을 새로 만들지 않는다(상태 Pill 의 '완료' 판정도
  ``domain.metrics.COMPLETED_KEYWORDS`` 의 기존 정의를 그대로 읽는다).
- **단일 출처**: 토큰/기본 컴포넌트는 ``qms_pro.ui.theme``(→ ``qms_styles``) 를 재노출.
  같은 객체를 재노출하므로 시각이 분기되지 않는다.
- **레이어 분리(상향 import 금지)**: 연계 드릴다운은 메인 앱의 ``show_linkage_drawer`` 를
  **콜백으로 주입**받는다. components 가 메인 앱을 import 하지 않는다.

표준 컴포넌트
------------
- ``kpi_stat_card``        : 목표 마커 진척 바 KPI 스탯 카드(1.6 표준, theme 재노출)
- ``data_table``           : st.dataframe 표준 래퍼 — 상태 Pill·진행률·모노 관리번호/D-day + 🔗
- ``status_badge`` / ``status_pill_label`` / ``derive_status`` : 상태 Pill(5종 의미색 고정)
- ``linkage_drilldown``    : 관리번호 선택 → 🔗 연계 드릴다운(콜백 주입)
- ``signal_card``          : 이상신호·기한위험 요약 카드(좌측 의미색 강조)
- ``empty_state``          : "데이터 없음" 빈 상태(theme 재노출)
"""
from __future__ import annotations

from typing import Callable, Iterable, Sequence

import pandas as pd
import streamlit as st

from qms_pro.ui import theme as T
from qms_pro.domain.metrics import COMPLETED_KEYWORDS

# ── 기존 컴포넌트 재노출(동일 객체, 시각 동일) ──────────────────────────────
kpi_stat_card = T.kpi_stat_card        # ★ 표준 KPI 스탯 카드(단일 출처)
section_header = T.section_header
badge = T.badge
empty_state = T.empty_state
metric_with_sparkline = T.metric_with_sparkline
sparkline_html = T.sparkline_html
overdue_alert_card = T.overdue_alert_card


# ════════════════════════════════════════════════════════════════════════════
# 상태 Pill — 5종 고정(의미색 고정, HANDOFF 토큰)
#   초과/임박/완료/진행/미해당. st.dataframe 은 canvas(glide-grid)라 HTML 셀이 불가하므로
#   표 셀에는 **이모지 프리픽스**("🔴 초과")로, 인라인에는 theme.badge HTML 로 렌더한다.
# ════════════════════════════════════════════════════════════════════════════
STATUS_PILL: dict[str, dict] = {
    "초과":  {"emoji": "🔴", "color": T.SEM_DANGER,  "badge": "red"},
    "임박":  {"emoji": "🟠", "color": T.SEM_WARN,    "badge": "yellow"},
    "완료":  {"emoji": "🟢", "color": T.SEM_OK,      "badge": "green"},
    "진행":  {"emoji": "🔵", "color": T.SEM_INFO,    "badge": "blue"},
    "미해당": {"emoji": "⚪", "color": T.SEM_NEUTRAL, "badge": "blue"},
}
_PILL_ORDER = ["초과", "임박", "완료", "진행", "미해당"]


def status_pill_label(state: str) -> str:
    """표 셀용 — '🔴 초과' 형태(이모지 프리픽스)."""
    meta = STATUS_PILL.get(state)
    return f"{meta['emoji']} {state}" if meta else (state or "")


def status_badge(state: str) -> str:
    """인라인 HTML 뱃지(theme.badge 재사용) 문자열 반환."""
    meta = STATUS_PILL.get(state)
    return badge(state, meta["badge"]) if meta else badge(state or "—", "blue")


def _completed_mask(df: pd.DataFrame) -> pd.Series:
    """행 단위 완료 마스크 — domain.metrics 와 **동일 정의**(키워드 우선, 없으면 완료여부=='C').

    새 판정 로직이 아니라 ``weighted_metric_completed`` 의 행 단위 표시용 재사용.
    """
    if "진행상태" in df.columns:
        return df["진행상태"].astype(str).str.contains(
            "|".join(COMPLETED_KEYWORDS), case=False, na=False
        )
    if "완료여부" in df.columns:
        return df["완료여부"] == "C"
    return pd.Series(False, index=df.index)


def derive_status(df: pd.DataFrame, imminent_days: int = 7) -> pd.Series:
    """표시용 상태(초과/임박/완료/진행/미해당) Series 파생 — 기존 컬럼만 읽는다.

    우선순위: 완료(최우선) > 초과(D-day<0) > 임박(0≤D-day≤N) > 진행(D-day>N) > 미해당(D-day 없음).
    임박 기준일 N 은 상단 필터바('D-day 임박 7일')·DATA_MAPPING §1 과 동일한 기본 7.
    """
    out = pd.Series(["미해당"] * len(df), index=df.index, dtype=object)
    if "D-day" in df.columns:
        dd = pd.to_numeric(df["D-day"], errors="coerce")
        has = dd.notna()
        out[has & (dd < 0)] = "초과"
        out[has & (dd >= 0) & (dd <= imminent_days)] = "임박"
        out[has & (dd > imminent_days)] = "진행"
    out[_completed_mask(df)] = "완료"   # 완료가 최우선(종결된 항목)
    return out


def status_label_series(df: pd.DataFrame, imminent_days: int = 7) -> pd.Series:
    """derive_status → 표 셀용 이모지 프리픽스 라벨 Series."""
    return derive_status(df, imminent_days=imminent_days).map(status_pill_label)


# ════════════════════════════════════════════════════════════════════════════
# 표준 데이터 테이블 — st.dataframe + 표준 column_config
#   · 모노 관리번호/상위번호/D-day/건수기여도(정렬·자릿수 안정)
#   · 상태 Pill 컬럼(옵션) · 진행률 ProgressColumn(옵션)
#   · 호출부 column_config override 우선
# ════════════════════════════════════════════════════════════════════════════
def _std_column_config(df: pd.DataFrame, progress_cols: Sequence[str], mono_extra: Sequence[str]) -> dict:
    cc: dict = {}
    if "관리번호" in df.columns:
        cc["관리번호"] = st.column_config.NumberColumn("관리번호", format="%d", help="QMS 관리번호")
    if "상위번호" in df.columns:
        cc["상위번호"] = st.column_config.NumberColumn("상위번호", format="%d")
    if "D-day" in df.columns:
        cc["D-day"] = st.column_config.NumberColumn("D-day", format="%d일")
    if "건수기여도" in df.columns:
        cc["건수기여도"] = st.column_config.NumberColumn("건수기여도", format="%.5f")
    for m in mono_extra:
        if m in df.columns:
            cc[m] = st.column_config.NumberColumn(m, format="%d")
    for pc in progress_cols:
        if pc in df.columns:
            cc[pc] = st.column_config.ProgressColumn(pc, format="%.0f%%", min_value=0, max_value=100)
    return cc


def data_table(
    df: pd.DataFrame,
    *,
    status: bool = False,
    status_imminent_days: int = 7,
    progress_cols: Sequence[str] = (),
    mono_extra: Sequence[str] = (),
    column_config: dict | None = None,
    height: int | None = None,
    hide_index: bool = True,
    use_container_width: bool = True,
    key: str | None = None,
) -> None:
    """표준 상세 데이터 테이블.

    Parameters
    ----------
    status : True 면 맨 앞에 '상태' Pill 컬럼을 파생 추가(초과/임박/완료/진행/미해당).
             표의 슬라이스에 ``진행상태``/``D-day`` 가 있어야 의미 있다.
    progress_cols : ProgressColumn(진행률 바) 로 렌더할 컬럼명들(0~100).
    mono_extra : 추가로 정수 모노 포맷할 컬럼명들.
    column_config : 호출부 override(여기 지정값이 표준값보다 **우선**).
    """
    show = df.copy()
    pill_col = None
    if status:
        pill_col = "상태" if "상태" not in show.columns else "상태(파생)"
        show.insert(0, pill_col, status_label_series(show, imminent_days=status_imminent_days).values)
    cc = _std_column_config(show, progress_cols=progress_cols, mono_extra=mono_extra)
    if pill_col:
        cc[pill_col] = st.column_config.TextColumn(pill_col, help="초과/임박/완료/진행/미해당", width="small")
    if column_config:
        cc.update(column_config)   # 호출부 우선
    st.dataframe(
        show, use_container_width=use_container_width, hide_index=hide_index,
        height=height, column_config=cc, key=key,
    )


# ════════════════════════════════════════════════════════════════════════════
# 연계 드릴다운 진입 — 관리번호 선택 → 🔗 → on_select(prno)
#   메인 앱의 show_linkage_drawer 를 콜백으로 주입(레이어 분리).
# ════════════════════════════════════════════════════════════════════════════
def linkage_drilldown(
    prnos: Iterable,
    *,
    key: str,
    on_select: Callable[[str], None],
    label: str = "🔗 연계 보기",
    caption: str | None = None,
) -> None:
    """상세 표의 관리번호를 선택해 🔗 연계 드릴다운(체인·종결여부·지연일)을 호출."""
    opts = [str(p) for p in prnos]
    if not opts:
        return
    if caption:
        st.caption(caption)
    c_sel, c_btn = st.columns([3, 1])
    with c_sel:
        sel = st.selectbox("관리번호", opts, key=f"linkdd_sel_{key}", label_visibility="collapsed")
    with c_btn:
        if st.button(label, key=f"linkdd_btn_{key}", use_container_width=True):
            on_select(sel)


# ════════════════════════════════════════════════════════════════════════════
# 신호/요약 카드 — 좌측 의미색 강조(이상신호·기한위험 공통)
#   값/델타는 호출부가 계산해 전달(임의 생성 금지).
# ════════════════════════════════════════════════════════════════════════════
_TONE: dict[str, tuple[str, str]] = {
    "danger":  (T.SEM_DANGER,  "🚨"),
    "warn":    (T.SEM_WARN,    "⚠️"),
    "ok":      (T.SEM_OK,      "✅"),
    "info":    (T.SEM_INFO,    "ℹ️"),
    "neutral": (T.SEM_NEUTRAL, "•"),
}


def signal_card(
    label: str,
    value,
    *,
    tone: str = "info",
    sub: str | None = None,
    icon: str | None = None,
) -> None:
    """신호/요약 카드(HTML). kpi_stat_card 와 동일 결(좌측 5px 의미색 + 카드 그림자)."""
    color, def_icon = _TONE.get(tone, _TONE["info"])
    ic = icon if icon is not None else def_icon
    prefix = f"{ic} " if ic else ""   # 라벨에 이미 이모지가 있으면 icon="" 로 중복 방지
    sub_html = (
        f'<div style="font-size:0.72rem;color:{T.SEM_NEUTRAL};margin-top:3px">{sub}</div>'
        if sub else ""
    )
    # HTML 은 한 줄(들여쓰기 금지) — 4칸 이상 들여쓰면 streamlit markdown 이 코드블록 처리.
    card = (
        f'<div style="background:#f8f9fa;border-radius:12px;padding:12px 16px;'
        f'border-left:5px solid {color};box-shadow:0 2px 8px rgba(0,0,0,0.06);'
        f'display:flex;flex-direction:column;gap:2px">'
        f'<span style="font-size:0.78rem;color:{T.SEM_NEUTRAL};font-weight:600;letter-spacing:0.2px">{prefix}{label}</span>'
        f'<span class="qms-num" style="font-size:1.5rem;font-weight:700;color:{T.NAVY_700};line-height:1.15">{value}</span>'
        f'{sub_html}</div>'
    )
    st.markdown(card, unsafe_allow_html=True)


__all__ = [
    # KPI / 카드
    "kpi_stat_card",
    "signal_card",
    "empty_state",
    # 표
    "data_table",
    # 상태 Pill
    "STATUS_PILL",
    "status_pill_label",
    "status_badge",
    "derive_status",
    "status_label_series",
    # 연계
    "linkage_drilldown",
    # 재노출(단일 출처)
    "section_header",
    "badge",
    "metric_with_sparkline",
    "sparkline_html",
    "overdue_alert_card",
]
