"""임베딩 백엔드 2종 — 실제 OpenAI 임베딩 / 네트워크 없는 결정론 해시 임베딩.

둘 다 `async embed(texts) -> list[list[float]]`와 `usage` 누적 카운터를 갖는다.
retriever는 어느 쪽이 꽂혔는지 모른다 (LLM_BACKEND와 같은 DI 패턴).
"""
import hashlib
import json
import math
import os
from pathlib import Path

import httpx

OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"
CACHE_PATH = Path(__file__).resolve().parent.parent.parent / ".cache" / "embeddings.json"


class OpenAIEmbedder:
    """text-embedding-3-small. 같은 텍스트는 디스크 캐시로 재사용해 재기동 비용을 없앤다."""

    usd_per_1m_tokens = 0.02

    def __init__(self, model: str = "text-embedding-3-small",
                 cache_path: Path | None = CACHE_PATH):
        self.name = f"openai:{model}"
        self.model = model
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.cache_path = cache_path
        self._cache: dict[str, list[float]] = {}
        if cache_path is not None and cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text())
            except json.JSONDecodeError:
                self._cache = {}
        self.usage = {"api_calls": 0, "tokens": 0, "cache_hits": 0}

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\n{text}".encode()).hexdigest()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [self._key(t) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self._cache]
        self.usage["cache_hits"] += len(texts) - len(missing)
        if missing:
            if not self.api_key:
                raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    OPENAI_EMBED_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "input": [texts[i] for i in missing]})
                resp.raise_for_status()
                body = resp.json()
            self.usage["api_calls"] += 1
            self.usage["tokens"] += body.get("usage", {}).get("total_tokens", 0)
            for i, item in zip(missing, body["data"]):
                self._cache[keys[i]] = item["embedding"]
            self._save()
        return [self._cache[k] for k in keys]

    def _save(self) -> None:
        if self.cache_path is None:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache))

    @property
    def cost_usd(self) -> float:
        return round(self.usage["tokens"] / 1_000_000 * self.usd_per_1m_tokens, 8)


class HashEmbedder:
    """문자 bigram을 해시 버킷에 세는 결정론 임베딩 — fake 백엔드·단위 테스트용.

    의미를 이해하진 못하지만 표면 어휘가 겹치면 유사도가 올라가므로, 네트워크 없이도
    retriever 파이프라인 전체(청킹→색인→검색→임계값 폴백)를 검증할 수 있다."""

    def __init__(self, dim: int = 512):
        self.name = f"hash-bigram:{dim}"
        self.dim = dim
        self.usage = {"api_calls": 0, "tokens": 0, "cache_hits": 0}
        self.cost_usd = 0.0

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        t = text.lower()
        for a, b in zip(t, t[1:]):
            if a.isspace() or b.isspace():
                continue
            h = int(hashlib.md5((a + b).encode()).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]
