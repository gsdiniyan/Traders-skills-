---
name: trader-scanner
description: >-
  Chart Scanner for the Indian market (NSE). Scans hundreds of charts for
  setups: opening-range breakouts, flags, oversold bounces. Builds the stock
  list from NSE's public files (Nifty 500, the stocks with futures and options,
  or every EQ-series stock), runs a scan of daily closes (20-day closing-high
  breakout, bull flag, reclaimed the 20-day average, oversold, oversold bounce)
  and an intraday opening-range breakout scan on 5-minute bars (09:15 IST
  open), then writes trader/watchlist.json for trader-news and trader-entry.
  Free Yahoo data, bar by bar. Use when the user says "scan the market", "find
  me setups", "what is breaking out", "opening range breakouts", "scan Nifty
  500", "scan F&O stocks", "oversold stocks", "build my watchlist", "what
  should I look at today", or at the start of an NSE trading day before
  /trader-news.
argument-hint: "[universe | holidays | daily | orb] [--at HH:MM]"
---

# trader-scanner (India)

The Chart Scanner is the first step of a day trade. It reads hundreds of free
charts and lists the ones that match a rule. That is all it does. A scan is a
list of charts that match a rule, not a list of stocks to buy, and a setup on
a chart is not a prediction. Most people who day trade lose money; the scanner
does not change that. It only saves you from flipping through hundreds of
charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # Nifty 500 -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --set fo              # only stocks with futures and options
python3 "${CLAUDE_SKILL_DIR}/scan.py" holidays                       # NSE's holiday list -> trader/holidays.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # after the close or before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,INFY   # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 09:35 IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                 # cut the clock at 10:15 IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--min-price 50` and `--min-volume` (daily 500,000 shares, orb 0),
`--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-23 10:15"` (IST) to pin the clock, `--offline PATH` to read
saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0 when the
scan found something, 1 when it found nothing or could not run (the reason is
one plain line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/holidays.json` | written by `holidays`: NSE's trading-holiday dates, merged by every tool of this pack |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `market`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:30) |

Symbols are NSE symbols as NSE writes them (`RELIANCE`, `M&M`, `BAJAJ-AUTO`).
The tools add `.NS` for Yahoo. The indices work too: `NIFTY` and `BANKNIFTY`.

## The universe

`universe` downloads one of NSE's public lists (nsearchives.nseindia.com) and
keeps the plain equity shares, series EQ:

| `--set` | what | about |
| --- | --- | --- |
| `nifty500` (default) | the Nifty 500 constituents, liquid names | 500 |
| `fo` | stocks with futures and options (`fo_mktlots.csv`); the index contracts NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY are left out | 215 |
| `all` | every EQ-series stock on NSE, thin names included | 2,360 |

Dropped: other series (BE trade-for-trade, BZ, RR and the like). Run it once a
week or so. SME stocks and ETFs are not in these lists.

## The calendar

The 2026 NSE trading holidays are built in (20 dates, read from NSE's holiday
master on 2026-10-25). `scan.py holidays` reads NSE's list again and saves it
to `trader/holidays.json`; every tool of the pack merges that file, so a
special closure or next year's list is picked up. Until NSE publishes 2027 the
tools treat every 2027 weekday as a trading day and say so. Extra closures can
also be set with `NSE_EXTRA_HOLIDAYS="2027-01-26,2027-03-25"`. Saturday and
Sunday are closed. The Diwali Laxmi Pujan Muhurat session is not covered.

## The daily scan (after the close or before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so 500 names
take 25 requests. Liquidity comes from the spark meta: last price at least
`--min-price` (Rs 50) and the session's volume at least `--min-volume`
(500,000 shares). Run it after the close or before the open: during the
session the volume is the day's so far, and today's daily bar is left out until
15:30 IST.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Hits are listed most traded first (price times volume, so rupees traded).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 09:15,
09:20 and 09:25 five-minute bars. A hit is the first completed 5-minute bar to
CLOSE above the range high, at that bar's close time.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a request.
   A chart goes on only if a completed bar after the range closed at or above
   the highest close of the range. That is a necessary condition (the range
   high is at least every range close), so no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one request
   each. Range high = the highest high of the range bars, range low = the
   lowest low. Stage 2 checks at most `--max-confirm` (200) candidates, the most
   traded first, and prints PARTIAL with the count it skipped. `--max-confirm 0`
   checks them all.

A chart is usable only when every 5-minute bar from 09:15 to the cutoff is
there; a missing bar means a halt or a thin stock. Null closes are dropped, a
bar off the 5-minute grid (the live tick, like 10:58:07) is dropped, and a bar
counts only once it is complete (start + 5 minutes). Before 09:35, on a weekend
or an NSE holiday, after the close, or after `no_entries_after` (an expired
setup) it prints one plain line and writes nothing.

## Data and limits

- Times are Indian Standard Time (UTC+5:30, no daylight saving). Regular
  session 09:15 to 15:30.
- Yahoo's chart and spark endpoints are free, unofficial and can lag; NSE
  stocks are the symbol plus `.NS`. Bars are 5-minute bars, bar by bar, never
  tick by tick. query2 first, query1 on a 429 or 5xx, one retry after a 5-second
  wait on a 429. Yahoo keeps about 60 days of 5-minute history (5 sessions for
  the `5d` range this tool asks for).
- NSE's servers stall on a User-Agent that contains a web address; the tools use
  a plain one.
- Partial results are said as partial: "Fetched 480 of 500 charts (20 failed or
  had no data)". Never round a partial scan up to "the whole market".

## Steps

1. Once a week or so: `scan.py universe` (and `scan.py holidays` once in a while).
2. After the close or before the open: `scan.py daily`. Tell the user the counts
   (fetched, liquid, scanned, hits) and the top few by setup.
3. After 09:35 IST (10:15 is the classic): `scan.py orb`. Tell the user how many
   usable charts broke their range and when, and that a later hit is closer to
   the price now.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL` (what is moving
   it), then `/trader-entry SYMBOL` (the entry bar by bar). Never pick for them.

## Example: a quiet Friday

Friday Oct 23 2026, replaying 25 large NSE names at 10:15 IST from the saved
charts in `tests/fixtures/trader-scanner/wall5m-2026-10-23.json.gz`:

```
python3 scan.py orb --offline wall5m-2026-10-23.json.gz --at 10:15 --min-price 0
Fetched 25 of 25 charts; 25 usable; 5 candidates, 5 confirmed with real highs and lows.
1 of 25 charts closed above their opening-range high by 10:15:
   10:05  ONGC          Rs 225.74  range 224.61 to 225.62  +0.05% over
```

A weak, falling morning: only ONGC cleared its range, and by 0.05 %. That is
what the scanner is for: it tells you when there is nothing to do. The daily
scan of the same names (closes through Oct 23) tags TCS a 20-day closing-high
breakout and a bull flag, INFY a bull flag, ONGC a reclaim of the 20-day
average, and ADANIENT, TITAN and NTPC oversold.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not investment advice. The author is not a SEBI-registered
  investment adviser or research analyst. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision.
- Nothing in this pack places an order. trader-broker only prepares order tickets that the
  user places themselves in their own broker app.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables, never in the chat.
