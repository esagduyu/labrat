#!/usr/bin/env python3
"""Build labrat_tour.html from SVG screenshots in screenshots/tour/."""

from __future__ import annotations

import base64
from pathlib import Path

ROOT = Path(__file__).parent.parent
SVG_DIR = ROOT / "screenshots" / "tour"
OUT = ROOT / "labrat_tour.html"

SLIDES = [
    # 0 — Splash
    {
        "splash": True,
        "accent": "#6ee7b7",
    },
    # 1 — Hero: agent answering live
    {
        "svg": "01_agent_response.svg",
        "tag": "Agent in Action",
        "title": "Ask. Get SQL. Get Answers.",
        "subtitle": "Type a question in plain English — the agent writes and runs the SQL for you",
        "body": "Here the agent answers \"How many orders are in the database? What is the total amount?\" It introspects the schema, writes dialect-correct SQL, executes it, and returns the answer — live, in your terminal.",
        "features": [
            "Reads your schema before writing any SQL",
            "Streams SQL character-by-character as it thinks",
            "Runs the query and surfaces the answer",
            "No manual SQL needed",
        ],
        "accent": "#f472b6",
    },
    # 2 — Three-pane overview (after agent ran, all panes populated)
    {
        "svg": "02_main_layout.svg",
        "tag": "Overview",
        "title": "Three-Pane Layout",
        "subtitle": "Chat · SQL whiteboard · Results · Schema — all visible at once",
        "body": "One terminal window keeps every piece of the workflow in sight: the agent's conversation on the left, the SQL it wrote in the center, the results below, and your live schema on the right.",
        "features": [
            "Ctrl+1 → focus AI chat",
            "Ctrl+2 → focus SQL editor",
            "Ctrl+3 → focus results table",
            "Ctrl+4 → focus schema browser",
        ],
        "accent": "#6ee7b7",
    },
    # 3 — SQL editor showing agent's SQL
    {
        "svg": "03_sql_editor.svg",
        "tag": "SQL Whiteboard",
        "title": "The Agent's Whiteboard",
        "subtitle": "Watch SQL stream into the editor as the agent thinks",
        "body": "The SQL editor is not just for you — it's the agent's working space. SQL streams in character-by-character as the agent reasons. You can edit it, run it manually, or ask the agent to revise it.",
        "features": [
            "Agent writes SQL here in real time",
            "Ctrl+Enter → run the query yourself",
            "Edit agent SQL and re-run freely",
            "Full syntax highlighting for every dialect",
        ],
        "accent": "#f59e0b",
    },
    # 4 — Results after agent query
    {
        "svg": "04_results_table.svg",
        "tag": "Results",
        "title": "Query Results",
        "subtitle": "Tabular output from the agent's SQL, ready to explore",
        "body": "After the agent runs its query the results appear here — scrollable, column-typed, row-counted. The agent reads these results to formulate its answer, and you can navigate them independently.",
        "features": [
            "Ctrl+3 → focus and navigate results",
            "Arrow keys to move between cells",
            "Column widths auto-sized to content",
            "Row count shown in pane header",
        ],
        "accent": "#34d399",
    },
    # 5 — Schema browser
    {
        "svg": "05_schema_browser.svg",
        "tag": "Schema",
        "title": "Schema Browser",
        "subtitle": "Live catalog of every table, column, and type",
        "body": "The right pane shows the full database catalog — introspected on connect and always visible. The agent uses this same catalog when deciding which tables to query.",
        "features": [
            "Auto-introspects on connect",
            "Expandable: schemas → tables → columns",
            "Column types shown inline",
            "Ctrl+H to hide for more editor space",
        ],
        "accent": "#818cf8",
    },
    # 6 — Thread manager
    {
        "svg": "06_thread_manager.svg",
        "tag": "Threads",
        "title": "Thread Manager",
        "subtitle": "Separate named sessions for each investigation",
        "body": "Each thread has its own AI conversation, query history, findings, and memories. Switch investigations without losing context — like branches for data work.",
        "features": [
            "Ctrl+T → open thread manager",
            "Create named threads per investigation",
            "Switch without losing history",
            "Each thread has independent AI context",
        ],
        "accent": "#a78bfa",
    },
    # 7 — Findings
    {
        "svg": "07_findings_viewer.svg",
        "tag": "Findings",
        "title": "Pinned Findings",
        "subtitle": "Key insights saved with provenance",
        "body": "When the agent surfaces something important it pins it as a Finding — a structured note with question, answer, and the exact SQL that produced it. Browse all findings with Ctrl+K.",
        "features": [
            "Ctrl+K → open findings viewer",
            "Agent pins insights automatically",
            "Each finding links to source SQL",
            "Persistent per thread across sessions",
        ],
        "accent": "#f472b6",
    },
    # 8 — History
    {
        "svg": "08_history_browser.svg",
        "tag": "History",
        "title": "Query History",
        "subtitle": "Every query logged with timestamps",
        "body": "All executed queries — by you or the agent — are logged automatically. Browse with Ctrl+R, see exactly when each ran, and re-run or copy any past query instantly.",
        "features": [
            "Ctrl+R → open history browser",
            "Timestamped, always-on JSONL log",
            "Browse and re-run past queries",
            "Scoped per thread",
        ],
        "accent": "#fb923c",
    },
    # 9 — Memories
    {
        "svg": "09_memories_viewer.svg",
        "tag": "Memory",
        "title": "Agent Memories",
        "subtitle": "Gets smarter about your data over time",
        "body": "The agent writes memories as it learns — schema patterns, your corrections, business rules. Day-30 LabRat is meaningfully better than day-1. Browse all memories with Ctrl+G.",
        "features": [
            "Ctrl+G → open memories viewer",
            "Agent writes memories automatically",
            "Scoped: global or per-table",
            "Applied to every future query",
        ],
        "accent": "#2dd4bf",
    },
    # 10 — Help
    {
        "svg": "10_help_screen.svg",
        "tag": "Help",
        "title": "Keyboard Shortcuts",
        "subtitle": "Every command one key away",
        "body": "Press F1 at any time to see the complete keyboard reference. Every panel, modal, and command is listed — no mouse required, ever.",
        "features": [
            "F1 → toggle help overlay",
            "All shortcuts in one place",
            "Navigation, panels, and commands",
            "Escape to dismiss",
        ],
        "accent": "#94a3b8",
    },
    # 11 — Schema hidden / focus mode
    {
        "svg": "11_schema_hidden.svg",
        "tag": "Focus Mode",
        "title": "Schema Hidden",
        "subtitle": "More room when you need it",
        "body": "Ctrl+H toggles the schema pane off, expanding the editor and results to fill the terminal. Useful for wide result sets or long queries where you need the horizontal space.",
        "features": [
            "Ctrl+H → toggle schema panel",
            "Editor + results expand to fill",
            "Schema remains loaded in memory",
            "Toggle back instantly",
        ],
        "accent": "#fbbf24",
    },
    # 12 — Final overview
    {
        "svg": "12_final_overview.svg",
        "tag": "Complete",
        "title": "Everything Together",
        "subtitle": "One terminal, one tool, no context switching",
        "body": "LabRat is a fully keyboard-driven AI data agent that lives in your terminal. Connect to any warehouse, ask questions in plain English, and get answers backed by auditable SQL.",
        "features": [
            "7 warehouses: DuckDB, Snowflake, Postgres, BigQuery, Redshift, Trino, MySQL",
            "4 LLM providers: Anthropic, OpenAI-compatible, Bedrock, Vertex",
            "Runs anywhere: local, SSH, CI",
            "github.com/esagduyu/labrat",
        ],
        "accent": "#6ee7b7",
    },
]

