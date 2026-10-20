"""동시 요청 시연 — 떠 있는 서버(/chat)에 같은 질문 N건을 동시에 보내고 건별·총 소요를 출력한다.

  .venv/bin/python -m eval.concurrency_demo http://127.0.0.1:8100 [N]

run_eval.py의 동시성 측정은 그래프를 직접 호출하고, 이 스크립트는 HTTP 서버 전체를
거친다(FastAPI·MCP 세션·LLM 포함). 발표 화면 캡처용.
"""
import asyncio
import sys
import time

import httpx

QUERY = "auth-service 로그인 응답이 느려졌어요. 원인 확인하고 대응 방법 알려주세요"


async def one(client, base, i, t0):
    s = time.perf_counter()
    r = await client.post(f"{base}/chat", json={"message": QUERY, "thread_id": f"conc-{i}-{t0}"})
    r.raise_for_status()
    e = time.perf_counter()
    return i, round(s - t0, 2), round(e - t0, 2), round(e - s, 2)


async def main():
    base = sys.argv[1].rstrip("/")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    async with httpx.AsyncClient(timeout=300.0) as client:
        health = (await client.get(f"{base}/health")).json()
        print(f"backend={health['llm_backend']}  retriever={health.get('retriever', '(없음 — baseline)')}")
        print(f"질문: {QUERY}\n")
        t0 = time.perf_counter()
        await one(client, base, -1, t0)            # 워밍업(커넥션·캐시) — 측정 제외
        singles = [(await one(client, base, k, t0))[3] for k in range(3)]
        single = sum(singles) / len(singles)
        print(f"단건 순차 3회: {', '.join(f'{x:.2f}s' for x in singles)} → 평균 {single:.2f}s"
              f"\n\n동시 {n}건 전송")
        t0 = time.perf_counter()
        rows = await asyncio.gather(*(one(client, base, i, t0) for i in range(1, n + 1)))
        wall = time.perf_counter() - t0
    print(f"{'#':>2}  {'시작':>6}  {'완료':>6}  {'소요':>6}")
    for i, s, e, d in sorted(rows, key=lambda r: r[2]):
        print(f"{i:>2}  {s:>5.2f}s  {e:>5.2f}s  {d:>5.2f}s")
    print(f"\n총 소요(wall) {wall:.2f}s = 단건의 {wall / single:.1f}배"
          f"  (1배에 가까울수록 병렬, {n}배에 가까울수록 직렬)")


if __name__ == "__main__":
    asyncio.run(main())
