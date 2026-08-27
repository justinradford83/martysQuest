"""Sleep-debt computation.

Pure functions over plain data — no I/O, no config loading, no network — so the
arithmetic that drives an alert can be tested directly.

    nightly_deficit(d) = baseline_need - total_sleep(d)
    sleep_debt         = sum(nightly_deficit(d) for observed d in window)

Days absent from the API are excluded from the window rather than imputed, so a
sparse window sums fewer terms. That means missing data pushes debt DOWN, not
up: a gap can only ever make things look better than they are. `coverage`
carries that fact alongside every result, and the dead-man's switch — not this
module — is what catches the gap itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class Night:
    """One day's sleep, already summed across that day's sessions."""
    day: date
    hours: float
    sessions: int = 1
    rhr: Optional[float] = None


@dataclass(frozen=True)
class DebtPoint:
    day: date
    debt_hours: float
    observed_days: int
    window_days: int
    mean_hours: Optional[float]
    sufficient: bool           # enough observed days to be worth alerting on
    deficits: Dict[date, float] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        return self.observed_days / self.window_days if self.window_days else 0.0


def nightly_deficit(hours: float, baseline_need: float,
                    surplus_cap: Optional[float] = None) -> float:
    """Hours short of baseline. Negative means a surplus that offsets debt.

    `surplus_cap` limits how much credit one night may contribute. None — the
    default and the intended behaviour — leaves it uncapped, so a 12 h night
    against a 6.5 h baseline returns -5.5.
    """
    deficit = baseline_need - hours
    if deficit < 0 and surplus_cap is not None:
        deficit = max(deficit, -abs(surplus_cap))
    return deficit


def window_days_for(as_of: date, window_days: int) -> List[date]:
    """The trailing window ending on `as_of` inclusive."""
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    return [as_of - timedelta(days=i) for i in range(window_days - 1, -1, -1)]


def compute_debt(nights: Dict[date, Night], as_of: date, *,
                 baseline_need: float, window_days: int,
                 surplus_cap: Optional[float] = None,
                 min_observed_days: int = 0) -> DebtPoint:
    """Sleep debt across the trailing window ending `as_of`."""
    deficits: Dict[date, float] = {}
    total_hours = 0.0
    for d in window_days_for(as_of, window_days):
        night = nights.get(d)
        if night is None:          # excluded, never imputed
            continue
        deficits[d] = nightly_deficit(night.hours, baseline_need, surplus_cap)
        total_hours += night.hours

    observed = len(deficits)
    return DebtPoint(
        day=as_of,
        debt_hours=round(sum(deficits.values()), 4),
        observed_days=observed,
        window_days=window_days,
        mean_hours=round(total_hours / observed, 4) if observed else None,
        sufficient=observed >= min_observed_days,
        deficits=deficits,
    )


def debt_series(nights: Dict[date, Night], start: date, end: date, *,
                baseline_need: float, window_days: int,
                surplus_cap: Optional[float] = None,
                min_observed_days: int = 0) -> List[DebtPoint]:
    """compute_debt for every day in [start, end]."""
    out, day = [], start
    while day <= end:
        out.append(compute_debt(nights, day, baseline_need=baseline_need,
                                window_days=window_days, surplus_cap=surplus_cap,
                                min_observed_days=min_observed_days))
        day += timedelta(days=1)
    return out


def rhr_baseline(nights: Dict[date, Night], as_of: date, days: int) -> Optional[float]:
    """Mean resting heart rate over the trailing `days`, or None if unknown."""
    vals = [n.rhr for d in window_days_for(as_of, days)
            if (n := nights.get(d)) is not None and n.rhr is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def rhr_elevated(nights: Dict[date, Night], as_of: date, *,
                 delta_bpm: float, baseline_days: int) -> Optional[bool]:
    """Is today's RHR more than `delta_bpm` above its trailing baseline?

    None when it cannot be determined, which callers must treat as "unknown",
    never as False.
    """
    today = nights.get(as_of)
    if today is None or today.rhr is None:
        return None
    # Baseline excludes the day under test so it cannot drag itself up.
    prior = {d: n for d, n in nights.items() if d < as_of}
    base = rhr_baseline(prior, as_of - timedelta(days=1), baseline_days)
    if base is None:
        return None
    return (today.rhr - base) > delta_bpm


def sessions_to_nights(sessions: Iterable[dict], *,
                       count_types: Iterable[str]) -> Dict[date, Night]:
    """Fold individual Oura sleep sessions into one Night per day key.

    Each session is expected as {"day": date, "hours": float,
    "type": str, "rhr": float|None}. Sessions whose type is not in
    `count_types` are dropped.
    """
    allowed = {t.lower() for t in count_types}
    acc: Dict[date, dict] = {}
    for s in sessions:
        if str(s.get("type", "")).lower() not in allowed:
            continue
        d = s["day"]
        slot = acc.setdefault(d, {"hours": 0.0, "sessions": 0, "rhr": None})
        slot["hours"] += float(s["hours"])
        slot["sessions"] += 1
        # Prefer the RHR from the longest session of the day.
        if s.get("rhr") is not None and (
                slot["rhr"] is None or float(s["hours"]) >= slot.get("_max", 0)):
            slot["rhr"] = float(s["rhr"])
            slot["_max"] = float(s["hours"])
    return {d: Night(day=d, hours=round(v["hours"], 4),
                     sessions=v["sessions"], rhr=v["rhr"])
            for d, v in acc.items()}
