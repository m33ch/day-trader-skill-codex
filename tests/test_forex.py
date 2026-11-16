"""FX arithmetic, broker unit contracts and isolated offline workflow tests."""
import copy
import datetime as dt
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import ROOT, load

fx = load('trader-forex', 'forex')
NOW = fx.stamp('2026-11-16T10:00:00Z')


def settings():
    return fx.config(os.path.join(ROOT, 'trader-forex', 'config.example.json'))


def quotes(now=NOW):
    return {'market': 'forex', 'quotes': {
        'EUR/USD': {'bid': 1.1000, 'ask': 1.1002, 'at': fx.iso(now), 'source': 'synthetic test fixture'},
        'USD/JPY': {'bid': 150.00, 'ask': 150.02, 'at': fx.iso(now), 'source': 'synthetic test fixture'},
        'EUR/GBP': {'bid': 0.8300, 'ask': 0.8302, 'at': fx.iso(now), 'source': 'synthetic test fixture'},
        'GBP/USD': {'bid': 1.3249, 'ask': 1.3251, 'at': fx.iso(now), 'source': 'synthetic test fixture'}
    }}


def book(document=None):
    data = copy.deepcopy(document or quotes())['quotes']
    return {p: dict(q, bid=Decimal(str(q['bid'])), ask=Decimal(str(q['ask']))) for p, q in data.items()}


def candles(day='2026-11-16', symbol='EUR/USD'):
    start = fx.stamp(day + 'T08:00:00Z')
    rows = []
    for i in range(12):
        close = 1.1010 if i >= 3 else 1.1000
        rows.append({'time': fx.iso(start + dt.timedelta(minutes=5*i)),
                     'open': 1.1000, 'high': max(1.1005, close), 'low': 1.0995, 'close': close})
    return {'market': 'forex', 'pair': symbol, 'interval_seconds': 300,
            'source': 'synthetic test fixture; not market history', 'bars': rows}


class Numbers(unittest.TestCase):
    def test_pair_formats_and_non_fx_rejection(self):
        for value in ('EURUSD', 'eur/usd', 'EUR_USD', 'EURUSD=X'):
            self.assertEqual(fx.pair(value), 'EUR/USD')
        for value in ('AAPL', 'BTCUSD', 'XAUUSD', 'USDUSD', 'EURUSD;rm', None):
            with self.assertRaises(fx.ForexError): fx.pair(value)

    def test_nonfinite_and_boolean_numbers_rejected(self):
        for value in ('NaN', 'Infinity', float('inf'), True, None, -1, 0):
            with self.assertRaises(fx.ForexError): fx.number(value, positive=True)
        with self.assertRaises(fx.ForexError): fx.integer(1000.5)

    def test_explicit_timezones_required(self):
        with self.assertRaises(fx.ForexError): fx.stamp('2026-11-16T10:00:00')
        self.assertEqual(fx.stamp('2026-11-16T11:00:00+01:00'), NOW)

    def test_pips_jpy_and_broker_override(self):
        cfg = settings()
        self.assertEqual(fx.instrument(cfg, 'EUR/USD')['pip'], Decimal('.0001'))
        self.assertEqual(fx.instrument(cfg, 'USD/JPY')['pip'], Decimal('.01'))
        cfg['broker']['symbols']['EUR/USD'] = {'pip_position': 3, 'digits': 4}
        self.assertEqual(fx.instrument(cfg, 'EUR/USD')['pip'], Decimal('.001'))

    def test_liability_and_proceeds_conversion(self):
        q = book()
        self.assertEqual(fx.conversion('USD', 'USD', q), 1)
        self.assertEqual(fx.conversion('EUR', 'USD', q), Decimal('1.1002'))
        self.assertEqual(fx.conversion('EUR', 'USD', q, False), Decimal('1.1'))
        self.assertEqual(fx.conversion('USD', 'EUR', q), 1 / Decimal('1.1'))
        self.assertEqual(fx.conversion('JPY', 'USD', q), 1 / Decimal('150'))
        self.assertEqual(fx.conversion('JPY', 'EUR', q), 1 / Decimal('150') / Decimal('1.1'))
        with self.assertRaises(fx.ForexError): fx.conversion('CHF', 'USD', q)


