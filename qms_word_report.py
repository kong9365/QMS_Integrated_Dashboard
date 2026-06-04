# -*- coding: utf-8 -*-
"""qms_word_report.py — OOS 경향분석보고서 Word(.docx) 생성기 (KD-MoaQ).

설계 원칙
- **신규 순수함수만 추가**. 기존 표시 함수 ``qms_oos_dashboard_panels.render_oos_report``
  및 도메인/건수기여도 계산 로직은 일절 수정하지 않는다.
- render_oos_report 의 집계(건수기여도 기반 groupby/sum)를 **그대로 미러링**해
  python-docx 표로 출력한다 → 화면 숫자와 보고서 숫자 일치.
- 차트(Plotly)는 kaleido 미설치 환경을 고려해 **데이터 표**로 대체(Word 문서에는
  정확한 수치 표가 차트보다 유용). 추후 kaleido 도입 시 이미지 임베드 확장 가능.

공개 API
- build_oos_trend_report_docx(filtered, safe_pct, completed_keywords, *,
      as_of=None, project_label="OOS (Out of Specification)", filter_note="") -> bytes
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Optional, Tuple

import pandas as pd
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

_MONTH_LABELS = [f"{m}월" for m in range(1, 13)]
_KD_RED = RGBColor(0xE8, 0x30, 0x08)   # 사이드바 로고 샘플색(앱과 동일)
_HDR_BG = "1F3A5F"                       # 표 헤더 배경(네이비)
_GREY = RGBColor(0x80, 0x80, 0x80)


def _set_cell_bg(cell, hex_color: str) -> None:
    """표 셀 배경 음영."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _style_runs(cell, *, bold=False, white=False, size=9) -> None:
    for p in cell.paragraphs:
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            if white:
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)


def _add_df_table(doc, df: pd.DataFrame) -> None:
    """DataFrame → Word 표(헤더 음영+굵게, 본문 9pt). 인덱스는 무시(필요 시 reset_index 후 전달)."""
    cols = [str(c) for c in df.columns]
    table = doc.add_table(rows=1, cols=len(cols))
    try:
        table.style = "Table Grid"   # 기본 템플릿에 항상 존재 → 안전
    except Exception:
        pass
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = table.rows[0].cells
    for j, c in enumerate(cols):
        hdr[j].text = c
        _set_cell_bg(hdr[j], _HDR_BG)
        _style_runs(hdr[j], bold=True, white=True, size=9)
    for _, row in df.iterrows():
        cells = table.add_row().cells
        for j, c in enumerate(df.columns):
            val = row[c]
            cells[j].text = "" if pd.isna(val) else str(val)
            _style_runs(cells[j], size=9)


def _heading(doc, text: str, level: int = 2) -> None:
    doc.add_heading(text, level=level)


def _completed_mask(filtered: pd.DataFrame, completed_keywords: Tuple[str, ...]) -> pd.Series:
    """render_oos_report 와 동일한 완료 판정(진행상태 contains → 완료여부=='C' → False)."""
    if "진행상태" in filtered.columns:
        return filtered["진행상태"].str.contains("|".join(completed_keywords), case=False, na=False)
    if "완료여부" in filtered.columns:
        return filtered["완료여부"] == "C"
    return pd.Series(False, index=filtered.index)


