# -*- coding: utf-8 -*-
"""QMS 부모-자식 체인 연계 분석 모듈.

리스트 API 응답의 ``parentPrno`` (== ``상위번호``) 를 근거로 전체 프로젝트
레코드에 대해 부모-자식 관계 그래프를 만들고, 각 레코드에 대해 체인 전체
(자신 → 손자 → …) 의 요약 지표와 직계 부모·최상위 조상 정보를 제공한다.

사용 흐름
---------
1. ``qms_fetch_uncached`` 에서 모든 프로젝트의 list-parser 결과(+detail 파서 결과)
   리스트를 하나로 합쳐 ``rows`` 를 만든다.
2. ``build_linkage(rows)`` 로 ``LinkageContext`` 를 생성한다.
3. 각 DataFrame 행에 대해 ``summarize_children(ctx, prno)`` /
   ``summarize_parent(ctx, prno)`` 를 호출해 연계 컬럼을 채운다.

필수 입력 컬럼 (각 row dict)
----------------------------
- ``관리번호``   : 고유 ID (prno)
- ``상위번호``   : 부모 관리번호 (없으면 0 / None / "")
- ``프로젝트``   : 프로젝트 키 (e.g. ``oos``, ``deviation``, ``investigation``, ``capa`` 등)
- ``완료여부``   : taskCondition ('C' 이면 종결 완료, 'T' 는 진행중)
- ``진행상태``   : status (표시용)
- ``제목``       : 제목
- ``기한일``     : limitDate (YYYY-MM-DD 가정, 지연일 계산용)
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable


# ─── 내부 유틸 ────────────────────────────────────────────────────────────

def _norm_prno(value) -> str:
    """관리번호/상위번호 → 비교 가능한 문자열. 빈값/0 은 "" 로 정규화."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s in ("0", "None", "null", "NULL"):
        return ""
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _is_closed(row: dict) -> bool:
    """taskCondition == 'C' 를 종결(완료)로 간주.

    QMS 공식 문서 기준: taskCondition C=완료, T=진행중. 대시보드 본체도 완료여부=='C'를
    완료로 사용한다. (과거에는 'T'를 종결로 보던 인버전 버그가 있었고,
    qms_linkage_ct_validation.py 의 검증·실데이터 스팟체크로 'C'가 옳음을 확인해 교정함.)
    """
    if not isinstance(row, dict):
        return False
    cond = str(row.get("완료여부", "") or "").strip().upper()
    return cond == "C"


def _today() -> date:
    return datetime.now().date()


def _days_overdue(limit_str: str) -> int | None:
    """기한일 문자열(YYYY-MM-DD)과 오늘의 차이. 기한일 없거나 파싱 실패시 None."""
    s = str(limit_str or "").strip()[:10]
    if not s:
        return None
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None
    return (_today() - d).days


# ─── 컨텍스트 ─────────────────────────────────────────────────────────────

@dataclass
class LinkageContext:
    """부모-자식 인덱스 + 체인 요약 캐시를 담는 컨테이너."""

    rows: list[dict] = field(default_factory=list)
    by_prno: dict[str, dict] = field(default_factory=dict)
    children_by_parent: dict[str, list[str]] = field(default_factory=dict)
    closure_set: set[str] = field(default_factory=set)

    # 캐시
    _descendants_cache: dict[str, list[str]] = field(default_factory=dict)
    _ancestors_cache: dict[str, list[str]] = field(default_factory=dict)
    _children_summary_cache: dict[str, dict] = field(default_factory=dict)
    _parent_summary_cache: dict[str, dict] = field(default_factory=dict)


# ─── 빌더 ────────────────────────────────────────────────────────────────

def build_linkage(all_rows: Iterable[dict]) -> LinkageContext:
    """관리번호 기반 부모-자식 인덱스 구성.

    동일 ``관리번호`` 가 여러 번 등장하면(예: OOS 가 testInfo 로 중복 전개) 첫
    레코드만 사용한다(리스트 기본 정보 기준).
    """
    ctx = LinkageContext()
    children_map: dict[str, list[str]] = defaultdict(list)

    for row in all_rows:
        if not isinstance(row, dict):
            continue
        prno = _norm_prno(row.get("관리번호"))
        if not prno:
            continue
        if prno in ctx.by_prno:
            continue  # 중복 전개 행은 스킵
        ctx.by_prno[prno] = row
        ctx.rows.append(row)

        parent = _norm_prno(row.get("상위번호"))
        if parent:
            children_map[parent].append(prno)

        if _is_closed(row):
            ctx.closure_set.add(prno)

    ctx.children_by_parent = dict(children_map)
    return ctx


# ─── 체인 해석 ────────────────────────────────────────────────────────────

