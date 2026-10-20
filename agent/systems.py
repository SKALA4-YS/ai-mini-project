"""모니터링 대상 시스템 목록 — graph/ollama_llm/openai_llm에 복붙돼 있던 로직을 한 곳으로.

data/monitoring.json의 키가 유일한 출처(single source of truth)다. 시스템을 추가할 때
JSON만 고치면 plan의 target 보정·system_name 추출이 함께 따라온다.
"""
import json
from pathlib import Path

MONITORING_PATH = Path(__file__).resolve().parent.parent / "data" / "monitoring.json"
DEFAULT_SYSTEM = "auth-service"   # 쿼리에 시스템명이 없을 때의 폴백 (가장 흔한 사례)


def load_known_systems(path: Path = MONITORING_PATH) -> set[str]:
    try:
        return set(json.loads(path.read_text()).keys())
    except (OSError, json.JSONDecodeError):
        return {DEFAULT_SYSTEM, "web-01"}


KNOWN_SYSTEMS = load_known_systems()


def mentioned_systems(query: str, known: set[str] = KNOWN_SYSTEMS) -> list[str]:
    """쿼리에 등장한 시스템을 등장 순서대로 반환한다."""
    return [s for _, s in sorted((query.index(s), s) for s in known if s in query)]


def primary_system(query: str, known: set[str] = KNOWN_SYSTEMS) -> str:
    found = mentioned_systems(query, known)
    return found[0] if found else DEFAULT_SYSTEM
