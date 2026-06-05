# -*- coding: utf-8 -*-
"""
KD-MoaQ — QMS 통합 모니터링 대시보드 (Streamlit + Plotly)
- 16개 프로젝트 통합 관리 (교육 제외)
- 탭1: 경영진 대시보드  탭2: 품질이상  탭3: CAPA관리  탭4: 변경관리
- 탭5: 고객불만  탭6: 워크플로우연계  탭7: 기한관리  탭8: 원본데이터  탭9: 설정

실행(메인 PC, LAN 공개): 이 폴더에서 `run_dashboard_LAN.bat` 또는
  streamlit run QMS_Integrated_Dashboard_v2.py
  → .streamlit/config.toml 에서 address=0.0.0.0 로 같은 네트워크 PC가 브라우저로 접속 가능.
"""
import sys, os, io, json, re, time, asyncio, logging, subprocess, base64
from collections import defaultdict
from datetime import datetime, date, timedelta

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
from streamlit_option_menu import option_menu  # 좌측 워크스페이스 레일(Task 2.1)
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from qms_pro.pages import oos_panels
from qms_pro.ui import theme as S
from qms_pro.ui import components as C  # 표준 컴포넌트 라이브러리(Task 3.1)
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
from qms_pro.domain.attribution import (  # 품목/lot 체인 귀속 파생(Task 3.2b, 배선 3.3a)
    attribute_dataframes as _attribute_dataframes,
    DERIVED_COLS as _ATTR_DERIVED_COLS,
)
from qms_pro.domain.disposition import (  # lot 처분(PASS/HOLD) 판정(Task 3.3b)
    judge_lot_dispositions as _judge_lot_dispositions,
    disposition_distribution as _disposition_distribution,
    LOT_COL as _DISP_LOT_COL,
    DISP_ORDER as _DISP_ORDER,
)

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

_BRAND_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CI", "logo1.png")
st.set_page_config(
    page_title="KD-MoaQ",
    page_icon=_BRAND_LOGO_PATH if os.path.exists(_BRAND_LOGO_PATH) else "▦",
    layout="wide",
    initial_sidebar_state="expanded",
)

_install_ws_logging_filter()
_install_ws_noise_filter()

# [Task 1.6 D2] 다크모드 제거(라이트 우선) — 세션 초기화 불필요.

# 디자인 시스템 CSS 주입 (사이드바 토글은 사이드바 마운트 후 주입)
S.apply_global_css()


# ============================================================================
# 상수 / 색상 / 프로젝트 메타
# ============================================================================

# [Task 1.6 commit2] CHART_COLORS 단일화 — 12종 혼용 폐지.
# 시리즈색은 토큰 시퀀스(네이비→블루→틸), 상태색은 의미색(고정)으로 매핑.
# 키 이름은 하위호환을 위해 유지하되 값은 모두 qms_styles 토큰을 가리킨다.
#   · 시리즈/구조 : primary/blue/light_blue/bar/purple → 네이비·블루 시퀀스
#   · 연계/개선   : teal → SEM_LINK
#   · 상태(고정)  : red=위험 / orange=주의 / green=정상 / gray·dark_gray=중립
CHART_COLORS = {
    "primary":    S.NAVY_800,
    "blue":       S.ACCENT_BLUE,
    "light_blue": S.CHART_SEQUENCE[2],
    "bar":        S.CHART_SEQUENCE[3],
    "purple":     S.NAVY_600,    # 기존 보라(12번째 혼용색) → 네이비 시퀀스로 흡수
    "teal":       S.SEM_LINK,
    "red":        S.SEM_DANGER,
    "orange":     S.SEM_WARN,
    "green":      S.SEM_OK,
    "gray":       S.SEM_NEUTRAL,
    "dark_gray":  S.NAVY_400,
    "brown":      S.NAVY_700,    # 미사용 잔여 키 — 시퀀스로 흡수
}
# 카테고리 다수 차트용 연속 시퀀스(색 부족 방지): 부족 시 cycle.
CHART_SEQUENCE = S.CHART_SEQUENCE

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
    active_mask,           # '기한 초과/위험' 통일: 살아있는(미완료·미취소) 행 마스크
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


# [Task 1.5 D1] render_header/render_footer 중복 래퍼 제거 — qms_styles 단일 소스를
# S.render_header / S.render_footer 로 직접 호출(아래 모든 호출부를 S. 접두로 통일).
# [Task 1.6 commit5] kpi_gauge(반원 게이지) 제거 → 경영진 화면은 C.kpi_stat_card(진척 바) 사용.


def render_analyst_error_reduction_kpi(
    foos: pd.DataFrame,
    df_oos_full: pd.DataFrame,
    primary_year: int,
    prev_year: int,
    *,
    year_col: str = "연도",
) -> None:
    """QMS_Dashboard.py 마감회의 탭과 동일: 전년 vs 필터 반영 당년 Analyst error 건수기여도 합 및 감소율."""
    S.section_header("Analyst error 감소율")
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
            plot_bgcolor=S.CHART_SURFACE,
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
#
# [Task 1.2] 앱은 라이브 fetch 를 직접 호출하지 않는다. 수집은 refresh_job 만 담당.
# 아래 모든 로더는 _da_load(=cache_only) 로 "마지막 정상 캐시" 만 읽는다(운영 디커플링).
# 캐시가 없으면 (빈 DF, "no_cache") 를 반환하고 화면은 빈 상태로 graceful 렌더.
def _da_load(project: str):
    return DA.load_project(project, cache_only=True)


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_list_project(project: str):
    return _da_load(project)


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_oos_data():
    return _da_load("oos")


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_deviation_data():
    return _da_load("deviation")


def fetch_devout_data_stub():
    return _da_load("deviationoutsourcing")


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_capa_data():
    return _da_load("capa")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_change_data():
    return _da_load("changemanagement")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_complain_data():
    return _da_load("complain")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_capaai_data():
    return _da_load("capaactionitem")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_changeai_data():
    return _da_load("changeactionitem")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_changeimpact_data():
    return _da_load("changeimpactassessment")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_changeout_data():
    return _da_load("changeoutsourcing")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_devoutai_data():
    return _da_load("deviationactionitem")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_transfer_data():
    return _da_load("businesstransfer")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_validity_data():
    return _da_load("validityevaluation")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_investigation_data():
    return _da_load("investigation")


# ============================================================================
# 사이드바 — 데이터 로드 & 필터
# ============================================================================

