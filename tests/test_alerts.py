"""trader-alerts tests (NSE): the IST clock, Yahoo quotes for .NS symbols, and rules that ring once."""

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

from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

QDIR = os.path.join(FIX, "trader-alerts")
PLAN = os.path.join(QDIR, "plan-ongc.json")
DT = datetime.datetime
UA = "Mozilla/5.0"

# NSE trading holidays 2026 from the NSE holiday master (read 2026-10-25)
NSE_2026 = ["2026-01-15", "2026-01-26", "2026-02-15", "2026-03-03", "2026-03-21", "2026-03-26", "2026-03-31",
            "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-08-15", "2026-09-14",
            "2026-10-02", "2026-10-20", "2026-11-08", "2026-11-10", "2026-11-24", "2026-12-25"]


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN) as fh:
        return alerts.rules_from_plan(json.load(fh))


def quote_dir(price):
    """A copy of the quote fixtures with ONGC at a price of our choosing (the real closing quote is 227.50)."""
    d = tempfile.mkdtemp()
    for name in os.listdir(QDIR):
        if name.startswith("yahoo-"):
            shutil.copy(os.path.join(QDIR, name), d)
    path = os.path.join(d, "yahoo-ONGC.json")
    with open(path) as fh:
        doc = json.load(fh)
    doc["chart"]["result"][0]["meta"]["regularMarketPrice"] = price
    with open(path, "w") as fh:
        json.dump(doc, fh)
    return d


class Calendar(unittest.TestCase):
    def test_holidays_match_the_nse_master(self):
        self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026)
        self.assertEqual(alerts.CALENDAR_YEARS, (2026,))
        self.assertFalse(hasattr(alerts, "NYSE_HOLIDAYS"))

    def test_ist_has_no_daylight_saving(self):
        self.assertEqual(alerts.to_ist(DT(2026, 7, 1, 0, 0)), DT(2026, 7, 1, 5, 30))
        self.assertEqual(alerts.to_ist(DT(2026, 12, 1, 0, 0)), DT(2026, 12, 1, 5, 30))

    def test_parse_now(self):
        self.assertEqual(ist("2026-10-23T09:15"), DT(2026, 10, 23, 3, 45))
        self.assertEqual(ist("2026-10-23 10:15"), DT(2026, 10, 23, 4, 45))
        self.assertEqual(ist("2026-12-01T09:30"), DT(2026, 12, 1, 4, 0))
        self.assertEqual(ist("2026-10-23T09:58:26Z"), DT(2026, 10, 23, 9, 58, 26))
        with self.assertRaises(alerts.AlertError):
            ist("tomorrow")

    def test_market_status(self):
        st = alerts.market_status(ist("2026-10-23 10:00"))
        self.assertTrue(st["open"])
        self.assertEqual(st["now_ist"], "2026-10-23 10:00")
        self.assertEqual((st["opens"], st["closes"]), ("09:15", "15:30"))
        before = alerts.market_status(ist("2026-10-23 09:14"))
        self.assertFalse(before["open"])
        self.assertEqual((before["reason"], before["next_open"]), ("before the open", "2026-10-23 09:15"))
        self.assertTrue(alerts.market_status(ist("2026-10-23 09:15"))["open"])
        self.assertTrue(alerts.market_status(ist("2026-10-23 15:29"))["open"])
        after = alerts.market_status(ist("2026-10-23 15:30"))
        self.assertFalse(after["open"])
        self.assertEqual(after["next_open"], "2026-10-26 09:15")           # Friday evening: Monday
        dussehra = alerts.market_status(ist("2026-10-20 10:00"))
        self.assertFalse(dussehra["open"])
        self.assertIn("Dussehra", dussehra["reason"])
        self.assertEqual(dussehra["next_open"], "2026-10-21 09:15")
        xmas = alerts.market_status(ist("2026-12-25 10:00"))
        self.assertIn("Christmas", xmas["reason"])
        self.assertEqual(xmas["next_open"], "2026-12-28 09:15")
        self.assertEqual(alerts.market_status(ist("2026-10-24 10:00"))["reason"], "weekend")
        self.assertTrue(alerts.market_status(ist("2026-10-23 10:00"))["calendar_known"])
        self.assertFalse(alerts.market_status(ist("2027-03-01 10:00"))["calendar_known"])

    def test_a_holiday_file_extends_the_calendar(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "holidays.json")
            with open(path, "w") as fh:
                json.dump({"holidays": {"2027-01-26": "Republic Day"}}, fh)
            saved = alerts.HOLIDAY_FILE
            alerts.HOLIDAY_FILE = path
            try:
                st = alerts.market_status(ist("2027-01-26 10:00"))
                self.assertIn("Republic Day", st["reason"])
                self.assertTrue(st["calendar_known"])
            finally:
                alerts.HOLIDAY_FILE = saved
        finally:
            shutil.rmtree(d)


