"""RunbookRetriever — 키워드 규칙 대신 의미 기반으로 incident_type을 고른다.

색인: data/runbooks/*.md를 "제목+대표 증상" 1청크 + "제목+절차 한 줄" N청크로 쪼개고,
      각 청크의 metadata에 파일명(=incident_type)을 붙여 벡터 스토어에 넣는다.
검색: 사용자 증상(query)과 수집된 모니터링 알림을 각각 임베딩해 청크와 비교하고,
      incident_type별 최고 유사도를 가중합(query 0.75 + 알림 0.25)해 1등을 고른다.
      사용자가 말한 증상을 1순위 근거로, 알림은 보조 근거로 쓰는 설계다.
폴백: 임베딩 API 실패 또는 1등 점수가 min_score 미만이면 baseline 키워드 규칙으로 돌아간다
      (RAG가 망가져도 파이프라인은 멈추지 않는다).
"""
import re
from pathlib import Path

from langsmith import traceable

from ..systems import mentioned_systems
from .keyword import classify_by_keyword
from .vector_store import Chunk, InMemoryVectorStore

RUNBOOK_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "runbooks"


def chunk_runbook(incident_type: str, text: str) -> list[Chunk]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    title = lines[0].lstrip("# ").strip() if lines else incident_type
    chunks, symptom_lines, in_symptom = [], [], False
    for line in lines[1:]:
        if line.startswith("## "):
            in_symptom = "증상" in line
            continue
        if in_symptom:
            symptom_lines.append(line)
        elif re.match(r"^\d+\.\s+", line):
            chunks.append(Chunk(id=f"{incident_type}#step{len(chunks)}",
                                text=f"{title}: {line}",
                                metadata={"incident_type": incident_type, "kind": "step"}))
    if symptom_lines:
        chunks.insert(0, Chunk(id=f"{incident_type}#symptom",
                               text=f"{title}\n" + " ".join(symptom_lines),
                               metadata={"incident_type": incident_type, "kind": "symptom"}))
    return chunks


def _alert_text(query: str, collected: list[dict]) -> str:
    """쿼리에 언급된 시스템의 알림만 보조 근거로 쓴다 — 언급이 없으면 전부."""
    mon = [c["data"] for c in collected if c.get("source") == "monitoring"]
    named = set(mentioned_systems(query))
    focus = [d for d in mon if d.get("system_name") in named] or mon
    return " ".join(a for d in focus for a in d.get("alerts", []))


class RunbookRetriever:
    def __init__(self, embedder, runbook_dir: Path = RUNBOOK_DIR,
                 min_score: float = 0.25, query_weight: float = 0.75):
        self.embedder = embedder
        self.runbook_dir = runbook_dir
        self.min_score = min_score
        self.query_weight = query_weight
        self.store = InMemoryVectorStore()

    async def build(self) -> int:
        chunks = []
        for path in sorted(self.runbook_dir.glob("*.md")):
            chunks.extend(chunk_runbook(path.stem, path.read_text()))
        vectors = await self.embedder.embed([c.text for c in chunks])
        for c, v in zip(chunks, vectors):
            c.vector = v
        self.store = InMemoryVectorStore()
        self.store.add(chunks)
        return len(chunks)

    def _scores_by_type(self, vector: list[float]) -> dict[str, float]:
        best: dict[str, float] = {}
        for chunk, score in self.store.search(vector, k=len(self.store)):
            t = chunk.metadata["incident_type"]
            best[t] = max(best.get(t, -1.0), score)
        return best

    @traceable(run_type="retriever", name="rag.classify_incident")
    async def classify(self, query: str, collected: list[dict]) -> dict:
        alerts = _alert_text(query, collected)
        try:
            texts = [query] + ([alerts] if alerts else [])
            vectors = await self.embedder.embed(texts)
        except Exception as exc:   # 네트워크·인증 오류 → 규칙 기반으로 계속 진행
            return {"incident_type": classify_by_keyword(collected),
                    "method": "keyword-fallback", "reason": f"embed error: {exc!r}"[:200],
                    "score": None, "candidates": []}

        q_scores = self._scores_by_type(vectors[0])
        a_scores = self._scores_by_type(vectors[1]) if alerts else {}
        w = self.query_weight if alerts else 1.0
        combined = {t: w * q_scores[t] + (1 - w) * a_scores.get(t, 0.0) for t in q_scores}
        ranked = sorted(combined.items(), key=lambda x: x[1], reverse=True)
        candidates = [{"incident_type": t, "score": round(s, 4)} for t, s in ranked[:3]]
        top_type, top_score = ranked[0]
        if top_score < self.min_score:
            return {"incident_type": classify_by_keyword(collected),
                    "method": "keyword-fallback",
                    "reason": f"top score {top_score:.3f} < {self.min_score}",
                    "score": round(top_score, 4), "candidates": candidates}
        return {"incident_type": top_type, "method": "rag", "score": round(top_score, 4),
                "candidates": candidates}
