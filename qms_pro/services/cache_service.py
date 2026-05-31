# -*- coding: utf-8 -*-
"""qms_pro.services.cache_service — 디스크 캐시 호환 래퍼.

기존 ``qms_disk_cache.py`` 를 **이동/수정하지 않고** 얇게 위임만 한다(Phase 2-2).
캐시 디렉터리(.qms_cache/), TTL 의미, 반환 형식은 원본과 100% 동일하다.

- ``load(key, ttl=None)`` : 히트 시 ``(df, None)``, 미스/만료 시 ``None``.
  ttl=None 이면 원본 기본 TTL(``DISK_TTL``) 사용.
- ``save(key, df)``       : 원본에 위임(저장).
- ``clear()``            : 원본에 위임(전체 삭제).
- ``stats()``            : 원본에 위임(캐시 통계 dict).

주의: baseline 스냅샷 등 읽기 전용 용도에서는 save/clear 를 호출하지 않는다.
"""
from __future__ import annotations

import pandas as pd

from qms_disk_cache import (
    DISK_TTL,
    load as _load,
    save as _save,
    clear as _clear,
    stats as _stats,
)

__all__ = ["load", "save", "clear", "stats", "DISK_TTL"]


def load(key: str, ttl: int | None = None) -> tuple[pd.DataFrame, str | None] | None:
    """캐시 히트 시 (df, None), 미스/만료 시 None. ttl=None 이면 원본 기본 TTL."""
    return _load(key, ttl=DISK_TTL if ttl is None else ttl)


def save(key: str, df) -> None:
    """원본 디스크 캐시에 위임(저장)."""
    return _save(key, df)


def clear() -> None:
    """원본 디스크 캐시에 위임(전체 삭제)."""
    return _clear()


def stats() -> dict:
    """원본 디스크 캐시 통계에 위임."""
    return _stats()
