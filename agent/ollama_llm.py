"""LLM_BACKEND=ollama — 로컬 Ollama 실모델 백엔드 (fake_llm과 동일 인터페이스).

Wave 10a 구도를 실물로: fast(기본 qwen3:0.6b)는 분류·계획·단순 대화, quality(기본
qwen3:8b)는 최종 답변 작성 전담. Ollama의 구조화 출력(format=<JSON Schema>)과
temperature=0으로 변동을 줄이지만 실모델이므로 결정론은 아니다 — 그래서 gate(W6)·
replan(W7)·validator(W8)가 연출이 아니라 실제 방어선으로 동작한다.

baseline 대비 변경점: route/plan/analyze의 동기 `httpx.post`를 `AsyncClient`로 바꿔
이벤트 루프를 막지 않게 했고, 프롬프트·출력 보정은 llm_common.py로 통합했다.

비용(cost_usd)은 '가상 단가 × 실측 토큰 수(prompt_eval_count+eval_count)'다 — 로컬
추론이라 실제 과금은 0이지만, W10a의 모델 분리 효과를 같은 잣대로 비교하기 위함이다.

사전 조건: `ollama serve` 실행 중 + `ollama pull qwen3:0.6b qwen3:8b`.
"""
import json

import httpx
from langsmith import traceable

from . import llm_common as C

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["chat", "incident"]},
        "needs_analysis": {"type": "boolean"},
    },
    "required": ["route", "needs_analysis"],
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_analysis": {"type": "boolean"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string",
                              "enum": ["research", "analysis"]},
                    "target": {"type": "string"},
                },
                "required": ["agent"],
            },
        },
    },
    "required": ["needs_analysis", "tasks"],
}


class OllamaLLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"ollama:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=300.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _payload(self, system: str, user: str, stream: bool) -> dict:
        return {"model": self.model, "stream": stream, "options": {"temperature": 0.0},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}

    def _usage(self, data: dict) -> dict:
        tokens = (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0)
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6)}

    async def _chat(self, system: str, user: str, schema: dict | None = None) -> tuple[str, dict]:
        payload = self._payload(system, user, stream=False)
        if schema is not None:
            payload["format"] = schema
        resp = await self.client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"], self._usage(data)

    async def _chat_stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        """사용자 대면 텍스트 전용 — Ollama NDJSON 스트림을 토큰 단위로 on_token에 중계한다.
        JSON 스키마 출력(route/plan/analyze)은 부분 출력이 의미가 없어 _chat을 쓴다."""
        chunks: list[str] = []
        usage = {"model": self.name, "tokens": 0, "usd": 0.0}
        async with self.client.stream("POST", OLLAMA_URL,
                                      json=self._payload(system, user, stream=True)) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                piece = data.get("message", {}).get("content", "")
                if piece:
                    chunks.append(piece)
                    if on_token is not None:
                        on_token(piece)
                if data.get("done"):
                    usage = self._usage(data)
        return "".join(chunks), C.with_prompt(usage, system, user)


class OllamaFastLLM(OllamaLLM):
    """분류·계획·단순 대화 전담 (저사양 소형 모델)."""

    @traceable(run_type="llm", name="ollama_fast.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        content, usage = await self._chat(C.ROUTE_SYSTEM, query, schema=ROUTE_SCHEMA)
        return C.parse_route(content, query), usage

    @traceable(run_type="llm", name="ollama_fast.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        content, usage = await self._chat(
            C.PLAN_SYSTEM, C.plan_user_prompt(query, procedure_prompt), schema=PLAN_SCHEMA)
        return C.normalize_plan(content, query), usage

    @traceable(run_type="llm", name="ollama_fast.analyze")
    async def analyze(self, collected: list[dict]) -> tuple[str, dict]:
        return await self._chat(C.ANALYZE_SYSTEM, C.analyze_user_prompt(collected))

    @traceable(run_type="llm", name="ollama_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        return await self._chat_stream(C.CHAT_SYSTEM, C.chat_user_prompt(query, history),
                                       on_token=on_token)


class OllamaQualityLLM(OllamaLLM):
    """최종 답변 작성 전담 (상대적으로 큰 모델)."""

    @traceable(run_type="llm", name="ollama_quality.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        return await self._chat_stream(
            C.WRITE_SYSTEM, C.write_user_prompt(query, collected, feedback),
            on_token=on_token)
