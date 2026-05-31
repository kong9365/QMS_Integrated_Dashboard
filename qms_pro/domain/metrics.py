# -*- coding: utf-8 -*-
"""qms_pro.domain.metrics — QMS 공통 지표(가중 건수/완료/기한초과) 순수 함수.

이 모듈은 ``QMS_Integrated_Dashboard_v2.py`` 의 지표 함수를 **결과 동등성을 유지하며
그대로 이전**한 것이다(Phase 2-1). 계산 로직은 원본과 동일해야 하며, 변경 시
회귀 기준선(baseline_*.json)과 수치가 달라질 수 있으므로 주의한다.

원본 위치(이전 시점): QMS_Integrated_Dashboard_v2.py
  - COMPLETED_KEYWORDS         : 189
  - safe_pct                   : 184
  - weighted_metric_total      : 192
  - weighted_metric_completed  : 201
  - weighted_metric_overdue    : 221
  - _wcount                    : 245
  - _wgroupby                  : 260
  - _num_series                : 292

핵심 도메인 규칙(변경 금지)
---------------------------
- 건수는 ``건수기여도`` 합(동시분석 행은 문서당 1건에 근사). 컬럼 없으면 행 수.
- 완료는 ``진행상태`` 키워드 우선, 없으면 ``완료여부 == 'C'``.
- 기한초과는 ``D-day < 0``.
"""
from __future__ import annotations

import pandas as pd

# 원본: QMS_Integrated_Dashboard_v2.py:189
# QMS_GUI/QMS_Dashboard.py COMPLETED_KEYWORDS 와 동일
COMPLETED_KEYWORDS = ("시험실 이벤트 종료", "종료", "완료")


def safe_pct(a, b):
    """백분율(소수 1자리). 분모가 0 이하이면 0.0."""
    # 원본: QMS_Integrated_Dashboard_v2.py:184
    return round((a / b * 100), 1) if b > 0 else 0.0


def weighted_metric_total(df: pd.DataFrame) -> float:
    """QMS_Dashboard '현황' 탭 total_weighted: 동시분석 행은 건수기여도로 문서당 1건에 근사."""
    # 원본: QMS_Integrated_Dashboard_v2.py:192
    if df.empty:
        return 0.0
    if "건수기여도" in df.columns:
        return float(pd.to_numeric(df["건수기여도"], errors="coerce").fillna(0).sum())
    return float(len(df))


def weighted_metric_completed(df: pd.DataFrame) -> float:
    """진행상태 키워드 우선, 없으면 완료여부=='C' (건수기여도 있으면 가중)."""
    # 원본: QMS_Integrated_Dashboard_v2.py:201
    if df.empty:
        return 0.0
    if "건수기여도" in df.columns:
        w = pd.to_numeric(df["건수기여도"], errors="coerce").fillna(0)
        if "진행상태" in df.columns:
            m = df["진행상태"].str.contains("|".join(COMPLETED_KEYWORDS), case=False, na=False)
            return float(w[m].sum())
        if "완료여부" in df.columns:
            return float(w[df["완료여부"] == "C"].sum())
        return 0.0
    if "진행상태" in df.columns:
        m = df["진행상태"].str.contains("|".join(COMPLETED_KEYWORDS), case=False, na=False)
        return float(m.sum())
    if "완료여부" in df.columns:
        return float((df["완료여부"] == "C").sum())
    return 0.0


def weighted_metric_overdue(df: pd.DataFrame) -> float:
    """기한초과(D-day < 0) 가중 건수. D-day 컬럼 없으면 0."""
    # 원본: QMS_Integrated_Dashboard_v2.py:221
    if df.empty or "D-day" not in df.columns:
        return 0.0
    m = df["D-day"].notna() & (df["D-day"] < 0)
    if "건수기여도" in df.columns:
        w = pd.to_numeric(df["건수기여도"], errors="coerce").fillna(0)
        return float(w[m].sum())
    return float(m.sum())


def _wcount(df: pd.DataFrame, mask=None) -> int:
    """건수기여도 합 → 정수 반올림. mask 가 주어지면 필터 후 합산.

    건수기여도 없으면 행 수(고유화 없이)로 fallback.
    """
    # 원본: QMS_Integrated_Dashboard_v2.py:245
    if df is None or df.empty:
        return 0
    sub = df if mask is None else df[mask]
    if sub.empty:
        return 0
    if "건수기여도" in sub.columns:
        return int(round(float(pd.to_numeric(sub["건수기여도"], errors="coerce").fillna(0).sum())))
    return int(len(sub))


def _wgroupby(df: pd.DataFrame, by, name: str = "건수", round_int: bool = True) -> pd.DataFrame:
    """그룹별 건수기여도 합. 건수기여도 없으면 .size() 로 fallback.

    - by: 단일 str 또는 list[str]
    - round_int=True 면 정수로 반올림, False 면 float 유지.
    """
    # 원본: QMS_Integrated_Dashboard_v2.py:260
    if df is None or df.empty:
        cols = [by] if isinstance(by, str) else list(by)
        return pd.DataFrame(columns=cols + [name])
    if "건수기여도" in df.columns:
        g = df.groupby(by, dropna=False)["건수기여도"].sum().reset_index()
        g = g.rename(columns={"건수기여도": name})
    else:
        g = df.groupby(by, dropna=False).size().reset_index(name=name)
    if round_int and name in g.columns:
        g[name] = pd.to_numeric(g[name], errors="coerce").fillna(0).round().astype(int)
    return g


def _num_series(s: pd.Series, default: float = 0.0) -> pd.Series:
    """혼합 dtype 컬럼을 숫자 Series 로 안전 변환."""
    # 원본: QMS_Integrated_Dashboard_v2.py:292
    return pd.to_numeric(s, errors="coerce").fillna(default)
