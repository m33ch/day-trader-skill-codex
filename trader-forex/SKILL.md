---
name: trader-forex
description: >-
  Forex workflow for fiat currency pairs such as EUR/USD, GBP/USD and USD/JPY.
  Use for FX scanning, long/short entry signals, pip-based stops, lot sizing,
  account-currency risk, 24/5 sessions, macro-event checks, paper tickets and
  cTrader/TopFX, OANDA or manual integration drafts. Use instead of stock
  calculations whenever the user asks about currency trading. This skill
  supports a local demo ledger and broker payload preparation, not an
  authenticated brokerage connection or automatic live order submission.
---

# Forex workflow

Use the isolated forex engine for fiat currency trading. Keep stock calculations
and state separate. Prices are quote currency per base unit. A quantity is BASE
currency units, and lots depend on the symbol's lot size. All equity, fee and
loss settings are in the ACCOUNT currency. Never label an EUR balance as USD.

Resolve this SKILL.md file's directory to an absolute path and set `SKILL_DIR`
to that path in each shell call below. `SKILL_DIR` is a shell variable you set,
not a Codex-provided environment variable. Keep the working directory at the
user's trading project. All twelve skill folders must be installed together
when using the other skills' `--market forex` entry points.

## Set up once

```bash
python3 "${SKILL_DIR}/forex.py" init
python3 "${SKILL_DIR}/forex.py" clock
```

`init` copies `config.example.json` into `trader/forex/config.json` and refuses
to overwrite existing settings. Review every example setting with the user.
The default profile is cTrader / TopFX / demo, USD account, with example
instrument defaults. Those are NOT verified TopFX contract specifications.
Use `broker.symbols["EUR/USD"]` etc. to override each broker symbol's lot size,
minimum/maximum units, step, leverage, price digits, pip position and minimum
stop distance. Broker fees, tiered margin, holidays and maintenance must be
verified separately. The engine estimates fixed-leverage margin only.

`broker.adapter` can be `ctrader`, `oanda`, or `manual`. These adapters prepare
DRAFT_ONLY messages for separate integrations; they do not connect or submit.
No keys, passwords or OAuth tokens belong in the project or chat.

## Import trustworthy inputs

- Research bars: the engine can read unofficial Yahoo currency charts, or
  normalized broker OHLC exports via `--offline FILE` (see `DATA.md`).
  They are research prices, never a substitute for executable bid/ask.
- Execution inputs: import current broker bid/ask, actual quote timestamp and
  source. Required account-conversion pairs must also be present and fresh.
  Old or future-dated quotes block the workflow, including conversion quotes.
- Macro calendar: import a sourced, verified calendar in
  `trader/forex/calendar.json` with publication timestamp and events. Missing,
  unverified or stale calendars block entries by default. Never invent events
  or mark an empty example verified. The news command reads this imported
  calendar; it does not fetch live central-bank news.

```bash
# Replace every value with the actual broker quote and its UTC timestamp.
python3 "${SKILL_DIR}/forex.py" quote EUR/USD --bid BID --ask ASK --at TIMESTAMP --source "TopFX cTrader demo"
python3 "${SKILL_DIR}/forex.py" news --pair EUR/USD
```

For JPY quotes the default pip is 0.01; for other supported fiat pairs it is
0.0001. Broker-specific `pip_position` overrides that default. Gold, crypto,
indices and futures are outside this implementation.

## One plan at a time

```bash
python3 "${SKILL_DIR}/forex.py" scan orb --pairs EUR/USD,GBP/USD
python3 "${SKILL_DIR}/forex.py" entry check --pair EUR/USD --side buy --write
python3 "${SKILL_DIR}/forex.py" exit plan --pair EUR/USD --side buy --stop-pips 20 --reward-risk 2 --write
python3 "${SKILL_DIR}/forex.py" simulate run --write
python3 "${SKILL_DIR}/forex.py" size --write
python3 "${SKILL_DIR}/forex.py" risk
python3 "${SKILL_DIR}/forex.py" broker show
```

