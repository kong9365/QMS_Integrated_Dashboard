# -*- coding: utf-8 -*-
"""qms_pro.domain.attribution — 품목/lot 체인 귀속(전파) 순수 도메인 함수 (Task 3.2b).

목적
----
품질 계보(OOS·일탈 뿌리 → 조사·CAPA·Action 자식)에서 자식 레코드가 품목/lot 를 **0% 보유**
(RECON 실측)하는 공백을, **부모(조상)의 값으로 전파(상속)** 해 메우기 위한 파생 컬럼을 만든다.

절대 규칙
--------
- **원본 불변**: 기존 ``품목코드``/``품목명``/``제조번호`` 컬럼 값은 **절대 덮어쓰지 않는다.**
  신규 파생 컬럼 4종만 추가한다: ``품목코드_귀속``·``품목명_귀속``·``제조번호_귀속``·``귀속출처``.
- **신규 순회/연계 로직 0**: 부모-자식 관계는 기존 ``data_access.build_ctx``
  (=``qms_linkage.build_linkage``)와 ``qms_linkage.resolve_chain`` 을 **읽기전용 재사용**한다.
- **추측 금지**: 채울 조상 값이 없으면 빈값(미상) 유지. 조상 품목값이 2개 이상 충돌하면 미분류.
- **멱등**: 파생은 항상 원본 컬럼에서만 재계산하므로 두 번 적용해도 동일 결과.

전파 규칙(자식 → 가장 가까운 '값 보유' 조상 상속)
------------------------------------------------
품목(코드+명은 **한 쌍**으로 함께 전파):
  1. 자체 ``품목코드`` 보유 → 그대로(귀속출처=**자체보유**).
  2. 비었으면 조상(가까운 순)에서 **서로 다른 품목코드**를 수집:
     - 1종 → 그 조상의 (코드,명) 상속(귀속출처=**상속**).
     - 2종+ 충돌 → ``복수(미분류)``(귀속출처=**복수(미분류)**).
     - 0종 → ``전사/미분류``(귀속출처=**미분류**).
lot(``제조번호``) — 품목과 독립, **추측 금지**:
  1. 자체 보유 → 그대로.
  2. 비었으면 조상에서 distinct lot 가 정확히 1종일 때만 상속. 0종·충돌 → 빈값('미상').

비고
----
- ``귀속출처`` 는 **품목 귀속 축**(APQR 1차 축)을 기술한다. lot 출처는 ``제조번호_귀속`` 의
  값 유무(빈값=미상)로 드러난다.
- 함수는 입력 DF 를 변형하지 않고 **새 DataFrame(복사본)** 에 컬럼을 추가해 반환한다.
"""
from __future__ import annotations

import pandas as pd

# 기존 연계 순회 재사용(읽기전용). _norm_prno 는 ctx 키와 동일 정규화를 위해 필수.
from qms_linkage import resolve_chain as _resolve_chain, _norm_prno as _norm

# 원본(읽기 전용) 컬럼명
SRC_CODE = "품목코드"
SRC_NAME = "품목명"
SRC_LOT = "제조번호"

# 신규 파생 컬럼명
COL_CODE = "품목코드_귀속"
COL_NAME = "품목명_귀속"
COL_LOT = "제조번호_귀속"
COL_SOURCE = "귀속출처"
DERIVED_COLS = (COL_CODE, COL_NAME, COL_LOT, COL_SOURCE)

# 귀속출처 값(품목 축)
SRC_SELF = "자체보유"
SRC_INHERIT = "상속"
SRC_NONE = "미분류"
SRC_MULTI = "복수(미분류)"
# 버킷 라벨
LABEL_UNCLASSIFIED = "전사/미분류"
LABEL_MULTI = "복수(미분류)"


def _clean(v) -> str:
    """값 → 정리 문자열. 결측/공백/'nan' 류는 ""."""
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:  # noqa: BLE001
        pass
    s = str(v).strip()
    if s.lower() in ("", "nan", "none", "null", "<na>", "nat"):
        return ""
    return s


def _clean_code(v) -> str:
    """코드(품목코드/제조번호)용 — float 저장 시 끝의 '.0' 제거(정수 코드 보존)."""
    s = _clean(v)
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def build_value_map(all_dfs: dict) -> dict:
    """관리번호(정규화) → {품목코드, 품목명, 제조번호} 룩업(첫 행 기준). 원본 읽기만.

    동일 관리번호가 시험전개로 여러 행이면 첫 행만 사용(linkage 빌더와 동일 규칙).
    """
    vmap: dict[str, dict] = {}
    for df in all_dfs.values():
        if df is None or getattr(df, "empty", True) or "관리번호" not in df.columns:
            continue
        prnos = df["관리번호"]
        codes = df[SRC_CODE] if SRC_CODE in df.columns else None
        names = df[SRC_NAME] if SRC_NAME in df.columns else None
        lots = df[SRC_LOT] if SRC_LOT in df.columns else None
        n = len(df)
        for i in range(n):
            prno = _norm(prnos.iat[i])
            if not prno or prno in vmap:
                continue
            vmap[prno] = {
                SRC_CODE: _clean_code(codes.iat[i]) if codes is not None else "",
                SRC_NAME: _clean(names.iat[i]) if names is not None else "",
                SRC_LOT: _clean_code(lots.iat[i]) if lots is not None else "",
            }
    return vmap


