# -*- coding: utf-8 -*-
"""qms_pro.services.alert_service — qms_alert 호환 래퍼.

기존 ``qms_alert.py`` 를 **이동/수정하지 않고** 공개 알림 함수만 얇게 재노출한다
(Phase 2-7). Slack/Email 전송 로직, 기한초과(D-day) 트리거 기준, 환경변수 기반
자격증명 처리는 모두 원본 그대로이며 이 모듈은 import 재노출만 담당한다.

보안
----
- Slack Webhook URL / SMTP 비밀번호 등은 원본에서 ``os.getenv`` 로 읽으며,
  이 래퍼는 어떤 secret 도 상수로 노출하지 않는다.

대시보드는 아직 이 래퍼를 사용하지 않는다(현재 대시보드는 ``import qms_alert`` 직접 사용).
"""
from __future__ import annotations

from qms_alert import (
    send_slack,
    send_email,
    run_overdue_alert,
    load_alert_roster,
    normalize_person_name,
    preview_overdue_routing,
)

__all__ = [
    "send_slack",
    "send_email",
    "run_overdue_alert",
    "load_alert_roster",
    "normalize_person_name",
    "preview_overdue_routing",
]
