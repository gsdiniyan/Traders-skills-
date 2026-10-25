"""trader-entry tests (NSE). Fixtures in tests/fixtures/trader-entry/:

ONGC-2026-10-23-5m.json  ONGC's Fri Oct 23 2026 five-minute bars (Yahoo chart JSON v8, real), cut like a live
                         answer: it ends with the in-progress 10:45 IST bar and a live tick at 10:49:45.
DEMO-1m-synthetic.json   SYNTHETIC one-minute bars (not market data): a wick above the range high, a close
                         AT the high, a null bar, a live tick.
"""

import copy
import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

entry = load("trader-entry", "entry")

EDIR = os.path.join(FIX, "trader-entry")
ONGC = os.path.join(EDIR, "ONGC-2026-10-23-5m.json")
DEMO = os.path.join(EDIR, "DEMO-1m-synthetic.json")
OPEN_TS = 1792727100       # 2026-10-23 09:15 IST
DT = datetime.datetime
DAY = "2026-10-23"


def ist(text):
    return entry.parse_now(text)


def doc(path=ONGC):
    with open(path) as fh:
        return json.load(fh)


def chart(d=None, sym="ONGC"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), ist(text), **kw)


def shift_to(d, day):
    """The same session moved to another date (IST has no daylight saving, so only the date changes)."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    new_open = int((DT.combine(day, datetime.time(3, 45)) - DT(1970, 1, 1)).total_seconds())
    delta = new_open - OPEN_TS
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    r["meta"]["regularMarketTime"] += delta
    return d


def no_breakout(d=None):
    """Every bar after the range stays under the 225.62 high."""
    d = copy.deepcopy(d if d is not None else doc())
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for i in range(3, len(q["close"])):
        q["close"][i] = min(q["close"][i], 225.50)
        q["high"][i] = min(q["high"][i], 225.60)
        q["open"][i] = min(q["open"][i], 225.50)
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheSession(unittest.TestCase):
    def test_triggers_at_1005_on_the_1000_bar(self):
        r = at(DAY + " 10:05")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["symbol"], "ONGC")
        self.assertEqual(r["opening_range"]["high"], 225.62)
        self.assertEqual(r["opening_range"]["low"], 224.61)
        self.assertEqual(r["opening_range"]["bars"], 3)
        t = r["trigger"]
        self.assertEqual(t["time"], "10:05")
        self.assertEqual(t["bar_start"], "10:00")
        self.assertEqual(t["bar_index"], 9)
        self.assertEqual(t["price"], 225.74)
        self.assertEqual(t["pct_over"], 0.05)
        self.assertEqual(r["level"], 225.62)
        self.assertEqual(r["prev_close"], 225.0)
        self.assertEqual((r["opens"], r["closes"]), ("09:15", "15:30"))

    def test_at_1002_the_1000_bar_is_still_forming(self):
        r = at(DAY + " 10:02")
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual(r["last"]["bar_start"], "09:55")
        self.assertEqual(r["last"]["close"], 225.46)
        self.assertEqual(r["forming"]["bar_start"], "10:00")
        self.assertIn("Rs 0.16 under the range high Rs 225.62", r["reason"])
        self.assertEqual(at(DAY + " 10:04:59")["status"], "waiting")

    def test_before_the_open_and_while_the_range_forms(self):
        r = at(DAY + " 09:05")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("NSE opens at 9:15", r["reason"])
        r = at(DAY + " 09:28")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        r = at(DAY + " 09:36")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "open"))
        self.assertEqual(r["opening_range"]["bars"], 3)

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        c = chart()
        self.assertEqual(c["bars"][-1]["t"], 1792732785)          # 10:49:45 is in the file
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))      # read as of the file's own time
        self.assertEqual(r["now"], DAY + " 10:49:45")
        self.assertEqual(r["_bars"][-1]["slot"], 17)               # the 10:40 bar
        self.assertEqual(r["forming"]["bar_start"], "10:45")
        self.assertEqual(r["bars_closed"], 18)
        r = at(DAY + " 10:51")                                    # a minute later it counts; the tick never becomes a bar
        self.assertEqual(r["_bars"][-1]["slot"], 18)
        self.assertEqual(r["bars_closed"], 19)
        self.assertTrue(all(b["t"] % 300 == 0 for b in r["_bars"]))

    def test_the_last_trade_before_a_bars_close_defers_it_until_yahoo_catches_up(self):
        d = no_breakout()
        r = at(DAY + " 10:50", d)
        self.assertEqual(r["_bars"][-1]["slot"], 17)
        self.assertIn("Yahoo's last trade is 10:49:45", r["lag_note"])
        self.assertEqual(at(DAY + " 10:51", d)["_bars"][-1]["slot"], 18)

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at(DAY + " 15:35")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertEqual(r["trigger"]["time"], "10:05")
        text = "\n".join(entry.describe(r))
        self.assertIn("minutes ago", text)
        self.assertNotIn("This is the moment", text)
        self.assertIn("Rs 225.74", text)


class Rules(unittest.TestCase):
    def test_volume_confirmation_moves_the_trigger(self):
        r = at(DAY + " 10:49", min_volume_x=3)
        self.assertEqual(r["opening_range"]["avg_volume"], 69399)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"]), ("10:25", 13, 225.74))
        self.assertEqual(t["volume_x"], 3.32)
        r = at(DAY + " 10:06", min_volume_x=3)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("2.34x the range's volume, under your 3.00x", r["reason"])
        self.assertEqual(at(DAY + " 10:30", min_volume_x=1)["trigger"]["time"], "10:05")

    def test_level_setup(self):
        r = at(DAY + " 10:30", setup="level", level=225.5)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"], r["trigger"]["price"]), ("10:05", 9, 225.74))
        r = at(DAY + " 10:30", setup="level", level=225.3)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("09:55", 7))
        # a level under the open: the first eligible close is the 09:25 bar's, at 09:30 (no_entries_before)
        r = at(DAY + " 10:30", setup="level", level=224.0)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("09:30", 2))
        with self.assertRaises(entry.EntryError):
            at(DAY + " 10:30", setup="level")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = no_breakout()
        self.assertEqual(at(DAY + " 10:51", d)["status"], "waiting")
        self.assertEqual(at(DAY + " 14:29", d)["status"], "waiting")
        r = at(DAY + " 14:30", d)
        self.assertEqual(r["status"], "expired")
        self.assertIn("by 14:30", r["reason"])
        self.assertEqual(at(DAY + " 16:30", d)["phase"], "after-close")

    def test_config_cutoff_and_floor(self):
        cfg = {"no_entries_before": "09:30", "no_entries_after": "10:00"}
        self.assertEqual(at(DAY + " 10:05", cfg=cfg)["status"], "expired")
        cfg = {"no_entries_before": "10:15", "no_entries_after": "14:30"}
        r = at(DAY + " 10:30", cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "10:25")          # the 10:20 bar, first to close above at or after 10:15

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at(DAY + " 10:15", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at(DAY + " 10:15", interval="15m")
        with self.assertRaises(entry.EntryError):
            entry.parse_now("tomorrow")
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")
        self.assertEqual(entry.parse_now(DAY + " 10:15"), DT(2026, 10, 23, 4, 45))
        self.assertEqual(entry.parse_now("2026-10-23T04:45:00Z"), DT(2026, 10, 23, 4, 45))
        self.assertEqual(entry.parse_now(DAY + " 10:15 IST"), DT(2026, 10, 23, 4, 45))

    def test_or_minutes_30(self):
        r = at(DAY + " 10:49", or_minutes=30)
        self.assertEqual(r["opening_range"]["bars"], 6)
        self.assertEqual(r["opening_range"]["high"], 225.62)
        self.assertEqual(r["trigger"]["time"], "10:05")


class OneMinute(unittest.TestCase):
    def c(self):
        return entry.parse_chart(json.dumps(doc(DEMO)), "DEMO")

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m")
        self.assertEqual(r["now"], DAY + " 09:50:30")
        orr = r["opening_range"]
        self.assertEqual((orr["high"], orr["low"], orr["minutes"], orr["bars"], orr["of"], orr["complete"]), (100.5, 99.0, 15, 15, 15, True))
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("09:44", "09:43", 28, 100.62))
        r = entry.evaluate(c, ist(DAY + " 09:43:59"), interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:43")
        self.assertEqual(r["last"]["close"], 100.5)               # the 09:42 bar closed AT the high

    def test_null_bar_tick_and_volume(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", min_volume_x=1.5)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("09:44", 28))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(32, slots)                                # 09:47 came as nulls
        self.assertEqual(slots[-1], 34)                            # the 09:49 bar; the 09:50 bar is still forming and the 09:50:30 tick is dropped

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "DEMO", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)


class Calendar(unittest.TestCase):
    def test_the_same_session_on_another_date_triggers_at_the_same_clock_time(self):
        for day in (datetime.date(2026, 7, 1), datetime.date(2026, 12, 7), datetime.date(2026, 3, 9)):
            d = shift_to(doc(), day)
            r = entry.evaluate(chart(d), ist("%s 10:05" % day))
            self.assertEqual(r["status"], "triggered", day)
            self.assertEqual(r["trigger"]["time"], "10:05", day)
            self.assertEqual(r["trigger"]["bar_index"], 9, day)
            self.assertEqual(entry.evaluate(chart(d), ist("%s 10:02" % day))["status"], "waiting", day)
        self.assertEqual(ist("2026-12-07 10:15"), DT(2026, 12, 7, 4, 45))             # IST is UTC+5:30 all year

    def test_weekend_holiday_and_empty_data(self):
        r = at("2026-10-24 10:30")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertEqual(r["next_open"], "2026-10-26 09:15")
        self.assertIn("weekend", r["reason"])
        r = at("2026-10-20 10:30")
        self.assertIn("Dussehra", r["reason"])
        self.assertEqual(r["next_open"], "2026-10-21 09:15")
        empty = copy.deepcopy(doc())
        res = empty["chart"]["result"][0]
        del res["timestamp"]
        res["indicators"] = {"quote": [{}]}
        r = at(DAY + " 10:15", empty)
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))
        r = at("2026-10-26 10:15")                                # Yahoo sent Friday's bars only
        self.assertEqual(r["status"], "no-data")

    def test_the_calendar_knows_2026_and_asks_for_the_holiday_file_after_that(self):
        self.assertTrue(at(DAY + " 10:30")["calendar_known"])
        self.assertFalse(at("2028-03-01 10:30")["calendar_known"])
        self.assertIn("holiday list for 2028 is not loaded", "\n".join(entry.describe(at("2028-03-01 10:30"))))

    def test_trader_holidays_json_adds_closures(self):
        d = tempfile.mkdtemp()
        old = os.getcwd()
        try:
            os.chdir(d)
            os.mkdir("trader")
            with open(os.path.join("trader", "holidays.json"), "w") as fh:
                json.dump({"holidays": {"2027-01-26": "Republic Day"}}, fh)
            ok, why = entry.trading_day(datetime.date(2027, 1, 26))
            self.assertFalse(ok)
            self.assertIn("Republic Day", why)
            self.assertTrue(entry.calendar_known(2027))
            self.assertTrue(entry.trading_day(datetime.date(2027, 1, 27))[0])
        finally:
            os.chdir(old)
            shutil.rmtree(d)

    def test_yahoo_errors(self):
        bad = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_chart(bad, "ZZZZ")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_chart("<html>", "ONGC")


class Symbols(unittest.TestCase):
    def test_yahoo_symbols(self):
        self.assertEqual(entry.yahoo_symbol("ongc"), "ONGC.NS")
        self.assertEqual(entry.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(entry.yahoo_symbol("NIFTY"), "^NSEI")
        self.assertEqual(entry.yahoo_symbol("BANKNIFTY"), "^NSEBANK")
        self.assertEqual(entry.yahoo_symbol("TCS.NS"), "TCS.NS")
        self.assertEqual(entry.nse_symbol("ONGC.NS"), "ONGC")
        self.assertEqual(entry.nse_symbol("^NSEI"), "NIFTY")
        self.assertEqual(entry.chart_path("m&m", "1d", "5m"), "/v8/finance/chart/M%26M.NS?range=1d&interval=5m")
        self.assertEqual(entry.chart_path("BAJAJ-AUTO", "5d", "5m"), "/v8/finance/chart/BAJAJ-AUTO.NS?range=5d&interval=5m")


class Network(unittest.TestCase):
    """Yahoo is never called: _open_url and _sleep are swapped for fakes."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._open, self._sleep = entry._open_url, entry._sleep
        entry._sleep = lambda s: self.sleeps.append(s)
        with open(ONGC) as fh:
            self.body = fh.read()

    def tearDown(self):
        entry._open_url, entry._sleep = self._open, self._sleep

    def answers(self, *codes):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return self.body
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"chart":{"result":null,'
                                                                           b'"error":{"description":"No data found"}}}'))
        entry._open_url = fake

    def test_url_and_429_retry_once_after_a_pause(self):
        self.answers(429, 200)
        c = entry.get_chart("ONGC", "5m", datetime.date(2026, 10, 23), today=datetime.date(2026, 10, 23))
        self.assertEqual(len(c["bars"]), 20)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com/v8/finance/chart/ONGC.NS?range=1d&interval=5m"] * 2)
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_429_twice_falls_back_to_query1(self):
        self.answers(429, 429, 200)
        entry.get_chart("ONGC", "5m", datetime.date(2026, 10, 23), today=datetime.date(2026, 10, 23))
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])

    def test_5xx_falls_back_without_waiting(self):
        self.answers(503, 200)
        entry.get_chart("ONGC", "5m", datetime.date(2026, 10, 23), today=datetime.date(2026, 10, 23))
        self.assertEqual(self.sleeps, [])
        self.assertIn("query1", self.calls[1])

    def test_still_busy_is_one_plain_error(self):
        self.answers(429, 429, 429, 429)
        code, out, err = run(["check", "ONGC", "--now", DAY + " 10:15"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_symbol_404(self):
        self.answers(404)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("ZZZZ", "5m", datetime.date(2026, 10, 23), today=datetime.date(2026, 10, 23))
        self.assertIn("No data found", str(cm.exception))

    def test_ranges_for_past_sessions(self):
        today = datetime.date(2026, 10, 26)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 23), today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 13), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 9, 10), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 10, 13), today, "1m")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 6, 1), today, "5m")


