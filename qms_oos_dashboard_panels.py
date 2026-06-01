# -*- coding: utf-8 -*-
"""QMS_GUI/QMS_Dashboard.py OOS 탭(현황·경향·보고서·GMP) 이식 — 원본파일 탭 제외. 관리번호=QMS prno."""
from __future__ import annotations

from typing import Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from qms_pro.ui import charts as CH  # 차트 레이아웃/렌더 공통 헬퍼(Phase 3-1-d 파일럿)

MONTH_LABELS = [f"{m}월" for m in range(1, 13)]


def get_monthly_counts_weighted(
    data: pd.DataFrame, year: int, year_col: str, month_col: str = "월"
) -> list[int]:
    if data.empty or year_col not in data.columns or month_col not in data.columns:
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


def _id_col(df: pd.DataFrame) -> str:
    return "관리번호" if "관리번호" in df.columns else "QMS번호"


def render_oos_status(
    filtered: pd.DataFrame,
    primary_year: int,
    year_col: str,
    selected_years: list,
    chart_colors: dict,
    safe_pct,
    completed_keywords: Tuple[str, ...],
) -> None:
    if filtered.empty:
        st.info("OOS 필터 결과가 없습니다.")
        return
    idc = _id_col(filtered)
    total_weighted = float(pd.to_numeric(filtered["건수기여도"], errors="coerce").fillna(0).sum()) if "건수기여도" in filtered.columns else float(len(filtered))
    uniq = filtered[idc].nunique() if idc in filtered.columns else len(filtered)
    if "진행상태" in filtered.columns:
        completed_mask = filtered["진행상태"].str.contains("|".join(completed_keywords), case=False, na=False)
    else:
        completed_mask = filtered["완료여부"] == "C" if "완료여부" in filtered.columns else pd.Series(False, index=filtered.index)
    completed_df = filtered[completed_mask]
    cw = float(pd.to_numeric(completed_df["건수기여도"], errors="coerce").fillna(0).sum()) if "건수기여도" in completed_df.columns else float(completed_mask.sum())
    completion_rate = safe_pct(cw, total_weighted)

    capa_count = 0.0
    capa_completed = 0.0
    if "CAPA/Action item 필요여부" in filtered.columns:
        capa_mask = filtered["CAPA/Action item 필요여부"].fillna("").str.len() > 0
        capa_no_action = filtered["CAPA/Action item 필요여부"].fillna("").str.contains("No Action", case=False, na=False)
        capa_needed = filtered[capa_mask & ~capa_no_action]
        if "건수기여도" in capa_needed.columns:
            capa_count = float(capa_needed["건수기여도"].sum())
            cmask = capa_needed["진행상태"].str.contains("|".join(completed_keywords), case=False, na=False) if "진행상태" in capa_needed.columns else pd.Series(False, index=capa_needed.index)
            capa_completed = float(capa_needed.loc[cmask, "건수기여도"].sum()) if cmask.any() else 0.0
    capa_rate = safe_pct(capa_completed, capa_count)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("전체 건수", f"{total_weighted:.0f}건")
    c2.metric("고유 문서", f"{uniq}건")
    c3.metric("완료율 (%)", f"{completion_rate:.0f}%")
    c4.metric("CAPA 건수", f"{capa_count:.0f}건")
    c5.metric("CAPA 진행률", f"{capa_rate:.1f}%")
    st.caption("건수는 **건수기여도** 합, 완료는 **진행상태** 키워드(또는 완료여부) 기준입니다.")

    st.divider()
    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.subheader("시험종류별 건수")
        if "시험종류" in filtered.columns and "건수기여도" in filtered.columns:
            type_data = filtered.groupby("시험종류")["건수기여도"].sum().sort_values(ascending=True).reset_index()
            type_data.columns = ["시험종류", "건수"]
            fig = px.bar(type_data, x="건수", y="시험종류", orientation="h", color_discrete_sequence=[chart_colors["bar"]])
            CH.apply_layout(fig, height=400, margin=(0, 20, 10, 10), yaxis_title="", xaxis_title="건수 (기여도 합산)")
            CH.render_chart(fig)
        else:
            st.info("시험종류 데이터 없음")
    with chart_right:
        st.subheader("품목 TOP 10")
        if "품목명" in filtered.columns and "건수기여도" in filtered.columns:
            item_data = (
                filtered[filtered["품목명"].notna() & (filtered["품목명"] != "")]
                .groupby("품목명")["건수기여도"].sum()
                .nlargest(10).sort_values(ascending=True).reset_index()
            )
            item_data.columns = ["품목명", "건수"]
            fig = px.bar(item_data, x="건수", y="품목명", orientation="h", color_discrete_sequence=["#764ba2"])
            CH.apply_layout(fig, height=400, margin=(0, 20, 10, 10), yaxis_title="", xaxis_title="건수 (기여도 합산)")
            CH.render_chart(fig)
        else:
            st.info("품목명 데이터 없음")

    chart_left2, chart_right2 = st.columns(2)
    with chart_left2:
        st.subheader("원인 대분류")
        if "확인된 이벤트 분류" in filtered.columns and "건수기여도" in filtered.columns:
            event_data = (
                filtered[filtered["확인된 이벤트 분류"].notna() & (filtered["확인된 이벤트 분류"] != "")]
                .groupby("확인된 이벤트 분류")["건수기여도"].sum().reset_index()
            )
            event_data.columns = ["이벤트 분류", "건수"]
            fig = px.pie(event_data, values="건수", names="이벤트 분류", color_discrete_sequence=px.colors.qualitative.Set2, hole=0.4)
            CH.apply_layout(fig, height=380, margin=(0, 0, 10, 10))
            fig.update_traces(textinfo="label+value+percent", textfont_size=12)
            CH.render_chart(fig)
        else:
            st.info("확인된 이벤트 분류 없음")
    with chart_right2:
        st.subheader("이상발생 원인 상세")
        if "이상발생 원인" in filtered.columns and "건수기여도" in filtered.columns:
            cause_data = (
                filtered[filtered["이상발생 원인"].notna() & (filtered["이상발생 원인"] != "")]
                .groupby("이상발생 원인")["건수기여도"].sum().sort_values(ascending=False).reset_index()
            )
            cause_data.columns = ["이상발생 원인", "건수"]
            fig = px.pie(cause_data, values="건수", names="이상발생 원인", color_discrete_sequence=px.colors.qualitative.Pastel)
            CH.apply_layout(fig, height=380, margin=(0, 0, 10, 10))
            fig.update_traces(textinfo="label+value+percent", textfont_size=11)
            CH.render_chart(fig)
        else:
            st.info("이상발생 원인 없음")

    st.subheader("월별 OOS 건수 추이")
    yc = year_col if year_col in filtered.columns else "연도"
    mc = "월_등록" if "월_등록" in filtered.columns and year_col == "연도_등록" else "월"
    if mc in filtered.columns and yc in filtered.columns and "건수기여도" in filtered.columns:
        fp = filtered[filtered[yc] == primary_year] if primary_year in filtered[yc].values else filtered
        if fp.empty:
            fp = filtered
        monthly = fp.groupby(mc)["건수기여도"].sum()
        ys = [float(monthly.get(m, 0)) if m in monthly.index else 0.0 for m in range(1, 13)]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=MONTH_LABELS, y=ys, name="건수", marker_color=chart_colors["bar"], opacity=0.7))
        fig.add_trace(go.Scatter(x=MONTH_LABELS, y=ys, name="추이", mode="lines+markers", line=dict(color=chart_colors["red"], width=2)))
        fig.update_layout(
            height=400, margin=dict(l=40, r=20, t=10, b=40),
            xaxis_title="월", yaxis_title="건수 (기여도 합산)",
            legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("월·연도 데이터로 월별 추이를 그릴 수 없습니다.")


def render_oos_trend(
    filtered: pd.DataFrame,
    df_full: pd.DataFrame,
    primary_year: int,
    prev_year: int,
    selected_years: list,
    year_col: str,
    month_col: str,
    chart_colors: dict,
    safe_pct,
    completed_keywords: Tuple[str, ...],
) -> None:
    if df_full.empty:
        st.info("OOS 원본 데이터가 없습니다.")
        return
    yc = year_col if year_col in df_full.columns else "연도"
    mc = month_col if month_col in df_full.columns else "월"

    st.markdown(f"#### ◆ 전체 OOS 발생 건수 추이 ({prev_year} vs {primary_year}년 비교)")
    curr_src = filtered if (selected_years and primary_year in selected_years) else df_full[df_full[yc] == primary_year]
    curr_monthly = get_monthly_counts_weighted(curr_src, primary_year, yc, mc)
    prev_data = df_full[df_full[yc] == prev_year]
    prev_monthly = get_monthly_counts_weighted(prev_data, prev_year, yc, mc)

    ymax = max(max(curr_monthly + [1]), max(prev_monthly + [1])) * 1.2
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=MONTH_LABELS, y=prev_monthly, name=f"전년 ({prev_year})",
            mode="lines+markers+text", text=[str(v) if v > 0 else "" for v in prev_monthly],
            textposition="top center", textfont=dict(size=10),
            line=dict(color=chart_colors["gray"], width=2, dash="dash"), marker=dict(size=6),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=MONTH_LABELS, y=curr_monthly, name=f"당년 ({primary_year})",
            mode="lines+markers+text", text=[str(v) if v > 0 else "" for v in curr_monthly],
            textposition="top center", textfont=dict(size=10),
            line=dict(color=chart_colors["blue"], width=2), marker=dict(size=7),
        )
    )
    fig.update_layout(
        height=350, margin=dict(l=40, r=20, t=30, b=40),
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right"),
        yaxis=dict(range=[0, ymax]), plot_bgcolor=CH.CHART_SURFACE,
    )
    fig.update_xaxes(showgrid=True, gridcolor="#f0f0f0")
    fig.update_yaxes(showgrid=True, gridcolor="#f0f0f0")
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    idc = _id_col(filtered) if not filtered.empty else _id_col(df_full)
    col_oot, col_inst = st.columns(2)
    with col_oot:
        st.markdown("#### ◇ OOT 해당 품목")
        if not filtered.empty and "확인된 이벤트 분류" in filtered.columns and "건수기여도" in filtered.columns:
            oot_data = filtered[filtered["확인된 이벤트 분류"] == "OOT"]
            oot_count = round(float(oot_data["건수기여도"].sum()))
            st.caption(f"총 {oot_count}건 (가중)")
            if not oot_data.empty and idc in oot_data.columns:
                cols = [idc, "품목명"] if "품목명" in oot_data.columns else [idc]
                st.dataframe(oot_data[cols].drop_duplicates().head(20), use_container_width=True, hide_index=True, height=300)
            elif not oot_data.empty:
                st.dataframe(oot_data.head(20), use_container_width=True, hide_index=True, height=300)
        else:
            st.info("OOT 데이터가 없습니다.")
    with col_inst:
        st.markdown("#### ◇ Instrument error 기기명 순위")
        if not filtered.empty and "이상발생 원인" in filtered.columns and "기기명" in filtered.columns and "건수기여도" in filtered.columns:
            inst_data = filtered[filtered["이상발생 원인"] == "Instrument error"]
            inst_count = round(float(inst_data["건수기여도"].sum()))
            st.caption(f"총 {inst_count}건")
            if not inst_data.empty:
                device_rank = (
                    inst_data[inst_data["기기명"].notna() & (inst_data["기기명"] != "")]
                    .groupby("기기명")["건수기여도"].sum().sort_values(ascending=False).reset_index()
                )
                device_rank.columns = ["기기명", "발생 건수"]
                device_rank["발생 건수"] = device_rank["발생 건수"].round().astype(int)
                device_rank.insert(0, "순위", range(1, len(device_rank) + 1))
                st.dataframe(device_rank.head(10), use_container_width=True, hide_index=True, height=250)
                if len(device_rank) > 0:
                    top_device = device_rank.iloc[0]
                    st.markdown(
                        f'<div class="highlight-box">▸ <b>최다 발생 기기</b><br>'
                        f'{top_device["기기명"]}: 총 {top_device["발생 건수"]}건 발생</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.info("Instrument error 데이터가 없습니다.")
        else:
            st.info("기기명 또는 원인 데이터가 없습니다.")

    st.divider()
    st.markdown("#### ◆ Analyst error 증감률 (작성자별)")
    if "이상발생 원인" in df_full.columns and "작성자" in df_full.columns and "건수기여도" in df_full.columns:
        ae_curr = filtered[filtered["이상발생 원인"] == "Analyst error"] if not filtered.empty and "이상발생 원인" in filtered.columns else pd.DataFrame()
        ae_prev = df_full[(df_full[yc] == prev_year) & (df_full["이상발생 원인"] == "Analyst error")]
        if not ae_curr.empty or not ae_prev.empty:
            curr_by_author = ae_curr.groupby("작성자")["건수기여도"].sum() if not ae_curr.empty else pd.Series(dtype=float)
            prev_by_author = ae_prev.groupby("작성자")["건수기여도"].sum() if not ae_prev.empty else pd.Series(dtype=float)
            all_authors = sorted(set(curr_by_author.index) | set(prev_by_author.index))
            if all_authors:
                fig2 = make_subplots(specs=[[{"secondary_y": True}]])
                prev_vals = [round(prev_by_author.get(a, 0)) for a in all_authors]
                curr_vals = [round(curr_by_author.get(a, 0)) for a in all_authors]
                pct_change = []
                for p, c in zip(prev_vals, curr_vals):
                    if p > 0:
                        pct_change.append(round((c - p) / p * 100))
                    elif c > 0:
                        pct_change.append(100)
                    else:
                        pct_change.append(0)
                fig2.add_trace(go.Bar(x=all_authors, y=prev_vals, name=f"전년 ({prev_year})", marker_color=chart_colors["green"], opacity=0.8), secondary_y=False)
                fig2.add_trace(go.Bar(x=all_authors, y=curr_vals, name=f"당년 ({primary_year})", marker_color=chart_colors["orange"], opacity=0.8), secondary_y=False)
                fig2.add_trace(go.Scatter(x=all_authors, y=pct_change, name="전년대비", mode="lines+markers", line=dict(color=chart_colors["blue"], width=2), marker=dict(size=6)), secondary_y=True)
                fig2.update_layout(height=380, margin=dict(l=40, r=40, t=30, b=80), barmode="group", legend=dict(orientation="h", y=1.08, x=1, xanchor="right"), plot_bgcolor=CH.CHART_SURFACE)
                fig2.update_yaxes(title_text="건수", secondary_y=False)
                fig2.update_yaxes(title_text="전년대비(%)", secondary_y=True)
                fig2.update_xaxes(tickangle=-45)
                st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Analyst error 데이터가 없습니다.")
    else:
        st.info("작성자·원인 데이터가 없습니다.")

    st.divider()
    st.markdown("#### ◇ Analyst error 비율 (%) — 작성자별 전체 OOS 대비")
    if not filtered.empty and "이상발생 원인" in filtered.columns and "작성자" in filtered.columns and "건수기여도" in filtered.columns:
        author_total = filtered.groupby("작성자")["건수기여도"].sum()
        ae_by_author = filtered[filtered["이상발생 원인"] == "Analyst error"].groupby("작성자")["건수기여도"].sum()
        ratio_data = []
        for author in author_total.index:
            total = round(author_total.get(author, 0))
            ae = round(ae_by_author.get(author, 0))
            pct = safe_pct(ae, total)
            if ae > 0:
                ratio_data.append({"작성자": author, "전체 건수": total, "Analyst Error": ae, "비율 (%)": pct})
        if ratio_data:
            ratio_df = pd.DataFrame(ratio_data).sort_values("비율 (%)", ascending=False)
            st.dataframe(
                ratio_df, use_container_width=True, hide_index=True, height=400,
                column_config={"비율 (%)": st.column_config.ProgressColumn("비율 (%)", min_value=0, max_value=100, format="%.1f%%")},
            )
        else:
            st.info("Analyst error 데이터가 없습니다.")


def render_oos_report(
    filtered: pd.DataFrame,
    chart_colors: dict,
    safe_pct,
    completed_keywords: Tuple[str, ...],
) -> None:
    if filtered.empty:
        st.info("OOS 필터 결과가 없습니다.")
        return
    st.markdown("#### ◆ 경향 요약")
    if "건수기여도" not in filtered.columns:
        st.warning("건수기여도 컬럼이 없습니다.")
        return
    total_w = float(filtered["건수기여도"].sum())
    if "진행상태" in filtered.columns:
        completed_m = filtered["진행상태"].str.contains("|".join(completed_keywords), case=False, na=False)
    else:
        completed_m = filtered["완료여부"] == "C" if "완료여부" in filtered.columns else pd.Series(False, index=filtered.index)
    comp_w = float(filtered.loc[completed_m, "건수기여도"].sum())
    comp_rate = safe_pct(comp_w, total_w)
    summary_data = {
        "항목": ["전체 건수", "완료 건수", "완료율 (%)", "미완료 건수"],
        "값": [f"{total_w:.0f}건", f"{comp_w:.0f}건", f"{comp_rate:.1f}%", f"{total_w - comp_w:.0f}건"],
    }
    st.dataframe(pd.DataFrame(summary_data), use_container_width=True, hide_index=True)
    st.divider()

    st.markdown("#### ◇ 시험종류별 월별 건수")
    if "시험종류" in filtered.columns and "월" in filtered.columns:
        pivot = filtered.groupby(["시험종류", "월"])["건수기여도"].sum().round().unstack(fill_value=0)
        for m in range(1, 13):
            if m not in pivot.columns:
                pivot[m] = 0
        pivot = pivot[sorted(pivot.columns)]
        pivot.columns = MONTH_LABELS
        pivot["합계"] = pivot.sum(axis=1).round().astype(int)
        pivot = pivot.astype(int).sort_values("합계", ascending=False)
        total_row = pivot.sum().to_frame().T
        total_row.index = ["합계"]
        st.dataframe(pd.concat([pivot, total_row]), use_container_width=True)
        st.markdown("##### 시험종류별 월별 건수 차트")
        fig = go.Figure()
        colors = px.colors.qualitative.Set2
        for idx, test_type in enumerate(pivot.index):
            vals = [int(pivot.loc[test_type, ml]) for ml in MONTH_LABELS]
            fig.add_trace(go.Bar(x=MONTH_LABELS, y=vals, name=test_type, marker_color=colors[idx % len(colors)]))
        fig.update_layout(barmode="stack", height=400, margin=dict(l=40, r=20, t=30, b=40), legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"), plot_bgcolor=CH.CHART_SURFACE)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("시험종류·월 데이터 없음")

    st.divider()
    col_prod, col_item = st.columns(2)
    with col_prod:
        st.markdown("#### ◇ 제품별 OOS 건수")
        if "품목명" in filtered.columns:
            prod_data = (
                filtered[filtered["품목명"].notna() & (filtered["품목명"] != "")]
                .groupby("품목명")["건수기여도"].sum().round().sort_values(ascending=True).tail(15).reset_index()
            )
            prod_data.columns = ["품목명", "건수"]
            fig = px.bar(prod_data, x="건수", y="품목명", orientation="h", color_discrete_sequence=[chart_colors["bar"]], text="건수")
            fig.update_layout(height=450, margin=dict(l=0, r=20, t=10, b=10), yaxis_title="")
            fig.update_traces(textposition="outside")
            st.plotly_chart(fig, use_container_width=True)
    with col_item:
        st.markdown("#### ◇ 상위 OOS 시험항목")
        if "시험항목" in filtered.columns:
            item_data = (
                filtered[filtered["시험항목"].notna() & (filtered["시험항목"] != "")]
                .groupby("시험항목")["건수기여도"].sum().round().nlargest(10).reset_index()
            )
            item_data.columns = ["시험항목", "건수"]
            item_data.insert(0, "순위", range(1, len(item_data) + 1))
            st.dataframe(item_data, use_container_width=True, hide_index=True, height=400)

    st.divider()
    st.markdown("#### ◇ 조치현황 / CAPA 현황")
    if "CAPA/Action item 필요여부" in filtered.columns:
        capa_mask_r = filtered["CAPA/Action item 필요여부"].fillna("").str.len() > 0
        capa_no_r = filtered["CAPA/Action item 필요여부"].fillna("").str.contains("No Action", case=False, na=False)
        capa_df = filtered[capa_mask_r & ~capa_no_r]
        if not capa_df.empty:
            capa_types = capa_df["CAPA/Action item 필요여부"].value_counts()
            st.dataframe(pd.DataFrame({"CAPA 유형": capa_types.index, "건수": capa_types.values}), use_container_width=True, hide_index=True)
        else:
            st.info("CAPA 데이터가 없습니다.")
    else:
        st.info("CAPA/Action item 필요여부 컬럼이 없습니다.")

    st.divider()
    col_cause_pie, col_class_pie = st.columns(2)
    with col_cause_pie:
        st.markdown("#### ◆ 이상발생 원인별 분류")
        if "이상발생 원인" in filtered.columns:
            cause_pie = (
                filtered[filtered["이상발생 원인"].notna() & (filtered["이상발생 원인"] != "")]
                .groupby("이상발생 원인")["건수기여도"].sum().round().reset_index()
            )
            cause_pie.columns = ["이상발생 원인", "건수"]
            fig = px.pie(cause_pie, values="건수", names="이상발생 원인", color_discrete_sequence=px.colors.qualitative.Set3, hole=0.35)
            fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=10))
            fig.update_traces(textinfo="label+value+percent", textfont_size=11)
            st.plotly_chart(fig, use_container_width=True)
    with col_class_pie:
        st.markdown("#### ◆ 확인된 이벤트 분류별")
        if "확인된 이벤트 분류" in filtered.columns:
            class_pie = (
                filtered[filtered["확인된 이벤트 분류"].notna() & (filtered["확인된 이벤트 분류"] != "")]
                .groupby("확인된 이벤트 분류")["건수기여도"].sum().round().reset_index()
            )
            class_pie.columns = ["이벤트 분류", "건수"]
            fig = px.pie(class_pie, values="건수", names="이벤트 분류", color_discrete_sequence=px.colors.qualitative.Pastel, hole=0.35)
            fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=10))
            fig.update_traces(textinfo="label+value+percent", textfont_size=11)
            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.markdown("#### ◆ 이상발생 원인 분류 분석")
    if "이상발생 원인" in filtered.columns:
        cause_analysis = (
            filtered[filtered["이상발생 원인"].notna() & (filtered["이상발생 원인"] != "")]
            .groupby("이상발생 원인")["건수기여도"].sum().round().sort_values(ascending=False).reset_index()
        )
        cause_analysis.columns = ["이상발생 원인", "건수"]
        cause_total = cause_analysis["건수"].sum()
        cause_analysis["비율 (%)"] = cause_analysis["건수"].apply(lambda x: safe_pct(x, cause_total))
        cause_analysis["건수"] = cause_analysis["건수"].astype(int)
        st.dataframe(
            cause_analysis, use_container_width=True, hide_index=True,
            column_config={"비율 (%)": st.column_config.ProgressColumn("비율 (%)", min_value=0, max_value=100, format="%.1f%%")},
        )