# 사이드바 브랜드: 회사 로고(CI/logo1.png) + KD-MoaQ. (▦ 이모지 → 광동제약 로고 교체)
try:
    with open(_BRAND_LOGO_PATH, "rb") as _lf:
        _logo_raw = _lf.read()
    # 투명 여백 크롭(콘텐츠 141x34 / 캔버스 150x100 — 위아래 여백 제거)으로 floaty 공백 제거 +
    # 글씨(KD-MoaQ)와 크기 정합. PIL 없으면 원본 그대로(graceful).
    try:
        from PIL import Image as _PILImage
        _im = _PILImage.open(io.BytesIO(_logo_raw)).convert("RGBA")
        _bb = _im.getbbox()
        if _bb:
            _im = _im.crop(_bb)
        _bufp = io.BytesIO()
        _im.save(_bufp, format="PNG")
        _brand_b64 = base64.b64encode(_bufp.getvalue()).decode("ascii")
    except Exception:
        _brand_b64 = base64.b64encode(_logo_raw).decode("ascii")
    st.sidebar.markdown(
        f'<div style="display:flex;flex-direction:column;align-items:center;text-align:center;gap:5px;margin:8px 0 10px 0">'
        f'<img src="data:image/png;base64,{_brand_b64}" alt="광동제약" style="width:130px;height:auto"/>'
        f'<span style="font-size:1.9rem;font-weight:800;color:#E83008;letter-spacing:-0.5px;line-height:1.05">KD-MoaQ</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
except Exception:
    st.sidebar.title("KD-MoaQ")
st.sidebar.divider()

# 단일 사이드바 토글(iframe): 사이드바 DOM 생성 이후 주입 — 초기 로드 시 body 전역 관찰로 브라우저 멈춤 방지
S.inject_sidebar_toggle()

def _load_dotenv_env() -> dict:
    """레포 루트 ``.env``(KEY=VALUE · # 주석/빈줄 무시)를 dict 로 읽어 반환(없으면 {}).

    refresh_job 은 QMS 자격증명(QMS_API_BASE_URL/QMS_LOGIN_* 등)이 필요하지만, 코드가 .env 를
    자동 로드하지 않으므로 앱이 .env 없이 떠 있으면 백그라운드 갱신이 빈 base_url 로 **로그인 실패**한다
    (진단: 'No scheme supplied'). 이 함수로 .env 를 읽어 subprocess 환경에 주입한다
    (deploy/run_refresh.bat 와 동일 취지). 비밀값은 로깅/출력하지 않는다.
    """
    env: dict[str, str] = {}
    _envp = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(_envp, "r", encoding="utf-8") as _f:
            for _line in _f:
                _s = _line.strip()
                if not _s or _s.startswith("#") or "=" not in _s:
                    continue
                _k, _v = _s.split("=", 1)
                env[_k.strip()] = _v.strip()
    except Exception:
        pass
    return env


def _trigger_refresh_job_background() -> bool:
    """refresh_job 을 백그라운드 subprocess 로 실행(동기 80s 블로킹 금지, Task 1.3).

    캐시를 지우지 않는다(지우면 cache_only 앱이 빈 화면이 됨). 수집이 끝나면
    refresh_job 이 캐시를 원자적으로 교체하고, 다음 새로고침 때 새 데이터가 보인다.
    성공적으로 '시작'했으면 True. (완료를 기다리지 않음)

    [수정] .env 자격증명을 subprocess 환경에 주입한다 — 앱을 .env 없이 `streamlit run` 으로
    띄워도 백그라운드 갱신이 로그인에 성공하고, 실패 수집이 _meta.json 을 덮어쓰는 일이 없도록 한다.
    """
    try:
        _sub_env = {**os.environ, **_load_dotenv_env()}
        subprocess.Popen(
            [sys.executable, "-m", "qms_pro.jobs.refresh_job"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=_sub_env,
        )
        return True
    except Exception:
        return False


# (수동 갱신 버튼은 Task 2.3 에서 상단 필터바로 이전됨 — 사이드바 중복 제거.)

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

# ─── 부모-자식 체인 연계 인덱스(ctx) 구축 — 드릴다운용 ───
# [Task 1.2] 연계 컬럼(최종 종결 여부(체인) 등)은 refresh_job 이 캐시에 미리 머지하므로
# 앱은 무거운 컬럼 머지(build_and_apply_linkage)를 돌리지 않는다. 행 클릭 드릴다운에
# 필요한 그래프 인덱스(ctx)만 DA.build_ctx 로 경량 재구성해 세션에 보관한다.
try:
    _linkage_ctx = DA.build_ctx(ALL_DFS)
    st.session_state["qms_linkage_ctx"] = _linkage_ctx
except Exception as _linkage_err:
    _linkage_ctx = None
    st.session_state["qms_linkage_ctx"] = None
    st.sidebar.warning(f"연계 인덱스 빌드 실패: {_linkage_err}")

# ─── 품목/lot 체인 귀속(전파) 파생 컬럼 부여 — 단일 지점 배선(Task 3.3a) ───
# attribution.attribute_dataframes 는 읽기전용·멱등(원본 컬럼 불변, 신규 4컬럼만 추가).
# 위 build_ctx(_linkage_ctx)를 재사용해 중복 계산 회피. 데이터 시그니처 기준 1회만 계산
# (세션 메모 — 추가 캐시 계층 신설 없음). 실패해도 원본으로 graceful 렌더.
def _attributed_all_dfs(all_dfs: dict, ctx) -> dict:
    try:
        _sig = (str(DA.get_refresh_meta().get("last_refresh")),
                tuple(len(d) for d in all_dfs.values()))
    except Exception:
        _sig = None
    _memo = st.session_state.get("_qms_attr_memo")
    if _sig is not None and _memo is not None and _memo.get("sig") == _sig:
        return _memo["dfs"]
    try:
        _out = _attribute_dataframes(all_dfs, ctx=ctx)
    except Exception as _attr_err:  # noqa: BLE001
        st.sidebar.warning(f"품목 귀속 계산 실패(원본으로 표시): {_attr_err}")
        return all_dfs
    st.session_state["_qms_attr_memo"] = {"sig": _sig, "dfs": _out}
    return _out


ALL_DFS = _attributed_all_dfs(ALL_DFS, _linkage_ctx)

# ============================================================================
# 글로벌 필터바 (Task 2.3) — 상단 고정 바로 이전 (사이드바 → 메인 상단)
# 시각: docs/prototype.html 상단바. 구성: ①수집상태 ②필터칩(연도기준·연도·진행상태·D-day)
# ③검색 ④갱신·Excel. 역할(QC/QA) 없음. 필터 로직/변수는 불변 — 위치·표현만 이동.
# 필터값은 세션 위젯 key 로 워크스페이스 전환에도 유지된다.
# ============================================================================
_all_year_dfs = [d for d in ALL_DFS.values() if not d.empty and ("연도" in d.columns or "연도_등록" in d.columns)]
_year_set = set()
for d in _all_year_dfs:
    for _ycol in ("연도", "연도_등록"):
        if _ycol in d.columns:
            _year_set |= {int(y) for y in d[_ycol].dropna().unique()}
years_available = sorted(_year_set, reverse=True) if _year_set else [datetime.now().year]
current_year = datetime.now().year
_default_years = (
    [2026]
    if 2026 in years_available
    else ([current_year] if current_year in years_available else (years_available[:1] if years_available else []))
)

# 수집 상태(_meta.json) 문자열 — 상단바 ①
_refresh_meta = DA.get_refresh_meta()
if _refresh_meta.get("source") == "none":
    _status_md = "🟡 **수집 상태**: refresh_job 미실행"
    _failed = []
else:
    _ok = _refresh_meta.get("ok_count", 0)
    _tot = _refresh_meta.get("total_count", 0)
    _last = _refresh_meta.get("last_refresh") or "(미상)"
    _badge = "✅" if (_tot and _ok >= _tot) else "⚠️"
    _status_md = f"{_badge} **{_ok}/{_tot}** · 갱신 {_last}"
    _failed = [p for p, v in (_refresh_meta.get("projects") or {}).items()
               if isinstance(v, dict) and v.get("status") != "ok"]

# 총 건수(상단 수집상태 보조) — 사이드바 16개 캡션 대체
total_all = 0
for pk, df_p in ALL_DFS.items():
    if pk == "deviationoutsourcing":
        continue
    total_all += df_p["관리번호"].nunique() if not df_p.empty and "관리번호" in df_p.columns else len(df_p)

# ─── 상단: 통합검색(상시) + 수집상태·필터(드롭다운) ───
# 수집 상태·필터 → 드롭다운(expander, 기본 접힘, 라벨 상시 표시). 접혀도 위젯은 실행되어
# 필터 값(selected_years/status_filter/dday_filter)은 그대로 유지된다.
with st.expander("⚙️ 수집 상태 · 필터", expanded=False):
    _c_status, _c_yb, _c_year, _c_status_f, _c_dday, _c_actions = st.columns([2.6, 1.5, 1.4, 1.6, 1.7, 1.4])
    with _c_status:
        st.caption("수집 상태")
        st.markdown(_status_md)
        st.caption(f"총 {total_all:,}건 · 로드 {_fetch_elapsed:.1f}s")
    with _c_yb:
        year_basis = st.radio(
            "연도 기준", ("발견일시", "등록일"), index=0, horizontal=True, key="flt_year_basis",
            help="QMS 과제 목록 기본은 등록일(regDate). 발견일과 다르면 건수가 달라집니다.",
        )
    with _c_year:
        selected_years = st.multiselect("연도", years_available, default=_default_years, key="flt_years")
    with _c_status_f:
        status_filter = st.radio("진행상태", ["전체", "진행중", "완료"], horizontal=True, key="flt_status")
    with _c_dday:
        dday_filter = st.radio("기한", ["전체", "D-day 임박 (7일)", "기한 초과"], horizontal=False, key="flt_dday")
    with _c_actions:
        st.caption("작업")
        if st.button("↻ 백그라운드 갱신", use_container_width=True, key="top_refresh"):
            if _trigger_refresh_job_background():
                st.cache_data.clear()
                st.toast("갱신 시작됨 — 완료 후 새로고침 시 반영")
            else:
                st.toast("갱신 시작 실패", icon="⚠️")
        if st.button("↺ 필터 초기화", use_container_width=True, key="top_filter_reset"):
            for _k in list(st.session_state.keys()):
                # 필터 위젯 key(flt_*)만 리셋. 레일/연계 ctx/캐시시각은 보존.
                if _k.startswith("flt_"):
                    del st.session_state[_k]
            st.rerun()
if _failed:
    _shown = ", ".join(_failed[:6]) + (" 외" if len(_failed) > 6 else "")
    st.warning(f"수집 실패 {len(_failed)}건(옛 캐시로 표시 중): {_shown}")
# 통합검색 박스(상시 표시) + 결과 컨테이너(박스 바로 아래에 결과를 렌더하기 위한 자리).
st.text_input(
    "통합검색", key="flt_search", label_visibility="collapsed",
    placeholder="🔍 통합검색: 관리번호·제목·등록자·제조번호·품목코드 (전 프로젝트 · 필터 무관)",
)
_search_result_box = st.container()   # 결과는 이 컨테이너(검색박스 바로 아래)에 나중에 채운다.
st.divider()

YEAR_FILTER_COL = "연도_등록" if year_basis == "등록일" else "연도"


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
        S.section_header(title)

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
        S.section_header("PQR 보고용 요약 (복사·붙여넣기용)")
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
        # [Task 3.3a] 품목/lot 귀속 파생 4컬럼은 제품·배치품질 전용 — 원본 데이터 표에는 노출 안 함(기존 표시 보존).
        _hide_cols = ["연도", "월", "완료여부"] + list(_ATTR_DERIVED_COLS)
        all_other = [c for c in raw_all.columns if c not in priority and c not in _hide_cols]
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
        # [Task 3.1] 표준 데이터 테이블(모노 관리번호/D-day/건수기여도 자동). 발견일시만 override.
        _col_cfg = {}
        if "발견일시" in disp:
            _col_cfg["발견일시"] = st.column_config.DateColumn("발견일시", format="YYYY-MM-DD")
        _raw_disp = _to_arrow_safe_df(raw_all[disp])
        C.data_table(_raw_disp, height=500, column_config=_col_cfg or None)
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


@st.dialog("🔗 연계 드릴다운", width="large")
def show_linkage_drawer(prno: str):
    """[Task 2.4] 레코드별 부모-자식 체인 드릴다운 드로어(st.dialog 모달).

    시각: docs/prototype.html drawer. 로직: domain.linkage(summarize_*/resolve_chain)
    + 세션 ctx(build_ctx) 재사용(rebind, 재작성 금지). 전 워크스페이스에서 동일 호출.
    표시: 부모 체인 → 본 레코드 → 자식 체인 흐름 + 최종 종결 여부(체인) + 지연일.
    """
    from qms_pro.domain.linkage import summarize_children, summarize_parent, resolve_chain
    ctx = st.session_state.get("qms_linkage_ctx")
    if ctx is None:
        st.warning("연계 인덱스가 없습니다. 상단의 '백그라운드 갱신' 후 새로고침하세요.")
        return
    key = str(prno).strip()
    row = ctx.by_prno.get(key)
    if not row:
        st.info(f"관리번호 **{key}** 의 연계 정보를 찾지 못했습니다.")
        return

    sc = summarize_children(ctx, key)
    sp = summarize_parent(ctx, key)
    chain_closed = bool(sc.get("최종 종결 여부(체인)"))
    open_n = int(sc.get("자식 미종결 수") or 0)
    delay = sc.get("자식 최대 지연일")

    # ── 헤더: 본 레코드 + 종결 판정 ──
    _proj = str(row.get("프로젝트", "") or "?")
    _title = str(row.get("제목", "") or "")
    st.markdown(f"**{_proj}** · `{key}`" + (f" — {_title}" if _title else ""))
    cc1, cc2, cc3 = st.columns(3)
    cc1.metric("최종 종결(체인)", "✅ 종결" if chain_closed else "⛔ 미종결")
    cc2.metric("미종결 자식", f"{open_n}건")
    cc3.metric("최대 지연일", f"{int(delay)}일" if delay not in (None, "") else "—")

    # ── 부모(조상) 체인 ──
    st.markdown("---")
    st.markdown("**상위(부모·조상) 체인**")
    parent_prno = str(sp.get("부모 관리번호", "") or "")
    if parent_prno:
        top = str(sp.get("최상위 조상 관리번호", "") or "")
        depth = sp.get("체인 내 위치(깊이)", 1)
        st.caption(
            f"최상위 `{top}` ({sp.get('최상위 조상 프로젝트','')}) "
            f"→ … → 부모 `{parent_prno}` ({sp.get('부모 프로젝트','')}) → **본 레코드** (깊이 {depth})"
        )
    else:
        st.caption("부모 없음 — 이 레코드가 체인 루트입니다.")

    # ── 자식(후손) 체인 ──
    st.markdown("**하위(자식·후손) 체인**")
    st.caption(f"자식 구성: {sc.get('자식 구성') or '없음'} · 전체 자식 {int(sc.get('자식 수(전체)') or 0)}건")
    desc = resolve_chain(ctx, key, "descendants")
    if desc:
        rows = []
        for n in desc:
            npr = str(n.get("관리번호", "") or "")
            closed = npr in ctx.closure_set
            rows.append({
                "관리번호": npr,
                "프로젝트": n.get("프로젝트", ""),
                "제목": (str(n.get("제목", "") or ""))[:40],
                "종결": "✅" if closed else "⛔",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("연결된 자식 워크플로우가 없습니다.")

    # 미종결 목록(있으면)
    open_list = sc.get("자식 미종결 목록") or []
    if open_list:
        st.markdown(f"**미종결 자식 {len(open_list)}건**: " + ", ".join(f"`{p}`" for p in open_list[:20]))


def _linkage_drawer_entry(df: pd.DataFrame, key_suffix: str, title: str | None = None):
    """[Task 2.4] '연계 현황' sub-tab 을 대체하는 드로어 진입 UI.

    상세 표의 관리번호를 선택 → '🔗 연계 보기' 버튼 → show_linkage_drawer 모달.
    (기존 render_linkage_section 의 섹션형 패널 대신, 레코드별 드릴다운으로 대체.)
    """
    if title:
        S.section_header(title)
    if df is None or df.empty or "관리번호" not in df.columns:
        st.info("연계를 조회할 데이터가 없습니다.")
        return
    prnos = df.drop_duplicates(subset=["관리번호"])["관리번호"].astype(str).tolist()
    # [Task 3.1] 선택+🔗 패턴을 표준 컴포넌트로 통일(중복 제거).
    C.linkage_drilldown(
        prnos, key=f"drawer_{key_suffix}", on_select=show_linkage_drawer,
        caption="관리번호를 선택하면 부모-자식 체인과 최종 종결 여부(체인)·지연일을 모달로 봅니다.",
    )


# ============================================================================
# 종결순서 점검 (Task 2.5) — 워크스페이스 분산 + 종합현황 요약
# 탐지는 기존 '이상 케이스 플래그' 컬럼(refresh_job 이 domain.linkage 로 머지) 재사용:
#   · 부모종결_자식미종결 = 선종결 의심(본 종결·자식 미완료)
#   · 자식완료_부모미완료 = 종결처리 누락(자식 완료·본 미종결)
# 귀속 규칙(CONTENT_MAP / DATA_MAPPING §2~4): 워크스페이스별 소유 프로젝트(상호 배타 →
# 종합 요약 = 워크스페이스 합, 중복 없음). 일탈은 QA 전사 1곳에만 귀속(사양서 정정).
#   · QC 시험품질: OOS·조사 (시험실 일탈은 deviation 일부지만 분할 시 중복 → 일탈은 QA 단독 귀속 유지)
#   · QA 품질운영: 일탈(자사/외주/AI)·고객불만·기한연장
#   · 조치·변경: CAPA(+Action/모니터링AI)·변경(+AI/영향성/외주)·유효성평가
# 비고: businesstransfer(업무이전)는 어느 도메인 워크스페이스에도 속하지 않아 점검 귀속 제외
#       (플래그 0건이라 신호 누락 없음, 원본은 데이터·설정에서 조회). 종합요약=워크스페이스 합 일치.
# ============================================================================
_WS_OWNED_PROJECTS = {
    "qc":      ["oos", "investigation"],
    "qa":      ["deviation", "deviationoutsourcing", "deviationactionitem", "complain", "extension"],
    "actions": ["capa", "capaactionitem", "actionitem",
                "changemanagement", "changeactionitem", "changeimpactassessment",
                "changeoutsourcing", "validityevaluation"],
}
_FLAG_PRE = "부모종결_자식미종결"   # 선종결 의심
_FLAG_MISS = "자식완료_부모미완료"  # 종결처리 누락


def _closure_counts(df: pd.DataFrame) -> tuple[int, int]:
    """DF 에서 (선종결 의심, 종결처리 누락) 건수. 관리번호 기준 1:1(중복 제거)."""
    if df is None or df.empty or "이상 케이스 플래그" not in df.columns:
        return 0, 0
    base = df.drop_duplicates(subset=["관리번호"]) if "관리번호" in df.columns else df
    flags = base["이상 케이스 플래그"].fillna("")
    return int(flags.str.contains(_FLAG_PRE).sum()), int(flags.str.contains(_FLAG_MISS).sum())


def _ws_closure_counts(ws_id: str, dfs: dict) -> tuple[int, int]:
    """워크스페이스 소유 프로젝트들의 점검 건수 합(필터 적용 DF=dfs 기준)."""
    pre = miss = 0
    for pk in _WS_OWNED_PROJECTS.get(ws_id, []):
        a, b = _closure_counts(dfs.get(pk))
        pre += a; miss += b
    return pre, miss


def render_closure_check(ws_id: str, dfs: dict, key_suffix: str):
    """[Task 2.5] 워크스페이스 소유 레코드의 종결순서 점검 케이스 목록 + 🔗 드로어."""
    S.section_header("종결순서 점검 (소유 레코드 기준)", "🧭")
    owned = _WS_OWNED_PROJECTS.get(ws_id, [])
    # 소유 프로젝트들의 플래그 보유 레코드만 모음
    frames = []
    for pk in owned:
        d = dfs.get(pk)
        if d is None or d.empty or "이상 케이스 플래그" not in d.columns:
            continue
        b = d.drop_duplicates(subset=["관리번호"]) if "관리번호" in d.columns else d
        hit = b[b["이상 케이스 플래그"].fillna("").str.contains(f"{_FLAG_PRE}|{_FLAG_MISS}", na=False)]
        if not hit.empty:
            frames.append(hit)
    pre_n, miss_n = _ws_closure_counts(ws_id, dfs)
    cc1, cc2 = st.columns(2)
    cc1.metric("선종결 의심", f"{pre_n}건", help=_LINKAGE_FLAG_HELP[_FLAG_PRE])
    cc2.metric("종결처리 누락", f"{miss_n}건", help=_LINKAGE_FLAG_HELP[_FLAG_MISS])
    if not frames:
        st.success("점검 대상 케이스가 없습니다 — 소유 레코드 모두 종결순서 정상.")
        return
    allhit = pd.concat(frames, ignore_index=True)
    _disp_cols = [c for c in ["관리번호", "프로젝트", "제목", "진행상태", "이상 케이스 플래그", "자식 미종결 수"] if c in allhit.columns]
    st.caption("행의 관리번호를 선택해 🔗 연계 드릴다운으로 체인을 점검하세요.")
    # [Task 3.1] 표준 데이터 테이블 + 표준 드릴다운(중복 제거).
    C.data_table(
        allhit[_disp_cols].rename(columns={"이상 케이스 플래그": "점검 케이스"}),
        mono_extra=["자식 미종결 수"],
    )
    _prnos = allhit["관리번호"].astype(str).tolist() if "관리번호" in allhit.columns else []
    C.linkage_drilldown(_prnos, key=f"closure_{key_suffix}", on_select=show_linkage_drawer)


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
        S.section_header(title)
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
        S.section_header("본 프로젝트 → 연관프로젝트 흐름")
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
        S.section_header("연관프로젝트 종류별 분포")
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
                                 plot_bgcolor=S.CHART_SURFACE)
            st.plotly_chart(fig_b, use_container_width=True)
        else:
            st.info("집계 가능한 자식 구성이 없습니다.")

    st.markdown("---")

    # 섹션 3: 연관프로젝트 미완료 TOP 20 + drill-down
    S.section_header("연관프로젝트 미완료 TOP 20")
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
    S.section_header("점검 필요 케이스")
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
    S.section_header("연계 단계(깊이) 분포")
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
                         plot_bgcolor=S.CHART_SURFACE,
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
                                margin=dict(l=10, r=30, t=10, b=10), plot_bgcolor=S.CHART_SURFACE)
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
                                margin=dict(l=10, r=30, t=10, b=10), plot_bgcolor=S.CHART_SURFACE,
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

    S.render_header(f"{label}" + (" (자사 · 외주)" if kind == "일탈" else " (자사 · 외주)"))
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
                S.section_header("자사 vs 외주")
                if "자사/외주" in ftab.columns:
                    vc = _wgroupby(ftab, "자사/외주", name="건수")
                    fig = px.bar(vc, x="자사/외주", y="건수", text="건수",
                                 color="자사/외주",
                                 color_discrete_map={"자사": CHART_COLORS.get("blue", "#1f77b4"),
                                                      "외주": CHART_COLORS.get("orange", "#ff7f0e")})
                    fig.update_traces(textposition="outside")
                    fig.update_layout(height=300, plot_bgcolor=S.CHART_SURFACE, showlegend=False,
                                       margin=dict(l=10, r=10, t=10, b=10))
                    st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_kpi_src")
            with col_b:
                if kind == "일탈":
                    S.section_header("일탈 등급 분포")
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
                    S.section_header("진행상태 분포")
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
            S.section_header("월별 추이 (자사/외주)")
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
                        line=dict(color=color_map.get(str(col), CHART_COLORS["blue"]), width=2),
                        marker=dict(size=7),
                    ))
                fig_m.update_layout(height=320, plot_bgcolor=S.CHART_SURFACE,
                                      margin=dict(l=20, r=20, t=10, b=30),
                                      legend=dict(orientation="h", y=1.05))
                st.plotly_chart(fig_m, use_container_width=True, key=f"{key_prefix}_trend_month")

                table = piv.reset_index()
                first_col = table.columns[0]
                table = table.rename(columns={first_col: "월"})
                table["월"] = pd.to_numeric(table["월"], errors="coerce").fillna(0).astype(int).astype(str) + "월"
                st.dataframe(table, use_container_width=True, hide_index=True)

            if kind == "일탈":
                S.section_header("일탈 등급 (대분류) × 자사/외주")
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
                    fig_s1.update_layout(height=320, plot_bgcolor=S.CHART_SURFACE,
                                           margin=dict(l=10, r=10, t=10, b=10),
                                           legend=dict(orientation="h", y=1.05))
                    st.plotly_chart(fig_s1, use_container_width=True, key=f"{key_prefix}_trend_grade")

            # YoY
            S.section_header(f"YoY 비교 — {trend_year} vs {trend_year - 1}")
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
                S.section_header("발생 유형 — 자사/외주 분리")
                _render_source_split(ftab, f"{key_prefix}_cause_main",
                                       dim="발생 유형", orientation="h", top_n=15)
            else:
                st.info("`발생 유형` 컬럼 없음")

            st.divider()
            if "발생 세부유형" in ftab.columns:
                S.section_header("발생 세부유형 — 자사/외주 분리 (Top 20)")
                _render_source_split(ftab, f"{key_prefix}_cause_sub",
                                       dim="발생 세부유형", orientation="h", top_n=20)

            st.divider()
            S.section_header("이상발생 원인 (Analyst Error 등)")
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
                S.section_header("재발여부 분포 — 자사/외주 분리")
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
                S.section_header("재발건 샘플 목록")
                show_cols = [c for c in ["관리번호", "자사/외주", "제목", "재발여부", "작성팀",
                                           "이상발생 원인", "발생 유형", "발생 세부유형"]
                             if c in rf.columns]
                C.data_table(rf[show_cols].head(80), height=320)

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
                                            plot_bgcolor=S.CHART_SURFACE,
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
                                            plot_bgcolor=S.CHART_SURFACE,
                                            margin=dict(l=10, r=30, t=10, b=10))
                        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_team_out")

                        if kind == "일탈" and "일탈 등급 대분류" in out_df.columns:
                            maj = out_df[out_df["일탈 등급 대분류"] == "Major"]
                            if not maj.empty:
                                st.caption("Major 일탈 발생 외주업체")
                                disp_cols = [c for c in ["관리번호", "위탁업체", "일탈 등급", "제목", "접수월"]
                                             if c in maj.columns]
                                C.data_table(maj[disp_cols], height=220)
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
                                            plot_bgcolor=S.CHART_SURFACE,
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
            _linkage_drawer_entry(ftab, key_suffix=f"{key_prefix}_link",
                                  title=f"{label} 연계 드릴다운")

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

    S.render_footer()


