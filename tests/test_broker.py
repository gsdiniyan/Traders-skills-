"""trader-broker tests (NSE): tickets only, one explicit yes per order, nothing is ever placed by the pack."""

import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
NOW = "2026-10-23 10:06"
TODAY = "2026-10-23"
BATCH = ["RELIANCE", "TCS", "INFY", "ITC", "ONGC"]
ONGC_FIELDS = {"symbol": "ONGC", "exchange": "NSE", "side": "buy", "qty": "332", "type": "market", "product": "INTRADAY",
               "time_in_force": "day", "order_class": "bracket", "take_profit_limit_price": "227.00",
               "stop_loss_stop_price": "225.10"}
ONGC_PLAIN = ("BUY 332 ONGC, market, intraday (MIS), good for today / stop-loss 225.10 / take-profit 227.00 / max loss "
              "about Rs 212.48 if the stop fills at 225.10 (it can fill worse in a fast market)")
PLAN = {"symbol": "ONGC", "flat_by": "15:15"}


def ongc(**over):
    o = dict(ONGC_FIELDS, why="opening-range breakout on ONGC",
             plan={"entry": 225.74, "stop": 225.10, "target": 227.0, "charges_at_stop": 73.55})
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


def write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(doc, fh)


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=None, batch=BATCH):
        return ticket.parse_yes(reply, order or ongc(), batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, I will take it", "yes ONGC", "yes 332 ONGC",
                  "yes 225.10", "yes max loss 212.48", "yes target 227.00"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "yeah", "yep", "absolutely", "send it",
                  "yes to all", "yes, all of them", "yes to the rest", "yes both",
                  "yes TCS", "yes 300", "yes 400 ONGC", "yes but stop at 224", "yes?", "no", "not yes",
                  "yes, wait", "yes if it is under 226"):
            self.assertFalse(self.yes(r), r)

    def test_amount_must_match_this_order(self):
        sell = {"symbol": "ITC", "side": "sell", "qty": "30", "type": "market", "product": "INTRADAY", "time_in_force": "day"}
        self.assertTrue(self.yes("yes sell 30 ITC", sell))
        self.assertFalse(self.yes("yes sell 40", sell))

    def test_names_with_ampersands_and_digits_are_other_stocks(self):
        self.assertFalse(self.yes("yes M&M", ongc(), ["ONGC", "M&M"]))
        self.assertFalse(self.yes("yes 3MINDIA", ongc(), ["ONGC", "3MINDIA"]))