def render_oos_gmp(
    filtered: pd.DataFrame,
    df_full: pd.DataFrame,
    primary_year: int,
    prev_year: int,
    year_col: str,
    month_col: str,
    chart_colors: dict,
    safe_pct,
    completed_keywords: Tuple[str, ...],
) -> None:
    if filtered.empty:
        st.info("OOS 필터 결과가 없습니다.")
        return
    yc = year_col if year_col in filtered.columns else "연도"
    mc = month_col if month_col in filtered.columns else "월"

    kpi_col, chart_col, ae_col = st.columns([1.2, 2.5, 1.5])
    total_events = round(float(filtered["건수기여도"].sum())) if "건수기여도" in filtered.columns else 0
    if "진행상태" in filtered.columns:
        completed_ev = round(float(filtered[filtered["진행상태"].str.contains("|".join(completed_keywords), case=False, na=False)]["건수기여도"].sum()))
    else:
        completed_ev = round(float(filtered[filtered["완료여부"] == "C"]["건수기여도"].sum())) if "완료여부" in filtered.columns else 0
    ev_comp_rate = safe_pct(completed_ev, total_events)

    capa_df2 = pd.DataFrame()
    if "CAPA/Action item 필요여부" in filtered.columns:
        capa_m2 = filtered["CAPA/Action item 필요여부"].fillna("").str.len() > 0
        capa_no2 = filtered["CAPA/Action item 필요여부"].fillna("").str.contains("No Action", case=False, na=False)
        capa_df2 = filtered[capa_m2 & ~capa_no2]
    capa_cnt2 = round(float(capa_df2["건수기여도"].sum())) if not capa_df2.empty and "건수기여도" in capa_df2.columns else 0
    if not capa_df2.empty and "진행상태" in capa_df2.columns and "건수기여도" in capa_df2.columns:
        capa_prog2 = safe_pct(
            round(float(capa_df2[capa_df2["진행상태"].str.contains("|".join(completed_keywords), case=False, na=False)]["건수기여도"].sum())),
            capa_cnt2,
        )
    else:
        capa_prog2 = 0.0

    with kpi_col:
        st.markdown("##### 시험실 이벤트 발생 현황")
        st.markdown(
            f"""
        | 항목 | 값 |
        |------|---:|
        | 전체 건수 | **{total_events}건** |
        | 완료 건수 | **{completed_ev}건** |
        | 완료율 (%) | **{ev_comp_rate:.0f}%** |
        | CAPA 건수 | **{capa_cnt2}건** |
        | CAPA 진행률 | **{capa_prog2:.1f}%** |
        """
        )
    with chart_col:
        st.markdown("##### 월별 시험실이벤트 건수")
        monthly_curr = get_monthly_counts_weighted(filtered, primary_year, yc, mc)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=MONTH_LABELS, y=monthly_curr, marker_color=chart_colors["bar"], text=monthly_curr, textposition="outside", textfont=dict(size=11)))
        max_val = max(monthly_curr) if monthly_curr else 1
        fig.update_layout(height=320, margin=dict(l=30, r=10, t=10, b=30), yaxis=dict(range=[0, max_val * 1.25]), plot_bgcolor=CH.CHART_SURFACE)
        st.plotly_chart(fig, use_container_width=True)
    with ae_col:
        st.markdown("##### Analyst error 감소율")
        if "이상발생 원인" in df_full.columns and "건수기여도" in df_full.columns and yc in df_full.columns:
            ae_prev_cnt = round(float(df_full[(df_full[yc] == prev_year) & (df_full["이상발생 원인"] == "Analyst error")]["건수기여도"].sum()))
            ae_curr_cnt = round(float(filtered[filtered["이상발생 원인"] == "Analyst error"]["건수기여도"].sum())) if "이상발생 원인" in filtered.columns else 0
            if ae_prev_cnt > 0 or ae_curr_cnt > 0:
                reduction = safe_pct(ae_prev_cnt - ae_curr_cnt, ae_prev_cnt) if ae_prev_cnt > 0 else 0.0
                fig_ae = go.Figure()
                fig_ae.add_trace(
                    go.Bar(
                        x=[str(prev_year), str(primary_year)], y=[ae_prev_cnt, ae_curr_cnt],
                        marker_color=[chart_colors["gray"], chart_colors["blue"]],
                        text=[str(ae_prev_cnt), str(ae_curr_cnt)], textposition="outside", textfont=dict(size=14), width=0.5,
                    )
                )
                if ae_prev_cnt > 0:
                    fig_ae.add_annotation(
                        x=0.5, y=max(ae_prev_cnt, ae_curr_cnt) * 0.85, xref="paper",
                        text=f'<b>감소율: -{reduction:.0f}%</b>' if reduction > 0 else f'<b>증가율: +{abs(reduction):.0f}%</b>',
                        font=dict(size=14, color=chart_colors["red"]), showarrow=False,
                    )
                max_ae = max(ae_prev_cnt, ae_curr_cnt, 1)
                fig_ae.update_layout(height=280, margin=dict(l=20, r=10, t=30, b=30), yaxis=dict(range=[0, max_ae * 1.4]), plot_bgcolor=CH.CHART_SURFACE, bargap=0.3)
                fig_ae.add_annotation(
                    x=0.5, y=-0.18, xref="paper", yref="paper",
                    text=f"<span style='color:gray'>■ 전년 ({prev_year})</span>  <span style='color:{chart_colors['blue']}'>■ 당년 ({primary_year})</span>",
                    showarrow=False, font=dict(size=10),
                )
                st.plotly_chart(fig_ae, use_container_width=True)
            else:
                st.info("Analyst error 데이터가 없습니다.")
        else:
            st.info("데이터 없음")

    st.divider()
    col_major, col_minor, col_test_type = st.columns(3)
    with col_major:
        st.markdown("##### 시험실이벤트 원인 대분류")
        if "확인된 이벤트 분류" in filtered.columns and "건수기여도" in filtered.columns:
            major_data = (
                filtered[filtered["확인된 이벤트 분류"].notna() & (filtered["확인된 이벤트 분류"] != "")]
                .groupby("확인된 이벤트 분류")["건수기여도"].sum().round().sort_values(ascending=False).reset_index()
            )
            major_data.columns = ["분류", "건수"]
            fig = px.bar(major_data, x="분류", y="건수", color_discrete_sequence=[chart_colors["bar"]], text="건수")
            fig.update_layout(height=350, margin=dict(l=30, r=10, t=10, b=30), xaxis_title="", yaxis_title="", plot_bgcolor=CH.CHART_SURFACE)
            fig.update_traces(textposition="outside")
            st.plotly_chart(fig, use_container_width=True)
    with col_minor:
        st.markdown("##### 시험실이벤트 원인 소분류")
        if "이상발생 원인" in filtered.columns and "건수기여도" in filtered.columns:
            minor_data = (
                filtered[filtered["이상발생 원인"].notna() & (filtered["이상발생 원인"] != "")]
                .groupby("이상발생 원인")["건수기여도"].sum().round().sort_values(ascending=True).reset_index()
            )
            minor_data.columns = ["원인", "건수"]
            fig = px.bar(minor_data, x="건수", y="원인", orientation="h", color_discrete_sequence=[chart_colors["bar"]], text="건수")
            fig.update_layout(height=350, margin=dict(l=0, r=20, t=10, b=10), yaxis_title="", xaxis_title="", plot_bgcolor=CH.CHART_SURFACE)
            fig.update_traces(textposition="outside")
            st.plotly_chart(fig, use_container_width=True)
    with col_test_type:
        st.markdown("##### 시험종류별 시험실이벤트 발생 건수")
        if "시험종류" in filtered.columns and "건수기여도" in filtered.columns:
            ttype_data = (
                filtered[filtered["시험종류"].notna() & (filtered["시험종류"] != "")]
                .groupby("시험종류")["건수기여도"].sum().round().sort_values(ascending=False).reset_index()
            )
            ttype_data.columns = ["시험종류", "건수"]
            fig = px.bar(ttype_data, x="시험종류", y="건수", color_discrete_sequence=[chart_colors["dark_gray"]], text="건수")
            fig.update_layout(height=350, margin=dict(l=30, r=10, t=10, b=60), xaxis_title="", yaxis_title="", plot_bgcolor=CH.CHART_SURFACE)
            fig.update_traces(textposition="outside")
            fig.update_xaxes(tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
