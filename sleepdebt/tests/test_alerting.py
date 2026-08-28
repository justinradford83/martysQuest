"""Tier ladder behaviour.

Crossing up always alerts. Same tier waits out the cooldown. Dropping below
the lowest tier resets, so a later climb warns again from the bottom.
"""
from datetime import date
import pytest
from sleepdebt.alerting import evaluate_debt, evaluate_silence, tier_for
from sleepdebt.debt import DebtPoint

D = date(2026, 8, 18)
TIERS = [{"hours": 6.0, "label": "building"},
         {"hours": 8.0, "label": "high"},
         {"hours": 10.0, "label": "severe"}]
K = dict(tiers=TIERS, consecutive_days=2, cooldown_days=3)


def pt(debt, observed=14, sufficient=True):
    return DebtPoint(D, debt, observed, 14, 5.0, sufficient)


# ---- tier selection ----
@pytest.mark.parametrize("debt,expected", [
    (0.0, None), (5.9, None), (6.0, 6.0), (7.9, 6.0),
    (8.0, 8.0), (9.9, 8.0), (10.0, 10.0), (25.0, 10.0)])
def test_tier_boundaries(debt, expected):
    t = tier_for(debt, TIERS)
    assert (t["hours"] if t else None) == expected


def test_negative_debt_is_under_all_tiers():
    assert tier_for(-12.0, TIERS) is None


# ---- firing ----
def test_below_lowest_tier_resets_the_ladder():
    d = evaluate_debt(pt(4.2), 5, 8.0, D, D, **K)
    assert d.fire is False and d.reset is True


def test_consecutive_day_requirement():
    assert evaluate_debt(pt(6.5), 0, None, None, D, **K).fire is False
    assert evaluate_debt(pt(6.5), 1, None, None, D, **K).fire is True


def test_first_crossing_fires_at_the_right_tier():
    d = evaluate_debt(pt(8.4), 1, None, None, D, **K)
    assert d.fire is True and d.tier["hours"] == 8.0 and d.tier["label"] == "high"


def test_same_tier_waits_out_cooldown():
    d = evaluate_debt(pt(6.5), 5, 6.0, date(2026, 8, 17), D, **K)
    assert d.fire is False and d.suppressed_by == "cooldown"


def test_same_tier_fires_once_cooldown_expires():
    assert evaluate_debt(pt(6.5), 5, 6.0, date(2026, 8, 14), D, **K).fire is True


def test_escalation_beats_cooldown():
    """The whole point: a worsening picture must get through the quiet period."""
    d = evaluate_debt(pt(8.4), 5, 6.0, date(2026, 8, 17), D, **K)
    assert d.fire is True and d.tier["hours"] == 8.0
    assert "escalated" in d.reason


def test_escalation_to_top_tier():
    d = evaluate_debt(pt(10.9), 6, 8.0, D, D, **K)
    assert d.fire is True and d.tier["hours"] == 10.0


def test_easing_to_a_lower_tier_does_not_re_alert():
    d = evaluate_debt(pt(8.1), 7, 10.0, D, D, **K)
    assert d.fire is False and d.suppressed_by == "eased"


def test_sparse_window_suppressed():
    d = evaluate_debt(pt(30, observed=3, sufficient=False), 5, None, None, D, **K)
    assert d.fire is False and d.suppressed_by == "insufficient_data"


def test_full_climb_then_recovery_then_climb_again():
    """A whole arc: warn at 6, escalate to 8 and 10, recover, warn at 6 again."""
    fired, streak, last_tier, last_day = [], 0, None, None
    for day, debt in [(14, 6.4), (15, 6.6), (16, 8.2), (17, 10.4),
                      (18, 3.0), (19, 6.7), (20, 6.9)]:
        d0 = date(2026, 8, day)
        p = DebtPoint(d0, debt, 14, 14, 5.0, True)
        dec = evaluate_debt(p, streak, last_tier, last_day, d0, **K)
        streak = 0 if dec.reset else streak + 1
        if dec.reset:
            last_tier = None
        if dec.fire:
            fired.append((day, dec.tier["hours"]))
            last_tier, last_day = dec.tier["hours"], d0
    assert fired == [(15, 6.0), (16, 8.0), (17, 10.0), (20, 6.0)]


# ---- dead-man's switch, unchanged and unconditional ----
def test_silence_fires_after_gap():
    assert evaluate_silence(date(2026, 8, 14), D, None,
                            silence_days=3, repeat_every_days=3).fire is True

def test_silence_quiet_when_current():
    assert evaluate_silence(date(2026, 8, 17), D, None,
                            silence_days=3, repeat_every_days=3).fire is False

def test_silence_when_no_data_ever():
    assert evaluate_silence(None, D, None, silence_days=3, repeat_every_days=3).fire is True

def test_silence_repeat_throttled():
    assert evaluate_silence(date(2026, 8, 10), D, date(2026, 8, 17),
                            silence_days=3, repeat_every_days=3).fire is False
