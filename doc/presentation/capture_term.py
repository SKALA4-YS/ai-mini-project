"""실제 명령 출력(텍스트 파일)을 터미널 창 모양 PNG로 렌더링한다 — 발표 '화면 캡처'용.

  .venv/bin/python doc/presentation/capture_term.py <출력이름> "<실행한 명령>" <출력.txt> [폭px]

텍스트는 명령의 실제 출력을 넣고, 생략한 부분이 있으면 발표 자료 캡션에 밝힌다. 헤드리스 Chrome 렌더링.
"""
import html
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "captures"

TEMPLATE = """<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#fff;padding:0}
.win{width:%(w)dpx;background:#1e1e2a;border-radius:10px;overflow:hidden;
 font:14px/1.5 "SF Mono",Menlo,"Apple SD Gothic Neo",monospace;color:#d8dae0}
.bar{background:#2b2b3a;padding:9px 14px;display:flex;gap:7px;align-items:center}
.dot{width:11px;height:11px;border-radius:50%%}
.t{color:#9a9cab;font-size:12px;margin-left:10px}
pre{margin:0;padding:14px 18px 16px;white-space:pre-wrap;word-break:break-all;font:inherit}
.cmd{color:#7ee0a3}
</style><div class="win" id="w"><div class="bar"><span class="dot" style="background:#ff5f57"></span>
<span class="dot" style="background:#febc2e"></span><span class="dot" style="background:#28c840"></span>
<span class="t">zsh — all_total_vue_student</span></div><pre><span class="cmd">$ %(cmd)s</span>
%(body)s</pre></div>"""


def main():
    name, cmd, src = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    width = int(sys.argv[4]) if len(sys.argv) > 4 else 900
    page_html = TEMPLATE % {"w": width, "cmd": html.escape(cmd),
                            "body": html.escape(src.read_text().rstrip())}
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        pg = b.new_page(viewport={"width": width, "height": 400}, device_scale_factor=2)
        pg.set_content(page_html)
        pg.locator("#w").screenshot(path=str(OUT / f"{name}.png"))
        b.close()


if __name__ == "__main__":
    main()