CSS = """
  :root {
    --bg: #0a0a0f;
    --surface: #13131a;
    --border: #1e1e2e;
    --text: #e2e8f0;
    --muted: #64748b;
    --accent: #6ee7b7;
    --font-mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
    --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font-sans); height: 100vh; overflow: hidden; user-select: none; }

  header {
    position: fixed; top: 0; left: 0; right: 0; height: 52px;
    background: rgba(10,10,15,0.95); backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between; padding: 0 32px; z-index: 100;
  }
  .header-logo { font-family: var(--font-mono); font-size: 18px; font-weight: 700; color: #fff; letter-spacing: -0.5px; }
  .header-logo span { color: #6ee7b7; }
  .header-tagline { font-family: var(--font-mono); font-size: 11px; color: var(--muted); margin-left: 16px; }
  .header-left { display: flex; align-items: center; }
  .header-nav { display: flex; gap: 8px; }
  .nav-btn { background: var(--surface); border: 1px solid var(--border); color: var(--muted); padding: 6px 18px; border-radius: 6px; font-size: 13px; cursor: pointer; font-family: var(--font-mono); transition: all .15s; }
  .nav-btn:hover { color: var(--text); border-color: #6ee7b7; }
  .nav-btn.primary { background: #6ee7b7; color: #000; border-color: #6ee7b7; font-weight: 700; }
  .nav-btn.primary:hover { opacity: .9; }

  .progress-bar { position: fixed; top: 52px; left: 0; height: 3px; background: var(--accent); transition: width .4s cubic-bezier(.4,0,.2,1), background .4s; z-index: 99; box-shadow: 0 0 10px var(--accent); }

  .slideshow { position: fixed; top: 52px; bottom: 64px; left: 0; right: 0; overflow: hidden; }

  .slide { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 24px 48px; opacity: 0; transform: translateX(50px); pointer-events: none; transition: opacity .4s cubic-bezier(.4,0,.2,1), transform .4s cubic-bezier(.4,0,.2,1); }
  .slide.active { opacity: 1; transform: translateX(0); pointer-events: auto; }
  .slide.exit-left { opacity: 0; transform: translateX(-50px); transition: opacity .3s, transform .3s; }

  .slide-inner { display: grid; grid-template-columns: 1fr 1.8fr; gap: 48px; align-items: center; width: 100%; max-width: 1400px; }

  .slide-tag { display: inline-block; font-family: var(--font-mono); font-size: 10px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent); padding: 3px 10px; border-radius: 4px; margin-bottom: 14px; }
  .slide-text h2 { font-size: 34px; font-weight: 800; line-height: 1.1; letter-spacing: -1px; color: #fff; margin-bottom: 10px; }
  .slide-subtitle { font-size: 14px; color: var(--accent); font-weight: 500; margin-bottom: 14px; line-height: 1.5; }
  .slide-body { font-size: 13px; color: var(--muted); line-height: 1.7; margin-bottom: 20px; }
  .slide-features { list-style: none; display: flex; flex-direction: column; gap: 7px; }
  .slide-features li { font-family: var(--font-mono); font-size: 11.5px; color: #94a3b8; display: flex; align-items: flex-start; gap: 8px; line-height: 1.5; }
  .slide-features li::before { content: '›'; color: var(--accent); flex-shrink: 0; font-size: 14px; line-height: 1.4; }

  .slide-preview { position: relative; border-radius: 10px; overflow: hidden; border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent); box-shadow: 0 0 0 1px var(--border), 0 20px 60px rgba(0,0,0,.6), 0 0 80px color-mix(in srgb, var(--accent) 8%, transparent); background: #0d1117; max-height: calc(100vh - 180px); display: flex; flex-direction: column; transition: box-shadow .3s; }
  .slide-preview:hover { box-shadow: 0 0 0 1px var(--border), 0 30px 80px rgba(0,0,0,.7), 0 0 120px color-mix(in srgb, var(--accent) 16%, transparent); }
  .preview-titlebar { background: #161b22; height: 28px; display: flex; align-items: center; padding: 0 12px; gap: 6px; flex-shrink: 0; border-bottom: 1px solid var(--border); }
  .tb-dot { width: 10px; height: 10px; border-radius: 50%; }
  .tb-title { font-family: var(--font-mono); font-size: 10px; color: var(--muted); margin-left: 8px; }
  .slide-img { width: 100%; height: auto; display: block; }

  .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; height: 64px; background: rgba(10,10,15,0.95); backdrop-filter: blur(12px); border-top: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; padding: 0 32px; z-index: 100; }
  .dots { display: flex; gap: 5px; align-items: center; }
  .dot { width: 6px; height: 6px; border-radius: 3px; background: var(--border); border: none; cursor: pointer; padding: 0; transition: all .2s; }
  .dot:hover { background: var(--muted); }
  .dot.active { width: 22px; background: var(--accent); box-shadow: 0 0 8px var(--accent); }
  .arrow-btns { display: flex; gap: 8px; }
  .arrow-btn { background: var(--surface); border: 1px solid var(--border); color: var(--muted); width: 38px; height: 38px; border-radius: 8px; font-size: 16px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all .15s; }
  .arrow-btn:hover { color: #fff; border-color: var(--accent); }
  .arrow-btn:disabled { opacity: .3; cursor: not-allowed; }
  .key-hint { font-family: var(--font-mono); font-size: 11px; color: var(--muted); }
  .key-hint kbd { background: var(--surface); border: 1px solid var(--border); border-radius: 3px; padding: 1px 5px; font-family: inherit; }
  .slide-number { position: absolute; bottom: 4px; right: 10px; font-family: var(--font-mono); font-size: 10px; color: var(--muted); opacity: .35; }

  /* Splash slide */
  .splash-inner { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; width: 100%; max-width: 820px; }
  .splash-hero { font-family: var(--font-mono); font-size: clamp(56px,7vw,100px); font-weight: 900; line-height: 1; letter-spacing: -3px; color: #fff; margin-bottom: 10px; }
  .splash-hero span { color: #6ee7b7; }
  .splash-tagline { font-family: var(--font-mono); font-size: 13px; color: var(--muted); margin-bottom: 40px; letter-spacing: 2px; text-transform: uppercase; }
  .splash-stats { display: flex; gap: 56px; margin-bottom: 40px; flex-wrap: wrap; justify-content: center; }
  .stat-num { font-family: var(--font-mono); font-size: 32px; font-weight: 800; color: #6ee7b7; line-height: 1; }
  .stat-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1.5px; margin-top: 4px; }
  .splash-desc { font-size: 15px; color: var(--muted); line-height: 1.7; max-width: 520px; }
"""

