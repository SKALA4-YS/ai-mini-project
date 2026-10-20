"""수치 근거 검사 — 초안에 나온 숫자가 수집된 사실(또는 사용자 요청)에 실제로 있는가.

baseline validator는 "30자 이상 + '1)' 포함"만 봤기 때문에, 그럴듯한 형식으로 수치를
지어낸 초안도 합격했다. 이 검사는 LLM 호출 없이 문자열 수준에서 판정하므로 비용 0,
지연 ~0ms다. 대신 "91%"를 "91.0%"로 바꿔 쓰는 식의 표기 차이는 정규화로만 흡수하고,
단위 환산(2.4s ↔ 2400ms) 같은 의미적 동치는 잡지 못한다(한계).
"""
import json
import re

_NUM = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?!\d)")
# 단위가 붙은 수치(2.4s, 512MB)도 잡도록 앞뒤로 '숫자'만 경계로 본다. 그래서 web-01의
# 01, payments-service-3의 3도 추출되는데, 한 자리 정수 제외 규칙에 함께 걸러진다.
_IGNORE_SMALL_INT = 10   # 절차 번호(1) 2) 3))·'2개' 같은 서술용 한 자리 정수는 제외


def _canon(n: str) -> str:
    return str(float(n)).rstrip("0").rstrip(".") if "." in n else n


def _fact_text(collected: list[dict]) -> str:
    return "\n".join(c["data"] if isinstance(c.get("data"), str)
                     else json.dumps(c.get("data"), ensure_ascii=False) for c in collected)


def ungrounded_numbers(draft: str, query: str, collected: list[dict]) -> list[str]:
    known = {_canon(n) for n in _NUM.findall(query + "\n" + _fact_text(collected))}
    out = []
    for n in _NUM.findall(draft):
        if n.isdigit() and int(n) < _IGNORE_SMALL_INT:
            continue
        if _canon(n) not in known and n not in out:
            out.append(n)
    return out
