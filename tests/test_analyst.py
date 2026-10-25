"""trader-analyst tests (NSE): Yahoo quarterly figures with data-quality checks, NSE results filings, Indian units."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load

facts = load("trader-analyst", "facts")
DIR = os.path.join(FIX, "trader-analyst")
NOW = "2026-10-25 12:00"          # IST


def raw(name):
    with open(os.path.join(DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = facts.main(args)
    return code, out.getvalue(), err.getvalue()


def make_doc(**series):
    """A Yahoo timeseries answer from {metric: {end: value}}."""
    result = []
    for key, concept in facts.CONCEPTS.items():
        rows = [{"asOfDate": e, "periodType": "3M", "currencyCode": series.get("currency", "INR"),
                 "reportedValue": {"raw": v, "fmt": str(v)}} for e, v in sorted(series.get(key, {}).items())]
        result.append({"meta": {"symbol": ["X.NS"], "type": [concept]}, concept: rows or None})
    return {"timeseries": {"result": result, "error": None}}


class Units(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(facts.indian(999), "999")
        self.assertEqual(facts.indian(1000), "1,000")
        self.assertEqual(facts.indian(100000), "1,00,000")
        self.assertEqual(facts.indian(12345678), "1,23,45,678")
        self.assertEqual(facts.indian(-204987), "-2,04,987")
        self.assertEqual(facts.cr(2049873500000.0), "2,04,987")
        self.assertEqual(facts.cr(None), "-")
        self.assertEqual(facts.cr_text(118989300000.0), "Rs 11,899 crore")
        self.assertEqual(facts.cr_text(None), "n/a")

    def test_fiscal_quarters_run_april_to_march(self):
        self.assertEqual(facts.fiscal_label("2026-06-30"), "Q1 FY27")
        self.assertEqual(facts.fiscal_label("2026-09-30"), "Q2 FY27")
        self.assertEqual(facts.fiscal_label("2026-12-31"), "Q3 FY27")
        self.assertEqual(facts.fiscal_label("2026-03-31"), "Q4 FY26")
        self.assertEqual(facts.fiscal_label("2026-05-31"), "")

    def test_month_ends(self):
        self.assertEqual(facts.month_end_before("2026-06-30"), datetime.date(2026, 3, 31))
        self.assertEqual(facts.month_end_before("2026-03-31"), datetime.date(2025, 12, 31))
        self.assertEqual(facts.month_end_before("2026-12-31"), datetime.date(2026, 9, 30))
        self.assertEqual(facts.month_end_before("2026-09-30", 12), datetime.date(2025, 9, 30))

    def test_clock_and_nse_dates(self):
        self.assertEqual(facts.parse_now(NOW), datetime.datetime(2026, 10, 25, 12, 0))
        self.assertEqual(facts.parse_now("2026-10-25T06:30:00Z"), datetime.datetime(2026, 10, 25, 12, 0))
        with self.assertRaises(facts.FactsError):
            facts.parse_now("tomorrow")
        self.assertEqual(facts.parse_nse_date("04-Aug-2026 20:03:37"), datetime.date(2026, 8, 4))
        self.assertEqual(facts.parse_nse_date("05-Nov-2026"), datetime.date(2026, 11, 5))
        self.assertIsNone(facts.parse_nse_date("soon"))

    def test_urls(self):
        now = facts.parse_now(NOW)
        url = facts.ts_url("ongc", now)
        self.assertTrue(url.startswith("https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/ONGC.NS?"
                                       "type=quarterlyTotalRevenue,quarterlyNetIncome,quarterlyPretaxIncome,quarterlyEBITDA,"
                                       "quarterlyDilutedEPS&period1="))
        self.assertTrue(url.endswith("&period2=%d&merge=false&padTimeSeries=true"
                                     % int(datetime.datetime(2026, 10, 25, 6, 30, tzinfo=datetime.timezone.utc).timestamp())))
        self.assertIn("//query2.finance.yahoo.com/", facts.ts_url("ONGC", now, "query2"))
        self.assertIn("/M%26M.NS?", facts.ts_url("M&M", now))
        self.assertEqual(facts.yahoo_symbol("NIFTY"), "^NSEI")


class Quarters(unittest.TestCase):
    def test_ongc_latest_quarter(self):
        res = facts.analyse(raw("yf-ts-ONGC.json"), "ONGC", name="Oil & Natural Gas Corporation")
        self.assertEqual(len(res["rows"]), 5)
        cur = res["rows"][0]
        self.assertEqual((cur["end"], cur["fiscal"]), ("2026-06-30", "Q1 FY27"))
        self.assertEqual((cur["revenue"], cur["net_income"], cur["eps_diluted"]), (2049873500000.0, 118989300000.0, 9.46))
        self.assertEqual((cur["revenue_yoy"], cur["net_income_yoy"], cur["eps_yoy"]), (25.7, 21.4, 21.4))
        self.assertEqual((cur["ebitda_margin"], cur["pretax_margin"], cur["net_margin"]), (4.8, 3.4, 5.8))
        self.assertEqual(res["ttm"]["revenue"], 1579110800000.0 + 1674229300000.0 + 1738051900000.0 + 2049873500000.0)
        self.assertEqual(res["ttm"]["eps_diluted"], 34.6)
        self.assertEqual((res["dropped"], res["missing_quarters"], res["ttm_note"], res["currency"]), ([], [], None, "INR"))
        self.assertEqual(facts.data_notes(res), [])
        self.assertNotIn("gross_margin", cur)
        self.assertNotIn("operating_margin", cur)

    def test_what_changed(self):
        lines = facts.changes(facts.analyse(raw("yf-ts-ONGC.json"), "ONGC"))
        self.assertEqual(lines[0], "Quarter ending 2026-06-30 (Q1 FY27): revenue Rs 2,04,987 crore (+25.7% vs a year earlier), "
                                   "net income Rs 11,899 crore (+21.4%), diluted EPS Rs 9.46 (+21.4%).")
        self.assertIn("Net margin 5.8% (-0.4 points vs the quarter ending 2026-03-31).", lines)
        self.assertTrue(any(l.startswith("Net income is above pre-tax income") for l in lines))
        self.assertTrue(any("Revenue +17.9% vs the quarter ending 2026-03-31" in l for l in lines))
        self.assertTrue(lines[-1].startswith("Yahoo does not say whether these figures are consolidated or standalone"))

    def test_a_missing_quarter_is_named_and_the_total_is_skipped(self):
        res = facts.analyse(raw("yf-ts-RELIANCE.json"), "RELIANCE")
        self.assertEqual(res["missing_quarters"], ["2025-09-30"])
        self.assertEqual(res["ttm"], {})
        self.assertIn("skipped for revenue, net income, eps diluted", res["ttm_note"])
        notes = facts.data_notes(res)
        self.assertEqual(notes[0], "Yahoo has no figures for the quarter ending 2025-09-30.")
        # the quarter a year back is still matched for growth
        self.assertEqual(res["rows"][0]["revenue_yoy"], 27.0)

    def test_a_duplicated_quarter_is_dropped(self):
        res = facts.analyse(raw("yf-ts-ITC.json"), "ITC")
        self.assertEqual(res["dropped"], [{"end": "2025-12-31", "same_as": "2025-03-31"}])
        self.assertNotIn("2025-12-31", [r["end"] for r in res["rows"]])
        self.assertEqual(res["missing_quarters"], ["2025-09-30", "2025-12-31"])
        notes = facts.data_notes(res)
        self.assertTrue(notes[0].startswith("Dropped the quarter ending 2025-12-31: its revenue and net income are identical "
                                            "to the quarter ending 2025-03-31"))
        self.assertEqual(res["rows"][0]["revenue_yoy"], -11.1)

    def test_a_complete_year_gives_the_total(self):
        res = facts.analyse(raw("yf-ts-TCS.json"), "TCS")
        self.assertEqual(res["missing_quarters"], ["2025-09-30"])
        self.assertEqual(sorted(res["ttm"]), ["eps_diluted", "net_income", "revenue"])
        self.assertEqual(res["ttm"]["eps_diluted"], 142.66)
        self.assertEqual(res["rows"][0]["fiscal"], "Q2 FY27")

    def test_a_bank_has_no_ebitda(self):
        res = facts.analyse(raw("yf-ts-SBIN.json"), "SBIN")
        self.assertIsNone(res["rows"][0]["ebitda_margin"])
        self.assertEqual(res["rows"][0]["pretax_margin"], 33.6)
        self.assertNotIn("EBITDA", "\n".join(facts.changes(res)))

    def test_nothing_for_an_unknown_symbol(self):
        with self.assertRaises(facts.FactsError) as ctx:
            facts.analyse(raw("yf-ts-ZZZZ.json"), "ZZZZ")
        self.assertIn("no quarterly revenue or net income for ZZZZ", str(ctx.exception))
        with self.assertRaises(facts.FactsError):
            facts.analyse({"finance": {"error": "blocked"}}, "ONGC")

    def test_other_currency_and_synthetic_series(self):
        doc = make_doc(currency="USD", revenue={"2026-03-31": 100.0, "2026-06-30": 120.0, "2025-06-30": 90.0},
                       net_income={"2026-03-31": 10.0, "2026-06-30": 15.0, "2025-06-30": 9.0})
        res = facts.analyse(doc, "X")
        self.assertIn("in USD, not rupees", facts.data_notes(res)[0])
        self.assertEqual(res["rows"][0]["revenue_yoy"], 33.3)

    def test_missing_quarters_function(self):
        self.assertEqual(facts.missing_quarters(["2026-03-31"]), [])
        self.assertEqual(facts.missing_quarters(["2025-06-30", "2026-06-30"]), ["2025-09-30", "2025-12-31", "2026-03-31"])
        self.assertEqual(facts.missing_quarters(["2025-12-31", "2026-03-31", "2026-06-30"]), [])


class Filings(unittest.TestCase):
    def test_ongc_results_press_release_and_call(self):
        f = facts.latest_filings(raw("nse-ann-ONGC.json"))
        self.assertEqual(f["results"]["date"], "2026-08-04")
        self.assertTrue(f["results"]["link"].endswith("ONGC_04082026200149_finalfinancials04082026.pdf"))
        self.assertEqual(f["press_release"]["date"], "2026-08-04")
        self.assertTrue(f["press_release"]["link"].endswith("PressreleaseQ1FY27.pdf"))
        self.assertEqual(f["transcript"]["date"], "2026-08-12")
        self.assertEqual(f["recording"]["date"], "2026-08-05")

    def test_the_press_release_must_come_with_the_results(self):
        # ONGC's 21 September press release (a gas find) is not the results press release
        f = facts.latest_filings(raw("nse-ann-ONGC.json"))
        self.assertNotEqual(f["press_release"]["date"], "2026-09-21")

    def test_tcs_results(self):
        f = facts.latest_filings(raw("nse-ann-TCS.json"))
        self.assertEqual((f["results"]["date"], f["press_release"]["date"], f["transcript"]["date"]),
                         ("2026-10-08", "2026-10-08", "2026-10-14"))

    def test_no_results_filing(self):
        self.assertEqual(facts.latest_filings([]), {"results": None, "press_release": None, "transcript": None,
                                                    "recording": None})

    def test_expected_quarter_end(self):
        self.assertEqual(facts.expected_quarter_end("2026-08-04"), datetime.date(2026, 6, 30))
        self.assertEqual(facts.expected_quarter_end("2026-05-26"), datetime.date(2026, 3, 31))
        self.assertEqual(facts.expected_quarter_end("2026-10-08"), datetime.date(2026, 9, 30))

    def test_stale_figures_are_called_out(self):
        res = facts.analyse(raw("yf-ts-ONGC.json"), "ONGC")
        filings = facts.latest_filings(raw("nse-ann-ONGC.json"))
        self.assertIsNone(facts.stale_note(res, filings))
        old = dict(res, rows=[dict(r) for r in res["rows"][1:]])          # pretend Yahoo stops at 2026-03-31
        note = facts.stale_note(old, filings)
        self.assertIn("Yahoo's figures stop at 2026-03-31", note)
        self.assertIn("results filed on 2026-08-04 for the quarter ending about 2026-06-30", note)
        self.assertIn("finalfinancials04082026.pdf", note)
        self.assertIsNone(facts.stale_note(res, {"results": None}))

    def test_next_results_date(self):
        now = facts.parse_now(NOW)
        f = facts.Fetcher(offline=DIR)
        errors = []
        nxt = facts.next_results(f, "ONGC", now, errors)
        self.assertEqual((nxt["date"], nxt["days"], nxt["purpose"]), ("2026-11-05", 11, "Financial Results/Dividend"))
        self.assertIsNone(facts.next_results(f, "TCS", now, errors))
        self.assertEqual(errors, [])
        facts.next_results(f, "NOPE", now, errors)
        self.assertIn("NSE event calendar for NOPE: offline: no file", errors[0])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_ongc_report(self):
        code, out, err = run(["ONGC", "--offline", DIR, "--now", NOW])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Oil & Natural Gas Corporation (ONGC). Rs crore except EPS.", out)
        self.assertRegex(out, r"2026-06-30\s+Q1 FY27\s+2,04,987\s+\+25\.7%\s+4\.8%\s+3\.4%\s+5\.8%\s+11,899\s+9\.46\s+\+21\.4%")
        self.assertIn("Last 4 quarters: revenue Rs 7,04,127 crore, net income Rs 43,519 crore, diluted EPS Rs 34.60.", out)
        self.assertIn("Latest results filing (NSE): filed 2026-08-04  https://nsearchives.nseindia.com/corporate/ONGC_", out)
        self.assertIn("Press release: filed 2026-08-04", out)
        self.assertIn("Earnings call transcript: filed 2026-08-12", out)
        self.assertIn("Next board meeting for results: 05-Nov-2026 (in 11 days), Financial Results/Dividend.", out)
        for gone in ("$", "SEC", "10-Q", "8-K", "US$", chr(0x2014)):
            self.assertNotIn(gone, out)

    def test_data_problems_are_printed_as_notes(self):
        code, out, _ = run(["ITC", "--offline", DIR, "--now", NOW])
        self.assertIn("NOTE: Dropped the quarter ending 2025-12-31", out)
        self.assertIn("NOTE: Yahoo has no figures for the quarters ending 2025-09-30, 2025-12-31.", out)
        self.assertIn("Next board meeting for results: 02-Nov-2026 (in 8 days)", out)

    def test_a_missing_filing_source_is_a_note_not_a_crash(self):
        code, out, err = run(["RELIANCE", "--offline", DIR, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("NOTE: NSE announcements for RELIANCE: offline: no file", out)
        self.assertRegex(out, r"2026-06-30\s+Q1 FY27")

    def test_no_filings(self):
        code, out, _ = run(["SBIN", "--no-filings", "--offline", DIR, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertNotIn("Latest results filing", out)
        self.assertNotIn("NOTE: NSE", out)

    def test_json(self):
        code, out, _ = run(["ONGC", "--json", "--offline", DIR, "--now", NOW])
        doc = json.loads(out)
        self.assertEqual(doc[0]["symbol"], "ONGC")
        self.assertEqual(doc[0]["filings"]["results"]["date"], "2026-08-04")
        self.assertEqual(doc[0]["next_results"]["date"], "2026-11-05")
        self.assertEqual(doc[0]["result"]["rows"][0]["fiscal"], "Q1 FY27")
        self.assertIsNone(doc[0]["stale"])

    def test_metrics_file_merges_rows(self):
        path = os.path.join(self.tmp.name, "metrics", "metrics.json")
        run(["ONGC", "--no-filings", "--metrics", path, "--offline", DIR, "--now", NOW])
        code, out, _ = run(["TCS", "--no-filings", "--metrics", path, "--offline", DIR, "--now", NOW])
        self.assertIn("wrote %s (2 companies)" % path, out)
        with open(path) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["as_of"], "2026-10-25")
        self.assertEqual([c["symbol"] for c in doc["companies"]], ["ONGC", "TCS"])
        ongc = doc["companies"][0]
        self.assertEqual((ongc["period_end"], ongc["fiscal"], ongc["revenue_growth_yoy"], ongc["eps_ttm"], ongc["net_margin"]),
                         ("2026-06-30", "Q1 FY27", 25.7, 34.6, 5.8))
        self.assertEqual((ongc["currency"], "gross_margin" in ongc), ("INR", False))

    def test_the_example_metrics_file_is_real_and_in_rupees(self):
        with open(os.path.join(ROOT, "trader-analyst", "metrics.example.json")) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["as_of"], "2026-10-25")
        self.assertEqual([c["symbol"] for c in doc["companies"]], ["ITC", "ONGC", "RELIANCE", "SBIN", "TCS"])
        ongc = next(c for c in doc["companies"] if c["symbol"] == "ONGC")
        self.assertEqual((ongc["revenue_ttm"], ongc["eps_ttm"], ongc["currency"]), (7041265500000.0, 34.6, "INR"))

    def test_exit_codes(self):
        code, out, err = run(["ONGC", "ZZZZ", "--offline", DIR, "--now", NOW])
        self.assertEqual(code, 1)                                    # one worked, one did not
        self.assertIn("facts: ZZZZ: Yahoo has no quarterly revenue or net income for ZZZZ", err)
        self.assertEqual(run(["ZZZZ", "--offline", DIR, "--now", NOW])[0], 2)
        self.assertEqual(run([])[0], 2)
        code, _, err = run(["ONGC", "--now", "tomorrow", "--offline", DIR])
        self.assertEqual(code, 2)
        self.assertIn("--now wants", err)


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
        self.saved = facts.urllib.request.urlopen
        self.calls = []

    def tearDown(self):
        facts.urllib.request.urlopen = self.saved

    def fake(self, answers):
        def urlopen(req, timeout=None):
            self.calls.append((req.full_url, req.get_header("User-agent")))
            a = answers.pop(0)
            if isinstance(a, int):
                raise urllib.error.HTTPError(req.full_url, a, "x", {}, io.BytesIO(b""))
            return FakeResponse(a)
        facts.urllib.request.urlopen = urlopen

    def body(self, name):
        with open(os.path.join(DIR, name), "rb") as fh:
            return fh.read()

    def test_the_calls_and_the_user_agent(self):
        self.fake([self.body("yf-ts-ONGC.json"), self.body("nse-ann-ONGC.json"), self.body("nse-events-ONGC.json")])
        with redirect_stdout(io.StringIO()):
            code = facts.main(["ONGC", "--now", NOW])
        self.assertEqual(code, 0)
        urls = [u for u, _ in self.calls]
        self.assertTrue(urls[0].startswith("https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/ONGC.NS?"))
        self.assertEqual(urls[1], "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=ONGC"
                                  "&from_date=08-04-2026&to_date=25-10-2026")
        self.assertEqual(urls[2], "https://www.nseindia.com/api/event-calendar?index=equities&symbol=ONGC"
                                  "&from_date=25-10-2026&to_date=22-02-2027")
        self.assertEqual({ua for _, ua in self.calls}, {"Mozilla/5.0"})

    def test_yahoo_falls_back_to_query2(self):
        self.fake([429, self.body("yf-ts-ONGC.json")])
        f = facts.Fetcher(pause=0)
        doc = facts.fetch_timeseries(f, "ONGC", facts.parse_now(NOW))
        self.assertIn("timeseries", doc)
        self.assertIn("//query1.", self.calls[0][0])
        self.assertIn("//query2.", self.calls[1][0])

    def test_blocked_nse_is_a_note_and_the_figures_still_print(self):
        self.fake([self.body("yf-ts-ONGC.json"), 403, 403])
        out = io.StringIO()
        with redirect_stdout(out):
            code = facts.main(["ONGC", "--now", NOW])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("NOTE: NSE announcements for ONGC: HTTP 403 from www.nseindia.com", text)
        self.assertIn("NOTE: NSE event calendar for ONGC: HTTP 403 from www.nseindia.com", text)
        self.assertRegex(text, r"2026-06-30\s+Q1 FY27")
        self.assertNotIn("No results filing found", text)

    def test_html_instead_of_json_is_named(self):
        self.fake([b"<html>Access Denied</html>"])
        with self.assertRaises(facts.FactsError) as ctx:
            facts.Fetcher(pause=0).json("https://www.nseindia.com/api/x", "x")
        self.assertIn("did not answer JSON (blocked or changed?)", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
