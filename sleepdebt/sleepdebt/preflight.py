"""Go-live checklist. One command, one answer.

    python -m sleepdebt.preflight            # config + credentials + live API
    python -m sleepdebt.preflight --offline  # skip anything needing the network

Exits non-zero if anything is unresolved, so it can gate a deploy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

from . import config

OK, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""


def _cfg_checks(cfg) -> List[Check]:
    out = [Check("config.yaml and deadman.yaml load", OK,
                 f"baseline {cfg.baseline_need:g} h · {cfg.window_days}-day window")]

    try:
        tiers = cfg.tiers
        out.append(Check("debt tiers configured", OK,
                         " / ".join(f"{t['hours']:g} h {t['label']}" for t in tiers),
                         "crossing up a tier always alerts; same tier waits out the "
                         f"{cfg.cooldown_days}-day cooldown"))
    except config.ConfigError as exc:
        out.append(Check("debt tiers configured", FAIL, str(exc)))

    ph = [f"{t}:{r.get('name')}" for t, ppl in (("tier1", cfg.tier1), ("tier2", cfg.tier2))
          for r in ppl if "XXXX" in str(r.get("sms", ""))]
    out.append(Check("recipients have real numbers", OK if not ph else FAIL,
                     f"{len(cfg.tier1)} tier-1, {len(cfg.tier2)} tier-2",
                     "" if not ph else "placeholders remain: " + ", ".join(ph)))
    if not cfg.tier2:
        out.append(Check("tier-2 contacts", WARN, "none configured",
                         "nobody but you will be told — intended?"))

    backend = str(cfg.raw.get("notifier", {}).get("backend", "console")).lower()
    if backend == "console":
        out.append(Check("notifier backend", FAIL, "console — prints, sends nothing",
                         "set notifier.backend: twilio when ready to go live"))
    else:
        missing = [v for v in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")
                   if not os.environ.get(v)]
        out.append(Check("notifier backend", OK if not missing else FAIL,
                         backend, "missing env: " + ", ".join(missing) if missing else ""))

    try:
        sp = cfg.state_path
        sp.parent.mkdir(parents=True, exist_ok=True)
        t = sp.parent / ".preflight_write_test"
        t.write_text("ok"); t.unlink()
        out.append(Check("state path writable", OK, str(sp)))
    except OSError as exc:
        out.append(Check("state path writable", FAIL, str(exc),
                         "alert history and cooldown cannot persist without this"))

    out.append(Check("dead-man's switch", OK,
                     f"fires after {cfg.silence_days} silent day(s), repeats every "
                     f"{cfg.silence_repeat_days}",
                     "separate file, no enable flag — cannot be switched off here"))
    return out


def _live_checks(cfg) -> List[Check]:
    missing = [v for v in ("OURA_CLIENT_ID", "OURA_CLIENT_SECRET", "OURA_REFRESH_TOKEN")
               if not os.environ.get(v)]
    if missing:
        fix = "export them; never put them in config.yaml"
        if "OURA_REFRESH_TOKEN" in missing:
            fix = ("run `python -m sleepdebt.authorize` once to mint the refresh "
                   "token, then export it" if len(missing) == 1 else fix)
        return [Check("Oura credentials in environment", FAIL,
                      "missing: " + ", ".join(missing), fix),
                Check("Oura field mapping verified", SKIP, "needs credentials")]

    out = [Check("Oura credentials in environment", OK, "all three present")]
    try:
        from .oura import verify
        r = verify(cfg, days=7)
        out.append(Check("Oura API reachable", OK,
                         f"{r['records']} record(s) in the last 7 days"))
        if r["problems"]:
            out.append(Check("Oura field mapping verified", FAIL,
                             f"{r['parsed']}/{r['records']} parsed",
                             " | ".join(r["problems"][:3])))
        else:
            out.append(Check("Oura field mapping verified", OK,
                             f"{r['parsed']} record(s), types {sorted(r['types'])}"))
    except Exception as exc:                       # noqa: BLE001
        out.append(Check("Oura API reachable", FAIL, str(exc)[:160],
                         "check the refresh token has not been rotated or revoked"))
        out.append(Check("Oura field mapping verified", SKIP, "API unreachable"))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="skip network checks")
    a = ap.parse_args(argv)

    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"FAIL  config: {exc}")
        return 1

    checks = _cfg_checks(cfg)
    checks += ([Check("Oura credentials in environment", SKIP, "--offline"),
                Check("Oura field mapping verified", SKIP, "--offline")]
               if a.offline else _live_checks(cfg))

    width = max(len(c.name) for c in checks)
    print("\n  GO-LIVE PREFLIGHT\n  " + "─" * (width + 30))
    for c in checks:
        mark = {OK: "✓", FAIL: "✗", WARN: "!", SKIP: "·"}[c.status]
        print(f"  {mark} {c.name.ljust(width)}  {c.detail}")
        if c.fix:
            print(f"    {' ' * width}  → {c.fix}")

    fails = [c for c in checks if c.status == FAIL]
    warns = [c for c in checks if c.status == WARN]
    print()
    if fails:
        print(f"  NOT READY — {len(fails)} blocker(s). `python -m sleepdebt.run` will "
              f"refuse to send until these clear.")
        print(f"  The dead-man's switch is unaffected and still works.\n")
        return 1
    print(f"  READY{f' — {len(warns)} warning(s) worth a look' if warns else ''}.\n"
          f"  Next: `python -m sleepdebt.run --dry-run`, then schedule it.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
