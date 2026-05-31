# -*- coding: utf-8 -*-
"""qms_pro.ui.filters — 공통 필터/검색 헬퍼 (추가 전용, Phase 3-1-b).

설계 원칙 (무손실 교체 가능)
---------------------------
- ``apply_sidebar_filters`` / ``apply_sidebar_filters_no_year`` / ``apply_search_filters`` 는
  ``QMS_Integrated_Dashboard_v2.py`` 의 현행 필터 로직을 **그대로 옮긴 순수 함수**다.
  단, 대시보드는 모듈 전역(selected_years/status_filter/dday_filter/YEAR_FILTER_COL)을 쓰지만
  여기서는 **인자로 받아** 동일 결과를 내도록 파라미터화했다(테스트·재사용 용이).
- 위젯 렌더 헬퍼는 기존 사이드바와 동일한 라벨/옵션을 쓰는 얇은 래퍼다(키만 인자화).
- 이 모듈은 **추가 전용**이다. 현재 대시보드는 사용하지 않으며, 이후 단계에서 호출부를
  1:1 치환하며 baseline/육안 검증한다.

핵심 도메인 규칙(변경 금지)
- 완료판정: ``완료여부 == 'C'`` (대시보드 현행과 동일)
- D-day 임박: ``0 <= D-day <= 7`` / 기한 초과: ``D-day < 0``
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

# 사이드바/검색 옵션 라벨 (현행 대시보드와 동일)
STATUS_OPTIONS = ["전체", "진행중", "완료"]
DDAY_OPTIONS = ["전체", "D-day 임박 (7일)", "기한 초과"]
YEAR_BASIS_OPTIONS = ("발견일시", "등록일")


# ── 데이터 필터 적용(순수 함수) ─────────────────────────────────────────────

def apply_sidebar_filters(
    df: pd.DataFrame,
    *,
    selected_years,
    status_filter: str,
    dday_filter: str,
    year_filter_col: str = "연도",
) -> pd.DataFrame:
    """연도·진행상태·기한일 필터 적용. 원본: QMS_Integrated_Dashboard_v2.apply_filters."""
    if df.empty:
        return df
    result = df.copy()
    ycol = year_filter_col if year_filter_col in result.columns else "연도"
    if selected_years and ycol in result.columns:
        result = result[result[ycol].isin(selected_years)]
    if status_filter == "진행중" and "완료여부" in result.columns:
        result = result[result["완료여부"] != "C"]
    elif status_filter == "완료" and "완료여부" in result.columns:
        result = result[result["완료여부"] == "C"]
    if dday_filter == "D-day 임박 (7일)" and "D-day" in result.columns:
        result = result[(result["D-day"].notna()) & (result["D-day"] >= 0) & (result["D-day"] <= 7)]
    elif dday_filter == "기한 초과" and "D-day" in result.columns:
        result = result[(result["D-day"].notna()) & (result["D-day"] < 0)]
    return result


def apply_sidebar_filters_no_year(
    df: pd.DataFrame,
    *,
    status_filter: str,
    dday_filter: str,
) -> pd.DataFrame:
    """연도만 제외한 필터(진행상태·기한일). 원본: apply_filters_no_year. 전년 비교용."""
    if df.empty:
        return df
    result = df.copy()
    if status_filter == "진행중" and "완료여부" in result.columns:
        result = result[result["완료여부"] != "C"]
    elif status_filter == "완료" and "완료여부" in result.columns:
        result = result[result["완료여부"] == "C"]
    if dday_filter == "D-day 임박 (7일)" and "D-day" in result.columns:
        result = result[(result["D-day"].notna()) & (result["D-day"] >= 0) & (result["D-day"] <= 7)]
    elif dday_filter == "기한 초과" and "D-day" in result.columns:
        result = result[(result["D-day"].notna()) & (result["D-day"] < 0)]
    return result


def apply_search_filters(
    df: pd.DataFrame,
    *,
    qms_no: str = "",
    title: str = "",
    registrant: str = "",
    lot: str = "",
    item_code: str = "",
) -> pd.DataFrame:
    """원본 데이터 검색 필터(QMS번호/제목/등록자/제조번호/품목코드).

    원본: QMS_Integrated_Dashboard_v2.render_raw_data_section 내 contains 매칭과 동일.
    - 관리번호: 대소문자 구분, astype(str) 포함 매칭
    - 제목·등록자: 대소문자 무시, fillna("") 포함 매칭
    - 제조번호·품목코드: 대소문자 무시, strip 후 astype(str) 포함 매칭
    각 조건은 값이 있고 해당 컬럼이 존재할 때만 적용.
    """
    if df.empty:
        return df
    result = df
    if qms_no and "관리번호" in result.columns:
        q = str(qms_no).strip()
        result = result[result["관리번호"].astype(str).str.contains(q, na=False)]
    if title and "제목" in result.columns:
        result = result[result["제목"].fillna("").str.contains(title, case=False, na=False)]
    if registrant and "등록자" in result.columns:
        result = result[result["등록자"].fillna("").str.contains(registrant, case=False, na=False)]
    if lot and "제조번호" in result.columns:
        result = result[result["제조번호"].astype(str).str.contains(str(lot).strip(), case=False, na=False)]
    if item_code and "품목코드" in result.columns:
        result = result[result["품목코드"].astype(str).str.contains(str(item_code).strip(), case=False, na=False)]
    return result


# ── 위젯 렌더 헬퍼(얇은 래퍼, 키만 인자화) ─────────────────────────────────

def sidebar_status_filter(key: str = "status_filter") -> str:
    return st.sidebar.radio("진행상태", STATUS_OPTIONS, horizontal=True, key=key)


def sidebar_dday_filter(key: str = "dday_filter") -> str:
    return st.sidebar.radio("기한일 기준", DDAY_OPTIONS, key=key)


def sidebar_year_filter(years, default, key: str = "year_select") -> list:
    return st.sidebar.multiselect("연도", years, default=default, key=key)


# ── 입력 UI 함수(Raw Data Search / 출하 전 로트 모니터링용) ──────────────────
# 주의: 이 함수들은 "입력 위젯 렌더 + 값 반환"만 한다. DataFrame 필터링·자동 판정은
#       하지 않는다(필터링은 apply_* 순수 함수 또는 호출부에서 별도로 수행).

def render_text_filter(
    label: str,
    *,
    key: str,
    placeholder: str = "",
    help: str | None = None,
) -> str:
    """단일 텍스트 입력 공통 함수. st.text_input 만 사용, 문자열 반환."""
    return st.text_input(label, placeholder=placeholder, help=help, key=key)


def render_date_range_filter(
    label: str,
    *,
    key_prefix: str,
    default_start=None,
    default_end=None,
) -> tuple:
    """조회 시작일/종료일 입력. st.columns(2)+st.date_input, (start, end) 반환.

    복잡한 검증(시작>종료 등)은 호출부 책임 — 여기서는 단순 입력만.
    """
    c1, c2 = st.columns(2)
    with c1:
        start = st.date_input(f"{label} 시작", value=default_start, key=f"{key_prefix}_start")
    with c2:
        end = st.date_input(f"{label} 종료", value=default_end, key=f"{key_prefix}_end")
    return start, end


def render_project_filter(
    options: list[str],
    *,
    key: str,
    default: list[str] | None = None,
    label: str = "조회 프로젝트",
) -> list[str]:
    """프로젝트 다중 선택. st.multiselect 만 사용."""
    return st.multiselect(label, options, default=default or [], key=key)


def render_status_filter(
    *,
    key: str,
    label: str = "진행상태",
    options: list[str] | None = None,
    default: str = "전체",
) -> str:
    """완료/진행중/전체 선택. 선택값 문자열만 반환(필터링 안 함).

    참고(필터링 시 기준, 여기서는 미적용): 완료여부=='C' → 완료, =='T' → 진행중.
    """
    opts = options or STATUS_OPTIONS
    idx = opts.index(default) if default in opts else 0
    return st.radio(label, opts, index=idx, horizontal=True, key=key)


def render_deadline_filter(
    *,
    key: str,
    label: str = "기한 상태",
    options: list[str] | None = None,
    default: str = "전체",
) -> str:
    """기한 상태 선택. 선택값 문자열만 반환(필터링 안 함)."""
    opts = options or ["전체", "D-day 임박", "기한 초과"]
    idx = opts.index(default) if default in opts else 0
    return st.radio(label, opts, index=idx, horizontal=True, key=key)


def render_raw_search_filters(*, key_prefix: str = "raw_search") -> dict:
    """Raw Data Search 검색 입력 묶음. dict 반환(필터링 안 함)."""
    return {
        "qms_no": render_text_filter("QMS번호", key=f"{key_prefix}_qms_no", placeholder="예: 7078"),
        "title": render_text_filter("제목", key=f"{key_prefix}_title", placeholder="키워드"),
        "registrant": render_text_filter("등록자", key=f"{key_prefix}_registrant", placeholder="이름"),
        "lot_no": render_text_filter("제조번호", key=f"{key_prefix}_lot_no", placeholder="일부 입력"),
        "item_code": render_text_filter("품목코드", key=f"{key_prefix}_item_code", placeholder="예: 23262"),
    }


def render_lot_release_filters(
    *,
    key_prefix: str = "lot_release",
    project_options: list[str] | None = None,
) -> dict:
    """출하 전 로트 종합 모니터링 입력 묶음. dict 반환.

    입력 위젯만 렌더하고 값을 모아 반환한다. 출하 가부 자동 판정·검토 안내 문구는 넣지 않는다
    (그 책임은 화면/호출부에 있음).
    """
    item_code = render_text_filter("품목코드", key=f"{key_prefix}_item_code", placeholder="예: 23262")
    lot_no = render_text_filter("제조번호 / Lot No.", key=f"{key_prefix}_lot_no", placeholder="일부 입력")
    item_name = render_text_filter("품목명(선택)", key=f"{key_prefix}_item_name", placeholder="선택")
    date_start, date_end = render_date_range_filter("조회기간(선택)", key_prefix=f"{key_prefix}_date")
    projects = render_project_filter(project_options or [], key=f"{key_prefix}_projects")
    status = render_status_filter(key=f"{key_prefix}_status")
    deadline = render_deadline_filter(key=f"{key_prefix}_deadline")
    overdue_only = st.checkbox("기한초과만 보기", value=False, key=f"{key_prefix}_overdue_only")
    open_linkage_only = st.checkbox("연계 미완료만 보기", value=False, key=f"{key_prefix}_open_linkage_only")
    return {
        "item_code": item_code,
        "lot_no": lot_no,
        "item_name": item_name,
        "date_start": date_start,
        "date_end": date_end,
        "projects": projects,
        "status": status,
        "deadline": deadline,
        "overdue_only": overdue_only,
        "open_linkage_only": open_linkage_only,
    }


__all__ = [
    "STATUS_OPTIONS",
    "DDAY_OPTIONS",
    "YEAR_BASIS_OPTIONS",
    "apply_sidebar_filters",
    "apply_sidebar_filters_no_year",
    "apply_search_filters",
    "sidebar_status_filter",
    "sidebar_dday_filter",
    "sidebar_year_filter",
    "render_text_filter",
    "render_date_range_filter",
    "render_project_filter",
    "render_status_filter",
    "render_deadline_filter",
    "render_raw_search_filters",
    "render_lot_release_filters",
]
