# The Day Trader skill

12 Codex skills for stocks and forex: scan thousands of charts, track breaking news, time every entry and exit, and replay every outcome before you risk a dollar.

MIT licensed, standard-library Python, with no pip dependencies. Stock
research reads public Yahoo Finance charts, Nasdaq Trader symbol lists, SEC
EDGAR and House Clerk disclosures. Forex research supports Yahoo currency
charts and imported broker bars; planning also needs current broker bid/ask
and a sourced macro calendar. Brokerage accounts and authenticated external
integrations have their own setup requirements.

One scans thousands of charts for setups. Another tracks breaking news on
every stock you watch. One watches a setup bar by bar and tells you the moment
to get in, and another sets your stop and your target before you do. And the
craziest one is the Time Machine: before you risk a dollar, it replays the
rest of the session 10,000 times from the stock's own recent bars, plus every
past day the same setup fired, and tells you the honest odds.

And no trade goes through until you say yes. Nothing in this pack can place
an order on its own. Paper trading is the default.

> **Looking for the original Trader skill?** Copy an investor's 13F, the
> stock screener, tax lots and rebalancing live in
> [github.com/Jakeschincariol/trader-skill](https://github.com/Jakeschincariol/trader-skill).
> This repo is its day-trading sequel: the Trader skill v2.

## Install in Codex

Clone this fork and open it in Codex:

```bash
git clone https://github.com/m33ch/day-trader-skill-codex.git
cd day-trader-skill-codex
codex
```

The checked-in `.agents/skills/` directory links to the twelve skill folders
at the repository root. Codex follows those links, so the skills are available
in this checkout without copying or maintaining duplicate scripts.
Use a checkout that preserves Git symlinks (for example Linux, macOS, or WSL).

To install all twelve skills across projects on your Mac, run this from the
checkout in Terminal:

```bash
bash install-skills.sh
```

The installer copies the source folders into `$HOME/.agents/skills` and backs
up any existing folders or symlinks under `$HOME/.agents/skill-backups/`.
It does not overwrite your trading project's configuration or ledger. Install
the complete pack together: forex entry points in the other nine tools need
the sibling `trader-forex` folder. Copying source folders also works on systems
without symlink support.

For one project only, choose that project's absolute skill-directory path:

```bash
bash install-skills.sh "/absolute/path/to/project/.agents/skills"
```

To update a personal installation later, return to this checkout and run:

```bash
git pull --ff-only
bash install-skills.sh
```

Installed copies do not automatically update when the checkout changes. Codex
detects newly installed skills automatically; restart it if they do not appear.

In Codex CLI or the IDE, type `$` to select a skill or use `/skills`. For example:

```text
$trader-scanner scan TSLA, NVDA and AMD for opening-range breakouts
$trader-risk check the proposed orders
```

Start Codex in your trading project's working directory. All relative `trader/`
files are read and written there. The skill instructions resolve each Python
script from its installed skill directory; they do not depend on a built-in
skill-directory environment variable. You can also run the scripts directly
from this checkout, as shown below.

See the [Codex skills documentation](https://developers.openai.com/codex/skills/).

The tools need Python 3.8 or newer. Nothing to pip install. The SEC tools
(news filings, the analyst) ask you to identify yourself: put your name and
email in `trader/config.json` (`"sec_user_agent": "Your Name you@example.com"`)
or in the `SEC_USER_AGENT` environment variable.

## The twelve

| command | job | what it does |
| --- | --- | --- |
| `$trader-forex` | Forex workflow | Currency pairs, pip/lot sizing, account-currency risk, 24/5 calendar and configurable cTrader/TopFX, OANDA or manual drafts. |
| `$trader-scanner` | Chart Scanner | Scans thousands of charts for setups: opening-range breakouts, flags, oversold bounces. |
| `$trader-news` | News Monitor | Tracks breaking news on every stock you watch and flags what moves it. |
| `$trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. |
| `$trader-exit` | Exit Planner | Sets your stop and your target before you enter, so you know when to get out. |
| `$trader-timemachine` | Time Machine | Replays stock setups; forex mode bootstraps historical price paths with a configurable sample count. |
| `$trader-risk` | Risk Manager | Checks every trade against your limits and stops anything over them. |
| `$trader-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade. |
| `$trader-broker` | Order Executor | Stock tickets for a configured broker MCP; forex local paper fills and integration drafts, each requiring exact consent. |
| `$trader-alerts` | Price Alerts | Rings when a price, a stop or the opening bell hits. |
| `$trader-analyst` | Earnings Analyst | Reads earnings reports and sums up what changed. |
| `$trader-pelosi` | Pelosi Tracker | Reads Congress's stock-trade disclosures the minute they are filed. |

## Forex, including cTrader / TopFX

The new `$trader-forex` skill adds a separate forex engine. The nine applicable
skills also accept `--market forex`; the default CLI path remains stocks.
Stock earnings and Congress disclosures retain their equity-only scope.

After installing, open your trading project in Codex and start with:

```text
$trader-forex configure a demo project for cTrader / TopFX.
Ask for my account currency, broker symbol specifications, costs and risk limits
before saving them. Explain which quote and calendar inputs I need to import.
```

Codex resolves the installed scripts and keeps generated data in the current
project. The shell examples below run from the repository checkout; if you
run scripts directly from another project, use their absolute installed paths.
Initialize the isolated FX configuration:

```bash
python3 trader-forex/forex.py init
python3 trader-forex/forex.py clock
```

Review `trader/forex/config.json`: account currency, equity, risk/fee amounts in
that currency, allowed pairs, leverage/margin caps, UTC session, spread limits,
and symbol-specific lot size / minimum / maximum / step. The example profile
is **cTrader / TopFX / demo**; its numerical defaults are illustrative, not
verified TopFX specifications. `broker.adapter` can be `ctrader`, `oanda`, or
`manual`. Gold, crypto, indices and futures are outside the FX engine.

Import current **broker bid/ask**, actual quote timestamps and the conversion
pairs needed for the account currency. Research bars come from normalized
broker exports or unofficial Yahoo currency charts; a research close is not an
executable quote. The macro check uses an imported, verified calendar and
blocks entries when it is missing/stale by default. See
[forex data contracts and broker setup](trader-forex/DATA.md).

Then use the pipeline (examples require those current inputs):

```bash
python3 trader-scanner/scan.py --market forex orb --pairs EUR/USD,USD/JPY
python3 trader-news/news.py --market forex --pair EUR/USD
python3 trader-entry/entry.py --market forex check --pair EUR/USD --side buy --write
python3 trader-exit/exit.py --market forex plan --pair EUR/USD --side buy --stop-pips 20 --reward-risk 2 --write
python3 trader-timemachine/timemachine.py --market forex run --write
python3 trader-budget/size.py --market forex --write
python3 trader-risk/check.py --market forex
python3 trader-broker/ticket.py --market forex show
```

`--side sell` supports FX shorts. Sizing includes pip value, conservative
account-currency conversion, slippage, round-trip commission, optional swap
reserve, volume steps and existing paper-position margin. Risk also checks
quotes, spreads, macro windows, rollover and cumulative daily reserved risk.
The indicative week is Sunday 17:00 to Friday 17:00 New York with US DST;
strategy times are explicitly UTC, and broker holidays may differ.

The complete execution loop is a **local paper ledger**: exact risk PASS,
user yes, approval, then a simulated fill. After the user actually replies
yes for the displayed ticket, run:

```bash
python3 trader-forex/forex.py broker approve --reply "yes"
python3 trader-forex/forex.py broker paper-fill
python3 trader-forex/forex.py broker log
python3 trader-forex/forex.py alerts add --from-plan
python3 trader-forex/forex.py alerts check
```

Use `python3 trader-forex/forex.py broker flatten --position-id ID` with the
local paper position ID from the log to prepare an exit, then repeat risk,
ticket review and fresh consent before the closing paper fill. Alerts report
stop, target and flat-by from the executable exit side; they never close a
position. Each alert command runs one check and requires current quotes.

`python3 trader-forex/forex.py broker export` prepares a **DRAFT_ONLY**
integration message after approval.
cTrader needs verified account-specific symbol IDs/volume metadata and emits
its hundredths-of-unit volume and relative protection distances correctly.
The adapters do not authenticate, send orders, or synchronize real accounts.
Actual cTrader OAuth, broker fills and live margin require a separate connected
integration. No live account is connected by installing these skills.

Simulation reports historical-bootstrap price-path frequencies, not predicted
returns or a validated strategy. It excludes bars after the plan's creation,
requires twenty historical dates and caps the horizon at flat-by.

The broker settings and data are separate for each market:

| purpose | stocks | forex |
| --- | --- | --- |
| Configuration | `trader/config.json` | `trader/forex/config.json` |
| Order proposal | `trader/orders.json` | `trader/forex/orders.json` |
| Execution state | `trader/state/broker.json` | `trader/forex/state.json` (local demo only) |
| Prices and macro data | `trader/prices.json`, stock news feeds | `trader/forex/quotes.json`, `trader/forex/calendar.json` |
| Broker route | Configured stock MCP connection | cTrader, OANDA or manual draft exporter |

Switching an adapter name does not authenticate a broker. cTrader account and
symbol IDs must come from the actual TopFX environment; contract defaults in
this repo are examples. Forex risk clearance is restricted to demo mode.

## Stock workflow

The stock workflow writes directly under `trader/` in your project. Each skill
reads what the last one wrote. Forex uses its separate subdirectory described
above:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by the close                                       (while it is open)
news / analyst / pelosi                         (on request, or a user-configured scheduler)
```

Checks are single runs unless a bounded watch process is active. Recurring
checks need a scheduler explicitly configured by the user; no order submission
is allowed from scheduled or unattended tasks.

An example, the trade in the reel: Tesla, Monday October 19 2026, real data.

1. **`$trader-scanner`** at 10:15 AM ET: of the 231 charts in the reel's
   scan, 42 had closed above their 15-minute opening range. TSLA is one: range 354.47 to 362.18,
   the 10:10 candle closed at 364.18, 0.55 % over the top.
2. **`$trader-news`**: the morning's TSLA headlines, in New York time
   ("Tesla-SpaceX Merger Mania Strikes Wall Street Ahead of Earnings", 8:04,
   tagged earnings and deal). Tesla reports this week: a day trade is out
   before the close, so it is not exposed to the report.
3. **`$trader-entry`** watched TSLA bar by bar. At 10:12 it said waiting (the
   10:10 bar was still forming). At 10:15 it said triggered: the first
   5-minute close above the range, 364.18.
4. **`$trader-exit`** set the plan before the entry: stop at the range's
   midpoint, 358.32, so 5.86 a share at risk; target one times that risk,
   370.04; flat by 3:55 PM either way.
5. **`$trader-timemachine`** replayed the rest of the day 10,000 times, each
   5-minute candle drawn from TSLA's own candles at that time of day over the
   previous 59 sessions (seed 19, so anyone can repeat it): target first
   29.4 %, stop first 25.5 %, still open at the close 45.1 %. It also re-ran
   the same rule on those 59 real sessions: it fired 33 times, 13 hit the
   target, 15 the stop, 5 neither. Its verdict: close to a coin flip. That is
   the point of looking before you risk a dollar.
6. **`$trader-budget`** with a $5,000 account and a $50 max loss per trade:
   50 / 5.86 = 8 shares ($2,913.44). If the stop fills at 358.32, the loss is
   $46.88. It writes one bracket order: buy 8 TSLA, stop-loss 358.32,
   take-profit 370.04.
7. **`$trader-risk`** checks it against your limits: the max loss, the daily
   loss limit, trades per day, reward to risk, the entry window, position
   size and cash. PASS or BLOCK, with the reason.
8. **`$trader-broker`** shows you the ticket in plain words. You type yes for
   that exact order. Codex uses the configured Alpaca MCP server (paper
   account by default), with separate approval for the exact tool call.
   Without separate tool approval, it stops at the ticket.
9. **`$trader-alerts`** rings on the stop, the target and at 3:55 PM to
   flatten whatever is still open.

## Stock CLI tools and verification

All standard-library Python. The data tools read public endpoints and are
polite about it.

```bash
python3 trader-scanner/scan.py universe                                # ~5,900 US stocks, Nasdaq Trader
python3 trader-scanner/scan.py daily                                   # pre-market setups, whole universe
python3 trader-scanner/scan.py orb --at 10:15                          # opening-range breakouts so far
python3 trader-news/news.py --tickers TSLA --hours 12                  # headlines, 8-Ks, today's move
python3 trader-entry/entry.py check TSLA --setup orb                   # waiting / triggered / expired
python3 trader-entry/entry.py watch TSLA --every 30                    # bar by bar until it triggers
python3 trader-exit/exit.py plan --stop or-mid --target 1R --write     # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python3 trader-budget/size.py --write                                  # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-alerts/alerts.py add --from-plan                        # stop, target, flat by close
python3 trader-alerts/alerts.py check                                  # rings what hit, once
python3 trader-analyst/facts.py TSLA                                   # quarters, growth, EPS
python3 trader-pelosi/ptr.py index --member pelosi                     # her PTRs this year
```

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on fixtures cut from real data: TSLA's 5-minute bars
for the 60 days to October 19 2026, the 10:15 AM opening-range scan, Yahoo's
TSLA headlines that morning, the House Clerk's 2026 index, real PTR text and
real XBRL facts. They reproduce every number in the stock example above.
The forex tests use explicitly synthetic data to verify pip/lot arithmetic,
account-currency conversions, long/short stop losses, costs, margin, session
boundaries, invalidated approvals and broker draft units. These tests do not
establish a live broker connection or validate a trading strategy.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
Most people who day trade lose money. You can lose money. Decide for
yourself, and talk to a licensed professional if you need advice.

**No trade goes through until you say yes.** Only `$trader-broker` can lead to
an order, and only after `$trader-risk` passes that exact order and you type
yes for it in the chat. Configure the broker MCP server with
`default_tools_approval_mode = "prompt"` in Codex and require separate
approval for the exact tool call. If the client cannot enforce that, prepare
the ticket for manual submission. Stock defaults disable options, margin and
shorting. The separate forex demo configuration permits long/short signals
within its configured leverage and margin caps; its adapters export drafts.

**Paper first.** Both [stock defaults](trader-budget/config.example.json) and
[forex defaults](trader-forex/config.example.json) set `"paper": true`. The
Alpaca MCP example selects a paper account for the stock workflow. Merge
[trader-broker/config.example.toml](trader-broker/config.example.toml) into your
Codex config to connect Alpaca; do not overwrite your existing settings. Keys
are forwarded from the Codex process environment, never entered in chat.
Broker setup needs a current Codex client with MCP tool approval support;
see [the broker skill](trader-broker/SKILL.md) for stock setup and IBKR limitations.
Forex paper fills stay local; they are not fills in a cTrader demo account.

**The Time Machine is a simulation, not a prediction.** The stock implementation
uses the stock's own last 60 days of 5-minute bars. The forex implementation
uses the separate bootstrap method described above. The future can do things those 60
days never did: an earnings report, a halt, a headline. Treat its odds as a
reality check, never as a promise.

**A stop is a plan, not a guarantee.** In a fast market or across a gap, a
stop order can fill well past its price. The max loss shown is the loss at
the stop price.

**The data is free and unofficial.** Yahoo Finance's chart endpoint is not an
official API: it can lag, change or rate-limit you. The scanner and the entry
timer read 1-minute or 5-minute bars, not every tick. Check anything that
matters against your broker before you act on it.

**US stock day-trading rules.** FINRA removed the $25,000 pattern-day-trader minimum
on June 4, 2026 (Regulatory Notice 26-10), and brokers may phase the change
in until October 20, 2027, so yours may still apply the old rule. If it does,
set `"pdt_guard": true` and `$trader-risk` counts your day trades.

**Congress's trades are public, not inside information.** Members must disclose
trades over $1,000 within 45 days (the STOCK Act). The Pelosi skill reads those
public filings the minute they post.

**US stock taxes.** Day-trading gains are short-term gains, and frequent trading makes
wash sales likely. Nothing here is tax advice.

## Migration from Claude Code

This fork replaces Claude plugin installation with Codex skill discovery,
`$skill-name` invocation, explicit script path resolution, and a Codex TOML MCP
example. It removes the Claude marketplace manifests and desktop JSON config.
The original eleven stock tools, their shared `trader/` data formats, offline
fixtures and risk/consent checks are preserved. A twelfth skill provides the
separate forex engine and opt-in dispatch from the applicable stock tools. No brokerage account is connected by
installing the skills.

## Files

```
trader-forex/       SKILL.md, forex.py, config.example.json, DATA.md
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json, targets.example.json
trader-broker/      SKILL.md, ticket.py, config.example.toml
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader-analyst/     SKILL.md, facts.py, metrics.example.json
trader-pelosi/      SKILL.md, ptr.py
.agents/skills/     Codex discovery links to the twelve source folders
install-skills.sh  Full-pack installer with backups of existing skills
tests/              unittest suite + offline fixtures
```

MIT. Made by [Jake Schincariol](https://opusjake.ai).
