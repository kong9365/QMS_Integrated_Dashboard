# -*- coding: utf-8 -*-
"""qms_flow.py — '품질이슈 → 후속조치 흐름' Sankey 의 흐름 값 산출(순수함수).

설계 원칙
- **신규 순수함수만 추가**. 데이터 수집/연계 인덱스(build_and_apply_linkage)·건수기여도 가중
  로직은 일절 수정하지 않는다. 본 모듈은 호출부가 넘겨준 (이미 필터된) df 의 기존 컬럼
  (`관리번호`·`상위번호`·`건수기여도`)만 읽어 흐름을 도출한다.

모델(실데이터 검증 기반)
- 이슈(OOS·일탈)는 상위번호 그래프상 **루트**. 후속조치(조사/CAPA/CAPA AI)는 `상위번호`로
  부모를 가리킨다(조사·CAPA는 주로 일탈 직속, CAPA AI 는 CAPA 의 손자).
- 이슈 **관리번호 1건 = 1버킷**으로 분할(하위체인 transitive 에 존재하는 후속조치 타입을
  우선순위 **CAPA AI > CAPA > 조사 > 종결·조치불요** 로 배정). → Sankey 양변 합이 자동 균형.
- 흐름 값 = 그 버킷에 속한 이슈들의 **건수기여도 합**(이슈 df 기준). 후속조치 df 는 연결만 제공.

공개 API
- compute_quality_flow(foos, fdev, finv, fcapa, fcapaai) -> (flows, drill, totals)
- crosstab_quality_flow(...) -> pandas.DataFrame  (From×To 교차표, 가중)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

import pandas as pd

ISSUE_LABELS = ("OOS", "일탈")
BUCKETS = ("조사", "CAPA", "CAPA AI", "종결·조치불요")
_PRIORITY = ("CAPA AI", "CAPA", "조사")   # 하위체인에 더 무거운 후속조치가 있으면 그쪽으로 귀속


def _ids(df) -> set:
    if df is None or getattr(df, "empty", True) or "관리번호" not in df.columns:
        return set()
    return set(df["관리번호"].dropna().astype(str))


def _build_children_and_kind(finv, fcapa, fcapaai):
    """후속조치 df 들의 상위번호로 parent→children 역맵 + 노드 타입(kind) 구성."""
    kind: Dict[str, str] = {}
    for _df, _k in ((finv, "조사"), (fcapa, "CAPA"), (fcapaai, "CAPA AI")):
        for _x in _ids(_df):
            kind[_x] = _k
    children: Dict[str, List[str]] = defaultdict(list)
    for _df in (finv, fcapa, fcapaai):
        if _df is None or _df.empty or "상위번호" not in _df.columns or "관리번호" not in _df.columns:
            continue
        for _c, _p in zip(_df["관리번호"].astype(str), _df["상위번호"].astype(str)):
            if _p and _p not in ("0", "nan", "None", "0.0", ""):
                children[_p].append(_c)
    return children, kind


def _bucket_of(prno: str, children: Dict[str, List[str]], kind: Dict[str, str]) -> str:
    """이슈 prno 의 하위체인(transitive)에 존재하는 후속조치 타입 → 우선순위 버킷."""
    seen, stack, types = set(), list(children.get(prno, [])), set()
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        if x in kind:
            types.add(kind[x])
        stack.extend(children.get(x, []))
    for t in _PRIORITY:
        if t in types:
            return t
    return "종결·조치불요"


def compute_quality_flow(foos, fdev, finv, fcapa, fcapaai
                         ) -> Tuple[Dict[Tuple[str, str], float],
                                    Dict[Tuple[str, str], List[str]],
                                    Dict[str, float]]:
    """품질이슈 → 후속조치 흐름.

    반환:
      flows  : {(이슈라벨, 버킷): 건수기여도 가중값(float)}  — 누락 조합은 0 으로 간주
      drill  : {(이슈라벨, 버킷): [관리번호, ...]}            — 드릴다운용
      totals : {이슈라벨: 건수기여도 합}
    """
    children, kind = _build_children_and_kind(finv, fcapa, fcapaai)
    flows: Dict[Tuple[str, str], float] = {}
    drill: Dict[Tuple[str, str], List[str]] = {}
    totals: Dict[str, float] = {}
    for label, df in (("OOS", foos), ("일탈", fdev)):
        totals[label] = 0.0
        if df is None or df.empty or "관리번호" not in df.columns:
            continue
        w = (pd.to_numeric(df["건수기여도"], errors="coerce").fillna(0)
             if "건수기여도" in df.columns else pd.Series(1.0, index=df.index))
        g = pd.DataFrame({"관리번호": df["관리번호"].astype(str), "w": w}).groupby("관리번호")["w"].sum()
        totals[label] = float(g.sum())
        for prno, wt in g.items():
            b = _bucket_of(prno, children, kind)
            key = (label, b)
            flows[key] = flows.get(key, 0.0) + float(wt)
            drill.setdefault(key, []).append(prno)
    return flows, drill, totals


def crosstab_quality_flow(foos, fdev, finv, fcapa, fcapaai) -> pd.DataFrame:
    """From(이슈) × To(후속조치) 교차표(건수기여도 가중, 정수 반올림). 합계 행/열 포함."""
    flows, _, totals = compute_quality_flow(foos, fdev, finv, fcapa, fcapaai)
    mat = pd.DataFrame(0, index=list(ISSUE_LABELS), columns=list(BUCKETS), dtype=float)
    for (label, bucket), v in flows.items():
        if label in mat.index and bucket in mat.columns:
            mat.loc[label, bucket] = v
    mat = mat.round().astype(int)
    mat["합계"] = mat.sum(axis=1)
    total_row = mat.sum(axis=0).to_frame().T
    total_row.index = ["합계"]
    out = pd.concat([mat, total_row])
    out.index.name = "이슈 \\ 후속조치"
    return out.reset_index()
