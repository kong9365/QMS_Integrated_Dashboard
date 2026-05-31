# -*- coding: utf-8 -*-
"""
QMS OOS 자동 수집 프로그램 (API 모드 전용)
- REST API를 통해 QMS 데이터를 수집
- 기존 QMS.py의 API 모드와 동일한 기능
- taskId 버그 수정 + SSL 자체서명 인증서 지원
"""
import sys
import os
import json
import time
import logging
import re
import html
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
import urllib3
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

os.environ["PYTHONIOENCODING"] = "utf-8"

# ============================================================================
# 설정
# ============================================================================

# 자격증명/접속정보: 전적으로 환경변수(.env)에서 읽는다. 미설정 시 빈 값.
# (하드코딩 폴백을 두지 않는다 — 자격증명을 소스코드/저장소에 남기지 않기 위함.)
# 설정 방법은 secrets.example.env 를 .env 로 복사 후 값 입력. 미설정 시 아래에서 1회 경고.
API_BASE_URL = os.getenv("QMS_API_BASE_URL", "")
API_LIST_ENDPOINT = "/API/workflow/task/list"
API_DETAIL_ENDPOINT = "/API/workflow/task/getWorfklowInfo"
API_LOGIN_ENDPOINT = "/API/user/loginForKeycloak"
CLIENT_NAME = os.getenv("QMS_CLIENT_NAME", "")
CLIENT_SECRET = os.getenv("QMS_CLIENT_SECRET", "")
REALM_NAME = os.getenv("QMS_REALM_NAME", "")

LOGIN_USERNAME = os.getenv("QMS_LOGIN_USERNAME", "")
LOGIN_PASSWORD = os.getenv("QMS_LOGIN_PASSWORD", "")

# 민감 환경변수(server / client secret / 계정 / 비밀번호)가 미설정인지 여부.
# 미설정이면 API 호출이 실패하므로 로거 설정 후 1회 경고한다. 실제 값은 절대 출력하지 않는다.
_CREDENTIAL_MISSING = not all((API_BASE_URL, CLIENT_SECRET, LOGIN_USERNAME, LOGIN_PASSWORD))

debug_mode = True

save_dir = Path(r"C:\Temp\QMS_수집")
save_dir.mkdir(parents=True, exist_ok=True)

existing_qms_cache_path = save_dir / "existing_qms_ids.json"

timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

# ============================================================================
# 로깅
# ============================================================================

logger = logging.getLogger("QMS_API_Crawler")
logger.setLevel(logging.DEBUG if debug_mode else logging.WARNING)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

log_file_path = save_dir / f"QMS_수집_로그_{timestamp_str}.log"
try:
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.info(f"📋 로그 파일 저장: {log_file_path}")
except Exception as e:
    logger.warning(f"⚠️ 로그 파일 생성 실패: {e}")

# 민감 자격증명이 환경변수에 설정되지 않았으면 1회 경고 (실제 값은 출력하지 않음)
if _CREDENTIAL_MISSING:
    logger.warning(
        "[CONFIG] QMS credentials are not set. "
        "Copy secrets.example.env to .env and fill in QMS_API_BASE_URL / "
        "QMS_CLIENT_SECRET / QMS_LOGIN_USERNAME / QMS_LOGIN_PASSWORD before use."
    )

log_records = []


def log_and_record(level: str, message: str):
    log_records.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {level.upper()} - {message}")
    getattr(logger, level, logger.info)(message)


# ============================================================================
# JSON 파서 함수들
# ============================================================================

def extract_data_from_response(json_data: dict) -> dict:
    """API 응답에서 실제 폼 데이터를 추출 (propertyEntity > workflowEntity > extention)"""
    if not isinstance(json_data, dict):
        return {}

    data = json_data.get("data", json_data)

    property_entity = None
    if isinstance(data, dict):
        property_entity = data.get("propertyEntity") or json_data.get("propertyEntity")

    if property_entity and isinstance(property_entity, dict) and "workflowEntity" in property_entity:
        workflow_entity = property_entity.get("workflowEntity", {})
        if isinstance(workflow_entity, dict):
            extention_str = workflow_entity.get("extention", "")
            if extention_str:
                try:
                    extention_data = json.loads(extention_str)
                    if isinstance(extention_data, list):
                        parsed = {}
                        for item in extention_data:
                            if isinstance(item, dict) and "key" in item and "value" in item:
                                parsed[item["key"]] = item["value"]
                        if parsed:
                            data = parsed
                    elif isinstance(extention_data, dict):
                        data = extention_data
                except (json.JSONDecodeError, TypeError):
                    pass

    return data if isinstance(data, dict) else {}


def extract_test_item_code(test: dict) -> str:
    """testInfo / 영향품목(affectedItems) 행에서 품목코드 후보 통합 추출.

    API·버전에 따라 `itemCd`, SAP 연동 시 `matnr`·`zzinmat` 등 키가 다름.
    """
    if not isinstance(test, dict):
        return ""
    for k in ("itemCd", "itemcd", "ITEM_CD", "item_cd", "ITEMCD", "matnr", "MATNR", "zzinmat", "ZZINMAT"):
        v = test.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() not in ("none", "null", ""):
            return s
    return ""


def affected_items_to_test_rows(affected) -> list[dict]:
    """일탈·외주 등 `affectedItems`(SAP 스타일)만 있고 testInfo가 빈 경우 품목 행 생성."""
    rows: list[dict] = []
    if not isinstance(affected, list):
        return rows
    for it in affected:
        if not isinstance(it, dict):
            continue
        code = extract_test_item_code(it)
        name = str(
            it.get("maktx") or it.get("maktx2") or it.get("maktx3") or it.get("itemNmReport") or ""
        ).strip()
        if code or name:
            rows.append({
                "시험종류": "",
                "품목코드": code,
                "품목명": name,
                "제조번호": "",
                "시험항목": "",
            })
    return rows


def normalize_find_date_to_display(find_date) -> str:
    """QMS findDate를 'YYYY-MM-DD HH:MM:SS'로 통일.

    API가 ISO가 아닌 '2026.04.08' 형태를 주면 datetime.fromisoformat이 실패하고
    원문 문자열이 그대로 들어가 대시보드 DateColumn 등에서 None/NaT로 보일 수 있다.
    """
    if find_date is None:
        return ""
    s = str(find_date).strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return s
    s2 = s.replace("+09:00", "")
    if len(s2) >= 1 and s2[-1] == "Z":
        s2 = s2[:-1]
    try:
        dt = datetime.fromisoformat(s2)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return s


def classify_deviation_vs_incident(ext: dict) -> str:
    """이벤트 구분 판정 (일탈/인시던트).

    판정 기준 (사용자 확정):
    - status 에 ``finalCheckInsident`` 포함 → ``인시던트``
    - status 에 ``finalCheck`` 포함(단, Insident 아님) → ``일탈``
    - 그 외(종결 전·판정 불가) → ``인시던트`` (fallback)
    """
    if not isinstance(ext, dict):
        return "인시던트"
    status = str(ext.get("status", "") or "")
    if "finalCheckInsident" in status:
        return "인시던트"
    if "finalCheck" in status:
        return "일탈"
    return "인시던트"


_GRADE_CANONICAL = {
    "critical": "Critical",
    "major": "Major",
    "minor": "Minor",
}


def classify_deviation_grade(rating: str, event_kind: str) -> str:
    """일탈 등급 대분류 (Critical/Major/Minor/인시던트/미판정) 산출.

    보고서 4.1 의 "등급 | 총계 | Critical | Major | Minor | 인시던트" 컬럼을 재현하기
    위한 분류 키. 이벤트 구분이 인시던트이면 등급을 따지지 않고 ``인시던트`` 로 고정.
    """
    if event_kind == "인시던트":
        return "인시던트"
    s = str(rating or "").strip()
    if not s or s == "-":
        return "미판정"
    head = s.split()[0].strip().lower()
    return _GRADE_CANONICAL.get(head, "미판정")


