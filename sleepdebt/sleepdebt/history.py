"""Backfill and report sleep-debt history.

Not a calibration step — the tiers are fixed in config. This is here so the
curve can be looked at: how often debt has actually reached each tier, and
therefore how often the alerts would have fired.

    python -m sleepdebt.history                    # live, needs credentials
    python -m sleepdebt.history --from-csv f.csv   # re-run on an earlier export
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from . import config
from .alerting import tier_for
from .debt import Night, debt_series, sessions_to_nights
from .plot import debt_svg


def load_nights_live(cfg) -> Dict[date, Night]:
    from .oura import fetch_sessions
    end = date.today()
    start = end - timedelta(days=int(cfg.raw["oura"]["backfill_days"]))
    print(f"fetching {start} -> {end} ...", file=sys.stderr)
    sessions = fetch_sessions(cfg, start, end)
    print(f"  {len(sessions)} sleep sessions", file=sys.stderr)
    return sessions_to_nights(sessions, count_types=cfg.count_session_types)


def load_nights_csv(path: Path) -> Dict[date, Night]:
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


def would_have_fired(series, tiers, *, consecutive_days: int,
                     cooldown_days: int) -> List[dict]:
    """Replay the tier ladder over history. Same rules as live alerting."""
    fires, streak, last_tier, last_day = [], 0, None, None
    for p in series:
        if not p.sufficient:
            continue
        t = tier_for(p.debt_hours, tiers)
        if t is None:
            streak, last_tier, last_day = 0, None, None
            continue
        streak += 1
        if streak < consecutive_days:
            continue
        escalating = last_tier is None or t["hours"] > last_tier
        cooled = last_day is not None and (p.day - last_day).days >= cooldown_days
        if escalating or (t["hours"] == last_tier and cooled):
            fires.append({"day": p.day.isoformat(), "tier": t["hours"],
                          "label": t["label"], "debt": round(p.debt_hours, 2)})
            last_tier, last_day = t["hours"], p.day
    return fires


def run(cfg, nights: Dict[date, Night], outdir: Path) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    if not nights:
        raise SystemExit("no sleep data - nothing to report on")
    start, end = min(nights), max(nights)
    series = debt_series(nights, start, end, baseline_need=cfg.baseline_need,
                         window_days=cfg.window_days, surplus_cap=cfg.surplus_cap,
                         min_observed_days=cfg.min_observed_days)
    tiers = cfg.tiers

    nightly = outdir / "nightly.csv"
    with open(nightly, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["day", "hours", "sessions", "rhr", "deficit_hours",
                    "debt_hours", "tier_hours", "observed_days", "window_days",
                    "sufficient"])
        for p in series:
            n = nights.get(p.day)
            t = tier_for(p.debt_hours, tiers) if p.sufficient else None
            w.writerow([p.day.isoformat(),
                        f"{n.hours:.4f}" if n else "",
                        n.sessions if n else "",
                        f"{n.rhr:.1f}" if n and n.rhr is not None else "",
                        f"{cfg.baseline_need - n.hours:.4f}" if n else "",
                        f"{p.debt_hours:.4f}",
                        f"{t['hours']:g}" if t else "",
                        p.observed_days, p.window_days, int(p.sufficient)])

    obs = [p for p in series if p.sufficient]
    days_at = {t["hours"]: sum(1 for p in obs
                               if (tf := tier_for(p.debt_hours, tiers))
                               and tf["hours"] == t["hours"]) for t in tiers}
    fires = would_have_fired(series, tiers, consecutive_days=cfg.consecutive_days,
                             cooldown_days=cfg.cooldown_days)
    years = max(len(obs) / 365.25, 1e-9)

    (outdir / "sleep_debt.svg").write_text(debt_svg(
        [p.day for p in series],
        [p.debt_hours if p.sufficient else None for p in series],
        threshold=tiers[-1]["hours"], episodes=(), lead_in_days=0,
        title=f"Sleep debt - {cfg.window_days}-day window, baseline "
              f"{cfg.baseline_need:g} h, tiers "
              + "/".join(f"{t['hours']:g}" for t in tiers) + " h"))

    report = {
        "range": [start.isoformat(), end.isoformat()],
        "nights_with_data": len(nights),
        "calendar_days": (end - start).days + 1,
        "coverage": round(len(nights) / ((end - start).days + 1), 3),
        "baseline_need_hours": cfg.baseline_need,
        "window_days": cfg.window_days,
        "tiers": tiers,
        "days_at_tier": {str(k): v for k, v in days_at.items()},
        "peak_debt_hours": round(max((p.debt_hours for p in obs), default=0), 2),
        "alerts_that_would_have_fired": fires,
        "alerts_per_year": round(len(fires) / years, 2),
        "outputs": {"nightly_csv": str(nightly),
                    "plot_svg": str(outdir / "sleep_debt.svg")},
    }
    (outdir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def _print(r: dict) -> None:
    p = print
    p("\n" + "=" * 66)
    p(f"  SLEEP DEBT HISTORY  {r['range'][0]} -> {r['range'][1]}")
    p("=" * 66)
    p(f"  {r['nights_with_data']} nights across {r['calendar_days']} calendar days "
      f"({r['coverage']*100:.0f}% coverage)")
    p(f"  baseline {r['baseline_need_hours']:g} h - {r['window_days']}-day window")
    p(f"  peak debt {r['peak_debt_hours']:.1f} h\n")
    p("  DAYS SPENT AT EACH TIER")
    for t in r["tiers"]:
        n = r["days_at_tier"].get(str(t["hours"]), 0)
        p(f"    {t['hours']:>5g} h  {t['label']:<10} {n:>4} days")
    p(f"\n  ALERTS THAT WOULD HAVE FIRED: {len(r['alerts_that_would_have_fired'])} "
      f"(~{r['alerts_per_year']}/year)")
    for f in r["alerts_that_would_have_fired"][-8:]:
        p(f"    {f['day']}  {f['tier']:g} h {f['label']:<10} debt {f['debt']:.1f} h")
    if r["alerts_per_year"] > 12:
        p("\n  That is more than one a month. Alerts at that rate get muted.")
        p("  Consider raising the tiers in config.yaml.")
    p(f"\n  files: {r['outputs']['nightly_csv']}")
    p(f"         {r['outputs']['plot_svg']}\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-csv", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    cfg = config.load()
    for w in cfg.validate():
        print(f"warning: {w}", file=sys.stderr)
    nights = load_nights_csv(a.from_csv) if a.from_csv else load_nights_live(cfg)
    outdir = a.out or (cfg.path / "history")
    _print(run(cfg, nights, Path(outdir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