def _descendants(ctx: LinkageContext, prno: str) -> list[str]:
    """BFS 로 자신 밑 모든 자손(직계+손자…) 관리번호 목록을 반환. 자신은 제외."""
    if prno in ctx._descendants_cache:
        return ctx._descendants_cache[prno]
    result: list[str] = []
    visited: set[str] = set()
    queue: deque[str] = deque(ctx.children_by_parent.get(prno, []))
    while queue:
        cur = queue.popleft()
        if cur in visited or cur == prno:
            continue
        visited.add(cur)
        result.append(cur)
        queue.extend(ctx.children_by_parent.get(cur, []))
    ctx._descendants_cache[prno] = result
    return result


def _ancestors(ctx: LinkageContext, prno: str) -> list[str]:
    """자신에서 루트 방향으로 조상 목록 (가까운 순). 자신은 제외."""
    if prno in ctx._ancestors_cache:
        return ctx._ancestors_cache[prno]
    result: list[str] = []
    visited: set[str] = {prno}
    cur_row = ctx.by_prno.get(prno)
    while cur_row:
        parent = _norm_prno(cur_row.get("상위번호"))
        if not parent or parent in visited:
            break
        visited.add(parent)
        result.append(parent)
        cur_row = ctx.by_prno.get(parent)
    ctx._ancestors_cache[prno] = result
    return result


def resolve_chain(
    ctx: LinkageContext,
    prno,
    direction: str = "both",
) -> list[dict]:
    """재귀 full-chain 해석.

    - ``direction='descendants'`` : 자식→손자→…
    - ``direction='ancestors'``   : 부모→조부모→…
    - ``direction='both'``        : descendants + ancestors (순서는 ancestors 먼저)
    """
    key = _norm_prno(prno)
    if not key:
        return []
    out: list[dict] = []
    if direction in ("ancestors", "both"):
        out.extend(ctx.by_prno[p] for p in _ancestors(ctx, key) if p in ctx.by_prno)
    if direction in ("descendants", "both"):
        out.extend(ctx.by_prno[c] for c in _descendants(ctx, key) if c in ctx.by_prno)
    return out


# ─── 요약기 ───────────────────────────────────────────────────────────────

_EMPTY_CHILDREN_SUMMARY: dict = {
    "자식 수(직계)": 0,
    "자식 수(전체)": 0,
    "자식 종결 수": 0,
    "자식 미종결 수": 0,
    "자식 종결률 %": None,
    "자식 구성": "",
    "최종 종결 여부(체인)": False,
    "체인 최대 깊이": 0,
    "자식 최대 지연일": None,
    "이상 케이스 플래그": "",
    "자식 미종결 목록": [],
}


def summarize_children(ctx: LinkageContext, prno) -> dict:
    """자신 이하 체인의 요약 지표."""
    key = _norm_prno(prno)
    if not key or key not in ctx.by_prno:
        return dict(_EMPTY_CHILDREN_SUMMARY)
    if key in ctx._children_summary_cache:
        return ctx._children_summary_cache[key]

    direct = ctx.children_by_parent.get(key, [])
    descendants = _descendants(ctx, key)

    self_closed = key in ctx.closure_set
    closed_children = [p for p in descendants if p in ctx.closure_set]
    open_children = [p for p in descendants if p not in ctx.closure_set]

    # 체인 최대 깊이 (자신=1 기준, 자손 없으면 1)
    max_depth = 1
    if descendants:
        depth_map: dict[str, int] = {key: 1}
        queue: deque[str] = deque([key])
        while queue:
            cur = queue.popleft()
            for child in ctx.children_by_parent.get(cur, []):
                d = depth_map[cur] + 1
                if d > depth_map.get(child, 0):
                    depth_map[child] = d
                    queue.append(child)
                    if d > max_depth:
                        max_depth = d

    # 자식 구성 — 직계 자식 기준 프로젝트 카운트
    comp_count: dict[str, int] = defaultdict(int)
    for p in descendants:
        proj = str(ctx.by_prno[p].get("프로젝트", "") or "").strip() or "?"
        comp_count[proj] += 1
    comp_str = ", ".join(f"{k} {v}" for k, v in sorted(comp_count.items(), key=lambda kv: -kv[1]))

    # 자식 최대 지연일 (미종결 자식 중 가장 오래된 기한)
    max_overdue: int | None = None
    for p in open_children:
        row = ctx.by_prno[p]
        od = _days_overdue(row.get("기한일"))
        if od is None:
            continue
        if max_overdue is None or od > max_overdue:
            max_overdue = od

    # 이상 케이스 플래그
    flags: list[str] = []
    if self_closed and open_children:
        flags.append("부모종결_자식미종결")
    if (not self_closed) and descendants and not open_children:
        flags.append("자식완료_부모미완료")
    flag_str = ", ".join(flags)

    # 미종결 자식 목록 (대시보드 drill-down 용)
    open_children_rows: list[dict] = []
    for p in open_children:
        r = ctx.by_prno[p]
        open_children_rows.append({
            "관리번호": r.get("관리번호"),
            "프로젝트": r.get("프로젝트"),
            "제목": r.get("제목"),
            "진행상태": r.get("진행상태"),
            "기한일": r.get("기한일"),
            "지연일": _days_overdue(r.get("기한일")),
        })

    total_desc = len(descendants)
    summary = {
        "자식 수(직계)": len(direct),
        "자식 수(전체)": total_desc,
        "자식 종결 수": len(closed_children),
        "자식 미종결 수": len(open_children),
        "자식 종결률 %": (
            round(len(closed_children) * 100 / total_desc, 1)
            if total_desc > 0 else None
        ),
        "자식 구성": comp_str,
        "최종 종결 여부(체인)": bool(self_closed and not open_children),
        "체인 최대 깊이": max_depth,
        "자식 최대 지연일": max_overdue,
        "이상 케이스 플래그": flag_str,
        "자식 미종결 목록": open_children_rows,
    }
    ctx._children_summary_cache[key] = summary
    return summary


