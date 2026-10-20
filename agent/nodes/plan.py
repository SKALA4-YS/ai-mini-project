"""plan / gate 노드 — Wave 4 Task 분해 + Wave 6 권위 게이트."""
from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import AgentState, add_cost
from ..systems import primary_system


@traceable(name="plan_node")
async def plan_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 4 — MCP Prompt가 깔아준 표준 절차를 따라 계획을 세운다.

    prompt를 어느 서버가 들고 있는지 가정하지 않고 find_prompt()로 먼저 찾는다."""
    prompt_server = await deps.mcp.find_prompt("incident_analysis_prompt")
    procedure = await deps.mcp.get_prompt(
        "incident_analysis_prompt",
        {"system_name": primary_system(state["query"]), "symptom": state["query"]},
        server=prompt_server,
    )
    result, usage = await deps.fast_llm.plan(state["query"], procedure)
    targets = [t["target"] for t in result["tasks"]
               if t["agent"] == "research" and t.get("target")]
    event = {"type": "PlanCreated", "task_count": len(result["tasks"]), "targets": targets}
    if result.get("dropped_targets"):
        event["dropped_targets"] = result["dropped_targets"]
    return {"tasks": result["tasks"], "needs_analysis": result["needs_analysis"],
            "cost_usd": add_cost(state, usage), "events": state["events"] + [event]}


@traceable(name="gate_node")
async def gate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 6 — needs_analysis=False인데 섞여 들어온 analysis task를 코드로 강제 제거."""
    tasks = state["tasks"]
    if not state.get("needs_analysis", False):
        kept = [t for t in tasks if t["agent"] != "analysis"]
        removed = len(tasks) - len(kept)
    else:
        kept, removed = tasks, 0
    event = {"type": "GateApplied", "removed_analysis_tasks": removed}
    return {"tasks": kept, "gate_removed": removed, "events": state["events"] + [event]}
