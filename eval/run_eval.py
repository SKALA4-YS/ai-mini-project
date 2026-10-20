"""Before/After 공통 평가 하네스.

같은 입력(eval/dataset.json)을 그래프에 in-process로 흘려 아래 지표를 JSON으로 남긴다.
baseline 코드(git tag `baseline`)와 개선 코드 양쪽에서 수정 없이 돌아가도록, 그래프
내부 구현에 기대지 않고 최종 state(events/collected/final)만 읽는다.

  - route 정확도        : incident 쿼리 → incident, chat 쿼리 → chat
  - incident_type 정확도 : 실제로 조회된 runbook이 라벨과 일치하는가 (subset별)
  - 지연시간 / 비용 / 재작성 횟수 / research 병렬 구간 시간
  - 근거 없는 수치 비율   : 답변 속 숫자가 수집된 사실(모니터링·runbook)에 없는 경우
  - LLM-as-judge         : 답변이 수집된 수치를 정확히 인용했는가 (1~5점, gpt-4o-mini)
  - 동시성               : 같은 쿼리 N건을 동시에 보냈을 때 총 소요시간
  - TTFT                 : /chat/stream과 같은 방식(custom 모드)으로 첫 답변 토큰까지 걸린 시간

사용법 (프로젝트 루트에서):
  LLM_BACKEND=openai .venv/bin/python -m eval.run_eval --label after
  LLM_BACKEND=fake   .venv/bin/python -m eval.run_eval --label after-fake --no-judge
"""
import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv   # noqa: E402

load_dotenv(ROOT / ".env")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

import httpx   # noqa: E402

DATASET = json.loads((Path(__file__).parent / "dataset.json").read_text())
RESULTS_DIR = Path(__file__).parent / "results"


async def _make_runtime():
    """server.py의 lifespan과 같은 방식으로 deps/graph를 만든다."""
    import server
    from agent.graph import build_graph
    from agent.session_store import SqliteSessionStore

    session_store = SqliteSessionStore(Path(tempfile.mkdtemp()) / "eval_session.db")
    if hasattr(server, "create_deps"):   # 개선 코드 — 조립 로직을 server에서 재사용
        deps = await server.create_deps(session_store=session_store)
    else:                                # baseline — lifespan 본문을 그대로 재현
        from agent.deps import SupervisorDeps
        from agent.mcp_client import MCPClient
        mcp = MCPClient()
        await mcp.start()
        _, fast, quality = server._build_llms()
        deps = SupervisorDeps(fast_llm=fast, quality_llm=quality, mcp=mcp,
                              session_store=session_store, version="v2")
    return deps, build_graph(deps)


async def _run_one(graph, query: str, thread_id: str) -> dict:
    started = time.perf_counter()
    state = await graph.ainvoke({"query": query, "thread_id": thread_id,
                                 "history": [], "events": [], "cost_usd": 0.0})
    state["_latency"] = round(time.perf_counter() - started, 3)
    return state


async def _ttft(graph, query: str, thread_id: str) -> dict:
    """첫 AnswerToken 도착 시각과 전체 완료 시각 — 체감 응답성 지표."""
    started = time.perf_counter()
    first = None
    async for mode, payload in graph.astream(
            {"query": query, "thread_id": thread_id, "history": [], "events": [],
             "cost_usd": 0.0}, stream_mode=["updates", "custom"]):
        if mode == "custom" and payload.get("type") == "AnswerToken" and first is None:
            first = time.perf_counter() - started
    total = time.perf_counter() - started
    return {"ttft_s": round(first, 3) if first is not None else None,
            "total_s": round(total, 3)}


def _runbook_types(state: dict) -> list[str]:
    return [e["target"] for e in state.get("events", [])
            if e.get("type") == "TaskCompleted" and e.get("source") == "runbook"]


def _facts_text(collected: list[dict]) -> str:
    parts = []
    for c in collected:
        data = c.get("data")
        parts.append(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))
    return "\n".join(parts)


_NUM = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?!\d)")


