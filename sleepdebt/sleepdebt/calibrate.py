"""Calibration — run this before any alerting goes live.

Backfills all available history, computes the debt curve, writes a CSV and an
SVG plot, reports what the curve looked like in the lead-in to each annotated
episode, and sweeps candidate thresholds to recommend one fitted to this
person's actual history.

The point is not to produce a number. It is to show the trade-off: a threshold
that fires before every episode but also fires eleven times a year is worse than
useless, because a system that cries wolf gets muted and then deleted.

    python -m sleepdebt.calibrate                  # live, needs Oura credentials
    python -m sleepdebt.calibrate --from-csv f.csv # re-run on an earlier export
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .debt import Night, debt_series, sessions_to_nights
from .plot import debt_svg


# ───────────────────────── data acquisition ─────────────────────────

def load_nights_live(cfg) -> Dict[date, Night]:
    from .oura import fetch_sessions
    end = date.today()
    start = end - timedelta(days=int(cfg.raw["oura"]["backfill_days"]))
    print(f"fetching {start} → {end} …", file=sys.stderr)
    sessions = fetch_sessions(cfg, start, end)
    print(f"  {len(sessions)} sleep sessions", file=sys.stderr)
    return sessions_to_nights(sessions, count_types=cfg.count_session_types)


def load_nights_csv(path: Path) -> Dict[date, Night]:
    """Re-run calibration from a previous nightly CSV, so the sweep can be
    retuned without re-hitting the API."""
    out: Dict[date, Night] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("hours"):
                continue
            d = date.fromisoformat(row["day"])
            out[d] = Night(day=d, hours=float(row["hours"]),
                           sessions=int(row.get("sessions") or 1),
                           rhr=float(row["rhr"]) if row.get("rhr") else None)
    return out


# ───────────────────────── threshold sweep ─────────────────────────

def _fires(series, threshold: float, consecutive: int) -> List[date]:
    """Days an alert would have fired, applying the consecutive-day rule.
    Cooldown is deliberately excluded — this measures the raw trigger rate."""
    days, streak = [], 0
    for p in series:
        if p.sufficient and p.debt_hours >= threshold:
            streak += 1
            if streak >= consecutive:
                days.append(p.day)
        else:
            streak = 0
    return days


def sweep(series, episodes: Sequence[Tuple[date, str]], *, consecutive: int,
          lead_in: int, lo: float, hi: float, step: float) -> List[dict]:
    """For each candidate threshold: which episodes it would have anticipated,
    and how often it fires the rest of the time."""
    lead_windows = [(ed - timedelta(days=lead_in), ed, label) for ed, label in episodes]
    total_days = len([p for p in series if p.sufficient]) or 1
    rows = []
    t = lo
    while t <= hi + 1e-9:
        fires = _fires(series, t, consecutive)
        caught, warn_days = [], {}
        for s, e, label in lead_windows:
            hit = [d for d in fires if s <= d <= e]
            if hit:
                caught.append(label)
                warn_days[label] = (e - min(hit)).days
        outside = [d for d in fires
                   if not any(s <= d <= e for s, e, _ in lead_windows)]
        # collapse runs into episodes-of-alerting rather than counting each day
        bursts, prev = 0, None
        for d in sorted(outside):
            if prev is None or (d - prev).days > 1:
                bursts += 1
            prev = d
        rows.append({
            "threshold_hours": round(t, 2),
            "episodes_caught": len(caught),
            "episodes_total": len(lead_windows),
            "caught_labels": ";".join(caught),
            "median_warning_days": (sorted(warn_days.values())[len(warn_days)//2]
                                    if warn_days else None),
            "false_alert_bursts": bursts,
            "false_alerts_per_year": round(bursts / (total_days / 365.25), 2) if total_days else None,
        })
        t += step
    return rows


def recommend(rows: List[dict], episodes_total: int) -> Optional[dict]:
    """Lowest false-alarm rate among thresholds that catch every episode; among
    ties, the lowest threshold, since earlier warning is worth more than a
    marginally tidier alert log."""
    full = [r for r in rows if r["episodes_caught"] == episodes_total and episodes_total > 0]
    if not full:
        return None
    best = min(full, key=lambda r: (r["false_alert_bursts"], r["threshold_hours"]))
    return best


# ───────────────────────────── report ─────────────────────────────

def run(cfg, nights: Dict[date, Night], outdir: Path) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    cal = cfg.raw.get("calibration", {})
    lead_in = int(cal.get("lead_in_days", 21))
    episodes: List[Tuple[date, str]] = []
    for e in cal.get("episodes", []) or []:
        try:
            episodes.append((date.fromisoformat(str(e["date"])), str(e.get("label", "episode"))))
        except (KeyError, ValueError):
            print(f"skipping malformed episode entry: {e!r}", file=sys.stderr)

    if not nights:
        raise SystemExit("no sleep data — nothing to calibrate on")
    start, end = min(nights), max(nights)
    series = debt_series(nights, start, end, baseline_need=cfg.baseline_need,
                         window_days=cfg.window_days, surplus_cap=cfg.surplus_cap,
                         min_observed_days=cfg.min_observed_days)

    # ---- nightly CSV ----
    nightly = outdir / "nightly.csv"
    with open(nightly, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["day", "hours", "sessions", "rhr", "deficit_hours",
                    "debt_hours", "observed_days", "window_days", "sufficient"])
        for p in series:
            n = nights.get(p.day)
            w.writerow([p.day.isoformat(),
                        f"{n.hours:.4f}" if n else "",
                        n.sessions if n else "",
                        f"{n.rhr:.1f}" if n and n.rhr is not None else "",
                        f"{cfg.baseline_need - n.hours:.4f}" if n else "",
                        f"{p.debt_hours:.4f}", p.observed_days, p.window_days,
                        int(p.sufficient)])

    # ---- sweep ----
    sw = cal.get("sweep", {})
    rows = sweep(series, episodes, consecutive=cfg.consecutive_days, lead_in=lead_in,
                 lo=float(sw.get("min_hours", 4)), hi=float(sw.get("max_hours", 30)),
                 step=float(sw.get("step_hours", 0.5)))
    sweep_csv = outdir / "threshold_sweep.csv"
    with open(sweep_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    rec = recommend(rows, len(episodes))

    # ---- lead-in detail per episode ----
    by_day = {p.day: p for p in series}
    lead_detail = []
    for ed, label in episodes:
        pts = [by_day[ed - timedelta(days=k)] for k in range(lead_in, -1, -1)
               if (ed - timedelta(days=k)) in by_day]
        if not pts:
            lead_detail.append({"label": label, "date": ed.isoformat(),
                                "note": "no data in the lead-in window"})
            continue
        obs = [p for p in pts if p.sufficient]
        lead_detail.append({
            "label": label, "date": ed.isoformat(),
            "days_with_data": len(obs),
            "peak_debt_hours": round(max((p.debt_hours for p in obs), default=0), 2),
            "debt_on_the_day": round(by_day[ed].debt_hours, 2) if ed in by_day else None,
            "mean_sleep_hours": (round(sum(nights[d].hours for d in
                                 (ed - timedelta(days=k) for k in range(lead_in, -1, -1))
                                 if d in nights) / max(len([d for d in
                                 (ed - timedelta(days=k) for k in range(lead_in, -1, -1))
                                 if d in nights]), 1), 2)),
        })

    # ---- plot ----
    days = [p.day for p in series]
    svg = debt_svg(days, [p.debt_hours if p.sufficient else None for p in series],
                   threshold=rec["threshold_hours"] if rec else cfg.threshold_hours,
                   episodes=episodes, lead_in_days=lead_in,
                   title=f"Sleep debt — {cfg.window_days}-day trailing window, "
                         f"baseline {cfg.baseline_need:g} h")
    (outdir / "sleep_debt.svg").write_text(svg)

    report = {
        "range": [start.isoformat(), end.isoformat()],
        "nights_with_data": len(nights),
        "calendar_days": (end - start).days + 1,
        "coverage": round(len(nights) / ((end - start).days + 1), 3),
        "baseline_need_hours": cfg.baseline_need,
        "window_days": cfg.window_days,
        "episodes": lead_detail,
        "recommendation": rec,
        "outputs": {"nightly_csv": str(nightly), "sweep_csv": str(sweep_csv),
                    "plot_svg": str(outdir / "sleep_debt.svg")},
    }
    (outdir / "report.json").write_text(json.dumps(report, indent=2))
    return report


def _print_report(r: dict, cfg) -> None:
    p = print
    p("\n" + "=" * 68)
    p(f"  CALIBRATION  {r['range'][0]} → {r['range'][1]}")
    p("=" * 68)
    p(f"  {r['nights_with_data']} nights of data across {r['calendar_days']} "
      f"calendar days ({r['coverage']*100:.0f}% coverage)")
    p(f"  baseline {r['baseline_need_hours']:g} h · {r['window_days']}-day window\n")
    p("  LEAD-IN TO ANNOTATED EPISODES")
    for e in r["episodes"]:
        if "note" in e:
            p(f"    {e['label']:<20} {e['date']}   {e['note']}")
        else:
            p(f"    {e['label']:<20} {e['date']}   peak debt "
              f"{e['peak_debt_hours']:>6.1f} h · on the day "
              f"{e['debt_on_the_day']:>6.1f} h · mean sleep "
              f"{e['mean_sleep_hours']:.1f} h over {e['days_with_data']} days")
    rec = r["recommendation"]
    p("\n  RECOMMENDED THRESHOLD")
    if not rec:
        p("    None. No threshold in the sweep caught every annotated episode —")
        p("    either the episodes are not preceded by sleep debt in this data,")
        p("    or the dates need checking. Do NOT fall back to a generic number;")
        p("    read threshold_sweep.csv and decide deliberately.")
    else:
        p(f"    {rec['threshold_hours']:g} h  — catches {rec['episodes_caught']}/"
          f"{rec['episodes_total']} episodes"
          + (f", median {rec['median_warning_days']} days' warning"
             if rec.get("median_warning_days") is not None else "")
          + f", ~{rec['false_alerts_per_year']} false alerts/year")
        p(f"\n    Set alerting.threshold_hours: {rec['threshold_hours']:g} in config.yaml,")
        p("    then add `_calibrated: true` to confirm you reviewed this.")
    p(f"\n  files: {r['outputs']['nightly_csv']}")
    p(f"         {r['outputs']['sweep_csv']}")
    p(f"         {r['outputs']['plot_svg']}\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-csv", type=Path, help="re-run on a previous nightly.csv")
    ap.add_argument("--out", type=Path, help="output directory")
    a = ap.parse_args(argv)
    cfg = config.load()
    for w in cfg.validate():
        print(f"warning: {w}", file=sys.stderr)
    nights = load_nights_csv(a.from_csv) if a.from_csv else load_nights_live(cfg)
    outdir = a.out or (cfg.path / str(cfg.raw.get("calibration", {})
                                      .get("output_dir", "./calibration")))
    _print_report(run(cfg, nights, Path(outdir)), cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