_EMPTY_PARENT_SUMMARY: dict = {
    "부모 관리번호": "",
    "부모 프로젝트": "",
    "부모 제목": "",
    "부모 종결 여부": None,
    "최상위 조상 관리번호": "",
    "최상위 조상 프로젝트": "",
    "체인 내 위치(깊이)": 1,
}


def summarize_parent(ctx: LinkageContext, prno) -> dict:
    """자신의 부모·최상위 조상 정보 및 체인 내 위치."""
    key = _norm_prno(prno)
    if not key or key not in ctx.by_prno:
        return dict(_EMPTY_PARENT_SUMMARY)
    if key in ctx._parent_summary_cache:
        return ctx._parent_summary_cache[key]

    ancestors = _ancestors(ctx, key)
    if not ancestors:
        summary = dict(_EMPTY_PARENT_SUMMARY)
        ctx._parent_summary_cache[key] = summary
        return summary

    direct_parent = ctx.by_prno.get(ancestors[0], {})
    top = ctx.by_prno.get(ancestors[-1], {})

    summary = {
        "부모 관리번호": direct_parent.get("관리번호", "") or "",
        "부모 프로젝트": direct_parent.get("프로젝트", "") or "",
        "부모 제목": direct_parent.get("제목", "") or "",
        "부모 종결 여부": (
            True if _is_closed(direct_parent)
            else False if direct_parent else None
        ),
        "최상위 조상 관리번호": top.get("관리번호", "") or "",
        "최상위 조상 프로젝트": top.get("프로젝트", "") or "",
        "체인 내 위치(깊이)": len(ancestors) + 1,
    }
    ctx._parent_summary_cache[key] = summary
    return summary


# ─── DataFrame 머지 helper ────────────────────────────────────────────────

_LINKAGE_MERGE_COLUMNS: list[str] = [
    # 자식 측
    "자식 수(직계)", "자식 수(전체)", "자식 종결 수", "자식 미종결 수",
    "자식 종결률 %", "자식 구성", "최종 종결 여부(체인)", "체인 최대 깊이",
    "자식 최대 지연일", "이상 케이스 플래그",
    # 부모 측
    "부모 관리번호", "부모 프로젝트", "부모 제목", "부모 종결 여부",
    "최상위 조상 관리번호", "최상위 조상 프로젝트", "체인 내 위치(깊이)",
]


def linkage_columns_for(ctx: LinkageContext, prno) -> dict:
    """단일 행용: summarize_children + summarize_parent 결과에서 DataFrame 에
    들어갈 스칼라 컬럼만 뽑아 합친 dict 반환 (``자식 미종결 목록`` 등 list 제외)."""
    child = summarize_children(ctx, prno)
    parent = summarize_parent(ctx, prno)
    merged = {}
    for k in _LINKAGE_MERGE_COLUMNS:
        if k in child:
            merged[k] = child[k]
        elif k in parent:
            merged[k] = parent[k]
    return merged


def apply_linkage_to_dataframe(df, ctx: LinkageContext):
    """pandas DataFrame 에 연계 컬럼을 일괄 머지. 원본 df 를 반환(in-place + return)."""
    if df is None or len(df) == 0:
        return df
    if "관리번호" not in df.columns:
        return df
    for col in _LINKAGE_MERGE_COLUMNS:
        if col not in df.columns:
            df[col] = None
    for idx, prno in df["관리번호"].items():
        merged = linkage_columns_for(ctx, prno)
        for col, val in merged.items():
            df.at[idx, col] = val
    return df