# ============================================================================
# 탭 구성
# ============================================================================

# 기한 초과 건수 계산 (탭 배지용) — '기한 초과' 통일: 살아있는(미완료·미취소) 행만 카운트.
_overdue_all_count = sum(
    int(df_p[active_mask(df_p)]["D-day"].lt(0).sum())
    for df_p in F.values()
    if not df_p.empty and "D-day" in df_p.columns
)
_deadline_label = f"⏰ 기한관리 ({_overdue_all_count}건 초과)" if _overdue_all_count > 0 else "⏰ 기한관리"

# ============================================================================
# 좌측 워크스페이스 레일 (Task 2.1) — 11 상단탭 → 7 워크스페이스
# 구조: CONTENT_MAP.md 매핑. 시각: docs/prototype.html(좌측 레일). 가로 sticky 탭 제거로
# 헤더 클릭 가로채기 현상 해소. 기존 탭 본문(렌더 함수 호출)은 rebind(재배치)만, 로직 불변.
#
# 동작: 사이드바 option_menu 로 워크스페이스 선택 → 그 안에서 도메인이 여럿이면 sub-view
# (segmented_control)로 1개 선택. 각 기존 'with tab_x:' 블록은 'if _render_tab("x"):' 로 바뀌어
# (1줄 치환, 본문/들여쓰기 불변) 활성 sub-view 일 때만 실행된다.
# ============================================================================
# 워크스페이스 정의: (id, 라벨, Bootstrap아이콘, [(탭키, sub-view 라벨), ...])
_WORKSPACES = [
    ("overview", "종합 현황",     "grid-1x2-fill",
        [("exec", "경영진 KPI"), ("workflow", "워크플로우 연계"), ("deadline", "기한 관리")]),
    ("qc",       "QC 시험품질",   "eyedropper",
        [("oos", "OOS"), ("inv", "조사")]),
    ("qa",       "QA 품질운영",   "shield-check",
        [("dev", "일탈"), ("incident", "인시던트"), ("complain", "고객불만")]),
    ("actions",  "조치·변경",     "arrow-repeat",
        [("capa", "CAPA·Action"), ("change", "변경관리")]),
    ("product",  "제품·배치품질", "box-seam",          [("product_apqr", "APQR"), ("product_lot", "lot 처분")]),
    ("alerts",   "알림·모니터링", "bell",              [("alerts_new", "알림·모니터링")]),
    ("data",     "데이터·설정",   "table",             [("settings", "설정")]),
]
_WS_LABELS = [w[1] for w in _WORKSPACES]
_WS_ICONS = [w[2] for w in _WORKSPACES]
_WS_BY_LABEL = {w[1]: w[0] for w in _WORKSPACES}
_WS_SUBVIEWS = {w[0]: w[3] for w in _WORKSPACES}          # ws_id -> [(tabkey, label)]
_TABKEY_TO_WS = {tk: w[0] for w in _WORKSPACES for (tk, _l) in w[3]}
_TABKEY_LABEL = {tk: l for w in _WORKSPACES for (tk, l) in w[3]}

with st.sidebar:
    st.divider()
    # 프로그램적 점프(종합현황 요약 버튼 등): _ws_jump_target 가 설정돼 있으면 그 인덱스로
    # manual_select 강제. (option_menu 는 default_index 만으로는 rerun 시 세션을 덮어쓰므로
    # manual_select 가 필요하다.)
    _jump_idx = None
    if st.session_state.get("_ws_jump_target") in _WS_LABELS:
        _jump_idx = _WS_LABELS.index(st.session_state.pop("_ws_jump_target"))
    _menu_kwargs = dict(
        menu_title="워크스페이스",
        options=_WS_LABELS,
        icons=_WS_ICONS,
        menu_icon="columns-gap",
        default_index=_jump_idx if _jump_idx is not None else 0,
        key="qms_ws_rail",
        styles={
            "container": {"padding": "4px", "background-color": "transparent"},
            "nav-link": {"font-size": "14px", "font-weight": "600", "--hover-color": "#eef1f8"},
            "nav-link-selected": {"background-color": S.ACCENT_BLUE},
            "icon": {"font-size": "15px"},
        },
    )
    if _jump_idx is not None:
        _menu_kwargs["manual_select"] = _jump_idx
    _active_ws_label = option_menu(**_menu_kwargs)
_active_ws = _WS_BY_LABEL.get(_active_ws_label, "overview")

# 활성 워크스페이스의 sub-view 선택(도메인이 2개 이상일 때만 세그먼트 노출).
_subviews = _WS_SUBVIEWS.get(_active_ws, [])
if len(_subviews) > 1:
    _sub_labels = [l for (_tk, l) in _subviews]
    # 기한 관리 라벨에 초과 배지 반영(기존 _deadline_label 취지 유지)
    _sub_labels = [(f"{l} ({_overdue_all_count}건 초과)" if tk == "deadline" and _overdue_all_count > 0 else l)
                   for (tk, l) in _subviews]
    _picked = st.segmented_control(
        "보기", options=_sub_labels, default=_sub_labels[0],
        key=f"subview_{_active_ws}", label_visibility="collapsed",
    )
    _idx = _sub_labels.index(_picked) if _picked in _sub_labels else 0
    _active_tabkey = _subviews[_idx][0]
else:
    _active_tabkey = _subviews[0][0] if _subviews else None


def _render_tab(tabkey: str) -> bool:
    """기존 'with tab_x:' 를 대체하는 가드. 활성 워크스페이스의 선택된 sub-view 일 때만 True.

    (Python with 본문은 항상 실행되므로 no-op 컨텍스트로는 비활성 탭이 메인에 새어나온다.
     그래서 'with tab_x:' → 'if _render_tab(\"x\"):' 1줄 치환으로 본문 실행 자체를 가드한다.)
    """
    return tabkey == _active_tabkey


# ============================================================================
# 통합검색(🔍) 결과 패널 (Task 2.3 보류분) — 검색어가 있을 때만 전 화면 상단에 노출.
#   전 프로젝트(ALL_DFS) · 필터 무관. 5필드(관리번호·제목·등록자·제조번호·품목코드) OR
#   매칭은 기존 UIF.apply_search_filters 재사용(신규 매칭 로직 0). 결과 행 🔗 → 드로어.
#   검색은 레코드 나열(집계 아님) → 관리번호 dedup 표시. 검색어 없으면 패널 미표시.
# ============================================================================
_global_q = str(st.session_state.get("flt_search", "") or "").strip()
# 결과 계산(검색어 있을 때만) — 드롭다운 라벨에 건수를 표기하기 위해 먼저 집계한다.
_gs_frames = []
if _global_q:
    _GS_COLS = ["관리번호", "제목", "작성팀", "제조번호", "품목코드", "기한일", "D-day", "진행상태"]
    for _gk, _gd in ALL_DFS.items():
        if _gd is None or _gd.empty or "관리번호" not in _gd.columns:
            continue
        # 필드별 OR. apply_search_filters 는 컬럼 부재 시 미적용(=전체 반환)이므로,
        # 해당 컬럼이 있는 필드만 호출해 합집합한다(부재 필드가 전 레코드를 끌어오는 것 방지).
        _parts = []
        for _fld, _col in (("qms_no", "관리번호"), ("title", "제목"), ("registrant", "등록자"),
                           ("lot", "제조번호"), ("item_code", "품목코드")):
            if _col in _gd.columns:
                _parts.append(UIF.apply_search_filters(_gd, **{_fld: _global_q}))
        if not _parts:
            continue
        _hit = pd.concat(_parts, ignore_index=True).drop_duplicates(subset=["관리번호"])
        if not _hit.empty:
            _gf = _hit[[c for c in _GS_COLS if c in _hit.columns]].copy()
            _gf.insert(0, "프로젝트", PROJECT_META[_gk]["label"])
            _gs_frames.append(_gf)