class Symbols(unittest.TestCase):
    def test_yahoo_symbols(self):
        self.assertEqual(alerts.yahoo_symbol("ongc"), "ONGC.NS")
        self.assertEqual(alerts.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(alerts.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(alerts.yahoo_symbol("NIFTY"), "^NSEI")
        self.assertEqual(alerts.yahoo_symbol("ITC.BO"), "ITC.BO")
        self.assertEqual(alerts.nse_symbol("ONGC.NS"), "ONGC")
        self.assertEqual(alerts.nse_symbol("^NSEBANK"), "BANKNIFTY")

    def test_indices_are_points_not_rupees(self):
        self.assertEqual((alerts.unit("ONGC"), alerts.unit("NIFTY")), ("Rs ", ""))


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        with open(os.path.join(QDIR, "yahoo-ONGC.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "ONGC")
        self.assertEqual((q["price"], q["prev_close"], round(q["open"], 2)), (227.5, 225.0, 225.7))
        self.assertEqual((q["time"], q["currency"], q["source"]), ("2026-10-23T09:58:26Z", "INR", "yahoo"))
        self.assertEqual((q["high"], q["low"]), (227.5, 223.69))
        with open(os.path.join(QDIR, "yahoo-NIFTY.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "NIFTY")
        self.assertEqual((q["price"], q["prev_close"]), (22600.0, 22687.7))

    def test_stooq_is_gone(self):
        self.assertFalse(hasattr(alerts, "parse_stooq"))
        self.assertFalse(hasattr(alerts, "stooq_url"))

    def test_urls_and_user_agent(self):
        self.assertEqual(alerts.UA, UA)
        self.assertEqual(alerts.yahoo_url("ongc"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/ONGC.NS?range=1d&interval=1d")
        self.assertIn("/chart/M%26M.NS?", alerts.yahoo_url("M&M"))
        self.assertIn("/chart/%5ENSEI?", alerts.yahoo_url("NIFTY"))
        self.assertIn("//query1.finance.yahoo.com/", alerts.yahoo_url("ONGC", alerts.YAHOO_HOSTS[1]))

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["ONGC", "ITC", "ZZZZ", "NOPE"], "yahoo", QDIR)
        self.assertEqual(sorted(q), ["ITC", "ONGC"])
        self.assertEqual(q["ITC"]["price"], 257.9)
        self.assertEqual(len(errors), 2)
        self.assertIn("No data found", errors[0])
        self.assertIn("no offline quote file", errors[1])

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-ONGC.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-ONGC.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["ONGC"], "yahoo", d)
            self.assertEqual((q["ONGC"]["price"], errors), (227.5, []))
        finally:
            shutil.rmtree(d)


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes, body=None):
        self.codes, self.calls, self.sleeps = list(codes), [], []
        with open(os.path.join(QDIR, "yahoo-ONGC.json")) as fh:
            self.body = body or fh.read()

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.get_header("User-agent"), req.full_url))
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        return io.BytesIO(self.body.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _, _ in f.calls]

    def test_query2_first_asks_for_the_ns_symbol(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["ONGC"])
        self.assertEqual((q["ONGC"]["price"], errors), (227.5, []))
        self.assertEqual([(h, ua) for h, ua, _ in f.calls], [("query2.finance.yahoo.com", UA)])
        self.assertIn("/chart/ONGC.NS?", f.calls[0][2])

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["ONGC"])[0]["ONGC"]["price"], 227.5)
        self.assertEqual(self.hosts(f), ["query2", "query2"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1_on_429_and_5xx(self):
        f = self.net([429, 429, 200])
        self.assertIn("ONGC", alerts.get_quotes(["ONGC"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query2", "query1"])
        f = self.net([503, 200])
        self.assertIn("ONGC", alerts.get_quotes(["ONGC"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query1"])
        f = self.net([500, 502])
        q, errors = alerts.get_quotes(["ONGC"])
        self.assertEqual(q, {})
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart/ONGC.NS answered HTTP 502", errors[0])

    def test_a_404_is_yahoos_answer_and_a_403_is_not_retried(self):
        f = self.net([404])
        q, errors = alerts.get_quotes(["NOPE"])
        self.assertIn("No data found", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])
        f = self.net([403])
        q, errors = alerts.get_quotes(["ONGC"])
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])

    def test_a_pause_between_symbols(self):
        f = self.net([200, 200])
        alerts.get_quotes(["ONGC", "ITC"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["ONGC", "ITC", "SBIN", "TCS"], "yahoo", QDIR)
        self.open = alerts.market_status(ist("2026-10-23 10:00"))
        self.now = ist("2026-10-23 10:00")

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def test_a_target_rings_once(self):
        rules = [{"id": "ongc-227", "symbol": "ONGC", "type": "above", "price": 227}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertIn("ONGC is Rs 227.50: at or above your alert level Rs 227", rings[0]["message"])
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])

    def test_stop_and_rearm(self):
        rules = [{"id": "sbin-stop", "symbol": "SBIN", "type": "stop", "price": 960, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertIn("your stop Rs 960", rings[0]["message"])
        self.q["SBIN"]["price"] = 962.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("sbin-stop", st["rung"])
        self.q["SBIN"]["price"] = 955.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "sbin-3", "symbol": "SBIN", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        small = [{"id": "sbin-1", "symbol": "SBIN", "type": "pct_move", "pct": 1, "direction": "down"}]
        rings, st = alerts.evaluate(small, self.q, self.open, {}, self.now)
        self.assertIn("SBIN moved -1.18% to Rs 958.60 (from Rs 970.00)", rings[0]["message"])
        self.assertEqual(alerts.evaluate(small, self.q, self.open, st, self.now)[0], [])
        up = [{"id": "sbin-up", "symbol": "SBIN", "type": "pct_move", "pct": 1, "direction": "up"}]
        self.assertEqual(alerts.evaluate(up, self.q, self.open, {}, self.now)[0], [])
        ongc = [{"id": "ongc-1", "symbol": "ONGC", "type": "pct_move", "pct": 1}]
        rings, _ = self.at("2026-10-23 15:29", ongc)
        self.assertIn("ONGC moved +1.11% to Rs 227.50 (from Rs 225.00)", rings[0]["message"])

    def test_an_index_level_has_no_rupee_sign(self):
        q, _ = alerts.get_quotes(["NIFTY"], "yahoo", QDIR)
        rules = [{"id": "nifty-below", "symbol": "NIFTY", "type": "below", "price": 22650}]
        rings, _ = alerts.evaluate(rules, q, self.open, {}, self.now)
        self.assertIn("NIFTY is 22600.00: at or below your alert level 22650", rings[0]["message"])
        self.assertNotIn("Rs", rings[0]["message"])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        closed = alerts.market_status(ist("2026-10-23 08:00"))
        self.assertEqual(alerts.evaluate(rules, {}, closed, {}, ist("2026-10-23 08:00"))[0], [])
        rings, st = alerts.evaluate(rules, {}, self.open, {}, self.now)
        self.assertEqual(rings[0]["message"], "Opening bell: the NSE is open (2026-10-23 10:00 IST).")
        self.assertEqual(alerts.evaluate(rules, {}, self.open, st, ist("2026-10-23 11:00"))[0], [])
        nxt = alerts.market_status(ist("2026-10-26 09:16"))
        self.assertEqual(len(alerts.evaluate(rules, {}, nxt, st, ist("2026-10-26 09:16"))[0]), 1)
        holiday = alerts.market_status(ist("2026-10-20 10:00"))
        self.assertEqual(alerts.evaluate(rules, {}, holiday, {}, ist("2026-10-20 10:00"))[0], [])

    def test_the_plan_gives_a_stop_a_target_and_a_flat_by_time(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("ongc-plan-stop", "stop"), ("ongc-plan-target", "target"), ("ongc-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (225.10, 227.0))
        self.assertEqual((rules[2]["at"], rules[2]["label"]), ("15:15", "flat-by time"))
        self.assertNotIn("before_close", rules[2])
        self.assertTrue(all(r["date"] == "2026-10-23" for r in rules))
        q = json.loads(json.dumps(self.q))
        q["ONGC"]["price"] = 226.30
        rings, st = self.at("2026-10-23 10:52", rules, quotes=q)
        self.assertEqual(rings, [])
        q["ONGC"]["price"] = 225.05
        rings, st = self.at("2026-10-23 11:30", rules, st, q)
        self.assertEqual([r["id"] for r in rings], ["ongc-plan-stop"])
        self.assertIn("ONGC is Rs 225.05: at or below your stop Rs 225.10 (plan stop: opening-range midpoint)",
                      rings[0]["message"])
        q["ONGC"]["price"] = 227.0
        self.assertEqual([r["id"] for r in self.at("2026-10-23 12:00", rules, st, q)[0]], ["ongc-plan-target"])

    def test_the_flat_by_time_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-10-23 15:14", rules)[0], [])
        rings, st = self.at("2026-10-23 15:15", rules)
        self.assertEqual(len(rings), 1)
        self.assertIn("Flat-by time: it is 15:15 IST (set for 15:15), the market closes at 15:30.", rings[0]["message"])
        self.assertIn("trader-broker flatten --symbol ONGC", rings[0]["message"])
        self.assertIn("square-off", rings[0]["message"])
        self.assertEqual(st["rung"]["ongc-flat-by"], "2026-10-23T09:45:00Z")
        self.assertEqual(self.at("2026-10-23 15:20", rules, st)[0], [])
        rings, _ = self.at("2026-10-23 15:40", rules)
        self.assertIn("the market has closed", rings[0]["message"])
        self.assertEqual(self.at("2026-10-26 15:16", rules, st)[0], [])     # plan rules: their day only

    def test_a_time_alert_skips_weekends_and_holidays(self):
        rule = [{"id": "check", "type": "time", "at": "15:15", "label": "check"}]
        self.assertEqual(self.at("2026-10-24 15:16", rule)[0], [])         # Saturday
        self.assertEqual(self.at("2026-10-20 15:16", rule)[0], [])         # Dussehra
        self.assertEqual(len(self.at("2026-10-26 15:15", rule)[0]), 1)

    def test_the_example_rules_are_valid(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.example.json")) as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        self.assertEqual([r for r in rules if r["id"].startswith("ongc-")], plan_rules())

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "ONGC"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "time", "at": "4pm"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "ONGC", "stop": 225.10})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")
        self.mid = quote_dir(226.30)

    def tearDown(self):
        shutil.rmtree(self.d)
        shutil.rmtree(self.mid)

    def check(self, now, offline=None):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", offline or self.mid, "--now", now])

    def test_the_ongc_trade_from_the_plan(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", os.path.join(self.d, "none")])
        self.assertEqual(code, 0)
        self.assertIn("Added ongc-flat-by", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell"])[0], 0)

        code, text, _ = self.check("2026-10-23 10:15")
        self.assertEqual(code, 0)                                      # the opening bell rings first
        code, text, _ = self.check("2026-10-23 10:16")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit. 4 rule(s) watched today, ONGC Rs 226.30.", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"ONGC": 226.3})
        self.assertEqual(doc["detail"]["ONGC"]["time"], "2026-10-23T09:58:26Z")
        self.assertEqual(doc["detail"]["ONGC"]["fetched"], "2026-10-23T04:46:00Z")

        code, text, _ = self.check("2026-10-23 15:15")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat-by time", text)
        self.assertIn("not an order", text)
        self.assertEqual(self.check("2026-10-23 15:17")[0], 1)
        code, text, _ = self.check("2026-10-26 10:00")
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-26 10:05")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        # a new plan replaces the plan rules and re-arms them
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        with open(self.rules) as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["ongc-flat-by", "ongc-plan-stop", "ongc-plan-target", "opening-bell"])
        with open(self.state) as fh:
            self.assertNotIn("ongc-flat-by", json.load(fh)["rung"])

    def test_the_real_closing_quote_hit_the_target(self):
        run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        code, text, _ = self.check("2026-10-23 15:29", offline=QDIR)
        self.assertEqual(code, 0)
        self.assertIn("ALERT ONGC is Rs 227.50: at or above your target Rs 227 (plan target: 2R", text)
        self.assertNotIn("your stop", text)

    def test_a_portfolio_checked_in_the_evening_and_the_next_morning(self):
        for args in (["--opening-bell"], ["--symbol", "ONGC", "--above", "230"], ["--symbol", "ITC", "--stop", "250"]):
            code, _, _ = run(["add", "--rules", self.rules] + args)
            self.assertEqual(code, 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "ONGC", "--above", "230"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, text, _ = self.check("2026-10-23 16:00", offline=QDIR)
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"ONGC": 227.5, "ITC": 257.9})

        code, text, _ = self.check("2026-10-26 09:16", offline=QDIR)
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-26 09:45", offline=QDIR)
        self.assertEqual(code, 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_and_target(self):
        code, text, _ = run(["add", "--rules", self.rules, "--at", "15:15", "--label", "flat-by time",
                             "--symbol", "ongc"])
        self.assertEqual(code, 0)
        code, text, _ = run(["add", "--rules", self.rules, "--symbol", "ONGC", "--target", "227",
                             "--date", "2026-10-23"])
        self.assertEqual(code, 0)
        with open(self.rules) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1515", "type": "time", "at": "15:15", "label": "flat-by time",
                                    "symbol": "ONGC"})
        self.assertEqual(rules[1], {"id": "ongc-target-227", "symbol": "ONGC", "type": "target",
                                    "price": 227.0, "date": "2026-10-23"})
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "4pm"])[0], 1)
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none")])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                run(["add", "--rules", self.rules, "--at", "15:15", "--before-close"])

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "ONGC", "ITC", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-10-23T09:58:26Z"])
        self.assertEqual(code, 0)
        self.assertIn("Rs 227.50 (+1.11% vs prev close Rs 225.00)", text)
        self.assertIn("delayed", text)
        run(["quote", "SBIN", "--offline", QDIR, "--write", self.prices])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["ITC", "ONGC", "SBIN"])
        self.assertEqual(doc["detail"]["ITC"]["prev_close"], 258.9)
        self.assertEqual(doc["detail"]["ITC"]["fetched"], "2026-10-23T09:58:26Z")
        self.assertIn("in rupees", doc["note"])

    def test_quote_shows_an_index_in_points(self):
        code, text, _ = run(["quote", "NIFTY", "--offline", QDIR])
        self.assertEqual(code, 0)
        self.assertIn("22600.00 (-0.39% vs prev close 22687.70)", text)
        self.assertNotIn("Rs 22600", text)

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "ITC", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["ITC", "ONGC"])

    def test_clock_exit_codes(self):
        code, text, _ = run(["clock", "--now", "2026-10-23 10:15"])
        self.assertEqual(code, 0)
        self.assertIn("IST 2026-10-23 10:15 (Friday): NSE OPEN", text)
        self.assertIn("09:15 to 15:30", text)
        code, text, _ = run(["clock", "--now", "2026-12-25T10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Christmas", text)
        self.assertIn("Next open: 2026-12-28 09:15 IST.", text)
        code, text, _ = run(["clock", "--now", "2027-03-01 10:00"])
        self.assertIn("run scan.py holidays", text)

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)

    def test_nothing_in_the_output_is_american(self):
        run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        _, text, _ = self.check("2026-10-23 15:29", offline=QDIR)
        _, clock, _ = run(["clock", "--now", "2026-10-23 10:15"])
        for out in (text, clock):
            for gone in ("$", "New York", "NYSE", "Eastern"):
                self.assertNotIn(gone, out)


if __name__ == "__main__":
    unittest.main()
