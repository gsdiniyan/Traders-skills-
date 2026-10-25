---
name: trader-alerts
description: >-
  Price Alerts for NSE stocks and indices. Rings when a price, a stop or the opening
  bell hits. Turns the trade plan into three alerts in one go (the stop, the target and
  the flat-by time), plus above / below / stop / target levels, percent moves from the
  previous close, any time of day, and the NSE opening bell, each ringing once so you
  are not spammed. Knows NSE hours (09:15 to 15:30 IST) and the 2026 trading holidays,
  works out IST itself, fetches free delayed Yahoo quotes (RELIANCE.NS style, indices
  too) one symbol at a time, and writes trader/prices.json for sizing and the risk
  check. Use when the user says "alert me when", "tell me if ONGC hits", "watch my
  stop", "watch my trade", "remind me to close by 3:15", "is the market open", "when
  does the market open", "get me a price", or right after /trader-exit writes a plan.
argument-hint: "[symbol] [price]"
---

# trader-alerts (India)

Price Alerts watches the numbers so the user does not have to stare at a chart. An alert is
information, never an order: when a stop alert rings, nothing is sold by this skill. The stop-loss
and target orders the user placed at their broker do the work; the alerts tell the user what
happened and when it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat-by time from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol ONGC --stop 225.10
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol ONGC --target 227
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:15 --label "flat-by time"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol ITC --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote ONGC NIFTY --write       # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan symbols
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset ongc-plan-stop
```

`--now "2026-10-23 15:29"` pins the clock (IST); `--offline DIR` reads saved quote files
(`yahoo-ONGC.json`) instead of the network.

Symbols are NSE symbols (`ONGC`, `RELIANCE`, `M&M`, `BAJAJ-AUTO`) and the index names `NIFTY`,
`BANKNIFTY`, `SENSEX`, `INDIAVIX`. An index is shown in points, a share in rupees.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |
| `trader/holidays.json` | NSE holiday dates from `scan.py holidays` (trader-scanner), merged with the built-in list |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:15") on a trading day | once per trading day |
| `opening_bell` | the NSE is open | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan` rules carry the plan's date, so
yesterday's stop never rings today.

## Quotes

- `yahoo` (the only source): `query2.finance.yahoo.com/v8/finance/chart/ONGC.NS`, an unofficial free JSON
  endpoint, one request per symbol, half a second apart. On a 429 it waits and tries once more; on a 429 or
  a 5xx after that it tries `query1`. It sends a plain `Mozilla/5.0` user agent. Indices use `^NSEI` and
  friends; the tool maps the names for you.
- NSE's own quote pages need a browser session and cookies, so the pack does not call them.
- Free quotes are unofficial, delayed and can lag by minutes. They are fine for alerts and for checking a size,
  not for timing an order. The broker's price is the one that fills. Each price in `trader/prices.json` keeps its
  quote time, and trader-risk only uses one under 15 minutes old.
- If Yahoo is down, the user can read the price off their broker app and write it into `trader/prices.json` as
  `{"as_of": "2026-10-23T09:58:00Z", "prices": {"ONGC": 227.50}}`.

## The NSE clock

Regular session 09:15 to 15:30 IST, Monday to Friday. IST is UTC+5:30 all year, so there is no daylight saving
to work out. The built-in 2026 list is NSE's trading holiday master (read 2026-10-25): Jan 15, Jan 26, Feb 15,
Mar 3, Mar 21, Mar 26, Mar 31, Apr 3, Apr 14, May 1, May 28, Jun 26, Aug 15, Sep 14, Oct 2, Oct 20, Nov 8, Nov 10,
Nov 24, Dec 25. Some fall on a weekend. `scan.py holidays` fetches NSE's list again into
`trader/holidays.json`, and `NSE_EXTRA_HOLIDAYS="2027-01-26"` adds dates by hand. For a year it does not know, the
clock says so.

The NSE has no early closes. Its one-hour Diwali Muhurat session in the evening is not covered: the clock calls
that day a holiday, so check nseindia.com if the user wants to trade it.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm the three alerts back to the user:
   the stop, the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes from a Claude scheduled task or `/loop`. Each run
   fetches only the symbols in today's rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop or target alert means the exit order at the broker has
   probably filled: the user checks the positions page and records the fill with trader-broker. The flat-by time
   means close anything still open: trader-broker `flatten`, then trader-risk, then the user's yes. Never act for
   them.

## Example: ONGC, Friday 2026-10-23

Entry 225.74 at 10:05, stop 225.10, target 227.00, flat by 15:15. `add --from-plan` writes `ongc-plan-stop`,
`ongc-plan-target` and `ongc-flat-by` for that day. A `check` at 10:16 with ONGC at 226.30, between the stop and
the target:

```
NSE OPEN (2026-10-23 10:16 IST, regular session).
Nothing hit. 3 rule(s) watched today, ONGC Rs 226.30.
```

At 15:15:

```
ALERT Flat-by time: it is 15:15 IST (set for 15:15), the market closes at 15:30. If ONGC is still open, close it
before the broker's own square-off (from about 15:15): trader-broker flatten --symbol ONGC builds the sell
ticket, then trader-risk and your yes.
```

It rings once. The real last quote that day was Rs 227.50 at 15:28 IST, so a check at 15:29 also rings
`ALERT ONGC is Rs 227.50: at or above your target Rs 227 (plan target: 2R (two times the risk))`. The next
morning the plan's three alerts are no longer watched; with `add --opening-bell`, the bell rings at 09:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: symbol, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is live, and never predicts where a price
is going.

## Safety

- Education and tooling, not investment advice. The author is not a SEBI-registered
  investment adviser or research analyst. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision.
- Nothing in this pack places an order. trader-broker only prepares order tickets that the
  user places themselves in their own broker app.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables, never in the chat.
