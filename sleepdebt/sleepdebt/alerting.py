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
    kind: str                      # "debt" | "debt+rhr" | "silence" | ""
    reason: str
    debt: Optional[float] = None
    suppressed_by: str = ""


def evaluate_debt(point: DebtPoint, streak: int, *, threshold: float,
                  consecutive_days: int, rhr_flag: Optional[bool],
                  rhr_mode: str, lower_threshold: float) -> Decision:
    """Decide whether today's debt warrants an alert.

    `streak` counts prior consecutive days already over the bar, today excluded.
    `rhr_flag` is True/False/None, where None means "could not be determined"
    and is never treated as False.
    """
    if not point.sufficient:
        return Decision(False, "", (
            f"only {point.observed_days} of {point.window_days} days observed — "
            f"too sparse to judge; the dead-man's switch covers this"),
            debt=point.debt_hours, suppressed_by="insufficient_data")

    debt = point.debt_hours
    over_high = debt >= threshold
    over_low = debt >= lower_threshold
    mode = rhr_mode.upper()

    if mode == "OFF":
        qualifies, kind = over_high, "debt"
    elif mode == "AND":
        qualifies = over_high and rhr_flag is True
        kind = "debt+rhr"
    else:  # OR — high debt alone, or lower debt corroborated by RHR
        if over_high:
            qualifies, kind = True, "debt"
        elif over_low and rhr_flag is True:
            qualifies, kind = True, "debt+rhr"
        else:
            qualifies, kind = False, "debt"

    if not qualifies:
        return Decision(False, "", f"debt {debt:.1f} h below the bar", debt=debt)

    run = streak + 1
    if run < consecutive_days:
        return Decision(False, kind, (
            f"debt {debt:.1f} h qualifies but only {run} of {consecutive_days} "
            f"consecutive days"), debt=debt, suppressed_by="streak")

    return Decision(True, kind,
                    f"debt {debt:.1f} h for {run} consecutive days", debt=debt)


def apply_cooldown(decision: Decision, last_alert: Optional[Dict], today: date, *,
                   cooldown_days: int, escalation_hours: float) -> Decision:
    """Suppress repeats inside the cooldown, unless debt climbed enough to
    escalate. Escalation always wins — the point is not to nag daily, but a
    worsening picture must still get through."""
    if not decision.fire or not last_alert:
        return decision
    last_day = date.fromisoformat(last_alert["day"])
    days_since = (today - last_day).days
    if days_since >= cooldown_days:
        return decision
    climb = (decision.debt or 0) - float(last_alert.get("debt", 0))
    if climb >= escalation_hours:
        return Decision(True, decision.kind,
                        f"{decision.reason}; escalating — up {climb:.1f} h since "
                        f"the alert {days_since} day(s) ago", debt=decision.debt)
    return Decision(False, decision.kind,
                    f"within {cooldown_days}-day cooldown ({days_since} day(s) "
                    f"since last alert, up only {climb:.1f} h)",
                    debt=decision.debt, suppressed_by="cooldown")


def evaluate_silence(last_data_day: Optional[date], today: date,
                     last_notice: Optional[date], *,
                     silence_days: int, repeat_every_days: int) -> Decision:
    """The dead-man's switch. Deliberately has no off switch."""
    if last_data_day is None:
        gap = silence_days          # never received anything — treat as silent
        detail = "no sleep data has ever been retrieved"
    else:
        gap = (today - last_data_day).days
        detail = f"last data was {last_data_day.isoformat()} ({gap} days ago)"
    if gap < silence_days:
        return Decision(False, "silence", f"data current — {detail}")
    if last_notice and (today - last_notice).days < repeat_every_days:
        return Decision(False, "silence", "silence notice already sent recently",
                        suppressed_by="cooldown")
    return Decision(True, "silence", detail)


# ─────────────────────────── message copy ───────────────────────────

def tier1_body(point: DebtPoint, decision: Decision, *, baseline: float,
               rhr_flag: Optional[bool], rhr_today: Optional[float],
               rhr_base: Optional[float]) -> str:
    lines = [
        f"Sleep debt {point.debt_hours:.1f} h over {point.observed_days} of "
        f"{point.window_days} nights (baseline {baseline:g} h).",
    ]
    if point.mean_hours is not None:
        lines.append(f"Mean {point.mean_hours:.1f} h/night.")
    if rhr_flag is True and rhr_today is not None and rhr_base is not None:
        lines.append(f"Resting HR {rhr_today:.0f} vs {rhr_base:.0f} baseline.")
    elif rhr_flag is None:
        lines.append("Resting HR: not enough data to compare.")
    lines.append(decision.reason.capitalize() + ".")
    return " ".join(lines)


def tier2_body(point: DebtPoint, name: str = "Justin") -> str:
    """Plain language. No clinical framing, no numbers beyond the obvious one,
    and an explicit ask — a message nobody knows how to act on gets ignored."""
    mean = f"{point.mean_hours:.1f}" if point.mean_hours is not None else "very few"
    return (f"{name}'s sleep tracking has crossed the threshold he asked to be "
            f"flagged. He's averaged {mean} hours a night over the last "
            f"{point.observed_days} nights. He asked that you check in.")


def silence_body(decision: Decision, tier: str, name: str = "Justin") -> str:
    if tier == "tier1":
        return (f"Sleep data has stopped arriving — {decision.reason}. "
                f"Ring not worn, token revoked, or the job is not running. "
                f"The debt monitor is blind until this is fixed.")
    return (f"{name}'s sleep tracking has stopped reporting, so no one is "
            f"getting the check he set up. He asked that you check in.")
