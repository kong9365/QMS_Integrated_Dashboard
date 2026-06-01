# -*- coding: utf-8 -*-
"""
QMS 통합 모니터링 대시보드 v2.0 (Streamlit + Plotly)
- 16개 프로젝트 통합 관리 (교육 제외)
- 탭1: 경영진 대시보드  탭2: 품질이상  탭3: CAPA관리  탭4: 변경관리
- 탭5: 고객불만  탭6: 워크플로우연계  탭7: 기한관리  탭8: 원본데이터  탭9: 설정

실행(메인 PC, LAN 공개): 이 폴더에서 `run_dashboard_LAN.bat` 또는
  streamlit run QMS_Integrated_Dashboard_v2.py
  → .streamlit/config.toml 에서 address=0.0.0.0 로 같은 네트워크 PC가 브라우저로 접속 가능.
"""
import sys, os, io, json, re, time, asyncio, logging
from collections import defaultdict
from datetime import datetime, date, timedelta

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from qms_pro.pages import oos_panels
from qms_pro.ui import theme as S
from qms_pro.ui import filters as UIF

from qms_pro.config.project_meta import PROJECT_META
from qms_pro.services import cache_service as DC
from qms_pro.services.fetcher_service import (
    fetch_list_project_impl,
    fetch_oos_data_impl,
    fetch_deviation_data_impl,
    fetch_devout_data_stub_impl,
    fetch_capa_data_impl,
    fetch_change_data_impl,
    fetch_complain_data_impl,
    fetch_capaai_data_impl,
    fetch_changeai_data_impl,
    fetch_changeimpact_data_impl,
    fetch_changeout_data_impl,
    fetch_devoutai_data_impl,
    fetch_transfer_data_impl,
    fetch_validity_data_impl,
    fetch_investigation_data_impl,
    build_and_apply_linkage,
)

from qms_pro.services.qms_client import API_BASE_URL
from qms_pro.services import data_access as DA  # 데이터 읽기 단일 진입 계층(Task 1.1)

# ============================================================================
# 런타임 예외 노이즈 억제 (탭 닫기·새로고침 시 Tornado WebSocket 잡음)
# ============================================================================

_QMS_WS_LOG_FILTER_INSTALLED = False
_QMS_WS_LOOP_HANDLER_INSTALLED = False


def _install_ws_logging_filter() -> None:
    """asyncio / tornado 로거에 남는 WebSocketClosedError 스택을 필터링."""
    global _QMS_WS_LOG_FILTER_INSTALLED
    if _QMS_WS_LOG_FILTER_INSTALLED:
        return

    class _WsClosedLogFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                if record.exc_info and record.exc_info[0] is not None:
                    name = getattr(record.exc_info[0], "__name__", "")
                    if name in ("WebSocketClosedError", "StreamClosedError"):
                        return False
            except Exception:
                pass
            try:
                msg = record.getMessage()
            except Exception:
                msg = ""
            if "WebSocketClosedError" in msg or "StreamClosedError" in msg:
                if "websocket" in msg.lower() or "tornado" in msg.lower():
                    return False
            if "Task exception was never retrieved" in msg:
                if "WebSocketClosedError" in msg or "StreamClosedError" in msg:
                    return False
            return True

    flt = _WsClosedLogFilter()
    for _name in (
        "asyncio",
        "tornado",
        "tornado.application",
        "tornado.web",
        "tornado.websocket",
        "tornado.iostream",
    ):
        logging.getLogger(_name).addFilter(flt)
    _QMS_WS_LOG_FILTER_INSTALLED = True


def _install_ws_noise_filter() -> None:
    """이벤트 루프 예외 핸들러로 동일 예외를 한 번 더 걸러냄 (루프가 있을 때만)."""
    global _QMS_WS_LOOP_HANDLER_INSTALLED
    if _QMS_WS_LOOP_HANDLER_INSTALLED:
        return
    try:
        from tornado.iostream import StreamClosedError
        from tornado.websocket import WebSocketClosedError
    except Exception:
        return

    loop = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            return
    if loop is None or loop.is_closed():
        return

    default_handler = loop.get_exception_handler()

    def _handler(loop_obj, context):
        exc = context.get("exception")
        if isinstance(exc, (WebSocketClosedError, StreamClosedError)):
            return
        if default_handler is not None:
            default_handler(loop_obj, context)
        else:
            loop_obj.default_exception_handler(context)

    loop.set_exception_handler(_handler)
    _QMS_WS_LOOP_HANDLER_INSTALLED = True


# ============================================================================
# 페이지 설정
# ============================================================================

st.set_page_config(
    page_title="QMS 통합 모니터링 v2.0",
    page_icon="▦",
    layout="wide",
    initial_sidebar_state="expanded",
)

_install_ws_logging_filter()
_install_ws_noise_filter()

# 다크모드 세션 초기화 (apply_global_css 이전)
if "dark_mode" not in st.session_state:
    st.session_state["dark_mode"] = False

# 디자인 시스템 CSS 주입 (사이드바 토글은 사이드바 마운트 후 주입)
S.apply_global_css()


# ============================================================================
# 상수 / 색상 / 프로젝트 메타
# ============================================================================

CHART_COLORS = {
    "primary": "#0d1b3e", "blue": "#3f51b5", "light_blue": "#5c6bc0",
    "bar": "#4a5899", "red": "#e53935", "orange": "#fb8c00",
    "green": "#27ae60", "gray": "#9e9e9e", "dark_gray": "#616161",
    "purple": "#8e24aa", "teal": "#00897b", "brown": "#795548",
}

MONTH_LABELS = [f"{m}월" for m in range(1, 13)]

# 경영진 KPI 목표 기준
KPI_TARGETS = {
    "CAPA 이행률": 90.0,
    "변경 완료율": 85.0,
    "불만 평균처리일": 30,
}


# ============================================================================
# 유틸 함수
# ============================================================================

# 지표 함수는 qms_pro.domain.metrics 로 이전됨(Phase 2-final-b).
# 결과 동등성 보존을 위해 원본과 동일 로직을 그대로 사용한다(정의 대신 import).
from qms_pro.domain.metrics import (
    COMPLETED_KEYWORDS,
    safe_pct,
    weighted_metric_total,
    weighted_metric_completed,
    weighted_metric_overdue,
    _wcount,
    _wgroupby,
    _num_series,
)


def _monthly_weighted_series(df_p: pd.DataFrame, month_col: str) -> pd.DataFrame:
    """월별 건수: QMS_Dashboard get_monthly_counts 와 동일하게 건수기여도 합."""
    if df_p.empty or month_col not in df_p.columns:
        return pd.DataFrame({"월": range(1, 13), "건수": [0.0] * 12})
    if "건수기여도" in df_p.columns:
        monthly = df_p.groupby(month_col, dropna=False)["건수기여도"].sum()
    else:
        monthly = df_p.groupby(month_col, dropna=False).size().astype(float)
    vals = []
    for m in range(1, 13):
        vals.append(float(monthly.loc[m]) if m in monthly.index else 0.0)
    return pd.DataFrame({"월": range(1, 13), "건수": vals})


def _is_displayable(val) -> bool:
    try:
        na = pd.isna(val)
        if isinstance(na, (bool, np.bool_)):
            if na:
                return False
        else:
            return False
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return bool(s) and s not in ("[]", "{}", "nan", "None", "NaT")

def _to_arrow_safe_df(df: pd.DataFrame) -> pd.DataFrame:
    """Streamlit Arrow 직렬화 실패를 줄이기 위한 최소 정규화."""
    if df is None or df.empty:
        return df
    out = df.copy()
    for c in out.columns:
        col = out[c]
        if col.dtype != "object":
            continue
        non_na = col.dropna()
        if non_na.empty:
            continue
        type_names = {type(v).__name__ for v in non_na.head(200).tolist()}
        if len(type_names) > 1:
            out[c] = col.astype("string").fillna("")
    return out


_EXCEL_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _excel_cell_safe(v):
    """단일 셀을 openpyxl/lxml XML 직렬화 가능한 형태로 정규화."""
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    if isinstance(v, (bool, np.bool_)):
        return "True" if bool(v) else "False"
    if isinstance(v, (int, float, np.integer, np.floating)):
        return v
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return v
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8", "replace")
        except Exception:
            v = repr(v)
    elif isinstance(v, (list, tuple, set, frozenset, dict)):
        try:
            v = json.dumps(v, ensure_ascii=False, default=str)
        except Exception:
            v = str(v)
    elif not isinstance(v, str):
        v = str(v)
    return _EXCEL_ILLEGAL_RE.sub("", v)


