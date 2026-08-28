"""Alert decision logic.

Pure evaluation, separated from delivery, so every branch can be tested without
sending anything. Three independent checks:

  1. Debt over threshold for N consecutive days.
  2. RHR corroboration, AND / OR / OFF.
  3. Dead-man's switch — data has stopped arriving.

(3) does not depend on (1) or (2) and is evaluated even when debt alerting is
suppressed for sparse data. A quiet window and a healthy window look identical
to the debt maths; only this check tells them apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

from .debt import DebtPoint, Night, compute_debt, rhr_baseline, rhr_elevated


@dataclass
class Decision:
    fire: bool
    kind: str                      # "debt" | "silence" | ""
    reason: str
    debt: Optional[float] = None
    tier: Optional[dict] = None    # the tier crossed, if any
    reset: bool = False            # debt fell below the lowest tier
    suppressed_by: str = ""


def tier_for(debt: float, tiers: List[dict]) -> Optional[dict]:
    """Highest tier at or below `debt`, or None if under them all."""
    hit = [t for t in tiers if debt >= t["hours"]]
    return max(hit, key=lambda t: t["hours"]) if hit else None


def evaluate_debt(point: DebtPoint, streak: int, last_tier: Optional[float],
                  last_alert_day: Optional[date], today: date, *,
                  tiers: List[dict], consecutive_days: int,
                  cooldown_days: int) -> Decision:
    """Decide whether today's debt warrants an alert.

    How a tier behaves:
      - crossing UP into a higher tier fires immediately, cooldown or not, so a
        worsening picture always gets through;
      - staying at the same tier waits out the cooldown;
      - dropping below the lowest tier resets the ladder, so a later climb
        warns again from the bottom.

    `streak` counts prior consecutive days at or above the lowest tier, today
    excluded.
    """
    if not point.sufficient:
        return Decision(False, "", (
            f"only {point.observed_days} of {point.window_days} days observed - "
            f"too sparse to judge; the dead-man's switch covers this"),
            debt=point.debt_hours, suppressed_by="insufficient_data")

    debt = point.debt_hours
    t = tier_for(debt, tiers)
    if t is None:
        return Decision(False, "", f"debt {debt:.1f} h below the {tiers[0]['hours']:g} h tier",
                        debt=debt, reset=True)

    run = streak + 1
    if run < consecutive_days:
        return Decision(False, "debt", (
            f"debt {debt:.1f} h is at the {t['hours']:g} h tier but only {run} of "
            f"{consecutive_days} consecutive days"),
            debt=debt, tier=t, suppressed_by="streak")

    if last_tier is None or t["hours"] > last_tier:
        why = ("first warning" if last_tier is None
               else f"escalated from the {last_tier:g} h tier")
        return Decision(True, "debt",
                        f"debt {debt:.1f} h crossed the {t['hours']:g} h tier "
                        f"({t['label']}) - {why}", debt=debt, tier=t)

    if t["hours"] < last_tier:
        return Decision(False, "debt", (
            f"debt {debt:.1f} h has eased to the {t['hours']:g} h tier, already "
            f"warned at {last_tier:g} h"), debt=debt, tier=t, suppressed_by="eased")

    days_since = (today - last_alert_day).days if last_alert_day else 10**6
    if days_since >= cooldown_days:
        return Decision(True, "debt",
                        f"debt {debt:.1f} h still at the {t['hours']:g} h tier "
                        f"({t['label']}), {days_since} days since the last warning",
                        debt=debt, tier=t)
    return Decision(False, "debt",
                    f"still at the {t['hours']:g} h tier, {days_since} of "
                    f"{cooldown_days} cooldown days elapsed",
                    debt=debt, tier=t, suppressed_by="cooldown")


def evaluate_silence(last_data_day: Optional[date], today: date,
                     last_notice: Optional[date], *,
                     silence_days: int, repeat_every_days: int) -> Decision:
    """The dead-man's switch. Deliberately has no off switch."""
    if last_data_day is None:
        gap = silence_days
        detail = "no sleep data has ever been retrieved"
    else:
        gap = (today - last_data_day).days
        detail = f"last data was {last_data_day.isoformat()} ({gap} days ago)"
    if gap < silence_days:
        return Decision(False, "silence", f"data current - {detail}")
    if last_notice and (today - last_notice).days < repeat_every_days:
        return Decision(False, "silence", "silence notice already sent recently",
                        suppressed_by="cooldown")
    return Decision(True, "silence", detail)


# ─────────────────────────── message copy ───────────────────────────

def tier1_body(point: DebtPoint, decision: Decision, *, baseline: float,
               rhr_flag: Optional[bool], rhr_today: Optional[float],
               rhr_base: Optional[float]) -> str:
    t = decision.tier or {}
    parts = [f"Sleep debt {point.debt_hours:.1f} h - {str(t.get('label','')).upper()} "
             f"({t.get('hours', 0):g} h tier).",
             f"{point.observed_days} of {point.window_days} nights recorded, "
             f"baseline {baseline:g} h."]
    if point.mean_hours is not None:
        parts.append(f"Mean {point.mean_hours:.1f} h/night.")
    if rhr_flag is True and rhr_today is not None and rhr_base is not None:
        parts.append(f"Resting HR {rhr_today:.0f} vs {rhr_base:.0f} baseline.")
    parts.append(decision.reason.capitalize() + ".")
    return " ".join(parts)


def tier2_body(point: DebtPoint, decision: Decision, name: str = "Justin") -> str:
    """Plain language. No clinical framing, no jargon, and an explicit ask - a
    message nobody knows how to act on gets ignored."""
    mean = f"{point.mean_hours:.1f}" if point.mean_hours is not None else "very few"
    return (f"{name}'s sleep tracking has crossed a level he asked to be "
            f"flagged. He's averaged {mean} hours a night over the last "
            f"{point.observed_days} nights. He asked that you check in.")


def silence_body(decision: Decision, tier: str, name: str = "Justin") -> str:
    if tier == "tier1":
        return (f"Sleep data has stopped arriving — {decision.reason}. "
                f"Ring not worn, token revoked, or the job is not running. "
                f"The debt monitor is blind until this is fixed.")
    return (f"{name}'s sleep tracking has stopped reporting, so no one is "
            f"getting the check he set up. He asked that you check in.")
