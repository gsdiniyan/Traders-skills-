# Traders Skills (India)

11 Claude skills for day trading on the NSE: scan hundreds of charts, track announcements and news, time every entry and exit, and replay every outcome before you risk a rupee.

Free, MIT, no signup, no API key. The tools are plain Python that read free public data: Yahoo Finance charts and fundamentals, NSE's symbol lists, holiday master, corporate announcements, event calendar, bulk and block deals, and SEBI insider disclosures published on NSE.

One scans Nifty 500 charts for setups. Another reads what each company told the exchange and what the Indian press is saying. One watches a setup bar by bar and tells you the moment to get in, and another sets your stop and your target before you do. The Time Machine replays the rest of the session 10,000 times from the stock's own recent bars, plus every past day the same setup fired, and tells you the odds.

No order is ever placed by this pack. It prepares a ticket and you place it yourself in your own broker app (Dhan or any other), or you paper trade it by writing it in the log. Paper trading is the default.

This is the Indian-market port of [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) by Jake Schincariol (MIT). The eleven skills are rebuilt for NSE: IST and the 09:15 to 15:30 session, the NSE holiday calendar, rupees with Indian digit grouping, the Rs 0.05 price tick, lot sizes, Indian intraday charges, NSE and SEBI data sources, and no pattern-day-trader rule (that is a US rule). The order executor became a ticket desk, and the Congress-trades tracker became a bulk-deal and insider-filing tracker. See [NOTICE](NOTICE).

## Install

Paste this into Claude:

```
https://github.com/gsdiniyan/Traders-skills-

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add gsdiniyan/Traders-skills-
/plugin install traders-skills@traders-skills
```

Claude Code namespaces plugin skills, so installed as a plugin they show up as `/traders-skills:trader-scanner` and so on. Copy the folders instead if you want plain `/trader-scanner`:

```bash
git clone https://github.com/gsdiniyan/Traders-skills-.git
cp -r Traders-skills-/trader-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's `.claude/skills/`. No Claude Code at all? Paste any single `SKILL.md` at the top of a chat and it runs as a mode. You lose the Python tools, but the method works.

The tools need Python 3.8 or newer. Nothing to pip install, nothing to sign up for.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Scans Nifty 500 (or the F&O stocks) for setups: opening-range breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Reads NSE announcements, the results calendar and Indian headlines for every stock you watch, and flags what moves it. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Sets your stop and your target before you enter, on the NSE price tick. |
| `/trader-timemachine` | Time Machine | Replays every possible outcome of a trade before you risk a rupee: 10,000 simulated sessions plus every past time the setup fired. |
| `/trader-risk` | Risk Manager | Checks every trade against your limits, the NSE calendar and the price tick, and stops anything over them. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade, with the intraday charges and the break-even price. |
| `/trader-broker` | Order Ticket Desk | Prepares the ticket for your broker app, records your yes, and logs fills. Never places an order. |
| `/trader-alerts` | Price Alerts | Rings when a price, a stop or the opening bell hits. |
| `/trader-analyst` | Earnings Analyst | Lays out the quarterly numbers in rupees crore, checks them, and links the results filing. |
| `/trader-insider` | Big-Money and Insider Tracker | Reads NSE bulk and block deals and SEBI insider filings, and tells you what is new. |

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by 15:15                                           (while it is open)
news / analyst / insider                                                      (on a schedule)
```

An example, one trade on real data: ONGC, Friday October 23 2026.

1. **`/trader-scanner`** at 10:15 IST: of the 25 charts in the saved scan, 1 had closed above its 15-minute opening
   range. ONGC: range Rs 224.61 to Rs 225.62, the 10:00 bar closed at Rs 225.74 (closing at 10:05), 0.05 % over the top.
2. **`/trader-news`**: at 10:15 the one ONGC headline of the last day was the evening before's "Sunil Kumar to be
   next ONGC Chairman" (BusinessLine, 20:02), and the event calendar shows the results board meeting on 5
   November, 13 days out. A day trade is out well before that.
3. **`/trader-entry`** watched ONGC bar by bar. At 10:03 it said waiting (the 10:00 bar was still forming). At 10:07 it
   said triggered: the first 5-minute close above the range, Rs 225.74.
4. **`/trader-exit`** set the plan before the entry: stop at the range's midpoint, Rs 225.10, so Rs 0.64 a share at
   risk; target two times that risk, Rs 227.00; flat by 15:15 either way.
5. **`/trader-timemachine`** replayed the rest of the day 10,000 times, each 5-minute candle drawn from ONGC's own
   candles at that time of day over its last 51 full sessions (seed 19, so anyone can repeat it): target first
   22.9 %, stop first 75.2 %, still open at 15:20 2.0 %. On those 51 real sessions the same rule fired 20 times:
   3 hit the target, 11 the stop, 6 neither. Its verdict: leans toward the stop. That is the point of looking
   before you risk a rupee.
6. **`/trader-budget`** with a Rs 1,00,000 account and a Rs 1,000 max loss per trade: the stop would allow 1,562
   shares, but the 75 % position cap holds it to 332 shares (Rs 74,945.68). If the stop fills at Rs 225.10 the loss
   is Rs 212.48, plus about Rs 73.55 of intraday charges. The trade has to reach Rs 225.96 just to break even. It
   writes one bracket order: buy 332 ONGC, stop-loss 225.10, target 227.00.
7. **`/trader-risk`** checks it against your limits: the max loss, the daily loss limit, trades per day, reward to
   risk, the entry window (09:30 to 14:30 IST), position size and cash, the NSE calendar and the price tick. PASS or
   BLOCK, with the reason.
