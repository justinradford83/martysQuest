"""Oura API v2 client.

Personal access tokens were retired in December 2025, so this uses OAuth2:
a stored refresh token is exchanged for a short-lived access token on each run.
Credentials come from the environment — OURA_CLIENT_ID, OURA_CLIENT_SECRET,
OURA_REFRESH_TOKEN — and are never written to config.

RESPONSE SHAPE: the field names below are the documented ones, but they have
NOT been verified against a live response from this account. `parse_session`
raises a loud, specific error naming the keys it actually received rather than
guessing, and `python -m sleepdebt.oura --dump` prints one raw day so the
mapping can be confirmed before anything depends on it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

# Documented field names, in preference order. Extend rather than replace if a
# live response turns out to differ.
DURATION_KEYS = ("total_sleep_duration",)
DAY_KEYS = ("day",)
START_KEYS = ("bedtime_start",)
END_KEYS = ("bedtime_end",)
TYPE_KEYS = ("type",)
# Values Oura is known to emit for `type`. Matching elsewhere is by substring
# and case-insensitive, so "nap" in config also covers "late_nap".
KNOWN_TYPES = ("long_sleep", "sleep", "late_nap", "rest")
RHR_KEYS = ("average_heart_rate", "lowest_heart_rate")


class OuraError(RuntimeError):
    pass


def _first(d: Dict[str, Any], keys) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


class OuraClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg.raw["oura"]["base_url"].rstrip("/")
        self.token_url = cfg.raw["oura"]["token_url"]
        self.timeout = int(cfg.raw["oura"].get("timeout_seconds", 30))
        self._access: Optional[str] = None

    # ---- auth ----
    def access_token(self) -> str:
        if self._access:
            return self._access
        r = requests.post(self.token_url, timeout=self.timeout, data={
            "grant_type": "refresh_token",
            "refresh_token": self.cfg.env("OURA_REFRESH_TOKEN"),
            "client_id": self.cfg.env("OURA_CLIENT_ID"),
            "client_secret": self.cfg.env("OURA_CLIENT_SECRET"),
        })
        if r.status_code != 200:
            raise OuraError(
                f"token refresh failed ({r.status_code}). If the refresh token was "
                f"rotated or revoked, re-run the one-time authorisation. Body: {r.text[:300]}")
        payload = r.json()
        self._access = payload["access_token"]
        if "refresh_token" in payload and payload["refresh_token"] != self.cfg.env("OURA_REFRESH_TOKEN"):
            # Oura rotates refresh tokens; losing the new one locks the job out.
            print("NOTE: Oura returned a new refresh token. Update OURA_REFRESH_TOKEN to:\n"
                  f"  {payload['refresh_token']}", file=sys.stderr)
        return self._access

    # ---- fetch ----
    def _get(self, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        out, token, url = [], None, f"{self.base}{path}"
        while True:
            p = dict(params)
            if token:
                p["next_token"] = token
            r = requests.get(url, params=p, timeout=self.timeout,
                             headers={"Authorization": f"Bearer {self.access_token()}"})
            if r.status_code == 401:
                raise OuraError("401 from Oura — the access token was rejected.")
            if r.status_code == 429:
                raise OuraError("429 from Oura — rate limited. Back off and retry.")
            if r.status_code != 200:
                raise OuraError(f"{r.status_code} from {path}: {r.text[:300]}")
            body = r.json()
            out.extend(body.get("data", []))
            token = body.get("next_token")
            if not token:
                return out

    def sleep_sessions(self, start: date, end: date) -> List[Dict[str, Any]]:
        """Raw sleep session records for [start, end]."""
        return self._get("/usercollection/sleep", {
            "start_date": start.isoformat(), "end_date": end.isoformat()})


def parse_session(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise one raw Oura sleep record.

    Returns {"day", "hours", "type", "start", "end", "rhr"}.
    Raises OuraError naming the actual keys if the expected ones are absent, so
    a shape change surfaces immediately instead of silently producing zeros.
    """
    secs = _first(rec, DURATION_KEYS)
    day = _first(rec, DAY_KEYS)
    if secs is None or day is None:
        raise OuraError(
            "sleep record is missing the fields this parser needs "
            f"({DURATION_KEYS[0]!r}, {DAY_KEYS[0]!r}). Keys present: "
            f"{sorted(rec.keys())}. Run `python -m sleepdebt.oura --dump` and "
            "update the *_KEYS constants in oura.py to match.")
    start = _first(rec, START_KEYS)
    end = _first(rec, END_KEYS)
    return {
        "day": date.fromisoformat(str(day)[:10]),
        # total_sleep_duration is documented in SECONDS.
        "hours": round(float(secs) / 3600.0, 4),
        "type": str(_first(rec, TYPE_KEYS) or "unknown"),
        "start": start,
        "end": end,
        "rhr": _first(rec, RHR_KEYS),
    }


