# Forex data and broker contracts

All examples below are synthetic schema examples, not current quotes, a verified
news calendar, broker credentials or TopFX contract specifications. Timestamps
must have an explicit UTC offset; replace them with the source timestamp.

## Bid/ask snapshot

`trader/forex/quotes.json`:

```json
{
  "market": "forex",
  "quotes": {
    "EUR/USD": {"bid": 1.1000, "ask": 1.1002, "at": "2026-11-16T10:00:00Z", "source": "synthetic example"},
    "USD/JPY": {"bid": 150.00, "ask": 150.02, "at": "2026-11-16T10:00:00Z", "source": "synthetic example"}
  }
}
```

The `slippage_pips` setting is reserved on each side (entry and exit).
Risk conversion also revalues the traded pair at the assumed stop fill; other
conversion pairs are held at their current quotes, so future cross-rate moves
can still change the realized account-currency loss.

All quotes in the snapshot must be fresh, including any cross used to convert
account risk. Remove unused old entries instead of relabeling old timestamps.
There is no inference of bid/ask from OHLC, no constant fake spread, and no
implicit 1:1 conversion. Fiat pairs accept EUR/USD, EURUSD, EUR_USD, or EURUSD=X.
Use normalized fiat pairs internally even when the broker's symbol has a suffix.

## Bars

`--offline FILE`, or `--offline DIRECTORY` holding `EURUSD.json` etc.:

```json
{
  "market": "forex", "pair": "EUR/USD", "interval_seconds": 300,
  "source": "synthetic example; replace with broker export source",
  "bars": [
    {"time": "2026-11-16T08:00:00Z", "open": 1.1000, "high": 1.1005, "low": 1.0995, "close": 1.1002}
  ]
}
```

Times are bar OPEN times, strictly increasing. Only bars whose full interval has
ended are used. Supported intervals: 60, 300, 900, 3600, 86400 seconds. Imported
Yahoo responses must identify the requested symbol as instrumentType CURRENCY.
Range signals require every expected opening-range bar. Daily bars can differ
by provider's day boundary; a twenty-bar comparison is not a universal FX day.

## Macro calendar

`trader/forex/calendar.json`:

```json
{
  "source": "REPLACE_WITH_ACTUAL_PROVIDER_AND_LINK",
  "as_of": "2026-11-16T09:50:00Z",
  "verified": false,
  "events": [
    {"currency": "USD", "at": "2026-11-16T10:00:00Z", "impact": "high", "title": "SYNTHETIC EXAMPLE ONLY"}
  ]
}
```

Only mark verified after checking the actual source and coverage. The engine
checks freshness, both currencies and high-impact windows; it cannot verify
provider accuracy or detect unscheduled announcements. `require_calendar=false`
is an explicit user policy choice, never a fallback for a failed import.

## Configurable broker profiles

`config.example.json` starts with adapter `ctrader`, name `TopFX`, environment
`demo`. Change the adapter to `manual` or `oanda` for their draft formats.
The name is descriptive; it does not discover a brokerage account. All adapters
are draft exporters, and the executable ledger remains local paper only.

Set account currency, actual fees expressed in that currency, allowed pairs,
volume/margin constraints, UTC session and broker contract details explicitly.
Never use the stock `trader/config.json` or stock `orders.json` for FX.

For cTrader, merge account-specific metadata into `broker.symbols`:

```json
{
  "adapter": "ctrader", "name": "TopFX", "environment": "demo",
  "account_id": null,
  "symbols": {
    "EUR/USD": {
      "verified": false,
      "symbol_id": null,
      "digits": 5, "pip_position": 4,
      "lot_size_units": 100000, "min_units": 1000,
      "max_units": 1000000, "step_units": 1000,
      "leverage": 30, "min_stop_pips": 1
    }
  }
}
```

The null IDs and unverified metadata deliberately prevent cTrader export. Obtain
IDs and contract limits from the connected cTrader account or its Open API
symbol/account responses, check them against TopFX, then configure them privately.
The engine does not open a cTrader connection or request OAuth credentials.
Symbol IDs vary between brokers and server environments. Tiered margin, limited
risk accounts, trading modes and order-specific restrictions require additional
broker checks before a draft can be submitted by an external integration.

cTrader's protocol represents volume, minVolume, maxVolume, stepVolume and lotSize
in **hundredths of a base unit**. Divide imported volume metadata by 100 before
storing the `_units` configuration values. Export multiplies base units by 100;
10,000 EUR units become volume=1,000,000, not 0.1 or 10,000.
Relative stop and target are **1/100000 price units**, independently of pip size.
A 20-pip EUR/USD stop is 200 protocol units; a 20-pip USD/JPY stop is 20000.
For a market order the engine emits relative protection, not unsupported absolute
stopLoss/takeProfit fields. The draft is shaped as ProtoOANewOrderReq and is not
a REST request. Send only through an authenticated, approved cTrader integration.

Official references checked for these units:

- [cTrader Open API messages / ProtoOANewOrderReq](https://help.ctrader.com/open-api/messages/#protooaneworderreq)
- [cTrader model messages / ProtoOASymbol and ProtoOATradeData](https://help.ctrader.com/open-api/model-messages/)
- [Official protocol definitions](https://github.com/spotware/openapi-proto-messages)

OANDA export uses signed base units and separate stopLossOnFill/takeProfitOnFill
prices; it does not send the cTrader message or an Alpaca stock payload. Account
provisioning, authentication, hedging/netting restrictions and live trading are
outside these draft adapters.
