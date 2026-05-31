# -*- coding: utf-8 -*-
"""
QMS 통합 대시보드 — 디자인 시스템 (단일 출처)

import qms_styles as S
S.apply_global_css()          # 앱 시작 시 1회 호출
S.metric_card(...)
S.section_header(...)
S.empty_state(...)
"""

import streamlit as st

# ─── 색상 토큰 ────────────────────────────────────────────────────────────────

PRIMARY   = "#0d1b3e"
PRIMARY_L = "#1a3a6c"
ACCENT    = "#3f51b5"
LIGHT_BG  = "#f8f9fa"
BORDER    = "#e0e0e0"

# 상태 색상
GREEN  = "#27ae60"
YELLOW = "#f39c12"
RED    = "#e74c3c"
ORANGE = "#fb8c00"

# 차트 팔레트
CHART_COLORS = {
    "primary":    PRIMARY,
    "blue":       ACCENT,
    "light_blue": "#5c6bc0",
    "bar":        "#4a5899",
    "red":        "#e53935",
    "orange":     ORANGE,
    "green":      GREEN,
    "gray":       "#9e9e9e",
    "dark_gray":  "#616161",
    "purple":     "#8e24aa",
    "teal":       "#00897b",
    "brown":      "#795548",
}

# 다크모드용 색상 오버라이드
_DARK = {
    "bg":          "#0e1117",
    "card_bg":     "#1e2130",
    "text":        "#e8eaf6",
    "sub_text":    "#9fa8da",
    "border":      "#2e3250",
    "header_grad": "linear-gradient(135deg, #0d1b3e, #0a2342)",
    "tab_active":  "#3f51b5",
    "tab_bg":      "#1a1f36",
    "grid":        "#1e2130",
}

_LIGHT = {
    "bg":          "#ffffff",
    "card_bg":     "#f8f9fa",
    "text":        "#212121",
    "sub_text":    "#666666",
    "border":      "#e0e0e0",
    "header_grad": "linear-gradient(135deg, #0d1b3e, #1a3a6c)",
    "tab_active":  PRIMARY,
    "tab_bg":      "#ffffff",
    "grid":        "#f0f0f0",
}


def _theme() -> dict:
    dark = st.session_state.get("dark_mode", False)
    return _DARK if dark else _LIGHT


