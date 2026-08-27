"""The go-live gates. These exist so the system cannot alert on a threshold
that was never fitted to anything — the failure mode that gets a monitor muted
and then deleted."""

import yaml
import pytest
from pathlib import Path
from sleepdebt import config


def _cfg(tmp_path, **overrides):
    raw = yaml.safe_load((config.HERE / "config.yaml").read_text())
    for dotted, val in overrides.items():
        cur, *rest = dotted.split("__")
        node = raw
        for k in [cur] + rest[:-1]:
            node = node[k]
        node[rest[-1] if rest else cur] = val
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / "deadman.yaml").write_text("silence_days: 3\nrepeat_every_days: 3\n")
    return config.load(tmp_path)


def _ready(tmp_path):
    raw = yaml.safe_load((config.HERE / "config.yaml").read_text())
    raw["_calibrated"] = True
    raw["recipients"]["tier1"] = [{"name": "J", "sms": "+15551234567"}]
    raw["calibration"]["episodes"] = [
        {"label": "a", "date": "2026-08-15", "confirmed": True},
        {"label": "b", "date": "2025-01-15", "confirmed": True}]
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / "deadman.yaml").write_text("silence_days: 3\n")
    return config.load(tmp_path)


def test_shipped_config_is_blocked(tmp_path):
    """Straight out of the box it must not be able to send."""
    assert config.load().blockers()


def test_uncalibrated_is_a_blocker(tmp_path):
    c = _ready(tmp_path)
    c.raw["_calibrated"] = False
    assert any("uncalibrated" in b for b in c.blockers())


def test_unconfirmed_episode_is_a_blocker(tmp_path):
    c = _ready(tmp_path)
    c.raw["calibration"]["episodes"][0]["confirmed"] = False
    assert any("not confirmed" in b for b in c.blockers())


def test_missing_episode_date_is_a_blocker(tmp_path):
    c = _ready(tmp_path)
    c.raw["calibration"]["episodes"][1]["date"] = None
    assert any("has no date" in b for b in c.blockers())


def test_placeholder_number_is_a_blocker(tmp_path):
    c = _ready(tmp_path)
    c.raw["recipients"]["tier1"] = [{"name": "J", "sms": "+1XXXXXXXXXX"}]
    assert any("placeholder number" in b for b in c.blockers())


def test_fully_configured_has_no_blockers(tmp_path):
    assert _ready(tmp_path).blockers() == []


def test_calibration_withholds_fit_on_unconfirmed_episodes(tmp_path):
    """A fitted-looking wrong answer is more dangerous than a missing one."""
    from datetime import date, timedelta
    from sleepdebt import calibrate
    from sleepdebt.debt import Night
    c = _ready(tmp_path)
    c.raw["calibration"]["episodes"][0]["confirmed"] = False
    nights = {date(2026, 8, 18) - timedelta(days=i): Night(
        date(2026, 8, 18) - timedelta(days=i), 4.0) for i in range(60)}
    r = calibrate.run(c, nights, tmp_path / "out")
    assert r["recommendation"] is None
    assert r["unconfirmed_episodes"]


def test_late_nap_is_counted_not_dropped():
    """Oura emits `late_nap`; an exact-match filter silently lost those hours."""
    from datetime import date
    from sleepdebt.debt import sessions_to_nights
    d = date(2026, 8, 18)
    n = sessions_to_nights(
        [{"day": d, "hours": 4.5, "type": "long_sleep", "rhr": None},
         {"day": d, "hours": 0.7, "type": "late_nap", "rhr": None}],
        count_types=["long_sleep", "nap"])
    assert n[d].hours == pytest.approx(5.2) and n[d].sessions == 2