# 검색박스 바로 아래(_search_result_box)에 결과 드롭다운(expander)을 렌더.
#   라벨 상시 표시 · 검색 시 기본 펼침(expanded=True) · 결과 없거나 미검색 시 접힘+안내.
with _search_result_box:
    if not _global_q:
        with st.expander("🔍 통합검색 결과", expanded=False):
            st.caption("위 검색창에 입력하면 전 프로젝트(관리번호·제목·등록자·제조번호·품목코드) "
                       "결과가 여기에 표시됩니다. (필터 무관)")
    elif _gs_frames:
        import warnings as _gw
        with _gw.catch_warnings():
            _gw.simplefilter("ignore", FutureWarning)
            _gs_res = pd.concat(_gs_frames, ignore_index=True)
        _gs_total = len(_gs_res)
        _gs_show = _gs_res.sort_values("D-day").head(200) if "D-day" in _gs_res.columns else _gs_res.head(200)
        _lbl = f"🔍 통합검색 결과 — '{_global_q}' · 총 {_gs_total}건 · {len(_gs_frames)}개 프로젝트"
        if _gs_total > 200:
            _lbl += " (상위 200건 표시)"
        with st.expander(_lbl, expanded=True):
            st.caption("전 프로젝트 · 필터 무관")
            C.data_table(_gs_show, status=True, height=360)
            C.linkage_drilldown(
                _gs_show["관리번호"].astype(str).tolist(), key="global_search",
                on_select=show_linkage_drawer,
                caption="검색 결과의 관리번호 선택 → 🔗 로 부모-자식 체인·종결여부 추적",
            )
    else:
        with st.expander(f"🔍 통합검색 결과 — '{_global_q}'", expanded=True):
            S.empty_state(f"'{_global_q}' 검색 결과가 없습니다.")


# ============================================================================
# 탭 1: 경영진 대시보드
# ============================================================================

if _render_tab("exec"):
    S.render_header("경영진 품질 대시보드", f"경영진·전사 통제탑 | MFDS GMP 점검 대비 | {datetime.now().strftime('%Y-%m-%d')}")

    primary_year = selected_years[0] if selected_years else current_year
    prev_year = primary_year - 1

    # ════════════════════════════════════════════════════════════════════
    # ① 핵심 (DATA_MAPPING §1) — KPI 4 카드(진척바·목표마커) + 이상신호 2 카드(재발 일시 비활성)
    # ════════════════════════════════════════════════════════════════════
    S.section_header("핵심 KPI · 목표 대비", "①")
    # CAPA 이행률 / 변경 완료율 = safe_pct(weighted_completed, weighted_total)
    capa_rate = safe_pct(weighted_metric_completed(fcapa), weighted_metric_total(fcapa))
    change_rate = safe_pct(weighted_metric_completed(fchg), weighted_metric_total(fchg))
    # 불만 평균처리일 = 접수일~처리완료일 평균
    avg_complaint_days = None
    if not fcmp.empty and "접수일" in fcmp.columns and "처리완료일" in fcmp.columns:
        _rcpt = pd.to_datetime(fcmp["접수일"], errors="coerce")
        _cmpl = pd.to_datetime(fcmp["처리완료일"], errors="coerce")
        _dd = (_cmpl - _rcpt).dt.days.dropna()
        if not _dd.empty:
            avg_complaint_days = round(_dd.mean(), 1)
    # 기한초과(전사) = 전 프로젝트 weighted_metric_overdue 합 — '기한 초과' 통일: 살아있는(미완료·미취소)만.
    overdue_total = sum(weighted_metric_overdue(df_p[active_mask(df_p)]) for df_p in F.values())
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        C.kpi_stat_card(capa_rate, KPI_TARGETS["CAPA 이행률"], "CAPA 이행률")
    with k2:
        C.kpi_stat_card(change_rate, KPI_TARGETS["변경 완료율"], "변경 완료율")
    with k3:
        _cval = avg_complaint_days if avg_complaint_days is not None else 0
        C.kpi_stat_card(_cval, KPI_TARGETS["불만 평균처리일"], "불만 평균처리일", suffix="일", inverse=True)
    with k4:
        # 기한초과는 '낮을수록 좋음' → inverse, 목표 0(초과 없음). 진척바는 0 기준.
        C.kpi_stat_card(round(overdue_total), 0, "기한초과(전사)", suffix="건", inverse=True, max_val=max(round(overdue_total), 1))

    # · 이상신호 카드: 종결순서 점검(2.5 요약) / Analyst error
    #   [재발 일시 비활성] '재발' 라인 숨김 — 재발여부 = recurrence1('A.재발가능성' select 점수)이며
    #   '예' 비교 무효(실데이터 '예' 0건)·값 2/6 라벨 미확인이라 현 집계 의미 불명(정직화, 추측 금지).
    #   TODO: 재발 신호 도메인 재정의 대기 — 라이브 폼 'A.재발가능성' 값 2/6 라벨 확인 후 재정의.
    st.caption("전사 이상신호")
    _sg1, _sg3 = st.columns(2)
    # 종결순서 점검 = 2.5 와 동일 수치(워크스페이스 합 = 전 DF 카운트)
    _ov_pre = _ov_miss = 0
    for _wsid in ("qc", "qa", "actions"):
        _a, _b = _ws_closure_counts(_wsid, F)
        _ov_pre += _a; _ov_miss += _b
    with _sg1:
        # [Task 3.1] 이상신호 = 표준 signal_card(의미색 좌측 강조). 값/로직 불변.
        C.signal_card("🧭 종결순서 점검", f"{_ov_pre + _ov_miss}건", tone="danger", icon="",
                      sub=f"선종결 의심 {_ov_pre} · 종결처리 누락 {_ov_miss} (플래그 2종 합)")
        if st.button("점검하러 가기 →", key="sig_jump_closure", use_container_width=True):
            st.session_state["_ws_jump_target"] = "QA 품질운영"
            st.rerun()
    # [재발 카드 일시 비활성 — 위 TODO 참조. 재발 집계/표시 모두 보류, 추측 집계 미작성.]
    # Analyst error = 이상발생 원인=="Analyst error" 건수기여도(oos+dev)
    _ae_df = pd.concat([d for d in (foos, fdev) if not d.empty], ignore_index=True) if any(not d.empty for d in (foos, fdev)) else pd.DataFrame()
    _ae_n = 0
    if not _ae_df.empty and "이상발생 원인" in _ae_df.columns:
        _ae_hit = _ae_df[_ae_df["이상발생 원인"] == "Analyst error"]
        _ae_n = round(_ae_hit["건수기여도"].sum()) if "건수기여도" in _ae_hit.columns else len(_ae_hit)
    with _sg3:
        C.signal_card("🔬 Analyst error", f"{_ae_n}건", tone="info", icon="",
                      sub="이상발생 원인='Analyst error' 건수기여도 합(OOS+일탈)")

    st.divider()

    # ════════════════════════════════════════════════════════════════════
    # ② 추세·분포 (DATA_MAPPING §1) — 월별 품질이상 추세(누적) + 이상발생 원인 도넛
    # ════════════════════════════════════════════════════════════════════
    S.section_header("추세 · 분포", "②")
    t1, t2 = st.columns([2, 1])
    with t1:
        st.caption("월별 품질이상 추세 (OOS + 일탈, 건수기여도 누적)")
        _mc_o = _month_col_for_df(foos)
        _mc_d = _month_col_for_df(fdev)
        _oos_m = [round(v) for v in _monthly_weighted_series(foos, _mc_o)["건수"].tolist()] if (not foos.empty and _mc_o in foos.columns) else [0] * 12
        _dev_m = [round(v) for v in _monthly_weighted_series(fdev, _mc_d)["건수"].tolist()] if (not fdev.empty and _mc_d in fdev.columns) else [0] * 12
        fig_tr = go.Figure()
        fig_tr.add_trace(go.Bar(x=MONTH_LABELS, y=_oos_m, name="OOS", marker_color=CHART_COLORS["blue"]))
        fig_tr.add_trace(go.Bar(x=MONTH_LABELS, y=_dev_m, name="일탈", marker_color=CHART_COLORS["teal"]))
        fig_tr.update_layout(barmode="stack", height=320, margin=dict(l=40, r=20, t=10, b=40),
                             plot_bgcolor=S.CHART_SURFACE, legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"))
        st.plotly_chart(fig_tr, use_container_width=True)
    with t2:
        st.caption("이상발생 원인 분포 (OOS + 일탈)")
        if not _ae_df.empty and "이상발생 원인" in _ae_df.columns:
            _cause = _wgroupby(_ae_df[_ae_df["이상발생 원인"].fillna("").str.strip() != ""], "이상발생 원인", name="건수")
            if not _cause.empty:
                fig_dn = px.pie(_cause, values="건수", names="이상발생 원인", hole=0.5,
                                color_discrete_sequence=CHART_SEQUENCE)
                fig_dn.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=10),
                                     legend=dict(orientation="h", y=-0.1))
                st.plotly_chart(fig_dn, use_container_width=True)
            else:
                S.empty_state("이상발생 원인 데이터가 없습니다.")
        else:
            S.empty_state("이상발생 원인 데이터가 없습니다.")

    st.divider()

    # ════════════════════════════════════════════════════════════════════
    # ③ 상세·점검 (DATA_MAPPING §1) — 기한 위험 테이블(🔗) + 종결순서 점검 요약
    # ════════════════════════════════════════════════════════════════════
    S.section_header("상세 · 점검", "③")
    d1, d2 = st.columns([3, 2])
    with d1:
        st.caption("🚨 기한 위험 — 즉시 조치 (전 프로젝트 D-day 오름차순)")
        _ov_frames = []
        for pk, df_p in F.items():
            if df_p.empty or "D-day" not in df_p.columns:
                continue
            _dn = _num_series(df_p["D-day"], default=0.0)
            _over = df_p[_dn < 0].copy()
            if not _over.empty:
                _over["프로젝트"] = PROJECT_META[pk]["label"]
                _ov_frames.append(_over)
        if _ov_frames:
            _oa = pd.concat(_ov_frames, ignore_index=True)
            _team_col = "작성팀" if "작성팀" in _oa.columns else None
            _cols = ["프로젝트"] + [c for c in ["관리번호", "제목", _team_col, "기한일", "D-day", "진행상태"] if c and c in _oa.columns]
            _top = _oa.sort_values("D-day").head(20)
            # [Task 3.1] 표준 데이터 테이블(상태 Pill·모노 관리번호/D-day) + 표준 드릴다운(중복 제거).
            C.data_table(_top[_cols], status=True, height=300)
            _prnos = _top["관리번호"].astype(str).tolist() if "관리번호" in _top.columns else []
            C.linkage_drilldown(_prnos, key="ov_overdue", on_select=show_linkage_drawer)
        else:
            st.success("기한 초과 항목이 없습니다.")
    with d2:
        st.caption("🧭 종결순서 점검 — 전사 요약")
        C.signal_card("선종결 의심", f"{_ov_pre}건", tone="warn", sub=_LINKAGE_FLAG_HELP[_FLAG_PRE])
        C.signal_card("종결처리 누락", f"{_ov_miss}건", tone="danger", sub=_LINKAGE_FLAG_HELP[_FLAG_MISS])
        st.caption("상세는 각 워크스페이스 하단 '종결순서 점검' 패널에서 (소유 레코드 기준).")

    st.divider()

    # · 수집 상태 — 16개 프로젝트 건수 그리드 (관리번호.nunique)
    S.section_header("수집 상태 (16개 프로젝트)", "▦")
    _grid_cols = st.columns(8)
    _gi = 0
    for pk, df_p in ALL_DFS.items():
        if pk == "deviationoutsourcing":
            continue  # 일탈에 통합 표기
        _n = df_p["관리번호"].nunique() if not df_p.empty and "관리번호" in df_p.columns else len(df_p)
        with _grid_cols[_gi % 8]:
            st.metric(PROJECT_META[pk]["label"], f"{_n}건")
        _gi += 1
    # 교육: QMS 통합수집 중단(LMS 시스템 이전) — 데이터 수집 없는 '표시용' 안내 카드.
    #   st.metric 은 값 색상을 못 바꾸므로 메트릭 카드 스타일을 맞춘 커스텀 카드로 표기
    #   (사용중단=빨강, 다음 줄에 LMS시스템 이전, 좌측 보더도 위험색).
    with _grid_cols[_gi % 8]:
        st.markdown(
            '<div style="background:#ffffff;border-radius:10px;padding:14px 18px;'
            'border-left:4px solid #D7263D;box-shadow:0 2px 8px rgba(0,0,0,0.06)">'
            '<div style="font-size:0.77rem;color:#6c757d;font-weight:600;letter-spacing:0.3px">교육</div>'
            '<div style="font-size:1.42rem;font-weight:700;color:#D7263D;line-height:1.35">사용중단</div>'
            '<div style="font-size:0.72rem;color:#868e96;margin-top:2px">LMS시스템 이전</div>'
            '</div>', unsafe_allow_html=True)

    S.render_footer()


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

