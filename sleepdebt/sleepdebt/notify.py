"""Notification delivery.

Notifier is a two-method interface so another channel — email, push, a webhook —
can be dropped in without touching the alert logic. TwilioNotifier is the
default; ConsoleNotifier is for dry runs and tests.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Dict, List, Protocol

import requests


@dataclass
class Message:
    to: str
    name: str
    body: str
    tier: str


class Notifier(Protocol):
    def send(self, msg: Message) -> bool: ...
    def describe(self) -> str: ...


class ConsoleNotifier:
    """Prints instead of sending. The default, so a misconfigured deploy is
    quiet rather than spraying messages at real phones."""

    def __init__(self, **_: object) -> None:
        self.sent: List[Message] = []

    def send(self, msg: Message) -> bool:
        self.sent.append(msg)
        print(f"\n--- [{msg.tier}] to {msg.name} <{msg.to}> ---\n{msg.body}\n", file=sys.stderr)
        return True

    def describe(self) -> str:
        return "console (nothing is actually sent)"


class TwilioNotifier:
    def __init__(self, account_sid: str, auth_token: str, from_number: str, timeout: int = 20):
        self.sid, self.token, self.frm, self.timeout = account_sid, auth_token, from_number, timeout

    def send(self, msg: Message) -> bool:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
            auth=(self.sid, self.token), timeout=self.timeout,
            data={"From": self.frm, "To": msg.to, "Body": msg.body})
        if r.status_code >= 300:
            print(f"twilio send to {msg.name} failed ({r.status_code}): {r.text[:200]}",
                  file=sys.stderr)
            return False
        return True

    def describe(self) -> str:
        return f"twilio from {self.frm}"


def build(cfg) -> Notifier:
    backend = str(cfg.raw.get("notifier", {}).get("backend", "console")).lower()
    if backend == "twilio":
        return TwilioNotifier(
            account_sid=cfg.env("TWILIO_ACCOUNT_SID"),
            auth_token=cfg.env("TWILIO_AUTH_TOKEN"),
            from_number=cfg.raw["notifier"]["twilio"]["from_number"])
    if backend == "console":
        return ConsoleNotifier()
    raise ValueError(f"unknown notifier backend: {backend!r}")


def fan_out(notifier: Notifier, recipients: List[Dict[str, str]], body: str, tier: str) -> int:
    ok = 0
    for r in recipients:
        if not r.get("sms"):
            continue
        if notifier.send(Message(to=r["sms"], name=r.get("name", "?"), body=body, tier=tier)):
            ok += 1
    return ok