def _ungrounded_numbers(answer: str, query: str, collected: list[dict]) -> list[str]:
    """답변에 나온 숫자 중 사용자 요청·수집된 사실 어디에도 없는 숫자. 절차 번호
    (1) 2))와 한 자리 정수는 서술용으로 흔해서 제외한다 — 측정 기준은 Before/After 동일."""
    facts = set(_NUM.findall(query + "\n" + _facts_text(collected)))
    found = [n for n in _NUM.findall(answer) if not (n.isdigit() and int(n) < 10)]
    return sorted({n for n in found if n not in facts})


JUDGE_SYSTEM = (
    "당신은 장애 대응 보고서를 엄격하게 채점하는 SRE 리뷰어다. 두 항목을 각각 1~5점으로 "
    "채점하라.\n"
    "grounding: 답변이 수집된 수치(CPU·메모리·지연·알림 등)를 정확히 인용했는가. 사실에 없는 "
    "수치나 원인을 지어냈으면 감점. 5=모든 수치가 정확하고 근거 있음.\n"
    "relevance: 답변이 사용자가 말한 증상에 맞는 원인·조치에 집중했는가. 질문과 무관한 "
    "시스템/증상을 늘어놓거나, 증상과 다른 유형의 대응 절차를 제시했으면 감점. "
    "5=증상에 정확히 맞는 진단과 조치.\n"
    'JSON으로만 답하라: {"grounding": 정수, "relevance": 정수, "reason": "한 문장"}'
)


async def _judge(client: httpx.AsyncClient, query: str, collected: list[dict],
                 answer: str) -> dict:
    user = (f"## 사용자 요청\n{query}\n\n## 수집된 사실\n{_facts_text(collected)}\n\n"
            f"## 답변\n{answer}")
    resp = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={"model": "gpt-4o-mini", "temperature": 0,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": JUDGE_SYSTEM},
                           {"role": "user", "content": user}]},
        timeout=120.0)
    resp.raise_for_status()
    raw = json.loads(resp.json()["choices"][0]["message"]["content"])
    return {"grounding": int(raw.get("grounding", 0)),
            "relevance": int(raw.get("relevance", 0)), "reason": raw.get("reason", "")}


def _mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 4) if values else None