if _render_tab("oos"):
    S.render_header("OOS (Out of Specification)")
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
        # 📄 Word(.docx) 보고서 — 아래 경향분석보고서 내용을 표 기반 Word 문서로 생성(현재 필터 반영).
        #   신규 순수모듈 qms_word_report 사용. render_oos_report(표시)·도메인 로직 불변.
        try:
            import qms_word_report as _wr
            _docx_bytes = _wr.build_oos_trend_report_docx(
                foos, safe_pct, COMPLETED_KEYWORDS,
                as_of=datetime.now().strftime("%Y-%m-%d %H:%M"),
                project_label="OOS (Out of Specification)",
                filter_note=f"연도 {selected_years} · 진행상태 {status_filter} · 기한 {dday_filter}",
            )
            _wc1, _wc2 = st.columns([3, 1])
            _wc1.caption("아래 경향분석보고서를 Word(.docx) 문서로 내려받습니다 — 현재 필터 기준.")
            _wc2.download_button(
                "📄 Word 보고서", data=_docx_bytes,
                file_name=f"OOS_경향분석보고서_{datetime.now():%Y%m%d}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True, key="oos_trend_word_dl",
            )
            st.divider()
        except Exception as _wr_e:
            st.warning(f"Word 보고서 생성 실패: {_wr_e}")
        oos_panels.render_oos_report(foos, CHART_COLORS, safe_pct, COMPLETED_KEYWORDS)
    with o_tab4:
        # 📊 PPT(.pptx) 보고서 — 마감회의 & GMP 내용을 PowerPoint로 생성(현재 필터 반영).
        #   신규 순수모듈 qms_ppt_report 사용. 아래 render_oos_gmp(표시 레이아웃)·도메인 로직 불변,
        #   다운로드 버튼만 상단에 추가하며 보고서 표/차트는 PPT 네이티브 요소로 그대로 재현한다.
        try:
            import qms_ppt_report as _pr
            _pptx_bytes = _pr.build_oos_gmp_report_pptx(
                foos, oos_ny, primary_year, prev_year, ycol, mc_oos, safe_pct, COMPLETED_KEYWORDS,
                as_of=datetime.now().strftime("%Y-%m-%d %H:%M"),
                project_label="OOS (Out of Specification)",
                filter_note=f"연도 {selected_years} · 진행상태 {status_filter} · 기한 {dday_filter}",
            )
            _pc1, _pc2 = st.columns([3, 1])
            _pc1.caption("마감회의 & GMP 보고서를 PowerPoint(.pptx)로 내려받습니다 — 현재 필터 기준 (표·차트 재현).")
            _pc2.download_button(
                "📊 PPT 보고서", data=_pptx_bytes,
                file_name=f"OOS_마감회의_GMP_{datetime.now():%Y%m%d}.pptx",
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                use_container_width=True, key="oos_gmp_ppt_dl",
            )
            st.divider()
        except Exception as _pr_e:
            st.warning(f"PPT 보고서 생성 실패: {_pr_e}")
        oos_panels.render_oos_gmp(
            foos, oos_ny, primary_year, prev_year, ycol, mc_oos, CHART_COLORS, safe_pct, COMPLETED_KEYWORDS,
        )
    with o_tab_link:
        _linkage_drawer_entry(F.get("oos", pd.DataFrame()), key_suffix="oos",
                              title="OOS 연계 드릴다운 (OOS → 조사 → CAPA → AI)")
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

    S.render_footer()


# ============================================================================
# ============================================================================
# 탭 3: 일탈 (자사 · 외주)
# ============================================================================

if _render_tab("dev"):
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

if _render_tab("incident"):
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

if _render_tab("inv"):
    S.render_header("조사 (Investigation)")
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
            S.section_header("5M1E 원인 조사 현황")
            m1e_cols = [c for c in finv.columns if c.startswith("5M1E_") and not c.endswith("_내용")]
            if m1e_cols:
                m1e_data = finv[m1e_cols].apply(lambda x: (x == "수행").sum()).reset_index()
                m1e_data.columns = ["항목", "수행 건수"]
                m1e_data["항목"] = m1e_data["항목"].str.replace("5M1E_", "")
                fig_m1e = px.bar(m1e_data.sort_values("수행 건수", ascending=True),
                                 x="수행 건수", y="항목", orientation="h",
                                 color_discrete_sequence=[CHART_COLORS["blue"]], text="수행 건수")
                fig_m1e.update_layout(height=300, margin=dict(l=0, r=20, t=10, b=10),
                                       plot_bgcolor=S.CHART_SURFACE)
                fig_m1e.update_traces(textposition="outside")
                st.plotly_chart(fig_m1e, use_container_width=True)

                S.section_header("5M1E 항목별 수행 비율")
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
                                     plot_bgcolor=S.CHART_SURFACE)
                st.plotly_chart(fig_m, use_container_width=True)
            else:
                st.info("5M1E 컬럼이 없습니다.")

    with i_trend:
        if finv.empty:
            st.warning("조사 데이터가 없습니다.")
        else:
            mc_i = _month_col_for_df(finv)
            S.section_header("월별 조사 발생 추이")
            if mc_i in finv.columns:
                mf = _monthly_weighted_series(finv, mc_i)
                fig = go.Figure(go.Scatter(
                    x=MONTH_LABELS, y=[round(v) for v in mf["건수"].tolist()],
                    mode="lines+markers", line=dict(color=CHART_COLORS.get("blue", "#1f77b4"), width=2)
                ))
                fig.update_layout(height=280, plot_bgcolor=S.CHART_SURFACE,
                                    margin=dict(l=30, r=10, t=10, b=30))
                st.plotly_chart(fig, use_container_width=True)

            S.section_header("팀별 조사 현황")
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
                                     plot_bgcolor=S.CHART_SURFACE)
                st.plotly_chart(fig_t, use_container_width=True)
            else:
                st.info("작성팀 컬럼 없음")

    with i_link:
        _linkage_drawer_entry(F.get("investigation", pd.DataFrame()), key_suffix="inv",
                              title="조사 연계 드릴다운 (OOS/일탈 → 조사 → CAPA → AI)")

    with i_tab_raw:
        render_raw_data_section(
            default_project_keys=["investigation"],
            key_suffix="inv",
            allow_change=False,
            title="원본 데이터 (조사)",
            extra_priority=["작성팀", "부모 프로젝트", "부모 관리번호",
                             "자식 수(전체)", "자식 미종결 수", "체인 최대 깊이"],
        )

    S.render_footer()


# ============================================================================
# 탭 3: CAPA 관리
# ============================================================================

if _render_tab("capa"):
    S.render_header("CAPA & Action Item 관리")
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
                S.section_header("CAPA 진행상태")
                if "진행상태" in fcapa.columns:
                    sd = fcapa["진행상태"].value_counts().reset_index()
                    sd.columns = ["상태", "건수"]
                    fig_cs = px.pie(sd, values="건수", names="상태", hole=0.5,
                                    color_discrete_sequence=px.colors.qualitative.Set2)
                    fig_cs.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=10))
                    fig_cs.update_traces(textinfo="label+value", textfont_size=11)
                    st.plotly_chart(fig_cs, use_container_width=True)
            with cc2:
                S.section_header("CAPA 구분")
                if "CAPA 구분" in fcapa.columns:
                    gd = fcapa["CAPA 구분"].value_counts().reset_index()
                    gd.columns = ["구분", "건수"]
                    fig_g = px.bar(gd, x="구분", y="건수", text="건수",
                                    color_discrete_sequence=[CHART_COLORS.get("blue", "#1f77b4")])
                    fig_g.update_traces(textposition="outside")
                    fig_g.update_layout(height=280, plot_bgcolor=S.CHART_SURFACE)
                    st.plotly_chart(fig_g, use_container_width=True)

            st.divider()
            S.section_header("CAPA 상세 목록")
            capa_disp = [c for c in ["관리번호", "제목", "등록자", "기한일", "진행상태",
                                      "D-day", "CAPA 구분", "사유"] if c in fcapa.columns]
            # [Task 3.1] 표준 데이터 테이블(상태 Pill·모노 관리번호/D-day).
            C.data_table(
                fcapa[capa_disp].sort_values("D-day") if "D-day" in fcapa.columns else fcapa[capa_disp],
                status=True, height=360,
            )

    with capa_ai:
        ck1, ck2, ck3 = st.columns(3)
        ck1.metric("CAPA AI", f"{cai_t}건", delta=f"{safe_pct(cai_d, cai_t):.0f}%")
        ck2.metric("모니터링 AI", f"{ai_t}건", delta=f"{safe_pct(ai_d, ai_t):.0f}%")
        ck3.metric("AI 합계", f"{cai_t + ai_t}건",
                    delta=f"{safe_pct(cai_d + ai_d, cai_t + ai_t):.0f}%")

        st.divider()
        # [Task 2.6] 반원 게이지 제거 → 진척 바 KPI 스탯 카드(토큰 일관). 게이지 잔존 0.
        S.section_header("모니터링AI 이행률")
        ai_rate = safe_pct(ai_d, ai_t)
        C.kpi_stat_card(round(ai_rate, 1), 80, "모니터링AI 이행률")

    with capa_deadline:
        S.section_header("기한 초과·지연 현황")
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
            # [Task 3.1] 표준 데이터 테이블(상태 Pill — 기한초과 맥락이라 대부분 🔴 초과).
            C.data_table(all_over[disp].sort_values("D-day"), status=True, height=380)
        else:
            st.success("기한 초과 항목이 없습니다.")

    with capa_link:
        _linkage_drawer_entry(F.get("capa", pd.DataFrame()), key_suffix="capa",
                              title="CAPA 연계 드릴다운 (OOS/일탈 → 조사 → CAPA → AI)")

    with capa_tab_raw:
        render_raw_data_section(
            default_project_keys=["capa", "capaactionitem", "actionitem"],
            key_suffix="capa",
            allow_change=False,
            title="원본 데이터 (CAPA · CAPA AI · 모니터링AI)",
            extra_priority=["CAPA 구분", "부모 프로젝트", "부모 관리번호",
                             "자식 수(전체)", "자식 미종결 수"],
        )

    S.render_footer()


# ============================================================================
# 탭 4: 변경관리
# ============================================================================

if _render_tab("change"):
    S.render_header("변경관리 통합 현황")
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
                S.section_header("변경 등급별 분포")
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
                S.section_header("변경 구분 (영구/임시)")
                if "변경 구분" in fchg.columns:
                    dd = fchg["변경 구분"].value_counts().reset_index()
                    dd.columns = ["구분", "건수"]
                    dd = dd[dd["구분"].notna() & (dd["구분"] != "")]
                    if not dd.empty:
                        fig_d = px.bar(dd, x="구분", y="건수", text="건수",
                                        color_discrete_sequence=[CHART_COLORS.get("teal", "#17becf")])
                        fig_d.update_traces(textposition="outside")
                        fig_d.update_layout(height=320, plot_bgcolor=S.CHART_SURFACE)
                        st.plotly_chart(fig_d, use_container_width=True)

    with chg_impact:
        if fchgimp.empty:
            st.info("변경영향성평가 데이터가 없습니다.")
        elif "영향 GMP 영역" in fchgimp.columns:
            S.section_header("영향성평가 GMP 영역 분포")
            areas = fchgimp["영향 GMP 영역"].dropna().str.split(", ").explode()
            areas = areas[areas != "해당 없음"].value_counts().reset_index()
            areas.columns = ["영역", "건수"]
            if not areas.empty:
                fig_ar = px.bar(areas.sort_values("건수", ascending=True),
                                 x="건수", y="영역", orientation="h",
                                 color_discrete_sequence=[CHART_COLORS.get("teal", "#17becf")], text="건수")
                fig_ar.update_layout(height=max(260, 22 * len(areas)),
                                      margin=dict(l=0, r=30, t=10, b=10), plot_bgcolor=S.CHART_SURFACE)
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
            S.section_header("외주변경 위탁처별 현황")
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
                                            plot_bgcolor=S.CHART_SURFACE)
                    fig_cmo.update_traces(textposition="outside")
                    st.plotly_chart(fig_cmo, use_container_width=True)
            else:
                st.info("위탁처 컬럼 없음")

    with chg_ai:
        if fchgai.empty:
            st.info("변경 Action Item 데이터가 없습니다.")
        else:
            S.section_header("변경 Action Item 이행 현황")
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
        _linkage_drawer_entry(F.get("changemanagement", pd.DataFrame()), key_suffix="chg",
                              title="변경관리 연계 드릴다운")

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

    S.render_footer()


# ============================================================================
# 탭 5: 고객불만
# ============================================================================

if _render_tab("complain"):
    S.render_header("고객불만 현황")
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
            S.section_header("월별 불만 접수")
            _mc = _month_col_for_df(fcmp)
            if _mc in fcmp.columns:
                cmf = _monthly_weighted_series(fcmp, _mc)
                fig_cm = go.Figure(go.Bar(
                    x=MONTH_LABELS, y=[round(v) for v in cmf["건수"].tolist()],
                    marker_color=CHART_COLORS.get("red", "#d62728"),
                    text=[round(v) for v in cmf["건수"].tolist()], textposition="outside",
                ))
                fig_cm.update_layout(height=300, margin=dict(l=30, r=10, t=10, b=30),
                                      plot_bgcolor=S.CHART_SURFACE)
                st.plotly_chart(fig_cm, use_container_width=True)

    with cmp_type:
        if fcmp.empty:
            st.info("고객불만 데이터가 없습니다.")
        else:
            cc1, cc2 = st.columns(2)
            with cc1:
                S.section_header("불만 유형별 분류")
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
                S.section_header("처리 결과 분포")
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
                        fig_r.update_layout(height=320, plot_bgcolor=S.CHART_SURFACE)
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
                S.section_header(f"{label} 분포")
                fig_v = px.bar(vc.sort_values("건수", ascending=True),
                                x="건수", y=label, orientation="h", text="건수",
                                color_discrete_sequence=[CHART_COLORS.get("purple", "#9467bd")])
                fig_v.update_traces(textposition="outside")
                fig_v.update_layout(height=max(260, 22 * len(vc)),
                                      margin=dict(l=10, r=30, t=10, b=10),
                                      plot_bgcolor=S.CHART_SURFACE)
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
                    height=320, plot_bgcolor=S.CHART_SURFACE,
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
                            plot_bgcolor=S.CHART_SURFACE,
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
        _linkage_drawer_entry(F.get("complain", pd.DataFrame()), key_suffix="cmp",
                              title="고객불만 연계 드릴다운")

    with cmp_tab_raw:
        render_raw_data_section(
            default_project_keys=["complain"],
            key_suffix="complain",
            allow_change=False,
            title="원본 데이터 (고객불만)",
            extra_priority=["불만 유형", "불만 구분", "처리 결과", "원인 분류",
                             "접수일", "처리완료일", "자식 수(전체)", "자식 미종결 수"],
        )

    S.render_footer()


