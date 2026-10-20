"""실모델 백엔드(openai/ollama)가 공유하는 프롬프트·출력 정규화 로직.

baseline에서는 ollama_llm.py와 openai_llm.py에 같은 코드가 복붙돼 있었다. 모델 호출
방식(HTTP 형태·스트리밍 포맷)만 백엔드별로 다르고, "무엇을 묻고 출력을 어떻게 믿지
않고 보정하는가"는 여기 한 곳에서 관리한다.
"""
import json
import re

from .systems import DEFAULT_SYSTEM, KNOWN_SYSTEMS, mentioned_systems, primary_system

ROUTE_SYSTEM = (
    "당신은 ICT 운영 지원 라우터다. 사용자 요청을 분류하라. "
    "장애·성능 저하·지연·진단·모니터링·티켓 등 운영 이슈면 route=incident, "
    "인사·잡담·일반 질문이면 route=chat. "
    "needs_analysis는 패턴 비교/통계 분석이 꼭 필요할 때만 true. "
    'JSON 객체로만 응답하라. 스키마: {"route": "chat" 또는 "incident", '
    '"needs_analysis": true 또는 false}'
)

PLAN_SYSTEM = (
    "당신은 ICT 운영 Supervisor의 planning 노드다. 표준 진단 절차에 따라 "
    "실행 계획(tasks)을 JSON으로 세워라. "
    "사용자 요청에 등장한 시스템마다 agent=research task를 하나씩 세워라 "
    f"(알려진 시스템: {', '.join(sorted(KNOWN_SYSTEMS))} — 요청에 등장하지 않은 시스템은 "
    f"조사하지 말고, 특정 시스템이 언급되지 않았으면 {DEFAULT_SYSTEM}를 기본으로 조사하라). "
    "패턴 분석이 꼭 필요하다고 판단되면 agent=analysis task를 추가해도 된다. "
    'JSON 객체로만 응답하라. 스키마: {"needs_analysis": boolean, '
    '"tasks": [{"agent": "research"|"analysis", "target": string(선택)}]}'
)

ANALYZE_SYSTEM = (
    "당신은 ICT 운영 패턴 분석가다. 여러 시스템의 모니터링 결과를 비교해 "
    "공통 원인이 의심되는 패턴이 있는지 한두 문장으로 짧게 답하라. "
    "패턴이 없으면 '특이 패턴 없음'이라고만 답하라."
)

CHAT_SYSTEM = (
    "당신은 간결한 한국어 비서다. 한두 문장으로 답하라. "
    "대화 이력에서 사용자가 밝힌 이름이 있으면 정확히 기억해 답하라."
)

WRITE_SYSTEM = (
    "당신은 ICT 운영 보고 작성자다. 수집된 사실만 사용해 한국어로 "
    "간결한 대응 보고를 작성하라. 반드시 '1) ... 2) ... 3) ...' 형식의 "
    "번호 절차로 답하고, 수치는 수집된 값을 그대로 인용하라. "
    "수집된 사실에 없는 수치는 절대 만들어내지 마라."
)

_FALLBACK_INCIDENT = re.compile(r"장애|진단|다운|지연|급증|느려|타임아웃|incident|티켓")


def parse_route(content: str, query: str) -> dict:
    """JSON 모드는 '유효한 JSON'만 보장 — 필드 누락·오탐 시 코드 레벨 fallback (W6)."""
    try:
        raw = json.loads(content)
        route = raw.get("route") if raw.get("route") in ("chat", "incident") else None
        return {"route": route or "chat",
                "needs_analysis": bool(raw.get("needs_analysis", False))}
    except (json.JSONDecodeError, AttributeError):
        return {"route": "incident" if _FALLBACK_INCIDENT.search(query) else "chat",
                "needs_analysis": False}


def normalize_plan(content: str, query: str) -> dict:
    """모델이 세운 계획을 실행 가능한 형태로 보정한다.

    - 알 수 없는 target → 쿼리의 주 시스템으로 교정
    - 쿼리에 시스템이 언급됐는데 언급되지 않은 시스템을 조사하려 하면 제거 (과잉 조사 방지:
      baseline에서 gateway-service 하나를 물었는데 6개 시스템을 전부 조회한 사례)
    - research task가 0개면 언급된 시스템 전부(없으면 기본 시스템)로 복구
    """
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        raw = {"needs_analysis": False, "tasks": []}
    mentioned = mentioned_systems(query)
    tasks, seen, dropped = [], [], []
    analysis_added = False
    for t in raw.get("tasks", []) or []:
        if not isinstance(t, dict):
            continue
        if t.get("agent") == "research":
            target = t.get("target", "")
            target = target if target in KNOWN_SYSTEMS else primary_system(query)
            if mentioned and target not in mentioned:
                dropped.append(target)
                continue
            if target not in seen:
                seen.append(target)
        elif t.get("agent") == "analysis":
            analysis_added = True
    if not seen:
        seen = mentioned or [DEFAULT_SYSTEM]
    tasks = [{"id": f"task-{i}", "agent": "research", "kind": "monitoring",
              "target": target, "status": "pending"} for i, target in enumerate(seen)]
    if analysis_added:
        tasks.append({"id": "task-analysis", "agent": "analysis",
                      "kind": "pattern-analysis", "status": "pending"})
    return {"needs_analysis": bool(raw.get("needs_analysis", False)), "tasks": tasks,
            "dropped_targets": sorted(set(dropped))}


def plan_user_prompt(query: str, procedure_prompt: str) -> str:
    return f"## 표준 절차\n{procedure_prompt}\n\n## 사용자 요청\n{query}"


def analyze_user_prompt(collected: list[dict]) -> str:
    mon = [c["data"] for c in collected if c["source"] == "monitoring"]
    return "\n".join(
        f"- {d['system_name']}: 상태={d['status']}, CPU {d['cpu_percent']}%, "
        f"알림 {d['alerts']}" for d in mon) or "(모니터링 데이터 없음)"


def chat_user_prompt(query: str, history: list[dict]) -> str:
    rendered = "\n".join(f"{m['role']}: {m['content']}" for m in history[-10:])
    return f"## 지금까지의 대화\n{rendered or '(없음)'}\n\n## 사용자의 새 메시지\n{query}"


def render_facts(collected: list[dict]) -> str:
    facts = []
    for c in collected:
        if c["source"] == "monitoring":
            d = c["data"]
            facts.append(f"- 모니터링 {d['system_name']}: 상태={d['status']}, "
                         f"CPU {d['cpu_percent']}%, 메모리 {d['memory_percent']}%, "
                         f"알림 {len(d['alerts'])}건 {d['alerts']}")
        elif c["source"] == "runbook":
            facts.append(f"- runbook 발췌:\n{c['data'].strip()}")
        elif c["source"] == "analysis":
            facts.append(f"- 패턴 분석: {c['data'].strip()}")
    return "\n".join(facts)


def write_user_prompt(query: str, collected: list[dict], feedback: str | None) -> str:
    user = f"## 사용자 요청\n{query}\n\n## 수집된 사실\n{render_facts(collected)}"
    if feedback:
        user += f"\n\n## 이전 초안에 대한 검증 반려 사유 (반드시 반영)\n{feedback}"
    return user


def with_prompt(usage: dict, system: str, user: str) -> dict:
    usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
    return usage