def apply_global_css() -> None:
    """앱 시작 시 1회 호출. 전체 CSS 주입."""
    T = _theme()
    st.markdown(f"""
<style>
    /* ─── 기본 리셋 ─────────────────────────────────────── */
    #MainMenu {{ visibility: hidden; }}
    footer {{ visibility: hidden; }}
    .block-container {{
        padding-top: 0.5rem;
        max-width: 98%;
        background: {T['bg']};
    }}
    [data-testid="stHeader"] {{ background: transparent !important; }}
    /* stToolbar 전체 숨김 금지: 좁은 화면·원격 접속 시 사이드바 열기 컨트롤이 툴바 안에만
       있을 수 있어 display:none 이면 토글이 완전히 사라짐 (로컬 넓은 창에서는 펼쳐진 채로만 써서 문제 없음). */
    [data-testid="stDecoration"] {{ display: none !important; }}

    /* 사이드바 토글은 inject_sidebar_toggle() 단일 버튼으로만 처리 (네이티브는 JS에서 숨김). */

    .stApp [data-testid="stMain"] .block-container {{
        padding-top: 1rem !important;
    }}
    body, .stApp {{
        background: {T['bg']};
        color: {T['text']};
    }}

    /* ─── 헤더 ──────────────────────────────────────────── */
    .qms-header {{
        background: {T['header_grad']};
        color: white;
        padding: 16px 28px;
        border-radius: 12px;
        margin-bottom: 14px;
        box-shadow: 0 4px 14px rgba(0,0,0,0.18);
    }}
    .qms-header h1 {{ margin: 0; font-size: 1.45rem; letter-spacing: -0.3px; }}
    .qms-header p  {{ margin: 3px 0 0 0; font-size: 0.82rem; opacity: 0.8; }}

    /* ─── 섹션 헤더 ─────────────────────────────────────── */
    .qms-section-header {{
        border-left: 4px solid {ACCENT};
        padding: 6px 0 6px 12px;
        margin: 14px 0 10px 0;
        font-size: 1rem;
        font-weight: 700;
        color: {T['text']};
        background: {'rgba(63,81,181,0.06)' if not st.session_state.get('dark_mode') else 'rgba(63,81,181,0.12)'};
        border-radius: 0 6px 6px 0;
    }}

    /* ─── 메트릭 카드 ───────────────────────────────────── */
    div[data-testid="stMetric"] {{
        background: {T['card_bg']};
        border-radius: 10px;
        padding: 14px 18px;
        border-left: 4px solid {ACCENT};
        box-shadow: 0 2px 8px rgba(0,0,0,{'0.10' if st.session_state.get('dark_mode') else '0.06'});
        transition: box-shadow .18s;
    }}
    div[data-testid="stMetric"]:hover {{
        box-shadow: 0 4px 16px rgba(0,0,0,0.16);
    }}
    div[data-testid="stMetric"] label {{
        font-size: 0.77rem;
        color: {T['sub_text']};
        font-weight: 600;
        letter-spacing: 0.3px;
    }}
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {{
        font-size: 1.42rem;
        font-weight: 700;
        color: {T['text']};
    }}
    div[data-testid="stMetric"] div[data-testid="stMetricDelta"] {{
        font-size: 0.78rem;
    }}
    .kpi-card-good {{ border-left-color: {GREEN}  !important; }}
    .kpi-card-warn {{ border-left-color: {YELLOW} !important; }}
    .kpi-card-bad  {{ border-left-color: {RED}    !important; }}

    /* ─── 탭 ────────────────────────────────────────────── */
    [data-testid="stMain"] .stTabs:first-of-type > div:first-child {{
        position: sticky;
        top: 0;
        z-index: 100;
        background: {T['tab_bg']};
        padding-top: 4px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }}
    .stTabs [data-baseweb="tab-list"] {{
        gap: 3px;
        border-bottom: 2px solid {T['border']};
        overflow-x: auto;
        flex-wrap: nowrap;
        background: {T['tab_bg']};
    }}
    .stTabs [data-baseweb="tab"] {{
        padding: 9px 16px !important;
        min-height: 42px !important;
        border-radius: 8px 8px 0 0;
        font-weight: 600;
        font-size: 0.88rem;
        color: {T['text']} !important;
        background: transparent;
        white-space: nowrap;
        transition: background .15s;
    }}
    .stTabs [data-baseweb="tab"] p,
    .stTabs [data-baseweb="tab"] div[data-testid="stMarkdownContainer"],
    .stTabs [data-baseweb="tab"] span {{
        color: inherit !important;
        font-size: inherit !important;
        font-weight: inherit !important;
        margin: 0 !important;
        line-height: 1.3 !important;
    }}
    .stTabs [aria-selected="true"] {{
        background: {T['tab_active']} !important;
        color: #ffffff !important;
    }}
    .stTabs [aria-selected="true"] p,
    .stTabs [aria-selected="true"] div[data-testid="stMarkdownContainer"],
    .stTabs [aria-selected="true"] span {{
        color: #ffffff !important;
    }}
    .stTabs [data-baseweb="tab-highlight"] {{
        background: {T['tab_active']} !important;
    }}

    /* ─── 하이라이트 박스 ───────────────────────────────── */
    .qms-highlight {{
        background: {'#fff3cd' if not st.session_state.get('dark_mode') else '#2d2600'};
        border: 1px solid {'#ffc107' if not st.session_state.get('dark_mode') else '#6d5500'};
        border-radius: 8px;
        padding: 10px 14px;
        margin-top: 8px;
        color: {T['text']};
    }}

    /* ─── 배지 ──────────────────────────────────────────── */
    .qms-badge {{
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.3px;
        vertical-align: middle;
    }}
    .badge-green  {{ background: #e8f5e9; color: #2e7d32; }}
    .badge-yellow {{ background: #fff8e1; color: #e65100; }}
    .badge-red    {{ background: #ffebee; color: #c62828; }}
    .badge-blue   {{ background: #e8eaf6; color: #283593; }}

    /* ─── 빈 상태 안내 ─────────────────────────────────── */
    .qms-empty-state {{
        text-align: center;
        padding: 48px 24px;
        color: {T['sub_text']};
    }}
    .qms-empty-state .icon {{ font-size: 3rem; }}
    .qms-empty-state .msg  {{ font-size: 1rem; margin-top: 12px; }}

    /* ─── 푸터 ──────────────────────────────────────────── */
    .qms-footer {{
        text-align: center;
        color: {T['sub_text']};
        font-size: 0.77rem;
        padding: 16px 0 8px 0;
        border-top: 1px solid {T['border']};
        margin-top: 20px;
    }}

    /* ─── 사이드바 ──────────────────────────────────────── */
    [data-testid="stSidebar"] {{
        background: {'#f3f4f8' if not st.session_state.get('dark_mode') else '#131625'} !important;
    }}
    [data-testid="stSidebar"] .stButton button {{
        border-radius: 8px;
        font-weight: 600;
    }}

    /* ─── 데이터프레임 ──────────────────────────────────── */
    .stDataFrame {{ border-radius: 8px; overflow: hidden; }}

    /* ─── 알림 카드 ─────────────────────────────────────── */
    .qms-alert-card {{
        border-radius: 8px;
        padding: 10px 16px;
        margin: 6px 0;
        font-size: 0.88rem;
        border-left: 4px solid;
    }}
    .qms-alert-overdue  {{ border-left-color: {RED};    background: #fff5f5; color: #c0392b; }}
    .qms-alert-upcoming {{ border-left-color: {YELLOW}; background: #fffbf0; color: #c0710a; }}
    .qms-alert-ok       {{ border-left-color: {GREEN};  background: #f0fff4; color: #276a38; }}
</style>
""", unsafe_allow_html=True)


