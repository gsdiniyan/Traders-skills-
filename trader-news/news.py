#!/usr/bin/env python3
"""News monitor for trader-news (India, NSE). Standard library only.

Watches NSE corporate announcements and headlines for the stocks you watch or hold and
prints only what is new since the last check, newest first, with how far each stock has
moved today, the board meetings and results dates coming up, and a flag on anything that
touches your symbols.

    python3 news.py                                  # watchlist + plan + portfolio symbols
    python3 news.py --tickers ONGC RELIANCE ITC
    python3 news.py --tickers ONGC --all --hours 72  # everything in the last 3 days, seen or not
    python3 news.py --no-nse                         # headlines only
    python3 news.py --no-moves                       # skip the price line per symbol

Your symbols, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the positions in
trader/portfolio.json.

Sources, one request each per symbol (all free, none official, any can change or block you):
  - NSE corporate announcements, what the company told the exchange (results, board outcomes,
    directors, credit ratings, deals, dividends, record dates):
    https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=ONGC&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
  - NSE event calendar, upcoming board meetings and results dates:
    https://www.nseindia.com/api/event-calendar?index=equities&symbol=ONGC&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
  - Google News headline RSS (Indian publishers), searched by company name:
    https://news.google.com/rss/search?q=...&hl=en-IN&gl=IN&ceid=IN:en
  - Yahoo Finance headline RSS: https://feeds.finance.yahoo.com/rss/2.0/headline?s=ONGC.NS&region=IN&lang=en-IN
    (thin for Indian stocks, often empty)
  - The move: Yahoo Finance's chart endpoint, the last price against yesterday's close:
    https://query2.finance.yahoo.com/v8/finance/chart/ONGC.NS?range=1d&interval=5m
  The NSE endpoints answered a plain browser User-Agent without cookies on 2026-10-25. NSE changes
  them without notice: when one fails the tool says so and carries on with the rest.

Times print in IST. State: trader/state/news.json remembers what you have seen (30 days), so an
item shows once. Offline: --offline DIR reads nse-ann-<SYMBOL>.json, nse-events-<SYMBOL>.json,
gnews-<SYMBOL>.xml, yahoo-<SYMBOL>.xml and chart-<SYMBOL>.json from DIR. The tests use it.

Headlines are leads, not facts: open the story before acting, and treat nothing here as advice.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

NSE_ANN = ("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s"
           "&from_date=%s&to_date=%s")
NSE_EVENTS = ("https://www.nseindia.com/api/event-calendar?index=equities&symbol=%s"
              "&from_date=%s&to_date=%s")
GNEWS = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
UA = "Mozilla/5.0"
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving
IST_OFFSET = _dt.timedelta(hours=5, minutes=30)
INDEX_YAHOO = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN", "INDIAVIX": "^INDIAVIX"}

# What an NSE announcement is about that should stop you before you trade: results and board outcomes,
# directors and key managers, deals and restructuring, defaults and fraud, ratings, capital actions,
# surveillance, orders from authorities, disruptions.
IMPORTANT = re.compile(
    r"outcome of board meeting|financial results|\bresults?\b|resignation|cessation|change in director|"
    r"\bKMP\b|key managerial|appointment of|acquisition|amalgamation|\bmerger\b|demerger|scheme of arrangement|"
    r"sale or disposal|\bdefault|\bfraud|insolvency|\bCIRP\b|delisting|suspension|open offer|buy-?back|\bbonus\b|"
    r"stock split|sub-?division|\bdividend|record date|preferential|rights issue|\bQIP\b|credit rating|"
    r"downgrade|litigation|\border passed|actions? (?:initiated|taken)|penalty|\bfire\b|\bstrike\b|lock-?out|"
    r"shutdown|cyber|public offer|IPO|OFS|offer for sale", re.I)
# Filing types that are routine paperwork even when the text mentions an important word.
ROUTINE = re.compile(r"newspaper publication|trading window|ESOP|ESOS|ESPS|analysts?/|investor meet|"
                     r"con\. call|loss of share certificate|duplicate share", re.I)
KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults?\b|\bQ[1-4]\s?FY\d{2}\b|\bPAT\b|\bnet profit\b|\bEBITDA\b|\bguidance\b|\bforecast\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|target price|price target|\boutperform\b|\bunderperform\b|"
                r"\boverweight\b|\bunderweight\b|\bbuy rating\b|initiates coverage|\bbrokerage\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bSEBI (?:order|probe|notice)\b|\bED\b|\bCBI\b|"
              r"\bNCLT\b|\bantitrust\b|\bcourt\b|\bsupreme court\b|\bHigh Court\b|\btribunal\b|\bpenalty\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bdemerger\b|\bbuyout\b|\btakeover\b|\bopen offer\b|"
             r"\bstake sale\b|\bbulk deal\b|\bblock deal\b|\bdeal\b"),
    ("capital", r"\bdividend\b|\bbonus\b|\bbuy-?back\b|\bstock split\b|\brights issue\b|\bQIP\b|\bpreferential\b|"
                r"\bIPO\b|\bOFS\b|\brecord date\b|\boffering\b"),
    ("people", r"\bCEO\b|\bCMD\b|\bCFO\b|\bchairman\b|\bmanaging director\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\brecall\b|\bbankrupt|\binsolvency\b|\bdefault\b|\bfraud\b|\bdelist|\bhack|\bbreach\b|\boutage\b|"
             r"\bfire\b|\bshutdown\b|\bban\b"),
    ("halt", r"\bcircuit\b|\bhalt(?:s|ed)?\b|\btrading (?:pause|suspen)|\bsuspend|\bASM\b|\bGSM\b|\btrade[- ]for[- ]trade\b"),
    ("regulatory", r"\bSEBI\b|\bRBI\b|\bCCI\b|\bDGCA\b|\bUSFDA\b|\bFDA\b|\bapproval\b|\bapproves\b|\blicen[cs]e\b|\bclinical\b"),
    ("macro", r"\bRBI policy\b|\brepo rate\b|\binflation\b|\bCPI\b|\bGDP\b|\bFed\b|\bFOMC\b|\bcrude\b|\brupee\b|"
              r"\bFII\b|\bFPI\b|\bbudget\b|\btariffs?\b|\bNifty\b|\bSensex\b|\bGIFT Nifty\b|\bGift City\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    """Aware or naive UTC -> naive IST wall-clock time (UTC+5:30, no daylight saving)."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + IST_OFFSET


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 -> aware UTC datetime, or None. A time with no offset is read as UTC."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_now(text):
    """--now "2026-10-23 15:30" is IST; a value with Z or an offset is taken as written."""
    if not text:
        return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    try:
        d = _dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise NewsError("--now wants a time like \"2026-10-23 15:30\" (IST) or ...Z (UTC)")
    if d.tzinfo is None:
        d = (d - IST_OFFSET).replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_nse_time(value):
    """NSE writes "23-Oct-2026 13:18:34" in IST -> aware UTC datetime, or None."""
    value = (value or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return (_dt.datetime.strptime(value, fmt) - IST_OFFSET).replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
    return None


def nse_date(d):
    return d.strftime("%d-%m-%Y")


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{symbol: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[sym.upper()] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(pl["symbol"].upper(), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[sym.upper()] = p.get("name") or out.get(sym.upper())
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.3):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"})
        host = url.split("/")[2]
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s from %s" % (exc.code, host))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (host, exc.reason))
        except OSError as exc:
            raise NewsError("could not read %s: %s" % (host, exc))


def yahoo_symbol(sym):
    """NSE symbol -> Yahoo symbol: ONGC -> ONGC.NS, M&M -> M&M.NS, NIFTY -> ^NSEI (not URL-quoted)."""
    s = sym.strip().upper()
    if s in INDEX_YAHOO:
        return INDEX_YAHOO[s]
    if s.startswith("^") or s.endswith((".NS", ".BO")):
        return s
    return s + ".NS"


def clean_name(name):
    """"Oil and Natural Gas Corporation Limited" -> "Oil and Natural Gas Corporation"."""
    n = html.unescape(name or "").strip()
    return re.sub(r"\s+(?:Limited|Ltd\.?)$", "", n, flags=re.I).strip()


def gnews_query(symbol, name, hours):
    """Search words for Google News: the company name if we know it, else the symbol next to NSE."""
    when = "when:%dh" % max(1, int(round(hours)))
    n = clean_name(name)
    if n and len(n) >= 3:
        return '"%s" %s' % (n, when)
    return '"%s" NSE %s' % (symbol, when)


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def _plain(markup):
    """Strip tags and entities out of a feed description."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(markup or "")))).strip()


def parse_rss(data, ticker, source="Yahoo Finance", feed="yahoo"):
    """Yahoo or Google News headline RSS -> items. Google titles end in " - Publisher": the publisher
    becomes the source."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = html.unescape(_text(it, "title"))
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        src = source
        publisher = _text(it, "source")
        if publisher:
            src = publisher
            suffix = " - " + publisher
            if title.endswith(suffix):
                title = title[:-len(suffix)].rstrip()
        summary = _plain(_text(it, "description"))
        if publisher and summary.startswith(title):
            summary = ""
        out.append({"id": "rss:" + guid, "source": src, "kind": "headline", "feed": feed,
                    "ticker": ticker, "title": title, "summary": summary,
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_announcements(data, ticker):
    """NSE corporate-announcements JSON -> items."""
    try:
        rows = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError:
        raise NewsError("NSE announcements for %s are not JSON (blocked or changed?)" % ticker)
    if not isinstance(rows, list):
        raise NewsError("NSE announcements for %s are not a list (blocked or changed?)" % ticker)
    out = []
    for r in rows:
        desc = (r.get("desc") or "").strip()
        text = re.sub(r"\s+", " ", r.get("attchmntText") or "").strip()
        company = r.get("sm_name") or ticker
        detail = re.sub(r"^.{0,120}? has informed the Exchange (?:about|regarding|that|of)\s+", "", text,
                        flags=re.I).strip(" .")
        if detail.lower() in (desc.lower(), "") or (detail == text and len(text) > 160):
            detail = ""
        if len(detail) > 120:
            detail = detail[:117].rstrip() + "..."
        title = ("%s: %s" % (desc, detail) if detail else desc) or clean_name(company)
        out.append({"id": "nse:%s" % (r.get("seq_id") or "%s-%s" % (ticker, r.get("an_dt"))), "source": "NSE",
                    "kind": "filing", "form": desc, "ticker": ticker, "title": title, "summary": text,
                    "feed": "nse", "important": bool(IMPORTANT.search("%s %s" % (desc, text))) and not ROUTINE.search(desc),
                    "link": r.get("attchmntFile") or "", "time": parse_nse_time(r.get("an_dt")),
                    "company": company})
    return out


def parse_events(data, ticker, today, days=14):
    """NSE event-calendar JSON -> upcoming board meetings and results dates within `days` of `today` (an IST date)."""
    try:
        rows = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError:
        raise NewsError("NSE event calendar for %s is not JSON (blocked or changed?)" % ticker)
    if not isinstance(rows, list):
        raise NewsError("NSE event calendar for %s is not a list (blocked or changed?)" % ticker)
    out = []
    for r in rows:
        try:
            day = _dt.datetime.strptime((r.get("date") or "").strip(), "%d-%b-%Y").date()
        except ValueError:
            continue
        gap = (day - today).days
        if gap < 0 or gap > days:
            continue
        purpose = (r.get("purpose") or "").strip()
        out.append({"ticker": ticker, "date": day.isoformat(), "days": gap, "purpose": purpose,
                    "detail": re.sub(r"\s+", " ", r.get("bm_desc") or "").strip(),
                    "results": bool(re.search(r"result", purpose, re.I)), "today": gap == 0})
    out.sort(key=lambda e: (e["date"], e["ticker"]))
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "name", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "name": meta.get("longName") or meta.get("shortName"),
            "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        ysym = urllib.parse.quote(yahoo_symbol(t), safe="")
        try:
            try:
                data = fetcher.get(CHART % ("query2", ysym), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", ysym), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch, names=None):
    """A pattern per symbol: "(ONGC)", "NSE: ONGC", the bare capitals when the symbol is 4+ letters, and the
    company names we know."""
    pats = {}
    for t, name in watch.items():
        alts = [r"\(%s\)" % re.escape(t), r"\bNSE\s*:\s*%s\b" % re.escape(t)]
        if re.fullmatch(r"[A-Z0-9&\-]{3,}", t):
            alts.append(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(t))
        known = set()
        for n in [name] + sorted((names or {}).get(t, ())):
            n = clean_name(n)
            if n and len(n) >= 4:
                known.add(n)
        for n in sorted(known):
            alts.append(r"\b%s\b" % re.escape(n).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch, names=None):
    """Which of your symbols each item touches, and why it matters."""
    pats = mention_patterns(watch, names)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        # NSE and Yahoo items are fetched for one symbol, so they touch it. A Google search is loose: it touches
        # a symbol only when the text names it.
        touches = {it["ticker"]} if (it["ticker"] in watch and it.get("feed") != "google") else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        if it["kind"] == "filing" and re.search(r"financial results|outcome of board meeting", it.get("form") or "", re.I) \
                and "earnings" not in it["tags"]:
            it["tags"].insert(0, "earnings")
    return items


def relevant(items):
    """Google News searches match loosely: keep a Google item only when it names the symbol or its company."""
    return [it for it in items if it.get("feed") != "google" or it["ticker"] in it.get("touches", ())]


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           to_ist(it["time"]).date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, now, hours=48, nse=True, google=True, yahoo=True, names=None, errors=None):
    """Items from every source for every symbol. `names` ({symbol: name}) feeds the Google News search."""
    errors = [] if errors is None else errors
    names = {} if names is None else names
    items = []
    today = to_ist(now).date()
    first = to_ist(now - _dt.timedelta(hours=hours)).date()
    for t in sorted(watch):
        if nse:
            try:
                data = fetcher.get(NSE_ANN % (urllib.parse.quote(t, safe=""), nse_date(first), nse_date(today)),
                                   "nse-ann-%s.json" % t)
                found = parse_announcements(data, t)
                items += found
                for f in found[:1]:
                    names.setdefault(t, f["company"])
            except NewsError as exc:
                errors.append("NSE announcements for %s: %s" % (t, exc))
        if google:
            q = gnews_query(t, names.get(t) or watch.get(t), hours)
            try:
                items += parse_rss(fetcher.get(GNEWS % urllib.parse.quote_plus(q), "gnews-%s.xml" % t), t, "Google News", "google")
            except NewsError as exc:
                errors.append("Google News for %s: %s" % (t, exc))
        if yahoo:
            try:
                items += parse_rss(fetcher.get(YAHOO % urllib.parse.quote(yahoo_symbol(t), safe=""), "yahoo-%s.xml" % t), t)
            except NewsError as exc:
                errors.append("Yahoo headlines for %s: %s" % (t, exc))
    return items


def collect_events(fetcher, watch, now, days=14, errors=None):
    errors = [] if errors is None else errors
    today = to_ist(now).date()
    last = today + _dt.timedelta(days=days)
    out = []
    for t in sorted(watch):
        try:
            data = fetcher.get(NSE_EVENTS % (urllib.parse.quote(t, safe=""), nse_date(today), nse_date(last)),
                               "nse-events-%s.json" % t)
            out += parse_events(data, t, today, days)
        except NewsError as exc:
            errors.append("NSE event calendar for %s: %s" % (t, exc))
    out.sort(key=lambda e: (e["date"], e["ticker"]))
    return out


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        unit = "" if m["ticker"] in INDEX_YAHOO else "Rs "
        out.append("%-10s %s%s %+.2f%% vs yesterday's close %s%s%s  %s" % (
            m["ticker"], unit, "{:,.2f}".format(m["price"]), m["pct"], unit, "{:,.2f}".format(m["prev_close"]),
            when, flag))
    return [l.rstrip() for l in out]


def render_events(events):
    out = []
    for e in events:
        when = "TODAY" if e["today"] else ("tomorrow" if e["days"] == 1 else "in %d days" % e["days"])
        mark = "!" if e["results"] and e["days"] <= 1 else " "
        detail = e["detail"] if len(e["detail"]) <= 110 else e["detail"][:107].rstrip() + "..."
        out.append("%s %-10s %s (%s)  %s%s" % (mark, e["ticker"], _dt.date.fromisoformat(e["date"]).strftime("%d-%b-%Y"),
                                               when, e["purpose"], (": " + detail) if detail else ""))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT, events=None):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    ev = render_events(events or [])
    if ev:
        out.append("Coming up (NSE event calendar):")
        out += ev
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-11s %-14s %s" % (mark, when, who, it["source"][:14], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story before acting. "
               "Not investment advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="NSE symbols; default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-nse", action="store_true", help="skip NSE announcements")
    ap.add_argument("--no-google", action="store_true", help="skip Google News")
    ap.add_argument("--no-yahoo", action="store_true", help="skip Yahoo headlines")
    ap.add_argument("--no-events", action="store_true", help="skip the board meeting and results calendar")
    ap.add_argument("--events-days", type=int, default=14, help="how far ahead to list board meetings")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="pretend it is this time, e.g. \"2026-10-23 15:30\" (IST)")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.tickers:
        watch = {t.upper(): None for t in args.tickers}
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        watch.update({t: known[t] for t in watch if known.get(t)})
    else:
        watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if not watch:
        print("news: no symbols. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    try:
        now = parse_now(args.now)
    except NewsError as exc:
        print("news: %s" % exc, file=sys.stderr)
        return 2
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    names = {m["ticker"]: {m["name"]} for m in moves if m.get("name")}
    gnames = {t: next(iter(n)) for t, n in names.items()}
    items = collect(fetcher, watch, now, hours=args.hours, nse=not args.no_nse, google=not args.no_google,
                    yahoo=not args.no_yahoo, names=gnames, errors=errors)
    for t, n in gnames.items():
        names.setdefault(t, set()).add(n)
    items = dedupe(relevant(tag(items, watch, names)))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    events = [] if (args.no_events or args.no_nse) else collect_events(fetcher, watch, now, args.events_days, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "events": events, "items": _json_ready(shown),
                          "notes": errors}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct, events))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
