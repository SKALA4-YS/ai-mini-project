"""LLM_BACKEND=openai — 실제 OpenAI API 백엔드 (fake_llm/ollama_llm과 동일 인터페이스).

fast(기본 gpt-4o-mini)는 분류·계획·단순 대화, quality(기본 gpt-4o)는 최종 답변 작성
전담(W10a). 실모델이라 결정론이 아니므로 gate(W6)·replan(W7)·validator(W8)가 실제
방어선으로 동작한다. 실제 과금이 발생하는 원격 API다.

baseline 대비 변경점:
- 동기 `httpx.post` → 인스턴스당 1개의 `httpx.AsyncClient`(커넥션 재사용). baseline은
  async 노드 안에서 동기 호출을 해 이벤트 루프 전체를 막았고, 그 결과 동시 요청 5건이
  완전히 직렬 처리됐다(eval/results/before-openai.json: 5건 26.1s ≈ 1건 × 5).
- chat/write는 `stream=True` SSE를 파싱해 토큰이 생성되는 즉시 on_token으로 흘려보낸다.
- 프롬프트·출력 보정은 llm_common.py로 통합(ollama_llm.py와 중복 제거).

구조화 출력은 `response_format={"type": "json_object"}`(JSON 모드)로 강제하고, 스키마
준수는 llm_common의 코드 레벨 보정이 책임진다. 비용(cost_usd)은 실측 total_tokens ×
가상 블렌디드 단가(입·출력 단가 구분 없이 단순화)다.
"""
import json
import os

import httpx
from langsmith import traceable

from . import llm_common as C

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class OpenAILLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"openai:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _request(self, system: str, user: str, **extra) -> tuple[dict, dict]:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY가 설정되지 않았습니다 — .env 또는 환경변수로 지정하세요.")
        payload = {"model": self.model, "temperature": 0,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}], **extra}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return payload, headers

    def _usage(self, tokens: int) -> dict:
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6)}

    async def _chat(self, system: str, user: str, json_mode: bool = False) -> tuple[str, dict]:
        extra = {"response_format": {"type": "json_object"}} if json_mode else {}
        payload, headers = self._request(system, user, **extra)
        resp = await self.client.post(OPENAI_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content, self._usage(data.get("usage", {}).get("total_tokens", 0))

    async def _chat_stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        """사용자 대면 텍스트 전용 — SSE 청크의 delta를 즉시 on_token으로 중계한다."""
        payload, headers = self._request(system, user, stream=True,
                                         stream_options={"include_usage": True})
        pieces, tokens = [], 0
        async with self.client.stream("POST", OPENAI_URL, json=payload,
                                      headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                body = line[len("data: "):]
                if body == "[DONE]":
                    break
                data = json.loads(body)
                if data.get("usage"):
                    tokens = data["usage"].get("total_tokens", 0)
                for choice in data.get("choices", []):
                    piece = choice.get("delta", {}).get("content")
                    if piece:
                        pieces.append(piece)
                        if on_token is not None:
                            on_token(piece)
        return "".join(pieces), C.with_prompt(self._usage(tokens), system, user)


class OpenAIFastLLM(OpenAILLM):
    """분류·계획·단순 대화 전담 (저비용 모델, 기본 gpt-4o-mini)."""

    @traceable(run_type="llm", name="openai_fast.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        content, usage = await self._chat(C.ROUTE_SYSTEM, query, json_mode=True)
        return C.parse_route(content, query), usage

    @traceable(run_type="llm", name="openai_fast.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        content, usage = await self._chat(
            C.PLAN_SYSTEM, C.plan_user_prompt(query, procedure_prompt), json_mode=True)
        return C.normalize_plan(content, query), usage

    @traceable(run_type="llm", name="openai_fast.analyze")
    async def analyze(self, collected: list[dict]) -> tuple[str, dict]:
        return await self._chat(C.ANALYZE_SYSTEM, C.analyze_user_prompt(collected))

    @traceable(run_type="llm", name="openai_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        return await self._chat_stream(C.CHAT_SYSTEM, C.chat_user_prompt(query, history),
                                       on_token=on_token)


class OpenAIQualityLLM(OpenAILLM):
    """최종 답변 작성 전담 (고품질 모델, 기본 gpt-4o)."""

    @traceable(run_type="llm", name="openai_quality.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        return await self._chat_stream(
            C.WRITE_SYSTEM, C.write_user_prompt(query, collected, feedback),
            on_token=on_token)