# ─── 컴포넌트 함수 ────────────────────────────────────────────────────────────

def section_header(text: str, icon: str = "") -> None:
    """좌측 강조선 있는 섹션 제목."""
    label = f"{icon} {text}" if icon else text
    st.markdown(f'<div class="qms-section-header">{label}</div>', unsafe_allow_html=True)


def render_header(title: str, subtitle: str = "") -> None:
    """페이지 상단 그라디언트 헤더."""
    from datetime import datetime
    sub = subtitle or f"기준일: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 광동제약 품질관리부문"
    st.markdown(f"""
    <div class="qms-header">
        <h1>▦ {title}</h1>
        <p>{sub}</p>
    </div>
    """, unsafe_allow_html=True)


def render_footer() -> None:
    from datetime import datetime
    st.markdown(
        f'<div class="qms-footer">© {datetime.now().year} 광동제약 품질관리부문 | QMS 통합 모니터링 대시보드 v2.0</div>',
        unsafe_allow_html=True,
    )


def badge(text: str, level: str = "blue") -> str:
    """HTML 뱃지 문자열 반환. level: green|yellow|red|blue"""
    return f'<span class="qms-badge badge-{level}">{text}</span>'


def empty_state(message: str, icon: str = "📭") -> None:
    """데이터 없음 안내 위젯."""
    st.markdown(f"""
    <div class="qms-empty-state">
        <div class="icon">{icon}</div>
        <div class="msg">{message}</div>
    </div>
    """, unsafe_allow_html=True)