# ============================================================================
# 탭 6: 워크플로우 연계
# ============================================================================

if _render_tab("workflow"):
    S.render_header("워크플로우 연계 분석")
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
        S.section_header("후속 워크플로우 연계 현황")
        ldf = pd.DataFrame(link_data)
        st.dataframe(ldf, use_container_width=True, hide_index=True)

        # ── 품질이슈 → 후속조치 흐름 (2단계 단방향 Sankey, 실제 상위번호 연계 기반) ──
        #   흐름 값은 qms_flow.compute_quality_flow 로 산출(이슈 1건=1버킷 분할 → 양변 균형,
        #   건수기여도 가중). 수집/연계/가중 로직 불변(읽기만). CAPA 중간노드 폐지 → 우측 1열.
        import qms_flow as QF
        _qflows, _qdrill, _qtot = QF.compute_quality_flow(foos, fdev, finv, fcapa, fcapaai)
        _qfl = {k: int(round(v)) for k, v in _qflows.items()}   # 정수화 — 라벨·헤더·카드·교차표 수치 일치
        _issue_total = sum(_qfl.values())
        if _issue_total > 0:
            S.section_header("품질이슈 → 후속조치 흐름")
            _ISSUE_COLOR = {"OOS": "#16244F", "일탈": "#6B79A6"}
            _ACT_COLOR = {"종결·조치불요": "#1F9D63", "조사": "#2F6FED", "CAPA": "#0E9AA7", "CAPA AI": "#7A5AF0"}
            _RIGHT = ["종결·조치불요", "조사", "CAPA", "CAPA AI"]
            _bsum = lambda b: sum(v for (lab, bb), v in _qfl.items() if bb == b)
            _isum = lambda lab: sum(_qfl.get((lab, b), 0) for b in _RIGHT)
            _closed = _bsum("종결·조치불요")
            _inv, _capa, _cai = _bsum("조사"), _bsum("CAPA"), _bsum("CAPA AI")
            _act_started = _issue_total - _closed

            # 단계 헤더 2개
            _h1, _h2 = st.columns(2)
            _h1.markdown(f"**STAGE 1 · 품질이슈 발생**  ·  총 **{_issue_total:,}**건")
            _h2.markdown(f"**STAGE 2 · 후속조치 분기**  ·  조치 착수 **{safe_pct(_act_started, _issue_total):.0f}%** · {_act_started:,}건")
            # 후속조치 4색 범례
            st.markdown(
                "".join(
                    f'<span style="display:inline-block;margin-right:16px;font-size:0.82rem;color:#334">'
                    f'<span style="display:inline-block;width:11px;height:11px;border-radius:3px;'
                    f'background:{_ACT_COLOR[b]};margin-right:5px;vertical-align:middle"></span>{b}</span>'
                    for b in _RIGHT
                ),
                unsafe_allow_html=True,
            )
            # 뷰 토글: 흐름도 / 교차표
            _qview = st.radio("뷰", ["흐름도", "교차표"], horizontal=True,
                              label_visibility="collapsed", key="qflow_view")

            if _qview == "교차표":
                C.data_table(QF.crosstab_quality_flow(foos, fdev, finv, fcapa, fcapaai), height=170)
            else:
                _left = [l for l in ["OOS", "일탈"] if _isum(l) > 0]
                _nodes = _left + _RIGHT
                _idx = {n: i for i, n in enumerate(_nodes)}
                _node_color = [_ISSUE_COLOR[n] for n in _left] + [_ACT_COLOR[n] for n in _RIGHT]
                _node_val = lambda n: _isum(n) if n in _left else _bsum(n)
                _node_lab = [f"{n}<br>{_node_val(n):,}건 · {safe_pct(_node_val(n), _issue_total):.0f}%" for n in _nodes]
                _node_x = [0.001] * len(_left) + [0.999] * len(_RIGHT)

                def _rgba(hexc, a):
                    h = hexc.lstrip("#")
                    return f"rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{a})"

                _src, _tgt, _val, _lcol, _lcustom = [], [], [], [], []
                for lab in _left:
                    for b in _RIGHT:
                        v = _qfl.get((lab, b), 0)
                        if v <= 0:
                            continue
                        _src.append(_idx[lab]); _tgt.append(_idx[b]); _val.append(v)
                        _lcol.append(_rgba(_ACT_COLOR[b], 0.42))
                        _lcustom.append(f"{lab} → {b} · {v:,}건 ({safe_pct(v, _isum(lab)):.1f}% of {lab})")
                _sk = go.Figure(go.Sankey(
                    arrangement="snap",
                    node=dict(pad=26, thickness=22, label=_node_lab, color=_node_color, x=_node_x,
                              line=dict(color="rgba(255,255,255,0.55)", width=0.6),
                              hovertemplate="%{label}<extra></extra>"),
                    link=dict(source=_src, target=_tgt, value=_val, color=_lcol,
                              customdata=_lcustom, hovertemplate="%{customdata}<extra></extra>"),
                ))
                _sk.update_layout(height=390, margin=dict(l=10, r=10, t=10, b=10),
                                  font=dict(family="Pretendard, 'Malgun Gothic', 'NanumGothic', sans-serif",
                                            size=14, color="#0E1B3D"))
                st.plotly_chart(_sk, use_container_width=True, config={"displayModeBar": False})

            # 전환율 카드 4개 (safe_pct)
            _qc = st.columns(4)
            with _qc[0]:
                C.signal_card("조치불요 종결률", f"{safe_pct(_closed, _issue_total):.0f}% · {_closed:,}건", tone="info")
            with _qc[1]:
                C.signal_card("조사 착수율", f"{safe_pct(_inv, _issue_total):.0f}% · {_inv:,}건", tone="info")
            with _qc[2]:
                C.signal_card("CAPA 연계율", f"{safe_pct(_capa + _cai, _issue_total):.0f}% · {_capa + _cai:,}건", tone="info")
            with _qc[3]:
                C.signal_card("AI 보조 비율", f"{safe_pct(_cai, max(_capa + _cai, 1)):.0f}% · {_cai:,}건", tone="info")

            # 드릴다운: 흐름 선택 → 관리번호 목록 + 🔗 연계 보기(기존 드로어 재사용)
            _opts = [f"{lab} → {b}" for (lab, b) in sorted(_qdrill.keys()) if _qdrill.get((lab, b))]
            if _opts:
                with st.expander("🔗 흐름 드릴다운 — 관리번호 목록 (선택 → 연계 추적)"):
                    _sel = st.selectbox("흐름 선택", _opts, key="qflow_drill")
                    _lab, _b = _sel.split(" → ")
                    _ids_list = [str(x) for x in _qdrill.get((_lab, _b), [])]
                    st.caption(f"{_sel} · {len(_ids_list):,}건")
                    C.linkage_drilldown(_ids_list, key="qflow_link", on_select=show_linkage_drawer,
                                        caption="관리번호 선택 → 🔗 로 부모-자식 체인 추적")
        else:
            st.info("흐름을 그릴 이슈 데이터가 없습니다.")
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
                        height=320, plot_bgcolor=S.CHART_SURFACE,
                        margin=dict(l=10, r=10, t=30, b=10),
                        title=dict(text="OOS 건수 vs CAPA 완료율 (월별)", font=dict(size=13)),
                    )
                    st.plotly_chart(fig_corr, use_container_width=True)

                with c_col2:
                    S.section_header("상관계수")
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

    S.render_footer()


# ============================================================================
# 탭 7: 기한관리
# ============================================================================

if _render_tab("deadline"):
    S.render_header("기한 & 일정 관리")
    st.markdown("---")

    # 전 프로젝트 D-day 분포
    S.section_header("전 프로젝트 기한 현황")
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
            S.section_header("기한연장 현황")
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
                    plot_bgcolor=S.CHART_SURFACE, showlegend=True,
                    legend=dict(orientation="h", y=1.05),
                )
                st.plotly_chart(fig_gantt, use_container_width=True)
            except Exception as _ge:
                st.warning(f"간트 차트 렌더링 오류: {_ge}")

        st.divider()
        S.section_header("기한 임박 상세 목록 (D-day ≤ 7일)", "⚠️")
        urgent = dd_all[dd_all["D-day"] <= 7].sort_values("D-day")
        if not urgent.empty:
            # [Task 3.1] 표준 데이터 테이블(상태 Pill·모노 관리번호/D-day).
            C.data_table(urgent.head(30), status=True, height=400)
        else:
            st.success("임박한 기한 항목이 없습니다.")
    else:
        S.empty_state("기한 데이터가 없습니다.", "📭")

    S.render_footer()


# ============================================================================
# 탭 9: 설정
# ============================================================================

if _render_tab("settings"):
    S.render_header("시스템 설정 & 관리")
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
        # [Task 3.1] 표준 데이터 테이블(건수 컬럼은 호출부 '%d건' override). '상태' 컬럼은 수집상태 라벨.
        C.data_table(
            pd.DataFrame(status_data),
            column_config={
                "기한 초과": st.column_config.NumberColumn("기한 초과", format="%d건"),
                "수집 건수": st.column_config.NumberColumn("수집 건수", format="%d건"),
            },
        )
        # Task 1.3: _meta.json(refresh_job 기록) 기반 실제 수집 시각·상태 표기.
        # (datetime.now() 는 '앱 렌더 시각'이라 캐시 분리 후엔 오해 소지가 있어 교체)
        _m = DA.get_refresh_meta()
        if _m.get("source") == "none":
            st.caption("마지막 수집: (refresh_job 미실행) | 화면 로드 소요: "
                       f"{_fetch_elapsed:.1f}s")
        else:
            _badge = "✅" if _m.get("ok_count", 0) >= _m.get("total_count", 0) else "⚠️"
            st.caption(
                f"마지막 수집: {_m.get('last_refresh', '(미상)')} | "
                f"수집 상태: {_badge} {_m.get('ok_count', 0)}/{_m.get('total_count', 0)} | "
                f"화면 로드 소요: {_fetch_elapsed:.1f}s"
            )
            _failed_p = [p for p, v in (_m.get("projects") or {}).items()
                         if isinstance(v, dict) and v.get("status") != "ok"]
            if _failed_p:
                st.warning(
                    f"수집 실패 {len(_failed_p)}건(옛 캐시로 표시 중): "
                    + ", ".join(_failed_p)
                )

    with cfg_tab2:
        S.section_header("캐시 관리")
        _m2 = DA.get_refresh_meta()
        _last2 = _m2.get("last_refresh") if _m2.get("source") != "none" else "(refresh_job 미실행)"
        st.info(f"데이터 소스: 로컬 캐시(.qms_cache parquet) | 마지막 수집: {_last2}")
        c_btn1, c_btn2 = st.columns(2)
        with c_btn1:
            # 메모이즈(@st.cache_data)만 비우고 디스크 캐시(parquet)는 보존 → 다음 렌더에서 재읽기.
            if st.button("♻️ 화면 캐시 새로고침", use_container_width=True):
                st.cache_data.clear()
                st.session_state.pop("_cache_fetch_time", None)
                st.success("화면 캐시를 비웠습니다. 페이지를 새로고침하세요. (디스크 데이터는 유지)")
        with c_btn2:
            # Task 1.3: 동기 갱신(80s 블로킹) 금지 → refresh_job 백그라운드 트리거.
            if st.button("↻ 백그라운드 데이터 갱신", use_container_width=True, type="primary"):
                if _trigger_refresh_job_background():
                    st.cache_data.clear()
                    st.info("갱신 시작됨 — 수집 완료 후 새로고침하면 반영됩니다. "
                            "(정규 갱신은 스케줄러가 담당)")
                else:
                    st.warning("갱신 시작 실패 — 스케줄러(Task 1.4) 또는 수동 실행을 사용하세요.")
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
        # ── [①] 수신자 명부 보기 (읽기전용, 드롭다운·기본 숨김) — 사내 주소록(이름·이메일) ──
        try:
            from qms_pro.services import alert_service as _al
            _n2e, _rdf = _al.load_alert_roster()
        except Exception:
            _n2e, _rdf = {}, None
        if _rdf is not None and not _rdf.empty:
            with st.expander(f"📒 수신자 명부 보기 ({len(_rdf):,}명) — 읽기전용", expanded=False):
                _rc1, _rc2 = st.columns([2, 1])
                with _rc1:
                    st.dataframe(_rdf, use_container_width=True, hide_index=True, height=300)
                with _rc2:
                    _pick = st.selectbox("이름으로 빠른 조회", [""] + _rdf["이름"].tolist(), key="cfg_roster_pick")
                    if _pick:
                        st.text_input("이메일", value=_n2e.get(_pick, ""), disabled=True, key="cfg_roster_email")
                st.caption("※ 읽기 전용 — 명부 등록·수정은 개발자(품질부문 AI TF)에게 요청하세요.")
        else:
            with st.expander("📒 수신자 명부 보기 — 미등록", expanded=False):
                S.empty_state("알림 명부가 미등록 상태입니다(주소록 파일 없음/비어 있음).")

        st.divider()
        st.markdown("**기한 초과 알림 — 라우팅 미리보기 & 발송**")
        _excl_default = (os.environ.get("QMS_ALERT_EXCLUDE_NAMES", "")
                         or _load_dotenv_env().get("QMS_ALERT_EXCLUDE_NAMES", ""))
        _excl_in = st.text_input(
            "제외 명단 (퇴사자 등 · 콤마 구분)", value=_excl_default,
            key="cfg_exclude_names",
            help="여기 적힌 이름은 담당자/등록자 후보 및 '미매칭' 집계에서 제외됩니다(예: 퇴사자). 기본값은 .env QMS_ALERT_EXCLUDE_NAMES.",
        )
        os.environ["QMS_ALERT_EXCLUDE_NAMES"] = _excl_in
        _excl_list = [a.strip() for a in _excl_in.split(",") if a.strip()]
        _ar1, _ar2 = st.columns(2)
        with _ar1:
            if st.button("🔎 라우팅 미리보기 (dry-run · 발송 없음)", use_container_width=True, key="cfg_route_preview"):
                try:
                    from qms_pro.services import alert_service as _al
                    _n2e2, _ = _al.load_alert_roster()
                    st.session_state["_route_preview"] = _al.preview_overdue_routing(
                        F, PROJECT_META, _n2e2, exclude_names=_excl_list)
                except Exception as _e:
                    st.error(f"미리보기 실패: {_e}")
        with _ar2:
            if st.button("🚨 지금 기한 초과 알림 발송 (콤마목록)", use_container_width=True):
                try:
                    from qms_pro.services import alert_service as _al
                    _al.run_overdue_alert(F, PROJECT_META)
                    st.success("알림 발송 완료")
                except Exception as e:
                    st.error(f"알림 발송 실패: {e}")
        # ── [②] dry-run 미리보기 결과 (실제 발송 0건) ──
        _rep = st.session_state.get("_route_preview")
        if _rep:
            st.markdown("##### 🔎 담당자·등록자 라우팅 미리보기 (dry-run — 실제 발송 0건)")
            _mc = st.columns(4)
            _mc[0].metric("대상 건(미완료·미취소·D-day<0)", _rep["items_total"])
            _mc[1].metric("개인 수신자", _rep["recipients"])
            _mc[2].metric("미매칭 이름(종)", _rep["unmatched_unique"])
            _mc[3].metric("관리자 fallback 건", _rep["admin_fallback_count"])
            if _rep.get("excluded_names"):
                st.caption(f"🚫 제외 적용(퇴사자 등): {', '.join(_rep['excluded_names'])}")
            with st.expander("프로젝트별 사용 컬럼 (담당자/등록자 자동 탐색 결과)"):
                st.dataframe(pd.DataFrame([
                    {"프로젝트": k, "담당자 컬럼": v["담당자"] or "—", "등록자 컬럼": v["등록자"] or "—"}
                    for k, v in _rep["columns_used"].items()
                ]), use_container_width=True, hide_index=True)
            if _rep["person_table"]:
                st.caption("수신자별 개인 다이제스트(미리보기 · 사람당 1통 예정)")
                st.dataframe(pd.DataFrame([
                    {"이름": p["이름"], "이메일": p["이메일"], "건수": p["건수"],
                     "역할": ", ".join(f"{k} {v}" for k, v in p["역할분포"].items())}
                    for p in _rep["person_table"]
                ]), use_container_width=True, hide_index=True, height=220)
            if _rep["unmatched_names"]:
                _un = ", ".join(f"{n}({c})" for n, c in _rep["unmatched_names"].items())
                st.warning(f"⚠ 명부 미매칭 담당자/등록자 {_rep['unmatched_unique']}종: {_un}\n\n"
                           "→ 1) 해당 담당자에게 알림을 전달, 2) 명부 등록은 개발자(품질부문 AI TF)에게 요청.")
            st.caption("※ 1차는 미리보기 전용입니다 — 실제 개인 발송은 다음 단계 승인 후 활성화됩니다. "
                       "현재 발송 경로(콤마목록)는 그대로 보존됩니다.")

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

    S.render_footer()