def _to_excel_safe_df(df: pd.DataFrame) -> pd.DataFrame:
    """openpyxl to_excel 직전 dataframe 안전화.
    object dtype 컬럼만 셀 단위 정규화 (수치/날짜 컬럼은 그대로 유지),
    bool/bytes/list/dict/set/np.bool_ 등을 문자열로 변환,
    XML 1.0 에서 거부되는 컨트롤 문자(\\x00-\\x08,\\x0B,\\x0C,\\x0E-\\x1F) 제거.
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == "object":
            out[c] = out[c].map(_excel_cell_safe)
    return out


def render_footer():
    S.render_footer()

def render_header(title: str, subtitle: str = ""):
    S.render_header(title, subtitle)

def kpi_gauge(value, target, title, suffix="%", inverse=False):
    """개선된 반원 게이지 차트 (qms_styles 위임)."""
    return S.kpi_gauge_improved(value, target, title, suffix=suffix, inverse=inverse)


def render_analyst_error_reduction_kpi(
    foos: pd.DataFrame,
    df_oos_full: pd.DataFrame,
    primary_year: int,
    prev_year: int,
    *,
    year_col: str = "연도",
) -> None:
    """QMS_Dashboard.py 마감회의 탭과 동일: 전년 vs 필터 반영 당년 Analyst error 건수기여도 합 및 감소율."""
    st.markdown("##### Analyst error 감소율")
    yc = year_col if year_col in df_oos_full.columns else "연도"
    if (
        not df_oos_full.empty
        and "이상발생 원인" in df_oos_full.columns
        and "건수기여도" in df_oos_full.columns
        and yc in df_oos_full.columns
    ):
        ae_prev_cnt = round(
            df_oos_full[
                (df_oos_full[yc] == prev_year)
                & (df_oos_full["이상발생 원인"] == "Analyst error")
            ]["건수기여도"].sum()
        )
    else:
        ae_prev_cnt = 0
    if not foos.empty and "이상발생 원인" in foos.columns and "건수기여도" in foos.columns:
        ae_curr_cnt = round(foos[foos["이상발생 원인"] == "Analyst error"]["건수기여도"].sum())
    else:
        ae_curr_cnt = 0

    if ae_prev_cnt > 0 or ae_curr_cnt > 0:
        reduction = safe_pct(ae_prev_cnt - ae_curr_cnt, ae_prev_cnt) if ae_prev_cnt > 0 else 0.0
        fig_ae = go.Figure()
        fig_ae.add_trace(
            go.Bar(
                x=[str(prev_year), str(primary_year)],
                y=[ae_prev_cnt, ae_curr_cnt],
                marker_color=[CHART_COLORS["gray"], CHART_COLORS["blue"]],
                text=[str(ae_prev_cnt), str(ae_curr_cnt)],
                textposition="outside",
                textfont=dict(size=14),
                width=0.5,
            )
        )
        if ae_prev_cnt > 0:
            fig_ae.add_annotation(
                x=0.5,
                y=max(ae_prev_cnt, ae_curr_cnt) * 0.85,
                xref="paper",
                text=(
                    f"<b>감소율: -{reduction:.0f}%</b>"
                    if reduction > 0
                    else f"<b>증가율: +{abs(reduction):.0f}%</b>"
                ),
                font=dict(size=14, color=CHART_COLORS["red"]),
                showarrow=False,
            )
        max_ae = max(ae_prev_cnt, ae_curr_cnt, 1)
        fig_ae.update_layout(
            height=280,
            margin=dict(l=20, r=10, t=10, b=40),
            yaxis=dict(range=[0, max_ae * 1.4]),
            plot_bgcolor="white",
            bargap=0.3,
        )
        fig_ae.add_annotation(
            x=0.5,
            y=-0.22,
            xref="paper",
            yref="paper",
            text=(
                f"<span style='color:gray'>■ 전년 ({prev_year})</span>  "
                f"<span style='color:{CHART_COLORS['blue']}'>■ 당년 ({primary_year})</span>  "
                "<span style='color:gray'>— 전년대비</span>"
            ),
            showarrow=False,
            font=dict(size=9),
        )
        st.plotly_chart(fig_ae, use_container_width=True)
    else:
        st.info("Analyst error 데이터가 없습니다.")


# ============================================================================
# 데이터 수집 함수 (캐시 래퍼 — 디스크 캐시/impl 매핑은 data_access 로 이전)
# ============================================================================

# 데이터 로딩은 전부 data_access(DA) 계층을 경유한다(Task 1.1).
# @st.cache_data 는 UI 계층의 메모이즈로 유지(동작 동일). 디스크 캐시 키/impl 매핑과
# 캐시 래퍼 로직은 DA.load_project 안으로 이전됨. 결과(df, err) 계약은 기존과 동일.
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_list_project(project: str):
    return DA.load_project(project)


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_oos_data():
    return DA.load_project("oos")


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_deviation_data():
    return DA.load_project("deviation")


def fetch_devout_data_stub():
    return DA.load_project("deviationoutsourcing")


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_capa_data():
    return DA.load_project("capa")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_change_data():
    return DA.load_project("changemanagement")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_complain_data():
    return DA.load_project("complain")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_capaai_data():
    return DA.load_project("capaactionitem")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_changeai_data():
    return DA.load_project("changeactionitem")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_changeimpact_data():
    return DA.load_project("changeimpactassessment")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_changeout_data():
    return DA.load_project("changeoutsourcing")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_devoutai_data():
    return DA.load_project("deviationactionitem")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_transfer_data():
    return DA.load_project("businesstransfer")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_validity_data():
    return DA.load_project("validityevaluation")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_investigation_data():
    return DA.load_project("investigation")


# ============================================================================
# 사이드바 — 데이터 로드 & 필터
# ============================================================================

st.sidebar.title("▦ QMS 통합 v2.0")
st.sidebar.divider()

# 단일 사이드바 토글(iframe): 사이드바 DOM 생성 이후 주입 — 초기 로드 시 body 전역 관찰로 브라우저 멈춤 방지
S.inject_sidebar_toggle()

# 다크모드 토글
S.dark_mode_toggle()

if st.sidebar.button("↻ 전체 데이터 갱신", use_container_width=True, type="primary"):
    st.cache_data.clear()
    DC.clear()
    st.session_state.pop("_cache_fetch_time", None)
    st.rerun()

_load_progress = st.sidebar.progress(0)
_load_caption = st.sidebar.empty()

_FETCH_STEPS = [
    ("OOS",        fetch_oos_data),
    ("일탈",       fetch_deviation_data),
    ("조사",       fetch_investigation_data),
    ("CAPA",       fetch_capa_data),
    ("CAPA AI",    fetch_capaai_data),
    ("모니터링AI", lambda: fetch_list_project("actionitem")),
    ("변경",       fetch_change_data),
    ("변경AI",     fetch_changeai_data),
    ("변경영향성", fetch_changeimpact_data),
    ("외주변경",   fetch_changeout_data),
    ("고객불만",   fetch_complain_data),
    ("일탈외주",   fetch_devout_data_stub),
    ("일탈외주AI", fetch_devoutai_data),
    ("기한연장",   lambda: fetch_list_project("extension")),
    ("업무이전",   fetch_transfer_data),
    ("유효성평가", fetch_validity_data),
]
_n_fetch = len(_FETCH_STEPS)
_fetch_results = [None] * _n_fetch
_step_times: list[tuple[str, float]] = []
_fetch_t0 = time.perf_counter()

# 병렬 페치 (ThreadPoolExecutor): Streamlit 캐시 함수는 thread-safe
from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

_load_caption.caption(f"병렬 수집 중 (16개 프로젝트)…")
_load_progress.progress(0.05)

def _run_step(idx_label_fn):
    idx, label, fn = idx_label_fn
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    return idx, label, result, elapsed

with ThreadPoolExecutor(max_workers=8) as _pool:
    _futures = {_pool.submit(_run_step, (i, lbl, fn)): i
                for i, (lbl, fn) in enumerate(_FETCH_STEPS)}
    _done_count = 0
    for _fut in _as_completed(_futures):
        _i, _lbl, _res, _el = _fut.result()
        _fetch_results[_i] = _res
        _step_times.append((_lbl, _el))
        _done_count += 1
        _load_progress.progress(_done_count / _n_fetch)

_fetch_elapsed = time.perf_counter() - _fetch_t0
_load_progress.empty()
_load_caption.empty()

# 결과 언팩
(df_oos, _e1), (df_dev, _e2), (df_inv, _e3), (df_capa, _e4), \
(df_capaai, _e5), (df_ai, _e6), (df_chg, _e7), (df_chgai, _e8), \
(df_chgimp, _e9), (df_chgout, _e10), (df_cmp, _e11), \
(df_devout, _e12), (df_devoutai, _e13), (df_ext, _e14), \
(df_transfer, _e15), (df_validity, _e16) = _fetch_results

# 사이드바: 수집 결과 → 필터(UI 상단) → 데이터 현황(UI 하단)
ALL_DFS = {
    "oos": df_oos, "deviation": df_dev, "investigation": df_inv,
    "capa": df_capa, "capaactionitem": df_capaai, "actionitem": df_ai,
    "changemanagement": df_chg, "changeactionitem": df_chgai,
    "changeimpactassessment": df_chgimp, "changeoutsourcing": df_chgout,
    "complain": df_cmp, "deviationoutsourcing": df_devout,
    "deviationactionitem": df_devoutai, "extension": df_ext,
    "businesstransfer": df_transfer, "validityevaluation": df_validity,
}

# ─── 부모-자식 체인 연계 인덱스 구축 (필터 적용 전, 전체 그래프 기준) ───
# 각 DF 에 부모/자식 요약 컬럼을 머지하고 ctx 를 세션에 보관한다.
try:
    _linkage_ctx = build_and_apply_linkage(ALL_DFS)
    st.session_state["qms_linkage_ctx"] = _linkage_ctx
except Exception as _linkage_err:
    st.session_state["qms_linkage_ctx"] = None
    st.sidebar.warning(f"연계 인덱스 빌드 실패: {_linkage_err}")

# 필터 (사이드바 상단에 표시)
st.sidebar.markdown("**필터**")
_all_year_dfs = [d for d in ALL_DFS.values() if not d.empty and ("연도" in d.columns or "연도_등록" in d.columns)]
_year_set = set()
for d in _all_year_dfs:
    for _ycol in ("연도", "연도_등록"):
        if _ycol in d.columns:
            _year_set |= {int(y) for y in d[_ycol].dropna().unique()}
years_available = sorted(_year_set, reverse=True) if _year_set else [datetime.now().year]
current_year = datetime.now().year

year_basis = st.sidebar.radio(
    "연도 기준",
    ("발견일시", "등록일"),
    index=0,
    horizontal=True,
    help="QMS 웹 과제 목록은 기본 필터가 등록일(regDate)입니다. 시험 발견일과 등록일이 다른 건이 있으면 건수가 달라집니다.",
)
YEAR_FILTER_COL = "연도_등록" if year_basis == "등록일" else "연도"

_default_years = (
    [2026]
    if 2026 in years_available
    else ([current_year] if current_year in years_available else (years_available[:1] if years_available else []))
)
selected_years = st.sidebar.multiselect("연도", years_available, default=_default_years)
status_filter = st.sidebar.radio("진행상태", ["전체", "진행중", "완료"], horizontal=True)
dday_filter = st.sidebar.radio("기한일 기준", ["전체", "D-day 임박 (7일)", "기한 초과"], horizontal=False)

# 필터 초기화 버튼
if S.filter_reset_button():
    # 세션에서 필터 관련 키 리셋
    for _k in list(st.session_state.keys()):
        if _k.startswith("_") or _k in ("dark_mode", "qms_linkage_ctx", "_cache_fetch_time"):
            continue
        del st.session_state[_k]
    st.rerun()

st.sidebar.divider()
st.sidebar.markdown("**데이터 현황**")
total_all = 0
for pk, df_p in ALL_DFS.items():
    label = PROJECT_META[pk]["label"]
    if pk == "deviationoutsourcing":
        st.sidebar.caption(f"📎 {label}: 「일탈」에 자사+외주 통합 수집")
        continue
    n = df_p["관리번호"].nunique() if not df_p.empty and "관리번호" in df_p.columns else len(df_p)
    total_all += n
    if df_p.empty:
        st.sidebar.caption(f"⚪ {label}: 0건")
    else:
        st.sidebar.caption(f"🟢 {label}: {n}건")
st.sidebar.success(f"총 {total_all}건")
st.sidebar.caption(f"마지막 갱신: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
st.sidebar.caption(
    f"⏱️ 16스텝 수집 {_fetch_elapsed:.1f}s "
    f"(캐시 히트 시 ≈0s)"
)
S.cache_age_bar(_fetch_elapsed, ttl=1800)
with st.sidebar.expander("스텝별 소요", expanded=False):
    _slow = sorted(_step_times, key=lambda x: -x[1])[:16]
    for _lbl, _sec in _slow:
        st.caption(f"• {_lbl}: {_sec:.2f}s")


def _month_col_for_df(df: pd.DataFrame) -> str:
    if year_basis == "등록일" and not df.empty and "월_등록" in df.columns:
        return "월_등록"
    return "월"


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    # 필터 로직은 qms_pro.ui.filters 로 이전(동일 결과). 사이드바 전역값을 인자로 전달.
    return UIF.apply_sidebar_filters(
        df,
        selected_years=selected_years,
        status_filter=status_filter,
        dday_filter=dday_filter,
        year_filter_col=YEAR_FILTER_COL,
    )


def apply_filters_no_year(df: pd.DataFrame) -> pd.DataFrame:
    """사이드바에서 연도만 제외한 필터(진행상태·기한일). 전년 OOS/일탈 비교용."""
    return UIF.apply_sidebar_filters_no_year(
        df,
        status_filter=status_filter,
        dday_filter=dday_filter,
    )


# 필터 적용
F = {k: apply_filters(v) for k, v in ALL_DFS.items()}
foos, fdev, finv = F["oos"], F["deviation"], F["investigation"]
fcapa, fcapaai, fai = F["capa"], F["capaactionitem"], F["actionitem"]
fchg, fchgai, fchgimp, fchgout = F["changemanagement"], F["changeactionitem"], F["changeimpactassessment"], F["changeoutsourcing"]
fcmp = F["complain"]
fdevout, fdevoutai = F["deviationoutsourcing"], F["deviationactionitem"]
fext, ftransfer, fvalidity = F["extension"], F["businesstransfer"], F["validityevaluation"]


# ============================================================================
# 공통 헬퍼: 프로젝트별 원본 데이터 섹션
# ============================================================================

def render_raw_data_section(
    default_project_keys: list[str],
    key_suffix: str,
    allow_change: bool = True,
    title: str | None = None,
    include_raw: bool = True,
    oos_filters: bool = False,
    pqr_mode: bool = False,
    detail_view: bool = True,
    unique_count_col: str | None = "관리번호",
    extra_priority: list[str] | None = None,
    df_override: pd.DataFrame | None = None,
):
    """특정 프로젝트 데이터만(혹은 전체 중 기본값만 선택되어) 조회 가능한 원본 데이터 패널.

    - allow_change=True       : 멀티셀렉트 노출 (다른 프로젝트도 추가 조회 가능)
    - allow_change=False      : 멀티셀렉트 숨김 (default_project_keys 고정)
    - key_suffix              : st.text_input/multiselect key 충돌 방지용 식별자
    - include_raw=True        : extention 전체 key(`_ext_*` 칼럼)까지 표에 포함
    - oos_filters=True        : OOS 전용 필터(시험종류·이상발생 원인) 노출
    - pqr_mode=True           : PQR 보고서 요약(문서번호·발생내용·조치사항) 모드 체크박스 노출
    - detail_view=True        : 표 하단에 관리번호별 상세 조회 expander 노출
    - unique_count_col        : 고유 건수를 표기할 칼럼(기본: 관리번호). None 이면 표기 생략
    """
    proj_options = list(PROJECT_META.keys())
    proj_labels = [PROJECT_META[k]["label"] for k in proj_options]
    default_labels = [
        PROJECT_META[k]["label"] for k in default_project_keys if k in PROJECT_META
    ]

    if title:
        st.subheader(title)

    if allow_change:
        selected_labels = st.multiselect(
            "조회 프로젝트", proj_labels,
            default=default_labels or proj_labels,
            key=f"raw_proj_{key_suffix}",
        )
    else:
        selected_labels = default_labels
        lbl_list = ", ".join(default_labels) if default_labels else "(없음)"
        st.caption(f"조회 범위: {lbl_list}")

    selected_keys = [proj_options[proj_labels.index(l)] for l in selected_labels]

    if df_override is not None:
        raw_all = df_override.copy()
        if "프로젝트" not in raw_all.columns:
            lbl = ", ".join(PROJECT_META[k]["label"] for k in default_project_keys if k in PROJECT_META)
            raw_all["프로젝트"] = lbl or "(override)"
    else:
        frames_raw = []
        for k in selected_keys:
            df_k = F.get(k, pd.DataFrame())
            if not df_k.empty:
                tmp = df_k.copy()
                tmp["프로젝트"] = PROJECT_META[k]["label"]
                frames_raw.append(tmp)
        raw_all = pd.concat(frames_raw, ignore_index=True) if frames_raw else pd.DataFrame()

    r1, r2, r3 = st.columns(3)
    with r1:
        sn = st.text_input("QMS번호", placeholder="예: 7078", key=f"raw_n_{key_suffix}",
                           help="데이터 컬럼명은 관리번호(prno)와 동일합니다.")
    with r2:
        st_t = st.text_input("제목 검색", placeholder="키워드", key=f"raw_t_{key_suffix}")
    with r3:
        sp = st.text_input("등록자", placeholder="이름", key=f"raw_p_{key_suffix}")
    r4, r5 = st.columns(2)
    with r4:
        raw_lot = st.text_input("제조번호", placeholder="일부 입력", key=f"raw_lot_{key_suffix}")
    with r5:
        raw_item_cd = st.text_input("품목코드", placeholder="예: 23262", key=f"raw_item_cd_{key_suffix}")

    filter_type = "전체"
    filter_cause = "전체"
    if oos_filters:
        fc1, fc2 = st.columns(2)
        with fc1:
            _type_opts = (
                sorted(raw_all["시험종류"].dropna().unique().tolist())
                if "시험종류" in raw_all.columns else []
            )
            filter_type = st.selectbox(
                "시험종류", ["전체"] + _type_opts, key=f"raw_type_{key_suffix}"
            )
        with fc2:
            _cause_opts = (
                sorted(raw_all["이상발생 원인"].dropna().unique().tolist())
                if "이상발생 원인" in raw_all.columns else []
            )
            filter_cause = st.selectbox(
                "이상발생 원인", ["전체"] + _cause_opts, key=f"raw_cause_{key_suffix}"
            )

    do_pqr = False
    if pqr_mode:
        do_pqr = st.checkbox(
            "제품품질평가(PQR) — 보고서 입력용 요약(문서번호·발생내용·조치사항) 함께 표시",
            key=f"raw_pqr_{key_suffix}",
            help="발생내용=이벤트 정보, 조치사항=결론 - 최종 결론, 문서번호=관리번호",
        )

    if not raw_all.empty:
        if sn:
            q = str(sn).strip()
            raw_all = raw_all[raw_all["관리번호"].astype(str).str.contains(q, na=False)]
        if st_t and "제목" in raw_all.columns:
            raw_all = raw_all[raw_all["제목"].fillna("").str.contains(st_t, case=False, na=False)]
        if sp and "등록자" in raw_all.columns:
            raw_all = raw_all[raw_all["등록자"].fillna("").str.contains(sp, case=False, na=False)]
        if raw_lot and "제조번호" in raw_all.columns:
            raw_all = raw_all[
                raw_all["제조번호"].astype(str).str.contains(str(raw_lot).strip(), case=False, na=False)
            ]
        if raw_item_cd and "품목코드" in raw_all.columns:
            raw_all = raw_all[
                raw_all["품목코드"].astype(str).str.contains(str(raw_item_cd).strip(), case=False, na=False)
            ]
        if filter_type != "전체" and "시험종류" in raw_all.columns:
            raw_all = raw_all[raw_all["시험종류"] == filter_type]
        if filter_cause != "전체" and "이상발생 원인" in raw_all.columns:
            raw_all = raw_all[raw_all["이상발생 원인"] == filter_cause]

    if unique_count_col and unique_count_col in raw_all.columns and not raw_all.empty:
        n_unique = raw_all[unique_count_col].nunique()
        st.caption(f"조회 결과: {len(raw_all)}건 (고유 {unique_count_col}: {n_unique}건)")
    else:
        st.caption(f"조회 결과: {len(raw_all)}건")

    if do_pqr and not raw_all.empty:
        st.markdown("##### PQR 보고용 요약 (복사·붙여넣기용)")
        _d = raw_all.reset_index(drop=True)
        _doc = _d["관리번호"] if "관리번호" in _d.columns else pd.Series([pd.NA] * len(_d))
        _event = (
            _d["이벤트 정보"].fillna("").astype(str)
            if "이벤트 정보" in _d.columns else pd.Series([""] * len(_d))
        )
        _final = (
            _d["결론 - 최종 결론"].fillna("").astype(str)
            if "결론 - 최종 결론" in _d.columns else pd.Series([""] * len(_d))
        )
        _doc_num = pd.to_numeric(_doc, errors="coerce")
        pqr_df = pd.DataFrame({
            "문서번호": _doc_num.map(lambda x: "" if pd.isna(x) else str(int(x))),
            "발생내용": _event,
            "조치사항": _final,
        })
        st.dataframe(
            pqr_df, use_container_width=True,
            height=min(400, 80 + len(pqr_df) * 35),
            column_config={
                "문서번호": st.column_config.TextColumn("문서번호", help="관리번호"),
                "발생내용": st.column_config.TextColumn("발생내용", help="이벤트 정보"),
                "조치사항": st.column_config.TextColumn("조치사항", help="결론 - 최종 결론"),
            },
        )
        st.caption("아래는 전체 컬럼 검색 결과 및 상세 조회입니다.")
        st.divider()

    if not raw_all.empty:
        priority = [
            "프로젝트", "관리번호", "제목", "품목코드", "제조번호",
            "등록일", "기한일", "진행상태", "D-day", "등록자",
        ]
        if extra_priority:
            for _ec in extra_priority:
                if _ec and _ec not in priority:
                    priority.append(_ec)
        avail = [c for c in priority if c in raw_all.columns]
        all_other = [c for c in raw_all.columns if c not in priority and c not in ["연도", "월", "완료여부"]]
        parser_cols = [c for c in all_other if not c.startswith("_ext_")]
        ext_cols = [c for c in all_other if c.startswith("_ext_")]
        if include_raw:
            show_ext = st.checkbox(
                f"extention 원본 키 포함 (+{len(ext_cols)}개 `_ext_*` 컬럼)",
                value=True, key=f"raw_ext_toggle_{key_suffix}",
                help="파서가 한국어 라벨로 매핑하지 않은 API 필드까지 모두 노출합니다.",
            )
            other = parser_cols + (ext_cols if show_ext else [])
        else:
            other = parser_cols
        disp = avail + other
        _col_cfg = {
            "관리번호": st.column_config.NumberColumn(format="%d"),
            "D-day": st.column_config.NumberColumn(format="%d일"),
        }
        if "건수기여도" in disp:
            _col_cfg["건수기여도"] = st.column_config.NumberColumn("건수기여도", format="%.5f")
        if "발견일시" in disp:
            _col_cfg["발견일시"] = st.column_config.DateColumn("발견일시", format="YYYY-MM-DD")
        _raw_disp = _to_arrow_safe_df(raw_all[disp])
        st.dataframe(_raw_disp, use_container_width=True, height=500, hide_index=True,
                     column_config=_col_cfg)
        st.caption(
            f"표시 칼럼: 우선 {len(avail)} · 파서 {len(parser_cols)}"
            + (f" · _ext_* {len(ext_cols)}" if include_raw else "")
            + f" (총 {len(disp)}개)"
        )

        if detail_view and "관리번호" in raw_all.columns:
            st.divider()
            qms_numeric = pd.to_numeric(raw_all["관리번호"], errors="coerce").dropna()
            qms_list = sorted(qms_numeric.unique().astype(int).tolist())
            if qms_list:
                selected_qms = st.selectbox(
                    "상세 조회할 관리번호 선택", qms_list, key=f"raw_detail_{key_suffix}"
                )
                if selected_qms is not None:
                    detail_rows = raw_all[
                        pd.to_numeric(raw_all["관리번호"], errors="coerce") == selected_qms
                    ]
                    with st.expander(
                        f"관리번호 {selected_qms} 상세 정보 ({len(detail_rows)}행)",
                        expanded=True,
                    ):
                        skip_cols = {"연도", "월", "월문자", "완료여부"}
                        for _, row in detail_rows.iterrows():
                            items = []
                            for col in raw_all.columns:
                                if col in skip_cols:
                                    continue
                                v = row[col]
                                if v is None:
                                    continue
                                if isinstance(v, float) and pd.isna(v):
                                    continue
                                if isinstance(v, str) and not v.strip():
                                    continue
                                items.append((col, v))
                            left, right = st.columns(2)
                            mid = (len(items) + 1) // 2
                            with left:
                                for k_, v_ in items[:mid]:
                                    st.markdown(f"**{k_}**: {v_}")
                            with right:
                                for k_, v_ in items[mid:]:
                                    st.markdown(f"**{k_}**: {v_}")
                            st.divider()

        st.divider()
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            for k in selected_keys:
                lbl = PROJECT_META[k]["label"]
                dk = F.get(k, pd.DataFrame())
                if not dk.empty:
                    sd = _to_excel_safe_df(dk)
                    sd.to_excel(w, index=False, sheet_name=lbl[:31])
        st.download_button(
            "↓ 엑셀 다운로드", data=buf.getvalue(),
            file_name=f"QMS_{key_suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key=f"raw_dl_{key_suffix}",
        )
    else:
        st.info("조회된 데이터가 없습니다.")


# ============================================================================
# 공통 헬퍼: 프로젝트별 연계 현황(부모-자식 체인) 섹션
# ============================================================================

_LINKAGE_ABNORMAL_FLAGS = [
    "부모종결_자식미종결",
    "자식완료_부모미완료",
]

# 내부 플래그 → 대시보드 표시용 라벨 + 설명
_LINKAGE_FLAG_LABEL = {
    "부모종결_자식미종결": "본 프로젝트 종결 · 연관프로젝트 미완료",
    "자식완료_부모미완료": "연관프로젝트 완료 · 본 프로젝트 미종결",
}
_LINKAGE_FLAG_HELP = {
    "부모종결_자식미종결": (
        "본 프로젝트(OOS/일탈/변경 등)는 이미 종결되었지만, "
        "그로부터 파생된 연관프로젝트(조사·CAPA·Action Item 등)가 아직 닫히지 않은 상태입니다. "
        "원래는 후속 조치가 완료된 후에 본 프로젝트를 종결해야 하므로 **선(先)종결** 이슈로 점검이 필요합니다."
    ),
    "자식완료_부모미완료": (
        "연관프로젝트(조사·CAPA·Action Item 등) 는 모두 완료되었지만, "
        "본 프로젝트가 여전히 미종결 상태입니다. "
        "후속 조치가 끝났으므로 **본 프로젝트 종결 처리 누락** 가능성이 있어 점검이 필요합니다."
    ),
}


def render_linkage_section(project_key: str, key_suffix: str, title: str | None = None,
                            df_override: pd.DataFrame | None = None):
    """프로젝트 DataFrame 에 머지된 linkage 컬럼을 기반으로 체인 요약 패널을 렌더.

    요구사항 (Plan Phase C):
    - KPI 4카드 : Child 개설율 / 최종 종결률(체인) / 평균 자식 수 / 평균 체인 깊이
    - 섹션 1   : 체인 Sankey (부모 프로젝트 → 자식 프로젝트 타입)
    - 섹션 2   : 자식 프로젝트 타입 분포 (bar)
    - 섹션 3   : 자식 미종결 TOP 20 (+ 관리번호 선택 시 drill-down)
    - 섹션 4   : 이상 케이스 테이블 (플래그별 탭)
    - 섹션 5   : 체인 깊이 히스토그램
    """
    df = df_override if df_override is not None else F.get(project_key, pd.DataFrame())
    if title:
        st.subheader(title)
    if df is None or df.empty:
        st.info("조회된 데이터가 없습니다.")
        return
    if "자식 수(전체)" not in df.columns:
        st.warning("연계 인덱스가 아직 빌드되지 않았습니다. 사이드바의 전체 갱신을 눌러주세요.")
        return

    base = df.drop_duplicates(subset=["관리번호"], keep="first") if "관리번호" in df.columns else df
    total_n = len(base)
    if total_n == 0:
        st.info("조회된 데이터가 없습니다.")
        return

    child_cnt = _num_series(base["자식 수(전체)"], default=0.0)
    depth_num = _num_series(base["체인 최대 깊이"], default=1.0)
    child_open = _num_series(base["자식 미종결 수"], default=0.0)

    has_child = (child_cnt > 0).sum()
    chain_closed = (base["최종 종결 여부(체인)"] == True).sum()
    avg_children = float(child_cnt.mean()) if total_n else 0.0
    avg_depth = float(depth_num.mean()) if total_n else 1.0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("연관프로젝트 연결률", f"{safe_pct(has_child, total_n):.1f}%",
              help=(f"전체 {total_n}건 중, 조사·CAPA·Action Item 등 **연관프로젝트(자식 워크플로우)** 가 "
                    "1건 이상 생성·연결된 비율입니다."))
    k2.metric("전체(본+연관) 종결률", f"{safe_pct(chain_closed, total_n):.1f}%",
              help=("본 프로젝트와 그에 연결된 모든 연관프로젝트(조사·CAPA·Action Item 등)가 "
                    "**한꺼번에 종결**된 비율입니다. 하나라도 열려 있으면 미종결로 계산."))
    k3.metric("과제당 연관프로젝트 수(평균)", f"{avg_children:.2f}건",
              help=("한 건의 본 프로젝트가 끌고 있는 연관프로젝트의 평균 개수입니다. "
                    "바로 아래 단계(자식)뿐 아니라 더 깊은 단계(손자·증손자 등)까지 모두 합산."))
    k4.metric("평균 연계 단계", f"{avg_depth:.2f}단계",
              help=("본 프로젝트 → 연관프로젝트 → 그 다음 연관프로젝트 …로 이어지는 "
                    "연결 체인의 **최대 깊이** 평균입니다. 예: 본 프로젝트만 있으면 1, "
                    "본→조사 까지면 2, 본→조사→CAPA 까지면 3."))

    st.markdown("---")

    ctx = st.session_state.get("qms_linkage_ctx")

    # 섹션 1+2: 체인 Sankey + 자식 프로젝트 타입 분포
    col_s1, col_s2 = st.columns([3, 2])
    with col_s1:
        st.markdown("#### 본 프로젝트 → 연관프로젝트 흐름")
        st.caption("왼쪽(본 프로젝트 프로젝트) 에서 시작해 오른쪽(연관프로젝트 프로젝트) 로 흘러가는 건수를 "
                   "선 굵기로 표시합니다. 예: `일탈 → 조사 15건` 은 일탈 15건에서 조사 과제가 생성됐다는 의미입니다.")
        link_counts: dict[tuple[str, str], int] = {}
        if ctx is not None:
            source_prnos = set(base["관리번호"].astype(str).tolist())
            for parent_prno, child_list in ctx.children_by_parent.items():
                if str(parent_prno) not in source_prnos:
                    continue
                parent_row = ctx.by_prno.get(str(parent_prno), {})
                psrc = str(parent_row.get("프로젝트", "") or "?")
                for child in child_list:
                    ptgt = str(ctx.by_prno.get(child, {}).get("프로젝트", "") or "?")
                    link_counts[(psrc, ptgt)] = link_counts.get((psrc, ptgt), 0) + 1
        if link_counts:
            nodes = sorted({n for pair in link_counts for n in pair})
            node_idx = {n: i for i, n in enumerate(nodes)}
            sankey = go.Figure(go.Sankey(
                node=dict(label=nodes, pad=16, thickness=14,
                          color=CHART_COLORS.get("blue", "#1f77b4")),
                link=dict(
                    source=[node_idx[s] for (s, _), _ in link_counts.items()],
                    target=[node_idx[t] for (_, t), _ in link_counts.items()],
                    value=list(link_counts.values()),
                ),
            ))
            sankey.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=10))
            st.plotly_chart(sankey, use_container_width=True)
        else:
            st.info("직계 자식 링크가 없습니다.")
    with col_s2:
        st.markdown("#### 연관프로젝트 종류별 분포")
        st.caption("본 프로젝트에서 파생된 연관프로젝트를 **프로젝트 종류별(조사 / CAPA / Action Item …)** 로 합산한 건수입니다.")
        comp_rows = []
        for comp_str in base["자식 구성"].fillna(""):
            if not comp_str:
                continue
            for part in str(comp_str).split(","):
                part = part.strip()
                if not part:
                    continue
                toks = part.rsplit(" ", 1)
                if len(toks) == 2 and toks[1].isdigit():
                    comp_rows.append((toks[0], int(toks[1])))
        if comp_rows:
            comp_df = pd.DataFrame(comp_rows, columns=["프로젝트", "건수"])
            comp_agg = (comp_df.groupby("프로젝트", as_index=False)["건수"].sum()
                              .sort_values("건수", ascending=True))
            fig_b = px.bar(comp_agg, x="건수", y="프로젝트", orientation="h",
                           text="건수", color_discrete_sequence=[CHART_COLORS.get("purple", "#9467bd")])
            fig_b.update_traces(textposition="outside")
            fig_b.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                                 plot_bgcolor="white")
            st.plotly_chart(fig_b, use_container_width=True)
        else:
            st.info("집계 가능한 자식 구성이 없습니다.")

    st.markdown("---")

    # 섹션 3: 연관프로젝트 미완료 TOP 20 + drill-down
    st.markdown("#### 연관프로젝트 미완료 TOP 20")
    st.caption(
        "본 프로젝트에 연결된 연관프로젝트(조사·CAPA·Action Item 등) 중 **아직 종결되지 않은 건수가 많은 순서** 로 "
        "상위 20건을 보여줍니다. 같으면 `미완료 최장 지연일` 기준으로 내림차순."
    )
    top_rows = (base[child_open > 0]
                  .sort_values(["자식 미종결 수", "자식 최대 지연일"],
                               ascending=[False, False])
                  .head(20))
    if top_rows.empty:
        st.success("연관프로젝트가 모두 종결된 상태입니다. 미완료 건 없음.")
    else:
        _disp_rename = {
            "자식 수(전체)": "연관프로젝트 수",
            "자식 미종결 수": "연관프로젝트 미완료 수",
            "자식 종결률 %": "연관프로젝트 종결률(%)",
            "자식 최대 지연일": "연관프로젝트 최장 지연일",
            "체인 최대 깊이": "연계 단계 최대",
            "자식 구성": "연관프로젝트 구성",
        }
        disp_cols = [c for c in [
            "관리번호", "제목", "진행상태",
            "자식 수(전체)", "자식 미종결 수", "자식 종결률 %",
            "자식 최대 지연일", "체인 최대 깊이", "자식 구성",
        ] if c in top_rows.columns]
        st.dataframe(top_rows[disp_cols].rename(columns=_disp_rename),
                     use_container_width=True, hide_index=True, height=360)

        drill_keys = top_rows["관리번호"].astype(str).tolist() if "관리번호" in top_rows.columns else []
        if drill_keys and ctx is not None:
            pick = st.selectbox(
                "관리번호 선택 → 미완료 연관프로젝트 상세",
                ["(선택)"] + drill_keys,
                key=f"linkage_drill_{key_suffix}",
                help="선택한 관리번호의 **아직 닫히지 않은 연관프로젝트 목록** 을 아래 표로 보여줍니다.",
            )
            if pick and pick != "(선택)":
                from qms_pro.domain.linkage import summarize_children as _sc
                child_rows = _sc(ctx, pick).get("자식 미종결 목록", [])
                if child_rows:
                    cdf = pd.DataFrame(child_rows)
                    st.dataframe(cdf, use_container_width=True,
                                 hide_index=True, height=280)
                else:
                    st.info("선택한 건의 연관프로젝트는 모두 종결 상태입니다.")

    st.markdown("---")

    # 섹션 4: 이상 케이스 탭
    st.markdown("#### 점검 필요 케이스")
    st.caption(
        "본 프로젝트와 연관프로젝트 간 **종결 순서가 맞지 않는** 건을 탭별로 모아 보여줍니다. "
        "각 탭 제목에 마우스를 올리면 판정 기준 설명이 나타납니다."
    )
    _flag_labels = [_LINKAGE_FLAG_LABEL[f] for f in _LINKAGE_ABNORMAL_FLAGS]
    flag_tabs = st.tabs(_flag_labels)
    _flag_rename = {
        "자식 수(전체)": "연관프로젝트 수",
        "자식 미종결 수": "연관프로젝트 미완료 수",
        "자식 종결률 %": "연관프로젝트 종결률(%)",
        "자식 구성": "연관프로젝트 구성",
        "자식 최대 지연일": "연관프로젝트 최장 지연일",
        "이상 케이스 플래그": "점검 케이스",
    }
    for flag, tab in zip(_LINKAGE_ABNORMAL_FLAGS, flag_tabs):
        with tab:
            st.info(_LINKAGE_FLAG_HELP.get(flag, ""), icon="⚠️")
            hit = base[base["이상 케이스 플래그"].fillna("").str.contains(flag, na=False)]
            if hit.empty:
                st.success(f"해당 점검 케이스는 현재 없습니다 — {_LINKAGE_FLAG_LABEL.get(flag, flag)}")
                continue
            disp = [c for c in [
                "관리번호", "제목", "진행상태", "완료여부",
                "자식 수(전체)", "자식 미종결 수", "자식 종결률 %",
                "자식 구성", "자식 최대 지연일", "이상 케이스 플래그",
            ] if c in hit.columns]
            hit_disp = hit[disp].rename(columns=_flag_rename)
            if "점검 케이스" in hit_disp.columns:
                hit_disp["점검 케이스"] = hit_disp["점검 케이스"].apply(
                    lambda s: ", ".join(
                        _LINKAGE_FLAG_LABEL.get(tok.strip(), tok.strip())
                        for tok in str(s).split(",") if tok.strip()
                    )
                )
            st.dataframe(hit_disp, use_container_width=True,
                         hide_index=True, height=320)

    st.markdown("---")

    # 섹션 5: 연계 단계 히스토그램
    st.markdown("#### 연계 단계(깊이) 분포")
    st.caption(
        "한 건의 본 프로젝트가 얼마나 깊게 이어지는지(연관프로젝트 체인 길이) 를 히스토그램으로 보여줍니다. "
        "**1단계 = 본 프로젝트만 · 연관프로젝트 없음**, **2단계 = 본 프로젝트 → 연관프로젝트 1단계(예: 일탈→조사)**, "
        "**3단계 = 본 → 연관 → 그 다음 연관(예: 일탈→조사→CAPA)**, "
        "**4단계 이상 = CAPA → Action Item 등 추가 연결**."
    )
    depth_counts = (depth_num.round().astype(int)
                        .value_counts().sort_index().reset_index())
    depth_counts.columns = ["연계 단계", "건수"]
    _depth_map = {1: "1단계 (본 프로젝트만)",
                  2: "2단계 (본→연관 1단계)",
                  3: "3단계 (본→연관→다음)",
                  4: "4단계",
                  5: "5단계+"}
    depth_counts["라벨"] = depth_counts["연계 단계"].apply(
        lambda d: _depth_map.get(int(d), f"{int(d)}단계")
    )
    fig_h = px.bar(depth_counts, x="라벨", y="건수", text="건수",
                   color_discrete_sequence=[CHART_COLORS.get("green", "#2ca02c")])
    fig_h.update_traces(textposition="outside")
    fig_h.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10),
                         plot_bgcolor="white",
                         xaxis_title="", yaxis_title="건수")
    st.plotly_chart(fig_h, use_container_width=True)


# ============================================================================
# 일탈/인시던트 공통 렌더러
# ============================================================================

def _render_source_split(df_all: pd.DataFrame, key_prefix: str,
                         dim: str, label: str = "건수",
                         orientation: str = "v",
                         top_n: int | None = None) -> None:
    """자사/외주 분리 bar chart. 좌: 자사, 우: 외주, 하단: 통합"""
    if "자사/외주" not in df_all.columns or dim not in df_all.columns:
        if dim in df_all.columns:
            gg = _wgroupby(df_all, dim, name="건수")
            gg = gg[gg[dim].astype(str).str.strip() != ""].sort_values("건수", ascending=(orientation == "h"))
            if top_n:
                gg = gg.tail(top_n) if orientation == "h" else gg.head(top_n)
            if gg.empty:
                st.info("데이터 없음")
                return
            fig = (px.bar(gg, x="건수", y=dim, orientation="h", text="건수")
                   if orientation == "h"
                   else px.bar(gg, x=dim, y="건수", text="건수"))
            fig.update_traces(textposition="outside")
            fig.update_layout(height=max(260, 22 * len(gg)) if orientation == "h" else 320,
                                margin=dict(l=10, r=30, t=10, b=10), plot_bgcolor="white")
            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_{dim}_single")
        return

    c_own, c_out = st.columns(2)
    for col, src, palette, c_ui in [
        ("자사", "자사", CHART_COLORS.get("blue", "#1f77b4"), c_own),
        ("외주", "외주", CHART_COLORS.get("orange", "#ff7f0e"), c_out),
    ]:
        with c_ui:
            st.caption(f"**{col}**")
            sub = df_all[df_all["자사/외주"] == src]
            if sub.empty or dim not in sub.columns:
                st.info("데이터 없음")
                continue
            gg = _wgroupby(sub, dim, name="건수")
            gg = gg[gg[dim].astype(str).str.strip() != ""].sort_values("건수", ascending=(orientation == "h"))
            if top_n:
                gg = gg.tail(top_n) if orientation == "h" else gg.head(top_n)
            if gg.empty:
                st.info("데이터 없음")
                continue
            fig = (px.bar(gg, x="건수", y=dim, orientation="h", text="건수",
                           color_discrete_sequence=[palette])
                   if orientation == "h"
                   else px.bar(gg, x=dim, y="건수", text="건수",
                                 color_discrete_sequence=[palette]))
            fig.update_traces(textposition="outside")
            fig.update_layout(height=max(240, 22 * len(gg)) if orientation == "h" else 300,
                                margin=dict(l=10, r=30, t=10, b=10), plot_bgcolor="white",
                                showlegend=False)
            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_{dim}_{src}")


def render_event_category_tab(
    kind: str,            # "일탈" | "인시던트"
    key_prefix: str,      # "dev" | "inc"
    fdev_full: pd.DataFrame,
    ycol_in: str,
    primary_year_in: int,
) -> None:
    """일탈·인시던트 공통 렌더러. `이벤트 구분` 으로 필터링 후 동일 구조 sub 탭 제공."""
    label = kind
    header_color = "red" if kind == "일탈" else "orange"

    if "이벤트 구분" in fdev_full.columns:
        ftab = fdev_full[fdev_full["이벤트 구분"] == kind].copy()
    else:
        st.warning("`이벤트 구분` 컬럼이 없어 일탈/인시던트 분리가 불가합니다.")
        ftab = fdev_full.copy()

    render_header(f"{label}" + (" (자사 · 외주)" if kind == "일탈" else " (자사 · 외주)"))
    st.markdown("---")

    # ─── 상단 공통 필터 ──────────────────────────────────────────────
    c_f1, c_f2, c_f3 = st.columns([1.1, 1.0, 2.2])
    with c_f1:
        src_opts = ["자사", "외주"]
        src_sel = st.multiselect("자사/외주", src_opts, default=src_opts,
                                   key=f"{key_prefix}_src")
        if "자사/외주" in ftab.columns and src_sel:
            ftab = ftab[ftab["자사/외주"].isin(src_sel)]

    avail_years = sorted(
        set(int(y) for y in ftab[ycol_in].dropna().unique()) if ycol_in in ftab.columns else set(),
        reverse=True,
    ) or [primary_year_in]
    with c_f2:
        trend_year = st.selectbox("연도 (Y)", avail_years, index=0,
                                    key=f"{key_prefix}_year")

    with c_f3:
        tcol = "작성팀" if "작성팀" in ftab.columns else None
        team_opts = (sorted(ftab[tcol].dropna().unique().tolist())
                     if tcol else [])
        team_sel = st.multiselect(
            "팀", team_opts,
            default=team_opts,
            key=f"{key_prefix}_team",
            help="기본값: 전체 팀 선택",
        )
        if tcol and team_sel:
            ftab = ftab[ftab[tcol].isin(team_sel)]

    st.caption(
        f"※ 현재 탭은 **{label}** 만 집계 · 모든 수치는 **건수기여도** 합(동시분석 1/N) "
        "을 정수 반올림한 값입니다."
    )

    # 연도 기준 뷰 (경향분석 / YoY 용)
    if ycol_in in ftab.columns:
        ftab_y = ftab[ftab[ycol_in] == trend_year].copy()
        ftab_y_prev = ftab[ftab[ycol_in] == (trend_year - 1)].copy()
    else:
        ftab_y = ftab.copy()
        ftab_y_prev = pd.DataFrame()

    mc = _month_col_for_df(ftab)

    tab_kpi, tab_trend, tab_cause, tab_recur, tab_team, tab_link, tab_raw = st.tabs(
        ["개요·KPI", "경향분석", "원인·유형", "재발", "팀별·외주", "연계 현황", "원본 데이터"]
    )

    # ─── 개요·KPI ─────────────────────────────────────────────────────
    with tab_kpi:
        if ftab.empty:
            st.info(f"선택 필터에 {label} 데이터가 없습니다.")
        else:
            total = _wcount(ftab)
            n_own = _wcount(ftab, ftab.get("자사/외주") == "자사") if "자사/외주" in ftab.columns else 0
            n_out = _wcount(ftab, ftab.get("자사/외주") == "외주") if "자사/외주" in ftab.columns else 0
            n_closed = _wcount(ftab, ftab.get("완료여부") == "C") if "완료여부" in ftab.columns else 0
            n_chain_done = _wcount(ftab, ftab.get("최종 종결 여부(체인)") == True) if "최종 종결 여부(체인)" in ftab.columns else 0
            close_rate = safe_pct(n_closed, total)
            chain_rate = safe_pct(n_chain_done, total)

            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric(f"총 {label}", f"{total}건")
            k2.metric("자사", f"{n_own}건")
            k3.metric("외주", f"{n_out}건")
            k4.metric("종결률", f"{close_rate:.1f}%")
            k5.metric("최종 종결률(체인)", f"{chain_rate:.1f}%")

            st.markdown("---")
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("##### 자사 vs 외주")
                if "자사/외주" in ftab.columns:
                    vc = _wgroupby(ftab, "자사/외주", name="건수")
                    fig = px.bar(vc, x="자사/외주", y="건수", text="건수",
                                 color="자사/외주",
                                 color_discrete_map={"자사": CHART_COLORS.get("blue", "#1f77b4"),
                                                      "외주": CHART_COLORS.get("orange", "#ff7f0e")})
                    fig.update_traces(textposition="outside")
                    fig.update_layout(height=300, plot_bgcolor="white", showlegend=False,
                                       margin=dict(l=10, r=10, t=10, b=10))
                    st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_kpi_src")
            with col_b:
                if kind == "일탈":
                    st.markdown("##### 일탈 등급 분포")
                    if "일탈 등급 대분류" in ftab.columns:
                        g = _wgroupby(ftab, "일탈 등급 대분류", name="건수")
                        g = g[g["건수"] > 0]
                        if not g.empty:
                            fig2 = px.pie(g, values="건수", names="일탈 등급 대분류", hole=0.4,
                                          color_discrete_sequence=px.colors.qualitative.Set2)
                            fig2.update_traces(textinfo="label+value+percent")
                            fig2.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=10))
                            st.plotly_chart(fig2, use_container_width=True, key=f"{key_prefix}_kpi_grade")
                else:
                    st.markdown("##### 진행상태 분포")
                    if "진행상태" in ftab.columns:
                        g = _wgroupby(ftab, "진행상태", name="건수")
                        g = g[g["건수"] > 0]
                        if not g.empty:
                            fig2 = px.pie(g, values="건수", names="진행상태", hole=0.4,
                                          color_discrete_sequence=px.colors.qualitative.Pastel)
                            fig2.update_traces(textinfo="label+percent")
                            fig2.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=10))
                            st.plotly_chart(fig2, use_container_width=True, key=f"{key_prefix}_kpi_status")

    # ─── 경향분석 (월별 추이 + 연/팀 + YoY) ─────────────────────────────
    with tab_trend:
        st.caption(f"**{trend_year}년 기준** · 접수·발견일 기반 월별 추이와 YoY 비교.")
        if ftab_y.empty:
            st.info(f"{trend_year}년 {label} 데이터가 없습니다.")
        else:
            # ── 월별 추이 (자사/외주 분리)
            st.markdown("##### 월별 추이 (자사/외주)")
            src_col = "자사/외주" if "자사/외주" in ftab_y.columns else None
            month_basis_col = "접수월" if (kind == "일탈" and "접수월" in ftab_y.columns) else mc

            if month_basis_col == "접수월":
                tmp = ftab_y.copy()
                tmp["_월"] = pd.to_numeric(tmp["접수월"].astype(str).str[5:7], errors="coerce")
                tmp = tmp.dropna(subset=["_월"])
                if src_col:
                    g_m = _wgroupby(tmp, ["_월", src_col], name="건수")
                    piv = g_m.pivot_table(index="_월", columns=src_col,
                                             values="건수", aggfunc="sum", fill_value=0)
                else:
                    g_m = _wgroupby(tmp, "_월", name="건수")
                    piv = g_m.set_index("_월")[["건수"]]
                piv = piv.reindex(range(1, 13), fill_value=0)
            else:
                if mc in ftab_y.columns and src_col:
                    g_m = _wgroupby(ftab_y, [mc, src_col], name="건수")
                    piv = g_m.pivot_table(index=mc, columns=src_col,
                                             values="건수", aggfunc="sum", fill_value=0)
                    piv = piv.reindex(range(1, 13), fill_value=0)
                elif mc in ftab_y.columns:
                    monthly = _monthly_weighted_series(ftab_y, mc)
                    piv = pd.DataFrame({"건수": monthly["건수"].tolist()},
                                         index=range(1, 13))
                else:
                    piv = pd.DataFrame(index=range(1, 13))

            if piv.empty or piv.sum().sum() == 0:
                st.info("월별 데이터 없음")
            else:
                fig_m = go.Figure()
                color_map = {"자사": CHART_COLORS.get("blue", "#1f77b4"),
                              "외주": CHART_COLORS.get("orange", "#ff7f0e"),
                              "건수": CHART_COLORS.get("red", "#d62728")}
                for col in piv.columns:
                    fig_m.add_trace(go.Scatter(
                        x=[f"{m}월" for m in piv.index.astype(int)],
                        y=piv[col].tolist(),
                        mode="lines+markers+text",
                        text=[int(v) for v in piv[col].tolist()],
                        textposition="top center",
                        name=str(col),
                        line=dict(color=color_map.get(str(col), "#636efa"), width=2),
                        marker=dict(size=7),
                    ))
                fig_m.update_layout(height=320, plot_bgcolor="white",
                                      margin=dict(l=20, r=20, t=10, b=30),
                                      legend=dict(orientation="h", y=1.05))
                st.plotly_chart(fig_m, use_container_width=True, key=f"{key_prefix}_trend_month")

                table = piv.reset_index()
                first_col = table.columns[0]
                table = table.rename(columns={first_col: "월"})
                table["월"] = pd.to_numeric(table["월"], errors="coerce").fillna(0).astype(int).astype(str) + "월"
                st.dataframe(table, use_container_width=True, hide_index=True)

            if kind == "일탈":
                st.markdown("##### 일탈 등급 (대분류) × 자사/외주")
                if "일탈 등급 대분류" in ftab_y.columns:
                    if "자사/외주" in ftab_y.columns:
                        g1 = _wgroupby(ftab_y, ["일탈 등급 대분류", "자사/외주"], name="건수")
                    else:
                        g1 = _wgroupby(ftab_y, "일탈 등급 대분류", name="건수").assign(**{"자사/외주": "전체"})
                    grade_order = ["Critical", "Major", "Minor", "미판정"]
                    fig_s1 = px.bar(g1, x="일탈 등급 대분류", y="건수",
                                     color="자사/외주", barmode="stack", text="건수",
                                     category_orders={"일탈 등급 대분류": grade_order},
                                     color_discrete_map={"자사": CHART_COLORS.get("blue", "#1f77b4"),
                                                          "외주": CHART_COLORS.get("orange", "#ff7f0e")})
                    fig_s1.update_traces(textposition="inside")
                    fig_s1.update_layout(height=320, plot_bgcolor="white",
                                           margin=dict(l=10, r=10, t=10, b=10),
                                           legend=dict(orientation="h", y=1.05))
                    st.plotly_chart(fig_s1, use_container_width=True, key=f"{key_prefix}_trend_grade")

            # YoY
            st.markdown(f"##### YoY 비교 — {trend_year} vs {trend_year - 1}")
            if ftab_y_prev.empty:
                st.info("전년도 데이터가 없어 YoY 비교가 불가합니다.")
            else:
                n_cur = _wcount(ftab_y)
                n_prv = _wcount(ftab_y_prev)
                own_cur = _wcount(ftab_y, ftab_y.get("자사/외주") == "자사") if "자사/외주" in ftab_y.columns else 0
                own_prv = _wcount(ftab_y_prev, ftab_y_prev.get("자사/외주") == "자사") if "자사/외주" in ftab_y_prev.columns else 0
                out_cur = _wcount(ftab_y, ftab_y.get("자사/외주") == "외주") if "자사/외주" in ftab_y.columns else 0
                out_prv = _wcount(ftab_y_prev, ftab_y_prev.get("자사/외주") == "외주") if "자사/외주" in ftab_y_prev.columns else 0

                def _pct(n, p):
                    return ((n - p) * 100 / p) if p else 0.0
                c1, c2, c3 = st.columns(3)
                c1.metric(f"총 {label}", f"{n_cur}건", delta=f"{_pct(n_cur, n_prv):+.1f}%")
                c2.metric("자사", f"{own_cur}건", delta=f"{_pct(own_cur, own_prv):+.1f}%")
                c3.metric("외주", f"{out_cur}건", delta=f"{_pct(out_cur, out_prv):+.1f}%")

    # ─── 원인·유형 (자사/외주 분리) ─────────────────────────────────────
    with tab_cause:
        if ftab.empty:
            st.info(f"{label} 데이터가 없습니다.")
        else:
            if "발생 유형" in ftab.columns:
                st.markdown("##### 발생 유형 — 자사/외주 분리")
                _render_source_split(ftab, f"{key_prefix}_cause_main",
                                       dim="발생 유형", orientation="h", top_n=15)
            else:
                st.info("`발생 유형` 컬럼 없음")

            st.divider()
            if "발생 세부유형" in ftab.columns:
                st.markdown("##### 발생 세부유형 — 자사/외주 분리 (Top 20)")
                _render_source_split(ftab, f"{key_prefix}_cause_sub",
                                       dim="발생 세부유형", orientation="h", top_n=20)

            st.divider()
            st.markdown("##### 이상발생 원인 (Analyst Error 등)")
            if "이상발생 원인" in ftab.columns:
                _render_source_split(ftab, f"{key_prefix}_cause_aer",
                                       dim="이상발생 원인", orientation="h", top_n=15)

    # ─── 재발 (자사/외주 분리) ─────────────────────────────────────────
    with tab_recur:
        if ftab.empty:
            st.info(f"{label} 데이터가 없습니다.")
        elif "재발여부" not in ftab.columns:
            st.warning("`재발여부` 필드가 아직 수집되지 않았습니다. 데이터 갱신 후 표시됩니다.")
        else:
            rf = ftab[ftab["재발여부"].notna() & (ftab["재발여부"].astype(str).str.strip() != "")]
            if rf.empty:
                st.info("재발여부 값이 없습니다.")
            else:
                st.markdown("##### 재발여부 분포 — 자사/외주 분리")
                if "자사/외주" in rf.columns:
                    r_own, r_out = st.columns(2)
                    for col_ui, src in [(r_own, "자사"), (r_out, "외주")]:
                        with col_ui:
                            st.caption(f"**{src}**")
                            sub_s = rf[rf["자사/외주"] == src]
                            if sub_s.empty:
                                st.info("데이터 없음")
                                continue
                            g = _wgroupby(sub_s, "재발여부", name="건수")
                            fig = px.pie(g, values="건수", names="재발여부", hole=0.35,
                                           color_discrete_sequence=px.colors.qualitative.Pastel)
                            fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=10))
                            fig.update_traces(textinfo="label+value+percent")
                            st.plotly_chart(fig, use_container_width=True,
                                             key=f"{key_prefix}_recur_{src}")
                else:
                    g = _wgroupby(rf, "재발여부", name="건수")
                    fig = px.pie(g, values="건수", names="재발여부", hole=0.35)
                    st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_recur_all")

                st.divider()
                st.markdown("##### 재발건 샘플 목록")
                show_cols = [c for c in ["관리번호", "자사/외주", "제목", "재발여부", "작성팀",
                                           "이상발생 원인", "발생 유형", "발생 세부유형"]
                             if c in rf.columns]
                st.dataframe(rf[show_cols].head(80), use_container_width=True,
                              hide_index=True, height=320)

    # ─── 팀별·외주 (자사/외주 분리) ─────────────────────────────────────
    with tab_team:
        if ftab.empty:
            st.info(f"{label} 데이터가 없습니다.")
        else:
            if "자사/외주" in ftab.columns:
                t_own_tab, t_out_tab, t_all_tab = st.tabs(["자사 팀", "외주 위탁업체", "통합"])
                with t_own_tab:
                    own_df = ftab[ftab["자사/외주"] == "자사"]
                    if own_df.empty or "작성팀" not in own_df.columns:
                        st.info("자사 데이터 없음")
                    else:
                        gg = _wgroupby(own_df, "작성팀", name="건수").sort_values("건수", ascending=True)
                        gg = gg[gg["작성팀"].astype(str).str.strip() != ""]
                        fig = px.bar(gg, x="건수", y="작성팀", orientation="h", text="건수",
                                       color_discrete_sequence=[CHART_COLORS.get("blue", "#1f77b4")])
                        fig.update_traces(textposition="outside")
                        fig.update_layout(height=max(300, 22 * len(gg)),
                                            plot_bgcolor="white",
                                            margin=dict(l=10, r=30, t=10, b=10))
                        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_team_own")
                with t_out_tab:
                    out_df = ftab[ftab["자사/외주"] == "외주"]
                    if out_df.empty:
                        st.info("외주 데이터 없음")
                    else:
                        dim = "위탁업체" if "위탁업체" in out_df.columns else "작성팀"
                        gg = _wgroupby(out_df, dim, name="건수").sort_values("건수", ascending=True)
                        gg = gg[gg[dim].astype(str).str.strip() != ""]
                        fig = px.bar(gg, x="건수", y=dim, orientation="h", text="건수",
                                       color_discrete_sequence=[CHART_COLORS.get("orange", "#ff7f0e")])
                        fig.update_traces(textposition="outside")
                        fig.update_layout(height=max(300, 22 * len(gg)),
                                            plot_bgcolor="white",
                                            margin=dict(l=10, r=30, t=10, b=10))
                        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_team_out")

                        if kind == "일탈" and "일탈 등급 대분류" in out_df.columns:
                            maj = out_df[out_df["일탈 등급 대분류"] == "Major"]
                            if not maj.empty:
                                st.caption("Major 일탈 발생 외주업체")
                                disp_cols = [c for c in ["관리번호", "위탁업체", "일탈 등급", "제목", "접수월"]
                                             if c in maj.columns]
                                st.dataframe(maj[disp_cols], use_container_width=True,
                                              hide_index=True, height=220)
                with t_all_tab:
                    if "작성팀" in ftab.columns:
                        gg = _wgroupby(ftab, ["작성팀", "자사/외주"], name="건수")
                        gg = gg[gg["작성팀"].astype(str).str.strip() != ""]
                        piv = gg.pivot_table(index="작성팀", columns="자사/외주",
                                                values="건수", aggfunc="sum", fill_value=0)
                        piv = piv.loc[piv.sum(axis=1).sort_values(ascending=True).index]
                        fig = go.Figure()
                        for src, color in [("자사", CHART_COLORS.get("blue", "#1f77b4")),
                                            ("외주", CHART_COLORS.get("orange", "#ff7f0e"))]:
                            if src in piv.columns:
                                fig.add_trace(go.Bar(name=src, y=piv.index, x=piv[src],
                                                      orientation="h",
                                                      marker_color=color,
                                                      text=[int(v) for v in piv[src]],
                                                      textposition="inside"))
                        fig.update_layout(barmode="stack",
                                            height=max(300, 22 * len(piv)),
                                            plot_bgcolor="white",
                                            margin=dict(l=10, r=30, t=10, b=10),
                                            legend=dict(orientation="h", y=1.05))
                        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_team_all")

    # ─── 연계 현황 ────────────────────────────────────────────────────
    with tab_link:
        ctx = st.session_state.get("qms_linkage_ctx")
        if ctx is None:
            st.info("연계 분석 컨텍스트가 없습니다.")
        else:
            # 해당 kind 행들의 관리번호만으로 서브셋 만들어 render_linkage_section 내부 로직과 동일하게 출력
            render_linkage_section("deviation", key_suffix=f"{key_prefix}_link",
                                    title=f"{label} 체인 연계 현황",
                                    df_override=ftab)

    # ─── 원본 데이터 ───────────────────────────────────────────────────
    with tab_raw:
        render_raw_data_section(
            default_project_keys=["deviation", "deviationoutsourcing"],
            key_suffix=key_prefix,
            allow_change=False,
            title=f"원본 데이터 ({label} · 자사/외주)",
            extra_priority=[
                "이벤트 구분", "일탈 등급", "일탈 등급 대분류",
                "자사/외주", "이벤트 접수일자", "접수월",
                "발생 유형", "발생 세부유형", "위탁업체", "작성팀",
                "재발여부", "이상발생 원인",
            ],
            df_override=ftab,
        )

    render_footer()


# ============================================================================
# 탭 구성
# ============================================================================

# 기한 초과 건수 계산 (탭 배지용)
_overdue_all_count = sum(
    int(df_p["D-day"].lt(0).sum())
    for df_p in F.values()
    if not df_p.empty and "D-day" in df_p.columns
)
_deadline_label = f"⏰ 기한관리 ({_overdue_all_count}건 초과)" if _overdue_all_count > 0 else "⏰ 기한관리"

tab_exec, tab_oos, tab_dev, tab_incident, tab_inv, tab_capa, tab_change, \
tab_complain, tab_workflow, tab_deadline, tab_settings = st.tabs([
    "📊 KPI",
    "🔬 OOS", "🧪 일탈", "⚠️ 인시던트", "🔍 조사",
    "✅ CAPA관리", "🔄 변경관리", "📢 고객불만",
    "🔗 워크플로우", _deadline_label, "⚙️ 설정",
])


# ============================================================================
# 탭 1: 경영진 대시보드
# ============================================================================

with tab_exec:
    render_header("경영진 품질 대시보드", f"MFDS GMP 점검 대비 KPI | {datetime.now().strftime('%Y-%m-%d')}")
    st.markdown("---")

    # KPI 산출
    def _completion_rate(df):
        if df.empty or "완료여부" not in df.columns:
            return 0.0
        return safe_pct((df["완료여부"] == "C").sum(), len(df))

    primary_year = selected_years[0] if selected_years else current_year
    prev_year = primary_year - 1

    capa_all = pd.concat([fcapa, fcapaai, fai], ignore_index=True) if any(not d.empty for d in [fcapa, fcapaai, fai]) else pd.DataFrame()
    capa_rate = _completion_rate(capa_all)
    chg_all = pd.concat([fchg, fchgai], ignore_index=True) if any(not d.empty for d in [fchg, fchgai]) else pd.DataFrame()
    change_rate = _completion_rate(chg_all)

    avg_complaint_days = None
    if not fcmp.empty and "접수일" in fcmp.columns and "처리완료일" in fcmp.columns:
        receipt = pd.to_datetime(fcmp["접수일"], errors="coerce")
        complete = pd.to_datetime(fcmp["처리완료일"], errors="coerce")
        delta = (complete - receipt).dt.days.dropna()
        if not delta.empty:
            avg_complaint_days = round(delta.mean(), 1)

    # KPI 1: Analyst error 감소율(막대) + 게이지 3개 (QMS_Dashboard.py 마감회의 탭과 동일 로직)
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        render_analyst_error_reduction_kpi(
            foos, ALL_DFS.get("oos", pd.DataFrame()), primary_year, prev_year, year_col=YEAR_FILTER_COL
        )
    with g2:
        st.plotly_chart(kpi_gauge(capa_rate, 90, "CAPA 이행률"), use_container_width=True)
    with g3:
        st.plotly_chart(kpi_gauge(change_rate, 85, "변경 완료율"), use_container_width=True)
    with g4:
        val = avg_complaint_days if avg_complaint_days is not None else 0
        st.plotly_chart(kpi_gauge(val, 30, "불만 평균처리일", suffix="일", inverse=True), use_container_width=True)

    st.divider()

    # 프로젝트별 KPI 카드 (주요 8개) — 스파크라인 포함
    S.section_header("프로젝트별 현황 요약", "📋")
    main_projects = [
        ("oos", foos), ("deviation", fdev), ("capa", fcapa), ("actionitem", fai),
        ("changemanagement", fchg), ("complain", fcmp), ("extension", fext), ("investigation", finv),
    ]
    kpi_cols = st.columns(8)
    for col_ui, (pk, df_p) in zip(kpi_cols, main_projects):
        label = PROJECT_META[pk]["label"]
        color = PROJECT_META[pk]["color"]
        total = weighted_metric_total(df_p)
        done = weighted_metric_completed(df_p)
        rate = safe_pct(done, total)
        overdue = weighted_metric_overdue(df_p)
        # 월별 스파크라인 데이터
        mc = _month_col_for_df(df_p)
        spark = []
        if not df_p.empty and mc in df_p.columns:
            _s = _monthly_weighted_series(df_p, mc)
            spark = [round(v) for v in _s["건수"].tolist()]
        status = "bad" if overdue > 0 else ("good" if rate >= 80 else "warn")
        delta_str = f"완료 {rate:.0f}%" if done else None
        with col_ui:
            S.metric_with_sparkline(
                label=label,
                value=f"{round(total)}건",
                delta=delta_str,
                spark_values=spark,
                spark_color=color,
                status=status,
            )

    st.divider()

    # 월별 추이 (주요 5개 프로젝트 라인) + 트렌드 예측선
    S.section_header("월별 발생 건수 추이", "📈")
    _show_forecast = st.toggle("3개월 예측선 표시", value=False, key="kpi_forecast")
    fig_trend = go.Figure()
    for pk, df_p in [("oos", foos), ("deviation", fdev), ("capa", fcapa), ("changemanagement", fchg), ("complain", fcmp)]:
        mc = _month_col_for_df(df_p)
        if df_p.empty or mc not in df_p.columns:
            continue
        full = _monthly_weighted_series(df_p, mc)
        ys = [round(v) for v in full["건수"].tolist()]
        color = PROJECT_META[pk]["color"]
        label = PROJECT_META[pk]["label"]
        fig_trend.add_trace(go.Scatter(
            x=MONTH_LABELS, y=ys, name=label,
            mode="lines+markers", line=dict(color=color, width=2), marker=dict(size=5),
        ))
        if _show_forecast:
            # 데이터 있는 월만 사용해 선형 회귀 예측
            xs_fit = [i for i, v in enumerate(ys) if v > 0]
            ys_fit = [ys[i] for i in xs_fit]
            if len(xs_fit) >= 3:
                try:
                    coef = np.polyfit(xs_fit, ys_fit, 1)
                    fc_x = list(range(12, 15))
                    fc_y = [max(0, round(np.polyval(coef, x))) for x in fc_x]
                    fc_labels = ["13월(예측)", "14월(예측)", "15월(예측)"]
                    fig_trend.add_trace(go.Scatter(
                        x=fc_labels, y=fc_y, name=f"{label} 예측",
                        mode="lines+markers",
                        line=dict(color=color, width=1.5, dash="dot"),
                        marker=dict(size=4, symbol="diamond"),
                        showlegend=False,
                    ))
                except Exception:
                    pass
    fig_trend.update_layout(
        height=360, margin=dict(l=40, r=20, t=10, b=40),
        plot_bgcolor="white",
        legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center"),
    )
    fig_trend.update_xaxes(showgrid=True, gridcolor="#f0f0f0")
    fig_trend.update_yaxes(showgrid=True, gridcolor="#f0f0f0")
    st.plotly_chart(fig_trend, use_container_width=True)

    st.divider()

    # 하단 2단: 기한초과 + 신규 프로젝트 분포
    ov1, ov2 = st.columns(2)
    with ov1:
        st.markdown("#### 🚨 기한 초과 항목 (전 프로젝트)")
        overdue_frames = []
        for pk, df_p in F.items():
            if df_p.empty or "D-day" not in df_p.columns:
                continue
            dday_num = _num_series(df_p["D-day"], default=0.0)
            over = df_p[dday_num < 0].copy()
            if not over.empty:
                over["프로젝트"] = PROJECT_META[pk]["label"]
                overdue_frames.append(over)
        if overdue_frames:
            overdue_all = pd.concat(overdue_frames, ignore_index=True)
            disp = ["프로젝트"] + [c for c in ["관리번호", "제목", "기한일", "D-day"] if c in overdue_all.columns]
            st.dataframe(overdue_all[disp].sort_values("D-day").head(20),
                         use_container_width=True, hide_index=True, height=320,
                         column_config={"D-day": st.column_config.NumberColumn("D-day", format="%d일")})
        else:
            st.success("기한 초과 항목이 없습니다.")

    with ov2:
        S.section_header("프로젝트별 진행/완료 비율", "📊")
        status_rows = []
        for pk, df_p in F.items():
            if df_p.empty:
                continue
            tw = weighted_metric_total(df_p)
            dw = weighted_metric_completed(df_p)
            if tw > 0:
                status_rows.append({
                    "프로젝트": PROJECT_META[pk]["label"],
                    "진행중": round(tw - dw, 2),
                    "완료": round(dw, 2),
                })
        if status_rows:
            sdf = pd.DataFrame(status_rows).sort_values("진행중", ascending=True)
            fig_s = go.Figure()
            fig_s.add_trace(go.Bar(name="진행중", x=sdf["진행중"], y=sdf["프로젝트"], orientation="h",
                                    marker_color=CHART_COLORS["orange"], text=sdf["진행중"], textposition="inside"))
            fig_s.add_trace(go.Bar(name="완료", x=sdf["완료"], y=sdf["프로젝트"], orientation="h",
                                    marker_color=CHART_COLORS["green"], text=sdf["완료"], textposition="inside"))
            fig_s.update_layout(barmode="stack", height=360, margin=dict(l=0, r=20, t=10, b=10),
                                 legend=dict(orientation="h", y=1.05), plot_bgcolor="white")
            st.plotly_chart(fig_s, use_container_width=True)

    st.divider()

    # YoY 비교 파넬 (전년 대비)
    S.section_header("전년 대비 주요 지표 (YoY)", "📅")
    _yoy_projects = [("oos", foos), ("deviation", fdev), ("capa", fcapa), ("complain", fcmp)]
    _yoy_rows = []
    for pk, df_p in _yoy_projects:
        if df_p.empty:
            continue
        ycol_yoy = YEAR_FILTER_COL if YEAR_FILTER_COL in df_p.columns else "연도"
        if ycol_yoy not in df_p.columns:
            continue
        py = selected_years[0] if selected_years else current_year
        prev_y = py - 1
        curr_cnt = _wcount(df_p[df_p[ycol_yoy] == py]) if py else 0
        prev_cnt = _wcount(df_p[df_p[ycol_yoy] == prev_y]) if prev_y else 0
        _yoy_rows.append({
            "프로젝트": PROJECT_META[pk]["label"],
            f"{prev_y}년": prev_cnt,
            f"{py}년": curr_cnt,
            "증감": curr_cnt - prev_cnt,
            "증감률": f"{(curr_cnt - prev_cnt) / prev_cnt * 100:+.1f}%" if prev_cnt > 0 else "-",
        })
    if _yoy_rows:
        _yoy_df = pd.DataFrame(_yoy_rows)
        _yoy_curr = selected_years[0] if selected_years else current_year
        _yoy_prev = _yoy_curr - 1
        prev_col = f"{_yoy_prev}년"
        curr_col = f"{_yoy_curr}년"
        fig_yoy = go.Figure()
        fig_yoy.add_trace(go.Bar(
            name=prev_col, x=_yoy_df["프로젝트"], y=_yoy_df[prev_col],
            marker_color=CHART_COLORS["gray"], text=_yoy_df[prev_col], textposition="outside",
        ))
        fig_yoy.add_trace(go.Bar(
            name=curr_col, x=_yoy_df["프로젝트"], y=_yoy_df[curr_col],
            marker_color=CHART_COLORS["blue"], text=_yoy_df[curr_col], textposition="outside",
        ))
        fig_yoy.update_layout(
            barmode="group", height=300,
            margin=dict(l=10, r=10, t=20, b=10),
            plot_bgcolor="white",
            legend=dict(orientation="h", y=1.08),
        )
        st.plotly_chart(fig_yoy, use_container_width=True)
        _yoy_disp = [c for c in _yoy_df.columns if c != "증감"]
        st.dataframe(_yoy_df[_yoy_disp], use_container_width=True, hide_index=True)

    render_footer()


# ============================================================================
# 품질이상 공통 사전 계산 (OOS / 일탈 / 조사 상위 탭에서 공유)
# ============================================================================

_primary_year = selected_years[0] if selected_years else current_year
_prev_year = _primary_year - 1
_ycol = YEAR_FILTER_COL
_oos_ny = apply_filters_no_year(ALL_DFS["oos"])
_dev_ny = apply_filters_no_year(ALL_DFS["deviation"])
_inv_ny = apply_filters_no_year(ALL_DFS["investigation"])
_mc_oos = _month_col_for_df(foos)


# ============================================================================
# 탭 2: OOS
# ============================================================================

with tab_oos:
    render_header("OOS (Out of Specification)")
    st.markdown("---")
    primary_year = _primary_year
    prev_year = _prev_year
    ycol = _ycol
    oos_ny = _oos_ny
    mc_oos = _mc_oos

    o_tab1, o_tab2, o_tab3, o_tab4, o_tab_link, o_tab_raw = st.tabs(
        ["현황", "경향분석", "경향분석보고서", "마감회의 & GMP", "연계 현황", "원본 데이터"]
    )
    with o_tab1:
        oos_panels.render_oos_status(
            foos, primary_year, ycol, selected_years, CHART_COLORS, safe_pct, COMPLETED_KEYWORDS,
        )
    with o_tab2:
        oos_panels.render_oos_trend(
            foos, oos_ny, primary_year, prev_year, selected_years, ycol, mc_oos,
            CHART_COLORS, safe_pct, COMPLETED_KEYWORDS,
        )
    with o_tab3:
        oos_panels.render_oos_report(foos, CHART_COLORS, safe_pct, COMPLETED_KEYWORDS)
    with o_tab4:
        oos_panels.render_oos_gmp(
            foos, oos_ny, primary_year, prev_year, ycol, mc_oos, CHART_COLORS, safe_pct, COMPLETED_KEYWORDS,
        )
    with o_tab_link:
        render_linkage_section("oos", key_suffix="oos",
                                title="OOS → 조사 → CAPA → AI 체인 연계 현황")
    with o_tab_raw:
        render_raw_data_section(
            default_project_keys=["oos"],
            key_suffix="oos",
            allow_change=True,
            title="원본 데이터 (기본: OOS · 필요 시 다른 프로젝트 추가 가능)",
            oos_filters=True,
            pqr_mode=True,
            detail_view=True,
        )

    render_footer()


# ============================================================================
# ============================================================================
# 탭 3: 일탈 (자사 · 외주)
# ============================================================================

with tab_dev:
    render_event_category_tab(
        kind="일탈",
        key_prefix="dev",
        fdev_full=fdev,
        ycol_in=_ycol,
        primary_year_in=_primary_year,
    )


# ============================================================================
# 탭 3-2: 인시던트 (자사 · 외주)
# ============================================================================

with tab_incident:
    render_event_category_tab(
        kind="인시던트",
        key_prefix="inc",
        fdev_full=fdev,
        ycol_in=_ycol,
        primary_year_in=_primary_year,
    )


# ============================================================================
# 탭 4: 조사
# ============================================================================

with tab_inv:
    render_header("조사 (Investigation)")
    st.markdown("---")

    i_overview, i_m1e, i_trend, i_link, i_tab_raw = st.tabs(
        ["개요·KPI", "5M1E 상세", "추이·팀별", "연계 현황", "원본 데이터"]
    )

    with i_overview:
        if finv.empty:
            st.warning("조사 데이터가 없습니다.")
        else:
            t_i = weighted_metric_total(finv)
            d_i = weighted_metric_completed(finv)
            o_i = weighted_metric_overdue(finv)
            base_u = finv.drop_duplicates(subset=["관리번호"]) if "관리번호" in finv.columns else finv
            chain_rate = safe_pct(
                (base_u.get("최종 종결 여부(체인)") == True).sum() if "최종 종결 여부(체인)" in base_u.columns else 0,
                len(base_u),
            )
            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("전체(가중)", f"{round(t_i)}건")
            k2.metric("완료(가중)", f"{round(d_i)}건")
            k3.metric("완료율", f"{safe_pct(d_i, t_i):.0f}%")
            k4.metric("기한초과(가중)", f"{round(o_i)}건")
            k5.metric("체인 종결률", f"{chain_rate:.0f}%")

    with i_m1e:
        if finv.empty:
            st.warning("조사 데이터가 없습니다.")
        else:
            st.subheader("5M1E 원인 조사 현황")
            m1e_cols = [c for c in finv.columns if c.startswith("5M1E_") and not c.endswith("_내용")]
            if m1e_cols:
                m1e_data = finv[m1e_cols].apply(lambda x: (x == "수행").sum()).reset_index()
                m1e_data.columns = ["항목", "수행 건수"]
                m1e_data["항목"] = m1e_data["항목"].str.replace("5M1E_", "")
                fig_m1e = px.bar(m1e_data.sort_values("수행 건수", ascending=True),
                                 x="수행 건수", y="항목", orientation="h",
                                 color_discrete_sequence=["#795548"], text="수행 건수")
                fig_m1e.update_layout(height=300, margin=dict(l=0, r=20, t=10, b=10),
                                       plot_bgcolor="white")
                fig_m1e.update_traces(textposition="outside")
                st.plotly_chart(fig_m1e, use_container_width=True)

                st.markdown("##### 5M1E 항목별 수행 비율")
                per_row = []
                for c in m1e_cols:
                    done = int((finv[c] == "수행").sum())
                    total = int(finv[c].notna().sum())
                    per_row.append({"항목": c.replace("5M1E_", ""),
                                     "수행": done, "미수행/기타": max(total - done, 0)})
                m1e_df = pd.DataFrame(per_row).melt(id_vars="항목", var_name="구분", value_name="건수")
                fig_m = px.bar(m1e_df, x="항목", y="건수", color="구분", barmode="stack",
                               color_discrete_map={"수행": CHART_COLORS.get("green", "#2ca02c"),
                                                    "미수행/기타": CHART_COLORS.get("gray", "#7f7f7f")})
                fig_m.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                                     plot_bgcolor="white")
                st.plotly_chart(fig_m, use_container_width=True)
            else:
                st.info("5M1E 컬럼이 없습니다.")

    with i_trend:
        if finv.empty:
            st.warning("조사 데이터가 없습니다.")
        else:
            mc_i = _month_col_for_df(finv)
            st.markdown("##### 월별 조사 발생 추이")
            if mc_i in finv.columns:
                mf = _monthly_weighted_series(finv, mc_i)
                fig = go.Figure(go.Scatter(
                    x=MONTH_LABELS, y=[round(v) for v in mf["건수"].tolist()],
                    mode="lines+markers", line=dict(color=CHART_COLORS.get("blue", "#1f77b4"), width=2)
                ))
                fig.update_layout(height=280, plot_bgcolor="white",
                                    margin=dict(l=30, r=10, t=10, b=30))
                st.plotly_chart(fig, use_container_width=True)

            st.markdown("##### 팀별 조사 현황")
            tcol = "작성팀" if "작성팀" in finv.columns else None
            if tcol:
                tm = (finv.drop_duplicates(subset=["관리번호"])
                            .groupby(tcol).size().reset_index(name="건수")
                            .sort_values("건수", ascending=True))
                fig_t = px.bar(tm, x="건수", y=tcol, orientation="h", text="건수",
                               color_discrete_sequence=[CHART_COLORS.get("purple", "#9467bd")])
                fig_t.update_traces(textposition="outside")
                fig_t.update_layout(height=max(260, 22 * len(tm)),
                                     margin=dict(l=10, r=30, t=10, b=10),
                                     plot_bgcolor="white")
                st.plotly_chart(fig_t, use_container_width=True)
            else:
                st.info("작성팀 컬럼 없음")

    with i_link:
        render_linkage_section("investigation", key_suffix="inv",
                                title="조사 체인 연계 현황 (OOS/일탈 → 조사 → CAPA → AI)")

    with i_tab_raw:
        render_raw_data_section(
            default_project_keys=["investigation"],
            key_suffix="inv",
            allow_change=False,
            title="원본 데이터 (조사)",
            extra_priority=["작성팀", "부모 프로젝트", "부모 관리번호",
                             "자식 수(전체)", "자식 미종결 수", "체인 최대 깊이"],
        )

    render_footer()


# ============================================================================
# 탭 3: CAPA 관리
# ============================================================================

with tab_capa:
    render_header("CAPA & Action Item 관리")
    st.markdown("---")

    c_t = len(fcapa); c_d = int((fcapa["완료여부"] == "C").sum()) if "완료여부" in fcapa.columns and c_t else 0
    ai_t = len(fai); ai_d = int((fai["완료여부"] == "C").sum()) if "완료여부" in fai.columns and ai_t else 0
    cai_t = len(fcapaai); cai_d = int((fcapaai["완료여부"] == "C").sum()) if "완료여부" in fcapaai.columns and cai_t else 0

    capa_kpi, capa_status, capa_ai, capa_deadline, capa_link, capa_tab_raw = st.tabs(
        ["통합 KPI", "CAPA 현황", "Action Item 이행", "기한·지연", "연계 현황", "원본 데이터"]
    )

    with capa_kpi:
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("CAPA", f"{c_t}건", delta=f"완료 {safe_pct(c_d, c_t):.0f}%")
        k2.metric("CAPA AI", f"{cai_t}건", delta=f"완료 {safe_pct(cai_d, cai_t):.0f}%")
        k3.metric("모니터링AI", f"{ai_t}건", delta=f"완료 {safe_pct(ai_d, ai_t):.0f}%")
        k4.metric("통합 이행률",
                   f"{safe_pct(c_d + ai_d + cai_d, c_t + ai_t + cai_t):.0f}%")
        k5.metric("기한초과",
                   f"{sum(int((d['D-day'].fillna(999) < 0).sum()) for d in [fcapa, fcapaai, fai] if not d.empty and 'D-day' in d.columns)}건")

    with capa_status:
        if fcapa.empty:
            st.info("CAPA 데이터가 없습니다.")
        else:
            cc1, cc2 = st.columns(2)
            with cc1:
                st.subheader("CAPA 진행상태")
                if "진행상태" in fcapa.columns:
                    sd = fcapa["진행상태"].value_counts().reset_index()
                    sd.columns = ["상태", "건수"]
                    fig_cs = px.pie(sd, values="건수", names="상태", hole=0.5,
                                    color_discrete_sequence=px.colors.qualitative.Set2)
                    fig_cs.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=10))
                    fig_cs.update_traces(textinfo="label+value", textfont_size=11)
                    st.plotly_chart(fig_cs, use_container_width=True)
            with cc2:
                st.subheader("CAPA 구분")
                if "CAPA 구분" in fcapa.columns:
                    gd = fcapa["CAPA 구분"].value_counts().reset_index()
                    gd.columns = ["구분", "건수"]
                    fig_g = px.bar(gd, x="구분", y="건수", text="건수",
                                    color_discrete_sequence=[CHART_COLORS.get("blue", "#1f77b4")])
                    fig_g.update_traces(textposition="outside")
                    fig_g.update_layout(height=280, plot_bgcolor="white")
                    st.plotly_chart(fig_g, use_container_width=True)

            st.divider()
            st.subheader("CAPA 상세 목록")
            capa_disp = [c for c in ["관리번호", "제목", "등록자", "기한일", "진행상태",
                                      "D-day", "CAPA 구분", "사유"] if c in fcapa.columns]
            st.dataframe(
                fcapa[capa_disp].sort_values("D-day") if "D-day" in fcapa.columns else fcapa[capa_disp],
                use_container_width=True, hide_index=True, height=360,
                column_config={
                    "D-day": st.column_config.NumberColumn("D-day", format="%d일"),
                    "관리번호": st.column_config.NumberColumn("관리번호", format="%d"),
                },
            )

    with capa_ai:
        ck1, ck2, ck3 = st.columns(3)
        ck1.metric("CAPA AI", f"{cai_t}건", delta=f"{safe_pct(cai_d, cai_t):.0f}%")
        ck2.metric("모니터링 AI", f"{ai_t}건", delta=f"{safe_pct(ai_d, ai_t):.0f}%")
        ck3.metric("AI 합계", f"{cai_t + ai_t}건",
                    delta=f"{safe_pct(cai_d + ai_d, cai_t + ai_t):.0f}%")

        st.divider()
        st.subheader("모니터링AI 이행률 게이지")
        ai_rate = safe_pct(ai_d, ai_t)
        fig_g = go.Figure(go.Indicator(mode="gauge+number", value=ai_rate,
            number={"suffix": "%", "font": {"size": 36}},
            gauge={"axis": {"range": [0, 100]},
                    "bar": {"color": CHART_COLORS["blue"]},
                    "steps": [{"range": [0, 50], "color": "#ffebee"},
                              {"range": [50, 80], "color": "#fff3e0"},
                              {"range": [80, 100], "color": "#e8f5e9"}],
                    "threshold": {"line": {"color": CHART_COLORS["red"], "width": 3}, "value": 80}}))
        fig_g.update_layout(height=260, margin=dict(l=20, r=20, t=30, b=10))
        st.plotly_chart(fig_g, use_container_width=True)

    with capa_deadline:
        st.subheader("기한 초과·지연 현황")
        overdue_frames = []
        for _label, _df in [("CAPA", fcapa), ("CAPA AI", fcapaai), ("모니터링AI", fai)]:
            if _df.empty or "D-day" not in _df.columns:
                continue
            over = _df[_df["D-day"].fillna(999) < 0].copy()
            if not over.empty:
                over["구분"] = _label
                overdue_frames.append(over)
        if overdue_frames:
            all_over = pd.concat(overdue_frames, ignore_index=True)
            disp = [c for c in ["구분", "관리번호", "제목", "기한일", "D-day", "등록자", "진행상태"]
                    if c in all_over.columns]
            st.dataframe(all_over[disp].sort_values("D-day"),
                         use_container_width=True, hide_index=True, height=380,
                         column_config={"D-day": st.column_config.NumberColumn("D-day", format="%d일")})
        else:
            st.success("기한 초과 항목이 없습니다.")

    with capa_link:
        render_linkage_section("capa", key_suffix="capa",
                                title="CAPA 체인 연계 현황 (OOS/일탈 → 조사 → CAPA → AI)")

    with capa_tab_raw:
        render_raw_data_section(
            default_project_keys=["capa", "capaactionitem", "actionitem"],
            key_suffix="capa",
            allow_change=False,
            title="원본 데이터 (CAPA · CAPA AI · 모니터링AI)",
            extra_priority=["CAPA 구분", "부모 프로젝트", "부모 관리번호",
                             "자식 수(전체)", "자식 미종결 수"],
        )

    render_footer()


# ============================================================================
# 탭 4: 변경관리
# ============================================================================

with tab_change:
    render_header("변경관리 통합 현황")
    st.markdown("---")

    chg_kpi, chg_grade, chg_impact, chg_out, chg_ai, chg_link, chg_tab_raw = st.tabs(
        ["통합 KPI", "등급·구분", "영향성평가", "외주변경", "Action Item",
         "연계 현황", "원본 데이터"]
    )

    with chg_kpi:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("변경관리", f"{len(fchg)}건")
        k2.metric("변경AI", f"{len(fchgai)}건")
        k3.metric("변경영향성", f"{len(fchgimp)}건")
        k4.metric("외주변경", f"{len(fchgout)}건")

        st.divider()
        all_df = pd.concat([d for d in [fchg, fchgai, fchgimp, fchgout] if not d.empty],
                             ignore_index=True) if any(not d.empty for d in [fchg, fchgai, fchgimp, fchgout]) else pd.DataFrame()
        if not all_df.empty and "완료여부" in all_df.columns:
            done = int((all_df["완료여부"] == "C").sum())
            st.metric("변경 계열 전체 완료율",
                       f"{safe_pct(done, len(all_df)):.0f}%",
                       help=f"완료 {done} / 전체 {len(all_df)}건")

    with chg_grade:
        if fchg.empty:
            st.info("변경관리 데이터가 없습니다.")
        else:
            cc1, cc2 = st.columns(2)
            with cc1:
                st.subheader("변경 등급별 분포")
                if "변경 등급" in fchg.columns:
                    gd = fchg["변경 등급"].value_counts().reset_index()
                    gd.columns = ["등급", "건수"]
                    gd = gd[gd["등급"].notna() & (gd["등급"] != "")]
                    if not gd.empty:
                        fig_gr = px.pie(gd, values="건수", names="등급", hole=0.4,
                                         color_discrete_sequence=px.colors.qualitative.Set1)
                        fig_gr.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=10))
                        fig_gr.update_traces(textinfo="label+value+percent", textfont_size=11)
                        st.plotly_chart(fig_gr, use_container_width=True)
            with cc2:
                st.subheader("변경 구분 (영구/임시)")
                if "변경 구분" in fchg.columns:
                    dd = fchg["변경 구분"].value_counts().reset_index()
                    dd.columns = ["구분", "건수"]
                    dd = dd[dd["구분"].notna() & (dd["구분"] != "")]
                    if not dd.empty:
                        fig_d = px.bar(dd, x="구분", y="건수", text="건수",
                                        color_discrete_sequence=[CHART_COLORS.get("teal", "#17becf")])
                        fig_d.update_traces(textposition="outside")
                        fig_d.update_layout(height=320, plot_bgcolor="white")
                        st.plotly_chart(fig_d, use_container_width=True)

    with chg_impact:
        if fchgimp.empty:
            st.info("변경영향성평가 데이터가 없습니다.")
        elif "영향 GMP 영역" in fchgimp.columns:
            st.subheader("영향성평가 GMP 영역 분포")
            areas = fchgimp["영향 GMP 영역"].dropna().str.split(", ").explode()
            areas = areas[areas != "해당 없음"].value_counts().reset_index()
            areas.columns = ["영역", "건수"]
            if not areas.empty:
                fig_ar = px.bar(areas.sort_values("건수", ascending=True),
                                 x="건수", y="영역", orientation="h",
                                 color_discrete_sequence=[CHART_COLORS.get("teal", "#17becf")], text="건수")
                fig_ar.update_layout(height=max(260, 22 * len(areas)),
                                      margin=dict(l=0, r=30, t=10, b=10), plot_bgcolor="white")
                fig_ar.update_traces(textposition="outside")
                st.plotly_chart(fig_ar, use_container_width=True)
            else:
                st.info("영향 영역 데이터 없음")
        else:
            st.info("영향 GMP 영역 컬럼이 없습니다.")

    with chg_out:
        if fchgout.empty:
            st.info("외주변경 데이터가 없습니다.")
        else:
            st.subheader("외주변경 위탁처별 현황")
            if "위탁처" in fchgout.columns:
                cmo = (fchgout[fchgout["위탁처"].notna() & (fchgout["위탁처"] != "")]
                       ["위탁처"].value_counts().head(15).reset_index())
                cmo.columns = ["위탁처", "건수"]
                if not cmo.empty:
                    fig_cmo = px.bar(cmo.sort_values("건수", ascending=True),
                                       x="건수", y="위탁처", orientation="h",
                                       color_discrete_sequence=[CHART_COLORS.get("teal", "#17becf")], text="건수")
                    fig_cmo.update_layout(height=max(260, 22 * len(cmo)),
                                            margin=dict(l=0, r=30, t=10, b=10),
                                            plot_bgcolor="white")
                    fig_cmo.update_traces(textposition="outside")
                    st.plotly_chart(fig_cmo, use_container_width=True)
            else:
                st.info("위탁처 컬럼 없음")

    with chg_ai:
        if fchgai.empty:
            st.info("변경 Action Item 데이터가 없습니다.")
        else:
            st.subheader("변경 Action Item 이행 현황")
            ai_total = len(fchgai)
            ai_done = int((fchgai["완료여부"] == "C").sum()) if "완료여부" in fchgai.columns else 0
            k1, k2, k3 = st.columns(3)
            k1.metric("변경 AI 총 건수", f"{ai_total}건")
            k2.metric("완료", f"{ai_done}건")
            k3.metric("이행률", f"{safe_pct(ai_done, ai_total):.0f}%")
            if "진행상태" in fchgai.columns:
                st_df = fchgai["진행상태"].value_counts().reset_index()
                st_df.columns = ["상태", "건수"]
                fig_st = px.pie(st_df, values="건수", names="상태", hole=0.45,
                                 color_discrete_sequence=px.colors.qualitative.Pastel)
                fig_st.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=10))
                st.plotly_chart(fig_st, use_container_width=True)

    with chg_link:
        render_linkage_section("changemanagement", key_suffix="chg",
                                title="변경관리 체인 연계 현황")

    with chg_tab_raw:
        render_raw_data_section(
            default_project_keys=[
                "changemanagement", "changeactionitem",
                "changeimpactassessment", "changeoutsourcing",
            ],
            key_suffix="change",
            allow_change=False,
            title="원본 데이터 (변경관리 · 변경AI · 변경영향성 · 외주변경)",
            extra_priority=["변경 등급", "변경 구분", "위탁처",
                             "부모 관리번호", "자식 수(전체)", "자식 미종결 수"],
        )

    render_footer()


# ============================================================================
# 탭 5: 고객불만
# ============================================================================

with tab_complain:
    render_header("고객불만 현황")
    st.markdown("---")

    cmp_kpi, cmp_type, cmp_cause, cmp_perf, cmp_link, cmp_tab_raw = st.tabs(
        ["개요·KPI", "유형·처리결과", "원인·결론", "처리 성능", "연계 현황", "원본 데이터"]
    )

    ct = len(fcmp)
    cd = int((fcmp["완료여부"] == "C").sum()) if "완료여부" in fcmp.columns and ct else 0
    avg_d = None
    if not fcmp.empty and "접수일" in fcmp.columns and "처리완료일" in fcmp.columns:
        _r = pd.to_datetime(fcmp["접수일"], errors="coerce")
        _c = pd.to_datetime(fcmp["처리완료일"], errors="coerce")
        _delta = (_c - _r).dt.days.dropna()
        if not _delta.empty:
            avg_d = round(_delta.mean(), 1)

    with cmp_kpi:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("총 건수", f"{ct}건")
        k2.metric("완료", f"{cd}건")
        k3.metric("처리 중", f"{ct - cd}건")
        k4.metric("평균 처리일", f"{avg_d}일" if avg_d else "N/A")

        if not fcmp.empty:
            st.markdown("##### 월별 불만 접수")
            _mc = _month_col_for_df(fcmp)
            if _mc in fcmp.columns:
                cmf = _monthly_weighted_series(fcmp, _mc)
                fig_cm = go.Figure(go.Bar(
                    x=MONTH_LABELS, y=[round(v) for v in cmf["건수"].tolist()],
                    marker_color=CHART_COLORS.get("red", "#d62728"),
                    text=[round(v) for v in cmf["건수"].tolist()], textposition="outside",
                ))
                fig_cm.update_layout(height=300, margin=dict(l=30, r=10, t=10, b=30),
                                      plot_bgcolor="white")
                st.plotly_chart(fig_cm, use_container_width=True)

    with cmp_type:
        if fcmp.empty:
            st.info("고객불만 데이터가 없습니다.")
        else:
            cc1, cc2 = st.columns(2)
            with cc1:
                st.subheader("불만 유형별 분류")
                tc = ("불만 구분" if "불만 구분" in fcmp.columns
                       else "불만 유형" if "불만 유형" in fcmp.columns else None)
                if tc:
                    td = fcmp[tc].value_counts().reset_index()
                    td.columns = ["유형", "건수"]
                    td = td[td["유형"].notna() & (td["유형"] != "")]
                    if not td.empty:
                        fig_tc = px.pie(td, values="건수", names="유형", hole=0.35,
                                         color_discrete_sequence=px.colors.qualitative.Pastel)
                        fig_tc.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=10))
                        fig_tc.update_traces(textinfo="label+value+percent", textfont_size=11)
                        st.plotly_chart(fig_tc, use_container_width=True)
                else:
                    st.info("불만 유형 컬럼 없음")
            with cc2:
                st.subheader("처리 결과 분포")
                rc = ("처리 결과" if "처리 결과" in fcmp.columns
                       else "처리결과" if "처리결과" in fcmp.columns else None)
                if rc:
                    rd = fcmp[rc].value_counts().reset_index()
                    rd.columns = ["결과", "건수"]
                    rd = rd[rd["결과"].notna() & (rd["결과"] != "")]
                    if not rd.empty:
                        fig_r = px.bar(rd, x="결과", y="건수", text="건수",
                                        color_discrete_sequence=[CHART_COLORS.get("blue", "#1f77b4")])
                        fig_r.update_traces(textposition="outside")
                        fig_r.update_layout(height=320, plot_bgcolor="white")
                        st.plotly_chart(fig_r, use_container_width=True)
                else:
                    st.info("처리 결과 컬럼 없음")

    with cmp_cause:
        if fcmp.empty:
            st.info("고객불만 데이터가 없습니다.")
        else:
            for label, cols in [
                ("원인 분류", ["원인 분류", "불만 원인", "원인"]),
                ("결론", ["결론", "최종 결론"]),
            ]:
                picked = next((c for c in cols if c in fcmp.columns), None)
                if not picked:
                    continue
                vc = fcmp[picked].value_counts().reset_index()
                vc.columns = [label, "건수"]
                vc = vc[vc[label].notna() & (vc[label] != "")]
                if vc.empty:
                    continue
                st.markdown(f"##### {label} 분포")
                fig_v = px.bar(vc.sort_values("건수", ascending=True),
                                x="건수", y=label, orientation="h", text="건수",
                                color_discrete_sequence=[CHART_COLORS.get("purple", "#9467bd")])
                fig_v.update_traces(textposition="outside")
                fig_v.update_layout(height=max(260, 22 * len(vc)),
                                      margin=dict(l=10, r=30, t=10, b=10),
                                      plot_bgcolor="white")
                st.plotly_chart(fig_v, use_container_width=True)

    with cmp_perf:
        if fcmp.empty:
            st.info("고객불만 데이터가 없습니다.")
        elif "접수일" in fcmp.columns and "처리완료일" in fcmp.columns:
            _r2 = pd.to_datetime(fcmp["접수일"], errors="coerce")
            _c2 = pd.to_datetime(fcmp["처리완료일"], errors="coerce")
            _perf_df = fcmp.copy()
            _perf_df["_처리일"] = (_c2 - _r2).dt.days
            delta = _perf_df["_처리일"].dropna()
            if delta.empty:
                st.info("처리일 계산 가능 데이터가 없습니다.")
            else:
                # ── KPI 요약 ─────────────────────────────────────
                LEGAL_LIMIT = 30  # 법정 기준일 (예: 30일)
                over_legal = (delta > LEGAL_LIMIT).sum()
                over_pct   = over_legal / len(delta) * 100

                pm1, pm2, pm3, pm4 = st.columns(4)
                pm1.metric("평균 처리일", f"{delta.mean():.1f}일",
                           delta=f"기준 {LEGAL_LIMIT}일 대비 {delta.mean()-LEGAL_LIMIT:+.1f}일",
                           delta_color="inverse")
                pm2.metric("중앙값", f"{delta.median():.1f}일")
                pm3.metric("최대 처리일", f"{delta.max():.0f}일")
                pm4.metric("법정기준 초과", f"{over_legal}건",
                           delta=f"전체의 {over_pct:.1f}%",
                           delta_color="inverse")

                st.divider()

                # ── 처리일 분포 히스토그램 + 법정 기준선 ─────────
                S.section_header("처리일 분포 (법정 기준 30일 기준선)", "📊")
                fig_h = px.histogram(
                    delta, nbins=25,
                    color_discrete_sequence=[CHART_COLORS.get("orange", "#ff7f0e")],
                    labels={"value": "처리일(일)", "count": "건수"},
                )
                fig_h.add_vline(
                    x=LEGAL_LIMIT, line_color="#e74c3c", line_dash="dash", line_width=2,
                    annotation_text=f"법정 기준 {LEGAL_LIMIT}일",
                    annotation_position="top right",
                    annotation_font_color="#e74c3c",
                )
                # 초과 구간 음영
                fig_h.add_vrect(
                    x0=LEGAL_LIMIT, x1=max(delta.max(), LEGAL_LIMIT + 5),
                    fillcolor="rgba(231,76,60,0.08)", line_width=0,
                    annotation_text=f"초과 {over_pct:.0f}%",
                    annotation_position="top right",
                    annotation_font_color="#e74c3c",
                )
                fig_h.update_layout(
                    height=320, plot_bgcolor="white",
                    xaxis_title="처리일(일)", yaxis_title="건수",
                    showlegend=False, margin=dict(l=10, r=10, t=30, b=10),
                )
                st.plotly_chart(fig_h, use_container_width=True)

                st.divider()

                # ── 제품군별 평균 처리일 비교 바차트 ─────────────
                _prod_col = next((c for c in ["제품", "품목", "제품명", "품목명", "제품군"]
                                  if c in fcmp.columns), None)
                if _prod_col:
                    S.section_header(f"{_prod_col}별 평균 처리일 비교", "🏭")
                    _prod_perf = (
                        _perf_df.dropna(subset=["_처리일"])
                        .groupby(_prod_col)["_처리일"]
                        .agg(평균처리일="mean", 건수="count")
                        .reset_index()
                        .query("건수 >= 2")
                        .sort_values("평균처리일", ascending=False)
                        .head(20)
                    )
                    if not _prod_perf.empty:
                        _prod_perf["색상"] = _prod_perf["평균처리일"].apply(
                            lambda d: "#e74c3c" if d > LEGAL_LIMIT else "#27ae60"
                        )
                        fig_prod = go.Figure(go.Bar(
                            x=_prod_perf["평균처리일"].round(1),
                            y=_prod_perf[_prod_col],
                            orientation="h",
                            text=_prod_perf["평균처리일"].round(1).astype(str) + "일",
                            textposition="outside",
                            marker_color=_prod_perf["색상"],
                            customdata=_prod_perf["건수"],
                            hovertemplate="%{y}<br>평균: %{x:.1f}일<br>건수: %{customdata}건<extra></extra>",
                        ))
                        fig_prod.add_vline(
                            x=LEGAL_LIMIT, line_color="#e74c3c",
                            line_dash="dash", line_width=1.5,
                        )
                        fig_prod.update_layout(
                            height=max(300, 28 * len(_prod_perf)),
                            plot_bgcolor="white",
                            xaxis_title="평균 처리일(일)",
                            margin=dict(l=10, r=60, t=20, b=10),
                        )
                        st.plotly_chart(fig_prod, use_container_width=True)
                        st.caption("🔴 빨간색: 법정 기준(30일) 초과 제품군 | 🟢 녹색: 기준 이내")
                    else:
                        st.info("건수 2건 이상인 제품군 데이터가 없습니다.")
                else:
                    st.info("제품/품목 컬럼을 찾을 수 없습니다.")
        else:
            st.info("접수일·처리완료일 컬럼이 필요합니다.")

    with cmp_link:
        render_linkage_section("complain", key_suffix="cmp",
                                title="고객불만 체인 연계 현황")

    with cmp_tab_raw:
        render_raw_data_section(
            default_project_keys=["complain"],
            key_suffix="complain",
            allow_change=False,
            title="원본 데이터 (고객불만)",
            extra_priority=["불만 유형", "불만 구분", "처리 결과", "원인 분류",
                             "접수일", "처리완료일", "자식 수(전체)", "자식 미종결 수"],
        )

    render_footer()


# ============================================================================
# 탭 6: 워크플로우 연계
# ============================================================================

with tab_workflow:
    render_header("워크플로우 연계 분석")
    st.markdown("---")
    st.markdown("OOS/일탈 발생 시 후속 워크플로우(조사, CAPA, Action Item)로 어떻게 연결되는지 분석합니다.")
    st.markdown("")

    # parentPrno 기반 연계 분석
    link_data = []
    for pk, df_p in [("investigation", finv), ("capa", fcapa), ("capaactionitem", fcapaai)]:
        if df_p.empty or "상위번호" not in df_p.columns:
            continue
        parents = df_p["상위번호"].dropna()
        parents = parents[parents != 0]
        link_data.append({"프로젝트": PROJECT_META[pk]["label"], "연계 건수": len(parents),
                          "고유 상위번호": parents.nunique()})

    if link_data:
        st.subheader("후속 워크플로우 연계 현황")
        ldf = pd.DataFrame(link_data)
        st.dataframe(ldf, use_container_width=True, hide_index=True)

        # Sankey-like 시각화
        st.subheader("품질이슈 → 후속조치 흐름")
        oos_total = foos["관리번호"].nunique() if not foos.empty and "관리번호" in foos.columns else 0
        dev_total = fdev["관리번호"].nunique() if not fdev.empty and "관리번호" in fdev.columns else 0
        inv_cnt = finv["관리번호"].nunique() if not finv.empty and "관리번호" in finv.columns else 0
        capa_cnt = fcapa["관리번호"].nunique() if not fcapa.empty and "관리번호" in fcapa.columns else 0
        cai_cnt = fcapaai["관리번호"].nunique() if not fcapaai.empty and "관리번호" in fcapaai.columns else 0

        fig_sk = go.Figure(go.Sankey(
            node=dict(pad=15, thickness=20,
                      label=[f"OOS ({oos_total})", f"일탈 ({dev_total})", f"조사 ({inv_cnt})", f"CAPA ({capa_cnt})", f"CAPA AI ({cai_cnt})"],
                      color=["#e53935", "#fb8c00", "#795548", "#8e24aa", "#ab47bc"]),
            link=dict(
                source=[0, 1, 0, 1, 3],
                target=[2, 2, 3, 3, 4],
                value=[max(inv_cnt // 2, 1), max(inv_cnt // 2, 1), max(capa_cnt // 2, 1), max(capa_cnt // 2, 1), max(cai_cnt, 1)],
                color=["rgba(229,57,53,0.3)", "rgba(251,140,0,0.3)", "rgba(229,57,53,0.2)", "rgba(251,140,0,0.2)", "rgba(142,36,170,0.3)"],
            ),
        ))
        fig_sk.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), font=dict(size=12))
        st.plotly_chart(fig_sk, use_container_width=True)
    else:
        st.info("연계 분석에 필요한 데이터가 없습니다.")

    st.divider()

    # ── OOS 발생 건수 vs CAPA 완료율 월별 상관 히트맵 ──────────
    S.section_header("OOS 발생 vs CAPA 완료율 — 월별 상관 분석", "🔗")
    _corr_ok = False
    try:
        _mc_oos  = _month_col_for_df(foos)
        _mc_capa = _month_col_for_df(fcapa)
        if (not foos.empty and _mc_oos in foos.columns and
                not fcapa.empty and _mc_capa in fcapa.columns):
            # OOS 월별 건수
            _oos_m = (foos.groupby(_mc_oos)["관리번호"].count()
                      if "관리번호" in foos.columns
                      else foos.groupby(_mc_oos).size())
            _oos_m.name = "OOS 건수"

            # CAPA 월별 완료율 (완료여부=="C" / 전체)
            if "완료여부" in fcapa.columns:
                _capa_total = fcapa.groupby(_mc_capa).size()
                _capa_done  = fcapa[fcapa["완료여부"] == "C"].groupby(_mc_capa).size()
                _capa_m     = (_capa_done / _capa_total * 100).round(1)
            else:
                _capa_m = fcapa.groupby(_mc_capa).size().rename("CAPA 건수")
            _capa_m.name = "CAPA 완료율(%)"

            # 월 인덱스 정수화 후 merge
            def _to_int_index(s):
                s = s.copy()
                s.index = pd.to_numeric(s.index, errors="coerce")
                return s.dropna()

            _oos_m  = _to_int_index(_oos_m)
            _capa_m = _to_int_index(_capa_m)
            _corr_df = pd.DataFrame({"OOS 건수": _oos_m, "CAPA 완료율(%)": _capa_m}).dropna()

            if len(_corr_df) >= 3:
                _corr_val = _corr_df.corr().iloc[0, 1]
                _corr_dir = "양의" if _corr_val > 0 else "음의"
                _corr_str = abs(_corr_val)

                c_col1, c_col2 = st.columns([2, 1])
                with c_col1:
                    # 산점도 + 추세선
                    fig_corr = px.scatter(
                        _corr_df, x="OOS 건수", y="CAPA 완료율(%)",
                        trendline="ols",
                        labels={"OOS 건수": "OOS 발생 건수", "CAPA 완료율(%)": "CAPA 완료율 (%)"},
                        color_discrete_sequence=[CHART_COLORS.get("blue", "#4a5899")],
                    )
                    fig_corr.update_traces(marker=dict(size=9, opacity=0.75))
                    fig_corr.update_layout(
                        height=320, plot_bgcolor="white",
                        margin=dict(l=10, r=10, t=30, b=10),
                        title=dict(text="OOS 건수 vs CAPA 완료율 (월별)", font=dict(size=13)),
                    )
                    st.plotly_chart(fig_corr, use_container_width=True)

                with c_col2:
                    st.markdown("##### 상관계수")
                    _level = "강함" if _corr_str >= 0.7 else ("보통" if _corr_str >= 0.4 else "약함")
                    _color = "#e74c3c" if _corr_str >= 0.7 else ("#f39c12" if _corr_str >= 0.4 else "#27ae60")
                    st.markdown(f"""
