"""trader-news tests (NSE): announcements, event calendar, headlines and the day's move, all from saved real answers."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")
NOW = "2026-10-23 15:30"          # IST, a few minutes after the day's last quote
UTC = datetime.timezone.utc


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Feeds(unittest.TestCase):
    def test_yahoo_rss(self):
        items = news.parse_rss(raw("yahoo-ONGC.xml"), "ONGC")
        self.assertEqual(len(items), 13)
        first = items[0]
        self.assertIn("ONGC discovers gas at Mahanadi deepwater well", first["title"])
        self.assertEqual(first["time"], datetime.datetime(2026, 9, 22, 10, 47, 53, tzinfo=UTC))
        self.assertTrue(first["id"].startswith("rss:"))
        self.assertEqual((first["source"], first["feed"], first["kind"]), ("Yahoo Finance", "yahoo", "headline"))
        self.assertEqual(news.parse_rss(raw("yahoo-ITC.xml"), "ITC"), [])        # Yahoo's feed is often empty here

    def test_google_news_rss(self):
        items = news.parse_rss(raw("gnews-ONGC.xml"), "ONGC", "Google News", "google")
        self.assertEqual(len(items), 9)
        first = items[0]
        self.assertEqual(first["title"], "ONGC appoints Sunil Kumar as Chairman effective Dec 7, 2026")
        self.assertEqual((first["source"], first["feed"]), ("scanx.trade", "google"))
        self.assertEqual(first["time"], datetime.datetime(2026, 10, 23, 9, 5, 47, tzinfo=UTC))
        self.assertTrue(first["link"].startswith("https://news.google.com/rss/articles/"))
        for it in items:
            self.assertNotIn("<", it["summary"])
            self.assertNotIn("&lt;", it["summary"])

    def test_nse_announcements(self):
        items = news.parse_announcements(raw("nse-ann-ONGC.json"), "ONGC")
        self.assertEqual([i["id"] for i in items], ["nse:106826173", "nse:106813069"])
        chair = items[0]
        self.assertTrue(chair["title"].startswith("General Updates: Letter from Ministry of Petroleum and Natural Gas"))
        self.assertTrue(chair["title"].endswith("..."))
        self.assertTrue(chair["important"])
        self.assertEqual((chair["source"], chair["kind"], chair["form"], chair["feed"]), ("NSE", "filing", "General Updates", "nse"))
        self.assertTrue(chair["link"].endswith("ONGC_23102026131801_ministryletterchairman.pdf"))
        # NSE writes 13:18:34 IST: that is 07:48:34 UTC
        self.assertEqual(chair["time"], datetime.datetime(2026, 10, 23, 7, 48, 34, tzinfo=UTC))
        self.assertEqual(items[1]["title"], "Credit Rating")
        self.assertTrue(items[1]["important"])

    def test_routine_filings_are_not_flagged(self):
        by = {i["id"]: i for i in news.parse_announcements(raw("nse-ann-RELIANCE.json"), "RELIANCE")}
        self.assertTrue(by["nse:106827783"]["important"])                  # the Jio Platforms IPO update
        self.assertFalse(by["nse:106820709"]["important"])                 # newspaper clippings about the same IPO
        self.assertFalse(by["nse:106823997"]["important"])                 # a media statement
        itc = news.parse_announcements(raw("nse-ann-ITC.json"), "ITC")
        self.assertFalse(itc[0]["important"])                              # ESOP allotment
        self.assertEqual(itc[0]["title"], "ESOP/ESOS/ESPS: Allotment of 1395500 Shares")

    def test_a_results_filing_is_tagged_earnings(self):
        row = [{"an_dt": "23-Oct-2026 16:00:00", "desc": "Financial Results", "seq_id": "1", "sm_name": "Xyz Limited",
                "attchmntText": "Xyz Limited has informed the Exchange about Financial Results for the quarter ended 30 Sep 2026",
                "attchmntFile": "https://nsearchives.nseindia.com/corporate/XYZ.pdf"}]
        items = news.tag(news.parse_announcements(json.dumps(row), "XYZ"), {"XYZ": None})
        self.assertEqual(items[0]["tags"][0], "earnings")
        self.assertTrue(items[0]["important"])

    def test_a_blocked_nse_answer_is_an_error_not_a_crash(self):
        with self.assertRaises(news.NewsError):
            news.parse_announcements(b"<html>Access Denied</html>", "ONGC")
        with self.assertRaises(news.NewsError):
            news.parse_announcements(b'{"error": "blocked"}', "ONGC")
        with self.assertRaises(news.NewsError):
            news.parse_events(b"<html></html>", "ONGC", datetime.date(2026, 10, 23))

    def test_events(self):
        today = datetime.date(2026, 10, 23)
        ev = news.parse_events(raw("nse-events-ONGC.json"), "ONGC", today)
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["date"], ev[0]["days"], ev[0]["purpose"]), ("2026-11-05", 13, "Financial Results/Dividend"))
        self.assertTrue(ev[0]["results"])
        self.assertFalse(ev[0]["today"])
        self.assertEqual(news.parse_events(raw("nse-events-ONGC.json"), "ONGC", today, days=5), [])
        self.assertEqual(news.parse_events(raw("nse-events-RELIANCE.json"), "RELIANCE", today), [])
        on_the_day = news.parse_events(raw("nse-events-ONGC.json"), "ONGC", datetime.date(2026, 11, 5))
        self.assertTrue(on_the_day[0]["today"])
        line = news.render_events(on_the_day)[0]
        self.assertTrue(line.startswith("! ONGC"))
        self.assertIn("05-Nov-2026 (TODAY)  Financial Results/Dividend", line)
        self.assertEqual(news.parse_events(raw("nse-events-ONGC.json"), "ONGC", datetime.date(2026, 11, 6)), [])   # past

    def test_times(self):
        self.assertEqual(news.parse_time("Tue, 13 Oct 2026 20:02:03 +0000").hour, 20)
        self.assertEqual(news.parse_time("2026-09-01T16:30:35+05:30").hour, 11)
        self.assertIsNone(news.parse_time("soon"))
        self.assertEqual(news.parse_nse_time("23-Oct-2026 13:18:34").isoformat(), "2026-10-23T07:48:34+00:00")
        self.assertIsNone(news.parse_nse_time("yesterday"))
        self.assertEqual(news.parse_now("2026-10-23 15:30"), datetime.datetime(2026, 10, 23, 10, 0, tzinfo=UTC))
        self.assertEqual(news.parse_now("2026-10-23T10:00:00Z"), datetime.datetime(2026, 10, 23, 10, 0, tzinfo=UTC))
        with self.assertRaises(news.NewsError):
            news.parse_now("tomorrow")

    def test_ist_has_no_daylight_saving(self):
        self.assertEqual(news.to_ist(datetime.datetime(2026, 7, 1, 0, 0, tzinfo=UTC)), datetime.datetime(2026, 7, 1, 5, 30))
        self.assertEqual(news.to_ist(datetime.datetime(2026, 12, 1, 0, 0)), datetime.datetime(2026, 12, 1, 5, 30))

    def test_symbols_and_names(self):
        self.assertEqual(news.yahoo_symbol("ongc"), "ONGC.NS")
        self.assertEqual(news.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(news.yahoo_symbol("NIFTY"), "^NSEI")
        self.assertEqual(news.yahoo_symbol("ITC.BO"), "ITC.BO")
        self.assertEqual(news.clean_name("Oil and Natural Gas Corporation Limited"), "Oil and Natural Gas Corporation")
        self.assertEqual(news.clean_name("Tata Motors Ltd."), "Tata Motors")
        self.assertEqual(news.gnews_query("ONGC", "Oil and Natural Gas Corporation Limited", 48),
                         '"Oil and Natural Gas Corporation" when:48h')
        self.assertEqual(news.gnews_query("ITC", None, 72), '"ITC" NSE when:72h')


class Tagging(unittest.TestCase):
    def test_touches_and_tags(self):
        items = news.parse_rss(raw("gnews-ONGC.xml"), "ONGC", "Google News", "google")
        news.tag(items, {"ONGC": None, "RELIANCE": None}, {"ONGC": {"Oil and Natural Gas Corporation"}})
        chair = next(i for i in items if i["title"].startswith("ONGC appoints Sunil Kumar"))
        self.assertEqual(chair["touches"], ["ONGC"])
        self.assertIn("people", chair["tags"])

    def test_loose_google_results_are_dropped(self):
        items = news.parse_rss(raw("gnews-ITC.xml"), "ITC", "Google News", "google")
        self.assertEqual(len(items), 13)
        kept = news.relevant(news.tag(items, {"ITC": None}))
        titles = [i["title"] for i in kept]
        self.assertEqual(len(kept), 3, titles)
        self.assertTrue(any("ITC stock: 360 ONE sets INR 440 target" in t for t in titles))
        self.assertTrue(any("ITC sets November 2 results date" in t for t in titles))
        self.assertFalse(any("Titan" in t or "Sensex" in t or "ORIENTCEM" in t for t in titles))

    def test_nse_and_yahoo_items_touch_the_symbol_they_were_fetched_for(self):
        items = news.tag(news.parse_announcements(raw("nse-ann-ITC.json"), "ITC"), {"ITC": None})
        self.assertEqual(items[0]["touches"], ["ITC"])
        self.assertEqual(len(news.relevant(items)), 1)

    def test_short_symbols_need_a_clear_mention(self):
        pats = news.mention_patterns({"LT": None, "TCS": None, "M&M": None})
        self.assertIsNone(pats["LT"].search("Salt LT bag"))
        self.assertTrue(pats["LT"].search("Larsen (LT) wins order"))
        self.assertTrue(pats["LT"].search("NSE: LT up"))
        self.assertTrue(pats["TCS"].search("TCS results beat estimates"))
        self.assertIsNone(pats["TCS"].search("Tcsa results"))
        self.assertTrue(pats["M&M"].search("M&M hikes SUV prices"))

    def test_dedupe_merges_the_same_story(self):
        a = news.parse_rss(raw("yahoo-ONGC.xml"), "ONGC")
        b = news.parse_rss(raw("yahoo-ONGC.xml"), "RELIANCE")
        items = news.dedupe(news.tag(a + b, {"ONGC": None, "RELIANCE": None}))
        self.assertEqual(len(items), 13)
        self.assertEqual(items[0]["touches"], ["ONGC", "RELIANCE"])

    def test_indian_tags(self):
        items = [{"id": "x", "source": "Yahoo Finance", "kind": "headline", "feed": "yahoo", "ticker": "SBIN",
                  "title": "SEBI order on XYZ; circuit hit at 5%; RBI policy next week; Q2FY27 results beat",
                  "summary": "", "link": "", "time": None}]
        tags = news.tag(items, {"SBIN": None})[0]["tags"]
        for t in ("halt", "regulatory", "macro", "earnings", "legal"):
            self.assertIn(t, tags)


class Moves(unittest.TestCase):
    def test_chart(self):
        m = news.parse_chart(raw("chart-ONGC.json"), "ONGC")
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (227.5, 225.0, 1.11))
        self.assertEqual(m["name"], "Oil and Natural Gas Corporation Limited")
        self.assertEqual(news.to_ist(m["time"]).strftime("%H:%M"), "15:28")
        line = news.render_moves([m])[0]
        self.assertEqual(line, "ONGC       Rs 227.50 +1.11% vs yesterday's close Rs 225.00 at 15:28 IST")
        self.assertIn("MOVING", news.render_moves([m], move_pct=1.0)[0])
        rel = news.parse_chart(raw("chart-RELIANCE.json"), "RELIANCE")
        self.assertIn("Rs 1,190.00 +0.46%", news.render_moves([rel])[0])
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "ZZZZ")

    def test_an_index_is_in_points(self):
        line = news.render_moves([{"ticker": "NIFTY", "price": 22600.0, "prev_close": 22687.7, "pct": -0.39, "time": None}])[0]
        self.assertEqual(line, "NIFTY      22,600.00 -0.39% vs yesterday's close 22,687.70")


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        self.none = os.path.join(self.tmp.name, "none.json")
        self.base = ["--offline", DIR, "--state", self.state, "--now", NOW,
                     "--portfolio", self.none, "--watchlist", self.none, "--plan", self.none]

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_items_once_newest_first(self):
        code, out, err = run(["--tickers", "ONGC", "ITC", "--hours", "48", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(data["items"]), 6)       # 1 NSE + 4 Google for ONGC, 1 Google for ITC in the 48 hours
        self.assertEqual({i["feed"] for i in data["items"]}, {"nse", "google"})
        self.assertEqual(sorted(m["ticker"] for m in data["moves"]), ["ITC", "ONGC"])
        self.assertEqual([e["ticker"] for e in data["events"]], ["ITC", "ONGC"])
        code, out, _ = run(["--tickers", "ONGC", "ITC", "--hours", "48"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)

    def test_the_ongc_chairman_story(self):
        code, out, err = run(["--tickers", "ONGC", "--hours", "6", "--no-moves"] + self.base)
        self.assertEqual(code, 0, err)
        lines = [l for l in out.splitlines() if l[2:12] == "2026-10-23"]
        self.assertEqual(len(lines), 4)
        nse = next(l for l in lines if " NSE " in l)
        self.assertTrue(nse.startswith("! 2026-10-23 13:18 IST  ONGC"))
        self.assertIn("General Updates: Letter from Ministry of Petroleum and Natural Gas", nse)
        self.assertIn("ONGC appoints Sunil Kumar as Chairman effective Dec 7, 2026", out)
        self.assertIn("tags: people", out)
        self.assertIn("Checked 2026-10-23 15:30 IST", out)
        self.assertIn("Not investment advice", out)
        for gone in ("$", " ET", "SEC", chr(0x2014)):
            self.assertNotIn(gone, out)
        # nothing published after the pinned clock shows
        self.assertNotIn("2026-10-24", out)

    def test_filings_show_in_a_wider_window(self):
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "240", "--no-moves", "--no-events"] + self.base[:4] +
                             ["--now", "2026-10-25 15:30"])
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"! 2026-10-21 20:43 IST\s+RELIANCE\s+NSE\s+Credit Rating")
        self.assertRegex(out, r"! 2026-10-24 21:48 IST\s+RELIANCE\s+NSE\s+General Updates: Initial Public Offer of Jio Platforms")
        self.assertRegex(out, r"  2026-10-21 22:44 IST\s+RELIANCE\s+NSE\s+Updates: Please find attached")

    def test_results_dates_come_up_in_the_brief(self):
        code, out, err = run(["--tickers", "ONGC", "ITC", "RELIANCE", "--no-moves"] + self.base)
        self.assertIn("Coming up (NSE event calendar):", out)
        self.assertIn("ITC        02-Nov-2026 (in 10 days)  Financial Results: To consider and approve", out)
        self.assertIn("ONGC       05-Nov-2026 (in 13 days)  Financial Results/Dividend", out)
        self.assertNotIn("RELIANCE   0", out)
        code, out, _ = run(["--tickers", "ONGC", "ITC", "--no-moves", "--events-days", "5", "--dry-run"] + self.base)
        self.assertNotIn("Coming up", out)
        code, out, _ = run(["--tickers", "ONGC", "--no-moves", "--no-events", "--dry-run"] + self.base)
        self.assertNotIn("Coming up", out)

    def test_the_price_line(self):
        code, out, err = run(["--tickers", "ONGC", "--hours", "6", "--dry-run"] + self.base)
        self.assertIn("ONGC       Rs 227.50 +1.11% vs yesterday's close Rs 225.00 at 15:28 IST", out)
        self.assertNotIn("MOVING", out)
        code, out, _ = run(["--tickers", "ONGC", "--hours", "6", "--dry-run", "--move-pct", "1"] + self.base)
        self.assertIn("MOVING", out)

    def test_a_missing_chart_file_is_no_move_line_and_no_note(self):
        d = tempfile.mkdtemp()
        try:
            for name in os.listdir(DIR):
                if name != "chart-ITC.json":
                    shutil.copy(os.path.join(DIR, name), d)
            args = list(self.base)
            args[1] = d
            code, out, _ = run(["--tickers", "ITC", "--hours", "48", "--dry-run"] + args)
            self.assertNotIn("vs yesterday's close", out)
            self.assertNotIn("price for", out)
        finally:
            shutil.rmtree(d)

    def test_symbols_from_watchlist_and_plan(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [{"symbol": "ITC", "setups": ["orb"]}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as fh:
            json.dump({"created_by": "trader-exit", "symbol": "ONGC"}, fh)
        self.assertEqual(sorted(news.watch_from_files("/nonexistent.json", wl, plan)), ["ITC", "ONGC"])
        args = list(self.base)
        args[args.index("--watchlist") + 1] = wl
        args[args.index("--plan") + 1] = plan
        code, out, err = run(["--json", "--dry-run", "--hours", "48"] + args)
        self.assertEqual(code, 0, err)
        touched = {t for i in json.loads(out)["items"] for t in i["touches"]}
        self.assertEqual(touched, {"ITC", "ONGC"})
        self.assertFalse(os.path.exists(self.state))

    def test_no_symbols_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no symbols", err)
        args = list(self.base)
        args[args.index("--now") + 1] = "tomorrow"
        code, _, err = run(["--tickers", "ONGC"] + args)
        self.assertEqual(code, 2)
        self.assertIn("--now wants", err)

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-08-01T00:00:00+00:00"}}
        now = news.parse_now(NOW)
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertEqual(state["last_check"], now.isoformat())


class FakeResponse(object):
    def __init__(self, body):
        self.body = body
        self.headers = {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = news.urllib.request.urlopen
        self.calls = []

    def tearDown(self):
        news.urllib.request.urlopen = self.saved

    def fake(self, answers):
        def urlopen(req, timeout=None):
            self.calls.append((req.full_url, req.get_header("User-agent")))
            a = answers.pop(0)
            if isinstance(a, int):
                raise urllib.error.HTTPError(req.full_url, a, "x", {}, io.BytesIO(b""))
            return FakeResponse(a)
        news.urllib.request.urlopen = urlopen

    def test_the_urls_and_the_user_agent(self):
        now = news.parse_now(NOW)
        f = news.Fetcher(pause=0)
        self.fake([raw("nse-ann-ONGC.json"), raw("gnews-ONGC.xml"), raw("yahoo-ONGC.xml"), raw("nse-events-ONGC.json"),
                   raw("chart-ONGC.json")])
        items = news.collect(f, {"ONGC": None}, now, hours=48)
        news.collect_events(f, {"ONGC": None}, now, 14)
        news.collect_moves(f, {"ONGC": None})
        urls = [u for u, _ in self.calls]
        self.assertEqual(urls[0], "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=ONGC"
                                  "&from_date=21-10-2026&to_date=23-10-2026")
        self.assertEqual(urls[1], "https://news.google.com/rss/search?q=%22Oil+%26+Natural+Gas+Corporation%22"
                                  "+when%3A48h&hl=en-IN&gl=IN&ceid=IN:en")
        self.assertEqual(urls[2], "https://feeds.finance.yahoo.com/rss/2.0/headline?s=ONGC.NS&region=IN&lang=en-IN")
        self.assertEqual(urls[3], "https://www.nseindia.com/api/event-calendar?index=equities&symbol=ONGC"
                                  "&from_date=23-10-2026&to_date=06-11-2026")
        self.assertEqual(urls[4], "https://query2.finance.yahoo.com/v8/finance/chart/ONGC.NS?range=1d&interval=5m")
        self.assertEqual({ua for _, ua in self.calls}, {"Mozilla/5.0"})
        self.assertEqual(len(items), 2 + 9 + 13)

    def test_a_blocked_source_is_noted_and_the_rest_carry_on(self):
        now = news.parse_now(NOW)
        f = news.Fetcher(pause=0)
        self.fake([403, raw("gnews-ONGC.xml"), 503])
        errors = []
        items = news.collect(f, {"ONGC": None}, now, hours=48, errors=errors)
        self.assertEqual(len(items), 9)
        self.assertEqual(errors, ["NSE announcements for ONGC: HTTP 403 from www.nseindia.com",
                                  "Yahoo headlines for ONGC: HTTP 503 from feeds.finance.yahoo.com"])
        self.fake([429, raw("chart-ONGC.json")])
        errors = []
        moves = news.collect_moves(f, {"ONGC": None}, errors)
        self.assertEqual((len(moves), errors), (1, []))
        self.assertIn("query1.finance.yahoo.com", self.calls[-1][0])

    def test_the_announcement_company_name_feeds_the_google_search(self):
        now = news.parse_now(NOW)
        f = news.Fetcher(pause=0)
        self.fake([raw("nse-ann-ONGC.json"), raw("gnews-ONGC.xml")])
        names = {}
        news.collect(f, {"ONGC": None}, now, hours=24, yahoo=False, names=names)
        self.assertEqual(names["ONGC"], "Oil & Natural Gas Corporation Limited")
        self.assertIn("%22Oil+%26+Natural+Gas+Corporation%22+when%3A24h", self.calls[1][0])


if __name__ == "__main__":
    unittest.main()
