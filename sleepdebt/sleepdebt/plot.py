"""Dependency-free SVG line chart for the calibration report.

matplotlib is optional and often absent on a server; an SVG built from stdlib
always renders and diffs cleanly in git.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional, Sequence, Tuple

W, H = 1100, 420
ML, MR, MT, MB = 62, 20, 26, 46


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def debt_svg(days: Sequence[date], debt: Sequence[Optional[float]], *,
             threshold: Optional[float] = None,
             episodes: Sequence[Tuple[date, str]] = (),
             lead_in_days: int = 21, title: str = "Sleep debt") -> str:
    pts = [(i, v) for i, v in enumerate(debt) if v is not None]
    if not pts or not days:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"></svg>'
    vals = [v for _, v in pts]
    lo, hi = min(min(vals), 0.0), max(max(vals), threshold or 0.0)
    span = (hi - lo) or 1.0
    lo -= span * 0.08
    hi += span * 0.08
    iw, ih = W - ML - MR, H - MT - MB
    x = lambda i: ML + (i / max(len(days) - 1, 1)) * iw
    y = lambda v: MT + ih - ((v - lo) / (hi - lo)) * ih

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="ui-monospace,Menlo,monospace">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'<text x="{ML}" y="16" font-size="13" font-weight="600" fill="#111">{_esc(title)}</text>']

    # 21-day lead-in bands, drawn under everything
    index = {d: i for i, d in enumerate(days)}
    for ed, label in episodes:
        s = index.get(ed - timedelta(days=lead_in_days))
        e = index.get(ed)
        if s is None or e is None:
            continue
        out.append(f'<rect x="{x(s):.1f}" y="{MT}" width="{x(e)-x(s):.1f}" height="{ih}" '
                   f'fill="#d94f45" opacity="0.10"/>')
        out.append(f'<line x1="{x(e):.1f}" y1="{MT}" x2="{x(e):.1f}" y2="{MT+ih}" '
                   f'stroke="#d94f45" stroke-width="1.5"/>')
        out.append(f'<text x="{x(e)+4:.1f}" y="{MT+12}" font-size="10" fill="#d94f45">{_esc(label)}</text>')

    # gridlines
    steps = 5
    for k in range(steps + 1):
        v = lo + (hi - lo) * k / steps
        yy = y(v)
        out.append(f'<line x1="{ML}" x2="{W-MR}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#e6e6e6"/>')
        out.append(f'<text x="{ML-8}" y="{yy+3.5:.1f}" font-size="10" fill="#666" '
                   f'text-anchor="end">{v:.0f}h</text>')
    out.append(f'<line x1="{ML}" x2="{W-MR}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#999"/>')

    if threshold is not None:
        out.append(f'<line x1="{ML}" x2="{W-MR}" y1="{y(threshold):.1f}" y2="{y(threshold):.1f}" '
                   f'stroke="#1f7a8c" stroke-width="2" stroke-dasharray="6 4"/>')
        out.append(f'<text x="{W-MR}" y="{y(threshold)-5:.1f}" font-size="10" fill="#1f7a8c" '
                   f'text-anchor="end">threshold {threshold:g} h</text>')

    # the series, broken at gaps so missing days are visibly missing
    seg, path = [], []
    prev = None
    for i, v in pts:
        if prev is not None and i != prev + 1:
            path.append(seg); seg = []
        seg.append(f"{x(i):.1f},{y(v):.1f}")
        prev = i
    path.append(seg)
    for s in path:
        if len(s) > 1:
            out.append(f'<polyline points="{" ".join(s)}" fill="none" stroke="#1f4e5f" stroke-width="1.6"/>')
        elif s:
            cx, cy = s[0].split(",")
            out.append(f'<circle cx="{cx}" cy="{cy}" r="1.8" fill="#1f4e5f"/>')

    # x labels, roughly monthly
    step = max(len(days) // 12, 1)
    for i in range(0, len(days), step):
        out.append(f'<text x="{x(i):.1f}" y="{H-MB+16}" font-size="9" fill="#666" '
                   f'text-anchor="middle">{days[i].isoformat()[:7]}</text>')
    out.append(f'<text x="{ML}" y="{H-8}" font-size="10" fill="#888">'
               f'shaded = {lead_in_days}-day lead-in before an annotated episode; '
               f'gaps = days with no data (excluded, never imputed)</text>')
    out.append("</svg>")
    return "\n".join(out)
