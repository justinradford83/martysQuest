"""The scheduled job. Fetch, compute, decide, notify.

    python -m sleepdebt.run             # normal run
    python -m sleepdebt.run --dry-run   # decide and print, send nothing

Order matters: the dead-man's switch is evaluated first and independently. If
data has stopped arriving, that is the finding — and it is precisely the case
where the debt maths would otherwise report a reassuringly low number.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import Optional

from . import config, notify
from .alerting import (evaluate_debt, evaluate_silence, silence_body,
                       tier1_body, tier2_body)
from .debt import compute_debt, rhr_baseline, rhr_elevated, sessions_to_nights
from .store import Store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="decide but send nothing")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    ap.add_argument("--force", action="store_true",
                    help="send despite unresolved go-live blockers (not advised)")
    a = ap.parse_args(argv)

    cfg = config.load()
    for w in cfg.validate():
        print(f"warning: {w}", file=sys.stderr)

    # Hard gate. A generic threshold produces false positives, false positives
    # get the system muted, and a muted system is worse than none — so an
    # uncalibrated config cannot send. --dry-run still works, and the dead-man's
    # switch is unaffected: it does not depend on the threshold at all.
    blockers = cfg.blockers()
    if blockers and not a.dry_run and not a.force:
        print("\nREFUSING TO SEND — not ready to go live:", file=sys.stderr)
        for b in blockers:
            print(f"  · {b}", file=sys.stderr)
        print("\nRun `python -m sleepdebt.preflight` for the full checklist, or "
              "`--dry-run` to see what it would have decided.", file=sys.stderr)
        return 2
    if blockers and a.force:
        print(f"warning: --force set, sending despite {len(blockers)} "
              f"unresolved blocker(s)", file=sys.stderr)

    today = a.as_of or date.today()
    store = Store(cfg.state_path)
    notifier = notify.ConsoleNotifier() if a.dry_run else notify.build(cfg)
    print(f"notifier: {notifier.describe()}", file=sys.stderr)

    # ---- fetch (failure here is itself a signal, never a crash) ----
    nights, fetch_error = {}, None
    try:
        from .oura import fetch_sessions
        lookback = max(cfg.window_days, cfg.rhr_baseline_days) + 2
        sessions = fetch_sessions(cfg, today - timedelta(days=lookback), today)
        nights = sessions_to_nights(sessions, count_types=cfg.count_session_types)
        if nights:
            store.last_data_day = max(nights)
    except Exception as exc:                      # noqa: BLE001 — deliberate
        fetch_error = exc
        print(f"fetch failed: {exc}", file=sys.stderr)

    # ---- 1. dead-man's switch, always, first ----
    sil = evaluate_silence(store.last_data_day, today, store.last_silence_notice,
                           silence_days=cfg.silence_days,
                           repeat_every_days=cfg.silence_repeat_days)
    print(f"dead-man: {sil.reason}", file=sys.stderr)
    if sil.fire:
        notify.fan_out(notifier, cfg.silence_recipients,
                       silence_body(sil, "tier1"), "tier1-silence")
        for r in cfg.tier2:
            notify.fan_out(notifier, [r], silence_body(sil, "tier2"), "tier2-silence")
        if not a.dry_run:
            store.last_silence_notice = today
            store.save()
        return 0 if fetch_error is None else 1

    if fetch_error is not None:
        if not a.dry_run:
            store.save()
        return 1

    # ---- 2. debt ----
    point = compute_debt(nights, today, baseline_need=cfg.baseline_need,
                         window_days=cfg.window_days, surplus_cap=cfg.surplus_cap,
                         min_observed_days=cfg.min_observed_days)
    flag = (None if cfg.rhr_mode == "OFF"
            else rhr_elevated(nights, today, delta_bpm=cfg.rhr_delta_bpm,
                              baseline_days=cfg.rhr_baseline_days))
    last = store.last_alert
    last_day = date.fromisoformat(last["day"]) if last else None
    decision = evaluate_debt(point, store.streak(), store.last_tier, last_day, today,
                             tiers=cfg.tiers, consecutive_days=cfg.consecutive_days,
                             cooldown_days=cfg.cooldown_days)

    if decision.reset:
        store.set_streak(0)
        store.last_tier = None          # ladder resets below the lowest tier
    else:
        store.set_streak(store.streak() + 1)

    tier_txt = (f"{decision.tier['hours']:g} h {decision.tier['label']}"
                if decision.tier else "under tiers")
    print(f"debt {point.debt_hours:.1f} h over {point.observed_days}/"
          f"{point.window_days} nights - {tier_txt} - streak {store.streak()} - "
          f"{'FIRE' if decision.fire else 'hold'} - {decision.reason}", file=sys.stderr)

    if decision.fire:
        tn = nights.get(today)
        notify.fan_out(notifier, cfg.tier1,
                       tier1_body(point, decision, baseline=cfg.baseline_need,
                                  rhr_flag=flag,
                                  rhr_today=tn.rhr if tn else None,
                                  rhr_base=rhr_baseline(nights, today - timedelta(days=1),
                                                        cfg.rhr_baseline_days)),
                       "tier1")
        notify.fan_out(notifier, cfg.tier2, tier2_body(point, decision), "tier2")
        if not a.dry_run:
            store.record_alert(today, point.debt_hours, decision.kind)
            store.last_tier = decision.tier["hours"]

    if not a.dry_run:
        store.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
