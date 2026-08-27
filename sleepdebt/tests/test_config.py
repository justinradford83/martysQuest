import pytest, yaml
from pathlib import Path
from sleepdebt import config

def test_loads_shipped_config():
    c = config.load()
    assert c.baseline_need == 6.5 and c.window_days == 14
    assert c.surplus_cap is None          # uncapped by default

def test_deadman_file_is_required(tmp_path):
    (tmp_path / "config.yaml").write_text(
        Path(config.HERE / "config.yaml").read_text())
    with pytest.raises(config.ConfigError, match="dead-man"):
        config.load(tmp_path)

def test_placeholder_threshold_warns():
    assert any("uncalibrated" in w for w in config.load().validate())

def test_bad_rhr_mode_rejected(tmp_path):
    raw = yaml.safe_load((config.HERE / "config.yaml").read_text())
    raw["alerting"]["rhr"]["mode"] = "MAYBE"
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / "deadman.yaml").write_text("silence_days: 3\n")
    with pytest.raises(config.ConfigError, match="rhr.mode"):
        config.load(tmp_path).validate()
