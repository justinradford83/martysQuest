# Sleep-debt monitor

Pulls sleep from Oura, computes a trailing sleep-debt figure, and notifies when
it crosses a threshold **fitted to your own history** rather than a generic one.

```
pip install -r requirements.txt

export OURA_CLIENT_ID=...        # never put these in config.yaml
export OURA_CLIENT_SECRET=...

python -m sleepdebt.authorize            # 0. one-time: mint the refresh token
export OURA_REFRESH_TOKEN=...            #    from what it prints

python -m sleepdebt.preflight            # what is still blocking go-live
python -m sleepdebt.oura --verify        # 1. confirm the field mapping
python -m sleepdebt.history              # 2. see how often the tiers are hit
#    edit config.yaml: real numbers, notifier.backend: twilio
python -m sleepdebt.preflight            # 3. must print READY
python -m sleepdebt.run --dry-run        # 4. decide, send nothing
python -m sleepdebt.run                  # 5. live
```

## Go-live gates

`python -m sleepdebt.run` **refuses to send** while any of these is unresolved,
and exits 2:

- no tiers configured
- any recipient still on a placeholder number
- a placeholder Twilio sending number

`--dry-run` always works, and the dead-man's switch is unaffected — it does not
depend on the tiers, so a half-configured system still tells you when data stops
arriving. `--force` overrides, and says so loudly; it exists for testing, not
for going live.

## Authorisation

Oura v2 is OAuth2-only since personal access tokens were retired in December
2025. `client_id` and `client_secret` alone cannot read your data — an
authorization-code round trip through a browser is what mints the refresh
token, and that token is what the job uses.

`python -m sleepdebt.authorize` does that round trip: it opens the consent page,
catches the redirect on `localhost`, verifies the `state` parameter, exchanges
the code, and prints the refresh token. Nothing is written to disk.

The `redirect_uri` in config.yaml must be registered **exactly** on your app in
the Oura developer console first, or Oura refuses the request.

Credentials live in the environment only. If a secret ever reaches a chat
window, a log, a screenshot, or a commit, treat it as burned and rotate it —
Oura lets you regenerate the secret without re-registering the app, and the
refresh token survives a secret rotation only if Oura says it does, so re-run
`authorize` afterwards if the job starts failing to refresh.

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

## History

`python -m sleepdebt.history` backfills ~2 years and writes to `history/`:

| file | what |
|---|---|
| `nightly.csv` | per-night hours, deficit, running debt, tier reached, coverage |
| `sleep_debt.svg` | the curve |
| `report.json` | days spent at each tier, and every alert that *would* have fired |

Not a gate — the tiers are fixed in config. It exists so you can see how often
each tier is actually reached, and therefore how often you would be messaged.
If the replay says more than one alert a month, the tiers are too low for your
baseline, and the report says so.

Re-run without re-hitting the API:
`python -m sleepdebt.history --from-csv history/nightly.csv`

## Alerting

Three debt tiers, set in `config.yaml`:

```yaml
tiers:
  - {hours: 15.0, label: "building"}
  - {hours: 25.0, label: "high"}
  - {hours: 35.0, label: "severe"}
```

- Debt must sit at or above the lowest tier for `consecutive_days` before
  anything fires. Suppresses single-night noise.
- **Crossing up into a higher tier fires immediately**, cooldown or not — a
  worsening picture always gets through.
- Staying at the same tier waits out `cooldown_days`.
- Easing to a lower tier does not re-alert.
- **Dropping below the lowest tier resets the ladder**, so a later climb warns
  again from the bottom.

Resting heart rate is no longer a trigger — the tiers are. With `rhr.mode: ON`,
an elevation over the trailing baseline is added to the tier-1 message as
corroborating detail.

### Picking tiers that discriminate

Debt is a sum over the window, so tier spacing is far tighter than it looks. At
a 6.5 h baseline over 14 days:

| tier | equivalent average night |
|---|---|
| 15 h | 5.43 h |
| 25 h | 4.71 h |
| 35 h | 4.00 h |

These sit about **43 minutes of average nightly sleep apart**, which is wide
enough that the ladder actually discriminates.

The earlier 6/8/10 h tiers did not: they spanned only seventeen minutes
(6.07 / 5.93 / 5.79 h), so anyone averaging under ~5.8 h pinned to the top tier
permanently and escalation carried no information.

If you change `baseline_need_hours` or `window_days`, recompute — a tier is
`(baseline - nightly_average) x window_days`, so both inputs move it. Run
`history` and read `alerts_per_year` before trusting any spacing.

### Dead-man's switch

No data for `silence_days`. Lives in its own `deadman.yaml`, has no enable flag,
and the loader refuses to start without it. Silence is signal, so it cannot be
switched off while tuning tiers.

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

## Field mapping

The names in `oura.py` are the documented ones — `total_sleep_duration`
(seconds), `day`, `bedtime_start`, `bedtime_end`, `type`, `average_heart_rate` —
but they have **not** been checked against a live response from this account.

`python -m sleepdebt.oura --verify` closes that gap: it fetches real records,
runs them through the parser, and reports whether the mapping held. It flags
implausible durations (which would mean the units are not seconds), implausible
heart rates (wrong field), and any session type not in `count_session_types`
that would otherwise be silently dropped. `--dump` prints the raw JSON.

Known `type` values: `long_sleep`, `sleep`, `late_nap`, `rest`. Type matching is
case-insensitive and by substring, so `nap` in config also admits `late_nap`.
