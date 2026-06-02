# -*- coding: utf-8 -*-
"""qms_pro.domain.disposition — lot 처분(PASS/HOLD) 판정 순수 도메인 함수 (Task 3.3b).

목적
----
제조번호(lot)별로 관련 품질 이벤트를 모아 **적합/보류/부적합/미상** 4분류(최악 우선)로 판정.
APQR 의 출하 전 확인 보조용. **모니터링 보조이며 정식 출하 판정 시스템이 아니다.**

절대 규칙
--------
- **기존 컬럼만 읽는다**(원본 불변, 신규 집계/순회 로직 최소). 가중 집계는 기존 ``_wgroupby`` 재사용.
- lot 키 = ``제조번호_귀속``(attribution 산출). lot 가 빈값(미상)인 레코드는 **처분 집계에서 제외**.
- 판정 소스: OOS ``기준 일탈 최종 결과``(적합/부적합/빈) + ``최종 종결 여부(체인)``(bool).
- **추측 금지** — 판정·종결 정보가 부족하면 ``미상``.

최악 우선 4분류(lot 단위)
------------------------
1. **부적합** : 관련 OOS 중 ``기준 일탈 최종 결과 == '부적합'`` 1건 이상.
2. **보류**   : 부적합 없음 + 관련 레코드 중 ``최종 종결 여부(체인) == False``(미종결) 존재.
3. **적합**   : 부적합 없음 + 전부 종결 + ``적합`` 결과 1건 이상.
4. **미상**   : 그 외(판정·종결 정보 부족).

비고
----
- 실측(2026-06 스냅샷): lot 보유 511(자체467/상속44), lot 미상 8,545. ``기준 일탈 최종 결과`` 는
  OOS 전용, ``최종 종결 여부(체인)`` 은 OOS/조사/CAPA/Action/기한연장 모두 보유.
"""
from __future__ import annotations

import pandas as pd

from qms_pro.domain.metrics import _wgroupby   # 가중 집계 재사용(신규 집계 로직 0)

# 원본/파생 컬럼명(읽기 전용)
LOT_COL = "제조번호_귀속"
ITEM_COL = "품목명_귀속"
RESULT_COL = "기준 일탈 최종 결과"
CHAIN_COL = "최종 종결 여부(체인)"

# 판정 입력 값
RESULT_FAIL = "부적합"
RESULT_PASS = "적합"

# 처분 라벨(단일 출처 — 조정 용이)
DISP_FAIL = "부적합"
DISP_HOLD = "보류"
DISP_PASS = "적합"
DISP_UNKNOWN = "미상"
DISP_ORDER = [DISP_FAIL, DISP_HOLD, DISP_PASS, DISP_UNKNOWN]   # 최악 우선


def _is_chain_closed(v) -> bool:
    """``최종 종결 여부(체인)`` → 종결 여부. True/'True'/1 만 종결로 본다(그 외 미종결)."""
    if v is True:
        return True
    return str(v).strip().lower() in ("true", "1")


def _lot_nonempty(df: pd.DataFrame) -> pd.DataFrame:
    """lot(제조번호_귀속) 가 채워진 행만."""
    return df[df[LOT_COL].astype(str).str.strip() != ""]


def judge_lot_dispositions(all_dfs: dict, *, oos_key: str = "oos") -> pd.DataFrame:
    """lot(제조번호_귀속)별 처분 판정 표를 반환.

    반환 컬럼: ``제조번호_귀속``·``품목명_귀속``·``처분``·``관련건수``·``OOS수``·``미종결수``.
    관련건수 = 프로젝트별 ``_wgroupby`` 건수기여도 합(없으면 행수)을 합산(혼합 NaN 누락 방지).
    """
    related: dict[str, float] = {}             # lot -> 건수기여도 합
    sig: dict[str, dict] = {}                  # lot -> 판정 신호

    for k, d in all_dfs.items():
        if d is None or getattr(d, "empty", True) or LOT_COL not in d.columns or "관리번호" not in d.columns:
            continue
        sub = _lot_nonempty(d)
        if sub.empty:
            continue

        # 관련건수: 프로젝트 단위 건수기여도 합(없으면 size) → lot 별 누적
        g = _wgroupby(sub, LOT_COL, name="건수")
        for _lot, _c in zip(g[LOT_COL].astype(str), g["건수"]):
            related[_lot] = related.get(_lot, 0.0) + float(_c)

        # 판정 신호: 고유 관리번호 단위
        b = sub.drop_duplicates(subset=["관리번호"], keep="first")
        is_oos = (k == oos_key)
        has_res = RESULT_COL in b.columns
        has_chain = CHAIN_COL in b.columns
        for _, row in b.iterrows():
            lot = str(row[LOT_COL]).strip()
            s = sig.setdefault(lot, {"fail": False, "pass": False, "open_n": 0, "oos_n": 0, "item": ""})
            if has_res:
                rv = str(row.get(RESULT_COL, "") or "").strip()
                if rv == RESULT_FAIL:
                    s["fail"] = True
                elif rv == RESULT_PASS:
                    s["pass"] = True
            if has_chain and not _is_chain_closed(row.get(CHAIN_COL)):
                s["open_n"] += 1
            if is_oos:
                s["oos_n"] += 1
            if not s["item"]:
                it = str(row.get(ITEM_COL, "") or "").strip()
                if it:
                    s["item"] = it

    rows = []
    for lot, s in sig.items():
        if s["fail"]:
            disp = DISP_FAIL
        elif s["open_n"] > 0:
            disp = DISP_HOLD
        elif s["pass"]:
            disp = DISP_PASS
        else:
            disp = DISP_UNKNOWN
        rows.append({
            LOT_COL: lot,
            ITEM_COL: s["item"],
            "처분": disp,
            "관련건수": int(round(related.get(lot, 0.0))),
            "OOS수": s["oos_n"],
            "미종결수": s["open_n"],
        })
    cols = [LOT_COL, ITEM_COL, "처분", "관련건수", "OOS수", "미종결수"]
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows)[cols]
    # 정렬: 최악 우선(부적합>보류>적합>미상) → 관련건수 desc
    out["_ord"] = out["처분"].map({d: i for i, d in enumerate(DISP_ORDER)}).fillna(99)
    out = out.sort_values(["_ord", "관련건수"], ascending=[True, False]).drop(columns="_ord").reset_index(drop=True)
    return out


def disposition_distribution(disp_df: pd.DataFrame) -> dict:
    """처분 표 → {적합/보류/부적합/미상: 고유 lot 수}."""
    if disp_df is None or disp_df.empty or "처분" not in disp_df.columns:
        return {d: 0 for d in DISP_ORDER}
    vc = disp_df["처분"].value_counts()
    return {d: int(vc.get(d, 0)) for d in DISP_ORDER}


__all__ = [
    "judge_lot_dispositions",
    "disposition_distribution",
    "LOT_COL", "ITEM_COL",
    "DISP_FAIL", "DISP_HOLD", "DISP_PASS", "DISP_UNKNOWN", "DISP_ORDER",
]
