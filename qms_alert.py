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


# ════════════════════════════════════════════════════════════════════════════
# 알림 명부(수신자 라우팅) — 1차: 표시·미리보기(dry-run)만. **실제 발송 없음.**
#   · load_alert_roster        : 사내 주소록(이름→이메일) 로더(단일 소스, ①②가 공용)
#   · normalize_person_name    : 이름 정규화(공백/괄호/직급 제거)
#   · preview_overdue_routing  : 기한위험(미완료·미취소·D-day<0) 건을 담당자+등록자
#     개인 다이제스트로 라우팅한 결과를 '리포트(dict)'로 반환(발송 0).
# 기존 send_*/run_overdue_alert(콤마목록) 경로는 변경하지 않는다(fallback 보존).
# 개인정보(이름·이메일)는 로컬 파일에서만 읽으며 어떤 주소도 상수로 두지 않고 로깅하지 않는다.
# ════════════════════════════════════════════════════════════════════════════
_ROSTER_NAME_COLS = ("이름", "성명", "name")
_ROSTER_EMAIL_COLS = ("전자우편", "이메일", "email", "e-mail", "메일")
MANAGER_COL_CANDIDATES = ("담당자",)
REGISTRANT_COL_CANDIDATES = ("등록자",)


def _default_roster_path() -> str:
    p = os.environ.get("QMS_ALERT_ROSTER_PATH", "").strip()
    if p:
        return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "contact (1).xls")


def load_alert_roster(path=None):
    """사내 알림 명부 로더(단일 소스) → (name_to_email: dict, df: DataFrame[이름,이메일]).

    시트 '주소록'(없으면 첫 시트)의 이름/전자우편을 읽어 이름 strip 정규화.
    파일 없거나 비면 ({}, 빈 DataFrame). *개인정보는 로깅하지 않는다.*
    """
    import pandas as pd
    empty = ({}, pd.DataFrame(columns=["이름", "이메일"]))
    path = path or _default_roster_path()
    if not path or not os.path.exists(path):
        return empty
    try:
        engine = "xlrd" if str(path).lower().endswith(".xls") else "openpyxl"
        xls = pd.ExcelFile(path, engine=engine)
        sheet = "주소록" if "주소록" in xls.sheet_names else xls.sheet_names[0]
        df = pd.read_excel(xls, sheet_name=sheet)
    except Exception:
        return empty
    name_col = next((c for c in df.columns if str(c).strip() in _ROSTER_NAME_COLS), None)
    email_col = next((c for c in df.columns if str(c).strip() in _ROSTER_EMAIL_COLS), None)
    if name_col is None or email_col is None:
        return empty
    out = df[[name_col, email_col]].copy()
    out.columns = ["이름", "이메일"]
    out["이름"] = out["이름"].astype(str).str.strip()
    out["이메일"] = out["이메일"].astype(str).str.strip()
    out = out[(out["이름"] != "") & (out["이름"].str.lower() != "nan")
              & (out["이메일"].str.contains("@", na=False))]
    return dict(zip(out["이름"], out["이메일"])), out.reset_index(drop=True)


_NAME_TITLES = (
    "사장", "부사장", "전무", "상무", "이사", "부장", "차장", "과장", "대리", "사원",
    "팀장", "실장", "본부장", "센터장", "그룹장", "파트장", "직장", "반장",
    "선임", "책임", "수석", "주임", "연구원", "연구위원", "매니저", "원장", "소장", "님",
)


def normalize_person_name(name) -> str:
    """이름 정규화: 괄호 표기·후행 직급 제거(이름 내부 공백은 보존). '홍길동(QC2)'·'홍길동 차장' → '홍길동'."""
    import re
    if name is None:
        return ""
    s = str(name).strip()
    if not s or s.lower() == "nan":
        return ""
    s = re.sub(r"[\(\（\[].*?[\)\）\]]", "", s).strip()   # 괄호/대괄호 안 제거
    # 후행 직급(공백 동반)만 제거 — 내부 공백 이름은 보존
    for _t in _NAME_TITLES:
        if s.endswith(" " + _t):
            s = s[: -len(_t)].strip()
            break
    return s.strip()


