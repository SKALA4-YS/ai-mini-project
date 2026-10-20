"""발표용 웹 채팅 화면 캡처 — 실제 백엔드 + web_demo_server 프록시가 떠 있어야 한다.

  .venv/bin/pip install playwright        # 브라우저는 설치된 Chrome 사용(channel="chrome")
  CAPTURE_URL=http://localhost:8600 \
  .venv/bin/python doc/presentation/capture_web.py <접두어> "<질문>" ["<질문2>" ...]
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "captures"
URL = os.environ.get("CAPTURE_URL", "http://localhost:8600")


def main():
    prefix, queries = sys.argv[1], sys.argv[2:]
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for i, q in enumerate(queries, 1):
            # 질문마다 새 창(새 thread). 화면이 고정 높이라 창을 넉넉히 잡고 대화 끝에서 자른다
            page = browser.new_page(viewport={"width": 900, "height": 1400}, device_scale_factor=2)
            page.goto(URL)
            page.wait_for_timeout(800)
            page.fill("input", q)
            page.click("button[type=submit]")
            page.wait_for_selector("button[type=submit]:not([disabled])", timeout=180_000)
            page.wait_for_timeout(500)
            bottom = page.evaluate(
                "() => Math.max(...[...document.querySelector('#log').children]"
                ".map(el => el.getBoundingClientRect().bottom))")
            page.screenshot(path=str(OUT / f"{prefix}_{i}.png"),
                            clip={"x": 0, "y": 0, "width": 900, "height": min(bottom + 20, 1400)})
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
