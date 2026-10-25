#!/usr/bin/env python3
"""Big-money and insider tracker for trader-insider (India, NSE). Standard library only.

Two public disclosures that show who is trading a stock:

  deals     NSE bulk and block deals: a client who buys or sells more than 0.5% of a company's shares in
            one day is named in the exchange's bulk-deal list the same evening; block deals are the large
            single trades done in the exchange's special windows.
  insiders  SEBI insider-trading disclosures (PIT regulations): promoters, directors, key managers,
            designated employees and their relatives report their trades in the company's shares. The
            insider has two trading days to tell the company and the company two more to tell the exchange,
            so these come days after the trade.

    python3 insider.py deals                           # every bulk and block deal of the latest trading day, grouped
    python3 insider.py deals --symbols ONGC RELIANCE   # only those stocks
    python3 insider.py deals --who vanguard --min-cr 5 # a client by name, deals of Rs 5 crore or more
    python3 insider.py insiders RELIANCE --days 90     # what insiders of a company filed
    python3 insider.py new --holdings --who "mirae"    # only what is new since the last check

Sources (free, unofficial, no keys; NSE can change or block them without notice):
  https://nsearchives.nseindia.com/content/equities/bulk.csv       the latest trading day's bulk deals
  https://nsearchives.nseindia.com/content/equities/block.csv      the latest trading day's block deals
  https://www.nseindia.com/api/corporates-pit?index=equities&symbol=RELIANCE&from_date=..&to_date=..
The deal files hold ONE day. Run `new` once a day after about 19:00 IST (weekdays) to build the history in
trader/state/insider.json. Insider filings are listed by NSE with a date; an empty answer is not proof that
nobody traded (on 2026-10-25 NSE's answers for several large companies stopped months back).

Fast, not early: a bulk deal is public the evening it happens and an insider filing days later. There is no
inside information here, only public filings read quickly. Offline: --offline DIR reads bulk.csv, block.csv and
pit-<SYMBOL>.json from DIR. Times and dates are IST. Not investment advice.
"""

import argparse
import csv
import datetime as _dt
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BULK_URL = "https://nsearchives.nseindia.com/content/equities/bulk.csv"
BLOCK_URL = "https://nsearchives.nseindia.com/content/equities/block.csv"
PIT_URL = ("https://www.nseindia.com/api/corporates-pit?index=equities&symbol=%s"
           "&from_date=%s&to_date=%s")
UA = "Mozilla/5.0"
IST_OFFSET = _dt.timedelta(hours=5, minutes=30)
KEEP_DAYS = 60
LATE_TRADING_DAYS = 4     # 2 for the insider to tell the company + 2 for the company to tell the exchange
NOTE = ("Public disclosures: a bulk deal is public the evening it happens, an insider filing days later. No inside "
        "information here, and neither is a reason to trade on its own. Not investment advice.")


class InsiderError(Exception):
    pass


# ---------------------------------------------------------------- time and money

def parse_now(text):
    """--now "2026-10-25 12:00" is IST; with Z or an offset it is taken as written. Returns naive IST."""
    if not text:
        return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None, microsecond=0) + IST_OFFSET
    try:
        d = _dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise InsiderError("--now wants a time like \"2026-10-25 12:00\" (IST) or ...Z (UTC)")
    if d.tzinfo is None:
        return d
    return d.astimezone(_dt.timezone.utc).replace(tzinfo=None) + IST_OFFSET