Use `--side sell` for a short. Check the signal status, trigger time, missing
bars and stale-signal flag; a wick or an incomplete candle is not a trigger.
Never imply that an old trigger is a current entry. A plan can be made manually
from a user-specified setup, but say so instead of inventing a signal.

`scan orb` uses the configured UTC opening range, not the NYSE opening bell.
Default session start is 08:00 UTC. `scan daily --interval 1d` compares the
last completed close against the previous twenty daily bars. Forex has no
centralized volume, so the FX workflow does not use stock volume filters.

Simulation bootstraps historical OHLC transitions from at least twenty dates
and excludes every bar ending after the plan's creation. It caps the horizon
at flat-by. Frequencies describe sampled price paths, not a prediction, an
out-of-sample strategy backtest, or net returns. Spread changes, conversion
changes, actual fills and gaps are not modeled by the simulation.

Sizing rounds DOWN to the broker's base-unit step. Risk includes stop distance,
configured per-side slippage at both entry and exit, round-trip commission
and optional overnight swap reserve,
converted conservatively to the account currency. The gross target ratio can
be 2R while the net ratio is smaller after costs. Spread, margin, quote age,
macro blackouts, daily entry count and cumulative reserved risk can BLOCK it.
Never relax a limit to obtain PASS. A stop fill can exceed the modeled loss.

## User consent and broker drafts

Show the exact ticket and ask for an explicit yes for that one order. Only
record the user's actual reply:

```bash
python3 "${SKILL_DIR}/forex.py" broker approve --reply "yes"
python3 "${SKILL_DIR}/forex.py" broker export
```

Changes to the order, quotes, config, balance or positions invalidate the PASS
and approval context. Limits and calendar windows are checked again at every
broker decision. A new risk check is required after each fill or state change.

For cTrader, export also requires account-specific `account_id` and verified
symbol metadata from the actual broker environment. Never copy symbol IDs
from another broker or fabricate them. See `DATA.md` for wire units and the
verified official protocol references. Broker authentication, OAuth and actual
submission require a separate connected adapter with its own tool approval;
this engine never sends the draft. Local paper IDs are not broker position IDs.

## Local paper ledger and monitoring

```bash
python3 "${SKILL_DIR}/forex.py" broker paper-fill
python3 "${SKILL_DIR}/forex.py" broker log
python3 "${SKILL_DIR}/forex.py" alerts add --from-plan
python3 "${SKILL_DIR}/forex.py" alerts check
python3 "${SKILL_DIR}/forex.py" broker flatten --position-id LOCAL_POSITION_ID
# Then risk, show, the user's new yes, approve, and paper-fill for the exit.
```

`paper-fill` requires demo mode and exact consent; it simulates execution from
the imported quote plus configured slippage. It never reaches cTrader/TopFX or
any other broker. It logs units, fees, reserved risk, P&L and a conservative
swap estimate. Actual swap schedules (including triple swap) need broker data.
Daily risk sums the reserved risk of all entries, including closed winners.
Do not mix real account balances or positions into this local demo ledger.

Alerts use the executable exit side (bid for long, ask for short), ring once,
and never sell anything. No process runs after a single check. Request a
bounded watch or an explicitly user-configured scheduler for repeated checks;
never schedule order submission. The indicative FX week is Sunday 17:00 to
Friday 17:00 New York, with US DST. Rollover blackouts are checked separately.
Broker closures can differ. Strategy session and flat-by settings are UTC and
do not automatically follow London daylight saving time.

## Scope and safety

Education and tooling, not financial advice. Start with a local demo account.
No order or paper fill without a fresh risk PASS and the user's explicit yes.
No unattended order submission and no claim that an exported JSON is a placed
order. Require separate approval in any future connected broker integration.

Stock earnings/SEC analysis and Congress disclosures are equity-only tools;
`trader-analyst` and `trader-pelosi` are not FX macroeconomic indicators.
