"""RunbookRetriever 단위 테스트 — 네트워크 없는 HashEmbedder로 청킹·검색·폴백을 검증."""
import asyncio

from agent.rag.embedder import HashEmbedder
from agent.rag.retriever import RUNBOOK_DIR, RunbookRetriever, chunk_runbook
from agent.rag.vector_store import Chunk, InMemoryVectorStore, cosine

from conftest import collected_for


def _retriever(**kw):
    r = RunbookRetriever(HashEmbedder(), **kw)
    asyncio.run(r.build())
    return r


def test_chunk_runbook_has_symptom_and_steps_with_metadata():
    chunks = chunk_runbook("disk-full", (RUNBOOK_DIR / "disk-full.md").read_text())
    assert chunks[0].metadata == {"incident_type": "disk-full", "kind": "symptom"}
    assert sum(c.metadata["kind"] == "step" for c in chunks) == 5
    assert all(c.text.startswith("Runbook: 디스크") for c in chunks)


def test_vector_store_returns_most_similar_first():
    store = InMemoryVectorStore()
    store.add([Chunk("a", "a", vector=[1.0, 0.0]), Chunk("b", "b", vector=[0.0, 1.0])])
    (best, score), _ = store.search([0.9, 0.1], k=2)
    assert best.id == "a" and score > 0.9
    assert cosine([0, 0], [1, 1]) == 0.0


def test_index_covers_every_runbook():
    r = _retriever()
    types = {c.metadata["incident_type"] for c in r.store.chunks}
    assert types == {p.stem for p in RUNBOOK_DIR.glob("*.md")}


def test_rag_uses_user_symptom_over_alerts():
    """키워드 규칙은 high-latency로 틀리는 케이스 (test_rules 참고)."""
    r = _retriever()
    out = asyncio.run(r.classify("auth-service 데이터베이스 다운, DB 연결 실패",
                                 collected_for("auth-service")))
    assert out["method"] == "rag"
    assert out["incident_type"] == "db-down"
    assert len(out["candidates"]) == 3


def test_low_score_falls_back_to_keyword():
    r = _retriever(min_score=0.99)
    out = asyncio.run(r.classify("뭔가 이상해요", collected_for("cache-service")))
    assert out["method"] == "keyword-fallback"
    assert out["incident_type"] == "memory-leak"


class BrokenEmbedder(HashEmbedder):
    def __init__(self):
        super().__init__()
        self.fail = False

    async def embed(self, texts):
        if self.fail:
            raise ConnectionError("embedding API down")
        return await super().embed(texts)


def test_embedding_failure_falls_back_to_keyword():
    emb = BrokenEmbedder()
    r = RunbookRetriever(emb)
    asyncio.run(r.build())
    emb.fail = True
    out = asyncio.run(r.classify("gateway-service 504", collected_for("gateway-service")))
    assert out["method"] == "keyword-fallback"
    assert out["incident_type"] == "network-timeout"
    assert "embedding API down" in out["reason"]
