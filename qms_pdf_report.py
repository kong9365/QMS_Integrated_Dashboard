# -*- coding: utf-8 -*-
"""
QMS 대시보드 PDF 보고서 생성기

의존성: fpdf2 (pip install fpdf2)
사용법:
    import qms_pdf_report as pdf_rep
    buf = pdf_rep.generate_report(kpi_data, overdue_items, chart_figs)
    st.download_button("PDF 다운로드", data=buf, file_name="QMS_report.pdf", mime="application/pdf")
"""

from __future__ import annotations
import io
from datetime import datetime
from typing import Any


def _check_fpdf():
    try:
        from fpdf import FPDF
        return FPDF
    except ImportError:
        return None


def generate_report(
    kpi_data: dict[str, Any],
    overdue_items: list[dict],
    project_summary: list[dict],
    company: str = "광동제약 품질관리부문",
    title: str = "QMS 통합 모니터링 보고서",
) -> bytes | None:
    """
    KPI 요약 + 기한 초과 목록 PDF 보고서 생성.

    Parameters
    ----------
    kpi_data : dict  e.g. {"CAPA 이행률": 92.1, "변경 완료율": 87.0, "불만 평균처리일": 24}
    overdue_items : list[dict]  각 dict: 프로젝트, 관리번호, 제목, 기한일, D-day
    project_summary : list[dict]  각 dict: 프로젝트, 수집 건수, 기한 초과
    company : str
    title : str

    Returns
    -------
    bytes  PDF 바이트, fpdf2 미설치 시 None 반환
    """
    FPDF = _check_fpdf()
    if FPDF is None:
        return None

    from fpdf import FPDF as _FPDF

    class QMSReport(_FPDF):
        _company = company
        _title = title

        def header(self):
            self.set_fill_color(13, 27, 62)
            self.rect(0, 0, 210, 18, "F")
            self.set_font("Helvetica", "B", 11)
            self.set_text_color(255, 255, 255)
            self.set_xy(8, 4)
            self.cell(0, 10, self._title, ln=False)
            self.set_font("Helvetica", "", 8)
            self.set_xy(0, 4)
            self.cell(200, 10, datetime.now().strftime("%Y-%m-%d"), align="R", ln=True)
            self.set_text_color(0, 0, 0)
            self.ln(4)

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(150, 150, 150)
            self.cell(0, 8, f"{self._company}  |  {self.page_no()} / {{nb}}", align="C")

        def section_title(self, text: str):
            self.set_fill_color(63, 81, 181)
            self.set_text_color(255, 255, 255)
            self.set_font("Helvetica", "B", 10)
            self.cell(0, 8, f"  {text}", fill=True, ln=True)
            self.set_text_color(0, 0, 0)
            self.ln(2)

        def kpi_row(self, label: str, value: str, status: str = "normal"):
            colors = {"good": (39, 174, 96), "warn": (243, 156, 18), "bad": (231, 76, 60)}
            r, g, b = colors.get(status, (63, 81, 181))
            self.set_fill_color(r, g, b)
            self.rect(self.get_x(), self.get_y(), 3, 8, "F")
            self.set_x(self.get_x() + 5)
            self.set_font("Helvetica", "", 9)
            self.cell(80, 8, label)
            self.set_font("Helvetica", "B", 9)
            self.cell(0, 8, value, ln=True)
            self.ln(1)

        def table_header(self, cols: list[tuple[str, int]]):
            self.set_fill_color(243, 244, 248)
            self.set_font("Helvetica", "B", 8)
            for label, width in cols:
                self.cell(width, 7, label, border=1, fill=True)
            self.ln()

        def table_row(self, values: list[str], widths: list[int], fill: bool = False):
            if fill:
                self.set_fill_color(255, 245, 245)
            self.set_font("Helvetica", "", 8)
            for val, width in zip(values, widths):
                txt = str(val)[:28]
                self.cell(width, 6, txt, border=1, fill=fill)
            self.ln()

    pdf = QMSReport()
    pdf.alias_nb_pages()
    pdf.add_page()

    # ─── KPI 요약 ────────────────────────────────────────────────────────────
    pdf.section_title("KPI 요약")
    for label, value in kpi_data.items():
        if isinstance(value, float):
            val_str = f"{value:.1f}%"
            status = "good" if value >= 85 else ("warn" if value >= 70 else "bad")
        else:
            val_str = f"{value}일"
            status = "good" if value <= 30 else ("warn" if value <= 45 else "bad")
        pdf.kpi_row(label, val_str, status)
    pdf.ln(4)

    # ─── 프로젝트별 수집 현황 ─────────────────────────────────────────────────
    pdf.section_title("프로젝트별 수집 현황")
    cols = [("프로젝트", 35), ("그룹", 30), ("수집 건수", 25), ("기한 초과", 20)]
    widths = [c[1] for c in cols]
    pdf.table_header(cols)
    for row in project_summary:
        overdue_n = int(row.get("기한 초과", 0))
        pdf.table_row(
            [row.get("프로젝트", "-"), row.get("그룹", "-"),
             str(row.get("수집 건수", 0)), str(overdue_n)],
            widths,
            fill=(overdue_n > 0),
        )
    pdf.ln(4)

    # ─── 기한 초과 목록 ──────────────────────────────────────────────────────
    if overdue_items:
        pdf.add_page()
        pdf.section_title(f"기한 초과 항목 ({len(overdue_items)}건)")
        cols2 = [("프로젝트", 28), ("관리번호", 20), ("제목", 70), ("기한일", 25), ("D-day", 17)]
        widths2 = [c[1] for c in cols2]
        pdf.table_header(cols2)
        for item in sorted(overdue_items, key=lambda x: x.get("D-day", 0))[:100]:
            pdf.table_row(
                [
                    str(item.get("프로젝트", "-"))[:12],
                    str(item.get("관리번호", "-")),
                    str(item.get("제목", "-"))[:30],
                    str(item.get("기한일", "-")),
                    f"{item.get('D-day', '?')}일",
                ],
                widths2,
                fill=True,
            )

    return bytes(pdf.output())


def generate_report_streamlit(
    kpi_data: dict,
    overdue_items: list[dict],
    project_summary: list[dict],
) -> bytes | None:
    """Streamlit 다운로드 버튼용 래퍼. fpdf2 미설치 시 None 반환."""
    return generate_report(kpi_data, overdue_items, project_summary)
