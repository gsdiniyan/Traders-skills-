"""trader-budget tests (NSE): sizing one day trade to a budget and a max loss, charges, ticks, lots, and the weights mode."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load

size = load("trader-budget", "size")

PLAN = {"created_by": "trader-exit", "symbol": "ONGC", "side": "buy", "date": "2026-10-23", "setup": "orb",
        "entry": 225.74, "entry_time": "10:05", "entry_bar": 9, "interval": "5m",
        "stop": 225.10, "stop_label": "opening-range midpoint", "target": 227.00, "target_label": "2R (two times the risk)",
        "risk_per_share": 0.64, "reward_risk": 1.97, "flat_by": "15:15"}
CFG = {"budget": 100000, "max_loss_per_trade_inr": 1000, "max_order_inr": 75000}
TODAY = "2026-10-23"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def sz(plan=None, cfg=None, **kw):
    kw.setdefault("today", TODAY)
    return size.size_trade(dict(PLAN, **(plan or {})), dict(CFG, **(cfg or {})), **kw)


class Money(unittest.TestCase):
    def test_rupees_with_indian_grouping(self):
        self.assertEqual(size.money(1234567.5), "Rs 12,34,567.50")
        self.assertEqual(size.money(100000), "Rs 1,00,000")
        self.assertEqual(size.money(999), "Rs 999")
        self.assertEqual(size.money(-45678.9), "-Rs 45,678.90")
        self.assertEqual(size.money(0.5), "Rs 0.50")
        self.assertEqual(size._group("12345678"), "1,23,45,678")

    def test_prices_round_to_the_paisa_and_limits_to_the_tick(self):
        self.assertEqual(size.round_cent(225.745), 225.75)
        self.assertEqual(size.round_tick(225.74), 225.75)
        self.assertEqual(size.round_tick(225.72), 225.70)
        self.assertEqual(size.round_tick(225.775), 225.80)
        self.assertEqual(size.round_tick(225.74, 0.01), 225.74)
        self.assertEqual(size.shares_for(75000, 225.74), 332)
        self.assertEqual(size.shares_for(0, 10), 0)
        self.assertEqual(size.shares_for(10, 0), 0)

    def test_the_input_time_is_ist(self):
        import datetime
        self.assertEqual(size.parse_now("2026-10-23 10:06"), datetime.datetime(2026, 10, 23, 4, 36))
        self.assertEqual(size.to_ist(datetime.datetime(2026, 10, 23, 4, 36)), datetime.datetime(2026, 10, 23, 10, 6))
        with self.assertRaises(size.SizeError):
            size.parse_now("soon")


class Charges(unittest.TestCase):
    def test_the_round_trip_charges_by_hand(self):
        c = size.trade_charges(332, 225.74, 225.10)
        bv, sv = 332 * 225.74, 332 * 225.10
        brokerage = min(20, bv * 0.0003) + min(20, sv * 0.0003)           # Rs 20 or 0.03 %, whichever is lower, each way
        self.assertEqual(c["brokerage"], round(brokerage, 2))
        self.assertEqual(c["stt"], round(sv * 0.00025, 2))                 # 0.025 % of the SALE
        self.assertEqual(c["exchange"], round((bv + sv) * 0.0000297, 2))
        self.assertEqual(c["sebi"], round((bv + sv) * 0.000001, 2))
        self.assertEqual(c["stamp"], round(bv * 0.00003, 2))               # 0.003 % of the PURCHASE
        self.assertEqual(c["gst"], round((brokerage + (bv + sv) * 0.0000297 + (bv + sv) * 0.000001) * 0.18, 2))
        self.assertEqual(c["total"], 73.55)

    def test_a_small_order_pays_the_percentage_not_the_flat_fee(self):
        self.assertEqual(size.trade_charges(10, 100, 100)["brokerage"], 0.6)       # 0.03 % of Rs 1,000 twice
        self.assertEqual(size.trade_charges(1000, 100, 100)["brokerage"], 40.0)    # capped at Rs 20 a side

    def test_the_config_can_change_the_rates(self):
        zero = size.trade_charges(332, 225.74, 225.10, {"charges": {"brokerage_per_order": 0, "brokerage_pct": 0}})
        self.assertEqual(zero["brokerage"], 0)
        self.assertLess(zero["total"], 73.55)
        flat = size.trade_charges(10, 100, 100, {"charges": {"brokerage_pct": 0, "brokerage_per_order": 20}})
        self.assertEqual(flat["brokerage"], 40.0)

    def test_breakeven_covers_the_charges(self):
        be = size.breakeven_price(332, 225.74)
        self.assertAlmostEqual(be, 225.96, places=2)
        gain = 332 * (be - 225.74)
        self.assertAlmostEqual(gain, size.trade_charges(332, 225.74, be)["total"], places=3)


class Sizing(unittest.TestCase):
    def test_the_ongc_plan_is_bound_by_the_position_cap(self):
        s = sz()
        self.assertEqual((s["qty"], s["bound_by"]), (332, "position cap"))
        self.assertEqual((s["cost"], s["max_loss"], s["entry"], s["reward_risk"]), (74945.68, 212.48, 225.74, 1.97))
        self.assertEqual([l["shares"] for l in s["limits"]], [1562, 332, 332, 398])
        self.assertTrue([l for l in s["limits"] if l["binds"]][0]["name"] == "position cap")
        self.assertIsNone(s["refusal"])
        self.assertEqual((s["exchange"], s["product"], s["tick_size"]), ("NSE", "INTRADAY", 0.05))

    def test_costs_are_added_to_the_loss_and_taken_off_the_gain(self):
        cs = sz()["costs"]
        self.assertEqual(cs["at_stop"]["total"], 73.55)
        self.assertEqual(cs["at_target"]["total"], 73.73)
        self.assertEqual(cs["net_loss_at_stop"], 286.03)           # 212.48 + 73.55
        self.assertEqual(cs["net_gain_at_target"], 344.59)         # 332 x 1.26 = 418.32 - 73.73
        self.assertEqual(cs["net_reward_risk"], 1.2)
        self.assertEqual((cs["breakeven_price"], cs["breakeven_pct"]), (225.96, 0.098))
        self.assertEqual(cs["share_of_max_loss_pct"], 34.6)

    def test_the_max_loss_binds_when_the_stop_is_wide(self):
        s = sz(plan={"stop": 223.45, "risk_per_share": 2.29, "target": 230.30}, cfg={"budget": 300000, "max_order_inr": 300000})
        self.assertEqual((s["qty"], s["bound_by"]), (436, "max loss"))      # 1000 / 2.29 = 436.7
        self.assertLessEqual(s["max_loss"], 1000)
        self.assertEqual(s["max_loss"], round(436 * 2.29, 2))

    def test_the_cash_buffer_and_the_portfolio_cash_can_bind(self):
        s = sz(cfg={"max_position_pct": 100, "max_order_inr": 500000, "cash_buffer_pct": 20, "max_loss_per_trade_inr": 100000})
        self.assertEqual((s["qty"], s["bound_by"]), (354, "cash"))                  # (100000 - 20000) / 225.74
        s = sz(cfg={"max_position_pct": 100, "max_order_inr": 500000, "max_loss_per_trade_inr": 100000}, cash=30000)
        self.assertEqual((s["qty"], s["bound_by"]), (88, "cash"))                   # (30000 - 10000) / 225.74
        self.assertIn("trader/portfolio.json", [l for l in s["limits"] if l["name"] == "cash"][0]["how"])

    def test_a_fresh_higher_price_sizes_the_loss_at_that_price(self):
        s = sz(price=226.40)
        self.assertEqual((s["entry"], s["entry_from"]), (226.40, "price"))
        self.assertEqual(s["risk_per_share"], 1.3)
        s = sz(price=225.00)
        self.assertEqual((s["entry"], s["entry_from"]), (225.74, "plan"))

    def test_a_limit_order_is_sized_at_the_limit_on_the_tick(self):
        s = sz(limit=225.72)
        self.assertEqual((s["entry"], s["entry_from"], s["order_type"]), (225.70, "limit", "limit"))
        s = sz(limit=225.74)
        self.assertEqual(s["entry"], 225.75)
        s = size.size_trade(dict(PLAN), dict(CFG, tick_size=0.01), limit=225.74, today=TODAY)
        self.assertEqual(s["entry"], 225.74)

    def test_refusals(self):
        cases = ((dict(cfg={"max_loss_per_trade_inr": 0.5}), "more than your Rs 0.50 max loss per trade"),
                 (dict(cfg={"max_order_inr": 100}), "costs Rs 225.74, more than your max order limit"),
                 (dict(cfg={"min_order_inr": 80000}), "under your Rs 80,000 minimum order"),
                 (dict(plan={"target": 225.80}), None),
                 (dict(cfg={"min_reward_risk": 3}), "reward:risk 1.97"))
        for kw, words in cases:
            if words is None:
                continue
            s = sz(**kw)
            self.assertIsNotNone(s["refusal"], kw)
            self.assertIn(words, s["refusal"])
            self.assertIsNone(size.bracket_order(s))
        for plan, words in (({"stop": 226.0}, "not below the entry"), ({"target": 225.0}, "not above the entry"),
                            ({"entry": None}, "needs a positive entry"), ({"symbol": ""}, "no symbol"), ({"side": "sell"}, "long (buy)"),
                            ({"date": "2026-10-22"}, "make a new plan")):
            with self.assertRaises(size.SizeError) as cm:
                sz(plan=plan)
            self.assertIn(words, str(cm.exception))
        with self.assertRaises(size.SizeError):
            size.size_trade(PLAN, {"max_loss_per_trade_inr": 1000}, today=TODAY)          # no budget
        with self.assertRaises(size.SizeError):
            sz(cfg={"blocked": ["ongc"]})

    def test_lots_round_down_and_a_lot_too_big_is_refused(self):
        s = sz(cfg={"max_order_inr": 500000, "max_position_pct": 100, "max_loss_per_trade_inr": 100000, "budget": 1000000}, lot=100)
        self.assertEqual(s["qty"] % 100, 0)
        self.assertEqual(s["bound_by"], "lot size")
        self.assertIn("lots of 100", s["lot_note"])
        s = sz(lot=1700)
        self.assertEqual(s["qty"], 0)
        self.assertIn("one lot is 1700 shares", s["refusal"])
        with self.assertRaises(size.SizeError):
            sz(lot=-5)


class Orders(unittest.TestCase):
    def test_the_proposed_order_shape(self):
        s = sz()
        o = size.bracket_order(s)
        want = {"symbol": "ONGC", "exchange": "NSE", "side": "buy", "qty": "332", "type": "market", "product": "INTRADAY",
                "time_in_force": "day", "order_class": "bracket", "take_profit_limit_price": "227.00", "stop_loss_stop_price": "225.10"}
        for k, v in want.items():
            self.assertEqual(o[k], v, k)
        self.assertNotIn("limit_price", o)
        self.assertIn("332 shares", o["why"])
        self.assertEqual(o["plan"]["max_loss"], 212.48)
        self.assertEqual(o["plan"]["breakeven_price"], 225.96)
        self.assertEqual(o["plan"]["charges_at_stop"], 73.55)
        o = size.bracket_order(sz(limit=225.70))
        self.assertEqual((o["type"], o["limit_price"]), ("limit", "225.70"))

    def test_the_report_says_the_money_in_rupees(self):
        s = sz()
        text = size.plan_report(s, size.bracket_order(s), ["a note"])
        for must in ("ONGC buy, opening-range breakout, entry 225.74 at 10:05", "Rs 1,00,000", "332 shares of ONGC = Rs 74,945.68",
                     "Charges (intraday round trip): Rs 73.55 if it stops out", "It needs to reach 225.96 (+0.10% from the entry)",
                     "Warning: charges are 35% of the loss at the stop", "buy 332 ONGC on NSE, intraday, market, day, take-profit 227.00, stop-loss 225.10",
                     "Flat by 15:15 IST", "not investment advice", "a note"):
            self.assertIn(must, text)
        self.assertNotIn("$", text)
        self.assertNotIn("New York", text)

    def test_a_refusal_report(self):
        s = sz(cfg={"max_loss_per_trade_inr": 0.5})
        text = size.plan_report(s, None)
        self.assertIn("No order: one share risks Rs 0.64", text)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")
        for name, doc in (("plan.json", PLAN), ("config.json", CFG)):
            with open(os.path.join("trader", name), "w") as fh:
                json.dump(doc, fh)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_size_and_write_orders(self):
        code, text, err = run(["--now", "2026-10-23 10:06", "--write"])
        self.assertEqual(code, 0, err)
        self.assertIn("Wrote 1 proposed order to trader/orders.json", text)
        with open(os.path.join("trader", "orders.json")) as fh:
            doc = json.load(fh)
        self.assertEqual((doc["created_by"], doc["status"], doc["paper"], doc["broker"]), ("trader-budget", "proposed", True, "dhan"))
        self.assertEqual(doc["orders"][0]["qty"], "332")
        self.assertIn("place yourself", doc["note"])

    def test_json_limit_and_lot_size(self):
        code, text, _ = run(["--now", "2026-10-23 10:06", "--json", "--limit", "225.70"])
        doc = json.loads(text)
        self.assertEqual((code, doc["sizing"]["qty"], doc["orders"][0]["limit_price"]), (0, 332, "225.70"))
        code, text, _ = run(["--now", "2026-10-23 10:06", "--lot-size", "1700"])
        self.assertEqual(code, 1)
        self.assertIn("No order:", text)

    def test_a_stale_price_file_is_not_used_and_a_fresh_one_is(self):
        with open(os.path.join("trader", "prices.json"), "w") as fh:
            json.dump({"as_of": "2026-10-23T04:30:00Z", "prices": {"ONGC": 226.4}, "detail": {"ONGC": {"time": "2026-10-23T04:30:00Z"}}}, fh)
        code, text, _ = run(["--now", "2026-10-23 10:06", "--json"])
        self.assertEqual(json.loads(text)["sizing"]["entry_from"], "price")
        self.assertTrue(any("IST" in n for n in json.loads(text)["notes"]))
        code, text, _ = run(["--now", "2026-10-23 11:30", "--json"])
        self.assertEqual(json.loads(text)["sizing"]["entry_from"], "plan")
        self.assertTrue(any("not used" in n for n in json.loads(text)["notes"]))

    def test_errors_are_one_plain_line(self):
        os.remove(os.path.join("trader", "plan.json"))
        code, _, err = run([])
        self.assertEqual(code, 1)
        self.assertIn("no plan (run trader-exit first", err)
        code, _, err = run(["--top", "3"])
        self.assertIn("work with --weights", err)
        code, _, err = run(["--weights", "RELIANCE"])
        self.assertIn("TICKER=WEIGHT", err)


class Weights(unittest.TestCase):
    ROWS = size.parse_weights({"RELIANCE": 40, "TCS": 30, "INFY": 20, "ITC": 10})

    def test_normalise_blocked_and_top(self):
        keep, dropped = size.normalise(self.ROWS, ["itc"], None)
        self.assertEqual([r["ticker"] for r in keep], ["RELIANCE", "TCS", "INFY"])
        self.assertAlmostEqual(sum(r["weight"] for r in keep), 100.0)
        self.assertEqual(dropped[0]["reason"], "on your blocked list")
        keep, dropped = size.normalise(self.ROWS, [], 2)
        self.assertEqual(len(keep), 2)
        with self.assertRaises(size.SizeError):
            size.normalise(self.ROWS + self.ROWS)

    def test_the_cap_shares_the_excess_and_the_split_never_overspends(self):
        r = size.size(self.ROWS, {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 30, "min_order_inr": 500}, whole=False)
        self.assertEqual(r["investable"], 90000)
        for p in r["positions"]:
            self.assertLessEqual(p["rupees"], 30000 + 0.01)
        self.assertLessEqual(r["invested"], 90000)
        self.assertTrue(r["positions"][0]["capped"])

    def test_whole_shares_need_prices_and_floor_each_slice(self):
        with self.assertRaises(size.SizeError) as cm:
            size.size(self.ROWS, {"budget": 500000, "fractional": False})
        self.assertIn("whole shares need a price for", str(cm.exception))
        prices = {"RELIANCE": 1450.5, "TCS": 2530.0, "INFY": 1153.8, "ITC": 420.15}
        r = size.size(self.ROWS, {"budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_inr": 500}, prices=prices, whole=True)
        spent = 0
        for p in r["positions"]:
            self.assertEqual(p["shares"], int(p["shares"]))
            spent += p["shares"] * prices[p["ticker"]]
        self.assertLessEqual(spent, r["investable"] + 0.01)
        self.assertGreater(spent, r["investable"] - 2600)                    # at most about one share's worth idle

    def test_a_slice_under_the_minimum_is_dropped(self):
        r = size.size(self.ROWS, {"budget": 2000, "cash_buffer_pct": 0, "max_position_pct": 100, "min_order_inr": 600}, whole=False)
        self.assertTrue(any("minimum order" in d["reason"] for d in r["dropped"]))

    def test_the_cli_split_writes_delivery_orders(self):
        d = tempfile.mkdtemp()
        old = os.getcwd()
        try:
            os.chdir(d)
            os.mkdir("trader")
            with open(os.path.join("trader", "prices.json"), "w") as fh:
                json.dump({"prices": {"RELIANCE": 1450.5, "TCS": 2530.0, "INFY": 1153.8, "ITC": 420.15}}, fh)
            code, text, err = run(["--weights", "RELIANCE=40,TCS=30,INFY=20,ITC=10", "--budget", "500000", "--whole-shares", "--write"])
            self.assertEqual(code, 0, err)
            self.assertIn("Proposed orders (delivery buys, nothing has been placed)", text)
            with open(os.path.join("trader", "orders.json")) as fh:
                orders = json.load(fh)["orders"]
            self.assertTrue(all(o["product"] == "DELIVERY" and o["exchange"] == "NSE" and "qty" in o for o in orders))
            self.assertIn("These buys have no stop", text)
        finally:
            os.chdir(old)
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
