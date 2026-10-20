"""최소 인메모리 벡터 스토어 — 문서 수십 개 규모라 외부 벡터 DB 없이 코사인 전수 탐색.

각 청크는 metadata(incident_type 등)를 함께 저장하고, 검색 결과로 그대로 돌려준다.
문서가 수만 건 이상으로 늘면 FAISS/Chroma 같은 ANN 인덱스로 교체할 지점이다.
"""
import math
from dataclasses import dataclass, field


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict = field(default_factory=dict)
    vector: list[float] = field(default_factory=list)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class InMemoryVectorStore:
    def __init__(self):
        self.chunks: list[Chunk] = []

    def add(self, chunks: list[Chunk]) -> None:
        self.chunks.extend(chunks)

    def search(self, vector: list[float], k: int = 5) -> list[tuple[Chunk, float]]:
        scored = [(c, cosine(vector, c.vector)) for c in self.chunks]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def __len__(self) -> int:
        return len(self.chunks)
