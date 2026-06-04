# -*- coding: utf-8 -*-
"""
QMS 알림 시스템 — Slack Webhook + 이메일(SMTP)

환경변수:
  QMS_SLACK_WEBHOOK   : Slack Incoming Webhook URL
  QMS_SMTP_HOST       : SMTP 서버 (예: smtp.gmail.com)
  QMS_SMTP_PORT       : SMTP 포트 (기본 587)
  QMS_SMTP_USER       : 발신 계정
  QMS_SMTP_PASS       : 발신 비밀번호
  QMS_ALERT_TO        : 수신자 이메일 (콤마 구분)

사용법:
  python qms_alert.py            # CLI 직접 실행 → 즉시 기한 초과 알림 발송
  import qms_alert; qms_alert.run_overdue_alert(F, PROJECT_META)
"""

import os
import smtplib
import json
import urllib.request
import urllib.parse
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any


# ─── Slack ───────────────────────────────────────────────────────────────────

def send_slack(webhook_url: str, text: str, blocks: list | None = None) -> bool:
    """Slack Webhook으로 메시지 전송. 성공 시 True."""
    if not webhook_url:
        webhook_url = os.environ.get("QMS_SLACK_WEBHOOK", "")
    if not webhook_url:
        raise ValueError("Slack Webhook URL이 설정되지 않았습니다. QMS_SLACK_WEBHOOK 환경변수를 설정하세요.")
    payload: dict[str, Any] = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status == 200


def _build_slack_blocks(overdue_items: list[dict]) -> list[dict]:
    """기한 초과 항목을 Slack Block Kit 형식으로 변환."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🚨 QMS 기한 초과 알림 ({now_str})"},
        },
        {"type": "divider"},
    ]
    for item in overdue_items[:20]:
        proj = item.get("프로젝트", "-")
        title = str(item.get("제목", "-"))[:40]
        dday = item.get("D-day", "?")
        deadline = item.get("기한일", "-")
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*[{proj}]* {title}\n"
                    f"> D-day: *{dday}일* | 기한일: {deadline}"
                ),
            },
        })
    if len(overdue_items) > 20:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"외 {len(overdue_items) - 20}건 추가 초과"}],
        })
    return blocks


# ─── 이메일 ──────────────────────────────────────────────────────────────────

def send_email(
    subject: str,
    body: str,
    to_addrs: list[str],
    html: bool = True,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_user: str | None = None,
    smtp_pass: str | None = None,
) -> bool:
    """SMTP로 이메일 전송. 성공 시 True."""
    host = smtp_host or os.environ.get("QMS_SMTP_HOST", "smtp.gmail.com")
    port = smtp_port or int(os.environ.get("QMS_SMTP_PORT", 587))
    user = smtp_user or os.environ.get("QMS_SMTP_USER", "")
    pwd  = smtp_pass or os.environ.get("QMS_SMTP_PASS", "")

    if not user:
        raise ValueError("SMTP 발신 계정이 설정되지 않았습니다. QMS_SMTP_USER 환경변수를 설정하세요.")
    if not to_addrs:
        raise ValueError("수신자 주소가 없습니다.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to_addrs)
    part = MIMEText(body, "html" if html else "plain", "utf-8")
    msg.attach(part)

    with smtplib.SMTP(host, port, timeout=15) as server:
        server.ehlo()
        server.starttls()
        if pwd:
            server.login(user, pwd)
        server.sendmail(user, to_addrs, msg.as_string())
    return True


def _build_email_html(overdue_items: list[dict]) -> str:
    """기한 초과 항목을 HTML 이메일 본문으로 변환."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = ""
    for item in overdue_items[:50]:
        proj    = item.get("프로젝트", "-")
        title   = str(item.get("제목", "-"))[:50]
        dday    = item.get("D-day", "?")
        deadline = item.get("기한일", "-")
        bg = "#fff5f5" if int(dday) < -7 else "#fffbf0"
        color = "#c62828" if int(dday) < -7 else "#e65100"
        rows += f"""
        <tr style="background:{bg}">
            <td style="padding:8px 12px;border-bottom:1px solid #eee">{proj}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee">{title}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;color:{color};font-weight:700">{dday}일</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee">{deadline}</td>
        </tr>"""
    return f"""
    <html><body style="font-family:sans-serif;color:#212121">
    <div style="background:#0d1b3e;color:#fff;padding:16px 24px;border-radius:8px 8px 0 0">
        <h2 style="margin:0;font-size:1.1rem">🚨 QMS 기한 초과 알림</h2>
        <p style="margin:4px 0 0;font-size:0.85rem;opacity:0.8">{now_str} | 광동제약 품질부문</p>
    </div>
    <div style="padding:16px 0">
        <p>총 <b>{len(overdue_items)}건</b>의 기한 초과 항목이 있습니다.</p>
        <table style="border-collapse:collapse;width:100%;font-size:0.88rem">
            <thead>
                <tr style="background:#f3f4f8">
                    <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #0d1b3e">프로젝트</th>
                    <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #0d1b3e">제목</th>
                    <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #0d1b3e">D-day</th>
                    <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #0d1b3e">기한일</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        {"<p style='color:#888;font-size:0.8rem'>상위 50건 표시</p>" if len(overdue_items) > 50 else ""}
    </div>
    <div style="background:#f3f4f8;padding:10px 24px;font-size:0.77rem;color:#666;border-radius:0 0 8px 8px">
        KD-MoaQ | 광동제약 품질부문
    </div>
    </body></html>"""