def parse_nse_date(value):
    """"23-OCT-2026", "18-Feb-2026 19:06" or "18-Feb-2026 19:06:00" -> date, or None."""
    value = (value or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return _dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def nse_date(d):
    return d.strftime("%d-%m-%Y")


def indian(n):
    """12345678 -> "1,23,45,678"."""
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


def rupees(v):
    """Rs 12,345 / Rs 4.56 lakh / Rs 119.30 crore."""
    if v is None:
        return "n/a"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e7:
        return "%sRs %s crore" % (sign, "{:,.2f}".format(a / 1e7))
    if a >= 1e5:
        return "%sRs %.2f lakh" % (sign, a / 1e5)
    return "%sRs %s" % (sign, indian(round(a)))


def weekdays_between(a, b):
    """Weekdays after date a up to and including date b (holidays are not subtracted)."""
    if b <= a:
        return 0
    n, d = 0, a
    while d < b:
        d += _dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json", plan="trader/plan.json"):
    """Symbols you hold, are planning, or the scanner listed: {symbol: True if held}."""
    out = {}
    for h in (load_json(watchlist) or {}).get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out.setdefault(sym.upper(), False)
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(pl["symbol"].upper(), False)
    for p in (load_json(portfolio) or {}).get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[sym.upper()] = True
    return out


def held_symbols(portfolio="trader/portfolio.json"):
    return {str(p.get("ticker") or p.get("symbol") or "").upper() for p in (load_json(portfolio) or {}).get("positions", [])}


def name_match(query, name):
    """Every word of the query starts a word of the name: "mirae" matches "MIRAE ASSET MUTUAL FUND"."""
    q = re.findall(r"[a-z0-9]+", (query or "").lower())
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return bool(q) and all(any(w.startswith(t) for w in words) for t in q)


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
                raise InsiderError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"})
        host = url.split("/")[2]
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise InsiderError("HTTP %s from %s" % (exc.code, host))
        except urllib.error.URLError as exc:
            raise InsiderError("could not reach %s: %s" % (host, exc.reason))
        except OSError as exc:
            raise InsiderError("could not read %s: %s" % (host, exc))


# ---------------------------------------------------------------- bulk and block deals

def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def deal_id(row):
    key = "|".join(str(row[k]) for k in ("kind", "date", "symbol", "client", "side", "qty", "price"))
    return "deal:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def parse_deals(data, kind):
    """bulk.csv or block.csv text -> deal rows. "NO RECORDS" is an empty day."""
    text = data.decode("utf-8-sig", "replace") if isinstance(data, bytes) else data
    reader = csv.reader(io.StringIO(text))
    try:
        head = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        raise InsiderError("the %s deals file is empty" % kind)
    if "symbol" not in head or "client name" not in head:
        raise InsiderError("the %s deals file is not what NSE usually sends (blocked or changed?)" % kind)
    ix = {name: head.index(name) for name in head}
    price_key = next((h for h in head if h.startswith("trade price")), None)
    rows = []
    for rec in reader:
        if not rec or not rec[0].strip() or rec[0].strip().upper().startswith("NO RECORDS"):
            continue
        d = parse_nse_date(rec[ix["date"]])
        try:
            qty = int(rec[ix["quantity traded"]].replace(",", ""))
            price = float(rec[ix[price_key]].replace(",", "")) if price_key else 0.0
        except (ValueError, KeyError, IndexError):
            continue
        row = {"kind": kind, "date": d.isoformat() if d else "", "symbol": _clean(rec[ix["symbol"]]).upper(),
               "name": _clean(rec[ix["security name"]]), "client": _clean(rec[ix["client name"]]),
               "side": _clean(rec[ix["buy/sell"]]).upper(), "qty": qty, "price": price, "value": qty * price,
               "remarks": _clean(rec[ix["remarks"]]) if "remarks" in ix and len(rec) > ix["remarks"] else ""}
        row["id"] = deal_id(row)
        rows.append(row)
    return rows


def group_deals(rows):
    """One line per (kind, date, symbol, client, side): total quantity and the weighted average price."""
    groups = {}
    for r in rows:
        key = (r["kind"], r["date"], r["symbol"], r["client"], r["side"])
        g = groups.setdefault(key, {"kind": r["kind"], "date": r["date"], "symbol": r["symbol"], "name": r["name"],
                                    "client": r["client"], "side": r["side"], "qty": 0, "value": 0.0, "rows": 0,
                                    "ids": []})
        g["qty"] += r["qty"]
        g["value"] += r["value"]
        g["rows"] += 1
        g["ids"].append(r["id"])
    out = []
    sides = {}
    for key in groups:
        sides.setdefault(key[:3] + (key[3],), set()).add(key[4])
    for key, g in groups.items():
        g["price"] = round(g["value"] / g["qty"], 2) if g["qty"] else 0.0
        g["both_sides"] = len(sides[key[:4]]) > 1
        out.append(g)
    out.sort(key=lambda g: (-g["value"], g["symbol"]))
    return out


def filter_deals(groups, symbols=None, who=None, min_value=0.0, held=(), either=False):
    """Keep the groups for these symbols and/or these client names, at least this rupee value. With both a symbol
    list and a name list, a deal must match both, or either one when `either` is set (watching holdings and a fund)."""
    out = []
    for g in groups:
        g["held"] = g["symbol"] in held
        if g["value"] < min_value:
            continue
        by_symbol = (g["symbol"] in symbols) if symbols else None
        by_who = any(name_match(w, g["client"]) for w in who) if who else None
        checks = [c for c in (by_symbol, by_who) if c is not None]
        if checks and not (any(checks) if either else all(checks)):
            continue
        out.append(g)
    return out


def render_deal(g):
    mark = "!" if g.get("held") else " "
    rows = "" if g["rows"] == 1 else ", %d rows" % g["rows"]
    both = "  (same client on both sides that day: a trading firm, not a view)" if g.get("both_sides") else ""
    return "%s %-5s %s  %-10s %-4s %s: %s shares at Rs %.2f = %s%s%s%s" % (
        mark, g["kind"].upper(), g["date"], g["symbol"], g["side"], g["client"], indian(g["qty"]), g["price"],
        rupees(g["value"]), rows, both, "  YOU HOLD IT" if g.get("held") else "")


def collect_deals(fetcher, kinds=("bulk", "block"), errors=None):
    errors = [] if errors is None else errors
    rows = []
    for kind in kinds:
        url = BULK_URL if kind == "bulk" else BLOCK_URL
        try:
            rows += parse_deals(fetcher.get(url, "%s.csv" % kind), kind)
        except InsiderError as exc:
            errors.append("%s deals: %s" % (kind, exc))
    return rows


# ---------------------------------------------------------------- insider disclosures

def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_pit(data, symbol):
    """NSE corporates-pit JSON -> insider rows."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        rows = doc["data"]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InsiderError("NSE's insider answer for %s is not what it usually sends (blocked or changed?)" % symbol)
    if not isinstance(rows, list):
        raise InsiderError("NSE's insider answer for %s has no data list (blocked or changed?)" % symbol)
    out = []
    for r in rows:
        kind = _clean(r.get("tdpTransactionType"))
        trade_from, trade_to = parse_nse_date(r.get("acqfromDt")), parse_nse_date(r.get("acqtoDt"))
        told = parse_nse_date(r.get("intimDt"))
        shown = parse_nse_date(r.get("date"))
        qty = _num(r.get("secAcq"))
        val = _num(r.get("secVal"))
        if kind.lower() == "buy" and not val:
            val = _num(r.get("buyValue"))
        if kind.lower() == "sell" and not val:
            val = _num(r.get("sellValue"))
        last = trade_to or trade_from
        delay = weekdays_between(last, shown) if (last and shown) else None
        rid = "pit:%s:%s" % (r.get("did") or r.get("pid") or "", hashlib.sha1(
            "|".join(str(r.get(k)) for k in ("acqName", "acqfromDt", "acqtoDt", "secAcq", "tdpTransactionType",
                                              "date")).encode("utf-8")).hexdigest()[:10])
        out.append({"id": rid, "symbol": _clean(r.get("symbol")).upper() or symbol, "company": _clean(r.get("company")),
                    "person": _clean(r.get("acqName")), "category": _clean(r.get("personCategory")),
                    "type": kind, "mode": _clean(r.get("acqMode")), "security": _clean(r.get("secType")),
                    "quantity": qty, "value": val,
                    "trade_from": trade_from.isoformat() if trade_from else "",
                    "trade_to": trade_to.isoformat() if trade_to else "",
                    "told": told.isoformat() if told else "", "filed": shown.isoformat() if shown else "",
                    "weekdays_to_file": delay, "late": bool(delay is not None and delay > LATE_TRADING_DAYS),
                    "before_pct": _num(r.get("befAcqSharesPer")), "after_pct": _num(r.get("afterAcqSharesPer")),
                    "link": r.get("xbrl") or ""})
    return out


def buy_or_sell(row):
    t = row["type"].lower()
    return "buy" if t == "buy" else ("sell" if t == "sell" else "other")


def render_insider(r, held=False):
    qty = "n/a" if r["quantity"] is None else indian(r["quantity"])
    when = r["trade_to"] or r["trade_from"]
    if r["trade_from"] and r["trade_to"] and r["trade_from"] != r["trade_to"]:
        when = "%s to %s" % (r["trade_from"], r["trade_to"])
    delay = ""
    if r["weekdays_to_file"] is not None:
        delay = ", filed %d weekday%s after the trade%s" % (r["weekdays_to_file"], "" if r["weekdays_to_file"] == 1 else "s",
                                                           " (LATE: the rules allow 2 + 2 trading days)" if r["late"] else "")
    pct = ""
    if r["before_pct"] is not None and r["after_pct"] is not None and (r["before_pct"] or r["after_pct"]):
        pct = ", holding %.2f%% -> %.2f%%" % (r["before_pct"], r["after_pct"])
    return "%s %-10s %-5s %s (%s): %s %s shares, %s, %s%s%s%s" % (
        "!" if held else " ", r["symbol"], r["type"].upper() if r["type"] else "?", r["person"], r["category"] or "insider",
        r["mode"] or "mode not given", qty, rupees(r["value"]), when, delay, pct, "  YOU HOLD IT" if held else "")


def collect_insiders(fetcher, symbols, now, days=30, errors=None):
    errors = [] if errors is None else errors
    today = now.date()
    first = today - _dt.timedelta(days=days)
    out = []
    for s in symbols:
        try:
            data = fetcher.get(PIT_URL % (urllib.parse.quote(s, safe=""), nse_date(first), nse_date(today)), "pit-%s.json" % s)
            out += parse_pit(data, s)
        except InsiderError as exc:
            errors.append("insider filings for %s: %s" % (s, exc))
    out.sort(key=lambda r: (r["filed"], r["trade_to"]), reverse=True)
    return out


# ---------------------------------------------------------------- state

def remember(state, ids, now):
    seen = state.setdefault("seen", {})
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S")
    for i in ids:
        seen.setdefault(i, stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def save_state(path, state):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------- commands

def _symbols(a):
    syms = [s.upper() for s in (a.symbols or [])]
    if getattr(a, "holdings", False):
        for s in watch_from_files(a.portfolio, a.watchlist, a.plan):
            if s not in syms:
                syms.append(s)
    return syms


def _deal_view(rows, a, held):
    groups = group_deals(rows)
    if a.rows:
        both = {i: g["both_sides"] for g in groups for i in g["ids"]}
        groups = sorted(({"kind": r["kind"], "date": r["date"], "symbol": r["symbol"], "name": r["name"],
                          "client": r["client"], "side": r["side"], "qty": r["qty"], "price": r["price"],
                          "value": r["value"], "rows": 1, "ids": [r["id"]], "both_sides": both[r["id"]]} for r in rows),
                        key=lambda g: (-g["value"], g["symbol"]))
    return filter_deals(groups, set(_symbols(a)) or None, a.who or None, (a.min_cr or 0) * 1e7, held)


def cmd_deals(a):
    errors = []
    kinds = ("bulk", "block") if a.kind == "both" else (a.kind,)
    rows = collect_deals(Fetcher(a.offline), kinds, errors)
    held = held_symbols(a.portfolio)
    shown = _deal_view(rows, a, held)
    days = sorted({r["date"] for r in rows})
    if a.json:
        print(json.dumps({"dates": days, "deals": shown, "notes": errors}, indent=2))
    else:
        if days:
            print("NSE bulk and block deals for %s%s." % (", ".join(days), "" if len(shown) == len(group_deals(rows)) else
                                                           " (filtered: %d of %d lines)" % (len(shown), len(group_deals(rows)))))
        for g in shown[:a.limit]:
            print(render_deal(g))
        if len(shown) > a.limit:
            print("  ... and %d more (--limit N for more, or filter with --symbols, --who, --min-cr)." % (len(shown) - a.limit))
        if not shown:
            print("No deals match." if rows else "No deals found.")
        for e in errors:
            print("note: " + e)
        print(NOTE)
    return 0 if shown else 1


def cmd_insiders(a):
    now = parse_now(a.now)
    syms = _symbols(a)
    if not syms:
        raise InsiderError("which stocks? insider.py insiders RELIANCE ITC, or --holdings")
    errors = []
    rows = collect_insiders(Fetcher(a.offline), syms, now, a.days, errors)
    held = held_symbols(a.portfolio)
    if not a.all_types:
        rows = [r for r in rows if buy_or_sell(r) != "other"]
    if a.who:
        rows = [r for r in rows if any(name_match(w, r["person"]) for w in a.who)]
    if a.json:
        print(json.dumps({"as_of": now.strftime("%Y-%m-%d %H:%M"), "filings": rows, "notes": errors}, indent=2))
    else:
        for r in rows:
            print(render_insider(r, r["symbol"] in held))
            if r["link"]:
                print("      " + r["link"])
        if not rows:
            print("No insider buys or sells filed in the last %d days for %s." % (a.days, ", ".join(syms)))
            print("An empty answer is not proof that nobody traded: NSE's list can lag or stop. Check the company's page on "
                  "nseindia.com (Corporate Filings, Insider Trading).")
        for e in errors:
            print("note: " + e)
        print(NOTE)
    return 0 if rows else 1


def cmd_new(a):
    now = parse_now(a.now)
    syms = _symbols(a)
    if not syms and not a.who:
        raise InsiderError("what to watch? give --who NAME (a fund or investor), --symbols, or --holdings")
    errors = []
    state = load_json(a.state) or {}
    seen = state.get("seen", {})
    held = held_symbols(a.portfolio)
    fetcher = Fetcher(a.offline)
    deal_rows = collect_deals(fetcher, ("bulk", "block"), errors)
    deals = filter_deals(group_deals(deal_rows), set(syms) or None, a.who or None, (a.min_cr or 0) * 1e7, held,
                         either=True)
    new_deals = [g for g in deals if any(i not in seen for i in g["ids"])]
    ins = []
    if syms:
        ins = [r for r in collect_insiders(fetcher, syms, now, a.days, errors) if buy_or_sell(r) != "other"]
    new_ins = [r for r in ins if r["id"] not in seen]
    if a.json:
        print(json.dumps({"deals": new_deals, "insiders": new_ins, "notes": errors}, indent=2))
    else:
        for g in new_deals:
            print("NEW " + render_deal(g).lstrip())
        for r in new_ins:
            print("NEW " + render_insider(r, r["symbol"] in held).lstrip())
            if r["link"]:
                print("      " + r["link"])
        if not new_deals and not new_ins:
            print("Nothing new.")
        for e in errors:
            print("note: " + e)
        print(NOTE)
    if not a.dry_run:
        ids = [i for g in deals for i in g["ids"]] + [r["id"] for r in ins]
        remember(state, ids, now)
        save_state(a.state, state)
    return 0 if (new_deals or new_ins) else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Big-money and insider tracker (trader-insider, NSE).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p, symbols=True):
        p.add_argument("--offline", metavar="DIR", help="read bulk.csv, block.csv, pit-<SYMBOL>.json from DIR")
        p.add_argument("--now", help="pretend it is this time, e.g. \"2026-10-25 12:00\" (IST)")
        p.add_argument("--json", action="store_true")
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--watchlist", default="trader/watchlist.json")
        p.add_argument("--plan", default="trader/plan.json")
        p.add_argument("--who", action="append", metavar="NAME", help="a client or person by name (repeatable)")
        p.add_argument("--holdings", action="store_true", help="add the symbols from your portfolio, plan and watchlist")

    p = sub.add_parser("deals", help="bulk and block deals of the latest trading day")
    common(p)
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--kind", choices=["bulk", "block", "both"], default="both")
    p.add_argument("--min-cr", type=float, help="only deals of at least this many rupees crore")
    p.add_argument("--rows", action="store_true", help="every trade row, not grouped by client")
    p.add_argument("--limit", type=int, default=40)
    p = sub.add_parser("insiders", help="SEBI insider-trading disclosures for stocks")
    common(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--all-types", action="store_true", help="include pledges and other non-trades")
    p = sub.add_parser("new", help="only what is new since the last check")
    common(p)
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--min-cr", type=float)
    p.add_argument("--state", default="trader/state/insider.json")
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        return {"deals": cmd_deals, "insiders": cmd_insiders, "new": cmd_new}[a.cmd](a)
    except InsiderError as exc:
        print("insider.py: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
