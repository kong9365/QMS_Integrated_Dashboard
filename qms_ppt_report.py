# -*- coding: utf-8 -*-
"""qms_ppt_report.py — OOS 마감회의 & GMP 보고서 PowerPoint(.pptx) 생성기 (KD-MoaQ).

설계 원칙
- **신규 순수함수만 추가**. 기존 표시 함수 ``qms_oos_dashboard_panels.render_oos_gmp``
  및 도메인/건수기여도 계산 로직은 일절 수정하지 않는다(탭 레이아웃·디자인 불변).
- render_oos_gmp 의 집계(건수기여도 기반 groupby/sum)를 **그대로 미러링**하여, 화면의
  표/차트를 **python-pptx 네이티브 요소(표·차트)**로 충실히 재현한다.
  → 슬라이드 구성은 화면의 2행 레이아웃을 따른다(1행=현황표·월별·Analyst error,
     2행=원인 대분류·소분류·시험종류별).
- 차트는 python-pptx 네이티브 차트(편집 가능) → kaleido(이미지 변환) 불필요.

공개 API
- build_oos_gmp_report_pptx(filtered, df_full, primary_year, prev_year, year_col,
      month_col, safe_pct, completed_keywords, *,
      as_of=None, project_label="OOS (Out of Specification)", filter_note="") -> bytes
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Optional, Tuple

import pandas as pd
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

_MONTH_LABELS = [f"{m}월" for m in range(1, 13)]
_KD_RED = RGBColor(0xE8, 0x30, 0x08)
_NAVY = RGBColor(0x1F, 0x3A, 0x5F)
_BAR = RGBColor(0x4C, 0x78, 0xA8)
_DARK = RGBColor(0x59, 0x59, 0x59)
_GRAY = RGBColor(0x80, 0x80, 0x80)
_WHITE = RGBColor(0xFF, 0xFF, 0xFF)


# ── render_oos_gmp 와 동일한 보조 계산(미러링) ──
def _monthly_counts(data: pd.DataFrame, year, year_col: str, month_col: str = "월"):
    if data is None or data.empty or year_col not in data.columns or month_col not in data.columns:
        return [0] * 12
    yr = data[data[year_col] == year]
    if yr.empty:
        return [0] * 12
    if "건수기여도" in yr.columns:
        monthly = yr.groupby(month_col, dropna=False)["건수기여도"].sum()
    else:
        monthly = yr.groupby(month_col, dropna=False).size().astype(float)
    monthly = monthly.reindex(range(1, 13), fill_value=0)
    return [round(float(x)) for x in monthly.tolist()]


def _completed_mask(df: pd.DataFrame, kw: Tuple[str, ...]) -> pd.Series:
    if "진행상태" in df.columns:
        return df["진행상태"].str.contains("|".join(kw), case=False, na=False)
    if "완료여부" in df.columns:
        return df["완료여부"] == "C"
    return pd.Series(False, index=df.index)


# ── PPT 요소 헬퍼 ──
def _textbox(slide, text, *, size=20, color=_NAVY, bold=True, left=0.5, top=0.3,
             width=12.3, height=0.7, align=PP_ALIGN.LEFT):
    tb = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    r.font.name = "맑은 고딕"
    return tb


def _kpi_table(slide, rows, *, left, top, width, height):
    shape = slide.shapes.add_table(len(rows) + 1, 2, Inches(left), Inches(top), Inches(width), Inches(height))
    table = shape.table
    table.columns[0].width = Inches(width * 0.6)
    table.columns[1].width = Inches(width * 0.4)
    for j, h in enumerate(("항목", "값")):
        c = table.cell(0, j)
        c.text = h
        c.fill.solid()
        c.fill.fore_color.rgb = _NAVY
        for p in c.text_frame.paragraphs:
            p.alignment = PP_ALIGN.CENTER
            for run in p.runs:
                run.font.bold = True
                run.font.color.rgb = _WHITE
                run.font.size = Pt(12)
                run.font.name = "맑은 고딕"
    for i, (label, value) in enumerate(rows, start=1):
        for j, val in enumerate((label, value)):
            c = table.cell(i, j)
            c.text = str(val)
            for p in c.text_frame.paragraphs:
                p.alignment = PP_ALIGN.LEFT if j == 0 else PP_ALIGN.RIGHT
                for run in p.runs:
                    run.font.size = Pt(12)
                    run.font.bold = (j == 1)
                    run.font.name = "맑은 고딕"


def _bar_chart(slide, categories, values, *, left, top, width, height,
               title=None, horizontal=False, color=_BAR):
    cd = CategoryChartData()
    cd.categories = [str(c) for c in categories]
    cd.add_series("건수", [float(v) for v in values])
    ctype = XL_CHART_TYPE.BAR_CLUSTERED if horizontal else XL_CHART_TYPE.COLUMN_CLUSTERED
    frame = slide.shapes.add_chart(ctype, Inches(left), Inches(top), Inches(width), Inches(height), cd)
    chart = frame.chart
    chart.has_legend = False
    if title:
        chart.has_title = True
        chart.chart_title.text_frame.text = title
        try:
            chart.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(13)
        except Exception:
            pass
    else:
        chart.has_title = False
    try:
        plot = chart.plots[0]
        plot.has_data_labels = True
        plot.data_labels.number_format = "0"
        plot.data_labels.number_format_is_linked = False
        plot.data_labels.font.size = Pt(10)
        series = plot.series[0]
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = color
    except Exception:
        pass
    return chart


def build_oos_gmp_report_pptx(
    filtered: pd.DataFrame,
    df_full: pd.DataFrame,
    primary_year: int,
    prev_year: int,
    year_col: str,
    month_col: str,
    safe_pct,
    completed_keywords: Tuple[str, ...],
    *,
    as_of: Optional[str] = None,
    project_label: str = "OOS (Out of Specification)",
    filter_note: str = "",
) -> bytes:
    """마감회의 & GMP 보고서를 .pptx 바이트로 생성(render_oos_gmp 집계·레이아웃 미러링)."""
    prs = Presentation()
    prs.slide_width = Inches(13.333)   # 16:9
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    as_of = as_of or datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Slide 1: 표지 ──
    s = prs.slides.add_slide(blank)
    _textbox(s, "광동제약 품질부문 · KD-MoaQ", size=15, color=_KD_RED, top=2.2, align=PP_ALIGN.CENTER)
    _textbox(s, "OOS 마감회의 & GMP 보고서", size=36, color=_NAVY, top=2.75, align=PP_ALIGN.CENTER)
    _textbox(s, f"대상: {project_label}    기준일: {as_of}", size=15, color=_GRAY, bold=False, top=3.85, align=PP_ALIGN.CENTER)
    if filter_note:
        _textbox(s, f"필터: {filter_note}", size=12, color=_GRAY, bold=False, top=4.3, align=PP_ALIGN.CENTER)

    yc = year_col if (filtered is not None and year_col in getattr(filtered, "columns", [])) else "연도"
    mc = month_col if (filtered is not None and month_col in getattr(filtered, "columns", [])) else "월"

    if filtered is None or getattr(filtered, "empty", True) or "건수기여도" not in filtered.columns:
        s2 = prs.slides.add_slide(blank)
        _textbox(s2, "표시할 OOS 데이터가 없습니다 (필터 결과 0건 또는 건수기여도 컬럼 없음).", size=18, top=3.0)
        buf = io.BytesIO()
        prs.save(buf)
        return buf.getvalue()

    # ── 집계(render_oos_gmp 미러링) ──
    total_events = round(float(filtered["건수기여도"].sum()))
    completed_ev = round(float(filtered.loc[_completed_mask(filtered, completed_keywords), "건수기여도"].sum()))
    ev_comp_rate = safe_pct(completed_ev, total_events)
    capa_df2 = pd.DataFrame()
    if "CAPA/Action item 필요여부" in filtered.columns:
        m2 = filtered["CAPA/Action item 필요여부"].fillna("").str.len() > 0
        no2 = filtered["CAPA/Action item 필요여부"].fillna("").str.contains("No Action", case=False, na=False)
        capa_df2 = filtered[m2 & ~no2]
    capa_cnt2 = round(float(capa_df2["건수기여도"].sum())) if (not capa_df2.empty and "건수기여도" in capa_df2.columns) else 0
    if not capa_df2.empty and "진행상태" in capa_df2.columns and "건수기여도" in capa_df2.columns:
        capa_prog2 = safe_pct(round(float(capa_df2.loc[_completed_mask(capa_df2, completed_keywords), "건수기여도"].sum())), capa_cnt2)
    else:
        capa_prog2 = 0.0

    # ── Slide 2: 1행 — 현황표 · 월별 건수 · Analyst error ──
    s = prs.slides.add_slide(blank)
    _textbox(s, "시험실 이벤트 발생 현황", size=20)
    _textbox(s, "시험실 이벤트 발생 현황", size=12, color=_DARK, top=1.15, left=0.5, width=3.6, height=0.3)
    _kpi_table(s, [
        ("전체 건수", f"{total_events}건"),
        ("완료 건수", f"{completed_ev}건"),
        ("완료율 (%)", f"{ev_comp_rate:.0f}%"),
        ("CAPA 건수", f"{capa_cnt2}건"),
        ("CAPA 진행률", f"{capa_prog2:.1f}%"),
    ], left=0.5, top=1.5, width=3.6, height=3.0)

    monthly = _monthly_counts(filtered, primary_year, yc, mc)
    _bar_chart(s, _MONTH_LABELS, monthly, left=4.4, top=1.3, width=4.5, height=5.4, title="월별 시험실이벤트 건수")

    # Analyst error 감소율
    if "이상발생 원인" in df_full.columns and "건수기여도" in df_full.columns and year_col in df_full.columns:
        ae_prev = round(float(df_full[(df_full[year_col] == prev_year) & (df_full["이상발생 원인"] == "Analyst error")]["건수기여도"].sum()))
        ae_curr = round(float(filtered[filtered["이상발생 원인"] == "Analyst error"]["건수기여도"].sum())) if "이상발생 원인" in filtered.columns else 0
        if ae_prev > 0 or ae_curr > 0:
            _bar_chart(s, [str(prev_year), str(primary_year)], [ae_prev, ae_curr],
                       left=9.1, top=1.3, width=3.8, height=4.6, title="Analyst error 감소율")
            red = safe_pct(ae_prev - ae_curr, ae_prev) if ae_prev > 0 else 0.0
            _txt = f"감소율: -{red:.0f}%" if red > 0 else f"증가율: +{abs(red):.0f}%"
            _textbox(s, _txt, size=15, color=_KD_RED, top=6.0, left=9.1, width=3.8, height=0.5, align=PP_ALIGN.CENTER)

    # ── Slide 3: 2행 — 원인 대분류 · 소분류 · 시험종류별 ──
    s = prs.slides.add_slide(blank)
    _textbox(s, "시험실이벤트 원인 분석", size=20)
    if "확인된 이벤트 분류" in filtered.columns:
        major = (
            filtered[filtered["확인된 이벤트 분류"].notna() & (filtered["확인된 이벤트 분류"] != "")]
            .groupby("확인된 이벤트 분류")["건수기여도"].sum().round().sort_values(ascending=False).reset_index()
        )
        major.columns = ["분류", "건수"]
        if not major.empty:
            _bar_chart(s, major["분류"].tolist(), major["건수"].tolist(),
                       left=0.4, top=1.3, width=4.1, height=5.4, title="원인 대분류")
    if "이상발생 원인" in filtered.columns:
        minor = (
            filtered[filtered["이상발생 원인"].notna() & (filtered["이상발생 원인"] != "")]
            .groupby("이상발생 원인")["건수기여도"].sum().round().sort_values(ascending=True).reset_index()
        )
        minor.columns = ["원인", "건수"]
        if not minor.empty:
            _bar_chart(s, minor["원인"].tolist(), minor["건수"].tolist(),
                       left=4.7, top=1.3, width=4.1, height=5.4, title="원인 소분류", horizontal=True)
    if "시험종류" in filtered.columns:
        ttype = (
            filtered[filtered["시험종류"].notna() & (filtered["시험종류"] != "")]
            .groupby("시험종류")["건수기여도"].sum().round().sort_values(ascending=False).reset_index()
        )
        ttype.columns = ["시험종류", "건수"]
        if not ttype.empty:
            _bar_chart(s, ttype["시험종류"].tolist(), ttype["건수"].tolist(),
                       left=9.0, top=1.3, width=4.0, height=5.4, title="시험종류별 발생 건수", color=_DARK)

    # 푸터
    _textbox(s, f"생성: KD-MoaQ · 광동제약 품질부문 · {datetime.now():%Y-%m-%d %H:%M}",
             size=9, color=_GRAY, bold=False, top=7.05, left=0.4, width=9.0, height=0.35)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