# ─── 통합 알림 실행 ──────────────────────────────────────────────────────────

def run_overdue_alert(F: dict, PROJECT_META: dict, threshold_days: int = 0) -> dict:
    """
    모든 프로젝트 DataFrame에서 기한 초과 항목을 수집해
    Slack과 이메일로 알림 발송.

    Returns: {"slack": bool, "email": bool, "count": int}
    """
    import pandas as pd

    overdue_items: list[dict] = []
    for pk, df_p in F.items():
        if df_p is None or (hasattr(df_p, "empty") and df_p.empty):
            continue
        if "D-day" not in df_p.columns:
            continue
        mask = df_p["D-day"].notna() & (df_p["D-day"] < threshold_days)
        for _, row in df_p[mask].iterrows():
            overdue_items.append({
                "프로젝트": PROJECT_META.get(pk, {}).get("label", pk),
                "관리번호": row.get("관리번호", "-"),
                "제목": row.get("제목", "-"),
                "기한일": row.get("기한일", "-"),
                "D-day": int(row["D-day"]),
            })

    overdue_items.sort(key=lambda x: x["D-day"])
    results = {"slack": False, "email": False, "count": len(overdue_items)}

    if not overdue_items:
        return results

    summary = f"🚨 QMS 기한 초과: {len(overdue_items)}건 ({datetime.now().strftime('%Y-%m-%d %H:%M')})"

    # Slack 발송
    slack_url = os.environ.get("QMS_SLACK_WEBHOOK", "")
    if slack_url:
        try:
            blocks = _build_slack_blocks(overdue_items)
            results["slack"] = send_slack(slack_url, summary, blocks)
        except Exception as e:
            print(f"[qms_alert] Slack 발송 실패: {e}")

    # 이메일 발송
    to_raw = os.environ.get("QMS_ALERT_TO", "")
    to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
    if to_addrs and os.environ.get("QMS_SMTP_USER"):
        try:
            html_body = _build_email_html(overdue_items)
            results["email"] = send_email(
                subject=f"[QMS 알림] 기한 초과 {len(overdue_items)}건 ({datetime.now().strftime('%Y-%m-%d')})",
                body=html_body,
                to_addrs=to_addrs,
            )
        except Exception as e:
            print(f"[qms_alert] 이메일 발송 실패: {e}")

    return results


# ─── CLI 진입점 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("[qms_alert] QMS 기한 초과 알림 발송 시작")
    # CLI 실행 시: fetch 없이 환경변수만으로 테스트 가능하도록
    # 실제 데이터가 필요하면 qms_fetch_uncached 등 직접 import 후 호출하면 됨
    if "--test" in sys.argv:
        # 테스트 모드: 더미 데이터로 Slack/이메일 발송
        dummy_item = {
            "프로젝트": "OOS",
            "관리번호": 9999,
            "제목": "[테스트] 알림 발송 테스트 항목",
            "기한일": datetime.now().strftime("%Y-%m-%d"),
            "D-day": -3,
        }
        slack_url = os.environ.get("QMS_SLACK_WEBHOOK", "")
        if slack_url:
            ok = send_slack(slack_url, "✅ QMS 알림 테스트 메시지입니다.", _build_slack_blocks([dummy_item]))
            print(f"Slack 전송: {'성공' if ok else '실패'}")
        to_raw = os.environ.get("QMS_ALERT_TO", "")
        to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
        if to_addrs:
            ok = send_email("[QMS 테스트] 알림 테스트", _build_email_html([dummy_item]), to_addrs)
            print(f"이메일 전송: {'성공' if ok else '실패'}")
    else:
        print("사용법: python qms_alert.py --test")
        print("       또는 대시보드 설정 탭에서 알림 발송 버튼 사용")
