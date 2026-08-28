"""Go-live gates. Calibration is gone; the tiers are fixed, so what remains is
making sure a half-configured deploy cannot text real people."""
import yaml
import pytest
from sleepdebt import config


def _ready(tmp_path):
    raw = yaml.safe_load((config.HERE / "config.yaml").read_text())
    raw["recipients"]["tier1"] = [{"name": "J", "sms": "+15551234567"}]
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / "deadman.yaml").write_text("silence_days: 3\n")
    return config.load(tmp_path)


def test_shipped_config_is_blocked():
    """Out of the box it must not be able to send — placeholder number."""
    assert config.load().blockers()


def test_placeholder_number_is_a_blocker(tmp_path):
    c = _ready(tmp_path)
    c.raw["recipients"]["tier1"] = [{"name": "J", "sms": "+1XXXXXXXXXX"}]
    assert any("placeholder number" in b for b in c.blockers())


def test_placeholder_twilio_number_is_a_blocker(tmp_path):
    c = _ready(tmp_path)
    c.raw["notifier"]["backend"] = "twilio"
    assert any("twilio" in b for b in c.blockers())


def test_fully_configured_has_no_blockers(tmp_path):
    assert _ready(tmp_path).blockers() == []


def test_tiers_load_sorted(tmp_path):
    c = _ready(tmp_path)
    c.raw["alerting"]["tiers"] = [{"hours": 10}, {"hours": 6}, {"hours": 8}]
    assert [t["hours"] for t in c.tiers] == [6.0, 8.0, 10.0]


def test_empty_tiers_is_a_blocker(tmp_path):
    c = _ready(tmp_path)
    c.raw["alerting"]["tiers"] = []
    assert any("tiers" in b for b in c.blockers())


def test_bad_rhr_mode_rejected(tmp_path):
    c = _ready(tmp_path)
    c.raw["alerting"]["rhr"]["mode"] = "MAYBE"
    with pytest.raises(config.ConfigError, match="rhr.mode"):
        c.validate()


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
