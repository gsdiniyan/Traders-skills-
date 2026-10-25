#!/usr/bin/env python3
"""Earnings reader for trader-analyst (India, NSE). Standard library only.

Pulls a company's reported quarterly numbers and lays out what changed, quarter by quarter:
revenue, EBITDA, pre-tax and net margins, net income, diluted EPS, and growth against the same
quarter a year earlier, in rupees crore. It also finds what the company filed with the NSE for its
latest results (the results filing, the press release, the earnings call transcript or recording)
and the next board meeting for results.

    python3 facts.py ONGC
    python3 facts.py ONGC TCS --quarters 8
    python3 facts.py ONGC --json
    python3 facts.py ONGC RELIANCE ITC --metrics trader/metrics.json

Sources (free, unofficial, no keys; any can change or block you):
  Yahoo Finance fundamentals timeseries, the quarterly figures:
    https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/ONGC.NS?type=quarterlyTotalRevenue,...
  NSE corporate announcements, the results filing and its press release and call transcript:
    https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=ONGC&from_date=..&to_date=..
  NSE event calendar, the next board meeting for results:
    https://www.nseindia.com/api/event-calendar?index=equities&symbol=ONGC&from_date=..&to_date=..

The numbers come from Yahoo, which is patchy for Indian companies: it often holds only the last five
quarters, skips a quarter, repeats one quarter's figures under another date, and does not say whether
the figures are consolidated or standalone. So this tool checks them and says what it found:
  - a quarter whose revenue and net income are identical to an earlier quarter is dropped as a data error;
  - quarters missing inside the series are listed, and the last-four-quarters total is skipped over a gap;
  - when the exchange shows a results filing for a quarter newer than Yahoo's latest, it says so, with the link.
Read the filing before you quote a number.

Quarters are labelled for an April to March financial year (Q1 FY27 is April to June 2026). One crore is
10 million rupees. Gross and operating margins are not shown: Indian companies report expenses by nature,
and Yahoo's fill-ins for them are not reliable. Banks have no EBITDA line: blank, not zero.

--metrics FILE merges one row per symbol into a JSON file (for notes or a sheet). Offline: --offline DIR
reads yf-ts-<SYMBOL>.json, nse-ann-<SYMBOL>.json and nse-events-<SYMBOL>.json from DIR.

Numbers only: this tool never says whether a stock is a buy.
"""

import argparse
import datetime as _dt
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TS_URL = ("https://%s.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/%s"
          "?type=%s&period1=%d&period2=%d&merge=false&padTimeSeries=true")
NSE_ANN = ("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s"
           "&from_date=%s&to_date=%s")
NSE_EVENTS = ("https://www.nseindia.com/api/event-calendar?index=equities&symbol=%s"
              "&from_date=%s&to_date=%s")
UA = "Mozilla/5.0"
IST_OFFSET = _dt.timedelta(hours=5, minutes=30)
INDEX_YAHOO = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN", "INDIAVIX": "^INDIAVIX"}

CONCEPTS = {
    "revenue": "quarterlyTotalRevenue",
    "net_income": "quarterlyNetIncome",
    "pretax_income": "quarterlyPretaxIncome",
    "ebitda": "quarterlyEBITDA",
    "eps_diluted": "quarterlyDilutedEPS",
}
CRORE = 1e7
FILING_DAYS = 200   # how far back to look for the results filing


class FactsError(Exception):
    pass


# ---------------------------------------------------------------- time and money

def parse_now(text):
    """--now "2026-10-25 12:00" is IST; with Z or an offset it is taken as written. Returns naive IST."""
    if not text:
        return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None, microsecond=0) + IST_OFFSET
    try:
        d = _dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise FactsError("--now wants a time like \"2026-10-25 12:00\" (IST) or ...Z (UTC)")
    if d.tzinfo is None:
        return d
    return d.astimezone(_dt.timezone.utc).replace(tzinfo=None) + IST_OFFSET


def parse_nse_date(value):
    """"04-Aug-2026 20:03:37" or "05-Nov-2026" -> date, or None."""
    value = (value or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return _dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def nse_date(d):
    return d.strftime("%d-%m-%Y")


def _date(s):
    return _dt.date.fromisoformat(s)


def indian(n):
    """12345678 -> "1,23,45,678" (Indian digit grouping), integers only."""
    sign = "-" if n < 0 else ""
    s = str(int(abs(n)))
    if len(s) <= 3:
        return sign + s
    head, tail, groups = s[:-3], s[-3:], []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups + [tail])