def build_oos_trend_report_docx(
    filtered: pd.DataFrame,
    safe_pct,
    completed_keywords: Tuple[str, ...],
    *,
    as_of: Optional[str] = None,
    project_label: str = "OOS (Out of Specification)",
    filter_note: str = "",
) -> bytes:
    """OOS 경향분석보고서를 .docx 바이트로 생성(render_oos_report 집계 미러링)."""
    doc = Document()
    # 기본 폰트(맑은 고딕)
    try:
        normal = doc.styles["Normal"]
        normal.font.name = "맑은 고딕"
        normal.font.size = Pt(10)
        normal.element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")
    except Exception:
        pass

    # ── 표지/헤더 ──
    brand = doc.add_paragraph()
    br = brand.add_run("광동제약 품질부문 · KD-MoaQ")
    br.font.size = Pt(10)
    br.font.bold = True
    br.font.color.rgb = _KD_RED

    doc.add_heading("OOS 경향분석 보고서", level=0)

    as_of = as_of or datetime.now().strftime("%Y-%m-%d %H:%M")
    meta = doc.add_paragraph()
    meta.add_run(f"대상: {project_label}    기준일: {as_of}").font.size = Pt(9)
    if filter_note:
        fp = doc.add_paragraph()
        fp.add_run(f"필터: {filter_note}").font.size = Pt(9)

    # 데이터 없음 가드
    if filtered is None or getattr(filtered, "empty", True) or "건수기여도" not in filtered.columns:
        doc.add_paragraph("표시할 OOS 데이터가 없습니다 (필터 결과 0건 또는 건수기여도 컬럼 없음).")
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    # ── ◆ 경향 요약 ──
    total_w = float(filtered["건수기여도"].sum())
    comp_w = float(filtered.loc[_completed_mask(filtered, completed_keywords), "건수기여도"].sum())
    comp_rate = safe_pct(comp_w, total_w)
    _heading(doc, "◆ 경향 요약")
    _add_df_table(doc, pd.DataFrame({
        "항목": ["전체 건수", "완료 건수", "완료율 (%)", "미완료 건수"],
        "값": [f"{total_w:.0f}건", f"{comp_w:.0f}건", f"{comp_rate:.1f}%", f"{total_w - comp_w:.0f}건"],
    }))

    # ── ◇ 시험종류별 월별 건수 ──
    if "시험종류" in filtered.columns and "월" in filtered.columns:
        pivot = filtered.groupby(["시험종류", "월"])["건수기여도"].sum().round().unstack(fill_value=0)
        for m in range(1, 13):
            if m not in pivot.columns:
                pivot[m] = 0
        pivot = pivot[sorted(pivot.columns)]
        pivot.columns = _MONTH_LABELS
        pivot["합계"] = pivot.sum(axis=1).round().astype(int)
        pivot = pivot.astype(int).sort_values("합계", ascending=False)
        total_row = pivot.sum().to_frame().T
        total_row.index = ["합계"]
        full = pd.concat([pivot, total_row])
        full.index.name = "시험종류"
        _heading(doc, "◇ 시험종류별 월별 건수")
        _add_df_table(doc, full.reset_index())

    # ── ◇ 상위 OOS 시험항목 (Top 10) ──
    if "시험항목" in filtered.columns:
        item = (
            filtered[filtered["시험항목"].notna() & (filtered["시험항목"] != "")]
            .groupby("시험항목")["건수기여도"].sum().round().nlargest(10).reset_index()
        )
        if not item.empty:
            item.columns = ["시험항목", "건수"]
            item.insert(0, "순위", range(1, len(item) + 1))
            item["건수"] = item["건수"].astype(int)
            _heading(doc, "◇ 상위 OOS 시험항목 (Top 10)")
            _add_df_table(doc, item)

    # ── ◇ 제품별 OOS 건수 (Top 15) ──
    if "품목명" in filtered.columns:
        prod = (
            filtered[filtered["품목명"].notna() & (filtered["품목명"] != "")]
            .groupby("품목명")["건수기여도"].sum().round().sort_values(ascending=False).head(15).reset_index()
        )
        if not prod.empty:
            prod.columns = ["품목명", "건수"]
            prod["건수"] = prod["건수"].astype(int)
            _heading(doc, "◇ 제품별 OOS 건수 (Top 15)")
            _add_df_table(doc, prod)

    # ── ◇ 조치현황 / CAPA 현황 ──
    if "CAPA/Action item 필요여부" in filtered.columns:
        has = filtered["CAPA/Action item 필요여부"].fillna("").str.len() > 0
        no_act = filtered["CAPA/Action item 필요여부"].fillna("").str.contains("No Action", case=False, na=False)
        capa_df = filtered[has & ~no_act]
        _heading(doc, "◇ 조치현황 / CAPA 현황")
        if not capa_df.empty:
            vc = capa_df["CAPA/Action item 필요여부"].value_counts()
            _add_df_table(doc, pd.DataFrame({"CAPA 유형": vc.index, "건수": vc.values.astype(int)}))
        else:
            doc.add_paragraph("CAPA 데이터가 없습니다.")

    # ── ◆ 이상발생 원인 분류 분석 (건수 + 비율) ──
    if "이상발생 원인" in filtered.columns:
        cause = (
            filtered[filtered["이상발생 원인"].notna() & (filtered["이상발생 원인"] != "")]
            .groupby("이상발생 원인")["건수기여도"].sum().round().sort_values(ascending=False).reset_index()
        )
        if not cause.empty:
            cause.columns = ["이상발생 원인", "건수"]
            tot = cause["건수"].sum()
            cause["비율 (%)"] = cause["건수"].apply(lambda x: f"{safe_pct(x, tot):.1f}%")
            cause["건수"] = cause["건수"].astype(int)
            _heading(doc, "◆ 이상발생 원인 분류 분석")
            _add_df_table(doc, cause)

    # ── ◆ 확인된 이벤트 분류별 ──
    if "확인된 이벤트 분류" in filtered.columns:
        cls = (
            filtered[filtered["확인된 이벤트 분류"].notna() & (filtered["확인된 이벤트 분류"] != "")]
            .groupby("확인된 이벤트 분류")["건수기여도"].sum().round().sort_values(ascending=False).reset_index()
        )
        if not cls.empty:
            cls.columns = ["이벤트 분류", "건수"]
            cls["건수"] = cls["건수"].astype(int)
            _heading(doc, "◆ 확인된 이벤트 분류별")
            _add_df_table(doc, cls)

    # ── 푸터 ──
    foot = doc.add_paragraph()
    fr = foot.add_run(f"생성: KD-MoaQ · 광동제약 품질부문 · {datetime.now():%Y-%m-%d %H:%M}")
    fr.font.size = Pt(8)
    fr.font.color.rgb = _GREY

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
