---
name: trader-news
description: >-
  News Monitor for NSE stocks. Tracks breaking news on every stock you watch and flags
  what moves it: what each company told the NSE (results, board outcomes, directors,
  credit ratings, deals, dividends, record dates), Indian headlines from Google News and
  Yahoo Finance, the board meetings and results dates coming up, and how far each stock
  is from yesterday's close. Shows only what is new since the last check, newest first, in
  IST, tags each item (earnings, analyst, deal, halt, regulatory, macro...) and never
  repeats one. Use when the user says "any news on ONGC", "why is this moving", "what's
  the news on my watchlist", "morning briefing", "pre-market news", "monitor the news",
  "did anything break", "are results coming", "check the announcements", or after
  /trader-scanner finds setups.
argument-hint: "[symbols]"
---

# trader-news (India)

A chart can look perfect and still be about to gap on a results date, a board outcome or an order from a
regulator. Before a setup becomes a trade, this skill reads what the company told the exchange and what the
Indian press is saying, for every stock on the watchlist, and tells you only what is new. It also prints how far
each stock has moved today and which board meetings are coming, so a headline, a move and a results date sit
side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers ONGC RELIANCE ITC --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-nse --json
```

Symbols are NSE symbols (`ONGC`, `M&M`, `BAJAJ-AUTO`). `--now "2026-10-23 15:30"` pins the clock (IST).

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| NSE corporate announcements | `nseindia.com/api/corporate-announcements?index=equities&symbol=ONGC&from_date=..&to_date=..`, what the company filed with the exchange |
| NSE event calendar | `nseindia.com/api/event-calendar?...`, board meetings and results dates in the next 14 days (`--events-days`) |
| Google News RSS | `news.google.com/rss/search?q="<company name>" when:48h&hl=en-IN&gl=IN&ceid=IN:en`, Indian publishers |
| Yahoo Finance headline RSS | `feeds.finance.yahoo.com/rss/2.0/headline?s=ONGC.NS&region=IN&lang=en-IN` (thin for Indian stocks, often empty) |
| Yahoo Finance chart endpoint | the last price against yesterday's close (unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) |

All of these are free and unofficial. They answered a plain `Mozilla/5.0` User-Agent with no cookies and no keys on
2026-10-25. NSE changes or blocks its endpoints without notice (it often wants a browser session): when a source
fails the tool prints a `note:` line and carries on with the rest. If the NSE announcements are blocked, say so to
the user and send them to nseindia.com/companies-listing/corporate-filings-announcements for that symbol. Never
claim "no announcements" when the source failed.

## The rules

- **Headlines are leads, not facts.** Before telling the user something happened, open the story or the filing and
  read it. Quote the source and link it. Never summarise a paywalled story you could not read: say so.
- **Filings beat headlines.** If an NSE announcement and a headline disagree, the announcement is what the company
  told the exchange.
- **Say what it touches.** Every item lists the symbols it touches and its tags: earnings, analyst, legal, deal,
  capital, people, risk, halt, regulatory, macro. NSE announcements marked `!` are about results or board
  outcomes, directors and key managers, deals and restructuring, defaults or fraud, credit ratings, dividends and
  other capital actions, IPOs, orders from authorities, or disruptions. Routine paperwork (newspaper
  clippings, ESOP allotments, investor call recordings) is never marked.
- **Google items are kept only when they name the company or the symbol.** A loose search brings in other
  companies' pages. This drops some real stories that use a nickname ("Ambani's Jio"), so a quiet result is not proof
  that nothing happened.
- **Results dates matter most for a day trade.** A `!` in the "Coming up" list means results are today or
  tomorrow. A stock can gap through any stop on the results day.
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`). A move with no headline is worth
  saying out loud too.
- **No predictions.** Report what was published and how far the price is from yesterday's close. Never say what the
  stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means nothing new. `--all` shows items
   already seen, `--json` gives everything machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter: every `!` announcement, anything tagged earnings, halt, legal, regulatory or risk,
   every MOVING symbol, and every results date in the "Coming up" list.
3. Write a short brief, per symbol: what happened, the source and link, the move, and one line on why it matters to
   the setup (for example "results are on 5 November, so an overnight gap can jump past any stop").
4. If a story changes the picture, say so before `/trader-entry` and `/trader-exit`: a results date inside the
   trade, a board outcome, a rating change. Never act on a headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each morning before the open.

## Example: ONGC, Friday 2026-10-23

The check at 15:30 IST on ONGC alone, six hours back:

```
ONGC       Rs 227.50 +1.11% vs yesterday's close Rs 225.00 at 15:28 IST

Coming up (NSE event calendar):
  ONGC       05-Nov-2026 (in 13 days)  Financial Results/Dividend: To consider and approve the financial results ...

  2026-10-23 14:35 IST  ONGC        scanx.trade    ONGC appoints Sunil Kumar as Chairman effective Dec 7, 2026
  2026-10-23 13:28 IST  ONGC        Business Uptur ONGC appoints Sunil Kumar as chairman for a tenure up to three years
! 2026-10-23 13:18 IST  ONGC        NSE            General Updates: Letter from Ministry of Petroleum and Natural Gas conveying ...
  2026-10-23 11:59 IST  ONGC        GK Today       Sunil Kumar named next ONGC Chairman
```

The `!` line is ONGC's own filing at 13:18: the Ministry's letter that the Appointments Committee approved a new
chairman from 7 December. The brief: "ONGC told the exchange at 13:18 that Sunil Kumar will be chairman from 7
December; the press follows. Results are 13 days away, so nothing falls inside today's trade." When a symbol has no
news, say so rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a headline.
- Never hammers a server: one request per feed per symbol, a pause between calls.

## Safety

- Education and tooling, not investment advice. The author is not a SEBI-registered
  investment adviser or research analyst. This skill only reads and reports.
- Paper trading by default (`"paper": true`). Going live is the user's own decision.
- Nothing in this pack places an order. trader-broker only prepares order tickets that the
  user places themselves in their own broker app.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables, never in the chat.
