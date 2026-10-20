"""노드·그래프 테스트 — stub MCP + fake LLM을 주입해 그래프를 end-to-end로 돌린다."""
import asyncio
import time

from agent.graph import build_graph
from agent.nodes.plan import gate_node
from agent.nodes.replan import replan_check_node
from agent.nodes.research import TASK_DELAY_S, research_node
from agent.rag.embedder import HashEmbedder
from agent.rag.retriever import RunbookRetriever

from conftest import StubMCP, collected_for


def _run(coro):
    return asyncio.run(coro)


def _retriever():
    r = RunbookRetriever(HashEmbedder())
    _run(r.build())
    return r


def _invoke(deps, query):
    return _run(build_graph(deps).ainvoke(
        {"query": query, "thread_id": "t", "history": [], "events": [], "cost_usd": 0.0}))


def test_gate_removes_self_contradicting_analysis_task(make_deps):
    state = {"needs_analysis": False, "events": [],
             "tasks": [{"agent": "research"}, {"agent": "analysis"}]}
    out = _run(gate_node(state, make_deps()))
    assert out["gate_removed"] == 1 and len(out["tasks"]) == 1


def test_replan_uses_injected_retriever(make_deps):
    state = {"query": "auth-service 데이터베이스 다운, DB 연결 실패", "events": [],
             "tasks": [], "collected": collected_for("auth-service")}
    with_rag = _run(replan_check_node(state, make_deps(retriever=_retriever())))
    without = _run(replan_check_node(state, make_deps(retriever=None)))
    assert with_rag["incident_type"] == "db-down"
    assert with_rag["events"][-1]["method"] == "rag"
    assert without["incident_type"] == "high-latency"   # baseline 키워드 규칙
    assert without["events"][-1]["method"] == "keyword"


def test_replan_stops_after_one_round(make_deps):
    state = {"query": "q", "events": [], "tasks": [], "collected": [], "replan_rounds": 1}
    out = _run(replan_check_node(state, make_deps()))
    assert out["events"][-1]["is_sufficient"] is True


def test_research_runs_tasks_concurrently(make_deps):
    mcp = StubMCP(tool_delay=0.2)
    systems = ["auth-service", "web-01", "payments-service", "cache-service"]
    state = {"events": [], "collected": [], "tasks": [
        {"id": f"t{i}", "agent": "research", "kind": "monitoring", "target": s,
         "status": "pending"} for i, s in enumerate(systems)]}
    started = time.perf_counter()
    out = _run(research_node(state, make_deps(mcp=mcp)))
    elapsed = time.perf_counter() - started
    assert mcp.max_in_flight == len(systems)   # MCP 호출이 실제로 겹쳐 실행됨
    assert elapsed < (TASK_DELAY_S + 0.2) * 2  # 순차였다면 (0.35+0.2)×4 ≈ 2.2s
    assert all(t["status"] == "done" for t in out["tasks"])


def test_graph_chat_route_skips_pipeline(make_deps, stub_mcp):
    result = _invoke(make_deps(), "안녕, 너는 누구야?")
    assert result["route"] == "chat"
    assert stub_mcp.calls == []


def test_graph_incident_end_to_end_with_rag(make_deps, stub_mcp):
    result = _invoke(make_deps(retriever=_retriever()),
                     "web-01 서버 디스크 사용률 경고가 떴어요")
    types = [e["type"] for e in result["events"]]
    assert result["route"] == "incident"
    assert result["incident_type"] == "disk-full"
    assert ("read_resource", "runbook://incident-response/disk-full") in stub_mcp.calls
    # fake quality LLM은 1차 초안을 일부러 짧게 써서 validator 반려 → 재작성 루프가 돈다
    assert types.count("DraftWritten") == 2
    assert result["final"].startswith("'web-01 서버")
    assert types[-1] == "Finalize"