def _ancestor_prnos(ctx, prno: str) -> list[str]:
    """가까운 순 조상 관리번호 목록 — 기존 resolve_chain(읽기전용) 재사용."""
    rows = _resolve_chain(ctx, prno, direction="ancestors")
    out: list[str] = []
    for r in rows:
        a = _norm(r.get("관리번호"))
        if a:
            out.append(a)
    return out


def attribute_one(prno: str, vmap: dict, ctx) -> dict:
    """단일 관리번호의 귀속 4값 계산. (품목코드_귀속, 품목명_귀속, 제조번호_귀속, 귀속출처)."""
    self_v = vmap.get(prno) or {}
    self_code = _clean_code(self_v.get(SRC_CODE, ""))
    self_name = _clean(self_v.get(SRC_NAME, ""))
    self_lot = _clean_code(self_v.get(SRC_LOT, ""))

    # 조상은 품목/lot 어느 쪽이든 비었을 때만 1회 조회(캐시)
    _anc: list[str] | None = None

    def anc() -> list[str]:
        nonlocal _anc
        if _anc is None:
            _anc = _ancestor_prnos(ctx, prno)
        return _anc

    # ── 품목(코드+명 한 쌍) ──
    if self_code:
        code, name, source = self_code, self_name, SRC_SELF
    else:
        distinct: list[str] = []
        name_of: dict[str, str] = {}
        for a in anc():
            av = vmap.get(a)
            if not av:
                continue
            ac = _clean_code(av.get(SRC_CODE, ""))
            if ac and ac not in distinct:
                distinct.append(ac)
                name_of[ac] = _clean(av.get(SRC_NAME, ""))
        if not distinct:
            code, name, source = LABEL_UNCLASSIFIED, LABEL_UNCLASSIFIED, SRC_NONE
        elif len(distinct) == 1:
            code, name, source = distinct[0], name_of[distinct[0]], SRC_INHERIT
        else:
            code, name, source = LABEL_MULTI, LABEL_MULTI, SRC_MULTI

    # ── lot(제조번호) — 독립, 추측 금지 ──
    if self_lot:
        lot = self_lot
    else:
        dl: list[str] = []
        for a in anc():
            av = vmap.get(a)
            if not av:
                continue
            al = _clean_code(av.get(SRC_LOT, ""))
            if al and al not in dl:
                dl.append(al)
        lot = dl[0] if len(dl) == 1 else ""   # 0종·충돌 → 미상(빈값)

    return {COL_CODE: code, COL_NAME: name, COL_LOT: lot, COL_SOURCE: source}


def attribute_dataframes(all_dfs: dict, *, ctx=None, value_map: dict | None = None) -> dict:
    """전 프로젝트 DF 에 귀속 파생 컬럼 4종을 추가한 **새 dict**(복사본) 반환. 원본 불변·멱등.

    Parameters
    ----------
    all_dfs : {project_key: DataFrame}
    ctx : LinkageContext. None 이면 기존 ``data_access.build_ctx`` 로 생성(읽기전용 재사용).
    value_map : 사전 계산된 룩업(선택). None 이면 ``build_value_map`` 으로 생성.
    """
    if ctx is None:
        from qms_pro.services import data_access as _DA   # build_ctx 재사용(상향 import 아님)
        ctx = _DA.build_ctx(all_dfs)
    vmap = value_map if value_map is not None else build_value_map(all_dfs)

    cache: dict[str, dict] = {}

    def attr(prno: str) -> dict:
        r = cache.get(prno)
        if r is None:
            r = attribute_one(prno, vmap, ctx)
            cache[prno] = r
        return r

    out: dict = {}
    for key, df in all_dfs.items():
        if df is None:
            out[key] = df
            continue
        nd = df.copy()
        if df.empty or "관리번호" not in df.columns:
            # 스키마 일관성: 빈/무 관리번호 DF 에도 컬럼 추가(미분류)
            nd[COL_CODE] = pd.Series([LABEL_UNCLASSIFIED] * len(nd), index=nd.index, dtype=object)
            nd[COL_NAME] = pd.Series([LABEL_UNCLASSIFIED] * len(nd), index=nd.index, dtype=object)
            nd[COL_LOT] = pd.Series([""] * len(nd), index=nd.index, dtype=object)
            nd[COL_SOURCE] = pd.Series([SRC_NONE] * len(nd), index=nd.index, dtype=object)
            out[key] = nd
            continue
        recs = [attr(_norm(p)) for p in nd["관리번호"]]
        nd[COL_CODE] = [r[COL_CODE] for r in recs]
        nd[COL_NAME] = [r[COL_NAME] for r in recs]
        nd[COL_LOT] = [r[COL_LOT] for r in recs]
        nd[COL_SOURCE] = [r[COL_SOURCE] for r in recs]
        out[key] = nd
    return out


__all__ = [
    "attribute_dataframes",
    "attribute_one",
    "build_value_map",
    "DERIVED_COLS",
    "COL_CODE", "COL_NAME", "COL_LOT", "COL_SOURCE",
    "SRC_SELF", "SRC_INHERIT", "SRC_NONE", "SRC_MULTI",
    "LABEL_UNCLASSIFIED", "LABEL_MULTI",
]
