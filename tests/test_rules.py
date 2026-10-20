"""순수 함수 단위 테스트 — 시스템 추출, plan 정규화, 수치 근거 검사, 검증 규칙."""
import json

from agent.grounding import ungrounded_numbers
from agent.llm_common import normalize_plan, parse_route
from agent.nodes.write import check_draft
from agent.rag.keyword import classify_by_keyword
from agent.systems import DEFAULT_SYSTEM, mentioned_systems, primary_system

from conftest import collected_for


# ── systems ────────────────────────────────────────────────────────────
def test_mentioned_systems_in_order_of_appearance():
    q = "gateway-service에서 payments-service로 가는 요청이 타임아웃"
    assert mentioned_systems(q) == ["gateway-service", "payments-service"]


def test_primary_system_falls_back_to_default():
    assert primary_system("로그인이 느려요") == DEFAULT_SYSTEM


# ── llm_common ─────────────────────────────────────────────────────────
def _plan(tasks, needs_analysis=False):
    return json.dumps({"needs_analysis": needs_analysis, "tasks": tasks})


def test_normalize_plan_drops_unmentioned_systems():
    """baseline 과잉 조사 재현: gateway만 물었는데 모델이 6개 시스템을 다 조사하려 함."""
    raw = _plan([{"agent": "research", "target": s} for s in
                 ["gateway-service", "auth-service", "cache-service", "web-01"]])
    result = normalize_plan(raw, "gateway-service 패킷 손실이 생기고 504 에러가 나요")
    assert [t["target"] for t in result["tasks"]] == ["gateway-service"]
    assert result["dropped_targets"] == ["auth-service", "cache-service", "web-01"]


def test_normalize_plan_recovers_when_no_research_task():
    raw = _plan([{"agent": "analysis"}], needs_analysis=True)
    result = normalize_plan(raw, "payments-service랑 cache-service 둘 다 저하")
    targets = [t["target"] for t in result["tasks"] if t["agent"] == "research"]
    assert targets == ["payments-service", "cache-service"]
    assert any(t["agent"] == "analysis" for t in result["tasks"])


def test_normalize_plan_survives_broken_json():
    result = normalize_plan("not json", "로그인이 느려요")
    assert [t["target"] for t in result["tasks"]] == [DEFAULT_SYSTEM]


def test_normalize_plan_fixes_unknown_target():
    raw = _plan([{"agent": "research", "target": "paymnts-svc"}])
    result = normalize_plan(raw, "payments-service CPU가 높아요")
    assert result["tasks"][0]["target"] == "payments-service"


def test_parse_route_fallback_catches_symptom_words():
    assert parse_route("{{broken", "billing-service 응답이 느려요")["route"] == "incident"
    assert parse_route("{{broken", "안녕?")["route"] == "chat"


# ── grounding / validate ───────────────────────────────────────────────
def test_ungrounded_detects_fabricated_number():
    collected = collected_for("payments-service")   # cpu 91.0
    draft = "1) payments-service CPU 91.0%, 응답시간 3.7s로 측정됨"
    assert ungrounded_numbers(draft, "결제가 느려요", collected) == ["3.7"]


def test_ungrounded_accepts_query_numbers_and_normalized_forms():
    collected = collected_for("payments-service")
    draft = "1) 사용자가 말한 100% 급증은 관측되지 않았고 현재 CPU는 91%입니다"
    assert ungrounded_numbers(draft, "CPU가 100% 가까이 튀었어요", collected) == []


def test_check_draft_rejects_format_and_fabrication():
    collected = collected_for("auth-service")
    problems = check_draft("재시작하면 됩니다.", "로그인이 느려요", collected)
    assert len(problems) == 2   # 너무 짧음 + 번호 없음
    problems = check_draft("1) auth-service CPU 78.0%, 2) 메모리 99.9%로 위험합니다. 조치하세요",
                           "로그인이 느려요", collected)
    assert len(problems) == 1 and "99.9" in problems[0]


# ── baseline 키워드 규칙의 알려진 한계 (회귀 기록용) ─────────────────────
def test_keyword_rule_ignores_user_symptom():
    """사용자는 DB 장애를 말했지만 키워드 규칙은 알림의 'latency'만 보고 판단한다."""
    assert classify_by_keyword(collected_for("auth-service")) == "high-latency"


def test_keyword_rule_first_match_wins_on_multi_system():
    """gateway(timeout) + payments(CPU) 알림이 섞이면 규칙 순서상 cpu가 먼저 걸린다."""
    collected = collected_for("gateway-service", "payments-service")
    assert classify_by_keyword(collected) == "cpu-spike"
