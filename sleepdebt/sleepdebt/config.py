"""Configuration loading.

config.yaml holds everything tunable. deadman.yaml holds the dead-man's switch
and is loaded separately and unconditionally: the switch has no enable flag and
its absence is a hard error, so it cannot be turned off in passing while tuning
thresholds in config.yaml.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

HERE = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    pass


def _require(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ConfigError(f"config.yaml is missing required key: {path}")
        cur = cur[part]
    return cur


@dataclass
class Config:
    raw: Dict[str, Any]
    deadman: Dict[str, Any]
    path: Path

    @property
    def baseline_need(self) -> float: return float(_require(self.raw, "debt.baseline_need_hours"))
    @property
    def window_days(self) -> int: return int(_require(self.raw, "debt.window_days"))
    @property
    def surplus_cap(self) -> Optional[float]:
        v = _require(self.raw, "debt.surplus_credit_cap_hours")
        return None if v is None else float(v)
    @property
    def min_observed_days(self) -> int: return int(_require(self.raw, "debt.min_observed_days"))
    @property
    def count_session_types(self) -> List[str]: return list(_require(self.raw, "debt.count_session_types"))

    @property
    def threshold_hours(self) -> float: return float(_require(self.raw, "alerting.threshold_hours"))
    @property
    def max_false_alerts_per_year(self) -> float:
        return float(self.raw["alerting"].get("max_false_alerts_per_year", 4.0))
    @property
    def consecutive_days(self) -> int: return int(_require(self.raw, "alerting.consecutive_days"))
    @property
    def rhr_mode(self) -> str: return str(_require(self.raw, "alerting.rhr.mode")).upper()
    @property
    def rhr_lower_threshold(self) -> float: return float(_require(self.raw, "alerting.rhr.lower_threshold_hours"))
    @property
    def rhr_delta_bpm(self) -> float: return float(_require(self.raw, "alerting.rhr.delta_bpm"))
    @property
    def rhr_baseline_days(self) -> int: return int(_require(self.raw, "alerting.rhr.baseline_days"))
    @property
    def cooldown_days(self) -> int: return int(_require(self.raw, "alerting.cooldown_days"))
    @property
    def escalation_hours(self) -> float: return float(_require(self.raw, "alerting.escalation_hours"))

    @property
    def tier1(self) -> List[Dict[str, str]]: return list(_require(self.raw, "recipients.tier1"))
    @property
    def tier2(self) -> List[Dict[str, str]]: return list(self.raw.get("recipients", {}).get("tier2") or [])

    @property
    def silence_days(self) -> int: return int(self.deadman["silence_days"])
    @property
    def silence_repeat_days(self) -> int: return int(self.deadman.get("repeat_every_days", 3))
    @property
    def silence_recipients(self) -> List[Dict[str, str]]:
        return list(self.deadman.get("recipients") or self.tier1)

    @property
    def state_path(self) -> Path:
        return (self.path / str(_require(self.raw, "storage.state_path"))).resolve()

    def env(self, name: str) -> str:
        v = os.environ.get(name)
        if not v:
            raise ConfigError(
                f"environment variable {name} is not set. Credentials are read "
                f"from the environment, never from config.yaml.")
        return v

    @property
    def calibrated(self) -> bool:
        return self.raw.get("_calibrated") is True

    def episodes(self) -> List[Dict[str, Any]]:
        return list(self.raw.get("calibration", {}).get("episodes") or [])

    def blockers(self) -> List[str]:
        """Conditions that must be cleared before live alerting.

        Returned as a list rather than raised so preflight can show all of them
        at once instead of one per run.
        """
        out: List[str] = []
        if not self.calibrated:
            out.append("threshold is uncalibrated — run `python -m sleepdebt.calibrate`, "
                       "set alerting.threshold_hours from its recommendation, then add "
                       "`_calibrated: true` to config.yaml")
        eps = self.episodes()
        if not eps:
            out.append("calibration.episodes is empty — nothing to fit a threshold against")
        for e in eps:
            label = e.get("label", "?")
            if not e.get("date"):
                out.append(f"episode {label!r} has no date — the lead-in report covers the "
                           f"21 days before it, so a month is not precise enough")
            elif e.get("confirmed") is not True:
                out.append(f"episode {label!r} ({e['date']}) is not confirmed — set "
                           f"`confirmed: true` once you have checked the date")
        for tier, people in (("tier1", self.tier1), ("tier2", self.tier2)):
            for r in people:
                if "XXXX" in str(r.get("sms", "")):
                    out.append(f"{tier} recipient {r.get('name')!r} has a placeholder number")
        if str(self.raw.get("notifier", {}).get("backend", "")).lower() == "twilio":
            if "XXXX" in str(self.raw["notifier"]["twilio"].get("from_number", "")):
                out.append("notifier.twilio.from_number is a placeholder")
        return out

    def validate(self) -> List[str]:
        warn: List[str] = []
        for p in ("debt.baseline_need_hours", "debt.window_days", "alerting.threshold_hours",
                  "alerting.consecutive_days", "recipients.tier1", "storage.state_path"):
            _require(self.raw, p)
        if self.rhr_mode not in {"AND", "OR", "OFF"}:
            raise ConfigError("alerting.rhr.mode must be one of AND, OR, OFF")
        if self.min_observed_days > self.window_days:
            raise ConfigError("debt.min_observed_days cannot exceed debt.window_days")
        if not self.tier1:
            raise ConfigError("recipients.tier1 is empty — nobody would be told")
        if self.raw.get("_calibrated") is not True:
            warn.append(
                "threshold_hours is still the uncalibrated placeholder. Run "
                "`python -m sleepdebt.calibrate`, set the recommended value, then "
                "add `_calibrated: true` to config.yaml to silence this.")
        for tier, people in (("tier1", self.tier1), ("tier2", self.tier2)):
            for p in people:
                if "XXXX" in str(p.get("sms", "")):
                    warn.append(f"{tier} recipient {p.get('name')!r} still has a placeholder number")
        return warn


def load(directory: Optional[Path] = None) -> Config:
    d = Path(directory) if directory else HERE
    cfg_file, dm_file = d / "config.yaml", d / "deadman.yaml"
    if not cfg_file.exists():
        raise ConfigError(f"no config.yaml at {cfg_file}")
    if not dm_file.exists():
        raise ConfigError(
            f"no deadman.yaml at {dm_file}. The dead-man's switch is required — "
            f"silence is signal, and this system will not run without it.")
    raw = yaml.safe_load(cfg_file.read_text()) or {}
    dm = yaml.safe_load(dm_file.read_text()) or {}
    if "silence_days" not in dm:
        raise ConfigError("deadman.yaml must define silence_days")
    return Config(raw=raw, deadman=dm, path=d)
