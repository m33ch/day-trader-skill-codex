#!/usr/bin/env python3
"""Broker-independent spot-FX planning and local paper ledger. Python 3.8+.

Prices are quote currency per base unit. Units are BASE currency, lots depend
on instrument metadata, all money limits/fees are ACCOUNT currency. No function
in this module submits an order or holds brokerage credentials.
"""
import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys
import urllib.parse
import urllib.request

UTC = dt.timezone.utc
CURRENCIES = set('USD EUR GBP JPY CHF CAD AUD NZD SEK NOK DKK SGD HKD ZAR MXN PLN CNH TRY'.split())
ROOT = 'trader/forex'
CURRENT_TIME = object()


class ForexError(ValueError):
    pass


def number(value, name='number', positive=False, nonnegative=False):
    if isinstance(value, bool):
        raise ForexError('%s must be numeric' % name)
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ForexError('%s must be numeric' % name)
    if not n.is_finite() or (positive and n <= 0) or (nonnegative and n < 0):
        raise ForexError('invalid %s' % name)
    return n


def integer(value, name='integer', positive=True):
    n = number(value, name, positive=positive, nonnegative=not positive)
    if n != n.to_integral_value():
        raise ForexError('%s must be an integer' % name)
    return int(n)


def pair(value):
    s = str(value).upper().strip()
    if s.endswith('=X'):
        s = s[:-2]
    s = s.replace('/', '').replace('_', '')
    if not re.fullmatch('[A-Z]{6}', s) or s[:3] not in CURRENCIES or s[3:] not in CURRENCIES or s[:3] == s[3:]:
        raise ForexError('expected a fiat currency pair such as EUR/USD or USD/JPY')
    return s[:3] + '/' + s[3:]


def stamp(value=CURRENT_TIME):
    if value is CURRENT_TIME:
        return dt.datetime.now(UTC)
    if isinstance(value, dt.datetime):
        result = value
    else:
        try:
            result = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            raise ForexError('timestamps must be ISO 8601 with an explicit UTC offset')
    if result.tzinfo is None or result.utcoffset() is None:
        raise ForexError('timestamps must include Z or a UTC offset')
    return result.astimezone(UTC)


def iso(value):
    return stamp(value).isoformat().replace('+00:00', 'Z')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path, default=None):
    try:
        with open(path, encoding='utf-8') as fh:
            value = json.load(fh, parse_constant=lambda x: (_ for _ in ()).throw(ForexError('non-finite JSON number')))
            if not isinstance(value, dict):
                raise ForexError('document must be a JSON object')
            return value
    except FileNotFoundError:
        if default is not None:
            return default
        raise ForexError('missing file: %s' % path)
    except (ValueError, OSError) as exc:
        raise ForexError('cannot read %s: %s' % (path, exc))