# ============================================================================
# 종결순서 점검 패널 (Task 2.5) — 워크스페이스별 1회, 소유 레코드 기준.
# _active_ws 로 게이트(현재 워크스페이스가 qc/qa/actions 일 때만 그 소유 점검을 하단에 표시).
# 어느 sub-view 에 있든 워크스페이스 하단에 일관 노출 → '분산' 요건 충족.
# ============================================================================
if _active_ws in _WS_OWNED_PROJECTS:
    st.markdown("---")
    render_closure_check(_active_ws, F, key_suffix=f"ws_{_active_ws}")


# ============================================================================
# 신설 워크스페이스 자리 확보 (Task 2.1) — 내용은 Phase 3에서 구현
#  · 제품·배치품질: APQR(품목×연도) + 출하 전 확인(lot) — Task 3.3
#  · 알림·모니터링: 룰 기반 알림 센터 — Task 3.4 (현 설정탭 알림설정이 모태)
# 레일에는 지금 노출하되, 본문은 "준비 중 + 예정 내용" 안내로 자리만 잡는다.
# ============================================================================
if _render_tab("product_apqr"):
    S.render_header("제품·배치 품질", "APQR · 품목 × 연도")
    st.caption("※ 모니터링 보조 — 정식 출하 판정 시스템이 아닙니다. (lot 처분 PASS/HOLD 는 상단 'lot 처분' 탭)")
    st.divider()

    _ATTR_NAME = "품목명_귀속"
    _ATTR_SRC = "귀속출처"

    # ════════════════════════════════════════════════════════════════════
    # ① 품목 귀속 커버리지 (전체 데이터 기준 — 체인 전파 정직 표기)
    #   원본 품목 컬럼 불변, attribution.py 파생(귀속출처)만 사용. 고유 관리번호 기준.
    # ════════════════════════════════════════════════════════════════════
    S.section_header("품목 귀속 커버리지 (전체 데이터 기준)", "①")
    _src_uniq: dict[str, int] = {}
    _seen_prno: set[str] = set()
    for _k, _d in ALL_DFS.items():
        if _d is None or _d.empty or "관리번호" not in _d.columns or _ATTR_SRC not in _d.columns:
            continue
        _b = _d.drop_duplicates(subset=["관리번호"])
        for _prno, _s in zip(_b["관리번호"].astype(str), _b[_ATTR_SRC]):
            if _prno in _seen_prno:
                continue
            _seen_prno.add(_prno)
            _src_uniq[_s] = _src_uniq.get(_s, 0) + 1
    _self_n = _src_uniq.get("자체보유", 0)
    _inh_n = _src_uniq.get("상속", 0)
    _none_n = _src_uniq.get("미분류", 0)
    _multi_n = _src_uniq.get("복수(미분류)", 0)
    _cov1, _cov2, _cov3 = st.columns(3)
    with _cov1:
        C.signal_card("품목 귀속 (자체+상속)", f"{_self_n + _inh_n:,}건", tone="ok", icon="",
                      sub=f"자체보유 {_self_n:,} · 상속(체인) {_inh_n:,}")
    with _cov2:
        C.signal_card("전사/미분류", f"{_none_n:,}건", tone="neutral", icon="",
                      sub="품목 앵커 없음(변경계보·고립)")
    with _cov3:
        C.signal_card("복수(미분류)", f"{_multi_n:,}건", tone=("warn" if _multi_n else "neutral"), icon="",
                      sub="도달 조상 품목 충돌")
    st.caption("귀속출처: **자체보유**=레코드 자체 품목 · **상속**=품질계보 조상(OOS·일탈)에서 체인 전파 · "
               "**미분류**=귀속 불가. 원본 `품목코드`/`품목명` 컬럼은 불변, 파생(`품목명_귀속`)만 집계에 사용.")

    st.divider()

    # ════════════════════════════════════════════════════════════════════
    # ② 품목별 품질 이벤트 매트릭스 (선택 연도, 건수기여도 합)
    #   행=품목명_귀속(미분류 포함) · 열=6 품질 카테고리(기존 프로젝트 분류 재사용) + 합계.
    #   값=_wgroupby(건수기여도 합, 없으면 size) — 기존 가중 집계 헬퍼 재사용.
    # ════════════════════════════════════════════════════════════════════
    _yr_label = ", ".join(str(y) for y in selected_years) if selected_years else "전체"
    S.section_header(f"품목별 품질 이벤트 — {_yr_label} (건수기여도 합)", "②")
    _APQR_CATS = [
        ("OOS", ["oos"]),
        ("일탈", ["deviation", "deviationoutsourcing", "deviationactionitem"]),
        ("조사", ["investigation"]),
        ("CAPA", ["capa", "capaactionitem", "actionitem"]),
        ("변경", ["changemanagement", "changeactionitem", "changeimpactassessment", "changeoutsourcing"]),
        ("불만", ["complain"]),
    ]
    # 카테고리별 집계는 **프로젝트별 _wgroupby 후 합산**한다(신규 집계 로직 0, 기존 헬퍼 재사용).
    # 카테고리 안에 건수기여도 보유(OOS/일탈)·미보유(조사/CAPA…) 프로젝트가 섞일 때 concat 후
    # 집계하면 미보유 행의 NaN 건수기여도가 0 처리되어 누락되므로, 프로젝트 단위로 집계해 합친다.
    _cat_map: dict[str, dict] = {}
    _items: set[str] = set()
    for _cat, _keys in _APQR_CATS:
        _acc: dict[str, int] = {}
        for _k in _keys:
            _d = F.get(_k)
            if _d is None or _d.empty or _ATTR_NAME not in _d.columns:
                continue
            _g = _wgroupby(_d, _ATTR_NAME, name="건수")   # 프로젝트별 건수기여도 합(없으면 size)
            for _it, _v in zip(_g[_ATTR_NAME].astype(str), _g["건수"]):
                _acc[_it] = _acc.get(_it, 0) + int(_v)
                _items.add(_it)
        _cat_map[_cat] = _acc

    if not _items:
        S.empty_state("선택한 연도에 해당하는 품질 이벤트가 없습니다.")
    else:
        _rows = []
        for _it in _items:
            _row = {"품목명": _it or "(품목명 없음)"}
            _tot = 0
            for _cat, _ in _APQR_CATS:
                _v = int(_cat_map.get(_cat, {}).get(_it, 0))
                _row[_cat] = _v
                _tot += _v
            _row["합계"] = _tot
            _rows.append(_row)
        _mat = pd.DataFrame(_rows).sort_values("합계", ascending=False).reset_index(drop=True)
        st.caption(f"품목 {len(_mat)}행(전사/미분류 포함) · OOS·일탈은 건수기여도 가중, 그 외는 건수. "
                   "변경·불만은 품목 귀속 약함 → 대부분 전사/미분류.")
        C.data_table(
            _mat, height=420,
            column_config={_c: st.column_config.NumberColumn(_c, format="%d")
                           for _c in ["OOS", "일탈", "조사", "CAPA", "변경", "불만", "합계"]},
        )

        st.divider()

        # ════════════════════════════════════════════════════════════════
        # ③ 품목 드릴다운 — 이벤트 목록(상태 Pill·D-day·관리번호) + 🔗 연계 추적
        # ════════════════════════════════════════════════════════════════
        S.section_header("품목 드릴다운 — 이벤트 추적", "③")
        _sel_item = st.selectbox("품목 선택", _mat["품목명"].tolist(), key="apqr_item_sel")
        _sel_key = "" if _sel_item == "(품목명 없음)" else _sel_item
        # 프로젝트별 스키마가 달라 전열 concat 시 all-NA 컬럼 경고 → 표시 컬럼만 투영 후 concat.
        _EV_COLS = ["관리번호", "프로젝트", "제목", "작성팀", "기한일", "D-day", "진행상태", "완료여부"]
        _ev_frames = []
        for _cat, _keys in _APQR_CATS:
            for _k in _keys:
                _d = F.get(_k)
                if _d is None or _d.empty or _ATTR_NAME not in _d.columns:
                    continue
                _sub = _d[_d[_ATTR_NAME].astype(str) == str(_sel_key)]
                if not _sub.empty:
                    _sub2 = _sub[[c for c in _EV_COLS if c in _sub.columns]].copy()
                    _sub2.insert(0, "카테고리", _cat)
                    _ev_frames.append(_sub2)
        if _ev_frames:
            # 이종 프로젝트 스키마 정렬 시 all-NA 컬럼 dtype FutureWarning(무해) — 이 concat 한정 억제.
            import warnings as _w
            with _w.catch_warnings():
                _w.simplefilter("ignore", FutureWarning)
                _ev = pd.concat(_ev_frames, ignore_index=True)
            _ev_cols = [c for c in ["카테고리", "관리번호", "프로젝트", "제목", "작성팀", "기한일", "D-day", "진행상태"]
                        if c in _ev.columns]
            _ev_disp = _ev[_ev_cols].sort_values("D-day") if "D-day" in _ev.columns else _ev[_ev_cols]
            st.caption(f"**{_sel_item}** — 이벤트 {len(_ev)}건(선택 연도)")
            C.data_table(_ev_disp, status=True, height=340)
            _ev_prnos = _ev["관리번호"].astype(str).tolist() if "관리번호" in _ev.columns else []
            C.linkage_drilldown(_ev_prnos, key="apqr_item", on_select=show_linkage_drawer,
                                caption="관리번호 선택 → 🔗 로 부모-자식 체인·종결여부 추적")
        else:
            S.empty_state("선택한 품목의 이벤트가 없습니다.")

    S.render_footer()


