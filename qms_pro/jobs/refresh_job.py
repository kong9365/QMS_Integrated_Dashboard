# -*- coding: utf-8 -*-
"""qms_pro.jobs.refresh_job — QMS 수집·연계·파생 → 디스크 캐시 적재 (Task 1.2).

목적
----
QMS fetch·연계(linkage)·파생 계산을 **화면과 분리**해 주기 실행하고, 결과를
``.qms_cache`` (parquet) 에 원자적으로 적재한다. 화면(앱)은 이 캐시만 읽는다.
=> "앱은 캐시만, 무거운 계산은 refresh_job" 운영 디커플링.

저장소 결정(사양서 Task 1.2 정정)
- SQLite 대신 **기존 .qms_cache parquet 재사용**. 사유: 동작 중인 캐시 존재 ·
  APQR는 DB 스냅샷 불필요(이벤트에 날짜) · 1인+AI 운영엔 단순 우선. SQLite/이력질의는
  Phase 4 옵션으로 보류.

파이프라인 (로직 불변 — 기존 함수 그대로 호출)
1. ``run_all_snapshot_fetches()``  → 16개 (df, err)  (fetch_*_impl, 파생 D-day·건수기여도·자사외주 포함)
2. ``build_and_apply_linkage(dfs)`` → 각 DF 에 연계 컬럼(최종 종결 여부(체인)·부모/자식 등) in-place 머지
3. 성공 DF 를 ``data_access`` 의 캐시 키로 디스크 캐시에 저장(원자적 temp→swap; qms_disk_cache.save)
4. ``_meta.json`` 에 last_refresh · 프로젝트별 rows·status·error 기록

실행
----
    python -m qms_pro.jobs.refresh_job

부분 실패해도 성공분은 커밋한다(프로젝트별 status 기록). 자격증명은 .env/OS 환경변수로
주입(이 스크립트는 비밀값을 출력하지 않는다).
"""
from __future__ import annotations

import json
import logging
import os
import time

from qms_pro.services import cache_service as DC
from qms_pro.services import data_access as DA

# 기존 백엔드 함수 재사용(이동/수정 없음). facade 가 아닌 원본 직접 import.
from qms_fetch_uncached import (
    run_all_snapshot_fetches,
    build_and_apply_linkage,
)

logger = logging.getLogger("qms_refresh_job")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_h)
logger.setLevel(os.environ.get("QMS_REFRESH_LOG_LEVEL", "INFO").upper())

# _meta.json 위치: 디스크 캐시 디렉터리와 동일(.qms_cache/_meta.json)
_META_PATH = os.path.join(DC.cache_dir(), "_meta.json")


def _write_meta(meta: dict) -> None:
    """_meta.json 원자적 기록(temp→replace)."""
    os.makedirs(os.path.dirname(_META_PATH), exist_ok=True)
    tmp = _META_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _META_PATH)


def run_refresh() -> dict:
    """수집→연계→파생→캐시 적재 1회 실행. 반환: _meta dict."""
    t0 = time.time()
    logger.info("QMS refresh 시작 — 16개 프로젝트 fetch")

    # 1) fetch (파생 D-day·건수기여도·자사외주 포함). dict[project] = (df, err)
    results = run_all_snapshot_fetches()

    # 2) 성공 DF 만 모아 연계(linkage) 컬럼 in-place 머지
    ok_dfs = {k: df for k, (df, err) in results.items() if err is None and df is not None}
    try:
        build_and_apply_linkage(ok_dfs)
        logger.info("연계(linkage) 컬럼 머지 완료 — %d개 프로젝트", len(ok_dfs))
    except Exception as e:  # noqa: BLE001
        logger.error("연계 머지 실패(개별 저장은 계속): %s", e)

    # 3) 캐시 적재 + 프로젝트별 메타 수집
    projects_meta: dict[str, dict] = {}
    saved = 0
    for project, (df, err) in results.items():
        cache_key = DA.cache_key_for(project)
        if err is not None:
            projects_meta[project] = {"rows": 0, "status": "fail", "error": str(err)}
            logger.warning("[%s] fetch 실패: %s", project, err)
            continue
        rows = 0 if df is None else int(len(df))
        if cache_key is None:
            # stub(일탈외주): 디스크 캐시 미적용 — 기존 동작과 동일. 메타에는 ok 로 기록.
            projects_meta[project] = {"rows": rows, "status": "ok", "error": None, "cached": False}
            continue
        try:
            DC.save(cache_key, df)  # 원자적 temp→swap (qms_disk_cache.save)
            saved += 1
            projects_meta[project] = {"rows": rows, "status": "ok", "error": None, "cached": True}
        except Exception as e:  # noqa: BLE001
            projects_meta[project] = {"rows": rows, "status": "fail", "error": f"save: {e}"}
            logger.error("[%s] 캐시 저장 실패: %s", project, e)

    elapsed = time.time() - t0
    ok_count = sum(1 for m in projects_meta.values() if m["status"] == "ok")
    meta = {
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)),
        "elapsed_sec": round(elapsed, 2),
        "ok_count": ok_count,
        "total_count": len(results),
        "saved_to_cache": saved,
        "projects": projects_meta,
    }
    _write_meta(meta)
    logger.info("QMS refresh 완료 — %d/%d ok, %.1fs, _meta.json 기록",
                ok_count, len(results), elapsed)
    return meta


def main() -> int:
    meta = run_refresh()
    # CLI 종료코드: 전부 성공 0, 일부 실패 1
    return 0 if meta["ok_count"] == meta["total_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
