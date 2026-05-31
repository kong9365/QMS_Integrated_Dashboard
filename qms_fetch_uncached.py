# -*- coding: utf-8 -*-
"""
Streamlit 캐시 없이 QMS API 전체 수집 (주기 스냅샷·배치용).
대시보드 v2와 동일한 파싱·후처리를 유지한다.
"""
from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qms_project_meta import PROJECT_META

from QMS_API import (
    QMSAPIClient,
    API_BASE_URL,
    LOGIN_USERNAME,
    LOGIN_PASSWORD,
    get_shared_client,
    parse_qms_detail_json,
    parse_conclusion_json,
    apply_normalized_weights,
    parse_list_only,
    resolve_list_task_id,
    parse_deviation_json,
    parse_capa_json,
    parse_change_json,
    parse_complain_json,
    parse_capaactionitem_json,
    parse_changeactionitem_json,
    parse_changeimpact_json,
    parse_changeoutsourcing_json,
    parse_deviationoutsourcing_json,
    parse_deviationactionitem_json,
    parse_businesstransfer_json,
    parse_validityevaluation_json,
    parse_investigation_json,
    enrich_with_raw_extention,
)

from qms_linkage import (
    LinkageContext,
    build_linkage,
    apply_linkage_to_dataframe,
)


def _get_client_or_err() -> tuple["QMSAPIClient | None", str | None]:
    """공유 세션 클라이언트 반환. 로그인 실패 시 (None, err)."""
    try:
        return get_shared_client(), None
    except RuntimeError as e:
        return None, str(e)


# ----------------------------------------------------------------------------
# 글로벌 상세조회 Executor (시스템 전체 동시성 캡 = 8)
# 다중 사용자가 동시 접속해도 QMS 서버 부하는 일정하게 유지된다.
# ----------------------------------------------------------------------------

_DETAIL_EXECUTOR: "ThreadPoolExecutor | None" = None
_DETAIL_EXEC_LOCK = threading.Lock()
_DETAIL_MAX_WORKERS = 8


def _get_detail_executor() -> ThreadPoolExecutor:
    global _DETAIL_EXECUTOR
    with _DETAIL_EXEC_LOCK:
        if _DETAIL_EXECUTOR is None:
            _DETAIL_EXECUTOR = ThreadPoolExecutor(
                max_workers=_DETAIL_MAX_WORKERS,
                thread_name_prefix="qms-detail",
            )
        return _DETAIL_EXECUTOR


# ----------------------------------------------------------------------------
# Single-flight: 동일 프로젝트 fetch가 진행 중이면 그 결과를 공유
# (다중 사용자 thundering herd 방지)
# ----------------------------------------------------------------------------

_INFLIGHT: "dict[str, Future]" = {}
_INFLIGHT_LOCK = threading.Lock()


def _singleflight(key: str, fn):
    """동일 key 의 fn 호출을 1건으로 합친다."""
    is_owner = False
    with _INFLIGHT_LOCK:
        existing = _INFLIGHT.get(key)
        if existing is not None and not existing.done():
            shared = existing
        else:
            shared = Future()
            _INFLIGHT[key] = shared
            is_owner = True
    if is_owner:
        try:
            result = fn()
            shared.set_result(result)
        except BaseException as e:
            shared.set_exception(e)
        finally:
            with _INFLIGHT_LOCK:
                if _INFLIGHT.get(key) is shared:
                    _INFLIGHT.pop(key, None)
    return shared.result()


# ----------------------------------------------------------------------------
# 지능형 재시도 — transient 오류는 동일 세션 재시도, 2회째에만 1회 force_new
# ----------------------------------------------------------------------------

def _safe_get_list(client: QMSAPIClient, project: str, page: int, page_size: int = 1000) -> dict:
    """목록 조회 — transient 5xx/HTML 응답에 대비, 최대 3회 시도."""
    import time as _t

    last: dict = {}
    for attempt in range(3):
        try:
            resp = client.get_workflow_list(project=project, page=page, page_size=page_size)
        except Exception:
            resp = {}
        if resp and "list" in resp:
            return resp
        last = resp or {}
        if attempt == 1:
            try:
                client = get_shared_client(force_new=True)
            except RuntimeError:
                return last
        _t.sleep(0.4 * (attempt + 1))
    return last