class Sessions(unittest.TestCase):
    def test_weekly_boundaries_and_us_dst(self):
        cases = [('2026-01-11T21:59:59Z', False), ('2026-01-11T22:00:00Z', True),
                 ('2026-07-12T20:59:59Z', False), ('2026-07-12T21:00:00Z', True),
                 ('2026-07-17T20:59:59Z', True), ('2026-07-17T21:00:00Z', False),
                 ('2026-01-16T22:00:00Z', False), ('2026-11-14T12:00:00Z', False)]
        for at, opened in cases:
            self.assertEqual(fx.market_clock(at)['open'], opened, at)

    def test_us_holidays_are_not_nyse_closures(self):
        self.assertTrue(fx.market_clock('2026-11-26T15:00:00Z')['open'])

    def test_rollover_blackout(self):
        self.assertTrue(fx.market_clock('2026-07-13T21:00:00Z')['rollover_blackout'])
        self.assertTrue(fx.market_clock('2026-01-12T22:00:00Z')['rollover_blackout'])
        self.assertFalse(fx.market_clock('2026-01-12T21:00:00Z')['rollover_blackout'])

    def test_flat_by_uses_utc_and_protects_against_rollover(self):
        cfg = settings(); cfg['flat_by_utc'] = '23:00'
        start, end = fx.session_bounds('2026-07-13T10:00:00Z', cfg)
        self.assertEqual(fx.iso(end), '2026-07-13T20:45:00Z')
        cfg['allow_overnight'] = True
        self.assertEqual(fx.iso(fx.session_bounds('2026-07-13T10:00:00Z', cfg)[1]), '2026-07-13T23:00:00Z')


class Research(unittest.TestCase):
    def test_closed_bars_only_and_opening_range_trigger(self):
        data = candles()
        at = fx.stamp('2026-11-16T08:19:59Z')
        bars, interval, _ = fx.validate_bars(data, 'EUR/USD', at)
        self.assertEqual(len(bars), 3)
        self.assertEqual(fx.signal('EUR/USD', bars, interval, at, settings())['status'], 'waiting')
        at = fx.stamp('2026-11-16T08:20:00Z')
        bars, interval, _ = fx.validate_bars(data, 'EUR/USD', at)
        result = fx.signal('EUR/USD', bars, interval, at, settings())
        self.assertEqual(result['status'], 'triggered')
        self.assertEqual(result['trigger_at'], '2026-11-16T08:20:00Z')
        self.assertFalse(result['stale_signal'])

    def test_missing_range_bar_and_duplicate_bars(self):
        data = candles(); del data['bars'][1]
        bars, interval, _ = fx.validate_bars(data, 'EUR/USD', NOW)
        self.assertEqual(fx.signal('EUR/USD', bars, interval, NOW, settings())['status'], 'incomplete_range')
        data = candles(); data['bars'][1]['time'] = data['bars'][0]['time']
        with self.assertRaises(fx.ForexError): fx.validate_bars(data, 'EUR/USD', NOW)

    def test_bad_ohlc_wrong_pair_and_expired_session(self):
        data = candles(); data['bars'][0]['high'] = 1
        with self.assertRaises(fx.ForexError): fx.validate_bars(data, 'EUR/USD', NOW)
        with self.assertRaises(fx.ForexError): fx.validate_bars(candles(), 'USD/JPY', NOW)
        bars, interval, _ = fx.validate_bars(candles(), 'EUR/USD', NOW)
        self.assertEqual(fx.signal('EUR/USD', bars, interval, '2026-11-16T21:00:00Z', settings())['status'], 'expired')

    def test_short_signal_and_custom_level(self):
        data = candles()
        for row in data['bars'][3:]: row.update(close=1.0990, low=1.0985)
        bars, interval, _ = fx.validate_bars(data, 'EUR/USD', NOW)
        self.assertEqual(fx.signal('EUR/USD', bars, interval, NOW, settings(), 'sell')['status'], 'triggered')
        self.assertEqual(fx.signal('EUR/USD', bars, interval, NOW, settings(), 'sell', '1.0992')['status'], 'triggered')

    def test_yahoo_fx_metadata_is_verified(self):
        data = {'chart': {'result': [{'meta': {'symbol': 'EURUSD=X', 'instrumentType': 'CURRENCY', 'dataGranularity': '5m'},
                 'timestamp': [int(NOW.timestamp()) - 300], 'indicators': {'quote': [{'open': [1.1], 'high': [1.2], 'low': [1.0], 'close': [1.11]}]}}]}}
        self.assertEqual(len(fx.validate_bars(data, 'EUR/USD', NOW)[0]), 1)
        data['chart']['result'][0]['meta']['instrumentType'] = 'EQUITY'
        with self.assertRaises(fx.ForexError): fx.validate_bars(data, 'EUR/USD', NOW)

    def test_bootstrap_reproducible_and_no_future_leakage(self):
        cfg = settings()
        plan = fx.make_plan('EUR/USD', 'buy', 20, 2, book(), NOW, cfg)
        rows = []
        for day in range(25):
            document = candles((NOW.date() - dt.timedelta(days=day)).isoformat())
            rows.extend(document['bars'])
        rows.sort(key=lambda r: r['time'])
        doc = dict(candles(), bars=rows)
        bars, interval, _ = fx.validate_bars(doc, 'EUR/USD', NOW)
        first = fx.simulate(plan, bars, interval, cfg, paths=30, horizon=20, seed=7)
        future = {'time': NOW + dt.timedelta(days=1), 'open': Decimal(5), 'high': Decimal(10), 'low': Decimal('.1'), 'close': Decimal(9)}
        self.assertEqual(first, fx.simulate(plan, bars + [future], interval, cfg, paths=30, horizon=20, seed=7))
        self.assertAlmostEqual(sum(first['frequencies'].values()), 1)
        with self.assertRaises(fx.ForexError): fx.simulate(plan, bars[:30], interval, cfg)
        with self.assertRaises(fx.ForexError): fx.simulate(plan, bars, interval, cfg, paths=1000000)


class Workflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fx project ')
        self.root = Path(self.temp.name) / 'trader' / 'forex'
        self.root.mkdir(parents=True)
        self.cfg = settings()
        fx.write(self.root / 'config.json', self.cfg)
        fx.write(self.root / 'quotes.json', quotes())
        fx.write(self.root / 'calendar.json', {'verified': True, 'as_of': fx.iso(NOW),
                  'source': 'synthetic test calendar', 'events': []})
        self.state = fx.account_state(self.root / 'state.json', self.cfg)
        self.plan = fx.make_plan('EUR/USD', 'buy', 20, 2, book(), NOW, self.cfg)
        self.order = fx.size_order(self.plan, book(), self.cfg, self.state)
        fx.write(self.root / 'plan.json', self.plan)
        fx.write(self.root / 'orders.json', self.order)

    def tearDown(self): self.temp.cleanup()

    def run_cli(self, *args, now=NOW):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = fx.main(list(args) + ['--root', str(self.root), '--now', fx.iso(now)])
        return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def risk(self, order=None, cfg=None, state=None, now=NOW, document=None):
        document = document or quotes()
        return fx.risk_check(order or self.order, book(document), document, cfg or self.cfg,
                             state or self.state, now, self.root / 'calendar.json')

    def test_sizing_rounds_down_and_fits_limits(self):
        metrics = fx.order_metrics(self.order, book(), self.cfg)
        self.assertEqual(self.order['units'] % 1000, 0)
        self.assertLessEqual(metrics['risk_account'], 50)
        self.assertLessEqual(metrics['margin_account'], 1500)
        self.assertEqual(metrics['spread_pips'], 2)
        self.assertEqual(metrics['lots'], Decimal(self.order['units']) / 100000)
        next_order = dict(self.order, units=self.order['units'] + 1000)
        next_metrics = fx.order_metrics(next_order, book(), self.cfg)
        self.assertTrue(next_metrics['risk_account'] > 50 or next_metrics['margin_account'] > 1500)
        self.assertTrue(self.risk()['pass'])

    def test_usdjpy_and_eur_account_pip_values(self):
        plan = fx.make_plan('USD/JPY', 'sell', 20, 2, book(), NOW, self.cfg)
        candidate = dict(plan, units=10000)
        metrics = fx.order_metrics(candidate, book(), self.cfg)
        self.assertAlmostEqual(float(metrics['pip_value_account']), 100 / 150)
        cfg = dict(self.cfg, account_currency='EUR')
        metrics = fx.order_metrics(dict(self.order), book(), cfg)
        self.assertAlmostEqual(float(metrics['pip_value_account']), self.order['units'] * .0001 / 1.1)

    def test_fees_slippage_and_overnight_reserve_reduce_size(self):
        cfg = dict(self.cfg, max_margin_pct=100, max_leverage=30, round_trip_commission_per_lot=0, slippage_pips=0)
        plain = fx.size_order(self.plan, book(), cfg, self.state)
        cfg.update(round_trip_commission_per_lot=100, slippage_pips=5, allow_overnight=True, swap_cost_per_lot_per_night=100)
        costly = fx.size_order(self.plan, book(), cfg, self.state)
        self.assertLess(costly['units'], plain['units'])

    def test_fresh_quote_required_including_conversions(self):
        for delta in (-121, 1):
            doc = quotes(); doc['quotes']['EUR/USD']['at'] = fx.iso(NOW + dt.timedelta(seconds=delta))
            fx.write(self.root / 'quotes.json', doc)
            self.assertEqual(self.run_cli('risk')[0], 2)
        doc = quotes(); doc['quotes']['EUR/USD']['ask'] = 1.09
        fx.write(self.root / 'quotes.json', doc)
        self.assertEqual(self.run_cli('risk')[0], 2)
        with self.assertRaises(fx.ForexError): fx.order_metrics(self.order, {'EUR/USD': book()['EUR/USD']}, dict(self.cfg, account_currency='JPY'))

    def test_wider_spread_and_price_drift_block(self):
        doc = quotes(); doc['quotes']['EUR/USD']['ask'] += .001
        result = self.risk(document=doc)
        self.assertFalse(result['pass'])
        self.assertIn('spread exceeds max_spread_pips', result['reasons'])
        doc = quotes(); doc['quotes']['EUR/USD'].update(bid=1.1040, ask=1.1042)
        with self.assertRaises(fx.ForexError): self.risk(document=doc)

    def test_bad_stop_step_precision_and_short_flag(self):
        for changes in ({'stop': self.order['entry']}, {'target': self.order['entry']},
                        {'units': 1500}, {'entry': 1.100201}, {'units': True}):
            with self.assertRaises(fx.ForexError): self.risk(order=dict(self.order, **changes))
        plan = fx.make_plan('EUR/USD', 'sell', 20, 2, book(), NOW, self.cfg)
        order = fx.size_order(plan, book(), self.cfg, self.state)
        self.assertFalse(self.risk(order=order, cfg=dict(self.cfg, allow_short=False))['pass'])

    def test_zero_size_and_account_state_currency(self):
        with self.assertRaises(fx.ForexError): fx.size_order(self.plan, book(), dict(self.cfg, max_loss_per_trade=.001), self.state)
        fx.write(self.root / 'state.json', dict(self.state, account_currency='EUR'))
        with self.assertRaises(fx.ForexError): fx.account_state(self.root / 'state.json', self.cfg)

    def test_macro_calendar_missing_stale_future_unverified_and_high_impact(self):
        path = self.root / 'calendar.json'
        for change in ({'verified': False}, {'as_of': '2026-11-14T10:00:00Z'}, {'as_of': '2026-11-17T10:00:00Z'}):
            doc = {'verified': True, 'as_of': fx.iso(NOW), 'source': 'fixture', 'events': []}; doc.update(change)
            fx.write(path, doc)
            with self.assertRaises(fx.ForexError): self.risk()
        fx.write(path, {'verified': True, 'as_of': fx.iso(NOW), 'source': 'fixture',
                       'events': [{'currency': 'USD', 'impact': 'high', 'at': '2026-11-16T10:10:00Z', 'title': 'Synthetic release'}]})
        self.assertIn('high-impact macro news blackout', self.risk()['reasons'])
        path.unlink()
        with self.assertRaises(fx.ForexError): self.risk()

    def test_daily_limits_and_pair_allowlist(self):
        state = copy.deepcopy(self.state)
        state['trades'] = [{'kind': 'entry', 'at': fx.iso(NOW), 'reserved_risk': 50}] * 3
        result = self.risk(state=state)
        self.assertIn('daily entry count exceeded', result['reasons'])
        self.assertTrue(any('daily reserved risk' in r for r in result['reasons']))
        self.assertFalse(self.risk(cfg=dict(self.cfg, allowed_pairs=['USD/JPY']))['pass'])

    def test_existing_positions_consume_margin_and_block_duplicates(self):
        state = copy.deepcopy(self.state)
        state['positions'] = [{'id': 'p1', 'pair': 'EUR/USD', 'side': 'buy', 'units': 10000, 'entry': 1.1002}]
        result = self.risk(state=state)
        self.assertIn('an open position already exists for this pair', result['reasons'])
        self.assertIn('estimated total margin exceeds configured cap', result['reasons'])

    def test_stale_order_previous_day_and_weekend(self):
        old = dict(self.order, created_at='2026-11-13T10:00:00Z', expires_at='2026-11-13T20:30:00Z')
        self.assertFalse(self.risk(order=old)['pass'])
        with self.assertRaises(fx.ForexError): fx.make_plan('EUR/USD', 'buy', 20, 2, book(), '2026-11-16T21:00:00Z', self.cfg)

    def test_exact_risk_context_required_and_no_bulk_yes(self):
        self.assertEqual(self.run_cli('risk')[0], 0)
        self.assertEqual(self.run_cli('broker', 'approve', '--reply', 'yes to all')[0], 2)
        changed = dict(self.order, units=self.order['units'] + 1000)
        fx.write(self.root / 'orders.json', changed)
        self.assertEqual(self.run_cli('broker', 'approve', '--reply', 'yes')[0], 2)

    def test_config_quote_or_account_change_invalidates_pass(self):
        for target in ('config', 'quotes', 'state'):
            self.assertEqual(self.run_cli('risk')[0], 0)
            if target == 'config':
                doc = fx.read(self.root / 'config.json'); doc['max_loss_per_trade'] -= 1
                fx.write(self.root / 'config.json', doc)
            elif target == 'quotes':
                doc = fx.read(self.root / 'quotes.json'); doc['quotes']['EUR/USD']['bid'] -= .00001
                fx.write(self.root / 'quotes.json', doc)
            else:
                state = fx.account_state(self.root / 'state.json', fx.config(self.root / 'config.json')); state['balance'] -= 1
                fx.write(self.root / 'state.json', state)
            self.assertEqual(self.run_cli('broker', 'approve', '--reply', 'yes')[0], 2, target)

    def test_complete_local_paper_entry_and_exit_with_pnl(self):
        self.assertEqual(self.run_cli('risk')[0], 0)
        self.assertEqual(self.run_cli('broker', 'paper-fill')[0], 2)
        self.assertEqual(self.run_cli('broker', 'approve', '--reply', 'yes')[0], 0)
        self.assertEqual(self.run_cli('broker', 'approve', '--reply', 'yes')[0], 2)
        code, result, err = self.run_cli('broker', 'paper-fill')
        self.assertEqual(code, 0, err)
        self.assertTrue(result['local_paper_only'])
        self.assertEqual(self.run_cli('broker', 'paper-fill')[0], 2)
        state = fx.read(self.root / 'state.json')
        position = state['positions'][0]
        later = NOW + dt.timedelta(minutes=5)
        doc = quotes(later); doc['quotes']['EUR/USD'].update(bid=1.1020, ask=1.1022)
        fx.write(self.root / 'quotes.json', doc)
        self.assertEqual(self.run_cli('broker', 'flatten', '--position-id', position['id'], now=later)[0], 0)
        self.assertEqual(self.run_cli('risk', now=later)[0], 0)
        self.assertEqual(self.run_cli('broker', 'approve', '--reply', 'yes', now=later)[0], 0)
        code, fill, err = self.run_cli('broker', 'paper-fill', now=later)
        self.assertEqual(code, 0, err)
        self.assertGreater(fill['pnl_account'], 0)
        final = fx.read(self.root / 'state.json')
        self.assertEqual(final['positions'], [])
        self.assertEqual(len(final['trades']), 2)
        self.assertGreater(final['balance'], 5000)

    def test_ctrader_draft_uses_account_specific_ids_and_centiunits(self):
        with self.assertRaises(fx.ForexError): fx.broker_payload(self.order, self.cfg)
        cfg = copy.deepcopy(self.cfg)
        cfg['broker']['account_id'] = 999
        cfg['broker']['symbols']['EUR/USD'] = dict(cfg['instrument_defaults'], verified=True, symbol_id=123, digits=5, pip_position=4)
        payload = fx.broker_payload(self.order, cfg)
        self.assertEqual(payload['status'], 'DRAFT_ONLY')
        wire = payload['payload']
        self.assertEqual(wire['volume'], self.order['units'] * 100)
        self.assertEqual(wire['ctidTraderAccountId'], 999)
        self.assertEqual(wire['symbolId'], 123)
        self.assertEqual(wire['relativeStopLoss'], 200)
        self.assertEqual(wire['relativeTakeProfit'], 400)

    def test_configurable_oanda_and_manual_payloads(self):
        cfg = copy.deepcopy(self.cfg); cfg['broker']['adapter'] = 'oanda'
        plan = fx.make_plan('EUR/USD', 'sell', 20, 2, book(), NOW, cfg)
        order = fx.size_order(plan, book(), cfg, self.state)
        result = fx.broker_payload(order, cfg)
        self.assertEqual(result['order']['instrument'], 'EUR_USD')
        self.assertEqual(result['order']['units'], str(-order['units']))
        cfg['broker']['adapter'] = 'manual'
        self.assertEqual(fx.broker_payload(order, cfg)['adapter'], 'manual')

    def test_alerts_use_exit_side_and_ring_once(self):
        self.assertEqual(self.run_cli('alerts', 'add', '--from-plan')[0], 0)
        doc = quotes(); doc['quotes']['EUR/USD'].update(bid=self.plan['stop'], ask=self.plan['stop'] + .0002)
        fx.write(self.root / 'quotes.json', doc)
        first = self.run_cli('alerts', 'check')
        self.assertEqual(first[0], 0, first[2])
        self.assertIn('stop', first[1]['rang'])
        self.assertEqual(self.run_cli('alerts', 'check')[1]['rang'], [])
        self.assertEqual(fx.read(self.root / 'state.json')['positions'], [])

    def test_installed_wrapper_end_to_end_and_stock_state_is_untouched(self):
        installed = Path(self.temp.name) / 'personal' / '.agents' / 'skills'
        import shutil
        for skill in ('trader-forex', 'trader-budget', 'trader-risk', 'trader-broker'):
            shutil.copytree(Path(ROOT) / skill, installed / skill, ignore=shutil.ignore_patterns('__pycache__'))
        stock_config = self.root.parent / 'config.json'
        stock_config.write_text('{"stock_sentinel":true}')
        for tool, args in [('trader-budget/size.py', ['--write']), ('trader-risk/check.py', []),
                           ('trader-broker/ticket.py', ['show'])]:
            result = subprocess.run([sys.executable, str(installed / tool), '--market', 'forex'] + args +
                                    ['--root', str(self.root), '--now', fx.iso(NOW)], cwd=self.temp.name,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stock_config.read_text(), '{"stock_sentinel":true}')
        self.assertFalse((self.root.parent / 'orders.json').exists())

    def test_null_timestamps_cannot_be_treated_as_current_time(self):
        with self.assertRaises(fx.ForexError): fx.stamp(None)
        document = quotes(); document['quotes']['EUR/USD']['at'] = None
        fx.write(self.root / 'quotes.json', document)
        self.assertEqual(self.run_cli('risk')[0], 2)
        fx.write(self.root / 'quotes.json', quotes())
        calendar_path = self.root / 'calendar.json'
        document = fx.read(calendar_path); document['as_of'] = None
        fx.write(calendar_path, document)
        self.assertEqual(self.run_cli('risk')[0], 2)

    def test_live_mode_never_uses_local_balance_as_real_account_clearance(self):
        result = self.risk(cfg=dict(self.cfg, paper=False))
        self.assertFalse(result['pass'])
        self.assertFalse(result['broker_connected'])
        self.assertEqual(result['ledger_source'], 'local_paper')
        cfg = copy.deepcopy(self.cfg); cfg['broker']['environment'] = 'live'
        self.assertFalse(self.risk(cfg=cfg)['pass'])

    def test_news_window_is_rechecked_after_initial_pass(self):
        fx.write(self.root / 'calendar.json', {'verified': True, 'as_of': fx.iso(NOW), 'source': 'fixture',
                 'events': [{'currency': 'EUR', 'impact': 'high', 'at': '2026-11-16T10:16:00Z', 'title': 'Synthetic release'}]})
        self.assertEqual(self.run_cli('risk')[0], 0)
        code, _, error = self.run_cli('broker', 'approve', '--reply', 'yes', now=NOW + dt.timedelta(seconds=61))
        self.assertEqual(code, 2)
        self.assertIn('high-impact macro news blackout', error)
        self.assertFalse((self.root / 'state.json').exists())

    def test_round_trip_stop_fills_respect_reserved_slippage_and_fees(self):
        for symbol, side, currency in [('EUR/USD', 'buy', 'USD'), ('EUR/USD', 'sell', 'USD'),
                                       ('USD/JPY', 'buy', 'USD'), ('USD/JPY', 'sell', 'USD'),
                                       ('EUR/USD', 'buy', 'EUR')]:
            cfg = dict(self.cfg, account_currency=currency)
            state = dict(self.state, account_currency=currency, positions=[], trades=[], approvals=[], rung=[])
            plan = fx.make_plan(symbol, side, 20, 2, book(), NOW, cfg)
            order = fx.size_order(plan, book(), cfg, state)
            verdict = self.risk(order=order, cfg=cfg, state=state)
            self.assertTrue(verdict['pass'])
            fx.paper_fill(order, book(), cfg, state, NOW, verdict)
            later = NOW + dt.timedelta(minutes=5)
            document = quotes(later)
            spread = Decimal('.02') if symbol.endswith('/JPY') else Decimal('.0002')
            stop = Decimal(str(plan['stop']))
            if side == 'buy': document['quotes'][symbol].update(bid=float(stop), ask=float(stop+spread))
            else: document['quotes'][symbol].update(bid=float(stop-spread), ask=float(stop))
            position = state['positions'][0]
            exit_order = {'market': 'forex', 'kind': 'exit', 'pair': symbol, 'units': order['units'],
                          'side': 'sell' if side == 'buy' else 'buy', 'position_id': position['id'],
                          'account_currency': currency}
            fx.paper_fill(exit_order, book(document), cfg, state, later, {})
            actual_loss = 5000 - state['balance']
            self.assertLessEqual(actual_loss, verdict['metrics']['risk_account'] + 1e-8, (symbol, side, currency))

    def test_ctrader_jpy_protection_is_price_units_not_pip_count(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['broker']['account_id'] = 999
        cfg['broker']['symbols']['USD/JPY'] = dict(cfg['instrument_defaults'], verified=True, symbol_id=789, digits=3, pip_position=2)
        plan = fx.make_plan('USD/JPY', 'sell', 20, 2, book(), NOW, cfg)
        order = fx.size_order(plan, book(), cfg, self.state)
        wire = fx.broker_payload(order, cfg)['payload']
        self.assertEqual(wire['relativeStopLoss'], 20000)
        self.assertEqual(wire['relativeTakeProfit'], 40000)
        bad = dict(order, units=1500)
        with self.assertRaises(fx.ForexError): fx.broker_payload(bad, cfg)

    def test_macro_only_matches_both_pair_currencies(self):
        fx.write(self.root / 'calendar.json', {'verified': True, 'as_of': fx.iso(NOW), 'source': 'fixture',
                 'events': [{'currency': 'JPY', 'impact': 'high', 'at': fx.iso(NOW), 'title': 'Synthetic JPY release'}]})
        self.assertTrue(self.risk()['pass'])
        plan = fx.make_plan('USD/JPY', 'buy', 20, 2, book(), NOW, self.cfg)
        order = fx.size_order(plan, book(), self.cfg, self.state)
        self.assertFalse(self.risk(order=order)['pass'])

    def test_malformed_json_objects_fail_with_a_clear_cli_error(self):
        for value in ([], True, None, {'market': 'forex', 'quotes': {'EUR/USD': False}}):
            fx.write(self.root / 'quotes.json', value)
            code, _, error = self.run_cli('risk')
            self.assertEqual(code, 2)
            self.assertIn('forex:', error)
            self.assertNotIn('Traceback', error)

    def test_scanner_entry_and_plan_commands_with_normalized_broker_bars(self):
        path = Path(self.temp.name) / 'bars.json'
        fx.write(path, candles())
        code, scanned, error = self.run_cli('scan', 'orb', '--pairs', 'EUR/USD', '--offline', str(path))
        self.assertEqual(code, 0, error)
        self.assertEqual(scanned['results'][0]['status'], 'triggered')
        code, entry, error = self.run_cli('entry', 'check', '--pair', 'EUR/USD', '--offline', str(path), '--write')
        self.assertEqual(code, 0, error)
        self.assertEqual(entry['results'][0]['trigger_at'], '2026-11-16T08:20:00Z')
        self.assertTrue(entry['results'][0]['stale_signal'])
        code, plan, error = self.run_cli('exit', 'plan', '--pair', 'EUR/USD', '--side', 'sell', '--stop-pips', '20', '--write')
        self.assertEqual(code, 0, error)
        self.assertGreater(plan['stop'], plan['entry'])
        self.assertLess(plan['target'], plan['entry'])

    def test_margin_sizing_accounts_for_entry_spread_slippage_and_fee(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['instrument_defaults'].update(min_units=1, step_units=1)
        order = fx.size_order(self.plan, book(), cfg, self.state)
        verdict = self.risk(order=order, cfg=cfg)
        self.assertTrue(verdict['pass'])
        state = copy.deepcopy(self.state)
        fx.paper_fill(order, book(), cfg, state, NOW, verdict)
        equity, margin = fx.exposure(state, book(), cfg)
        self.assertLessEqual(margin, equity * Decimal('.30'))
        next_order = dict(order, units=order['units']+1)
        self.assertFalse(self.risk(order=next_order, cfg=cfg)['pass'])

    def test_lot_size_and_volume_cap_come_from_broker_contract(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['instrument_defaults'].update(lot_size_units=10000, max_units=1000)
        order = fx.size_order(self.plan, book(), cfg, self.state)
        self.assertEqual(order['units'], 1000)
        self.assertEqual(order['sizing']['lots'], .1)
        self.assertEqual(order['sizing']['binding_limit'], 'broker_max')

    def test_favorable_entry_price_cannot_invert_protective_stop(self):
        for side in ('buy', 'sell'):
            plan = fx.make_plan('EUR/USD', side, 20, 2, book(), NOW, self.cfg)
            order = fx.size_order(plan, book(), self.cfg, self.state)
            document = quotes()
            if side == 'buy':
                document['quotes']['EUR/USD'].update(bid=plan['stop']-.0002, ask=plan['stop'])
            else:
                document['quotes']['EUR/USD'].update(bid=plan['stop'], ask=plan['stop']+.0002)
            with self.assertRaises(fx.ForexError): self.risk(order=order, document=document)

    def test_stop_inside_bid_ask_spread_is_rejected(self):
        plan = fx.make_plan('EUR/USD', 'buy', 1, 2, book(), NOW, self.cfg)
        with self.assertRaises(fx.ForexError): fx.size_order(plan, book(), self.cfg, self.state)

    def test_all_nine_wrappers_support_opt_in_forex(self):
        for folder, tool in [('trader-scanner', 'scan'), ('trader-news', 'news'), ('trader-entry', 'entry'),
                             ('trader-exit', 'exit'), ('trader-timemachine', 'timemachine'), ('trader-budget', 'size'),
                             ('trader-risk', 'check'), ('trader-broker', 'ticket'), ('trader-alerts', 'alerts')]:
            command = [sys.executable, str(Path(ROOT) / folder / (tool+'.py')), '--market=forex', '--help']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--root', result.stdout)


if __name__ == '__main__': unittest.main()
