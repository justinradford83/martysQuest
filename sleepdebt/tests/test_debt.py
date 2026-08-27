from datetime import date, timedelta
import pytest
from sleepdebt.debt import (Night, compute_debt, nightly_deficit, rhr_elevated,
                            sessions_to_nights)

D = date(2026, 8, 18)
def nights(vals, start=D - timedelta(days=13)):
    return {start + timedelta(days=i): Night(start + timedelta(days=i), v)
            for i, v in enumerate(vals)}

def test_deficit_sign():
    assert nightly_deficit(4.0, 6.5) == 2.5           # short
    assert nightly_deficit(8.0, 6.5) == -1.5          # surplus credits

def test_surplus_uncapped_by_default():
    assert nightly_deficit(12.0, 6.5) == -5.5

def test_surplus_cap_when_set():
    assert nightly_deficit(12.0, 6.5, surplus_cap=2.0) == -2.0

def test_sum_over_window():
    p = compute_debt(nights([4.0] * 14), D, baseline_need=6.5, window_days=14)
    assert p.debt_hours == pytest.approx(35.0)
    assert p.observed_days == 14 and p.coverage == 1.0

def test_missing_days_excluded_not_imputed():
    n = nights([4.0] * 14)
    for k in (3, 4, 5):
        del n[D - timedelta(days=k)]
    p = compute_debt(n, D, baseline_need=6.5, window_days=14)
    assert p.observed_days == 11
    assert p.debt_hours == pytest.approx(11 * 2.5)     # 3 gaps contribute nothing

def test_sparse_window_marked_insufficient():
    n = {D: Night(D, 3.0)}
    p = compute_debt(n, D, baseline_need=6.5, window_days=14, min_observed_days=7)
    assert p.observed_days == 1 and p.sufficient is False

def test_one_long_night_offsets_debt():
    a = compute_debt(nights([4.0] * 14), D, baseline_need=6.5, window_days=14)
    b = compute_debt(nights([4.0] * 13 + [12.0]), D, baseline_need=6.5, window_days=14)
    assert b.debt_hours == pytest.approx(a.debt_hours - 2.5 - 5.5)

def test_sessions_fold_into_one_night():
    s = [{"day": D, "hours": 4.5, "type": "long_sleep", "rhr": 60},
         {"day": D, "hours": 0.6, "type": "nap", "rhr": None}]
    n = sessions_to_nights(s, count_types=["long_sleep", "nap"])
    assert n[D].hours == pytest.approx(5.1) and n[D].sessions == 2

def test_session_types_filtered():
    s = [{"day": D, "hours": 4.5, "type": "long_sleep", "rhr": None},
         {"day": D, "hours": 0.6, "type": "nap", "rhr": None}]
    n = sessions_to_nights(s, count_types=["long_sleep"])
    assert n[D].hours == pytest.approx(4.5) and n[D].sessions == 1

def test_rhr_unknown_is_none_not_false():
    assert rhr_elevated({D: Night(D, 6.0, rhr=None)}, D, delta_bpm=5, baseline_days=30) is None
    assert rhr_elevated({}, D, delta_bpm=5, baseline_days=30) is None

def test_rhr_elevated_excludes_today_from_baseline():
    n = {D - timedelta(days=i): Night(D - timedelta(days=i), 7.0, rhr=55) for i in range(1, 31)}
    n[D] = Night(D, 7.0, rhr=64)
    assert rhr_elevated(n, D, delta_bpm=5, baseline_days=30) is True
    n[D] = Night(D, 7.0, rhr=58)
    assert rhr_elevated(n, D, delta_bpm=5, baseline_days=30) is False