def _safe_get_detail(client: QMSAPIClient, task_id: str) -> dict:
    """상세 조회 — transient 오류 재시도, 2회째에만 1회 force_new."""
    import time as _t

    if not task_id:
        return {}
    for attempt in range(3):
        try:
            detail = client.get_workflow_detail(task_id=task_id)
            if detail:
                return detail
        except Exception:
            pass
        if attempt == 1:
            try:
                client = get_shared_client(force_new=True)
            except RuntimeError:
                return {}
        _t.sleep(0.3 * (attempt + 1))
    return {}


def calc_dday(limit_date_str) -> int | None:
    if not limit_date_str or str(limit_date_str).strip() in ("", "None", "nan"):
        return None
    try:
        limit = pd.to_datetime(str(limit_date_str)[:10], errors="coerce")
        if pd.isna(limit):
            return None
        return (limit.date() - date.today()).days
    except Exception:
        return None


def add_dday_column(df: pd.DataFrame) -> pd.DataFrame:
    if "기한일" in df.columns:
        df["D-day"] = df["기한일"].apply(calc_dday)
    return df


def _collect_list_pages(client: QMSAPIClient, project: str, page_size: int = 1000) -> list[dict]:
    """큰 pageSize로 호출해 페이지 수를 최소화한다.

    서버는 pageSize>=3000까지 전량을 한 번에 돌려주므로,
    1회 호출로 대부분 프로젝트가 완결된다. 예외(>1000건)만 2~3페이지 돈다.
    """
    all_items: list[dict] = []
    page = 1
    while True:
        resp = _safe_get_list(client, project, page, page_size=page_size)
        if not resp or "list" not in resp:
            break
        items = resp.get("list", [])
        if not items:
            break
        all_items.extend(items)
        total = resp.get("totalCount", 0) or 0
        if len(all_items) >= total or page * page_size >= total:
            break
        page += 1
    return all_items


