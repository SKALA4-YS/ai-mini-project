"""write / validate / finalize 노드 — Wave 8 작성·검증 루프."""
from langgraph.config import get_stream_writer
from langsmith import traceable

from ..deps import SupervisorDeps
from ..grounding import ungrounded_numbers
from ..state import AgentState, add_cost

MIN_LENGTH = 30
MAX_ATTEMPTS = 3   # 실모델 무한 재작성 방지 — 최대 3회 후 통과 처리


@traceable(name="write_node")
async def write_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """고품질 모델로 초안 작성. 토큰마다 attempt 번호를 실어 보내 재작성 시 프론트가
    새 말풍선으로 끊어 보여줄 수 있게 한다."""
    attempt = state.get("validation_attempts", 0) + 1
    writer = get_stream_writer()
    draft, usage = await deps.quality_llm.write(
        state["query"], state.get("collected", []),
        attempt, state.get("validation_feedback"),
        on_token=lambda piece: writer(
            {"type": "AnswerToken", "attempt": attempt, "delta": piece}))
    return {"draft": draft, "last_prompt": usage.get("prompt"),
            "cost_usd": add_cost(state, usage),
            "events": state["events"] + [{"type": "DraftWritten", "attempt": attempt,
                                          "length": len(draft)}]}


def check_draft(draft: str, query: str, collected: list[dict]) -> list[str]:
    """반려 사유 목록 (빈 리스트면 합격). 형식 검사 + 수치 근거 검사."""
    problems = []
    if len(draft) < MIN_LENGTH:
        problems.append(f"응답이 너무 짧습니다({len(draft)}자, 최소 {MIN_LENGTH}자).")
    if "1)" not in draft:
        problems.append("절차를 1) 2) 3) 번호로 구체적으로 설명하세요.")
    fabricated = ungrounded_numbers(draft, query, collected)
    if fabricated:
        problems.append(f"수집된 사실에 없는 수치가 있습니다: {', '.join(fabricated)}. "
                        "수집된 값만 그대로 인용하고, 없는 수치는 빼세요.")
    return problems


@traceable(name="validate_node")
async def validate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    attempts = state.get("validation_attempts", 0) + 1
    problems = check_draft(state["draft"], state["query"], state.get("collected", []))
    ok = not problems
    verdict = "합격" if ok else "불합격"
    if not ok and attempts >= MAX_ATTEMPTS:
        ok, verdict = True, "합격(최대 시도 도달 — 통과 처리)"
    event = {"type": "Validation", "attempt": attempts, "verdict": verdict}
    if problems:
        event["problems"] = problems
    return {"validation_attempts": attempts,
            "validation_feedback": None if ok else " ".join(problems),
            "events": state["events"] + [event]}


@traceable(name="finalize_node")
async def finalize_node(state: AgentState, deps: SupervisorDeps) -> dict:
    return {"final": state["draft"],
            "events": state["events"] + [{"type": "Finalize", "pipeline": "incident-v2"}]}