async def main(label: str, use_judge: bool, concurrency: int) -> None:
    deps, graph = await _make_runtime()
    rows = []
    judge_client = httpx.AsyncClient() if use_judge else None
    try:
        for case in DATASET["incident"]:
            state = await _run_one(graph, case["query"], f"eval-{case['id']}")
            types = _runbook_types(state)
            row = {
                "id": case["id"], "subset": case["subset"], "query": case["query"],
                "kind": "incident",
                "route": state.get("route"),
                "route_ok": state.get("route") == "incident",
                "expected_incident_type": case["expected_incident_type"],
                "runbook_types": types,
                "type_ok": bool(types) and types[0] == case["expected_incident_type"],
                "latency_s": state["_latency"],
                "cost_usd": state.get("cost_usd", 0.0),
                "validation_attempts": state.get("validation_attempts", 0),
                "retry_count": state.get("retry_count", 0),
                "parallel_elapsed": state.get("parallel_elapsed"),
                "ungrounded_numbers": _ungrounded_numbers(state.get("final", ""), case["query"],
                                                          state.get("collected", [])),
                "research_targets": [e["target"] for e in state.get("events", [])
                                     if e.get("type") == "TaskCompleted"
                                     and e.get("source") == "monitoring"],
                "collected": state.get("collected", []),
                "final": state.get("final", ""),
            }
            if use_judge and state.get("route") == "incident":
                row["judge"] = await _judge(judge_client, case["query"],
                                            state.get("collected", []), row["final"])
            rows.append(row)
            print(f"[{case['id']}] route={row['route']} type={types[:1]} "
                  f"ok={row['type_ok']} {row['latency_s']}s "
                  f"targets={row['research_targets']} judge={row.get('judge')}", flush=True)

        for case in DATASET["chat"]:
            state = await _run_one(graph, case["query"], f"eval-{case['id']}")
            rows.append({"id": case["id"], "kind": "chat", "query": case["query"],
                         "route": state.get("route"),
                         "route_ok": state.get("route") == "chat",
                         "latency_s": state["_latency"],
                         "cost_usd": state.get("cost_usd", 0.0),
                         "final": state.get("final", "")})
            print(f"[{case['id']}] route={state.get('route')} {state['_latency']}s",
                  flush=True)

        # 동시성 — 같은 incident 쿼리 N건을 한꺼번에 보낸다 (서버에 동시 접속한 상황)
        q = DATASET["concurrency_query"]
        started = time.perf_counter()
        await asyncio.gather(*(_run_one(graph, q, f"eval-conc-{i}")
                               for i in range(concurrency)))
        conc_wall = round(time.perf_counter() - started, 3)
        print(f"[concurrency] {concurrency}건 동시 → {conc_wall}s", flush=True)

        ttfts = [await _ttft(graph, q, f"eval-ttft-{i}") for i in range(3)]
        print(f"[ttft] {ttfts}", flush=True)
    finally:
        if judge_client is not None:
            await judge_client.aclose()
        import server
        if hasattr(server, "close_deps"):
            await server.close_deps(deps)
        else:
            await deps.mcp.stop()

    inc = [r for r in rows if r["kind"] == "incident"]
    summary = {
        "label": label,
        "llm_backend": os.environ.get("LLM_BACKEND", "fake"),
        "route_accuracy": _mean([r["route_ok"] for r in rows]),
        "incident_type_accuracy": _mean([r["type_ok"] for r in inc]),
        "incident_type_accuracy_by_subset": {
            s: _mean([r["type_ok"] for r in inc if r["subset"] == s])
            for s in ("aligned", "multi", "symptom")},
        "incident_latency_mean_s": _mean([r["latency_s"] for r in inc]),
        "incident_latency_p50_s": round(statistics.median(r["latency_s"] for r in inc), 3),
        "incident_cost_mean_usd": _mean([r["cost_usd"] for r in inc]),
        "validation_attempts_mean": _mean([r["validation_attempts"] for r in inc]),
        "research_parallel_elapsed_mean_s": _mean([r["parallel_elapsed"] for r in inc]),
        "ungrounded_answer_rate": _mean([bool(r["ungrounded_numbers"]) for r in inc]),
        "research_tasks_mean": _mean([len(r["research_targets"]) for r in inc]),
        "judge_grounding_mean": _mean([r.get("judge", {}).get("grounding") for r in inc]),
        "judge_relevance_mean": _mean([r.get("judge", {}).get("relevance") for r in inc]),
        "concurrency": {"n": concurrency, "wall_s": conc_wall,
                        "per_request_s": round(conc_wall / concurrency, 3)},
        "stream": {"ttft_mean_s": _mean([t["ttft_s"] for t in ttfts]),
                   "total_mean_s": _mean([t["total_s"] for t in ttfts])},
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{label}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows},
                              ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"→ {out.relative_to(ROOT)}")


def rescore(path: Path) -> None:
    """저장된 답변(final·collected)으로 근거 없는 수치 지표만 다시 계산한다 (LLM 재호출 없음)."""
    doc = json.loads(path.read_text())
    inc = [r for r in doc["rows"] if r["kind"] == "incident"]
    for r in inc:
        r["ungrounded_numbers"] = _ungrounded_numbers(r["final"], r["query"], r["collected"])
    doc["summary"]["ungrounded_answer_rate"] = _mean([bool(r["ungrounded_numbers"]) for r in inc])
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    print(path.name, doc["summary"]["ungrounded_answer_rate"],
          {r["id"]: r["ungrounded_numbers"] for r in inc if r["ungrounded_numbers"]})


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--rescore":
        for name in sys.argv[2:]:
            rescore(RESULTS_DIR / name)
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()
    asyncio.run(main(args.label, not args.no_judge, args.concurrency))