class TicketShape(unittest.TestCase):
    def test_fields_are_the_order_without_why_and_plan(self):
        self.assertEqual(ticket.ticket_fields(ongc()), ONGC_FIELDS)
        self.assertEqual(ticket.fingerprint(ongc()), ticket.fingerprint(ONGC_FIELDS))

    def test_fingerprint_matches_trader_risk(self):
        self.assertEqual(ticket.fingerprint(ongc()), check.fingerprint(ongc()))

    def test_plain_english(self):
        self.assertEqual(ticket.plain(ongc()), ONGC_PLAIN)
        lim = ongc(type="limit", limit_price="225.70")
        self.assertEqual(ticket.plain(lim).split(" / ")[0], "BUY 332 ONGC, limit 225.70, intraday (MIS), good for today")
        self.assertEqual(ticket.max_loss(lim), (199.2, 225.10))
        self.assertEqual(ticket.max_loss(dict(ONGC_FIELDS)), (None, 225.10))
        delivery = {"symbol": "ITC", "side": "buy", "qty": "10", "type": "market", "product": "DELIVERY", "time_in_force": "day"}
        self.assertIn("delivery (CNC)", ticket.plain(delivery))

    def test_rupees_use_indian_grouping(self):
        self.assertEqual(ticket._money(123456), "Rs 1,23,456")
        self.assertEqual(ticket._money(1234.5), "Rs 1,234.50")
        self.assertEqual(ticket._money(212.48), "Rs 212.48")
        self.assertEqual(ticket._money(-1000), "-Rs 1,000")

    def test_dhan_fields(self):
        f = ticket.dhan_fields(ongc())
        self.assertEqual((f["transactionType"], f["exchangeSegment"], f["productType"], f["orderType"], f["quantity"]),
                         ("BUY", "NSE_EQ", "INTRADAY", "MARKET", 332))
        self.assertEqual((f["targetPrice"], f["stopLossPrice"]), (227.0, 225.1))
        lim = ticket.dhan_fields(ongc(type="limit", limit_price="225.70", product="DELIVERY"))
        self.assertEqual((lim["orderType"], lim["price"], lim["productType"]), ("LIMIT", 225.7, "CNC"))
        self.assertEqual(ticket.dhan_fields(ongc(exchange="BSE"))["exchangeSegment"], "BSE_EQ")

    def test_ist_clock(self):
        self.assertEqual(ticket.ist_iso(ticket.parse_now("2026-10-23 10:06")), "2026-10-23T10:06:00+05:30")
        self.assertEqual(ticket.ist_iso(ticket.parse_now("2026-10-23T04:36:00Z")), "2026-10-23T10:06:00+05:30")


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        write(self.orders, {"orders": [ongc()]})
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.plan = os.path.join(self.d, "plan.json")
        write(self.plan, PLAN)
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--now", now, "--out", self.risk])

    def state_doc(self):
        with open(self.state) as fh:
            return json.load(fh)

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn('Reply "yes"', text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_full_yes_flow_and_nothing_is_placed(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, PAPER trade (nothing is placed anywhere), Dhan", text)
        for part in ONGC_PLAIN.split(" / "):
            self.assertIn("  " + part + "\n", text)
        self.assertIn("READY: trader-risk PASS", text)
        self.assertIn("Nothing in this pack sends orders.", text)
        self.assertIn("In the Dhan app (or your broker's): BUY | NSE | ONGC | intraday (MIS) | qty 332 | market", text)
        self.assertIn('"exchangeSegment": "NSE_EQ"', text)
        self.assertIn('"stopLossPrice": 225.1', text)
        self.assertNotIn('"plan"', text)
        self.assertNotIn('"why"', text)
        for gone in ("Allow once", "place_stock_order", "Alpaca", "$", "New York"):
            self.assertNotIn(gone, text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        self.assertFalse(os.path.exists(self.state))
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes to all"))
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("Recorded your yes for order 1", text)
        self.assertIn("Paper trade: nothing is placed", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("APPROVED", err)

        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "112111182198",
                                      "--filled-qty", "332", "--filled-price", "225.75", "--portfolio", self.pf,
                                      now="2026-10-23 10:08"))
        self.assertEqual(code, 0)
        st = self.state_doc()
        self.assertEqual([r["status"] for r in st["orders"]], ["approved", "placed", "filled"])
        appr, plc = st["orders"][0], st["orders"][1]
        self.assertEqual((appr["reply"], appr["kind"], appr["max_loss"], appr["date"]), ("yes", "entry", 212.48, TODAY))
        self.assertEqual(appr["at"], "2026-10-23T10:06:00+05:30")
        self.assertEqual((plc["broker_order_id"], plc["max_loss"], plc["kind"]), ("112111182198", 212.48, "entry"))
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], round(100000 - 332 * 225.75, 2))
        self.assertEqual(pf["positions"][0]["shares"], 332)

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: placed", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("1 placed today", text)
        self.assertIn("max loss Rs 212.48", text)

        res = check.check([ongc()], {"budget": 100000}, state=st, now=check.parse_now("2026-10-23 10:30"))
        self.assertIn("already placed today", " ".join(res["results"][0]["reasons"]))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["loss_at_risk"]), (1, 212.48))

    def test_status_words_are_placed_skipped_failed(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                run(self.args("result", "--index", "1", "--status", "denied"))
        code, _, _ = run(self.args("result", "--index", "1", "--status", "skipped", "--note", "price ran away"))
        self.assertEqual(code, 0)
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "skipped")

    def test_flatten_by_the_close(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "abc",
                      "--filled-qty", "332", "--filled-price", "225.75", "--portfolio", self.pf))
        late = "2026-10-23 15:20"
        code, text, _ = run(["flatten", "--symbol", "ONGC", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", self.plan, "--config", self.cfg, "--now", late])
        self.assertEqual(code, 0)
        self.assertIn("SELL 332 shares of ONGC, market order, intraday (MIS), good for today (flat by 15:15 IST)", text)
        self.assertIn("replaced 1 earlier proposal", text)
        self.assertIn("square", text)
        with open(self.orders) as fh:
            doc = json.load(fh)
        self.assertEqual(ticket.ticket_fields(doc["orders"][0]),
                         {"symbol": "ONGC", "exchange": "NSE", "side": "sell", "qty": "332", "type": "market",
                          "product": "INTRADAY", "time_in_force": "day"})
        self.assertEqual(self.run_risk(now=late), 0)       # after the 14:30 entry cutoff: an exit still passes
        with open(self.risk) as fh:
            self.assertEqual(json.load(fh)["results"][0]["kind"], "exit")
        code, text, _ = run(self.args("show", now=late))
        self.assertEqual(code, 0)
        self.assertIn("READY", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes sell 332 ONGC", now=late))[0], 0)
        code, _, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "332",
                                   "--filled-price", "226.40", "--portfolio", self.pf, now="2026-10-23 15:21"))
        self.assertEqual(code, 0)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["positions"], [])
        self.assertEqual(pf["cash"], round(100000 + 332 * (226.40 - 225.75), 2))
        code, _, err = run(["flatten", "--symbol", "ONGC", "--orders", self.orders, "--portfolio", self.pf,
                            "--now", late])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_flatten_with_the_broker_qty(self):
        code, text, _ = run(["flatten", "--symbol", "ongc", "--qty", "332", "--orders", self.orders,
                             "--portfolio", self.none, "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("(flat by 15:15 IST)", text)
        self.assertNotIn("Check it against your broker's positions page", text)
        self.assertEqual(run(["flatten", "--symbol", "ONGC", "--qty", "0", "--orders", self.orders])[0], 1)

    def test_an_edited_order_is_not_ready(self):
        self.run_risk()
        write(self.orders, {"orders": [ongc(qty="333")]})
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_yesterdays_check_does_not_count(self):
        self.run_risk(now="2026-10-22 10:15")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)

    def test_blocked_orders_are_never_ready(self):
        self.assertEqual(self.run_risk(now="2026-10-23 09:20"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-23 09:20"))
        self.assertIn("BLOCKED by trader-risk: window: no new entries before 09:30", text)
        code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-23 09:20"))
        self.assertEqual(code, 1)

    def test_a_holiday_blocks_the_ticket(self):
        self.assertEqual(self.run_risk(now="2026-10-20 10:15"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-20 10:15"))
        self.assertIn("NSE holiday", text)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.run_risk()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded", err)
        code, text, _ = run(self.args("decline", "--index", "1", "--reply", "no thanks"))
        self.assertEqual(code, 0)
        self.assertIn("Nothing was placed", text)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: declined", text)

    def test_a_live_config_says_you_place_it(self):
        with open(self.cfg) as fh:
            c = json.load(fh)
        c["paper"] = False
        c["broker"] = "zerodha"
        write(self.cfg, c)
        self.run_risk()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("LIVE: you place this yourself, REAL MONEY", text)
        self.assertIn("Zerodha", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertIn("Place it yourself", text)

    def test_fill_sell_relieves_the_oldest_lot(self):
        pf = {"cash": 0, "positions": [{"ticker": "ITC", "shares": 30, "lots": [
            {"id": "ITC-1", "date": "2026-01-02", "shares": 20, "cost": 8000.0},
            {"id": "ITC-2", "date": "2026-02-02", "shares": 10, "cost": 4200.0}]}]}
        write(self.pf, pf)
        code, text, _ = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "25", "--price", "420",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 10500.0)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["ITC-2"])
        self.assertEqual(lots[0]["shares"], 5)
        self.assertEqual(lots[0]["cost"], 2100.0)
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "filled")
        code, _, err = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "50", "--price", "420",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


class NoNetwork(unittest.TestCase):
    def test_the_script_cannot_reach_a_broker(self):
        src = open(os.path.join(ROOT, "trader-broker", "ticket.py"), encoding="utf-8").read()
        for word in ("urllib", "socket", "http.client", "requests", "subprocess", "API_KEY", "SECRET"):
            self.assertNotIn(word, src, word)

    def test_skill_md_says_it_never_places_orders(self):
        text = open(os.path.join(ROOT, "trader-broker", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("Never places an order", text)
        for gone in ("Alpaca", "place_stock_order", "Allow once", "Interactive Brokers", "dollar"):
            self.assertNotIn(gone, text)
        self.assertNotIn("—", text)


if __name__ == "__main__":
    unittest.main()
