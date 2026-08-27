from datetime import date
import pytest
from sleepdebt.alerting import (apply_cooldown, evaluate_debt, evaluate_silence)
from sleepdebt.debt import DebtPoint

D = date(2026, 8, 18)
def pt(debt, observed=14, sufficient=True):
    return DebtPoint(D, debt, observed, 14, 6.0, sufficient)

BASE = dict(threshold=10.0, consecutive_days=2, rhr_mode="OR", lower_threshold=7.0)

def test_below_threshold_holds():
    assert evaluate_debt(pt(5), 1, rhr_flag=None, **BASE).fire is False

def test_consecutive_day_requirement():
    assert evaluate_debt(pt(12), 0, rhr_flag=None, **BASE).fire is False
    assert evaluate_debt(pt(12), 1, rhr_flag=None, **BASE).fire is True

def test_or_mode_lower_threshold_needs_rhr():
    assert evaluate_debt(pt(8), 1, rhr_flag=None, **BASE).fire is False
    d = evaluate_debt(pt(8), 1, rhr_flag=True, **BASE)
    assert d.fire is True and d.kind == "debt+rhr"

def test_and_mode_requires_rhr():
    cfg = dict(BASE, rhr_mode="AND")
    assert evaluate_debt(pt(20), 1, rhr_flag=None, **cfg).fire is False
    assert evaluate_debt(pt(20), 1, rhr_flag=True, **cfg).fire is True

def test_off_mode_ignores_rhr():
    cfg = dict(BASE, rhr_mode="OFF")
    assert evaluate_debt(pt(12), 1, rhr_flag=None, **cfg).fire is True

def test_sparse_window_suppresses_debt_alert():
    d = evaluate_debt(pt(30, observed=3, sufficient=False), 5, rhr_flag=None, **BASE)
    assert d.fire is False and d.suppressed_by == "insufficient_data"

def test_cooldown_suppresses_repeat():
    d = evaluate_debt(pt(12), 1, rhr_flag=None, **BASE)
    out = apply_cooldown(d, {"day": "2026-08-17", "debt": 11.0}, D,
                         cooldown_days=3, escalation_hours=5.0)
    assert out.fire is False and out.suppressed_by == "cooldown"

def test_escalation_breaks_cooldown():
    d = evaluate_debt(pt(17), 1, rhr_flag=None, **BASE)
    out = apply_cooldown(d, {"day": "2026-08-17", "debt": 11.0}, D,
                         cooldown_days=3, escalation_hours=5.0)
    assert out.fire is True and "escalating" in out.reason

def test_cooldown_expires():
    d = evaluate_debt(pt(12), 1, rhr_flag=None, **BASE)
    out = apply_cooldown(d, {"day": "2026-08-10", "debt": 11.0}, D,
                         cooldown_days=3, escalation_hours=5.0)
    assert out.fire is True

# ---- dead-man's switch ----
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
