"""incident_type 분류기 단독 비교 — 키워드 규칙(baseline) vs RAG.

그래프·LLM을 거치지 않고 분류기만 떼어 같은 입력으로 비교한다. collected는 쿼리에
언급된 시스템(없으면 auth-service)의 monitoring.json 값을 그대로 넣어 재현성을 확보한다.
LLM의 plan 결과에 따라 흔들리는 end-to-end 평가(run_eval.py)를 보완하는 통제 실험이다.

  .venv/bin/python -m eval.compare_classifier                 # dataset.json
  .venv/bin/python -m eval.compare_classifier --set holdout   # 튜닝에 안 쓴 holdout.json
  .venv/bin/python -m eval.compare_classifier --embedder hash # 네트워크 없이
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv   # noqa: E402

load_dotenv(ROOT / ".env")

from agent.rag.embedder import HashEmbedder, OpenAIEmbedder   # noqa: E402
from agent.rag.keyword import classify_by_keyword              # noqa: E402
from agent.rag.retriever import RunbookRetriever               # noqa: E402
from agent.systems import DEFAULT_SYSTEM, mentioned_systems    # noqa: E402

MONITORING = json.loads((ROOT / "data" / "monitoring.json").read_text())


def _collected(query: str) -> list[dict]:
    systems = mentioned_systems(query) or [DEFAULT_SYSTEM]
    return [{"source": "monitoring",
             "data": {"system_name": s, "status": MONITORING[s]["status"],
                      "alerts": MONITORING[s].get("alerts", [])}}
            for s in systems]


async def main(set_name: str, embedder_name: str) -> None:
    path = Path(__file__).parent / ("dataset.json" if set_name == "dataset" else "holdout.json")
    cases = json.loads(path.read_text())["incident"]
    embedder = OpenAIEmbedder() if embedder_name == "openai" else HashEmbedder()
    retriever = RunbookRetriever(embedder)
    t0 = time.perf_counter()
    n_chunks = await retriever.build()
    build_s = time.perf_counter() - t0

    rows, rag_latency = [], []
    for case in cases:
        collected = _collected(case["query"])
        kw = classify_by_keyword(collected)
        t0 = time.perf_counter()
        rag = await retriever.classify(case["query"], collected)
        rag_latency.append(time.perf_counter() - t0)
        rows.append({"id": case["id"], "subset": case.get("subset", "-"),
                     "expected": case["expected_incident_type"], "keyword": kw,
                     "rag": rag["incident_type"], "rag_method": rag["method"],
                     "rag_score": rag["score"], "candidates": rag["candidates"]})

    def acc(key, subset=None):
        rs = [r for r in rows if subset is None or r["subset"] == subset]
        return round(sum(r[key] == r["expected"] for r in rs) / len(rs), 4) if rs else None

    print(f"{'id':5} {'subset':8} {'expected':16} {'keyword':16} {'rag':16} score")
    for r in rows:
        mark = lambda k: ("✓" if r[k] == r["expected"] else "✗")
        print(f"{r['id']:5} {r['subset']:8} {r['expected']:16} "
              f"{mark('keyword')} {r['keyword']:14} {mark('rag')} {r['rag']:14} {r['rag_score']}")
    subsets = sorted({r["subset"] for r in rows})
    summary = {
        "set": set_name, "embedder": embedder.name, "n": len(rows), "chunks": n_chunks,
        "keyword_accuracy": acc("keyword"), "rag_accuracy": acc("rag"),
        "by_subset": {s: {"keyword": acc("keyword", s), "rag": acc("rag", s)} for s in subsets},
        "rag_fallbacks": sum(r["rag_method"] != "rag" for r in rows),
        "index_build_s": round(build_s, 3),
        "rag_classify_mean_ms": round(sum(rag_latency) / len(rag_latency) * 1000, 1),
        "embedding_usage": embedder.usage, "embedding_cost_usd": embedder.cost_usd,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out = Path(__file__).parent / "results" / f"classifier-{set_name}-{embedder_name}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="dataset", choices=["dataset", "holdout"])
    ap.add_argument("--embedder", default="openai", choices=["openai", "hash"])
    args = ap.parse_args()
    asyncio.run(main(args.set, args.embedder))
