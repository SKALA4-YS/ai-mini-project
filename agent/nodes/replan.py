"""replan_check 노드 — Wave 7 재계획 + RAG 기반 incident_type 결정.

1차 조사 결과에 runbook이 없으면, 어떤 runbook이 필요한지 정해 gap task를 추가한다.
baseline은 모니터링 알림 문구의 키워드 규칙으로 정했고, 지금은 deps.retriever(RAG)가
사용자 증상 + 알림을 의미 기반으로 비교해 정한다. retriever가 주입되지 않았거나
(RAG_ENABLED=0) 실패하면 키워드 규칙으로 폴백한다.
"""
from langsmith import traceable

from ..deps import SupervisorDeps
from ..rag.keyword import classify_by_keyword
from ..state import AgentState

MAX_REPLAN_ROUNDS = 1


async def decide_incident_type(state: AgentState, deps: SupervisorDeps) -> dict:
    collected = state.get("collected", [])
    if deps.retriever is None:
        return {"incident_type": classify_by_keyword(collected), "method": "keyword",
                "score": None, "candidates": []}
    return await deps.retriever.classify(state["query"], collected)


@traceable(name="replan_check_node")
async def replan_check_node(state: AgentState, deps: SupervisorDeps) -> dict:
    has_runbook = any(c["source"] == "runbook" for c in state.get("collected", []))
    rounds = state.get("replan_rounds", 0)
    if has_runbook or rounds >= MAX_REPLAN_ROUNDS:
        verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": True}
        return {"events": state["events"] + [verdict]}

    decision = await decide_incident_type(state, deps)
    incident_type = decision["incident_type"]
    gap_task = {"id": "task-gap-runbook", "agent": "research", "kind": "runbook",
                "target": incident_type, "status": "pending"}
    verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": False,
               "gap_task": gap_task["id"], "target": incident_type,
               "method": decision["method"], "score": decision.get("score"),
               "candidates": decision.get("candidates", [])}
    return {"tasks": state["tasks"] + [gap_task], "replan_rounds": rounds + 1,
            "incident_type": incident_type, "events": state["events"] + [verdict]}