def inject_sidebar_toggle() -> None:
    """
    Streamlit 네이티브 토글(좌/우 분리 렌더)을 숨기고 #qms-sb-toggle 하나로 접기/펼치기.
    펼침 시 사이드바 오른쪽 경계 근처, 접힘 시 왼쪽 끝. 열림 판별은 data-testid=stSidebarCollapseButton 유무.
    """
    import streamlit.components.v1 as components
    components.html(r"""
<script>
(function () {
  'use strict';
  var P = window.parent.document;

  function injectCSS() {
    if (P.getElementById('qms-sb-css')) return;
    var s = P.createElement('style');
    s.id = 'qms-sb-css';
    s.textContent = [
      '[data-testid="collapsedControl"],',
      '[data-testid="stSidebarCollapsedControl"],',
      '[data-testid="stSidebarCollapseButton"] {',
      '  position: fixed !important;',
      '  left: -99999px !important;',
      '  top: 0 !important;',
      '  opacity: 0 !important;',
      '  pointer-events: auto !important;',
      '}',
      '#qms-sb-toggle {',
      '  position: fixed;',
      '  top: 50vh;',
      '  left: 0;',
      '  transform: translateY(-50%);',
      '  z-index: 10000001;',
      '  box-sizing: border-box;',
      '  width: 36px;',
      '  height: 72px;',
      '  margin: 0;',
      '  padding: 0;',
      '  border: 2px solid #ffffff;',
      '  outline: 1px solid rgba(63,81,181,0.55);',
      '  border-radius: 0 12px 12px 0;',
      '  background: linear-gradient(180deg,#7e8eed 0%,#5c6bc0 35%,#3949ab 100%);',
      '  color: #ffffff;',
      '  cursor: pointer;',
      '  display: flex;',
      '  align-items: center;',
      '  justify-content: center;',
      '  font-size: 17px;',
      '  font-weight: 700;',
      '  line-height: 1;',
      '  user-select: none;',
      '  box-shadow: 0 0 0 2px rgba(255,255,255,0.85), 4px 2px 18px rgba(13,27,62,0.45);',
      '  transition: left .26s cubic-bezier(.4,0,.2,1), background .18s;',
      '  filter: saturate(1.06);',
      '}',
      '#qms-sb-toggle:hover {',
      '  background: linear-gradient(180deg,#9fa8da 0%,#7e8eed 50%,#5c6bc0 100%);',
      '}',
      '#qms-sb-toggle:active {',
      '  background: linear-gradient(180deg,#3949ab 0%,#303f9f 100%);',
      '}',
    ].join('\n');
    P.head.appendChild(s);
  }

    function initToggle() {
    injectCSS();
    if (P.getElementById('qms-sb-toggle')) return;

    var btn = P.createElement('button');
    btn.id = 'qms-sb-toggle';
    btn.type = 'button';
    btn.setAttribute('aria-label', '사이드바 접기 · 펼치기');
    P.body.appendChild(btn);

    function getSB() { return P.querySelector('[data-testid="stSidebar"]'); }

    function isOpen() {
      return !!P.querySelector('[data-testid="stSidebarCollapseButton"]');
    }

    function targetNative() {
      return P.querySelector('[data-testid="stSidebarCollapseButton"] button') ||
        P.querySelector('[data-testid="collapsedControl"] button') ||
        P.querySelector('[data-testid="stSidebarCollapsedControl"] button');
    }

    var _updating = false;
    function update() {
      if (_updating) return;
      _updating = true;
      try {
        var open = isOpen();
        var sb = getSB();
        if (open && sb) {
          var r = sb.getBoundingClientRect();
          btn.style.left = Math.max(0, Math.round(r.right) - 20) + 'px';
        } else {
          btn.style.left = '0px';
        }
        btn.textContent = open ? '\u2039' : '\u203a';
        btn.title = open ? '사이드바 접기' : '사이드바 펼치기';
      } finally {
        _updating = false;
      }
    }

    var _deb = null;
    function scheduleUpdate() {
      if (_deb) return;
      _deb = setTimeout(function () {
        _deb = null;
        update();
      }, 150);
    }

    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var h = targetNative();
      if (h) { h.click(); }
      setTimeout(scheduleUpdate, 400);
    });

    if (window.qmsSbIntervalId) { clearInterval(window.qmsSbIntervalId); }
    window.qmsSbIntervalId = setInterval(scheduleUpdate, 1200);

    function attachSidebarObserver() {
      var sb = getSB();
      if (!sb || window.qmsSbToggleObserver) return;
      window.qmsSbToggleObserver = new MutationObserver(scheduleUpdate);
      window.qmsSbToggleObserver.observe(sb, { childList: true, subtree: true, attributes: false });
    }
    attachSidebarObserver();
    if (!window.qmsSbResizeBound) {
      window.qmsSbResizeBound = true;
      window.addEventListener('resize', scheduleUpdate);
    }
    scheduleUpdate();
  }

  function tryInit() {
    if (P.querySelector('[data-testid="stSidebar"]')) {
      initToggle();
    } else {
      setTimeout(tryInit, 180);
    }
  }
  tryInit();

  setInterval(function () {
    if (!P.getElementById('qms-sb-toggle')) { tryInit(); }
  }, 4000);
})();
</script>
""", height=0, scrolling=False)


def dark_mode_toggle() -> None:
    """사이드바 다크모드 토글. apply_global_css() 이전에 세션 상태만 변경."""
    if "dark_mode" not in st.session_state:
        st.session_state["dark_mode"] = False
    icon = "☀️ 라이트 모드" if st.session_state["dark_mode"] else "🌙 다크 모드"
    if st.sidebar.button(icon, use_container_width=True, key="_dark_toggle"):
        st.session_state["dark_mode"] = not st.session_state["dark_mode"]
        st.rerun()


def sparkline_html(values: list[float], color: str = "#3f51b5", height: int = 32, width: int = 80) -> str:
    """SVG 스파크라인 HTML 문자열 반환."""
    if not values or len(values) < 2:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1.0
    step = width / (len(values) - 1)
    pts = " ".join(
        f"{i * step:.1f},{height - (v - mn) / rng * height:.1f}"
        for i, v in enumerate(values)
    )
    return (
        f'<svg width="{width}" height="{height}" style="vertical-align:middle">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round"/>'
        f'</svg>'
    )