def _resolve_person_cols(df):
    cols = list(df.columns)
    mgr = next((c for c in MANAGER_COL_CANDIDATES if c in cols), None)
    reg = next((c for c in REGISTRANT_COL_CANDIDATES if c in cols), None)
    return mgr, reg


def preview_overdue_routing(F: dict, PROJECT_META: dict, name_to_email: dict,
                            threshold_days: int = 0, exclude_names=None) -> dict:
    """기한위험(미완료·미취소·D-day<0) 건을 담당자+등록자 개인 다이제스트로 라우팅 — **dry-run**.

    실제 발송은 하지 않고 라우팅 결과 리포트(dict)만 반환. 활성(미완료·미취소) 필터는
    domain.metrics.active_mask 재사용(없으면 D-day<0 만).
    exclude_names: 퇴사자 등 제외 명단(정규화 후 후보에서 제거 — 미매칭으로도 집계하지 않음).
    """
    import pandas as pd
    try:
        from qms_pro.domain.metrics import active_mask
    except Exception:
        active_mask = None
    _excl = {normalize_person_name(x) for x in (exclude_names or []) if normalize_person_name(x)}

    cols_used, items = {}, []
    for pk, df_p in F.items():
        if df_p is None or getattr(df_p, "empty", True) or "D-day" not in df_p.columns:
            continue
        label = PROJECT_META.get(pk, {}).get("label", pk)
        mgr_col, reg_col = _resolve_person_cols(df_p)
        cols_used[label] = {"담당자": mgr_col, "등록자": reg_col}
        sub = df_p
        if active_mask is not None:
            try:
                sub = df_p[active_mask(df_p)]
            except Exception:
                sub = df_p
        dd = pd.to_numeric(sub["D-day"], errors="coerce")
        for _, row in sub[dd.notna() & (dd < threshold_days)].iterrows():
            items.append({
                "관리번호": row.get("관리번호", "-"), "프로젝트": label,
                "제목": row.get("제목", "-"), "기한일": row.get("기한일", "-"),
                "D-day": int(row["D-day"]) if pd.notna(row.get("D-day")) else None,
                "담당자_raw": row.get(mgr_col) if mgr_col else None,
                "등록자_raw": row.get(reg_col) if reg_col else None,
            })

    digests, unmatched_names, admin_fallback = {}, {}, []
    for it in items:
        cands = [(role, normalize_person_name(it.get(key)))
                 for role, key in (("담당자", "담당자_raw"), ("등록자", "등록자_raw"))]
        cands = [(r, nm) for r, nm in cands if nm and nm not in _excl]   # 빈값·제외명단(퇴사자) 제거
        seen, dedup = set(), []
        for r, nm in cands:
            if nm not in seen:
                seen.add(nm); dedup.append((r, nm))
        matched = [(r, nm) for r, nm in dedup if nm in name_to_email]
        unmatched = [(r, nm) for r, nm in dedup if nm not in name_to_email]
        for r, nm in unmatched:
            unmatched_names[nm] = unmatched_names.get(nm, 0) + 1
        if not matched:
            admin_fallback.append(it)
            continue
        other_unmatched = ", ".join(sorted({nm for _, nm in unmatched}))
        for r, nm in matched:
            d = digests.setdefault(name_to_email[nm], {"이름": nm, "items": []})
            d["items"].append({**{k: it[k] for k in ("관리번호", "프로젝트", "제목", "기한일", "D-day")},
                               "역할": r, "상대미매칭": other_unmatched})

    person_table = []
    for email, d in digests.items():
        roles = {}
        for r in d["items"]:
            roles[r["역할"]] = roles.get(r["역할"], 0) + 1
        person_table.append({"이름": d["이름"], "이메일": email, "건수": len(d["items"]), "역할분포": roles})
    person_table.sort(key=lambda x: -x["건수"])
    return {
        "dry_run": True,
        "items_total": len(items),
        "columns_used": cols_used,
        "recipients": len(digests),
        "person_table": person_table,
        "unmatched_names": dict(sorted(unmatched_names.items(), key=lambda x: -x[1])),
        "unmatched_unique": len(unmatched_names),
        "admin_fallback_count": len(admin_fallback),
        "excluded_names": sorted(_excl),
        "digests": digests,
    }


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
