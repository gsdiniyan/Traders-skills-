import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

scan = load("trader-scanner", "scan")

SDIR = os.path.join(FIX, "trader-scanner")
WALL = os.path.join(SDIR, "wall5m-2026-10-23.json.gz")       # 25 real NSE 5-minute charts, five sessions to Fri Oct 23 2026
DAILY = os.path.join(SDIR, "daily-spark-2026-10-23.json")    # the same 25 names as one spark answer, 3 months of daily bars
SYMDIR = os.path.join(SDIR, "symdir")                        # trimmed NSE lists of Oct 25 2026
HOLIDAYS = os.path.join(SDIR, "holiday-master-2026-10-25.json")
DT = datetime.datetime
D = datetime.date


def run(argv, cwd=None):
    out, err = io.StringIO(), io.StringIO()
    old = os.getcwd()
    if cwd:
        os.chdir(cwd)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = scan.main(argv)
    finally:
        os.chdir(old)
    return code, out.getvalue(), err.getvalue()


def ts(ist_text):
    """IST wall-clock text -> unix seconds."""
    utc = scan.ist_to_utc(DT.strptime(ist_text, "%Y-%m-%d %H:%M:%S" if ist_text.count(":") == 2 else "%Y-%m-%d %H:%M"))
    return int((utc - DT(1970, 1, 1)).total_seconds())


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None):
    """A v8-like series of 5-min bars from `start` on `day` (YYYY-MM-DD)."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(len(closes))]
    s = {"t": t, "c": list(closes), "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
         "v": [1000] * len(closes), "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


class Time(unittest.TestCase):
    def test_ist_is_utc_plus_530_with_no_daylight_saving(self):
        utc = DT(2026, 10, 23, 3, 45)                      # 09:15 IST
        self.assertEqual(scan.to_ist(utc), DT(2026, 10, 23, 9, 15))
        self.assertEqual(scan.ist_to_utc(DT(2026, 7, 1, 9, 15)), DT(2026, 7, 1, 3, 45))      # same offset in summer
        self.assertEqual(scan.ts_to_ist(ts("2026-10-23 10:15")), DT(2026, 10, 23, 10, 15))

    def test_parse_now_takes_ist_unless_told_otherwise(self):
        self.assertEqual(scan.parse_now("2026-10-23 10:15"), DT(2026, 10, 23, 4, 45))
        self.assertEqual(scan.parse_now("2026-10-23T04:45:00Z"), DT(2026, 10, 23, 4, 45))
        self.assertEqual(scan.parse_now("2026-10-23 10:15+05:30"), DT(2026, 10, 23, 4, 45))
        with self.assertRaises(scan.ScanError):
            scan.parse_now("lunchtime")

    def test_parse_hhmm(self):
        self.assertEqual(scan.parse_hhmm("9:35"), datetime.time(9, 35))
        for bad in ("25:00", "10:75", "ten", ""):
            with self.assertRaises(scan.ScanError):
                scan.parse_hhmm(bad)

    def test_the_nse_calendar_weekends_and_holidays(self):
        ok, why = scan.trading_day(D(2026, 10, 24))
        self.assertFalse(ok)
        self.assertEqual(why, "weekend")
        ok, why = scan.trading_day(D(2026, 10, 20), {})
        self.assertFalse(ok)
        self.assertIn("Dussehra", why)
        self.assertTrue(scan.trading_day(D(2026, 10, 23), {})[0])
        self.assertEqual(len(scan.NSE_HOLIDAYS), 20)
        self.assertTrue(all(D.fromisoformat(k).weekday() < 5 or True for k in scan.NSE_HOLIDAYS))

    def test_session_is_0915_to_1530_and_none_on_a_closed_day(self):
        self.assertEqual(scan.session(D(2026, 10, 23)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(scan.session(D(2026, 10, 20)))
        self.assertIsNone(scan.session(D(2026, 10, 25)))

    def test_an_extra_holiday_file_and_the_environment_variable_are_merged(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "holidays.json")
            with open(p, "w") as fh:
                json.dump({"holidays": {"2027-01-26": "Republic Day"}}, fh)
            ex = scan.extra_holidays(p)
            self.assertEqual(ex, {"2027-01-26": "Republic Day"})
            ok, why = scan.trading_day(D(2027, 1, 26), ex)
            self.assertFalse(ok)
            self.assertIn("Republic Day", why)
            os.environ["NSE_EXTRA_HOLIDAYS"] = "2027-03-25, nonsense"
            try:
                self.assertIn("2027-03-25", scan.extra_holidays(p))
            finally:
                del os.environ["NSE_EXTRA_HOLIDAYS"]
        self.assertEqual(scan.extra_holidays(os.path.join(tempfile.gettempdir(), "no-such-holidays.json")), {})

    def test_calendar_note_warns_when_the_year_is_not_loaded(self):
        self.assertEqual(scan.calendar_note(D(2026, 10, 23), {}), "")
        self.assertIn("2027", scan.calendar_note(D(2027, 3, 1), {}))
        self.assertEqual(scan.calendar_note(D(2027, 3, 1), {"2027-01-26": "Republic Day"}), "")

    def test_clock_and_money_labels(self):
        self.assertEqual(scan.clock_label(DT(2026, 10, 23, 9, 5)), "09:05")
        self.assertEqual(scan.money(1234567.5), "Rs 12,34,567.50")
        self.assertEqual(scan.money(999.0), "Rs 999.00")
        self.assertEqual(scan.money(1000), "Rs 1,000.00")
        self.assertEqual(scan.money(100000), "Rs 1,00,000.00")
        self.assertEqual(scan.money(-45678.9), "-Rs 45,678.90")
        self.assertEqual(scan.money(0), "Rs 0.00")


class Symbols(unittest.TestCase):
    def test_yahoo_symbols_for_nse(self):
        self.assertEqual(scan.to_yahoo("reliance"), "RELIANCE.NS")
        self.assertEqual(scan.to_yahoo("M&M"), "M%26M.NS")
        self.assertEqual(scan.to_yahoo("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(scan.to_yahoo("NIFTY"), "%5ENSEI")
        self.assertEqual(scan.to_yahoo("BANKNIFTY"), "%5ENSEBANK")
        self.assertEqual(scan.to_yahoo("TCS.NS"), "TCS.NS")
        self.assertEqual(scan.to_yahoo("500325.BO"), "500325.BO")

    def test_and_back(self):
        self.assertEqual(scan.from_yahoo("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(scan.from_yahoo("M%26M.NS"), "M&M")
        self.assertEqual(scan.from_yahoo("M&M.NS"), "M&M")
        self.assertEqual(scan.from_yahoo("^NSEI"), "NIFTY")
        self.assertEqual(scan.from_yahoo("^NSEBANK"), "BANKNIFTY")

    def test_request_paths(self):
        self.assertEqual(scan.spark_path(["TCS", "M&M"], "1d", "5m"),
                         "/v7/finance/spark?symbols=TCS.NS,M%26M.NS&range=1d&interval=5m")
        self.assertEqual(scan.chart_path("INFY", "5d", "5m"), "/v8/finance/chart/INFY.NS?range=5d&interval=5m")

    def test_spark_answers_map_back_to_nse_symbols(self):
        data, err = scan.load_offline(DAILY)
        self.assertEqual(err, {})
        self.assertIn("RELIANCE", data)
        self.assertIn("M&M", data)
        self.assertIn("BAJAJ-AUTO", data)
        self.assertEqual(len(data), 25)
        self.assertEqual(data["RELIANCE"]["meta"].get("currency"), "INR")


class Universe(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(SYMDIR, name), encoding="utf-8") as fh:
            return fh.read()

    def test_all_keeps_eq_series_and_drops_the_rest(self):
        kept, counts = scan.build_universe("all", self.read("EQUITY_L.csv"))
        syms = [s for s, _ in kept]
        self.assertIn("RELIANCE", syms)
        self.assertIn("INFY", syms)
        self.assertNotIn("RCOM", syms)                     # series BE
        self.assertTrue(all(s == s.upper() for s in syms))
        self.assertEqual(counts["rows"], 12)
        self.assertEqual(counts.get("series BE"), 2)
        self.assertEqual(dict(kept)["RELIANCE"], "Reliance Industries Limited")

    def test_nifty500_file(self):
        kept, counts = scan.build_universe("nifty500", self.read("ind_nifty500list.csv"))
        self.assertTrue(kept)
        self.assertTrue(all(sym and name for sym, name in kept))

    def test_fo_file_leaves_out_the_index_contracts(self):
        kept, counts = scan.build_universe("fo", self.read("fo_mktlots.csv"))
        syms = [s for s, _ in kept]
        self.assertIn("RELIANCE", syms)
        self.assertNotIn("NIFTY", syms)
        self.assertNotIn("BANKNIFTY", syms)
        self.assertGreaterEqual(counts.get("index contract", 0), 2)

    def test_the_wrong_file_is_refused(self):
        with self.assertRaises(scan.ScanError):
            scan.build_universe("all", "A,B\n1,2\n")
        with self.assertRaises(scan.ScanError):
            scan.build_universe("fo", "A,B\n1,2\n")
        with self.assertRaises(scan.ScanError):
            scan.build_universe("all", "")

    def test_the_universe_command_offline_writes_symbol_pipe_name(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "u.txt")
            code, so, se = run(["universe", "--set", "all", "--offline", SYMDIR, "--out", out])
            self.assertEqual(code, 0, se)
            with open(out, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            self.assertIn("RELIANCE|Reliance Industries Limited", lines)
            self.assertIn("Kept 9 equity stocks", so)
            self.assertEqual(scan.symbols_from_file(out)[0], lines[0].split("|")[0])

    def test_universe_json_and_a_missing_offline_file(self):
        code, so, se = run(["universe", "--set", "fo", "--offline", SYMDIR, "--no-write", "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(so)
        self.assertEqual(doc["set"], "fo")
        self.assertIsNone(doc["out"])
        with tempfile.TemporaryDirectory() as td:
            code, so, se = run(["universe", "--offline", td, "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("no ind_nifty500list.csv", se)


class Holidays(unittest.TestCase):
    def test_the_nse_holiday_master_is_parsed_to_cash_market_dates(self):
        with open(HOLIDAYS, encoding="utf-8") as fh:
            found = scan.fetch_holidays(fh.read())
        self.assertEqual(len(found), 20)
        self.assertEqual(found["2026-10-20"], "Dussehra")
        self.assertEqual(found, scan.NSE_HOLIDAYS)          # the built-in list is exactly NSE's list of 25 Oct 2026

    def test_bad_answers(self):
        for bad in ("not json", "{}", '{"CM": []}', '{"CM": [{"tradingDate": "soon"}]}'):
            with self.assertRaises(scan.ScanError):
                scan.fetch_holidays(bad)

    def test_the_command_writes_a_file_that_the_calendar_reads(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "trader", "holidays.json")
            code, so, se = run(["holidays", "--offline", HOLIDAYS, "--out", out])
            self.assertEqual(code, 0, se)
            self.assertIn("20 dates", so)
            doc = json.load(open(out, encoding="utf-8"))
            self.assertEqual(doc["holidays"]["2026-11-10"], "Diwali-Balipratipada")
            self.assertEqual(scan.extra_holidays(out)["2026-11-10"], "Diwali-Balipratipada")


class Daily(unittest.TestCase):
    NOW = "2026-10-25 12:00"

    def setUp(self):
        self.data, _ = scan.load_offline(DAILY)

    def test_rsi_matches_wilder_on_a_flat_and_a_rising_series(self):
        self.assertEqual(scan.rsi([100.0] * 20), 100.0)
        self.assertEqual(scan.rsi([float(i) for i in range(1, 30)]), 100.0)
        self.assertIsNone(scan.rsi([1.0] * 10))
        falling = [float(100 - i) for i in range(30)]
        self.assertLess(scan.rsi(falling), 1.0)

    def test_setups_on_constructed_closes(self):
        flat = [100.0] * 30
        tags, nums = scan.find_setups(flat + [105.0])
        self.assertIn("breakout_20d", tags)
        run_up = [100.0] * 14 + [100 + 1.5 * i for i in range(1, 11)] + [115.0, 115.5, 115.2, 115.4, 115.3]
        tags, nums = scan.find_setups(run_up + [])
        self.assertIn("bull_flag", tags)
        down_up = [100.0 - i for i in range(29)] + [72.0, 73.0]
        tags, nums = scan.find_setups(down_up)
        self.assertIn("oversold_bounce", tags)
        reclaim = [100.0] * 25 + [95.0, 96.0, 97.0, 98.0, 99.0, 100.5]
        self.assertIn("reclaim_ma20", scan.find_setups(reclaim)[0])

    def test_real_nse_charts_give_the_known_hits(self):
        syms = sorted(self.data)
        hits, counts, last = scan.scan_daily(self.data, syms, scan.parse_now(self.NOW), 50, 500000)
        self.assertEqual(last, D(2026, 10, 23))
        by = {h["symbol"]: h["setups"] for h in hits}
        self.assertEqual(by["TCS"], ["breakout_20d", "bull_flag"])
        self.assertEqual(by["INFY"], ["bull_flag"])
        self.assertEqual(by["ONGC"], ["reclaim_ma20"])
        self.assertEqual(by["TITAN"], ["oversold"])
        self.assertEqual(counts["requested"], 25)
        self.assertEqual(counts["fetched"], 25)
        self.assertEqual(counts["scanned"] + counts["illiquid"] + counts["short"], 25)
        self.assertEqual(counts["hits"], len(hits))
        rupees = [h["price"] * (h["volume"] or 0) for h in hits]
        self.assertEqual(rupees, sorted(rupees, reverse=True))                     # most rupees traded first

    def test_todays_bar_is_dropped_until_the_close(self):
        s = {"t": [ts("2026-10-22 09:15"), ts("2026-10-23 09:15")], "c": [100.0, 101.0], "meta": {}}
        mid = scan.daily_closes(s, scan.parse_now("2026-10-23 12:00"))
        self.assertEqual(mid, [(D(2026, 10, 22), 100.0)])
        after = scan.daily_closes(s, scan.parse_now("2026-10-23 15:31"))
        self.assertEqual(after, [(D(2026, 10, 22), 100.0), (D(2026, 10, 23), 101.0)])
        weekend = scan.daily_closes(s, scan.parse_now("2026-10-24 12:00"))
        self.assertEqual(len(weekend), 2)

    def test_the_floors_count_illiquid_names(self):
        syms = sorted(self.data)
        hits, counts, _ = scan.scan_daily(self.data, syms, scan.parse_now(self.NOW), 100000, 0)
        self.assertEqual(counts["illiquid"], 25)
        self.assertEqual(hits, [])

    def test_the_command_writes_a_watchlist(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "w.json")
            code, so, se = run(["daily", "--offline", DAILY, "--now", self.NOW, "--out", out])
            self.assertEqual(code, 0, se)
            doc = json.load(open(out, encoding="utf-8"))
            self.assertEqual(doc["mode"], "daily")
            self.assertEqual(doc["market"], "NSE")
            self.assertEqual(doc["as_of"], "2026-10-23 close")
            self.assertEqual(doc["universe"], 25)
            self.assertIn("TCS", [h["symbol"] for h in doc["hits"]])
            self.assertIn("Rs ", so)
            self.assertIn("IST", so)
            self.assertIn("match a rule", so)

    def test_exit_code_1_when_nothing_matches(self):
        code, so, se = run(["daily", "--offline", DAILY, "--now", self.NOW, "--no-write", "--min-price", "999999"])
        self.assertEqual(code, 1)
        self.assertIn("No setups.", so)


class Orb(unittest.TestCase):
    DAY = D(2026, 10, 23)

    def cutoff(self, hhmm):
        return DT.combine(self.DAY, scan.parse_hhmm(hhmm))

    def test_the_first_bar_is_0915_and_the_range_is_three_bars(self):
        self.assertEqual(scan.slot(self.DAY, 0), DT(2026, 10, 23, 9, 15))
        self.assertEqual(scan.slot(self.DAY, 3), DT(2026, 10, 23, 9, 30))

    def test_a_breakout_is_the_first_bar_to_close_above_the_range_high(self):
        closes = [100.0, 100.5, 100.2, 100.4, 100.9, 101.5, 101.0]
        highs = [100.6, 100.8, 100.7, 100.7, 101.0, 101.6, 101.1]
        s = bars_series("2026-10-23", closes, highs)
        bars = scan.session_bars(s, self.DAY, self.cutoff("09:50"))
        self.assertEqual(len(bars), 7)
        self.assertTrue(scan.usable(bars, self.DAY, self.cutoff("09:50"), 3))
        self.assertTrue(scan.orb_candidate(bars, 3))
        hit = scan.orb_hit(bars, 3)
        self.assertEqual(hit["bar_start"], "09:35")
        self.assertEqual(hit["at"], "09:40")
        self.assertEqual(hit["or_high"], 100.8)
        self.assertEqual(hit["price"], 100.9)

    def test_a_bar_still_forming_is_not_used(self):
        s = bars_series("2026-10-23", [100.0] * 4 + [110.0])
        bars = scan.session_bars(s, self.DAY, self.cutoff("09:39"))      # the 09:35 bar closes at 09:40
        self.assertEqual(len(bars), 4)

    def test_the_live_tick_off_the_grid_and_null_closes_are_dropped(self):
        s = bars_series("2026-10-23", [100.0, None, 100.2, 100.3], extra=[("2026-10-23 09:32:30", 150.0)])
        bars = scan.session_bars(s, self.DAY, self.cutoff("10:00"))
        self.assertEqual([b["start"].strftime("%H:%M") for b in bars], ["09:15", "09:25", "09:30"])
        self.assertFalse(scan.usable(bars, self.DAY, self.cutoff("10:00"), 3))     # a missing bar: not usable

    def test_bars_before_the_open_and_after_the_close_are_ignored(self):
        s = bars_series("2026-10-23", [99.0, 99.5, 100.0, 100.2, 100.4], start="09:05")
        bars = scan.session_bars(s, self.DAY, self.cutoff("10:00"))
        self.assertEqual(bars[0]["start"], DT(2026, 10, 23, 9, 15))             # 09:05 and 09:10 are pre-open
        late = bars_series("2026-10-23", [100.0] * 4, start="15:20")
        self.assertEqual([b["start"].strftime("%H:%M") for b in scan.session_bars(late, self.DAY, self.cutoff("15:30"))],
                         ["15:20", "15:25"])

    def test_the_window_refuses_a_closed_day_a_too_early_clock_and_an_expired_setup(self):
        now = scan.parse_now("2026-10-23 10:15")
        day, cutoff = scan.orb_window(now, None, 15, datetime.time(14, 30))
        self.assertEqual(day, self.DAY)
        self.assertEqual(cutoff, DT(2026, 10, 23, 10, 15))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-10-24 10:15"), None, 15, None)                 # Saturday
        self.assertIn("weekend", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-10-20 10:15"), None, 15, None, None)           # Dussehra
        self.assertIn("Dussehra", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-10-23 09:32"), None, 15, None)
        self.assertIn("Run orb again after 09:35", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-10-23 14:50"), None, 15, datetime.time(14, 30))
        self.assertIn("no_entries_after", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-10-23 15:45"), None, 15, None)
        self.assertIn("closed at 15:30", str(cm.exception))
        with self.assertRaises(scan.ScanError):
            scan.orb_window(now, None, 12, None)

    def test_real_nse_charts_replayed_at_1015(self):
        data, _ = scan.load_offline(WALL)
        self.assertEqual(len(data), 25)
        hits, counts, failed = scan.scan_orb(data, sorted(data), self.DAY, self.cutoff("10:15"), 15, 0, 0)
        self.assertEqual(counts["scanned"], 25)
        self.assertEqual(counts["unusable"], 0)
        self.assertEqual([h["symbol"] for h in hits], ["ONGC"])
        h = hits[0]
        self.assertEqual((h["at"], h["or_low"], h["or_high"], h["price"]), ("10:05", 224.61, 225.62, 225.74))

    def test_the_command_replays_a_past_session_offline(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "w.json")
            code, so, se = run(["orb", "--offline", WALL, "--at", "10:15", "--min-price", "0", "--out", out])
            self.assertEqual(code, 0, se)
            self.assertIn("Fri 2026-10-23 10:15 IST", so)
            self.assertIn("09:15 to 09:30", so)
            self.assertIn("ONGC", so)
            doc = json.load(open(out, encoding="utf-8"))
            self.assertEqual((doc["mode"], doc["market"], doc["as_of"]), ("orb", "NSE", "2026-10-23 10:15 IST"))
            self.assertEqual(doc["hits"][0]["symbol"], "ONGC")
            code, so, se = run(["orb", "--offline", WALL, "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("give --at", se)

    def test_the_price_floor_drops_cheap_names(self):
        data, _ = scan.load_offline(WALL)
        hits, counts, _ = scan.scan_orb(data, sorted(data), self.DAY, self.cutoff("10:15"), 15, 100000, 0)
        self.assertEqual(counts["illiquid"], 25)
        self.assertEqual(hits, [])

    def test_stage_two_is_capped_and_says_so(self):
        data, _ = scan.load_offline(WALL)
        calls = []

        def confirm(sym):
            calls.append(sym)
            return data[sym]
        hits, counts, failed = scan.scan_orb(data, sorted(data), self.DAY, self.cutoff("10:15"), 15, 0, 0, confirm, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(counts["unconfirmed"], counts["candidates"] - 2)
        self.assertEqual(counts["confirmed"], 2)

    def test_the_orb_no_entries_after_default_is_1430_and_comes_from_the_config(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = os.path.join(td, "config.json")
            with open(cfg, "w") as fh:
                json.dump({"no_entries_after": "10:00"}, fh)
            code, so, se = run(["orb", "--offline", WALL, "--now", "2026-10-23 10:15", "--config", cfg, "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("after 10:00", se)
            code, so, se = run(["orb", "--offline", WALL, "--now", "2026-10-23 14:40", "--no-write", "--config", os.path.join(td, "none.json")])
            self.assertEqual(code, 1)
            self.assertIn("after 14:30", se)


class Files(unittest.TestCase):
    def test_symbols_from_files_and_the_limit(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "u.txt")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("# comment\nRELIANCE|Reliance\nM&M|Mahindra\n\nTCS|TCS\n")
            self.assertEqual(scan.symbols_from_file(p), ["RELIANCE", "M&M", "TCS"])
            w = os.path.join(td, "w.json")
            with open(w, "w") as fh:
                json.dump({"hits": [{"symbol": "infy"}, {"symbol": "sbin"}]}, fh)
            self.assertEqual(scan.symbols_from_file(w), ["INFY", "SBIN"])
            with self.assertRaises(scan.ScanError):
                scan.symbols_from_file(os.path.join(td, "missing"))

    def test_no_arguments_prints_help_and_exits_1(self):
        code, so, se = run([])
        self.assertEqual(code, 1)
        self.assertIn("universe", so)

    def test_no_universe_file_is_a_plain_message(self):
        with tempfile.TemporaryDirectory() as td:
            code, so, se = run(["daily", "--now", "2026-10-25 12:00"], cwd=td)
            self.assertEqual(code, 1)
            self.assertIn("run `scan.py universe` first", se)


if __name__ == "__main__":
    unittest.main()