# ============================================================================
# 제품·배치품질 — lot 처분(PASS/HOLD) sub-view (Task 3.3b)
#   lot 키=제조번호_귀속(3.3a attribution). 판정=disposition.judge_lot_dispositions
#   (적합/보류/부적합/미상, 최악 우선). 표준 컴포넌트만 사용. 건수=건수기여도 합.
# ============================================================================
if _render_tab("product_lot"):
    S.render_header("제품·배치 품질", "lot 처분 — 출하 전 확인 (PASS/HOLD 보조)")
    st.caption("※ 모니터링 보조 — 정식 출하 판정 시스템이 아닙니다. 최악 우선: 부적합 > 보류 > 적합 > 미상.")
    st.divider()

    _LOT_ATTR = "제조번호_귀속"

    # ════════════════════════════════════════════════════════════════════
    # ① lot 커버리지(전체 기준) + 처분 분포(선택 연도)
    # ════════════════════════════════════════════════════════════════════
    S.section_header("lot 커버리지 · 처분 분포", "①")
    # 커버리지(전체 데이터, 고유 관리번호): lot 보유(자체/상속)·미상. 자체=원본 제조번호 보유.
    _lot_total = _lot_self = _lot_none = 0
    _lseen: set[str] = set()
    for _k, _d in ALL_DFS.items():
        if _d is None or _d.empty or "관리번호" not in _d.columns or _LOT_ATTR not in _d.columns:
            continue
        _b = _d.drop_duplicates(subset=["관리번호"])
        _prnos = _b["관리번호"].astype(str)
        _lots = _b[_LOT_ATTR].astype(str)
        _origs = _b["제조번호"].astype(str) if "제조번호" in _b.columns else None
        for _i in range(len(_b)):
            _p = _prnos.iat[_i]
            if _p in _lseen:
                continue
            _lseen.add(_p)
            _lv = _lots.iat[_i].strip()
            if _lv and _lv.lower() not in ("nan", "none"):
                _lot_total += 1
                _ov = _origs.iat[_i].strip() if _origs is not None else ""
                if _ov and _ov.lower() not in ("nan", "none"):
                    _lot_self += 1
            else:
                _lot_none += 1
    _lot_inh = _lot_total - _lot_self

    _disp = _judge_lot_dispositions(F)             # 선택 연도(글로벌 필터) 기준
    _dist = _disposition_distribution(_disp)
    _r1 = st.columns(2)
    with _r1[0]:
        C.signal_card("lot 보유 (전체)", f"{_lot_total:,}건", tone="info", icon="",
                      sub=f"자체보유 {_lot_self:,} · 상속(체인) {_lot_inh:,}")
    with _r1[1]:
        C.signal_card("lot 미상 (전체)", f"{_lot_none:,}건", tone="neutral", icon="",
                      sub="제조번호 없음 — 처분 집계 제외(추측 금지)")
    _r2 = st.columns(4)
    _disp_tone = {"부적합": "danger", "보류": "warn", "적합": "ok", "미상": "neutral"}
    _disp_sub = {"부적합": "OOS 기준일탈 부적합", "보류": "미종결 존재",
                 "적합": "전 종결 + 적합", "미상": "판정 정보 부족"}
    for _i, _lab in enumerate(_DISP_ORDER):
        with _r2[_i]:
            C.signal_card(f"{_lab} lot", f"{_dist.get(_lab, 0)}", tone=_disp_tone[_lab], icon="",
                          sub=_disp_sub[_lab])
    st.caption("커버리지=전체 데이터 기준 · 처분 분포·표=선택 연도(글로벌 필터) 이벤트 기준. "
               "판정 소스: OOS `기준 일탈 최종 결과`(적합/부적합) + `최종 종결 여부(체인)`. lot 미상은 처분 집계 제외.")

    st.divider()

    # ════════════════════════════════════════════════════════════════════
    # ② lot 처분 표 (행=제조번호, 처분 Pill·품목명·관련건수·OOS수·미종결수)
    # ════════════════════════════════════════════════════════════════════
    _yr_l = ", ".join(str(y) for y in selected_years) if selected_years else "전체"
    S.section_header(f"lot 처분 표 — {_yr_l}", "②")
    if _disp.empty:
        S.empty_state("선택한 연도에 lot(제조번호) 보유 이벤트가 없습니다.")
    else:
        _disp_show = _disp.copy()
        _disp_show["처분"] = _disp_show["처분"].map(C.disposition_pill_label)
        _disp_show = _disp_show.rename(columns={"제조번호_귀속": "제조번호(lot)", "품목명_귀속": "품목명"})
        st.caption(f"lot {len(_disp_show)}개 · 관련건수=건수기여도 합 · 처분 최악 우선(부적합>보류>적합>미상) 정렬.")
        C.data_table(
            _disp_show, height=420,
            column_config={
                "처분": st.column_config.TextColumn("처분", help="적합🟢/보류🟠/부적합🔴/미상⚪", width="small"),
                **{_c: st.column_config.NumberColumn(_c, format="%d") for _c in ["관련건수", "OOS수", "미종결수"]},
            },
        )

        st.divider()

        # ════════════════════════════════════════════════════════════════
        # ③ lot 드릴다운 — 관련 이벤트 목록(상태 Pill·결과·종결) + 🔗 연계 추적
        # ════════════════════════════════════════════════════════════════
        S.section_header("lot 드릴다운 — 관련 이벤트", "③")
        _sel_lot = st.selectbox("제조번호(lot) 선택", _disp[_DISP_LOT_COL].tolist(), key="lot_disp_sel")
        _LEV_COLS = ["관리번호", "프로젝트", "제목", "작성팀", "기한일", "D-day", "진행상태",
                     "완료여부", "기준 일탈 최종 결과", "최종 종결 여부(체인)"]
        _lev_frames = []
        for _k, _d in F.items():
            if _d is None or _d.empty or _LOT_ATTR not in _d.columns:
                continue
            _sub = _d[_d[_LOT_ATTR].astype(str).str.strip() == str(_sel_lot)]
            if not _sub.empty:
                _lev_frames.append(_sub[[c for c in _LEV_COLS if c in _sub.columns]].copy())
        if _lev_frames:
            import warnings as _w2
            with _w2.catch_warnings():
                _w2.simplefilter("ignore", FutureWarning)
                _lev = pd.concat(_lev_frames, ignore_index=True)
            _lev_cols = [c for c in ["관리번호", "프로젝트", "제목", "작성팀", "기한일", "D-day",
                                     "진행상태", "기준 일탈 최종 결과", "최종 종결 여부(체인)"] if c in _lev.columns]
            _lev_disp = _lev[_lev_cols].sort_values("D-day") if "D-day" in _lev.columns else _lev[_lev_cols]
            st.caption(f"lot **{_sel_lot}** — 관련 이벤트 {len(_lev)}건(선택 연도)")
            C.data_table(_lev_disp, status=True, height=320)
            _lev_prnos = _lev["관리번호"].astype(str).tolist() if "관리번호" in _lev.columns else []
            C.linkage_drilldown(_lev_prnos, key="lot_disp", on_select=show_linkage_drawer,
                                caption="관리번호 선택 → 🔗 로 부모-자식 체인·종결여부 추적")
        else:
            S.empty_state("선택한 lot 의 관련 이벤트가 없습니다.")

    S.render_footer()

if _render_tab("alerts_new"):
    S.render_header("알림·모니터링", "기한 위험 · 신규 OOS (읽기 전용 모니터링)")
    st.caption("※ 모니터링 보조 — 이 화면은 현황 표시 전용입니다. 알림 발송·규칙 편집은 "
               "데이터·설정 → 알림 설정 탭 또는 스케줄러가 담당합니다.")
    st.divider()

    # ════════════════════════════════════════════════════════════════════
    # ① 기한 위험 모니터링 (전 프로젝트 D-day 재사용 — 신규 로직 0, 건수기여도 합)
    # ════════════════════════════════════════════════════════════════════
    S.section_header("기한 위험 모니터링 (전 프로젝트)", "①")
    # [기한 초과 통일] 살아있는(미완료·미취소) 건만 — active_mask(완료 + 취소 제외)로 KPI·배지와 단일 기준.
    #   D-day 만 보면 이미 완료·취소된 과거 건이 '기한 초과'로 잡혀 카운트·표(D-day 오름차순)를 점령한다.
    #   각 프로젝트 df 를 active(_F_open)로 먼저 거른 뒤 카운트·표를 산출(신규 순수함수 active_mask 재사용).
    _F_open = {
        _pk: (_d[active_mask(_d)] if (_d is not None and not _d.empty) else _d)
        for _pk, _d in F.items()
    }
    _over_w = round(sum(weighted_metric_overdue(_d) for _d in _F_open.values()))

    def _wcount_dday(_lo, _hi) -> int:
        _t = 0
        for _d in _F_open.values():
            if _d is None or _d.empty or "D-day" not in _d.columns:
                continue
            _dd = _num_series(_d["D-day"], default=99999)
            _t += _wcount(_d, (_dd >= _lo) & (_dd <= _hi))
        return _t

    _imm3 = _wcount_dday(0, 3)
    _imm7 = _wcount_dday(0, 7)
    _ac = st.columns(3)
    with _ac[0]:
        C.signal_card("기한 초과", f"{_over_w:,}건", tone="danger", icon="", sub="D-day < 0 (전사)")
    with _ac[1]:
        C.signal_card("D-3 임박", f"{_imm3:,}건", tone="warn", icon="", sub="0 ≤ D-day ≤ 3")
    with _ac[2]:
        C.signal_card("D-7 임박", f"{_imm7:,}건", tone="warn", icon="", sub="0 ≤ D-day ≤ 7")

    _risk_frames = []
    for _pk, _d in _F_open.items():   # [버그수정] 미완료(_F_open)에서만 추출 → 표에 '완료' 행 미노출
        if _d is None or _d.empty or "D-day" not in _d.columns:
            continue
        _dd = _num_series(_d["D-day"], default=99999)
        _sub = _d[_dd <= 7]   # 초과 + 7일 이내 임박(완료 제외)
        if not _sub.empty:
            _keep = [c for c in ["관리번호", "제목", "작성팀", "기한일", "D-day", "진행상태"] if c in _sub.columns]
            _f = _sub[_keep].copy()
            _f.insert(0, "프로젝트", PROJECT_META[_pk]["label"])
            _risk_frames.append(_f)
    if _risk_frames:
        import warnings as _wa
        with _wa.catch_warnings():
            _wa.simplefilter("ignore", FutureWarning)
            _risk = pd.concat(_risk_frames, ignore_index=True)
        _risk = _risk.sort_values("D-day").head(100)
        st.caption(f"기한 위험(초과 + 7일 이내) 상위 {len(_risk)}건 — D-day 오름차순")
        C.data_table(_risk, status=True, height=360)
        C.linkage_drilldown(_risk["관리번호"].astype(str).tolist(), key="alert_risk",
                            on_select=show_linkage_drawer)
    else:
        S.empty_state("기한 위험 항목이 없습니다.")

    st.divider()

    # ════════════════════════════════════════════════════════════════════
    # ② 신규 OOS 모니터링 (최근 30일 · 등록일 기준 — 기존 데이터 재사용)
    # ════════════════════════════════════════════════════════════════════
    _NEW_DAYS = 30
    S.section_header(f"신규 OOS 모니터링 (최근 {_NEW_DAYS}일 · 등록일)", "②")
    _new_oos = pd.DataFrame()
    if not foos.empty and "등록일" in foos.columns:
        _reg = pd.to_datetime(foos["등록일"], errors="coerce")
        _cut = pd.Timestamp(datetime.now().date()) - pd.Timedelta(days=_NEW_DAYS)
        _new_oos = foos[_reg.notna() & (_reg >= _cut)].copy()
    if not _new_oos.empty:
        _new_w = _wcount(_new_oos)
        _open_mask = None
        if "최종 종결 여부(체인)" in _new_oos.columns:
            _open_mask = ~_new_oos["최종 종결 여부(체인)"].map(
                lambda v: v is True or str(v).strip().lower() in ("true", "1"))
        _new_open = _wcount(_new_oos, _open_mask) if _open_mask is not None else 0
        _nc = st.columns(2)
        with _nc[0]:
            C.signal_card(f"최근 {_NEW_DAYS}일 신규 OOS", f"{_new_w:,}건", tone="info", icon="",
                          sub="등록일 기준 · 건수기여도 합")
        with _nc[1]:
            C.signal_card("신규 OOS 미종결", f"{_new_open:,}건", tone="warn", icon="",
                          sub="최종 종결 여부(체인) == False")
        _oos_cols = [c for c in ["관리번호", "제목", "품목명", "제조번호", "이상발생 원인",
                                 "기준 일탈 최종 결과", "진행상태", "D-day"] if c in _new_oos.columns]
        _new_sorted = (_new_oos.sort_values("등록일", ascending=False)
                       if "등록일" in _new_oos.columns else _new_oos)
        C.data_table(_new_sorted[_oos_cols], status=True, height=320)
        C.linkage_drilldown(_new_oos["관리번호"].astype(str).tolist(), key="alert_newoos",
                            on_select=show_linkage_drawer)
    else:
        S.empty_state(f"최근 {_NEW_DAYS}일 신규 OOS 가 없습니다(선택 연도 기준).")

    st.divider()

    # ════════════════════════════════════════════════════════════════════
    # ③ 알림 규칙·채널 현황 (READ-ONLY — 비밀값/수신자 주소 비표시, 발송·편집 없음)
    # ════════════════════════════════════════════════════════════════════
    S.section_header("알림 규칙·채널 현황 (읽기 전용)", "③")
    _slack_on = bool(os.environ.get("QMS_SLACK_WEBHOOK", "").strip())
    _email_on = bool(os.environ.get("QMS_SMTP_USER", "").strip())
    _to_on = bool(os.environ.get("QMS_ALERT_TO", "").strip())
    _status_rows = [
        {"항목": "규칙", "현황": "기한 초과 (D-day < 0)", "소스": "run_overdue_alert(threshold_days=0)"},
        {"항목": "Slack 채널", "현황": "설정됨" if _slack_on else "미설정", "소스": "QMS_SLACK_WEBHOOK (.env)"},
        {"항목": "이메일 채널", "현황": "설정됨" if _email_on else "미설정", "소스": "QMS_SMTP_USER (.env)"},
        {"항목": "이메일 수신자", "현황": "설정됨" if _to_on else "미설정", "소스": "QMS_ALERT_TO (.env)"},
    ]
    C.data_table(pd.DataFrame(_status_rows))
    st.caption("※ 비밀값·수신자 주소는 표시하지 않습니다(설정 여부만). 발송·규칙 편집·`.env` 쓰기는 "
               "이 화면에서 하지 않습니다 — 데이터·설정 → 알림 설정 탭 또는 스케줄러가 담당.")

    S.render_footer()
