"""JSON-file state that must survive between runs: the last day data actually
arrived, and the alert history driving cooldown and escalation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: Dict[str, Any] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                # Never silently reset history — a corrupt store would clear the
                # cooldown and let a burst of alerts through.
                raise RuntimeError(
                    f"{self.path} exists but is not valid JSON. Refusing to start "
                    f"with an empty alert history; inspect or remove it by hand.")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, default=str))
        tmp.replace(self.path)

    @property
    def last_data_day(self) -> Optional[date]:
        v = self.data.get("last_data_day")
        return date.fromisoformat(v) if v else None

    @last_data_day.setter
    def last_data_day(self, d: date) -> None:
        self.data["last_data_day"] = d.isoformat()

    @property
    def last_alert(self) -> Optional[Dict[str, Any]]:
        return self.data.get("last_alert")

    def record_alert(self, day: date, debt: float, kind: str) -> None:
        self.data["last_alert"] = {"day": day.isoformat(), "debt": debt, "kind": kind}
        self.data.setdefault("alert_log", []).append(
            {"day": day.isoformat(), "debt": debt, "kind": kind})

    @property
    def last_silence_notice(self) -> Optional[date]:
        v = self.data.get("last_silence_notice")
        return date.fromisoformat(v) if v else None

    @last_silence_notice.setter
    def last_silence_notice(self, d: date) -> None:
        self.data["last_silence_notice"] = d.isoformat()

    def streak(self) -> int:
        return int(self.data.get("over_threshold_streak", 0))

    def set_streak(self, n: int) -> None:
        self.data["over_threshold_streak"] = int(n)
