"""eval/results/*.json → Before/After 비교표(markdown). 보고서 수치는 전부 여기서 나온다.

  .venv/bin/python -m eval.report > doc/results.md
"""
import json
from pathlib import Path

R = Path(__file__).parent / "results"


def load(name):
    d = json.loads((R / name).read_text())
    s = d["summary"]
    inc = [r for r in d["rows"] if r["kind"] == "incident"]
    # retry 횟수는 summary에 없어서 행 단위 기록에서 계산한다(재측정 불필요)
    s.setdefault("retry_count_mean", sum(r["retry_count"] for r in inc) / len(inc) if inc else None)
    return s


def pct(v):
    return "-" if v is None else f"{v * 100:.1f}%"


def num(v, unit="", nd=2):
    return "-" if v is None else f"{v:.{nd}f}{unit}"


def delta(b, a, lower_is_better=True, unit="", nd=2):
    if b is None or a is None:
        return "-"
    d = a - b
    better = (d < 0) if lower_is_better else (d > 0)
    sign = "+" if d > 0 else ""
    mark = "▲" if better else ("▼" if d else "=")
    return f"{mark} {sign}{d:.{nd}f}{unit}"


def main():
    b, a = load("before-openai.json"), load("after-openai.json")
    bf, af = load("before-fake.json"), load("after-fake.json")
    cd = json.loads((R / "classifier-dataset-openai.json").read_text())["summary"]
    ch = json.loads((R / "classifier-holdout-openai.json").read_text())["summary"]

    print("## 1. End-to-end (LLM_BACKEND=openai, gpt-4o-mini/gpt-4o, eval/dataset.json 22문항)\n")
    print("| 지표 | Before | After | 변화 |")
    print("|---|---|---|---|")
    rows = [
        ("incident_type 정확도 (전체 19)", pct(b["incident_type_accuracy"]),
         pct(a["incident_type_accuracy"]),
         delta(b["incident_type_accuracy"] * 100, a["incident_type_accuracy"] * 100, False, "%p", 1)),
    ]
    for s, label in (("aligned", "알림 일치 10"), ("multi", "복수 시스템 3"), ("symptom", "증상 기반 6")):
        bv, av = b["incident_type_accuracy_by_subset"][s], a["incident_type_accuracy_by_subset"][s]
        rows.append((f"└ {label}", pct(bv), pct(av), delta(bv * 100, av * 100, False, "%p", 1)))
    rows += [
        ("route 정확도", pct(b["route_accuracy"]), pct(a["route_accuracy"]),
         delta(b["route_accuracy"] * 100, a["route_accuracy"] * 100, False, "%p", 1)),
        ("요청당 조사 시스템 수", num(b["research_tasks_mean"]), num(a["research_tasks_mean"]),
         delta(b["research_tasks_mean"], a["research_tasks_mean"])),
        ("LLM-judge 진단 적합성(1~5)", num(b["judge_relevance_mean"]), num(a["judge_relevance_mean"]),
         delta(b["judge_relevance_mean"], a["judge_relevance_mean"], False)),
        ("LLM-judge 수치 근거(1~5)", num(b["judge_grounding_mean"]), num(a["judge_grounding_mean"]),
         delta(b["judge_grounding_mean"], a["judge_grounding_mean"], False)),
        ("근거 없는 수치 포함 답변 비율", pct(b["ungrounded_answer_rate"]),
         pct(a["ungrounded_answer_rate"]),
         delta(b["ungrounded_answer_rate"] * 100, a["ungrounded_answer_rate"] * 100, True, "%p", 1)),
        ("검수(validate) 시도 횟수 평균", num(b["validation_attempts_mean"]),
         num(a["validation_attempts_mean"]),
         delta(b["validation_attempts_mean"], a["validation_attempts_mean"])),
        ("MCP 도구 재시도 횟수 평균", num(b.get("retry_count_mean")), num(a.get("retry_count_mean")),
         delta(b.get("retry_count_mean"), a.get("retry_count_mean"))),
        ("단건 지연 평균", num(b["incident_latency_mean_s"], "s"), num(a["incident_latency_mean_s"], "s"),
         delta(b["incident_latency_mean_s"], a["incident_latency_mean_s"], True, "s")),
        ("첫 토큰까지(TTFT) 평균", num(b["stream"]["ttft_mean_s"], "s"),
         num(a["stream"]["ttft_mean_s"], "s"),
         delta(b["stream"]["ttft_mean_s"], a["stream"]["ttft_mean_s"], True, "s")),
        (f"동시 {b['concurrency']['n']}건 총 소요", num(b["concurrency"]["wall_s"], "s"),
         num(a["concurrency"]["wall_s"], "s"),
         delta(b["concurrency"]["wall_s"], a["concurrency"]["wall_s"], True, "s")),
        ("요청당 비용(가상 단가)", num(b["incident_cost_mean_usd"], "$", 4),
         num(a["incident_cost_mean_usd"], "$", 4),
         delta(b["incident_cost_mean_usd"], a["incident_cost_mean_usd"], True, "$", 4)),
    ]
    for r in rows:
        print("| " + " | ".join(r) + " |")
    speedup = b["concurrency"]["wall_s"] / a["concurrency"]["wall_s"]
    print(f"\n동시 처리 처리량: **{speedup:.1f}배** "
          f"(Before 1건당 {b['concurrency']['per_request_s']}s → After {a['concurrency']['per_request_s']}s)\n")

    print("## 2. 분류기 단독 통제 실험 (eval/compare_classifier.py, text-embedding-3-small)\n")
    print("| 셋 | 키워드 규칙 | RAG | 평균 분류 지연 | 임베딩 비용 |")
    print("|---|---|---|---|---|")
    for name, s in (("dataset (19)", cd), ("holdout (12, 튜닝 미사용)", ch)):
        print(f"| {name} | {pct(s['keyword_accuracy'])} | {pct(s['rag_accuracy'])} | "
              f"{s['rag_classify_mean_ms']}ms | ${s['embedding_cost_usd']:.6f} |")
    print("\n| holdout subset | 키워드 | RAG |\n|---|---|---|")
    for k, v in ch["by_subset"].items():
        print(f"| {k} | {pct(v['keyword'])} | {pct(v['rag'])} |")

    print("\n## 3. fake 백엔드 (결정론, 비용 0)\n")
    print("| 지표 | Before | After |\n|---|---|---|")
    print(f"| route 정확도 | {pct(bf['route_accuracy'])} | {pct(af['route_accuracy'])} |")
    print(f"| incident_type 정확도 | {pct(bf['incident_type_accuracy'])} | "
          f"{pct(af['incident_type_accuracy'])} |")


if __name__ == "__main__":
    main()
