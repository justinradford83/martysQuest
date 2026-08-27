# Sleep-debt monitor

Pulls sleep from Oura, computes a trailing sleep-debt figure, and notifies when
it crosses a threshold **fitted to your own history** rather than a generic one.

```
pip install -r requirements.txt

export OURA_CLIENT_ID=...        # never put these in config.yaml
export OURA_CLIENT_SECRET=...
export OURA_REFRESH_TOKEN=...

python -m sleepdebt.oura --dump          # 1. confirm the response shape
python -m sleepdebt.calibrate            # 2. fit the threshold — do not skip
python -m sleepdebt.run --dry-run        # 3. decide, send nothing
python -m sleepdebt.run                  # 4. live
```

## The computation

```
nightly_deficit(d) = baseline_need - total_sleep(d)
sleep_debt         = sum(nightly_deficit(d) for observed d in trailing window)
```

Surplus nights offset debt and are **uncapped** by default: a 12 h night against
a 6.5 h baseline credits 5.5 h back. Set `debt.surplus_credit_cap_hours` to cap it.

Days missing from the API are **excluded, never imputed**. That has a consequence
worth stating plainly: because debt is a sum, a gap can only make the number look
*better*. A fortnight with four missing nights sums ten terms, not fourteen. Two
things guard against reading that as good news — `min_observed_days`, below which
debt alerting is suppressed as too sparse to judge, and the dead-man's switch,
which is what actually catches the gap.

## Calibration

`python -m sleepdebt.calibrate` backfills ~2 years, then writes to `calibration/`:

| file | what |
|---|---|
| `nightly.csv` | per-night hours, deficit, running debt, coverage |
| `threshold_sweep.csv` | every candidate threshold vs episodes caught and false-alarm rate |
| `sleep_debt.svg` | the curve, with episode lead-ins shaded |
| `report.json` | the above, machine-readable |

Put your real episode dates in `calibration.episodes` first — exact dates, not
months, since the report covers the 21 days preceding each.

The recommendation is the lowest false-alarm rate among thresholds that catch
every annotated episode, ties broken toward the lower threshold for earlier
warning. Read `threshold_sweep.csv` before accepting it; the trade-off is yours
to make, and a threshold that fires eleven times a year will get muted and then
deleted.

Re-tune without re-hitting the API: `python -m sleepdebt.calibrate --from-csv calibration/nightly.csv`

## Alerting

1. **Debt** over `threshold_hours` for `consecutive_days` running.
2. **RHR corroboration** — `AND`, `OR`, or `OFF`. `OR` is the default: high debt
   alone, or lower debt plus RHR elevated over its 30-day baseline. RHR that
   cannot be determined is `None` and never silently counts as "not elevated".
3. **Dead-man's switch** — no data for `silence_days`. Lives in its own
   `deadman.yaml`, has no enable flag, and the loader refuses to start without
   it. Silence is signal, so it cannot be switched off while tuning thresholds.

Cooldown suppresses repeats for `cooldown_days`, broken by a further
`escalation_hours` of debt. No daily nagging; escalation still gets through.

Two message tiers. Tier 1 gets numbers. Tier 2 gets plain language, no clinical
framing, and an explicit ask.

## Swapping the channel

`notify.Notifier` is a two-method protocol — `send(Message) -> bool` and
`describe() -> str`. `TwilioNotifier` and `ConsoleNotifier` implement it; add
another and return it from `notify.build`. `console` is the default so a
half-configured deploy is quiet rather than texting real phones.

## Scheduling

Any cron. The job is idempotent per day — state lives in `state.json`, which
must persist between runs.

```
0 10,15,20 * * *  cd /path/to/sleepdebt && /usr/bin/python3 -m sleepdebt.run >> run.log 2>&1
```

## Unverified

The Oura field names in `oura.py` are the documented ones but have **not** been
checked against a live response from this account — Oura's docs were unreachable
from the machine this was written on. `parse_session` fails loudly and names the
keys it actually received rather than guessing. Run `--dump` first.