JS = """
const slides = document.querySelectorAll('.slide');
const dots = document.querySelectorAll('.dot');
const prev = document.getElementById('prev');
const next = document.getElementById('next');
const progress = document.getElementById('progress');
let current = 0;

function applyAccent(n) {
  const accent = slides[n].dataset.accent || '#6ee7b7';
  progress.style.width = ((n + 1) / slides.length * 100) + '%';
  progress.style.background = accent;
  progress.style.boxShadow = '0 0 10px ' + accent;
}

function goTo(n, dir) {
  if (n < 0 || n >= slides.length) return;
  const old = current;
  // Exit old slide
  slides[old].classList.remove('active');
  if (dir > 0) {
    slides[old].classList.add('exit-left');
    setTimeout(() => slides[old].classList.remove('exit-left'), 400);
  }
  dots[old].classList.remove('active');
  // Enter new slide
  current = n;
  slides[current].classList.add('active');
  dots[current].classList.add('active');
  applyAccent(current);
  prev.disabled = current === 0;
  next.disabled = current === slides.length - 1;
}

// Hard reset: clear any DOM state restored by browser cache before initializing
slides.forEach(s => s.classList.remove('active', 'exit-left'));
dots.forEach(d => d.classList.remove('active'));
slides[0].classList.add('active');
dots[0].classList.add('active');
applyAccent(0);
prev.disabled = true;
next.disabled = slides.length <= 1;

prev.onclick = () => goTo(current - 1, -1);
next.onclick = () => goTo(current + 1, 1);
dots.forEach((d, i) => d.onclick = () => goTo(i, i > current ? 1 : -1));

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); goTo(current + 1, 1); }
  if (e.key === 'ArrowLeft') { e.preventDefault(); goTo(current - 1, -1); }
  if (e.key === 'Home') { e.preventDefault(); goTo(0, -1); }
  if (e.key === 'End') { e.preventDefault(); goTo(slides.length - 1, 1); }
});
"""