def metric_with_sparkline(
    label: str,
    value: str,
    delta: str | None,
    spark_values: list[float],
    spark_color: str = ACCENT,
    status: str = "normal",   # "good" | "warn" | "bad" | "normal"
) -> None:
    """스파크라인 포함 커스텀 메트릭 카드."""
    T = _theme()
    svg = sparkline_html(spark_values, color=spark_color)
    delta_html = ""
    if delta:
        d_color = GREEN if delta.startswith("+") or "↑" in delta else RED
        delta_html = f'<span style="font-size:0.75rem;color:{d_color};font-weight:600">{delta}</span>'
    border_color = {"good": GREEN, "warn": YELLOW, "bad": RED}.get(status, ACCENT)
    st.markdown(f"""
    <div style="
        background:{T['card_bg']};
        border-radius:10px;
        padding:14px 16px;
        border-left:4px solid {border_color};
        box-shadow:0 2px 8px rgba(0,0,0,0.07);
        display:flex; flex-direction:column; gap:4px;
    ">
        <span style="font-size:0.75rem;color:{T['sub_text']};font-weight:600;letter-spacing:0.3px">{label}</span>
        <div style="display:flex;align-items:center;justify-content:space-between">
            <span style="font-size:1.4rem;font-weight:700;color:{T['text']}">{value}</span>
            {svg}
        </div>
        {delta_html}
    </div>
    """, unsafe_allow_html=True)


def kpi_gauge_improved(value: float, target: float, title: str, suffix: str = "%", inverse: bool = False):
    """세련된 반원 게이지 차트 반환 (Plotly Figure)."""
    import plotly.graph_objects as go

    if inverse:
        color = GREEN if value <= target else (RED if value > target * 1.5 else YELLOW)
    else:
        color = GREEN if value >= target else (RED if value < target * 0.7 else YELLOW)

    max_val = max(100, target * 1.5, value * 1.2) if not inverse else max(target * 2, value * 1.5, 60)
    T = _theme()

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=value,
        delta={
            "reference": target,
            "valueformat": ".1f",
            "increasing": {"color": GREEN if not inverse else RED},
            "decreasing": {"color": RED if not inverse else GREEN},
            "font": {"size": 11},
        },
        number={"suffix": suffix, "font": {"size": 28, "color": color}},
        title={"text": title, "font": {"size": 12, "color": T["text"]}},
        gauge={
            "axis": {
                "range": [0, max_val],
                "tickfont": {"size": 9, "color": T["sub_text"]},
                "tickcolor": T["border"],
                "nticks": 5,
            },
            "bar": {"color": color, "thickness": 0.65},
            "bgcolor": T["card_bg"],
            "borderwidth": 0,
            "steps": [
                {"range": [0, max_val * 0.5],
                 "color": "#ffebee" if not inverse else "#e8f5e9"},
                {"range": [max_val * 0.5, max_val * 0.8],
                 "color": "#fff8e1"},
                {"range": [max_val * 0.8, max_val],
                 "color": "#e8f5e9" if not inverse else "#ffebee"},
            ],
            "threshold": {
                "line": {"color": "#333", "width": 2},
                "value": target,
                "thickness": 0.85,
            },
            "shape": "angular",
        },
    ))
    fig.update_layout(
        height=210,
        margin=dict(l=20, r=20, t=45, b=10),
        paper_bgcolor=T["bg"],
        font={"color": T["text"]},
    )
    return fig


def filter_reset_button(key: str = "filter_reset") -> bool:
    """필터 초기화 버튼. 클릭 시 True 반환."""
    return st.sidebar.button("↺ 필터 초기화", use_container_width=True, key=key)


def cache_age_bar(fetch_elapsed: float, ttl: int = 1800) -> None:
    """사이드바 캐시 TTL 잔여 시간 프로그레스바."""
    import time as _time
    age_key = "_cache_fetch_time"
    if st.session_state.get(age_key) is None:
        st.session_state[age_key] = _time.time()
    age = _time.time() - st.session_state[age_key]
    remaining_pct = max(0.0, 1.0 - age / ttl)
    remaining_min = max(0, int((ttl - age) / 60))
    st.sidebar.progress(remaining_pct, text=f"캐시 잔여 {remaining_min}분")


def overdue_alert_card(label: str, count: int, level: str = "overdue") -> None:
    """기한 초과/임박 알림 카드."""
    cls_map = {"overdue": "qms-alert-overdue", "upcoming": "qms-alert-upcoming", "ok": "qms-alert-ok"}
    icon_map = {"overdue": "🚨", "upcoming": "⚠️", "ok": "✅"}
    cls = cls_map.get(level, "qms-alert-upcoming")
    icon = icon_map.get(level, "")
    st.markdown(
        f'<div class="qms-alert-card {cls}">{icon} <b>{label}</b>: {count}건</div>',
        unsafe_allow_html=True,
    )
