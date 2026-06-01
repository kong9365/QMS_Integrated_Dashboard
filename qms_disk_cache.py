# -*- coding: utf-8 -*-
"""Parquet 기반 디스크 캐시 — 콜드 스타트 가속, 사용자 간 공유.

저장 위치: <project_root>/.qms_cache/{key}.parquet (+ .meta)
TTL: 기본 3600초 (사용자 간 공유로 안전 마진 크게).
"""
from __future__ import annotations

import json
import os
import threading
import time

import pandas as pd

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".qms_cache")
DISK_TTL = 3600
_FILE_LOCK = threading.Lock()


def cache_dir() -> str:
    """디스크 캐시 디렉터리 절대경로(.qms_cache). refresh_job 의 _meta.json 위치 등에 사용."""
    return _CACHE_DIR


def _ensure_dir() -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _paths(key: str) -> tuple[str, str]:
    return (
        os.path.join(_CACHE_DIR, f"{key}.parquet"),
        os.path.join(_CACHE_DIR, f"{key}.meta"),
    )


def load(key: str, ttl: int = DISK_TTL) -> tuple[pd.DataFrame, str | None] | None:
    """캐시 히트 시 (df, None), 미스/만료 시 None."""
    _ensure_dir()
    pq, mt = _paths(key)
    if not (os.path.exists(pq) and os.path.exists(mt)):
        return None
    try:
        with open(mt, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if time.time() - float(meta.get("ts", 0)) > ttl:
            return None
        df = pd.read_parquet(pq)
        return df, None
    except Exception:
        return None


def _coerce_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """object dtype 의 mixed-type 컬럼을 문자열로 통일 (Arrow 직렬화 호환)."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype != object:
            continue
        s = out[col]
        # 빠른 통일: 모두 None/NaN/문자열이면 통과, 아니면 str 캐스팅
        types = {type(v).__name__ for v in s.dropna().head(50)}
        if len(types) > 1 or types - {"str"}:
            out[col] = s.where(s.notna(), None).map(
                lambda v: None if v is None else str(v)
            )
    return out


def save(key: str, df: pd.DataFrame) -> None:
    """원자적 쓰기 (tmp → replace) + 동시 쓰기 직렬화."""
    if df is None or df.empty:
        return
    _ensure_dir()
    pq, mt = _paths(key)
    tmp_pq = pq + ".tmp"
    with _FILE_LOCK:
        try:
            safe = _coerce_for_parquet(df)
            safe.to_parquet(tmp_pq, index=False)
            os.replace(tmp_pq, pq)
            with open(mt, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "rows": int(len(df))}, f)
        except Exception:
            try:
                if os.path.exists(tmp_pq):
                    os.remove(tmp_pq)
            except Exception:
                pass


def clear() -> None:
    """전체 디스크 캐시 삭제."""
    import shutil
    if os.path.exists(_CACHE_DIR):
        shutil.rmtree(_CACHE_DIR, ignore_errors=True)


def stats() -> dict:
    """현재 캐시 파일 통계 (디버그용)."""
    if not os.path.exists(_CACHE_DIR):
        return {"keys": [], "total_rows": 0}
    keys = []
    total = 0
    for fn in os.listdir(_CACHE_DIR):
        if fn.endswith(".meta"):
            try:
                with open(os.path.join(_CACHE_DIR, fn), "r", encoding="utf-8") as f:
                    meta = json.load(f)
                keys.append({
                    "key": fn[:-5],
                    "rows": meta.get("rows", 0),
                    "age_sec": int(time.time() - float(meta.get("ts", 0))),
                })
                total += int(meta.get("rows", 0))
            except Exception:
                pass
    return {"keys": keys, "total_rows": total}
