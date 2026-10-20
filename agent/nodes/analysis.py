"""analysis 노드 — needs_analysis=True가 정당할 때만 수집 결과를 비교 분석한다 (Wave 6 보완)."""
from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import AgentState, add_cost


@traceable(name="analysis_node")
async def analysis_node(state: AgentState, deps: SupervisorDeps) -> dict:
    tasks = state["tasks"]
    pending = [t for t in tasks if t["agent"] == "analysis" and t["status"] == "pending"]
    if not pending:
        return {"events": state["events"] +
                [{"type": "AnalysisSkipped", "reason": "analysis task 0개"}]}
    summary, usage = await deps.fast_llm.analyze(state.get("collected", []))
    new_tasks = [dict(t) for t in tasks]
    for t in new_tasks:
        if t["agent"] == "analysis" and t["status"] == "pending":
            t["status"] = "done"
    collected = state.get("collected", []) + [{"source": "analysis", "data": summary}]
    event = {"type": "AnalysisCompleted", "length": len(summary)}
    return {"tasks": new_tasks, "collected": collected,
            "cost_usd": add_cost(state, usage), "events": state["events"] + [event]}
