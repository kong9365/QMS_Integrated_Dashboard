# -*- coding: utf-8 -*-
"""qms_pro.config.project_meta — 기존 프로젝트 메타 호환 래퍼.

원본 ``qms_project_meta.py`` 는 그대로 유지하고, 새 패키지에서는 여기서 참조만 한다.
원본을 import 할 수 없는 환경(경로 미설정 등)에서도 깨지지 않도록 빈 dict 로 폴백한다.
실제 이전(원본 삭제/이동)은 Phase 2 후속 단계에서 진행한다.
"""
from __future__ import annotations

import os
import sys

# 루트 경로 보장(새 패키지에서 루트 모듈 qms_project_meta 를 찾기 위함)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from qms_project_meta import PROJECT_META  # type: ignore  # noqa: F401
except Exception:  # noqa: BLE001
    # 원본을 찾지 못해도 import 가 깨지지 않도록 안전 폴백
    PROJECT_META: dict = {}
