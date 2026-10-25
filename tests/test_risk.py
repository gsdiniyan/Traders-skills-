"""trader-risk tests (NSE): every rule blocks what it should, exits stay possible, and the verdicts are recorded."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load

risk = load("trader-risk", "check")

CFG = {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 75000, "min_order_inr": 500,
       "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3, "max_orders_per_day": 10,
       "min_reward_risk": 1.0, "require_stop": True, "no_entries_before": "09:30", "no_entries_after": "14:30", "tick_size": 0.05}
NOW = "2026-10-23 10:06"


def order(**kw):
    o = {"symbol": "ONGC", "exchange": "NSE", "side": "buy", "qty": "332", "type": "market", "product": "INTRADAY", "time_in_force": "day",
         "order_class": "bracket", "take_profit_limit_price": "227.00", "stop_loss_stop_price": "225.10",
         "why": "test", "plan": {"entry": 225.74, "stop": 225.10, "target": 227.0, "charges_at_stop": 73.55}}
    o.update(kw)
    return {k: v for k, v in o.items() if v is not None}


def chk(orders, cfg=None, now=NOW, **kw):
    c = dict(CFG)
    c.update(cfg or {})
    return risk.check(orders if isinstance(orders, list) else [orders], c, now=risk.parse_now(now), **kw)


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = risk.main(argv)
    return code, out.getvalue(), err.getvalue()


class ThePass(unittest.TestCase):
    def test_a_sized_bracket_order_passes_with_its_numbers(self):
        r = chk(order())
        x = r["results"][0]
        self.assertEqual((x["verdict"], x["kind"], r["pass"], r["block"]), ("PASS", "entry", 1, 0))
        self.assertEqual((x["stop"], x["target"], x["risk_per_share"], x["max_loss"], x["reward_risk"]), (225.10, 227.0, 0.64, 212.48, 1.97))
        self.assertEqual((x["entry_ref"], x["entry_ref_from"], x["value_inr"]), (225.74, "plan", 74945.68))
        self.assertEqual(r["now_ist"], "2026-10-23 10:06")
        self.assertEqual(r["cash_after"], round(100000 - 74945.68, 2))
        self.assertTrue(any("charges at the stop about Rs 73.55" in i for i in x["info"]))
        self.assertTrue(r["paper"])

    def test_the_report_is_in_rupees_and_ist(self):
        text = risk.report(chk(order()))
        self.assertIn("PASS  #1 buy ONGC 332 shares, entry", text)
        self.assertIn("max loss Rs 212.48 (limit Rs 1,000)", text)
        self.assertIn("at 2026-10-23 10:06 IST", text)
        self.assertIn("Rs 25,054.32", text)
        self.assertIn("not investment advice", text)
        self.assertNotIn("$", text)
        self.assertNotIn("New York", text)

    def test_a_live_account_is_warned_about(self):
        self.assertTrue(any("LIVE account" in w for w in chk(order(), {"paper": False})["warnings"]))


class Format(unittest.TestCase):
    def test_missing_pieces(self):
        for kw, words in ((dict(symbol=""), "no symbol"), (dict(side="hold"), "side must be buy or sell"),
                          (dict(qty=None), "give a qty"), (dict(qty="0"), "must be a positive number"),
                          (dict(notional="5000", qty=None), "no rupee-amount orders"),
                          (dict(type="limit"), "needs a limit_price"), (dict(type="stop"), "needs a stop_price"),
                          (dict(type="weird"), "unknown order type"), (dict(time_in_force="gtc"), "unknown time_in_force"),
                          (dict(product="CNC"), "unknown product"),
                          (dict(stop_loss_stop_price=None), "needs both take_profit_limit_price and stop_loss_stop_price"),
                          (dict(qty="332.5"), "whole shares only"),
                          (dict(take_profit_limit_price="-1"), "must be a positive number")):
            r = chk(order(**kw))
            self.assertEqual(r["results"][0]["verdict"], "BLOCK", kw)
            self.assertIn(words, reasons(r), kw)

    def test_prices_must_sit_on_the_tick(self):
        r = chk(order(take_profit_limit_price="227.03"))
        self.assertIn("take_profit_limit_price 227.03 is not on the Rs 0.05 price tick (nearest 227.05)", reasons(r))
        r = chk(order(type="limit", limit_price="225.72"))
        self.assertIn("limit_price 225.72 is not on the Rs 0.05 price tick (nearest 225.70)", reasons(r))
        self.assertEqual(chk(order(type="limit", limit_price="225.70"))["results"][0]["verdict"], "PASS")
        self.assertEqual(chk(order(take_profit_limit_price="227.03", ), {"tick_size": 0.01})["results"][0]["verdict"], "PASS")
        self.assertIsNone(risk.off_tick(225.75, 0.05))
        self.assertEqual(risk.off_tick(225.74, 0.05), 225.75)

    def test_numbers_sent_as_numbers_get_a_warning_not_a_block(self):
        r = chk(order(take_profit_limit_price=227.0))
        self.assertEqual(r["results"][0]["verdict"], "PASS")
        self.assertTrue(any("send take_profit_limit_price as a string" in w for w in r["results"][0]["warnings"]))


class Assets(unittest.TestCase):
    def test_derivative_contracts_are_off_until_allowed(self):
        for sym in ("RELIANCE26NOV1500CE", "NIFTY26OCT25000PE", "TCS26NOVFUT", "BANKNIFTY26OCT55000.5CE"):
            r = chk(order(symbol=sym, qty="10"))
            self.assertIn("derivatives are off", reasons(r), sym)
        r = chk(order(symbol="RELIANCE26NOV1500CE", qty="10"), {"allow_options": True})
        self.assertNotIn("derivatives are off", reasons(r))
        self.assertTrue(any("options can lose 100%" in w for w in r["results"][0]["warnings"]))

    def test_nse_symbols_with_ampersands_and_dashes_are_fine_and_junk_is_not(self):
        for sym in ("M&M", "BAJAJ-AUTO", "3MINDIA", "ONGC"):
            self.assertNotIn("asset:", reasons(chk(order(symbol=sym))), sym)
        for sym in ("TSLA/USD", "ongc nse", "!!"):
            self.assertIn("asset:", reasons(chk(order(symbol=sym))), sym)


class Entries(unittest.TestCase):
    def test_the_stop_is_required_and_must_sit_under_the_entry(self):
        r = chk(order(stop_loss_stop_price=None, take_profit_limit_price=None, order_class=None, plan={"entry": 225.74}))
        self.assertIn("stop: no stop", reasons(r))
        r = chk(order(stop_loss_stop_price="226.00"))
        self.assertIn("the stop 226.00 must be below the entry 225.74", reasons(r))
        r = chk(order(stop_loss_stop_price=None, take_profit_limit_price=None, order_class=None, plan={"entry": 225.74}), {"require_stop": False})
        self.assertEqual(r["results"][0]["verdict"], "PASS")
        self.assertTrue(any("no stop" in w for w in r["results"][0]["warnings"]))

    def test_max_loss_and_reward_risk(self):
        r = chk(order(qty="1700", stop_loss_stop_price="224.00", take_profit_limit_price="228.00"), {"max_order_inr": 500000, "budget": 1000000})
        self.assertIn("loss: 1700 shares x 1.74 risk a share = Rs 2,958 at the stop, over your Rs 1,000 max loss per trade", reasons(r))
        r = chk(order(take_profit_limit_price="226.00"))
        self.assertIn("reward:risk: 0.41", reasons(r))
        r = chk(order(take_profit_limit_price="225.50"))
        self.assertIn("the target 225.50 must be above the entry 225.74", reasons(r))
        r = chk(order(take_profit_limit_price=None, plan={"entry": 225.74, "stop": 225.10}))
        self.assertIn("no target to measure it against", reasons(r))

    def test_size_position_cash_and_margin(self):
        self.assertIn("is under your Rs 500 minimum order", reasons(chk(order(qty="1"))))
        self.assertIn("is over your Rs 75,000 max order", reasons(chk(order(qty="400"))))
        r = chk(order(), {"max_position_pct": 50, "max_order_inr": 500000})
        self.assertIn("over your 50% cap", reasons(r))
        r = chk(order(), {"cash_buffer_pct": 40, "max_position_pct": 100})
        self.assertIn("under your 40% buffer", reasons(r))
        r = chk(order(), {"budget": 50000, "max_order_inr": 500000, "max_position_pct": 100})
        self.assertIn("that would be margin", reasons(r))
        r = chk(order(), {"allow_margin": True, "budget": 50000, "max_order_inr": 500000, "max_position_pct": 100})
        self.assertNotIn("margin", reasons(r))

    def test_blocked_symbols_duplicates_and_the_batch_is_checked_in_order(self):
        self.assertIn("is on your blocked list", reasons(chk(order(), {"blocked": ["ongc"]})))
        r = chk([order(), order()], {"max_order_inr": 500000})
        self.assertEqual([x["verdict"] for x in r["results"]], ["PASS", "BLOCK"])
        self.assertIn("a second buy ONGC in this batch", reasons(r, 1))
        two = [order(), order(symbol="TCS", qty="20", stop_loss_stop_price="2520.00", take_profit_limit_price="2560.00",
                              plan={"entry": 2530.0, "stop": 2520.0, "target": 2560.0})]
        r = chk(two, {"max_loss_per_trade_inr": 1000})
        self.assertEqual(r["results"][1]["verdict"], "BLOCK")                       # Rs 74,946 + Rs 50,600 is more than the cash
        self.assertIn("cash:", reasons(r, 1))

    def test_trades_orders_and_daily_loss_for_the_day(self):
        state = {"orders": [{"status": "placed", "date": "2026-10-23", "symbol": "TCS", "side": "buy", "kind": "entry", "max_loss": 900.0},
                            {"status": "placed", "date": "2026-10-23", "symbol": "INFY", "side": "buy", "kind": "entry", "max_loss": 800.0},
                            {"status": "placed", "date": "2026-10-23", "symbol": "SBIN", "side": "buy", "kind": "entry", "max_loss": 700.0}]}
        r = chk(order(), state=state)
        self.assertIn("trades: 3 entries placed today", reasons(r))
        self.assertIn("daily loss: Rs 2,400 already at risk today plus Rs 212.48", reasons(r)) if False else None
        r = chk(order(), {"max_trades_per_day": 10, "max_daily_loss_inr": 2500}, state=state)
        self.assertIn("daily loss: Rs 2,400 already at risk today plus Rs 212.48 for this one = Rs 2,612.48, over your Rs 2,500 daily loss limit", reasons(r))
        r = chk(order(), {"max_trades_per_day": 10, "max_orders_per_day": 3}, state=state)
        self.assertIn("daily limit: 3 placed today", reasons(r))
        old = {"orders": [dict(state["orders"][0], date="2026-10-22")]}
        self.assertEqual(chk(order(), state=old)["results"][0]["verdict"], "PASS")
        r = chk(order(), state={"orders": [{"status": "placed", "date": "2026-10-23", "symbol": "ONGC", "side": "buy", "kind": "entry"}]})
        self.assertIn("a buy ONGC was already placed today", reasons(r))
        self.assertTrue(any("no max loss in the log" in w for w in r["warnings"]))

    def test_the_prices_file_and_a_run_up_since_the_plan(self):
        now = risk.parse_now(NOW)
        prices = {"ONGC": 226.40}
        r = risk.check([order(qty="500")], dict(CFG, max_order_inr=500000, budget=1000000), prices=prices, now=now,
                       price_times={"ONGC": risk.parse_now("2026-10-23 10:00")})
        x = r["results"][0]
        self.assertEqual((x["entry_ref"], x["entry_ref_from"]), (226.40, "price"))
        self.assertTrue(any("above the plan's entry" in i for i in x["info"]))
        r = risk.check([order(qty="500")], dict(CFG, max_order_inr=500000, budget=1000000), prices=prices, now=now,
                       price_times={"ONGC": risk.parse_now("2026-10-23 09:00")})
        self.assertEqual(r["results"][0]["entry_ref_from"], "plan")
        self.assertTrue(any("older than 15 minutes" in w for w in r["results"][0]["warnings"]))


class Window(unittest.TestCase):
    def test_the_entry_window_in_ist(self):
        for now, words in (("2026-10-23 09:20", "no new entries before 09:30 IST"), ("2026-10-23 14:31", "no new entries after 14:30 IST"),
                           ("2026-10-23 08:50", "the market is closed"), ("2026-10-23 15:31", "the market is closed"),
                           ("2026-10-24 10:30", "NSE holiday" if False else "weekend"), ("2026-10-20 10:30", "NSE holiday: Dussehra")):
            r = chk(order(), now=now)
            self.assertEqual(r["results"][0]["verdict"], "BLOCK", now)
            self.assertIn(words, reasons(r), now)
        self.assertEqual(chk(order(), now="2026-10-23 14:30")["results"][0]["verdict"], "PASS")
        self.assertEqual(chk(order(), now="2026-10-23 09:30")["results"][0]["verdict"], "PASS")
        self.assertEqual(chk(order(), {"no_entries_before": "", "no_entries_after": None}, now="2026-10-23 15:00")["results"][0]["verdict"], "PASS")

    def test_a_year_without_a_holiday_list_is_warned_about(self):
        r = chk(order(), now="2028-03-01 10:30")
        self.assertTrue(any("holiday list for 2028 is not loaded" in w for w in r["warnings"]))
        self.assertEqual(r["results"][0]["verdict"], "PASS")

    def test_trader_holidays_json_closes_a_day(self):
        d = tempfile.mkdtemp()
        old = os.getcwd()
        try:
            os.chdir(d)
            os.mkdir("trader")
            with open(os.path.join("trader", "holidays.json"), "w") as fh:
                json.dump({"holidays": {"2027-01-26": "Republic Day"}}, fh)
            r = chk(order(), now="2027-01-26 10:30")
            self.assertIn("NSE holiday: Republic Day", reasons(r))
            self.assertFalse(any("not loaded" in w for w in r["warnings"]))
        finally:
            os.chdir(old)
            shutil.rmtree(d)

    def test_bad_times_in_the_config(self):
        with self.assertRaises(risk.RiskError):
            chk(order(), {"no_entries_after": "half past two"})


class Exits(unittest.TestCase):
    def test_getting_out_is_never_blocked_by_the_entry_rules(self):
        pf = {"cash": 10000, "positions": [{"symbol": "ONGC", "shares": 332}]}
        bare = dict(side="sell", order_class=None, take_profit_limit_price=None, stop_loss_stop_price=None, plan=None)
        r = chk(order(**bare), {"no_entries_after": "10:00"}, portfolio=pf, now="2026-10-23 14:50")
        x = r["results"][0]
        self.assertEqual((x["verdict"], x["kind"]), ("PASS", "exit"))                 # after the entry window, still allowed out
        self.assertEqual(x["reasons"], [])
        pf = {"cash": 10000, "positions": [{"symbol": "ONGC", "shares": 1}]}
        x = chk(order(qty="1", **bare), portfolio=pf, prices={"ONGC": 225.74})["results"][0]
        self.assertEqual((x["verdict"], x["kind"]), ("PASS", "exit"))
        self.assertTrue(any(w.startswith("exit, not blocked: size:") for w in x["warnings"]))      # an entry rule, shown as a warning

    def test_an_exit_is_still_checked_for_format_and_selling_more_than_you_hold(self):
        pf = {"cash": 10000, "positions": [{"symbol": "ONGC", "shares": 100}]}
        r = chk(order(side="sell", qty="332", order_class=None, take_profit_limit_price=None, stop_loss_stop_price=None, plan=None), portfolio=pf)
        self.assertIn("short: selling more ONGC than you hold (100 shares)", reasons(r))
        r = chk(order(side="sell", qty="100", type="limit", order_class=None, take_profit_limit_price=None, stop_loss_stop_price=None, plan=None), portfolio=pf)
        self.assertIn("needs a limit_price", reasons(r))
        r = chk(order(side="sell", qty="332", order_class=None, take_profit_limit_price=None, stop_loss_stop_price=None, plan=None), {"allow_short": True}, portfolio=pf)
        self.assertNotIn("short:", reasons(r))


class Fingerprint(unittest.TestCase):
    def test_it_covers_the_order_not_the_commentary(self):
        a = risk.fingerprint(order())
        self.assertEqual(a, risk.fingerprint(order(why="other words", plan={"x": 1})))
        self.assertNotEqual(a, risk.fingerprint(order(qty="333")))
        self.assertNotEqual(a, risk.fingerprint(order(stop_loss_stop_price="225.05")))
        self.assertNotEqual(a, risk.fingerprint(order(exchange="BSE")))
        self.assertNotEqual(a, risk.fingerprint(order(product="DELIVERY")))
        self.assertEqual(len(a), 16)


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

    def test_pass_records_the_verdict_with_its_fingerprint(self):
        self.write("orders.json", {"orders": [order()]})
        self.write("config.json", CFG)
        code, text, err = run(["--now", NOW])
        self.assertEqual(code, 0, err)
        with open(os.path.join("trader", "state", "risk.json")) as fh:
            rec = json.load(fh)
        self.assertEqual(rec["results"][0]["fingerprint"], risk.fingerprint(order()))
        self.assertEqual((rec["pass"], rec["block"], rec["orders_file"]), (1, 0, "trader/orders.json"))
        self.assertIn("checked_at", rec)

    def test_a_block_exits_1_and_json_and_no_write(self):
        self.write("orders.json", {"orders": [order(stop_loss_stop_price="226.00")]})
        self.write("config.json", CFG)
        code, text, _ = run(["--now", NOW, "--json", "--no-write"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)["block"], 1)
        self.assertFalse(os.path.exists(os.path.join("trader", "state")))

    def test_missing_files_and_bad_input_are_one_plain_line(self):
        code, _, err = run([])
        self.assertEqual((code, "no orders file" in err or "orders file (run trader-budget first)" in err), (1, True))
        self.write("orders.json", {"orders": []})
        code, _, err = run([])
        self.assertIn("no orders in", err)
        self.write("orders.json", {"orders": [order()]})
        code, _, err = run(["--today", "tomorrow"])
        self.assertIn("--today wants a date", err)
        code, _, err = run(["--now", "noonish"])
        self.assertIn("IST", err)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            fh.write("{broken")
        code, _, err = run(["--now", NOW])
        self.assertIn("not valid JSON", err)

    def test_a_portfolio_replaces_the_budget_as_the_account(self):
        self.write("orders.json", {"orders": [order()]})
        self.write("config.json", dict(CFG, budget=None))
        code, _, err = run(["--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("nothing to check the orders against", err)
        self.write("portfolio.json", {"cash": 100000, "positions": []})
        code, text, err = run(["--now", NOW])
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
