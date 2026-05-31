# -*- coding: utf-8 -*-
"""대시보드·스냅샷 수집에서 공통으로 쓰는 프로젝트 메타 (단일 출처)."""

PROJECT_META = {
    "oos":                    {"label": "OOS",           "color": "#e53935", "group": "품질이상", "detail": True},
    "deviation":              {"label": "일탈",          "color": "#fb8c00", "group": "품질이상", "detail": True},
    "investigation":          {"label": "조사",          "color": "#795548", "group": "품질이상", "detail": True},
    "capa":                   {"label": "CAPA",          "color": "#8e24aa", "group": "CAPA",     "detail": True},
    "capaactionitem":         {"label": "CAPA AI",       "color": "#ab47bc", "group": "CAPA",     "detail": True},
    "actionitem":             {"label": "모니터링AI",    "color": "#5c6bc0", "group": "CAPA",     "detail": False},
    "changemanagement":       {"label": "변경",          "color": "#00897b", "group": "변경",     "detail": True},
    "changeactionitem":       {"label": "변경AI",        "color": "#26a69a", "group": "변경",     "detail": True},
    "changeimpactassessment": {"label": "변경영향성",    "color": "#4db6ac", "group": "변경",     "detail": True},
    "changeoutsourcing":      {"label": "외주변경",      "color": "#009688", "group": "변경",     "detail": True},
    "complain":               {"label": "고객불만",      "color": "#c0392b", "group": "불만",     "detail": True},
    "deviationoutsourcing":   {"label": "일탈외주",      "color": "#ef6c00", "group": "품질이상", "detail": False},
    "deviationactionitem":    {"label": "일탈외주AI",    "color": "#f57c00", "group": "일탈외주", "detail": True},
    "extension":              {"label": "기한연장",      "color": "#607d8b", "group": "기타",     "detail": False},
    "businesstransfer":       {"label": "업무이전",      "color": "#78909c", "group": "기타",     "detail": True},
    "validityevaluation":     {"label": "유효성평가",    "color": "#90a4ae", "group": "기타",     "detail": True},
}