def fetch_sessions(cfg, start: date, end: date) -> List[Dict[str, Any]]:
    return [parse_session(r) for r in OuraClient(cfg).sleep_sessions(start, end)]


def verify(cfg, days: int = 7) -> dict:
    """Fetch real records and report exactly which field mapping held.

    This is the check that closes the one gap I could not close myself: the
    field names are documented but unconfirmed against this account until a
    live response has actually been parsed.
    """
    end = date.today()
    raw = OuraClient(cfg).sleep_sessions(end - timedelta(days=days), end)
    result = {"records": len(raw), "parsed": 0, "problems": [], "types": set(),
              "sample": None, "unmapped_keys": set()}
    if not raw:
        result["problems"].append(
            f"no sleep records in the last {days} days — wear the ring, sync the "
            f"app, then re-run")
        return result
    mapped = set(DURATION_KEYS + DAY_KEYS + START_KEYS + END_KEYS + TYPE_KEYS + RHR_KEYS)
    for rec in raw:
        result["unmapped_keys"] |= (set(rec.keys()) - mapped)
        try:
            p = parse_session(rec)
        except OuraError as exc:
            result["problems"].append(str(exc))
            continue
        result["parsed"] += 1
        result["types"].add(p["type"])
        if result["sample"] is None:
            result["sample"] = p
        if not (0 < p["hours"] <= 24):
            result["problems"].append(
                f"{p['day']}: {p['hours']} h is not a plausible duration — "
                f"total_sleep_duration may not be in seconds on this account")
        if p["rhr"] is not None and not (25 <= float(p["rhr"]) <= 120):
            result["problems"].append(
                f"{p['day']}: resting HR {p['rhr']} is out of plausible range — "
                f"check which of {RHR_KEYS} is the right field")
    unknown = result["types"] - set(KNOWN_TYPES)
    if unknown:
        result["problems"].append(
            f"unrecognised session type(s) {sorted(unknown)} — add them to "
            f"debt.count_session_types in config.yaml or they will be dropped")
    return result


def _main() -> int:
    from . import config
    ap = argparse.ArgumentParser(description="Oura connectivity and shape check.")
    ap.add_argument("--dump", action="store_true",
                    help="print raw records and exit")
    ap.add_argument("--verify", action="store_true",
                    help="parse real records and report whether the mapping held")
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    cfg = config.load()
    end = date.today()
    start = end - timedelta(days=a.days)

    if a.verify:
        r = verify(cfg, a.days)
        print(f"records fetched : {r['records']}")
        print(f"parsed cleanly  : {r['parsed']}")
        print(f"session types   : {sorted(r['types']) or '—'}")
        if r["sample"]:
            print(f"sample          : {r['sample']}")
        if r["unmapped_keys"]:
            print(f"other keys seen : {sorted(r['unmapped_keys'])[:14]}")
        if r["problems"]:
            print("\nPROBLEMS")
            for p in r["problems"]:
                print(f"  - {p}")
            return 1
        print("\nfield mapping confirmed against live data")
        return 0

    raw = OuraClient(cfg).sleep_sessions(start, end)
    if a.dump:
        print(json.dumps(raw, indent=2)[:20000])
        return 0
    for rec in raw:
        print(parse_session(rec))
    print(f"{len(raw)} session(s) over {a.days} day(s)")
    return 0


def main() -> int:
    """Entry point. Turns the expected failures into advice rather than a
    traceback — the actionable line is otherwise buried under a stack."""
    from . import config
    try:
        return _main()
    except config.ConfigError as exc:
        print(f"\n{exc}", file=sys.stderr)
        if "OURA_REFRESH_TOKEN" in str(exc):
            print("\nMint one first:\n  python -m sleepdebt.authorize", file=sys.stderr)
        return 1
    except OuraError as exc:
        print(f"\nOura: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"\ncould not reach Oura: {type(exc).__name__}", file=sys.stderr)
        print("check your network, and any proxy or VPN blocking api.ouraring.com",
              file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
