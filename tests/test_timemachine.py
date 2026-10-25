import copy
import gzip
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

tm = load("trader-timemachine", "timemachine")

DIR = os.path.join(FIX, "trader-timemachine")
ONGC = os.path.join(DIR, "ONGC-60d5m.json.gz")   # Yahoo v8 chart, range=60d interval=5m, ONGC.NS, fetched Sun 2026-10-25 (real)
with gzip.open(ONGC, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
NOW = "2026-10-25 12:00"
NOW_UTC = tm.parse_now(NOW)

# the ONGC plan, as trader-exit writes it
PLAN = {"created_by": "trader-exit", "symbol": "ONGC", "side": "buy", "date": "2026-10-23", "setup": "orb",
        "entry": 225.74, "entry_time": "10:05", "entry_bar": 9, "interval": "5m",
        "opening_range": {"high": 225.62, "low": 224.61, "minutes": 15},
        "stop": 225.10, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 227.00, "target_r": 1.97, "target_label": "2R (two times the risk)",
        "risk_per_share": 0.64, "reward_risk": 1.97, "flat_by": "15:15", "tick_size": 0.05}


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn):
    """The fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def ist(ts):
    return tm.to_ist(tm.utc_of(ts))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheOngcPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_odds(self):
        mc = self.doc["mc"]
        self.assertEqual((mc["replays"], mc["seed"], mc["window"], mc["bars_left"]), (10000, 19, 2, 62))
        self.assertEqual((mc["p_target"], mc["p_stop"], mc["p_neither"], mc["p_green"]), (0.2133, 0.7603, 0.0264, 0.2324))
        self.assertEqual((mc["median_close"], mc["p5_close"], mc["p95_close"]), (225.29, 222.5, 228.2))
        self.assertEqual((mc["avg_R"], mc["r_at_target"], mc["until"]), (-0.328, 1.9687, "15:15"))      # until = the plan's flat_by
        self.assertEqual((mc["pool_sessions"], mc["pool_from"], mc["pool_to"]), (51, "2026-07-29", "2026-10-22"))
        self.assertAlmostEqual(mc["p_target"] + mc["p_stop"] + mc["p_neither"], 1.0, places=3)

    def test_the_history_replay_on_real_sessions(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"], h["target"], h["stop"], h["close"], h["green"], h["avg_R"]),
                         (51, 19, 3, 11, 5, 6, -0.228))
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.97, "tick": 0.05})
        self.assertEqual(h["target"] + h["stop"] + h["close"], h["triggered"])
        for t in h["trades"]:
            self.assertEqual(round(t["stop"] / 0.05) * 0.05, round(t["stop"] / 0.05) * 0.05)
            self.assertAlmostEqual(t["stop"] / 0.05, round(t["stop"] / 0.05), places=4)       # every history stop is on the tick
            self.assertAlmostEqual(t["target"] / 0.05, round(t["target"] / 0.05), places=4)
            self.assertLess(t["stop"], t["entry"])
            self.assertGreater(t["target"], t["entry"])
            self.assertRegex(t["time"], r"^\d\d:\d\d$")

    def test_the_verdict_leans_toward_the_stop(self):
        self.assertTrue(self.doc["verdict"].startswith("Leans toward the stop: at 1.97R the target has to come first in 34 %"))
        self.assertIn("target first 21.3 % vs stop first 76.0 %", self.doc["verdict"])
        self.assertEqual(self.doc["verdict_notes"], [])

    def test_bands_start_at_the_entry_and_end_at_the_flat_by(self):
        mc = self.doc["mc"]
        self.assertEqual(mc["bands"][0], [225.74] * 5)
        self.assertEqual(len(mc["bands"]), 63)
        self.assertEqual((mc["band_times"][0], mc["band_times"][-1]), ("10:05", "15:15"))
        self.assertEqual(sum(mc["hist"]["bins"]), 9843)

    def test_the_plan_is_carried_through_and_the_data_described(self):
        self.assertEqual(self.doc["plan"]["symbol"], "ONGC")
        self.assertEqual((self.doc["plan"]["entry_time"], self.doc["plan"]["entry_bar"]), ("10:05", 9))
        self.assertEqual(self.doc["data"]["last_bar"], "2026-10-23 15:15 IST")
        self.assertEqual(self.doc["data"]["interval"], "5m")
        self.assertEqual(len(self.doc["data"]["short_sessions_left_out"]), 8)
        self.assertEqual(self.doc["created_by"], "trader-timemachine")
        self.assertEqual(self.doc["warnings"], [])

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        self.assertIn("ONGC buy, Friday 2026-10-23: entry 225.74 at 10:05 (bar 9), stop 225.10, target 227.00 (1.97R, risk 0.64 a share).", text)
        self.assertIn("10,000 replays of the rest of the session, 10:05 to 15:15 (62 five-minute bars), seed 19.", text)
        self.assertIn("  target first   21.3 %", text)
        self.assertIn("  stop first     76.0 %", text)
        self.assertIn("History: the same rule (15-min opening range, stop at the opening-range midpoint, 1.97R target)", text)
        self.assertIn("fired 19 times: 3 target, 11 stop, 5 neither", text)
        self.assertIn("Muhurat sessions or gaps", text)
        self.assertIn("brokerage and taxes", text)
        self.assertNotIn("$", text)
        self.assertNotIn("New York", text)


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers_and_another_seed_differs(self):
        a, b = machine(replays=2000, seed=5), machine(replays=2000, seed=5)
        self.assertEqual(sha(a["mc"]["bands"]), sha(b["mc"]["bands"]))
        self.assertEqual(a["mc"]["p_target"], b["mc"]["p_target"])
        c = machine(replays=2000, seed=6)
        self.assertNotEqual(sha(a["mc"]["bands"]), sha(c["mc"]["bands"]))
        self.assertEqual(machine(seed=7)["mc"]["p_target"], 0.2159)

    def test_window_and_replays_are_checked(self):
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)
        zero = machine(window=0, replays=500)
        self.assertEqual(zero["mc"]["window"], 0)


class Bars(unittest.TestCase):
    def test_the_empty_1520_and_1525_bars_do_not_make_a_session_short(self):
        # Yahoo's NSE history leaves the 15:20 and 15:25 bars empty: 73 bars is a full session here
        res = tm.chart_result(TEXT, "ONGC")
        meta, bars, dropped = tm.parse_bars(res, NOW_UTC)
        self.assertGreater(dropped["no_price"], 100)
        days = tm.split_sessions(bars)
        full, short = tm.full_sessions(days, "2026-10-23")
        self.assertEqual((len(full), len(short)), (51, 8))
        self.assertEqual(len(full[0][1]), tm.USED_BARS)
        self.assertTrue({s["bars"] for s in short} <= {72, 73})         # a missing bar mid-session makes a session short

    def test_the_in_progress_tick_is_dropped_and_now_pins_the_clock(self):
        res = json.loads(TEXT)["chart"]["result"][0]
        last = res["timestamp"][-1]
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-10-23 09:00"))
        self.assertTrue(all(b["date"] < "2026-10-23" for b in bars))
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-10-23 10:07"))
        self.assertEqual(bars[-1]["date"] + " " + bars[-1]["start"], "2026-10-23 10:00")
        self.assertGreaterEqual(dropped["not_complete"], 1)

    def test_a_missing_price_and_bars_off_the_grid_are_dropped(self):
        def fn(r):
            q = r["indicators"]["quote"][0]
            q["close"][100] = None
            r["timestamp"][101] += 7
        meta, bars, dropped = tm.parse_bars(tm.chart_result(edited(fn), "ONGC"), NOW_UTC)
        self.assertEqual(dropped["off_grid"], 1)
        base = tm.parse_bars(tm.chart_result(TEXT, "ONGC"), NOW_UTC)[2]
        self.assertEqual(dropped["no_price"], base["no_price"] + 1)

    def test_a_gap_in_a_session_leaves_that_session_out(self):
        gone = {}

        def fn(r):
            ts = r["timestamp"]
            gone["t"] = ts[1000]
            keep_bars(r, lambda t: t != gone["t"])
        d = machine(text=edited(fn))
        self.assertEqual(d["mc"]["pool_sessions"], 50)
        self.assertEqual(len(d["data"]["short_sessions_left_out"]), 9)

    def test_today_is_not_in_the_pool(self):
        self.assertEqual(machine()["mc"]["pool_to"], "2026-10-22")

    def test_empty_and_broken_data(self):
        for bad, words in (("<html>", "not JSON"), ('{"x": 1}', "unexpected"),
                           ('{"chart": {"result": null, "error": {"description": "No data found"}}}', "No data found")):
            with self.assertRaises(tm.TMError) as cm:
                machine(text=bad)
            self.assertIn(words, str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(lambda r: keep_bars(r, lambda t: False)))
        self.assertIn("no complete 5-minute bars", str(cm.exception))

    def test_fewer_than_20_sessions_is_refused(self):
        text = edited(lambda r: keep_bars(r, lambda t: ist(t).date().isoformat() >= "2026-10-01"))
        with self.assertRaises(tm.TMError) as cm:
            machine(text=text)
        self.assertIn("needs at least 20", str(cm.exception))

    def test_the_data_must_be_the_plans_symbol_and_5_minute_bars(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(p=plan(symbol="TCS"))
        self.assertIn("the data is for ONGC but the plan is for TCS", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text=edited(lambda r: r["meta"].update(dataGranularity="1m")))


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(p=plan(date="2026-10-24"))
        self.assertIn("the entry is not in the data", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:
            machine(p=plan(entry_time="15:00", entry_bar=None, date="2026-10-23"), text=edited(lambda r: keep_bars(r, lambda t: ist(t).strftime("%Y-%m-%d %H:%M") < "2026-10-23 10:00")))
        self.assertIn("the data stops at the", str(cm.exception))

    def test_plan_checks(self):
        for kw, words in (({"stop": 226.0}, "stop < entry < target"),
                          ({"target": 225.0}, "stop < entry < target"),
                          ({"entry_time": "10:07", "entry_bar": None}, "not on the 5-minute grid"),
                          ({"entry_time": "15:20", "entry_bar": None}, "entry must close between 09:20 and 15:15"),
                          ({"entry_time": "09:15", "entry_bar": None}, "entry must close between 09:20 and 15:15"),
                          ({"entry_bar": 3}, "disagrees with itself"),
                          ({"side": "hold"}, "buy or sell"),
                          ({"interval": "1m"}, "5-minute bars only"),
                          ({"date": "soon"}, "needs a date"),
                          ({"symbol": ""}, "NSE symbol"),
                          ({"entry": "x"}, "needs a number"),
                          ({"opening_range": {"minutes": 7}}, "multiple of 5")):
            with self.assertRaises(tm.TMError) as cm:
                plan(**kw)
            self.assertIn(words, str(cm.exception), kw)
        self.assertEqual(plan(symbol="m&m")["symbol"], "M&M")
        self.assertEqual(plan(symbol="bajaj-auto")["symbol"], "BAJAJ-AUTO")

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 9)
        p = plan(entry_time=None)
        self.assertEqual((p["entry_bar"], p["entry_time"]), (9, "10:05"))
        with self.assertRaises(tm.TMError):
            plan(entry_time=None, entry_bar=None)

    def test_until_flat_by_and_the_data_end(self):
        d = machine(until="14:45")
        self.assertEqual((d["mc"]["until"], d["mc"]["bars_left"]), ("14:45", 56))
        d = machine(p=plan(flat_by=None))
        self.assertEqual((d["mc"]["until"], d["mc"]["bars_left"]), ("15:20", 63))           # no flat_by: the last bar Yahoo has
        d = machine(p=plan(flat_by="15:17"))
        self.assertEqual(d["mc"]["until"], "15:20")                                      # a flat_by off the grid is ignored
        with self.assertRaises(tm.TMError) as cm:
            machine(until="15:30")
        self.assertIn("no later than 15:20", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(until="10:05")
        with self.assertRaises(tm.TMError):
            machine(until="14:43")

    def test_an_entry_outside_its_bar_is_flagged(self):
        d = machine(p=plan(entry=226.5, stop=225.5, target=228.5))
        self.assertTrue(any("outside that bar's range" in w for w in d["warnings"]))

    def test_the_stop_rule_is_worked_out_on_the_tick_when_not_given(self):
        d = machine(p=plan(stop_rule="auto"))
        self.assertEqual(d["history"]["rule"]["stop_rule"], "or_mid")                    # 225.115 floors to the plan's 225.10
        d = machine(p=plan(stop_rule="auto", stop=224.60, target=228.0, risk_per_share=1.14, target_r=1.98))
        self.assertEqual(d["history"]["rule"]["stop_rule"], "or_low")
        d = machine(p=plan(stop_rule="auto", stop=225.00, target=227.0, risk_per_share=0.74, target_r=1.97))
        self.assertEqual(d["history"]["rule"]["stop_rule"], "pct")
        self.assertIn("stop_pct", d["history"]["rule"])

    def test_the_tick_helpers(self):
        self.assertEqual(tm.cents_away(225.115, True), 225.10)
        self.assertEqual(tm.cents_away(225.115, False), 225.15)
        self.assertEqual(tm.cents_away(225.10, True), 225.10)                            # already on the tick
        self.assertEqual(tm.cents_away(225.115, True, 0.01), 225.11)
        self.assertEqual(tm.round_tick(226.38), 226.40)
        self.assertEqual(tm.round_tick(226.375), 226.40)
        self.assertEqual(tm.round_tick(226.37), 226.35)

    def test_a_setup_other_than_orb_skips_the_history(self):
        d = machine(p=plan(setup="level"))
        self.assertEqual(d["history"]["triggered"], 0)
        self.assertIn("orb", d["history"]["note"])
        self.assertIn("History: skipped", "\n".join(tm.report(d)))

    def test_a_sell_plan_mirrors_the_rules(self):
        d = machine(p=plan(side="sell", entry=225.74, stop=226.38, target=224.46, risk_per_share=0.64, target_r=2.0, stop_rule="auto"))
        mc = d["mc"]
        self.assertAlmostEqual(mc["p_target"] + mc["p_stop"] + mc["p_neither"], 1.0, places=3)
        with self.assertRaises(tm.TMError):
            plan(side="sell")                                                           # stop below the entry
        self.assertIn("below", "\n".join(tm.report(d)))

    def test_the_verdict_thresholds(self):
        def mc(pt, ps, r=1.0, pn=0.0, avg=0.0):
            return {"p_target": pt, "p_stop": ps, "p_neither": pn, "r_at_target": r, "avg_R": avg, "until": "15:15"}
        self.assertTrue(tm.verdict(mc(0.50, 0.50)).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict(mc(0.40, 0.60)).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict(mc(0.60, 0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(mc(0.30, 0.70)).startswith("Strongly toward the stop"))
        self.assertTrue(tm.verdict(mc(0.70, 0.30)).startswith("Strongly toward the target"))
        self.assertTrue(tm.verdict(mc(0.33, 0.67, r=2.0)).startswith("Close to break-even"))
        self.assertIn("decides every one", tm.verdict(mc(0.0, 0.0)))
        self.assertTrue(tm.verdict_notes(mc(0.2, 0.3, pn=0.5)))
        self.assertTrue(tm.verdict_notes(mc(0.2, 0.7, avg=0.1)))


class Network(unittest.TestCase):
    """Yahoo is never called: _get and _sleep are swapped for fakes."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = lambda s: self.sleeps.append(s)

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answers(self, *codes):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return TEXT
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
        tm._get = fake

    def test_url_has_the_ns_suffix_and_a_plain_user_agent(self):
        self.assertEqual(tm.chart_url("ONGC"), "https://query2.finance.yahoo.com/v8/finance/chart/ONGC.NS?range=60d&interval=5m")
        self.assertIn("M%26M.NS", tm.chart_url("m&m"))
        self.assertIn("%5ENSEI", tm.chart_url("NIFTY"))
        self.assertNotIn("http", tm.UA)

    def test_429_waits_retries_then_falls_back_to_query1(self):
        self.answers(429, 429, 200)
        self.assertEqual(tm.fetch_chart("ONGC"), TEXT)
        self.assertEqual([u.split("/")[2] for u in self.calls], ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])
        self.assertEqual(self.sleeps[0], tm.BACKOFF)

    def test_5xx_falls_back_and_rate_limited_everywhere_is_one_plain_line(self):
        self.answers(503, 200)
        tm.fetch_chart("ONGC")
        self.assertIn("query1", self.calls[1])
        self.calls[:] = []
        self.answers(429, 429, 429, 429)
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ONGC")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answers(404)
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ")
        self.assertIn("check the ticker", str(cm.exception).replace("symbol", "ticker"))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        with open(os.path.join("trader", "plan.json"), "w") as fh:
            json.dump(PLAN, fh)
        code, text, err = run(["run", "--offline", ONGC, "--now", NOW, "--write"])
        self.assertEqual(code, 0, err)
        self.assertIn("ONGC buy, Friday 2026-10-23", text)
        self.assertIn("Wrote trader/timemachine.json.", text)
        self.assertIn("/trader-budget", text)
        with open(os.path.join("trader", "timemachine.json")) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["mc"]["p_target"], 0.2133)
        self.assertEqual(doc["created_at"], "2026-10-25T06:30:00Z")

    def test_json_output_and_the_plan_as_flags_match_the_file(self):
        flags = ["run", "--offline", ONGC, "--now", NOW, "--symbol", "ONGC", "--date", "2026-10-23", "--entry", "225.74",
                 "--stop", "225.10", "--target", "227.00", "--entry-time", "10:05", "--until", "15:15", "--json"]
        code, text, err = run(flags)
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["mc"]["p_target"], 0.2133)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_mid")

    def test_offline_folder_and_default_paths(self):
        os.mkdir("charts")
        shutil.copy(ONGC, os.path.join("charts", "ONGC-60d5m.json.gz"))
        code, text, err = run(["run", "--offline", "charts", "--now", NOW, "--symbol", "ONGC", "--date", "2026-10-23", "--entry", "225.74",
                               "--stop", "225.10", "--target", "227.00", "--entry-time", "10:05"])
        self.assertEqual(code, 0, err)
        code, text, err = run(["run", "--offline", "empty-folder", "--now", NOW, "--symbol", "ONGC", "--date", "2026-10-23", "--entry", "225.74",
                               "--stop", "225.10", "--target", "227.00", "--entry-time", "10:05"])
        self.assertEqual(code, 1)
        self.assertIn("no offline chart file", err)

    def test_errors_are_one_plain_line(self):
        code, _, err = run(["run", "--offline", ONGC])
        self.assertEqual(code, 1)
        self.assertIn("no plan at trader/plan.json", err)
        code, _, err = run(["run", "--symbol", "ONGC"])
        self.assertIn("also give --entry", err)
        for e in (err,):
            self.assertNotIn("Traceback", e)
        code, _, err = run(["chart", "--from", "nope.json"])
        self.assertIn("run timemachine.py run --write first", err)

    def test_chart(self):
        with open(os.path.join("trader", "plan.json"), "w") as fh:
            json.dump(PLAN, fh)
        code, text, err = run(["chart", "--offline", ONGC, "--now", NOW])
        self.assertEqual(code, 0, err)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("ONGC 2026-10-23, 2,000 replay paths from the 10:05 entry (seed 19)"))
        self.assertTrue(any(l.endswith("target") for l in lines))
        self.assertTrue(any(l.endswith("stop") for l in lines))
        self.assertTrue(any(l.endswith("entry") for l in lines))
        axis = [l for l in lines if l.strip().startswith("10:05")]
        self.assertTrue(axis and "15:15" in axis[0])
        self.assertIn("Leans toward the stop", text)
        run(["run", "--offline", ONGC, "--now", NOW, "--write"])
        code, text2, err = run(["chart", "--from"])
        self.assertEqual(code, 0, err)
        self.assertEqual(text.splitlines()[:21], text2.splitlines()[:21])


if __name__ == "__main__":
    unittest.main()
