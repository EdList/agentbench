"""HTML report generation from JSON test reports."""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path


def generate_html_report(json_path: Path, output_path: Path) -> None:
    """Read a JSON report and produce a self-contained HTML file."""
    data = json.loads(json_path.read_text())
    rendered = _render_html(data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")


def _render_html(data: dict) -> str:
    total_tests = data.get("total_tests", 0)
    passed = data.get("passed", 0)
    failed = data.get("failed", 0)
    duration_ms = data.get("duration_ms", 0)
    duration_s = duration_ms / 1000 if duration_ms else 0
    suites = data.get("suites", [])

    status_color = "#22c55e" if failed == 0 else "#ef4444"
    status_text = "PASSED" if failed == 0 else "FAILED"

    suites_html = ""
    for idx, suite in enumerate(suites):
        suites_html += _render_suite(suite, idx)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentBench Report</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
          Helvetica, Arial, sans-serif;
          background: #f8fafc; color: #1e293b; line-height: 1.6; padding: 2rem; }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  header {{ background: {status_color}; color: #fff; border-radius: 12px; padding: 2rem;
            margin-bottom: 2rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,.1); }}
  header h1 {{ font-size: 1.75rem; margin-bottom: .25rem; }}
  header .subtitle {{ opacity: .85; font-size: .95rem; }}
  .stats {{ display: flex; gap: 1.5rem; margin-top: 1rem; flex-wrap: wrap; }}
  .stat {{ background: rgba(255,255,255,.2); border-radius: 8px; padding: .75rem 1.25rem;
           font-weight: 600; }}
  .stat span {{ font-size: 1.5rem; display: block; }}
  .suite {{ background: #fff; border-radius: 12px; margin-bottom: 1rem;
            box-shadow: 0 1px 3px rgba(0,0,0,.08); overflow: hidden; }}
  .suite-header {{ padding: 1rem 1.5rem; cursor: pointer; display: flex;
                   justify-content: space-between; align-items: center;
                   border-bottom: 1px solid #e2e8f0; user-select: none; }}
  .suite-header:hover {{ background: #f1f5f9; }}
  .suite-header h2 {{ font-size: 1.1rem; }}
  .suite-badge {{ font-size: .8rem; padding: .25rem .75rem; border-radius: 9999px;
                  font-weight: 600; }}
  .badge-pass {{ background: #dcfce7; color: #166534; }}
  .badge-fail {{ background: #fee2e2; color: #991b1b; }}
  .suite-body {{ padding: 0; }}
  .suite-body.collapsed {{ display: none; }}
  .test-row {{ padding: .75rem 1.5rem; border-bottom: 1px solid #f1f5f9;
              display: flex; align-items: flex-start; gap: .75rem; }}
  .test-row:last-child {{ border-bottom: none; }}
  .test-icon {{ font-size: 1.1rem; flex-shrink: 0; margin-top: .1rem; }}
  .test-info {{ flex: 1; }}
  .test-name {{ font-weight: 600; }}
  .test-duration {{ color: #94a3b8; font-size: .85rem; }}
  .test-error {{ background: #fef2f2; border-left: 3px solid #ef4444; padding: .5rem .75rem;
                 margin-top: .5rem; border-radius: 4px; font-size: .85rem; color: #991b1b; }}
  .assertions {{ margin-top: .35rem; }}
  .assertion {{ font-size: .85rem; color: #64748b; }}
  .assertion.pass {{ color: #16a34a; }}
  .assertion.fail {{ color: #dc2626; }}
  footer {{ text-align: center; color: #94a3b8; font-size: .85rem; margin-top: 2rem; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>🧪 AgentBench Report</h1>
    <p class="subtitle">{html.escape(status_text)} &middot;
    Generated {html.escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</p>
    <div class="stats">
      <div class="stat"><span>{passed}</span>Passed</div>
      <div class="stat"><span>{failed}</span>Failed</div>
      <div class="stat"><span>{total_tests}</span>Total</div>
      <div class="stat"><span>{duration_s:.1f}s</span>Duration</div>
    </div>
  </header>

  <section>
    {suites_html}
  </section>

  <footer>AgentBench &mdash; Behavioral Testing for AI Agents</footer>
</div>

<script>
document.querySelectorAll('.suite-header').forEach(function(header) {{
  header.addEventListener('click', function() {{
    var body = header.nextElementSibling;
    body.classList.toggle('collapsed');
  }});
}});
</script>
</body>
</html>"""


def _render_suite(suite: dict, idx: int) -> str:
    name = html.escape(suite.get("name", f"Suite {idx + 1}"))
    passed = suite.get("passed", 0)
    failed = suite.get("failed", 0)
    total = passed + failed
    badge_class = "badge-pass" if failed == 0 else "badge-fail"
    badge_text = f"{passed} passed" if failed == 0 else f"{failed} failed / {total} total"

    tests_html = ""
    for test in suite.get("tests", []):
        tests_html += _render_test(test)

    collapsed = "" if failed > 0 else " collapsed"

    return f"""\
<div class="suite">
  <div class="suite-header">
    <h2>{name}</h2>
    <span class="suite-badge {badge_class}">{badge_text}</span>
  </div>
  <div class="suite-body{collapsed}">
    {tests_html}
  </div>
</div>"""


def _render_test(test: dict) -> str:
    name = html.escape(test.get("name", "unknown"))
    passed = test.get("passed", False)
    duration_ms = test.get("duration_ms", 0)
    error = test.get("error")

    icon = "✅" if passed else "❌"
    duration_text = f" ({duration_ms / 1000:.1f}s)" if duration_ms else ""

    assertions_html = ""
    for a in test.get("assertions", []):
        a_passed = a.get("passed", False)
        a_msg = html.escape(a.get("message", ""))
        a_type = html.escape(a.get("type", ""))
        cls = "pass" if a_passed else "fail"
        a_icon = "✓" if a_passed else "✗"
        assertions_html += f'<div class="assertion {cls}">{a_icon} {a_msg} [{a_type}]</div>'

    error_html = ""
    if error:
        error_html = f'<div class="test-error">{html.escape(error)}</div>'

    return f"""\
<div class="test-row">
  <div class="test-icon">{icon}</div>
  <div class="test-info">
    <div class="test-name">{name}</div>
    <div class="test-duration">{duration_text}</div>
    {assertions_html}
    {error_html}
  </div>
</div>"""