def _normalize_date_10(value) -> str:
    """임의 datetime 문자열을 ``YYYY-MM-DD`` 로 정규화. 파싱 실패 시 앞 10자."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    s_clean = s.replace("+09:00", "")
    if s_clean.endswith("Z"):
        s_clean = s_clean[:-1]
    try:
        dt = datetime.fromisoformat(s_clean)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    m = re.match(r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if m:
        try:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        except Exception:
            pass
    return s[:10]


def _manufacturing_label(yn: str) -> str:
    """manufacturingDeviation y/n → '제조'/'품질실' (기타/빈값은 ``)."""
    v = str(yn or "").strip().lower()
    if v == "y":
        return "제조"
    if v == "n":
        return "품질실"
    return ""


def parse_qms_detail_json(json_data: dict) -> dict:
    data = extract_data_from_response(json_data)
    if not data:
        return {}

    result = {}

    result["제목"] = data.get("title", "")
    find_date = data.get("findDate", "")
    disp = normalize_find_date_to_display(find_date)
    if disp:
        result["발견일시"] = disp

    team_map = {"first": "품질관리1팀", "second": "품질관리2팀"}
    result["작성팀"] = team_map.get(data.get("teamName", ""), data.get("teamName", ""))

    reg_match_map = {"y": "YES", "n": "NO"}
    result["OOS등록자와 시험자 일치여부"] = reg_match_map.get(data.get("registrarApproverMatchYN", ""), data.get("registrarApproverMatchYN", ""))
    result["OOS등록자와 시험자 불일치 이유"] = data.get("registrarApproverMatchYNText", "")

    test_info_list = []
    for test in data.get("testInfo", []):
        lot_no = test.get("lotNo", "")
        제조번호 = f"'{lot_no}" if lot_no and lot_no.isdigit() and len(lot_no) > 10 else lot_no
        ic = extract_test_item_code(test) or str(test.get("itemCd", "") or "").strip()
        test_info_list.append({
            "시험종류": test.get("bizprocessNm", ""),
            "품목코드": ic,
            "품목명": test.get("itemNmReport", ""),
            "제조번호": 제조번호,
            "시험항목": test.get("testitemNm", ""),
            "시험기준": test.get("standardText", ""),
            "의뢰번호": test.get("requestNo", ""),
            "시험결과": test.get("resultValue", ""),
        })
    result["시험정보목록"] = test_info_list

    device_infos = data.get("deviceInfo", [])
    result["기기명"] = device_infos[0].get("equipNm", "") if device_infos else ""
    result["허용기준"] = data.get("acceptableStan", "")
    result["최초 시험결과"] = data.get("result", "")

    attach_yn_map = {"y": "YES", "n": "NO"}
    result["첨부파일 여부"] = attach_yn_map.get(data.get("attachFileYN", ""), data.get("attachFileYN", ""))
    attach_files = data.get("attachFile1", [])
    if isinstance(attach_files, list):
        file_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in attach_files]
        result["첨부파일"] = ", ".join([f for f in file_names if f])
    else:
        result["첨부파일"] = ""

    result["처리기한"] = data.get("limitDate", "")

    obvious_map = {"y": "명백한 오류(Non test result)", "n": "명백하지 않은 오류(Suspect result)", "na": "N/A"}
    result["시험실 이벤트 유형"] = obvious_map.get(data.get("obviousErrors", ""), data.get("obviousErrors", ""))
    result["이벤트 정보"] = data.get("eventInfo", "")
    result["작성자"] = data.get("regUsername", "")
    result["시험조사자"] = data.get("investigator", "")
    # OOS 폼에는 일탈의 reportTeam에 해당하는 필드가 없음 — 원본 탭 컬럼 정렬용
    result["보고부서"] = ""

    return result


def parse_lab_investigation_json(json_data: dict) -> dict:
    data = extract_data_from_response(json_data)
    if not data:
        return {}

    result = {}

    review_map = {"g": "일반", "m": "미생물", "na": "N/A"}
    result["Check list 검토 진행여부"] = review_map.get(data.get("reviewProgress", ""), data.get("reviewProgress", ""))

    checklist_items = []
    for i in range(1, 16):
        c_val = data.get(f"c{i}", "")
        comment = data.get(f"comment{i}", "")
        if c_val or comment:
            c_map = {"y": "YES", "n": "NO", "na": "N/A", "uncertain": "불확실"}
            item_text = f"항목{i}: {c_map.get(c_val, c_val)}"
            if comment:
                item_text += f" (의견: {comment})"
            checklist_items.append(item_text)
    result["Check list 항목 (일반)"] = "\n".join(checklist_items) if checklist_items else ""

    checklist_micro = []
    for i in range(1, 20):
        d_val = data.get(f"d{i}", "")
        comment = data.get(f"comment{i+15}", "")
        if d_val or comment:
            d_map = {"y": "YES", "n": "NO", "na": "N/A", "uncertain": "불확실"}
            item_text = f"항목{i}: {d_map.get(d_val, d_val)}"
            if comment:
                item_text += f" (의견: {comment})"
            checklist_micro.append(item_text)
    result["Check list 항목 (미생물)"] = "\n".join(checklist_micro) if checklist_micro else ""

    reg_comments = data.get("regUsernameCommentGrid", [])
    result["Check list - 작성자 의견"] = reg_comments[0].get("regUsernameComment", "") if reg_comments else ""

    inv_comments = data.get("investigatorCommentGrid", [])
    result["Check list - 시험조사자 의견"] = inv_comments[0].get("investigatorComment", "") if inv_comments else ""

    def _join_comments(grid, text_key, date_key):
        lines = []
        for c in grid:
            text = c.get(text_key, "")
            date = c.get(date_key, "")
            if text:
                lines.append(f"{text} ({date})" if date else text)
        return "\n---\n".join(lines) if lines else ""

    result["시험실 조사 - 작성자 의견"] = _join_comments(data.get("regUsernameCommentGrid", []), "regUsernameComment", "regUsernameCommentDate")
    result["시험실 조사 - 시험조사자 의견"] = _join_comments(data.get("investigatorCommentGrid", []), "investigatorComment", "investigatorCommentDate")

    case_details = []
    for i, case in enumerate(data.get("dataGrid6", []), 1):
        title = case.get("reAnalysisCase", "") or case.get("textField", f"Case {i}")
        block = (f"[Case {i}] {title}\n- 시험조사자 의견(계획): {case.get('investigatorDComment', '')}"
                 f"\n- 작성자 의견(결과): {case.get('regUsername4Comment3', '')}"
                 f"\n- 시험조사자 의견(확인): {case.get('investigatorDComment1', '')}")
        case_details.append(block)
    result["재분석 - 시험조사자 의견(계획)"] = "\n\n".join(case_details) if case_details else ""

    hypo_details = []
    for i, hypo in enumerate(data.get("dataGrid9", []), 1):
        title = hypo.get("hypoCase", "") or hypo.get("textField", f"Case {i}")
        block = (f"[Case {i}] {title}\n- 시험조사자 의견(계획): {hypo.get('investigatorEComment', '')}"
                 f"\n- 작성자 의견(결과): {hypo.get('regUsernameDComment3', '')}"
                 f"\n- 시험조사자 의견(확인): {hypo.get('investigatorEComment1', '')}")
        hypo_details.append(block)
    result["Hypothesis - 시험조사자 의견(계획)"] = "\n\n".join(hypo_details) if hypo_details else ""

    other_comments = data.get("regUsernameCommentGrid", [])
    result["기타 의견 목록"] = [c.get("regUsernameComment", "") for c in other_comments if c.get("regUsernameComment")]
    result["기타조사내용"] = data.get("repeatEtc", "")
    result["기타시험 - 시험조사자 의견(계획)"] = data.get("investigatorFComment", "")
    result["기타시험 - 작성자 의견(결과)"] = data.get("testerAComment", "")
    result["기타시험 - 시험조사자 의견(확인)"] = data.get("investigatorFComment1", "")
    result["시험검토자1 의견"] = data.get("reviewerAComment", "")

    return result


def parse_full_investigation_json(json_data: dict) -> dict:
    data = extract_data_from_response(json_data)
    if not data:
        return {}

    result = {}
    result["전면 조사 담당자"] = data.get("investigationManager", "")

    def _join_comments(grid, text_key, date_key):
        lines = []
        for c in grid:
            text = c.get(text_key, "")
            date = c.get(date_key, "")
            if text:
                lines.append(f"{text} ({date})" if date else text)
        return "\n---\n".join(lines) if lines else ""

    result["품질보증팀 담당자 의견"] = _join_comments(data.get("qaCommentGrid", []), "qaComment", "qaCommentDate")
    result["품질보증팀장 의견"] = _join_comments(data.get("qaReviewerCommentGrid", []), "qaReviewerComment", "qaReviewerCommentDate")
    if not result["품질보증팀장 의견"]:
        result["품질보증팀장 의견"] = data.get("qaReviewerComment", "")

    add_test_plans = data.get("addTestPlan", [])
    if add_test_plans:
        plan_list = []
        for plan in add_test_plans:
            pt_map = {"1": "가설시험", "2": "재시험", "3": "필요없음"}
            cy_map = {"y": "YES", "n": "NO"}
            line = f"구분: {pt_map.get(plan.get('planType', ''), plan.get('planType', ''))}, 재검체채취: {cy_map.get(plan.get('collectYN', ''), plan.get('collectYN', ''))}"
            plan_text = plan.get("plan", "")
            if plan_text:
                line += f"\n계획: {plan_text}"
            plan_list.append(line)
        result["추가시험 계획 검토"] = "\n\n".join(plan_list)
    else:
        result["추가시험 계획 검토"] = ""

    result["품질보증팀 담당자 (추가시험)"] = data.get("qa", "")
    result["품질보증팀 담당자 의견 (추가시험)"] = _join_comments(data.get("qa1CommentGrid", []), "qa1Comment", "qa1CommentDate")
    result["품질보증팀장 의견 (추가시험)"] = _join_comments(data.get("qaReviewer1CommentGrid", []), "qaReviewer1Comment", "qaReviewer1CommentDate")
    result["시험조사자 의견 (추가시험)"] = _join_comments(data.get("investigator1CommentGrid", []), "investigator1Comment", "investigator1CommentDate")
    result["시험검토자2 의견 (추가시험)"] = _join_comments(data.get("reviewerB1CommentGrid", []), "reviewerB1Comment", "reviewerB1CommentDate")
    result["의약품품질부문장 의견 (추가시험)"] = _join_comments(data.get("qualityHead1CommentGrid", []), "qualityHead1Comment", "qualityHead1CommentDate")
    result["품질(보증)부서 책임자 의견 (추가시험)"] = _join_comments(data.get("quality1CommentGrid", []), "quality1Comment", "quality1CommentDate")

    result["전면조사 - 시험조사자 의견"] = data.get("investigator1Comment", "")
    result["전면조사 - 시험검토자2 의견"] = data.get("reviewerB1Comment", "")
    result["전면조사 - 의약품품질부문장 의견"] = data.get("qualityHead1Comment", "")
    result["전면조사 - 품질부서책임자 의견"] = data.get("quality1Comment", "")

    return result


def parse_conclusion_json(json_data: dict) -> dict:
    data = extract_data_from_response(json_data)
    if not data:
        return {}

    result = {}

    retest_plans = data.get("retestPlan", [])
    if retest_plans:
        details = []
        for plan in retest_plans:
            text = f"조치사항/CAPA: {plan.get('caparRadio', '')}"
            sb = plan.get("selectBoxes", {})
            if isinstance(sb, dict):
                selected = []
                if sb.get("column"): selected.append("칼럼 폐기")
                if sb.get("machine"): selected.append("기기수리")
                if sb.get("etc"): selected.append("기타")
                if selected:
                    text += f" ({', '.join(selected)})"
            etc = plan.get("etcComment", "")
            if etc:
                text += f"\n기타 사유: {etc}"
            details.append(text)
        result["결론 - Repeat to Replace Test Plan 선택"] = "\n\n".join(details)
    else:
        result["결론 - Repeat to Replace Test Plan 선택"] = ""

    author_comments = [c.get("regUsername4Comment", "") for c in data.get("regUsername4CommentGrid", []) if c.get("regUsername4Comment")]
    result["결론 - Repeat to Replace Test Plan - 작성자 의견"] = "\n---\n".join(author_comments) if author_comments else ""

    inv_comments = [c.get("investigator4Comment", "") for c in data.get("investigator4CommentGrid", []) if c.get("investigator4Comment")]
    result["결론 - Repeat to Replace Test Plan - 시험조사자 의견"] = "\n---\n".join(inv_comments) if inv_comments else ""

    analysis_results = data.get("analysisResult", [])
    result["결론 - 최종 결론"] = analysis_results[0].get("analysisResultDescription", "") if analysis_results else ""

    process_map = {"l": "시험오류", "t": "OOT", "s": "OOS"}
    result["확인된 이벤트 분류"] = process_map.get(data.get("process", ""), data.get("process", ""))

    retest_yn_on = data.get("retestYN_retestYN_y_on", "")
    result["재시험 필요여부"] = "예" if retest_yn_on else data.get("retestYN", "")

    deviation_map = {"y": "적합", "n": "부적합"}
    result["기준 일탈 최종 결과"] = deviation_map.get(data.get("deviationYN", ""), data.get("deviationYN", ""))

    influence_map = {"y": "있음", "n": "없음"}
    result["타 제조번호 영향"] = influence_map.get(data.get("influenceYN", ""), data.get("influenceYN", ""))

    cause_map = {
        "m": "Method", "a": "Analyst error", "i": "Instrument error",
        "c": "Contamination", "e": "Environment", "man": "Man",
        "mac": "Machine", "mat": "Material", "mea": "Measurement", "o": "Other"
    }
    result["이상발생 원인"] = cause_map.get(data.get("cause", ""), data.get("cause", ""))
    result["이상발생 원인 이유"] = data.get("textArea", "")

    actions = []
    for action in data.get("addAction", []):
        actions.append({
            "조치 내용": action.get("actionDescription", ""),
            "수행자": action.get("performer", ""),
            "완료예정일": action.get("expectedDate", "")
        })
    result["조치사항 및 조치계획"] = actions

    necessary_yn = data.get("necessaryYN", "")
    if isinstance(necessary_yn, str):
        try:
            nd = json.loads(necessary_yn)
            selected = []
            if nd.get("c"): selected.append("Corrective Action")
            if nd.get("m"): selected.append("Preventive Action")
            if nd.get("nA"): selected.append("No Action")
            result["CAPA/Action item 필요여부"] = ", ".join(selected) if selected else ""
        except Exception:
            result["CAPA/Action item 필요여부"] = necessary_yn
    else:
        result["CAPA/Action item 필요여부"] = ""

    result["결론탭 - 작성자 의견"] = data.get("regUsernameComment2", "")
    result["결론탭 - 시험조사자 의견"] = data.get("investigatorIComment", "")
    result["결론탭 - 시험검토자1 의견"] = data.get("reviewerAAComment", "")
    result["결론탭 - 품질(보증)부서 책임자 의견"] = data.get("qualityAComment", "")

    return result


def parse_activity_log_json(json_data: dict) -> dict:
    data = extract_data_from_response(json_data)
    if not data:
        return {}

    result = {}

    for src_key, dst_key in [
        ("rootcauseApproval_rootcauseApproval_y_on", "근본원인 승인 대기 승인 On"),
        ("reanalysisResultAcceptReview_reanalysisResultAcceptReview_y_on", "재분석 결과 승인 대기 On"),
    ]:
        val = data.get(src_key, "")
        if val:
            try:
                dt = datetime.fromisoformat(val.replace("+09:00", ""))
                result[dst_key] = dt.strftime("%Y-%m-%d")
            except Exception:
                result[dst_key] = val
        else:
            result[dst_key] = ""

    return result


# ============================================================================
# 통합 대시보드용 추가 파서 함수들
# ============================================================================

def task_id_detail_candidates(task_id: str) -> list[str]:
    """상세 API용 taskId 후보. 목록에 `-COMPLETED` 접미사가 붙은 경우 접미사 제거본도 시도."""
    t = str(task_id or "").strip()
    if not t:
        return []
    out = [t]
    suf = "-COMPLETED"
    if len(t) > len(suf) and t.upper().endswith(suf.upper()):
        stripped = t[: -len(suf)]
        if stripped and stripped != t:
            out.append(stripped)
    return out


def resolve_list_task_id(item: dict) -> str:
    """목록 항목에서 작업 ID 추출 (API/버전별 키 이름 차이 대응)."""
    if not isinstance(item, dict):
        return ""
    for key in (
        "taskId",
        "taskID",
        "task_id",
        "workflowTaskId",
        "wfTaskId",
        "currentTaskId",
    ):
        v = item.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    for k, v in item.items():
        if not isinstance(k, str) or v is None:
            continue
        ks = k.replace("_", "").lower()
        if "taskid" in ks and str(v).strip():
            return str(v).strip()
    return ""


_EXT_PREFIX = "_ext_"
_EXT_SKIP_KEYS = {
    "tenantId", "tenantid",
}
_EXT_VALUE_MAX = 2000


def _ext_column_name(label: str, api_key: str, used: set[str]) -> str:
    """`_ext_<한글라벨>` 형태의 컬럼 이름. 충돌 시 `(key)` 를 덧붙여 고유화."""
    base_label = (label or api_key).strip().replace("\n", " ").replace("|", "／")
    base_label = base_label[:60] if len(base_label) > 60 else base_label
    name = f"{_EXT_PREFIX}{base_label}"
    if name in used:
        name = f"{_EXT_PREFIX}{base_label}({api_key})"
        i = 2
        while name in used:
            name = f"{_EXT_PREFIX}{base_label}({api_key})#{i}"
            i += 1
    used.add(name)
    return name


def _stringify_ext_value(v):
    if v is None:
        return ""
    # bool 은 int 보다 먼저 분기 (isinstance(True, int) == True 함정 회피)
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (str, int, float)):
        return v
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        s = str(v)
    return s[:_EXT_VALUE_MAX]


def enrich_with_raw_extention(row: dict, detail: dict, project: str) -> dict:
    """parser 결과 `row` 에 extention 전체 key 를 `_ext_<한글라벨>` 컬럼으로 추가.

    - 한글 라벨은 `qms_label_map.get_label_map(project)` 에서 조회; 없으면 원본 영문 key.
    - list/dict 값은 JSON 문자열로 직렬화 후 2000자로 절단.
    - 기존 row 에 있는 (parser 매핑된) 한국어 칼럼과는 접두 `_ext_` 로 항상 구분되므로 충돌 없음.
    - 동일 라벨이 여러 영문 key 에 매핑될 수 있어(예: `regUsername*`) 중복 컬럼이 생기면
      접미 `(api_key)` 로 구분.
    """
    if not isinstance(detail, dict):
        return row
    ext = extract_data_from_response(detail)
    if not isinstance(ext, dict) or not ext:
        return row
    from qms_label_map import get_label_map
    label_map = get_label_map(project)
    used: set[str] = {c for c in row.keys() if isinstance(c, str) and c.startswith(_EXT_PREFIX)}
    for k, v in ext.items():
        if not isinstance(k, str) or not k:
            continue
        if k in _EXT_SKIP_KEYS:
            continue
        label = label_map.get(k, k)
        col = _ext_column_name(label, k, used)
        row[col] = _stringify_ext_value(v)
    return row


def parse_list_only(item: dict, project: str) -> dict:
    """목록 API 응답 단일 항목에서 공통 필드만 추출 (detail 조회 없음)."""
    reg_user = str(item.get("regUserName", "") or "")
    # "홍길동 <id@example.com>" 형태에서 이름만 추출
    name_part = reg_user.split("<")[0].strip() if "<" in reg_user else reg_user

    return {
        "프로젝트": project,
        "관리번호": item.get("prno"),
        "제목": str(item.get("title", "") or ""),
        "진행상태": str(item.get("status", "") or ""),
        "완료여부": str(item.get("taskCondition", "") or ""),
        "등록일": str(item.get("regDate") or item.get("writeDate", "") or "")[:10],
        "기한일": str(item.get("limitDate", "") or ""),
        "등록자": name_part,
        "상위번호": item.get("parentPrno"),
        "taskId": resolve_list_task_id(item),
        # 목록 API 등록 시각 — 발견일시 없을 때 연도 필터 보정용 (대시보드 후처리에서 제거)
        "_list_writeDate": str(item.get("writeDate") or "").strip(),
    }


def parse_deviation_json(json_data: dict) -> dict:
    """이벤트(일탈) 상세 정보 파싱."""
    data = extract_data_from_response(json_data)
    if not data:
        return {}

    result = {}
    result["제목"] = data.get("title", "")

    find_date = data.get("findDate", "") or data.get("regDate", "") or data.get("occurrenceDate", "")
    disp = normalize_find_date_to_display(find_date)
    if disp:
        result["발견일시"] = disp

    team_map = {"first": "품질관리1팀", "second": "품질관리2팀"}
    qc_team = team_map.get(data.get("teamName", ""), data.get("teamName", ""))
    report_team = str(data.get("reportTeam", "") or "").strip()
    result["보고부서"] = report_team
    # 차트·필터 공통: 일탈은 보고부서(reportTeam)가 실무 표기인 경우가 많아 우선 사용, 없으면 QC teamName
    result["작성팀"] = report_team or qc_team
    result["QC작성팀"] = qc_team
    result["작성자"] = data.get("regUsername", "")
    result["처리기한"] = data.get("limitDate", "")

    # 일탈 분류
    dev_type_map = {"l": "시험오류", "t": "OOT", "s": "OOS", "d": "일탈"}
    result["일탈 유형"] = dev_type_map.get(data.get("process", ""), data.get("process", ""))

    cause_map = {
        "m": "Method", "a": "Analyst error", "i": "Instrument error",
        "c": "Contamination", "e": "Environment", "man": "Man",
        "mac": "Machine", "mat": "Material", "mea": "Measurement", "o": "Other"
    }
    result["이상발생 원인"] = cause_map.get(data.get("cause", ""), data.get("cause", ""))

    # 시험정보 (OOS와 동일 구조) + 품목코드 다중 키 / testInfo 없으면 affectedItems(SAP)
    test_info_list = []
    for test in data.get("testInfo", []):
        lot_no = test.get("lotNo", "")
        lot_display = f"'{lot_no}" if lot_no and lot_no.isdigit() and len(lot_no) > 10 else lot_no
        test_info_list.append({
            "시험종류": test.get("bizprocessNm", ""),
            "품목코드": extract_test_item_code(test),
            "품목명": test.get("itemNmReport", ""),
            "제조번호": lot_display,
            "시험항목": test.get("testitemNm", ""),
        })
    if not test_info_list:
        test_info_list = affected_items_to_test_rows(data.get("affectedItems"))
    else:
        aff_rows = affected_items_to_test_rows(data.get("affectedItems"))
        if aff_rows:
            existing_codes = {r.get("품목코드") for r in test_info_list if r.get("품목코드")}
            for r in aff_rows:
                c = r.get("품목코드")
                if c and c not in existing_codes:
                    test_info_list.append(r)
                    existing_codes.add(c)
    result["시험정보목록"] = test_info_list

    analysis_results = data.get("analysisResult", [])
    result["최종 결론"] = analysis_results[0].get("analysisResultDescription", "") if analysis_results else ""

    necessary_yn = data.get("necessaryYN", "")
    if isinstance(necessary_yn, str) and necessary_yn:
        try:
            nd = json.loads(necessary_yn)
            selected = []
            if nd.get("c"): selected.append("Corrective Action")
            if nd.get("m"): selected.append("Preventive Action")
            if nd.get("nA"): selected.append("No Action")
            result["CAPA 필요여부"] = ", ".join(selected)
        except Exception:
            result["CAPA 필요여부"] = necessary_yn
    else:
        result["CAPA 필요여부"] = ""

    # 재발 여부 (extention recurrence1: y/n 등)
    rec = data.get("recurrence1", "")
    if rec is not None and str(rec).strip() != "":
        rv = str(rec).strip().lower()
        if rv in ("y", "yes", "1", "true"):
            result["재발여부"] = "예"
        elif rv in ("n", "no", "0", "false"):
            result["재발여부"] = "아니오"
        else:
            result["재발여부"] = str(rec).strip()
    else:
        result["재발여부"] = ""

    # ─── 일탈·인시던트 구분 + 경향분석 필드 (보고서 4.1~4.7 재현용) ───
    event_kind = classify_deviation_vs_incident(data)
    result["이벤트 구분"] = event_kind

    rating_raw = str(data.get("deviationRating1", "") or "").strip()
    result["일탈 등급"] = rating_raw if rating_raw else "-"
    result["일탈 등급 대분류"] = classify_deviation_grade(rating_raw, event_kind)

    result["제조관련 일탈"] = _manufacturing_label(data.get("manufacturingDeviation", ""))
    result["자사/외주"] = "자사"

    recv_date = _normalize_date_10(data.get("receivedDate", ""))
    result["이벤트 접수일자"] = recv_date
    result["접수월"] = recv_date[:7] if recv_date else ""

    result["발생 유형"] = str(data.get("cateCause", "") or "").strip() or "미분류"
    result["발생 세부유형"] = str(data.get("cateCauseDetail1", "") or "").strip()
    result["위탁업체"] = ""

    return result


def parse_capa_json(json_data: dict) -> dict:
    """CAPA 상세 정보 파싱.
    실제 extention 필드: reason, further_investigation, capa_item(list), completion_date, overall_results
    """
    data = extract_data_from_response(json_data)
    if not data:
        return {}

    result = {}
    result["제목"] = data.get("title", "")
    result["작성자"] = str(data.get("regUsername", "") or "").split("<")[0].strip()
    result["처리기한"] = str(data.get("limitDate", "") or "")[:10]
    result["CAPA 구분"] = "제조" if data.get("manufacturingCapaYN") == "y" else "품질"

    # 사유 (CAPA가 열린 근거)
    result["사유"] = data.get("reason", "")

    # 근본원인 조사 내용
    result["근본원인"] = data.get("further_investigation", "") or ""

    # 조치내용: capa_item 배열에서 추출
    actions = []
    for item in data.get("capa_item", []):
        if not isinstance(item, dict):
            continue
        item_type = item.get("capa_item_type", "")
        item_desc = item.get("capa_item_item", "")
        item_date = str(item.get("capa_item_date", "") or "")[:10]
        worker = item.get("investigationWorker", "")
        if item_desc:
            actions.append(f"[{item_type}] {item_desc} (담당: {worker}, 기한: {item_date})")
    result["조치내용"] = " | ".join(actions)

    # 전체 결과
    result["전체 결과"] = data.get("overall_results", "")

    # 완료일
    raw_completion = str(data.get("completion_date", "") or "")
    result["완료일"] = raw_completion[:10] if raw_completion else ""

    # 유효성 평가 여부
    yn_map = {"y": "예", "n": "아니오", "na": "N/A"}
    result["유효성평가 필요"] = yn_map.get(data.get("validationYN", ""), data.get("validationYN", ""))

    return result


def parse_change_json(json_data: dict) -> dict:
    """변경관리 상세 정보 파싱.
    실제 extention 필드: changeGrade, changeReason, changeContent, existContent,
                        division, remark, requestDate, charger, action1(list), affectedItems(list)
    """
    data = extract_data_from_response(json_data)
    if not data:
        return {}

    result = {}
    result["제목"] = data.get("title", "")
    result["작성자"] = str(data.get("regUsername", "") or "").split("<")[0].strip()
    raw_limit = str(data.get("limitDate", "") or "")
    result["처리기한"] = raw_limit[:10] if raw_limit else ""

    # 변경 등급 (실제 필드: changeGrade - lv1/lv2/lv3)
    grade_map = {"lv1": "Level 1 (단순)", "lv2": "Level 2 (일반)", "lv3": "Level 3 (중요)"}
    raw_grade = str(data.get("changeGrade", "") or "")
    result["변경 등급"] = grade_map.get(raw_grade, raw_grade)

    # 변경 구분 (division: permanent / temporary)
    div_map = {"permanent": "영구 변경", "temporary": "임시 변경"}
    raw_div = str(data.get("division", "") or "")
    result["변경 구분"] = div_map.get(raw_div, raw_div)

    # 변경 이유 / 변경 내용 / 기존 내용
    result["변경 이유"] = data.get("changeReason", "")
    result["변경 내용"] = data.get("changeContent", "")
    result["기존 내용"] = data.get("existContent", "")

    # 요청일
    raw_req = str(data.get("requestDate", "") or "")
    result["요청일"] = raw_req[:10] if raw_req else ""

    # 담당자
    result["담당자"] = str(data.get("charger", "") or "").split("<")[0].strip()

    # 검토 의견 (remark 필드)
    result["검토 의견"] = data.get("remark", "")

    # 영향성평가 여부
    yn_map = {"y": "예", "n": "아니오", "na": "N/A"}
    result["영향성평가 필요"] = yn_map.get(str(data.get("impactAssessmentYN", "") or ""), "")

    # 조치 계획 (action1 배열에서 investigationDate, investigationTextarea)
    actions = []
    for act in data.get("action1", []):
        if not isinstance(act, dict):
            continue
        textarea = act.get("investigationTextarea", "")
        inv_date = str(act.get("investigationDate", "") or "")[:10]
        actor = str(act.get("actor", "") or "").split("<")[0].strip()
        if textarea:
            actions.append(f"{textarea} (담당: {actor}, 일자: {inv_date})")
    result["조치 계획"] = " | ".join(actions)

    return result


def parse_complain_json(json_data: dict) -> dict:
    """고객불만 상세 정보 파싱.
    실제 extention 필드: complaint(str), complaintType(dict), complaints(str),
                        processingResults(dict), CauseAnalysisResults, conclusion,
                        complaintComment, open_on, investigationFinalCheck_*_y_on
    """
    data = extract_data_from_response(json_data)
    if not data:
        return {}

    result = {}
    result["제목"] = data.get("title", "")
    result["작성자"] = str(data.get("regUsername", "") or "").split("<")[0].strip()
    raw_limit = str(data.get("limitDate", "") or "")
    result["처리기한"] = raw_limit[:10] if raw_limit else ""

    # 불만 구분 (complaint: "other", "quality" 등 단일 값)
    complaint_map = {
        "quality": "품질불만", "delivery": "납기불만",
        "service": "서비스불만", "other": "기타", "safety": "안전불만",
    }
    raw_complaint = str(data.get("complaint", "") or "")
    result["불만 구분"] = complaint_map.get(raw_complaint, raw_complaint)

    # 불만 유형 (complaintType: dict with bool flags {c: True, f: False, ...})
    ctype = data.get("complaintType", {})
    if isinstance(ctype, dict):
        type_map = {
            "c": "이물", "f": "변질", "i": "이취/이미",
            "d": "포장불량", "e": "용량/중량", "s": "색상변화", "o": "기타"
        }
        selected = [type_map.get(k, k) for k, v in ctype.items() if v and k in type_map]
        result["불만 유형"] = ", ".join(selected)
    else:
        result["불만 유형"] = str(ctype)

    # 접수 내용 (complaints: 실제 불만 텍스트)
    result["접수 내용"] = data.get("complaints", "")

    # 불만 의견
    result["불만 의견"] = data.get("complaintComment", "")

    # 처리 결과 (processingResults: dict {d:bool, e:bool, s:bool, ...})
    presult = data.get("processingResults", {})
    if isinstance(presult, dict):
        presult_map = {
            "d": "교환", "e": "환불", "s": "조사 후 통보",
            "c": "회수", "n": "조치 불필요", "o": "기타"
        }
        presult_list = [presult_map.get(k, k) for k, v in presult.items() if v and k in presult_map]
        result["처리 결과"] = ", ".join(presult_list)
    else:
        result["처리 결과"] = str(presult) if presult else ""

    # 원인분석 결과
    result["원인분석"] = data.get("CauseAnalysisResults", "") or data.get("complaintCauseComment", "")
    result["결론"] = data.get("conclusion", "")

    # 접수일 (open_on: "YYYY-MM-DD HH:MM:SS")
    result["접수일"] = str(data.get("open_on", "") or "")[:10]

    # 처리완료일 (investigationFinalCheck 또는 finalReportWrite)
    result["처리완료일"] = (
        str(data.get("investigationFinalCheck_investigationFinalCheck_y_on", "") or "")[:10]
        or str(data.get("finalReportWrite_finalReportWrite_y_on", "") or "")[:10]
    )

    return result


# ============================================================================
# 추가 프로젝트 파서 (1단계 확장 - 8개 프로젝트)
# ============================================================================

def parse_capaactionitem_json(json_data: dict) -> dict:
    """CAPA Action Item 상세 파싱."""
    data = extract_data_from_response(json_data)
    if not data:
        return {}
    result = {}
    result["제목"] = data.get("title", "")
    result["수행자"] = str(data.get("actor", "") or "").split("<")[0].strip()
    result["작성자"] = str(data.get("regUsername", "") or "").split("<")[0].strip()
    result["검토자"] = str(data.get("reviewer", "") or "").split("<")[0].strip()
    result["처리기한"] = str(data.get("limitDate", "") or "")[:10]
    actions = data.get("action", [])
    action_texts = []
    for act in actions:
        if not isinstance(act, dict):
            continue
        item = act.get("actionItem", "")
        done_desc = act.get("completedDescription", "")
        if item:
            action_texts.append(f"{item}" + (f" → {done_desc}" if done_desc else ""))
    result["조치내용"] = " | ".join(action_texts)
    return result


def parse_changeactionitem_json(json_data: dict) -> dict:
    """변경 Action Item 상세 파싱."""
    data = extract_data_from_response(json_data)
    if not data:
        return {}
    result = {}
    result["제목"] = data.get("title", "")
    result["수행자"] = str(data.get("performer", "") or "").split("<")[0].strip()
    result["작성자"] = str(data.get("regUsername", "") or "").split("<")[0].strip()
    result["처리기한"] = str(data.get("limitDate", "") or "")[:10]
    actions = data.get("action", [])
    action_texts = []
    for act in actions:
        if not isinstance(act, dict):
            continue
        item = act.get("actionItem", "")
        done_desc = act.get("completedDescription", "")
        if item:
            action_texts.append(f"{item}" + (f" → {done_desc}" if done_desc else ""))
    result["조치내용"] = " | ".join(action_texts)
    return result


def parse_changeimpact_json(json_data: dict) -> dict:
    """변경영향성평가 상세 파싱."""
    data = extract_data_from_response(json_data)
    if not data:
        return {}
    result = {}
    result["제목"] = data.get("title", "")
    result["평가자"] = str(data.get("evaluator", "") or "").split("<")[0].strip()
    result["평가부서"] = data.get("group", "")
    gmp_areas = {
        "a1": "제조소 및 시설", "a2": "문서관리", "a3": "품질관리",
        "a4": "원자재 관리", "a5": "제조관리", "a6": "포장/표시관리",
        "a7": "보관/출하", "a8": "밸리데이션", "a9": "불만처리",
        "a10": "자율점검", "a11": "교육훈련", "a12": "위탁제조",
    }
    affected = []
    for key, label in gmp_areas.items():
        if data.get(key, "") == "y":
            affected.append(label)
    result["영향 GMP 영역"] = ", ".join(affected) if affected else "해당 없음"
    result["영향 영역 수"] = len(affected)
    return result


def parse_changeoutsourcing_json(json_data: dict) -> dict:
    """외주 변경 상세 파싱."""
    data = extract_data_from_response(json_data)
    if not data:
        return {}
    result = {}
    result["제목"] = data.get("title", "")
    result["작성자"] = str(data.get("regUsername", "") or "").split("<")[0].strip()
    result["처리기한"] = str(data.get("limitDate", "") or "")[:10]
    result["변경 내용"] = data.get("changeContent", "")
    result["변경 사유"] = data.get("changeRequestReason", "")
    result["기존 내용"] = data.get("existContent", "")
    result["비고"] = data.get("remark", "")
    result["단순변경여부"] = "단순" if data.get("actionPlan", "") == "n" else "일반"
    # 위탁처 추출 (제목에서 [위탁처 : xxx] 패턴)
    title = data.get("title", "")
    import re as _re
    m = _re.search(r'\[위탁처\s*:\s*(.+?)\]', title)
    result["위탁처"] = m.group(1).strip() if m else ""
    return result


def parse_deviationoutsourcing_json(json_data: dict) -> dict:
    """일탈 외주 상세 파싱 (자사 일탈과 통합 대시보드용 공통 컬럼 보강)."""
    data = extract_data_from_response(json_data)
    if not data:
        return {}
    result = {}
    result["제목"] = data.get("title", "")
    result["작성자"] = str(
        data.get("regUsername", "") or data.get("open_by", "") or ""
    ).split("<")[0].strip()
    result["검토자"] = str(data.get("reviewer", "") or "").split("<")[0].strip()
    result["처리기한"] = str(data.get("limitDate", "") or "")[:10]
    result["비고"] = data.get("remark", "")

    occur = data.get("occurDate", "") or data.get("findDate", "")
    if occur:
        disp = normalize_find_date_to_display(occur)
        if disp:
            result["발견일시"] = disp

    devc = data.get("deviationContent", "")
    if devc:
        result["일탈_상세내용"] = str(devc).strip()

    cons = str(data.get("consignmentCompany", "") or "").strip()
    title = data.get("title", "")
    import re as _re
    m = _re.search(r"\[위탁처\s*:\s*(.+?)\]", str(title or ""))
    title_vendor = m.group(1).strip() if m else ""
    result["위탁처"] = cons or title_vendor

    # 팀별 차트용: 외주는 위탁처를 작성팀 대용으로 사용
    if result["위탁처"]:
        result["작성팀"] = f"외주·{result['위탁처']}"
    else:
        result["작성팀"] = "외주일탈"

    actions = data.get("actions", "")
    if actions and str(actions).strip():
        result["조치·평가 요약"] = str(actions).strip()[:2000]

    rec = data.get("recurrence1", "")
    if rec is not None and str(rec).strip() != "":
        rv = str(rec).strip().lower()
        if rv in ("y", "yes", "1", "true"):
            result["재발여부"] = "예"
        elif rv in ("n", "no", "0", "false"):
            result["재발여부"] = "아니오"
        else:
            result["재발여부"] = str(rec).strip()
    else:
        result["재발여부"] = ""

    result["보고부서"] = ""
    result["QC작성팀"] = ""

    out_tests = []
    for test in data.get("testInfo", []):
        if not isinstance(test, dict):
            continue
        lot_no = test.get("lotNo", "")
        lot_display = f"'{lot_no}" if lot_no and lot_no.isdigit() and len(lot_no) > 10 else lot_no
        out_tests.append({
            "시험종류": test.get("bizprocessNm", ""),
            "품목코드": extract_test_item_code(test),
            "품목명": test.get("itemNmReport", ""),
            "제조번호": lot_display,
            "시험항목": test.get("testitemNm", ""),
        })
    if not out_tests:
        out_tests = affected_items_to_test_rows(data.get("affectedItems"))
    else:
        aff_rows = affected_items_to_test_rows(data.get("affectedItems"))
        if aff_rows:
            codes = {r.get("품목코드") for r in out_tests if r.get("품목코드")}
            for r in aff_rows:
                c = r.get("품목코드")
                if c and c not in codes:
                    out_tests.append(r)
                    codes.add(c)
    result["시험정보목록"] = out_tests

    # ─── 일탈·인시던트 구분 + 경향분석 필드 (보고서 4.1~4.7 재현용) ───
    event_kind = classify_deviation_vs_incident(data)
    result["이벤트 구분"] = event_kind

    rating_raw = str(data.get("deviationRating1", "") or "").strip()
    result["일탈 등급"] = rating_raw if rating_raw else "-"
    result["일탈 등급 대분류"] = classify_deviation_grade(rating_raw, event_kind)

    result["제조관련 일탈"] = _manufacturing_label(data.get("manufacturingDeviation", ""))
    result["자사/외주"] = "외주"

    recv_date = _normalize_date_10(data.get("receivedDate", ""))
    result["이벤트 접수일자"] = recv_date
    result["접수월"] = recv_date[:7] if recv_date else ""

    result["발생 유형"] = str(data.get("cateCause", "") or "").strip() or "미분류"
    result["발생 세부유형"] = str(data.get("cateCauseDetail1", "") or "").strip()
    # 외주: 위탁처(consignmentCompany 또는 title 추출) 값을 '위탁업체'로도 노출
    result["위탁업체"] = result.get("위탁처", "") or str(data.get("from", "") or "").strip()

    return result


def parse_deviationactionitem_json(json_data: dict) -> dict:
    """일탈 외주 Action Item 상세 파싱."""
    data = extract_data_from_response(json_data)
    if not data:
        return {}
    result = {}
    result["제목"] = data.get("title", "")
    result["작성자"] = str(data.get("regUsername", "") or "").split("<")[0].strip()
    result["검토자"] = str(data.get("reviewer", "") or "").split("<")[0].strip()
    result["처리기한"] = str(data.get("limitDate", "") or "")[:10]
    actions = data.get("action", [])
    action_texts = []
    for act in actions:
        if not isinstance(act, dict):
            continue
        item = act.get("actionItem", "")
        done_desc = act.get("completedDescription", "")
        if item:
            action_texts.append(f"{item}" + (f" → {done_desc}" if done_desc else ""))
    result["조치내용"] = " | ".join(action_texts)
    # 위탁처 추출
    title = data.get("title", "")
    import re as _re
    m = _re.search(r'\[위탁처\s*:\s*(.+?)\]', title)
    result["위탁처"] = m.group(1).strip() if m else ""
    return result


def parse_businesstransfer_json(json_data: dict) -> dict:
    """업무 이전 상세 파싱."""
    data = extract_data_from_response(json_data)
    if not data:
        return {}
    result = {}
    result["제목"] = data.get("title", "")
    result["이전 대상자"] = str(data.get("manager", "") or "").split("<")[0].strip()
    result["수령자"] = str(data.get("recipient", "") or "").split("<")[0].strip()
    result["승인자"] = str(data.get("approver", "") or "").split("<")[0].strip()
    result["사유"] = data.get("reason", "")
    result["승인 의견"] = data.get("approvalOpinion", "")
    return result


def parse_validityevaluation_json(json_data: dict) -> dict:
    """유효성평가 상세 파싱."""
    data = extract_data_from_response(json_data)
    if not data:
        return {}
    result = {}
    result["제목"] = data.get("title", "")
    result["작성자"] = str(data.get("regUsername", "") or "").split("<")[0].strip()
    result["처리기한"] = str(data.get("limitDate", "") or "")[:10]
    return result


def parse_investigation_json(json_data: dict) -> dict:
    """조사(5M1E) 상세 파싱."""
    data = extract_data_from_response(json_data)
    if not data:
        return {}
    result = {}
    result["제목"] = data.get("title", "")
    result["조사자"] = str(data.get("investigator", "") or "").split("<")[0].strip()
    result["작성자"] = str(data.get("regUsername", "") or "").split("<")[0].strip()
    result["처리기한"] = str(data.get("limitDate", "") or "")[:10]
    result["조사 유형"] = data.get("investigation", "")
    # 5M1E 조사 항목
    yn_map = {"y": "수행", "n": "미수행"}
    m1e_fields = {
        "machineInvestigation": "설비(Machine)",
        "methodInvestigation": "방법(Method)",
        "workInvestigation": "작업자(Man)",
        "rawmaterialInvestigation": "원료(Material)",
        "measurementInvestigation": "측정(Measurement)",
        "environmentInvestigation": "환경(Environment)",
        "otherInvestigation": "기타(Other)",
    }
    investigated = []
    for key, label in m1e_fields.items():
        val = data.get(key, "n")
        result[f"5M1E_{label}"] = yn_map.get(val, val)
        if val == "y":
            investigated.append(label)
            comment_key = key + "Comment"
            result[f"5M1E_{label}_내용"] = data.get(comment_key, "")
    result["수행된 조사 항목"] = ", ".join(investigated) if investigated else "없음"
    result["기타 의견"] = data.get("etc", "")

    team_map = {"first": "품질관리1팀", "second": "품질관리2팀"}
    if data.get("teamName"):
        result["작성팀"] = team_map.get(data.get("teamName", ""), data.get("teamName", ""))
    rt = str(data.get("reportTeam", "") or "").strip()
    result["보고부서"] = rt
    if rt and "작성팀" not in result:
        result["작성팀"] = rt

    inv_tests = []
    for test in data.get("testInfo", []):
        if not isinstance(test, dict):
            continue
        lot_no = test.get("lotNo", "")
        lot_display = f"'{lot_no}" if lot_no and lot_no.isdigit() and len(lot_no) > 10 else lot_no
        inv_tests.append({
            "시험종류": test.get("bizprocessNm", ""),
            "품목코드": extract_test_item_code(test),
            "품목명": test.get("itemNmReport", ""),
            "제조번호": lot_display,
            "시험항목": test.get("testitemNm", ""),
        })
    if not inv_tests:
        inv_tests = affected_items_to_test_rows(data.get("affectedItems"))
    if inv_tests:
        result["시험정보목록"] = inv_tests
        first = inv_tests[0]
        if first.get("품목코드"):
            result["품목코드"] = first["품목코드"]
        if first.get("품목명"):
            result["품목명"] = first["품목명"]

    return result


# ============================================================================
# 공통 유틸리티
# ============================================================================

def apply_normalized_weights(df, group_col="QMS번호", weight_col="건수기여도"):
    if group_col not in df.columns:
        return df

    df = df.copy()
    for _, idx_group in df.groupby(group_col):
        n = len(idx_group)
        if n == 1:
            df.loc[idx_group.index, weight_col] = 1.0
        else:
            weights = [round(1 / n, 5)] * n
            weights[-1] = round(1 - sum(weights[:-1]), 5)
            df.loc[idx_group.index, weight_col] = weights
    return df


def save_results(all_data, total_processed, failed_logs, start_time, end_time, mode_name):
    try:
        logger.info(f"💾 데이터 저장 시작... (총 {len(all_data)}건)")
        if not all_data:
            logger.warning("⚠️ 저장할 데이터 없음")
            return

        df_new = pd.DataFrame(all_data)

        for col in ["발견일시", "근본원인 승인 대기 승인 On", "재분석 결과 승인 대기 On"]:
            if col in df_new.columns:
                df_new[col] = pd.to_datetime(df_new[col], errors="coerce").dt.strftime("%Y-%m-%d")

        if "QMS번호" in df_new.columns:
            df_new["QMS번호"] = pd.to_numeric(df_new["QMS번호"], errors="coerce")

        save_path = save_dir / f"QMS_수집결과_{timestamp_str}.xlsx"

        dedup_cols = ["QMS번호", "시험항목", "시험종류", "제조번호", "의뢰번호"]
        existing_dedup_cols = [col for col in dedup_cols if col in df_new.columns]
        if existing_dedup_cols:
            df_new.drop_duplicates(subset=existing_dedup_cols, keep="last", inplace=True)

        result_df = df_new
        logger.info(f"📊 API 단독 수집: {len(result_df)}건 (병합 없음)")

        result_df = apply_normalized_weights(result_df)
        result_df = result_df.loc[:, result_df.notna().any()]

        column_order = [
            "QMS번호", "진행상태", "제목", "발견일시", "작성팀", "보고부서", "QC작성팀",
            "시험종류", "품목코드", "품목명", "제조번호", "시험항목", "시험기준", "의뢰번호", "시험결과",
            "기기명", "최초 시험결과", "시험실 이벤트 유형", "이벤트 정보",
            "작성자", "시험조사자",
            "OOS등록자와 시험자 일치여부", "OOS등록자와 시험자 불일치 이유", "허용기준",
            "첨부파일 여부", "첨부파일", "처리기한",
            "Check list 검토 진행여부", "Check list 항목 (일반)", "Check list 항목 (미생물)",
            "Check list - 작성자 의견", "Check list - 시험조사자 의견",
            "시험실 조사 - 작성자 의견", "시험실 조사 - 시험조사자 의견",
            "재분석 - 시험조사자 의견(계획)", "Hypothesis - 시험조사자 의견(계획)",
            "기타 의견 목록", "기타조사내용",
            "기타시험 - 시험조사자 의견(계획)", "기타시험 - 작성자 의견(결과)", "기타시험 - 시험조사자 의견(확인)",
            "시험검토자1 의견",
            "전면 조사 담당자",
            "품질보증팀 담당자 의견", "품질보증팀장 의견",
            "추가시험 계획 검토", "품질보증팀 담당자 (추가시험)",
            "품질보증팀 담당자 의견 (추가시험)", "품질보증팀장 의견 (추가시험)",
            "시험조사자 의견 (추가시험)", "시험검토자2 의견 (추가시험)",
            "의약품품질부문장 의견 (추가시험)", "품질(보증)부서 책임자 의견 (추가시험)",
            "전면조사 - 시험조사자 의견", "전면조사 - 시험검토자2 의견",
            "전면조사 - 의약품품질부문장 의견", "전면조사 - 품질부서책임자 의견",
            "결론 - Repeat to Replace Test Plan 선택",
            "결론 - Repeat to Replace Test Plan - 작성자 의견",
            "결론 - Repeat to Replace Test Plan - 시험조사자 의견",
            "결론 - 최종 결론", "확인된 이벤트 분류", "재시험 필요여부",
            "기준 일탈 최종 결과", "타 제조번호 영향", "이상발생 원인", "이상발생 원인 이유",
            "조치사항 및 조치계획", "CAPA/Action item 필요여부",
            "결론탭 - 작성자 의견", "결론탭 - 시험조사자 의견",
            "결론탭 - 시험검토자1 의견", "결론탭 - 품질(보증)부서 책임자 의견",
            "근본원인 승인 대기 승인 On", "재분석 결과 승인 대기 On",
            "동시분석", "건수기여도"
        ]

        existing_columns = [col for col in column_order if col in result_df.columns]
        other_columns = [col for col in result_df.columns if col not in column_order]
        result_df = result_df[existing_columns + other_columns]

        logger.info(f"📁 저장 경로: {save_path}")
        logger.info(f"📊 저장할 데이터프레임 크기: {result_df.shape}")

        result_df_safe = result_df.copy()
        for col in result_df_safe.columns:
            if result_df_safe[col].dtype == "object":
                mask = result_df_safe[col].astype(str).str.match(r"^[=+\-@]", na=False)
                if mask.any():
                    result_df_safe.loc[mask, col] = "'" + result_df_safe.loc[mask, col].astype(str)
                result_df_safe[col] = result_df_safe[col].fillna("")

        logger.info("📝 엑셀 파일 작성 중...")
        with pd.ExcelWriter(save_path, engine="openpyxl") as writer:
            result_df_safe.to_excel(writer, index=False, sheet_name="QMS_데이터")
            pd.DataFrame({
                "수집 모드": [mode_name],
                "총 수집 시도 건수": [total_processed],
                "실패 건수": [len(failed_logs)],
                "수집 시작 시각": [start_time.strftime("%Y-%m-%d %H:%M:%S")],
                "수집 종료 시각": [end_time.strftime("%Y-%m-%d %H:%M:%S")],
                "소요 시간 (HH:MM:SS)": [str(end_time - start_time)]
            }).to_excel(writer, sheet_name="요약", index=False)
            if failed_logs:
                pd.DataFrame(failed_logs).to_excel(writer, sheet_name="실패로그", index=False)
            if log_records:
                pd.DataFrame(log_records, columns=["로그기록"]).to_excel(writer, sheet_name="로그기록", index=False)

        logger.info(f"✅ 엑셀 파일 작성 완료")

        if "QMS번호" in result_df.columns:
            try:
                qms_series = result_df["QMS번호"].dropna()
                try:
                    qms_series = qms_series.astype(pd.Int64Dtype())
                    qms_cache_values = sorted({str(int(qid)) for qid in qms_series.dropna()})
                except Exception:
                    qms_cache_values = sorted({str(qid).strip() for qid in qms_series.astype(str) if str(qid).strip()})
                with existing_qms_cache_path.open("w", encoding="utf-8") as f:
                    json.dump(qms_cache_values, f, ensure_ascii=False)
                logger.info(f"📝 QMS번호 캐시 업데이트 완료: {len(qms_cache_values)}건")
            except Exception as e:
                logger.warning(f"⚠️ QMS번호 캐시 저장 실패: {e}")

        logger.info(f"✅ 엑셀 저장 완료: {save_path}")
        print(f"\n✅ 엑셀 파일 저장 완료!")
        print(f"   저장 경로: {save_path}")

    except Exception as e:
        logger.exception(f"❌ 결과 저장 중 예외 발생: {e}")
        print(f"\n❌ 엑셀 파일 저장 실패: {e}")
        import traceback
        traceback.print_exc()


# ============================================================================
# API 클라이언트
# ============================================================================

class QMSAPIClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        # 호스트는 base_url(환경변수 QMS_API_BASE_URL)에서 유도한다(하드코딩 금지).
        self.host = urlparse(self.base_url).netloc
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.verify = False
        _adapter = HTTPAdapter(pool_connections=12, pool_maxsize=12, max_retries=0)
        self.session.mount("https://", _adapter)
        self.session.mount("http://", _adapter)
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Isajax": "true",
            "Clientname": CLIENT_NAME,
            "Clientsecret": CLIENT_SECRET,
            "Realmname": REALM_NAME,
            "Redirecturl": "/project/taskList",
            "Connection": "keep-alive"
        })
        self.authenticated = False
        self.jsessionid = None

    def login(self) -> bool:
        try:
            self.session.get(f"{self.base_url}/", timeout=10)
        except Exception:
            pass

        login_url = f"{self.base_url}{API_LOGIN_ENDPOINT}"
        login_data = {"userId": self.username, "password": self.password}
        headers = {"Content-Type": "application/json", "Accept": "application/json", "Referer": f"{self.base_url}/"}

        try:
            response = self.session.post(login_url, json=login_data, headers=headers, timeout=30)
            logger.debug(f"🔑 로그인 응답: {response.status_code}")

            jsessionid = response.cookies.get("JSESSIONID") or self.session.cookies.get("JSESSIONID")
            if not jsessionid:
                m = re.search(r"JSESSIONID=([^;]+)", response.headers.get("Set-Cookie", ""))
                if m:
                    jsessionid = m.group(1)

            if jsessionid:
                self.jsessionid = jsessionid
                for c in [c for c in list(self.session.cookies) if c.name == "JSESSIONID"]:
                    self.session.cookies.clear(c.domain, c.path, c.name)
                cookie_kwargs = {"path": "/"}
                if self.host:
                    cookie_kwargs["domain"] = self.host
                self.session.cookies.set("JSESSIONID", jsessionid, **cookie_kwargs)

            test_params = {
                "page": 1, "pageSize": 1, "project": "oos", "plant": "kd",
                "statusType": "all", "searchType": "title", "searchVal": "",
                "dateType": "regDate", "startDate": "2000-01-01",
                "endDate": "2099-12-31",
                "userId": "", "sortCol": "undefined", "sortColAsc": "undefined"
            }
            test_resp = self.session.get(f"{self.base_url}{API_LIST_ENDPOINT}", params=test_params, timeout=10)
            if test_resp.status_code == 200:
                try:
                    result = test_resp.json()
                    if isinstance(result, dict) and ("list" in result or "totalCount" in result):
                        self.authenticated = True
                        logger.info(f"✅ 로그인 성공 (총 {result.get('totalCount', 0)}건 확인)")
                        return True
                    logger.error(
                        "❌ 로그인 후 API 테스트: JSON 구조 이상 — type=%s, keys=%s",
                        type(result).__name__,
                        list(result.keys())[:10] if isinstance(result, dict) else "—",
                    )
                except Exception as e:
                    ct = test_resp.headers.get("Content-Type", "")
                    body = (test_resp.text or "")[:200].replace("\n", " ")
                    logger.error(
                        "❌ 로그인 후 API 테스트 JSON 파싱 실패 — CT=%r, body[:200]=%r, err=%s",
                        ct, body, e,
                    )
                    return False

            logger.error(
                "❌ 로그인 후 API 테스트 실패: HTTP %s, CT=%r, body[:200]=%r",
                test_resp.status_code,
                test_resp.headers.get("Content-Type", ""),
                (test_resp.text or "")[:200].replace("\n", " "),
            )
            return False

        except Exception as e:
            logger.error(f"❌ 로그인 실패: {e}")
            return False

    def get_workflow_list(self, project="oos", page=1, page_size=50) -> dict:
        try:
            params = {
                "page": page, "pageSize": page_size,
                "dateType": "regDate", "startDate": "2000-01-01",
                "endDate": "2099-12-31",
                "plant": "kd", "project": project,
                "statusType": "all", "searchType": "title", "searchVal": "",
                "userId": "", "sortCol": "undefined", "sortColAsc": "undefined"
            }
            headers = {
                "Referer": f"{self.base_url}/project/taskList",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            }
            if self.host:
                headers["Host"] = self.host
            response = self.session.get(f"{self.base_url}{API_LIST_ENDPOINT}", params=params, headers=headers, timeout=30)

            if response.status_code != 200:
                logger.error(f"❌ 목록 조회 실패: {response.status_code}")
                return {}
            try:
                result = response.json()
            except (ValueError, json.JSONDecodeError):
                ct = response.headers.get("Content-Type", "")
                body_head = (response.text or "")[:200].replace("\n", " ")
                logger.error(
                    "❌ 목록 조회 응답 JSON 파싱 실패 — CT=%r, len=%d, body[:200]=%r",
                    ct, len(response.text or ""), body_head,
                )
                return {}
            if isinstance(result, dict) and ("list" in result or "totalCount" in result):
                logger.info(f"✅ 목록 조회 성공 - totalCount: {result.get('totalCount', 0)}")
                return result
            logger.error(
                "❌ 목록 조회: 응답에 list/totalCount 없음 — type=%s, keys=%s",
                type(result).__name__,
                list(result.keys())[:10] if isinstance(result, dict) else "—",
            )
            return {}
        except Exception as e:
            logger.error(f"❌ 목록 조회 중 예외 발생: {e}")
            return {}

    def get_workflow_detail(self, task_id: str) -> dict:
        """워크플로우 상세 정보. taskId 후보(예: -COMPLETED 제거)를 순차 시도."""
        if not task_id:
            logger.error("❌ taskId가 필요합니다")
            return {}

        detail_url = f"{self.base_url}{API_DETAIL_ENDPOINT}"
        headers = {"Referer": f"{self.base_url}/project/taskList"}
        last_status = None
        last_len = 0

        last_ct = ""
        last_head = ""
        for tid in task_id_detail_candidates(str(task_id).strip()):
            try:
                params = {"taskId": tid}
                response = self.session.get(detail_url, params=params, headers=headers, timeout=30)
                last_status = response.status_code
                last_len = len(response.text or "")
                last_ct = response.headers.get("Content-Type", "")
                last_head = (response.text or "")[:200].replace("\n", " ")
                if response.status_code == 200 and last_len > 10:
                    try:
                        result = response.json()
                        if isinstance(result, dict):
                            return result
                        logger.warning(
                            "⚠️ 상세 조회: JSON 이 dict 아님 — type=%s, taskId=%r",
                            type(result).__name__, tid,
                        )
                    except json.JSONDecodeError as je:
                        logger.warning(
                            "⚠️ 상세 조회 JSON 파싱 실패 — taskId=%r, CT=%r, body[:200]=%r, err=%s",
                            tid, last_ct, last_head, je,
                        )
            except Exception as e:
                logger.debug(f"상세 조회 시도 실패 taskId={tid!r}: {e}")

        logger.error(
            "❌ 상세 조회 실패(모든 후보): 마지막 HTTP %s, 길이 %d, CT=%r, body[:200]=%r",
            last_status, last_len, last_ct, last_head,
        )
        return {}


# ============================================================================
# 메인 수집 로직
# ============================================================================

def run_api_mode():
    log_and_record("info", "==== QMS 자동 수집 로그 시작 (API 모드) ====")

    print("=" * 60)
    print("🚀 QMS 자동 수집 프로그램 시작 (API 모드)")
    print("=" * 60)

    start_time = datetime.now()
    print(f"\n⏰ 수집 시작 시간: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    print("\n🔗 QMS API 연결 중...")
    api_client = QMSAPIClient(API_BASE_URL, LOGIN_USERNAME, LOGIN_PASSWORD)

    if not api_client.login():
        print("❌ API 로그인 실패! 프로그램을 종료합니다.")
        return

    print("\n" + "=" * 60)
    print("🚀 데이터 수집 시작!")
    print("=" * 60)

    all_data = []
    total_processed = 0
    failed_logs = []
    processed_in_current_run = set()
    current_page = 1

    try:
        while True:
            try:
                logger.info(f"📄 {current_page}페이지 목록 조회 중...")
                list_response = api_client.get_workflow_list(project="oos", page=current_page, page_size=50)

                if not list_response or "list" not in list_response:
                    logger.warning("⚠️ 목록 응답이 비어있습니다. 마지막 페이지일 수 있습니다.")
                    break

                items = list_response.get("list", [])
                if not items:
                    logger.warning("⚠️ 현재 페이지에 데이터가 없습니다. 종료합니다.")
                    break

                logger.info(f"✅ {len(items)}개 항목 조회 완료")

                for idx, item in enumerate(items, 1):
                    qms_id = str(item.get("prno", "")).strip()
                    if not qms_id:
                        continue

                    if qms_id in processed_in_current_run:
                        continue

                    task_id = resolve_list_task_id(item)
                    if not task_id:
                        failed_logs.append({"QMS번호": qms_id, "실패사유": "taskId 없음", "에러": ""})
                        processed_in_current_run.add(qms_id)
                        continue

                    try:
                        contents = {
                            "QMS번호": qms_id,
                            "진행상태": str(item.get("status", "")).strip()
                        }

                        logger.info(f"🔍 {total_processed + 1}번째 상세 정보 조회 중: {qms_id}")
                        detail_response = api_client.get_workflow_detail(task_id=task_id)

                        if not detail_response:
                            failed_logs.append({"QMS번호": qms_id, "실패사유": "상세 정보 응답 없음", "에러": ""})
                            processed_in_current_run.add(qms_id)
                            continue

                        contents.update(parse_qms_detail_json(detail_response))
                        contents.update(parse_lab_investigation_json(detail_response))
                        contents.update(parse_full_investigation_json(detail_response))
                        contents.update(parse_conclusion_json(detail_response))
                        contents.update(parse_activity_log_json(detail_response))

                        test_infos = contents.pop("시험정보목록", [])
                        num_tests = len(test_infos)
                        test_fields = ["시험종류", "품목명", "제조번호", "시험항목", "시험기준", "시험결과"]

                        if num_tests <= 1:
                            if test_infos:
                                contents.update(test_infos[0])
                            contents["동시분석"] = "No"
                            contents["건수기여도"] = 1.0
                            all_data.append(contents)
                        else:
                            for test in test_infos:
                                row_data = {k: v for k, v in contents.items() if k not in test_fields}
                                row_data.update(test)
                                row_data["동시분석"] = "Yes"
                                row_data["건수기여도"] = round(1 / num_tests, 5)
                                all_data.append(row_data)

                        processed_in_current_run.add(qms_id)
                        total_processed += 1
                        print(f"✅ {total_processed}건 추출 완료: QMS {qms_id}")

                    except Exception as e:
                        logger.warning(f"❌ 상세 정보 처리 실패 ({qms_id}): {e}")
                        failed_logs.append({"QMS번호": qms_id, "실패사유": "상세 정보 처리 실패", "에러": str(e)})
                        processed_in_current_run.add(qms_id)
                        continue

                total_count = list_response.get("totalCount", 0)
                if current_page * 50 >= total_count:
                    logger.info("⛔ 마지막 페이지에 도달했습니다.")
                    break

                current_page += 1
                time.sleep(0.5)

            except Exception as e:
                logger.warning(f"❌ 페이지 {current_page} 처리 중 예외 발생: {e}")
                failed_logs.append({"QMS번호": "", "실패사유": f"페이지 {current_page} 예외 발생", "에러": str(e)})
                current_page += 1
                time.sleep(2)
                continue

    finally:
        end_time = datetime.now()
        try:
            logger.info(f"\n💾 수집 완료! 총 {total_processed}건 처리됨")
            logger.info(f"💾 데이터 저장 시작... (all_data 길이: {len(all_data)})")
            save_results(all_data, total_processed, failed_logs, start_time, end_time, "API 모드")
        except Exception as e:
            logger.exception(f"❌ 저장 함수 호출 중 예외 발생: {e}")
            print(f"\n❌ 저장 중 오류 발생: {e}")
            import traceback
            traceback.print_exc()
        finally:
            log_and_record("info", "==== QMS 자동 수집 로그 종료 (API 모드) ====")


# ============================================================================
# 공유 세션 클라이언트 (대시보드/스냅샷/감사 공용)
# ============================================================================

import threading as _threading

_SHARED_CLIENT: "QMSAPIClient | None" = None
_SHARED_LOGIN_AT: float = 0.0
_SHARED_LOCK = _threading.Lock()

# QMS 세션 만료(~30분)보다 5분 마진을 두고 선제 재로그인.
SESSION_TTL_SEC = 1500


def get_shared_client(force_new: bool = False) -> "QMSAPIClient":
    """프로세스 전체가 공유하는 로그인된 QMSAPIClient 반환.

    - 최초 호출 또는 세션 TTL 경과·로그아웃 감지 시 1회만 로그인.
    - 스레드 세이프 (Streamlit 멀티 스레드 안전).
    - 로그인 실패 시 RuntimeError 발생 — 호출부가 (df_empty, err_str)로 변환.
    """
    global _SHARED_CLIENT, _SHARED_LOGIN_AT
    with _SHARED_LOCK:
        needs_fresh = (
            force_new
            or _SHARED_CLIENT is None
            or not getattr(_SHARED_CLIENT, "authenticated", False)
            or (time.time() - _SHARED_LOGIN_AT) > SESSION_TTL_SEC
        )
        if needs_fresh:
            new_client = QMSAPIClient(API_BASE_URL, LOGIN_USERNAME, LOGIN_PASSWORD)
            if not new_client.login():
                raise RuntimeError("QMS 로그인 실패")
            _SHARED_CLIENT = new_client
            _SHARED_LOGIN_AT = time.time()
            logger.info(
                "🔐 공유 QMSAPIClient 로그인 완료 (force_new=%s, ttl=%ds)",
                force_new, SESSION_TTL_SEC,
            )
        return _SHARED_CLIENT


def reset_shared_client() -> None:
    """테스트·배치 종료 시 공유 클라이언트를 해제."""
    global _SHARED_CLIENT, _SHARED_LOGIN_AT
    with _SHARED_LOCK:
        _SHARED_CLIENT = None
        _SHARED_LOGIN_AT = 0.0


# ============================================================================
# 엔트리포인트
# ============================================================================

if __name__ == "__main__":
    print("QMS OOS 자동 수집 프로그램 (API 모드)")
    print(f"저장 경로: {save_dir}")
    print()
    run_api_mode()