def build_splash_html(accent: str) -> str:
    return f"""
<div class="slide" data-accent="{accent}">
  <div class="slide-inner" style="display:flex;align-items:center;justify-content:center">
    <div class="splash-inner">
      <div class="splash-hero">🐀 <span>LabRat</span></div>
      <div class="splash-tagline">Terminal-native AI data agent</div>
      <div class="splash-stats">
        <div><div class="stat-num">32</div><div class="stat-label">Milestones</div></div>
        <div><div class="stat-num">7</div><div class="stat-label">Warehouses</div></div>
        <div><div class="stat-num">500</div><div class="stat-label">Tests passing</div></div>
        <div><div class="stat-num">4</div><div class="stat-label">LLM providers</div></div>
      </div>
      <div class="splash-desc">Connect to DuckDB, Snowflake, Postgres, or MySQL. Ask questions in plain English. Get answers backed by real SQL — all without leaving your terminal.</div>
    </div>
  </div>
</div>"""


def svg_to_data_uri(svg_path: Path) -> str:
    data = svg_path.read_bytes()
    b64 = base64.b64encode(data).decode()
    return f"data:image/svg+xml;base64,{b64}"


def build_slide_html(s: dict, idx: int) -> str:
    if s.get("splash"):
        return build_splash_html(s["accent"])
    svg_path = SVG_DIR / s["svg"]
    src = svg_to_data_uri(svg_path)
    accent = s["accent"]
    features_html = "\n".join(f"<li>{f}</li>" for f in s["features"])
    alt = s["title"].replace('"', "&quot;")
    num = idx + 1
    return f"""
<div class="slide" data-accent="{accent}">
  <div class="slide-inner">
    <div class="slide-text">
      <div class="slide-tag" style="color:{accent};background:color-mix(in srgb,{accent} 12%,transparent);border-color:color-mix(in srgb,{accent} 30%,transparent)">{s["tag"]}</div>
      <h2>{s["title"]}</h2>
      <div class="slide-subtitle" style="color:{accent}">{s["subtitle"]}</div>
      <div class="slide-body">{s["body"]}</div>
      <ul class="slide-features">
{features_html}
      </ul>
    </div>
    <div class="slide-preview" style="border-color:color-mix(in srgb,{accent} 25%,transparent);box-shadow:0 0 0 1px #1e1e2e,0 20px 60px rgba(0,0,0,.6),0 0 80px color-mix(in srgb,{accent} 8%,transparent)">
      <div class="preview-titlebar">
        <div class="tb-dot" style="background:#ff5f57"></div>
        <div class="tb-dot" style="background:#ffbd2e"></div>
        <div class="tb-dot" style="background:#28c840"></div>
        <div class="tb-title">LabRat — {s["tag"]}</div>
      </div>
      <img class="slide-img" src="{src}" alt="{alt}">
      <div class="slide-number">{num}/{len(SLIDES)}</div>
    </div>
  </div>
</div>"""