def _post_process_common(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    for col in ["등록일", "기한일"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d").where(
                pd.to_datetime(df[col], errors="coerce").notna(), other=""
            )
    if "등록일" in df.columns:
        _reg = pd.to_datetime(df["등록일"], errors="coerce")
        df["연도_등록"] = _reg.dt.year.astype("Int64")
        df["월_등록"] = _reg.dt.month.astype("Int64")

    date_col = "발견일시" if "발견일시" in df.columns else "등록일"
    if date_col in df.columns:
        df["_기준일"] = pd.to_datetime(df[date_col], errors="coerce")
        df["연도"] = df["_기준일"].dt.year.astype("Int64")
        df["월"] = df["_기준일"].dt.month.astype("Int64")
        df.drop(columns=["_기준일"], inplace=True)

    if "_list_writeDate" in df.columns:
        list_dt = pd.to_datetime(df["_list_writeDate"], errors="coerce")
        need_fill = df["연도"].isna() if "연도" in df.columns else pd.Series(True, index=df.index)
        if need_fill.any():
            if "연도" not in df.columns:
                df["연도"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
            if "월" not in df.columns:
                df["월"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
            df.loc[need_fill, "연도"] = list_dt.dt.year
            df.loc[need_fill, "월"] = list_dt.dt.month
        df = df.drop(columns=["_list_writeDate"], errors="ignore")

    df = add_dday_column(df)
    return df


def _fetch_detail_project(project: str, label: str, parser_fn):
    """일반 프로젝트 상세 수집 — single-flight + 글로벌 풀로 inner 병렬화."""
    return _singleflight(
        f"fetch:{project}",
        lambda: _fetch_detail_project_inner(project, label, parser_fn),
    )


def _fetch_detail_project_inner(project: str, label: str, parser_fn):
    client, err = _get_client_or_err()
    if err:
        return pd.DataFrame(), err
    items = _collect_list_pages(client, project)

    bases: list[dict] = []
    tid_jobs: list[tuple[int, str]] = []
    processed: set[str] = set()
    for item in items:
        prno = str(item.get("prno", "")).strip()
        if not prno or prno in processed:
            continue
        processed.add(prno)
        base = parse_list_only(item, label)
        idx = len(bases)
        bases.append(base)
        tid = resolve_list_task_id(item)
        if tid and parser_fn:
            tid_jobs.append((idx, tid))

    if tid_jobs:
        pool = _get_detail_executor()
        futures = {
            pool.submit(_safe_get_detail, client, tid): idx
            for idx, tid in tid_jobs
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                detail = fut.result()
            except Exception:
                detail = {}
            if detail:
                bases[idx].update(parser_fn(detail))
                enrich_with_raw_extention(bases[idx], detail, project)

    if not bases:
        return pd.DataFrame(), None
    df = pd.DataFrame(bases)
    df = _post_process_common(df)
    return df, None


def _collect_deviation_branch_rows(
    client: QMSAPIClient,
    project: str,
    parser_fn,
    일탈구분: str,
) -> list[dict]:
    """일탈 가지(자사/외주) 수집 — 상세 조회를 글로벌 풀로 병렬화."""
    items = _collect_list_pages(client, project)

    # 1단계: base 레코드 + task_id 매핑 준비
    bases: list[dict] = []
    tid_jobs: list[tuple[int, str]] = []
    seen: set[str] = set()
    for item in items:
        prno = str(item.get("prno", "") or "").strip()
        if not prno:
            continue
        dedupe = f"{project}:{prno}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        base = parse_list_only(item, "일탈")
        base["일탈구분"] = 일탈구분
        base["_source_project"] = project
        idx = len(bases)
        bases.append(base)
        tid = resolve_list_task_id(item)
        if tid:
            tid_jobs.append((idx, tid))

    # 2단계: 상세 조회 병렬 수행 → idx → detail 매핑
    details: dict[int, dict] = {}
    if tid_jobs:
        pool = _get_detail_executor()
        futures = {
            pool.submit(_safe_get_detail, client, tid): idx
            for idx, tid in tid_jobs
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                details[idx] = fut.result() or {}
            except Exception:
                details[idx] = {}

    # 3단계: 시험정보 전개 (CPU 처리만)
    rows: list[dict] = []
    job_idxs = {idx for idx, _ in tid_jobs}
    for idx, base in enumerate(bases):
        if idx not in job_idxs:
            base["동시분석"] = "No"
            base["건수기여도"] = 1.0
            rows.append(base)
            continue
        detail = details.get(idx, {})
        if detail:
            base.update(parser_fn(detail))
            enrich_with_raw_extention(base, detail, project)
        test_infos = base.pop("시험정보목록", [])
        num_tests = len(test_infos)
        if num_tests <= 1:
            if test_infos:
                base.update(test_infos[0])
            base["동시분석"] = "No"
            base["건수기여도"] = 1.0
            rows.append(base)
        else:
            tf = ["시험종류", "품목코드", "품목명", "제조번호", "시험항목"]
            for test in test_infos:
                row = {k: v for k, v in base.items() if k not in tf}
                row.update(test)
                row["동시분석"] = "Yes"
                row["건수기여도"] = round(1 / num_tests, 5)
                rows.append(row)
    return rows


def fetch_list_project_impl(project: str):
    """list-only 프로젝트 — single-flight 로 thundering herd 방지."""
    return _singleflight(f"fetch:{project}", lambda: _fetch_list_project_inner(project))


def _fetch_list_project_inner(project: str):
    client, err = _get_client_or_err()
    if err:
        return pd.DataFrame(), err
    items = _collect_list_pages(client, project)
    if not items:
        return pd.DataFrame(), None
    label = PROJECT_META.get(project, {}).get("label", project)
    df = pd.DataFrame([parse_list_only(i, label) for i in items])
    df = _post_process_common(df)
    return df, None


def fetch_oos_data_impl():
    """OOS 수집 — single-flight + 글로벌 풀로 inner 병렬화."""
    return _singleflight("fetch:oos", _fetch_oos_data_inner)


def _fetch_oos_data_inner():
    client, err = _get_client_or_err()
    if err:
        return pd.DataFrame(), err

    items = _collect_list_pages(client, "oos")

    # 1단계: base 레코드 + task_id 작업 분리
    bases: list[dict] = []
    tid_jobs: list[tuple[int, str]] = []
    items_for_idx: list[dict] = []
    processed: set[str] = set()
    for item in items:
        qms_id = str(item.get("prno", "")).strip()
        if not qms_id or qms_id in processed:
            continue
        processed.add(qms_id)
        contents = {
            "프로젝트": "OOS",
            "관리번호": item.get("prno"),
            "진행상태": str(item.get("status", "")),
            "완료여부": str(item.get("taskCondition", "")),
            "등록일": str(item.get("regDate") or item.get("writeDate", "") or "")[:10],
            "기한일": str(item.get("limitDate", "") or ""),
            "_list_writeDate": str(item.get("writeDate") or "").strip(),
        }
        idx = len(bases)
        bases.append(contents)
        items_for_idx.append(item)
        tid = resolve_list_task_id(item)
        if tid:
            tid_jobs.append((idx, tid))

    # 2단계: 상세조회 글로벌 풀 병렬 — idx -> detail
    details: dict[int, dict] = {}
    if tid_jobs:
        pool = _get_detail_executor()
        futures = {
            pool.submit(_safe_get_detail, client, tid): idx
            for idx, tid in tid_jobs
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                details[idx] = fut.result() or {}
            except Exception:
                details[idx] = {}

    # 3단계: detail 적용 + 시험정보 전개 (CPU 처리)
    all_data: list[dict] = []
    job_idxs = {idx for idx, _ in tid_jobs}
    for idx, contents in enumerate(bases):
        item = items_for_idx[idx]
        if idx not in job_idxs:
            # task_id 없음 → list-only base
            base = parse_list_only(item, "OOS")
            base["건수기여도"] = 1.0
            base["동시분석"] = "No"
            all_data.append(base)
            continue
        detail = details.get(idx, {})
        if not detail:
            contents["동시분석"] = "No"
            contents["건수기여도"] = 1.0
            t = str(item.get("title", "") or "").strip()
            if t:
                contents["제목"] = t
            all_data.append(contents)
            continue
        contents.update(parse_qms_detail_json(detail))
        contents.update(parse_conclusion_json(detail))
        enrich_with_raw_extention(contents, detail, "oos")
        test_infos = contents.pop("시험정보목록", [])
        num_tests = len(test_infos)
        if num_tests <= 1:
            if test_infos:
                contents.update(test_infos[0])
            contents["동시분석"] = "No"
            contents["건수기여도"] = 1.0
            all_data.append(contents)
        else:
            tf = ["시험종류", "품목코드", "품목명", "제조번호", "시험항목", "시험기준", "시험결과"]
            for test in test_infos:
                row = {k: v for k, v in contents.items() if k not in tf}
                row.update(test)
                row["동시분석"] = "Yes"
                row["건수기여도"] = round(1 / num_tests, 5)
                all_data.append(row)

    if not all_data:
        return pd.DataFrame(), None
    df = pd.DataFrame(all_data)
    df = apply_normalized_weights(df, group_col="관리번호")
    df = _post_process_common(df)
    return df, None


def fetch_deviation_data_impl():
    """일탈 수집 — single-flight + 두 가지 brach 모두 글로벌 풀 병렬화."""
    return _singleflight("fetch:deviation", _fetch_deviation_data_inner)


def _fetch_deviation_data_inner():
    client, err = _get_client_or_err()
    if err:
        return pd.DataFrame(), err
    all_data: list[dict] = []
    all_data.extend(_collect_deviation_branch_rows(client, "deviation", parse_deviation_json, "자사"))
    all_data.extend(
        _collect_deviation_branch_rows(client, "deviationoutsourcing", parse_deviationoutsourcing_json, "외주")
    )
    if not all_data:
        return pd.DataFrame(), None
    df = pd.DataFrame(all_data)
    df = _post_process_common(df)
    return df, None


def fetch_devout_data_stub_impl():
    return pd.DataFrame(), None


def fetch_capa_data_impl():
    return _fetch_detail_project("capa", "CAPA", parse_capa_json)


def fetch_change_data_impl():
    return _fetch_detail_project("changemanagement", "변경", parse_change_json)


def fetch_complain_data_impl():
    return _fetch_detail_project("complain", "고객불만", parse_complain_json)


def fetch_capaai_data_impl():
    return _fetch_detail_project("capaactionitem", "CAPA AI", parse_capaactionitem_json)


def fetch_changeai_data_impl():
    return _fetch_detail_project("changeactionitem", "변경AI", parse_changeactionitem_json)


def fetch_changeimpact_data_impl():
    return _fetch_detail_project("changeimpactassessment", "변경영향성", parse_changeimpact_json)


def fetch_changeout_data_impl():
    return _fetch_detail_project("changeoutsourcing", "외주변경", parse_changeoutsourcing_json)


def fetch_devoutai_data_impl():
    return _fetch_detail_project("deviationactionitem", "일탈외주AI", parse_deviationactionitem_json)


def fetch_transfer_data_impl():
    return _fetch_detail_project("businesstransfer", "업무이전", parse_businesstransfer_json)


def fetch_validity_data_impl():
    return _fetch_detail_project("validityevaluation", "유효성평가", parse_validityevaluation_json)


def fetch_investigation_data_impl():
    return _fetch_detail_project("investigation", "조사", parse_investigation_json)


_LINKAGE_ROW_COLS: list[str] = [
    "관리번호", "상위번호", "프로젝트", "완료여부",
    "제목", "진행상태", "기한일",
]


def _df_to_linkage_rows(df: pd.DataFrame) -> list[dict]:
    """DataFrame 에서 linkage 용 필수 컬럼만 추출 (관리번호 중복 시 첫 행만).

    여러 시험종류로 전개된 행이 있을 수 있으므로 ``drop_duplicates`` 로 1:1 로 줄인다.
    """
    if df is None or df.empty or "관리번호" not in df.columns:
        return []
    cols = [c for c in _LINKAGE_ROW_COLS if c in df.columns]
    slim = df[cols].copy()
    if "관리번호" in slim.columns:
        slim = slim.drop_duplicates(subset=["관리번호"], keep="first")
    return slim.to_dict(orient="records")


def build_and_apply_linkage(all_dfs: dict[str, pd.DataFrame]) -> LinkageContext:
    """모든 프로젝트 DF 의 list-level 레코드를 합쳐 linkage 그래프를 구성하고,
    각 DF 에 부모/자식 요약 컬럼을 머지한다.

    반환: ``LinkageContext`` (자식 미종결 drill-down 등 대시보드에서 재사용 가능).
    """
    rows: list[dict] = []
    for df in all_dfs.values():
        rows.extend(_df_to_linkage_rows(df))
    ctx = build_linkage(rows)
    for key, df in list(all_dfs.items()):
        if df is None or df.empty:
            continue
        apply_linkage_to_dataframe(df, ctx)
    return ctx


def run_all_snapshot_fetches() -> dict[str, tuple[pd.DataFrame, str | None]]:
    """ALL_DFS 키 순서와 동일하게 (df, err) 반환."""
    return {
        "oos": fetch_oos_data_impl(),
        "deviation": fetch_deviation_data_impl(),
        "investigation": fetch_investigation_data_impl(),
        "capa": fetch_capa_data_impl(),
        "capaactionitem": fetch_capaai_data_impl(),
        "actionitem": fetch_list_project_impl("actionitem"),
        "changemanagement": fetch_change_data_impl(),
        "changeactionitem": fetch_changeai_data_impl(),
        "changeimpactassessment": fetch_changeimpact_data_impl(),
        "changeoutsourcing": fetch_changeout_data_impl(),
        "complain": fetch_complain_data_impl(),
        "deviationoutsourcing": fetch_devout_data_stub_impl(),
        "deviationactionitem": fetch_devoutai_data_impl(),
        "extension": fetch_list_project_impl("extension"),
        "businesstransfer": fetch_transfer_data_impl(),
        "validityevaluation": fetch_validity_data_impl(),
    }