def cr(v):
    """Rupees -> "2,04,987" (crore). None -> "-"."""
    return "-" if v is None else indian(round(v / CRORE))


def cr_text(v):
    return "n/a" if v is None else "Rs %s crore" % indian(round(v / CRORE))


def fiscal_label(end):
    """Quarter end date -> "Q1 FY27" (April to March financial year)."""
    d = _date(end)
    q = {6: "Q1", 9: "Q2", 12: "Q3", 3: "Q4"}.get(d.month)
    if q is None:
        return ""
    fy = d.year if d.month <= 3 else d.year + 1
    return "%s FY%02d" % (q, fy % 100)


def month_end_before(end, months=3):
    """The date `months` earlier, as a month end: 2026-06-30 -> 2026-03-31."""
    d = _date(end)
    m = d.month - months
    y = d.year
    while m <= 0:
        m += 12
        y -= 1
    nxt = _dt.date(y + (m == 12), (m % 12) + 1, 1)
    return nxt - _dt.timedelta(days=1)


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.3):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def json(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise FactsError("offline: no file %s" % path)
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"})
        host = url.split("/")[2]
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
        except urllib.error.HTTPError as exc:
            raise FactsError("HTTP %s from %s" % (exc.code, host))
        except urllib.error.URLError as exc:
            raise FactsError("could not reach %s: %s" % (host, exc.reason))
        except OSError as exc:
            raise FactsError("could not read %s: %s" % (host, exc))
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            raise FactsError("%s did not answer JSON (blocked or changed?)" % host)


def yahoo_symbol(sym):
    s = sym.strip().upper()
    if s in INDEX_YAHOO:
        return INDEX_YAHOO[s]
    if s.startswith("^") or s.endswith((".NS", ".BO")):
        return s
    return s + ".NS"


def ts_url(sym, now_ist, host="query1", years=4):
    start = _dt.datetime.combine(now_ist.date() - _dt.timedelta(days=365 * years), _dt.time())
    p1 = int((start - IST_OFFSET).replace(tzinfo=_dt.timezone.utc).timestamp())
    p2 = int((now_ist - IST_OFFSET).replace(tzinfo=_dt.timezone.utc).timestamp())
    return TS_URL % (host, urllib.parse.quote(yahoo_symbol(sym), safe=""), ",".join(CONCEPTS.values()), p1, p2)


def fetch_timeseries(fetcher, sym, now_ist):
    name = "yf-ts-%s.json" % sym
    try:
        return fetcher.json(ts_url(sym, now_ist, "query1"), name)
    except FactsError as exc:
        if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
            raise
        return fetcher.json(ts_url(sym, now_ist, "query2"), name)


# ---------------------------------------------------------------- quarters

def quarterly_series(doc, sym):
    """Yahoo timeseries JSON -> ({metric: {end: value}}, currency)."""
    try:
        results = doc["timeseries"]["result"] or []
    except (KeyError, TypeError):
        raise FactsError("Yahoo's answer for %s is not a timeseries (blocked or changed?)" % sym)
    out = {k: {} for k in CONCEPTS}
    currency = None
    by_type = {v: k for k, v in CONCEPTS.items()}
    for r in results:
        types = (r.get("meta") or {}).get("type") or []
        if not types or types[0] not in by_type:
            continue
        for x in r.get(types[0]) or []:
            if not x:
                continue
            raw = (x.get("reportedValue") or {}).get("raw")
            if raw is None or not x.get("asOfDate"):
                continue
            out[by_type[types[0]]][x["asOfDate"]] = float(raw)
            currency = currency or x.get("currencyCode")
    return out, currency


def drop_duplicates(series):
    """A quarter whose revenue and net income equal an earlier quarter's is a data error: drop it.
    Returns the dropped list [{"end", "same_as"}]."""
    dropped = []
    seen = {}
    for end in sorted(set(series["revenue"]) | set(series["net_income"])):
        key = (series["revenue"].get(end), series["net_income"].get(end))
        if key == (None, None):
            continue
        if key in seen:
            dropped.append({"end": end, "same_as": seen[key]})
            for m in series:
                series[m].pop(end, None)
        else:
            seen[key] = end
    return dropped


def missing_quarters(ends):
    """Quarter ends absent between the oldest and newest in `ends` (sorted ascending), as ISO dates."""
    if len(ends) < 2:
        return []
    out = []
    cur = _date(ends[-1])
    have = [_date(e) for e in ends]
    while True:
        prev = month_end_before(cur.isoformat(), 3)
        if prev < have[0] - _dt.timedelta(days=10):
            break
        if not any(abs((h - prev).days) <= 10 for h in have):
            out.append(prev.isoformat())
        cur = prev
    return sorted(out)


def _yoy_match(series_, end, days=365, slack=10):
    target = _date(end) - _dt.timedelta(days=days)
    best = None
    for e in series_:
        gap = abs((_date(e) - target).days)
        if gap <= slack and (best is None or gap < best[0]):
            best = (gap, e)
    return series_[best[1]] if best else None


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def analyse(doc, sym, quarters=6, name=None):
    """Every number in the report, as a dict."""
    series, currency = quarterly_series(doc, sym)
    if not series["revenue"] and not series["net_income"]:
        raise FactsError("Yahoo has no quarterly revenue or net income for %s (check the symbol; new listings and "
                         "some banks are missing)" % sym)
    dropped = drop_duplicates(series)
    all_ends = sorted(set(series["revenue"]) | set(series["net_income"]))
    gaps = missing_quarters(all_ends)
    ends = sorted(all_ends, reverse=True)[:quarters]
    rows = []
    for end in ends:
        r, ni = series["revenue"].get(end), series["net_income"].get(end)
        pt, eb = series["pretax_income"].get(end), series["ebitda"].get(end)
        eps = series["eps_diluted"].get(end)
        prior = {k: _yoy_match(series[k], end) for k in ("revenue", "net_income", "eps_diluted")}
        rows.append({
            "end": end, "fiscal": fiscal_label(end),
            "revenue": r, "ebitda": eb, "pretax_income": pt, "net_income": ni,
            "eps_diluted": round(eps, 2) if eps is not None else None,
            "ebitda_margin": margin(eb, r), "pretax_margin": margin(pt, r), "net_margin": margin(ni, r),
            "revenue_yoy": pct(r, prior["revenue"]), "net_income_yoy": pct(ni, prior["net_income"]),
            "eps_yoy": pct(eps, prior["eps_diluted"]),
        })
    ttm, skipped = {}, []
    for key in ("revenue", "net_income", "eps_diluted"):
        last4 = sorted(series[key])[-4:]
        if len(last4) == 4 and (_date(last4[-1]) - _date(last4[0])).days < 300:
            ttm[key] = sum(series[key][e] for e in last4)
        elif series[key]:
            skipped.append(key.replace("_", " "))
    ttm_note = None
    if skipped:
        ttm_note = ("The last-four-quarters total is skipped for %s: the series has no four consecutive quarters."
                    % ", ".join(skipped))
    if "eps_diluted" in ttm:
        ttm["eps_diluted"] = round(ttm["eps_diluted"], 2)
    return {"entity": name or sym, "symbol": sym, "currency": currency or "INR", "rows": rows, "ttm": ttm,
            "ttm_note": ttm_note, "dropped": dropped, "missing_quarters": gaps}


def data_notes(result):
    """Plain-words warnings about the data itself."""
    notes = []
    if result["currency"] != "INR":
        notes.append("Yahoo reports these figures in %s, not rupees." % result["currency"])
    for d in result["dropped"]:
        notes.append("Dropped the quarter ending %s: its revenue and net income are identical to the quarter ending %s, "
                     "which is almost certainly a data error." % (d["end"], d["same_as"]))
    if result["missing_quarters"]:
        notes.append("Yahoo has no figures for the quarter%s ending %s." % (
            "" if len(result["missing_quarters"]) == 1 else "s", ", ".join(result["missing_quarters"])))
    if result.get("ttm_note"):
        notes.append(result["ttm_note"])
    return notes


# ---------------------------------------------------------------- filings

def latest_filings(rows):
    """From NSE corporate-announcements rows: the newest results filing, the press release and the call
    transcript or recording filed with or after it."""
    out = {"results": None, "press_release": None, "transcript": None, "recording": None}
    parsed = []
    for r in rows or []:
        d = parse_nse_date(r.get("an_dt"))
        if d is None:
            continue
        parsed.append((r.get("sort_date") or r.get("an_dt"), d, r))
    parsed.sort(key=lambda t: t[0], reverse=True)

    def entry(d, r):
        return {"date": d.isoformat(), "desc": (r.get("desc") or "").strip(), "link": r.get("attchmntFile") or "",
                "text": re.sub(r"\s+", " ", r.get("attchmntText") or "").strip()}

    for key, d, r in parsed:
        desc = (r.get("desc") or "").lower()
        text = r.get("attchmntText") or ""
        if desc in ("outcome of board meeting", "financial results") and re.search(r"financial results", text, re.I):
            out["results"] = entry(d, r)
            break
    if not out["results"]:
        return out
    # The press release comes with the results (within 3 days); the call and its transcript within 3 weeks.
    since = _date(out["results"]["date"])
    for key, d, r in reversed(parsed):
        if d < since:
            continue
        desc = (r.get("desc") or "").lower()
        text = r.get("attchmntText") or ""
        days = (d - since).days
        if out["press_release"] is None and desc == "press release" and days <= 3:
            out["press_release"] = entry(d, r)
        if days <= 21 and (desc.startswith("analysts/institutional investor meet") or "con. call" in desc):
            if out["transcript"] is None and re.search(r"transcript", text, re.I):
                out["transcript"] = entry(d, r)
            elif out["recording"] is None and re.search(r"recording|audio|video", text, re.I):
                out["recording"] = entry(d, r)
    return out


def collect_filings(fetcher, sym, now_ist, errors):
    today = now_ist.date()
    first = today - _dt.timedelta(days=FILING_DAYS)
    try:
        rows = fetcher.json(NSE_ANN % (urllib.parse.quote(sym, safe=""), nse_date(first), nse_date(today)),
                            "nse-ann-%s.json" % sym)
    except FactsError as exc:
        errors.append("NSE announcements for %s: %s" % (sym, exc))
        return None, None
    if not isinstance(rows, list):
        errors.append("NSE announcements for %s are not a list (blocked or changed?)" % sym)
        return None, None
    name = None
    for r in rows:
        if r.get("sm_name"):
            name = clean_name(r["sm_name"])
            break
    return latest_filings(rows), name


def next_results(fetcher, sym, now_ist, errors, days=120):
    today = now_ist.date()
    try:
        rows = fetcher.json(NSE_EVENTS % (urllib.parse.quote(sym, safe=""), nse_date(today),
                                          nse_date(today + _dt.timedelta(days=days))),
                            "nse-events-%s.json" % sym)
    except FactsError as exc:
        errors.append("NSE event calendar for %s: %s" % (sym, exc))
        return None
    if not isinstance(rows, list):
        errors.append("NSE event calendar for %s is not a list (blocked or changed?)" % sym)
        return None
    best = None
    for r in rows:
        d = parse_nse_date(r.get("date"))
        if d is None or d < today or d > today + _dt.timedelta(days=days):
            continue
        if not re.search(r"result", r.get("purpose") or "", re.I):
            continue
        if best is None or d < best[0]:
            best = (d, r)
    if best is None:
        return None
    d, r = best
    return {"date": d.isoformat(), "days": (d - today).days, "purpose": (r.get("purpose") or "").strip(),
            "detail": re.sub(r"\s+", " ", r.get("bm_desc") or "").strip()}


def clean_name(name):
    n = html.unescape(name or "").strip()
    return re.sub(r"\s+(?:Limited|Ltd\.?)$", "", n, flags=re.I).strip() or None


def expected_quarter_end(filed):
    """The newest quarter end that a results filing made on `filed` (an ISO date) can be about: a week or more earlier."""
    d = _date(filed) - _dt.timedelta(days=7)
    for back in range(0, 120):
        c = d - _dt.timedelta(days=back)
        if (c + _dt.timedelta(days=1)).day == 1 and c.month in (3, 6, 9, 12):
            return c
    return None


def stale_note(result, filings):
    """A note when the exchange shows results for a quarter newer than Yahoo's latest."""
    res = (filings or {}).get("results")
    if not res or not result["rows"]:
        return None
    want = expected_quarter_end(res["date"])
    latest = _date(result["rows"][0]["end"])
    if want and want > latest and (want - latest).days > 20:
        return ("Yahoo's figures stop at %s, but the NSE shows results filed on %s for the quarter ending about %s. "
                "Read the filing: %s" % (latest.isoformat(), res["date"], want.isoformat(), res["link"] or "(no link)"))
    return None


# ---------------------------------------------------------------- output

def changes(result):
    """Plain sentences: what changed in the latest quarter."""
    rows = result["rows"]
    if not rows:
        return []
    cur = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    out = []
    label = "Quarter ending %s (%s)" % (cur["end"], cur["fiscal"]) if cur["fiscal"] else "Quarter ending %s" % cur["end"]
    line = "%s: revenue %s" % (label, cr_text(cur["revenue"]))
    if cur["revenue_yoy"] is not None:
        line += " (%+.1f%% vs a year earlier)" % cur["revenue_yoy"]
    line += ", net income %s" % cr_text(cur["net_income"])
    if cur["net_income_yoy"] is not None:
        line += " (%+.1f%%)" % cur["net_income_yoy"]
    if cur["eps_diluted"] is not None:
        line += ", diluted EPS Rs %.2f" % cur["eps_diluted"]
        if cur["eps_yoy"] is not None:
            line += " (%+.1f%%)" % cur["eps_yoy"]
    out.append(line + ".")
    for key, name in (("ebitda_margin", "EBITDA margin"), ("pretax_margin", "Pre-tax margin"), ("net_margin", "Net margin")):
        if cur[key] is None:
            continue
        s = "%s %.1f%%" % (name, cur[key])
        if prev and prev[key] is not None:
            s += " (%+.1f points vs the quarter ending %s)" % (cur[key] - prev[key], prev["end"])
        out.append(s + ".")
    if cur["net_income"] is not None and cur["pretax_income"] is not None and cur["net_income"] > cur["pretax_income"]:
        out.append("Net income is above pre-tax income: a tax credit, an exceptional item or profit from associates and "
                   "joint ventures is inside it. Check the results filing before reading it as growth.")
    if prev and cur["revenue"] is not None and prev["revenue"]:
        out.append("Revenue %+.1f%% vs the quarter ending %s (many businesses are seasonal: compare with a year "
                   "earlier first)." % (pct(cur["revenue"], prev["revenue"]), prev["end"]))
    out.append("Yahoo does not say whether these figures are consolidated or standalone: the filing does.")
    return out


def _p(v):
    return "-" if v is None else ("%+.1f%%" % v if v else "0.0%")


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def render(sym, result, filings, nxt=None, errors=None):
    who = result["entity"] if result["entity"].upper() == sym else "%s (%s)" % (result["entity"], sym)
    out = ["%s. Rs crore except EPS. Source: Yahoo Finance quarterly figures; check the filing." % who, ""]
    out.append("%-11s %-8s %10s %8s %7s %7s %7s %10s %8s %7s" % (
        "quarter end", "fiscal", "revenue", "rev yoy", "EBITDA", "pretax", "net", "net inc", "EPS dil", "EPS yoy"))
    for r in result["rows"]:
        out.append("%-11s %-8s %10s %8s %7s %7s %7s %10s %8s %7s" % (
            r["end"], r["fiscal"], cr(r["revenue"]), _p(r["revenue_yoy"]), _pm(r["ebitda_margin"]),
            _pm(r["pretax_margin"]), _pm(r["net_margin"]), cr(r["net_income"]),
            "-" if r["eps_diluted"] is None else "%.2f" % r["eps_diluted"], _p(r["eps_yoy"])))
    t = result["ttm"]
    if t:
        out.append("")
        out.append("Last 4 quarters: revenue %s, net income %s, diluted EPS %s." % (
            cr_text(t.get("revenue")) if "revenue" in t else "-", cr_text(t.get("net_income")) if "net_income" in t else "-",
            "Rs %.2f" % t["eps_diluted"] if t.get("eps_diluted") is not None else "-"))
    out.append("")
    out.append("What changed:")
    for line in changes(result):
        out.append("  - " + line)
    if filings:
        out.append("")
        res, pr, tr, rec = (filings.get(k) for k in ("results", "press_release", "transcript", "recording"))
        if res:
            out.append("Latest results filing (NSE): filed %s  %s" % (res["date"], res["link"] or "(no link)"))
        else:
            out.append("No results filing found in the last %d days of NSE announcements." % FILING_DAYS)
        if pr:
            out.append("Press release: filed %s  %s" % (pr["date"], pr["link"]))
        if tr:
            out.append("Earnings call transcript: filed %s  %s" % (tr["date"], tr["link"]))
        elif rec:
            out.append("Earnings call recording: filed %s  %s" % (rec["date"], rec["link"]))
    if nxt:
        when = "today" if nxt["days"] == 0 else ("tomorrow" if nxt["days"] == 1 else "in %d days" % nxt["days"])
        out.append("Next board meeting for results: %s (%s), %s." % (
            _date(nxt["date"]).strftime("%d-%b-%Y"), when, nxt["purpose"]))
    note = stale_note(result, filings)
    notes = data_notes(result) + ([note] if note else [])
    for e in errors or []:
        notes.append(e)
    if notes:
        out.append("")
        for n in notes:
            out.append("NOTE: " + n)
    return "\n".join(out) + "\n"


def metrics_row(sym, result, as_of):
    rows = result["rows"]
    cur = rows[0] if rows else {}
    t = result["ttm"]
    return {"symbol": sym, "name": result["entity"], "period_end": cur.get("end"), "fiscal": cur.get("fiscal"),
            "revenue_ttm": t.get("revenue"), "net_income_ttm": t.get("net_income"), "eps_ttm": t.get("eps_diluted"),
            "revenue_growth_yoy": cur.get("revenue_yoy"), "eps_growth_yoy": cur.get("eps_yoy"),
            "net_income_growth_yoy": cur.get("net_income_yoy"), "ebitda_margin": cur.get("ebitda_margin"),
            "pretax_margin": cur.get("pretax_margin"), "net_margin": cur.get("net_margin"),
            "currency": result["currency"], "source": "Yahoo Finance quarterly figures (unofficial)"}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("symbols", nargs="*", help="NSE symbols, e.g. ONGC TCS")
    ap.add_argument("--quarters", type=int, default=6)
    ap.add_argument("--no-filings", action="store_true", help="skip the NSE results filing, press release and calendar")
    ap.add_argument("--metrics", metavar="FILE", help="merge a row per symbol into this JSON")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="pretend it is this time, e.g. \"2026-10-25 12:00\" (IST)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.symbols:
        ap.print_help()
        return 2
    try:
        now = parse_now(args.now)
    except FactsError as exc:
        print("facts: %s" % exc, file=sys.stderr)
        return 2
    fetcher = Fetcher(offline=args.offline)
    results, rows, failed = [], [], 0
    for t in args.symbols:
        sym = t.upper()
        errors = []
        try:
            doc = fetch_timeseries(fetcher, sym, now)
            filings, name, nxt = None, None, None
            if not args.no_filings:
                filings, name = collect_filings(fetcher, sym, now, errors)
                nxt = next_results(fetcher, sym, now, errors)
            result = analyse(doc, sym, quarters=args.quarters, name=name)
        except FactsError as exc:
            print("facts: %s: %s" % (sym, exc), file=sys.stderr)
            failed += 1
            continue
        results.append({"symbol": sym, "result": result, "filings": filings, "next_results": nxt,
                        "changes": changes(result), "notes": data_notes(result) + errors,
                        "stale": stale_note(result, filings)})
        rows.append(metrics_row(sym, result, now.date().isoformat()))
        if not args.json:
            sys.stdout.write(render(sym, result, filings, nxt, errors))
            sys.stdout.write("\n")
    if args.json:
        print(json.dumps(results, indent=2))
    if args.metrics and rows:
        existing = {}
        if os.path.isfile(args.metrics):
            with open(args.metrics, encoding="utf-8") as fh:
                existing = {r["symbol"]: r for r in json.load(fh).get("companies", [])}
        for r in rows:
            existing[r["symbol"]] = r
        folder = os.path.dirname(args.metrics)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as fh:
            json.dump({"as_of": now.date().isoformat(),
                       "companies": sorted(existing.values(), key=lambda r: r["symbol"])}, fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("wrote %s (%d companies)" % (args.metrics, len(existing)))
    return 0 if results and not failed else (1 if results else 2)


if __name__ == "__main__":
    sys.exit(main())