def build() -> None:
    slides_html = "\n".join(build_slide_html(s, i) for i, s in enumerate(SLIDES))
    dots_html = "\n".join(
        f'<button class="dot" aria-label="Slide {i+1}"></button>' for i in range(len(SLIDES))
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🐀 LabRat — Feature Tour</title>
<style>{CSS}</style>
</head>
<body>

<header>
  <div class="header-left">
    <div class="header-logo">🐀 <span>LabRat</span></div>
    <div class="header-tagline">terminal-native data agent</div>
  </div>
  <div class="header-nav">
    <button class="nav-btn" onclick="window.open('https://github.com/esagduyu/labrat','_blank')">★ GitHub</button>
    <button class="nav-btn primary">pip install labrat</button>
  </div>
</header>

<div class="progress-bar" id="progress" style="width:{100/len(SLIDES):.1f}%"></div>

<div class="slideshow" id="slideshow">
{slides_html}
</div>

<nav class="bottom-nav">
  <button class="arrow-btn" id="prev" disabled>←</button>
  <div class="dots">{dots_html}</div>
  <button class="arrow-btn" id="next">→</button>
</nav>

<script>{JS}</script>
</body>
</html>"""

    OUT.write_text(html)
    size_kb = OUT.stat().st_size // 1024
    print(f"✅  Built {OUT.name} ({size_kb}KB, {len(SLIDES)} slides)")


if __name__ == "__main__":
    build()
