"""trader-exit tests (NSE). Bars for the ATR stop reuse tests/fixtures/trader-entry/ (ONGC's real Oct 23 2026
five-minute bars, see test_entry.py)."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")
entry = load("trader-entry", "entry")

ONGC = os.path.join(FIX, "trader-entry", "ONGC-2026-10-23-5m.json")
ONGC5D = os.path.join(FIX, "trader-entry", "ONGC-5d-5m-2026-10-23.json")

# trader/entry.json for the ONGC breakout, exactly as trader-entry writes it
ONGC_ENTRY = {"created_by": "trader-entry", "symbol": "ONGC", "setup": "orb", "side": "buy", "date": "2026-10-23",
              "status": "triggered", "time": "10:05", "bar_start": "10:00", "bar_index": 9, "price": 225.74,
              "level": 225.62, "interval": "5m", "opening_range": {"high": 225.62, "low": 224.61, "minutes": 15},
              "source": "yahoo", "checked_at": "2026-10-23T04:35:00Z"}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=225.62, ol=224.61, atr=None, n=None, tick=0.05):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n, tick)


class Math(unittest.TestCase):
    def test_orb_or_mid_1r_on_the_tick(self):
        p = c(225.74, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("225.10"))           # floor to 0.05 of 225.115
        self.assertEqual(p["risk"], Decimal("0.64"))
        self.assertEqual(p["target"], Decimal("226.40"))         # 225.74 + 0.64 = 226.38, half up to the tick
        self.assertEqual(p["reward_risk"], Decimal("1.03"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")
        self.assertIn("rounded down to the tick", p["stop_detail"])
        self.assertEqual(p["tick"], Decimal("0.05"))

    def test_the_default_2r(self):
        p = c(225.74, "or-mid")
        self.assertEqual(p["target"], Decimal("227.00"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_give_the_same_prices(self):
        p = c(225.74000549316406, "or-mid", "2R", 225.6199951171875, 224.61000061035156)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (Decimal("225.74"), Decimal("225.10"), Decimal("227.00")))

    def test_every_stop_and_target_is_a_multiple_of_the_tick(self):
        for stop in ("or-mid", "or-low", "price:225.33", "pct:1", "pct:0.5"):
            for target in ("1R", "1.5R", "2R", "price:230.07"):
                p = c(225.74, stop, target)
                self.assertEqual(p["stop"] % Decimal("0.05"), 0, (stop, target))
                self.assertEqual(p["target"] % Decimal("0.05"), 0, (stop, target))
                self.assertLess(p["stop"], p["entry"])
                self.assertGreater(p["target"], p["entry"])

    def test_other_stops(self):
        p = c(225.74, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("224.60"), Decimal("1.14"), Decimal("228.00")))
        p = c(225.74, "price:225.33")
        self.assertEqual(p["stop"], Decimal("225.30"))             # rounds away from the entry
        p = c(225.74, "pct:1")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("223.45"), Decimal("2.29"), Decimal("230.30")))
        p = c(225.74, "atr:1.5", atr=0.4642, n=9)
        self.assertEqual((p["stop"], p["target"]), (Decimal("225.00"), Decimal("227.20")))
        self.assertEqual(p["stop_label"], "1.5 x ATR(9) under the entry")
        self.assertIn("fewer than 14", p["stop_detail"])

    def test_a_paisa_tick_changes_the_rounding(self):
        p = c(225.74, "or-mid", "1R", tick=0.01)
        self.assertEqual((p["stop"], p["target"]), (Decimal("225.11"), Decimal("226.37")))

    def test_price_targets_and_a_big_stock(self):
        p = c(1450.50, "pct:1", "1.5R", None, None)
        self.assertEqual((p["stop"], p["target"]), (Decimal("1435.95"), Decimal("1472.35")))
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        p = c(225.74, "or-mid", "price:228.03")
        self.assertEqual(p["target"], Decimal("228.05"))
        self.assertEqual(p["target_label"], "your price (%sR)" % exitp.ratio(p["reward_risk"]))

    def test_refusals_say_why(self):
        for args, words in (((225.74, "price:225.80"), "at or above the entry"),
                            ((225.74, "price:225.71"), "less than one price tick"),
                            ((225.74, "or-mid", "price:225.50"), "at or below the entry"),
                            ((100000, "price:99999.99"), "less than 0.05%"),
                            ((225.74, "or-mid", "1R", 224.0, 225.0), "must be above its low"),
                            ((225.74, "or-mid", "1R", None, None), "needs the opening range"),
                            ((0, "pct:1"), "above Rs 0")):
            with self.assertRaises(exitp.PlanError) as cm:
                c(*args)
            self.assertIn(words, str(cm.exception))
        with self.assertRaises(exitp.PlanError):
            c(225.74, "or-mid", tick=0)
        for bad in ("pct:0", "pct:100", "weird", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "price:", "fast"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)

    def test_rupee_money_uses_indian_grouping(self):
        self.assertEqual(exitp.money(1234567.5), "Rs 12,34,567.50")
        self.assertEqual(exitp.money(-45.5), "-Rs 45.50")
        self.assertEqual(exitp.money(999), "Rs 999.00")
        self.assertEqual(exitp.money(100000), "Rs 1,00,000.00")
        self.assertEqual(exitp.D("Rs 1,450.50"), Decimal("1450.50"))
        self.assertEqual(exitp.D("₹1,450.50"), Decimal("1450.50"))
        self.assertEqual(exitp.parse_stop("price:Rs 225.33"), ("price", Decimal("225.33")))


class Times(unittest.TestCase):
    def test_clock_forms(self):
        import datetime
        self.assertEqual(exitp.parse_clock("10:05", "x"), datetime.time(10, 5))
        self.assertEqual(exitp.parse_clock("10:05 AM", "x"), datetime.time(10, 5))
        self.assertEqual(exitp.parse_clock("3:05 PM", "x"), datetime.time(15, 5))
        for bad in ("25:00", "noon", "13:00 PM"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_clock(bad, "x")
        self.assertEqual(exitp.clock24(datetime.time(9, 5)), "09:05")

    def test_the_session_and_the_calendar(self):
        import datetime
        self.assertEqual(exitp.session(datetime.date(2026, 10, 23)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(exitp.session(datetime.date(2026, 10, 24)))           # Saturday
        self.assertIsNone(exitp.session(datetime.date(2026, 10, 20)))           # Dussehra
        self.assertEqual(exitp.parse_now("2026-10-23 10:15"), datetime.datetime(2026, 10, 23, 4, 45))
        self.assertEqual(exitp.parse_now("2026-10-23 10:15 IST"), datetime.datetime(2026, 10, 23, 4, 45))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, name, doc):
        with open(os.path.join("trader", name), "w") as fh:
            json.dump(doc, fh)

    def test_entry_to_plan_end_to_end(self):
        code, _, _ = run(entry, ["check", "ONGC", "--offline", ONGC, "--now", "2026-10-23 10:05", "--write"])
        self.assertEqual(code, 0)
        code, text, err = run(exitp, ["plan", "--write", "--now", "2026-10-23 10:06"])
        self.assertEqual(code, 0, err)
        self.assertIn("Stop          Rs 225.10", text)
        self.assertIn("Target        Rs 227.00", text)
        self.assertIn("Flat by       15:15 IST", text)
        self.assertIn("Rs 0.05 price tick", text)
        self.assertIn("Nothing was placed", text)
        with open(os.path.join("trader", "plan.json")) as fh:
            plan = json.load(fh)
        want = {"created_by": "trader-exit", "symbol": "ONGC", "side": "buy", "date": "2026-10-23", "setup": "orb",
                "entry": 225.74, "entry_time": "10:05", "entry_bar": 9, "interval": "5m",
                "opening_range": {"high": 225.62, "low": 224.61, "minutes": 15}, "stop": 225.1, "stop_rule": "or_mid",
                "target": 227.0, "target_r": 2.0, "risk_per_share": 0.64, "reward_risk": 1.97, "flat_by": "15:15",
                "tick_size": 0.05}
        for k, v in want.items():
            self.assertEqual(plan[k], v, k)

    def test_manual_flags_and_json(self):
        code, text, _ = run(exitp, ["plan", "--symbol", "RELIANCE", "--entry", "1450.50", "--setup", "level", "--stop", "pct:1",
                                    "--target", "1.5R", "--json", "--now", "2026-10-23 10:06"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual((doc["stop"], doc["target"], doc["setup"], doc["warnings"]), (1435.95, 1472.35, "level", []))
        code, text, err = run(exitp, ["plan", "--symbol", "RELIANCE", "--entry", "1450.50", "--setup", "level", "--now", "2026-10-23 10:06"])
        self.assertEqual(code, 1)
        self.assertIn("has no opening-range stop to default to", err)

    def test_tick_flag_and_config(self):
        args = ["plan", "--symbol", "ONGC", "--entry", "225.74", "--or-high", "225.62", "--or-low", "224.61", "--target", "1R",
                "--json", "--now", "2026-10-23 10:06"]
        code, text, _ = run(exitp, args + ["--tick", "0.01"])
        self.assertEqual((json.loads(text)["stop"], json.loads(text)["tick_size"]), (225.11, 0.01))
        self.write("config.json", {"tick_size": 0.1})
        code, text, _ = run(exitp, args)
        self.assertEqual((json.loads(text)["stop"], json.loads(text)["tick_size"]), (225.1, 0.1))

    def test_atr_stop_reads_bars(self):
        code, text, err = run(exitp, ["plan", "--symbol", "ONGC", "--entry", "225.74", "--or-high", "225.62", "--or-low", "224.61",
                                      "--stop", "atr:1.5", "--offline", ONGC5D, "--date", "2026-10-23", "--entry-bar", "9",
                                      "--time", "10:05", "--now", "2026-10-23 10:06"])
        self.assertEqual(code, 0, err)
        self.assertIn("ATR(10)", text)
        self.assertIn("Stop          Rs 225.00", text)
        code, text, err = run(exitp, ["plan", "--symbol", "ONGC", "--entry", "225.74", "--stop", "atr:1", "--setup", "level",
                                      "--offline", ONGC5D, "--date", "2026-10-23", "--entry-bar", "1"])
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)

    def test_refusals_from_the_cli(self):
        self.write("entry.json", dict(ONGC_ENTRY, status="waiting"))
        code, _, err = run(exitp, ["plan"])
        self.assertEqual(code, 1)
        self.assertIn("not triggered", err)
        self.write("entry.json", dict(ONGC_ENTRY, side="sell"))
        code, _, err = run(exitp, ["plan"])
        self.assertIn("longs only", err)
        code, _, err = run(exitp, ["plan", "--symbol", "ONGC", "--entry", "225.74", "--side", "short"])
        self.assertIn("longs only", err)
        code, _, err = run(exitp, ["plan", "--symbol", "ONGC"])
        self.assertIn("needs --entry", err)
        code, _, err = run(exitp, ["plan", "--from", "nope.json"])
        self.assertIn("no entry", err)
        code, _, err = run(exitp, ["plan", "--symbol", "ONGC", "--entry", "225.74", "--or-high", "225.62"])
        self.assertIn("both --or-high and --or-low", err)

    def test_flat_by_config_and_late_entries(self):
        base = ["plan", "--symbol", "ONGC", "--entry", "225.74", "--or-high", "225.62", "--or-low", "224.61", "--json",
                "--date", "2026-10-23", "--now", "2026-10-23 10:06"]
        code, text, _ = run(exitp, base)
        self.assertEqual(json.loads(text)["flat_by"], "15:15")
        code, text, _ = run(exitp, base + ["--flat-by", "14:45"])
        self.assertEqual(json.loads(text)["flat_by"], "14:45")
        code, _, err = run(exitp, base + ["--flat-by", "15:30"])
        self.assertEqual(code, 1)
        self.assertIn("at or after the 15:30 close", err)
        code, _, err = run(exitp, base + ["--time", "15:20"])
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)
        self.write("config.json", {"flat_by": "14:00", "min_reward_risk": 3})
        code, text, _ = run(exitp, base + ["--time", "10:05"])
        doc = json.loads(text)
        self.assertEqual(doc["flat_by"], "14:00")
        self.assertTrue(any("min_reward_risk" in w for w in doc["warnings"]))
        code, text, _ = run(exitp, base[:-2] + ["--now", "2026-10-26 10:06", "--time", "10:05"])
        self.assertTrue(any("not today" in w for w in json.loads(text)["warnings"]))

    def test_show(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan at", text)
        run(exitp, ["plan", "--symbol", "ONGC", "--entry", "225.74", "--or-high", "225.62", "--or-low", "224.61",
                    "--write", "--date", "2026-10-23", "--now", "2026-10-23 10:06"])
        code, text, _ = run(exitp, ["show", "--now", "2026-10-23 12:00"])
        self.assertEqual(code, 0)
        self.assertIn("Exit plan: ONGC long", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-26 12:00"])
        self.assertIn("not today", text)
        code, text, _ = run(exitp, ["show", "--json"])
        self.assertEqual(json.loads(text)["stop"], 225.1)


if __name__ == "__main__":
    unittest.main()
