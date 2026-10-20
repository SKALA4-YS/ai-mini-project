"""baseline의 키워드 규칙 분류기 — RAG가 실패(임베딩 API 오류·낮은 유사도)했을 때의 폴백.

모니터링 알림 문구만 보고 첫 번째로 걸리는 규칙을 고른다. 사용자가 말한 증상은
보지 않고, 여러 시스템의 알림이 섞이면 규칙 순서가 결과를 좌우한다는 한계가 있다
(eval/results/before-openai.json의 multi/symptom 정확도 0%).
"""

INCIDENT_TYPE_RULES = [
    ("latency", "high-latency"),
    ("pool", "high-latency"),
    ("cpu", "cpu-spike"),
    ("memory", "memory-leak"),
    ("heap", "memory-leak"),
    ("timeout", "network-timeout"),
    ("network", "network-timeout"),
    ("disk", "disk-full"),
    ("down", "db-down"),
]
DEFAULT_INCIDENT_TYPE = "db-down"


def classify_by_keyword(collected: list[dict]) -> str:
    text = " ".join(
        f"{c['data'].get('status', '')} " + " ".join(c["data"].get("alerts", []))
        for c in collected if c.get("source") == "monitoring"
    ).lower()
    for keyword, incident_type in INCIDENT_TYPE_RULES:
        if keyword in text:
            return incident_type
    return DEFAULT_INCIDENT_TYPE