<div style="background:#f8f9fa;border-radius:10px;padding:20px;text-align:center;margin-top:20px">
    <div style="font-size:2.2rem;font-weight:700;color:{_color}">{_corr_val:+.3f}</div>
    <div style="font-size:0.85rem;color:#666;margin-top:6px">{_corr_dir} 상관 · {_level}</div>
    <div style="font-size:0.75rem;color:#999;margin-top:10px">OOS 증가 시<br>CAPA 완료율이<br>{'함께 증가' if _corr_val > 0 else '감소'}하는 경향</div>
</div>
                    """, unsafe_allow_html=True)
                    st.markdown("")
                    st.dataframe(
                        _corr_df.rename(columns={"OOS 건수":"OOS","CAPA 완료율(%)":"CAPA완료율"})
                        .tail(12).sort_index(ascending=False),
                        use_container_width=True, height=200,
                    )
                _corr_ok = True
    except Exception as _ce:
        pass

    if not _corr_ok:
        st.info("OOS 또는 CAPA 데이터가 부족하여 상관 분석을 표시할 수 없습니다.")

    render_footer()


# ============================================================================
# 탭 7: 기한관리
# ============================================================================

with tab_deadline:
    render_header("기한 & 일정 관리")
    st.markdown("---")

    # 전 프로젝트 D-day 분포
    st.subheader("전 프로젝트 기한 현황")
    dd_frames = []
    for pk, df_p in F.items():
        if df_p.empty or "D-day" not in df_p.columns:
            continue
        tmp = df_p[df_p["D-day"].notna()].copy()
        if not tmp.empty:
            tmp["프로젝트"] = PROJECT_META[pk]["label"]
            dd_frames.append(tmp[["프로젝트", "관리번호", "제목", "기한일", "D-day", "진행상태"]])

    if dd_frames:
        dd_all = pd.concat(dd_frames, ignore_index=True)
        # 구간별 집계
        dd_all["구간"] = pd.cut(dd_all["D-day"], bins=[-9999, -30, -7, 0, 7, 30, 9999],
                                 labels=["30일+ 초과", "7~30일 초과", "0~7일 초과", "7일 이내", "7~30일", "30일+"])
        zone_cnt = dd_all.groupby("구간", observed=True).size().reset_index(name="건수")

        z1, z2, z3, z4 = st.columns(4)
        over30 = int((dd_all["D-day"] < -30).sum())
        over7 = int(((dd_all["D-day"] >= -30) & (dd_all["D-day"] < 0)).sum())
        within7 = int(((dd_all["D-day"] >= 0) & (dd_all["D-day"] <= 7)).sum())
        safe_zone = int((dd_all["D-day"] > 7).sum())
        z1.metric("30일+ 초과", f"{over30}건", delta="위험" if over30 > 0 else "양호", delta_color="inverse")
        z2.metric("7일 이내 초과", f"{over7}건", delta="주의" if over7 > 0 else "양호", delta_color="inverse")
        z3.metric("7일 이내 도래", f"{within7}건")
        z4.metric("여유 (7일+)", f"{safe_zone}건")

        st.divider()

        # 기한연장 분석
        if not fext.empty:
            st.subheader("기한연장 현황")
            ext_total = len(fext)
            ext_done = int((fext["완료여부"] == "C").sum()) if "완료여부" in fext.columns else 0
            e1, e2, e3 = st.columns(3)
            e1.metric("기한연장 신청", f"{ext_total}건")
            e2.metric("승인 완료", f"{ext_done}건")
            e3.metric("승인율", f"{safe_pct(ext_done, ext_total):.0f}%")

        st.divider()

        # 간트 차트: 기한 초과 & 임박 항목 시각화
        S.section_header("기한 현황 간트 차트", "📅")
        _gantt_df = dd_all.copy()
        if not _gantt_df.empty and "기한일" in _gantt_df.columns:
            try:
                _gantt_df["기한일_dt"] = pd.to_datetime(_gantt_df["기한일"], errors="coerce")
                _gantt_df = _gantt_df.dropna(subset=["기한일_dt"])
                _gantt_df["D-day_num"] = pd.to_numeric(_gantt_df["D-day"], errors="coerce").fillna(0)
                # 시작일: 기한일로부터 최대 30일 전 (가독성 유지)
                _gantt_df["시작일"] = _gantt_df["기한일_dt"] - pd.to_timedelta(
                    _gantt_df["D-day_num"].abs().clip(upper=30).astype(int), unit="D"
                )
                _gantt_df["색상"] = _gantt_df["D-day_num"].apply(
                    lambda d: "기한 초과" if d < 0 else ("임박 (≤7일)" if d <= 7 else "주의 (≤30일)")
                )
                _gantt_df["라벨"] = _gantt_df.apply(
                    lambda r: f"[{r['프로젝트']}] {str(r.get('제목',''))[:20]}", axis=1
                )
                # 초과 우선 정렬 후 상위 40개
                _gantt_plot = _gantt_df.sort_values("D-day_num").head(40)
                color_map = {"기한 초과": "#e74c3c", "임박 (≤7일)": "#f39c12", "주의 (≤30일)": "#3f51b5"}
                fig_gantt = px.timeline(
                    _gantt_plot,
                    x_start="시작일", x_end="기한일_dt",
                    y="라벨", color="색상",
                    color_discrete_map=color_map,
                    labels={"색상": "상태"},
                )
                fig_gantt.update_yaxes(autorange="reversed")
                _today_ts = int(pd.Timestamp.today().timestamp() * 1000)
                fig_gantt.add_shape(
                    type="line", xref="x", yref="paper",
                    x0=_today_ts, x1=_today_ts, y0=0, y1=1,
                    line=dict(color="#333", dash="dash", width=1.5),
                )
                fig_gantt.add_annotation(
                    x=_today_ts, y=1.02, xref="x", yref="paper",
                    text="오늘", showarrow=False, font=dict(size=10),
                )
                fig_gantt.update_layout(
                    height=max(300, 28 * min(40, len(_gantt_plot))),
                    margin=dict(l=10, r=20, t=30, b=10),
                    plot_bgcolor="white", showlegend=True,
                    legend=dict(orientation="h", y=1.05),
                )
                st.plotly_chart(fig_gantt, use_container_width=True)
            except Exception as _ge:
                st.warning(f"간트 차트 렌더링 오류: {_ge}")

        st.divider()
        S.section_header("기한 임박 상세 목록 (D-day ≤ 7일)", "⚠️")
        urgent = dd_all[dd_all["D-day"] <= 7].sort_values("D-day")
        if not urgent.empty:
            st.dataframe(urgent.head(30), use_container_width=True, hide_index=True, height=400,
                         column_config={"D-day": st.column_config.NumberColumn("D-day", format="%d일"),
                                        "관리번호": st.column_config.NumberColumn("관리번호", format="%d")})
        else:
            st.success("임박한 기한 항목이 없습니다.")
    else:
        S.empty_state("기한 데이터가 없습니다.", "📭")

    render_footer()


# ============================================================================
# 탭 9: 설정
# ============================================================================

with tab_settings:
    render_header("시스템 설정 & 관리")
    st.markdown("---")

    cfg_tab1, cfg_tab2, cfg_tab3, cfg_tab4 = st.tabs(
        ["📊 데이터 현황", "🗄️ 캐시 관리", "🔔 알림 설정", "⚙️ 시스템 정보"]
    )

    with cfg_tab1:
        S.section_header("프로젝트별 수집 현황")
        status_data = []
        for pk in PROJECT_META:
            df_p = ALL_DFS.get(pk, pd.DataFrame())
            n = df_p["관리번호"].nunique() if not df_p.empty and "관리번호" in df_p.columns else len(df_p)
            overdue_n = int(df_p["D-day"].lt(0).sum()) if not df_p.empty and "D-day" in df_p.columns else 0
            status_data.append({
                "프로젝트": PROJECT_META[pk]["label"],
                "그룹": PROJECT_META[pk]["group"],
                "수집 건수": n,
                "기한 초과": overdue_n,
                "상세수집": "✅" if PROJECT_META[pk]["detail"] else "목록만",
                "상태": "🟢 정상" if n > 0 else "⚪ 빈 데이터",
            })
        st.dataframe(
            pd.DataFrame(status_data),
            use_container_width=True, hide_index=True,
            column_config={
                "기한 초과": st.column_config.NumberColumn("기한 초과", format="%d건"),
                "수집 건수": st.column_config.NumberColumn("수집 건수", format="%d건"),
            },
        )
        st.caption(f"마지막 수집: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 수집 소요: {_fetch_elapsed:.1f}s")

    with cfg_tab2:
        S.section_header("캐시 관리")
        st.info(f"캐시 TTL: 30분 | 마지막 갱신: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        c_btn1, c_btn2 = st.columns(2)
        with c_btn1:
            if st.button("🗑️ 전체 캐시 초기화", use_container_width=True):
                st.cache_data.clear()
                st.session_state.pop("_cache_fetch_time", None)
                st.success("캐시 초기화 완료. 페이지를 새로고침하세요.")
        with c_btn2:
            if st.button("↻ 데이터 즉시 갱신", use_container_width=True, type="primary"):
                st.cache_data.clear()
                st.session_state.pop("_cache_fetch_time", None)
                st.rerun()
        st.divider()
        S.section_header("스텝별 수집 소요 시간")
        _st_df = pd.DataFrame(_step_times, columns=["프로젝트", "소요(s)"])
        _st_df = _st_df.sort_values("소요(s)", ascending=False)
        _st_df["소요(s)"] = _st_df["소요(s)"].round(3)
        st.dataframe(_st_df, use_container_width=True, hide_index=True)

    with cfg_tab3:
        S.section_header("알림 설정 (Slack & 이메일)")
        st.caption("아래 설정은 세션 중에만 유지됩니다. 영구 저장은 `.env` 파일을 수정하세요.")
        al1, al2 = st.columns(2)
        with al1:
            st.markdown("**Slack Webhook**")
            slack_url = st.text_input(
                "Webhook URL", value=os.environ.get("QMS_SLACK_WEBHOOK", ""),
                type="password", key="cfg_slack_url",
                placeholder="https://hooks.slack.com/services/..."
            )
            if slack_url:
                os.environ["QMS_SLACK_WEBHOOK"] = slack_url
            if st.button("📨 Slack 테스트 발송", key="cfg_slack_test"):
                try:
                    from qms_pro.services import alert_service as _al
                    _al.send_slack(slack_url, "✅ QMS 알림 테스트 메시지입니다.")
                    st.success("Slack 전송 성공")
                except Exception as e:
                    st.error(f"전송 실패: {e}")
        with al2:
            st.markdown("**이메일 (SMTP)**")
            smtp_host = st.text_input("SMTP 서버", value=os.environ.get("QMS_SMTP_HOST", "smtp.gmail.com"), key="cfg_smtp_host")
            smtp_port = st.number_input("포트", value=int(os.environ.get("QMS_SMTP_PORT", 587)), key="cfg_smtp_port")
            smtp_user = st.text_input("발신 계정", value=os.environ.get("QMS_SMTP_USER", ""), key="cfg_smtp_user")
            smtp_pass = st.text_input("비밀번호", value="", type="password", key="cfg_smtp_pass")
            smtp_to   = st.text_input("수신자 (콤마 구분)", value=os.environ.get("QMS_ALERT_TO", ""), key="cfg_smtp_to")
            if smtp_host: os.environ["QMS_SMTP_HOST"] = smtp_host
            if smtp_user: os.environ["QMS_SMTP_USER"] = smtp_user
            if smtp_pass: os.environ["QMS_SMTP_PASS"] = smtp_pass
            if smtp_to:   os.environ["QMS_ALERT_TO"]  = smtp_to
            os.environ["QMS_SMTP_PORT"] = str(int(smtp_port))
            if st.button("📧 이메일 테스트 발송", key="cfg_email_test"):
                try:
                    from qms_pro.services import alert_service as _al
                    _al.send_email(
                        subject="[QMS] 알림 테스트",
                        body="QMS 대시보드 이메일 알림 테스트 메시지입니다.",
                        to_addrs=[a.strip() for a in smtp_to.split(",") if a.strip()],
                    )
                    st.success("이메일 전송 성공")
                except Exception as e:
                    st.error(f"전송 실패: {e}")
        st.divider()
        st.markdown("**기한 초과 알림 즉시 실행**")
        if st.button("🚨 지금 기한 초과 알림 발송", use_container_width=True):
            try:
                from qms_pro.services import alert_service as _al
                _al.run_overdue_alert(F, PROJECT_META)
                st.success("알림 발송 완료")
            except Exception as e:
                st.error(f"알림 발송 실패: {e}")

    with cfg_tab4:
        S.section_header("시스템 정보")
        st.code(f"""
