"""v2 Supervisor — LangGraph StateGraph 배선(wiring)만 담당한다.

route → (chat 직행 | incident 파이프라인)
incident: plan(MCP Prompt 절차 반영) → gate(권위 게이트) → research(가드+병렬+재시도)
          → replan_check(부족 시 RAG로 runbook 유형 결정 후 재조사) → analysis
          (needs_analysis=True일 때만 실제 실행) → write → validate(형식+수치 근거,
          반려 시 재작성) → finalize

노드 구현은 agent/nodes/*.py에 있고, 모두 SupervisorDeps를 functools.partial로 주입받는
순수 함수다(Wave 13). 이 파일은 "어떤 노드가 어떤 순서로 이어지는가"만 기술한다.
"""
from functools import partial

from langgraph.graph import END, StateGraph

from .deps import SupervisorDeps
from .nodes.analysis import analysis_node
from .nodes.plan import gate_node, plan_node
from .nodes.replan import replan_check_node
from .nodes.research import research_node
from .nodes.route import chat_answer_node, route_node
from .nodes.write import finalize_node, validate_node, write_node
from .state import AgentState


def _after_route(state: AgentState) -> str:
    return "chat_answer" if state["route"] == "chat" else "plan"


def _after_replan_check(state: AgentState) -> str:
    last = state["events"][-1]
    return "analysis" if last.get("is_sufficient") else "research"


def _after_validate(state: AgentState) -> str:
    return "finalize" if state.get("validation_feedback") is None else "write"


def build_graph(deps: SupervisorDeps):
    g = StateGraph(AgentState)
    node = lambda fn: partial(fn, deps=deps)   # Wave 13 — 명시적 의존성 주입
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("plan", node(plan_node))
    g.add_node("gate", node(gate_node))
    g.add_node("research", node(research_node))
    g.add_node("replan_check", node(replan_check_node))
    g.add_node("analysis", node(analysis_node))
    g.add_node("write", node(write_node))
    g.add_node("validate", node(validate_node))
    g.add_node("finalize", node(finalize_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "plan": "plan"})
    g.add_edge("chat_answer", END)
    g.add_edge("plan", "gate")
    g.add_edge("gate", "research")
    g.add_edge("research", "replan_check")
    g.add_conditional_edges("replan_check", _after_replan_check,
                            {"analysis": "analysis", "research": "research"})
    g.add_edge("analysis", "write")
    g.add_edge("write", "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"finalize": "finalize", "write": "write"})
    g.add_edge("finalize", END)
    return g.compile()
