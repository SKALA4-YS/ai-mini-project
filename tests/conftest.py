"""공통 테스트 픽스처 — MCP subprocess·실제 LLM 없이 SupervisorDeps를 조립한다.

SupervisorDeps에 stub을 주입하는 것만으로 그래프 전체가 돈다는 것 자체가
의존성 주입(Wave 13) 설계의 검증이다.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from agent.deps import SupervisorDeps   # noqa: E402
from agent.fake_llm import FastLLM, QualityLLM   # noqa: E402

MONITORING = json.loads((ROOT / "data" / "monitoring.json").read_text())


class StubMCP:
    """MCPClient와 같은 인터페이스. 호출 기록과 동시 실행 수를 남긴다."""

    def __init__(self, tool_delay: float = 0.0):
        self.calls: list[tuple] = []
        self.tool_delay = tool_delay
        self.in_flight = 0
        self.max_in_flight = 0

    async def find_prompt(self, name):
        return "ops"

    async def get_prompt(self, name, arguments, server="ops"):
        self.calls.append(("get_prompt", arguments["system_name"]))
        return f"시스템: {arguments['system_name']}\n증상: {arguments['symptom']}"

    async def call_tool(self, name, arguments, server="ops"):
        self.calls.append(("call_tool", arguments["system_name"]))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(self.tool_delay)
        self.in_flight -= 1
        d = MONITORING[arguments["system_name"]]
        return {"system_name": arguments["system_name"], "status": d["status"],
                "cpu_percent": d["cpu_base"], "memory_percent": d["memory_base"],
                "last_checked": "2026-10-20T00:00:00", "alerts": d.get("alerts", [])}

    async def find_resource_template(self, name):
        return "ops", "runbook://incident-response/{incident_type}"

    async def read_resource(self, uri, server="ops"):
        self.calls.append(("read_resource", uri))
        incident_type = uri.rsplit("/", 1)[-1]
        return (ROOT / "data" / "runbooks" / f"{incident_type}.md").read_text()


class StubSessionStore:
    def load(self, thread_id):
        return []

    def append(self, *args):
        pass

    def close(self):
        pass


@pytest.fixture
def stub_mcp():
    return StubMCP()


@pytest.fixture
def make_deps(stub_mcp):
    def _make(retriever=None, mcp=None):
        return SupervisorDeps(
            fast_llm=FastLLM("fast-stub", 0.00015),
            quality_llm=QualityLLM("quality-stub", 0.005),
            mcp=mcp or stub_mcp, session_store=StubSessionStore(),
            version="v2", retriever=retriever)
    return _make


def collected_for(*systems):
    return [{"source": "monitoring",
             "data": {"system_name": s, "status": MONITORING[s]["status"],
                      "cpu_percent": MONITORING[s]["cpu_base"],
                      "memory_percent": MONITORING[s]["memory_base"],
                      "alerts": MONITORING[s].get("alerts", [])}}
            for s in systems]