def write(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = p.with_name(p.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(str(temporary), str(p))


def clock_minutes(text):
    if not re.fullmatch(r'\d{2}:\d{2}', str(text)):
        raise ForexError('session times must be HH:MM in UTC')
    h, m = map(int, text.split(':'))
    if h > 23 or m > 59:
        raise ForexError('invalid session time')
    return h * 60 + m


def ny_time(now):
    """US DST rules since 2007, without requiring platform timezone files."""
    now = stamp(now)
    if now.year < 2007:
        raise ForexError('FX calendar supports dates from 2007')
    march = 1 + (6 - dt.date(now.year, 3, 1).weekday()) % 7 + 7
    november = 1 + (6 - dt.date(now.year, 11, 1).weekday()) % 7
    start = dt.datetime(now.year, 3, march, 7, tzinfo=UTC)
    end = dt.datetime(now.year, 11, november, 6, tzinfo=UTC)
    return now.astimezone(dt.timezone(dt.timedelta(hours=-4 if start <= now < end else -5)))


def market_clock(now, buffer_minutes=15):
    now = stamp(now)
    ny = ny_time(now)
    minute = ny.hour * 60 + ny.minute + ny.second / 60.0
    opened = ny.weekday() < 4 or (ny.weekday() == 4 and minute < 1020) or (ny.weekday() == 6 and minute >= 1020)
    rollover = abs(minute - 1020) < buffer_minutes
    return {'at': iso(now), 'new_york': ny.isoformat(), 'open': opened,
            'rollover_blackout': opened and rollover,
            'note': 'Indicative FX week: Sunday 17:00 to Friday 17:00 New York. Broker holidays and maintenance may differ.'}


def config(path):
    cfg = read(path)
    if not isinstance(cfg, dict) or cfg.get('market') != 'forex':
        raise ForexError('use a dedicated forex config, not the stock configuration')
    if cfg.get('account_currency') not in CURRENCIES:
        raise ForexError('unsupported account_currency')
    for key in ('equity', 'max_loss_per_trade', 'max_daily_risk', 'max_leverage', 'max_spread_pips',
                'max_quote_age_seconds', 'max_calendar_age_hours', 'min_reward_risk'):
        number(cfg.get(key), key, positive=True)
    for key in ('slippage_pips', 'round_trip_commission_per_lot', 'swap_cost_per_lot_per_night',
                'rollover_block_minutes', 'news_blackout_minutes'):
        number(cfg.get(key), key, nonnegative=True)
    for key in ('max_trades_per_day', 'opening_range_minutes', 'max_holding_nights'):
        integer(cfg.get(key), key)
    if not 0 < number(cfg.get('max_margin_pct'), 'max_margin_pct') <= 100:
        raise ForexError('max_margin_pct must be in (0, 100]')
    for key in ('paper', 'allow_short', 'allow_overnight', 'require_calendar'):
        if not isinstance(cfg.get(key), bool):
            raise ForexError('%s must be true or false' % key)
    start, end = clock_minutes(cfg.get('session_start_utc')), clock_minutes(cfg.get('flat_by_utc'))
    if end <= start:
        raise ForexError('flat_by_utc must follow session_start_utc on the same UTC day')
    allowed = cfg.get('allowed_pairs')
    if not isinstance(allowed, list) or not allowed:
        raise ForexError('allowed_pairs must be a nonempty list')
    cfg['allowed_pairs'] = [pair(p) for p in allowed]
    broker = cfg.get('broker', {})
    if not isinstance(broker, dict) or not isinstance(broker.get('symbols'), dict):
        raise ForexError('broker and broker.symbols must be objects')
    if not isinstance(cfg.get('instrument_defaults'), dict):
        raise ForexError('instrument_defaults must be an object')
    for name, metadata in broker['symbols'].items():
        if pair(name) != name or not isinstance(metadata, dict):
            raise ForexError('broker symbol keys must be normalized pairs with object metadata')
    if broker.get('adapter') not in ('ctrader', 'oanda', 'manual') or broker.get('environment') not in ('demo', 'live'):
        raise ForexError('broker adapter must be ctrader, oanda or manual; environment demo or live')
    return cfg


def instrument(cfg, symbol):
    symbol = pair(symbol)
    spec = dict(cfg.get('instrument_defaults', {}))
    spec.update(cfg.get('broker', {}).get('symbols', {}).get(symbol, {}))
    spec.setdefault('pip_position', 2 if symbol.endswith('/JPY') else 4)
    spec.setdefault('digits', spec['pip_position'] + 1)
    for key in ('lot_size_units', 'min_units', 'max_units', 'step_units', 'leverage'):
        spec[key] = integer(spec.get(key), key)
    pip_position = integer(spec['pip_position'], 'pip_position')
    digits = integer(spec['digits'], 'digits')
    if digits < pip_position or digits > 8 or pip_position > 6:
        raise ForexError('invalid pip position / price precision')
    if spec['min_units'] > spec['max_units'] or spec['min_units'] % spec['step_units']:
        raise ForexError('inconsistent volume limits')
    number(spec.get('min_stop_pips'), 'min_stop_pips', nonnegative=True)
    spec['pip'] = Decimal(10) ** -pip_position
    spec['tick'] = Decimal(10) ** -digits
    return spec


def quote_book(path, now, cfg):
    document = read(path)
    if document.get('market') != 'forex' or not isinstance(document.get('quotes'), dict):
        raise ForexError('quotes need market=forex and a quotes mapping')
    result = {}
    for name, value in document['quotes'].items():
        if not isinstance(value, dict):
            raise ForexError('each quote must be an object')
        name = pair(name)
        if name in result:
            raise ForexError('duplicate normalized pair in quotes')
        bid, ask = number(value.get('bid'), 'bid', positive=True), number(value.get('ask'), 'ask', positive=True)
        if ask < bid:
            raise ForexError('ask must be at least bid')
        age = (stamp(now) - stamp(value.get('at', ''))).total_seconds()
        if age < 0 or age > float(cfg['max_quote_age_seconds']):
            raise ForexError('stale or future quote for %s' % name)
        if not str(value.get('source', '')).strip():
            raise ForexError('each quote needs its source')
        result[name] = {'bid': bid, 'ask': ask, 'at': value['at'], 'source': value['source']}
    return result


def conversion(source, target, quotes, liability=True):
    """Conservative conversion: cost of buying source using account currency.

    Proceeds use bid (inverse ask), costs use ask (inverse bid). Search at most
    three edges; every quote was checked for timestamp and bid/ask validity.
    """
    if source == target:
        return Decimal(1)
    edges = {}
    for symbol, q in sorted(quotes.items()):
        base, quoted = symbol.split('/')
        edges.setdefault(base, []).append((quoted, q['ask'] if liability else q['bid']))
        edges.setdefault(quoted, []).append((base, 1 / (q['bid'] if liability else q['ask'])))
    queue = [(source, Decimal(1), [source])]
    for current, rate, visited in queue:
        if len(visited) > 3:
            continue
        for following, value in edges.get(current, []):
            if following in visited:
                continue
            if following == target:
                return rate * value
            queue.append((following, rate * value, visited + [following]))
    raise ForexError('missing fresh conversion quotes from %s to %s' % (source, target))


def session_bounds(now, cfg):
    now = stamp(now)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight + dt.timedelta(minutes=clock_minutes(cfg['session_start_utc']))
    end = midnight + dt.timedelta(minutes=clock_minutes(cfg['flat_by_utc']))
    # With overnight disabled, require an exit before the next NY rollover.
    if not cfg['allow_overnight']:
        ny = ny_time(now)
        rollover = ny.replace(hour=17, minute=0, second=0, microsecond=0).astimezone(UTC)
        if rollover > now:
            end = min(end, rollover - dt.timedelta(minutes=cfg['rollover_block_minutes']))
    return start, end


def calendar_events(path, now, cfg, symbol):
    document = read(path)
    if document.get('verified') is not True or not document.get('source'):
        raise ForexError('macro calendar must identify a source and be verified; an empty example is not clearance')
    age = (stamp(now) - stamp(document.get('as_of', ''))).total_seconds()
    if age < 0 or age > float(cfg['max_calendar_age_hours']) * 3600:
        raise ForexError('macro calendar is stale or future-dated')
    if not isinstance(document.get('events'), list):
        raise ForexError('calendar events must be a list')
    relevant = []
    for event in document['events']:
        if not isinstance(event, dict):
            raise ForexError('each calendar event must be an object')
        if event.get('currency') not in CURRENCIES or event.get('impact') not in ('low', 'medium', 'high') or not event.get('title'):
            raise ForexError('invalid macro calendar event')
        when = stamp(event.get('at', ''))
        if event['currency'] in pair(symbol).split('/'):
            row = dict(event)
            row['minutes_from_now'] = (when - stamp(now)).total_seconds() / 60
            relevant.append(row)
    return sorted(relevant, key=lambda e: e['at'])


def validate_bars(document, symbol, now):
    symbol = pair(symbol)
    # Support normalized broker exports and Yahoo chart JSON for research.
    if 'chart' in document:
        try:
            raw = document['chart']['result'][0]
            metadata = raw['meta']
            if pair(metadata['symbol']) != symbol or metadata.get('instrumentType') != 'CURRENCY':
                raise ForexError('Yahoo response is not the requested FX pair')
            interval = {'1m': 60, '5m': 300, '15m': 900, '1h': 3600, '1d': 86400}[metadata['dataGranularity']]
            values = raw['indicators']['quote'][0]
            rows = []
            for i, value in enumerate(raw['timestamp']):
                if any(values[k][i] is None for k in ('open', 'high', 'low', 'close')):
                    continue
                rows.append(dict({'time': iso(dt.datetime.fromtimestamp(value, UTC))},
                                 **{k: values[k][i] for k in ('open', 'high', 'low', 'close')}))
        except (KeyError, IndexError, TypeError):
            raise ForexError('incomplete Yahoo FX chart')
        source = 'Yahoo Finance unofficial indicative FX bars; not executable broker quotes'
    else:
        if document.get('market') != 'forex' or pair(document.get('pair')) != symbol:
            raise ForexError('bar file market/pair does not match request')
        interval = integer(document.get('interval_seconds'), 'interval_seconds')
        rows, source = document.get('bars'), document.get('source')
    if interval not in (60, 300, 900, 3600, 86400) or not isinstance(rows, list) or not source:
        raise ForexError('bars need source, supported interval_seconds and a bars list')
    bars, previous = [], None
    for row in rows:
        if not isinstance(row, dict):
            raise ForexError('each bar must be an object')
        at = stamp(row.get('time', ''))
        if previous is not None and at <= previous:
            raise ForexError('bar timestamps must increase without duplicates')
        previous = at
        candle = {k: number(row.get(k), k, positive=True) for k in ('open', 'high', 'low', 'close')}
        if candle['low'] > min(candle['open'], candle['close']) or candle['high'] < max(candle['open'], candle['close']) or candle['low'] > candle['high']:
            raise ForexError('invalid OHLC bar')
        if at + dt.timedelta(seconds=interval) <= stamp(now):
            candle['time'] = at
            bars.append(candle)
    if not bars:
        raise ForexError('no completed bars available before the requested time')
    return bars, interval, source


def load_bars(symbol, offline, now, interval='5m'):
    symbol = pair(symbol)
    if offline:
        path = Path(offline)
        if path.is_dir():
            path = path / (symbol.replace('/', '') + '.json')
        document = read(path)
    else:
        encoded = urllib.parse.quote(symbol.replace('/', '') + '=X', safe='')
        url = 'https://query2.finance.yahoo.com/v8/finance/chart/%s?range=60d&interval=%s' % (encoded, interval)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Codex forex research)'})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                document = json.load(response)
        except (OSError, ValueError) as exc:
            raise ForexError('FX bars unavailable; use --offline with a broker export: %s' % exc)
    return validate_bars(document, symbol, now)


def signal(symbol, bars, interval, now, cfg, side='buy', level=None):
    start, end = session_bounds(now, cfg)
    range_end = start + dt.timedelta(minutes=cfg['opening_range_minutes'])
    status = 'waiting'
    trigger = None
    opening = [b for b in bars if start <= b['time'] and b['time'] + dt.timedelta(seconds=interval) <= range_end]
    boundary = None
    if level is not None:
        boundary = number(level, 'level', positive=True)
        begin = start
    else:
        expected = cfg['opening_range_minutes'] * 60 // interval
        if expected < 1 or expected * interval != cfg['opening_range_minutes'] * 60:
            raise ForexError('opening range must be an exact multiple of the bar interval')
        times = [start + dt.timedelta(seconds=i * interval) for i in range(expected)]
        if [b['time'] for b in opening] != times:
            return {'market': 'forex', 'pair': pair(symbol), 'status': 'incomplete_range', 'side': side}
        boundary = (max(b['high'] for b in opening) if side == 'buy' else min(b['low'] for b in opening))
        begin = range_end
    if stamp(now) >= end:
        status = 'expired'
    elif stamp(now) < begin:
        status = 'not_open'
    else:
        for candle in bars:
            close_at = candle['time'] + dt.timedelta(seconds=interval)
            crossed = candle['close'] > boundary if side == 'buy' else candle['close'] < boundary
            if candle['time'] >= begin and close_at < end and crossed:
                trigger = candle
                status = 'triggered'
                break
    latest = bars[-1]['time'] + dt.timedelta(seconds=interval)
    return {'market': 'forex', 'pair': pair(symbol), 'side': side, 'status': status,
            'level': float(boundary), 'signal_price': float(trigger['close']) if trigger else None,
            'trigger_at': iso(trigger['time'] + dt.timedelta(seconds=interval)) if trigger else None,
            'as_of': iso(now), 'latest_bar_close': iso(latest), 'interval_seconds': interval,
            'stale_signal': bool(trigger and (stamp(now) - trigger['time']).total_seconds() > 2 * interval),
            'range_high': float(max(b['high'] for b in opening)) if opening else None,
            'range_low': float(min(b['low'] for b in opening)) if opening else None}


def make_plan(symbol, side, stop_pips, reward_risk, quotes, now, cfg):
    symbol = pair(symbol)
    if symbol not in quotes:
        raise ForexError('missing executable bid/ask quote for %s' % symbol)
    spec, q = instrument(cfg, symbol), quotes[symbol]
    stop_pips = number(stop_pips, 'stop_pips', positive=True)
    reward_risk = number(reward_risk, 'reward_risk', positive=True)
    if stop_pips < number(spec['min_stop_pips']):
        raise ForexError('stop is inside the configured minimum distance')
    entry = q['ask'] if side == 'buy' else q['bid']
    direction = 1 if side == 'buy' else -1
    stop = (entry - direction * stop_pips * spec['pip']).quantize(spec['tick'], rounding=ROUND_HALF_UP)
    target = (entry + direction * stop_pips * spec['pip'] * reward_risk).quantize(spec['tick'], rounding=ROUND_HALF_UP)
    if stop <= 0 or target <= 0:
        raise ForexError('stop and target must remain positive')
    start, end = session_bounds(now, cfg)
    if not start <= stamp(now) < end:
        raise ForexError('outside the configured UTC entry session')
    return {'market': 'forex', 'pair': symbol, 'kind': 'entry', 'side': side, 'entry': float(entry),
            'stop': float(stop), 'target': float(target), 'created_at': iso(now), 'expires_at': iso(end),
            'quote_source': q['source'], 'quote_at': q['at']}


def account_state(path, cfg):
    state = read(path, {'market': 'forex', 'account_currency': cfg['account_currency'],
                        'balance': cfg['equity'], 'positions': [], 'trades': [], 'approvals': [], 'rung': []})
    if state.get('market') != 'forex' or state.get('account_currency') != cfg['account_currency']:
        raise ForexError('account state must match the forex market and account currency')
    number(state.get('balance'), 'balance', positive=True)
    for name in ('positions', 'trades', 'approvals', 'rung'):
        if not isinstance(state.get(name), list):
            raise ForexError('invalid account ledger')
    return state


def exposure(state, quotes, cfg):
    """Mark local paper positions to executable bid/ask; fixed-leverage estimate."""
    equity = number(state['balance'])
    margin = Decimal(0)
    account = cfg['account_currency']
    for position in state['positions']:
        symbol = pair(position['pair'])
        if symbol not in quotes:
            raise ForexError('fresh quotes required for open position %s' % symbol)
        q, units = quotes[symbol], number(position['units'], positive=True)
        spec = instrument(cfg, symbol)
        price = q['bid'] if position['side'] == 'buy' else q['ask']
        pnl = units * (price - number(position['entry'])) * (1 if position['side'] == 'buy' else -1)
        equity += pnl * conversion(symbol[4:], account, quotes, liability=pnl < 0)
        leverage = min(number(cfg['max_leverage']), number(spec['leverage']))
        margin += units * conversion(symbol[:3], account, quotes) / leverage
    if equity <= 0:
        raise ForexError('nonpositive marked equity')
    return equity, margin


def order_metrics(order, quotes, cfg):
    symbol = pair(order.get('pair'))
    if order.get('market') != 'forex' or order.get('side') not in ('buy', 'sell'):
        raise ForexError('expected a forex buy or sell order')
    if symbol not in quotes:
        raise ForexError('missing pair quote')
    spec, q = instrument(cfg, symbol), quotes[symbol]
    units = integer(order.get('units'), 'units')
    if units < spec['min_units'] or units > spec['max_units'] or units % spec['step_units']:
        raise ForexError('units violate broker minimum, maximum or step')
    entry = number(order.get('entry'), 'entry', positive=True)
    stop, target = number(order.get('stop'), 'stop', positive=True), number(order.get('target'), 'target', positive=True)
    for price in (entry, stop, target):
        if price % spec['tick']:
            raise ForexError('price exceeds broker precision')
    direction = 1 if order['side'] == 'buy' else -1
    quoted_entry = q['ask'] if direction == 1 else q['bid']
    exit_now = q['bid'] if direction == 1 else q['ask']
    adverse_entry = quoted_entry + direction * number(cfg['slippage_pips']) * spec['pip']
    if (exit_now - stop) * direction <= 0 or (target - adverse_entry) * direction <= 0:
        raise ForexError('current executable quote has already crossed the stop or target')
    effective = max(entry, q['ask']) if direction == 1 else min(entry, q['bid'])
    distance, reward = (effective - stop) * direction, (target - effective) * direction
    if distance <= 0 or reward <= 0 or distance / spec['pip'] < number(spec['min_stop_pips']):
        raise ForexError('invalid or already passed stop/target')
    current_rate = conversion(symbol[4:], cfg['account_currency'], quotes)
    slippage = number(cfg['slippage_pips']) * spec['pip']
    # Revalue the traded pair at the assumed stop fill as well. For USD/JPY
    # in a USD account, converting JPY losses at the current price understates
    # losses when a long's stop fills at a lower USD/JPY rate.
    spread = q['ask'] - q['bid']
    scenario = dict(quotes)
    stop_exit = stop - direction * slippage
    stop_bid = stop_exit if direction == 1 else stop_exit - spread
    if stop_bid <= 0:
        raise ForexError('stop/slippage scenario has a nonpositive bid')
    scenario[symbol] = dict(q, bid=stop_bid, ask=stop_bid + spread)
    loss_rate = max(current_rate, conversion(symbol[4:], cfg['account_currency'], scenario))
    commission_per_unit = number(cfg['round_trip_commission_per_lot']) / number(spec['lot_size_units'])
    swaps_per_unit = (number(cfg['swap_cost_per_lot_per_night']) * number(cfg['max_holding_nights']) /
                      number(spec['lot_size_units'])) if cfg['allow_overnight'] else Decimal(0)
    cost = commission_per_unit + swaps_per_unit
    # slippage_pips is a per-side reserve: adverse entry AND adverse exit.
    risk_per_unit = (distance + 2 * slippage) * loss_rate + cost
    target_exit = target - direction * slippage
    target_bid = target_exit if direction == 1 else target_exit - spread
    if target_bid <= 0:
        raise ForexError('target/slippage scenario has a nonpositive bid')
    scenario[symbol] = dict(q, bid=target_bid, ask=target_bid + spread)
    reward_rate = min(conversion(symbol[4:], cfg['account_currency'], quotes, liability=False),
                      conversion(symbol[4:], cfg['account_currency'], scenario, liability=False))
    reward_per_unit = (reward - 2 * slippage) * reward_rate - cost
    leverage = min(number(cfg['max_leverage']), number(spec['leverage']))
    margin_per_unit = conversion(symbol[:3], cfg['account_currency'], quotes) / leverage
    return {'units': units, 'lots': Decimal(units) / number(spec['lot_size_units']),
            'pip_value_account': Decimal(units) * spec['pip'] * current_rate,
            'loss_conversion_rate': loss_rate,
            'risk_per_unit': risk_per_unit, 'risk_account': units * risk_per_unit,
            'initial_equity_cost_per_unit': (spread + slippage) * current_rate + commission_per_unit / 2,
            'reward_risk': reward_per_unit / risk_per_unit, 'margin_account': units * margin_per_unit,
            'margin_per_unit': margin_per_unit, 'effective_entry': effective,
            'spread_pips': (q['ask'] - q['bid']) / spec['pip']}


def size_order(plan, quotes, cfg, state):
    candidate = dict(plan)
    spec = instrument(cfg, plan.get('pair'))
    candidate['units'] = spec['min_units']
    metrics = order_metrics(candidate, quotes, cfg)
    equity, used_margin = exposure(state, quotes, cfg)
    ratio = number(cfg['max_margin_pct']) / 100
    by_risk = number(cfg['max_loss_per_trade']) / metrics['risk_per_unit']
    immediate_cost = metrics['initial_equity_cost_per_unit']
    by_cap = max(Decimal(0), equity * ratio - used_margin) / (metrics['margin_per_unit'] + ratio * immediate_cost)
    by_free = max(Decimal(0), equity - used_margin) / (metrics['margin_per_unit'] + immediate_cost)
    by_margin = min(by_cap, by_free)
    limits = {'risk': by_risk, 'margin': by_margin, 'broker_max': Decimal(spec['max_units'])}
    binding = min(limits, key=limits.get)
    units = int((limits[binding] / spec['step_units']).to_integral_value(rounding=ROUND_FLOOR)) * spec['step_units']
    if units < spec['min_units']:
        raise ForexError('budget or free margin cannot fund the minimum broker volume')
    candidate['units'] = units
    candidate['sizing'] = json_values(order_metrics(candidate, quotes, cfg))
    candidate['sizing']['binding_limit'] = binding
    candidate['account_currency'] = cfg['account_currency']
    return candidate


def json_values(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: json_values(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_values(v) for v in value]
    return value


def context(order, quotes_document, cfg, state):
    return digest({'order': order, 'quotes': quotes_document, 'config': cfg,
                   'balance': state['balance'], 'positions': state['positions'], 'trades': state['trades']})


def risk_check(order, quotes, quotes_document, cfg, state, now, calendar_path):
    reasons = []
    now = stamp(now)
    if not cfg['paper'] or cfg['broker']['environment'] != 'demo':
        reasons.append('this ledger supports local demo only; live account risk requires an authenticated broker integration')
    symbol = pair(order.get('pair'))
    if order.get('market') != 'forex' or order.get('account_currency') != cfg['account_currency']:
        reasons.append('order market/account currency mismatch')
    if order.get('kind') == 'exit':
        position = next((p for p in state['positions'] if p['id'] == order.get('position_id')), None)
        if not position or position['pair'] != symbol or order.get('side') == position['side'] or integer(order.get('units')) != position['units']:
            reasons.append('exit must close exactly the identified local position')
        if symbol not in quotes:
            reasons.append('fresh bid/ask required for the position being closed')
        metrics = {}
    elif order.get('kind') == 'entry':
        metrics = order_metrics(order, quotes, cfg)
        if symbol not in cfg['allowed_pairs']:
            reasons.append('pair is not in allowed_pairs')
        if order['side'] == 'sell' and not cfg['allow_short']:
            reasons.append('short entries are disabled')
        start, end = session_bounds(now, cfg)
        created, expires = stamp(order.get('created_at', '')), stamp(order.get('expires_at', ''))
        if not start <= now < end or not start <= created <= now < expires or expires > end:
            reasons.append('plan is expired, future-dated or outside the configured session')
        if metrics['spread_pips'] > number(cfg['max_spread_pips']):
            reasons.append('spread exceeds max_spread_pips')
        if metrics['risk_account'] > number(cfg['max_loss_per_trade']):
            reasons.append('loss at stop plus slippage/fees exceeds max_loss_per_trade')
        if metrics['reward_risk'] < number(cfg['min_reward_risk']):
            reasons.append('net reward:risk below minimum')
        todays = [t for t in state['trades'] if t['kind'] == 'entry' and stamp(t['at']).date() == now.date()]
        if len(todays) >= cfg['max_trades_per_day']:
            reasons.append('daily entry count exceeded')
        if sum((number(t['reserved_risk']) for t in todays), Decimal(0)) + metrics['risk_account'] > number(cfg['max_daily_risk']):
            reasons.append('daily reserved risk exceeded (includes winning/closed entries)')
        if any(p['pair'] == symbol for p in state['positions']):
            reasons.append('an open position already exists for this pair')
        if any(t.get('order_fingerprint') == digest(order) for t in state['trades']):
            reasons.append('duplicate order already filled')
        equity, used_margin = exposure(state, quotes, cfg)
        projected_equity = equity - metrics['units'] * metrics['initial_equity_cost_per_unit']
        if used_margin + metrics['margin_account'] > min(projected_equity, projected_equity * number(cfg['max_margin_pct']) / 100):
            reasons.append('estimated total margin exceeds configured cap')
        clock = market_clock(now, cfg['rollover_block_minutes'])
        if clock['rollover_blackout']:
            reasons.append('NY rollover blackout')
        if cfg['require_calendar']:
            events = calendar_events(calendar_path, now, cfg, symbol)
            if any(e['impact'] == 'high' and abs(e['minutes_from_now']) <= cfg['news_blackout_minutes'] for e in events):
                reasons.append('high-impact macro news blackout')
    else:
        raise ForexError('order kind must be entry or exit')
    if order.get('side') not in ('buy', 'sell'):
        reasons.append('invalid order side')
    if not market_clock(now)['open']:
        reasons.append('FX week is closed')
    return {'market': 'forex', 'pass': not reasons, 'reasons': reasons, 'at': iso(now),
            'order_fingerprint': digest(order), 'context': context(order, quotes_document, cfg, state),
            'metrics': json_values(metrics), 'account_currency': cfg['account_currency'],
            'ledger_source': 'local_paper', 'broker_connected': False,
            'calendar_checked': order.get('kind') == 'entry' and cfg['require_calendar'],
            'margin_note': 'Fixed-leverage estimate; broker tiers, stop-out rules and special holidays require broker verification.'}


def require_approval(order, quotes_document, cfg, state, verdict, now):
    age = (stamp(now) - stamp(verdict.get('at', ''))).total_seconds()
    if not verdict.get('pass') or age < 0 or age > cfg['max_quote_age_seconds'] or verdict.get('context') != context(order, quotes_document, cfg, state):
        raise ForexError('run a fresh risk check for this exact order, quotes, configuration and account state')
    return digest(order)


def broker_payload(order, cfg):
    """Prepare an integration draft only; never send or imply broker readiness."""
    broker, symbol = cfg['broker'], pair(order['pair'])
    if order.get('kind') != 'entry':
        raise ForexError('close positions in the broker UI using its real position ID; local paper IDs are not broker IDs')
    if broker['adapter'] == 'manual':
        return {'adapter': 'manual', 'status': 'DRAFT_ONLY', 'ticket': order}
    if broker['adapter'] == 'oanda':
        return {'adapter': 'oanda', 'status': 'DRAFT_ONLY', 'environment': broker['environment'],
                'order': {'instrument': symbol.replace('/', '_'),
                          'units': str(order['units'] * (1 if order['side'] == 'buy' else -1)),
                          'type': 'MARKET', 'timeInForce': 'FOK', 'positionFill': 'DEFAULT',
                          'stopLossOnFill': {'price': str(order['stop'])},
                          'takeProfitOnFill': {'price': str(order['target'])}}}
    spec = instrument(cfg, symbol)
    units = integer(order['units'], 'units')
    if units < spec['min_units'] or units > spec['max_units'] or units % spec['step_units']:
        raise ForexError('cTrader volume violates verified symbol limits')
    metadata = broker.get('symbols', {}).get(symbol, {})
    required = ('symbol_id', 'digits', 'pip_position', 'lot_size_units', 'min_units', 'max_units', 'step_units', 'leverage', 'min_stop_pips')
    if metadata.get('verified') is not True or any(k not in metadata for k in required):
        raise ForexError('cTrader requires verified symbol metadata from this broker/account, including symbol_id and volume limits')
    account_id = integer(broker.get('account_id'), 'cTrader account_id')
    symbol_id = integer(metadata['symbol_id'], 'cTrader symbol_id')
    direction = 1 if order['side'] == 'buy' else -1
    loss = (number(order['entry']) - number(order['stop'])) * direction * 100000
    gain = (number(order['target']) - number(order['entry'])) * direction * 100000
    if loss != loss.to_integral_value() or gain != gain.to_integral_value() or loss <= 0 or gain <= 0:
        raise ForexError('cTrader relative protection distances must be positive multiples of 1/100000 price unit')
    return {'adapter': 'ctrader', 'status': 'DRAFT_ONLY', 'environment': broker['environment'],
            'message': 'ProtoOANewOrderReq', 'payload': {
                'ctidTraderAccountId': account_id, 'symbolId': symbol_id, 'orderType': 'MARKET',
                'tradeSide': order['side'].upper(), 'volume': order['units'] * 100,
                'relativeStopLoss': int(loss), 'relativeTakeProfit': int(gain),
                'clientOrderId': digest(order)[:40]},
            'note': 'cTrader volume is hundredths of a base unit. Relative protection is 1/100000 price unit. '
                    'This draft is not a connected cTrader session or a submitted order. Verify broker restrictions and expected margin.'}


def paper_fill(order, quotes, cfg, state, now, verdict):
    if not cfg['paper'] or cfg['broker']['environment'] != 'demo':
        raise ForexError('local paper fills require paper=true and broker environment=demo')
    symbol = pair(order['pair'])
    q = quotes[symbol]
    spec = instrument(cfg, symbol)
    direction = 1 if order['side'] == 'buy' else -1
    price = (q['ask'] if direction == 1 else q['bid']) + direction * number(cfg['slippage_pips']) * spec['pip']
    units = integer(order['units'])
    fee = Decimal(units) / spec['lot_size_units'] * number(cfg['round_trip_commission_per_lot']) / 2
    record = {'at': iso(now), 'kind': order['kind'], 'pair': symbol, 'side': order['side'],
              'units': units, 'price': float(price), 'fee_account': float(fee),
              'order_fingerprint': digest(order), 'local_paper_only': True}
    if order['kind'] == 'entry':
        position = {'id': digest(order)[:24], 'pair': symbol, 'side': order['side'], 'units': units,
                    'entry': float(price), 'opened_at': iso(now), 'stop': order['stop'], 'target': order['target']}
        record['reserved_risk'] = verdict['metrics']['risk_account']
        state['positions'].append(position)
        state['balance'] = float(number(state['balance']) - fee)
    else:
        position = next(p for p in state['positions'] if p['id'] == order['position_id'])
        pnl = (price - number(position['entry'])) * units * (1 if position['side'] == 'buy' else -1)
        pnl_account = pnl * conversion(symbol[4:], cfg['account_currency'], quotes, liability=pnl < 0)
        nights = max(0, (ny_time(now).date() - ny_time(position['opened_at']).date()).days)
        # Explicit conservative reserve; not a real broker swap schedule.
        swap = Decimal(units) / spec['lot_size_units'] * number(cfg['swap_cost_per_lot_per_night']) * nights
        record.update({'pnl_account': float(pnl_account), 'swap_estimate_account': float(swap)})
        state['balance'] = float(number(state['balance']) + pnl_account - fee - swap)
        state['positions'] = [p for p in state['positions'] if p['id'] != position['id']]
    state['trades'].append(record)
    return record


def simulate(plan, bars, interval, cfg, paths=1000, horizon=72, seed=19):
    if paths < 1 or horizon < 1 or paths * horizon > 2000000:
        raise ForexError('simulation requires positive paths/bars, capped at 2,000,000 simulated bars')
    cutoff = stamp(plan['created_at'])
    remaining = int((stamp(plan['expires_at']) - cutoff).total_seconds() // interval)
    if remaining < 1:
        raise ForexError('no complete simulated bar fits before flat-by expiry')
    horizon = min(horizon, remaining)
    history = [b for b in bars if b['time'] + dt.timedelta(seconds=interval) <= cutoff]
    days = {b['time'].date() for b in history}
    samples = []
    for before, after in zip(history, history[1:]):
        if (after['time'] - before['time']).total_seconds() == interval:
            samples.append(tuple(float(after[k] / before['close']) for k in ('high', 'low', 'close')))
    if len(days) < 20 or len(samples) < 100:
        raise ForexError('simulation needs 20 historical UTC dates and 100 contiguous completed bar transitions before entry')
    rng, result = random.Random(seed), {'stop': 0, 'target': 0, 'neither': 0}
    for _ in range(paths):
        price = float(plan['entry'])
        outcome = 'neither'
        for _ in range(horizon):
            high, low, close = [price * x for x in rng.choice(samples)]
            if plan['side'] == 'buy':
                stopped, targeted = low <= plan['stop'], high >= plan['target']
            else:
                stopped, targeted = high >= plan['stop'], low <= plan['target']
            if stopped or targeted:
                outcome = 'stop' if stopped else 'target'
                break
            price = close
        result[outcome] += 1
    return {'market': 'forex', 'pair': plan['pair'], 'seed': seed, 'paths': paths, 'horizon_bars': horizon,
            'historical_days': len(days), 'history_cutoff': iso(cutoff),
            'frequencies': {k: v / paths for k, v in result.items()},
            'method': 'Historical OHLC bootstrap, stop first when both barriers touch. Price-path frequencies only; '
                      'not a return forecast or a historical strategy backtest. No future bars are used. '
                      'Spread dynamics, gaps, conversion changes and broker fills are not simulated; use budget/risk for cost reserves.'}


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command')
    for name in ('init', 'clock', 'quote', 'scan', 'entry', 'exit', 'simulate', 'size', 'risk', 'broker', 'alerts', 'news'):
        p = sub.add_parser(name)
        p.add_argument('--root', default=ROOT, help='isolated forex state directory')
        p.add_argument('--config', help='forex config; default ROOT/config.json')
        p.add_argument('--now', help='ISO time with UTC offset (for replay only)')
        p.add_argument('--quotes', help='bid/ask snapshot; default ROOT/quotes.json')
        p.add_argument('--calendar', help='verified macro calendar; default ROOT/calendar.json')
        p.add_argument('--json', action='store_true', help='output is always JSON')
        if name in ('scan', 'entry', 'simulate'):
            p.add_argument('--offline', help='normalized or Yahoo FX chart JSON; directory uses EURUSD.json etc.')
            p.add_argument('--interval', choices=('1m', '5m', '15m', '1h', '1d'), default='5m')
        if name == 'quote':
            p.add_argument('pair'); p.add_argument('--bid', required=True); p.add_argument('--ask', required=True)
            p.add_argument('--at', required=True, help='actual quote timestamp, not import time')
            p.add_argument('--source', required=True, help='e.g. TopFX cTrader demo')
        if name == 'scan':
            p.add_argument('mode', nargs='?', choices=('orb', 'daily', 'universe'), default='orb')
            p.add_argument('--pairs', help='comma-separated currency pairs; defaults to allowed_pairs')
        if name == 'entry':
            p.add_argument('mode', nargs='?', choices=('check',), default='check')
            p.add_argument('--pair', required=True); p.add_argument('--side', choices=('buy', 'sell'), default='buy')
            p.add_argument('--level'); p.add_argument('--write', action='store_true')
        if name == 'exit':
            p.add_argument('mode', nargs='?', choices=('plan', 'show'), default='plan')
            p.add_argument('--pair'); p.add_argument('--side', choices=('buy', 'sell'), default='buy')
            p.add_argument('--stop-pips', type=str); p.add_argument('--reward-risk', default='2')
            p.add_argument('--write', action='store_true')
        if name == 'simulate':
            p.add_argument('mode', nargs='?', choices=('run',), default='run')
            p.add_argument('--paths', type=int, default=1000); p.add_argument('--bars', type=int, default=72)
            p.add_argument('--seed', type=int, default=19); p.add_argument('--write', action='store_true')
        if name == 'size':
            p.add_argument('--write', action='store_true')
        if name == 'broker':
            p.add_argument('action', choices=('show', 'approve', 'export', 'paper-fill', 'flatten', 'log'))
            p.add_argument('--reply'); p.add_argument('--position-id')
        if name == 'alerts':
            p.add_argument('action', choices=('clock', 'add', 'check', 'list'), default='check', nargs='?')
            p.add_argument('--from-plan', action='store_true')
        if name == 'news':
            p.add_argument('--pair', required=True)
    return ap


def main(argv=None):
    ap = parser()
    a = ap.parse_args(argv)
    if not a.command:
        ap.print_help(); return 0
    try:
        root = Path(a.root)
        now = stamp() if a.now is None else stamp(a.now)
        cfg_path = a.config or str(root / 'config.json')
        if a.command == 'init':
            if Path(cfg_path).exists():
                raise ForexError('configuration already exists; inspect/edit it explicitly')
            write(cfg_path, read(Path(__file__).with_name('config.example.json')))
            print(json.dumps({'config': cfg_path, 'status': 'created', 'next': 'Review broker specs, account currency, fees and limits. Import current bid/ask quotes and a verified macro calendar.'}))
            return 0
        cfg = config(cfg_path)
        state_path = root / 'state.json'
        state = account_state(state_path, cfg)
        quote_path = a.quotes or str(root / 'quotes.json')
        cal_path = a.calendar or str(root / 'calendar.json')
        if a.command == 'clock' or (a.command == 'alerts' and a.action == 'clock'):
            output = market_clock(now, cfg['rollover_block_minutes'])
        elif a.command == 'quote':
            document = read(quote_path, {'market': 'forex', 'quotes': {}})
            if document.get('market') != 'forex' or not isinstance(document.get('quotes'), dict):
                raise ForexError('quote import requires a dedicated forex snapshot')
            symbol = pair(a.pair)
            document['quotes'][symbol] = {'bid': float(number(a.bid, 'bid', positive=True)),
                                          'ask': float(number(a.ask, 'ask', positive=True)), 'at': iso(stamp(a.at)), 'source': a.source}
            # Validate only this quote so an old unrelated snapshot can be refreshed separately.
            q = document['quotes'][symbol]
            if q['ask'] < q['bid'] or not a.source.strip() or not 0 <= (now - stamp(a.at)).total_seconds() <= cfg['max_quote_age_seconds']:
                raise ForexError('bid/ask must be valid, fresh, timestamped and sourced')
            write(quote_path, document); output = {'pair': symbol, 'quote': q}
        elif a.command in ('scan', 'entry'):
            if a.command == 'scan' and a.mode == 'universe':
                output = {'market': 'forex', 'pairs': cfg['allowed_pairs']}
            else:
                symbols = ([pair(s.strip()) for s in a.pairs.split(',')] if a.command == 'scan' and a.pairs else
                           cfg['allowed_pairs'] if a.command == 'scan' else [pair(a.pair)])
                results = []
                for symbol in symbols:
                    bars, interval, source = load_bars(symbol, a.offline, now, a.interval)
                    if a.command == 'scan' and a.mode == 'daily':
                        if interval != 86400 or len(bars) < 21:
                            raise ForexError('daily scan needs --interval 1d and 21 completed daily bars')
                        high, low = max(b['high'] for b in bars[-21:-1]), min(b['low'] for b in bars[-21:-1])
                        results.append({'pair': symbol, 'close': float(bars[-1]['close']),
                                        'above_20bar_high': bars[-1]['close'] > high, 'below_20bar_low': bars[-1]['close'] < low,
                                        'source': source, 'last_bar_at': iso(bars[-1]['time'])})
                    else:
                        sides = ['buy', 'sell'] if a.command == 'scan' and cfg['allow_short'] else [getattr(a, 'side', 'buy')]
                        for side in sides:
                            result = signal(symbol, bars, interval, now, cfg, side, getattr(a, 'level', None))
                            result['source'] = source; results.append(result)
                output = {'market': 'forex', 'results': results}
                if a.command == 'scan':
                    write(root / 'watchlist.json', output)
                elif a.write:
                    write(root / 'entry.json', results[0])
        elif a.command == 'news':
            output = {'pair': pair(a.pair), 'events': calendar_events(cal_path, now, cfg, a.pair),
                      'scope': 'Imported macro calendar only; no SEC filings or stock headlines. Verify unscheduled central-bank/geopolitical news separately.'}
        elif a.command == 'broker' and a.action == 'log':
            output = state
        elif a.command == 'broker' and a.action == 'flatten':
            position = next((p for p in state['positions'] if p['id'] == a.position_id), None)
            if not position:
                raise ForexError('position-id must identify an open local paper position')
            output = {'market': 'forex', 'kind': 'exit', 'position_id': position['id'], 'pair': position['pair'],
                      'side': 'sell' if position['side'] == 'buy' else 'buy', 'units': position['units'],
                      'account_currency': cfg['account_currency'], 'created_at': iso(now)}
            write(root / 'orders.json', output)
        elif a.command == 'simulate':
            plan = read(root / 'plan.json')
            bars, interval, source = load_bars(plan['pair'], a.offline, min(now, stamp(plan['created_at'])), a.interval)
            output = simulate(plan, bars, interval, cfg, a.paths, a.bars, a.seed)
            output['source'] = source
            if a.write: write(root / 'simulation.json', output)
        elif a.command == 'exit' and a.mode == 'show':
            output = read(root / 'plan.json')
        elif a.command == 'alerts' and a.action == 'add':
            if not a.from_plan: raise ForexError('forex alerts add requires --from-plan')
            output = read(root / 'plan.json'); write(root / 'alerts.json', output)
        elif a.command == 'alerts' and a.action == 'list':
            output = read(root / 'alerts.json')
        else:
            quotes_document = read(quote_path)
            quotes = quote_book(quote_path, now, cfg)
            if a.command == 'exit':
                if not a.pair or not a.stop_pips:
                    raise ForexError('forex exit plan requires --pair and --stop-pips')
                output = make_plan(a.pair, a.side, a.stop_pips, a.reward_risk, quotes, now, cfg)
                if a.write: write(root / 'plan.json', output)
            elif a.command == 'size':
                output = size_order(read(root / 'plan.json'), quotes, cfg, state)
                if a.write: write(root / 'orders.json', output)
            elif a.command == 'risk':
                order = read(root / 'orders.json')
                output = risk_check(order, quotes, quotes_document, cfg, state, now, cal_path)
                write(root / 'risk.json', output)
            elif a.command == 'broker':
                order = read(root / 'orders.json')
                verdict = read(root / 'risk.json')
                fp = require_approval(order, quotes_document, cfg, state, verdict, now)
                # Re-evaluate the macro window and all limits at every decision boundary.
                current = risk_check(order, quotes, quotes_document, cfg, state, now, cal_path)
                if not current['pass']: raise ForexError('risk BLOCK: ' + '; '.join(current['reasons']))
                if a.action == 'show':
                    output = {'ticket': order, 'risk': current, 'next': 'Explicit yes for this single ticket; export is draft only. Local paper fills never reach a broker.'}
                elif a.action == 'approve':
                    if (a.reply or '').strip().lower() != 'yes': raise ForexError('requires the user reply yes for exactly this ticket')
                    if any(r['fingerprint'] == fp for r in state['approvals']): raise ForexError('ticket already approved; do not duplicate approval')
                    state['approvals'].append({'fingerprint': fp, 'context': current['context'], 'at': iso(now), 'reply': a.reply})
                    write(state_path, state); output = {'status': 'approved', 'fingerprint': fp}
                else:
                    approval = next((r for r in state['approvals'] if r['fingerprint'] == fp and r['context'] == current['context']), None)
                    if not approval: raise ForexError('exact ticket needs the user approval after the current risk check')
                    if a.action == 'export':
                        output = broker_payload(order, cfg); write(root / 'broker-draft.json', output)
                    elif a.action == 'paper-fill':
                        output = paper_fill(order, quotes, cfg, state, now, current); write(state_path, state)
                    else: raise ForexError('unsupported forex broker action')
            elif a.command == 'alerts':
                plan = read(root / 'alerts.json'); q = quotes[pair(plan['pair'])]
                price = q['bid'] if plan['side'] == 'buy' else q['ask']
                conditions = {'stop': price <= number(plan['stop']) if plan['side'] == 'buy' else price >= number(plan['stop']),
                              'target': price >= number(plan['target']) if plan['side'] == 'buy' else price <= number(plan['target']),
                              'flat_by': now >= stamp(plan['expires_at'])}
                rang = []
                for name, hit in conditions.items():
                    key = digest(plan) + ':' + name
                    if hit and key not in state['rung']:
                        state['rung'].append(key); rang.append(name)
                write(state_path, state); output = {'pair': plan['pair'], 'rang': rang, 'executable_exit_price': float(price),
                                                    'note': 'One check only. Alerts never close a position.'}
            else:
                raise ForexError('unsupported forex command')
        print(json.dumps(json_values(output), indent=2, allow_nan=False))
        return 1 if a.command == 'risk' and not output['pass'] else 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError, InvalidOperation) as exc:
        print('forex: %s' % exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
