# -*- coding: utf-8 -*-
"""qms_pro.config.settings — 안전한 환경변수 읽기 헬퍼 + QMS 설정 초안.

설계 원칙
---------
- **읽기 전용 헬퍼만**: 이 모듈은 환경변수를 읽기만 한다. 기존 코드 동작을
  바꾸지 않으며, QMS_API.py / 대시보드의 기존 폴백 로직과 독립적이다.
- **빈 문자열 == 미설정**: ``QMS_X=`` 처럼 값이 비어 있으면 기본값으로 처리한다
  (.env 의 빈 값이 폴백을 막는 문제를 방지).
- **secret 미출력**: 비밀값을 로그/repr 로 노출하지 않는다. QMSSettings 의 repr 은
  민감 필드를 마스킹한다.
- **python-dotenv 미도입**: 자동 .env 로딩은 하지 않는다(추후 별도 패치에서 검토).
  현재는 OS 환경변수(또는 외부에서 주입된 .env)만 읽는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def get_env(name: str, default: str | None = None, *, allow_empty: bool = False) -> str | None:
    """환경변수 문자열 읽기.

    - 미설정(None) → default
    - 빈/공백 문자열 → (allow_empty=False 일 때) default, (True 일 때) 원값
    """
    value = os.getenv(name)
    if value is None:
        return default
    if not allow_empty and value.strip() == "":
        return default
    return value


def get_int_env(name: str, default: int) -> int:
    """정수 환경변수 읽기. 미설정/빈값/파싱 실패 시 default."""
    raw = get_env(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return default


def get_bool_env(name: str, default: bool = False) -> bool:
    """불리언 환경변수 읽기. 1/true/yes/on → True, 0/false/no/off → False."""
    raw = get_env(name)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    return default


# 마스킹이 필요한 민감 필드명(repr 출력 시 가림)
_SECRET_FIELDS = {"client_secret", "login_password"}


@dataclass
class QMSSettings:
    """QMS API 접속 설정 초안(환경변수 기반).

    주의: 이 dataclass 는 **새 계층에서 참조용 초안**일 뿐, 현재 QMS_API.py 의
    실행 경로를 대체하지 않는다. 값이 미설정이면 None 이며, 기존 폴백은
    여전히 QMS_API.py 가 담당한다.
    """

    base_url: str | None = None
    client_name: str | None = None
    client_secret: str | None = None
    realm_name: str | None = None
    login_username: str | None = None
    login_password: str | None = None

    @classmethod
    def from_env(cls) -> "QMSSettings":
        return cls(
            base_url=get_env("QMS_API_BASE_URL"),
            client_name=get_env("QMS_CLIENT_NAME"),
            client_secret=get_env("QMS_CLIENT_SECRET"),
            realm_name=get_env("QMS_REALM_NAME"),
            login_username=get_env("QMS_LOGIN_USERNAME"),
            login_password=get_env("QMS_LOGIN_PASSWORD"),
        )

    def is_fully_configured(self) -> bool:
        """민감 3종(secret/계정/비밀번호)이 모두 설정되어 있는지."""
        return bool(self.client_secret and self.login_username and self.login_password)

    def __repr__(self) -> str:  # secret 마스킹
        parts = []
        for f in self.__dataclass_fields__:
            val = getattr(self, f)
            if f in _SECRET_FIELDS:
                shown = "***" if val else None
            else:
                shown = val
            parts.append(f"{f}={shown!r}")
        return f"QMSSettings({', '.join(parts)})"


@dataclass
class ProxySettings:
    """QMS 프록시 서버 설정 초안(환경변수 기반, 참조용)."""

    proxy_key: str | None = field(default=None, repr=False)  # repr 노출 금지
    allow_insecure: bool = False
    list_ttl_sec: int = 60
    detail_ttl_sec: int = 300
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "ProxySettings":
        return cls(
            proxy_key=get_env("QMS_PROXY_KEY"),
            allow_insecure=get_bool_env("QMS_PROXY_ALLOW_INSECURE", False),
            list_ttl_sec=get_int_env("QMS_PROXY_LIST_TTL", 60),
            detail_ttl_sec=get_int_env("QMS_PROXY_DETAIL_TTL", 300),
            log_level=get_env("QMS_PROXY_LOG_LEVEL", "INFO") or "INFO",
        )

    def has_key(self) -> bool:
        return bool(self.proxy_key)