8. **`/trader-broker`** shows you the ticket in plain words, with the steps to type into your broker app. You type
   yes for that exact order. Then you place it yourself, or log it as a paper trade, and tell the skill what
   happened.
9. **`/trader-alerts`** rings on the stop, the target and at 15:15 IST to flatten whatever is still open.

What happened: the 10:10 bar's low was Rs 224.95, so a stop at Rs 225.10 would have been hit, a loss of Rs 212.48
plus charges. ONGC then closed the day at Rs 227.50, above the target. The Time Machine had said the stop came
first about three times in four. A stop is a plan, not a prediction.

## The tools

All standard-library Python. The data tools read public endpoints and are polite about it.

```bash
python3 trader-scanner/scan.py universe                                # Nifty 500, from NSE's public files
python3 trader-scanner/scan.py holidays                                # NSE's holiday list -> trader/holidays.json
python3 trader-scanner/scan.py daily                                   # setups from daily closes -> trader/watchlist.json
python3 trader-scanner/scan.py orb --at 10:15                          # opening-range breakouts so far
python3 trader-news/news.py --tickers ONGC --hours 12                  # announcements, headlines, results dates, today's move
python3 trader-entry/entry.py check ONGC --setup orb                   # waiting / triggered / expired
python3 trader-entry/entry.py watch ONGC --every 30                    # bar by bar until it triggers
python3 trader-exit/exit.py plan --stop or-mid --target 2R --write     # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python3 trader-budget/size.py --write                                  # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-alerts/alerts.py add --from-plan                        # stop, target, flat by 15:15
python3 trader-alerts/alerts.py check                                  # rings what hit, once
python3 trader-analyst/facts.py ONGC                                   # quarters, growth, EPS, the results filing
python3 trader-insider/insider.py deals --who "mutual fund"            # bulk and block deals by a fund
```

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on fixtures cut from real data: NSE charts for Friday October 23 2026 and ONGC's 60 days of
5-minute bars, the NSE holiday master, NSE announcements, event calendars, bulk-deal files and insider filings,
Google News and Yahoo feeds, and Yahoo's quarterly figures. They reproduce the numbers in the example above.

## What is different from the US original

- **Market and clock.** NSE cash market, 09:15 to 15:30 IST, Monday to Friday, with the 2026 holiday list from NSE.
  IST has no daylight saving. NSE has no early closes. The Diwali Muhurat evening session is not covered.
- **Money.** Rupees, Indian digit grouping (Rs 1,00,000), crore and lakh where the numbers are big.
- **Prices.** The NSE tick is Rs 0.05, so stops and targets are rounded to it. Lot sizes are supported for stock futures.
- **Costs.** Brokerage, STT, exchange charges, stamp duty and GST, as a model you can overwrite in `config.json`. The
  report shows the price a trade needs just to break even.
- **Rules.** No pattern-day-trader rule exists in India, so there is none here. The risk check cannot see ASM or GSM
  surveillance, the trade-for-trade segment or a circuit limit, and says so.
- **Data.** Yahoo Finance for charts and fundamentals, NSE for everything the exchange publishes. No SEC, no EDGAR.
- **Orders.** The US pack drove an order tool through a connector. This one never places an order: the ticket desk
  prepares what you type into your own broker app and records what you did.
- **The tracker.** Congress's disclosures became NSE bulk and block deals and SEBI insider filings.

## Fine print

**Not investment advice.** This is software that reads public data and does arithmetic. It does not know your
situation and it cannot see the future. The author is not a SEBI-registered investment adviser or research analyst.
Most people who day trade lose money, and SEBI's studies of individual futures and options traders found that about
nine in ten lost money. You can lose money. Decide for yourself, and talk to a SEBI-registered adviser if you need
advice.

**No order is placed by this pack.** Only `/trader-broker` comes near one, and only to print a ticket after
`/trader-risk` passes that exact order and you type yes for it in the chat. You place the order yourself. No F&O,
margin, short selling or leverage unless you turn them on in the config.

**Paper first.** `"paper": true` is the default in `config.example.json`. Broker keys never go in the chat and this
pack never asks for them.

**The Time Machine is a simulation, not a prediction.** It is built from the stock's own last 60 days of 5-minute
bars. The future can do things those 60 days never did: a results announcement, a circuit limit, a halt, a
headline. Treat its odds as a reality check, never as a promise.

**A stop is a plan, not a guarantee.** In a fast market, at a circuit limit or across a gap, a stop order can fill well
past its price or not at all. The max loss shown is the loss at the stop price.

**The data is free and unofficial.** Yahoo Finance's endpoints and NSE's website APIs are not official, supported
APIs: they can lag, change, block you or hold stale numbers (the analyst and insider skills say so when they see it).
The scanner and the entry timer read 5-minute bars, not every tick. Check anything that matters against your broker
and the exchange filing before you act on it.

**Public disclosures are not inside information.** Bulk deals appear the evening of the trade and insider filings days
later. The insider skill reads them fast. It has no inside information and says so.

**Taxes.** Intraday equity profits in India are generally taxed as speculative business income, and futures and
options as non-speculative business income, not as capital gains. Nothing here is tax advice.

## Files

```
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json, targets.example.json
trader-broker/      SKILL.md, ticket.py
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader-analyst/     SKILL.md, facts.py, metrics.example.json
trader-insider/     SKILL.md, insider.py
tests/              unittest suite + offline fixtures
```

MIT. Made by [Ganesh S Diniyan](https://github.com/gsdiniyan), from the original by [Jake Schincariol](https://opusjake.ai).
