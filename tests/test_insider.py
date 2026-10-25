"""trader-insider tests (NSE): bulk and block deals and SEBI insider disclosures, from saved real answers."""

import datetime
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

ins = load("trader-insider", "insider")
DIR = os.path.join(FIX, "trader-insider")
NOW = "2026-10-25 12:00"          # IST


def read(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ins.main(args)
    return code, out.getvalue(), err.getvalue()


class Units(unittest.TestCase):
    def test_money(self):
        self.assertEqual(ins.indian(12345678), "1,23,45,678")
        self.assertEqual(ins.rupees(1189990871), "Rs 119.00 crore")
        self.assertEqual(ins.rupees(3294168), "Rs 32.94 lakh")
        self.assertEqual(ins.rupees(45678), "Rs 45,678")
        self.assertEqual(ins.rupees(None), "n/a")
        self.assertEqual(ins.rupees(-2.5e7), "-Rs 2.50 crore")

    def test_dates(self):
        self.assertEqual(ins.parse_nse_date("23-OCT-2026"), datetime.date(2026, 10, 23))
        self.assertEqual(ins.parse_nse_date("18-Feb-2026 19:06"), datetime.date(2026, 2, 18))
        self.assertEqual(ins.parse_nse_date("04-Aug-2026 20:03:37"), datetime.date(2026, 8, 4))
        self.assertIsNone(ins.parse_nse_date("soon"))
        self.assertEqual(ins.parse_now(NOW), datetime.datetime(2026, 10, 25, 12, 0))
        self.assertEqual(ins.parse_now("2026-10-25T06:30:00Z"), datetime.datetime(2026, 10, 25, 12, 0))
        with self.assertRaises(ins.InsiderError):
            ins.parse_now("tomorrow")

    def test_weekdays_between(self):
        d = datetime.date
        self.assertEqual(ins.weekdays_between(d(2026, 2, 13), d(2026, 2, 18)), 3)    # Fri -> Wed
        self.assertEqual(ins.weekdays_between(d(2026, 2, 2), d(2026, 2, 9)), 5)      # Mon -> next Mon
        self.assertEqual(ins.weekdays_between(d(2026, 2, 24), d(2026, 2, 24)), 0)
        self.assertEqual(ins.weekdays_between(d(2026, 2, 14), d(2026, 2, 16)), 1)    # Sat -> Mon

    def test_names(self):
        self.assertTrue(ins.name_match("mirae", "MIRAE ASSET MUTUAL FUND"))
        self.assertTrue(ins.name_match("hdfc mutual", "HDFC MUTUAL FUND"))
        self.assertTrue(ins.name_match("Mutual Fund hdfc", "HDFC MUTUAL FUND"))
        self.assertFalse(ins.name_match("hdfc bank", "HDFC MUTUAL FUND"))
        self.assertFalse(ins.name_match("", "HDFC MUTUAL FUND"))


class Deals(unittest.TestCase):
    def test_the_bulk_file(self):
        rows = ins.parse_deals(read("bulk.csv"), "bulk")
        self.assertEqual(len(rows), 118)
        first = rows[0]
        self.assertEqual((first["date"], first["symbol"], first["client"], first["side"], first["qty"], first["price"]),
                         ("2026-10-23", "BONFIG", "CATAMARAN VENTURES LLP", "BUY", 1045000, 581.35))
        self.assertEqual(first["name"], "Bonfiglioli Trans Limited")
        self.assertTrue(first["id"].startswith("deal:"))
        self.assertEqual(len({r["id"] for r in rows}), 118)

    def test_an_empty_block_day(self):
        self.assertEqual(ins.parse_deals(read("block.csv"), "block"), [])

    def test_block_deals_without_remarks(self):
        text = ("Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price\n"
                "23-OCT-2026,XYZ,Xyz Limited,SOME  FUND,BUY,\"5,00,000\",120.50\n")
        rows = ins.parse_deals(text, "block")           # a synthetic file in NSE's block.csv layout
        self.assertEqual((rows[0]["client"], rows[0]["qty"], rows[0]["price"], rows[0]["kind"]), ("SOME FUND", 500000, 120.5, "block"))

    def test_a_page_that_is_not_the_csv(self):
        with self.assertRaises(ins.InsiderError):
            ins.parse_deals("<html>Access Denied</html>", "bulk")
        with self.assertRaises(ins.InsiderError):
            ins.parse_deals("", "bulk")

    def test_grouping_one_client_one_side(self):
        groups = ins.group_deals(ins.parse_deals(read("bulk.csv"), "bulk"))
        self.assertEqual(len(groups), 115)
        self.assertEqual(groups, sorted(groups, key=lambda g: (-g["value"], g["symbol"])))
        cat = next(g for g in groups if g["symbol"] == "BONFIG" and g["client"] == "CATAMARAN VENTURES LLP")
        self.assertEqual((cat["side"], cat["qty"], cat["rows"], cat["price"]), ("BUY", 2134318, 2, 557.55))
        self.assertEqual(round(cat["value"]), round(1045000 * 581.35 + 1089318 * 534.72))
        self.assertFalse(cat["both_sides"])

    def test_a_trading_firm_is_marked(self):
        groups = ins.group_deals(ins.parse_deals(read("bulk.csv"), "bulk"))
        sw = [g for g in groups if g["symbol"] == "SWIGGY"]
        self.assertEqual(sorted(g["side"] for g in sw), ["BUY", "SELL"])
        self.assertTrue(all(g["both_sides"] for g in sw))
        self.assertIn("same client on both sides that day: a trading firm, not a view", ins.render_deal(sw[0]))
        hdfc = next(g for g in groups if g["client"] == "HDFC MUTUAL FUND")
        self.assertFalse(hdfc["both_sides"])

    def test_filters(self):
        groups = ins.group_deals(ins.parse_deals(read("bulk.csv"), "bulk"))
        mf = ins.filter_deals(groups, who=["mutual fund"])
        self.assertEqual(sorted(g["client"] for g in mf), ["HDFC MUTUAL FUND", "INVESCO MUTUAL FUND"])
        self.assertEqual([g["symbol"] for g in ins.filter_deals(groups, symbols={"TONBO"}, who=["hdfc mutual"])], ["TONBO"])
        self.assertEqual(ins.filter_deals(groups, symbols={"CYIENT"}, who=["hdfc mutual"]), [])             # both must match
        either = ins.filter_deals(groups, symbols={"CYIENT"}, who=["hdfc mutual"], either=True)
        self.assertEqual(len(either), 7)                                                                  # 6 CYIENT + HDFC
        self.assertEqual(len(ins.filter_deals(groups, min_value=5e7)), 83)
        held = ins.filter_deals(groups, symbols={"CYIENT"}, held={"CYIENT"})
        self.assertTrue(all(g["held"] for g in held))

    def test_render(self):
        groups = ins.group_deals(ins.parse_deals(read("bulk.csv"), "bulk"))
        hdfc = next(g for g in groups if g["client"] == "HDFC MUTUAL FUND")
        self.assertEqual(ins.render_deal(hdfc),
                         "  BULK  2026-10-23  TONBO      BUY  HDFC MUTUAL FUND: 10,00,000 shares at Rs 1177.32 = Rs 117.73 crore, 2 rows")
        cat = next(g for g in groups if g["client"] == "CATAMARAN VENTURES LLP")
        self.assertIn(", 2 rows", ins.render_deal(cat))


class Insiders(unittest.TestCase):
    def test_reliance_filings(self):
        rows = ins.parse_pit(read("pit-RELIANCE.json"), "RELIANCE")
        self.assertEqual(len(rows), 4)
        first = rows[0]
        self.assertEqual((first["person"], first["category"], first["type"], first["mode"]),
                         ("BALANADU NARAYAN", "Other", "Sell", "Off Market"))
        self.assertEqual((first["quantity"], first["value"], first["trade_to"], first["filed"]), (2320.0, 3294168.0, "2026-02-13", "2026-02-18"))
        self.assertEqual((first["weekdays_to_file"], first["late"]), (3, False))
        self.assertTrue(first["link"].endswith("IT_1194033_1627626_18022026070637_WEB.xml"))
        late = rows[2]
        self.assertEqual((late["person"], late["weekdays_to_file"], late["late"]), ("ASHWINI DARSHAN NAPHADE", 5, True))
        self.assertEqual([ins.buy_or_sell(r) for r in rows], ["sell", "buy", "buy", "other"])
        self.assertEqual(rows[3]["type"], "Pledge Revoke")

    def test_render(self):
        rows = ins.parse_pit(read("pit-RELIANCE.json"), "RELIANCE")
        self.assertEqual(ins.render_insider(rows[0]),
                         "  RELIANCE   SELL  BALANADU NARAYAN (Other): Off Market 2,320 shares, Rs 32.94 lakh, 2026-02-13, "
                         "filed 3 weekdays after the trade")
        self.assertIn("(LATE: the rules allow 2 + 2 trading days)", ins.render_insider(rows[2]))
        self.assertTrue(ins.render_insider(rows[0], held=True).startswith("! "))
        self.assertIn("YOU HOLD IT", ins.render_insider(rows[0], held=True))

    def test_holding_change_and_a_date_range(self):
        rows = ins.parse_pit(read("pit-LALPATHLAB.json"), "LALPATHLAB")
        self.assertEqual(len(rows), 7)
        director = next(r for r in rows if r["person"] == "Rahul Sharma" and r["type"] == "Sell")
        self.assertIn("holding 0.02% -> 0.02%", ins.render_insider(director))
        ranged = next(r for r in rows if r["trade_from"] != r["trade_to"])
        self.assertIn("%s to %s" % (ranged["trade_from"], ranged["trade_to"]), ins.render_insider(ranged))

    def test_an_empty_answer_and_a_blocked_one(self):
        self.assertEqual(ins.parse_pit(read("pit-ONGC.json"), "ONGC"), [])
        with self.assertRaises(ins.InsiderError):
            ins.parse_pit(b"<html>Access Denied</html>", "ONGC")
        with self.assertRaises(ins.InsiderError):
            ins.parse_pit(b'{"error": "x"}', "ONGC")

    def test_the_command(self):
        code, out, err = run(["insiders", "RELIANCE", "LALPATHLAB", "--days", "400", "--now", NOW, "--offline", DIR])
        self.assertEqual((code, err), (0, ""))
        lines = [l for l in out.splitlines() if l[2:12].strip() in ("RELIANCE", "LALPATHLAB")]
        self.assertEqual(len(lines), 9)                    # 3 + 6 buys and sells; the pledges are hidden
        self.assertNotIn("PLEDGE", out.upper().replace("PLEDGES", ""))
        self.assertIn("Not investment advice", out)
        code, out, _ = run(["insiders", "RELIANCE", "LALPATHLAB", "--days", "400", "--all-types", "--now", NOW, "--offline", DIR])
        self.assertEqual(len([l for l in out.splitlines() if l[2:12].strip() in ("RELIANCE", "LALPATHLAB")]), 11)
        self.assertIn("PLEDGE REVOKE", out)
        code, out, _ = run(["insiders", "LALPATHLAB", "--who", "rahul sharma", "--days", "400", "--now", NOW, "--offline", DIR])
        self.assertEqual(len([l for l in out.splitlines() if "Rahul Sharma" in l]), 2)

    def test_an_empty_answer_is_not_proof(self):
        code, out, _ = run(["insiders", "ONGC", "--days", "400", "--now", NOW, "--offline", DIR])
        self.assertEqual(code, 1)
        self.assertIn("No insider buys or sells filed in the last 400 days for ONGC.", out)
        self.assertIn("not proof that nobody traded", out)

    def test_holdings_are_marked(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            with open(pf, "w") as fh:
                json.dump({"cash": 0, "positions": [{"ticker": "RELIANCE", "shares": 10}]}, fh)
            code, out, _ = run(["insiders", "RELIANCE", "--days", "400", "--now", NOW, "--offline", DIR, "--portfolio", pf])
            self.assertIn("! RELIANCE", out)
            self.assertIn("YOU HOLD IT", out)

    def test_it_needs_stocks(self):
        code, _, err = run(["insiders", "--offline", DIR, "--portfolio", "/nonexistent", "--watchlist", "/nonexistent",
                            "--plan", "/nonexistent"])
        self.assertEqual(code, 2)
        self.assertIn("which stocks?", err)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "insider.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_deals_default_view(self):
        code, out, _ = run(["deals", "--offline", DIR, "--limit", "3"])
        self.assertEqual(code, 0)
        self.assertIn("NSE bulk and block deals for 2026-10-23.", out)
        self.assertIn("SWIGGY     SELL GRAVITON RESEARCH CAPITAL LLP: 1,44,04,126 shares at Rs 184.21 = Rs 265.34 crore", out)
        self.assertIn("... and 112 more", out)
        self.assertIn("a bulk deal is public the evening it happens", out)

    def test_deals_filters(self):
        code, out, _ = run(["deals", "--offline", DIR, "--who", "hdfc mutual"])
        self.assertIn("(filtered: 1 of 115 lines)", out)
        self.assertIn("TONBO      BUY  HDFC MUTUAL FUND", out)
        code, out, _ = run(["deals", "--offline", DIR, "--symbols", "CYIENT", "--who", "hdfc mutual"])
        self.assertEqual(code, 1)
        self.assertIn("No deals match.", out)
        code, out, _ = run(["deals", "--offline", DIR, "--min-cr", "100", "--kind", "bulk", "--json"])
        doc = json.loads(out)
        self.assertEqual(doc["dates"], ["2026-10-23"])
        self.assertTrue(all(d["value"] >= 1e9 for d in doc["deals"]))
        code, out, _ = run(["deals", "--offline", DIR, "--rows", "--symbols", "BONFIG"])
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("  BULK")]), 5)    # one line per trade row

    def test_a_held_stock_is_marked(self):
        pf = os.path.join(self.tmp.name, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "TONBO", "shares": 1}]}, fh)
        code, out, _ = run(["deals", "--offline", DIR, "--symbols", "TONBO", "--portfolio", pf])
        self.assertIn("! BULK", out)
        self.assertIn("YOU HOLD IT", out)

    def test_new_shows_each_deal_once(self):
        base = ["new", "--who", "mutual fund", "--symbols", "CYIENT", "--days", "400", "--now", NOW, "--offline", DIR,
                "--state", self.state, "--portfolio", "/nonexistent"]
        code, out, _ = run(base)
        self.assertEqual(code, 0)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("NEW ")]), 8)     # 2 funds + 6 CYIENT lines, no insiders
        self.assertIn("NEW BULK  2026-10-23  SMT", out)
        groups = ins.filter_deals(ins.group_deals(ins.parse_deals(read("bulk.csv"), "bulk")), {"CYIENT"}, ["mutual fund"],
                                  either=True)
        with open(self.state) as fh:
            self.assertEqual(len(json.load(fh)["seen"]), sum(len(g["ids"]) for g in groups))     # every row behind a line
        code, out, _ = run(base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)

    def test_new_with_insiders_and_a_dry_run(self):
        base = ["new", "--symbols", "RELIANCE", "--days", "400", "--now", NOW, "--offline", DIR, "--state", self.state,
                "--portfolio", "/nonexistent"]
        code, out, _ = run(base + ["--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("NEW RELIANCE")]), 3)
        self.assertFalse(os.path.exists(self.state))
        run(base)
        self.assertEqual(run(base)[0], 1)

    def test_new_needs_something_to_watch(self):
        code, _, err = run(["new", "--offline", DIR, "--state", self.state, "--portfolio", "/nonexistent",
                            "--watchlist", "/nonexistent", "--plan", "/nonexistent"])
        self.assertEqual(code, 2)
        self.assertIn("what to watch?", err)

    def test_state_forgets_after_60_days(self):
        state = {"seen": {"old": "2026-07-01T00:00:00"}}
        ins.remember(state, ["fresh"], datetime.datetime(2026, 10, 25, 12, 0))
        self.assertEqual(sorted(state["seen"]), ["fresh"])
        self.assertEqual(state["last_check"], "2026-10-25T12:00:00")

    def test_nothing_american(self):
        code, out, _ = run(["deals", "--offline", DIR, "--limit", "5"])
        for gone in ("$", "SEC ", "Congress", "STOCK Act", chr(0x2014)):
            self.assertNotIn(gone, out)


class FakeResponse(object):
    def __init__(self, body):
        self.body, self.headers = body, {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = ins.urllib.request.urlopen
        self.calls = []

    def tearDown(self):
        ins.urllib.request.urlopen = self.saved

    def fake(self, answers):
        def urlopen(req, timeout=None):
            self.calls.append((req.full_url, req.get_header("User-agent")))
            a = answers.pop(0)
            if isinstance(a, int):
                raise urllib.error.HTTPError(req.full_url, a, "x", {}, io.BytesIO(b""))
            return FakeResponse(a)
        ins.urllib.request.urlopen = urlopen

    def test_the_urls_and_the_user_agent(self):
        self.fake([read("bulk.csv"), read("block.csv"), read("pit-RELIANCE.json")])
        rows = ins.collect_deals(ins.Fetcher(pause=0))
        self.assertEqual(len(rows), 118)
        found = ins.collect_insiders(ins.Fetcher(pause=0), ["RELIANCE"], ins.parse_now(NOW), 30)
        self.assertEqual(len(found), 4)
        urls = [u for u, _ in self.calls]
        self.assertEqual(urls, ["https://nsearchives.nseindia.com/content/equities/bulk.csv",
                                "https://nsearchives.nseindia.com/content/equities/block.csv",
                                "https://www.nseindia.com/api/corporates-pit?index=equities&symbol=RELIANCE"
                                "&from_date=25-09-2026&to_date=25-10-2026"])
        self.assertEqual({ua for _, ua in self.calls}, {"Mozilla/5.0"})

    def test_a_blocked_file_is_a_note_and_the_other_still_reads(self):
        self.fake([403, read("block.csv")])
        errors = []
        rows = ins.collect_deals(ins.Fetcher(pause=0), errors=errors)
        self.assertEqual(rows, [])
        self.assertEqual(errors, ["bulk deals: HTTP 403 from nsearchives.nseindia.com"])

    def test_html_instead_of_the_csv_is_named(self):
        self.fake([b"<html>Access Denied</html>", read("block.csv")])
        errors = []
        ins.collect_deals(ins.Fetcher(pause=0), errors=errors)
        self.assertIn("not what NSE usually sends (blocked or changed?)", errors[0])


if __name__ == "__main__":
    unittest.main()
