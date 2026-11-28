"""Render py-spy sampled stacks as a Markdown report and flame graph."""

from datetime import datetime
from hashlib import blake2b
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPH_WIDTH = 1400
GRAPH_LEFT = 20
GRAPH_RIGHT = 20
ROW_HEIGHT = 23
GRAPH_TOP = 66


def _location(file, line):
    try:
        file = str(Path(file).relative_to(ROOT))
    except ValueError:
        pass
    return f"{file}:{line}" if line else file


def _markdown_cell(value):
    return str(value).replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def markdown_report(rows, metadata, started_at: datetime, game_args):
    """Include every sampled function; the CSV keeps full precision and sample counts."""
    sampler = metadata["sampler_cpu_s"]
    sampler_text = f"{sampler:.3f}초" if sampler is not None else "이 플랫폼에서 측정 불가"
    lines = [
        "# 게임 CPU 프로파일 보고서", "",
        f"- 시작: {started_at.isoformat(timespec='seconds')}",
        f"- 실행: `python -m prototype {' '.join(game_args)}`",
        f"- 표본 속도: 초당 {metadata['sample_rate_hz']}회",
        f"- 게임의 활성 스레드 표본 시간 합계: {metadata['sampled_active_thread_s']:.3f}초",
        f"- 별도 py-spy 프로세스 CPU: {sampler_text}",
        f"- 별도 보고서 작성 프로세스 CPU: {metadata['reporter_cpu_s']:.3f}초",
        f"- 표본에서 관측된 함수: {len(rows)}개", "",
        "## 시각화와 원본", "",
        "- [대화형 플레임 그래프](flamegraph.html): 마우스로 함수 시간을 확인하고 클릭해서 확대",
        "- [SVG 플레임 그래프](flamegraph.svg): 단독으로 열 수 있는 그래프",
        "- [함수별 CSV](functions.csv): 전체 함수의 표본 수와 초 단위 추정치",
        "- [speedscope 원본](samples.speedscope.json): 수집한 호출 스택",
        "- [계측 메타데이터](metadata.json): 게임 밖 프로세스의 CPU 시간", "",
        "## 함수별 자체 CPU 시간", "",
        "| 함수 | 위치 | 자체 CPU 추정 (ms) | 포괄 CPU 추정 (ms) | 자체 표본 |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for (file, name, line), (self_s, inclusive_s, self_samples, _) in rows:
        lines.append(f"| {_markdown_cell(name)} | {_markdown_cell(_location(file, line))} | "
                     f"{self_s * 1000:.1f} | {inclusive_s * 1000:.1f} | {self_samples} |")
    lines.extend([
        "", "## 포괄 CPU 시간 상위 30개", "",
        "| 함수 | 위치 | 포괄 CPU 추정 (ms) |",
        "| --- | --- | ---: |",
    ])
    for (file, name, line), (_, inclusive_s, _, _) in sorted(rows, key=lambda row: -row[1][1])[:30]:
        lines.append(f"| {_markdown_cell(name)} | {_markdown_cell(_location(file, line))} | "
                     f"{inclusive_s * 1000:.1f} |")
    lines.extend([
        "", "## 해석", "",
        "`자체 CPU`는 함수가 스택 맨 위에 있던 표본, `포괄 CPU`는 함수가 스택 어디에든 있던 표본의 시간이다.",
        "포괄 시간은 부모와 자식에 중복되므로 함수 사이에 합산하지 않는다.",
        "py-spy는 실행 중인 스레드를 표본 추출한다. 각 값은 CPU 활성 시간의 추정치이며 모든 개별 호출을 추적한 실측값은 아니다.",
        "짧은 호출은 표본에 없을 수 있고, 기본 수집에는 네이티브 라이브러리 내부와 대기 시간이 포함되지 않는다.",
        "py-spy와 보고서 작성은 게임 밖에서 실행되며, 위 별도 CPU 시간은 함수별 값에 포함되지 않는다.", "",
    ])
    return "\n".join(lines)


def _stack_keys(frames, stack):
    keys = []
    for index in stack:
        frame = frames[index]
        if not frame.get("file") and frame["name"].startswith("process "):
            continue
        keys.append((frame.get("file") or "", frame["name"], frame.get("line") or 0))
    return keys


def _new_node(key):
    return {"key": key, "weight": 0.0, "children": {}}


def _flame_tree(document):
    frames = document["shared"]["frames"]
    root = _new_node(("", "전체 활성 CPU 표본", 0))
    max_depth = 0
    for profile in document["profiles"]:
        thread_key = ("", profile.get("name", "Thread"), 0)
        for stack, weight in zip(profile["samples"], profile["weights"], strict=True):
            if not stack or weight <= 0:
                continue
            keys = [thread_key, *_stack_keys(frames, stack)]
            max_depth = max(max_depth, len(keys))
            root["weight"] += weight
            node = root
            for key in keys:
                node = node["children"].setdefault(key, _new_node(key))
                node["weight"] += weight
    return root, max_depth


def _color(key, depth):
    if depth == 0:
        return "#34445b"
    if depth == 1:
        return "#53677f"
    digest = blake2b(repr(key).encode("utf-8"), digest_size=2).digest()
    hue = 16 + (int.from_bytes(digest, "big") % 38)
    return f"hsl({hue} 82% 70%)"


def flamegraph_svg(document):
    """Create one standalone SVG from the same samples used by the time table."""
    root, max_depth = _flame_tree(document)
    width = GRAPH_WIDTH - GRAPH_LEFT - GRAPH_RIGHT
    height = GRAPH_TOP + (max_depth + 1) * ROW_HEIGHT + 22
    total = root["weight"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" id="flamegraph" width="{GRAPH_WIDTH}" '
        f'height="{height}" viewBox="0 0 {GRAPH_WIDTH} {height}" preserveAspectRatio="none" '
        'role="img" aria-label="함수별 CPU 표본 시간 플레임 그래프">',
        '<style>.frame rect{stroke:#ffffff;stroke-width:1;cursor:pointer}'
        '.frame:hover rect{stroke:#1b2432;stroke-width:2}'
        '.frame text{font:12px sans-serif;fill:#1b2432;pointer-events:none}'
        '.frame.thread text,.frame.root text{fill:white}'
        '.frame.dim{opacity:.16}.frame.match rect{stroke:#ad2c16;stroke-width:2}'
        '.axis{font:12px sans-serif;fill:#42536b}</style>',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        f'<text class="axis" x="{GRAPH_LEFT}" y="24">활성 CPU 표본 {total:.3f}초 · 가로폭은 포괄 시간에 비례</text>',
    ]
    for index in range(6):
        x = GRAPH_LEFT + width * index / 5
        parts.append(f'<line x1="{x:.2f}" y1="36" x2="{x:.2f}" y2="48" stroke="#98a6b9"/>')
        parts.append(f'<text class="axis" x="{x:.2f}" y="60" text-anchor="middle">{total * index / 5:.2f}s</text>')

    def draw(node, x, rect_width, depth):
        file, name, line = node["key"]
        y = GRAPH_TOP + depth * ROW_HEIGHT
        own = max(0.0, node["weight"] - sum(child["weight"] for child in node["children"].values()))
        tooltip = f"{name}\n{_location(file, line)}\n포괄 {node['weight'] * 1000:.1f} ms · 자체 {own * 1000:.1f} ms"
        group_class = "root" if depth == 0 else "thread" if depth == 1 else "function"
        parts.append(
            f'<g class="frame {group_class}" data-name="{escape(name, quote=True)}" '
            f'data-file="{escape(_location(file, line), quote=True)}" '
            f'data-inclusive="{node["weight"] * 1000:.1f}" data-self="{own * 1000:.1f}">'
            f'<title>{escape(tooltip)}</title>'
            f'<rect x="{x:.3f}" y="{y}" width="{rect_width:.3f}" height="{ROW_HEIGHT - 2}" '
            f'rx="2" fill="{_color(node["key"], depth)}"/>'
        )
        if rect_width >= 48:
            max_chars = max(1, int((rect_width - 9) / 7))
            label = name if len(name) <= max_chars else name[:max(1, max_chars - 1)] + "…"
            parts.append(f'<text x="{x + 5:.3f}" y="{y + 15}">{escape(label)}</text>')
        parts.append("</g>")
        child_x = x
        for child in sorted(node["children"].values(), key=lambda item: -item["weight"]):
            child_width = rect_width * child["weight"] / node["weight"] if node["weight"] else 0
            draw(child, child_x, child_width, depth + 1)
            child_x += child_width

    draw(root, GRAPH_LEFT, width, 0)
    parts.append("</svg>")
    return "\n".join(parts)


def interactive_html(svg):
    """Embed the SVG so search, hover details and horizontal zoom work offline."""
    return """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>게임 CPU 플레임 그래프</title>
<style>
body{margin:0;background:#eef2f6;color:#192536;font:15px system-ui,sans-serif}
main{max-width:1500px;margin:auto;padding:20px}
h1{font-size:22px;margin:0 0 8px}
p{color:#536276;margin:6px 0 14px}
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
input,button{font:inherit;padding:8px 10px;border:1px solid #9ba9ba;border-radius:6px;background:white}
input{min-width:280px}button{cursor:pointer}button:hover{background:#dde6ef}
#details{min-height:24px;font-variant-numeric:tabular-nums}
.graph{background:white;border:1px solid #cbd5e1;border-radius:8px;overflow:auto;max-height:75vh}
.graph svg{display:block;width:100%;min-width:900px;height:auto}
</style>
</head>
<body><main>
<h1>게임 CPU 플레임 그래프</h1>
<p>사각형 너비는 해당 호출 경로의 포괄 CPU 표본 시간입니다. 함수에 마우스를 올려 시간을 확인하고, 클릭해서 가로로 확대하세요.</p>
<div class="controls"><label>함수 검색 <input id="search" type="search" placeholder="함수명 또는 파일 경로"></label>
<button id="reset" type="button">확대 초기화</button><span id="matches"></span></div>
<p id="details" aria-live="polite">함수를 가리키면 자체·포괄 시간을 표시합니다.</p>
<div class="graph">
""" + svg + """
</div>
<script>
const svg = document.getElementById('flamegraph');
const frames = [...svg.querySelectorAll('g.frame')];
const initial = svg.getAttribute('viewBox');
const original = svg.viewBox.baseVal;
const fullHeight = original.height;
const details = document.getElementById('details');
svg.addEventListener('pointerover', event => {
  const frame = event.target.closest('g.frame');
  if (!frame) return;
  details.textContent = `${frame.dataset.name} · ${frame.dataset.file} · 자체 ${frame.dataset.self} ms · 포괄 ${frame.dataset.inclusive} ms`;
});
svg.addEventListener('click', event => {
  const frame = event.target.closest('g.frame');
  if (!frame) return;
  const rect = frame.querySelector('rect');
  const x = Number(rect.getAttribute('x'));
  const width = Number(rect.getAttribute('width'));
  if (width > 0) svg.setAttribute('viewBox', `${x} 0 ${width} ${fullHeight}`);
});
document.getElementById('reset').addEventListener('click', () => svg.setAttribute('viewBox', initial));
document.getElementById('search').addEventListener('input', event => {
  const query = event.target.value.trim().toLocaleLowerCase();
  let matches = 0;
  for (const frame of frames) {
    const found = query && `${frame.dataset.name} ${frame.dataset.file}`.toLocaleLowerCase().includes(query);
    frame.classList.toggle('dim', Boolean(query) && !found);
    frame.classList.toggle('match', Boolean(found));
    if (found) matches++;
  }
  document.getElementById('matches').textContent = query ? `${matches}개 일치` : '';
});
</script>
</main></body></html>
"""