QMS PRO Base URL : {API_BASE_URL}
대상 프로젝트    : {len(PROJECT_META)}개 (교육 제외)
Python           : {sys.version.split()[0]}
Streamlit        : {st.__version__}
마지막 수집      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
수집 소요        : {_fetch_elapsed:.2f}s
        """)
        S.section_header("API 연결 상태")
        _conn_ok = not all(d.empty for d in ALL_DFS.values())
        if _conn_ok:
            st.success("🟢 QMS API 연결 정상 — 데이터 수집 완료")
        else:
            st.error("🔴 QMS API 연결 실패 — 모든 프로젝트 데이터가 비어 있습니다")
        st.divider()
        S.section_header("PDF 보고서 내보내기")
        if st.button("📄 PDF 보고서 생성", use_container_width=True):
            try:
                from qms_pro.integrations import pdf_report as _pdf
                _kpi_data = {
                    "CAPA 이행률": safe_pct(
                        weighted_metric_completed(F.get("capa", pd.DataFrame())),
                        weighted_metric_total(F.get("capa", pd.DataFrame()))
                    ),
                    "변경 완료율": safe_pct(
                        weighted_metric_completed(F.get("changemanagement", pd.DataFrame())),
                        weighted_metric_total(F.get("changemanagement", pd.DataFrame()))
                    ),
                }
                _overdue = []
                for pk, df_p in F.items():
                    if not df_p.empty and "D-day" in df_p.columns:
                        for _, row in df_p[df_p["D-day"].lt(0)].iterrows():
                            _overdue.append({
                                "프로젝트": PROJECT_META[pk]["label"],
                                "관리번호": row.get("관리번호", "-"),
                                "제목": row.get("제목", "-"),
                                "기한일": row.get("기한일", "-"),
                                "D-day": int(row["D-day"]),
                            })
                _proj_sum = []
                for pk in PROJECT_META:
                    df_p = ALL_DFS.get(pk, pd.DataFrame())
                    n = df_p["관리번호"].nunique() if not df_p.empty and "관리번호" in df_p.columns else len(df_p)
                    ov = int(df_p["D-day"].lt(0).sum()) if not df_p.empty and "D-day" in df_p.columns else 0
                    _proj_sum.append({"프로젝트": PROJECT_META[pk]["label"],
                                       "그룹": PROJECT_META[pk]["group"],
                                       "수집 건수": n, "기한 초과": ov})
                _pdf_bytes = _pdf.generate_report_streamlit(_kpi_data, _overdue, _proj_sum)
                if _pdf_bytes:
                    st.download_button(
                        "⬇️ PDF 다운로드",
                        data=_pdf_bytes,
                        file_name=f"QMS_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )
                else:
                    st.warning("PDF 생성 실패: `pip install fpdf2` 를 실행한 후 다시 시도하세요.")
            except Exception as _e:
                st.error(f"PDF 오류: {_e}")

    render_footer()