def live_provider(d, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the in-progress bar
    showing a spike to 225.70 (above the 225.62 high, then it closes under), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind."""
    base = chart(d)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = 225.70, max(b["h"], 225.70)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": 225.70, "h": 225.70, "l": 225.70, "c": 225.70, "v": 0})
        return {"symbol": "ONGC.NS", "meta": {}, "bars": bars, "prev_close": 225.0, "data_time": data_ts}
    return provider


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, provider, start, **kw):
        clock = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        # 09:57: the 09:55 bar is in progress at a spike of 225.70, above the range high; it closes at 225.46
        code, res, lines, fired, clock = self.loop(live_provider(doc(), calls=calls), DAY + " 09:57", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:05")
        self.assertEqual(fired[0][0]["trigger"]["price"], 225.74)
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["09:57:00", "10:00:02", "10:05:02"])
        text = "\n".join(lines)
        self.assertIn(" 9:55 bar  closed Rs 225.46  Rs 0.16 under Rs 225.62", text)
        self.assertIn("10:00 bar  closed Rs 225.74  TRIGGERED", text)
        self.assertEqual(text.count("TRIGGERED"), 1)

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ist(DAY + " 10:05:30")
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), lag_until=lag, calls=calls), DAY + " 10:02", every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times, ["10:02:00", "10:05:02", "10:05:32"])    # 10:05:02: Yahoo still at 10:04:32
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:05")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop(live_provider(doc()), DAY + " 09:00", every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("not open yet", lines[0])
        self.assertIn(" 9:25 bar  closed Rs 224.84  opening range set: high Rs 225.62, low Rs 224.61", text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 10)    # 09:15 ... 10:00, one line each

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(ist(DAY + " 09:31"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(doc(), calls=calls), clock.now, clock.sleep, lines.append,
                                     dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        for start in (" 9:30", " 9:35", " 9:40", " 9:45"):
            self.assertIn(start + " bar  closed", text)          # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "10:05")

    def test_expires_and_until(self):
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), DAY + " 14:00", every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_ist(clock.now()).strftime("%H:%M"), "14:30")
        until = DT(2026, 10, 23, 11, 0)
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), DAY + " 10:30", every=300, until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-24 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, DAY + " 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "ONGC", "--offline", ONGC, "--now", DAY + " 10:05", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 10:05 at Rs 225.74", text)
        self.assertIn("/trader-exit", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "ONGC", "setup": "orb", "side": "buy", "date": DAY,
                "status": "triggered", "time": "10:05", "bar_start": "10:00", "bar_index": 9, "price": 225.74,
                "level": 225.62, "interval": "5m", "opening_range": {"high": 225.62, "low": 224.61, "minutes": 15},
                "source": "yahoo", "checked_at": "2026-10-23T04:35:00Z"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "ONGC", "--offline", ONGC, "--now", DAY + " 10:02", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "ONGC", "--offline", ONGC, "--now", DAY + " 10:02", "--write", "e.json"])
        with open("e.json") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)

    def test_offline_gz_folder_and_mismatch(self):
        os.mkdir("charts")
        with open(ONGC, "rb") as src, gzip.open(os.path.join("charts", "ONGC.json.gz"), "wb") as dst:
            dst.write(src.read())
        code, text, _ = run(["check", "ongc", "--offline", "charts", "--now", DAY + " 10:05"])
        self.assertEqual(code, 0)
        code, _, err = run(["check", "TCS", "--offline", ONGC])
        self.assertEqual(code, 1)
        self.assertIn("holds ONGC, not TCS", err)

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "ONGC", "--offline", ONGC, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)
        code, _, err = run(["check", "ONGC", "--offline", ONGC, "--level", "300"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "ONGC", "--offline", ONGC, "--setup", "level"])
        self.assertIn("needs --level", err)

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"no_entries_after": "10:00"}, fh)
        code, text, _ = run(["check", "ONGC", "--offline", ONGC, "--now", DAY + " 10:05"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "ONGC", "--offline", ONGC])
        self.assertIn("not valid JSON", err)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "ONGC", "--offline", ONGC, "--now", DAY + " 09:40", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn("10:00 bar  closed Rs 225.74  TRIGGERED", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["bar_index"], 9)
        code, text, _ = run(["watch", "ONGC", "--offline", ONGC, "--now", DAY + " 09:40", "--until", "09:50", "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(ONGC, os.path.join("charts", "ONGC.json"))
        shutil.copy(DEMO, os.path.join("charts", "DEMO.json"))
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [
                {"symbol": "ONGC", "setups": ["orb"]}, {"symbol": "ZZZZ", "setups": ["orb"]}, {"symbol": "DEMO", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", DAY + " 10:05"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertTrue(rows[1].startswith("ONGC          triggered  10:05     225.74      225.62"), rows[1])
        self.assertIn("ZZZZ          error: no offline file", rows[2])
        self.assertIn("DEMO          error: DEMO has 1m bars", rows[3])
        self.assertIn("1 of 3 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", DAY + " 10:02", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /trader-scanner first", err)


if __name__ == "__main__":
    unittest.main()
